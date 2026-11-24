# Third-Party Notices

This package incorporates and adapts clean MIT / Apache-2.0 / CC0 components from individual open-source authors:

1. **svg-to-drawio** (`V1rg1lee/svg-to-drawio`, MIT License) — pure-Python converter from layered SVG to editable draw.io (`mxCell`) XML, adapted with `defusedxml`-first + safe stdlib `xml.etree.ElementTree` fallback.
2. **drawio-skill** (`Adcage/drawio-skill`, MIT License) — `layout_tools.py` and `drawio_layout_check.py` for overlap and canvas boundary checks.
3. **nature-figure-editable** (`l2461090/nature-figure-editable`, MIT License) — `audit_editability.py` for standalone SVG editability auditing (only MIT Python checker script included; no external figure assets included).
4. **reconstruct-editable-figure** (`deathcats4/reconstruct-editable-figure`, MIT License) — `_portable_svg.py` portability logic adapted into `portable_svg_audit.py` without native Cairo dependencies.
5. **OpenTikZ** (`opentikz/opentikz`, MIT License / CC0 1.0) — standalone TikZ design rules, `edit_contract` schema pattern, and contract validator logic adapted into `validate_tikz_contract.py` and `assets/route_template.*`.

Every third-party snippet obeys permissive open-source terms and omits forbidden AGPL/CC-BY-NC/proprietary binary dependencies.

v3 adds an opt-in data-drawio-cell-id handoff on builder-owned rect and open-path elements, using the converter's existing explicit cell_id parameters. SVG elements without this attribute retain the converter's normal generated IDs.
