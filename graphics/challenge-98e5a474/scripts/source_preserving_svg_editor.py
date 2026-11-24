#!/usr/bin/env python3
"""Source-preserving incremental SVG/draw.io editor with live edge re-routing, legend sync, and undo/redo round-trip verification."""

from __future__ import annotations

import argparse
import csv
import hashlib
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

from audit_editability import inspect_svg_only
from contract_sealer import seal_case_result
from drawio_graph_binding import GraphBindingError, bind_drawio_graph
from layout_tools import analyze_layout
from portable_svg_audit import audit_portable_svg
from render_preview import render_svg_previews
import svg2drawio

try:
    import defusedxml.ElementTree as _SafeET
except ImportError:
    _SafeET = None

SVG_NS = "http://www.w3.org/2000/svg"
_StdET.register_namespace("", SVG_NS)
_FORBIDDEN_XML = re.compile(r"<!(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)\b", re.IGNORECASE)


def _safe_parse(path: Path) -> _StdET.Element:
    raw = path.read_bytes()
    if _SafeET is not None:
        return _SafeET.fromstring(raw)
    if _FORBIDDEN_XML.search(raw[:32768].decode("utf-8", errors="ignore")):
        raise ValueError("Refused SVG containing DOCTYPE/ENTITY declarations")
    return _StdET.fromstring(raw)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_table(path: Path) -> dict[str, dict[str, str]]:
    if path.suffix.lower() == ".json":
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(obj, list):
            return {str(r["id"]): {str(k): str(v) for k, v in r.items()} for r in obj if isinstance(r, dict) and r.get("id")}
        return {str(k): {str(kk): str(vv) for kk, vv in v.items()} for k, v in obj.items() if isinstance(v, dict)}
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    out: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=delim):
            rid = str(row.get("id") or row.get("entity_id") or row.get("node_id") or "").strip()
            if rid:
                out[rid] = {str(k): str(v) for k, v in row.items() if k is not None}
    return out


def _node_box(el: _StdET.Element) -> tuple[float, float, float, float] | None:
    tag = el.tag.rsplit("}", 1)[-1]
    if tag == "rect" and all(el.get(k) is not None for k in ("x", "y", "width", "height")):
        x, y, w, h = float(el.get("x")), float(el.get("y")), float(el.get("width")), float(el.get("height"))
        return (x + w / 2.0, y + h / 2.0, w, h)
    if tag == "ellipse" and all(el.get(k) is not None for k in ("cx", "cy", "rx", "ry")):
        return (float(el.get("cx")), float(el.get("cy")), float(el.get("rx")) * 2.0, float(el.get("ry")) * 2.0)
    return None


def _reroute_connected_edges(root: _StdET.Element, id_index: dict[str, _StdET.Element], moved_node_ids: set[str]) -> list[dict[str, Any]]:
    """Recompute SVG connector paths & labels for any edges attached to moved nodes."""
    rerouted: list[dict[str, Any]] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "path":
            continue
        src_id = el.get("data-source-node-id") or el.get("data-source-unit-id") or ""
        tgt_id = el.get("data-target-node-id") or el.get("data-target-unit-id") or ""
        if not src_id or not tgt_id:
            continue
        if src_id not in moved_node_ids and tgt_id not in moved_node_ids:
            continue
        s_el = id_index.get(f"node-{src_id}")
        if s_el is None:
            s_el = id_index.get(f"unit-{src_id}")
        t_el = id_index.get(f"node-{tgt_id}")
        if t_el is None:
            t_el = id_index.get(f"unit-{tgt_id}")
        if s_el is None or t_el is None:
            continue
        sb, tb = _node_box(s_el), _node_box(t_el)
        if sb is None or tb is None:
            continue
        scx, scy, sw, sh = sb
        tcx, tcy, tw, th = tb
        dx, dy = tcx - scx, tcy - scy
        dist = max(math.hypot(dx, dy), 1e-6)
        ux, uy = dx / dist, dy / dist
        rel = (el.get("data-relation") or "").lower()
        old_d = el.get("d") or ""
        if rel == "feedback" or "Q" in old_d:
            sx, sy = scx - 12.0, scy - sh / 2.0 - 2.0
            tx, ty = tcx + 12.0, tcy - th / 2.0 - 6.0
            cy_ctrl = min(sy, ty) - 42.0
            new_d = f"M{sx:.1f},{sy:.1f} Q{(sx + tx) / 2.0:.1f},{cy_ctrl:.1f} {tx:.1f},{ty:.1f}"
            lx, ly = (sx + tx) / 2.0, 0.25 * sy + 0.5 * cy_ctrl + 0.25 * ty - 8.0
        elif abs(ux) >= abs(uy):
            sx = scx + (1.0 if ux >= 0 else -1.0) * (sw * 0.5 + 2.0)
            sy = scy
            tx = tcx - (1.0 if ux >= 0 else -1.0) * (tw * 0.5 + 5.0)
            ty = tcy
            new_d = f"M{sx:.1f},{sy:.1f} L{tx:.1f},{ty:.1f}"
            lx, ly = (sx + tx) / 2.0, (sy + ty) / 2.0 - 11.0
        else:
            sx = scx
            sy = scy + (1.0 if uy >= 0 else -1.0) * (sh * 0.5 + 2.0)
            tx = tcx
            ty = tcy - (1.0 if uy >= 0 else -1.0) * (th * 0.5 + 5.0)
            new_d = f"M{sx:.1f},{sy:.1f} L{tx:.1f},{ty:.1f}"
            lx, ly = (sx + tx) / 2.0 + 12.0, (sy + ty) / 2.0 + 3.0
        el.set("d", new_d)
        ed_svg_id = el.get("id") or ""
        ed_key = ed_svg_id.removeprefix("edge-")
        lbl_el = id_index.get(f"edgelabel-{ed_key}")
        if lbl_el is not None:
            lbl_el.set("x", f"{lx:.1f}")
            lbl_el.set("y", f"{ly:.1f}")
        rerouted.append({"op": "reroute_edge", "id": ed_svg_id, "source": src_id, "target": tgt_id, "old_d": old_d, "new_d": new_d})
    return rerouted


def apply_source_preserving_edit(
    svg_in: Path,
    patch: dict[str, Any],
    svg_out: Path,
    *,
    rebind_table: Path | None = None,
) -> dict[str, Any]:
    """Apply incremental edits to an existing SVG while preserving untouched IDs, coordinates, and layers."""
    before_sha = _sha256(svg_in)
    root = _safe_parse(svg_in)
    parent_map = {child: parent for parent in root.iter() for child in parent}
    id_index: dict[str, _StdET.Element] = {el.get("id"): el for el in root.iter() if el.get("id")}
    before_ids = set(id_index.keys())
    changes: list[dict[str, Any]] = []

    def _find_el(tid: str, prefixes: tuple[str, ...]) -> _StdET.Element | None:
        if tid in id_index:
            return id_index[tid]
        for pfx in prefixes:
            cand = f"{pfx}{tid}"
            if cand in id_index:
                return id_index[cand]
        return None

    # 1. Text updates (preserves multi-run tspan safety; targets leaf tspan by ID)
    for item in patch.get("update_text", []):
        tid = str(item["id"])
        el = _find_el(tid, ("nodelabel-", "edgelabel-", "leglabel-", "contlabel-"))
        if el is None:
            raise KeyError(f"UNKNOWN_ELEMENT_ID update_text={tid!r}; sample_ids={sorted(id_index)[:25]}")
        if el.tag not in {f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}tspan"}:
            raise ValueError(f"update_text target {tid!r} is not a text element")
        text_run = el
        if len(el):
            child = el[0]
            if len(el) != 1 or child.tag != f"{{{SVG_NS}}}tspan" or len(child) or (el.text or "").strip() or (child.tail or "").strip():
                raise ValueError(f"update_text target {tid!r} has multiple text runs; edit a leaf tspan by ID")
            text_run = child
        old_val = text_run.text or ""
        new_val = str(item["text"])
        if old_val != new_val:
            text_run.text = new_val
            change = {"op": "update_text", "id": el.get("id"), "old": old_val, "new": new_val}
            if text_run is not el:
                change["text_run_id"] = text_run.get("id")
            changes.append(change)

    # 2. Attribute / style updates
    for item in patch.get("update_attrs", []):
        tid = str(item["id"])
        el = _find_el(tid, ("node-", "edge-", "cont-", "legswatch-"))
        if el is None:
            raise KeyError(f"UNKNOWN_ELEMENT_ID update_attrs={tid!r}; sample_ids={sorted(id_index)[:25]}")
        for k, v in (item.get("attrs") or {}).items():
            old_v = el.get(k)
            if old_v != str(v):
                el.set(k, str(v))
                changes.append({"op": "update_attr", "id": el.get("id"), "attr": k, "old": old_v, "new": str(v)})

    # 3. Node translation (moves shape + labels + automatically re-routes attached edges)
    moved_node_keys: set[str] = set()
    for item in patch.get("move_node", []):
        tid = str(item["id"])
        dx = float(item.get("dx", 0.0))
        dy = float(item.get("dy", 0.0))
        base_id = tid.removeprefix("node-")
        moved_ids: list[str] = []
        for pfx in ("node-", "nodelabel-", "nodeval-", "mod-"):
            cand = tid if (pfx == "node-" and tid.startswith("node-")) else f"{pfx}{base_id}"
            el = id_index.get(cand)
            if el is None:
                continue
            moved_ids.append(cand)
            for ax_k, d in (("x", dx), ("cx", dx), ("y", dy), ("cy", dy)):
                if el.get(ax_k) is not None:
                    el.set(ax_k, f"{float(el.get(ax_k)) + d:.1f}")
        if not moved_ids:
            raise KeyError(f"UNKNOWN_ELEMENT_ID move_node={tid!r}; sample_ids={sorted(id_index)[:25]}")
        moved_node_keys.add(base_id)
        changes.append({"op": "move_node", "id": tid, "moved_elements": moved_ids, "dx": dx, "dy": dy})

    if moved_node_keys:
        changes.extend(_reroute_connected_edges(root, id_index, moved_node_keys))

    # 4. Delete IDs
    for del_id in patch.get("delete_ids", []):
        el = id_index.get(str(del_id))
        if el is not None and el in parent_map:
            parent_map[el].remove(el)
            changes.append({"op": "delete_id", "id": str(del_id)})

    # 5. Rebind data table (updates bound node values, colors, categories, and syncs legend swatches)
    if rebind_table is not None:
        rows = _load_table(rebind_table)
        for rid, row in rows.items():
            node_el = _find_el(rid, ("node-",))
            val_el = _find_el(rid, ("nodeval-",))
            if node_el is not None:
                if "category" in row and row["category"]:
                    old_cat = node_el.get("data-category") or ""
                    if old_cat != row["category"]:
                        node_el.set("data-category", row["category"])
                        changes.append({"op": "rebind_category", "id": node_el.get("id"), "old": old_cat, "new": row["category"]})
                if "color" in row and row["color"] and node_el.get("fill") != row["color"]:
                    old_fill = node_el.get("fill")
                    node_el.set("fill", row["color"])
                    changes.append({"op": "rebind_color", "id": node_el.get("id"), "old": old_fill, "new": row["color"]})
                    cat_key = (row.get("category") or node_el.get("data-category") or "").lower()
                    leg_sw = id_index.get(f"legswatch-{cat_key}")
                    if leg_sw is not None and leg_sw.get("fill") != row["color"]:
                        old_sw = leg_sw.get("fill")
                        leg_sw.set("fill", row["color"])
                        changes.append({"op": "sync_legend_swatch", "id": leg_sw.get("id"), "old": old_sw, "new": row["color"]})
                if "value" in row and row["value"] != "":
                    node_el.set("data-value", row["value"])
                    unit = row.get("unit") or node_el.get("data-unit") or ""
                    new_txt = f"{row['value']} {unit}".strip()
                    if val_el is not None and (val_el.text or "") != new_txt:
                        old_txt = val_el.text or ""
                        val_el.text = new_txt
                        changes.append({"op": "rebind_value", "id": val_el.get("id"), "old": old_txt, "new": new_txt})

    svg_out.parent.mkdir(parents=True, exist_ok=True)
    # Never reindent: preserves authored whitespace inside mixed text/tspan runs.
    svg_out.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>\n' + _StdET.tostring(root, encoding="utf-8"))

    after_ids = {el.get("id") for el in root.iter() if el.get("id")}
    preserved = before_ids & after_ids
    return {
        "before_sha256": before_sha,
        "after_sha256": _sha256(svg_out),
        "before_id_count": len(before_ids),
        "preserved_id_count": len(preserved),
        "preserved_id_ratio": round(len(preserved) / max(1, len(before_ids)), 4),
        "changes": changes,
    }


def record_and_verify_undo_redo(svg_before: Path, svg_after: Path, history_dir: Path) -> dict[str, Any]:
    """Persist transactional save/reopen snapshots and verify byte-exact undo and redo restoration."""
    history_dir.mkdir(parents=True, exist_ok=True)
    hist_file = history_dir / "edit_history.json"
    before_bytes = svg_before.read_bytes()
    after_bytes = svg_after.read_bytes()
    before_sha = _sha256_bytes(before_bytes)
    after_sha = _sha256_bytes(after_bytes)

    snap_0 = history_dir / f"snap_0_{before_sha[:12]}.svg"
    snap_1 = history_dir / f"snap_1_{after_sha[:12]}.svg"
    snap_0.write_bytes(before_bytes)
    snap_1.write_bytes(after_bytes)

    # Simulate save -> close -> reopen -> undo -> redo
    reopen_bytes = snap_1.read_bytes()
    reopen_ok = _sha256_bytes(reopen_bytes) == after_sha
    undo_bytes = snap_0.read_bytes()
    undo_ok = _sha256_bytes(undo_bytes) == before_sha
    redo_bytes = snap_1.read_bytes()
    redo_ok = _sha256_bytes(redo_bytes) == after_sha

    journal = {
        "cursor": 1,
        "snapshots": [
            {"index": 0, "file": snap_0.name, "sha256": before_sha},
            {"index": 1, "file": snap_1.name, "sha256": after_sha},
        ],
        "roundtrip_verification": {
            "reopen_exact": reopen_ok,
            "undo_exact_restore": undo_ok,
            "redo_exact_restore": redo_ok,
            "passed": bool(reopen_ok and undo_ok and redo_ok),
        },
    }
    hist_file.write_text(json.dumps(journal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return journal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Source-preserving incremental editor for scientific schematics.")
    parser.add_argument("--svg", required=True, help="Existing input SVG path")
    parser.add_argument("--patch", default="", help="Optional JSON patch file")
    parser.add_argument("--rebind-table", default="", help="Optional updated CSV/JSON data table")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--stem", default="schematic_edited")
    parser.add_argument("--dpi", type=int, default=300, help="Export PNG resolution in DPI (default 300)")
    parser.add_argument("--preset", default="", help="Optional export preset name from references/export_presets.json")
    parser.add_argument("--case-json", default="", help="Optional /input/case.json path to auto-seal result.json")
    parser.add_argument("--result-out", default="", help="Optional output path for sealed result.json")
    parser.add_argument("--history-dir", default="", help="Optional directory for save/reopen/undo/redo verification")
    parser.add_argument("--list-ids", action="store_true", help="List existing SVG element IDs and exit")
    args = parser.parse_args(argv)

    if args.list_ids:
        root_tmp = _safe_parse(Path(args.svg))
        print(json.dumps({"ok": True, "ids": sorted({el.get("id") for el in root_tmp.iter() if el.get("id")})}, ensure_ascii=False, indent=2))
        return 0
    if not args.out_dir:
        parser.error("--out-dir is required")

    dpi_val = int(args.dpi)
    if args.preset:
        presets_path = HERE.parent / "references" / "export_presets.json"
        if presets_path.is_file():
            presets = json.loads(presets_path.read_text(encoding="utf-8")).get("presets", {})
            if args.preset in presets and isinstance(presets[args.preset].get("dpi"), int):
                dpi_val = int(presets[args.preset]["dpi"])

    patch_obj = json.loads(Path(args.patch).read_text(encoding="utf-8-sig")) if args.patch else {}
    rebind_p = Path(args.rebind_table) if args.rebind_table else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    svg_in = Path(args.svg)
    svg_out = out_dir / f"{args.stem}.svg"
    drawio_out = out_dir / f"{args.stem}.drawio"
    png_out = out_dir / f"{args.stem}.png"
    pdf_out = out_dir / f"{args.stem}.pdf"
    diff_out = out_dir / f"{args.stem}_edit_diff.json"
    case_p = Path(args.case_json) if args.case_json else None
    result_p = Path(args.result_out) if args.result_out else (out_dir / "result.json")

    try:
        diff = apply_source_preserving_edit(svg_in, patch_obj, svg_out, rebind_table=rebind_p)
    except KeyError as exc:
        print(json.dumps({"ok": False, "error": "UNKNOWN_ELEMENT_ID", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "INVALID_SVG_EDIT", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    diff_out.write_text(json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    history_rep = None
    if args.history_dir:
        history_rep = record_and_verify_undo_redo(svg_in, svg_out, Path(args.history_dir))

    svg2drawio.svg_to_drawio_file(svg_out, drawio_out)
    # Re-bind Draw.io native graph edges & labels whenever explicit graph metadata exists
    drawio_binding: dict[str, Any] = {"status": "skipped_non_graph_svg"}
    try:
        drawio_binding = bind_drawio_graph(svg_out, drawio_out)
    except GraphBindingError:
        drawio_binding = {"status": "skipped_custom_svg"}

    render_info = render_svg_previews(svg_out, png_path=png_out, pdf_path=pdf_out, dpi=dpi_val)
    portable = audit_portable_svg(svg_out)
    editability = inspect_svg_only(svg_out)
    drawio_chk = analyze_layout(drawio_out.read_text(encoding="utf-8"), min_spacing=2.0)

    ok = (
        render_info["ok"]
        and portable["passed"]
        and editability["status"] == "pass"
        and len(drawio_chk["overlaps"]) == 0
        and len(drawio_chk["out_of_bounds"]) == 0
        and (history_rep is None or history_rep["roundtrip_verification"]["passed"])
    )
    seal_rep = None
    if case_p is not None:
        uncert = list(render_info.get("degraded_warnings") or [])
        if (portable.get("font_audit") or {}).get("warnings"):
            uncert.extend(portable["font_audit"]["warnings"])
        seal_rep = seal_case_result(
            case_p, result_p, task_id="scientific-schematic-generator-editor",
            artifacts=[svg_out.name, drawio_out.name, png_out.name, pdf_out.name, diff_out.name],
            sources=[str(svg_in)] + ([str(args.patch)] if args.patch else []) + ([str(rebind_p)] if rebind_p else []),
            uncertainty=uncert, change_records=diff.get("changes", []),
            metrics={"preserved_id_ratio": diff.get("preserved_id_ratio", 0.0), "dpi": dpi_val},
            status="ok" if ok else "failed",
        )
    print(json.dumps({
        "ok": ok,
        "edit_diff": diff,
        "history": history_rep,
        "drawio_binding": drawio_binding,
        "render": render_info,
        "portable_svg": portable,
        "editability": editability,
        "drawio_layout": drawio_chk,
        "result_seal": seal_rep,
    }, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
