#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline Academic Literature Search Engine (challenge-a2909a72).

Pure Python 3.12 stdlib pipeline built on `search_core` + `common_core` (M1-M6):
  1. Structure-agnostic case intake with tri-triage of source health:
     - malformed JSON / unsupported shape -> `source_load_errors`
     - HTTP/cursor/rate-limit error payloads (e.g. PyAlex #103) -> `pagination_issues`
     - valid empty JSON arrays `[]` -> `valid_empty_sources` (never conflated with errors)
  2. Research element-card decomposition + dependency-ordered sub-questions (SQ0..SQ6).
  3. Two-round controlled retrieval:
     - Round 1: unexpanded core terms (Screening, not Cherry-picking)
     - Term zero-hit / postings diagnosis (metadata vs fulltext-only blindspot vs
       acronym-recoverable vs corpus-absent)
     - Round 2: corpus-mined abbreviation + controlled co-occurrence expansion with
       per-sub-question marginal gain and explicit stopping verdict.
  4. Three-perspective evidence annotation (method developer / frontline application /
     independent review & evaluation) + GAP/precedent role cues.
  5. Traceable deduplication with hard distinct-formal-DOI anti-false-merge guard
     (`ambiguous_candidates`), ASReview #328 `rollback_log`, `source_count_conserved`,
     transparent 4-tier ranking (`core / method-only / background / excluded`),
     deterministic tie-breaking, and Leave-One-Term-Out top-k rank stability.
  6. Full deliverable suite (`results_ranked.csv`, `evidence_chains.json`,
     `query_log.json`, `report.md`, `audit_log.json`, `run_manifest.json`, `result.json`)
     verified through M5 (`gap_gate`) and M6 (`final_audit`).
"""
from __future__ import annotations

import argparse
import json
import os
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
    graph as gr,
    pipeline as pl,
    query as qm,
    scholarmeta as sm,
    screen as sc,
)

SKILL_NAME = "academic-lit-search-engine"
TASK_ID = "challenge-a2909a72"

PERSPECTIVE_CUES: Dict[str, Tuple[str, ...]] = {
    "method_developer": (
        "dft", "density functional", "first-principles", "first principles", "ab initio",
        "synthesis", "synthesized", "design", "designed", "mechanism", "mechanistic",
        "lattice oxygen", "atomic", "electronic structure", "d-band", "doping", "doped",
        "heterostructure", "single-atom", "nanostructure", "algorithm", "model",
        "framework", "protocol", "computational", "descriptor", "theory", "theoretical",
    ),
    "frontline_application": (
        "pem", "pemwe", "electrolyzer", "electrolyser", "membrane electrode", "mea",
        "durability", "stability", "degradation", "dissolution", "lifetime", "hours",
        "overpotential", "current density", "ma cm", "a cm", "operando", "in situ",
        "real-world", "device", "cell", "stack", "experimental", "measured", "reproduc",
        "failure", "aging", "accelerated stress", "acidic", "h2so4", "hclo4",
    ),
    "independent_review_eval": (
        "review", "survey", "perspective", "benchmark", "meta-analysis", "overview",
        "progress", "challenges", "recent advances", "critical", "comparison",
        "comparative", "roadmap", "state of the art", "state-of-the-art", "assessment",
        "evaluation", "systematic", "summary",
    ),
}

GAP_PRECEDENT_CUES: Dict[str, Tuple[str, ...]] = {
    "negative_or_limit_finding": (
        "limit", "limitation", "limits", "degrade", "degradation", "dissolution",
        "unstable", "instability", "failure", "bottleneck", "trade-off", "tradeoff",
        "challenge", "challenges", "drawback", "poisoning", "leaching",
    ),
    "gap_anchor": (
        "remains unclear", "poorly understood", "unresolved", "gap", "elusive",
        "controversial", "debated", "open question", "mechanism remains", "critical challenge",
    ),
    "method_precedent": (
        "operando", "in situ", "xas", "xps", "icp-ms", "dft", "benchmark",
        "protocol", "accelerated", "mea", "electrolyzer",
    ),
}

CODE_DATA_CUES = (
    "github.com", "gitlab.com", "zenodo", "figshare", "code availability",
    "data availability", "open-source", "open source", "supplementary",
    "repository", "dataset",
)

TIER_ORDER = {"core": 0, "method-only": 1, "background": 2, "excluded": 3}


def _collect_valid_empty_sources(bundle: caseio.CaseBundle) -> List[Dict[str, Any]]:
    """Explicitly identify JSON files that parsed cleanly as empty arrays `[]`."""
    empties: List[Dict[str, Any]] = []
    for fe in bundle.files:
        if "valid empty json array" in (fe.note or "").lower():
            empties.append(
                {
                    "file": fe.rel,
                    "source_kind": fe.source_kind or "json_array",
                    "record_count": 0,
                    "classification": "valid_empty_snapshot",
                    "note": "Parsed cleanly as empty JSON array []; distinct from malformed JSON or cursor/HTTP failure.",
                }
            )
    return empties


def _build_fulltext_index(docs: List[Any]) -> Dict[str, int]:
    """Count how many extracted full-text documents contain each stemmed token."""
    doc_freq: Counter = Counter()
    for d in docs:
        toks = set(qm.stem_tokens(qm.tokenize((getattr(d, "text", "") or "")[:200000])))
        for t in toks:
            doc_freq[t] += 1
    return dict(doc_freq)


def diagnose_term_postings(
    terms: List[str],
    raw_searcher: Optional[sc.LocalSearcher],
    items: List[Dict[str, Any]],
    abbrevs: List[Dict[str, Any]],
    docs: List[Any],
) -> List[Dict[str, Any]]:
    """Diagnose per-term hit distribution across metadata, fulltext, and corpus-mined acronyms.
    Enforces unified tokenization/case/acronym normalization and strict invariant:
    a term occurring in any title or abstract can NEVER be diagnosed as corpus_absent."""
    ft_df = _build_fulltext_index(docs)
    abbr_lookup: Dict[str, List[str]] = defaultdict(list)
    for a in abbrevs:
        ab = str(a.get("abbr") or "").lower()
        exp = str(a.get("expansion") or "")
        if ab and exp:
            exp_stems = qm.stem_tokens(qm.tokenize(exp))
            abbr_lookup[ab].extend(exp_stems)
            for s in exp_stems:
                abbr_lookup[s].append(ab)

    diagnostics: List[Dict[str, Any]] = []
    seen: set = set()
    for raw_t in terms:
        tok_stems = qm.stem_tokens(qm.tokenize(raw_t))
        st = tok_stems[0] if len(tok_stems) == 1 else qm.stem(raw_t.lower().strip())
        if len(st) <= 1 or st in seen:
            continue
        seen.add(st)
        raw_low = raw_t.lower().strip()
        raw_pat = re.compile(r"(?<![a-z0-9])" + re.escape(raw_low) + r"(?![a-z0-9])", re.I) if raw_low else None

        title_hits = 0
        abstract_hits = 0
        kw_hits = 0
        meta_any = 0
        for i, it in enumerate(items):
            t_txt = str(it.get("title") or "")
            a_txt = str(it.get("abstract") or "")
            k_txt = " ".join(it.get("keywords") or []) + " " + str(it.get("venue") or "")
            t_match = bool(raw_searcher is not None and raw_searcher.idx["title"].tf[i].get(st))
            a_match = bool(raw_searcher is not None and raw_searcher.idx["abstract"].tf[i].get(st))
            k_match = bool(raw_searcher is not None and raw_searcher.idx["kw"].tf[i].get(st))
            if not t_match and raw_pat and raw_pat.search(t_txt):
                t_match = True
            if not a_match and raw_pat and raw_pat.search(a_txt):
                a_match = True
            if not k_match and raw_pat and raw_pat.search(k_txt):
                k_match = True
            if not t_match and st in set(qm.stem_tokens(qm.tokenize(t_txt))):
                t_match = True
            if not a_match and st in set(qm.stem_tokens(qm.tokenize(a_txt))):
                a_match = True
            if t_match:
                title_hits += 1
            if a_match:
                abstract_hits += 1
            if k_match:
                kw_hits += 1
            if t_match or a_match or k_match:
                meta_any += 1

        ft_hits = int(ft_df.get(st, 0))

        variants = sorted(set(abbr_lookup.get(st, []) + abbr_lookup.get(raw_low, [])) - {st})
        variant_hits = 0
        if raw_searcher is not None and variants:
            for i, _ in enumerate(items):
                if any(
                    raw_searcher.idx[f].tf[i].get(v)
                    for v in variants
                    for f in ("title", "abstract", "kw")
                ):
                    variant_hits += 1

        if meta_any > 0:
            diagnosis = "metadata_present"
            action = "retained_in_core_and_expansion"
        elif ft_hits > 0:
            diagnosis = "fulltext_only_metadata_blindspot"
            action = "rely_on_fulltext_anchor_and_expand_metadata_synonyms"
        elif variant_hits > 0:
            diagnosis = "acronym_or_variant_recoverable"
            action = f"recover_in_round2_via:{','.join(variants[:4])}"
        else:
            diagnosis = "corpus_absent"
            action = "report_honest_snapshot_gap_without_fabricating_recall"

        # Hard consistency invariant: title/abstract presence can NEVER be corpus_absent
        if (title_hits > 0 or abstract_hits > 0) and diagnosis == "corpus_absent":
            raise AssertionError(f"Posting invariant violated for term={raw_t!r}: title_hits={title_hits}, abstract_hits={abstract_hits}")

        diagnostics.append(
            {
                "term": raw_t,
                "stem": st,
                "title_hits": title_hits,
                "abstract_hits": abstract_hits,
                "keyword_venue_hits": kw_hits,
                "metadata_records_hit": meta_any,
                "fulltext_docs_hit": ft_hits,
                "variant_terms": variants[:6],
                "variant_metadata_records_hit": variant_hits,
                "diagnosis": diagnosis,
                "recommended_action": action,
            }
        )
    return diagnostics



def compute_ranking_quality_metrics(
    ranked_candidates: List[Dict[str, Any]],
    bundle: caseio.CaseBundle,
) -> Dict[str, Any]:
    """Compute metadata completeness rate, strictly distinguishing exhaustive gold qrels
    (Precision@k/Recall@k/nDCG@k) from non-exhaustive seed/known sets (seed_hit_rate only;
    never reports overall Recall on non-exhaustive seed lists)."""
    import math
    n = len(ranked_candidates)
    if n == 0:
        return {"metadata_completeness_rate": 0.0, "gold_eval_status": "empty_results"}
    comp_scores = []
    for r in ranked_candidates:
        f_ok = sum([
            1 if (r.get("title") or "").strip() else 0,
            1 if (r.get("authors") or []) else 0,
            1 if r.get("year") else 0,
            1 if (r.get("venue") or "").strip() else 0,
            1 if (r.get("doi") or r.get("arxiv") or r.get("pmid")) else 0,
        ])
        comp_scores.append(f_ok / 5.0)
    meta_rate = round(sum(comp_scores) / n, 4)

    gold_dois: set[str] = set()
    gold_titles: set[str] = set()
    seed_dois: set[str] = set()
    seed_titles: set[str] = set()
    for _, rs in (bundle.reference_sets or {}).items():
        key_low = str(rs.get("key") or rs.get("file") or "").lower()
        is_exhaustive_gold = any(k in key_low for k in ("gold", "ground_truth", "qrels", "exhaustive_relevant"))
        is_seed_only = (not is_exhaustive_gold) and any(k in key_low for k in ("seed", "known", "included", "relevant"))
        if not (is_exhaustive_gold or is_seed_only):
            continue
        for rec in rs.get("records", []) or []:
            if isinstance(rec, dict):
                d = sm.norm_doi(rec.get("doi") or "")
                t = sm.norm_title(rec.get("title") or "")
                if is_exhaustive_gold:
                    if d:
                        gold_dois.add(d)
                    if t:
                        gold_titles.add(t)
                else:
                    if d:
                        seed_dois.add(d)
                    if t:
                        seed_titles.add(t)

    if gold_dois or gold_titles:
        rel_vec = [
            1 if ((sm.norm_doi(r.get("doi") or "") in gold_dois) or (sm.norm_title(r.get("title") or "") in gold_titles)) else 0
            for r in ranked_candidates
        ]
        total_gold = max(len(gold_dois), len(gold_titles))
        out: Dict[str, Any] = {
            "metadata_completeness_rate": meta_rate,
            "gold_eval_status": "evaluated_against_exhaustive_gold_qrels",
            "gold_count": total_gold,
            "gold_hits_total": sum(rel_vec),
        }
        for k in (5, 10):
            top = rel_vec[:k]
            denom = max(1, min(k, len(rel_vec)))
            out[f"precision_at_{k}"] = round(sum(top) / denom, 4)
            out[f"recall_at_{k}"] = round(sum(top) / max(1, total_gold), 4)
            dcg = sum(v / math.log2(i + 2) for i, v in enumerate(top))
            idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, total_gold)))
            out[f"ndcg_at_{k}"] = round(dcg / idcg, 4) if idcg > 0 else 0.0
        return out

    if seed_dois or seed_titles:
        seed_vec = [
            1 if ((sm.norm_doi(r.get("doi") or "") in seed_dois) or (sm.norm_title(r.get("title") or "") in seed_titles)) else 0
            for r in ranked_candidates
        ]
        total_seeds = max(len(seed_dois), len(seed_titles))
        return {
            "metadata_completeness_rate": meta_rate,
            "gold_eval_status": "non_exhaustive_seed_set_only_overall_recall_not_applicable",
            "seed_count": total_seeds,
            "seed_hits_total": sum(seed_vec),
            "seed_hit_rate_at_5": round(sum(seed_vec[:5]) / max(1, total_seeds), 4),
            "seed_hit_rate_at_10": round(sum(seed_vec[:10]) / max(1, total_seeds), 4),
            "overall_recall_status": "not_applicable_non_exhaustive_seed_set",
        }

    return {
        "metadata_completeness_rate": meta_rate,
        "gold_eval_status": "not_applicable_no_gold_labels_in_input",
        "gold_count": 0,
    }

def annotate_perspectives(item: Dict[str, Any]) -> Dict[str, Any]:
    """Tag each record across three orthogonal scholarly perspectives using transparent lexical cues."""
    blob = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("abstract") or ""),
            " ".join(item.get("keywords") or []),
            " ".join(item.get("mesh") or []),
            str(item.get("pub_type") or ""),
            str(item.get("venue") or ""),
        ]
    ).lower()
    perspectives: List[str] = []
    cue_hits: Dict[str, List[str]] = {}
    for p_name, cues in PERSPECTIVE_CUES.items():
        matched = [c for c in cues if re.search(r"\b" + re.escape(c), blob)]
        if matched:
            perspectives.append(p_name)
            cue_hits[p_name] = matched[:6]
    if not perspectives:
        perspectives = ["unclassified_from_metadata"]

    roles: List[str] = []
    for r_name, cues in GAP_PRECEDENT_CUES.items():
        if any(re.search(r"\b" + re.escape(c), blob) for c in cues):
            roles.append(r_name)
    if not roles:
        roles = ["standard_evidence"]

    code_cues = [c for c in CODE_DATA_CUES if c in blob]
    return {
        "perspectives": perspectives,
        "perspective_cues": cue_hits,
        "gap_precedent_roles": roles,
        "code_or_data_cue": "mentioned_in_metadata" if code_cues else "not_reported_in_metadata",
        "code_or_data_matches": code_cues[:4],
    }


def assign_four_tier_label(
    scr: Dict[str, Any],
    item: Dict[str, Any],
    persp: Dict[str, Any],
    r1_score: float,
    r2_score: float,
    filters: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """Map screened candidate into transparent 4-tier taxonomy: core / method-only / background / excluded."""
    codes = list(scr.get("reason_codes") or [])
    year = item.get("year") or 0
    y_min = filters.get("year_min")
    y_max = filters.get("year_max")
    in_window = True
    if y_min and year and year < y_min:
        in_window = False
    if y_max and year and year > y_max:
        in_window = False

    if item.get("is_retracted"):
        codes.append("TIER:excluded_retracted")
        return "excluded", list(dict.fromkeys(codes))

    if not in_window:
        if r2_score >= 3.0 or r1_score >= 2.0:
            codes.append(f"PRE_WINDOW_CONTEXT:year={year}")
            codes.append("TIER:background_pre_window_foundational")
            return "background", list(dict.fromkeys(codes))
        codes.append(f"EXC:out_of_time_window({year})")
        return "excluded", list(dict.fromkeys(codes))

    if scr.get("rules", {}).get("exclusion_hits") or not scr.get("lang_ok", True):
        codes.append("TIER:excluded_hard_rule")
        return "excluded", list(dict.fromkeys(codes))

    has_abstract = bool((item.get("abstract") or "").strip())
    if r2_score <= 0.0 and has_abstract:
        codes.append("EXC:zero_query_overlap")
        return "excluded", list(dict.fromkeys(codes))

    per_sq = scr.get("per_sub_question") or {}
    sq_hits = sum(1 for v in per_sq.values() if v >= 1.2)
    persp_set = set(persp.get("perspectives") or [])

    if scr.get("final_label") == "include" and (sq_hits >= 2 or r2_score >= 8.0):
        codes.append("TIER:core_direct_evidence")
        return "core", list(dict.fromkeys(codes))

    if scr.get("final_label") == "include" or (
        r2_score >= 5.0 and has_abstract and ("frontline_application" in persp_set and "method_developer" in persp_set)
    ):
        codes.append("TIER:core_multi_facet_match")
        return "core", list(dict.fromkeys(codes))

    if not has_abstract and r2_score > 0.0:
        codes.append("TIER:method_only_protected_missing_abstract")
        return "method-only", list(dict.fromkeys(codes))

    if "method_developer" in persp_set and r2_score >= 2.5:
        codes.append("TIER:method_only_catalyst_or_mechanism")
        return "method-only", list(dict.fromkeys(codes))

    if scr.get("final_label") == "borderline" or r2_score >= 2.0:
        codes.append("TIER:background_adjacent_evidence")
        return "background", list(dict.fromkeys(codes))

    if r2_score > 0.0:
        codes.append("TIER:background_weak_lexical_overlap")
        return "background", list(dict.fromkeys(codes))

    codes.append("EXC:insufficient_signal")
    return "excluded", list(dict.fromkeys(codes))


def build_markdown_report(
    case_id: str,
    question: str,
    plan: Dict[str, Any],
    r1_vs_r2: Dict[str, Any],
    marginal_by_sq: List[Dict[str, Any]],
    term_diag: List[Dict[str, Any]],
    ranked_rows: List[Dict[str, Any]],
    tier_counts: Dict[str, int],
    persp_counts: Dict[str, int],
    dedup_rep: Dict[str, Any],
    ambiguous: List[Dict[str, Any]],
    version_pairs: List[Dict[str, Any]],
    rank_stab: Dict[str, Any],
    source_load_errors: List[Dict[str, Any]],
    pagination_issues: List[Dict[str, Any]],
    valid_empty_sources: List[Dict[str, Any]],
    stopping: Dict[str, Any],
) -> str:
    lines: List[str] = [
        f"# Academic Literature Search Report (`{case_id}`)",
        "",
        "## 1. Scope & Finite-Snapshot Boundary",
        f"- **Research Question**: {question}",
        f"- **Reference Year**: {plan.get('reference_year')}",
        "- **Boundary Statement**: Finite authorized offline snapshot only. No live remote database coverage, global novelty, or unverified full-text/citation status is claimed.",
        f"- **Stopping Verdict**: `{stopping.get('decision')}` — {stopping.get('reason')}",
        "",
        "## 2. Research Element Card & Screening Funnel",
    ]
    ec = plan.get("element_card") or {}
    ec_rows = [{"element": k, "value": v} for k, v in ec.items()]
    lines.append(ct.md_table(ec_rows, ["element", "value"], max_rows=20, widths={"element": 26, "value": 90}))

    lines += [
        "## 3. Source Health Tri-Triage (Errors vs Pagination vs Valid Empty)",
        f"- **Raw Records Loaded**: {dedup_rep.get('n_records')} -> **Unique Canonical Works**: {dedup_rep.get('n_unique')} (merged away with trace: {dedup_rep.get('n_merged_away')}, conserved: `{dedup_rep.get('source_count_conserved')}`)",
        f"- **Source Load Errors (Malformed/Unsupported JSON)**: {len(source_load_errors)}",
        f"- **Pagination / API Error Snapshots (PyAlex #103 / HTTP)**: {len(pagination_issues)}",
        f"- **Valid Empty JSON Array Sources (`[]`)**: {len(valid_empty_sources)}",
        "",
    ]
    if source_load_errors:
        lines.append("### Source Load Errors (NOT treated as zero hits)")
        lines.append(ct.md_table(source_load_errors, ["file", "error_type", "detail"], widths={"file": 36, "error_type": 20, "detail": 90}))
    if pagination_issues:
        lines.append("### Pagination / Access Issues (NOT treated as zero hits)")
        lines.append(ct.md_table(pagination_issues, ["file", "source", "status", "error", "cursor"], widths={"file": 32, "source": 14, "status": 12, "error": 65, "cursor": 24}))
    if valid_empty_sources:
        lines.append("### Valid Empty Sources (Cleanly parsed `[]`, 0 records)")
        lines.append(ct.md_table(valid_empty_sources, ["file", "record_count", "classification", "note"], widths={"file": 32, "note": 70}))

    lines += [
        "## 4. Two-Round Query Planning & Sub-Question Marginal Gain",
        f"- **Round 1 (Core Unexpanded) Positive Hits**: {r1_vs_r2.get('round1_positive_hits')} | **Round 2 (Controlled Expansion) Positive Hits**: {r1_vs_r2.get('round2_positive_hits')} | **Net Marginal New Hits**: +{r1_vs_r2.get('marginal_new_hits')}",
        f"- **Top-k Rank Stability (LOO, k={rank_stab.get('effective_k')})**: mean overlap = `{rank_stab.get('mean_topk_overlap_loo')}`, min = `{rank_stab.get('min_overlap')}`, verdict = `{rank_stab.get('stability')}`",
        "",
        ct.md_table(
            marginal_by_sq,
            ["id", "dimension", "r1_hits", "r2_hits", "marginal_new_hits", "evidence_state"],
            widths={"id": 8, "dimension": 24},
        ),
        "## 5. Term Postings & Zero-Hit Diagnosis",
        ct.md_table(
            term_diag,
            ["term", "title_hits", "abstract_hits", "keyword_venue_hits", "fulltext_docs_hit", "variant_metadata_records_hit", "diagnosis"],
            max_rows=30,
            widths={"term": 20, "diagnosis": 34},
        ),
        "## 6. Tiered & Three-Perspective Candidate Summary",
        f"- **Tier Breakdown**: `core`={tier_counts.get('core', 0)}, `method-only`={tier_counts.get('method-only', 0)}, `background`={tier_counts.get('background', 0)}, `excluded`={tier_counts.get('excluded', 0)}",
        f"- **Perspective Breakdown**: {json.dumps(persp_counts, ensure_ascii=False)}",
        "",
        "### Top 20 Ranked Candidates",
        ct.md_table(
            ranked_rows,
            ["rank", "tier", "year", "score", "perspectives", "title", "doi", "sources"],
            max_rows=20,
            widths={"title": 56, "perspectives": 34, "doi": 28, "sources": 22},
        ),
        "## 7. Deduplication, Version Chains & Ambiguous Arbitration",
        f"- **Ambiguous Same-Title Distinct-DOI Pairs Kept Separate**: {len(ambiguous)}",
        f"- **Preprint-to-VoR Version Chains Linked**: {len(version_pairs)}",
        f"- **ASReview #328 Rollback Entries Logged**: {dedup_rep.get('n_rollback_entries')}",
        "",
    ]
    if ambiguous:
        lines.append("### Ambiguous Identity Candidates (Kept Separate for Human Arbitration)")
        lines.append(
            ct.md_table(
                ambiguous,
                ["rid_a", "rid_b", "doi_a", "doi_b", "reason", "title_similarity", "action"],
                widths={"rid_a": 32, "rid_b": 32, "doi_a": 26, "doi_b": 26, "reason": 30},
            )
        )
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
    budget_guard.tick_phase(budget_file, "intake_and_triage")

    # Case intake via search_core
    bundle = caseio.load_case(str(input_dir), question_override=question_override)
    case_id = bundle.case_id or "case-unknown"

    # M2: Early skeleton result.json so timeout never leaves blank output
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id=TASK_ID)

    builder = ct.ResultBuilder(
        bundle,
        work_dir=str(work_dir),
        out_dir=str(output_dir),
        skill_name=SKILL_NAME,
        pipeline={"engine": "academic_search_pipeline.py", "core": "search_core+common_core", "version": "2.0"},
    )
    S = pl.prepare(builder, question=question_override, extract_docs=True)
    if S["docs"]:
        pl.attach_docs(S, builder=builder)

    valid_empty_sources = _collect_valid_empty_sources(bundle)
    source_load_errors = list(S.get("source_load_errors") or [])
    pagination_issues = list(S.get("pagination_issues") or [])

    # Re-mine abbreviations including the question text itself, then filter strictly to question-anchored pairs
    all_abbrev_texts = ([S["question"]] if S.get("question") else []) + list(S.get("texts") or [])
    raw_abbrevs, unexpanded = qm.mine_abbreviations(all_abbrev_texts)
    q_upper_tokens = set(re.findall(r"\b[A-Z][A-Z0-9\-]{1,11}\b", S.get("question") or ""))
    q_key_stems = set(S["plan"].get("key_terms") or [])
    abbrevs: List[Dict[str, Any]] = []
    for a in raw_abbrevs:
        abbr_str = str(a.get("abbr") or "")
        exp_stems = set(qm.stem_tokens(qm.tokenize(str(a.get("expansion") or ""))))
        # Prevent query drift (e.g. generic 'oxide' pulling unrelated GO/RGO/MOR):
        # require exact acronym mention in question OR >=2 distinct stem overlaps with question terms.
        if abbr_str in q_upper_tokens or len(exp_stems & q_key_stems) >= 2:
            abbrevs.append(a)
    S["abbrevs"] = abbrevs
    S["unexpanded"] = unexpanded

    plan = S["plan"]
    items = S["items"]
    clusters = S["clusters"]
    dedup_rep = S["dedup"]
    ambiguous = S["ambiguous_candidates"]
    rollback_log = S["rollback_log"]

    # Re-build adapted Round 1 & Round 2 queries with drift-guarded abbreviations
    q_sources = [
        s for s in ("crossref", "europepmc", "pubmed", "arxiv", "openalex", "publisher")
        if s in S["sources"] or s in ("crossref", "europepmc", "openalex")
    ]
    queries_plan = qm.build_queries(plan, abbrevs, S["co_terms"], q_sources)
    rounds = queries_plan.get("rounds") or []
    r1_terms = list(rounds[0]["terms"]) if len(rounds) >= 1 else list(plan.get("key_terms") or [])
    # Round 2 adds question-anchored acronym expansions + high-support co-occurrence terms
    r2_extra: List[str] = []
    for a in abbrevs:
        r2_extra.append(a["abbr"].lower())
        r2_extra.extend(t for t in qm.stem_tokens(qm.tokenize(a.get("expansion", ""))) if len(t) > 2)
    r2_extra.extend(c["term"] for c in (S.get("co_terms") or [])[:5] if c.get("count", 0) >= 3)
    r2_terms = list(dict.fromkeys(r1_terms + r2_extra))
    if len(rounds) >= 2:
        rounds[1]["terms"] = r2_terms

    budget_guard.tick_phase(budget_file, "two_round_retrieval_and_diagnosis")

    # Round 1: strictly unexpanded core retrieval
    raw_searcher = sc.LocalSearcher(items, abbrevs=[]) if items else None
    exp_searcher = sc.LocalSearcher(items, abbrevs=abbrevs) if items else None

    r1_scores = raw_searcher.score_all(r1_terms) if raw_searcher else {}
    r2_scores = exp_searcher.score_all(r2_terms) if exp_searcher else {}

    r1_hit_rids = {rid for rid, sc_val in r1_scores.items() if sc_val > 0.0}
    r2_hit_rids = {rid for rid, sc_val in r2_scores.items() if sc_val > 0.0}
    marginal_new_rids = sorted(r2_hit_rids - r1_hit_rids)

    # Term postings / zero-hit diagnosis across core + sub-question terms
    all_diag_terms: List[str] = list(r1_terms)
    for sq in plan.get("sub_questions") or []:
        all_diag_terms.extend(sq.get("terms") or [])
    term_diag = diagnose_term_postings(all_diag_terms, raw_searcher, items, abbrevs, S["docs"])

    # Per-sub-question Round 1 vs Round 2 marginal gain
    terms_by_sq: Dict[str, List[str]] = {}
    marginal_by_sq: List[Dict[str, Any]] = []
    cov_by_id = {c["id"]: c for c in (S["coverage"].get("sub_questions") or [])}
    for sq in plan.get("sub_questions") or []:
        sq_id = sq["id"]
        sq_terms = list(sq.get("terms") or r1_terms)
        if not sq_terms:
            sq_terms = list(r1_terms)
        terms_by_sq[sq_id] = sq_terms
        s1 = raw_searcher.score_all(sq_terms) if raw_searcher else {}
        sq_r2_terms = list(dict.fromkeys(sq_terms + [t for t in r2_terms if t not in r1_terms][:6]))
        s2 = exp_searcher.score_all(sq_r2_terms) if exp_searcher else {}
        h1 = {rid for rid, val in s1.items() if val > 0.0}
        h2 = {rid for rid, val in s2.items() if val > 0.0}
        new_h = sorted(h2 - h1)
        marginal_by_sq.append(
            {
                "id": sq_id,
                "dimension": sq.get("dimension", sq.get("type", "")),
                "text": sq.get("text", ""),
                "r1_terms": sq_terms,
                "r2_terms": sq_r2_terms,
                "r1_hits": len(h1),
                "r2_hits": len(h2),
                "marginal_new_hits": len(new_h),
                "marginal_gain_rate": round(len(new_h) / max(1, len(h1)), 3),
                "marginal_new_rids": new_h[:20],
                "evidence_state": cov_by_id.get(sq_id, {}).get("evidence_state", "UNKNOWN"),
            }
        )
    if not terms_by_sq:
        terms_by_sq["SQ1"] = r2_terms or ["literature"]

    r1_vs_r2 = {
        "round1_terms": r1_terms,
        "round2_terms": r2_terms,
        "round1_positive_hits": len(r1_hit_rids),
        "round2_positive_hits": len(r2_hit_rids),
        "marginal_new_hits": len(marginal_new_rids),
        "marginal_new_rids": marginal_new_rids[:30],
        "score_improved_count": sum(1 for rid in r2_scores if r2_scores[rid] > r1_scores.get(rid, 0.0) + 1e-6),
    }

    # Screening + 4-tier transparent ranking + three-perspective tagging + LOO stability
    budget_guard.tick_phase(budget_file, "screening_and_ranking")
    screened, screen_stats = (
        sc.screen(
            items,
            terms_by_sq,
            S["rules"],
            exp_searcher,
            filters=plan.get("filters") or {},
            external_opinions=S.get("opinions") or [],
        )
        if exp_searcher
        else ([], {"n": 0, "include": 0, "borderline": 0, "exclude": 0})
    )
    rank_stab = (
        sc.rank_stability(items, r2_terms or r1_terms, exp_searcher, k=10)
        if exp_searcher
        else {"top_k": 10, "effective_k": 0, "mean_topk_overlap_loo": 1.0, "min_overlap": 1.0, "stability": "empty"}
    )

    items_by_rid = {it["rid"]: it for it in items}
    filters = plan.get("filters") or {}

    ranked_candidates: List[Dict[str, Any]] = []
    evidence_chains: List[Dict[str, Any]] = []
    tier_counts: Counter = Counter()
    persp_counts: Counter = Counter()

    for scr in screened:
        rid = scr["rid"]
        it = items_by_rid[rid]
        persp = annotate_perspectives(it)
        r1_sc = round(r1_scores.get(rid, 0.0), 3)
        r2_sc = round(r2_scores.get(rid, 0.0), 3)
        tier, reason_codes = assign_four_tier_label(scr, it, persp, r1_sc, r2_sc, filters)
        # Per-record grounded field evidence (research_object, condition, outcome, time_scope)
        ecard = plan.get("element_card", {})
        rec_toks = set(qm.stem_tokens(qm.tokenize(f"{it.get('title', '')} {it.get('abstract', '')}")))
        obj_terms = qm.stem_tokens(qm.tokenize(str(ecard.get("research_object") or "")))
        out_terms = qm.stem_tokens(qm.tokenize(str(ecard.get("mechanism_or_outcome") or "")))
        obj_hits = sorted(set(obj_terms) & rec_toks)
        out_hits = sorted(set(out_terms) & rec_toks)
        yr_val = it.get("year") or 0
        yr_min = filters.get("year_min")
        time_ok = True if (not yr_min or (yr_val and yr_val >= yr_min)) else False
        field_ev = {
            "research_object_hits": obj_hits[:6],
            "outcome_or_condition_hits": out_hits[:6],
            "time_scope_satisfied": time_ok,
            "has_abstract": bool((it.get("abstract") or "").strip()),
            "snippet": str(it.get("abstract") or it.get("title") or "")[:220],
        }
        # Downgrade core candidate lacking research_object grounding when object terms exist
        if tier == "core" and obj_terms and not obj_hits:
            tier = "method-only" if out_hits else "background"
            reason_codes = list(reason_codes) + ["DOWNGRADED_MISSING_OBJECT_GROUNDING"]
        tier_counts[tier] += 1
        for p in persp["perspectives"]:
            persp_counts[p] += 1

        first_hit_round = 1 if rid in r1_hit_rids else (2 if rid in r2_hit_rids else 0)
        alt_dois = [d for d in (it.get("all_ids", {}).get("alt_doi") or []) if d != it.get("doi")]
        for d in it.get("all_ids", {}).get("doi") or []:
            if d and d != it.get("doi") and d not in alt_dois:
                alt_dois.append(d)

        cand = {
            "rank": 0,  # filled after final deterministic sort
            "tier": tier,
            "rid": rid,
            "title": it.get("title", ""),
            "authors": (it.get("authors") or [])[:10],
            "year": it.get("year") or None,
            "venue": it.get("venue", ""),
            "doi": it.get("doi", ""),
            "alt_dois": alt_dois,
            "arxiv": it.get("arxiv", ""),
            "pmid": it.get("pmid", ""),
            "sources": it.get("sources", []),
            "version": it.get("version", ""),
            "score": round(float(scr.get("score", 0.0)), 3),
            "round1_score": r1_sc,
            "round2_score": r2_sc,
            "first_hit_round": first_hit_round,
            "abstract_score": scr.get("abstract_score", 0.0),
            "title_score": scr.get("title_score", 0.0),
            "field_evidence": field_ev,
            "perspectives": persp["perspectives"],
            "gap_precedent_roles": persp["gap_precedent_roles"],
            "code_or_data_cue": persp["code_or_data_cue"],
            "confidence": scr.get("confidence", "medium"),
            "n_merged_records": it.get("n_members", 1),
            "reason_codes": reason_codes,
            "source_locators": it.get("paths", []),
        }
        ranked_candidates.append(cand)

        ft_hits = pl.fulltext_hits(S, rid, r2_terms) if it.get("docs") else []
        evidence_chains.append(
            {
                "rid": rid,
                "tier": tier,
                "title": it.get("title", ""),
                "doi": it.get("doi", ""),
                "first_hit_round": first_hit_round,
                "round1_score": r1_sc,
                "round2_score": r2_sc,
                "per_sub_question_scores": scr.get("per_sub_question", {}),
                "matched_by_field": (scr.get("explain") or {}).get("matched_by_field", {}),
                "snippet": scr.get("snippet", ""),
                "perspective_cues": persp["perspective_cues"],
                "gap_precedent_roles": persp["gap_precedent_roles"],
                "code_or_data_matches": persp["code_or_data_matches"],
                "reason_codes": reason_codes,
                "arbitration": scr.get("arbitration", ""),
                "field_provenance": it.get("field_provenance", {}),
                "conflicts": it.get("conflicts", []),
                "version_group": it.get("version_group", {}),
                "record_refs": it.get("record_refs", []),
                "fulltext_hits": ft_hits,
            }
        )

    # Deterministic final ranking: tier -> score -> abstract_score -> version -> year -> rid
    ranked_candidates.sort(
        key=lambda c: (
            TIER_ORDER.get(c["tier"], 4),
            -c["score"],
            -c["abstract_score"],
            -sc.VERSION_AUTHORITY.get(c.get("version", ""), 0),
            -(c.get("year") or 0),
            c["rid"],
        )
    )
    rank_by_rid: Dict[str, int] = {}
    for idx, c in enumerate(ranked_candidates, 1):
        c["rank"] = idx
        rank_by_rid[c["rid"]] = idx
    for ec in evidence_chains:
        ec["rank"] = rank_by_rid.get(ec["rid"], 0)
    evidence_chains.sort(key=lambda e: e["rank"])

    # Citation / reference graph summary (retained offline graph only)
    cg_summary = {"node_count": len(clusters), "edge_count": 0, "seed_count": 0}
    try:
        G = gr.build_graph(clusters)
        cg_summary = {
            "node_count": len(G.nodes),
            "edge_count": len(G.edges),
            "internal_citation_edges": sum(1 for e in G.edges if e.get("internal", True)),
        }
    except Exception:
        pass

    version_pairs = [c.version_group() for c in clusters if c.version_group().get("preprint_and_published")]
    prisma = sc.prisma_counts(
        records_n=dedup_rep["n_records"],
        duplicates_n=dedup_rep["n_merged_away"],
        screened_n=dedup_rep["n_unique"],
        excluded_n=tier_counts.get("excluded", 0),
        included_n=tier_counts.get("core", 0),
        borderline_n=tier_counts.get("method-only", 0) + tier_counts.get("background", 0),
    )

    stopping = qm.compute_stopping_verdict(
        S["coverage"],
        marginal_new_hits=r1_vs_r2["marginal_new_hits"],
        source_load_errors=source_load_errors,
        pagination_issues=pagination_issues,
        has_conflicts=bool(ambiguous or any(c.conflicts for c in clusters)),
    )

    # Write detailed artifacts
    csv_rows = [
        {
            "rank": c["rank"],
            "tier": c["tier"],
            "rid": c["rid"],
            "title": c["title"],
            "authors": "; ".join(c["authors"]),
            "year": c["year"] or "",
            "venue": c["venue"],
            "doi": c["doi"],
            "alt_dois": "; ".join(c["alt_dois"]),
            "arxiv": c["arxiv"],
            "sources": "; ".join(c["sources"]),
            "version": c["version"],
            "score": c["score"],
            "round1_score": c["round1_score"],
            "round2_score": c["round2_score"],
            "first_hit_round": c["first_hit_round"],
            "abstract_score": c["abstract_score"],
            "perspectives": "; ".join(c["perspectives"]),
            "gap_precedent_roles": "; ".join(c["gap_precedent_roles"]),
            "code_or_data_cue": c["code_or_data_cue"],
            "reason_codes": "; ".join(c["reason_codes"]),
            "source_locators": "; ".join(c["source_locators"]),
        }
        for c in ranked_candidates
    ]
    builder.write_csv("results_ranked.csv", csv_rows)
    builder.write_json(
        "evidence_chains.json",
        {
            "case_id": case_id,
            "n_candidates": len(evidence_chains),
            "rollback_log": rollback_log,
            "ambiguous_candidates": ambiguous,
            "version_pairs": version_pairs,
            "chains": evidence_chains,
        },
    )
    builder.write_json(
        "query_log.json",
        {
            "case_id": case_id,
            "question": S["question"],
            "element_card": plan.get("element_card", {}),
            "filters": filters,
            "sub_questions": plan.get("sub_questions", []),
            "query_rounds": queries_plan.get("rounds", []),
            "expansion_evidence": {
                "abbreviations_mined": abbrevs[:30],
                "unexpanded_acronyms": unexpanded[:20],
                "cooccurrence_terms": S.get("co_terms", [])[:15],
            },
            "round1_vs_round2": r1_vs_r2,
            "per_sub_question_marginal_gain": marginal_by_sq,
            "term_gap_diagnosis": term_diag,
            "stopping_verdict": stopping,
        },
    )

    report_md = build_markdown_report(
        case_id=case_id,
        question=S["question"],
        plan=plan,
        r1_vs_r2=r1_vs_r2,
        marginal_by_sq=marginal_by_sq,
        term_diag=term_diag,
        ranked_rows=csv_rows,
        tier_counts=dict(tier_counts),
        persp_counts=dict(persp_counts),
        dedup_rep=dedup_rep,
        ambiguous=ambiguous,
        version_pairs=version_pairs,
        rank_stab=rank_stab,
        source_load_errors=source_load_errors,
        pagination_issues=pagination_issues,
        valid_empty_sources=valid_empty_sources,
        stopping=stopping,
    )

    # Assemble structured gaps for honest status & M5/M6 compliance
    gaps: List[Dict[str, Any]] = []
    for err in source_load_errors:
        gaps.append(
            {
                "code": "SOURCE_MALFORMED_OR_UNSUPPORTED_JSON",
                "severity": "error",
                "source": err.get("file", "unknown"),
                "message": f"Failed to parse {err.get('file')}: {err.get('error_type')} ({err.get('detail', '')[:120]}). Not treated as zero hits.",
            }
        )
    for pi in pagination_issues:
        gaps.append(
            {
                "code": "SOURCE_PAGINATION_OR_CURSOR_ERROR",
                "severity": "error",
                "source": pi.get("file", "unknown"),
                "message": f"Pagination/API error in {pi.get('file')} ({pi.get('source')}, status={pi.get('status')}): {pi.get('error', '')[:140]}. Not treated as zero hits.",
            }
        )
    if ambiguous:
        gaps.append(
            {
                "code": "AMBIGUOUS_DISTINCT_FORMAL_DOI_QUEUE",
                "severity": "warning",
                "source": "search_core.dedup",
                "message": f"{len(ambiguous)} same-title distinct-formal-DOI pair(s) kept separate for human arbitration.",
            }
        )
    zero_meta_terms = [t["term"] for t in term_diag if t["diagnosis"] == "corpus_absent"]
    if zero_meta_terms:
        gaps.append(
            {
                "code": "QUERY_TERMS_ABSENT_FROM_SNAPSHOT",
                "severity": "info",
                "source": "diagnose_term_postings",
                "message": f"Term(s) absent from finite snapshot metadata/fulltext: {zero_meta_terms[:10]}. Absence reflects snapshot boundary, not non-existence.",
            }
        )

    status_report = pl.status_block(
        builder,
        S,
        extra={
            "valid_empty_sources": valid_empty_sources,
            "completed": [
                "source_health_tri_triage",
                "element_card_and_query_plan",
                "round1_core_retrieval",
                "term_postings_zero_hit_diagnosis",
                "round2_controlled_expansion",
                "traceable_deduplication_with_rollback_log",
                "three_perspective_annotation",
                "four_tier_ranking_and_loo_stability",
            ],
            "not_completed": [
                "live_remote_database_querying (offline sandbox boundary)",
                "fulltext_reading_for_records_without_local_pdf (metadata-only boundary)",
            ],
            "uncertainty": [
                "Finite organizer-authorized snapshot only; cannot claim global literature completeness or latest post-snapshot publications.",
                f"Ranking stability (LOO top-{rank_stab.get('effective_k')}) = {rank_stab.get('mean_topk_overlap_loo')} ({rank_stab.get('stability')}).",
            ],
            "rollback_log_count": len(rollback_log),
            "source_count_conserved": dedup_rep["source_count_conserved"],
        },
    )

    ranking_quality = compute_ranking_quality_metrics(ranked_candidates, bundle)
    metrics = {
        "raw_record_count": dedup_rep["n_records"],
        "canonical_record_count": dedup_rep["n_unique"],
        "dedup_merged_count": dedup_rep["n_merged_away"],
        "source_count_conserved": 1 if dedup_rep["source_count_conserved"] else 0,
        "ambiguous_candidates_count": len(ambiguous),
        "rollback_log_count": len(rollback_log),
        "version_pairs_count": len(version_pairs),
        "source_load_error_count": len(source_load_errors),
        "pagination_issue_count": len(pagination_issues),
        "valid_empty_source_count": len(valid_empty_sources),
        "round1_positive_hits": r1_vs_r2["round1_positive_hits"],
        "round2_positive_hits": r1_vs_r2["round2_positive_hits"],
        "round2_marginal_new_hits": r1_vs_r2["marginal_new_hits"],
        "tier_core_count": tier_counts.get("core", 0),
        "tier_method_only_count": tier_counts.get("method-only", 0),
        "tier_background_count": tier_counts.get("background", 0),
        "tier_excluded_count": tier_counts.get("excluded", 0),
        "loo_topk_overlap": rank_stab.get("mean_topk_overlap_loo", 1.0),
        "citation_edge_count": cg_summary.get("edge_count", 0),
        "metadata_completeness_rate": ranking_quality["metadata_completeness_rate"],
        "gold_eval_status": ranking_quality["gold_eval_status"],
        **({k: v for k, v in ranking_quality.items() if k.startswith(("precision_at_", "recall_at_", "ndcg_at_"))})
    }
    metric_provenance = {
        "raw_record_count": "evidence_chains.json:n_candidates + rollback_log (source_count_conserved)",
        "canonical_record_count": "results_ranked.csv:row_count",
        "dedup_merged_count": "evidence_chains.json:rollback_log",
        "source_count_conserved": "result.json:evidence.deduplication_summary.source_count_conserved",
        "ambiguous_candidates_count": "evidence_chains.json:ambiguous_candidates",
        "rollback_log_count": "evidence_chains.json:rollback_log",
        "version_pairs_count": "evidence_chains.json:version_pairs",
        "source_load_error_count": "run_manifest.json:source_load_errors",
        "pagination_issue_count": "result.json:status_report.pagination_issues",
        "valid_empty_source_count": "result.json:status_report.valid_empty_sources",
        "round1_positive_hits": "query_log.json:round1_vs_round2.round1_positive_hits",
        "round2_positive_hits": "query_log.json:round1_vs_round2.round2_positive_hits",
        "round2_marginal_new_hits": "query_log.json:round1_vs_round2.marginal_new_hits",
        "tier_core_count": "results_ranked.csv:tier==core",
        "tier_method_only_count": "results_ranked.csv:tier==method-only",
        "tier_background_count": "results_ranked.csv:tier==background",
        "tier_excluded_count": "results_ranked.csv:tier==excluded",
        "loo_topk_overlap": "result.json:results.rank_stability.mean_topk_overlap_loo",
        "citation_edge_count": "result.json:evidence.citation_graph_summary.edge_count",
        "metadata_completeness_rate": "results_ranked.csv:5_field_completeness",
        "gold_eval_status": "result.json:metrics.gold_eval_status",
    }

    findings = [
        f"Loaded {dedup_rep['n_records']} raw records across {len(dedup_rep['per_source_raw_counts'])} source(s) into {dedup_rep['n_unique']} unique canonical works (merged={dedup_rep['n_merged_away']}, conserved={dedup_rep['source_count_conserved']}).",
        f"Two-round retrieval expanded hits from {r1_vs_r2['round1_positive_hits']} (Round 1 core) to {r1_vs_r2['round2_positive_hits']} (Round 2 controlled expansion, +{r1_vs_r2['marginal_new_hits']} new); LOO top-{rank_stab.get('effective_k')} stability={rank_stab.get('mean_topk_overlap_loo')} ({rank_stab.get('stability')}).",
        f"4-tier screening yielded core={tier_counts.get('core', 0)}, method-only={tier_counts.get('method-only', 0)}, background={tier_counts.get('background', 0)}, excluded={tier_counts.get('excluded', 0)}.",
        f"Tri-triage flagged {len(source_load_errors)} malformed JSON error(s), {len(pagination_issues)} pagination/API error(s), {len(valid_empty_sources)} valid empty array(s), and kept {len(ambiguous)} same-title distinct-formal-DOI pair(s) separate.",
    ]

    # Write audit_log, run_manifest, report.md via ResultBuilder
    builder.log(
        "ranking",
        "completed",
        n_canonical=len(ranked_candidates),
        tiers=dict(tier_counts),
        source_load_errors=len(source_load_errors),
        pagination_issues=len(pagination_issues),
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
            "source_load_errors": source_load_errors,
            "pagination_issues": pagination_issues,
            "valid_empty_sources": valid_empty_sources,
            "notes": bundle.notes,
        },
    )
    builder.write_markdown("report.md", report_md)

    artifact_list = ["result.json"] + [a["path"] for a in builder.artifacts]
    patch = {
        "skill": SKILL_NAME,
        "artifacts": artifact_list,
        "results": {
            "ranked_candidates": ranked_candidates,
            "tier_counts": dict(tier_counts),
            "perspective_counts": dict(persp_counts),
            "screening_stats": screen_stats,
            "rank_stability": rank_stab,
            "prisma_conservation": prisma,
        },
        "evidence": {
            "element_card": plan.get("element_card", {}),
            "sub_questions": plan.get("sub_questions", []),
            "round1_vs_round2": r1_vs_r2,
            "per_sub_question_marginal_gain": marginal_by_sq,
            "term_gap_diagnosis": term_diag,
            "abbreviations_mined": abbrevs[:30],
            "cooccurrence_terms": S.get("co_terms", [])[:15],
            "stopping_verdict": stopping,
            "deduplication_summary": dedup_rep,
            "ambiguous_candidates": ambiguous,
            "rollback_log": rollback_log,
            "version_pairs": version_pairs,
            "citation_graph_summary": cg_summary,
            "evidence_chains_file": "evidence_chains.json",
            "query_log_file": "query_log.json",
        },
        "status_report": status_report,
        "source_load_errors": source_load_errors,
        "pagination_issues": pagination_issues,
        "valid_empty_sources": valid_empty_sources,
        "ambiguous_candidates": ambiguous,
        "rollback_log": rollback_log,
        "source_count_conserved": dedup_rep["source_count_conserved"],
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

    # M5: Grounding check against /input corpus
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
        "results_ranked.csv",
        "evidence_chains.json",
        "query_log.json",
        "report.md",
        "audit_log.json",
        "run_manifest.json",
    ]
    audit = final_audit.audit_deliverables(output_dir, result_file="result.json", required_files=required_files)
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor challenge-a2909a72 Academic Literature Search Pipeline.")
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
