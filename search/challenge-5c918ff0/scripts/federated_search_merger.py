#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline Multi-Database Federated Search Merger & Provenance Auditor (challenge-5c918ff0).

Pure Python 3.12 stdlib pipeline built on `search_core` + `common_core` (M1-M6):
  1. Multi-source query syntax adaptation matrix across Crossref, OpenAlex,
     EuropePMC, PubMed, and arXiv (never copying one naive string verbatim).
  2. Cross-database field semantic standardization with explicit Missing vs Unknown
     distinction and per-source field coverage matrix (`field_coverage_by_source`),
     automatically flagging systematically missing fields per database.
  3. 4-tier identity resolution with HARD anti-false-merge guard when two records
     carry distinct formal (non-preprint) DOIs despite identical titles ->
     routed to `ambiguous_same_title_distinct_doi` & `ambiguous_arbitration_queue.json`.
  4. Preprint <-> Version-of-Record (VoR) version chaining (`preprint_to_vor_chains`)
     and deterministic field conflict resolution (`version_rank > source_authority >
     field_richness`) preserving all raw values and source pointers.
  5. Single-database exclusive literature counts (`exclusive_works_by_source`),
     full N x N cross-database overlap matrix (`source_overlap_matrix`),
     `source_count_conserved == True` invariant, and ASReview #328 `rollback_log`.
  6. Degraded / unavailable / rate-limited source reporting (`degraded_sources`,
     e.g., PubMed HTTP 429) without silent dropping or record fabrication.
  7. Complete deliverable suite (`federated_merged.csv`, `source_coverage_matrix.json`,
     `dedup_rollback_log.json`, `ambiguous_arbitration_queue.json`, `report.md`,
     `audit_log.json`, `run_manifest.json`, `result.json`) verified via M5 & M6.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import budget_guard  # noqa: E402
import final_audit  # noqa: E402
import gap_gate  # noqa: E402
import preflight_probe  # noqa: E402
import result_guard  # noqa: E402
from search_core import (  # noqa: E402
    caseio,
    contract as ct,
    dedup as dd,
    pipeline as pl,
    query as qm,
    scholarmeta as sm,
)

SKILL_NAME = "multi-db-federated-search"
TASK_ID = "challenge-5c918ff0"

CORE_COVERAGE_FIELDS = ("title", "abstract", "authors", "year", "doi", "venue", "citations_count")

KNOWN_DB_TOKENS: Dict[str, str] = {
    "crossref": "crossref",
    "openalex": "openalex",
    "europepmc": "europepmc",
    "europe pmc": "europepmc",
    "pubmed": "pubmed",
    "arxiv": "arxiv",
    "semantic scholar": "semantic_scholar",
}

FIELD_SEMANTIC_NOTES: Dict[str, Dict[str, str]] = {
    "crossref": {
        "abstract": "Publisher-dependent; many journals omit JATS abstracts in Crossref REST deposits (structural metadata gap).",
        "citations_count": "Derived from Crossref is-referenced-by-count (open citation deposit subset).",
        "year": "Prefers published-print > issued > published-online.",
    },
    "openalex": {
        "abstract": "Reconstructed from abstract_inverted_index; absent when publisher restricts abstract redistribution.",
        "citations_count": "OpenAlex cited_by_count across OpenAlex graph.",
        "year": "publication_year (may differ from print vs online date by +/-1 year).",
    },
    "europepmc": {
        "abstract": "abstractText; omitted when EuropePMC resultType=lite or publisher embargo applies.",
        "citations_count": "citedByCount within EuropePMC citation network.",
        "venue": "journalInfo.journal.title or source repository label.",
    },
    "arxiv": {
        "doi": "Preprints typically lack publisher formal DOI (only optional 10.48550/arxiv.* or journal_ref DOI).",
        "venue": "Preprint server ('arXiv') unless journal_ref is deposited.",
        "citations_count": "Not provided by arXiv Atom XML API (structurally unavailable, not zero citations).",
    },
    "pubmed": {
        "citations_count": "Not provided natively by PubMed E-utilities summary/XML.",
        "abstract": "Structured MedlineCitation AbstractText; absent for letters/editorials.",
    },
}


def _infer_target_databases(bundle: caseio.CaseBundle, raw_records: List[sm.Record]) -> List[str]:
    found: List[str] = []
    for k in ("expected_databases", "databases", "sources"):
        v = bundle.case.get(k) if isinstance(bundle.case, dict) else None
        if isinstance(v, list):
            for x in v:
                s = str(x).strip().lower().replace(" ", "")
                if s and s not in found:
                    found.append(s)
    q_low = (bundle.question or "").lower()
    for token, canon in KNOWN_DB_TOKENS.items():
        if token in q_low and canon not in found:
            found.append(canon)
    for r in raw_records:
        s = (r.source or "unknown").lower()
        if s not in found:
            found.append(s)
    for pi in getattr(bundle, "pagination_issues", []) or []:
        s = str(pi.get("source") or "").lower()
        if s and s not in found:
            found.append(s)
    return found or ["crossref", "openalex", "europepmc", "arxiv", "pubmed"]


def _is_field_filled(rec: sm.Record, field_name: str) -> bool:
    val = getattr(rec, field_name, None)
    if field_name == "citations_count":
        return val is not None
    if field_name == "year":
        return bool(val and int(val) > 0)
    if isinstance(val, list):
        return len(val) > 0
    return bool(str(val or "").strip())


def compute_field_coverage_by_source(
    raw_records: List[sm.Record],
    degraded_sources: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Compute per-source field fill rates and detect systematically missing fields."""
    by_src: Dict[str, List[sm.Record]] = defaultdict(list)
    for r in raw_records:
        by_src[(r.source or "unknown").lower()].append(r)

    coverage: Dict[str, Any] = {}
    systematic_missing: List[Dict[str, Any]] = []

    for src in sorted(by_src.keys()):
        recs = by_src[src]
        n = len(recs)
        fields_info: Dict[str, Any] = {}
        sys_miss_for_src: List[str] = []
        for fname in CORE_COVERAGE_FIELDS:
            filled = sum(1 for r in recs if _is_field_filled(r, fname))
            missing = n - filled
            rate = round(filled / max(1, n), 4)
            is_sys_missing = rate <= 0.15
            if is_sys_missing:
                sys_miss_for_src.append(fname)
                systematic_missing.append(
                    {
                        "source": src,
                        "field": fname,
                        "filled": filled,
                        "total": n,
                        "fill_rate": rate,
                        "classification": "structurally_absent" if filled == 0 else "systematically_sparse",
                        "semantic_note": FIELD_SEMANTIC_NOTES.get(src, {}).get(
                            fname,
                            "Field absent or sparsely populated in this database snapshot; distinct from zero/false.",
                        ),
                    }
                )
            fields_info[fname] = {
                "filled": filled,
                "missing": missing,
                "fill_rate": rate,
                "systematically_missing": is_sys_missing,
                "semantic_note": FIELD_SEMANTIC_NOTES.get(src, {}).get(fname, ""),
            }
        coverage[src] = {
            "status": "available",
            "raw_record_count": n,
            "fields": fields_info,
            "systematic_missing_fields": sys_miss_for_src,
        }

    for deg in degraded_sources:
        dsrc = str(deg.get("source") or "unknown").lower()
        if dsrc not in coverage:
            coverage[dsrc] = {
                "status": deg.get("status", "unavailable"),
                "raw_record_count": 0,
                "fields": {
                    fname: {
                        "filled": 0,
                        "missing": 0,
                        "fill_rate": None,
                        "systematically_missing": None,
                        "semantic_note": f"Database unavailable ({deg.get('error', 'snapshot failed')}); coverage unknown, NOT zero.",
                    }
                    for fname in CORE_COVERAGE_FIELDS
                },
                "systematic_missing_fields": ["ALL_UNAVAILABLE_SOURCE"],
            }

    return coverage, systematic_missing


def compute_exclusivity_and_overlap(
    clusters: List[dd.Cluster],
    all_sources: List[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Quantify single-database exclusive canonical works and N x N pairwise overlap."""
    exclusive_counts: Counter = Counter()
    exclusive_rids: Dict[str, List[str]] = defaultdict(list)
    multi_source_count = 0
    src_to_rids: Dict[str, set] = {s: set() for s in all_sources}

    for c in clusters:
        srcs = sorted({(m.source or "unknown").lower() for m in c.members})
        rid = c.canonical.rid
        for s in srcs:
            src_to_rids.setdefault(s, set()).add(rid)
        if len(srcs) == 1:
            exclusive_counts[srcs[0]] += 1
            exclusive_rids[srcs[0]].append(rid)
        elif len(srcs) > 1:
            multi_source_count += 1

    active_sources = sorted(src_to_rids.keys())
    matrix: Dict[str, Dict[str, int]] = {}
    pairwise_details: List[Dict[str, Any]] = []
    for i, s1 in enumerate(active_sources):
        matrix[s1] = {}
        for j, s2 in enumerate(active_sources):
            inter = len(src_to_rids[s1] & src_to_rids[s2])
            matrix[s1][s2] = inter
            if j > i:
                union_n = len(src_to_rids[s1] | src_to_rids[s2])
                pairwise_details.append(
                    {
                        "source_a": s1,
                        "source_b": s2,
                        "shared_canonical_works": inter,
                        "jaccard_overlap": round(inter / max(1, union_n), 4),
                    }
                )

    exclusive_summary = {
        "counts_by_source": {s: exclusive_counts.get(s, 0) for s in active_sources},
        "total_exclusive_works": sum(exclusive_counts.values()),
        "multi_source_overlap_works": multi_source_count,
        "sample_exclusive_rids_by_source": {s: exclusive_rids.get(s, [])[:10] for s in active_sources},
    }
    overlap_summary = {
        "sources": active_sources,
        "canonical_coverage_by_source": {s: len(src_to_rids[s]) for s in active_sources},
        "matrix": matrix,
        "pairwise": pairwise_details,
    }
    return exclusive_summary, overlap_summary


def collect_degraded_sources(
    bundle: caseio.CaseBundle,
    target_dbs: List[str],
    raw_records: List[sm.Record],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Identify unavailable/rate-limited/broken sources vs valid empty JSON arrays."""
    degraded: List[Dict[str, Any]] = []
    seen_keys: set = set()
    valid_empty: List[Dict[str, Any]] = []

    for fe in bundle.files:
        if "valid empty json array" in (fe.note or "").lower():
            valid_empty.append(
                {
                    "file": fe.rel,
                    "source": fe.source_kind or "json_array",
                    "record_count": 0,
                    "note": "Valid empty JSON array [] (not a transport or parsing error).",
                }
            )

    for pi in getattr(bundle, "pagination_issues", []) or []:
        src = str(pi.get("source") or pi.get("file") or "unknown").lower()
        key = (src, pi.get("file", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        degraded.append(
            {
                "source": src,
                "status": pi.get("status", "unavailable"),
                "error": pi.get("error", "source reported error"),
                "file": pi.get("file", ""),
                "cursor": pi.get("cursor"),
                "impact_on_coverage": f"Records exclusively indexed in '{src}' are missing from this federated snapshot.",
                "mitigation": "Do not fabricate missing records; disclose coverage gap explicitly and re-query when quota/service recovers.",
            }
        )

    for err in getattr(bundle, "source_load_errors", []) or []:
        key = ("load_error", err.get("file", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        degraded.append(
            {
                "source": err.get("file", "unknown"),
                "status": "parse_error",
                "error": f"{err.get('error_type')}: {err.get('detail', '')[:160]}",
                "file": err.get("file", ""),
                "cursor": None,
                "impact_on_coverage": "Malformed or unsupported snapshot file could not be parsed; omitted records are unknown.",
                "mitigation": "Repair upstream export before claiming complete database recall.",
            }
        )

    present_srcs = {(r.source or "unknown").lower() for r in raw_records}
    degraded_srcs = {d["source"] for d in degraded}
    for db in target_dbs:
        if db not in present_srcs and db not in degraded_srcs:
            degraded.append(
                {
                    "source": db,
                    "status": "missing_from_snapshot",
                    "error": f"Database '{db}' referenced in task/config but absent from local snapshot files.",
                    "file": "",
                    "cursor": None,
                    "impact_on_coverage": f"No local records available for '{db}'; coverage cannot be verified.",
                    "mitigation": "Disclose missing source explicitly without substituting other databases.",
                }
            )
    return degraded, valid_empty


def build_preprint_vor_chains(clusters: List[dd.Cluster], cross_links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build structured preprint <-> Version-of-Record upgrade chains."""
    chains: List[Dict[str, Any]] = []
    for c in clusters:
        vg = c.version_group()
        if not vg.get("preprint_and_published"):
            continue
        pre_members = [
            m for m in c.members
            if m.version == "preprint" or dd.is_preprint_doi(m.doi) or (m.source.lower() == "arxiv")
        ]
        pub_members = [m for m in c.members if m not in pre_members]
        canon = c.canonical
        chains.append(
            {
                "link_type": "merged_preprint_to_vor",
                "canonical_rid": canon.rid,
                "canonical_title": canon.title,
                "canonical_doi": canon.doi,
                "canonical_venue": canon.venue,
                "canonical_year": canon.year,
                "canonical_source": canon.source,
                "preprint_members": [
                    {
                        "rid": m.rid,
                        "source": m.source,
                        "file": m.path,
                        "pointer": m.pointer,
                        "doi": m.doi,
                        "arxiv_id": m.arxiv_id,
                        "year": m.year,
                        "match_tier": dd.same_work(canon, m)[1],
                        "match_score": round(dd.same_work(canon, m)[2], 3),
                    }
                    for m in pre_members
                ],
                "published_members": [
                    {
                        "rid": m.rid,
                        "source": m.source,
                        "file": m.path,
                        "pointer": m.pointer,
                        "doi": m.doi,
                        "alt_dois": getattr(m, "alt_dois", []),
                        "venue": m.venue,
                        "year": m.year,
                    }
                    for m in pub_members
                ],
            }
        )
    for lnk in cross_links:
        chains.append({"link_type": "cross_cluster_candidate", **lnk})
    return chains


def build_markdown_report(
    case_id: str,
    question: str,
    per_source_queries: Dict[str, Any],
    dedup_rep: Dict[str, Any],
    field_cov: Dict[str, Any],
    sys_missing: List[Dict[str, Any]],
    excl_summary: Dict[str, Any],
    overlap_summary: Dict[str, Any],
    amb_same_title: List[Dict[str, Any]],
    preprint_chains: List[Dict[str, Any]],
    conflicts_resolved: List[Dict[str, Any]],
    degraded_sources: List[Dict[str, Any]],
    canonical_rows: List[Dict[str, Any]],
) -> str:
    lines: List[str] = [
        f"# Multi-Database Federated Search Audit Report (`{case_id}`)",
        "",
        "## 1. Scope & Conservation Invariant",
        f"- **Research Question**: {question}",
        f"- **Raw Records Loaded**: {dedup_rep['n_records']} -> **Unique Canonical Works**: {dedup_rep['n_unique']} (duplicates merged with trace: {dedup_rep['n_merged_away']})",
        f"- **Source Count Conserved (`raw == unique + merged_away`)**: `{dedup_rep['source_count_conserved']}`",
        f"- **Same-Title Distinct-Formal-DOI Pairs Kept Separate**: {len(amb_same_title)}",
        f"- **Preprint-to-VoR Upgrade Chains**: {len(preprint_chains)}",
        f"- **Deterministic Field Conflicts Resolved (Raw Values Preserved)**: {len(conflicts_resolved)}",
        f"- **Degraded / Unavailable Sources Disclosed**: {len(degraded_sources)}",
        "- **Offline Boundary**: Local authorized multi-database snapshot only; no live remote queries or fabricated records.",
        "",
        "## 2. Multi-Source Query Syntax Adaptation Matrix",
    ]
    q_rows = []
    for rnd in per_source_queries.get("rounds") or []:
        for src, q_str in (rnd.get("queries") or {}).items():
            q_rows.append({"round": rnd.get("round"), "label": rnd.get("label"), "database": src, "adapted_query": q_str})
    lines.append(ct.md_table(q_rows, ["round", "label", "database", "adapted_query"], widths={"adapted_query": 95}))

    lines += ["## 3. Degraded / Unavailable Sources (Never Silently Dropped)"]
    if degraded_sources:
        lines.append(ct.md_table(degraded_sources, ["source", "status", "file", "error", "impact_on_coverage"], widths={"error": 55, "impact_on_coverage": 60}))
    else:
        lines.append("_(No degraded or unavailable sources detected.)_\n")

    lines += ["## 4. Per-Source Field Coverage Matrix & Systematic Gaps"]
    cov_rows = []
    for src, info in field_cov.items():
        row: Dict[str, Any] = {"source": src, "status": info["status"], "raw_n": info["raw_record_count"]}
        for f in CORE_COVERAGE_FIELDS:
            fr = info["fields"][f]["fill_rate"]
            row[f] = "N/A" if fr is None else f"{fr * 100:.0f}%"
        cov_rows.append(row)
    lines.append(ct.md_table(cov_rows, ["source", "status", "raw_n", *CORE_COVERAGE_FIELDS], widths={"source": 14}))
    if sys_missing:
        lines.append("### Systematically Missing Fields (Missing vs Unknown Semantics)")
        lines.append(ct.md_table(sys_missing, ["source", "field", "filled", "total", "fill_rate", "classification", "semantic_note"], widths={"semantic_note": 75}))

    lines += [
        "## 5. Single-Source Exclusive Works & Cross-Database Overlap",
        f"- **Total Single-Database Exclusive Works**: {excl_summary['total_exclusive_works']} | **Multi-Database Overlap Works**: {excl_summary['multi_source_overlap_works']}",
        f"- **Exclusive Works by Source**: {json.dumps(excl_summary['counts_by_source'], ensure_ascii=False)}",
        "",
    ]
    if overlap_summary.get("pairwise"):
        lines.append(ct.md_table(overlap_summary["pairwise"], ["source_a", "source_b", "shared_canonical_works", "jaccard_overlap"]))

    lines += ["## 6. Hard Anti-False-Merge Queue (Same Title, Distinct Formal DOIs)"]
    if amb_same_title:
        lines.append(ct.md_table(amb_same_title, ["rid_a", "rid_b", "doi_a", "doi_b", "title_similarity", "action"], widths={"rid_a": 30, "rid_b": 30, "doi_a": 28, "doi_b": 28}))
    else:
        lines.append("_(No same-title distinct-formal-DOI conflicts detected.)_\n")

    lines += ["## 7. Preprint-to-VoR Version Upgrade Chains"]
    if preprint_chains:
        p_rows = []
        for ch in preprint_chains:
            if ch.get("link_type") == "merged_preprint_to_vor":
                pres = ", ".join(f"{m['source']}:{m['doi'] or m['arxiv_id'] or m['rid']}({m['year']})" for m in ch.get("preprint_members", []))
                p_rows.append(
                    {
                        "canonical_doi": ch.get("canonical_doi"),
                        "canonical_venue": ch.get("canonical_venue"),
                        "canonical_year": ch.get("canonical_year"),
                        "preprint_sources": pres,
                        "title": ch.get("canonical_title"),
                    }
                )
        lines.append(ct.md_table(p_rows, ["canonical_doi", "canonical_venue", "canonical_year", "preprint_sources", "title"], widths={"title": 52, "preprint_sources": 48}))

    lines += ["## 8. Deterministic Field Conflicts Resolved (`version_rank > source_authority > field_richness`)"]
    if conflicts_resolved:
        lines.append(ct.md_table(conflicts_resolved, ["canonical_rid", "field", "chosen_value", "chosen_source", "raw_values"], max_rows=25, widths={"canonical_rid": 30, "raw_values": 60}))
    else:
        lines.append("_(No cross-source field conflicts detected.)_\n")

    lines += ["## 9. Sample Canonical Merged Records (Top 20)"]
    lines.append(ct.md_table(canonical_rows, ["canonical_rid", "year", "version", "found_in_sources", "doi", "venue", "title"], max_rows=20, widths={"canonical_rid": 28, "title": 46, "venue": 24, "doi": 26}))
    return "\n".join(lines) + "\n"


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    question_override: str = "",
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # M1 & M3: Preflight + Budget initialization
    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    budget_guard.tick_phase(budget_file, "federated_intake")

    bundle = caseio.load_case(str(input_dir), question_override=question_override)
    case_id = bundle.case_id or "case-unknown"

    # M2: Early skeleton result.json
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id=TASK_ID)

    builder = ct.ResultBuilder(
        bundle,
        work_dir=str(work_dir),
        out_dir=str(output_dir),
        skill_name=SKILL_NAME,
        pipeline={"engine": "federated_search_merger.py", "core": "search_core+common_core", "version": "2.0"},
    )
    S = pl.prepare(builder, question=question_override, extract_docs=False)

    raw_records: List[sm.Record] = S["records"]
    clusters: List[dd.Cluster] = S["clusters"]
    ambiguous: List[Dict[str, Any]] = S["ambiguous_candidates"]
    rollback_log: List[Dict[str, Any]] = S["rollback_log"]
    dedup_rep: Dict[str, Any] = S["dedup"]

    target_dbs = _infer_target_databases(bundle, raw_records)
    degraded_sources, valid_empty_sources = collect_degraded_sources(bundle, target_dbs, raw_records)

    # Re-build per-source adapted query matrix over all target databases
    all_texts = ([S["question"]] if S.get("question") else []) + [r.text_for_search for r in raw_records]
    abbrevs, _ = qm.mine_abbreviations(all_texts)
    per_source_queries = qm.build_queries(S["plan"], abbrevs, S["co_terms"], target_dbs)
    round1_q_map = (per_source_queries.get("rounds") or [{}])[0].get("queries", {})
    round2_q_map = (per_source_queries.get("rounds") or [{}, {}])[-1].get("queries", {})

    budget_guard.tick_phase(budget_file, "coverage_and_conflict_audit")

    # Field coverage matrix & systematic missing fields
    field_cov, sys_missing = compute_field_coverage_by_source(raw_records, degraded_sources)

    # Single-source exclusive works & N x N overlap matrix
    excl_summary, overlap_summary = compute_exclusivity_and_overlap(clusters, target_dbs)

    # Separate hard same-title distinct-formal-DOI queue from other ambiguous candidates
    amb_same_title_distinct_doi: List[Dict[str, Any]] = []
    other_ambiguous: List[Dict[str, Any]] = []
    rec_by_rid = {r.rid: r for r in raw_records}
    for amb in ambiguous:
        ra = rec_by_rid.get(amb["rid_a"])
        rb = rec_by_rid.get(amb["rid_b"])
        enriched = {
            **amb,
            "authors_a": ra.authors[:6] if ra else [],
            "authors_b": rb.authors[:6] if rb else [],
            "venue_a": ra.venue if ra else "",
            "venue_b": rb.venue if rb else "",
            "year_a": ra.year if ra else None,
            "year_b": rb.year if rb else None,
            "recommended_action": "keep_separate_unless_erratum_or_parallel_edition_confirmed",
        }
        if amb.get("reason") == "ambiguous:distinct_formal_dois":
            amb_same_title_distinct_doi.append(enriched)
        else:
            other_ambiguous.append(enriched)

    # Preprint -> VoR version upgrade chains
    preprint_chains = build_preprint_vor_chains(clusters, S.get("version_links") or [])

    # Deterministic field conflicts resolved & canonical records with full 6-step lineage
    field_conflicts_resolved: List[Dict[str, Any]] = []
    canonical_records: List[Dict[str, Any]] = []
    csv_rows: List[Dict[str, Any]] = []

    for c in clusters:
        merged = c.merged()
        canon = c.canonical
        srcs = sorted({(m.source or "unknown").lower() for m in c.members})
        is_excl = len(srcs) == 1
        alt_dois = [d for d in (merged.get("all_ids", {}).get("alt_doi") or []) if d != merged.get("doi")]
        for d in merged.get("all_ids", {}).get("doi") or []:
            if d and d != merged.get("doi") and d not in alt_dois:
                alt_dois.append(d)

        for cf in c.conflicts:
            field_conflicts_resolved.append(
                {
                    "canonical_rid": canon.rid,
                    "title": (merged.get("title") or "")[:120],
                    "field": cf["field"],
                    "raw_values": cf["values"],
                    "chosen_value": cf["chosen_value"],
                    "chosen_source": cf["chosen_source"],
                    "resolution_rule": cf["resolution_rule"],
                }
            )

        lineage_chain = {
            "libraries": srcs,
            "queries_adapted": {s: {"round1": round1_q_map.get(s, ""), "round2": round2_q_map.get(s, "")} for s in srcs},
            "raw_responses": [
                {
                    "rid": m.rid,
                    "source": (m.source or "unknown").lower(),
                    "file": m.path,
                    "pointer": m.pointer,
                    "role": "canonical" if m is canon else "duplicate_ignored_with_trace",
                    "version": m.version,
                    "doi": m.doi,
                    "arxiv_id": m.arxiv_id,
                    "year": m.year or None,
                }
                for m in c.members
            ],
            "field_mapping_provenance": merged.get("field_provenance", {}),
            "dedup_match_tiers": merged.get("match_tiers", []),
            "conflicts_resolved": c.conflicts,
        }

        rec_out = {
            "canonical_rid": canon.rid,
            "title": merged.get("title", ""),
            "authors": (merged.get("authors") or [])[:12],
            "year": merged.get("year") or None,
            "venue": merged.get("venue", ""),
            "doi": merged.get("doi", ""),
            "alt_dois": alt_dois,
            "arxiv_id": canon.arxiv_id or (merged.get("all_ids", {}).get("arxiv") or [""])[0],
            "pmid": canon.pmid or (merged.get("all_ids", {}).get("pmid") or [""])[0],
            "version": canon.version,
            "citations_count": merged.get("citations_count"),
            "is_retracted": merged.get("is_retracted", False),
            "found_in_sources": srcs,
            "n_merged_records": len(c.members),
            "is_source_exclusive": is_excl,
            "exclusive_source": srcs[0] if is_excl else "",
            "version_group": merged.get("version_group", {}),
            "field_provenance": merged.get("field_provenance", {}),
            "conflicts": c.conflicts,
            "lineage": lineage_chain,
        }
        canonical_records.append(rec_out)

        prov_summary = "; ".join(
            f"{k}:{(v[0] if v else 'missing')}" for k, v in (merged.get("field_provenance") or {}).items()
        )
        csv_rows.append(
            {
                "canonical_rid": canon.rid,
                "title": rec_out["title"],
                "authors": "; ".join(rec_out["authors"]),
                "year": rec_out["year"] or "",
                "venue": rec_out["venue"],
                "doi": rec_out["doi"],
                "alt_dois": "; ".join(alt_dois),
                "arxiv_id": rec_out["arxiv_id"],
                "pmid": rec_out["pmid"],
                "version": rec_out["version"],
                "citations_count": "" if rec_out["citations_count"] is None else rec_out["citations_count"],
                "found_in_sources": "; ".join(srcs),
                "n_merged_records": len(c.members),
                "is_source_exclusive": is_excl,
                "exclusive_source": rec_out["exclusive_source"],
                "conflict_count": len(c.conflicts),
                "field_provenance_summary": prov_summary,
                "source_locators": "; ".join(sorted({f"{m.path}{'#' + m.pointer if m.pointer else ''}" for m in c.members})),
            }
        )

    # Strong conservation assertion
    conserved_ok = bool(dedup_rep["source_count_conserved"]) and (
        sum(r["n_merged_records"] for r in canonical_records) == len(raw_records)
    )
    if not conserved_ok:
        raise RuntimeError(
            f"Source count conservation violated: raw={len(raw_records)}, "
            f"canonical={len(canonical_records)}, merged={dedup_rep['n_merged_away']}"
        )

    # Write companion deliverables
    builder.write_csv("federated_merged.csv", csv_rows)
    builder.write_json(
        "source_coverage_matrix.json",
        {
            "case_id": case_id,
            "target_databases": target_dbs,
            "source_count_conserved": conserved_ok,
            "per_source_queries": per_source_queries,
            "field_coverage_by_source": field_cov,
            "systematic_missing_fields": sys_missing,
            "exclusive_works_by_source": excl_summary,
            "source_overlap_matrix": overlap_summary,
            "degraded_sources": degraded_sources,
            "valid_empty_sources": valid_empty_sources,
        },
    )
    builder.write_json(
        "dedup_rollback_log.json",
        {
            "case_id": case_id,
            "policy": "ASReview #328 reversible deduplication: duplicates are never silently deleted.",
            "source_count_conserved": conserved_ok,
            "summary": dedup_rep,
            "rollback_entries": rollback_log,
        },
    )
    builder.write_json(
        "ambiguous_arbitration_queue.json",
        {
            "case_id": case_id,
            "arbitration_policy": "Hard anti-false-merge guard: records with identical/similar titles but distinct formal (non-preprint) DOIs are never auto-merged.",
            "n_same_title_distinct_doi": len(amb_same_title_distinct_doi),
            "n_other_ambiguous": len(other_ambiguous),
            "ambiguous_same_title_distinct_doi": amb_same_title_distinct_doi,
            "other_ambiguous_candidates": other_ambiguous,
        },
    )

    report_md = build_markdown_report(
        case_id=case_id,
        question=S["question"],
        per_source_queries=per_source_queries,
        dedup_rep=dedup_rep,
        field_cov=field_cov,
        sys_missing=sys_missing,
        excl_summary=excl_summary,
        overlap_summary=overlap_summary,
        amb_same_title=amb_same_title_distinct_doi,
        preprint_chains=preprint_chains,
        conflicts_resolved=field_conflicts_resolved,
        degraded_sources=degraded_sources,
        canonical_rows=csv_rows,
    )

    # Structured gaps
    gaps: List[Dict[str, Any]] = []
    for deg in degraded_sources:
        gaps.append(
            {
                "code": "DEGRADED_OR_UNAVAILABLE_SOURCE",
                "severity": "error" if deg["status"] in ("unavailable", "rate_limited", "parse_error", "error") else "warning",
                "source": deg["source"],
                "message": f"Source '{deg['source']}' degraded ({deg['status']}): {deg['error']}. {deg['impact_on_coverage']}",
            }
        )
    if amb_same_title_distinct_doi:
        gaps.append(
            {
                "code": "AMBIGUOUS_SAME_TITLE_DISTINCT_FORMAL_DOI",
                "severity": "warning",
                "source": "search_core.dedup",
                "message": f"{len(amb_same_title_distinct_doi)} same-title distinct-formal-DOI pair(s) blocked from false merge and queued for human arbitration.",
            }
        )
    if sys_missing:
        gaps.append(
            {
                "code": "SYSTEMATIC_SOURCE_FIELD_GAPS",
                "severity": "info",
                "source": "compute_field_coverage_by_source",
                "message": f"{len(sys_missing)} database-field pair(s) exhibit systematic missingness (<=15% fill rate); see source_coverage_matrix.json.",
            }
        )
    if field_conflicts_resolved:
        gaps.append(
            {
                "code": "CROSS_SOURCE_FIELD_CONFLICTS_RESOLVED",
                "severity": "info",
                "source": "search_core.dedup",
                "message": f"{len(field_conflicts_resolved)} cross-source field conflict(s) deterministically resolved with all raw values preserved.",
            }
        )

    status_report = pl.status_block(
        builder,
        S,
        extra={
            "degraded_sources": degraded_sources,
            "valid_empty_sources": valid_empty_sources,
            "source_count_conserved": conserved_ok,
            "ambiguous_same_title_distinct_doi": amb_same_title_distinct_doi,
            "completed": [
                "multi_source_query_adaptation",
                "field_semantic_standardization_and_coverage_matrix",
                "four_tier_dedup_with_hard_distinct_doi_guard",
                "preprint_to_vor_version_chaining",
                "deterministic_conflict_resolution_with_raw_preservation",
                "source_exclusivity_and_overlap_matrix",
                "degraded_source_gap_disclosure",
            ],
            "not_completed": [
                f"unavailable_source_retrieval:{d['source']}({d['status']})" for d in degraded_sources
            ] or ["live_remote_api_querying (offline sandbox boundary)"],
            "uncertainty": [
                "Finite local multi-database snapshot only; unavailable or rate-limited databases create explicit coverage gaps.",
                "Systematic missing fields (e.g., arXiv citations_count or Crossref abstracts) reflect database schema semantics, not zero values.",
            ],
        },
    )

    metrics = {
        "raw_record_count": dedup_rep["n_records"],
        "canonical_record_count": dedup_rep["n_unique"],
        "dedup_merged_count": dedup_rep["n_merged_away"],
        "source_count_conserved": 1 if conserved_ok else 0,
        "available_source_count": len(dedup_rep["per_source_raw_counts"]),
        "degraded_source_count": len(degraded_sources),
        "single_source_exclusive_count": excl_summary["total_exclusive_works"],
        "multi_source_overlap_count": excl_summary["multi_source_overlap_works"],
        "ambiguous_same_title_distinct_doi_count": len(amb_same_title_distinct_doi),
        "preprint_to_vor_chain_count": len(preprint_chains),
        "field_conflicts_resolved_count": len(field_conflicts_resolved),
        "systematic_missing_field_count": len(sys_missing),
        "rollback_log_count": len(rollback_log),
    }
    metric_provenance = {
        "raw_record_count": "dedup_rollback_log.json:summary.n_records",
        "canonical_record_count": "federated_merged.csv:row_count",
        "dedup_merged_count": "dedup_rollback_log.json:summary.n_merged_away",
        "source_count_conserved": "dedup_rollback_log.json:source_count_conserved",
        "available_source_count": "source_coverage_matrix.json:field_coverage_by_source",
        "degraded_source_count": "source_coverage_matrix.json:degraded_sources",
        "single_source_exclusive_count": "source_coverage_matrix.json:exclusive_works_by_source.total_exclusive_works",
        "multi_source_overlap_count": "source_coverage_matrix.json:exclusive_works_by_source.multi_source_overlap_works",
        "ambiguous_same_title_distinct_doi_count": "ambiguous_arbitration_queue.json:n_same_title_distinct_doi",
        "preprint_to_vor_chain_count": "result.json:results.preprint_to_vor_chains",
        "field_conflicts_resolved_count": "result.json:evidence.field_conflicts_resolved",
        "systematic_missing_field_count": "source_coverage_matrix.json:systematic_missing_fields",
        "rollback_log_count": "dedup_rollback_log.json:rollback_entries",
    }

    findings = [
        f"Merged {dedup_rep['n_records']} raw multi-database records across {len(dedup_rep['per_source_raw_counts'])} available source(s) into {dedup_rep['n_unique']} canonical works (merged={dedup_rep['n_merged_away']}, conserved={conserved_ok}).",
        f"Blocked {len(amb_same_title_distinct_doi)} same-title distinct-formal-DOI pair(s) from false merging and queued them in ambiguous_arbitration_queue.json.",
        f"Linked {len(preprint_chains)} preprint-to-VoR upgrade chain(s) and deterministically resolved {len(field_conflicts_resolved)} cross-source field conflict(s) with full raw value retention.",
        f"Identified {excl_summary['total_exclusive_works']} single-source exclusive work(s), {excl_summary['multi_source_overlap_works']} multi-source overlap work(s), {len(sys_missing)} systematic source-field gap(s), and {len(degraded_sources)} degraded/unavailable source(s).",
    ]

    # Write audit_log, run_manifest, report.md
    builder.log(
        "federated_merge",
        "completed",
        raw=dedup_rep["n_records"],
        canonical=dedup_rep["n_unique"],
        ambiguous_distinct_doi=len(amb_same_title_distinct_doi),
        degraded_sources=len(degraded_sources),
    )
    audit_payload = {
        "entries": builder.audit,
        "chain_head": builder.prev_hash,
        "verified": ct._verify_chain(builder.audit),
    }
    builder.write_json("audit_log.json", audit_payload)
    builder.write_json(
        "run_manifest.json",
        {
            "case_id": case_id,
            "files": bundle.inventory(),
            "records_by_file": bundle.record_files,
            "source_load_errors": bundle.source_load_errors,
            "pagination_issues": bundle.pagination_issues,
            "degraded_sources": degraded_sources,
            "notes": bundle.notes,
        },
    )
    builder.write_markdown("report.md", report_md)

    artifact_list = ["result.json"] + [a["path"] for a in builder.artifacts]
    patch = {
        "skill": SKILL_NAME,
        "artifacts": artifact_list,
        "results": {
            "canonical_records": canonical_records,
            "preprint_to_vor_chains": preprint_chains,
            "source_count_conserved": conserved_ok,
        },
        "evidence": {
            "per_source_queries": per_source_queries,
            "field_coverage_by_source": field_cov,
            "systematic_missing_fields": sys_missing,
            "exclusive_works_by_source": excl_summary,
            "source_overlap_matrix": overlap_summary,
            "field_conflicts_resolved": field_conflicts_resolved,
            "deduplication_summary": dedup_rep,
            "rollback_log": rollback_log,
        },
        "status_report": status_report,
        "source_count_conserved": conserved_ok,
        "field_coverage_by_source": field_cov,
        "exclusive_works_by_source": excl_summary,
        "source_overlap_matrix": overlap_summary,
        "ambiguous_same_title_distinct_doi": amb_same_title_distinct_doi,
        "preprint_to_vor_chains": preprint_chains,
        "field_conflicts_resolved": field_conflicts_resolved,
        "rollback_log": rollback_log,
        "degraded_sources": degraded_sources,
        "metrics": metrics,
        "metric_provenance": metric_provenance,
        "findings": findings,
        "gaps": gaps,
        "warnings": list(bundle.notes),
        "uncertainty": status_report["uncertainty"],
        "audit": {
            "log": "audit_log.json",
            "entries": len(builder.audit),
            "chain_head": builder.prev_hash,
            "verified": audit_payload["verified"],
        },
    }

    res = result_guard.update_result(result_path, patch, finalize=True)

    # M5: Grounding check
    budget_guard.tick_phase(budget_file, "gap_gate_and_final_audit")
    grounding = gap_gate.check_grounding(res, [input_dir])
    (work_dir / "gaps.json").write_text(
        json.dumps({"gaps": gaps + grounding["gaps"], "grounding": grounding}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if grounding["gaps"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    # M6: Final deliverable audit
    required_files = [
        "federated_merged.csv",
        "source_coverage_matrix.json",
        "dedup_rollback_log.json",
        "ambiguous_arbitration_queue.json",
        "report.md",
        "audit_log.json",
        "run_manifest.json",
    ]
    audit = final_audit.audit_deliverables(output_dir, result_file="result.json", required_files=required_files)
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor challenge-5c918ff0 Multi-Database Federated Search Merger.")
    parser.add_argument("--input", "-i", default="", help="Input case directory (default: auto-detect /input).")
    parser.add_argument("--output", "-o", default="", help="Output directory (default: /app/output or /output).")
    parser.add_argument("--work", "-w", default="", help="Work directory (default: /app/work).")
    parser.add_argument("--question", "-q", default="", help="Optional research question override.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], question_override=args.question)
    print(
        json.dumps(
            {
                "ok": True,
                "case_id": res["case_id"],
                "status": res["status"],
                "metrics": res["metrics"],
                "artifacts": res["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
