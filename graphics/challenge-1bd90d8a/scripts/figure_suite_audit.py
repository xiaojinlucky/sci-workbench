#!/usr/bin/env python3
"""Offline Cell figure-suite auditor: mm dimension lock, PNG edge-clipping scan, backend-word leak, legend/typography & cross-panel consistency."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as _StdET
from pathlib import Path
from typing import Any

try:
    import defusedxml.ElementTree as _SafeET
except ImportError:
    _SafeET = None

from audit_editability import inspect_svg_only

_BACKEND_LEAK_PATTERNS = [
    (re.compile(r"\bQC[_\s-][A-Za-z0-9_]+\b"), "backend QC prefix"),
    (re.compile(r"\bpost[_\s-]?basic\b", re.I), "backend pipeline stage word"),
    (re.compile(r"\(in-sample\)", re.I), "non-standard cohort qualifier '(in-sample)'"),
    (re.compile(r"\bpreselector\b", re.I), "internal ML selector word"),
    (re.compile(r"#[0-9A-Fa-f]{6}\b"), "raw hex color code leaked into visible figure text"),
    (re.compile(r"\b[A-Za-z0-9_-]+\.(?:py|R|ipynb)\b"), "script filename leaked into visible figure text"),
]

_FONT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")


def _safe_parse_xml(path: Path) -> _StdET.Element:
    raw = path.read_bytes()
    if _SafeET is not None:
        return _SafeET.fromstring(raw)
    return _StdET.fromstring(raw)


def scan_png_edge_clipping(png_path: Path, *, border_px: int = 2, ink_thresh: int = 240) -> dict[str, Any]:
    """Detect non-background ink touching the outer PNG border (catches Matplotlib #32642 Agg edge clipping)."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return {"checked": False, "status": "unverified", "reason": "PIL/numpy unavailable"}

    with Image.open(png_path) as im:
        rgb = im.convert("RGB")
        arr = np.asarray(rgb)
        dpi = im.info.get("dpi", (300.0, 300.0))
        dx = float(dpi[0]) if isinstance(dpi, tuple) and dpi[0] > 0 else 300.0
        dy = float(dpi[1]) if isinstance(dpi, tuple) and dpi[1] > 0 else 300.0
        w_mm = round(arr.shape[1] * 25.4 / dx, 2)
        h_mm = round(arr.shape[0] * 25.4 / dy, 2)

    # Ink pixel = any RGB channel clearly below white background
    is_ink = (arr[:, :, 0] < ink_thresh) | (arr[:, :, 1] < ink_thresh) | (arr[:, :, 2] < ink_thresh)
    edges = {
        "top": int(is_ink[:border_px, :].sum()),
        "bottom": int(is_ink[-border_px:, :].sum()),
        "left": int(is_ink[:, :border_px].sum()),
        "right": int(is_ink[:, -border_px:].sum()),
    }
    clipped_edges = [k for k, v in edges.items() if v > 0]
    return {
        "checked": True,
        "status": "fail" if clipped_edges else "pass",
        "pixel_size": [int(arr.shape[1]), int(arr.shape[0])],
        "physical_mm": [w_mm, h_mm],
        "edge_ink_pixels": edges,
        "clipped_edges": clipped_edges,
    }


def scan_drawio_native_editability(drawio_path: Path) -> dict[str, Any]:
    """Count native editable Draw.io shapes/edges/texts vs embedded SVG fallback images."""
    if not drawio_path.is_file():
        return {"checked": False}
    root = _safe_parse_xml(drawio_path)
    native_shapes = 0
    native_edges = 0
    native_texts = 0
    embedded_svg = 0
    for cell in root.findall(".//mxCell"):
        style = cell.get("style") or ""
        if "image=data:image/svg+xml" in style:
            embedded_svg += 1
        elif cell.get("edge") == "1":
            native_edges += 1
        elif cell.get("vertex") == "1" and cell.find("mxGeometry") is not None:
            if style.startswith("text;") or ";text;" in style:
                native_texts += 1
            else:
                native_shapes += 1
    total_fg = native_shapes + native_edges + native_texts + embedded_svg
    return {
        "checked": True,
        "native_shapes": native_shapes,
        "native_edges": native_edges,
        "native_texts": native_texts,
        "embedded_svg_fallbacks": embedded_svg,
        "native_editable_ratio": round((native_shapes + native_edges + native_texts) / max(1, total_fg), 4),
    }


def audit_single_chart(
    svg_path: Path,
    *,
    png_path: Path | None = None,
    drawio_path: Path | None = None,
    provenance_path: Path | None = None,
    declared_width_mm: float | None = None,
    declared_height_mm: float | None = None,
    min_font_pt: float = 5.5,
) -> dict[str, Any]:
    """Run Cell publication audit on a single exported chart package."""
    blockers: list[str] = []
    majors: list[str] = []
    minors: list[str] = []

    if not svg_path.is_file():
        return {"status": "fail", "blockers": [f"Missing SVG: {svg_path}"], "majors": [], "minors": []}

    edit = inspect_svg_only(svg_path)
    if edit.get("status") != "pass":
        blockers.extend(edit.get("problems") or [])

    w_in, h_in = edit["svg"]["dimensions_inches"]
    svg_w_mm = round(float(w_in) * 25.4, 2)
    svg_h_mm = round(float(h_in) * 25.4, 2)
    if declared_width_mm is not None and abs(svg_w_mm - declared_width_mm) > 1.5:
        blockers.append(
            f"PHYSICAL_DIM_DRIFT: SVG width {svg_w_mm}mm differs from declared {declared_width_mm}mm (check bbox_inches='tight')"
        )
    if declared_height_mm is not None and abs(svg_h_mm - declared_height_mm) > 1.5:
        blockers.append(
            f"PHYSICAL_DIM_DRIFT: SVG height {svg_h_mm}mm differs from declared {declared_height_mm}mm"
        )

    # Scan visible SVG text for backend leaks & tiny fonts
    root = _safe_parse_xml(svg_path)
    leaks: list[dict[str, str]] = []
    tiny_fonts: list[dict[str, Any]] = []
    visible_texts: list[str] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "text":
            continue
        txt = "".join(el.itertext()).strip()
        if not txt:
            continue
        visible_texts.append(txt)
        for pat, desc in _BACKEND_LEAK_PATTERNS:
            if pat.search(txt):
                leaks.append({"text": txt[:80], "reason": desc})
        st = el.get("style") or ""
        fs_raw = el.get("font-size") or ""
        if not fs_raw and "font-size:" in st:
            m_st = re.search(r"font-size\s*:\s*([0-9.]+)", st)
            fs_raw = m_st.group(1) if m_st else ""
        m_f = _FONT_RE.search(fs_raw)
        if m_f and float(m_f.group(1)) < min_font_pt:
            tiny_fonts.append({"text": txt[:40], "font_pt": float(m_f.group(1))})

    if leaks:
        blockers.append(f"BACKEND_VOCAB_LEAK: {leaks[:5]}")
    if tiny_fonts:
        majors.append(f"TINY_FONT_BELOW_{min_font_pt}PT: {tiny_fonts[:5]}")

    png_rep: dict[str, Any] = {"checked": False}
    if png_path is not None and png_path.is_file():
        png_rep = scan_png_edge_clipping(png_path)
        if png_rep.get("status") == "fail":
            blockers.append(f"PNG_EDGE_CLIPPING: non-white ink touches outer canvas edges {png_rep['clipped_edges']}")

    drawio_rep: dict[str, Any] = {"checked": False}
    if drawio_path is not None and drawio_path.is_file():
        drawio_rep = scan_drawio_native_editability(drawio_path)

    prov_rep: dict[str, Any] = {"checked": False}
    if provenance_path is not None and provenance_path.is_file():
        prov = json.loads(provenance_path.read_text(encoding="utf-8-sig"))
        leg = prov.get("legend_audit") or {}
        if leg.get("placed") and leg.get("legend_clipped"):
            blockers.append("LEGEND_CLIPPED: bottom legend clipped outside canvas bounds")
        if leg.get("placed") and leg.get("gap_in_2_to_6pt_range") is False:
            minors.append(f"LEGEND_GAP_OUT_OF_2_6PT: measured={leg.get('measured_gap_pt')}pt")
        prov_rep = {
            "checked": True,
            "source_table": prov.get("source_table"),
            "physical_dimension_locked": prov.get("physical_dimension_locked"),
            "named_colors": prov.get("named_colors") or {},
        }

    status = "fail" if blockers else ("warn" if majors else "pass")
    return {
        "status": status,
        "svg_physical_mm": [svg_w_mm, svg_h_mm],
        "visible_text_count": len(visible_texts),
        "backend_leaks": leaks,
        "tiny_fonts": tiny_fonts,
        "png_edge_audit": png_rep,
        "drawio_editability": drawio_rep,
        "provenance_summary": prov_rep,
        "blockers": blockers,
        "majors": majors,
        "minors": minors,
    }


def audit_suite_consistency(provenance_paths: list[Path]) -> dict[str, Any]:
    """Check cross-panel named color identity consistency across a multi-panel figure suite."""
    seen: dict[str, dict[str, str]] = {}
    conflicts: list[dict[str, Any]] = []
    for pp in provenance_paths:
        if not pp.is_file():
            continue
        prov = json.loads(pp.read_text(encoding="utf-8-sig"))
        cmap = prov.get("named_colors") or {}
        for entity, color in cmap.items():
            c_norm = str(color).upper()
            if entity in seen and seen[entity]["color"] != c_norm:
                conflicts.append({
                    "entity": entity,
                    "first_file": seen[entity]["file"],
                    "first_color": seen[entity]["color"],
                    "conflict_file": pp.name,
                    "conflict_color": c_norm,
                })
            else:
                seen[entity] = {"color": c_norm, "file": pp.name}
    return {
        "passed": len(conflicts) == 0,
        "panels_checked": len(provenance_paths),
        "unique_entities": len(seen),
        "color_conflicts": conflicts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit single chart or multi-panel figure suite (Cell/audit-figure-suite offline gate).")
    parser.add_argument("--svg", default="")
    parser.add_argument("--png", default="")
    parser.add_argument("--drawio", default="")
    parser.add_argument("--provenance", default="")
    parser.add_argument("--declared-width-mm", type=float, default=None)
    parser.add_argument("--declared-height-mm", type=float, default=None)
    parser.add_argument("--suite-provenance", nargs="*", default=[], help="List of *_provenance.json files for cross-panel consistency audit")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    report: dict[str, Any] = {}
    ok = True
    if args.svg:
        single = audit_single_chart(
            Path(args.svg),
            png_path=Path(args.png) if args.png else None,
            drawio_path=Path(args.drawio) if args.drawio else None,
            provenance_path=Path(args.provenance) if args.provenance else None,
            declared_width_mm=args.declared_width_mm,
            declared_height_mm=args.declared_height_mm,
        )
        report["single_chart"] = single
        if single["status"] == "fail":
            ok = False
    if args.suite_provenance:
        suite = audit_suite_consistency([Path(p) for p in args.suite_provenance])
        report["suite_consistency"] = suite
        if not suite["passed"]:
            ok = False

    report["ok"] = ok
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
