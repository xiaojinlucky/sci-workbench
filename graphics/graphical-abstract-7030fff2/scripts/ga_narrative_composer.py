#!/usr/bin/env python3
"""Adaptive mm-locked 4-stage vector Graphical Abstract composer (narrow-canvas reflow, typed mechanisms, error/zero mini-bars, shared legend, caption draft, Draw.io binding)."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import tempfile
import xml.etree.ElementTree as _StdET
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    import defusedxml.ElementTree as _SafeET  # type: ignore
except ImportError:  # pragma: no cover
    _SafeET = None

from contract_sealer import seal_case_result
from figquilt_composer import PanelSpec, compose_svg_grid
from ga_visual_audit import audit_ga
from render_preview import render_svg_previews
import svg2drawio
from units import to_pt

SVG_NS = "http://www.w3.org/2000/svg"
_StdET.register_namespace("", SVG_NS)


class LayoutOverflowError(ValueError):
    """Raised when text content cannot fit inside physical panel bounds at minimum readable typography."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _add_el(parent: _StdET.Element, tag: str, attrib: dict[str, str], text: str | None = None) -> _StdET.Element:
    el = _StdET.SubElement(parent, f"{{{SVG_NS}}}{tag}", attrib)
    if text is not None:
        el.text = text
    return el


def _text_width_pt(text: str, font_pt: float, *, bold: bool = False) -> float:
    w = 0.0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            w += font_pt * 1.02
        elif ch in "ilI1.,|!;: ":
            w += font_pt * 0.34
        elif ch in "MWmw@#%&":
            w += font_pt * 0.82
        elif ch.isupper():
            w += font_pt * 0.66
        else:
            w += font_pt * 0.54
    return w * (1.16 if bold else 1.0)


def _wrap_to_width(text: str, max_w_pt: float, font_pt: float, *, max_lines: int = 3, bold: bool = False) -> tuple[list[str], bool]:
    """Wrap text cleanly to fit max_w_pt without silent truncation; returns (lines, overflowed)."""
    s = " ".join(str(text).strip().split())
    if not s:
        return [], False
    if _text_width_pt(s, font_pt, bold=bold) <= max_w_pt:
        return [s], False
    words = s.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip() if cur else w
        if cur and _text_width_pt(cand, font_pt, bold=bold) > max_w_pt:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    overflowed = len(lines) > max_lines or any(_text_width_pt(ln, font_pt, bold=bold) > max_w_pt * 1.08 for ln in lines)
    return lines, overflowed


def _add_wrapped_text(
    parent: _StdET.Element,
    *,
    base_id: str,
    x: float,
    y_start: float,
    lines: list[str],
    font_pt: float,
    line_step: float,
    fill: str,
    anchor: str = "start",
    weight: str = "normal",
    prefix_first: str = "",
    prefix_rest: str = "",
) -> float:
    cy = y_start
    for idx, ln in enumerate(lines):
        pfx = prefix_first if idx == 0 else prefix_rest
        attrib = {
            "id": base_id if len(lines) == 1 else f"{base_id}-l{idx+1}",
            "x": f"{x:.2f}",
            "y": f"{cy:.2f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": f"{font_pt:.2f}",
            "fill": fill,
            "text-anchor": anchor,
        }
        if weight != "normal":
            attrib["font-weight"] = weight
        _add_el(parent, "text", attrib, f"{pfx}{ln}")
        cy += line_step
    return cy


def _render_subpanel_svg(
    panel: dict[str, Any],
    panel_w_mm: float,
    panel_h_mm: float,
    out_svg: Path,
    *,
    strict_overflow: bool = True,
) -> tuple[Path, dict[str, Any]]:
    """Create a pure-vector adaptive sub-panel SVG in exact point coordinates."""
    pid = str(panel["id"])
    pw = to_pt(panel_w_mm, "mm")
    ph = to_pt(panel_h_mm, "mm")
    accent = str(panel.get("accent", "#547FA3"))
    badge = str(panel.get("badge", panel.get("stage", "Stage").title())).strip()
    title = str(panel.get("title", pid)).strip()
    highlight = str(panel.get("highlight", "")).strip()
    bullets = [str(b) for b in (panel.get("bullets") or [])]
    nodes = panel.get("nodes") or []
    diagram = panel.get("diagram") or {}
    mini_bars = panel.get("mini_bars") or []
    paired_dots = panel.get("paired_dots") or {}
    metrics_strip = panel.get("metrics_strip") or []

    narrow = pw < 105.0
    compact_h = ph < 140.0
    title_fs = 7.6 if (narrow or compact_h) else 8.8
    badge_fs = 6.9 if (narrow or compact_h) else 7.8
    body_fs = 6.5 if (narrow or compact_h) else 6.9
    pill_fs = 6.5 if (narrow or compact_h) else 6.8
    pad_x = 5.5 if narrow else 6.8
    inner_w = pw - 2.0 * pad_x

    root = _StdET.Element(f"{{{SVG_NS}}}svg", {
        "width": f"{panel_w_mm:.2f}mm",
        "height": f"{panel_h_mm:.2f}mm",
        "viewBox": f"0 0 {pw:.2f} {ph:.2f}",
    })
    defs = _add_el(root, "defs", {})
    m_act = _add_el(defs, "marker", {"id": "sub-arrow-act", "viewBox": "0 0 10 10", "refX": "9", "refY": "5", "markerWidth": "5.5", "markerHeight": "5.5", "orient": "auto"})
    _add_el(m_act, "path", {"d": "M 0 1.5 L 10 5 L 0 8.5 z", "fill": "#2E6B8E"})
    m_inh = _add_el(defs, "marker", {"id": "sub-arrow-inh", "viewBox": "0 0 8 12", "refX": "4", "refY": "6", "markerWidth": "6", "markerHeight": "9", "orient": "auto"})
    _add_el(m_inh, "line", {"x1": "4", "y1": "1", "x2": "4", "y2": "11", "stroke": "#B85F56", "stroke-width": "2.4"})
    m_flw = _add_el(defs, "marker", {"id": "sub-arrow-flow", "viewBox": "0 0 10 10", "refX": "9", "refY": "5", "markerWidth": "5.5", "markerHeight": "5.5", "orient": "auto"})
    _add_el(m_flw, "path", {"d": "M 1 2 L 9 5 L 1 8", "fill": "none", "stroke": "#4E9187", "stroke-width": "1.6"})

    # Card frame & header bar (embed element-level provenance anchors)
    card_attrs = {
        "id": f"card-bg-{pid}", "x": "0.8", "y": "0.8",
        "width": f"{pw - 1.6:.2f}", "height": f"{ph - 1.6:.2f}",
        "rx": "4.5", "fill": "#FAFBFD", "stroke": accent, "stroke-width": "1.05",
    }
    if panel.get("source_ref"):
        card_attrs["data-source-ref"] = str(panel["source_ref"])
    if panel.get("source_block_id"):
        card_attrs["data-source-block"] = str(panel["source_block_id"])
    _add_el(root, "rect", card_attrs)
    hdr_h = 13.8 if compact_h else (17.5 if narrow else 20.0)
    _add_el(root, "rect", {
        "id": f"card-hdr-{pid}", "x": "0.8", "y": "0.8",
        "width": f"{pw - 1.6:.2f}", "height": f"{hdr_h:.1f}",
        "rx": "4.0", "fill": accent,
    })
    _add_el(root, "text", {
        "id": f"panel-badge-{pid}", "x": f"{pad_x:.1f}", "y": f"{hdr_h * 0.70:.1f}",
        "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
        "font-size": f"{badge_fs:.1f}", "font-weight": "bold", "fill": "#FFFFFF",
    }, badge)

    # Adaptive wrapped title
    overflows: list[str] = []
    t_lines, t_ov = _wrap_to_width(title, inner_w, title_fs, max_lines=2, bold=True)
    if t_ov:
        overflows.append(f"panel {pid} title exceeds 2-line width budget ({title!r})")
    cur_y = hdr_h + (8.4 if compact_h else 11.5)
    cur_y = _add_wrapped_text(
        root, base_id=f"panel-title-{pid}", x=pw / 2.0, y_start=cur_y,
        lines=t_lines[:2], font_pt=title_fs, line_step=title_fs + (1.6 if compact_h else 2.0),
        fill="#0F172A", anchor="middle", weight="bold",
    )

    # Reserve bottom highlight pill space first so visual middle zone & bullets budget accurately
    pill_h = 13.5 if compact_h else 15.5
    pill_lines: list[str] = []
    if highlight:
        pill_lines, p_ov = _wrap_to_width(highlight, inner_w - 8.0, pill_fs, max_lines=2, bold=True)
        if p_ov:
            overflows.append(f"panel {pid} highlight exceeds pill width budget ({highlight!r})")
        pill_h = (13.5 if compact_h else 15.5) if len(pill_lines) <= 1 else (21.5 if compact_h else 24.0)
    bottom_limit = ph - (pill_h + (5.5 if compact_h else 7.5) if highlight else 4.5)

    # Compute bullet height budget ahead of time so middle visual zone scales smoothly
    bullet_blocks: list[list[str]] = []
    total_bullet_h = 0.0
    bullet_step = body_fs + (1.2 if compact_h else 2.1)
    max_b_lines = 2 if compact_h else 3
    for b in bullets[:2]:
        bl, b_ov = _wrap_to_width(b, inner_w - 7.0, body_fs, max_lines=max_b_lines)
        if b_ov:
            overflows.append(f"panel {pid} bullet exceeds {max_b_lines}-line budget ({b!r})")
        bullet_blocks.append(bl[:max_b_lines])
        total_bullet_h += len(bl[:max_b_lines]) * bullet_step + (0.8 if compact_h else 2.2)

    mid_y0 = cur_y + (0.2 if compact_h else 1.5)
    min_mid_h = (34.0 if mini_bars else 25.0) if compact_h else 44.0
    mid_h = max(min_mid_h, bottom_limit - total_bullet_h - mid_y0 - (2.0 if compact_h else 4.0))

    # Visual middle zone: mini_bars | paired_dots | metrics_strip | diagram | legacy nodes
    if mini_bars:
        vals = [float(b.get("value", 0.0)) for b in mini_bars]
        errs = [float(b.get("err", 0.0)) for b in mini_bars]
        has_neg = any(v - e < 0.0 for v, e in zip(vals, errs))
        y_hi = max([v + e for v, e in zip(vals, errs)] + [1e-6])
        y_lo = min([v - e for v, e in zip(vals, errs)] + [0.0]) if has_neg else 0.0
        y_rng = max(y_hi - y_lo, 1e-6)

        n_b = len(mini_bars)
        lbl_fs = 6.5 if (narrow or compact_h) else 7.2
        val_fs = 6.5 if (narrow or compact_h) else 7.2
        area_x = pad_x + 3.0
        area_w = inner_w - 6.0
        slot_w = area_w / max(1, n_b)
        bar_w = min(20.0, slot_w * 0.54)

        # Pre-wrap bar labels (up to 3 lines) so long biological category names NEVER collide on narrow 120mm canvas
        wrapped_lbls: list[list[str]] = []
        max_lbl_lines = 1
        for b in mini_bars:
            wl, l_ov = _wrap_to_width(str(b.get("label", "")), max(slot_w - 1.5, 14.0), lbl_fs, max_lines=3)
            if l_ov:
                overflows.append(f"panel {pid} bar label {b.get('label')!r} exceeds slot width {slot_w:.1f}pt")
            wrapped_lbls.append(wl[:3])
            max_lbl_lines = max(max_lbl_lines, len(wl[:3]))

        lbl_zone_h = max_lbl_lines * (lbl_fs + 1.4) + (9.5 if compact_h else 6.0)
        top_val_pad = val_fs + (11.0 if compact_h else 5.5)
        plot_top = mid_y0 + top_val_pad
        plot_bot = mid_y0 + mid_h - lbl_zone_h
        plot_h = max(11.0, plot_bot - plot_top)

        def _y_map(v: float) -> float:
            return plot_bot - ((v - y_lo) / y_rng) * plot_h

        zero_y = _y_map(0.0)
        _add_el(root, "line", {
            "id": f"mini-axis-{pid}", "x1": f"{area_x:.1f}", "y1": f"{zero_y:.1f}",
            "x2": f"{area_x + area_w:.1f}", "y2": f"{zero_y:.1f}", "stroke": "#475569", "stroke-width": "0.85",
        })
        if panel.get("ref_line") is not None:
            ry = _y_map(float(panel["ref_line"]))
            _add_el(root, "line", {
                "id": f"mini-ref-{pid}", "x1": f"{area_x:.1f}", "y1": f"{ry:.1f}",
                "x2": f"{area_x + area_w:.1f}", "y2": f"{ry:.1f}",
                "stroke": "#94A3B8", "stroke-width": "0.65", "stroke-dasharray": "3,2",
            })

        for bi, b in enumerate(mini_bars):
            val = float(b.get("value", 0.0))
            err = float(b.get("err", 0.0))
            cx = area_x + (bi + 0.5) * slot_w
            bx = cx - bar_w / 2.0
            bcol = str(b.get("color", accent))
            if val == 0.0:
                _add_el(root, "circle", {
                    "id": f"minibar-{pid}-{bi+1}", "cx": f"{cx:.1f}", "cy": f"{zero_y:.1f}",
                    "r": "2.1", "fill": "#FFFFFF", "stroke": bcol, "stroke-width": "1.5",
                })
                top_y, bh = zero_y, 0.0
            else:
                vy = _y_map(val)
                top_y = min(vy, zero_y)
                # Strict linear mapping: NEVER artificially inflate small bar heights!
                bh = abs(vy - zero_y)
                if bh >= 0.4:
                    _add_el(root, "rect", {
                        "id": f"minibar-{pid}-{bi+1}", "x": f"{bx:.1f}", "y": f"{top_y:.1f}",
                        "width": f"{bar_w:.1f}", "height": f"{bh:.2f}", "rx": f"{min(1.4, bh / 2.0):.2f}",
                        "fill": bcol, "stroke": "#1F2937", "stroke-width": "0.55",
                    })
                else:
                    # Sub-pixel non-zero value: mark true position with baseline indicator dot without distorting length
                    _add_el(root, "circle", {
                        "id": f"minibar-{pid}-{bi+1}", "cx": f"{cx:.1f}", "cy": f"{vy:.2f}",
                        "r": "1.8", "fill": bcol, "stroke": "#1F2937", "stroke-width": "0.55",
                    })
            # Decouple error bar rendering from val == 0.0 so 0 +- err always renders accurately!
            if err > 0.0:
                ey_hi = _y_map(val + err)
                ey_lo = _y_map(val - err)
                _add_el(root, "line", {"id": f"minierr-{pid}-{bi+1}", "x1": f"{cx:.1f}", "y1": f"{ey_hi:.1f}", "x2": f"{cx:.1f}", "y2": f"{ey_lo:.1f}", "stroke": "#111827", "stroke-width": "0.75"})
                _add_el(root, "line", {"x1": f"{cx - 2.6:.1f}", "y1": f"{ey_hi:.1f}", "x2": f"{cx + 2.6:.1f}", "y2": f"{ey_hi:.1f}", "stroke": "#111827", "stroke-width": "0.75"})
                _add_el(root, "line", {"x1": f"{cx - 2.6:.1f}", "y1": f"{ey_lo:.1f}", "x2": f"{cx + 2.6:.1f}", "y2": f"{ey_lo:.1f}", "stroke": "#111827", "stroke-width": "0.75"})
                val_y = (ey_hi - 3.0) if val >= 0 else (ey_lo + val_fs + 1.5)
            else:
                val_y = (zero_y - 4.8) if val == 0.0 else ((top_y - 3.0) if val >= 0 else (top_y + bh + val_fs + 1.5))
            # NEVER invent default unit "x": missing unit must be disclosed explicitly as unit_unknown
            has_unit_key = "unit" in b and str(b.get("unit", "")).strip() != ""
            unit = str(b.get("unit", "")).strip() if has_unit_key else ""
            if "unit" not in b:
                panel.setdefault("_panel_gaps", []).append(
                    f"MISSING_METRIC_UNIT: panel {pid} bar {b.get('label')!r} omits 'unit' (rendered unitless; unit_unknown)"
                )
            val_txt = f"{val:g}{unit}"
            _add_el(root, "text", {
                "id": f"minibar-val-{pid}-{bi+1}", "x": f"{cx:.1f}", "y": f"{val_y:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": f"{val_fs:.1f}", "font-weight": "bold", "fill": "#111827", "text-anchor": "middle",
            }, val_txt)
            _add_wrapped_text(
                root, base_id=f"minibar-lbl-{pid}-{bi+1}", x=cx, y_start=plot_bot + lbl_fs + 2.0,
                lines=wrapped_lbls[bi], font_pt=lbl_fs, line_step=lbl_fs + 1.4,
                fill="#334155", anchor="middle",
            )

    elif paired_dots and paired_dots.get("pairs"):
        pairs = paired_dots["pairs"]
        c_left = str(paired_dots.get("left_label", "Pre"))
        c_right = str(paired_dots.get("right_label", "Post"))
        unit = str(paired_dots.get("unit", "")).strip()
        all_v = [float(p[0]) for p in pairs] + [float(p[1]) for p in pairs]
        vmin, vmax = min(all_v), max(all_v)
        vspan = max(vmax - vmin, 1e-6)
        x1, x2 = pad_x + inner_w * 0.28, pad_x + inner_w * 0.72
        ptop, pbot = mid_y0 + 10.0, mid_y0 + mid_h - 14.0
        def _ym(v: float) -> float:
            return pbot - ((v - vmin) / vspan) * max(16.0, pbot - ptop)
        for pi, (v0, v1) in enumerate(pairs):
            y0, y1 = _ym(float(v0)), _ym(float(v1))
            _add_el(root, "line", {"x1": f"{x1:.1f}", "y1": f"{y0:.1f}", "x2": f"{x2:.1f}", "y2": f"{y1:.1f}", "stroke": "#94A3B8", "stroke-width": "0.8"})
            _add_el(root, "circle", {"cx": f"{x1:.1f}", "cy": f"{y0:.1f}", "r": "2.4", "fill": "#547FA3", "stroke": "#FFFFFF", "stroke-width": "0.4"})
            _add_el(root, "circle", {"cx": f"{x2:.1f}", "cy": f"{y1:.1f}", "r": "2.4", "fill": accent, "stroke": "#FFFFFF", "stroke-width": "0.4"})
        _add_el(root, "text", {"id": f"paired-l-{pid}", "x": f"{x1:.1f}", "y": f"{pbot + 10.0:.1f}", "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif", "font-size": "6.8", "fill": "#334155", "text-anchor": "middle"}, c_left)
        _add_el(root, "text", {"id": f"paired-r-{pid}", "x": f"{x2:.1f}", "y": f"{pbot + 10.0:.1f}", "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif", "font-size": "6.8", "fill": "#334155", "text-anchor": "middle"}, c_right)

    elif metrics_strip:
        m_cnt = min(len(metrics_strip), 2)
        gap_m = 5.0
        tile_w = (inner_w - gap_m * (m_cnt - 1)) / max(1, m_cnt)
        tile_h = min(33.0 if compact_h else 46.0, mid_h - 6.0)
        ty = mid_y0 + max(1.0, (mid_h - tile_h) / 2.0 - (2.0 if compact_h else 0.0))
        for mi, m in enumerate(metrics_strip[:m_cnt]):
            tx = pad_x + mi * (tile_w + gap_m)
            mc = str(m.get("color", accent))
            _add_el(root, "rect", {
                "id": f"metric-tile-{pid}-{mi+1}", "x": f"{tx:.1f}", "y": f"{ty:.1f}",
                "width": f"{tile_w:.1f}", "height": f"{tile_h:.1f}", "rx": "4",
                "fill": "#FFFFFF", "stroke": mc, "stroke-width": "1.0",
            })
            m_txt = str(m.get("metric", ""))
            m_base_fs = 8.8 if not narrow else 7.6
            m_fs = max(6.5, min(m_base_fs, (tile_w - 6.0) / max(len(m_txt) * 0.58, 1.0)))
            _add_el(root, "text", {
                "id": f"metric-val-{pid}-{mi+1}", "x": f"{tx + tile_w / 2.0:.1f}", "y": f"{ty + tile_h * 0.46:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": f"{m_fs:.1f}", "font-weight": "bold", "fill": mc, "text-anchor": "middle",
            }, m_txt)
            ml_lines, _ = _wrap_to_width(str(m.get("label", "")), tile_w - 5.0, 6.5, max_lines=2)
            _add_wrapped_text(
                root, base_id=f"metric-lbl-{pid}-{mi+1}", x=tx + tile_w / 2.0, y_start=ty + tile_h * 0.72,
                lines=ml_lines[:2], font_pt=6.5, line_step=7.5, fill="#334155", anchor="middle",
            )

    elif diagram or nodes:
        d_nodes = list(diagram.get("nodes") or nodes)
        d_links = list(diagram.get("links") or [])
        if len(d_nodes) > 4:
            panel.setdefault("_panel_gaps", []).append(
                f"MECHANISM_NODE_OVERFLOW_PARTIAL: panel {pid} has {len(d_nodes)} nodes; max 4 fit per micro-grid without clipping (split panel or enlarge canvas)"
            )
        n_cnt = min(len(d_nodes), 4)
        grid_2x2_mode = n_cnt == 4
        horiz_mode = (not grid_2x2_mode) and compact_h and n_cnt == 2 and inner_w >= 115.0
        node_fs = 6.2 if grid_2x2_mode else (6.5 if (narrow or compact_h or n_cnt == 3) else 7.0)
        coords: list[tuple[float, float, float, float]] = []
        if grid_2x2_mode:
            gap_h, gap_v = 10.0, 9.0
            box_w = (inner_w - gap_h) / 2.0
            box_h = min(22.0, (mid_h - gap_v - 4.0) / 2.0)
            start_y = mid_y0 + max(1.0, (mid_h - (2 * box_h + gap_v)) / 2.0)
            for ni in range(4):
                nd = d_nodes[ni]
                rr, cc = ni // 2, ni % 2
                nx = pad_x + cc * (box_w + gap_h)
                ny = start_y + rr * (box_h + gap_v)
                ncol = str(nd.get("color", accent))
                shape = str(nd.get("shape", "roundrect")).lower()
                rx_val = f"{box_h / 2.0:.1f}" if shape in ("pill", "ellipse") else "3.8"
                _add_el(root, "rect", {
                    "id": f"motif-box-{pid}-{ni+1}",
                    "x": f"{nx:.1f}", "y": f"{ny:.1f}", "width": f"{box_w:.1f}", "height": f"{box_h:.1f}",
                    "rx": rx_val, "fill": ncol, "stroke": "#1E293B", "stroke-width": "0.6",
                })
                nl_lines, n_ov = _wrap_to_width(str(nd.get("label", "")), box_w - 5.0, node_fs, max_lines=2, bold=True)
                if n_ov:
                    overflows.append(f"panel {pid} node label {nd.get('label')!r} exceeds box width")
                n_start_y = ny + box_h / 2.0 + (node_fs * 0.34 if len(nl_lines) == 1 else -node_fs * 0.14)
                _add_wrapped_text(
                    root, base_id=f"motif-lbl-{pid}-{ni+1}", x=nx + box_w / 2.0, y_start=n_start_y,
                    lines=nl_lines[:2], font_pt=node_fs, line_step=node_fs + 0.9,
                    fill="#FFFFFF", anchor="middle", weight="bold",
                )
                coords.append((nx, ny, box_w, box_h))
        elif horiz_mode:
            gap_h = 16.0
            box_w = (inner_w - gap_h) / 2.0
            box_h = min(24.0, mid_h - 2.0)
            ny = mid_y0 + (mid_h - box_h) / 2.0
            for ni in range(2):
                nd = d_nodes[ni]
                nx = pad_x + ni * (box_w + gap_h)
                ncol = str(nd.get("color", accent))
                shape = str(nd.get("shape", "roundrect")).lower()
                rx_val = f"{box_h / 2.0:.1f}" if shape in ("pill", "ellipse") else "3.8"
                _add_el(root, "rect", {
                    "id": f"motif-box-{pid}-{ni+1}",
                    "x": f"{nx:.1f}", "y": f"{ny:.1f}", "width": f"{box_w:.1f}", "height": f"{box_h:.1f}",
                    "rx": rx_val, "fill": ncol, "stroke": "#1E293B", "stroke-width": "0.6",
                })
                nl_lines, n_ov = _wrap_to_width(str(nd.get("label", "")), box_w - 5.0, node_fs, max_lines=2, bold=True)
                if n_ov:
                    overflows.append(f"panel {pid} node label {nd.get('label')!r} exceeds box width")
                n_start_y = ny + box_h / 2.0 + (node_fs * 0.34 if len(nl_lines) == 1 else -node_fs * 0.14)
                _add_wrapped_text(
                    root, base_id=f"motif-lbl-{pid}-{ni+1}", x=nx + box_w / 2.0, y_start=n_start_y,
                    lines=nl_lines[:2], font_pt=node_fs, line_step=node_fs + 0.9,
                    fill="#FFFFFF", anchor="middle", weight="bold",
                )
                coords.append((nx, ny, box_w, box_h))
        else:
            box_w = inner_w - 6.0
            gap_n = 11.0 if n_cnt == 2 else 8.0
            box_h = min(24.0 if n_cnt == 2 else 18.0, (mid_h - gap_n * max(0, n_cnt - 1) - 4.0) / max(1, n_cnt))
            start_y = mid_y0 + max(1.0, (mid_h - (n_cnt * box_h + max(0, n_cnt - 1) * gap_n)) / 2.0)
            for ni in range(n_cnt):
                nd = d_nodes[ni]
                ny = start_y + ni * (box_h + gap_n)
                nx = (pw - box_w) / 2.0
                ncol = str(nd.get("color", accent))
                shape = str(nd.get("shape", "roundrect")).lower()
                rx_val = f"{box_h / 2.0:.1f}" if shape in ("pill", "ellipse") else "3.8"
                _add_el(root, "rect", {
                    "id": f"motif-box-{pid}-{ni+1}",
                    "x": f"{nx:.1f}", "y": f"{ny:.1f}", "width": f"{box_w:.1f}", "height": f"{box_h:.1f}",
                    "rx": rx_val, "fill": ncol, "stroke": "#1E293B", "stroke-width": "0.6",
                })
                nl_lines, n_ov = _wrap_to_width(str(nd.get("label", "")), box_w - 8.0, node_fs, max_lines=2, bold=True)
                if n_ov:
                    overflows.append(f"panel {pid} node label {nd.get('label')!r} exceeds box width")
                n_start_y = ny + box_h / 2.0 + (node_fs * 0.34 if len(nl_lines) == 1 else -node_fs * 0.14)
                _add_wrapped_text(
                    root, base_id=f"motif-lbl-{pid}-{ni+1}", x=pw / 2.0, y_start=n_start_y,
                    lines=nl_lines[:2], font_pt=node_fs, line_step=node_fs + 1.0,
                    fill="#FFFFFF", anchor="middle", weight="bold",
                )
                coords.append((nx, ny, box_w, box_h))

        # Typed mechanism links: NEVER fabricate causal 'activate' arrows when links are omitted!
        for li, lk in enumerate(d_links):
            fi, ti = int(lk.get("from", 0)), int(lk.get("to", 1))
            if 0 <= fi < len(coords) and 0 <= ti < len(coords) and fi != ti:
                x1, y1, w1, h1 = coords[fi]
                x2, y2, w2, h2 = coords[ti]
                rel = str(lk.get("relation", "flow")).lower()
                if rel in ("inhibit", "block"):
                    scol, mref, dash = "#B85F56", "url(#sub-arrow-inh)", ""
                elif rel in ("flow", "transport", "uncertain", "sequence"):
                    scol, mref, dash = "#4E9187", "url(#sub-arrow-flow)", "3,2"
                else:
                    scol, mref, dash = "#2E6B8E", "url(#sub-arrow-act)", ""
                dx_n = (x2 + w2 / 2.0) - (x1 + w1 / 2.0)
                dy_n = (y2 + h2 / 2.0) - (y1 + h1 / 2.0)
                if abs(dx_n) >= abs(dy_n):
                    lx1 = (x1 + w1 + 1.0) if dx_n >= 0 else (x1 - 1.0)
                    lx2 = (x2 - 2.2) if dx_n >= 0 else (x2 + w2 + 2.2)
                    ly1, ly2 = y1 + h1 / 2.0, y2 + h2 / 2.0
                    lbl_x, lbl_y, lbl_anc = (lx1 + lx2) / 2.0, min(y1, y2) - 2.2, "middle"
                else:
                    lx1, lx2 = x1 + w1 / 2.0, x2 + w2 / 2.0
                    ly1 = (y1 + h1 + 0.8) if dy_n >= 0 else (y1 - 0.8)
                    ly2 = (y2 - 2.0) if dy_n >= 0 else (y2 + h2 + 2.0)
                    lbl_x, lbl_y, lbl_anc = (lx1 + lx2) / 2.0 + 4.0, (ly1 + ly2) / 2.0 + 2.2, "start"
                la = {
                    "id": f"motif-link-{pid}-{li+1}",
                    "x1": f"{lx1:.1f}", "y1": f"{ly1:.1f}",
                    "x2": f"{lx2:.1f}", "y2": f"{ly2:.1f}",
                    "stroke": scol, "stroke-width": "1.35", "marker-end": mref,
                }
                if dash:
                    la["stroke-dasharray"] = dash
                _add_el(root, "line", la)
                l_txt = str(lk.get("label", "")).strip()
                if l_txt:
                    _add_el(root, "text", {
                        "id": f"motif-linklbl-{pid}-{li+1}",
                        "x": f"{lbl_x:.1f}", "y": f"{lbl_y:.1f}",
                        "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                        "font-size": "6.5", "font-weight": "bold", "fill": scol, "text-anchor": lbl_anc,
                    }, l_txt)

    # Bullets
    b_y = mid_y0 + mid_h + (5.0 if (compact_h and mini_bars) else (2.5 if compact_h else 4.0))
    for bi, bl in enumerate(bullet_blocks):
        b_y = _add_wrapped_text(
            root, base_id=f"bullet-{pid}-{bi+1}", x=pad_x, y_start=b_y,
            lines=bl, font_pt=body_fs, line_step=bullet_step,
            fill="#334155", anchor="start", prefix_first="• ", prefix_rest="  ",
        )
        b_y += (0.8 if compact_h else 1.8)

    if b_y - bullet_step > bottom_limit + 2.0:
        overflows.append(f"panel {pid} vertical content ({b_y - bullet_step:.1f}pt) exceeds pill top ({bottom_limit:.1f}pt)")

    # Bottom highlight pill
    if highlight:
        py = ph - pill_h - 4.5
        _add_el(root, "rect", {
            "id": f"pill-bg-{pid}", "x": f"{pad_x:.1f}", "y": f"{py:.1f}",
            "width": f"{inner_w:.2f}", "height": f"{pill_h:.1f}", "rx": "3.2",
            "fill": "#EEF2F6", "stroke": accent, "stroke-width": "0.8",
        })
        p_y0 = py + (pill_h * 0.67 if len(pill_lines) <= 1 else 9.8)
        _add_wrapped_text(
            root, base_id=f"pill-txt-{pid}", x=pw / 2.0, y_start=p_y0,
            lines=pill_lines[:2], font_pt=pill_fs, line_step=pill_fs + 1.8,
            fill="#0F172A", anchor="middle", weight="bold",
        )

    if strict_overflow and overflows:
        raise LayoutOverflowError("; ".join(overflows))

    _StdET.indent(root, space="  ")
    out_svg.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))
    return out_svg, {"panel_id": pid, "width_pt": round(pw, 2), "height_pt": round(ph, 2), "overflows": overflows}


def _write_caption_draft(spec: dict[str, Any], caption_path: Path) -> None:
    lines = [
        f"# Graphical Abstract Caption & Provenance Draft",
        "",
        f"**Title**: {spec.get('title', 'Graphical Abstract')}",
        "",
        "## Panel-by-Panel Evidence Mapping",
    ]
    for idx, p in enumerate(spec.get("panels") or [], start=1):
        lines.append(
            f"- **Stage {idx} ({p.get('stage', 'unknown')}) — {p.get('title', p.get('id'))}**: "
            f"source_ref=`{p.get('source_ref') or 'UNSPECIFIED'}`; highlight=`{p.get('highlight') or 'none'}`."
        )
    if spec.get("caption_notes"):
        lines.extend(["", "## Statistical & Methodological Notes (Kept Off Visual Canvas)"])
        for n in spec["caption_notes"]:
            lines.append(f"- {n}")
    if spec.get("takeaway"):
        lines.extend(["", f"**Core Takeaway**: {spec['takeaway']}"])
    caption_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def compose_graphical_abstract(
    spec: dict[str, Any],
    svg_out: Path,
    *,
    base_dir: Path | None = None,
    strict_overflow: bool = True,
) -> dict[str, Any]:
    """Compose a single-page mm-locked Graphical Abstract via adaptive vector assembly."""
    width_mm = float(spec.get("width_mm", 174.0))
    height_mm = float(spec.get("height_mm", 96.0))
    req_layout = str(spec.get("layout", "auto")).lower()
    panels = spec.get("panels") or []
    layout = ("2x2" if (width_mm < 138.0 and len(panels) == 4) else "1x4") if req_layout == "auto" else req_layout

    title = str(spec.get("title", "Graphical Abstract")).strip()
    subtitle = str(spec.get("subtitle", "")).strip()
    takeaway = str(spec.get("takeaway", "")).strip()
    entity_colors: dict[str, str] = dict(spec.get("entity_colors") or {})

    w_pt = to_pt(width_mm, "mm")
    h_pt = to_pt(height_mm, "mm")
    margin_mm = 3.2
    mx = to_pt(margin_mm, "mm")
    banner_w_pt = w_pt - 2 * mx

    title_fs = 9.8 if width_mm >= 150 else 8.8
    sub_fs = 7.2 if width_mm >= 150 else 6.8
    t_lines, _ = _wrap_to_width(title, banner_w_pt, title_fs, max_lines=2, bold=True)
    s_lines, _ = _wrap_to_width(subtitle, banner_w_pt, sub_fs, max_lines=2) if subtitle else ([], False)

    header_pt = 8.0 + len(t_lines) * (title_fs + 2.2) + (len(s_lines) * (sub_fs + 1.8) + 2.0 if s_lines else 0.0)
    legend_h_pt = 13.5 if entity_colors else 0.0
    header_mm = (header_pt + legend_h_pt + 4.0) * 25.4 / 72.0

    take_fs = 7.0 if width_mm >= 145 else 6.5
    tk_lines, _ = _wrap_to_width(takeaway, banner_w_pt - 16.0, take_fs, max_lines=2, bold=True) if takeaway else ([], False)
    footer_pt = (len(tk_lines) * (take_fs + 2.0) + 11.0) if tk_lines else 8.0
    footer_mm = footer_pt * 25.4 / 72.0

    gap_mm = 4.6 if layout == "1x4" else 4.2
    cols = 2 if layout == "2x2" else max(1, len(panels))
    rows = 2 if layout == "2x2" else 1
    panel_w_mm = (width_mm - 2 * margin_mm - gap_mm * (cols - 1)) / cols
    panel_h_mm = (height_mm - header_mm - footer_mm - gap_mm * (rows - 1)) / rows

    panel_reports: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmpd:
        tmp_path = Path(tmpd)
        specs: list[PanelSpec] = []
        for idx, p in enumerate(panels):
            pid = str(p.get("id", f"p{idx+1}"))
            r_idx = idx // cols
            c_idx = idx % cols
            x_mm = margin_mm + c_idx * (panel_w_mm + gap_mm)
            y_mm = header_mm + r_idx * (panel_h_mm + gap_mm)

            ext_file = p.get("file")
            if ext_file:
                fp = Path(ext_file)
                if not fp.is_absolute() and base_dir is not None:
                    fp = base_dir / fp
                panel_reports.append({"panel_id": pid, "external_file": fp.name, "overflows": []})
            else:
                fp, prep = _render_subpanel_svg(p, panel_w_mm, panel_h_mm, tmp_path / f"{pid}.svg", strict_overflow=strict_overflow)
                panel_reports.append(prep)

            specs.append(PanelSpec(
                panel_id=pid, file=fp, label=" ",
                row=r_idx, col=c_idx, x_mm=x_mm, y_mm=y_mm,
                width_mm=panel_w_mm, height_mm=panel_h_mm, fit="contain", align="center",
            ))

        comp_rep = compose_svg_grid(
            specs, svg_out, width_mm=width_mm, height_mm=height_mm,
            margin_mm=margin_mm, gap_mm=gap_mm, cols=cols, label_font_pt=8.0, background="#FFFFFF",
        )

    raw = svg_out.read_bytes()
    root = _SafeET.fromstring(raw) if _SafeET is not None else _StdET.fromstring(raw)

    defs = _add_el(root, "defs", {})
    m = _add_el(defs, "marker", {"id": "ga-stage-arrow", "viewBox": "0 0 10 10", "refX": "9", "refY": "5", "markerWidth": "5.5", "markerHeight": "5.5", "orient": "auto"})
    _add_el(m, "path", {"d": "M 0 1.5 L 10 5 L 0 8.5 z", "fill": "#475569"})

    l_banner = _add_el(root, "g", {"id": "layer-banner", "data-layer-name": "banner"})
    by = _add_wrapped_text(
        l_banner, base_id="title-main", x=mx, y_start=13.5,
        lines=t_lines, font_pt=title_fs, line_step=title_fs + 2.2,
        fill="#0F172A", anchor="start", weight="bold",
    )
    if s_lines:
        by = _add_wrapped_text(
            l_banner, base_id="title-sub", x=mx, y_start=by + 0.5,
            lines=s_lines, font_pt=sub_fs, line_step=sub_fs + 1.8,
            fill="#475569", anchor="start",
        )

    # Shared cross-panel visual key strip (bounded within canvas width so narrow 120mm canvases never clip)
    if entity_colors:
        l_leg = _add_el(root, "g", {"id": "layer-shared-legend", "data-layer-name": "shared-legend"})
        ly = by + 2.0
        leg_fs = 6.5 if width_mm < 140 else 6.6
        items_leg = [(str(k).replace("_", " "), str(v)) for k, v in entity_colors.items()]
        usable_leg_w = to_pt(width_mm, "mm") - 2.0 * mx
        item_widths = [11.5 + _text_width_pt(lbl, leg_fs) for lbl, _ in items_leg]
        total_w = sum(item_widths) + 7.0 * max(0, len(items_leg) - 1)
        if total_w > usable_leg_w and items_leg:
            # Shorten labels gently & tighten gap so all items stay strictly inside canvas margins
            max_lbl_chars = max(8, int((usable_leg_w / len(items_leg) - 15.0) / (leg_fs * 0.56)))
            items_leg = [(lbl[:max_lbl_chars].rstrip() if len(lbl) > max_lbl_chars else lbl, col) for lbl, col in items_leg]
            item_widths = [11.0 + _text_width_pt(lbl, leg_fs) for lbl, _ in items_leg]
        gap_leg = min(8.0, max(3.5, (usable_leg_w - sum(item_widths)) / max(1, len(items_leg) - 1))) if len(items_leg) > 1 else 0.0
        lx = mx
        for ei, ((lbl, ecol), iw) in enumerate(zip(items_leg, item_widths)):
            _add_el(l_leg, "rect", {
                "id": f"ga-leg-swatch-{ei+1}", "x": f"{lx:.1f}", "y": f"{ly - 6.0:.1f}",
                "width": "7.2", "height": "7.2", "rx": "1.5",
                "fill": ecol, "stroke": "#334155", "stroke-width": "0.5",
            })
            _add_el(l_leg, "text", {
                "id": f"ga-leg-lbl-{ei+1}", "x": f"{lx + 9.5:.1f}", "y": f"{ly + 0.2:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": f"{leg_fs:.1f}", "fill": "#1E293B",
            }, lbl)
            lx += iw + gap_leg

    # Directional inter-panel stage arrows (1->2->3->4 in both 1x4 and 2x2 layouts!)
    l_flow = _add_el(root, "g", {"id": "layer-narrative-flow", "data-layer-name": "narrative-flow"})
    if rows == 1 and cols > 1:
        mid_arrow_y = to_pt(header_mm + panel_h_mm * 0.5, "mm")
        for ci in range(cols - 1):
            x1 = to_pt(margin_mm + (ci + 1) * panel_w_mm + ci * gap_mm + 0.5, "mm")
            x2 = to_pt(margin_mm + (ci + 1) * panel_w_mm + (ci + 1) * gap_mm - 0.8, "mm")
            _add_el(l_flow, "line", {
                "id": f"stage-arrow-{ci+1}to{ci+2}", "data-drawio-cell-id": f"stage-arrow-{ci+1}to{ci+2}",
                "x1": f"{x1:.2f}", "y1": f"{mid_arrow_y:.2f}", "x2": f"{x2:.2f}", "y2": f"{mid_arrow_y:.2f}",
                "stroke": "#475569", "stroke-width": "1.5", "marker-end": "url(#ga-stage-arrow)",
            })
    elif rows == 2 and cols == 2:
        # Row 1: 1 -> 2
        y_r1 = to_pt(header_mm + panel_h_mm * 0.5, "mm")
        x1 = to_pt(margin_mm + panel_w_mm + 0.5, "mm")
        x2 = to_pt(margin_mm + panel_w_mm + gap_mm - 0.8, "mm")
        _add_el(l_flow, "line", {"id": "stage-arrow-1to2", "x1": f"{x1:.2f}", "y1": f"{y_r1:.2f}", "x2": f"{x2:.2f}", "y2": f"{y_r1:.2f}", "stroke": "#475569", "stroke-width": "1.5", "marker-end": "url(#ga-stage-arrow)"})
        # Stepped bridge from Panel 2 (top-right) -> Panel 3 (bottom-left) cleanly through center gutter!
        y_gap1 = to_pt(header_mm + panel_h_mm + 0.5, "mm")
        y_gap2 = to_pt(header_mm + panel_h_mm + gap_mm - 0.8, "mm")
        y_gmid = (y_gap1 + y_gap2) / 2.0
        xc_left = to_pt(margin_mm + panel_w_mm * 0.5, "mm")
        xc_right = to_pt(margin_mm + panel_w_mm + gap_mm + panel_w_mm * 0.5, "mm")
        _add_el(l_flow, "path", {
            "id": "stage-arrow-2to3",
            "d": f"M {xc_right:.2f} {y_gap1:.2f} L {xc_right:.2f} {y_gmid:.2f} L {xc_left:.2f} {y_gmid:.2f} L {xc_left:.2f} {y_gap2:.2f}",
            "fill": "none", "stroke": "#475569", "stroke-width": "1.5", "marker-end": "url(#ga-stage-arrow)",
        })
        # Row 2: 3 -> 4
        y_r2 = to_pt(header_mm + panel_h_mm + gap_mm + panel_h_mm * 0.5, "mm")
        _add_el(l_flow, "line", {"id": "stage-arrow-3to4", "x1": f"{x1:.2f}", "y1": f"{y_r2:.2f}", "x2": f"{x2:.2f}", "y2": f"{y_r2:.2f}", "stroke": "#475569", "stroke-width": "1.5", "marker-end": "url(#ga-stage-arrow)"})

    if tk_lines:
        l_take = _add_el(root, "g", {"id": "layer-takeaway", "data-layer-name": "takeaway"})
        ty = to_pt(height_mm - footer_mm + 1.5, "mm")
        th = to_pt(footer_mm - 2.6, "mm")
        tw = w_pt - 2 * mx
        _add_el(l_take, "rect", {
            "id": "takeaway-box", "x": f"{mx:.2f}", "y": f"{ty:.2f}",
            "width": f"{tw:.2f}", "height": f"{th:.2f}", "rx": "3.2",
            "fill": "#EEF5F2", "stroke": "#4E9187", "stroke-width": "0.85",
        })
        tk_y0 = ty + (th * 0.65 if len(tk_lines) == 1 else 9.5)
        _add_wrapped_text(
            l_take, base_id="takeaway-text", x=w_pt / 2.0, y_start=tk_y0,
            lines=tk_lines, font_pt=take_fs, line_step=take_fs + 1.9,
            fill="#1F3A35", anchor="middle", weight="bold",
        )

    _StdET.indent(root, space="  ")
    svg_out.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))
    comp_rep["layout_resolved"] = layout
    comp_rep["panel_reports"] = panel_reports
    return comp_rep


def source_preserving_patch_ga_svg(svg_in: Path, patch: dict[str, Any], svg_out: Path) -> dict[str, Any]:
    """Edit specific GA text/attributes by element ID without regenerating sibling panels."""
    before_sha = _sha256(svg_in)
    raw = svg_in.read_bytes()
    root = _SafeET.fromstring(raw) if _SafeET is not None else _StdET.fromstring(raw)
    id_index = {el.get("id"): el for el in root.iter() if el.get("id")}
    changes: list[dict[str, Any]] = []

    def _find(tid: str) -> _StdET.Element | None:
        if tid in id_index:
            return id_index[tid]
        for eid, cand in id_index.items():
            if eid.endswith(f"__{tid}") or eid.endswith(f"_{tid}"):
                return cand
        return None

    raw_tu = patch.get("update_text", [])
    if isinstance(raw_tu, dict):
        raw_tu = [{"id": k, "text": v} for k, v in raw_tu.items()]
    for upd in raw_tu:
        tid = str(upd["id"])
        el = _find(tid)
        if el is None:
            raise KeyError(f"UNKNOWN_ELEMENT_ID update_text={tid!r}; sample_ids={sorted(id_index)[:25]}")
        old_t = el.text or ""
        new_t = str(upd["text"])
        if old_t != new_t:
            el.text = new_t
            changes.append({"op": "update_text", "id": el.get("id"), "old": old_t, "new": new_t})

    for upd in patch.get("update_attrs", []) or []:
        tid = str(upd["id"])
        el = _find(tid)
        if el is None:
            raise KeyError(f"UNKNOWN_ELEMENT_ID update_attrs={tid!r}; sample_ids={sorted(id_index)[:25]}")
        for k, v in (upd.get("attrs") or {}).items():
            old_v = el.get(k)
            if old_v != str(v):
                el.set(k, str(v))
                changes.append({"op": "update_attr", "id": el.get("id"), "attr": k, "old": old_v, "new": str(v)})

    svg_out.parent.mkdir(parents=True, exist_ok=True)
    _StdET.indent(root, space="  ")
    svg_out.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))
    after_ids = {el.get("id") for el in root.iter() if el.get("id")}
    preserved_ratio = round(len(set(id_index) & after_ids) / max(1, len(id_index)), 4)
    return {"before_sha256": before_sha, "after_sha256": _sha256(svg_out), "preserved_id_ratio": preserved_ratio, "changes": changes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compose or source-preserving edit single-page Graphical Abstract.")
    parser.add_argument("--spec", help="GA JSON spec for build mode")
    parser.add_argument("--from-input", default="", help="Optional raw /input directory to auto-extract grounded GA spec draft before composing")
    parser.add_argument("--edit-svg", help="Existing GA SVG for source-preserving edit mode")
    parser.add_argument("--patch", help="Patch JSON file for edit mode")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--stem", default="graphical_abstract")
    parser.add_argument("--dpi", type=int, default=300, help="Export PNG resolution in DPI (default 300)")
    parser.add_argument("--case-json", default="", help="Optional /input/case.json path to auto-seal result.json preserving output_contract")
    parser.add_argument("--result-out", default="", help="Optional output path for sealed result.json")
    parser.add_argument("--allow-overflow", action="store_true", help="Emit overflow warnings instead of hard-failing")
    parser.add_argument("--list-ids", action="store_true", help="List existing SVG element IDs for --edit-svg and exit")
    args = parser.parse_args(argv)

    if args.list_ids and args.edit_svg:
        r_tmp = _SafeET.fromstring(Path(args.edit_svg).read_bytes()) if _SafeET is not None else _StdET.fromstring(Path(args.edit_svg).read_bytes())
        print(json.dumps({"ok": True, "ids": sorted({e.get("id") for e in r_tmp.iter() if e.get("id")})}, ensure_ascii=False, indent=2))
        return 0
    if not args.out_dir:
        parser.error("--out-dir is required")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_p = out_dir / f"{args.stem}.svg"
    drawio_p = out_dir / f"{args.stem}.drawio"
    png_p = out_dir / f"{args.stem}.png"
    pdf_p = out_dir / f"{args.stem}.pdf"
    case_p = Path(args.case_json) if args.case_json else None
    result_p = Path(args.result_out) if args.result_out else (out_dir / "result.json")

    if args.edit_svg:
        if not args.patch:
            raise SystemExit("--edit-svg requires --patch")
        patch_obj = json.loads(Path(args.patch).read_text(encoding="utf-8-sig"))
        try:
            diff = source_preserving_patch_ga_svg(Path(args.edit_svg), patch_obj, svg_p)
        except KeyError as exc:
            print(json.dumps({"ok": False, "error": "UNKNOWN_ELEMENT_ID", "message": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        svg2drawio.svg_to_drawio_file(svg_p, drawio_p)
        ren = render_svg_previews(svg_p, png_path=png_p, pdf_path=pdf_p, scale=2.5, dpi=args.dpi)
        aud = audit_ga(None, svg_p, drawio_path=drawio_p, png_path=png_p)
        diff_p = out_dir / f"{args.stem}_edit_diff.json"
        diff_p.write_text(json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ok = bool(ren["ok"] and aud["passed"])
        seal_rep = None
        if case_p is not None:
            seal_rep = seal_case_result(
                case_p, result_p, task_id="graphical-abstract-narrative-composer",
                artifacts=[svg_p.name, drawio_p.name, png_p.name, pdf_p.name, diff_p.name],
                sources=[str(args.edit_svg), str(args.patch)],
                uncertainty=list(ren.get("degraded_warnings") or []),
                change_records=diff.get("changes", []),
                metrics={"preserved_id_ratio": diff.get("preserved_id_ratio", 0.0), "dpi": args.dpi},
                status="ok" if ok else "failed",
            )
        print(json.dumps({"mode": "edit", "ok": ok, "edit_diff": diff, "render": ren, "audit": aud, "result_seal": seal_rep}, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    if not args.spec and not args.from_input:
        raise SystemExit("Provide --spec, --from-input, or --edit-svg")
    if args.from_input and not args.spec:
        from draft_ga_spec import draft_ga_spec_from_input
        spec = draft_ga_spec_from_input(Path(args.from_input))
        spec_p = out_dir / f"{args.stem}_draft_spec.json"
        spec_p.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        spec_p = Path(args.spec).resolve()
        spec = json.loads(spec_p.read_text(encoding="utf-8-sig"))
    try:
        comp_rep = compose_graphical_abstract(spec, svg_p, base_dir=spec_p.parent, strict_overflow=not args.allow_overflow)
    except LayoutOverflowError as exc:
        print(json.dumps({"mode": "build", "ok": False, "error": "LAYOUT_OVERFLOW", "message": str(exc)}, indent=2, ensure_ascii=False))
        return 2

    svg2drawio.svg_to_drawio_file(svg_p, drawio_p)
    ren = render_svg_previews(svg_p, png_path=png_p, pdf_path=pdf_p, scale=2.5, dpi=args.dpi)
    aud = audit_ga(spec, svg_p, drawio_path=drawio_p, png_path=png_p)
    caption_p = out_dir / f"{args.stem}_caption_draft.md"
    _write_caption_draft(spec, caption_p)

    panel_gaps: list[str] = []
    for p in (spec.get("panels") or []):
        if isinstance(p, dict) and p.get("_panel_gaps"):
            panel_gaps.extend(str(g) for g in p["_panel_gaps"])
    entity_color_map = dict(spec.get("entity_colors") or {})
    artifact_hashes = {
        fp.name: _sha256(fp) for fp in (svg_p, drawio_p, png_p, pdf_p, caption_p) if fp.is_file()
    }
    manifest_p = out_dir / f"{args.stem}_manifest.json"
    manifest_p.write_text(json.dumps({
        "composition": comp_rep,
        "audit": aud,
        "entity_color_map": entity_color_map,
        "panel_gaps": panel_gaps,
        "artifact_sha256": artifact_hashes,
        "caption_draft": caption_p.name,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    ok = bool(ren["ok"] and aud["passed"])
    seal_rep = None
    if case_p is not None:
        srcs = sorted({str(p.get("source_ref", "")).strip() for p in (spec.get("panels") or []) if isinstance(p, dict) and str(p.get("source_ref", "")).strip()})
        uncert = list(ren.get("degraded_warnings") or []) + list(aud.get("warnings") or []) + panel_gaps
        seal_rep = seal_case_result(
            case_p, result_p, task_id="graphical-abstract-narrative-composer",
            artifacts=[svg_p.name, drawio_p.name, png_p.name, pdf_p.name, caption_p.name, manifest_p.name],
            sources=srcs, uncertainty=uncert,
            change_records=[{"op": "compose_graphical_abstract", "layout": comp_rep.get("layout"), "dpi": args.dpi}],
            metrics={"layout": comp_rep.get("layout"), "dpi": args.dpi, "width_mm": comp_rep.get("width_mm"), "height_mm": comp_rep.get("height_mm"), "entity_color_map": entity_color_map, "artifact_sha256": artifact_hashes},
            status="ok" if ok else "failed",
        )
    print(json.dumps({"mode": "build", "ok": ok, "composition": comp_rep, "render": ren, "audit": aud, "caption_draft": caption_p.name, "result_seal": seal_rep}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
