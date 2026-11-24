# Third-Party Notices

This package incorporates and adapts clean MIT / Apache-2.0 open-source components:

1. **figquilt** (`yy/figquilt`, MIT License) — millimeter/point multi-panel grid composition and unit conversion (`units.py`, `figquilt_composer.py`), refactored cleanly with zero PyMuPDF/`fitz`, `pydantic`, or `lxml` dependencies and without any doc symlinks.
2. **svg-to-drawio** (`V1rg1lee/svg-to-drawio`, MIT License) — pure-Python converter from layered SVG to editable draw.io (`mxCell`) XML, with `defusedxml`-first + safe stdlib `xml.etree.ElementTree` fallback.
3. **drawio-skill** (`Adcage/drawio-skill`, MIT License) — `layout_tools.py` and `drawio_layout_check.py`.
4. **nature-figure-editable** (`l2461090/nature-figure-editable`, MIT License) — `audit_editability.py` standalone SVG checker (no external CC-BY-NC figure datasets included).
5. **reconstruct-editable-figure** (`deathcats4/reconstruct-editable-figure`, MIT License) — portable SVG structural audit logic without Cairo dependencies.
6. **evident-charts** (`rhiever/evident-charts`, MIT License) — `check_palette.py` and `palettes.json` WCAG/CVD contrast verification.

All included components satisfy permissive licensing and exclude banned libraries (`fitz`, `cairosvg`, `pywin32`, `CC-BY-SA`/`CC-BY-NC` assets).
