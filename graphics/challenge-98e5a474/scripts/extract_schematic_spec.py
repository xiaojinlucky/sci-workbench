#!/usr/bin/env python3
"""Deterministic raw /input -> schematic_spec.json extractor with CSV binding, source_ref & license audit (stdlib-only)."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HDR_RE = re.compile(r"^(?:#{1,4}\s*|(?:stage|phase|module|section|step)\s*\d*\s*[:.\-–])\s*(.+)$", re.I)
_BULLET_RE = re.compile(r"^(?:[-*]\s*|\d+[.)]\s*)(.+)$")
_NUM_UNIT_RE = re.compile(r"(?P<val>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>%|x|fold|mg|uM|µM|nM|kPa|MPa|mm|nm|h|min|cells|samples|donors|AUROC)\b", re.I)


_HTML_NOISE_RE = re.compile(r"<(?:script|style|nav|header|footer|noscript|svg)\b[^>]*>[\s\S]*?</(?:script|style|nav|header|footer|noscript|svg)>", re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>", re.S)


def _read_lines(path: Path) -> list[tuple[int, str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        exe = shutil.which("pdftotext")
        if not exe:
            return []
        proc = subprocess.run([exe, str(path), "-"], capture_output=True, text=True, check=False, timeout=30)
        return [(i, ln.strip()) for i, ln in enumerate(proc.stdout.splitlines(), 1) if ln.strip()] if proc.returncode == 0 else []
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    if ext in (".html", ".htm", ".xml"):
        raw = _HTML_NOISE_RE.sub(" ", raw)
        m_art = re.search(r"<article\b[^>]*>([\s\S]*?)</article>", raw, re.I)
        if m_art:
            raw = m_art.group(1)
        raw = re.sub(r"</(?:p|div|h[1-6]|li|tr|section|article)\s*>", "\n", raw, flags=re.I)
        raw = html.unescape(_HTML_TAG_RE.sub(" ", raw))
        out: list[tuple[int, str]] = []
        for i, ln in enumerate(raw.splitlines(), 1):
            c = _WS_RE.sub(" ", ln).strip()
            if c.lower() in ("references", "acknowledgments"):
                break
            if len(c) >= 12 and not any(tok in c.lower() for tok in ("class=", "href=", "aria-")):
                out.append((i, c))
        return out
    return [(i, ln.strip()) for i, ln in enumerate(raw.splitlines(), 1) if ln.strip()]


def _san_id(raw: str, idx: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")[:18]
    return s or f"node_{idx}"


def extract_schematic_from_input(input_dir: Path) -> dict[str, Any]:
    """Deterministically build a grounded schematic_spec.json from raw /input texts, tables & case.json."""
    containers: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    not_found: list[str] = []
    title = ""
    csv_table: str = ""
    lines_indexed = 0

    cont_fills = [("#EDF4F5", "#547FA3"), ("#F3F6FA", "#4E9187"), ("#F7F3F8", "#967A9C")]
    cats = ["primary", "secondary", "accent", "warning", "highlight"]
    cur_cont = ""

    for p in sorted(input_dir.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(input_dir).as_posix()
        ext = p.suffix.lower()
        if p.name == "case.json":
            try:
                co = json.loads(p.read_text(encoding="utf-8-sig"))
                title = str(co.get("title") or "").strip()
            except Exception:
                pass
            continue
        if ext == ".csv":
            csv_table = rel
            continue
        if ext in (".md", ".txt", ".html", ".htm", ".pdf"):
            for lno, text in _read_lines(p):
                lines_indexed += 1
                if not title and (text.startswith("# ") or (lno <= 10 and 14 <= len(text) <= 105)):
                    title = text.lstrip("# ").strip()
                    continue
                m_h = _HDR_RE.match(text)
                if m_h and len(text) <= 80 and len(containers) < 3:
                    cid = f"stage{len(containers)+1}"
                    fc, sc = cont_fills[len(containers) % len(cont_fills)]
                    x0 = 36 + len(containers) * 344
                    containers.append({
                        "id": cid, "label": m_h.group(1).strip()[:32],
                        "x": x0, "y": 82, "width": 300, "height": 465,
                        "fill": fc, "stroke": sc, "source_ref": f"{rel}#L{lno}",
                    })
                    cur_cont = cid
                    continue
                m_b = _BULLET_RE.match(text)
                if m_b and len(nodes) < 9:
                    body = m_b.group(1).strip()
                    if len(body) < 8:
                        continue
                    if not cur_cont:
                        cur_cont = "stage1"
                        containers.append({
                            "id": "stage1", "label": "Stage 1: Core Workflow",
                            "x": 36, "y": 82, "width": 988, "height": 465,
                            "fill": "#EDF4F5", "stroke": "#547FA3", "source_ref": f"{rel}#L{lno}",
                        })
                    lbl = re.split(r"[:(\[—–]|\s-\s", body, maxsplit=1)[0].strip()[:28]
                    nid = _san_id(lbl, len(nodes) + 1)
                    if any(n["id"] == nid for n in nodes):
                        nid = f"{nid}_{len(nodes)+1}"
                    m_num = _NUM_UNIT_RE.search(body)
                    node_obj: dict[str, Any] = {
                        "id": nid,
                        "label": lbl or f"Entity {len(nodes)+1}",
                        "container": cur_cont,
                        "category": cats[len(containers) - 1 if containers else 0],
                        "source_ref": f"{rel}#L{lno}",
                        "_raw": body,
                    }
                    if m_num:
                        node_obj["value"] = m_num.group("val")
                        node_obj["unit"] = m_num.group("unit")
                    nodes.append(node_obj)

    if not nodes:
        not_found.append("schematic_entities")

    # Connect sequential entities and build relation_audit (polarity evidence cue + container hierarchy check)
    cont_order = {c["id"]: i for i, c in enumerate(containers)}
    relation_audit: list[dict[str, Any]] = []
    for idx in range(len(nodes) - 1):
        n1, n2 = nodes[idx], nodes[idx + 1]
        raw_ctx = f"{n1.get('_raw', '')} {n2.get('_raw', '')}"
        raw_low = raw_ctx.lower()
        cue = "sequential_flow"
        if any(k in raw_low for k in ("inhibit", "exclude", "reject", "block", "suppress")):
            rel_t = "inhibit"
            cue = next(k for k in ("inhibit", "exclude", "reject", "block", "suppress") if k in raw_low)
        elif any(k in raw_low for k in ("parallel", "concurrent", "bind", "pair")):
            rel_t = "parallel"
            cue = next(k for k in ("parallel", "concurrent", "bind", "pair") if k in raw_low)
        elif any(k in raw_low for k in ("putative", "uncertain", "may ", "hypothesis")):
            rel_t = "uncertain"
            cue = next(k for k in ("putative", "uncertain", "may ", "hypothesis") if k in raw_low)
        else:
            rel_t = "causal"
        s_ord = cont_order.get(n1["container"], 0)
        t_ord = cont_order.get(n2["container"], 0)
        hier_ok = t_ord >= s_ord
        ed_id = f"e{idx+1}"
        edges.append({
            "id": ed_id,
            "source": n1["id"],
            "target": n2["id"],
            "relation": rel_t,
            "label": rel_t,
            "source_ref": n2["source_ref"],
        })
        relation_audit.append({
            "edge_id": ed_id,
            "source": n1["id"],
            "target": n2["id"],
            "relation": rel_t,
            "polarity_cue": cue,
            "source_container_rank": s_ord,
            "target_container_rank": t_ord,
            "hierarchy_direction_ok": hier_ok,
            "source_ref": n2["source_ref"],
            "excerpt": str(n2.get("_raw", ""))[:140],
        })

    if len(nodes) >= 3 and any(re.search(r"\b(?:feedback|re-calibrat|iterat|loop)\b", str(n.get("_raw", "")), re.I) for n in nodes):
        ed_id = f"e{len(edges)+1}"
        edges.append({
            "id": ed_id,
            "source": nodes[-1]["id"],
            "target": nodes[0]["id"],
            "relation": "feedback",
            "label": "feedback",
            "source_ref": nodes[-1]["source_ref"],
        })
        relation_audit.append({
            "edge_id": ed_id,
            "source": nodes[-1]["id"],
            "target": nodes[0]["id"],
            "relation": "feedback",
            "polarity_cue": "feedback_loop",
            "source_container_rank": cont_order.get(nodes[-1]["container"], 0),
            "target_container_rank": cont_order.get(nodes[0]["container"], 0),
            "hierarchy_direction_ok": True,
            "source_ref": nodes[-1]["source_ref"],
            "excerpt": str(nodes[-1].get("_raw", ""))[:140],
        })

    clean_nodes = [{k: v for k, v in n.items() if k != "_raw"} for n in nodes]
    return {
        "title": title or "Scientific Schematic (Auto-Extracted Draft)",
        "subtitle": f"Derived deterministically from {input_dir.name} ({len(clean_nodes)} entities, {len(edges)} relations)",
        "canvas_width_px": 1060,
        "canvas_height_px": 640,
        "containers": containers,
        "nodes": clean_nodes,
        "edges": edges,
        "assets": [
            {"id": "native-svg-primitives", "author": "Self-Authored", "license": "Self-Authored", "source": "internal_svg_builder"}
        ],
        "relation_audit": relation_audit,
        "extraction_provenance": {
            "extractor": "extract_schematic_spec.py",
            "input_dir": str(input_dir.name),
            "lines_indexed": lines_indexed,
            "containers_extracted": len(containers),
            "nodes_extracted": len(clean_nodes),
            "edges_extracted": len(edges),
            "hierarchy_conflicts": sum(1 for r in relation_audit if not r["hierarchy_direction_ok"]),
            "csv_table_detected": csv_table or None,
            "not_found_in_source": not_found,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministically extract schematic_spec.json and relation_audit.json from raw /input files.")
    parser.add_argument("--input-dir", required=True, help="Raw input directory")
    parser.add_argument("--out", required=True, help="Output schematic_spec.json path")
    parser.add_argument("--relation-audit-out", default="", help="Optional relation_audit.json output path (defaults to sibling *_relation_audit.json)")
    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    if not in_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"Input directory not found: {in_dir}"}, indent=2))
        return 2
    spec = extract_schematic_from_input(in_dir)
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    audit_p = Path(args.relation_audit_out) if args.relation_audit_out else out_p.with_name(f"{out_p.stem}_relation_audit.json")
    audit_p.write_text(json.dumps({"ok": True, "edges_audited": len(spec["relation_audit"]), "relation_audit": spec["relation_audit"]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_p.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_p), "relation_audit_out": str(audit_p), "extraction_provenance": spec["extraction_provenance"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
