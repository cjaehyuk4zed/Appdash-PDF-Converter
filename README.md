# Appdash PDF Converter

A private PDF and image converter with one browser interface for Windows and headless Linux. Files are processed by the machine running this app; no external conversion service is used.

## Start on Windows

Clone the repository, install dependencies, and start the app:

```powershell
git clone https://github.com/cjaehyuk4zed/Appdash-PDF-Converter.git
cd Appdash-PDF-Converter
.\setup.ps1
.\start.ps1
```

Open **http://127.0.0.1:8765/**. Stop the server with **Ctrl+C**. Setup uses `uv` when available, otherwise the Windows Python launcher and pip. Python **3.12–3.14** is supported.

### AppDash

Copy the example configuration before registering the app:

```powershell
Copy-Item .\appdash-gateway-config.example.json .\appdash-gateway-config.json
```

In the copied file, set `gateway.workingDirectory` to the absolute folder where you cloned the repository and `gateway.start.executable` to that folder's `.venv\Scripts\python.exe`. Import the configured file using AppDash's **앱 만들기 · 연결하기**, review it, and register it as a service. AppDash can then start/stop its owned process and open the UI. The local configuration is ignored by Git so personal paths are not published. Registration is optional; the app also runs independently.

If you move the folder, update both absolute paths in your local configuration. If you change `CONVERTER_PORT`, update `openUrl` and `healthUrl` too.

## Start on Linux

Clone the repository first. Native installation requires Python 3.12–3.14 with venv/pip support:

```sh
sh setup.sh
.venv/bin/python run.py
```

The default bind address is loopback. Access it through an SSH tunnel or your authenticated reverse proxy/private network.

For Docker:

```sh
docker compose up -d --build
docker compose logs -f converter
docker compose down
```

Compose publishes the app only on `127.0.0.1:8765`, runs as an unprivileged user and retains temporary files in a named volume. `docker compose down` keeps that volume. The app automatically removes expired files while it is running, including leftovers from a previous run.

For your own HTTPS hostname, configure `CONVERTER_ALLOWED_HOSTS`, `CONVERTER_PUBLIC_ORIGINS` and `CONVERTER_SECURE_COOKIE=1`. Keep access behind your authentication proxy; there is no built-in login/account system. If the proxy runs in another container, connect it to the same private container network and route to port 8765. Do not expose an unauthenticated public converter. No domain is hardcoded.

## Interface

Korean is the default language. Use the language selector at the top right to switch between **한국어** and **English**; the choice is saved in your browser. Switching languages preserves current uploads, settings, and visible results.

The settings panel keeps **Image size & resolution / 이미지 크기·해상도** collapsed by default, with a live summary of the current DPI and dimension bounds. Expand it to change these values. **More options / 고급 설정** contains the remaining advanced controls.

## Tools

| Tool | Features |
|---|---|
| PDF to images | Page ranges, page selection/order, rotation, DPI, image encoding and size targets |
| Images to PDF | Combined or separate PDFs, margins, A4/Letter/automatic orientation, original image embedding |
| Image converter | Batch conversion, aspect-preserving resize, color modes, transparency/background, metadata removal |
| Compress PDF | Preserve-content structural optimization or explicitly selected rasterization |

Image input: **JPEG, PNG, WebP, TIFF, BMP, GIF, AVIF, HEIC/HEIF**. Image output: **JPEG, PNG, WebP, TIFF, BMP, AVIF**. Startup checks report which encoders are available. Multipage TIFFs are expanded into selectable pages. Animated GIF/WebP/AVIF and multi-image HEIF files import only their first frame/primary image, with a notice.

Mixed PDFs and images can be uploaded, but each tool displays the inputs it accepts. Each page appears as one file card with its thumbnail, name, dimensions, file size, selection checkbox and rotation/ordering controls. Use the drag handles or up/down buttons to reorder pages; drag handles also support the keyboard up/down arrows. A PDF page range applies to each uploaded PDF and intersects the checked pages. A range can also specify ordering, such as `3, 1-2`.

Output filenames use the source filename without its extension, followed by the output sequence: `document_001.png`, `document_002.png`, and so on. The same names appear in individual downloads and the ZIP archive.

### Quality, dimensions and bytes

- **Quality Select / 화질 선택** uses the selected format, quality and dimensions. The slider remains visible for lossless formats. Selecting Lossless shows 100; moving the slider switches to Custom without changing the selected output format. PNG/TIFF remain lossless, and BMP remains uncompressed. Lowering the slider in lossless WebP switches off its lossless option. Lossy encoder quality is limited to 1–95; 100 is the lossless UI indicator.
- **File Size Select / 용량 선택** automatically searches quality 1–95 and tests the finished file against a byte limit: **KB = 1,000 bytes; MB = 1,000,000 bytes**. Manual quality/preset controls are hidden in this mode. Image-output tools default to **1 MB per image**; PDF-output tools default to **10 MB per PDF file**. The limit is per output file, not the ZIP.
- JPEG/WebP/AVIF quality values are specific to their encoders. PNG/TIFF use lossless compression; BMP is uncompressed. Lossless WebP is optional. Changing a slider value does not turn PNG/TIFF into a lossy format.
- The size search starts at quality 95 and can reduce quality to 1. When permitted, it also tries progressively smaller dimensions, respecting the selected bounds. It refines quality at the first dimension tier that meets the target. It is a bounded search, not a guarantee of a mathematically optimal encoding.
- Search candidates always start from source pixels, avoiding repeated JPEG-generation damage. No format changes or content removal are used to meet a target.
- **Target not met** means that the smallest attempted result still exceeds the limit. It remains downloadable, clearly marked. Raising the limit or allowing smaller dimensions may help.
- Width/height are hard output bounds that preserve proportions. Image pixels are never enlarged. Minimum dimensions stop additional target-driven resizing; they do not enlarge a smaller original or override explicit maximum dimensions.
- PDF DPI controls actual rasterization. Changing metadata alone does not resize pixels. The default presets are Small (150 DPI/70), Balanced (200/85), Print (300/95), and Lossless (200 DPI, PNG/lossless page images).
- For images-to-PDF, **Keep original image data** avoids re-encoding compatible JPEG/PNG files when no resizing, rotation, color change or size target is requested. In that case the quality slider does not alter the embedded pixels. JPEG EXIF/XMP/comments are removed without recompressing the JPEG scan when metadata removal is enabled. Images requiring orientation correction are normalized.
- Original-proportion image PDF pages use the selected DPI to set their physical size; at the default 200 DPI, 200 source pixels occupy one inch. Target-driven downsampling keeps that paper size unchanged. A4/Letter use automatic portrait/landscape and fit the entire image without cropping.
- **Maximum embedded image size** is an advanced cap on each encoded raster page image before PDF overhead. It is not an exact byte size of a page in a combined PDF. For an exact per-page PDF limit, enable separate PDF output and set the per-output target.

### Existing PDF compression

**Preserve content** recompresses page content streams and deduplicates PDF objects. It preserves searchable text, forms, links and vector content for whole-document conversion. Subsetting/reordering can affect document-level navigation and form references; inspect the output before relying on those. It does not downsample images, and an already optimized file may not become smaller. A hard target is measured but cannot always be met in this mode. Its metadata switch removes document metadata, not metadata inside existing embedded images.

**Rasterize pages** makes an image-only PDF. It removes searchable text, links, editable forms, bookmarks and vector detail. Its quality, DPI, resizing and page-image budgets are adjustable. The original physical page sizes are preserved.

PDF modifications invalidate digital signatures. Original files are never overwritten. Passwords are used only for the upload/job in memory and are not saved to disk or logged. Converted outputs are not password-protected. PDFium can render password-protected PDFs; structural compression of AES-encrypted PDFs additionally requires pypdf's optional crypto dependency. Unsupported encryption fails without overwriting the source.

### Preview and download

**Preview / 미리보기** produces a real conversion of the first selected page with the current settings. It is a sample, not an estimate or proof that a complete PDF will meet its size target. The preview image is capped at 2400 pixels; use the download for full-resolution verification. Results include actual bytes, applied quality/resolution, target status and individual/ZIP downloads. **Delete now** removes the displayed job's results; **Clear all** removes current-tool inputs when they are not in use.

## Storage, limits and configuration

Inputs and outputs live in `.runtime/`, or `CONVERTER_DATA_DIR`. Files/results are temporary, browser-session protected, and expire after one hour by default. Restarting the server invalidates sessions and job status; the next cleanup removes abandoned files after the same retention interval. Refreshing the UI clears its file list. Downloaded files remain on your device.

The service runs one ASGI server process and one conversion job at a time. Native PDF/image processing occurs in disposable spawned subprocesses, making cancellation and deadlines work on Windows and Linux. Up to eight jobs can be queued. Do not launch multiple Uvicorn workers or multiple replicas against the same data directory.

| Environment variable | Default | Meaning |
|---|---|---|
| `CONVERTER_HOST` | `127.0.0.1` | Server bind address; container uses `0.0.0.0` internally |
| `CONVERTER_PORT` | `8765` | HTTP port |
| `CONVERTER_DATA_DIR` | `.runtime` beside the app | Dedicated temporary storage |
| `CONVERTER_ALLOWED_HOSTS` | `127.0.0.1,localhost,[::1]` | Comma-separated accepted hostnames, without schemes/ports |
| `CONVERTER_PUBLIC_ORIGINS` | empty | Additional exact origins, e.g. your HTTPS proxy origin |
| `CONVERTER_SECURE_COOKIE` | `0` | Set to `1` when serving over HTTPS |
| `CONVERTER_MAX_FILE_MB` | `100` | Maximum bytes per uploaded file, in decimal MB |
| `CONVERTER_MAX_BATCH_MB` | `250` | Maximum HTTP request body, including multipart overhead |
| `CONVERTER_MAX_PAGES` | `500` | Maximum input pages and selected pages per job |
| `CONVERTER_MAX_PIXELS` | `60000000` | Maximum decoded input or rendered page pixels |
| `CONVERTER_MAX_OUTPUT_MB` | `500` | Aggregate output safety budget per job |
| `CONVERTER_JOB_TIMEOUT` | `600` | Conversion deadline in seconds; inspection is capped at 60 seconds |
| `CONVERTER_RETENTION_SECONDS` | `3600` | Temporary-file retention |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted proxy addresses for Uvicorn forwarding headers |

The private-access proxy is the authentication boundary. Signed HttpOnly/SameSite browser-session cookies separate uploaded files and jobs. Same-origin checks protect mutation requests; Host validation and local assets avoid external dependencies. Limits and subprocesses protect ordinary personal use; this is not a sandbox for hostile public uploads.

## Code and tests

- `converter/engine.py`: codecs, PDF rendering/assembly and size search.
- `converter/server.py`: routes, session ownership, queue, limits and cleanup.
- `converter/worker.py`: cross-platform process lifecycle, deadlines and cancellation.
- `converter/models.py`: strict JSON input validation using standard-library dataclasses.
- `static/`: browser UI without a frontend build step or CDN.

The API provides `/api/ready`, file upload/thumbnail/delete routes and job submit/status/cancel/delete/download/preview/ZIP routes. Mutations need the browser session established by opening `/` first. Job submission takes `{selections: [{file_id, page, rotation}], options: {...}, preview: false}`; page indices are zero-based.

Development setup and tests:

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
node --test tests/i18n.test.cjs tests/ui-settings.test.cjs
```

On Linux use `.venv/bin/python -m pip install -r requirements-dev.txt` and `.venv/bin/python -m pytest -q`. Node.js 18+ is needed only for the JavaScript tests, not to run the app. Tests use generated files, not personal documents. They cover real codec outputs, PDF preservation/rasterization, target feasibility, EXIF/alpha/multipage inputs, protected downloads, cancellation, deadlines and cleanup.

The server uses Starlette, pypdf, and standard-library dataclass validation. img2pdf explicitly uses its internal PDF engine on both platforms; its optional pikepdf dependency may still be installed transitively.

OCR, animation export, RAW/SVG conversion and public-service accounts are outside this version. TIFF/HEIF/AVIF variants with unusual color depths or profiles can be normalized to 8-bit RGB for conversion; this is not a color-managed prepress or scientific-imaging tool.

## Verification

- Windows / Python 3.14: 42 Python tests and 12 JavaScript tests passed before publishing.
- Ubuntu 24.04 through WSL / Python 3.12: 42 Python tests passed.
- Browser checks covered Korean/English UI, desktop/mobile layouts, quality/lossless/custom transitions, collapsed dimension settings, and real sample conversions with measured byte targets.
- JavaScript syntax checks passed. Tests generate their own files and use temporary server ports/data directories.
- Docker image build and Compose startup have not been verified in the publishing environment. AppDash registration and remote deployment are separate setup steps.
