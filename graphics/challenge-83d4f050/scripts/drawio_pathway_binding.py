#!/usr/bin/env python3
"""Bind generated Draw.io biological pathway entities (preserving gene italics), values, and polarity-typed edges."""

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


class PathwayBindingError(ValueError):
    """Raised when explicit biological pathway SVG IDs cannot be bound to native Draw.io cells."""


_DRAWIO_POLARITY_STYLE = {
    "activate": ("classic", "0"),
    "inhibit": ("dash", "0"),
    "bind": ("oval", "0"),
    "modify": ("classic", "0"),
    "transport": ("open", "1"),
    "transcribe": ("classic", "1"),
    "secrete": ("open", "1"),
    "uncertain": ("open", "1"),
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _style_map(style: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for item in style.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            if k:
                entries[k] = v
    return entries


def _build_style(mapping: dict[str, str]) -> str:
    return "".join(f"{k}={v};" for k, v in mapping.items())


def _layer_id(cells: list[ET.Element], name: str) -> str:
    matches = [
        c for c in cells
        if c.get("vertex") == "1" and c.get("value") == name and c.find("mxGeometry") is None
    ]
    if len(matches) != 1 or not matches[0].get("id"):
        raise PathwayBindingError(f"Expected one Draw.io layer {name!r}, found {len(matches)}")
    return str(matches[0].get("id"))


def bind_drawio_pathway(
    svg_path: str | Path,
    drawio_path: str | Path,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Embed biological node names (italic for genes, upright for proteins) + values and bind polarity-typed edges."""
    svg_root = _safe_parse(str(svg_path)).getroot()
    drawio_tree = ET.parse(drawio_path)
    drawio_root = drawio_tree.getroot()
    cells = drawio_root.findall(".//mxCell")
    ids = [c.get("id") for c in cells if c.get("id")]
    if len(ids) != len(set(ids)):
        raise PathwayBindingError("Draw.io document contains duplicate mxCell IDs")
    cells_by_id = {c.get("id", ""): c for c in cells if c.get("id")}
    parent_by_cell = {child: parent for parent in drawio_root.iter() for child in parent}

    nodes_layer = _layer_id(cells, "nodes")
    edges_layer = _layer_id(cells, "edges")

    svg_nodes: dict[str, ET.Element] = {}
    svg_labels: dict[str, ET.Element] = {}
    svg_vals: dict[str, ET.Element] = {}
    svg_edges: dict[str, ET.Element] = {}

    for elem in svg_root.iter():
        eid = elem.get("id") or ""
        if eid.startswith("node-"):
            svg_nodes[eid[len("node-"):]] = elem
        elif eid.startswith("nodelabel-"):
            svg_labels[eid[len("nodelabel-"):]] = elem
        elif eid.startswith("nodeval-"):
            svg_vals[eid[len("nodeval-"):]] = elem
        elif eid.startswith("edge-") and _local_name(elem.tag) == "path":
            svg_edges[eid[len("edge-"):]] = elem

    if not svg_nodes:
        raise PathwayBindingError("SVG contains zero explicit biological node elements")

    node_cells: dict[str, str] = {}
    for nid, elem in svg_nodes.items():
        cid = elem.get("data-drawio-cell-id") or f"node-{nid}"
        cell = cells_by_id.get(cid)
        if cell is None or cell.get("vertex") != "1" or cell.get("parent") != nodes_layer:
            raise PathwayBindingError(f"Missing Draw.io node cell {cid!r}")
        node_cells[nid] = cid

    label_bindings: list[dict[str, str]] = []
    for nid, svg_lbl in svg_labels.items():
        if nid not in node_cells:
            continue
        lbl_cid = svg_lbl.get("data-drawio-cell-id") or f"nodelabel-{nid}"
        lbl_cell = cells_by_id.get(lbl_cid)
        if lbl_cell is None or lbl_cell.get("vertex") != "1" or lbl_cell.get("parent") != nodes_layer:
            continue
        txt = "".join(svg_lbl.itertext()).strip()
        is_italic = (svg_lbl.get("font-style") or "").lower() == "italic" or (svg_nodes[nid].get("data-entity-type") or "").lower() == "gene"
        val_txt = ""
        if nid in svg_vals:
            val_cid = svg_vals[nid].get("data-drawio-cell-id") or f"nodeval-{nid}"
            val_cell = cells_by_id.get(val_cid)
            if val_cell is not None and val_cell.get("parent") == nodes_layer:
                val_txt = html.unescape(val_cell.get("value") or "").strip()
                vp = parent_by_cell.get(val_cell)
                if vp is not None:
                    vp.remove(val_cell)
        name_html = f"<i><b>{html.escape(txt)}</b></i>" if is_italic else f"<b>{html.escape(txt)}</b>"
        if val_txt:
            name_html += f"<br/><span style=\"font-size:8.5px\">{html.escape(val_txt)}</span>"
        ncell = cells_by_id[node_cells[nid]]
        ncell.set("value", name_html)
        st_map = _style_map(ncell.get("style", ""))
        lbl_map = _style_map(lbl_cell.get("style", ""))
        for k in ("fontSize", "fontColor", "fontFamily"):
            if k in lbl_map:
                st_map[k] = lbl_map[k]
        st_map["html"] = "1"
        st_map["whiteSpace"] = "wrap"
        st_map["align"] = "center"
        st_map["verticalAlign"] = "middle"
        ncell.set("style", _build_style(st_map))
        lp = parent_by_cell.get(lbl_cell)
        if lp is not None:
            lp.remove(lbl_cell)
        label_bindings.append({
            "entity_id": nid,
            "node_cell_id": node_cells[nid],
            "italic_gene": str(is_italic).lower(),
            "text": txt if not val_txt else f"{txt} ({val_txt})",
        })

    edge_bindings: list[dict[str, str]] = []
    for ed_id, path in svg_edges.items():
        ecid = path.get("data-drawio-cell-id") or f"edge-{ed_id}"
        src = path.get("data-source-node-id") or ""
        tgt = path.get("data-target-node-id") or ""
        pol = (path.get("data-polarity") or "activate").lower()
        if path.get("data-uncertain") == "true":
            pol = "uncertain"
        if not src or not tgt or src not in node_cells or tgt not in node_cells:
            raise PathwayBindingError(f"Edge {ed_id!r} missing valid source/target node IDs ({src!r}->{tgt!r})")
        ecell = cells_by_id.get(ecid)
        if ecell is None or ecell.get("edge") != "1" or ecell.get("parent") != edges_layer:
            raise PathwayBindingError(f"Missing Draw.io edge cell {ecid!r}")
        ecell.set("source", node_cells[src])
        ecell.set("target", node_cells[tgt])
        end_arrow, dashed = _DRAWIO_POLARITY_STYLE.get(pol, ("classic", "0"))
        est = _style_map(ecell.get("style", ""))
        est["endArrow"] = end_arrow
        est["endFill"] = "0" if end_arrow in ("open", "dash") else "1"
        if dashed == "1":
            est["dashed"] = "1"
        ecell.set("style", _build_style(est))
        geom = ecell.find("mxGeometry")
        if geom is not None:
            for pt in list(geom):
                if pt.tag == "mxPoint" and pt.get("as") in ("sourcePoint", "targetPoint"):
                    geom.remove(pt)
        edge_bindings.append({
            "edge_id": ed_id,
            "edge_cell_id": ecid,
            "source_entity_id": src,
            "target_entity_id": tgt,
            "polarity": pol,
            "drawio_endArrow": end_arrow,
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
        "bound_entity_count": len(node_cells),
        "integrated_label_count": len(label_bindings),
        "bound_edge_count": len(edge_bindings),
        "edge_bindings": edge_bindings,
        "label_bindings": label_bindings,
    }


def verify_drawio_pathway_move(drawio_path: str | Path, node_cell_id: str, dx: float = 30.0, dy: float = 18.0) -> dict[str, Any]:
    """Verify in memory that moving a pathway entity keeps its label and polarity-typed edges bound."""
    root = ET.parse(drawio_path).getroot()
    cells = {c.get("id", ""): c for c in root.findall(".//mxCell") if c.get("id")}
    node = cells.get(node_cell_id)
    if node is None:
        return {"ok": False, "error": f"Missing node cell {node_cell_id!r}"}
    geom = node.find("mxGeometry")
    if geom is None:
        return {"ok": False, "error": "Missing mxGeometry"}
    geom.set("x", f"{float(geom.get('x', '0')) + dx:.1f}")
    geom.set("y", f"{float(geom.get('y', '0')) + dy:.1f}")
    attached = [
        {
            "edge_id": c.get("id"),
            "role": "source" if c.get("source") == node_cell_id else "target",
            "endArrow": _style_map(c.get("style", "")).get("endArrow", ""),
            "has_floating_endpoints": any(
                pt.tag == "mxPoint" and pt.get("as") in ("sourcePoint", "targetPoint")
                for pt in (c.find("mxGeometry") or [])
            ),
        }
        for c in cells.values()
        if c.get("edge") == "1" and (c.get("source") == node_cell_id or c.get("target") == node_cell_id)
    ]
    val = (node.get("value") or "").strip()
    return {
        "ok": bool(val) and len(attached) > 0 and all(not a["has_floating_endpoints"] for a in attached),
        "node_cell_id": node_cell_id,
        "embedded_value": val,
        "attached_edge_count": len(attached),
        "attached_edges": attached,
    }
