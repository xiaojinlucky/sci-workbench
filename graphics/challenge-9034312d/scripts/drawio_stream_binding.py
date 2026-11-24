"""Bind generated Draw.io apparatus units, labels, specs, and streams using explicit source identities."""

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


class BindingError(ValueError):
    """Raised when an explicit SVG ID cannot be mapped to one Draw.io cell."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _float(value: str | None, context: str) -> float:
    if value is None:
        raise BindingError(f"{context}: coordinate is missing")
    try:
        number = float(value)
    except ValueError as exc:
        raise BindingError(f"{context}: invalid coordinate {value!r}") from exc
    if not math.isfinite(number):
        raise BindingError(f"{context}: non-finite coordinate {value!r}")
    return number


def _one_cell_by_id(cells_by_id: dict[str, ET.Element], cell_id: str, kind: str, layer_id: str) -> ET.Element:
    cell = cells_by_id.get(cell_id)
    if cell is None:
        raise BindingError(f"Draw.io {kind} {cell_id!r}: explicit SVG cell ID is absent")
    expected_type = "vertex" if kind == "unit" else "edge"
    if cell.get(expected_type) != "1" or cell.get("parent") != layer_id:
        raise BindingError(f"Draw.io {kind} {cell_id!r}: wrong mxCell type or layer")
    return cell


def _layer_id(cells: list[ET.Element], name: str) -> str:
    layers = [
        cell for cell in cells
        if cell.get("vertex") == "1" and cell.get("value") == name
        and cell.find("mxGeometry") is None
    ]
    if len(layers) != 1 or not layers[0].get("id"):
        raise BindingError(f"Draw.io layer {name!r}: expected one named layer, found {len(layers)}")
    return str(layers[0].get("id"))


def _check_unit_geometry(svg_rect: ET.Element, drawio_cell: ET.Element, unit_id: str) -> None:
    if _local_name(svg_rect.tag) != "rect":
        raise BindingError(f"unit {unit_id!r}: semantic SVG id must identify a rect")
    geom = drawio_cell.find("mxGeometry")
    if geom is None:
        raise BindingError(f"unit {unit_id!r}: Draw.io mxCell has no geometry")
    attrs = ("x", "y", "width", "height")
    svg_box = tuple(round(_float(svg_rect.get(attr), f"unit {unit_id} {attr}"), 2) for attr in attrs)
    drawio_box = tuple(round(_float(geom.get(attr), f"unit cell {unit_id} {attr}"), 2) for attr in attrs)
    if svg_box != drawio_box:
        raise BindingError(f"unit {unit_id!r}: explicit mxCell ID does not retain the source SVG rectangle geometry")


def _style_map(style: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for item in style.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            if key:
                entries[key] = value
    return entries


def _merge_unit_label_style(shape_style: str, label_style: str) -> str:
    shape = _style_map(shape_style)
    label = _style_map(label_style)
    for key in ("align", "verticalAlign", "whiteSpace", "fontSize", "fontColor", "fontFamily", "fontStyle"):
        if key in label:
            shape[key] = label[key]
    shape["whiteSpace"] = "wrap"
    shape["html"] = "1"
    shape["align"] = "center"
    shape["verticalAlign"] = "middle"
    return "".join(f"{key}={value};" for key, value in shape.items())


def bind_drawio_streams(
    svg_path: str | Path,
    drawio_path: str | Path,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Embed unit labels (+ optional spec_text) into native Draw.io shapes and bind stream mxCells to source/target units."""
    svg_root = _safe_parse(str(svg_path)).getroot()
    drawio_tree = ET.parse(drawio_path)
    drawio_root = drawio_tree.getroot()
    cells = drawio_root.findall(".//mxCell")
    ids = [cell.get("id") for cell in cells if cell.get("id")]
    if len(ids) != len(set(ids)):
        raise BindingError("Draw.io document contains duplicate mxCell IDs")
    cells_by_id = {cell.get("id", ""): cell for cell in cells if cell.get("id")}
    units_layer_id = _layer_id(cells, "units")
    streams_layer_id = _layer_id(cells, "streams")

    svg_units: dict[str, ET.Element] = {}
    svg_streams: dict[str, ET.Element] = {}
    svg_unit_labels: dict[str, ET.Element] = {}
    svg_unit_specs: dict[str, ET.Element] = {}
    for elem in svg_root.iter():
        element_id = elem.get("id") or ""
        if element_id.startswith("unit-"):
            if _local_name(elem.tag) != "rect":
                raise BindingError(f"SVG unit id {element_id!r} is not attached to a rect")
            unit_id = element_id[len("unit-"):]
            if unit_id in svg_units:
                raise BindingError(f"SVG contains duplicate unit id {unit_id!r}")
            svg_units[unit_id] = elem
        elif element_id.startswith("stream-"):
            if _local_name(elem.tag) != "path":
                raise BindingError(f"SVG stream id {element_id!r} is not attached to a path")
            stream_id = element_id[len("stream-"):]
            if stream_id in svg_streams:
                raise BindingError(f"SVG contains duplicate stream id {stream_id!r}")
            svg_streams[stream_id] = elem
        elif element_id.startswith("unitlabel-"):
            if _local_name(elem.tag) != "text":
                raise BindingError(f"SVG unit label id {element_id!r} is not attached to text")
            unit_id = element_id[len("unitlabel-"):]
            if unit_id in svg_unit_labels:
                raise BindingError(f"SVG contains duplicate unit label id {unit_id!r}")
            svg_unit_labels[unit_id] = elem
        elif element_id.startswith("unitspec-"):
            if _local_name(elem.tag) == "text":
                svg_unit_specs[element_id[len("unitspec-"):]] = elem
    if not svg_units or not svg_streams:
        raise BindingError("SVG must contain explicitly identified unit rects and stream paths")
    if set(svg_unit_labels) != set(svg_units):
        raise BindingError("explicit SVG unit label IDs do not match the unit ID set")
    if spec is not None:
        spec_units = {str(unit["id"]) for unit in spec.get("units", [])}
        spec_streams = {str(stream["id"]): stream for stream in spec.get("streams", [])}
        if spec_units != set(svg_units) or set(spec_streams) != set(svg_streams):
            raise BindingError("explicit SVG unit/stream ID sets do not match the supplied spec")
    else:
        spec_streams = {}

    unit_cell_ids: dict[str, str] = {}
    for unit_id, rect in svg_units.items():
        cell_id = rect.get("data-drawio-cell-id")
        if cell_id != f"unit-{unit_id}":
            raise BindingError(f"unit {unit_id!r}: missing or unexpected explicit Draw.io cell ID")
        cell = _one_cell_by_id(cells_by_id, cell_id, "unit", units_layer_id)
        _check_unit_geometry(rect, cell, unit_id)
        unit_cell_ids[unit_id] = cell_id

    parent_by_cell = {child: parent for parent in drawio_root.iter() for child in parent}
    unit_label_bindings: list[dict[str, str]] = []
    for unit_id, svg_label in svg_unit_labels.items():
        label_cell_id = f"unitlabel-{unit_id}"
        label_cell = cells_by_id.get(label_cell_id)
        if label_cell is None or label_cell.get("vertex") != "1" or label_cell.get("parent") != units_layer_id:
            raise BindingError(f"unit {unit_id!r}: explicit Draw.io label cell ID is absent or in the wrong layer")
        svg_text = "".join(svg_label.itertext()).strip()
        drawio_text = label_cell.get("value") or ""
        if not svg_text or html.unescape(drawio_text).strip() != svg_text:
            raise BindingError(f"unit {unit_id!r}: explicit Draw.io label cell does not match its SVG source ID/text")

        spec_txt = ""
        spec_cell_id = ""
        if unit_id in svg_unit_specs:
            spec_cell_id = f"unitspec-{unit_id}"
            spec_cell = cells_by_id.get(spec_cell_id)
            if spec_cell is not None and spec_cell.get("vertex") == "1" and spec_cell.get("parent") == units_layer_id:
                spec_txt = html.unescape(spec_cell.get("value") or "").strip()
                sp_parent = parent_by_cell.get(spec_cell)
                if sp_parent is not None:
                    sp_parent.remove(spec_cell)

        combined_val = f"<b>{html.escape(svg_text)}</b>"
        if spec_txt:
            combined_val += f"<br/><span style=\"font-size:8.5px\">{html.escape(spec_txt)}</span>"

        unit_cell = cells_by_id[unit_cell_ids[unit_id]]
        unit_cell.set("value", combined_val)
        unit_cell.set("style", _merge_unit_label_style(unit_cell.get("style", ""), label_cell.get("style", "")))
        label_parent = parent_by_cell.get(label_cell)
        if label_parent is None:
            raise BindingError(f"unit {unit_id!r}: explicit Draw.io label cell has no parent")
        label_parent.remove(label_cell)
        unit_label_bindings.append({
            "unit_id": unit_id,
            "source_svg_label_id": label_cell_id,
            "integrated_spec_cell_id": spec_cell_id,
            "native_unit_cell_id": unit_cell_ids[unit_id],
            "text": svg_text if not spec_txt else f"{svg_text} | {spec_txt}",
        })

    bindings: list[dict[str, str]] = []
    for stream_id, path in svg_streams.items():
        edge_id = path.get("data-drawio-cell-id")
        source_unit = path.get("data-source-unit-id")
        target_unit = path.get("data-target-unit-id")
        if edge_id != f"stream-{stream_id}" or not source_unit or not target_unit:
            raise BindingError(f"stream {stream_id!r}: missing explicit Draw.io ID or unit endpoints")
        if source_unit not in unit_cell_ids or target_unit not in unit_cell_ids:
            raise BindingError(f"stream {stream_id!r}: references an unknown unit ID")
        if spec is not None:
            declared = spec_streams[stream_id]
            if str(declared.get("source")) != source_unit or str(declared.get("target")) != target_unit:
                raise BindingError(f"stream {stream_id!r}: SVG endpoint IDs disagree with the spec")
        edge = _one_cell_by_id(cells_by_id, edge_id, "stream", streams_layer_id)
        if edge.get("source") not in (None, unit_cell_ids[source_unit]) or edge.get("target") not in (None, unit_cell_ids[target_unit]):
            raise BindingError(f"stream {stream_id!r}: existing binding conflicts with explicit endpoint IDs")
        edge.set("source", unit_cell_ids[source_unit])
        edge.set("target", unit_cell_ids[target_unit])
        geom = edge.find("mxGeometry")
        if geom is None:
            raise BindingError(f"stream {stream_id!r}: mxCell has no geometry")
        for point in list(geom):
            if point.tag == "mxPoint" and point.get("as") in ("sourcePoint", "targetPoint"):
                geom.remove(point)
        bindings.append({
            "stream_id": stream_id,
            "edge_cell_id": edge_id,
            "source_unit_id": source_unit,
            "source_cell_id": unit_cell_ids[source_unit],
            "target_unit_id": target_unit,
            "target_cell_id": unit_cell_ids[target_unit],
        })

    ET.indent(drawio_tree, space="  ")
    serialized = ET.tostring(drawio_root, encoding="utf-8", xml_declaration=True)
    out_path = Path(drawio_path)
    fd, temp_path = tempfile.mkstemp(dir=out_path.parent, prefix=f".{out_path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(serialized)
        os.replace(temp_path, out_path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise

    return {
        "status": "pass",
        "mapping_basis": "explicit unit, unit-label, unit-spec, and stream SVG IDs preserved by converter; stream endpoints checked against spec",
        "unit_cells": unit_cell_ids,
        "unit_label_bindings": unit_label_bindings,
        "edge_bindings": bindings,
        "bound_stream_count": len(bindings),
    }


def verify_drawio_unit_move(drawio_path: str | Path, unit_cell_id: str, dx: float = 36.0, dy: float = 24.0) -> dict[str, Any]:
    """Verify in memory that translating a Draw.io apparatus unit keeps its integrated label/spec and bound edges attached."""
    root = ET.parse(drawio_path).getroot()
    cells = {c.get("id", ""): c for c in root.findall(".//mxCell") if c.get("id")}
    unit = cells.get(unit_cell_id)
    if unit is None:
        return {"ok": False, "error": f"Missing unit cell {unit_cell_id!r}"}
    geom = unit.find("mxGeometry")
    if geom is None:
        return {"ok": False, "error": f"Unit cell {unit_cell_id!r} missing mxGeometry"}
    old_x, old_y = float(geom.get("x", "0")), float(geom.get("y", "0"))
    geom.set("x", f"{old_x + dx:.1f}")
    geom.set("y", f"{old_y + dy:.1f}")
    attached = [
        {
            "edge_id": c.get("id"),
            "role": "source" if c.get("source") == unit_cell_id else "target",
            "has_floating_endpoints": any(
                pt.tag == "mxPoint" and pt.get("as") in ("sourcePoint", "targetPoint")
                for pt in (c.find("mxGeometry") or [])
            ),
        }
        for c in cells.values()
        if c.get("edge") == "1" and (c.get("source") == unit_cell_id or c.get("target") == unit_cell_id)
    ]
    val = (unit.get("value") or "").strip()
    ok = bool(val) and len(attached) > 0 and all(not a["has_floating_endpoints"] for a in attached)
    return {
        "ok": ok,
        "unit_cell_id": unit_cell_id,
        "integrated_value": val,
        "attached_stream_count": len(attached),
        "attached_streams": attached,
    }
