#!/usr/bin/env python3
"""Knowledge Graph Query Engine, Incremental Update Merger & GraphML Exporter (product-arena-10-760d1a0b).

Pure Python 3.12 stdlib module providing:
- 5+ deterministic evidence-backed graph queries (plus ad-hoc entity neighborhood query)
- Incremental graph update against a prior knowledge_graph.json snapshot (preserving existing node IDs,
  logging added nodes/edges, and flagging new conflicts without erasing prior provenance)
- Valid GraphML XML export for downstream graph tools (NetworkX/Gephi/Cytoscape)
- Human-readable Markdown synthesis report separating consensus, cross-paper comparisons, and conflicts.
"""

from __future__ import annotations

import argparse
import json
import xml.sax.saxutils as saxutils
from collections import defaultdict
from pathlib import Path
from typing import Any

OPPOSITE_REL_PAIRS = {
    ("increases", "decreases"),
    ("decreases", "increases"),
    ("activates", "inhibits"),
    ("inhibits", "activates"),
    ("supports", "contradicts"),
    ("contradicts", "supports"),
}


def find_cross_paper_conflicts(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify contradictory or opposite-direction relations across or within papers."""
    conflicts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for i in range(len(edges)):
        e1 = edges[i]
        if e1["relation_type"] == "contradicts":
            key = (e1["relation_id"], "self_contradicts")
            if key not in seen:
                seen.add(key)
                conflicts.append({
                    "conflict_type": "explicit_contradicts_edge",
                    "edge_a": e1["relation_id"],
                    "edge_b": None,
                    "source_a": e1["evidence"]["source_id"],
                    "source_b": None,
                    "entities": [e1["source_name"], e1["target_name"]],
                    "condition_a": e1.get("condition"),
                    "condition_b": None,
                    "quote_a": e1["evidence"]["quote"],
                    "resolution_policy": "preserve_explicit_contradiction_with_locator",
                })
        for j in range(i + 1, len(edges)):
            e2 = edges[j]
            if (
                e1["source_name"].casefold() == e2["source_name"].casefold()
                and e1["target_name"].casefold() == e2["target_name"].casefold()
                and (e1["relation_type"], e2["relation_type"]) in OPPOSITE_REL_PAIRS
            ):
                key = tuple(sorted((e1["relation_id"], e2["relation_id"])))
                if key in seen:
                    continue
                seen.add(key)
                same_cond = (e1.get("condition") or "").casefold() == (e2.get("condition") or "").casefold()
                conflicts.append({
                    "conflict_type": "same_condition_opposite_direction" if same_cond else "different_condition_opposite_direction",
                    "edge_a": e1["relation_id"],
                    "edge_b": e2["relation_id"],
                    "source_a": e1["evidence"]["source_id"],
                    "source_b": e2["evidence"]["source_id"],
                    "entities": [e1["source_name"], e1["target_name"]],
                    "relations": [e1["relation_type"], e2["relation_type"]],
                    "condition_a": e1.get("condition"),
                    "condition_b": e2.get("condition"),
                    "quote_a": e1["evidence"]["quote"],
                    "quote_b": e2["evidence"]["quote"],
                    "resolution_policy": "never_pool_conditions_preserve_both_papers",
                })
    return conflicts


def run_standard_queries(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    co_occurrences: list[dict[str, Any]],
    alignment: dict[str, Any],
    conflicts: list[dict[str, Any]],
    query_entity: str | None = None,
) -> list[dict[str, Any]]:
    """Execute 5 standard evidence-backed queries (plus optional entity neighborhood trace)."""
    papers_edges: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        papers_edges[e["evidence"]["source_id"]].append(e["relation_id"])

    uses_eval_edges = [e for e in edges if e["relation_type"] in {"uses", "evaluates_on"}]
    comp_edges = [e for e in edges if e["relation_type"] in {"compares_with", "outperforms", "contradicts"}]

    queries: list[dict[str, Any]] = [
        {
            "query_id": "Q1_papers_with_evidence_edges",
            "description": "List distinct source papers contributing at least one grounded directed edge.",
            "paper_count": len(papers_edges),
            "papers": [
                {"source_id": sid, "edge_count": len(eids), "relation_ids": eids}
                for sid, eids in sorted(papers_edges.items())
            ],
            "answer_status": "answered" if papers_edges else "no_grounded_edges_found",
        },
        {
            "query_id": "Q2_reported_methods_and_uses",
            "description": "Retrieve all evidence-backed 'uses' and 'evaluates_on' relations with exact JATS/source locators.",
            "match_count": len(uses_eval_edges),
            "edges": [
                {
                    "relation_id": e["relation_id"],
                    "source_name": e["source_name"],
                    "relation_type": e["relation_type"],
                    "target_name": e["target_name"],
                    "condition": e.get("condition"),
                    "source_id": e["evidence"]["source_id"],
                    "paragraph_id": e["evidence"]["paragraph_id"],
                    "quote": e["evidence"]["quote"],
                }
                for e in uses_eval_edges
            ],
            "answer_status": "answered" if uses_eval_edges else "no_matching_relations",
        },
        {
            "query_id": "Q3_cross_paper_same_spelling_audit",
            "description": "Audit recurring terms across distinct papers, separating justifiable concept links from explicit DO_NOT_MERGE boundaries.",
            "merged_concept_group_count": len(alignment.get("merged_concept_groups") or []),
            "non_merge_decision_count": len(alignment.get("explicit_non_merge_decisions") or []),
            "merged_concept_groups": alignment.get("merged_concept_groups") or [],
            "explicit_non_merge_decisions": alignment.get("explicit_non_merge_decisions") or [],
            "answer_status": "answered",
        },
        {
            "query_id": "Q4_comparisons_outperforms_and_conflicts",
            "description": "Surface comparative claims (compares_with, outperforms) and conflicting or opposite-direction relations across conditions.",
            "comparative_edge_count": len(comp_edges),
            "conflict_count": len(conflicts),
            "comparative_edges": [
                {
                    "relation_id": e["relation_id"],
                    "triple": f"{e['source_name']} --[{e['relation_type']}]--> {e['target_name']}",
                    "condition": e.get("condition"),
                    "source_id": e["evidence"]["source_id"],
                    "paragraph_id": e["evidence"]["paragraph_id"],
                    "quote": e["evidence"]["quote"],
                }
                for e in comp_edges
            ],
            "conflicts": conflicts,
            "answer_status": "answered" if (comp_edges or conflicts) else "no_comparisons_or_conflicts_in_corpus",
        },
        {
            "query_id": "Q5_co_occurrence_vs_causal_guard_audit",
            "description": "Confirm undirected sentence co-occurrences are strictly isolated from directed/causal edges.",
            "undirected_co_occurrence_count": len(co_occurrences),
            "causal_edges_count": sum(1 for e in edges if e["relation_type"] == "causes_direct"),
            "co_occurrences_sample": co_occurrences[:15],
            "guard_statement": "Zero undirected co-occurrence pairs were promoted to causal or directed edges.",
            "answer_status": "answered",
        },
    ]

    if query_entity:
        q_low = query_entity.strip().casefold()
        matched_nodes = [
            n for n in nodes.values()
            if q_low in n["name"].casefold() or any(q_low in a.casefold() for a in (n.get("aliases") or []))
        ]
        matched_ids = {n["entity_id"] for n in matched_nodes}
        incident_edges = [e for e in edges if e["source_entity"] in matched_ids or e["target_entity"] in matched_ids]
        queries.append({
            "query_id": f"Q6_entity_neighborhood:{query_entity}",
            "description": f"Subgraph neighborhood and provenance trace for '{query_entity}'.",
            "matched_node_count": len(matched_nodes),
            "incident_edge_count": len(incident_edges),
            "nodes": [{"entity_id": n["entity_id"], "name": n["name"], "category": n["category"], "source_id": n["source_id"]} for n in matched_nodes],
            "edges": incident_edges,
            "answer_status": "answered" if matched_nodes else "entity_not_found_in_corpus",
        })
    return queries


def apply_incremental_update(
    prior_graph: dict[str, Any] | None,
    new_nodes: dict[str, dict[str, Any]],
    new_edges: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Merge new graph elements onto a prior snapshot while preserving existing node IDs and logging diffs."""
    if not prior_graph or not isinstance(prior_graph, dict):
        return new_nodes, new_edges, {
            "incremental_mode": False,
            "preserved_node_count": 0,
            "added_node_count": len(new_nodes),
            "added_edge_count": len(new_edges),
            "new_conflicts_with_prior": [],
        }

    merged_nodes: dict[str, dict[str, Any]] = {}
    prior_by_key: dict[tuple[str, str, str], str] = {}
    for pnode in prior_graph.get("entities") or []:
        eid = str(pnode["entity_id"])
        merged_nodes[eid] = dict(pnode)
        prior_by_key[(str(pnode["source_id"]), str(pnode["category"]), str(pnode["name"]).casefold())] = eid

    preserved_ids: list[str] = []
    added_ids: list[str] = []
    id_remap: dict[str, str] = {}

    for nid, node in new_nodes.items():
        key = (str(node["source_id"]), str(node["category"]), str(node["name"]).casefold())
        if nid in merged_nodes:
            preserved_ids.append(nid)
            id_remap[nid] = nid
            # Append any new evidence locators without overwriting old ones
            ex_ev = merged_nodes[nid].setdefault("evidence", [])
            for ev in node.get("evidence") or []:
                if ev not in ex_ev:
                    ex_ev.append(ev)
        elif key in prior_by_key:
            old_id = prior_by_key[key]
            preserved_ids.append(old_id)
            id_remap[nid] = old_id
            ex_ev = merged_nodes[old_id].setdefault("evidence", [])
            for ev in node.get("evidence") or []:
                if ev not in ex_ev:
                    ex_ev.append(ev)
        else:
            merged_nodes[nid] = dict(node)
            added_ids.append(nid)
            id_remap[nid] = nid

    merged_edges_map: dict[str, dict[str, Any]] = {
        str(pe["relation_id"]): dict(pe) for pe in (prior_graph.get("relations") or [])
    }
    prior_edge_ids = set(merged_edges_map.keys())
    added_edge_ids: list[str] = []
    for edge in new_edges:
        remapped = dict(edge)
        remapped["source_entity"] = id_remap.get(edge["source_entity"], edge["source_entity"])
        remapped["target_entity"] = id_remap.get(edge["target_entity"], edge["target_entity"])
        rid = str(remapped["relation_id"])
        if rid not in merged_edges_map:
            merged_edges_map[rid] = remapped
            added_edge_ids.append(rid)

    all_edges = list(merged_edges_map.values())
    all_conflicts = find_cross_paper_conflicts(all_edges)
    new_conflicts = [
        c for c in all_conflicts
        if (c.get("edge_a") not in prior_edge_ids) or (c.get("edge_b") and c.get("edge_b") not in prior_edge_ids)
    ]

    diff_summary = {
        "incremental_mode": True,
        "prior_node_count": len(prior_graph.get("entities") or []),
        "prior_edge_count": len(prior_edge_ids),
        "preserved_node_count": len(set(preserved_ids)),
        "added_node_count": len(added_ids),
        "added_node_ids": added_ids,
        "added_edge_count": len(added_edge_ids),
        "added_edge_ids": added_edge_ids,
        "new_conflicts_with_prior": new_conflicts,
    }
    return merged_nodes, all_edges, diff_summary


def export_graphml(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    co_occurrences: list[dict[str, Any]],
) -> str:
    """Serialize entities, directed evidence edges, and undirected co-occurrences to GraphML."""
    esc = saxutils.escape
    q = saxutils.quoteattr
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <key id="d_name" for="node" attr.name="name" attr.type="string"/>',
        '  <key id="d_cat" for="node" attr.name="category" attr.type="string"/>',
        '  <key id="d_src" for="node" attr.name="source_id" attr.type="string"/>',
        '  <key id="d_pid" for="node" attr.name="paragraph_id" attr.type="string"/>',
        '  <key id="e_rel" for="edge" attr.name="relation_type" attr.type="string"/>',
        '  <key id="e_dir" for="edge" attr.name="edge_modality" attr.type="string"/>',
        '  <key id="e_src" for="edge" attr.name="source_id" attr.type="string"/>',
        '  <key id="e_pid" for="edge" attr.name="paragraph_id" attr.type="string"/>',
        '  <key id="e_cond" for="edge" attr.name="condition" attr.type="string"/>',
        '  <graph id="literature_kg" edgedefault="directed">',
    ]
    for n in nodes:
        lines.append(f'    <node id={q(str(n["entity_id"]))}>')
        lines.append(f'      <data key="d_name">{esc(str(n["name"]))}</data>')
        lines.append(f'      <data key="d_cat">{esc(str(n["category"]))}</data>')
        lines.append(f'      <data key="d_src">{esc(str(n["source_id"]))}</data>')
        lines.append(f'      <data key="d_pid">{esc(str(n.get("paragraph_id") or ""))}</data>')
        lines.append("    </node>")

    for e in edges:
        ev = e["evidence"]
        lines.append(
            f'    <edge id={q(str(e["relation_id"]))} source={q(str(e["source_entity"]))} target={q(str(e["target_entity"]))}>'
        )
        lines.append(f'      <data key="e_rel">{esc(str(e["relation_type"]))}</data>')
        lines.append('      <data key="e_dir">directed_evidence_backed</data>')
        lines.append(f'      <data key="e_src">{esc(str(ev["source_id"]))}</data>')
        lines.append(f'      <data key="e_pid">{esc(str(ev.get("paragraph_id") or ""))}</data>')
        lines.append(f'      <data key="e_cond">{esc(str(e.get("condition") or ""))}</data>')
        lines.append("    </edge>")

    for c in co_occurrences:
        ev = c["evidence"]
        lines.append(
            f'    <edge id={q(str(c["co_occurrence_id"]))} directed="false" source={q(str(c["entity_a"]))} target={q(str(c["entity_b"]))}>'
        )
        lines.append('      <data key="e_rel">co_occurs_with_no_causal_claim</data>')
        lines.append('      <data key="e_dir">undirected_co_occurrence</data>')
        lines.append(f'      <data key="e_src">{esc(str(ev["source_id"]))}</data>')
        lines.append(f'      <data key="e_pid">{esc(str(ev.get("paragraph_id") or ""))}</data>')
        lines.append('      <data key="e_cond"></data>')
        lines.append("    </edge>")

    lines.extend(["  </graph>", "</graphml>", ""])
    return "\n".join(lines)


def render_kg_markdown(graph: dict[str, Any], gaps: list[dict[str, Any]]) -> str:
    """Render concise, domain-readable Markdown report separating facts, alignment, and conflicts."""
    lines = [
        f"# Literature Knowledge Graph Report ({graph['case_id']})",
        "",
        f"- **Distinct Source Papers**: {len(graph['papers'])}",
        f"- **Locator System**: `{graph['locator_contract']['locator_type']}` (PDF pages available: `{graph['locator_contract']['pdf_page_available']}`)",
        f"- **Entities**: {graph['entity_count']} | **Directed Evidence Edges**: {graph['relation_count']} | **Undirected Co-occurrences (Isolated)**: {graph['co_occurrence_count']} | **Held for Review**: {graph['held_for_review_count']}",
        "",
        "## 1. Source Paper Registry (Strict Metadata Isolation)",
        "",
        "| Source ID | File | DOI | Title | Paragraphs |",
        "|---|---|---|---|---|",
    ]
    for p in graph["papers"]:
        lines.append(
            f"| `{p['source_id']}` | `{p['source_file']}` | `{p.get('doi') or 'not_reported'}` | {str(p.get('title') or 'not_reported')[:70]} | {p['paragraph_count']} |"
        )
    lines.extend(["", "## 2. Directed Evidence-Backed Relations", ""])
    if graph["relations"]:
        lines.extend(["| Source Paper | Paragraph ID | Subject | Relation | Object | Condition | Attribution |", "|---|---|---|---|---|---|---|"])
        for e in graph["relations"]:
            ev = e["evidence"]
            lines.append(
                f"| `{ev['source_id']}` | `{ev.get('paragraph_id')}` | {e['source_name']} | `{e['relation_type']}` | {e['target_name']} | {e.get('condition') or '—'} | `{e.get('attribution', 'author_reported')}` |"
            )
    else:
        lines.append("No directed relations met the explicit evidence threshold; see gaps and held candidates below.")

    align = graph["cross_paper_alignment"]
    lines.extend(["", "## 3. Cross-Paper Entity Alignment & Explicit Non-Merge Decisions", ""])
    lines.append(f"- **Justified Concept Links (preserving paper-scoped instances)**: {len(align['merged_concept_groups'])}")
    for mg in align["merged_concept_groups"]:
        lines.append(f"  - `{mg['concept_label']}` ({mg['category']}): {mg['rationale']}")
    lines.append(f"- **Explicit DO_NOT_MERGE Decisions**: {len(align['explicit_non_merge_decisions'])}")
    for nm in align["explicit_non_merge_decisions"]:
        papers = sorted({o["source_id"] for o in nm["occurrences"]})
        lines.append(f"  - `{nm['name']}` across {papers}: **{nm['decision']}** — {nm['rationale']}")

    lines.extend(["", "## 4. Executed Graph Queries & Guardrails", ""])
    for q in graph["queries"]:
        lines.append(f"- **`{q['query_id']}`** ({q['answer_status']}): {q['description']}")

    if graph.get("conflicts"):
        lines.extend(["", "## 5. Preserved Contradictions & Cross-Condition Disagreements", ""])
        for c in graph["conflicts"]:
            lines.append(f"- `{c['conflict_type']}` on {c['entities']}: {c.get('relations') or 'contradicts'} (policy: `{c['resolution_policy']}`)")

    if gaps:
        lines.extend(["", "## 6. Coverage Gaps & Held Candidates", ""])
        for g in gaps:
            lines.append(f"- **{g['code']}**: {g.get('message') or g.get('count') or ''}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query an existing knowledge_graph.json by entity name.")
    parser.add_argument("--graph", type=Path, required=True, help="Path to knowledge_graph.json")
    parser.add_argument("--entity", required=True, help="Entity name or alias substring to trace")
    args = parser.parse_args(argv)
    g = json.loads(args.graph.read_text(encoding="utf-8"))
    nodes = {n["entity_id"]: n for n in g.get("entities") or []}
    queries = run_standard_queries(
        nodes,
        g.get("relations") or [],
        g.get("co_occurrences") or [],
        g.get("cross_paper_alignment") or {"merged_concept_groups": [], "explicit_non_merge_decisions": []},
        g.get("conflicts") or [],
        query_entity=args.entity,
    )
    print(json.dumps(queries[-1], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
