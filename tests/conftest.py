import random

import pytest
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas


@pytest.fixture
def image_path(tmp_path):
    image = Image.new("RGB", (640, 480), "#f4f5ef")
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 30, 615, 450), outline="#315b51", width=8)
    draw.text((55, 70), "PDF / IMG - Conversion fixture", fill="#263b36")
    draw.rectangle((55, 130, 280, 370), fill="#709e8a")
    draw.ellipse((320, 130, 550, 370), fill="#d9aa69")
    path = tmp_path / "sample.png"
    image.save(path)
    return path


@pytest.fixture
def noise_path(tmp_path):
    path = tmp_path / "photo.png"
    Image.frombytes("RGB", (640, 480), random.Random(42).randbytes(640 * 480 * 3)).save(path)
    return path


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "document.pdf"
    document = canvas.Canvas(str(path), pagesize=(360, 480), pageCompression=0)
    document.setTitle("Private test document")
    document.setFillColorRGB(.16, .35, .29)
    document.setFont("Helvetica-Bold", 22)
    document.drawString(32, 418, "A clearer document.")
    document.setFont("Helvetica", 12)
    document.drawString(32, 383, "Searchable text and vector content.")
    document.setFillColorRGB(.85, .92, .87)
    document.roundRect(32, 170, 296, 175, 12, fill=1, stroke=0)
    document.setFillColorRGB(.16, .35, .29)
    document.drawString(50, 300, "PDF to image conversion")
    document.acroForm.textfield(name="reference", value="Retain this form", x=32, y=100, width=230, height=24)
    document.linkURL("https://example.com", (32, 50, 260, 75))
    document.drawString(32, 57, "A live link in the source PDF")
    document.showPage()
    document.setFont("Helvetica", 18)
    document.drawString(32, 420, "Second page")
    document.showPage()
    document.save()
    return path


def item(path, kind="image", page=0, file_id="a" * 32):
    return {"file_id": file_id, "path": str(path), "name": path.name, "kind": kind, "pages": 2 if kind == "pdf" else 1, "page": page, "rotation": 0}


def convert(tmp_path, items, **options):
    from converter.engine import conversion
    directory = tmp_path / "results"
    directory.mkdir(exist_ok=True)
    result = conversion({"directory": str(directory), "items": items, "options": {"operation": "image_convert", **options}, "pixel_limit": 40000000}, lambda event: None)
    return result, directory
