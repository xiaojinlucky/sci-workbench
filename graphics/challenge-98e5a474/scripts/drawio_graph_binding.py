#!/usr/bin/env python3
"""Bind generated Draw.io schematic nodes, labels, values, and edges via explicit SVG cell IDs."""

from __future__ import annotations

import html
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    from defusedxml.ElementTree import parse as _safe_parse  # type: ignore
except ImportError:  # pragma: no cover
    _safe_parse = ET.parse


class GraphBindingError(ValueError):
    """Raised when explicit SVG node/label/edge IDs cannot be mapped to native Draw.io cells."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _float(val: str | None, ctx: str) -> float:
    if val is None:
        raise GraphBindingError(f"{ctx}: missing coordinate")
    try:
        num = float(val)
    except ValueError as exc:
        raise GraphBindingError(f"{ctx}: invalid float {val!r}") from exc
    if not math.isfinite(num):
        raise GraphBindingError(f"{ctx}: non-finite float {val!r}")
    return num


def _style_map(style: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for item in style.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            if k:
                entries[k] = v
    return entries


def _merge_label_style(shape_style: str, label_style: str) -> str:
    shape = _style_map(shape_style)
    label = _style_map(label_style)
    for k in ("align", "verticalAlign", "whiteSpace", "fontSize", "fontColor", "fontFamily", "fontStyle"):
        if k in label:
            shape[k] = label[k]
    shape["whiteSpace"] = "wrap"
    shape["html"] = "1"
    shape["align"] = "center"
    shape["verticalAlign"] = "middle"
    return "".join(f"{k}={v};" for k, v in shape.items())


def _layer_id(cells: list[ET.Element], name: str) -> str:
    matches = [
        c for c in cells
        if c.get("vertex") == "1" and c.get("value") == name and c.find("mxGeometry") is None
    ]
    if len(matches) != 1 or not matches[0].get("id"):
        raise GraphBindingError(f"Expected one Draw.io layer {name!r}, found {len(matches)}")
    return str(matches[0].get("id"))


def _svg_shape_box(elem: ET.Element, node_id: str) -> tuple[float, float, float, float]:
    tag = _local_name(elem.tag)
    if tag == "rect":
        return (
            round(_float(elem.get("x"), f"node {node_id} x"), 2),
            round(_float(elem.get("y"), f"node {node_id} y"), 2),
            round(_float(elem.get("width"), f"node {node_id} width"), 2),
            round(_float(elem.get("height"), f"node {node_id} height"), 2),
        )
    if tag == "ellipse":
        cx = _float(elem.get("cx"), f"node {node_id} cx")
        cy = _float(elem.get("cy"), f"node {node_id} cy")
        rx = _float(elem.get("rx"), f"node {node_id} rx")
        ry = _float(elem.get("ry"), f"node {node_id} ry")
        return (round(cx - rx, 2), round(cy - ry, 2), round(2 * rx, 2), round(2 * ry, 2))
    raise GraphBindingError(f"node {node_id!r}: unsupported SVG tag {tag!r} for native Draw.io binding")


def bind_drawio_graph(
    svg_path: str | Path,
    drawio_path: str | Path,
    *,
    node_layer: str = "nodes",
    edge_layer: str = "edges",
    node_prefix: str = "node-",
    label_prefix: str = "nodelabel-",
    val_prefix: str = "nodeval-",
    edge_prefix: str = "edge-",
    src_attr: str = "data-source-node-id",
    tgt_attr: str = "data-target-node-id",
    spec_nodes: set[str] | None = None,
    spec_edges: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Embed verified SVG node labels/values into native Draw.io shapes and bind edges by source/target IDs."""
    svg_root = _safe_parse(str(svg_path)).getroot()
    drawio_tree = ET.parse(drawio_path)
    drawio_root = drawio_tree.getroot()
    cells = drawio_root.findall(".//mxCell")
    ids = [c.get("id") for c in cells if c.get("id")]
    if len(ids) != len(set(ids)):
        raise GraphBindingError("Draw.io document has duplicate mxCell IDs")
    cells_by_id = {c.get("id", ""): c for c in cells if c.get("id")}
    parent_by_cell = {child: parent for parent in drawio_root.iter() for child in parent}

    node_layer_id = _layer_id(cells, node_layer)
    edge_layer_id = _layer_id(cells, edge_layer)

    svg_nodes: dict[str, ET.Element] = {}
    svg_labels: dict[str, ET.Element] = {}
    svg_vals: dict[str, ET.Element] = {}
    svg_edges: dict[str, ET.Element] = {}

    for elem in svg_root.iter():
        eid = elem.get("id") or ""
        if eid.startswith(node_prefix):
            nid = eid[len(node_prefix):]
            if nid in svg_nodes:
                raise GraphBindingError(f"Duplicate SVG node id {nid!r}")
            svg_nodes[nid] = elem
        elif eid.startswith(label_prefix):
            nid = eid[len(label_prefix):]
            if nid in svg_labels:
                raise GraphBindingError(f"Duplicate SVG label id {nid!r}")
            svg_labels[nid] = elem
        elif val_prefix and eid.startswith(val_prefix):
            nid = eid[len(val_prefix):]
            svg_vals[nid] = elem
        elif eid.startswith(edge_prefix) and _local_name(elem.tag) == "path":
            ed_id = eid[len(edge_prefix):]
            if ed_id in svg_edges:
                raise GraphBindingError(f"Duplicate SVG edge id {ed_id!r}")
            svg_edges[ed_id] = elem

    if not svg_nodes:
        raise GraphBindingError("SVG contains zero explicitly identified nodes")
    if spec_nodes is not None and spec_nodes != set(svg_nodes):
        raise GraphBindingError(f"SVG nodes {sorted(svg_nodes)} disagree with spec {sorted(spec_nodes)}")
    if spec_edges is not None and set(spec_edges) != set(svg_edges):
        raise GraphBindingError(f"SVG edges {sorted(svg_edges)} disagree with spec {sorted(spec_edges)}")

    node_cell_ids: dict[str, str] = {}
    for nid, elem in svg_nodes.items():
        cid = elem.get("data-drawio-cell-id") or f"{node_prefix}{nid}"
        cell = cells_by_id.get(cid)
        if cell is None or cell.get("vertex") != "1" or cell.get("parent") != node_layer_id:
            raise GraphBindingError(f"Draw.io node cell {cid!r} missing or in wrong layer")
        geom = cell.find("mxGeometry")
        if geom is None:
            raise GraphBindingError(f"Draw.io node cell {cid!r} missing mxGeometry")
        svg_box = _svg_shape_box(elem, nid)
        dio_box = tuple(round(_float(geom.get(a), f"cell {cid} {a}"), 2) for a in ("x", "y", "width", "height"))
        if any(abs(a - b) > 0.25 for a, b in zip(svg_box, dio_box)):
            raise GraphBindingError(f"node {nid!r}: Draw.io box {dio_box} drifted from SVG box {svg_box}")
        node_cell_ids[nid] = cid

    # Integrate node labels (+ optional secondary value/spec line) into native vertex cells
    label_bindings: list[dict[str, str]] = []
    for nid, svg_lbl in svg_labels.items():
        if nid not in node_cell_ids:
            continue
        lbl_cid = svg_lbl.get("data-drawio-cell-id") or f"{label_prefix}{nid}"
        lbl_cell = cells_by_id.get(lbl_cid)
        if lbl_cell is None or lbl_cell.get("vertex") != "1" or lbl_cell.get("parent") != node_layer_id:
            continue
        svg_txt = "".join(svg_lbl.itertext()).strip()
        val_txt = ""
        val_cid = None
        if nid in svg_vals:
            val_cid = svg_vals[nid].get("data-drawio-cell-id") or f"{val_prefix}{nid}"
            val_cell = cells_by_id.get(val_cid)
            if val_cell is not None and val_cell.get("vertex") == "1" and val_cell.get("parent") == node_layer_id:
                val_txt = html.unescape(val_cell.get("value") or "").strip()
                vp = parent_by_cell.get(val_cell)
                if vp is not None:
                    vp.remove(val_cell)
        combined = f"<b>{html.escape(svg_txt)}</b>"
        if val_txt:
            combined += f"<br/><span style=\"font-size:9px\">{html.escape(val_txt)}</span>"
        node_cell = cells_by_id[node_cell_ids[nid]]
        node_cell.set("value", combined)
        node_cell.set("style", _merge_label_style(node_cell.get("style", ""), lbl_cell.get("style", "")))
        lp = parent_by_cell.get(lbl_cell)
        if lp is not None:
            lp.remove(lbl_cell)
        label_bindings.append({
            "node_id": nid,
            "native_node_cell_id": node_cell_ids[nid],
            "integrated_label_id": lbl_cid,
            "integrated_value_id": val_cid or "",
            "text": svg_txt if not val_txt else f"{svg_txt} | {val_txt}",
        })

    # Bind edges natively to source & target Draw.io node cells
    edge_bindings: list[dict[str, str]] = []
    for ed_id, path in svg_edges.items():
        ecid = path.get("data-drawio-cell-id") or f"{edge_prefix}{ed_id}"
        src_n = path.get(src_attr) or ""
        tgt_n = path.get(tgt_attr) or ""
        if not src_n or not tgt_n or src_n not in node_cell_ids or tgt_n not in node_cell_ids:
            raise GraphBindingError(f"edge {ed_id!r}: invalid endpoints {src_n!r} -> {tgt_n!r}")
        if spec_edges is not None and spec_edges[ed_id] != (src_n, tgt_n):
            raise GraphBindingError(f"edge {ed_id!r}: SVG endpoints {(src_n, tgt_n)} != spec {spec_edges[ed_id]}")
        edge_cell = cells_by_id.get(ecid)
        if edge_cell is None or edge_cell.get("edge") != "1" or edge_cell.get("parent") != edge_layer_id:
            raise GraphBindingError(f"Draw.io edge cell {ecid!r} missing or in wrong layer")
        edge_cell.set("source", node_cell_ids[src_n])
        edge_cell.set("target", node_cell_ids[tgt_n])
        geom = edge_cell.find("mxGeometry")
        if geom is not None:
            for pt in list(geom):
                if pt.tag == "mxPoint" and pt.get("as") in ("sourcePoint", "targetPoint"):
                    geom.remove(pt)
        edge_bindings.append({
            "edge_id": ed_id,
            "edge_cell_id": ecid,
            "source_node_id": src_n,
            "source_cell_id": node_cell_ids[src_n],
            "target_node_id": tgt_n,
            "target_cell_id": node_cell_ids[tgt_n],
        })

    ET.indent(drawio_tree, space="  ")
    serialized = ET.tostring(drawio_root, encoding="utf-8", xml_declaration=True)
    out_p = Path(drawio_path)
    fd, tmp_p = tempfile.mkstemp(dir=out_p.parent, prefix=f".{out_p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(serialized)
        os.replace(tmp_p, out_p)
    except BaseException:
        try:
            os.remove(tmp_p)
        except OSError:
            pass
        raise

    return {
        "status": "pass",
        "bound_node_count": len(node_cell_ids),
        "integrated_label_count": len(label_bindings),
        "bound_edge_count": len(edge_bindings),
        "node_cells": node_cell_ids,
        "label_bindings": label_bindings,
        "edge_bindings": edge_bindings,
    }


def verify_drawio_move_persistence(drawio_path: str | Path, node_cell_id: str, dx: float = 40.0, dy: float = 20.0) -> dict[str, Any]:
    """Non-destructively verify in memory that translating a Draw.io node keeps its label and bound edges attached."""
    root = ET.parse(drawio_path).getroot()
    cells = {c.get("id", ""): c for c in root.findall(".//mxCell") if c.get("id")}
    node = cells.get(node_cell_id)
    if node is None:
        return {"ok": False, "error": f"Missing node cell {node_cell_id!r}"}
    geom = node.find("mxGeometry")
    if geom is None:
        return {"ok": False, "error": f"Node cell {node_cell_id!r} has no mxGeometry"}
    old_x, old_y = float(geom.get("x", "0")), float(geom.get("y", "0"))
    geom.set("x", f"{old_x + dx:.1f}")
    geom.set("y", f"{old_y + dy:.1f}")
    attached_edges = [
        {
            "edge_id": c.get("id"),
            "role": "source" if c.get("source") == node_cell_id else "target",
            "has_floating_endpoints": any(
                pt.tag == "mxPoint" and pt.get("as") in ("sourcePoint", "targetPoint")
                for pt in (c.find("mxGeometry") or [])
            ),
        }
        for c in cells.values()
        if c.get("edge") == "1" and (c.get("source") == node_cell_id or c.get("target") == node_cell_id)
    ]
    has_embedded_label = bool((node.get("value") or "").strip())
    ok = has_embedded_label and len(attached_edges) > 0 and all(not e["has_floating_endpoints"] for e in attached_edges)
    return {
        "ok": ok,
        "node_cell_id": node_cell_id,
        "moved_from": [old_x, old_y],
        "moved_to": [old_x + dx, old_y + dy],
        "embedded_label_present": has_embedded_label,
        "embedded_label_value": node.get("value", ""),
        "attached_edge_count": len(attached_edges),
        "attached_edges": attached_edges,
    }
