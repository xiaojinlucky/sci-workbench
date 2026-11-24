#!/usr/bin/env python3
"""Narrative flow, source_ref claim grounding, color consistency, backend-leak, PNG edge-clip, and vector audit for Graphical Abstracts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    import defusedxml.ElementTree as _SafeET  # type: ignore
except ImportError:  # pragma: no cover
    _SafeET = None
import xml.etree.ElementTree as _StdET

from audit_editability import inspect_svg_only
from layout_tools import analyze_layout
from portable_svg_audit import audit_portable_svg

REQUIRED_STAGES = ("question", "method", "result", "conclusion")
_FONT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
_NUM_CLAIM_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|x|fold|mg|uM|µM|nM|mm|kPa|h|d)\b", re.I)

_BACKEND_LEAK_PATTERNS = [
    (re.compile(r"\bQC[_\s-][A-Za-z0-9_]+\b"), "backend QC prefix"),
    (re.compile(r"\bpost[_\s-]?basic\b", re.I), "backend pipeline stage word"),
    (re.compile(r"\(in-sample\)", re.I), "non-standard cohort qualifier"),
    (re.compile(r"\bpreselector\b", re.I), "internal ML selector word"),
    (re.compile(r"#[0-9A-Fa-f]{6}\b"), "raw hex color code in visible text"),
    (re.compile(r"\b[A-Za-z0-9_-]+\.(?:py|R|ipynb)\b"), "script filename in visible text"),
]


def _scan_png_edges(png_path: Path, border_px: int = 2, ink_thresh: int = 240) -> dict[str, Any]:
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return {"checked": False, "status": "unverified"}
    with Image.open(png_path) as im:
        arr = np.asarray(im.convert("RGB"))
    is_ink = (arr[:, :, 0] < ink_thresh) | (arr[:, :, 1] < ink_thresh) | (arr[:, :, 2] < ink_thresh)
    edges = {
        "top": int(is_ink[:border_px, :].sum()),
        "bottom": int(is_ink[-border_px:, :].sum()),
        "left": int(is_ink[:, :border_px].sum()),
        "right": int(is_ink[:, -border_px:].sum()),
    }
    clipped = [k for k, v in edges.items() if v > 0]
    return {"checked": True, "status": "fail" if clipped else "pass", "clipped_edges": clipped, "edge_ink_pixels": edges}


def audit_ga(
    spec: dict[str, Any] | None,
    svg_path: Path,
    *,
    drawio_path: Path | None = None,
    png_path: Path | None = None,
    allow_raster: bool = False,
    min_font_pt: float = 6.4,
) -> dict[str, Any]:
    """Audit Graphical Abstract 4-stage completeness, claim grounding, typography, backend leaks, and editability."""
    errors: list[str] = []
    warnings: list[str] = []

    if not svg_path.exists():
        return {"passed": False, "blocking_errors": [f"Missing SVG: {svg_path}"], "warnings": []}

    raw = svg_path.read_bytes()
    root = _SafeET.fromstring(raw) if _SafeET is not None else _StdET.fromstring(raw)

    # 1. Stage completeness, reading order, claim provenance & cross-panel color map
    stages_found: list[str] = []
    ungrounded_panels: list[str] = []
    panels = (spec or {}).get("panels") or []
    entity_colors = {str(k): str(v).upper() for k, v in ((spec or {}).get("entity_colors") or {}).items()}
    used_colors: set[str] = set()

    if panels:
        for p in panels:
            pid = str(p.get("id", "unknown"))
            st = str(p.get("stage", "")).lower().strip()
            if st:
                stages_found.append(st)
            if p.get("accent"):
                used_colors.add(str(p["accent"]).upper())
            src_ref = str(p.get("source_ref", "")).strip()
            has_quant = bool(p.get("mini_bars") or p.get("paired_dots") or p.get("metrics_strip"))
            has_num_highlight = bool(_NUM_CLAIM_RE.search(str(p.get("highlight", ""))))
            if (has_quant or has_num_highlight) and not src_ref:
                ungrounded_panels.append(pid)
            for b in p.get("mini_bars") or []:
                if b.get("color"):
                    used_colors.add(str(b["color"]).upper())
                if "unit" in b and not str(b["unit"]).strip() and float(b.get("value", 0.0)) != 0.0:
                    warnings.append(f"Panel {pid} mini_bar {b.get('label')!r} has empty unit string")
            for n in (p.get("diagram") or {}).get("nodes") or []:
                if isinstance(n, dict) and n.get("color"):
                    used_colors.add(str(n["color"]).upper())
            for n in p.get("nodes") or []:
                if isinstance(n, dict) and n.get("color"):
                    used_colors.add(str(n["color"]).upper())
            for m in p.get("metrics_strip") or []:
                if isinstance(m, dict) and m.get("color"):
                    used_colors.add(str(m["color"]).upper())
                if not str(m.get("source_ref") or src_ref).strip():
                    ungrounded_panels.append(f"{pid}:{m.get('metric')}")

        missing_stages = [s for s in REQUIRED_STAGES if s not in stages_found]
        if missing_stages:
            errors.append(f"Missing required GA narrative stage(s): {missing_stages}")
        if not missing_stages:
            pos_map = {st: idx for idx, st in enumerate(stages_found)}
            orders = [pos_map[s] for s in REQUIRED_STAGES]
            if orders != sorted(orders):
                errors.append(f"Non-monotonic narrative order detected: {stages_found}; expected Question->Method->Result->Conclusion")
        if ungrounded_panels:
            errors.append(f"UNGROUNDED_CLAIM_BLOCKER: Quantitative/numeric panels lack source_ref: {ungrounded_panels}")
        if entity_colors:
            palette_vals = set(entity_colors.values())
            unmapped = sorted(c for c in used_colors if c not in palette_vals)
            if unmapped:
                errors.append(f"UNDECLARED_LEGEND_COLOR: Colors used in panels not declared in entity_colors legend: {unmapped}")
    else:
        layer_ids = [el.get("id", "") for el in root.iter() if el.get("id", "").startswith("layer-panel-")]
        if len(layer_ids) < 3:
            warnings.append(f"Fewer than 3 composed panel layers detected in standalone SVG audit: {layer_ids}")

    # 2. Vector typography minimum readable size, empty text & backend vocabulary leak check
    tiny_texts: list[dict[str, Any]] = []
    backend_leaks: list[dict[str, str]] = []
    text_count = 0
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "text":
            txt = "".join(el.itertext()).strip()
            if not txt:
                continue
            text_count += 1
            for pat, reason in _BACKEND_LEAK_PATTERNS:
                if pat.search(txt):
                    backend_leaks.append({"id": el.get("id") or "", "text": txt[:60], "reason": reason})
            fs_raw = el.get("font-size", "")
            m = _FONT_RE.search(fs_raw)
            if m:
                fs_val = float(m.group(1))
                if fs_val < min_font_pt:
                    tiny_texts.append({"id": el.get("id"), "font_size": fs_val, "text": txt[:40]})
    if tiny_texts:
        errors.append(f"Sub-minimum font size (<{min_font_pt}pt) text elements: {tiny_texts[:5]}")
    if backend_leaks:
        errors.append(f"BACKEND_VOCAB_LEAK: {backend_leaks[:5]}")
    if text_count < 4:
        errors.append(f"Too few editable <text> elements ({text_count}); possible flattened raster text")

    # 3. Portable SVG & editability checks
    port = audit_portable_svg(svg_path, allow_embedded_raster=allow_raster)
    if not port["passed"]:
        errors.extend(port.get("blockers") or [])
    edit = inspect_svg_only(svg_path)
    if edit.get("status") != "pass":
        errors.extend(edit.get("problems") or [])

    # 4. Draw.io overlap & out-of-bounds check
    drawio_rep: dict[str, Any] = {}
    if drawio_path is not None and drawio_path.exists():
        drawio_rep = analyze_layout(drawio_path.read_text(encoding="utf-8"), min_spacing=1.0)
        if drawio_rep.get("overlaps"):
            errors.append(f"Draw.io overlaps detected: {len(drawio_rep['overlaps'])}")
        if drawio_rep.get("out_of_bounds"):
            errors.append(f"Draw.io out_of_bounds detected: {len(drawio_rep['out_of_bounds'])}")

    # 5. PNG edge-clipping check
    png_rep: dict[str, Any] = {"checked": False}
    if png_path is not None and png_path.exists():
        png_rep = _scan_png_edges(png_path)
        if png_rep.get("status") == "fail":
            errors.append(f"PNG_EDGE_CLIPPING: non-white ink touches outer edges {png_rep['clipped_edges']}")

    return {
        "passed": len(errors) == 0,
        "stages_found": stages_found,
        "editable_text_count": text_count,
        "tiny_texts": tiny_texts,
        "backend_leaks": backend_leaks,
        "ungrounded_panels": ungrounded_panels,
        "portable_svg": port,
        "editability": edit,
        "drawio_layout": drawio_rep,
        "png_edge_audit": png_rep,
        "blocking_errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Graphical Abstract narrative flow, grounding, typography, and editability.")
    parser.add_argument("--spec", default=None)
    parser.add_argument("--svg", required=True)
    parser.add_argument("--drawio", default=None)
    parser.add_argument("--png", default=None)
    parser.add_argument("--allow-raster", action="store_true")
    args = parser.parse_args(argv)

    spec_obj = json.loads(Path(args.spec).read_text(encoding="utf-8-sig")) if args.spec else None
    rep = audit_ga(
        spec_obj,
        Path(args.svg),
        drawio_path=Path(args.drawio) if args.drawio else None,
        png_path=Path(args.png) if args.png else None,
        allow_raster=args.allow_raster,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
