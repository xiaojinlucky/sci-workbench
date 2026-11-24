# Third-Party Notices

This Skill package incorporates cleanly adapted, license-compatible open-source modules. Only permissive MIT and Apache-2.0 modules are included; copyleft, Windows-COM, and non-commercial third-party assets are omitted.

1. **V1rg1lee/svg-to-drawio** (MIT License, Copyright (c) 2026 Virgile Devolder)
   - Adapted into `scripts/svg2drawio/` (`__init__.py`, `xml_safe.py`, `geom_core.py`, `model_styles.py`, `emit_core.py`, `converter.py`).
   - Modifications: consolidated core modules for compact packaging, added `xml_safe.py` with hardened standard-library `xml.etree.ElementTree` fallback when `defusedxml` is absent, added `<g id="layer-*">` draw.io layer mapping, and mapped biological inhibition/activation markers (`arrow-inhibit` -> `dash`, `arrow-activate` -> `classic`, `arrow-bind` -> `oval`).

2. **Adcage/drawio-skill** (MIT License, Copyright (c) 2025 Adcage)
   - Adapted into `scripts/layout_tools.py` and `scripts/drawio_layout_check.py`.
   - Modifications: added safe XML parsing with `defusedxml`-optional stdlib fallback and separated background/compartment layers from sibling node collision checks.

3. **l2461090/nature-figure-editable** (Apache License 2.0)
   - Adapted `scripts/audit_editability.py` (structural SVG/PPTX editability audit only; `assets/figures4papers/` excluded completely).
   - Modifications: added `--svg` standalone mode (`inspect_svg_only`) when no PPTX is paired.

4. **deathcats4/reconstruct-editable-figure** (MIT License, Copyright (c) 2026 deathcats4)
   - Adapted structural SVG security/editability verification into `scripts/portable_svg_audit.py`.
   - Modifications: removed all external raster dependencies, kept pure standard-library XML structural inspection, and distinguished native draw.io `<marker>` portability warnings from hard security/non-editable blockers.
