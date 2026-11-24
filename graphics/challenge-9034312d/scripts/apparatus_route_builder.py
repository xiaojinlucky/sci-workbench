#!/usr/bin/env python3
"""Layered SVG, native Draw.io, parameterized standalone TikZ (.tex + .meta.json), and librsvg PNG/PDF builder for experimental apparatus & technical routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
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

from apparatus_route_linter import lint_route_spec
from audit_editability import inspect_svg_only
from contract_sealer import seal_case_result, validate_spec_schema
from drawio_stream_binding import bind_drawio_streams, verify_drawio_unit_move
from layout_tools import analyze_layout
from portable_svg_audit import audit_portable_svg
from render_preview import render_svg_previews
import svg2drawio
from validate_tikz_contract import validate_tikz_pair

SVG_NS = "http://www.w3.org/2000/svg"
_StdET.register_namespace("", SVG_NS)

_STREAM_STYLES: dict[str, tuple[str, str, str, str]] = {
    "material": ("#2E6B8E", "url(#arrow-material)", "", "Material / Fluid"),
    "fluid": ("#2E6B8E", "url(#arrow-material)", "", "Material / Fluid"),
    "reagent": ("#2E6B8E", "url(#arrow-material)", "", "Reagent Stream"),
    "optical": ("#D19A45", "url(#arrow-optical)", "", "Optical / Beam"),
    "laser": ("#D19A45", "url(#arrow-optical)", "", "Optical / Beam"),
    "electrical": ("#4E9187", "url(#arrow-signal)", "5,3", "Electrical / Signal"),
    "signal": ("#4E9187", "url(#arrow-signal)", "5,3", "Electrical / Signal"),
    "data": ("#4E9187", "url(#arrow-signal)", "5,3", "Data / Readout"),
    "control": ("#967A9C", "url(#arrow-control)", "2,3", "Control / Trigger"),
    "waste": ("#737373", "url(#arrow-waste)", "4,3", "Waste / Exhaust"),
    "exhaust": ("#737373", "url(#arrow-waste)", "4,3", "Waste / Exhaust"),
    "feedback": ("#D77C70", "url(#arrow-feedback)", "6,3", "Feedback Loop"),
    "recycle": ("#D77C70", "url(#arrow-feedback)", "6,3", "Recycle Loop"),
}

_UNIT_PALETTE: dict[str, tuple[str, str, str]] = {
    "primary": ("#547FA3", "#2E4E68", "#FFFFFF"),
    "secondary": ("#66A9B4", "#366872", "#FFFFFF"),
    "accent": ("#4E9187", "#2B5B53", "#FFFFFF"),
    "warning": ("#D19A45", "#8B601B", "#FFFFFF"),
    "muted": ("#EEF3F6", "#547FA3", "#1F2933"),
    "alert": ("#D77C70", "#8D3E35", "#FFFFFF"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _add_el(parent: _StdET.Element, tag: str, attrib: dict[str, str], text: str | None = None) -> _StdET.Element:
    el = _StdET.SubElement(parent, f"{{{SVG_NS}}}{tag}", attrib)
    if text is not None:
        el.text = text
    return el


def _add_marker(defs: _StdET.Element, mid: str, color: str, *, open_head: bool = False) -> None:
    m = _add_el(defs, "marker", {
        "id": mid, "viewBox": "0 0 10 10", "refX": "9", "refY": "5",
        "markerWidth": "7", "markerHeight": "7", "orient": "auto",
    })
    if open_head:
        _add_el(m, "path", {"d": "M 1 2 L 9 5 L 1 8", "fill": "none", "stroke": color, "stroke-width": "1.6"})
    else:
        _add_el(m, "path", {"d": "M 0 1.5 L 10 5 L 0 8.5 z", "fill": color})


def _tex_escape(s: str) -> str:
    return (
        s.replace("\\", "\\textbackslash{}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("#", "\\#")
        .replace("°", "$^\\circ$")
        .replace("μ", "$\\mu$")
    )


def _san_id(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", raw)


def build_apparatus_svg_and_tikz(spec: dict[str, Any], svg_out: Path, tex_out: Path, meta_out: Path) -> dict[str, Any]:
    """Render layered apparatus/roadmap SVG plus OpenTikZ-compatible standalone .tex & .meta.json."""
    zones = sorted(
        [z for z in (spec.get("zones") or []) if isinstance(z, dict) and z.get("id")],
        key=lambda z: float(z.get("stage_order", 0)),
    )
    units = spec.get("units") or []
    streams = spec.get("streams") or []

    default_w = max(1040, len(zones) * 285 + 60) if zones else 1040
    width = float(spec.get("canvas_width_px", default_w))
    height = float(spec.get("canvas_height_px", 660))
    title = str(spec.get("title", "Experimental Apparatus & Technical Route")).strip()
    subtitle = str(spec.get("subtitle", "")).strip()

    pad_x = 36.0
    # Generous vertical separation (>=28px baseline gap) eliminates v4 title/subtitle overlap
    top_y = 78.0 if subtitle else 52.0
    legend_h = 64.0
    has_feedback = any(str(st.get("stream_type", "")).lower() in ("feedback", "recycle") for st in streams)
    corridor_pad = 34.0 if has_feedback else 10.0
    usable_w = width - 2 * pad_x
    usable_h = height - top_y - legend_h - 18.0

    # Arrange zones left-to-right columns by stage_order and track vertical divider x-coords
    zone_boxes: dict[str, tuple[float, float, float, float]] = {}
    dividers_x: list[float] = []
    if zones:
        gap = 34.0
        zw = (usable_w - gap * (len(zones) - 1)) / len(zones)
        for idx, z in enumerate(zones):
            zid = str(z["id"])
            zx = pad_x + idx * (zw + gap)
            zone_boxes[zid] = (zx, top_y, zw, usable_h)
            if idx > 0:
                dividers_x.append(zx - gap / 2.0)
    else:
        zone_boxes["_root"] = (pad_x, top_y, usable_w, usable_h)

    by_zone: dict[str, list[dict[str, Any]]] = {zid: [] for zid in zone_boxes}
    for u in units:
        zid = str(u.get("zone", ""))
        if zid not in by_zone:
            zid = next(iter(zone_boxes))
        by_zone[zid].append(u)

    unit_geom: dict[str, dict[str, Any]] = {}
    for zid, items in by_zone.items():
        if not items:
            continue
        zx, zy, zw, zh = zone_boxes[zid]
        cnt = len(items)
        cols = 1 if (cnt <= 4 or zw < 380) else 2
        rows = math.ceil(cnt / cols)
        cell_w = (zw - 24.0) / cols
        cell_h = (zh - 44.0 - corridor_pad) / max(1, rows)
        for idx, u in enumerate(items):
            r_idx, c_idx = idx // cols, idx % cols
            uid = str(u["id"])
            step_num = u.get("step_number")
            raw_lbl = str(u.get("label", uid))
            label = f"{step_num}. {raw_lbl}" if step_num is not None and not raw_lbl.startswith(f"{step_num}.") else raw_lbl
            spec_txt = str(u.get("spec_text", "")).strip()
            min_w = max(132.0, len(label) * 7.2 + 28.0, len(spec_txt) * 6.0 + 24.0)
            uw = float(u.get("width", min(cell_w * 0.88, min_w)))
            uh = float(u.get("height", 54.0 if spec_txt else 44.0))
            cx = float(u["x"]) if "x" in u else (zx + 12.0 + (c_idx + 0.5) * cell_w)
            cy = float(u["y"]) if "y" in u else (zy + 36.0 + corridor_pad + (r_idx + 0.5) * cell_h)
            unit_geom[uid] = {
                "unit": u, "display_label": label, "cx": cx, "cy": cy, "w": uw, "h": uh,
                "x": cx - uw / 2.0, "y": cy - uh / 2.0, "zone": zid,
            }

    root = _StdET.Element(f"{{{SVG_NS}}}svg", {
        "width": f"{int(width)}px", "height": f"{int(height)}px",
        "viewBox": f"0 0 {int(width)} {int(height)}",
    })
    defs = _add_el(root, "defs", {})
    _add_marker(defs, "arrow-material", "#2E6B8E")
    _add_marker(defs, "arrow-optical", "#D19A45")
    _add_marker(defs, "arrow-signal", "#4E9187", open_head=True)
    _add_marker(defs, "arrow-control", "#967A9C")
    _add_marker(defs, "arrow-waste", "#737373", open_head=True)
    _add_marker(defs, "arrow-feedback", "#D77C70")

    # Layer 1: Background & header (zero title/subtitle collision)
    l_bg = _add_el(root, "g", {"id": "layer-background", "data-layer-name": "background"})
    _add_el(l_bg, "rect", {"id": "bg-canvas", "x": "0", "y": "0", "width": str(int(width)), "height": str(int(height)), "fill": "#FFFFFF"})
    _add_el(l_bg, "text", {
        "id": "title-main", "x": f"{pad_x:.1f}", "y": "26",
        "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
        "font-size": "15", "font-weight": "bold", "fill": "#111111",
    }, title)
    if subtitle:
        _add_el(l_bg, "text", {
            "id": "title-sub", "x": f"{pad_x:.1f}", "y": "54",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "10.5", "fill": "#555555",
        }, subtitle)

    # Layer 2: Zones
    l_zones = _add_el(root, "g", {"id": "layer-zones", "data-layer-name": "zones"})
    for z in zones:
        zid = str(z["id"])
        zx, zy, zw, zh = zone_boxes[zid]
        _add_el(l_zones, "rect", {
            "id": f"zone-{zid}",
            "data-stage-order": str(z.get("stage_order", "")),
            "data-source-ref": str(z.get("source_ref", "")),
            "x": f"{zx:.1f}", "y": f"{zy:.1f}", "width": f"{zw:.1f}", "height": f"{zh:.1f}",
            "rx": "8", "fill": str(z.get("fill", "#EDF4F5")), "stroke": str(z.get("stroke", "#547FA3")),
            "stroke-width": "1.3",
        })
        _add_el(l_zones, "text", {
            "id": f"zonelabel-{zid}", "x": f"{zx + 12:.1f}", "y": f"{zy + 20:.1f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "11", "font-weight": "bold", "fill": str(z.get("stroke", "#2E4E68")),
        }, str(z.get("label", zid)))

    # Layer 3: Streams (with zone-divider clearance for labels)
    l_streams = _add_el(root, "g", {"id": "layer-streams", "data-layer-name": "streams"})
    used_types: list[str] = []
    placed_label_boxes: list[tuple[float, float, float, float]] = []
    for idx, st in enumerate(streams):
        sid = str(st.get("id") or f"s{idx+1}")
        src, tgt = str(st["source"]), str(st["target"])
        stype = str(st.get("stream_type", "material")).lower().strip()
        if stype not in used_types:
            used_types.append(stype)
        col, mref, dash, _ = _STREAM_STYLES.get(stype, _STREAM_STYLES["material"])
        sg, tg = unit_geom[src], unit_geom[tgt]
        dx, dy = tg["cx"] - sg["cx"], tg["cy"] - sg["cy"]
        dist = max(math.hypot(dx, dy), 1e-6)
        ux, uy = dx / dist, dy / dist

        if (stype in ("feedback", "recycle") and not st.get("is_primary_forward")) or dx < -25.0:
            if sg["cy"] > top_y + usable_h * 0.52:
                # Route lower-row feedback along right margin -> top corridor -> target top (zero box crossing!)
                sx, sy = sg["x"] + sg["w"] + 2.0, sg["cy"]
                rx = min(width - pad_x - 8.0, sg["x"] + sg["w"] + 20.0)
                cy_top = top_y + 34.0
                tx, ty = tg["cx"], tg["y"] - 6.0
                d_path = f"M{sx:.1f},{sy:.1f} L{rx:.1f},{sy:.1f} L{rx:.1f},{cy_top:.1f} L{tx:.1f},{cy_top:.1f} L{tx:.1f},{ty:.1f}"
                lx, ly, anchor = (rx + tx) / 2.0, cy_top - 6.0, "middle"
            else:
                sx, sy = sg["cx"] - 14.0, sg["y"] - 2.0
                tx, ty = tg["cx"] + 14.0, tg["y"] - 6.0
                cy_ctrl = top_y + 30.0
                d_path = f"M{sx:.1f},{sy:.1f} Q{(sx + tx)/2.0:.1f},{cy_ctrl:.1f} {tx:.1f},{ty:.1f}"
                lx, ly, anchor = (sx + tx) / 2.0, 0.25 * sy + 0.5 * cy_ctrl + 0.25 * ty - 8.0, "middle"
        else:
            if abs(ux) >= abs(uy):
                sx = sg["cx"] + (1.0 if ux >= 0 else -1.0) * (sg["w"] / 2.0 + 2.0)
                sy = sg["cy"]
                tx = tg["cx"] - (1.0 if ux >= 0 else -1.0) * (tg["w"] / 2.0 + 5.0)
                ty = tg["cy"]
                lx = (sx + tx) / 2.0
                ly = (sy + ty) / 2.0 - 12.0
                anchor = "middle"
            else:
                sx = sg["cx"]
                sy = sg["cy"] + (1.0 if uy >= 0 else -1.0) * (sg["h"] / 2.0 + 2.0)
                tx = tg["cx"]
                ty = tg["cy"] - (1.0 if uy >= 0 else -1.0) * (tg["h"] / 2.0 + 5.0)
                lx = (sx + tx) / 2.0 + 12.0
                ly = (sy + ty) / 2.0 + 3.0
                anchor = "start"
            d_path = f"M{sx:.1f},{sy:.1f} L{tx:.1f},{ty:.1f}"

        attrs = {
            "id": f"stream-{sid}", "d": d_path, "fill": "none",
            "stroke": col, "stroke-width": "2.0", "marker-end": mref,
            "data-stream-type": stype, "data-source-ref": str(st.get("source_ref", "")),
            "data-drawio-cell-id": f"stream-{sid}",
            "data-source-unit-id": src, "data-target-unit-id": tgt,
        }
        if dash:
            attrs["stroke-dasharray"] = dash
        _add_el(l_streams, "path", attrs)
        lbl = str(st.get("label", "")).strip()
        if lbl:
            est_w = max(28.0, len(lbl) * 5.6)
            # Only shift clear of a vertical zone divider when there is enough horizontal gap on one side
            for div_x in dividers_x:
                if abs(lx - div_x) < est_w * 0.5 + 8.0:
                    right_room = tx - div_x - 16.0
                    left_room = div_x - sx - 16.0
                    if right_room >= est_w:
                        lx = div_x + 12.0
                        anchor = "start"
                    elif left_room >= est_w:
                        lx = div_x - 12.0
                        anchor = "end"
            # Avoid overlapping previously placed edge labels
            x0 = lx if anchor == "start" else (lx - est_w if anchor == "end" else lx - est_w / 2.0)
            for _ in range(4):
                cand = (x0, ly - 10.0, x0 + est_w, ly + 4.0)
                if any(not (cand[2] < b[0] or cand[0] > b[2] or cand[3] < b[1] or cand[1] > b[3]) for b in placed_label_boxes):
                    ly += 18.0
                else:
                    break
            placed_label_boxes.append((x0, ly - 10.0, x0 + est_w, ly + 4.0))
            _add_el(l_streams, "text", {
                "id": f"streamlabel-{sid}", "x": f"{lx:.1f}", "y": f"{ly:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": "8.6", "font-weight": "bold", "fill": col, "text-anchor": anchor,
            }, lbl)

    # Layer 4: Units & Layer 5: Ports
    l_units = _add_el(root, "g", {"id": "layer-units", "data-layer-name": "units"})
    l_ports = _add_el(root, "g", {"id": "layer-ports", "data-layer-name": "ports"})
    for uid, g in unit_geom.items():
        u = g["unit"]
        cat = str(u.get("category", "primary")).lower()
        fc, ec, tc = _UNIT_PALETTE.get(cat, _UNIT_PALETTE["primary"])
        label = g["display_label"]
        spec_txt = str(u.get("spec_text", "")).strip()
        _add_el(l_units, "rect", {
            "id": f"unit-{uid}",
            "data-drawio-cell-id": f"unit-{uid}",
            "data-role": str(u.get("role", "unit")),
            "data-source-ref": str(u.get("source_ref", "")),
            "x": f"{g['x']:.1f}", "y": f"{g['y']:.1f}", "width": f"{g['w']:.1f}", "height": f"{g['h']:.1f}",
            "rx": "6", "fill": fc, "stroke": ec, "stroke-width": "1.6",
        })
        _add_el(l_units, "text", {
            "id": f"unitlabel-{uid}",
            "data-drawio-cell-id": f"unitlabel-{uid}",
            "x": f"{g['cx']:.1f}",
            "y": f"{g['cy'] - 5.0 if spec_txt else g['cy'] + 4.0:.1f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "10.2", "font-weight": "bold", "fill": tc, "text-anchor": "middle",
        }, label)
        if spec_txt:
            _add_el(l_units, "text", {
                "id": f"unitspec-{uid}",
                "data-drawio-cell-id": f"unitspec-{uid}",
                "x": f"{g['cx']:.1f}", "y": f"{g['cy'] + 12.0:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": "8.4", "fill": tc, "text-anchor": "middle",
            }, spec_txt)

        ports = u.get("ports") or []
        if ports:
            ptxt = " | ".join(str(p) for p in ports[:3])
            _add_el(l_ports, "text", {
                "id": f"port-{uid}", "x": f"{g['x'] + 2.0:.1f}", "y": f"{g['y'] - 5.0:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": "7.5", "fill": "#475569", "text-anchor": "start",
            }, ptxt)

    # Layer 6: Legend
    l_leg = _add_el(root, "g", {"id": "layer-legend", "data-layer-name": "legend"})
    ly = height - legend_h + 6.0
    _add_el(l_leg, "rect", {
        "id": "legend-box", "x": f"{pad_x:.1f}", "y": f"{ly:.1f}",
        "width": f"{usable_w:.1f}", "height": "44", "rx": "6",
        "fill": "#FAFAFA", "stroke": "#D0D7DE", "stroke-width": "1.0",
    })
    seen_leg: list[tuple[str, str, str, str]] = []
    seen_lbls: set[str] = set()
    for stype in used_types:
        col, _, dash, desc = _STREAM_STYLES.get(stype, _STREAM_STYLES["material"])
        if desc not in seen_lbls:
            seen_lbls.add(desc)
            seen_leg.append((stype, col, dash, desc))
    step_x = (usable_w - 20.0) / max(1, len(seen_leg))
    for i, (stype, col, dash, desc) in enumerate(seen_leg):
        lx0 = pad_x + 16.0 + i * step_x
        ly0 = ly + 22.0
        la = {"id": f"legline-{i+1}", "x1": f"{lx0:.1f}", "y1": f"{ly0:.1f}", "x2": f"{lx0 + 26:.1f}", "y2": f"{ly0:.1f}", "stroke": col, "stroke-width": "2.2"}
        if dash:
            la["stroke-dasharray"] = dash
        _add_el(l_leg, "line", la)
        _add_el(l_leg, "text", {
            "id": f"legtext-{i+1}", "x": f"{lx0 + 34:.1f}", "y": f"{ly0 + 4.0:.1f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "9.8", "fill": "#222222",
        }, desc)

    svg_out.parent.mkdir(parents=True, exist_ok=True)
    _StdET.indent(root, space="  ")
    svg_out.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))

    # Emit standalone OpenTikZ-compliant .tex + .meta.json edit_contract
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in json.dumps(spec, ensure_ascii=False))
    cjk_preamble = [
        "\\ifdefined\\XeTeXrevision",
        "  \\usepackage{xeCJK}",
        "\\else",
        "  \\usepackage[UTF8]{ctex}",
        "\\fi",
    ] if has_cjk else []
    tex_lines = [
        "\\documentclass[border=10pt]{standalone}",
        "\\usepackage{tikz}",
        "\\usetikzlibrary{positioning, arrows.meta, calc, fit}",
        *cjk_preamble,
        "",
        "% Colorblind-safe Okabe-Ito / Cell named palette (zero inline hex inside tikzpicture)",
        "\\definecolor{otblue}{HTML}{547FA3}",
        "\\definecolor{otteal}{HTML}{4E9187}",
        "\\definecolor{otamber}{HTML}{D19A45}",
        "\\definecolor{otcoral}{HTML}{D77C70}",
        "\\definecolor{otpurple}{HTML}{967A9C}",
        "\\definecolor{otslate}{HTML}{64748B}",
        "\\definecolor{otzonebg}{HTML}{F4F7F9}",
        "",
        "\\begin{document}",
        "\\begin{tikzpicture}[",
        "    >={Stealth[length=2.4mm]},",
        "    font=\\sffamily,",
        "    unitbox/.style={draw=otblue!80!black, fill=otblue!14, rounded corners=4pt, minimum width=\\BoxW, minimum height=\\BoxH, align=center, font=\\sffamily\\small\\bfseries},",
        "    sensorbox/.style={draw=otteal!80!black, fill=otteal!16, rounded corners=4pt, minimum width=\\BoxW, minimum height=\\BoxH, align=center, font=\\sffamily\\small\\bfseries},",
        "    zonebox/.style={draw=otslate!60, fill=otzonebg, rounded corners=6pt, inner sep=10pt},",
        "    matflow/.style={->, draw=otblue, line width=1.4pt},",
        "    optflow/.style={->, draw=otamber, line width=1.4pt},",
        "    sigflow/.style={->, draw=otteal, dashed, line width=1.2pt},",
        "    ctrlflow/.style={->, draw=otpurple, dotted, line width=1.2pt},",
        "    feedbackflow/.style={->, draw=otcoral, dashed, line width=1.3pt},",
        "  ]",
        "  \\def\\BoxW{3.4cm}",
        "  \\def\\BoxH{1.2cm}",
        "  \\def\\StageGap{1.6cm}",
        "  \\def\\RowGap{1.0cm}",
        "",
        f"  % Title: {_tex_escape(title)}",
    ]
    # Position TikZ nodes cleanly by zone column & row so multi-unit apparatus never overlaps
    zone_order = list(zone_boxes.keys())
    col_prev_top: str | None = None
    for zid in zone_order:
        items = by_zone.get(zid) or []
        prev_in_col: str | None = None
        for r_idx, u in enumerate(items):
            uid = str(u["id"])
            safe_u = _san_id(uid)
            role = str(u.get("role", "unit")).lower()
            st_name = "sensorbox" if role in ("sensor", "detector", "analytics") else "unitbox"
            lbl = _tex_escape(unit_geom[uid]["display_label"])
            sp = _tex_escape(str(u.get("spec_text", "")))
            body = f"{lbl}\\\\\\scriptsize {sp}" if sp else lbl
            if r_idx == 0:
                pos = "" if col_prev_top is None else f"[right=\\StageGap of {col_prev_top}]"
                col_prev_top = safe_u
            else:
                pos = f"[below=\\RowGap of {prev_in_col}]"
            tex_lines.append(f"  \\node[{st_name}] ({safe_u}) {pos} {{{body}}};")
            prev_in_col = safe_u
    for st in streams:
        s_id = _san_id(str(st.get("source", "")))
        t_id = _san_id(str(st.get("target", "")))
        stype = str(st.get("stream_type", "material")).lower()
        st_cls = {
            "optical": "optflow", "laser": "optflow",
            "signal": "sigflow", "electrical": "sigflow", "data": "sigflow",
            "control": "ctrlflow",
            "feedback": "feedbackflow", "recycle": "feedbackflow",
        }.get(stype, "matflow")
        elbl = _tex_escape(str(st.get("label", "")))
        bend = "[bend right=26]" if stype in ("feedback", "recycle") else ""
        lbl_node = f" node[midway, above, font=\\sffamily\\scriptsize] {{{elbl}}}" if elbl else ""
        tex_lines.append(f"  \\draw[{st_cls}] ({s_id}) to{bend}{lbl_node} ({t_id});")
    tex_lines.extend(["\\end{tikzpicture}", "\\end{document}", ""])
    tex_out.write_text("\n".join(tex_lines), encoding="utf-8")

    meta_payload = {
        "id": _san_id(svg_out.stem),
        "name": title,
        "type": "template",
        "domain": ["experimental-apparatus", "technical-route"],
        "tags": ["apparatus", "flow-diagram", "tikz", "svg"],
        "author": "harbor-apparatus-builder",
        "license": "MIT",
        "requires": ["tikz", "ctex"] if has_cjk else ["tikz"],
        "preview": f"{svg_out.stem}.png",
        "edit_contract": {
            "parameters": [
                {"name": "\\BoxW", "type": "dimension", "meaning": "Standard unit block width", "default": "3.4cm"},
                {"name": "\\BoxH", "type": "dimension", "meaning": "Standard unit block height", "default": "1.2cm"},
                {"name": "\\StageGap", "type": "dimension", "meaning": "Horizontal separation between sequential stages", "default": "1.6cm"},
                {"name": "\\RowGap", "type": "dimension", "meaning": "Vertical separation between parallel branches", "default": "1.0cm"},
            ],
            "node_naming": "Semantic unit IDs matching SVG unit-<id>",
            "styles": ["unitbox", "sensorbox", "zonebox", "matflow", "optflow", "sigflow", "ctrlflow", "feedbackflow"],
            "operations": [
                {"id": "tune-spacing", "summary": "Adjust \\StageGap or \\RowGap without breaking relative positioning"},
                {"id": "edit-label", "summary": "Update node spec_text or stream labels matching source_ref"},
            ],
            "invariants": [
                "No inline hex colors inside tikzpicture; reference named \\definecolor entries",
                "Primary material/signal routes remain monotonically ordered from inlet to outlet",
            ],
        },
    }
    meta_out.write_text(json.dumps(meta_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"svg": str(svg_out), "tex": str(tex_out), "meta": str(meta_out), "units": len(unit_geom), "streams": len(streams)}


def _reroute_svg_streams(root: _StdET.Element, id_index: dict[str, _StdET.Element], moved_units: set[str]) -> list[dict[str, Any]]:
    rerouted: list[dict[str, Any]] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "path":
            continue
        src_u = el.get("data-source-unit-id") or ""
        tgt_u = el.get("data-target-unit-id") or ""
        if not src_u or not tgt_u or (src_u not in moved_units and tgt_u not in moved_units):
            continue
        s_el = id_index.get(f"unit-{src_u}")
        t_el = id_index.get(f"unit-{tgt_u}")
        if s_el is None or t_el is None:
            continue
        sx0, sy0, sw, sh = float(s_el.get("x")), float(s_el.get("y")), float(s_el.get("width")), float(s_el.get("height"))
        tx0, ty0, tw, th = float(t_el.get("x")), float(t_el.get("y")), float(t_el.get("width")), float(t_el.get("height"))
        scx, scy = sx0 + sw / 2.0, sy0 + sh / 2.0
        tcx, tcy = tx0 + tw / 2.0, ty0 + th / 2.0
        dx, dy = tcx - scx, tcy - scy
        dist = max(math.hypot(dx, dy), 1e-6)
        ux, uy = dx / dist, dy / dist
        old_d = el.get("d") or ""
        if "Q" in old_d:
            px1, py1 = scx - 14.0, sy0 - 2.0
            px2, py2 = tcx + 14.0, ty0 - 6.0
            cy_c = min(py1, py2) - 44.0
            new_d = f"M{px1:.1f},{py1:.1f} Q{(px1 + px2) / 2.0:.1f},{cy_c:.1f} {px2:.1f},{py2:.1f}"
        elif abs(ux) >= abs(uy):
            px1 = scx + (1.0 if ux >= 0 else -1.0) * (sw / 2.0 + 2.0)
            px2 = tcx - (1.0 if ux >= 0 else -1.0) * (tw / 2.0 + 5.0)
            new_d = f"M{px1:.1f},{scy:.1f} L{px2:.1f},{tcy:.1f}"
        else:
            py1 = scy + (1.0 if uy >= 0 else -1.0) * (sh / 2.0 + 2.0)
            py2 = tcy - (1.0 if uy >= 0 else -1.0) * (th / 2.0 + 5.0)
            new_d = f"M{scx:.1f},{py1:.1f} L{tcx:.1f},{py2:.1f}"
        el.set("d", new_d)
        rerouted.append({"op": "reroute_stream", "id": el.get("id"), "old_d": old_d, "new_d": new_d})
    return rerouted


def patch_svg_and_optional_tex(
    svg_in: Path,
    patch: dict[str, Any],
    svg_out: Path,
    *,
    tex_in: Path | None = None,
    tex_out: Path | None = None,
) -> dict[str, Any]:
    """Apply source-preserving ID-matched edits (text, attrs, move_node with edge re-routing, TikZ \\def) to SVG/TeX."""
    before_sha = _sha256(svg_in)
    raw = svg_in.read_bytes()
    root = _SafeET.fromstring(raw) if _SafeET is not None else _StdET.fromstring(raw)
    id_index = {el.get("id"): el for el in root.iter() if el.get("id")}
    changes: list[dict[str, Any]] = []

    raw_tu = patch.get("update_text", [])
    if isinstance(raw_tu, dict):
        raw_tu = [{"id": k, "text": v} for k, v in raw_tu.items()]
    for upd in raw_tu:
        tid = str(upd["id"])
        el = None
        for cand in (tid, f"unitlabel-{tid}", f"unitspec-{tid}", f"streamlabel-{tid}", f"zonelabel-{tid}"):
            if cand in id_index:
                el = id_index[cand]
                break
        if el is None:
            raise KeyError(f"UNKNOWN_ELEMENT_ID update_text={tid!r}; sample_ids={sorted(id_index)[:25]}")
        old_t = el.text or ""
        new_t = str(upd["text"])
        if old_t != new_t:
            el.text = new_t
            changes.append({"op": "update_text", "id": el.get("id"), "old": old_t, "new": new_t})

    for upd in patch.get("update_attrs", []) or []:
        tid = str(upd["id"])
        el = None
        for cand in (tid, f"unit-{tid}", f"stream-{tid}", f"zone-{tid}"):
            if cand in id_index:
                el = id_index[cand]
                break
        if el is None:
            raise KeyError(f"UNKNOWN_ELEMENT_ID update_attrs={tid!r}; sample_ids={sorted(id_index)[:25]}")
        for k, v in (upd.get("attrs") or {}).items():
            old_v = el.get(k)
            if old_v != str(v):
                el.set(k, str(v))
                changes.append({"op": "update_attr", "id": el.get("id"), "attr": k, "old": old_v, "new": str(v)})

    moved_units: set[str] = set()
    for mv in patch.get("move_node", []) or []:
        tid = str(mv["id"]).removeprefix("unit-")
        dx, dy = float(mv.get("dx", 0.0)), float(mv.get("dy", 0.0))
        moved_ids: list[str] = []
        for pfx in ("unit-", "unitlabel-", "unitspec-", "port-"):
            el = id_index.get(f"{pfx}{tid}")
            if el is None:
                continue
            moved_ids.append(f"{pfx}{tid}")
            if el.get("x") is not None:
                el.set("x", f"{float(el.get('x')) + dx:.1f}")
            if el.get("y") is not None:
                el.set("y", f"{float(el.get('y')) + dy:.1f}")
        if not moved_ids:
            raise KeyError(f"UNKNOWN_ELEMENT_ID move_node={tid!r}")
        moved_units.add(tid)
        changes.append({"op": "move_node", "id": tid, "moved_elements": moved_ids, "dx": dx, "dy": dy})
    if moved_units:
        changes.extend(_reroute_svg_streams(root, id_index, moved_units))

    svg_out.parent.mkdir(parents=True, exist_ok=True)
    _StdET.indent(root, space="  ")
    svg_out.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))
    after_ids = {el.get("id") for el in root.iter() if el.get("id")}
    preserved_ratio = round(len(set(id_index) & after_ids) / max(1, len(id_index)), 4)

    if tex_in is not None and tex_in.exists() and tex_out is not None:
        tex_src = tex_in.read_text(encoding="utf-8-sig")
        for pname, pval in (patch.get("tex_params") or {}).items():
            key = str(pname).lstrip("\\")
            pat = re.compile(rf"(\\def\s*\\{re.escape(key)}\{{)([^}}]*)(\}})")
            tex_src, n = pat.subn(rf"\g<1>{pval}\g<3>", tex_src)
            if n:
                changes.append({"op": "update_tex_param", "name": f"\\{key}", "new": str(pval)})
        tex_out.write_text(tex_src, encoding="utf-8")

    return {
        "before_sha256": before_sha,
        "after_sha256": _sha256(svg_out),
        "preserved_id_ratio": preserved_ratio,
        "changes": changes,
    }


def _try_pdflatex(tex_path: Path, out_dir: Path) -> str | None:
    tex_txt = tex_path.read_text(encoding="utf-8", errors="ignore")
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in tex_txt)
    candidates = ("xelatex", "pdflatex") if has_cjk else ("pdflatex", "xelatex")
    exe = next((shutil.which(c) for c in candidates if shutil.which(c)), None)
    if not exe:
        return None
    proc = subprocess.run(
        [exe, "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={out_dir}", str(tex_path)],
        capture_output=True, text=True, check=False, timeout=45,
    )
    pdf_cand = out_dir / f"{tex_path.stem}.pdf"
    return str(pdf_cand) if proc.returncode == 0 and pdf_cand.exists() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or source-preserving edit experimental apparatus/technical route diagrams.")
    parser.add_argument("--spec", help="Input route JSON spec for build mode")
    parser.add_argument("--edit-svg", help="Existing SVG path for source-preserving edit mode")
    parser.add_argument("--edit-tex", help="Optional companion TikZ .tex for parameter patching")
    parser.add_argument("--patch", help="Patch JSON path for edit mode")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--stem", default="apparatus_route")
    parser.add_argument("--dpi", type=int, default=300, help="Export PNG resolution in DPI (default 300; use 600 for line-art)")
    parser.add_argument("--case-json", default="", help="Optional /input/case.json path to auto-seal result.json preserving output_contract")
    parser.add_argument("--result-out", default="", help="Optional output path for sealed result.json (defaults to <out-dir>/result.json when --case-json is given)")
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
    tex_p = out_dir / f"{args.stem}.tex"
    meta_p = out_dir / f"{args.stem}.meta.json"
    case_p = Path(args.case_json) if args.case_json else None
    result_p = Path(args.result_out) if args.result_out else (out_dir / "result.json")

    if args.edit_svg:
        if not args.patch:
            raise SystemExit("--edit-svg requires --patch")
        patch_obj = json.loads(Path(args.patch).read_text(encoding="utf-8-sig"))
        try:
            diff = patch_svg_and_optional_tex(
                Path(args.edit_svg), patch_obj, svg_p,
                tex_in=Path(args.edit_tex) if args.edit_tex else None,
                tex_out=tex_p if args.edit_tex else None,
            )
        except KeyError as exc:
            print(json.dumps({"ok": False, "error": "UNKNOWN_ELEMENT_ID", "message": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        svg2drawio.svg_to_drawio_file(svg_p, drawio_p)
        drawio_bindings = bind_drawio_streams(svg_p, drawio_p)
        ren = render_svg_previews(svg_p, png_path=png_p, pdf_path=pdf_p, dpi=args.dpi)
        port = audit_portable_svg(svg_p)
        edit_rep = inspect_svg_only(svg_p)
        dl = analyze_layout(drawio_p.read_text(encoding="utf-8"), min_spacing=1.5)
        diff_p = out_dir / f"{args.stem}_edit_diff.json"
        diff_p.write_text(json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ok = bool(ren["ok"] and port["passed"] and edit_rep["status"] == "pass" and not dl["overlaps"])
        seal_rep = None
        if case_p is not None:
            arts = [svg_p.name, drawio_p.name, png_p.name, pdf_p.name, diff_p.name]
            if args.edit_tex and tex_p.exists():
                arts.append(tex_p.name)
            seal_rep = seal_case_result(
                case_p, result_p, task_id="apparatus-and-technical-route-builder",
                artifacts=arts, sources=[str(args.edit_svg), str(args.patch)],
                uncertainty=[], change_records=diff.get("changes", []),
                metrics={"preserved_id_ratio": diff.get("preserved_id_ratio", 0.0), "dpi": args.dpi},
                status="ok" if ok else "failed",
            )
        res = {
            "mode": "edit", "ok": ok,
            "edit_diff": diff, "render": ren, "portable_svg": port, "editability": edit_rep,
            "drawio_layout": dl, "drawio_bindings": drawio_bindings, "result_seal": seal_rep,
        }
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res["ok"] else 1

    if not args.spec:
        raise SystemExit("Provide --spec or --edit-svg")
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    schema_rep = validate_spec_schema(spec, "apparatus")
    if not schema_rep["passed"]:
        print(json.dumps({"mode": "build", "ok": False, "schema_validation": schema_rep}, indent=2, ensure_ascii=False))
        return 1
    lint_pre = lint_route_spec(spec)
    if not lint_pre["passed"]:
        print(json.dumps({"mode": "build", "ok": False, "schema_validation": schema_rep, "lint_pre": lint_pre}, indent=2, ensure_ascii=False))
        return 1

    built = build_apparatus_svg_and_tikz(spec, svg_p, tex_p, meta_p)
    lint_post = lint_route_spec(spec, svg_path=svg_p)
    tikz_rep = validate_tikz_pair(tex_p, meta_p)
    svg2drawio.svg_to_drawio_file(svg_p, drawio_p)
    drawio_bindings = bind_drawio_streams(svg_p, drawio_p, spec)
    first_unit = next(iter(drawio_bindings["unit_cells"].values())) if drawio_bindings["unit_cells"] else ""
    move_probe = verify_drawio_unit_move(drawio_p, first_unit) if first_unit else {}
    ren = render_svg_previews(svg_p, png_path=png_p, pdf_path=pdf_p, dpi=args.dpi)
    tikz_pdf = _try_pdflatex(tex_p, out_dir)
    port = audit_portable_svg(svg_p)
    edit_rep = inspect_svg_only(svg_p)
    dl = analyze_layout(drawio_p.read_text(encoding="utf-8"), min_spacing=1.5)

    ok = (
        lint_post["passed"] and tikz_rep["passed"] and ren["ok"]
        and port["passed"] and edit_rep["status"] == "pass"
        and len(dl["overlaps"]) == 0 and len(dl["out_of_bounds"]) == 0
    )
    seal_rep = None
    if case_p is not None:
        srcs = sorted({str(x.get("source_ref", "")).strip() for x in (spec.get("units") or []) + (spec.get("streams") or []) + (spec.get("zones") or []) if isinstance(x, dict) and str(x.get("source_ref", "")).strip()})
        uncert = [str(g.get("message", g.get("code", ""))) for g in (lint_post.get("gaps") or [])]
        if (port.get("font_audit") or {}).get("warnings"):
            uncert.extend(port["font_audit"]["warnings"])
        seal_rep = seal_case_result(
            case_p, result_p, task_id="apparatus-and-technical-route-builder",
            artifacts=[svg_p.name, drawio_p.name, png_p.name, pdf_p.name, tex_p.name, meta_p.name],
            sources=srcs, uncertainty=uncert,
            change_records=[{"op": "build_apparatus", "units": built["units"], "streams": built["streams"], "dpi": args.dpi}],
            metrics={"units": built["units"], "streams": built["streams"], "dpi": args.dpi, "tikz_pdflatex_compiled": bool(tikz_pdf)},
            status="ok" if ok else "failed",
        )
    res = {
        "mode": "build", "ok": ok, "schema_validation": schema_rep, "build": built,
        "lint_post": lint_post, "tikz_contract": tikz_rep, "tikz_pdflatex_pdf": tikz_pdf,
        "render": ren, "portable_svg": port, "editability": edit_rep, "drawio_layout": dl,
        "drawio_bindings": drawio_bindings, "drawio_move_probe": move_probe, "result_seal": seal_rep,
    }
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
