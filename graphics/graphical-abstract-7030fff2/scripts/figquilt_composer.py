#!/usr/bin/env python3
"""Zero-fitz mm-precision multi-panel SVG & draw.io composer (rewritten cleanly from yy/figquilt, MIT)."""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as _StdET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from units import calculate_fit, to_pt

try:
    import defusedxml.ElementTree as _SafeET
except ImportError:
    _SafeET = None

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_StdET.register_namespace("", SVG_NS)
_StdET.register_namespace("xlink", XLINK_NS)

_UNIT_TO_PT = {"": 72.0 / 96.0, "px": 72.0 / 96.0, "pt": 1.0, "pc": 12.0, "in": 72.0, "cm": 72.0 / 2.54, "mm": 72.0 / 25.4}
_DIM_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-Z]*)\s*$")
_FORBIDDEN_XML = re.compile(r"<!(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)\b", re.IGNORECASE)


def _safe_parse_xml(path: Path) -> _StdET.Element:
    raw = path.read_bytes()
    if _SafeET is not None:
        return _SafeET.fromstring(raw)
    if _FORBIDDEN_XML.search(raw[:32768].decode("utf-8", errors="ignore")):
        raise ValueError(f"Refused XML with DOCTYPE/ENTITY in {path}")
    return _StdET.fromstring(raw)


def _parse_len_pt(val: str | None) -> float | None:
    if not val:
        return None
    m = _DIM_RE.match(val)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2).lower()
    if unit not in _UNIT_TO_PT or num <= 0:
        return None
    return num * _UNIT_TO_PT[unit]


def measure_panel_source(path: str | Path) -> tuple[float, float]:
    """Measure source width and height in points without PyMuPDF (SVG / PNG / JPEG / PDF)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".svg":
        root = _safe_parse_xml(p)
        vb = (root.get("viewBox") or "").replace(",", " ").split()
        w_pt = _parse_len_pt(root.get("width"))
        h_pt = _parse_len_pt(root.get("height"))
        if w_pt and h_pt:
            return w_pt, h_pt
        if len(vb) == 4:
            vw, vh = float(vb[2]), float(vb[3])
            if vw > 0 and vh > 0:
                return vw * (72.0 / 96.0), vh * (72.0 / 96.0)
        raise ValueError(f"SVG panel lacks positive width/height or viewBox: {p}")
    if ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}:
        from PIL import Image
        with Image.open(p) as im:
            w, h = im.size
            dpi = im.info.get("dpi", (96.0, 96.0))
            dx = float(dpi[0]) if isinstance(dpi, tuple) and dpi[0] > 0 else 96.0
            dy = float(dpi[1]) if isinstance(dpi, tuple) and dpi[1] > 0 else 96.0
            return w * 72.0 / dx, h * 72.0 / dy
    if ext == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(p) as pdf:
                page = pdf.pages[0]
                return float(page.width), float(page.height)
        except ImportError:
            import pypdf
            reader = pypdf.PdfReader(str(p))
            mb = reader.pages[0].mediabox
            return float(mb.width), float(mb.height)
    raise ValueError(f"Unsupported panel format {ext!r}: {p}")


@dataclass
class PanelSpec:
    panel_id: str
    file: Path
    label: str = ""
    col_span: int = 1
    row_span: int = 1
    row: int | None = None
    col: int | None = None
    x_mm: float | None = None
    y_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    fit: str = "contain"
    align: str = "center"


def _prefix_svg_ids(elem: _StdET.Element, prefix: str) -> None:
    """Namespace local defs/id/url(#...) references so multiple inline SVGs never collide."""
    id_map: dict[str, str] = {}
    for node in elem.iter():
        old_id = node.get("id")
        if old_id:
            new_id = f"{prefix}_{old_id}"
            id_map[old_id] = new_id
            node.set("id", new_id)
    if not id_map:
        return
    url_re = re.compile(r"url\(#([^)]+)\)")
    for node in elem.iter():
        for k, v in list(node.attrib.items()):
            if k.rsplit("}", 1)[-1] == "href" and v.startswith("#") and v[1:] in id_map:
                node.set(k, "#" + id_map[v[1:]])
            elif "url(#" in v:
                node.set(k, url_re.sub(lambda m: f"url(#{id_map[m.group(1)]})" if m.group(1) in id_map else m.group(0), v))


def compose_svg_grid(
    panels: list[PanelSpec],
    output_svg: str | Path,
    *,
    width_mm: float = 174.0,
    height_mm: float = 95.0,
    margin_mm: float = 4.0,
    gap_mm: float = 4.0,
    cols: int = 2,
    col_ratios: list[float] | None = None,
    row_ratios: list[float] | None = None,
    label_font_pt: float = 9.0,
    background: str = "#FFFFFF",
) -> dict[str, Any]:
    """Compose panels into a single layered vector SVG preserving child vector editability."""
    w_pt = to_pt(width_mm, "mm")
    h_pt = to_pt(height_mm, "mm")
    m_pt = to_pt(margin_mm, "mm")
    g_pt = to_pt(gap_mm, "mm")
    inner_w = w_pt - 2 * m_pt
    inner_h = h_pt - 2 * m_pt
    if inner_w <= 0 or inner_h <= 0:
        raise ValueError("Page content area non-positive; reduce margin_mm")

    n_rows = max(1, (len(panels) + cols - 1) // cols)
    c_rat = col_ratios if col_ratios and len(col_ratios) == cols else [1.0] * cols
    r_rat = row_ratios if row_ratios and len(row_ratios) == n_rows else [1.0] * n_rows
    c_sum = sum(c_rat)
    r_sum = sum(r_rat)
    avail_w = inner_w - g_pt * max(0, cols - 1)
    avail_h = inner_h - g_pt * max(0, n_rows - 1)
    col_widths = [avail_w * (r / c_sum) for r in c_rat]
    row_heights = [avail_h * (r / r_sum) for r in r_rat]

    root = _StdET.Element(f"{{{SVG_NS}}}svg", {
        "width": f"{width_mm}mm",
        "height": f"{height_mm}mm",
        "viewBox": f"0 0 {w_pt:.2f} {h_pt:.2f}",
        "version": "1.1",
    })
    layer_bg = _StdET.SubElement(root, f"{{{SVG_NS}}}g", {"id": "layer-background", "data-layer-name": "background"})
    _StdET.SubElement(layer_bg, f"{{{SVG_NS}}}rect", {
        "x": "0", "y": "0", "width": f"{w_pt:.2f}", "height": f"{h_pt:.2f}", "fill": background,
    })

    placed: list[dict[str, Any]] = []
    for idx, ps in enumerate(panels):
        r_idx = ps.row if ps.row is not None else (idx // cols)
        c_idx = ps.col if ps.col is not None else (idx % cols)
        if ps.x_mm is not None and ps.y_mm is not None and ps.width_mm is not None and ps.height_mm is not None:
            cx = to_pt(ps.x_mm, "mm")
            cy = to_pt(ps.y_mm, "mm")
            cw = to_pt(ps.width_mm, "mm")
            ch = to_pt(ps.height_mm, "mm")
        else:
            cx = m_pt + sum(col_widths[:c_idx]) + g_pt * c_idx
            cy = m_pt + sum(row_heights[:r_idx]) + g_pt * r_idx
            cw = sum(col_widths[c_idx:c_idx + ps.col_span]) + g_pt * max(0, ps.col_span - 1)
            ch = sum(row_heights[r_idx:r_idx + ps.row_span]) + g_pt * max(0, ps.row_span - 1)

        src_w, src_h = measure_panel_source(ps.file)
        fit = calculate_fit(src_h / src_w, cw, ch, ps.fit, ps.align)

        layer = _StdET.SubElement(root, f"{{{SVG_NS}}}g", {
            "id": f"layer-panel-{ps.panel_id}",
            "data-layer-name": f"panel-{ps.panel_id}",
        })
        if ps.file.suffix.lower() == ".svg":
            child_root = _safe_parse_xml(ps.file)
            _prefix_svg_ids(child_root, f"p{idx}_{ps.panel_id}")
            vb = (child_root.get("viewBox") or "").replace(",", " ").split()
            vb_x, vb_y = (float(vb[0]), float(vb[1])) if len(vb) == 4 else (0.0, 0.0)
            vb_w, vb_h = (float(vb[2]), float(vb[3])) if len(vb) == 4 else (src_w, src_h)
            sx = fit.width / max(vb_w, 1e-6)
            sy = fit.height / max(vb_h, 1e-6)
            tx = cx + fit.offset_x - vb_x * sx
            ty = cy + fit.offset_y - vb_y * sy
            inner_g = _StdET.SubElement(layer, f"{{{SVG_NS}}}g", {
                "id": f"panel-content-{ps.panel_id}",
                "transform": f"translate({tx:.3f},{ty:.3f}) scale({sx:.5f},{sy:.5f})",
            })
            for sub in list(child_root):
                inner_g.append(copy.deepcopy(sub))
        else:
            import base64
            mime = "image/png" if ps.file.suffix.lower() == ".png" else "image/jpeg"
            b64 = base64.b64encode(ps.file.read_bytes()).decode("ascii")
            _StdET.SubElement(layer, f"{{{SVG_NS}}}image", {
                "x": f"{cx + fit.offset_x:.2f}",
                "y": f"{cy + fit.offset_y:.2f}",
                "width": f"{fit.width:.2f}",
                "height": f"{fit.height:.2f}",
                f"{{{XLINK_NS}}}href": f"data:{mime};base64,{b64}",
            })

        lbl = ps.label if ps.label else chr(ord("A") + idx)
        txt = _StdET.SubElement(layer, f"{{{SVG_NS}}}text", {
            "id": f"panel-label-{ps.panel_id}",
            "x": f"{cx + 2.0:.2f}",
            "y": f"{cy + label_font_pt + 1.0:.2f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": f"{label_font_pt:.1f}",
            "font-weight": "bold",
            "fill": "#1A1A1A",
        })
        txt.text = lbl
        placed.append({
            "panel_id": ps.panel_id,
            "label": lbl,
            "source": str(ps.file.name),
            "cell_pt": [round(cx, 2), round(cy, 2), round(cw, 2), round(ch, 2)],
            "content_pt": [round(cx + fit.offset_x, 2), round(cy + fit.offset_y, 2), round(fit.width, 2), round(fit.height, 2)],
            "vector_inlined": ps.file.suffix.lower() == ".svg",
        })

    out_p = Path(output_svg)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    _StdET.indent(root, space="  ")
    out_p.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))
    return {"output_svg": str(out_p), "width_mm": width_mm, "height_mm": height_mm, "panels": placed}
