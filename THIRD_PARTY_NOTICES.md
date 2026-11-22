# Third-party components

Dependencies are installed separately by pip/uv. Their complete license and copyright notices remain in their installed wheel distributions, including native-code notices. Keep those installed notices when distributing a container or runtime; do not strip `.dist-info/licenses` or PDFium's bundled license files.

| Component | License / notice source |
|---|---|
| Starlette, Uvicorn | BSD-3-Clause; installed package notices |
| python-multipart | Apache-2.0; installed package notices |
| Pillow | MIT-CMU / HPND and bundled codec notices |
| pypdfium2 | Apache-2.0 / BSD-3-Clause; PDFium BSD-style and bundled third-party notices |
| img2pdf | LGPL-3.0; installed package license and upstream source at https://gitlab.mister-muffin.de/josch/img2pdf |
| pypdf | BSD-3-Clause; installed package notices |
| pillow-heif | BSD-3-Clause; bundled libheif/codec licenses also apply |
| pikepdf, installed transitively by img2pdf | MPL-2.0; QPDF and other bundled notices remain in the installed wheel |

The application calls unmodified library APIs. img2pdf's internal PDF writer is explicitly selected. Development-only dependencies include pytest (MIT), HTTPX (BSD-3-Clause), and ReportLab (BSD-style); their licenses are included with their installed packages.
