"""Draw.io static layout overlap, containment, and out-of-bounds analyzer (adapted from Adcage/drawio-skill, MIT)."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as _StdET
from typing import Any

try:
    import defusedxml.ElementTree as _SafeET
except ImportError:
    _SafeET = None

_FORBIDDEN_XML = re.compile(r"<!(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)\b", re.IGNORECASE)


def _safe_parse_xml(xml_text: str) -> _StdET.Element:
    if _SafeET is not None:
        return _SafeET.fromstring(xml_text)
    if _FORBIDDEN_XML.search(xml_text[:32768]):
        raise ValueError("Refused XML with DOCTYPE/ENTITY declaration")
    return _StdET.fromstring(xml_text)


def _as_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _is_container_or_background(cell_id: str, style: str, w: float, h: float, canvas_area: float) -> bool:
    low_id = cell_id.lower()
    low_style = style.lower()
    if "group;" in low_style or low_style.startswith("group") or "swimlane" in low_style or "container=1" in low_style:
        return True
    if any(low_id.startswith(p) for p in ("comp-", "bg-", "layer-", "panel-", "subsystem-")):
        return True
    if canvas_area > 0 and (w * h) / canvas_area > 0.22:
        return True
    return False


def _extract_nodes(root: _StdET.Element) -> tuple[list[dict[str, Any]], float, float]:
    model = root.find(".//mxGraphModel")
    if model is None:
        return [], 1400.0, 1200.0

    layer_names: dict[str, str] = {}
    for cell in root.findall(".//mxCell"):
        if cell.get("parent") == "1" and cell.find("mxGeometry") is None and cell.get("edge") != "1":
            layer_names[cell.get("id", "")] = (cell.get("value") or "").strip().lower()

    page_width = _as_float(model.get("pageWidth"), 0.0)
    page_height = _as_float(model.get("pageHeight"), 0.0)
    if page_width <= 0 or page_height <= 0:
        bg_cands: list[tuple[float, float]] = []
        for cell in root.findall(".//mxCell"):
            if cell.get("vertex") != "1":
                continue
            g_bg = cell.find("mxGeometry")
            if g_bg is None or g_bg.get("relative") == "1":
                continue
            bw = _as_float(g_bg.get("width"))
            bh = _as_float(g_bg.get("height"))
            bx = _as_float(g_bg.get("x"))
            by = _as_float(g_bg.get("y"))
            pid = cell.get("parent", "")
            cid_bg = (cell.get("id") or "").lower()
            if bw >= 100.0 and bh >= 100.0 and (
                layer_names.get(pid) == "background"
                or cid_bg == "bg-canvas"
                or cid_bg.startswith("bg-")
                or (abs(bx) < 1e-3 and abs(by) < 1e-3 and "strokeColor=none" in (cell.get("style") or ""))
            ):
                bg_cands.append((bw, bh))
        if bg_cands:
            best_w, best_h = max(bg_cands, key=lambda wh: wh[0] * wh[1])
            if page_width <= 0:
                page_width = best_w
            if page_height <= 0:
                page_height = best_h
    if page_width <= 0:
        page_width = 1400.0
    if page_height <= 0:
        page_height = 1200.0
    canvas_area = max(page_width * page_height, 1.0)

    layer_names: dict[str, str] = {}
    for cell in root.findall(".//mxCell"):
        if cell.get("parent") == "1" and cell.find("mxGeometry") is None and cell.get("edge") != "1":
            layer_names[cell.get("id", "")] = (cell.get("value") or "").strip().lower()

    nodes: list[dict[str, Any]] = []
    for cell in root.findall(".//mxCell"):
        if cell.get("vertex") != "1":
            continue
        geom = cell.find("mxGeometry")
        if geom is None or geom.get("relative") == "1":
            continue
        w = _as_float(geom.get("width"))
        h = _as_float(geom.get("height"))
        if w <= 0 or h <= 0:
            continue
        cid = cell.get("id", "")
        pid = cell.get("parent", "1")
        pname = layer_names.get(pid, "")
        style = cell.get("style", "")
        is_bg_layer = pname in ("background", "compartments", "subsystems", "grid")
        nodes.append({
            "id": cid,
            "parent": pid,
            "layer_name": pname,
            "value": (cell.get("value") or "").strip(),
            "style": style,
            "is_container": is_bg_layer or _is_container_or_background(cid, style, w, h, canvas_area),
            "is_text": style.startswith("text;") or ";text;" in style,
            "cell": cell,
            "geometry": geom,
            "x": _as_float(geom.get("x")),
            "y": _as_float(geom.get("y")),
            "w": w,
            "h": h,
        })
    return nodes, page_width, page_height


def _overlap_area(a: dict[str, Any], b: dict[str, Any]) -> float:
    dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(0.0, dx) * max(0.0, dy)


def _box_distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    dx = max(a["x"] - (b["x"] + b["w"]), b["x"] - (a["x"] + a["w"]), 0.0)
    dy = max(a["y"] - (b["y"] + b["h"]), b["y"] - (a["y"] + a["h"]), 0.0)
    return math.hypot(dx, dy)


def analyze_layout(xml_text: str, min_spacing: float = 4.0) -> dict[str, Any]:
    root = _safe_parse_xml(xml_text)
    nodes, page_width, page_height = _extract_nodes(root)

    overlaps: list[dict[str, Any]] = []
    spacing_violations: list[dict[str, Any]] = []
    out_of_bounds: list[str] = []

    _cell_map = {c.get("id", ""): c for c in root.findall(".//mxCell") if c.get("id")}

    def _global_xy(cid: str, lx: float, ly: float) -> tuple[float, float]:
        gx, gy = lx, ly
        seen = {cid}
        cur = _cell_map.get(cid)
        pid = cur.get("parent", "") if cur is not None else ""
        while pid and pid not in ("0", "1") and pid not in seen:
            seen.add(pid)
            pcell = _cell_map.get(pid)
            if pcell is None:
                break
            pg = pcell.find("mxGeometry")
            if pg is not None and pg.get("relative") != "1":
                gx += _as_float(pg.get("x"))
                gy += _as_float(pg.get("y"))
            pid = pcell.get("parent", "")
        return gx, gy

    for a in nodes:
        if a["is_container"] or (a.get("style") or "").startswith("group"):
            continue
        gx, gy = _global_xy(a["id"], a["x"], a["y"])
        if gx < -6.0 or gy < -6.0 or gx + a["w"] > page_width + 6.0 or gy + a["h"] > page_height + 6.0:
            out_of_bounds.append(a["id"])

    # Compare only sibling foreground nodes (skip container/background containment and text-inside-its-box)
    fg = [n for n in nodes if not n["is_container"]]
    for i in range(len(fg)):
        a = fg[i]
        for j in range(i + 1, len(fg)):
            b = fg[j]
            if a["parent"] != b["parent"]:
                continue
            inter = _overlap_area(a, b)
            if inter > 0.0:
                area_a = a["w"] * a["h"]
                area_b = b["w"] * b["h"]
                min_area = max(min(area_a, area_b), 1e-6)
                max_area = max(area_a, area_b)
                frac = inter / min_area
                # Ignore centered label resting wholly inside its own shape box
                if (a["is_text"] ^ b["is_text"]) and frac >= 0.85:
                    continue
                # Ignore explicit container framing (e.g. legend box or panel border >= 4x larger containing child item)
                if frac >= 0.90 and max_area >= 4.0 * min_area:
                    continue
                if frac > 0.08:
                    overlaps.append({"a": a["id"], "b": b["id"], "overlap_frac": round(frac, 3)})
                    continue
            dist = _box_distance(a, b)
            if not (a["is_text"] ^ b["is_text"]) and 0.0 < dist < min_spacing:
                spacing_violations.append({"a": a["id"], "b": b["id"], "dist": round(dist, 2)})

    return {
        "canvas": {"width": page_width, "height": page_height},
        "total_vertices": len(nodes),
        "foreground_vertices": len(fg),
        "overlaps": overlaps,
        "out_of_bounds": out_of_bounds,
        "spacing_violations": spacing_violations,
    }
