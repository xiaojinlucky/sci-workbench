#!/usr/bin/env python3
"""Deterministic layered SVG + native Draw.io + librsvg PNG/PDF builder & source-preserving editor for biological pathways."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as _StdET
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from audit_editability import inspect_svg_only
from contract_sealer import seal_case_result, validate_spec_schema
from drawio_pathway_binding import PathwayBindingError, bind_drawio_pathway, verify_drawio_pathway_move
from layout_tools import analyze_layout
from pathway_topology_linter import lint_pathway_spec
from portable_svg_audit import audit_portable_svg
from render_preview import render_svg_previews
import svg2drawio

try:
    import defusedxml.ElementTree as _SafeET
except ImportError:
    _SafeET = None

SVG_NS = "http://www.w3.org/2000/svg"
_StdET.register_namespace("", SVG_NS)

_COMP_STYLE = {
    "extracellular": ("#F8FAFC", "#8EAAC2", "Extracellular Space"),
    "plasma_membrane": ("#F6F1E8", "#D19A45", "Plasma Membrane"),
    "membrane": ("#F6F1E8", "#D19A45", "Membrane"),
    "cytoplasm": ("#EDF4F5", "#547FA3", "Cytoplasm"),
    "cytosol": ("#EDF4F5", "#547FA3", "Cytosol"),
    "mitochondria": ("#F7EFE7", "#DF986D", "Mitochondria"),
    "er": ("#EEF5F0", "#4E9187", "Endoplasmic Reticulum"),
    "nucleus": ("#EFEBF5", "#967A9C", "Nucleus"),
}

_ENTITY_STYLE = {
    "ligand": ("#83A47B", "#4A6943", "#FFFFFF", "ellipse"),
    "receptor": ("#547FA3", "#2E4E68", "#FFFFFF", "roundrect"),
    "protein": ("#66A9B4", "#35666E", "#FFFFFF", "roundrect"),
    "complex": ("#4E9187", "#2B5B53", "#FFFFFF", "roundrect"),
    "family": ("#967A9C", "#5E4763", "#FFFFFF", "roundrect"),
    "gene": ("#FFFFFF", "#547FA3", "#1A1A1A", "rect"),
    "metabolite": ("#D19A45", "#8A6122", "#FFFFFF", "ellipse"),
    "rna": ("#DF986D", "#945734", "#FFFFFF", "roundrect"),
    "cell_type": ("#EDF4F5", "#547FA3", "#1A1A1A", "ellipse"),
    "process": ("#F6F1E8", "#B85F56", "#1A1A1A", "roundrect"),
}

_POLARITY_STYLE = {
    "activate": ("#2E6B8E", "url(#arrow-activate)", ""),
    "inhibit": ("#B85F56", "url(#arrow-inhibit)", ""),
    "bind": ("#4E9187", "url(#arrow-bind)", ""),
    "modify": ("#D19A45", "url(#arrow-modify)", ""),
    "transport": ("#547FA3", "url(#arrow-transport)", "6,4"),
    "transcribe": ("#967A9C", "url(#arrow-transcribe)", "4,3"),
    "secrete": ("#83A47B", "url(#arrow-transport)", "6,4"),
}

_NA_TOKENS = {"", "na", "n/a", "nan", "null", "none", "nd"}


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _tier_rank(comp_name: str) -> int:
    low = comp_name.lower()
    if "extra" in low:
        return 0
    if "membrane" in low:
        return 1
    if "cyto" in low or "er" in low or "mito" in low or "golgi" in low:
        return 2
    if "nuc" in low:
        return 3
    return 2


def _add_el(parent: _StdET.Element, tag: str, attrib: dict[str, str] | None = None, text: str | None = None) -> _StdET.Element:
    el = _StdET.SubElement(parent, f"{{{SVG_NS}}}{tag}", attrib or {})
    if text is not None:
        el.text = text
    return el


def _lerp_hex(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> str:
    t = max(0.0, min(1.0, t))
    r = int(round(c1[0] + (c2[0] - c1[0]) * t))
    g = int(round(c1[1] + (c2[1] - c1[1]) * t))
    b = int(round(c1[2] + (c2[2] - c1[2]) * t))
    return f"#{r:02X}{g:02X}{b:02X}"


def _diverging_color(val: float | None, clamp: float = 2.0) -> tuple[str, str]:
    """Cell blue-white-red diverging ramp (#3F6FAE -> #F7F7F5 -> #C6544D) with distinct NA gray (#737373)."""
    if val is None:
        return ("#737373", "#FFFFFF")
    lo = (63, 111, 174)
    mid = (247, 247, 245)
    hi = (198, 84, 77)
    t = max(-1.0, min(1.0, val / max(clamp, 1e-6)))
    if t < 0:
        fc = _lerp_hex(mid, lo, -t)
        tc = "#FFFFFF" if -t > 0.45 else "#1A1A1A"
    else:
        fc = _lerp_hex(mid, hi, t)
        tc = "#FFFFFF" if t > 0.45 else "#1A1A1A"
    return (fc, tc)


def _load_data_table(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.is_file():
        return {}
    if path.suffix.lower() == ".json":
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(obj, list):
            return {str(r.get("id") or r.get("entity_id")): {str(k): "" if v is None else str(v) for k, v in r.items()} for r in obj if isinstance(r, dict) and (r.get("id") or r.get("entity_id"))}
        return {str(k): {str(kk): "" if vv is None else str(vv) for kk, vv in v.items()} for k, v in obj.items() if isinstance(v, dict)}
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    out: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=delim):
            rid = str(row.get("id") or row.get("entity_id") or "").strip()
            if rid:
                out[rid] = {str(k): ("" if v is None else str(v)) for k, v in row.items() if k is not None}
    return out


def _parse_float(raw: str) -> float | None:
    s = raw.strip()
    if s.lower() in _NA_TOKENS:
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except ValueError:
        return None


def build_pathway_svg(spec: dict[str, Any], svg_out: Path, *, data_table: Path | None = None) -> dict[str, Any]:
    """Generate a layered, source-grounded, editable biological pathway SVG with diverging expression encoding and feedback corridors."""
    width = float(spec.get("canvas_width_px", 1040))
    height = float(spec.get("canvas_height_px", 720))
    title = str(spec.get("title", "Biological Mechanism Pathway")).strip()
    subtitle = str(spec.get("subtitle", "")).strip()

    compartments = spec.get("compartments") or [
        {"id": "extracellular", "kind": "extracellular", "label": "Extracellular Space"},
        {"id": "plasma_membrane", "kind": "plasma_membrane", "label": "Plasma Membrane"},
        {"id": "cytoplasm", "kind": "cytoplasm", "label": "Cytoplasm"},
        {"id": "nucleus", "kind": "nucleus", "label": "Nucleus"},
    ]
    entities = spec.get("entities") or []
    edges = spec.get("edges") or []
    legend = spec.get("legend") or []
    table_rows = _load_data_table(data_table)
    has_expr = bool(table_rows)
    expr_clamp = float(spec.get("expression_clamp", 2.0))
    expr_col = str(spec.get("expression_field", "log2fc"))
    expr_unit = str(spec.get("expression_unit", "log2FC"))

    comp_sorted = sorted(compartments, key=lambda c: _tier_rank(str(c.get("kind", c.get("id", "")))))

    pad_x = 36.0
    right_corridor = 68.0
    top_y = 74.0 if subtitle else 50.0
    legend_h = 84.0 if has_expr else 64.0
    n_fb_est = sum(1 for e in edges if bool(e.get("feedback")))
    right_corridor = max(float(spec.get("right_corridor", 110.0)), 54.0 + max(n_fb_est, 1) * 20.0)
    if right_corridor > 140.0:
        width = max(width, float(spec.get("width", 1120)) + (right_corridor - 110.0))
    usable_w = width - 2 * pad_x
    band_w = usable_w - right_corridor
    usable_h = height - top_y - legend_h - 18.0

    weights = []
    for c in comp_sorted:
        k = str(c.get("kind", c.get("id", ""))).lower()
        weights.append(0.14 if "membrane" in k else (0.18 if "extra" in k else (0.38 if "cyto" in k else 0.30)))
    w_sum = sum(weights) or 1.0

    comp_boxes: dict[str, tuple[float, float, float, float]] = {}
    comp_rank_by_id: dict[str, int] = {}
    cur_y = top_y
    for c, wt in zip(comp_sorted, weights):
        cid = str(c["id"])
        bh = usable_h * (wt / w_sum)
        comp_boxes[cid] = (pad_x, cur_y, band_w, bh - 8.0)
        comp_rank_by_id[cid] = _tier_rank(str(c.get("kind", cid)))
        cur_y += bh

    by_comp: dict[str, list[dict[str, Any]]] = {str(c["id"]): [] for c in comp_sorted}
    fallback_comp = str(comp_sorted[min(2, len(comp_sorted) - 1)]["id"])
    for e in entities:
        cid = str(e.get("compartment", fallback_comp))
        if cid not in by_comp:
            by_comp.setdefault(cid, [])
            if cid not in comp_boxes:
                comp_boxes[cid] = comp_boxes[fallback_comp]
                comp_rank_by_id[cid] = 2
        by_comp[cid].append(e)

    node_geom: dict[str, dict[str, Any]] = {}
    for cid, items in by_comp.items():
        if not items:
            continue
        bx, by, bw, bh = comp_boxes[cid]
        n = len(items)
        cols = min(max(1, n), 4)
        rows = math.ceil(n / cols)
        cell_w = (bw - 44.0) / cols
        cell_h = max(54.0, (bh - 30.0) / rows)
        for idx, ent in enumerate(items):
            r_idx = idx // cols
            c_idx = idx % cols
            eid = str(ent["id"])
            has_val = eid in table_rows
            # High-density auto-compaction so dense compartments never burst their bounding box
            compact_mode = (rows * (46.0 if has_val else 38.0) + 34.0) > bh
            default_h = (36.0 if has_val else 30.0) if compact_mode else (46.0 if has_val else 38.0)
            w = float(ent.get("width", min(cell_w * 0.78, max(98.0 if compact_mode else 106.0, len(str(ent["name"])) * 7.8 + 26.0))))
            h = float(ent.get("height", min(default_h, max(28.0, cell_h * 0.76))))
            cx = float(ent["x"]) if "x" in ent else (bx + 22.0 + (c_idx + 0.5) * cell_w)
            cy = float(ent["y"]) if "y" in ent else (by + 28.0 + (r_idx + 0.5) * cell_h)
            node_geom[eid] = {
                "ent": ent, "cx": cx, "cy": cy, "w": w, "h": h,
                "x": cx - w / 2.0, "y": cy - h / 2.0, "comp": cid,
                "rank": comp_rank_by_id.get(cid, 2),
                "compacted": compact_mode,
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
    m_mod = _add_el(defs, "marker", {"id": "arrow-modify", "markerWidth": "10", "markerHeight": "10", "refX": "9", "refY": "3.5", "orient": "auto"})
    _add_el(m_mod, "path", {"d": "M0,0 L9,3.5 L0,7 Z", "fill": "#D19A45"})
    m_inh = _add_el(defs, "marker", {"id": "arrow-inhibit", "markerWidth": "8", "markerHeight": "14", "refX": "4", "refY": "7", "orient": "auto"})
    _add_el(m_inh, "line", {"x1": "4", "y1": "1", "x2": "4", "y2": "13", "stroke": "#B85F56", "stroke-width": "2.4"})
    m_inh_u = _add_el(defs, "marker", {"id": "arrow-inhibit-uncertain", "markerWidth": "10", "markerHeight": "14", "refX": "5", "refY": "7", "orient": "auto"})
    _add_el(m_inh_u, "line", {"x1": "5", "y1": "1", "x2": "5", "y2": "13", "stroke": "#B85F56", "stroke-width": "2.2", "stroke-dasharray": "3,2"})
    m_bind = _add_el(defs, "marker", {"id": "arrow-bind", "markerWidth": "8", "markerHeight": "8", "refX": "4", "refY": "4", "orient": "auto"})
    _add_el(m_bind, "circle", {"cx": "4", "cy": "4", "r": "3", "fill": "#4E9187"})
    m_tr = _add_el(defs, "marker", {"id": "arrow-transport", "markerWidth": "10", "markerHeight": "10", "refX": "9", "refY": "3.5", "orient": "auto"})
    _add_el(m_tr, "path", {"d": "M0,0 L9,3.5 L0,7", "fill": "none", "stroke": "#547FA3", "stroke-width": "1.8"})
    m_tx = _add_el(defs, "marker", {"id": "arrow-transcribe", "markerWidth": "10", "markerHeight": "10", "refX": "9", "refY": "3.5", "orient": "auto"})
    _add_el(m_tx, "path", {"d": "M0,0 L9,3.5 L0,7 Z", "fill": "#967A9C"})

    # Layer 1: Background & title/subtitle
    layer_bg = _add_el(root, "g", {"id": "layer-background", "data-layer-name": "background"})
    _add_el(layer_bg, "rect", {"id": "bg-canvas", "x": "0", "y": "0", "width": f"{width:.0f}", "height": f"{height:.0f}", "fill": "#FFFFFF"})
    _add_el(layer_bg, "text", {
        "id": "title-main", "x": f"{pad_x:.0f}", "y": "26",
        "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
        "font-size": "15", "font-weight": "bold", "fill": "#1A1A1A",
    }, title)
    if subtitle:
        _add_el(layer_bg, "text", {
            "id": "title-sub", "x": f"{pad_x:.0f}", "y": "54",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "10.5", "fill": "#555555",
        }, subtitle)

    # Layer 2: Compartments & Membrane
    layer_comp = _add_el(root, "g", {"id": "layer-compartments", "data-layer-name": "compartments"})
    for c in comp_sorted:
        cid = str(c["id"])
        kind = str(c.get("kind", cid)).lower()
        fc, ec, def_lbl = _COMP_STYLE.get(kind, ("#EDF4F5", "#547FA3", cid.title()))
        lbl = str(c.get("label", def_lbl))
        bx, by, bw, bh = comp_boxes[cid]
        is_nuc = "nuc" in kind
        is_mem = "membrane" in kind
        _add_el(layer_comp, "rect", {
            "id": f"comp-{cid}",
            "data-bio-role": "compartment",
            "x": f"{bx:.1f}", "y": f"{by:.1f}", "width": f"{bw:.1f}", "height": f"{bh:.1f}",
            "rx": "10" if not is_mem else "4",
            "fill": fc, "stroke": ec,
            "stroke-width": "2.0" if is_nuc else "1.4",
            "stroke-dasharray": "6,3" if is_nuc else "none",
        })
        if is_mem:
            lbl_reserve = max(135.0, len(lbl) * 7.8 + 24.0)
            n_heads = max(10, int((bw - lbl_reserve) // 24))
            for hi in range(n_heads):
                hx = bx + lbl_reserve + hi * ((bw - lbl_reserve - 16.0) / max(1, n_heads - 1))
                _add_el(layer_comp, "circle", {"cx": f"{hx:.1f}", "cy": f"{by + 6:.1f}", "r": "3.2", "fill": "#D19A45", "stroke": "#8A6122", "stroke-width": "0.6"})
                _add_el(layer_comp, "circle", {"cx": f"{hx:.1f}", "cy": f"{by + bh - 6:.1f}", "r": "3.2", "fill": "#D19A45", "stroke": "#8A6122", "stroke-width": "0.6"})
        _add_el(layer_comp, "text", {
            "id": f"comp-label-{cid}",
            "x": f"{bx + 12:.1f}", "y": f"{by + 19:.1f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "11", "font-weight": "bold", "fill": ec,
        }, lbl)

    # Layer 3: Edges (feedback loops routed cleanly via right outer corridor!)
    layer_edges = _add_el(root, "g", {"id": "layer-edges", "data-layer-name": "edges"})
    fb_lane = 0
    for idx, edge in enumerate(edges):
        raw_id = str(edge.get("id", f"e{idx+1}")).removeprefix("edge-")
        ed_id_attr = f"edge-{raw_id}"
        s_id = str(edge["source"])
        t_id = str(edge["target"])
        pol = str(edge.get("polarity", "activate")).lower()
        uncertain = bool(edge.get("uncertain", False))
        col, marker_ref, dash = _POLARITY_STYLE.get(pol, ("#2E6B8E", "url(#arrow-activate)", ""))
        if uncertain:
            # Separate polarity from certainty: NEVER replace inhibit T-bar with an open arrow!
            dash = "4,3"
            if pol == "inhibit":
                marker_ref = "url(#arrow-inhibit-uncertain)"
        sg, tg = node_geom[s_id], node_geom[t_id]
        dx, dy = tg["cx"] - sg["cx"], tg["cy"] - sg["cy"]
        dist = max(math.hypot(dx, dy), 1e-6)
        ux, uy = dx / dist, dy / dist

        is_upward_feedback = bool(edge.get("feedback")) or (sg["rank"] > tg["rank"] and dy < -40.0)
        if is_upward_feedback:
            # Orthogonal right-corridor feedback path clears all intermediate compartment boxes
            corridor_x = pad_x + band_w + 22.0 + fb_lane * 18.0
            fb_lane += 1
            sx, sy = sg["x"] + sg["w"] + 2.0, sg["cy"]
            tx, ty = tg["x"] + tg["w"] + 6.0, tg["cy"]
            d_path = f"M{sx:.1f},{sy:.1f} L{corridor_x:.1f},{sy:.1f} L{corridor_x:.1f},{ty:.1f} L{tx:.1f},{ty:.1f}"
            mx, my, anchor = corridor_x - 6.0, (sy + ty) / 2.0, "end"
        elif abs(dy) < 12.0 and abs(dx) > 260.0:
            # Same-row non-adjacent fan-out (e.g., TF -> multiple nuclear target genes): arc below row cleanly
            sx, sy = sg["cx"], sg["y"] + sg["h"] + 2.0
            tx, ty = tg["cx"], tg["y"] + tg["h"] + 6.0
            drop = 28.0 + min(30.0, abs(dx) * 0.045)
            cy_arc = max(sy, ty) + drop
            d_path = f"M{sx:.1f},{sy:.1f} Q{(sx + tx) / 2.0:.1f},{cy_arc:.1f} {tx:.1f},{ty:.1f}"
            mx, my, anchor = (sx + tx) / 2.0, 0.25 * sy + 0.5 * cy_arc + 0.25 * ty + 10.0, "middle"
        else:
            if abs(ux) > abs(uy) * 1.1:
                sx = sg["cx"] + (1.0 if ux >= 0 else -1.0) * (sg["w"] * 0.5 + 2.0)
                sy = sg["cy"]
                tx = tg["cx"] - (1.0 if ux >= 0 else -1.0) * (tg["w"] * 0.5 + 5.0)
                ty = tg["cy"]
                mx, my, anchor = (sx + tx) / 2.0, (sy + ty) / 2.0 - 9.0, "middle"
            else:
                sx = sg["cx"]
                sy = sg["cy"] + (1.0 if uy >= 0 else -1.0) * (sg["h"] * 0.5 + 2.0)
                tx = tg["cx"]
                ty = tg["cy"] - (1.0 if uy >= 0 else -1.0) * (tg["h"] * 0.5 + 5.0)
                mx = (sx + tx) / 2.0 + 10.0
                # Keep extracellular->membrane labels slightly higher, clear of lipid bilayer heads
                my = (sy + 14.0) if (sg["rank"] == 0 and tg["rank"] == 1) else ((sy + ty) / 2.0 + 3.0)
                anchor = "start"
            d_path = f"M{sx:.1f},{sy:.1f} L{tx:.1f},{ty:.1f}"

        attrs = {
            "id": ed_id_attr,
            "data-drawio-cell-id": ed_id_attr,
            "data-source-node-id": s_id,
            "data-target-node-id": t_id,
            "d": d_path,
            "fill": "none", "stroke": col, "stroke-width": str(edge.get("stroke_width", 2.0)),
            "marker-end": marker_ref,
            "data-polarity": pol,
            "data-uncertain": "true" if uncertain else "false",
            "data-source-ref": str(edge.get("source_ref", "")),
        }
        if dash:
            attrs["stroke-dasharray"] = dash
        _add_el(layer_edges, "path", attrs)
        elabel = str(edge.get("label", "")).strip()
        if elabel:
            _add_el(layer_edges, "text", {
                "id": f"edgelabel-{raw_id}",
                "x": f"{mx:.1f}", "y": f"{my:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": "9.0", "fill": col, "font-weight": "bold", "text-anchor": anchor,
            }, elabel)

    # Layer 4: Biological Entities (Nodes) with optional diverging expression + padj significance encoding
    layer_nodes = _add_el(root, "g", {"id": "layer-nodes", "data-layer-name": "nodes"})
    data_bound_count = 0
    na_count = 0
    zero_count = 0
    sig_count = 0
    clamped_nodes: list[dict[str, Any]] = []
    for eid, g in node_geom.items():
        ent = g["ent"]
        etype = str(ent.get("entity_type", "protein")).lower()
        fc, ec, tc, shape = _ENTITY_STYLE.get(etype, ("#66A9B4", "#35666E", "#FFFFFF", "roundrect"))
        fc = str(ent.get("color", fc))
        val_txt = ""
        sig_star = ""
        node_sw = "1.6"
        if eid in table_rows:
            row = table_rows[eid]
            raw_v = row.get(expr_col, "")
            num_v = _parse_float(raw_v)
            fc, tc = _diverging_color(num_v, clamp=expr_clamp)
            if num_v is not None and abs(num_v) > expr_clamp:
                clamped_nodes.append({
                    "node_id": eid,
                    "raw_value": num_v,
                    "clamped_to": expr_clamp if num_v > 0 else -expr_clamp,
                    "unit": expr_unit,
                })
            padj_raw = next((row[k] for k in row if k.strip().lower() in ("padj", "fdr", "qval", "p_adj")), "")
            padj_v = _parse_float(padj_raw) if padj_raw else None
            if padj_v is not None and padj_v < 0.05:
                sig_count += 1
                sig_star = "***" if padj_v < 0.001 else ("**" if padj_v < 0.01 else "*")
                node_sw = "2.4"
                ec = "#1E293B"
            if num_v is None:
                val_txt = "NA"
                na_count += 1
            else:
                if num_v == 0.0:
                    zero_count += 1
                sign = "+" if num_v > 0 else ""
                val_txt = f"{sign}{num_v:.2f} {expr_unit}{(' ' + sig_star) if sig_star else ''}"
            data_bound_count += 1

        attrs_common = {
            "id": f"node-{eid}",
            "data-drawio-cell-id": f"node-{eid}",
            "data-bio-role": str(ent.get("role", etype)),
            "data-entity-type": etype,
            "data-compartment": str(ent.get("compartment", "")),
            "data-source-ref": str(ent.get("source_ref", "")),
        }
        if sig_star:
            attrs_common["data-padj-sig"] = sig_star
        if shape == "ellipse":
            _add_el(layer_nodes, "ellipse", {
                **attrs_common,
                "cx": f"{g['cx']:.1f}", "cy": f"{g['cy']:.1f}",
                "rx": f"{g['w']/2:.1f}", "ry": f"{g['h']/2:.1f}",
                "fill": fc, "stroke": ec, "stroke-width": node_sw,
            })
        else:
            _add_el(layer_nodes, "rect", {
                **attrs_common,
                "x": f"{g['x']:.1f}", "y": f"{g['y']:.1f}",
                "width": f"{g['w']:.1f}", "height": f"{g['h']:.1f}",
                "rx": "8" if shape == "roundrect" else "2",
                "fill": fc, "stroke": ec, "stroke-width": node_sw,
            })
        txt_attrs = {
            "id": f"nodelabel-{eid}",
            "data-drawio-cell-id": f"nodelabel-{eid}",
            "x": f"{g['cx']:.1f}",
            "y": f"{g['cy'] - 3.0 if val_txt else g['cy'] + 4.0:.1f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "10.8", "font-weight": "bold",
            "fill": tc, "text-anchor": "middle",
        }
        if etype == "gene":
            txt_attrs["font-style"] = "italic"
        _add_el(layer_nodes, "text", txt_attrs, str(ent["name"]))
        if val_txt:
            _add_el(layer_nodes, "text", {
                "id": f"nodeval-{eid}",
                "data-drawio-cell-id": f"nodeval-{eid}",
                "x": f"{g['cx']:.1f}", "y": f"{g['cy'] + 12.0:.1f}",
                "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
                "font-size": "8.4", "fill": tc, "text-anchor": "middle",
            }, val_txt)

    # Layer 4b: Post-translational modification badges
    layer_mods = _add_el(root, "g", {"id": "layer-modifications", "data-layer-name": "modifications"})
    for eid, g in node_geom.items():
        mod = str(g["ent"].get("modification", "")).strip()
        if mod:
            bx_m = g["x"] + g["w"] - 3.0
            by_m = g["y"] + 3.0
            _add_el(layer_mods, "circle", {"id": f"mod-{eid}", "cx": f"{bx_m:.1f}", "cy": f"{by_m:.1f}", "r": "10", "fill": "#FFF7E6", "stroke": "#D19A45", "stroke-width": "1.2"})
            _add_el(layer_mods, "text", {"id": f"modtxt-{eid}", "x": f"{bx_m:.1f}", "y": f"{by_m + 3.5:.1f}", "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif", "font-size": "8.2", "font-weight": "bold", "fill": "#6B4810", "text-anchor": "middle"}, mod)

    # Layer 5: Legend (row 1: interaction polarity; optional row 2: diverging expression colorbar + NA)
    layer_leg = _add_el(root, "g", {"id": "layer-legend", "data-layer-name": "legend"})
    ly = height - legend_h + 8.0
    leg_box_h = 66.0 if has_expr else 44.0
    _add_el(layer_leg, "rect", {"id": "legend-box", "x": f"{pad_x:.0f}", "y": f"{ly:.1f}", "width": f"{usable_w:.0f}", "height": f"{leg_box_h:.0f}", "rx": "6", "fill": "#FAFAFA", "stroke": "#D0D7DE", "stroke-width": "1.0"})
    default_leg = [
        {"label": "Activation (->)", "color": "#2E6B8E", "kind": "activate"},
        {"label": "Inhibition (-|)", "color": "#B85F56", "kind": "inhibit"},
        {"label": "Binding / Complex", "color": "#4E9187", "kind": "bind"},
        {"label": "Transcription (-->)", "color": "#967A9C", "kind": "transcribe"},
        {"label": "Transport / Uncertain", "color": "#547FA3", "kind": "transport"},
    ]
    leg_items = legend if legend else default_leg
    step_x = (usable_w - 24.0) / max(1, len(leg_items))
    for i, item in enumerate(leg_items):
        lx = pad_x + 14.0 + i * step_x
        col = str(item.get("color", "#547FA3"))
        lbl = str(item.get("label", item.get("key", f"Item {i+1}")))
        kind = str(item.get("kind", "rect")).lower()
        if kind in ("activate", "inhibit", "bind", "transport", "transcribe"):
            m_ref = f"url(#arrow-{kind})"
            dash_attr = {"stroke-dasharray": "4,3"} if kind == "transcribe" else ({"stroke-dasharray": "5,3"} if kind == "transport" else {})
            _add_el(layer_leg, "line", {
                "x1": f"{lx:.1f}", "y1": f"{ly + 20:.1f}", "x2": f"{lx + 24:.1f}", "y2": f"{ly + 20:.1f}",
                "stroke": col, "stroke-width": "2.0", "marker-end": m_ref, **dash_attr,
            })
        else:
            _add_el(layer_leg, "rect", {"x": f"{lx:.1f}", "y": f"{ly + 13:.1f}", "width": "14", "height": "14", "rx": "3", "fill": col, "stroke": "#333333", "stroke-width": "0.8"})
        _add_el(layer_leg, "text", {
            "x": f"{lx + 30:.1f}", "y": f"{ly + 24:.1f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "9.6", "fill": "#333333",
        }, lbl)

    if has_expr:
        cy_bar = ly + 44.0
        _add_el(layer_leg, "text", {
            "id": "expr-scale-title", "x": f"{pad_x + 14.0:.1f}", "y": f"{cy_bar + 10:.1f}",
            "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif",
            "font-size": "9.0", "font-weight": "bold", "fill": "#1A1A1A",
        }, f"Fill ({expr_unit}, clamp ±{expr_clamp:g}):")
        swatches = [(-expr_clamp, f"-{expr_clamp:g}"), (-expr_clamp / 2, f"-{expr_clamp/2:g}"), (0.0, "0.0"), (expr_clamp / 2, f"+{expr_clamp/2:g}"), (expr_clamp, f"+{expr_clamp:g}")]
        sx0 = pad_x + 195.0
        for si, (sv, slbl) in enumerate(swatches):
            scol, _ = _diverging_color(sv, clamp=expr_clamp)
            _add_el(layer_leg, "rect", {"x": f"{sx0 + si * 58:.1f}", "y": f"{cy_bar:.1f}", "width": "16", "height": "12", "rx": "2", "fill": scol, "stroke": "#555555", "stroke-width": "0.6"})
            _add_el(layer_leg, "text", {"x": f"{sx0 + si * 58 + 20:.1f}", "y": f"{cy_bar + 10:.1f}", "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif", "font-size": "8.8", "fill": "#333333"}, slbl)
        # Explicit NA swatch separate from 0.0 + padj significance key
        nax = sx0 + len(swatches) * 58 + 14.0
        _add_el(layer_leg, "rect", {"x": f"{nax:.1f}", "y": f"{cy_bar:.1f}", "width": "14", "height": "12", "rx": "2", "fill": "#737373", "stroke": "#333333", "stroke-width": "0.6"})
        _add_el(layer_leg, "text", {"x": f"{nax + 18:.1f}", "y": f"{cy_bar + 10:.1f}", "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif", "font-size": "8.4", "fill": "#333333"}, "NA")
        _add_el(layer_leg, "text", {"x": f"{nax + 44:.1f}", "y": f"{cy_bar + 10:.1f}", "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif", "font-size": "8.4", "font-weight": "bold", "fill": "#1E293B"}, "* padj<0.05 (bold border)")
        _add_el(layer_leg, "text", {"x": f"{nax + 178:.1f}", "y": f"{cy_bar + 10:.1f}", "font-family": "Arial, Liberation Sans, DejaVu Sans, sans-serif", "font-size": "8.4", "font-style": "italic", "fill": "#547FA3"}, "Italic=Gene | Upright=Protein")

    svg_out.parent.mkdir(parents=True, exist_ok=True)
    _StdET.indent(root, space="  ")
    svg_out.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))
    compacted_comps = sorted({g["comp"] for g in node_geom.values() if g.get("compacted")})
    return {
        "svg": str(svg_out), "nodes": len(node_geom), "edges": len(edges),
        "compartments": len(comp_sorted), "data_bound_nodes": data_bound_count,
        "significant_padj_nodes": sig_count,
        "explicit_zero_nodes": zero_count, "na_nodes": na_count,
        "clamped_nodes": clamped_nodes,
        "compacted_compartments": compacted_comps,
    }


def _reroute_pathway_edges(root: _StdET.Element, id_index: dict[str, _StdET.Element], moved: set[str]) -> list[dict[str, Any]]:
    rerouted: list[dict[str, Any]] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "path":
            continue
        src = el.get("data-source-node-id") or ""
        tgt = el.get("data-target-node-id") or ""
        if not src or not tgt or (src not in moved and tgt not in moved):
            continue
        s_el = id_index.get(f"node-{src}")
        t_el = id_index.get(f"node-{tgt}")
        if s_el is None or t_el is None:
            continue
        def _box(e):
            if e.tag.rsplit("}", 1)[-1] == "ellipse":
                return float(e.get("cx")), float(e.get("cy")), float(e.get("rx")) * 2, float(e.get("ry")) * 2
            x, y, w, h = float(e.get("x")), float(e.get("y")), float(e.get("width")), float(e.get("height"))
            return x + w / 2.0, y + h / 2.0, w, h
        scx, scy, sw, sh = _box(s_el)
        tcx, tcy, tw, th = _box(t_el)
        dx, dy = tcx - scx, tcy - scy
        dist = max(math.hypot(dx, dy), 1e-6)
        ux, uy = dx / dist, dy / dist
        old_d = el.get("d") or ""
        if old_d.count("L") >= 3:
            # Keep right-corridor x from existing orthogonal feedback path
            parts = old_d.replace("M", "").split("L")
            cx_corr = float(parts[1].strip().split(",")[0])
            new_d = f"M{scx + sw/2 + 2:.1f},{scy:.1f} L{cx_corr:.1f},{scy:.1f} L{cx_corr:.1f},{tcy:.1f} L{tcx + tw/2 + 6:.1f},{tcy:.1f}"
        elif abs(ux) > abs(uy) * 1.1:
            sx = scx + (1.0 if ux >= 0 else -1.0) * (sw / 2.0 + 2.0)
            tx = tcx - (1.0 if ux >= 0 else -1.0) * (tw / 2.0 + 5.0)
            new_d = f"M{sx:.1f},{scy:.1f} L{tx:.1f},{tcy:.1f}"
        else:
            sy = scy + (1.0 if uy >= 0 else -1.0) * (sh / 2.0 + 2.0)
            ty = tcy - (1.0 if uy >= 0 else -1.0) * (th / 2.0 + 5.0)
            new_d = f"M{scx:.1f},{sy:.1f} L{tcx:.1f},{ty:.1f}"
        el.set("d", new_d)
        rerouted.append({"op": "reroute_edge", "id": el.get("id"), "old_d": old_d, "new_d": new_d})
    return rerouted


def source_preserving_patch_svg(svg_in: Path, patch: dict[str, Any], svg_out: Path) -> dict[str, Any]:
    """Apply ID-preserving edits (text, attrs, move_node with edge re-routing) to an existing biological SVG."""
    before_sha = _sha256(svg_in)
    raw = svg_in.read_bytes()
    root = _SafeET.fromstring(raw) if _SafeET is not None else _StdET.fromstring(raw)
    id_index = {el.get("id"): el for el in root.iter() if el.get("id")}
    changes: list[dict[str, Any]] = []

    raw_updates = patch.get("update_text", [])
    if isinstance(raw_updates, dict):
        raw_updates = [{"id": k, "text": v} for k, v in raw_updates.items()]
    for upd in raw_updates:
        tid = str(upd["id"])
        el = None
        for cand in (tid, f"nodelabel-{tid}", f"nodeval-{tid}", f"edgelabel-{tid}"):
            if cand in id_index:
                el = id_index[cand]
                break
        if el is None:
            raise KeyError(f"UNKNOWN_ELEMENT_ID update_text={tid!r}; sample_ids={sorted(id_index)[:25]}")
        old_t = el.text or ""
        el.text = str(upd["text"])
        changes.append({"op": "update_text", "id": el.get("id"), "old": old_t, "new": el.text})

    for upd in patch.get("update_attrs", []) or []:
        tid = str(upd["id"])
        el = None
        for cand in (tid, f"node-{tid}", f"edge-{tid}"):
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

    moved: set[str] = set()
    for mv in patch.get("move_node", []) or []:
        tid = str(mv["id"]).removeprefix("node-")
        dx, dy = float(mv.get("dx", 0.0)), float(mv.get("dy", 0.0))
        moved_ids: list[str] = []
        for pfx in ("node-", "nodelabel-", "nodeval-", "mod-", "modtxt-"):
            el = id_index.get(f"{pfx}{tid}")
            if el is None:
                continue
            moved_ids.append(f"{pfx}{tid}")
            for k, d in (("x", dx), ("cx", dx), ("y", dy), ("cy", dy)):
                if el.get(k) is not None:
                    el.set(k, f"{float(el.get(k)) + d:.1f}")
        if not moved_ids:
            raise KeyError(f"UNKNOWN_ELEMENT_ID move_node={tid!r}")
        moved.add(tid)
        changes.append({"op": "move_node", "id": tid, "moved_elements": moved_ids, "dx": dx, "dy": dy})
    if moved:
        changes.extend(_reroute_pathway_edges(root, id_index, moved))

    svg_out.parent.mkdir(parents=True, exist_ok=True)
    _StdET.indent(root, space="  ")
    svg_out.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))
    after_ids = {el.get("id") for el in root.iter() if el.get("id")}
    preserved_ratio = round(len(set(id_index) & after_ids) / max(1, len(id_index)), 4)
    return {
        "before_sha256": before_sha,
        "after_sha256": _sha256(svg_out),
        "preserved_id_ratio": preserved_ratio,
        "changes": changes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or source-preserving edit a biological mechanism diagram.")
    parser.add_argument("--spec", default="", help="Path to pathway_spec.json")
    parser.add_argument("--data-table", default="", help="Optional CSV/JSON expression/log2FC table")
    parser.add_argument("--edit-svg", default="", help="Existing SVG to patch in source-preserving mode")
    parser.add_argument("--patch", default="", help="JSON patch file for --edit-svg mode")
    parser.add_argument("--out-dir", default="", help="Output directory")
    parser.add_argument("--stem", default="pathway_figure", help="Base filename without extension")
    parser.add_argument("--dpi", type=int, default=300, help="Export PNG resolution in DPI (default 300)")
    parser.add_argument("--case-json", default="", help="Optional /input/case.json path to auto-seal result.json preserving output_contract")
    parser.add_argument("--result-out", default="", help="Optional output path for sealed result.json")
    parser.add_argument("--list-ids", action="store_true", help="Print existing SVG element IDs for --edit-svg and exit")
    args = parser.parse_args(argv)

    if args.list_ids and args.edit_svg:
        r_tmp = _SafeET.fromstring(Path(args.edit_svg).read_bytes()) if _SafeET is not None else _StdET.fromstring(Path(args.edit_svg).read_bytes())
        print(json.dumps({"ok": True, "ids": sorted({e.get("id") for e in r_tmp.iter() if e.get("id")})}, ensure_ascii=False, indent=2))
        return 0
    if not args.out_dir:
        parser.error("--out-dir is required")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / f"{args.stem}.svg"
    drawio_path = out_dir / f"{args.stem}.drawio"
    png_path = out_dir / f"{args.stem}.png"
    pdf_path = out_dir / f"{args.stem}.pdf"
    case_p = Path(args.case_json) if args.case_json else None
    result_p = Path(args.result_out) if args.result_out else (out_dir / "result.json")

    summary: dict[str, Any] = {"mode": "edit" if args.edit_svg else "build"}
    spec_obj = json.loads(Path(args.spec).read_text(encoding="utf-8-sig")) if args.spec else None
    dt_p = Path(args.data_table) if args.data_table else None
    dt_rows = _load_data_table(dt_p) if dt_p else {}

    if args.edit_svg:
        patch_obj = json.loads(Path(args.patch).read_text(encoding="utf-8-sig"))
        try:
            diff = source_preserving_patch_svg(Path(args.edit_svg), patch_obj, svg_path)
        except KeyError as exc:
            print(json.dumps({"ok": False, "error": "UNKNOWN_ELEMENT_ID", "message": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        summary["edit_diff"] = diff
        (out_dir / f"{args.stem}_edit_diff.json").write_text(json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if spec_obj is not None:
            summary["topology_lint"] = lint_pathway_spec(spec_obj, svg_path=svg_path, data_table_rows=dt_rows or None)
    else:
        if spec_obj is None:
            parser.error("Either --spec or --edit-svg + --patch is required")
        schema_rep = validate_spec_schema(spec_obj, "pathway")
        summary["schema_validation"] = schema_rep
        if not schema_rep["passed"]:
            print(json.dumps({"ok": False, "stage": "schema_validation", **summary}, indent=2, ensure_ascii=False))
            return 1
        lint_pre = lint_pathway_spec(spec_obj, data_table_rows=dt_rows or None)
        summary["topology_lint_pre"] = lint_pre
        if not lint_pre["passed"]:
            print(json.dumps({"ok": False, "stage": "topology_lint_pre", **summary}, indent=2, ensure_ascii=False))
            return 1
        summary["build"] = build_pathway_svg(spec_obj, svg_path, data_table=dt_p)
        summary["topology_lint_post"] = lint_pathway_spec(spec_obj, svg_path=svg_path, data_table_rows=dt_rows or None)

    svg2drawio.svg_to_drawio_file(svg_path, drawio_path)
    try:
        summary["drawio_binding"] = bind_drawio_pathway(svg_path, drawio_path, spec_obj)
        if summary["drawio_binding"]["edge_bindings"]:
            probe_node = f"node-{summary['drawio_binding']['edge_bindings'][0]['source_entity_id']}"
            summary["drawio_move_probe"] = verify_drawio_pathway_move(drawio_path, probe_node)
    except PathwayBindingError as exc:
        summary["drawio_binding"] = {"status": "error", "message": str(exc)}

    summary["render"] = render_svg_previews(svg_path, png_path=png_path, pdf_path=pdf_path, dpi=args.dpi)
    summary["portable_svg"] = audit_portable_svg(svg_path)
    summary["editability"] = inspect_svg_only(svg_path)
    summary["drawio_layout"] = analyze_layout(drawio_path.read_text(encoding="utf-8"), min_spacing=2.0)

    lint_final = summary.get("topology_lint", summary.get("topology_lint_post", {"passed": True}))
    passed = (
        lint_final["passed"]
        and summary["drawio_binding"].get("status") == "pass"
        and summary["render"]["ok"]
        and summary["portable_svg"]["passed"]
        and summary["editability"]["status"] == "pass"
        and len(summary["drawio_layout"]["overlaps"]) == 0
        and len(summary["drawio_layout"]["out_of_bounds"]) == 0
    )
    if case_p is not None:
        srcs: list[str] = []
        if spec_obj is not None:
            srcs = sorted({str(x.get("source_ref", "")).strip() for x in (spec_obj.get("entities") or []) + (spec_obj.get("edges") or []) if isinstance(x, dict) and str(x.get("source_ref", "")).strip()})
        elif args.edit_svg:
            srcs = [str(args.edit_svg)]
        uncert = [str(g.get("message", g.get("code", ""))) for g in (lint_final.get("gaps") or [])]
        for cn in ((summary.get("build") or {}).get("clamped_nodes") or []):
            uncert.append(
                f"CLAMP_TRUNCATION_DISCLOSURE: Node {cn['node_id']} raw value {cn['raw_value']} {cn['unit']} color-clamped at {cn['clamped_to']} (exact numeric label preserved on node)"
            )
        if (summary["portable_svg"].get("font_audit") or {}).get("warnings"):
            uncert.extend(summary["portable_svg"]["font_audit"]["warnings"])
        arts = [svg_path.name, drawio_path.name, png_path.name, pdf_path.name]
        if args.edit_svg:
            arts.append(f"{args.stem}_edit_diff.json")
        summary["result_seal"] = seal_case_result(
            case_p, result_p, task_id="bio-pathway-mechanism-drawer",
            artifacts=arts, sources=srcs, uncertainty=uncert,
            change_records=summary.get("edit_diff", {}).get("changes", [{"op": "build_pathway", "dpi": args.dpi}]),
            metrics={"dpi": args.dpi, "data_conflicts": len(lint_final.get("data_conflicts") or [])},
            status="ok" if passed else "failed",
        )
    summary["ok"] = passed
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
