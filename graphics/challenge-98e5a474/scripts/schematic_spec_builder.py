#!/usr/bin/env python3
"""Scientific relationship/hierarchy/workflow schematic builder with CSV/JSON legend binding, native Draw.io graph binding, and asset license provenance."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import xml.etree.ElementTree as _StdET
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from asset_license_audit import audit_asset_licenses
from audit_editability import inspect_svg_only
from contract_sealer import seal_case_result, validate_spec_schema
from drawio_graph_binding import bind_drawio_graph, verify_drawio_move_persistence
from layout_tools import analyze_layout
from portable_svg_audit import audit_portable_svg
from render_preview import render_svg_previews
import svg2drawio

SVG_NS = "http://www.w3.org/2000/svg"
_StdET.register_namespace("", SVG_NS)

# Cell-oriented, colorblind-aware scientific palette (matches Cell / Okabe-Ito visual grammar)
_CATEGORY_COLORS = {
    "primary": ("#547FA3", "#2E4E68", "#FFFFFF"),
    "secondary": ("#66A9B4", "#35666E", "#FFFFFF"),
    "teal": ("#4E9187", "#2B5B53", "#FFFFFF"),
    "accent": ("#4E9187", "#2B5B53", "#FFFFFF"),
    "sage": ("#83A47B", "#4A6943", "#FFFFFF"),
    "highlight": ("#D77C70", "#8D433A", "#FFFFFF"),
    "alert": ("#D77C70", "#8D433A", "#FFFFFF"),
    "warm": ("#D19A45", "#8A6122", "#FFFFFF"),
    "warning": ("#D19A45", "#8A6122", "#FFFFFF"),
    "purple": ("#967A9C", "#5E4763", "#FFFFFF"),
    "neutral": ("#F4F6F8", "#547FA3", "#1A1A1A"),
    "muted": ("#EEF3F6", "#64748B", "#1F2933"),
}

_REL_STYLES = {
    "causal": ("#2E6B8E", "url(#arrow-activate)", ""),
    "activate": ("#2E6B8E", "url(#arrow-activate)", ""),
    "sequence": ("#4A5568", "url(#arrow-activate)", ""),
    "inhibit": ("#B85F56", "url(#arrow-inhibit)", ""),
    "parallel": ("#4E9187", "url(#arrow-bind)", "5,3"),
    "feedback": ("#D19A45", "url(#arrow-feedback)", "6,4"),
    "uncertain": ("#737373", "url(#arrow-open)", "5,3"),
}

_BACKEND_LEAK_RE = re.compile(
    r"\b(?:QC_[A-Za-z0-9_]+|post_basic|preselector|in-sample|defined_epithelial|tmp_cluster|debug_)\b|#[0-9A-Fa-f]{6}\b",
    re.IGNORECASE,
)


def _add_el(parent: _StdET.Element, tag: str, attrib: dict[str, str] | None = None, text: str | None = None) -> _StdET.Element:
    el = _StdET.SubElement(parent, f"{{{SVG_NS}}}{tag}", attrib or {})
    if text is not None:
        el.text = text
    return el


def _load_data_table(table_path: Path | None) -> dict[str, dict[str, str]]:
    if table_path is None or not table_path.is_file():
        return {}
    ext = table_path.suffix.lower()
    rows: dict[str, dict[str, str]] = {}
    if ext == ".json":
        obj = json.loads(table_path.read_text(encoding="utf-8-sig"))
        if isinstance(obj, list):
            for r in obj:
                if isinstance(r, dict) and r.get("id"):
                    rows[str(r["id"])] = {str(k): str(v) for k, v in r.items()}
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, dict):
                    rows[str(k)] = {str(kk): str(vv) for kk, vv in v.items()}
        return rows
    delim = "\t" if ext == ".tsv" else ","
    with table_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delim)
        for row in reader:
            rid = str(row.get("id") or row.get("entity_id") or row.get("node_id") or "").strip()
            if rid:
                rows[rid] = {str(k): str(v) for k, v in row.items() if k is not None}
    return rows


def _check_backend_leaks(root: _StdET.Element) -> list[dict[str, str]]:
    leaks: list[dict[str, str]] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "text":
            txt = "".join(el.itertext()).strip()
            if txt and _BACKEND_LEAK_RE.search(txt):
                leaks.append({"id": el.get("id") or "", "text": txt})
    return leaks


def build_schematic_svg(
    spec: dict[str, Any],
    svg_out: Path,
    *,
    data_table: Path | None = None,
) -> dict[str, Any]:
    """Generate a layered relationship/hierarchy/workflow schematic SVG bound to source specs & optional data table."""
    width = float(spec.get("canvas_width_px", 1000))
    height = float(spec.get("canvas_height_px", 660))
    title = str(spec.get("title", "Scientific Relationship Schematic")).strip()
    subtitle = str(spec.get("subtitle", "")).strip()

    containers = spec.get("containers") or []
    nodes = spec.get("nodes") or []
    edges = spec.get("edges") or []
    legend = list(spec.get("legend") or [])
    table_rows = _load_data_table(data_table)

    pad_x = 36.0
    # Generous vertical separation between title and subtitle prevents Draw.io text-box overlap
    top_y = 74.0 if subtitle else 50.0
    legend_h = 64.0
    has_feedback = any(str(e.get("relation", "")).lower() == "feedback" for e in edges)
    corridor_pad = 22.0 if has_feedback else 0.0
    usable_w = width - 2 * pad_x
    usable_h = height - top_y - legend_h - 16.0

    # Compute container boxes
    cont_boxes: dict[str, tuple[float, float, float, float]] = {}
    dividers_x: list[float] = []
    if containers:
        layout_dir = str(spec.get("container_layout", "columns")).lower()
        n_c = len(containers)
        gap = 28.0 if layout_dir != "rows" else 18.0
        if layout_dir == "rows":
            ch = (usable_h - gap * (n_c - 1)) / n_c
            for i, c in enumerate(containers):
                cid = str(c["id"])
                cont_boxes[cid] = (pad_x, top_y + i * (ch + gap), usable_w, ch)
        else:
            cw = (usable_w - gap * (n_c - 1)) / n_c
            for i, c in enumerate(containers):
                cid = str(c["id"])
                bx = pad_x + i * (cw + gap)
                cont_boxes[cid] = (bx, top_y, cw, usable_h)
                if i > 0:
                    dividers_x.append(bx - gap / 2.0)
    else:
        cont_boxes["_root"] = (pad_x, top_y, usable_w, usable_h)

    by_cont: dict[str, list[dict[str, Any]]] = {cid: [] for cid in cont_boxes}
    first_cid = next(iter(cont_boxes))
    for n in nodes:
        cid = str(n.get("container", first_cid))
        if cid not in by_cont:
            cid = first_cid
        by_cont[cid].append(n)

    node_geom: dict[str, dict[str, Any]] = {}
    for cid, items in by_cont.items():
        if not items:
            continue
        bx, by, bw, bh = cont_boxes[cid]
        cnt = len(items)
        cols = 1 if (bw < 400 or cnt <= 3) else min(3, max(1, math.ceil(math.sqrt(cnt))))
        rows = math.ceil(cnt / cols)
        cell_w = (bw - 28.0) / cols
        cell_h = max(76.0, (bh - 52.0 - corridor_pad) / rows)
        for i, n in enumerate(items):
            r_idx, c_idx = i // cols, i % cols
            nid = str(n["id"])
            row = table_rows.get(nid, {})
            label_runs = n.get("label_runs")
            if isinstance(label_runs, list) and label_runs:
                label = "".join(str(r.get("text", "")) for r in label_runs)
            else:
                label = str(n.get("label", nid))
            val_col = str(n.get("value_field", "value"))
            disp_val = row.get(val_col) if row.get(val_col) not in (None, "") else n.get("value", "")
            unit = str(row.get("unit") or n.get("unit") or "").strip()
            val_txt = f"{disp_val} {unit}".strip() if str(disp_val) != "" else ""
            w_est = max(136.0, len(label) * 7.6 + 34.0, len(val_txt) * 6.8 + 26.0)
            w = float(n.get("width", min(cell_w * 0.88, w_est)))
            h = float(n.get("height", 50.0 if val_txt else 42.0))
            cx = float(n["x"]) if "x" in n else (bx + 14.0 + (c_idx + 0.5) * cell_w)
            cy = float(n["y"]) if "y" in n else (by + 42.0 + corridor_pad + (r_idx + 0.5) * cell_h)
            node_geom[nid] = {
                "node": n, "cx": cx, "cy": cy, "w": w, "h": h,
                "x": cx - w / 2.0, "y": cy - h / 2.0,
            }

    root = _StdET.Element(f"{{{SVG_NS}}}svg", {
        "width": f"{width:.0f}px",
        "height": f"{height:.0f}px",
        "viewBox": f"0 0 {width:.0f} {height:.0f}",
        "version": "1.1",
    })

    defs = _add_el(root, "defs")
    m_act = _add_el(defs, "marker", {"id": "arrow-activate", "markerWidth": "10", "markerHeight": "10", "refX": "9", "refY": "3.5", "orient": "auto"})
    _add_el(m_act, "path", {"d": "M0,0 L9,3.5 L0,7 Z", "fill": "#2E6B8E"})
    m_inh = _add_el(defs, "marker", {"id": "arrow-inhibit", "markerWidth": "8", "markerHeight": "14", "refX": "4", "refY": "7", "orient": "auto"})
    _add_el(m_inh, "line", {"x1": "4", "y1": "1", "x2": "4", "y2": "13", "stroke": "#B85F56", "stroke-width": "2.4"})
    m_bind = _add_el(defs, "marker", {"id": "arrow-bind", "markerWidth": "8", "markerHeight": "8", "refX": "4", "refY": "4", "orient": "auto"})
    _add_el(m_bind, "circle", {"cx": "4", "cy": "4", "r": "3", "fill": "#4E9187"})
    m_fb = _add_el(defs, "marker", {"id": "arrow-feedback", "markerWidth": "10", "markerHeight": "10", "refX": "9", "refY": "3.5", "orient": "auto"})
    _add_el(m_fb, "path", {"d": "M0,0 L9,3.5 L0,7 Z", "fill": "#D19A45"})
    m_op = _add_el(defs, "marker", {"id": "arrow-open", "markerWidth": "10", "markerHeight": "10", "refX": "9", "refY": "3.5", "orient": "auto"})
    _add_el(m_op, "path", {"d": "M0,0.5 L9,3.5 L0,6.5", "fill": "none", "stroke": "#737373", "stroke-width": "1.6"})

    # Layer 1: background
    l_bg = _add_el(root, "g", {"id": "layer-background", "data-layer-name": "background"})
    _add_el(l_bg, "rect", {"id": "bg-canvas", "x": "0", "y": "0", "width": f"{width:.0f}", "height": f"{height:.0f}", "fill": "#FFFFFF"})
    _add_el(l_bg, "text", {
        "id": "title-main", "x": f"{pad_x:.0f}", "y": "26",
        "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
        "font-size": "15", "font-weight": "bold", "fill": "#1A1A1A",
    }, title)
    if subtitle:
        _add_el(l_bg, "text", {
            "id": "title-sub", "x": f"{pad_x:.0f}", "y": "54",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "10.5", "fill": "#555555",
        }, subtitle)

    # Layer 2: containers
    l_cont = _add_el(root, "g", {"id": "layer-containers", "data-layer-name": "compartments"})
    for c in containers:
        cid = str(c["id"])
        bx, by, bw, bh = cont_boxes[cid]
        c_attrs = {
            "id": f"cont-{cid}",
            "data-source-ref": str(c.get("source_ref", "")),
            "x": f"{bx:.1f}", "y": f"{by:.1f}", "width": f"{bw:.1f}", "height": f"{bh:.1f}",
            "rx": "8", "fill": str(c.get("fill", "#EDF4F5")), "stroke": str(c.get("stroke", "#547FA3")),
            "stroke-width": "1.3", "stroke-dasharray": "5,3" if c.get("dashed") else "none",
        }
        if c.get("asset_id"):
            c_attrs["data-asset-id"] = str(c["asset_id"])
        _add_el(l_cont, "rect", c_attrs)
        _add_el(l_cont, "text", {
            "id": f"contlabel-{cid}", "x": f"{bx + 12:.1f}", "y": f"{by + 20:.1f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "11", "font-weight": "bold", "fill": str(c.get("stroke", "#2E4E68")),
        }, str(c.get("label", cid)))

    # Layer 3: edges (with explicit Draw.io cell IDs & source/target node references)
    l_edges = _add_el(root, "g", {"id": "layer-edges", "data-layer-name": "edges"})
    spec_edges_map: dict[str, tuple[str, str]] = {}
    for i, edge in enumerate(edges):
        ed_id = str(edge.get("id", f"e{i+1}"))
        s_id, t_id = str(edge["source"]), str(edge["target"])
        if s_id not in node_geom or t_id not in node_geom:
            raise ValueError(f"Edge {ed_id!r} references missing node {s_id!r} or {t_id!r}")
        spec_edges_map[ed_id] = (s_id, t_id)
        rel = str(edge.get("relation", "causal")).lower()
        uncertain = bool(edge.get("uncertain", False))
        col, mref, dash = _REL_STYLES.get("uncertain" if uncertain else rel, _REL_STYLES["causal"])
        sg, tg = node_geom[s_id], node_geom[t_id]
        dx, dy = tg["cx"] - sg["cx"], tg["cy"] - sg["cy"]
        dist = max(math.hypot(dx, dy), 1e-6)
        ux, uy = dx / dist, dy / dist

        if rel == "feedback" or (dx < -25.0 and abs(dy) < 90.0):
            if sg["cy"] > top_y + usable_h * 0.45:
                # Route lower-row feedback cleanly along outer right margin -> top corridor -> target (avoiding nodes above target)
                sx, sy = sg["x"] + sg["w"] + 2.0, sg["cy"]
                rx = min(width - pad_x - 8.0, sg["x"] + sg["w"] + 22.0)
                cy_top = top_y + 34.0
                has_blocker_above = any(
                    oid != t_id and og["y"] < tg["y"] and abs(og["cx"] - tg["cx"]) < (og["w"] * 0.6)
                    for oid, og in node_geom.items()
                )
                if has_blocker_above:
                    lx_drop = max(pad_x + 8.0, tg["x"] - 22.0)
                    tx, ty = tg["x"] - 6.0, tg["cy"]
                    d_path = f"M{sx:.1f},{sy:.1f} L{rx:.1f},{sy:.1f} L{rx:.1f},{cy_top:.1f} L{lx_drop:.1f},{cy_top:.1f} L{lx_drop:.1f},{ty:.1f} L{tx:.1f},{ty:.1f}"
                    lx, ly, anchor = (rx + lx_drop) / 2.0, cy_top - 6.0, "middle"
                else:
                    tx, ty = tg["cx"], tg["y"] - 6.0
                    d_path = f"M{sx:.1f},{sy:.1f} L{rx:.1f},{sy:.1f} L{rx:.1f},{cy_top:.1f} L{tx:.1f},{cy_top:.1f} L{tx:.1f},{ty:.1f}"
                    lx, ly, anchor = (rx + tx) / 2.0, cy_top - 6.0, "middle"
            else:
                sx, sy = sg["cx"] - 12.0, sg["y"] - 2.0
                tx, ty = tg["cx"] + 12.0, tg["y"] - 6.0
                cy_ctrl = top_y + 26.0
                d_path = f"M{sx:.1f},{sy:.1f} Q{(sx + tx) / 2.0:.1f},{cy_ctrl:.1f} {tx:.1f},{ty:.1f}"
                lx = (sx + tx) / 2.0
                ly = 0.25 * sy + 0.5 * cy_ctrl + 0.25 * ty - 8.0
                anchor = "middle"
        else:
            if abs(ux) >= abs(uy):
                sx = sg["cx"] + (1.0 if ux >= 0 else -1.0) * (sg["w"] * 0.5 + 2.0)
                sy = sg["cy"]
                tx = tg["cx"] - (1.0 if ux >= 0 else -1.0) * (tg["w"] * 0.5 + 5.0)
                ty = tg["cy"]
                lx = (sx + tx) / 2.0
                ly = (sy + ty) / 2.0 - 11.0
                anchor = "middle"
            else:
                sx = sg["cx"]
                sy = sg["cy"] + (1.0 if uy >= 0 else -1.0) * (sg["h"] * 0.5 + 2.0)
                tx = tg["cx"]
                ty = tg["cy"] - (1.0 if uy >= 0 else -1.0) * (tg["h"] * 0.5 + 5.0)
                lx = (sx + tx) / 2.0 + 12.0
                ly = (sy + ty) / 2.0 + 3.0
                anchor = "start"
            # Avoid placing edge label directly on a vertical container divider
            for div_x in dividers_x:
                if abs(lx - div_x) < 18.0:
                    lx = div_x + 22.0 if lx >= div_x else div_x - 22.0
            d_path = f"M{sx:.1f},{sy:.1f} L{tx:.1f},{ty:.1f}"

        attrs = {
            "id": f"edge-{ed_id}",
            "data-drawio-cell-id": f"edge-{ed_id}",
            "data-source-node-id": s_id,
            "data-target-node-id": t_id,
            "d": d_path,
            "fill": "none", "stroke": col, "stroke-width": str(edge.get("stroke_width", 2.0)),
            "marker-end": mref, "data-relation": rel,
            "data-source-ref": str(edge.get("source_ref", "")),
        }
        if dash:
            attrs["stroke-dasharray"] = dash
        _add_el(l_edges, "path", attrs)
        elbl = str(edge.get("label", "")).strip()
        if elbl:
            _add_el(l_edges, "text", {
                "id": f"edgelabel-{ed_id}", "x": f"{lx:.1f}", "y": f"{ly:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": "8.8", "font-weight": "bold", "fill": col, "text-anchor": anchor,
            }, elbl)

    # Layer 4: nodes (merging optional CSV/JSON data_table values)
    l_nodes = _add_el(root, "g", {"id": "layer-nodes", "data-layer-name": "nodes"})
    data_bound_count = 0
    used_categories: dict[str, str] = {}
    for nid, g in node_geom.items():
        n = g["node"]
        row = table_rows.get(nid, {})
        cat = str(row.get("category") or n.get("category", "primary")).lower()
        fc, ec, tc = _CATEGORY_COLORS.get(cat, _CATEGORY_COLORS["primary"])
        fc = str(row.get("color") or n.get("color") or fc)
        used_categories[cat] = fc
        val_col = str(n.get("value_field", "value"))
        disp_val = row.get(val_col) if row.get(val_col) not in (None, "") else (n.get("value") if n.get("value") is not None else "")
        unit = str(row.get("unit") or n.get("unit") or "").strip()
        label = str(n.get("label", nid))
        if str(disp_val) != "":
            data_bound_count += 1
        shape = str(n.get("shape", "roundrect")).lower()
        common = {
            "id": f"node-{nid}",
            "data-drawio-cell-id": f"node-{nid}",
            "data-category": cat,
            "data-value": str(disp_val),
            "data-unit": unit,
            "data-source-ref": str(row.get("source_ref") or n.get("source_ref", "")),
        }
        if n.get("asset_id"):
            common["data-asset-id"] = str(n["asset_id"])
        if shape == "ellipse":
            _add_el(l_nodes, "ellipse", {
                **common, "cx": f"{g['cx']:.1f}", "cy": f"{g['cy']:.1f}",
                "rx": f"{g['w']/2:.1f}", "ry": f"{g['h']/2:.1f}",
                "fill": fc, "stroke": ec, "stroke-width": "1.6",
            })
        else:
            _add_el(l_nodes, "rect", {
                **common, "x": f"{g['x']:.1f}", "y": f"{g['y']:.1f}",
                "width": f"{g['w']:.1f}", "height": f"{g['h']:.1f}",
                "rx": "7" if shape == "roundrect" else "2",
                "fill": fc, "stroke": ec, "stroke-width": "1.6",
            })
        text_y = g["cy"] - 3.0 if str(disp_val) != "" else g["cy"] + 4.0
        label_runs = n.get("label_runs")
        if isinstance(label_runs, list) and label_runs:
            txt_el = _add_el(l_nodes, "text", {
                "id": f"nodelabel-{nid}", "x": f"{g['cx']:.1f}", "y": f"{text_y:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": "10.5", "fill": tc, "text-anchor": "middle",
            })
            for ri, run in enumerate(label_runs):
                t_attrs = {"id": str(run.get("id") or f"nodelabel-{nid}-span-{ri+1}")}
                if run.get("font_weight"):
                    t_attrs["font-weight"] = str(run["font_weight"])
                if run.get("font_style"):
                    t_attrs["font-style"] = str(run["font_style"])
                if run.get("fill"):
                    t_attrs["fill"] = str(run["fill"])
                _add_el(txt_el, "tspan", t_attrs, str(run.get("text", "")))
        else:
            _add_el(l_nodes, "text", {
                "id": f"nodelabel-{nid}",
                "data-drawio-cell-id": f"nodelabel-{nid}",
                "x": f"{g['cx']:.1f}", "y": f"{text_y:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": "10.5", "font-weight": "bold", "fill": tc, "text-anchor": "middle",
            }, label)
        if str(disp_val) != "":
            val_txt = f"{disp_val} {unit}".strip()
            _add_el(l_nodes, "text", {
                "id": f"nodeval-{nid}",
                "data-drawio-cell-id": f"nodeval-{nid}",
                "x": f"{g['cx']:.1f}", "y": f"{g['cy'] + 13.0:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": "9.2", "fill": tc, "text-anchor": "middle",
            }, val_txt)

    # Layer 5: legend (synchronized with bound categories)
    l_leg = _add_el(root, "g", {"id": "layer-legend", "data-layer-name": "legend"})
    ly = height - legend_h + 8.0
    _add_el(l_leg, "rect", {
        "id": "legend-box", "x": f"{pad_x:.0f}", "y": f"{ly:.1f}", "width": f"{usable_w:.0f}", "height": "44",
        "rx": "6", "fill": "#FAFAFA", "stroke": "#D0D7DE", "stroke-width": "1.0",
    })
    leg_items = legend or [
        {"key": cat, "label": cat.replace("_", " ").title(), "color": col}
        for cat, col in used_categories.items()
    ]
    # Audit legend-vs-data color consistency
    legend_mismatches: list[dict[str, str]] = []
    leg_by_key = {str(it.get("key", "")).lower(): str(it.get("color", "")).upper() for it in leg_items}
    for cat, col in used_categories.items():
        if cat in leg_by_key and leg_by_key[cat] != col.upper():
            legend_mismatches.append({"category": cat, "data_color": col, "legend_color": leg_by_key[cat]})

    step_x = (usable_w - 24.0) / max(1, len(leg_items))
    for i, item in enumerate(leg_items):
        lx = pad_x + 16.0 + i * step_x
        col = str(item.get("color", "#547FA3"))
        key = str(item.get("key", f"item-{i+1}"))
        _add_el(l_leg, "rect", {
            "id": f"legswatch-{key}", "x": f"{lx:.1f}", "y": f"{ly + 15:.1f}",
            "width": "14", "height": "14", "rx": "3", "fill": col, "stroke": "#333333", "stroke-width": "0.8",
        })
        _add_el(l_leg, "text", {
            "id": f"leglabel-{key}", "x": f"{lx + 21:.1f}", "y": f"{ly + 26:.1f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "10.0", "fill": "#333333",
        }, str(item.get("label", key)))

    backend_leaks = _check_backend_leaks(root)
    svg_out.parent.mkdir(parents=True, exist_ok=True)
    # Avoid indenting when structured tspans exist so authored whitespace stays exact
    has_structured_runs = any(isinstance(n.get("label_runs"), list) and n.get("label_runs") for n in nodes)
    if not has_structured_runs:
        _StdET.indent(root, space="  ")
    svg_out.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))
    return {
        "svg": str(svg_out),
        "nodes": len(node_geom),
        "edges": len(edges),
        "data_bound_nodes": data_bound_count,
        "spec_nodes": sorted(node_geom),
        "spec_edges": spec_edges_map,
        "legend_mismatches": legend_mismatches,
        "backend_leaks": backend_leaks,
    }


def _resolve_dpi(dpi_arg: int, preset_name: str) -> int:
    if preset_name:
        presets_path = HERE.parent / "references" / "export_presets.json"
        if presets_path.is_file():
            presets = json.loads(presets_path.read_text(encoding="utf-8")).get("presets", {})
            if preset_name in presets and isinstance(presets[preset_name].get("dpi"), int):
                return int(presets[preset_name]["dpi"])
    return int(dpi_arg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build layered editable scientific schematic SVG + native .drawio + PNG/PDF + license manifest.")
    parser.add_argument("--spec", required=True, help="Schematic spec JSON")
    parser.add_argument("--data-table", default="", help="Optional CSV/JSON data table driving node values & colors")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stem", default="schematic_figure")
    parser.add_argument("--dpi", type=int, default=300, help="Export PNG resolution in DPI (default 300; use 600 for line-art)")
    parser.add_argument("--preset", default="", help="Optional export preset name from references/export_presets.json")
    parser.add_argument("--case-json", default="", help="Optional /input/case.json path to auto-seal result.json preserving output_contract")
    parser.add_argument("--result-out", default="", help="Optional output path for sealed result.json")
    args = parser.parse_args(argv)

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    schema_rep = validate_spec_schema(spec, "schematic")
    if not schema_rep["passed"]:
        print(json.dumps({"ok": False, "schema_validation": schema_rep}, indent=2, ensure_ascii=False))
        return 1

    dpi_val = _resolve_dpi(args.dpi, args.preset)
    dt_path = Path(args.data_table) if args.data_table else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    svg_p = out_dir / f"{args.stem}.svg"
    drawio_p = out_dir / f"{args.stem}.drawio"
    png_p = out_dir / f"{args.stem}.png"
    pdf_p = out_dir / f"{args.stem}.pdf"
    lic_p = out_dir / f"{args.stem}_license_manifest.json"
    case_p = Path(args.case_json) if args.case_json else None
    result_p = Path(args.result_out) if args.result_out else (out_dir / "result.json")

    build_info = build_schematic_svg(spec, svg_p, data_table=dt_path)
    license_rep = audit_asset_licenses(spec, svg_path=svg_p)
    lic_p.write_text(json.dumps(license_rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    svg2drawio.svg_to_drawio_file(svg_p, drawio_p)
    drawio_binding = bind_drawio_graph(
        svg_p,
        drawio_p,
        spec_nodes=set(build_info["spec_nodes"]),
        spec_edges=build_info["spec_edges"],
    )
    move_probe = {}
    if build_info["spec_nodes"] and build_info["spec_edges"]:
        first_node = next(iter(build_info["spec_edges"].values()))[0]
        move_probe = verify_drawio_move_persistence(drawio_p, f"node-{first_node}")

    render_info = render_svg_previews(svg_p, png_path=png_p, pdf_path=pdf_p, dpi=dpi_val)
    portable = audit_portable_svg(svg_p)
    editability = inspect_svg_only(svg_p)
    drawio_chk = analyze_layout(drawio_p.read_text(encoding="utf-8"), min_spacing=2.0)

    ok = (
        render_info["ok"]
        and portable["passed"]
        and editability["status"] == "pass"
        and license_rep["passed"]
        and drawio_binding["status"] == "pass"
        and len(build_info["legend_mismatches"]) == 0
        and len(build_info["backend_leaks"]) == 0
        and len(drawio_chk["overlaps"]) == 0
        and len(drawio_chk["out_of_bounds"]) == 0
    )
    seal_rep = None
    if case_p is not None:
        srcs = sorted({str(x.get("source_ref", "")).strip() for x in (spec.get("nodes") or []) + (spec.get("edges") or []) + (spec.get("containers") or []) if isinstance(x, dict) and str(x.get("source_ref", "")).strip()})
        uncert = list(schema_rep.get("warnings") or []) + list(render_info.get("degraded_warnings") or [])
        if (portable.get("font_audit") or {}).get("warnings"):
            uncert.extend(portable["font_audit"]["warnings"])
        seal_rep = seal_case_result(
            case_p, result_p, task_id="scientific-schematic-generator-editor",
            artifacts=[svg_p.name, drawio_p.name, png_p.name, pdf_p.name, lic_p.name],
            sources=srcs, uncertainty=uncert,
            change_records=[{"op": "build_schematic", "nodes": build_info["nodes"], "edges": build_info["edges"], "dpi": dpi_val, "preset": args.preset or "default_300dpi"}],
            metrics={"nodes": build_info["nodes"], "edges": build_info["edges"], "dpi": dpi_val, "data_bound_nodes": build_info["data_bound_nodes"]},
            status="ok" if ok else "failed",
        )
    print(json.dumps({
        "ok": ok,
        "schema_validation": schema_rep,
        "build": build_info,
        "license_audit": license_rep,
        "drawio_binding": drawio_binding,
        "drawio_move_probe": move_probe,
        "render": render_info,
        "portable_svg": portable,
        "editability": editability,
        "drawio_layout": drawio_chk,
        "result_seal": seal_rep,
    }, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
