"""Conversion code. Called only inside disposable worker processes."""
import io
import math
import re
import shutil
import warnings
import zipfile
from contextlib import closing
from pathlib import Path

import img2pdf
import pypdfium2 as pdfium
from PIL import Image, ImageCms, ImageOps, features
from pillow_heif import register_heif_opener
from pypdf import PdfReader, PdfWriter

from .models import Options

register_heif_opener()
warnings.simplefilter("error", Image.DecompressionBombWarning)
EXTENSIONS = {"jpeg": "jpg", "png": "png", "webp": "webp", "tiff": "tif", "bmp": "bmp", "avif": "avif"}
INPUT_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF", "BMP", "GIF", "AVIF", "HEIF"}


def capabilities():
    Image.init()
    formats = [f for f in EXTENSIONS if f.upper() in Image.SAVE]
    if not features.check("webp"):
        formats = [f for f in formats if f != "webp"]
    if not features.check("avif"):
        formats = [f for f in formats if f != "avif"]
    return {"input": sorted(INPUT_FORMATS & set(Image.OPEN)), "output": formats}


def safe_stem(name):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name).stem).strip(". ")[:100]
    return value or "converted"


def page_indices(expression, count):
    if not expression.strip():
        return list(range(count))
    result = []
    for segment in expression.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", segment)
        if not match:
            raise ValueError("Use page ranges such as 1-3, 7, 10-12.")
        first, last = int(match[1]), int(match[2] or match[1])
        if not 1 <= first <= last <= count:
            raise ValueError(f"Page range must be between 1 and {count}.")
        for number in range(first - 1, last):
            if number not in result:
                result.append(number)
    return result


def check_pixels(width, height, limit):
    if width <= 0 or height <= 0 or width * height > limit:
        raise ValueError(f"Image/render exceeds the {limit:,}-pixel safety limit. Lower the DPI or dimensions.")


def render_pdf(item, dpi, pixel_limit, max_width=None, max_height=None):
    with pdfium.PdfDocument(item["path"], password=item.get("password") or None) as pdf:
        pdf.init_forms()
        with closing(pdf.get_page(item.get("page", 0))) as page:
            width, height = page.get_size()
            scale = dpi / 72
            if max_width:
                scale = min(scale, max_width / width)
            if max_height:
                scale = min(scale, max_height / height)
            check_pixels(math.ceil(width * scale), math.ceil(height * scale), pixel_limit)
            bitmap = page.render(scale=scale, draw_annots=True)
            try:
                return bitmap.to_pil().copy()
            finally:
                bitmap.close()


def load_image(item, options, pixel_limit, scale=1):
    if item["kind"] == "pdf":
        bounds = (options.max_width, options.max_height)
        if item.get("rotation") in (90, 270):
            bounds = bounds[::-1]
        image = render_pdf(item, options.dpi * scale, pixel_limit, *bounds)
    else:
        with Image.open(item["path"]) as source:
            source.seek(item.get("page", 0))
            check_pixels(*source.size, pixel_limit)
            image = ImageOps.exif_transpose(source).copy()
        if item.get("rotation"):
            image = image.rotate(-item["rotation"], expand=True)
        if options.max_width or options.max_height:
            image.thumbnail((options.max_width or image.width, options.max_height or image.height), Image.Resampling.LANCZOS)
        floor = min(1, options.min_long_edge / max(image.size))
        actual_scale = max(scale, floor)
        if actual_scale < 1:
            image = image.resize(tuple(max(1, round(v * actual_scale)) for v in image.size), Image.Resampling.LANCZOS)
    if item["kind"] == "pdf" and item.get("rotation"):
        image = image.rotate(-item["rotation"], expand=True)
    if options.color in ("grayscale", "bw"):
        alpha = image.getchannel("A") if image.mode in ("RGBA", "LA") else None
        image = image.convert("L")
        if alpha is not None:
            background = Image.new("L", image.size, 255)
            background.paste(image, mask=alpha)
            image = background
        if options.color == "bw":
            image = image.point(lambda value: 255 if value >= options.threshold else 0, mode="1")
    return image


def rgb_image(image, background):
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        out = Image.new("RGB", image.size, background)
        out.paste(rgba, mask=rgba.getchannel("A"))
        return out
    profile = image.info.get("icc_profile")
    if profile and image.mode in ("RGB", "CMYK"):
        try:
            return ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ValueError, OSError, ImageCms.PyCMSError):
            pass
    return image.convert("RGB")


def encode(image, path, fmt, quality, options):
    args = {}
    if fmt == "jpeg":
        image = rgb_image(image, options.background)
        args.update(quality=quality, optimize=True, progressive=True, subsampling=0 if quality >= 90 else 2)
    elif fmt in ("webp", "avif"):
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        args.update(quality=quality)
        if fmt == "webp":
            args.update(lossless=options.lossless, method=4)
        elif options.lossless:
            raise ValueError("Use PNG, TIFF or lossless WebP for lossless output.")
    elif fmt == "png":
        args.update(optimize=True, compress_level=9)
        if image.mode == "CMYK":
            image = rgb_image(image, options.background)
    elif fmt == "tiff":
        args.update(compression="group4" if image.mode == "1" else "tiff_deflate")
    elif fmt == "bmp":
        image = rgb_image(image, options.background)
    if not options.strip_metadata:
        # Orientation is already applied to pixels; never write the old orientation again.
        exif = image.getexif()
        exif.pop(274, None)
        if exif and fmt in ("jpeg", "png", "webp", "tiff", "avif"):
            args["exif"] = exif.tobytes()
    image.save(path, format=fmt.upper(), **args)


def fit_output(writer, options, destination, floor_scale=1, lossy=True, progress=None):
    """Bounded search; retain the best candidate and verify bytes on the finished file."""
    destination = Path(destination)
    trial = destination.with_name("_trial" + destination.suffix)
    best = destination.with_name("_best" + destination.suffix)
    target = math.floor(options.target_kb * 1000) if options.mode == "target" else None
    best_size = math.inf
    best_settings = None
    attempts = 0
    if target and options.allow_downscale and floor_scale < 1:
        scales = [floor_scale ** (i / 5) for i in range(6)]
    else:
        scales = [1.0]

    def attempt(quality, scale):
        nonlocal attempts, best_size, best_settings
        info = writer(trial, quality, scale) or {}
        size = trial.stat().st_size
        attempts += 1
        if size < best_size:
            shutil.copyfile(trial, best)
            best_size = size
            best_settings = {"quality": quality if lossy else None, "scale": round(scale, 4), **info}
        if progress:
            progress({"detail": f"Testing output size · attempt {attempts}"})
        return size, info

    try:
        for scale in scales:
            size, info = attempt(options.quality, scale)
            if target is None or size <= target:
                shutil.copyfile(trial, destination)
                return {"target_met": None if target is None else True, "quality": options.quality if lossy else None, "scale": round(scale, 4), "attempts": attempts, **info}
            if not lossy or options.min_quality == options.quality:
                continue
            size, info = attempt(options.min_quality, scale)
            if size > target:
                continue
            shutil.copyfile(trial, destination)
            winner = {"quality": options.min_quality, "scale": round(scale, 4), **info}
            low, high = options.min_quality, options.quality
            while high - low > 1:
                quality = (low + high) // 2
                size, info = attempt(quality, scale)
                if size <= target:
                    shutil.copyfile(trial, destination)
                    winner = {"quality": quality, "scale": round(scale, 4), **info}
                    low = quality
                else:
                    high = quality
            return {"target_met": True, "attempts": attempts, **winner}
        shutil.copyfile(best, destination)
        return {"target_met": False, "attempts": attempts, **best_settings}
    finally:
        trial.unlink(missing_ok=True)
        best.unlink(missing_ok=True)


def inspect_file(payload):
    path = Path(payload["path"])
    Image.MAX_IMAGE_PIXELS = payload["pixel_limit"]
    notes = []
    with path.open("rb") as stream:
        is_pdf = b"%PDF-" in stream.read(1024)
    if is_pdf:
        try:
            with pdfium.PdfDocument(path, password=payload.get("password") or None) as pdf:
                count = len(pdf)
                if not 0 < count <= payload["page_limit"]:
                    raise ValueError("PDF has no pages or exceeds the page-count limit.")
                with closing(pdf.get_page(0)) as page:
                    width, height = page.get_size()
        except pdfium.PdfiumError as error:
            raise ValueError("Cannot open PDF. Check the file and its password.") from error
        item = {"path": str(path), "kind": "pdf", "password": payload.get("password", "")}
        image = render_pdf(item, 72, payload["pixel_limit"], 360, 440)
        info = {"kind": "pdf", "format": "PDF", "pages": count, "width": round(width), "height": round(height), "unit": "pt"}
        if payload.get("password"):
            notes.append("Output files will not be password-protected.")
    else:
        try:
            with Image.open(path) as source:
                if source.format not in INPUT_FORMATS:
                    raise ValueError("Unsupported image format.")
                check_pixels(*source.size, payload["pixel_limit"])
                fmt = source.format
                count = getattr(source, "n_frames", 1) if fmt == "TIFF" else 1
                if count > payload["page_limit"]:
                    raise ValueError("Image exceeds the page-count limit.")
                if fmt != "TIFF" and getattr(source, "n_frames", 1) > 1:
                    notes.append("Only the first frame/primary image will be imported.")
                image = ImageOps.exif_transpose(source).copy()
                info = {"kind": "image", "format": fmt, "pages": count, "width": image.width, "height": image.height, "unit": "px"}
        except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValueError("Cannot open image, or it exceeds the pixel safety limit.") from error
    image.thumbnail((360, 440), Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
        image = image.convert("RGB")
    image.save(payload["thumbnail"], "PNG")
    image.close()
    return {**info, "warnings": notes}


def strip_jpeg_metadata(source, destination):
    """Remove EXIF/XMP/comments without recompressing JPEG scan data."""
    data = Path(source).read_bytes()
    output = bytearray(data[:2])
    position = 2
    while position < len(data):
        if data[position] != 0xFF:
            raise ValueError("Invalid JPEG marker.")
        start = position
        while position < len(data) and data[position] == 0xFF:
            position += 1
        marker = data[position]
        position += 1
        if marker in (0xDA, 0xD9):
            output.extend(data[start:])
            break
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            raise ValueError("Invalid JPEG metadata segment.")
        if marker not in (0xE1, 0xED, 0xFE):
            output.extend(data[start:position + length])
        position += length
    Path(destination).write_bytes(output)


def direct_image(item, options, destination):
    if not options.preserve_original or options.mode == "target" or options.page_image_kb or options.max_width or options.max_height or options.color != "original" or item.get("rotation"):
        return None
    with Image.open(item["path"]) as source:
        if source.getexif().get(274, 1) != 1 or item.get("page", 0):
            return None
        if source.format not in ("JPEG", "PNG"):
            return None
        if source.format == "JPEG" and options.strip_metadata:
            strip_jpeg_metadata(item["path"], destination)
            return str(destination)
        return item["path"]


def layout_function(options):
    margin = options.margin_mm * 72 / 25.4
    if options.paper == "original":
        def original(width, height, dpi):
            # Stable physical size independent of unreliable/missing camera DPI tags.
            image_width, image_height = width * 72 / options.dpi, height * 72 / options.dpi
            return image_width + margin * 2, image_height + margin * 2, image_width, image_height
        return original
    base = (595.276, 841.89) if options.paper == "a4" else (612, 792)
    def paper(width, height, dpi):
        page_width, page_height = base if height >= width else base[::-1]
        ratio = min((page_width - 2 * margin) / width, (page_height - 2 * margin) / height)
        return page_width, page_height, width * ratio, height * ratio
    return paper


def assemble_pdf(items, options, output, quality, scale, pixel_limit, progress, output_limit):
    work = Path(output).parent / "_pages"
    work.mkdir(exist_ok=True)
    pages = []
    page_bytes = 0
    original_pages = 0
    image_budget_met = True
    try:
        for index, item in enumerate(items):
            target_path = work / f"{index:04d}.img"
            direct = direct_image(item, options, target_path) if item["kind"] == "image" and scale == 1 else None
            if direct:
                pages.append(direct)
                original_pages += 1
                page_bytes += Path(direct).stat().st_size
                if page_bytes > output_limit:
                    raise ValueError("Encoded pages exceed the output safety limit. Use fewer pages or smaller dimensions.")
                continue
            image = load_image(item, options, pixel_limit, scale)
            try:
                fmt = "png" if options.lossless or options.color == "bw" else "jpeg"
                if options.page_image_kb:
                    page_options = options.model_copy(update={"mode": "target", "target_kb": options.page_image_kb, "quality": quality, "min_quality": min(quality, options.min_quality)})
                    floor = min(1, options.min_long_edge / max(image.size)) if item["kind"] == "image" else min(1, options.min_dpi / (options.dpi * scale))
                    def write_page(path, q, page_scale, source_image=image, page_format=fmt):
                        candidate = source_image if page_scale == 1 else source_image.resize(tuple(max(1, round(v * page_scale)) for v in source_image.size), Image.Resampling.LANCZOS)
                        try:
                            encode(candidate, path, page_format, q, options)
                        finally:
                            if candidate is not source_image:
                                candidate.close()
                    result = fit_output(write_page, page_options, target_path, floor, fmt == "jpeg")
                    image_budget_met &= result["target_met"]
                else:
                    encode(image, target_path, fmt, quality, options)
                pages.append(str(target_path))
                page_bytes += target_path.stat().st_size
                if page_bytes > output_limit:
                    raise ValueError("Encoded pages exceed the output safety limit. Use fewer pages or smaller dimensions.")
            finally:
                image.close()
            progress({"detail": f"Encoding page {index + 1} of {len(items)}"})
        # Rasterized PDF pages keep their original physical page dimensions.
        with PdfWriter() as combined:
            for item, image_path in zip(items, pages, strict=True):
                if item["kind"] == "pdf":
                    with pdfium.PdfDocument(item["path"], password=item.get("password") or None) as original:
                        with closing(original.get_page(item["page"])) as page:
                            width, height = page.get_size()
                    if item.get("rotation") in (90, 270):
                        width, height = height, width
                    layout = (width, height, width, height)
                else:
                    # Keep paper dimensions stable while the target search downsamples pixels.
                    with Image.open(item["path"]) as source:
                        source.seek(item.get("page", 0))
                        width, height = source.size
                        if source.getexif().get(274, 1) in (5, 6, 7, 8):
                            width, height = height, width
                    if item.get("rotation") in (90, 270):
                        width, height = height, width
                    bound = min(1, (options.max_width or width) / width, (options.max_height or height) / height)
                    layout = layout_function(options)(width * bound, height * bound, None)
                def fixed_layout(w, h, dpi, page_layout=layout):
                    return page_layout
                single = PdfReader(io.BytesIO(img2pdf.convert(image_path, layout_fun=fixed_layout, engine=img2pdf.Engine.internal)))
                combined.add_page(single.pages[0])
            combined.compress_identical_objects()
            combined.write(output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    encoded_quality = quality if original_pages < len(items) and not options.lossless and options.color != "bw" else None
    return {"page_image_target_met": image_budget_met if options.page_image_kb else None, "original_pages": original_pages, "quality": encoded_quality}


def preserve_pdf(items, options, output):
    # Preserve document-level forms, outlines and links by copying the whole source first.
    with PdfReader(items[0]["path"], password=items[0].get("password") or None) as pdf:
        selected = [item["page"] for item in items]
        with PdfWriter() as writer:
            if selected == list(range(len(pdf.pages))):
                writer.clone_document_from_reader(pdf)
            else:
                writer.append(pdf, pages=selected)
            for page, item in zip(writer.pages, items, strict=True):
                if item.get("rotation"):
                    page.rotate(item["rotation"])
                page.compress_content_streams(level=9)
            if options.strip_metadata:
                if "/Metadata" in writer.root_object:
                    del writer.root_object["/Metadata"]
                writer.metadata = None
            writer.compress_identical_objects()
            writer.write(output)


def conversion(payload, progress):
    options = Options.model_validate(payload["options"])
    root = Path(payload["directory"])
    pixel_limit = payload["pixel_limit"]
    output_limit = payload.get("output_limit", 500000000)
    Image.MAX_IMAGE_PIXELS = pixel_limit
    items = payload["items"]
    if options.pages and options.operation in ("pdf_to_images", "pdf_compress"):
        filtered = []
        for file_id in dict.fromkeys(item["file_id"] for item in items):
            group = [item for item in items if item["file_id"] == file_id]
            indices = page_indices(options.pages, group[0]["pages"])
            by_page = {item["page"]: item for item in group}
            filtered.extend(by_page[index] for index in indices if index in by_page)
        items = filtered
    if not items:
        raise ValueError("No pages selected after applying the page range.")
    if payload.get("preview"):
        items = items[:1]
    notes = []
    outputs = []
    image_output = options.operation in ("pdf_to_images", "image_convert")
    if image_output and options.format not in capabilities()["output"]:
        raise ValueError("This output codec is unavailable on this server.")
    groups = [[item] for item in items] if image_output or options.split else [items]
    if options.operation == "pdf_compress" and not options.split:
        groups = [[item for item in items if item["file_id"] == file_id] for file_id in dict.fromkeys(item["file_id"] for item in items)]
    for index, group in enumerate(groups):
        item = group[0]
        extension = EXTENSIONS[options.format] if image_output else "pdf"
        name = f"{index + 1:03d}_{safe_stem(item['name'])}"
        if len(group) == 1:
            name += f"_p{item['page'] + 1:03d}"
        destination = root / f"{name}.{extension}"
        progress({"progress": round(index / len(groups) * 90), "detail": f"Converting output {index + 1} of {len(groups)}"})
        if image_output:
            base = load_image(item, options, pixel_limit)
            try:
                floor = min(1, options.min_dpi / options.dpi) if item["kind"] == "pdf" else min(1, options.min_long_edge / max(base.size))
                def writer(path, quality, scale, source_image=base):
                    # Every attempt starts from the original decoded pixels, never the last JPEG.
                    image = source_image if scale == 1 else source_image.resize(tuple(max(1, round(v * scale)) for v in source_image.size), Image.Resampling.LANCZOS)
                    try:
                        encode(image, path, options.format, quality, options)
                        return {"width": image.width, "height": image.height}
                    finally:
                        if image is not source_image:
                            image.close()
                lossy = options.format in ("jpeg", "webp", "avif") and not options.lossless
                result = fit_output(writer, options, destination, floor, lossy, progress)
            finally:
                base.close()
        elif options.operation == "pdf_compress" and options.compression == "preserve":
            preserve_pdf(group, options, destination)
            target = math.floor(options.target_kb * 1000) if options.mode == "target" else None
            result = {"target_met": destination.stat().st_size <= target if target else None, "quality": None, "scale": 1, "attempts": 1}
            notes.append("Preserve-content compression does not downsample images; an optimized PDF may not get smaller. PDF editing invalidates existing digital signatures.")
        else:
            if options.operation == "pdf_compress":
                notes.append("Rasterized output has no searchable text, live links, forms or vector graphics.")
            floor = min(1, options.min_dpi / options.dpi) if item["kind"] == "pdf" else 0.08
            def writer(path, quality, scale, output_group=group):
                return assemble_pdf(output_group, options, path, quality, scale, pixel_limit, progress, output_limit)
            result = fit_output(writer, options, destination, floor, not options.lossless and options.color != "bw", progress)
        if result.get("target_met") is False:
            notes.append("A size target could not be met within the selected quality/resolution limits. The smallest attempted result is available.")
        if result.get("page_image_target_met") is False:
            notes.append("At least one embedded page image exceeds the page-image budget.")
        outputs.append({"name": destination.name, "size": destination.stat().st_size, "pages": len(group), **result})
        if sum(output["size"] for output in outputs) > output_limit:
            raise ValueError("Results exceed the output safety limit. Use fewer pages or smaller dimensions.")
    preview_path = root / "preview.png"
    first = root / outputs[0]["name"]
    if first.suffix == ".pdf":
        preview_image = render_pdf({"path": str(first), "page": 0}, options.dpi, pixel_limit, 2400, 2400)
    else:
        with Image.open(first) as decoded:
            preview_image = decoded.copy()
        preview_image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    if preview_image.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
        preview_image = preview_image.convert("RGB")
    preview_image.save(preview_path, "PNG")
    preview_image.close()
    with zipfile.ZipFile(root / "results.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        for output in outputs:
            archive.write(root / output["name"], output["name"])
    return {"outputs": outputs, "warnings": list(dict.fromkeys(notes)), "preview": bool(payload.get("preview"))}
