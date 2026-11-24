# -*- coding: utf-8 -*-
"""Direction-Aware Citation Graph Tracer, Identity Merger & Snapshot Diff Auditor (challenge-60a8202d).

Pure Python 3.12 stdlib offline pipeline built on `search_core` and `common_core`:
- Auto-discovers seeds from explicit seed/graph files or case.json (with explicit `auto_inferred` fallback).
- Runs strict cross-form identity merging via `search_core.dedup.cluster_records_detailed()`, blocking
  false merges across distinct formal DOIs (`ambiguous_candidates`) and preserving ASReview #328 rollback logs.
- Builds directed citation graphs via `search_core.graph.build_graph()`, reconciles cross-file canonical
  endpoints (OpenAlex ID / DOI / arXiv / PMID), and traces multi-hop backward/forward paths with branch
  pruning, co-citation, bibliographic coupling, structural roles, and per-hop direction-annotated paths.
- Audits prior vs. current graph snapshots via `search_core.graph.diff_graph_snapshots()` (CitationChaser #37).
- Outputs `edge_provenance_table.csv`, `citation_network.json`, `snapshot_diff.json`, `report.md`,
  `audit_log.json`, `run_manifest.json`, and Harbor-compliant `result.json` verified by M2/M5/M6 gates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from search_core import caseio
from search_core import contract as ct
from search_core import dedup as dd
from search_core import graph as gr
from search_core import pipeline as pl
from search_core import scholarmeta as sm


def _is_explicit_graph_payload(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    return any(isinstance(obj.get(k), list) for k in ("seeds", "seed_papers", "seed_ids", "nodes", "edges", "links"))


def _is_previous_snapshot_filename(rel: str) -> bool:
    low = os.path.basename(rel).lower()
    return any(tok in low for tok in ("previous", "prev", "prior", "baseline", "before", "old")) and (
        "snapshot" in low or "graph" in low
    )


def _extract_clean_graph_ref_sets(bundle: caseio.CaseBundle) -> Dict[str, Dict[str, Any]]:
    """Keep only genuine graph/seed files; ignore accidental inverted-index word keys in API JSON."""
    clean: Dict[str, Dict[str, Any]] = {}
    parsed_map: Dict[str, Any] = bundle.extra.get("parsed", {}) or {}

    for f in bundle.files:
        if f.ext not in (".json", ".jsonl"):
            continue
        if _is_previous_snapshot_filename(f.rel):
            continue
        obj = parsed_map.get(f.rel)
        if f.role == "reference_graph" and _is_explicit_graph_payload(obj):
            clean[f"file@{f.rel}"] = {"file": f.rel, "pointer": "", "data": obj}
        elif any(tok in os.path.basename(f.rel).lower() for tok in ("seed", "graph")) and _is_explicit_graph_payload(obj):
            clean.setdefault(f"file@{f.rel}", {"file": f.rel, "pointer": "", "data": obj})

    # Include explicit graph structure embedded in case.json if present
    if isinstance(bundle.case, dict) and _is_explicit_graph_payload(bundle.case):
        clean["case@case.json"] = {"file": "case.json", "pointer": "", "data": bundle.case}
    return clean


def _build_id_alias_map(
    nodes: Dict[str, Dict[str, Any]],
    clusters: List[dd.Cluster],
) -> Dict[str, str]:
    """Map every known identifier (rid, OpenAlex ID, DOI, alt_DOI, arXiv base, PMID, norm_title) -> canonical rid."""
    alias: Dict[str, str] = {}
    title_map: Dict[str, str] = {}

    def _reg(key: str, rid: str) -> None:
        if key:
            alias.setdefault(key, rid)
            alias.setdefault(key.lower(), rid)

    for c in clusters:
        rid = c.canonical.rid
        _reg(rid, rid)
        for m in c.members:
            _reg(m.rid, rid)
        for d in c.ids.get("doi", []) + c.ids.get("alt_doi", []):
            nd = sm.norm_doi(d)
            if nd:
                _reg(nd, rid)
                _reg(f"https://doi.org/{nd}", rid)
        for p in c.ids.get("pmid", []):
            np = sm.norm_pmid(p)
            if np:
                _reg(np, rid)
                _reg(f"pmid:{np}", rid)
        for a in c.ids.get("arxiv", []):
            na = sm.arxiv_base(a)
            if na:
                _reg(na, rid)
                _reg(f"arxiv:{na}", rid)
                _reg(f"10.48550/arxiv.{na}", rid)
        for oa in c.ids.get("openalex", []):
            tail = str(oa).rsplit("/", 1)[-1].strip()
            if tail:
                _reg(tail, rid)
                _reg(f"https://openalex.org/{tail}", rid)
        nt = sm.norm_title(c.canonical.title)
        if nt:
            title_map.setdefault(nt, rid)

    for rid, info in nodes.items():
        _reg(rid, rid)
        if info.get("doi"):
            nd = sm.norm_doi(info["doi"])
            _reg(nd, rid)
        if info.get("openalex"):
            tail = str(info["openalex"]).rsplit("/", 1)[-1].strip()
            _reg(tail, rid)
        if info.get("arxiv"):
            na = sm.arxiv_base(info["arxiv"])
            _reg(na, rid)
            _reg(f"10.48550/arxiv.{na}", rid)
        if info.get("pmid"):
            _reg(sm.norm_pmid(info["pmid"]), rid)
        nt = sm.norm_title(info.get("title", ""))
        if nt:
            title_map.setdefault(nt, rid)

    alias["__title_map__"] = title_map  # type: ignore[assignment]
    return alias


def _resolve_identifier(raw: Any, alias_map: Dict[str, Any]) -> Optional[str]:
    if not raw:
        return None
    title_map: Dict[str, str] = alias_map.get("__title_map__", {})
    if isinstance(raw, dict):
        for k in ("rid", "id", "openalex_id", "openalex", "doi", "DOI", "arxiv_id", "arxiv", "pmid"):
            v = raw.get(k)
            if v:
                hit = _resolve_identifier(v, alias_map)
                if hit:
                    return hit
        t = sm.norm_title(raw.get("title") or raw.get("label") or "")
        if t and t in title_map:
            return title_map[t]
        return None

    s = str(raw).strip()
    if not s:
        return None
    if s in alias_map:
        return alias_map[s]
    if s.lower() in alias_map:
        return alias_map[s.lower()]
    tail = s.rsplit("/", 1)[-1].strip()
    if tail in alias_map:
        return alias_map[tail]
    if tail.lower() in alias_map:
        return alias_map[tail.lower()]
    nd = sm.norm_doi(s)
    if nd and nd in alias_map:
        return alias_map[nd]
    na = sm.arxiv_base(s)
    if na and na in alias_map:
        return alias_map[na]
    np = sm.norm_pmid(s)
    if np and np in alias_map:
        return alias_map[np]
    nt = sm.norm_title(s)
    if nt and nt in title_map:
        return title_map[nt]
    return None


def _reconcile_graph_endpoints(graph_obj: Dict[str, Any], alias_map: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve dangling edge endpoints onto canonical node rids and rebuild in/out degree tables."""
    nodes: Dict[str, Dict[str, Any]] = graph_obj["nodes"]
    raw_edges: List[Dict[str, Any]] = graph_obj["edges"]

    # Reset adjacency lists cleanly
    for n in nodes.values():
        n["in"] = []
        n["out"] = []

    merged_edges: List[Dict[str, Any]] = []
    idx_map: Dict[Tuple[str, str, str], int] = {}
    dangling_edges: List[Dict[str, Any]] = []

    for e in raw_edges:
        src_orig = e["src"]
        dst_orig = e["dst"]
        src = _resolve_identifier(src_orig, alias_map) or src_orig
        dst = _resolve_identifier(dst_orig, alias_map) or dst_orig
        rel = e.get("relation", "cites")
        if src == dst:
            continue
        prov_list = list(e.get("provenance_list") or ([e["provenance"]] if e.get("provenance") else []))
        if src not in nodes or dst not in nodes:
            dangling_edges.append({"src": src, "dst": dst, "relation": rel, "provenance": prov_list[:2]})
            continue

        key = (src, dst, rel)
        if key in idx_map:
            existing = merged_edges[idx_map[key]]
            existing.setdefault("provenance_list", []).extend(prov_list)
            continue
        idx_map[key] = len(merged_edges)
        merged_edges.append(
            {
                "src": src,
                "dst": dst,
                "relation": rel,
                "provenance": prov_list[0] if prov_list else {},
                "provenance_list": prov_list,
            }
        )

    for e in merged_edges:
        nodes[e["src"]]["out"].append(e["dst"])
        nodes[e["dst"]]["in"].append(e["src"])

    for n in nodes.values():
        n["in"] = sorted(set(n["in"]))
        n["out"] = sorted(set(n["out"]))

    graph_obj["edges"] = merged_edges
    graph_obj["dangling_edges"] = dangling_edges
    graph_obj["stats"] = {
        "n_nodes": len(nodes),
        "n_edges": len(merged_edges),
        "n_unresolved_refs": graph_obj.get("stats", {}).get("n_unresolved_refs", len(graph_obj.get("unresolved_references", []))),
        "n_retracted_nodes": sum(1 for n in nodes.values() if n.get("is_retracted")),
        "n_connected_nodes": sum(1 for n in nodes.values() if n["in"] or n["out"]),
        "n_isolated_nodes": sum(1 for n in nodes.values() if not n["in"] and not n["out"]),
        "n_dangling_edges_dropped": len(dangling_edges),
    }
    return graph_obj


def _discover_seeds(
    bundle: caseio.CaseBundle,
    clean_ref_sets: Dict[str, Dict[str, Any]],
    graph_obj: Dict[str, Any],
    alias_map: Dict[str, Any],
    state: Dict[str, Any],
) -> Tuple[List[str], List[Dict[str, Any]], str]:
    """Identify seed nodes from explicit inputs or fall back transparently to auto_inferred."""
    nodes = graph_obj["nodes"]
    raw_seed_specs: List[Tuple[Any, str]] = []

    # 1. Explicit graph/seed files (e.g., seed_and_graph.json)
    for k, rs in clean_ref_sets.items():
        data = rs.get("data")
        if isinstance(data, dict):
            for sk in ("seeds", "seed_papers", "seed_ids", "seed"):
                val = data.get(sk)
                if isinstance(val, list):
                    for item in val:
                        raw_seed_specs.append((item, f"{rs.get('file')}:{sk}"))
                elif isinstance(val, (str, dict)) and val:
                    raw_seed_specs.append((val, f"{rs.get('file')}:{sk}"))

    # 2. Explicit fields in case.json (if not already collected)
    if isinstance(bundle.case, dict):
        for sk in ("seeds", "seed_papers", "seed_ids", "primary_doi", "seed_doi", "seed_openalex_id", "seed_arxiv_id"):
            val = bundle.case.get(sk)
            if isinstance(val, list):
                for item in val:
                    raw_seed_specs.append((item, f"case.json:{sk}"))
            elif isinstance(val, (str, dict)) and val:
                raw_seed_specs.append((val, f"case.json:{sk}"))

    seed_rids: List[str] = []
    seed_records: List[Dict[str, Any]] = []
    seen_rids = set()

    for spec, origin in raw_seed_specs:
        resolved = _resolve_identifier(spec, alias_map)
        if resolved and resolved in nodes:
            if resolved not in seen_rids:
                seen_rids.add(resolved)
                seed_rids.append(resolved)
                nd = nodes[resolved]
                seed_records.append(
                    {
                        "seed_rid": resolved,
                        "declared_raw": spec,
                        "origin": origin,
                        "title": nd.get("title", ""),
                        "doi": nd.get("doi", ""),
                        "openalex": nd.get("openalex", ""),
                        "year": nd.get("year") or 0,
                        "resolution": "matched_canonical_node",
                    }
                )
        else:
            # Create explicit external seed node so zero-citation or unindexed seeds are never dropped
            if isinstance(spec, dict):
                sid = str(spec.get("id") or spec.get("rid") or sm.norm_doi(spec.get("doi", "")) or "").strip()
                title = str(spec.get("title") or sid)
                doi = sm.norm_doi(spec.get("doi") or spec.get("DOI") or "")
                year = sm._year_from(spec.get("year"))
                version = str(spec.get("version") or "unknown")
            else:
                sid = str(spec).strip()
                title = sid
                doi = sm.norm_doi(sid) if sid.lower().startswith("10.") else ""
                year = 0
                version = "unknown"
            if not sid:
                continue
            if sid not in nodes:
                nodes[sid] = {
                    "rid": sid,
                    "title": title,
                    "authors": [],
                    "year": year,
                    "venue": "",
                    "version": version,
                    "source": "seed_declaration",
                    "sources": ["seed_declaration"],
                    "doi": doi,
                    "pmid": "",
                    "arxiv": sm.arxiv_base(sid),
                    "openalex": sid if sid.startswith("W") else "",
                    "is_retracted": False,
                    "is_correction": False,
                    "n_merged_versions": 1,
                    "external": True,
                    "in": [],
                    "out": [],
                }
                alias_map[sid] = sid
                alias_map[sid.lower()] = sid
                if doi:
                    alias_map[doi] = sid
            if sid not in seen_rids:
                seen_rids.add(sid)
                seed_rids.append(sid)
                seed_records.append(
                    {
                        "seed_rid": sid,
                        "declared_raw": spec,
                        "origin": origin,
                        "title": nodes[sid].get("title", ""),
                        "doi": nodes[sid].get("doi", ""),
                        "openalex": nodes[sid].get("openalex", ""),
                        "year": nodes[sid].get("year") or 0,
                        "resolution": "added_external_seed_node",
                    }
                )

    if seed_rids:
        return seed_rids, seed_records, "explicit_input"

    # 3. Auto-inferred fallback when no explicit seeds exist
    ranked_nodes = sorted(
        nodes.values(),
        key=lambda n: (-(len(n.get("in", [])) + len(n.get("out", []))), -len(n.get("in", [])), -(n.get("year") or 0), n["rid"]),
    )
    if ranked_nodes:
        top = ranked_nodes[0]
        top_rid = top["rid"]
        # If graph has zero edges, prefer top query-matched record if searcher is available
        if not top.get("in") and not top.get("out") and state.get("searcher") and state.get("question"):
            hits = state["searcher"].search(state["question"], top_k=1)
            if hits and hits[0].get("rid") in nodes:
                top_rid = hits[0]["rid"]
                top = nodes[top_rid]
        seed_rids = [top_rid]
        seed_records = [
            {
                "seed_rid": top_rid,
                "declared_raw": None,
                "origin": "auto_inferred:highest_degree_or_relevance",
                "title": top.get("title", ""),
                "doi": top.get("doi", ""),
                "openalex": top.get("openalex", ""),
                "year": top.get("year") or 0,
                "resolution": "auto_inferred",
            }
        ]
    return seed_rids, seed_records, "auto_inferred"


def _audit_previous_snapshot(
    bundle: caseio.CaseBundle,
    curr_graph: Dict[str, Any],
    alias_map: Dict[str, Any],
) -> Dict[str, Any]:
    """Align previous graph snapshot onto canonical IDs and run CitationChaser #37 diff."""
    parsed_map: Dict[str, Any] = bundle.extra.get("parsed", {}) or {}
    prev_rel: Optional[str] = None
    prev_data: Optional[Dict[str, Any]] = None

    for f in bundle.files:
        if f.ext in (".json", ".jsonl") and _is_previous_snapshot_filename(f.rel):
            obj = parsed_map.get(f.rel)
            if isinstance(obj, dict) and (isinstance(obj.get("nodes"), list) or isinstance(obj.get("edges"), list)):
                prev_rel = f.rel
                prev_data = obj
                break

    if prev_data is None:
        snap = bundle.snapshots.get("previous")
        if isinstance(snap, dict) and isinstance(snap.get("data"), dict):
            cand = snap["data"]
            if isinstance(cand.get("nodes"), list) or isinstance(cand.get("edges"), list):
                prev_rel = snap.get("file", "previous_snapshot")
                prev_data = cand

    if prev_data is None:
        return {
            "previous_snapshot_available": False,
            "previous_snapshot_file": None,
            "previous_snapshot_id": None,
            "nodes_added": [],
            "nodes_vanished": [],
            "nodes_persisted": 0,
            "edges_added": [],
            "edges_vanished": [],
            "unresolved_in_current": curr_graph.get("stats", {}).get("n_unresolved_refs", 0),
            "coverage_warning": (
                "No prior graph snapshot was provided in input; snapshot counts reflect finite local input only, "
                "not global citation coverage."
            ),
            "audit_rule": "CitationChaser #37: never report raw hit counts as global citation coverage.",
        }

    # Build canonicalized previous graph structure
    prev_nodes: Dict[str, Dict[str, Any]] = {}
    prev_local_map: Dict[str, str] = {}
    for n in prev_data.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        raw_id = str(n.get("id") or n.get("rid") or "").strip()
        if not raw_id:
            continue
        canon_id = _resolve_identifier(n, alias_map) or _resolve_identifier(raw_id, alias_map) or raw_id
        prev_local_map[raw_id] = canon_id
        prev_nodes[canon_id] = {
            "rid": canon_id,
            "raw_snapshot_id": raw_id,
            "title": n.get("title") or n.get("label") or raw_id,
            "doi": sm.norm_doi(n.get("doi") or n.get("DOI") or ""),
        }

    prev_edges: List[Dict[str, Any]] = []
    for e in prev_data.get("edges") or []:
        if not isinstance(e, dict):
            continue
        s_raw = str(e.get("source") or e.get("src") or e.get("from") or "").strip()
        t_raw = str(e.get("target") or e.get("dst") or e.get("to") or "").strip()
        rel = str(e.get("relation") or e.get("type") or "cites").lower()
        if rel in ("cited_by", "is_cited_by"):
            s_raw, t_raw = t_raw, s_raw
            rel = "cites"
        s = prev_local_map.get(s_raw) or _resolve_identifier(s_raw, alias_map) or s_raw
        t = prev_local_map.get(t_raw) or _resolve_identifier(t_raw, alias_map) or t_raw
        if s and t and s != t:
            prev_edges.append({"src": s, "dst": t, "relation": rel, "raw_source": s_raw, "raw_target": t_raw})

    prev_graph_canon = {"nodes": prev_nodes, "edges": prev_edges}
    diff = gr.diff_graph_snapshots(prev_graph_canon, curr_graph)

    curr_nodes = curr_graph.get("nodes", {})
    vanished_node_details = [
        {
            "rid": nid,
            "raw_snapshot_id": prev_nodes.get(nid, {}).get("raw_snapshot_id", nid),
            "title": prev_nodes.get(nid, {}).get("title", ""),
            "doi": prev_nodes.get(nid, {}).get("doi", ""),
            "status": "vanished_from_current_snapshot",
        }
        for nid in diff["nodes_vanished"]
    ]
    added_node_details = [
        {
            "rid": nid,
            "title": curr_nodes.get(nid, {}).get("title", ""),
            "doi": curr_nodes.get(nid, {}).get("doi", ""),
            "openalex": curr_nodes.get(nid, {}).get("openalex", ""),
            "is_retracted": bool(curr_nodes.get(nid, {}).get("is_retracted")),
        }
        for nid in diff["nodes_added"]
    ]
    vanished_edge_details = [
        {
            **ed,
            "src_title": prev_nodes.get(ed["src"], {}).get("title", curr_nodes.get(ed["src"], {}).get("title", "")),
            "dst_title": curr_nodes.get(ed["dst"], {}).get("title", prev_nodes.get(ed["dst"], {}).get("title", "")),
        }
        for ed in diff["edges_vanished"]
    ]

    return {
        "previous_snapshot_available": True,
        "previous_snapshot_file": prev_rel,
        "previous_snapshot_id": prev_data.get("snapshot_id") or prev_rel,
        "previous_node_count": len(prev_nodes),
        "previous_edge_count": len(prev_edges),
        "current_node_count": len(curr_nodes),
        "current_edge_count": len(curr_graph.get("edges", [])),
        "nodes_persisted": diff["nodes_persisted"],
        "nodes_added_count": len(diff["nodes_added"]),
        "nodes_vanished_count": len(diff["nodes_vanished"]),
        "edges_added_count": len(diff["edges_added"]),
        "edges_vanished_count": len(diff["edges_vanished"]),
        "nodes_added": diff["nodes_added"],
        "nodes_vanished": diff["nodes_vanished"],
        "edges_added": diff["edges_added"],
        "edges_vanished": diff["edges_vanished"],
        "nodes_vanished_details": vanished_node_details,
        "edges_vanished_details": vanished_edge_details,
        "nodes_added_sample": added_node_details[:40],
        "unresolved_in_current": diff["unresolved_in_current"],
        "coverage_warning": diff["coverage_warning"],
        "audit_rule": "CitationChaser #37: classify item-level added/vanished/unresolved deltas; never equate raw hit counts with global coverage.",
    }


def _collect_retracted_alerts(graph_obj: Dict[str, Any], seed_rids: List[str]) -> List[Dict[str, Any]]:
    nodes = graph_obj["nodes"]
    seed_set = set(seed_rids)
    alerts: List[Dict[str, Any]] = []
    for rid, info in sorted(nodes.items()):
        if not (info.get("is_retracted") or info.get("is_correction")):
            continue
        cites_seeds = [t for t in info.get("out", []) if t in seed_set]
        cited_by_seeds = [s for s in info.get("in", []) if s in seed_set]
        alerts.append(
            {
                "rid": rid,
                "title": info.get("title", ""),
                "doi": info.get("doi", ""),
                "year": info.get("year") or 0,
                "is_retracted": bool(info.get("is_retracted")),
                "is_correction": bool(info.get("is_correction")),
                "alert_severity": "critical" if info.get("is_retracted") else "warning",
                "cites_seeds": cites_seeds,
                "cited_by_seeds": cited_by_seeds,
                "in_degree": len(info.get("in", [])),
                "out_degree": len(info.get("out", [])),
                "sources": info.get("sources", []),
                "guidance": "Do not treat retracted work as valid supporting evidence; retain only as a flagged caution node.",
            }
        )
    return alerts


def _format_hop_chain(hops: List[Dict[str, Any]], nodes: Dict[str, Dict[str, Any]]) -> str:
    if not hops:
        return ""
    parts = [hops[0]["from"]]
    for h in hops:
        arrow = " --[cites]--> " if h["direction"] == "cites" else " <--[cited_by]-- "
        parts.append(f"{arrow}{h['to']}")
    return "".join(parts)


def _build_edge_csv_rows(graph_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes = graph_obj["nodes"]
    rows: List[Dict[str, Any]] = []
    for e in graph_obj["edges"]:
        s_info = nodes.get(e["src"], {})
        d_info = nodes.get(e["dst"], {})
        provs = e.get("provenance_list") or ([e["provenance"]] if e.get("provenance") else [{}])
        p0 = provs[0] if provs else {}
        rows.append(
            {
                "src_rid": e["src"],
                "src_title": s_info.get("title", ""),
                "src_doi": s_info.get("doi", ""),
                "src_year": s_info.get("year") or "",
                "src_retracted": bool(s_info.get("is_retracted")),
                "dst_rid": e["dst"],
                "dst_title": d_info.get("title", ""),
                "dst_doi": d_info.get("doi", ""),
                "dst_year": d_info.get("year") or "",
                "dst_retracted": bool(d_info.get("is_retracted")),
                "relation": e.get("relation", "cites"),
                "provenance_count": len(provs),
                "source": p0.get("source", ""),
                "resolution": p0.get("resolution", ""),
                "file": p0.get("file", ""),
                "pointer": p0.get("pointer", ""),
                "raw_evidence": str(p0.get("raw", ""))[:180],
            }
        )
    return rows


def _render_report_md(
    case_id: str,
    question: str,
    seed_mode: str,
    seed_records: List[Dict[str, Any]],
    graph_obj: Dict[str, Any],
    trace_res: Dict[str, Any],
    retracted_alerts: List[Dict[str, Any]],
    snapshot_diff: Dict[str, Any],
    dedup_summary: Dict[str, Any],
    ambiguous: List[Dict[str, Any]],
) -> str:
    nodes = graph_obj["nodes"]
    lines: List[str] = [
        f"# Citation Tracing & Related Literature Report (`{case_id}`)",
        "",
        "## 1. Research Scope & Finite Snapshot Boundary",
        f"- **Research Question**: {question or 'Not explicitly specified in case.json'}",
        f"- **Seed Selection Mode**: `{seed_mode}` ({len(seed_records)} seed(s))",
        "- **Coverage Boundary**: All citation edges, roles, and snapshot deltas are computed strictly over the supplied offline input snapshot. Absent citation links represent unobserved edges in the local snapshot, not proof of zero global citations.",
        "",
        "## 2. Seed Inventory & Coverage",
        ct.md_table(
            [
                {
                    "seed_rid": s["seed_rid"],
                    "title": s["title"],
                    "doi": s["doi"],
                    "in_deg": len(nodes.get(s["seed_rid"], {}).get("in", [])),
                    "out_deg": len(nodes.get(s["seed_rid"], {}).get("out", [])),
                    "resolution": s["resolution"],
                }
                for s in seed_records
            ],
            ["seed_rid", "title", "doi", "in_deg", "out_deg", "resolution"],
        ),
        "## 3. Graph & Multi-Hop Expansion Summary",
        f"- **Canonical Nodes**: {graph_obj['stats']['n_nodes']} (from {dedup_summary.get('n_records', 0)} raw records; {dedup_summary.get('n_merged_away', 0)} duplicates merged with rollback trace; count conserved: `{dedup_summary.get('source_count_conserved')}`)",
        f"- **Directed Citation Edges**: {graph_obj['stats']['n_edges']} (reconciled canonical endpoints)",
        f"- **Unresolved External References**: {graph_obj['stats']['n_unresolved_refs']} (logged with file pointers; zero fabricated edges)",
        f"- **Backward Hops (`cites`)**: " + ", ".join(f"hop {k}: {len(v)}" for k, v in sorted(trace_res["backward"].items())),
        f"- **Forward Hops (`cited_by`)**: " + ", ".join(f"hop {k}: {len(v)}" for k, v in sorted(trace_res["forward"].items())),
        f"- **Parallel Works (Co-citation / Bibliographic Coupling)**: {len(trace_res['parallel_works_by_coupling_or_cocitation'])}",
        f"- **Structural Role Breakdown**: {json.dumps(trace_res['role_counts'], ensure_ascii=False)}",
        "",
        "## 4. Structural Roles & Key Network Nodes",
    ]

    role_rows = []
    for rid, rinfo in trace_res["roles"].items():
        nd = nodes.get(rid, {})
        role_rows.append(
            {
                "rid": rid,
                "role": rinfo["role"],
                "year": nd.get("year") or "",
                "in_deg": rinfo["in_degree"],
                "out_deg": rinfo["out_degree"],
                "retracted": rinfo["is_retracted"],
                "title": nd.get("title", ""),
                "doi": nd.get("doi", ""),
            }
        )
    role_rows.sort(key=lambda r: (0 if r["role"] == "retracted_caution" else 1 if r["role"] == "seed" else 2, -(r["in_deg"] + r["out_deg"]), r["rid"]))
    lines.append(ct.md_table(role_rows, ["rid", "role", "year", "in_deg", "out_deg", "retracted", "title", "doi"], max_rows=35))

    lines.append("## 5. Shortest Directed Citation Chains (Per-Hop Direction)")
    path_rows = [
        {
            "seed": p["seed"],
            "target": p["target"],
            "hops": p["hop_count"],
            "reachability": p["reachability"],
            "directions": " -> ".join(p["directions"]),
            "target_title": nodes.get(p["target"], {}).get("title", ""),
        }
        for p in trace_res["paths"][:30]
    ]
    lines.append(ct.md_table(path_rows, ["seed", "target", "hops", "reachability", "directions", "target_title"], max_rows=30))

    lines.append("## 6. Parallel Literature Discovery (Co-Citation & Bibliographic Coupling)")
    if trace_res["coupling"]:
        lines.append("### Bibliographic Coupling (Shared Backward References)")
        lines.append(ct.md_table(trace_res["coupling"], ["seed", "peer", "shared_references", "peer_title"], max_rows=20))
    else:
        lines.append("- No bibliographic coupling pairs observed inside the local snapshot.")
    if trace_res["cocitation"]:
        lines.append("### Co-Citation (Shared Forward Citing Works)")
        lines.append(ct.md_table(trace_res["cocitation"], ["seed", "peer", "shared_citers", "peer_title"], max_rows=20))
    else:
        lines.append("- No co-citation pairs observed inside the local snapshot.")

    lines.extend(["", "## 7. Alerts: Zero-Citation Seeds, Retractions & Ambiguous Identity Guard"])
    if trace_res["zero_citation_seeds"]:
        lines.append("### Zero-Citation Seeds (No Local Edges)")
        lines.append(ct.md_table(trace_res["zero_citation_seeds"], ["seed_rid", "title", "in_degree", "out_degree", "note"]))
    else:
        lines.append("- All declared seeds have at least one observed edge in the local snapshot.")

    if retracted_alerts:
        lines.append("### Retracted / Correction Caution Nodes")
        lines.append(ct.md_table(retracted_alerts, ["rid", "alert_severity", "doi", "title", "cites_seeds", "cited_by_seeds"]))
    else:
        lines.append("- No retracted or correction-flagged nodes detected in the local snapshot.")

    lines.append(
        f"- **Distinct Formal DOI False-Merge Guard**: {len(ambiguous)} ambiguous pair(s) kept separate for human arbitration "
        f"(details in `citation_network.json:ambiguous_candidates`)."
    )
    if ambiguous:
        lines.append(ct.md_table(ambiguous[:15], ["rid_a", "rid_b", "doi_a", "doi_b", "reason", "title_similarity"], max_rows=15))

    lines.extend(["", "## 8. CitationChaser #37 Cross-Snapshot Diff Audit"])
    if snapshot_diff.get("previous_snapshot_available"):
        lines.extend(
            [
                f"- **Previous Snapshot**: `{snapshot_diff['previous_snapshot_id']}` (`{snapshot_diff['previous_snapshot_file']}`)",
                f"- **Nodes Persisted**: {snapshot_diff['nodes_persisted']} | **Added**: {snapshot_diff['nodes_added_count']} | **Vanished**: {snapshot_diff['nodes_vanished_count']}",
                f"- **Edges Added**: {snapshot_diff['edges_added_count']} | **Edges Vanished**: {snapshot_diff['edges_vanished_count']}",
                f"- **Unresolved References in Current Snapshot**: {snapshot_diff['unresolved_in_current']}",
                f"- **Audit Warning**: {snapshot_diff['coverage_warning']}",
            ]
        )
        if snapshot_diff.get("nodes_vanished_details"):
            lines.append("### Vanished Nodes")
            lines.append(ct.md_table(snapshot_diff["nodes_vanished_details"], ["rid", "raw_snapshot_id", "doi", "title", "status"]))
        if snapshot_diff.get("edges_vanished_details"):
            lines.append("### Vanished Edges")
            lines.append(ct.md_table(snapshot_diff["edges_vanished_details"], ["src", "dst", "relation", "src_title", "dst_title"]))
    else:
        lines.append(f"- {snapshot_diff['coverage_warning']}")

    return "\n".join(lines).strip() + "\n"


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    *,
    depth: int = 2,
    max_branch: int = 60,
    question_override: str = "",
    no_docs: bool = False,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)

    bundle = caseio.load_case(str(input_dir), question_override=question_override)
    case_id = bundle.case_id or "challenge-60a8202d"
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-60a8202d")

    builder = ct.ResultBuilder(
        bundle,
        str(work_dir),
        str(output_dir),
        "citation-graph-explorer",
        {"depth": depth, "max_branch": max_branch, "no_docs": no_docs},
    )
    budget_guard.tick_phase(budget_file, "intake_and_dedup")
    state = pl.prepare(builder, question=question_override, extract_docs=not no_docs)

    clusters: List[dd.Cluster] = state["clusters"]
    ambiguous: List[Dict[str, Any]] = state["ambiguous_candidates"]
    rollback_log: List[Dict[str, Any]] = state["rollback_log"]
    dedup_summary: Dict[str, Any] = state["dedup"]

    budget_guard.tick_phase(budget_file, "build_and_reconcile_graph")
    clean_ref_sets = _extract_clean_graph_ref_sets(bundle)
    graph_obj = gr.build_graph(clusters, clean_ref_sets)
    alias_map = _build_id_alias_map(graph_obj["nodes"], clusters)
    seed_rids, seed_records, seed_mode = _discover_seeds(bundle, clean_ref_sets, graph_obj, alias_map, state)
    # Rebuild alias map in case external seed nodes were added
    alias_map = _build_id_alias_map(graph_obj["nodes"], clusters)
    graph_obj = _reconcile_graph_endpoints(graph_obj, alias_map)
    builder.log(
        "graph",
        "reconcile_endpoints",
        nodes=graph_obj["stats"]["n_nodes"],
        edges=graph_obj["stats"]["n_edges"],
        seeds=len(seed_rids),
        seed_mode=seed_mode,
    )

    budget_guard.tick_phase(budget_file, "trace_and_snapshot_diff")
    trace_res = gr.trace(graph_obj, seed_rids, depth=depth, max_branch_per_hop=max_branch)

    # Enrich zero_citation_seeds & shortest paths with readable metadata
    nodes = graph_obj["nodes"]
    enriched_zero_seeds = []
    for zs in trace_res["zero_citation_seeds"]:
        nd = nodes.get(zs["seed_rid"], {})
        enriched_zero_seeds.append(
            {
                **zs,
                "doi": nd.get("doi", ""),
                "year": nd.get("year") or 0,
                "version": nd.get("version", "unknown"),
                "recommendation": "No observed citation edges in local snapshot; do not equate missing local edges with zero global impact.",
            }
        )
    trace_res["zero_citation_seeds"] = enriched_zero_seeds

    enriched_paths = []
    for p in trace_res["paths"]:
        enriched_paths.append(
            {
                **p,
                "seed_title": nodes.get(p["seed"], {}).get("title", ""),
                "target_title": nodes.get(p["target"], {}).get("title", ""),
                "target_role": trace_res["roles"].get(p["target"], {}).get("role", "supporting"),
                "chain_summary": _format_hop_chain(p["hops"], nodes),
            }
        )
    trace_res["paths"] = enriched_paths

    retracted_alerts = _collect_retracted_alerts(graph_obj, seed_rids)
    snapshot_diff = _audit_previous_snapshot(bundle, graph_obj, alias_map)
    builder.log(
        "trace",
        "completed",
        related=len(trace_res["related"]),
        parallel=len(trace_res["parallel_works_by_coupling_or_cocitation"]),
        zero_cit_seeds=len(enriched_zero_seeds),
        retracted=len(retracted_alerts),
        prev_snapshot=snapshot_diff["previous_snapshot_available"],
    )

    # Write domain artifacts
    edge_rows = _build_edge_csv_rows(graph_obj)
    edge_csv_fields = [
        "src_rid",
        "src_title",
        "src_doi",
        "src_year",
        "src_retracted",
        "dst_rid",
        "dst_title",
        "dst_doi",
        "dst_year",
        "dst_retracted",
        "relation",
        "provenance_count",
        "source",
        "resolution",
        "file",
        "pointer",
        "raw_evidence",
    ]
    builder.write_csv("edge_provenance_table.csv", edge_rows, fields=edge_csv_fields)

    network_payload = {
        "case_id": case_id,
        "research_question": state["question"],
        "seed_selection_mode": seed_mode,
        "seeds": seed_records,
        "graph_stats": graph_obj["stats"],
        "dedup_summary": dedup_summary,
        "version_links": state["version_links"],
        "ambiguous_candidates": ambiguous,
        "rollback_log": rollback_log,
        "trace": trace_res,
        "zero_citation_seeds": enriched_zero_seeds,
        "retracted_alerts": retracted_alerts,
        "unresolved_references_count": graph_obj["stats"]["n_unresolved_refs"],
        "unresolved_references_sample": graph_obj.get("unresolved_references", [])[:100],
        "nodes": list(nodes.values()),
        "edges": graph_obj["edges"],
    }
    builder.write_json("citation_network.json", network_payload)
    builder.write_json("snapshot_diff.json", snapshot_diff)

    report_md = _render_report_md(
        case_id=case_id,
        question=state["question"],
        seed_mode=seed_mode,
        seed_records=seed_records,
        graph_obj=graph_obj,
        trace_res=trace_res,
        retracted_alerts=retracted_alerts,
        snapshot_diff=snapshot_diff,
        dedup_summary=dedup_summary,
        ambiguous=ambiguous,
    )
    builder.write_markdown("report.md", report_md)

    # Write hash-chained audit_log.json & run_manifest.json
    builder.log("finalize", "write_audit_and_manifest")
    audit_obj = {"entries": builder.audit, "chain_head": builder.prev_hash, "verified": ct._verify_chain(builder.audit)}
    builder.write_json("audit_log.json", audit_obj)
    manifest_obj = {
        "case_id": case_id,
        "files": bundle.inventory(),
        "records_by_file": bundle.record_files,
        "source_load_errors": state["source_load_errors"],
        "pagination_issues": state["pagination_issues"],
        "notes": bundle.notes,
    }
    builder.write_json("run_manifest.json", manifest_obj)

    # Assemble structured status & honest gaps
    status_details = pl.status_block(
        builder,
        state,
        extra={
            "completed": [
                "cross_form_identity_resolution_with_distinct_doi_guard",
                "direction_aware_multihop_citation_trace",
                "cocitation_and_bibliographic_coupling_discovery",
                "node_structural_role_classification",
                "directed_shortest_path_explanation",
                "citationchaser_37_snapshot_diff_audit",
            ],
            "seed_selection_mode": seed_mode,
            "zero_citation_seeds_count": len(enriched_zero_seeds),
            "retracted_alerts_count": len(retracted_alerts),
            "unresolved_references_count": graph_obj["stats"]["n_unresolved_refs"],
            "previous_snapshot_available": snapshot_diff["previous_snapshot_available"],
        },
    )

    gaps: List[Dict[str, Any]] = []
    if graph_obj["stats"]["n_edges"] == 0:
        gaps.append(
            {
                "code": "NO_OBSERVED_CITATION_EDGES",
                "severity": "warning",
                "source": "citation_graph_tracer",
                "message": "Supplied local snapshot contains zero resolvable citation edges; refused to fabricate links.",
            }
        )
    for zs in enriched_zero_seeds:
        gaps.append(
            {
                "code": "ZERO_CITATION_SEED_IN_SNAPSHOT",
                "severity": "info",
                "source": "citation_graph_tracer",
                "message": f"Seed {zs['seed_rid']} has 0 in-degree and 0 out-degree in local snapshot (may be newly posted preprint or unindexed).",
            }
        )
    for err in state["source_load_errors"]:
        gaps.append(
            {
                "code": "SOURCE_LOAD_ERROR",
                "severity": "warning",
                "source": "caseio",
                "message": f"{err['file']}: {err['error_type']} - {err['detail']}",
            }
        )

    warnings_list: List[str] = list(bundle.notes)
    if retracted_alerts:
        warnings_list.append(f"Detected {len(retracted_alerts)} retracted/correction caution node(s) in citation graph.")
    if ambiguous:
        warnings_list.append(
            f"Blocked {len(ambiguous)} ambiguous merge candidate(s) (distinct formal DOIs or fuzzy title matches kept separate)."
        )
    if snapshot_diff.get("nodes_vanished_count", 0) > 0:
        warnings_list.append(
            f"CitationChaser #37 diff: {snapshot_diff['nodes_vanished_count']} node(s) and {snapshot_diff['edges_vanished_count']} edge(s) vanished relative to previous snapshot."
        )

    # Build concise, grounded contract fields for result.json
    traced_node_views = []
    for rid, rinfo in trace_res["roles"].items():
        nd = nodes.get(rid, {})
        traced_node_views.append(
            {
                "rid": rid,
                "title": nd.get("title", ""),
                "year": nd.get("year") or 0,
                "doi": nd.get("doi", ""),
                "openalex": nd.get("openalex", ""),
                "version": nd.get("version", ""),
                "role": rinfo["role"],
                "in_degree": rinfo["in_degree"],
                "out_degree": rinfo["out_degree"],
                "is_retracted": rinfo["is_retracted"],
                "n_merged_versions": nd.get("n_merged_versions", 1),
                "sources": nd.get("sources", []),
            }
        )

    metrics = {
        "raw_input_records": dedup_summary.get("n_records", 0),
        "canonical_unique_clusters": dedup_summary.get("n_unique", 0),
        "duplicates_merged_with_trace": dedup_summary.get("n_merged_away", 0),
        "ambiguous_pairs_kept_separate": len(ambiguous),
        "graph_node_count": graph_obj["stats"]["n_nodes"],
        "graph_edge_count": graph_obj["stats"]["n_edges"],
        "seed_count": len(seed_rids),
        "backward_hop1_count": len(trace_res["backward"].get(1, [])),
        "backward_hop2_count": len(trace_res["backward"].get(2, [])),
        "forward_hop1_count": len(trace_res["forward"].get(1, [])),
        "forward_hop2_count": len(trace_res["forward"].get(2, [])),
        "reachable_related_count": len(trace_res["related"]),
        "parallel_coupled_count": len(trace_res["parallel_works_by_coupling_or_cocitation"]),
        "cocitation_pair_count": len(trace_res["cocitation"]),
        "coupling_pair_count": len(trace_res["coupling"]),
        "directed_shortest_path_count": len(trace_res["paths"]),
        "zero_citation_seed_count": len(enriched_zero_seeds),
        "retracted_alert_count": len(retracted_alerts),
        "unresolved_reference_count": graph_obj["stats"]["n_unresolved_refs"],
        "snapshot_nodes_persisted": snapshot_diff.get("nodes_persisted", 0),
        "snapshot_nodes_added": snapshot_diff.get("nodes_added_count", 0),
        "snapshot_nodes_vanished": snapshot_diff.get("nodes_vanished_count", 0),
        "snapshot_edges_vanished": snapshot_diff.get("edges_vanished_count", 0),
    }
    metric_provenance = {
        "raw_input_records": "citation_network.json:dedup_summary.n_records",
        "canonical_unique_clusters": "citation_network.json:dedup_summary.n_unique",
        "duplicates_merged_with_trace": "citation_network.json:dedup_summary.n_merged_away",
        "ambiguous_pairs_kept_separate": "citation_network.json:ambiguous_candidates",
        "graph_node_count": "citation_network.json:graph_stats.n_nodes",
        "graph_edge_count": "citation_network.json:graph_stats.n_edges",
        "seed_count": "citation_network.json:seeds",
        "backward_hop1_count": "citation_network.json:trace.backward.1",
        "backward_hop2_count": "citation_network.json:trace.backward.2",
        "forward_hop1_count": "citation_network.json:trace.forward.1",
        "forward_hop2_count": "citation_network.json:trace.forward.2",
        "reachable_related_count": "citation_network.json:trace.related",
        "parallel_coupled_count": "citation_network.json:trace.parallel_works_by_coupling_or_cocitation",
        "cocitation_pair_count": "citation_network.json:trace.cocitation",
        "coupling_pair_count": "citation_network.json:trace.coupling",
        "directed_shortest_path_count": "citation_network.json:trace.paths",
        "zero_citation_seed_count": "citation_network.json:zero_citation_seeds",
        "retracted_alert_count": "citation_network.json:retracted_alerts",
        "unresolved_reference_count": "citation_network.json:unresolved_references_count",
        "snapshot_nodes_persisted": "snapshot_diff.json:nodes_persisted",
        "snapshot_nodes_added": "snapshot_diff.json:nodes_added_count",
        "snapshot_nodes_vanished": "snapshot_diff.json:nodes_vanished_count",
        "snapshot_edges_vanished": "snapshot_diff.json:edges_vanished_count",
    }

    artifacts_list = ["result.json"] + [a["path"] for a in builder.artifacts]
    findings = [
        (
            f"Built reconciled directed citation graph with {metrics['graph_node_count']} canonical node(s) and "
            f"{metrics['graph_edge_count']} directed edge(s) from {metrics['raw_input_records']} raw record(s) "
            f"({metrics['duplicates_merged_with_trace']} merged with rollback trace; "
            f"{metrics['ambiguous_pairs_kept_separate']} ambiguous pairs kept separate)."
        ),
        (
            f"Traced {metrics['reachable_related_count']} directly/indirectly connected node(s) and "
            f"{metrics['parallel_coupled_count']} parallel work(s) from {metrics['seed_count']} seed(s) "
            f"(seed_selection_mode={seed_mode}); explained {metrics['directed_shortest_path_count']} per-hop directed path(s)."
        ),
        (
            f"Flagged {metrics['zero_citation_seed_count']} zero-citation seed(s), {metrics['retracted_alert_count']} retracted "
            f"caution alert(s), {metrics['unresolved_reference_count']} unresolved external reference(s), and "
            f"{metrics['snapshot_nodes_vanished']} vanished node(s) across snapshots."
        ),
    ]

    patch: Dict[str, Any] = {
        "skill": "citation-graph-explorer",
        "seed_selection_mode": seed_mode,
        "results": {
            "seeds": seed_records,
            "role_counts": trace_res["role_counts"],
            "backward_hops": {str(k): v for k, v in trace_res["backward"].items()},
            "forward_hops": {str(k): v for k, v in trace_res["forward"].items()},
            "parallel_works": trace_res["parallel_works_by_coupling_or_cocitation"],
            "traced_nodes": traced_node_views,
            "shortest_directed_paths": trace_res["paths"],
        },
        "evidence": {
            "edge_provenance_csv": "edge_provenance_table.csv",
            "citation_network_json": "citation_network.json",
            "snapshot_diff_json": "snapshot_diff.json",
            "cocitation_pairs": trace_res["cocitation"],
            "bibliographic_coupling_pairs": trace_res["coupling"],
            "pruning_log": trace_res["pruning_log"],
            "dedup_summary": dedup_summary,
            "ambiguous_candidates_count": len(ambiguous),
            "rollback_entries_count": len(rollback_log),
            "sources_used": pl.sources_used(state),
            "audit_chain": {"log": "audit_log.json", "entries": len(builder.audit), "verified": audit_obj["verified"]},
        },
        "status_details": status_details,
        "zero_citation_seeds": enriched_zero_seeds,
        "retracted_alerts": retracted_alerts,
        "unresolved_references": {
            "count": graph_obj["stats"]["n_unresolved_refs"],
            "sample": graph_obj.get("unresolved_references", [])[:25],
        },
        "snapshot_diff": {
            "previous_snapshot_available": snapshot_diff["previous_snapshot_available"],
            "previous_snapshot_id": snapshot_diff["previous_snapshot_id"],
            "nodes_persisted": snapshot_diff["nodes_persisted"],
            "nodes_added_count": snapshot_diff.get("nodes_added_count", len(snapshot_diff.get("nodes_added", []))),
            "nodes_vanished": snapshot_diff["nodes_vanished"],
            "edges_added_count": snapshot_diff.get("edges_added_count", len(snapshot_diff.get("edges_added", []))),
            "edges_vanished": snapshot_diff["edges_vanished"],
            "unresolved_in_current": snapshot_diff["unresolved_in_current"],
            "coverage_warning": snapshot_diff["coverage_warning"],
        },
        "artifacts": artifacts_list,
        "metrics": metrics,
        "metric_provenance": metric_provenance,
        "findings": findings,
        "warnings": warnings_list,
        "gaps": gaps,
    }

    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"] or grounding["ungrounded_metrics"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit_res = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=[
            "citation_network.json",
            "edge_provenance_table.csv",
            "snapshot_diff.json",
            "report.md",
            "audit_log.json",
            "run_manifest.json",
        ],
    )
    if not audit_res["passed"]:
        raise RuntimeError(f"final_audit failed: {audit_res['blocking_failures']}")
    return res


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor 60a8202d Direction-Aware Citation Graph Tracer.")
    parser.add_argument("--input", "-i", default="", help="Input case directory (default: /input).")
    parser.add_argument("--output", "-o", default="", help="Output directory (default: /app/output).")
    parser.add_argument("--work", "-w", default="", help="Work directory (default: /app/work).")
    parser.add_argument("--depth", type=int, default=2, help="Max multi-hop depth per direction (default: 2).")
    parser.add_argument("--max-branch", type=int, default=60, help="Max pruned frontier size per hop (default: 60).")
    parser.add_argument("--question", "-q", default="", help="Optional research question override.")
    parser.add_argument("--no-docs", action="store_true", help="Skip PDF/DOCX extraction.")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout JSON summary.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(
        paths["input_dir"],
        paths["output_dir"],
        paths["work_dir"],
        depth=args.depth,
        max_branch=args.max_branch,
        question_override=args.question,
        no_docs=args.no_docs,
    )
    if not args.quiet:
        print(
            json.dumps(
                {
                    "ok": True,
                    "case_id": res["case_id"],
                    "status": res["status"],
                    "seed_selection_mode": res.get("seed_selection_mode"),
                    "metrics": res["metrics"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
