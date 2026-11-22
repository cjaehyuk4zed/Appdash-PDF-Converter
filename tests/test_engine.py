import math

import pytest
from conftest import convert, item
from PIL import Image
from pypdf import PdfReader

from converter.engine import inspect_file, page_indices, strip_jpeg_metadata
from converter.models import Options


@pytest.mark.parametrize("fmt", ["jpeg", "png", "webp", "tiff", "bmp", "avif"])
def test_image_output_codecs(tmp_path, image_path, fmt):
    result, directory = convert(tmp_path, [item(image_path)], format=fmt, max_width=320)
    output = result["outputs"][0]
    assert output["target_met"] is None
    with Image.open(directory / output["name"]) as image:
        assert image.size == (320, 240)
    assert (directory / "preview.png").is_file()
    assert (directory / "results.zip").is_file()


def test_size_target_measured_on_real_file(tmp_path, noise_path):
    result, directory = convert(tmp_path, [item(noise_path)], mode="target", target_kb=20, min_quality=35, min_long_edge=64)
    output = result["outputs"][0]
    assert output["target_met"]
    assert output["size"] == (directory / output["name"]).stat().st_size <= 20000


def test_impossible_target_is_not_reported_as_success(tmp_path, image_path):
    result, directory = convert(tmp_path, [item(image_path)], format="png", mode="target", target_kb=.1, allow_downscale=False)
    assert result["outputs"][0]["target_met"] is False
    assert result["warnings"]
    assert (directory / result["outputs"][0]["name"]).is_file()


def test_pdf_page_selection_and_rotation(tmp_path, pdf_path):
    result, directory = convert(tmp_path, [item(pdf_path, "pdf", 0), item(pdf_path, "pdf", 1)], operation="pdf_to_images", format="png", pages="2", dpi=144)
    assert len(result["outputs"]) == 1
    with Image.open(directory / result["outputs"][0]["name"]) as image:
        assert image.size == (720, 960)


def test_preserve_pdf_retains_text_fields_links_and_removes_metadata(tmp_path, pdf_path):
    result, directory = convert(tmp_path, [item(pdf_path, "pdf", 0), item(pdf_path, "pdf", 1)], operation="pdf_compress")
    reader = PdfReader(directory / result["outputs"][0]["name"])
    assert len(reader.pages) == 2
    assert "Searchable text" in reader.pages[0].extract_text()
    assert reader.get_fields()["reference"]["/V"] == "Retain this form"
    assert any(annotation.get_object().get("/Subtype") == "/Link" for annotation in reader.pages[0]["/Annots"])
    assert reader.metadata is None or "/Title" not in reader.metadata


def test_preserve_pdf_reorders_subset(tmp_path, pdf_path):
    selections = [item(pdf_path, "pdf", 1), item(pdf_path, "pdf", 0)]
    selections[0]["rotation"] = 90
    result, directory = convert(tmp_path, selections, operation="pdf_compress")
    reader = PdfReader(directory / result["outputs"][0]["name"])
    assert "Second page" in reader.pages[0].extract_text()
    assert reader.pages[0].rotation == 90


def test_raster_pdf_preserves_paper_size_and_removes_text(tmp_path, pdf_path):
    result, directory = convert(tmp_path, [item(pdf_path, "pdf")], operation="pdf_compress", compression="raster", mode="target", target_kb=25, dpi=150)
    output = result["outputs"][0]
    reader = PdfReader(directory / output["name"])
    assert reader.pages[0].extract_text() == ""
    assert float(reader.pages[0].mediabox.width) == pytest.approx(360, abs=.1)
    assert float(reader.pages[0].mediabox.height) == pytest.approx(480, abs=.1)
    assert any("Rasterized" in value for value in result["warnings"])
    if output["target_met"]:
        assert (directory / output["name"]).stat().st_size <= 25000


def test_transparent_images_to_pdf_and_paper_layout(tmp_path):
    path = tmp_path / "transparent.png"
    Image.new("RGBA", (200, 100), (30, 120, 70, 100)).save(path)
    result, directory = convert(tmp_path, [item(path)], operation="images_to_pdf", paper="a4", margin_mm=10)
    reader = PdfReader(directory / result["outputs"][0]["name"])
    assert float(reader.pages[0].mediabox.width) > float(reader.pages[0].mediabox.height)
    assert len(reader.pages) == 1


def test_images_to_pdf_total_and_page_image_limits(tmp_path, noise_path):
    result, directory = convert(tmp_path, [item(noise_path), item(noise_path)], operation="images_to_pdf", mode="target", target_kb=45, page_image_kb=21, min_quality=30, min_long_edge=64)
    output = result["outputs"][0]
    assert len(PdfReader(directory / output["name"]).pages) == 2
    assert output["size"] <= 45000
    assert output["target_met"] is True
    assert output["page_image_target_met"] is True


def test_exif_orientation_and_metadata(tmp_path):
    path = tmp_path / "phone.jpg"
    image = Image.new("RGB", (100, 60), "red")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private caption"
    image.save(path, exif=exif)
    result, directory = convert(tmp_path, [item(path)], format="jpeg")
    with Image.open(directory / result["outputs"][0]["name"]) as output:
        assert output.size == (60, 100)
        assert not output.getexif()


def test_original_jpeg_embedded_without_recompression(tmp_path, image_path):
    jpeg = tmp_path / "original.jpg"
    with Image.open(image_path) as image:
        exif = Image.Exif()
        exif[270] = "private caption"
        image.save(jpeg, quality=87, exif=exif)
    stripped = tmp_path / "stripped.jpg"
    strip_jpeg_metadata(jpeg, stripped)
    result, directory = convert(tmp_path, [item(jpeg)], operation="images_to_pdf")
    reader = PdfReader(directory / result["outputs"][0]["name"])
    streams = reader.pages[0]["/Resources"]["/XObject"].values()
    assert next(iter(streams)).get_object().get_data() == stripped.read_bytes()
    assert result["outputs"][0]["quality"] is None
    assert result["outputs"][0]["original_pages"] == 1


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "TIFF", "BMP", "GIF", "AVIF", "HEIF"])
def test_supported_inputs_can_be_inspected(tmp_path, image_path, fmt):
    from pillow_heif import register_heif_opener
    register_heif_opener()
    path = tmp_path / "input.bin"
    with Image.open(image_path) as image:
        image.save(path, format=fmt)
    result = inspect_file({"path": str(path), "thumbnail": str(tmp_path / "thumb.png"), "pixel_limit": 40000000, "page_limit": 500})
    assert result["format"] == fmt
    assert result["width"] == 640


def test_multipage_tiff_and_animated_gif_policy(tmp_path):
    first, second = Image.new("RGB", (80, 60), "red"), Image.new("RGB", (80, 60), "blue")
    for fmt in ("TIFF", "GIF"):
        path = tmp_path / f"multi.{fmt.lower()}"
        first.save(path, save_all=True, append_images=[second], format=fmt)
        result = inspect_file({"path": str(path), "thumbnail": str(tmp_path / "thumb.png"), "pixel_limit": 40000000, "page_limit": 500})
        assert result["pages"] == (2 if fmt == "TIFF" else 1)
        if fmt == "GIF":
            assert result["warnings"]


def test_page_ranges_and_strict_api_validation():
    assert page_indices("3, 1-2, 2", 3) == [2, 0, 1]
    for expression in ("0", "2-1", "4", "1,,2", "x"):
        with pytest.raises(ValueError):
            page_indices(expression, 3)
    for changes in ({"quality": True}, {"target_kb": math.nan}, {"extra": 1}, {"mode": "target"}, {"quality": 40, "mode": "target", "target_kb": 20}, {"operation": "pdf_to_images", "dpi": 72, "mode": "target", "target_kb": 20}):
        with pytest.raises(ValueError):
            Options.model_validate({"operation": "image_convert", **changes})


def test_pixel_limit_rejects_oversized_render(tmp_path, pdf_path):
    from converter.engine import render_pdf
    with pytest.raises(ValueError, match="pixel"):
        render_pdf(item(pdf_path, "pdf"), 600, 1000)


def test_password_pdf(tmp_path, pdf_path):
    from reportlab.lib.pdfencrypt import StandardEncryption
    from reportlab.pdfgen import canvas
    encrypted = tmp_path / "encrypted.pdf"
    document = canvas.Canvas(str(encrypted), encrypt=StandardEncryption("fixture-password"))
    for number in range(2):
        document.drawString(50, 700, f"Protected page {number + 1}")
        document.showPage()
    document.save()
    payload = {"path": str(encrypted), "thumbnail": str(tmp_path / "thumb.png"), "password": "fixture-password", "pixel_limit": 40000000, "page_limit": 500}
    assert inspect_file(payload)["pages"] == 2
    payload["password"] = "wrong"
    with pytest.raises(ValueError, match="password"):
        inspect_file(payload)


def test_final_dimension_bounds_apply_after_rotation(tmp_path, image_path):
    selection = item(image_path)
    selection["rotation"] = 90
    result, directory = convert(tmp_path, [selection], format="png", max_width=120)
    with Image.open(directory / result["outputs"][0]["name"]) as image:
        assert image.size == (120, 160)


def test_pdf_target_downsampling_keeps_original_image_paper_size(tmp_path, noise_path):
    result, directory = convert(tmp_path, [item(noise_path)], operation="images_to_pdf", mode="target", target_kb=20, min_quality=30, min_long_edge=64)
    output = result["outputs"][0]
    page = PdfReader(directory / output["name"]).pages[0]
    assert float(page.mediabox.width) == pytest.approx(640 * 72 / 200)
    assert float(page.mediabox.height) == pytest.approx(480 * 72 / 200)
    assert output["target_met"] is True


def test_low_quality_and_screen_resolution_are_valid_without_size_search():
    assert Options.model_validate({"operation": "pdf_to_images", "dpi": 72, "quality": 40}).dpi == 72
    assert Options.model_validate({"operation": "images_to_pdf", "dpi": 72, "mode": "target", "target_kb": 20}).dpi == 72


def test_render_includes_filled_form_appearance(pdf_path):
    from converter.engine import render_pdf
    image = render_pdf(item(pdf_path, "pdf"), 144, 40000000)
    # Interior of the filled text field; exclude its border.
    crop = image.convert("L").crop((70, 716, 500, 754))
    assert sum(value < 100 for value in crop.get_flattened_data()) > 100
    image.close()


def test_output_safety_budget(tmp_path, noise_path):
    from converter.engine import conversion
    directory = tmp_path / "budget"
    directory.mkdir()
    with pytest.raises(ValueError, match="output safety limit"):
        conversion({"directory": str(directory), "items": [item(noise_path)], "options": {"operation": "image_convert", "format": "bmp"}, "pixel_limit": 40000000, "output_limit": 1000}, lambda event: None)


def test_separate_pdf_outputs(tmp_path, image_path):
    result, directory = convert(tmp_path, [item(image_path), item(image_path)], operation="images_to_pdf", split=True)
    assert len(result["outputs"]) == 2
    assert len({output["name"] for output in result["outputs"]}) == 2
    assert all(len(PdfReader(directory / output["name"]).pages) == 1 for output in result["outputs"])
