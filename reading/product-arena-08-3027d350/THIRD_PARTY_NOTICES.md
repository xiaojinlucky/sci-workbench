# Third-Party Notices

This skill package contains original offline Python 3.12 standard-library scripts and Markdown documentation authored for scientific document parsing (`product-arena-08-3027d350`).

## Optional Preinstalled Runtime Dependencies (Not Bundled)

When present in the host container, `scripts/doc_parser_engine.py` invokes the following external tools or libraries via standard interfaces without bundling their source code or binaries:

- **Poppler (`pdftotext`, `pdfinfo`, `pdftoppm`)** — invoked as external CLI subprocesses when available in `PATH`.
- **Tesseract OCR (`tesseract`)** — invoked per-page as an external CLI subprocess when available in `PATH` with installed language data.
- **pdfplumber / pypdf / Pillow** — optional Python packages imported opportunistically with clean fallback when absent.

No AGPL/LGPL or prohibited PDF/SVG/Windows-COM libraries are bundled or imported.
