"""Search Coverage, Omission Attribution & Supplemental Marginal-Gain Engine (challenge-0467f5db).

Addresses all four classic coverage-audit failure modes and community failure reports:
1. Frozen independent reference-set denominator with SHA-256 fingerprint (never defined from search hits)
   + dual reporting of raw vs actionable (excluding retracted/unavailable) coverage ratios.
2. Identity-safe matching: exact DOI/PMID/arXiv authoritative; fuzzy match requires title>=88,
   both years present (|diff|<=1), AND matching normalized first-author surname. Unverified collisions
   stay unmatched with full candidate_match_evidence.
3. 7-class actionable omission attribution using Boolean concept-block parsing, field-scope check,
   date-window check, database coverage check, and explicit source failure/timeout attribution
   (never reports unreachable/timed-out source as "0 hits").
4. Per-query / per-source contribution matrix, supplemental marginal-gain curve, zero-gain spinning
   detection, targeted remediation query drafts, deterministic human verification sample, and
   dual JSON + Markdown audit reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log
from corpus_loader import load_input_bundle
from evidence_state_guard import (
    RETRACTION_CUE_RE,
    align_output_contract,
    classify_five_state_support,
    clause_term_match,
    fold_unicode,
    normalize_doi,
    normalize_first_author_surname,
)
from prisma_flow_auditor import deduplicate_bibliography, token_sort_ratio

OMISSION_CATEGORIES = (
    "retracted_or_unavailable",
    "source_unreachable_or_failed",
    "date_window_cutoff",
    "database_not_indexed",
    "field_restriction_too_narrow",
    "identifier_mismatch",
    "synonym_gap",
)

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "over", "under", "between",
    "among", "study", "studies", "analysis", "using", "based", "effect", "effects", "review",
    "systematic", "clinical", "research", "approach", "method", "methods", "results", "data",
    "not", "are", "was", "were", "have", "has", "been", "can", "may", "our", "their", "its",
}


def parse_boolean_concept_blocks(query_string: str) -> list[list[str]]:
    """Parse a Boolean query like '(A OR B) AND (C OR \"D E\")' into AND-joined OR-synonym blocks."""
    q = (query_string or "").strip()
    if not q:
        return []
    # Remove NOT clauses conservatively so negated terms are not treated as required blocks
    q = re.sub(r"\bNOT\s+(?:\([^)]*\)|\"[^\"]*\"|\S+)", " ", q, flags=re.I)
    # Split top-level AND segments (respecting parentheses)
    blocks_raw: list[str] = []
    depth = 0
    buf: list[str] = []
    tokens = re.split(r"(\(|\)|\bAND\b)", q, flags=re.I)
    for tok in tokens:
        if tok == "(":
            depth += 1
            buf.append(tok)
        elif tok == ")":
            depth = max(0, depth - 1)
            buf.append(tok)
        elif depth == 0 and tok.strip().upper() == "AND":
            seg = "".join(buf).strip()
            if seg:
                blocks_raw.append(seg)
            buf = []
        else:
            buf.append(tok)
    tail = "".join(buf).strip()
    if tail:
        blocks_raw.append(tail)

    concept_blocks: list[list[str]] = []
    for raw in blocks_raw:
        stripped = raw.strip().strip("()")
        # Strip field tags like [tiab], ti:, title:
        stripped = re.sub(r"\[[^\]]+\]", "", stripped)
        stripped = re.sub(r"\b(?:ti|ab|tiab|title|abstract|kw|mesh)\s*:", "", stripped, flags=re.I)
        or_parts = re.split(r"\bOR\b", stripped, flags=re.I)
        terms: list[str] = []
        for part in or_parts:
            cleaned = part.strip().strip("()\"'*").lower()
            cleaned = " ".join(cleaned.split())
            if len(cleaned) >= 2 and cleaned not in terms:
                terms.append(cleaned)
        if terms:
            concept_blocks.append(terms)
    return concept_blocks


def extract_candidate_keywords(rec: dict[str, Any], max_terms: int = 8) -> list[str]:
    text = fold_unicode(f"{rec.get('title', '')} {rec.get('abstract', '')[:500]}").lower()
    tokens = re.findall(r"[a-z][a-z0-9-]{3,}|[\u4e00-\u9fff]{2,6}", text)
    counts = Counter(t for t in tokens if t not in STOPWORDS and not t.isdigit())
    return [tok for tok, _ in counts.most_common(max_terms)]


def identity_candidate_evidence(
    ref: dict[str, Any],
    hit: dict[str, Any],
    score: float,
    identity_state: str,
) -> dict[str, Any]:
    authors = hit.get("authors") or []
    ref_authors = ref.get("authors") or []
    return {
        "record_id": hit.get("record_id"),
        "title": hit.get("title"),
        "year": hit.get("year"),
        "doi": hit.get("doi") or None,
        "source": hit.get("source"),
        "source_file": hit.get("source_file"),
        "query_id": hit.get("query_id"),
        "first_author": str(authors[0]) if isinstance(authors, list) and authors else None,
        "reference_first_author": str(ref_authors[0]) if isinstance(ref_authors, list) and ref_authors else None,
        "title_similarity": score,
        "identity_check": identity_state,
    }


def match_reference_against_hits(
    ref: dict[str, Any],
    hits: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
    r_doi = normalize_doi(str(ref.get("doi") or ""))
    r_alt_dois = {normalize_doi(str(d)) for d in (ref.get("alt_dois") or []) if normalize_doi(str(d))}
    r_all_dois = ({r_doi} if r_doi else set()) | r_alt_dois
    r_pmid = str(ref.get("pmid") or "").strip()
    r_arxiv = re.sub(r"v\d+$", "", str(ref.get("arxiv_id") or "").strip().lower())
    r_title = str(ref.get("title") or "").strip()
    r_year = ref.get("year")

    for h in hits:
        h_doi = normalize_doi(str(h.get("doi") or ""))
        h_alt_dois = {normalize_doi(str(d)) for d in (h.get("alt_dois") or []) if normalize_doi(str(d))}
        h_all_dois = ({h_doi} if h_doi else set()) | h_alt_dois
        if r_all_dois and h_all_dois and (r_all_dois & h_all_dois):
            return h, "exact_doi", None
        if r_pmid and str(h.get("pmid") or "").strip() == r_pmid:
            return h, "exact_pmid", None
        h_arxiv = re.sub(r"v\d+$", "", str(h.get("arxiv_id") or "").strip().lower())
        if r_arxiv and h_arxiv and h_arxiv == r_arxiv:
            return h, "exact_arxiv", None

    best_h = None
    best_sc = 0.0
    best_identity_candidate: dict[str, Any] | None = None
    best_identity_candidate_score = 0.0
    ref_author = normalize_first_author_surname(ref)

    for h in hits:
        sc = token_sort_ratio(r_title, str(h.get("title") or ""))
        h_yr = h.get("year")
        if sc >= 88.0:
            hit_author = normalize_first_author_surname(h)
            year_matches = r_year is not None and h_yr is not None and abs(int(r_year) - int(h_yr)) <= 1
            if year_matches and ref_author and hit_author and ref_author == hit_author:
                if sc > best_sc:
                    best_sc = sc
                    best_h = h
            elif sc > best_identity_candidate_score:
                identity_issues: list[str] = []
                if not ref_author or not hit_author:
                    identity_issues.append("first_author_unavailable")
                elif ref_author != hit_author:
                    identity_issues.append("first_author_mismatch")
                if r_year is None or h_yr is None:
                    identity_issues.append("publication_year_unavailable")
                elif abs(int(r_year) - int(h_yr)) > 1:
                    identity_issues.append("publication_year_mismatch")
                best_identity_candidate_score = sc
                best_identity_candidate = identity_candidate_evidence(ref, h, sc, ";".join(identity_issues))

    if best_h is not None:
        return best_h, f"fuzzy_title_author_year({best_sc})", None
    if best_identity_candidate is not None:
        return None, "title_author_identity_unverified", best_identity_candidate
    return None, "missed", None


def _collect_query_specs(case_data: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    qm = case_data.get("query_metadata")
    if isinstance(qm, dict) and qm:
        specs.append(qm)
    for key in ("queries", "baseline_queries", "query_runs"):
        val = case_data.get(key)
        if isinstance(val, list):
            for idx, item in enumerate(val, start=1):
                if isinstance(item, dict):
                    if str(item.get("phase") or item.get("role") or "").lower() == "supplemental":
                        continue
                    specs.append(item)
                elif isinstance(item, str) and item.strip():
                    specs.append({"query_id": f"Q{idx}", "query_string": item.strip()})
    return specs


def _collect_failed_sources(case_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    failed: dict[str, dict[str, Any]] = {}
    for key in ("source_failures", "source_statuses", "database_statuses"):
        items = case_data.get(key)
        if isinstance(items, dict):
            for src, info in items.items():
                if isinstance(info, dict):
                    st = str(info.get("status") or "").lower()
                    if st in {"failed", "timeout", "unreachable", "error", "auth_required", "rate_limited", "unavailable"}:
                        failed[src.lower()] = {"source": src, "status": st, "error": info.get("error") or info.get("reason") or st}
                elif isinstance(info, str) and info.lower() in {"failed", "timeout", "unreachable", "error", "unavailable"}:
                    failed[src.lower()] = {"source": src, "status": info.lower(), "error": info}
        elif isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    src = str(it.get("source") or it.get("database") or "").lower()
                    st = str(it.get("status") or "failed").lower()
                    if src and st in {"failed", "timeout", "unreachable", "error", "auth_required", "rate_limited", "unavailable"}:
                        failed[src] = {"source": src, "status": st, "error": it.get("error") or it.get("reason") or st, "query_id": it.get("query_id")}
    return failed


def attribute_omission(
    ref: dict[str, Any],
    case_data: dict[str, Any],
    query_specs: list[dict[str, Any]],
    failed_sources: dict[str, dict[str, Any]],
    identity_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically attribute WHY a reference paper was missed with query/field/source evidence."""
    title_txt = fold_unicode(str(ref.get("title") or "")).lower()
    abs_txt = fold_unicode(str(ref.get("abstract") or "")).lower()
    body_txt = fold_unicode(str(ref.get("text") or "")).lower()
    full_blob = f"{title_txt} {abs_txt} {body_txt} {str(ref.get('status') or '').lower()}"

    primary_qm = query_specs[0] if query_specs else (case_data.get("query_metadata") or {})
    window_start = primary_qm.get("year_min")
    window_end = primary_qm.get("year_max")
    queried_dbs: set[str] = set()
    field_scopes: list[str] = []
    concept_blocks: list[list[str]] = []
    flat_terms: list[str] = []

    for q in query_specs:
        for d in (q.get("databases") or q.get("sources") or []):
            queried_dbs.add(str(d).lower())
        if q.get("database"):
            queried_dbs.add(str(q["database"]).lower())
        if q.get("field_scope"):
            field_scopes.append(str(q["field_scope"]).lower())
        if q.get("terms"):
            tlist = [fold_unicode(str(t)).lower() for t in q["terms"] if str(t).strip()]
            flat_terms.extend(tlist)
            if tlist and not q.get("query_string") and not q.get("query"):
                concept_blocks.append(tlist)
        qstr = str(q.get("query_string") or q.get("query") or "")
        if qstr:
            parsed_blocks = parse_boolean_concept_blocks(qstr)
            concept_blocks.extend(parsed_blocks)
            for blk in parsed_blocks:
                flat_terms.extend(blk)

    field_scope = field_scopes[0] if field_scopes else str(primary_qm.get("field_scope") or "unspecified").lower()
    query_id = str(primary_qm.get("query_id") or "Q_baseline")
    ref_year = ref.get("year")
    ref_sources = {
        str(s).lower()
        for s in ([ref.get("source"), ref.get("indexed_in"), ref.get("database")] + (ref.get("indexed_databases") or []))
        if s and str(s).lower() not in {"reference_set", "unknown", "local_json"}
    }

    missing_blocks: list[list[str]] = []
    title_only_blocked_blocks: list[list[str]] = []
    for blk in concept_blocks:
        in_title = any(clause_term_match(t, title_txt)[0] for t in blk)
        in_full = any(clause_term_match(t, full_blob)[0] for t in blk)
        if not in_full:
            missing_blocks.append(blk)
        elif not in_title and in_full and "title" in field_scope and "abstract" not in field_scope and "all" not in field_scope:
            title_only_blocked_blocks.append(blk)

    failed_hit = next((failed_sources[s] for s in sorted(ref_sources) if s in failed_sources), None)
    if failed_hit is None and len(failed_sources) == 1 and not ref_sources:
        # Only note global source failure as secondary context unless source matches
        pass

    is_retracted = bool(RETRACTION_CUE_RE.search(full_blob)) or str(ref.get("availability") or "").lower() in {
        "retracted", "withdrawn", "unavailable", "corrupted"
    }

    if is_retracted:
        cat = "retracted_or_unavailable"
        ev = "Reference carries an explicit retraction, withdrawal, erratum, or unavailable record marker."
    elif failed_hit is not None:
        cat = "source_unreachable_or_failed"
        ev = (
            f"Source '{failed_hit['source']}' failed during retrieval (status={failed_hit['status']}: "
            f"{failed_hit['error']}); omission is due to source unavailability, not absence from database."
        )
        if failed_hit.get("query_id"):
            query_id = str(failed_hit["query_id"])
    elif identity_candidate is not None:
        cat = "identifier_mismatch"
        ev = (
            "Title-similar candidate found in retrieval hits but rejected as a confirmed hit because "
            f"author/year identity could not be corroborated ({identity_candidate['identity_check']})."
        )
    elif ref_year is not None and (
        (window_start is not None and int(ref_year) < int(window_start))
        or (window_end is not None and int(ref_year) > int(window_end))
    ):
        cat = "date_window_cutoff"
        ev = f"Publication year {ref_year} falls outside query date window [{window_start}, {window_end}]."
    elif queried_dbs and ref_sources and not (ref_sources & queried_dbs):
        cat = "database_not_indexed"
        ev = f"Reference indexed source(s) {sorted(ref_sources)} were outside queried databases {sorted(queried_dbs)}."
    elif title_only_blocked_blocks or (
        "title" in field_scope
        and "abstract" not in field_scope
        and flat_terms
        and not any(t in title_txt for t in flat_terms)
        and any(t in f"{abs_txt} {body_txt}" for t in flat_terms)
    ):
        cat = "field_restriction_too_narrow"
        ev = (
            f"Query terms match abstract/fulltext but were missed because field_scope='{field_scope}' "
            f"restricted matching to title-only."
        )
    elif missing_blocks:
        cat = "synonym_gap"
        ev = f"Missing required query concept block(s) {missing_blocks[:4]} in title/abstract; paper uses terms {extract_candidate_keywords(ref, 6)}."
    elif not normalize_doi(str(ref.get("doi") or "")) and not str(ref.get("pmid") or "").strip():
        cat = "identifier_mismatch"
        ev = "Missing normalized DOI/PMID prevented authoritative identifier alignment and no corroborated fuzzy hit existed."
    else:
        cat = "synonym_gap"
        ev = (
            f"Not retrieved under baseline query '{query_id}' (field_scope='{field_scope}'); "
            f"inspect candidate paper terms {extract_candidate_keywords(ref, 6)} or source pagination/coverage."
        )

    out: dict[str, Any] = {
        "record_id": ref.get("record_id"),
        "title": ref.get("title"),
        "doi": normalize_doi(str(ref.get("doi") or "")) or None,
        "year": ref_year,
        "reference_sources": sorted(ref_sources),
        "omission_category": cat,
        "evidence": ev,
        "query_id": query_id,
        "field_scope": field_scope or "unspecified",
        "missing_concept_blocks": missing_blocks[:5],
        "observed_paper_terms": extract_candidate_keywords(ref, 6),
        "evidence_state": classify_five_state_support(
            has_direct_support=(cat != "source_unreachable_or_failed"),
            has_opposing_evidence=False,
            missing_critical_context=False,
            is_superseded_or_retracted=(cat == "retracted_or_unavailable"),
            source_unavailable=(cat == "source_unreachable_or_failed"),
        ),
    }
    if identity_candidate is not None:
        out["candidate_match_evidence"] = identity_candidate
    if failed_hit is not None:
        out["source_failure_detail"] = failed_hit
    return out


def build_targeted_remediation(residual_omissions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate concrete, actionable remediation steps keyed by omission category (not generic advice)."""
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in residual_omissions:
        by_cat[m["omission_category"]].append(m)

    actions: list[dict[str, Any]] = []
    if by_cat.get("source_unreachable_or_failed"):
        srcs = sorted({(m.get("source_failure_detail") or {}).get("source", "unknown") for m in by_cat["source_unreachable_or_failed"]})
        actions.append({
            "omission_category": "source_unreachable_or_failed",
            "affected_count": len(by_cat["source_unreachable_or_failed"]),
            "action": f"Re-run frozen queries against failed/timed-out source(s) {srcs} once connectivity/auth is restored; do not treat prior failure as zero hits.",
        })
    if by_cat.get("field_restriction_too_narrow"):
        actions.append({
            "omission_category": "field_restriction_too_narrow",
            "affected_count": len(by_cat["field_restriction_too_narrow"]),
            "action": "Expand field_scope from title-only to title+abstract+keywords ([tiab] / TITLE-ABS-KEY) while preserving Boolean concept blocks.",
        })
    if by_cat.get("date_window_cutoff"):
        yrs = sorted({int(m["year"]) for m in by_cat["date_window_cutoff"] if m.get("year") is not None})
        actions.append({
            "omission_category": "date_window_cutoff",
            "affected_count": len(by_cat["date_window_cutoff"]),
            "action": f"Either explicitly document date-cutoff boundary exclusion for years {yrs} or widen protocol window to [{min(yrs)}, {max(yrs)}].",
        })
    if by_cat.get("database_not_indexed"):
        dbs = sorted({s for m in by_cat["database_not_indexed"] for s in (m.get("reference_sources") or [])})
        actions.append({
            "omission_category": "database_not_indexed",
            "affected_count": len(by_cat["database_not_indexed"]),
            "action": f"Add unqueried database/source(s) {dbs} or execute backward/forward citation chasing from matched seeds.",
        })
    if by_cat.get("synonym_gap"):
        cand_terms: list[str] = []
        for m in by_cat["synonym_gap"]:
            for t in (m.get("observed_paper_terms") or [])[:3]:
                if t not in cand_terms:
                    cand_terms.append(t)
        actions.append({
            "omission_category": "synonym_gap",
            "affected_count": len(by_cat["synonym_gap"]),
            "action": f"Add controlled synonym/MeSH variants observed in missed seeds ({cand_terms[:10]}) to the failing Boolean concept block(s) and re-audit net-new recovery.",
            "suggested_synonym_candidates": cand_terms[:12],
        })
    if by_cat.get("identifier_mismatch"):
        actions.append({
            "omission_category": "identifier_mismatch",
            "affected_count": len(by_cat["identifier_mismatch"]),
            "action": "Manually verify candidate_match_evidence records for author/year/DOI normalization before counting them as hits.",
        })
    if by_cat.get("retracted_or_unavailable"):
        actions.append({
            "omission_category": "retracted_or_unavailable",
            "affected_count": len(by_cat["retracted_or_unavailable"]),
            "action": "Retain in raw reference audit log as retracted/unavailable; exclude from actionable recall denominator rather than attempting supplemental search.",
        })
    return actions


def render_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        f"# Search Coverage & Omission Audit Report (`{report['case_id']}`)",
        "",
        f"> Scope: {report['scope_note']}",
        f"> Frozen Reference Set SHA-256: `{report['reference_set_sha256']}`",
        "",
        "## 1. Denominator & Coverage Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Frozen Reference Denominator (Raw) | {report['reference_denominator_count']} |",
        f"| Actionable Reference Denominator (Excl. Retracted/Unavailable) | {report['actionable_reference_count']} |",
        f"| Baseline Deduplicated Search Hits | {report['baseline_search_dedup_count']} |",
        f"| Baseline Matched References | {report['baseline_matched_count']} |",
        f"| Baseline Coverage Ratio (Raw / Actionable) | {report['baseline_coverage_ratio']} / {report['actionable_baseline_coverage_ratio']} |",
        f"| Supplemental Net-New Recoveries | {report['supplemental_net_new_hits_count']} |",
        f"| Post-Supplemental Coverage Ratio (Raw / Actionable) | {report['post_supplemental_coverage_ratio']} / {report['actionable_post_supplemental_coverage_ratio']} |",
        f"| Supplemental Marginal Net Gain | {report['supplemental_marginal_gain']} |",
        f"| Supplemental Empty Spin Detected | {report['supplemental_empty_spin_detected']} |",
        f"| Residual Omissions (Raw / Actionable) | {len(report['residual_omissions'])} / {report.get('actionable_residual_omission_count', len(report['residual_omissions']))} |",
        "",
        "## 2. Omission Breakdown by Category",
        "",
        "| Category | Baseline Count | Residual Count |",
        "| --- | --- | --- |",
    ]
    base_counts = report.get("omission_category_counts_baseline", {})
    res_counts = report.get("omission_category_counts_residual", {})
    for cat in OMISSION_CATEGORIES:
        if base_counts.get(cat, 0) or res_counts.get(cat, 0):
            lines.append(f"| `{cat}` | {base_counts.get(cat, 0)} | {res_counts.get(cat, 0)} |")

    if report.get("ingest_diagnostics"):
        lines.extend([
            "",
            "## 2b. Snapshot Input Provenance (Traceable Hit Pool)",
            "",
            "| Source Artifact | Format | Parsed Records |",
            "| --- | --- | ---: |",
        ])
        for diag in report["ingest_diagnostics"]:
            lines.append(f"| `{diag.get('file')}` | `{diag.get('format')}` | {diag.get('count', diag.get('chars', 0))} |")

    if report.get("per_source_coverage"):
        lines.extend([
            "",
            "## 3. Per-Source & Per-Query Contribution (In-Window Verified)",
            "",
            "| Source / Query | Raw Hits | Matched Seeds | Unique-Only Seeds | Coverage of Raw Ref |",
            "| --- | ---: | ---: | ---: | ---: |",
        ])
        for row in report["per_source_coverage"]:
            lines.append(
                f"| `{row['source_or_query']}` | {row.get('raw_hit_count', 0)} | {row['matched_count']} | {row['unique_matched_count']} | {row['coverage_ratio']} |"
            )

    if report.get("out_of_window_snapshot_matches"):
        lines.extend(["", "## 3b. Out-of-Window Snapshot Matches Excluded from In-Window Coverage", ""])
        for oow in report["out_of_window_snapshot_matches"]:
            lines.append(
                f"- **{oow['reference_id']}** (year={oow['year']}) matched raw snapshot `{oow['source_file']}` via `{oow['match_method']}`, "
                f"excluded from in-window query `{oow['query_id']}` ({oow['window_min']}–{oow['window_max']}) and attributed to `date_window_cutoff`."
            )

    recovered_map = {s["reference_id"]: s["query_id"] for s in report.get("supplemental_recoveries", [])}
    lines.extend(["", "## 4. Itemized Baseline Missed References & Post-Supplemental Status", ""])
    if not report["missed_references_attributed"]:
        lines.append("No baseline omissions detected against the frozen reference set.")
    else:
        for m in report["missed_references_attributed"]:
            rid = m["record_id"]
            if rid in recovered_map:
                post_tag = f"RECOVERED by `{recovered_map[rid]}`"
            elif m["omission_category"] == "retracted_or_unavailable":
                post_tag = "EXCLUDED FROM ACTIONABLE DENOMINATOR (retracted/unavailable)"
            else:
                post_tag = "RESIDUAL ACTIONABLE OMISSION"
            lines.append(
                f"- **{rid}** ({m.get('year') or 'n.d.'}) — `{m['omission_category']}` [{post_tag}] "
                f"[query=`{m['query_id']}`, field=`{m['field_scope']}`]: {m['evidence']}"
            )

    if report.get("targeted_remediation_actions"):
        lines.extend(["", "## 5. Targeted Remediation & Stopping Assessment", ""])
        for act in report["targeted_remediation_actions"]:
            lines.append(f"- `{act['omission_category']}` ({act['affected_count']}): {act['action']}")

    lines.extend([
        "",
        "## 6. Human Verification Sample",
        f"Selected {len(report.get('verification_sample', []))} priority record(s) (fuzzy matches, identity collisions, and stratified omissions) for independent human audit.",
        "",
    ])
    return "\n".join(lines)


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="0467f5db-coverage-omission")

    bundle = load_input_bundle(input_dir)
    case_id = bundle["case_id"]
    case_data = bundle["case_data"]

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-0467f5db")
    budget_guard.tick_phase(budget_file, "freeze_reference_and_measure_coverage")

    raw_refs = bundle["reference_set"]
    raw_hits = [r for r in bundle["records"] if str(r.get("source") or "").lower() != "supplemental" and str(r.get("phase") or "").lower() != "supplemental"]
    supp_hits = list(bundle.get("supplemental_hits") or []) + [
        r for r in bundle["records"] if str(r.get("source") or "").lower() == "supplemental" or str(r.get("phase") or "").lower() == "supplemental"
    ]

    frozen_refs, ref_dups, ref_conflicts, ref_near_dups = deduplicate_bibliography(raw_refs, return_review_candidates=True)
    dedup_hits, hit_dups, hit_conflicts = deduplicate_bibliography(raw_hits)
    dedup_supp, supp_dups, _ = deduplicate_bibliography(supp_hits) if supp_hits else ([], [], [])

    ref_fingerprint = hashlib.sha256(
        json.dumps(
            [{"id": r["record_id"], "doi": r.get("doi"), "title": r.get("title"), "year": r.get("year")} for r in frozen_refs],
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    query_specs = _collect_query_specs(case_data)
    if not query_specs:
        query_specs = [{"query_id": "Q_baseline", "field_scope": "unspecified"}]
    failed_sources = _collect_failed_sources(case_data)
    primary_qm = query_specs[0]
    q_year_min = primary_qm.get("year_min")
    q_year_max = primary_qm.get("year_max")
    q_primary_id = str(primary_qm.get("query_id") or "Q_baseline")

    def _has_complex_nested_boolean(qstr: str) -> bool:
        depth = 0
        max_depth = 0
        for ch in (qstr or ""):
            if ch == "(":
                depth += 1
                max_depth = max(max_depth, depth)
            elif ch == ")":
                depth = max(0, depth - 1)
        if max_depth >= 2:
            return True
        return bool(re.search(r"\b(?:NOT\s*\(|NEAR/\d+|ADJ\d*|W/\d+)\b", qstr or "", re.I))

    def _is_within_window(ref_obj: dict[str, Any], hit_obj: dict[str, Any], y_min: Any, y_max: Any) -> tuple[bool, int | None]:
        yr = ref_obj.get("year") if ref_obj.get("year") is not None else hit_obj.get("year")
        if yr is None:
            return True, None
        try:
            y_int = int(yr)
        except Exception:
            return True, None
        if y_min is not None and y_int < int(y_min):
            return False, y_int
        if y_max is not None and y_int > int(y_max):
            return False, y_int
        return True, y_int

    def _is_within_query_window(ref_obj: dict[str, Any], hit_obj: dict[str, Any]) -> tuple[bool, int | None]:
        return _is_within_window(ref_obj, hit_obj, q_year_min, q_year_max)

    # Build strict per-query (query x reference) attribution matrix — never mix multi-query concept blocks
    complex_boolean_queries: list[str] = []
    query_reference_matrix: list[dict[str, Any]] = []
    ref_in_window_hits: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ref_per_query_omissions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    out_of_window_matches: list[dict[str, Any]] = []

    for q_idx, qspec in enumerate(query_specs, 1):
        qid = str(qspec.get("query_id") or f"Q{q_idx}_baseline")
        qstr = str(qspec.get("query_string") or qspec.get("query") or "")
        q_dbs = {str(d).lower() for d in (qspec.get("databases") or qspec.get("sources") or []) if d}
        if qspec.get("database"):
            q_dbs.add(str(qspec["database"]).lower())
        y_min_q = qspec.get("year_min")
        y_max_q = qspec.get("year_max")
        f_scope_q = str(qspec.get("field_scope") or "unspecified").lower()
        is_complex_bool = _has_complex_nested_boolean(qstr)
        if is_complex_bool:
            complex_boolean_queries.append(qid)

        # Scope hits to this executed query (match query_id or, when single baseline query, all baseline hits)
        if len(query_specs) == 1:
            q_hits_pool = dedup_hits
        else:
            q_hits_pool = [
                h for h in dedup_hits
                if (str(h.get("query_id") or "") == qid)
                or (not h.get("query_id") and (not q_dbs or str(h.get("source") or "").lower() in q_dbs))
            ]

        q_status = str(qspec.get("status") or ("partial_source_failure" if failed_sources else "executed"))
        for ref in frozen_refs:
            hit, method, identity_candidate = match_reference_against_hits(ref, q_hits_pool)
            if hit is not None:
                in_win, eff_yr = _is_within_window(ref, hit, y_min_q, y_max_q)
                if in_win:
                    match_rec = {
                        "reference_id": ref["record_id"],
                        "hit_id": hit["record_id"],
                        "title": ref.get("title"),
                        "doi": ref.get("doi") or hit.get("doi") or None,
                        "match_method": method,
                        "source": hit.get("source"),
                        "found_in_sources": hit.get("found_in_sources", [hit.get("source")]),
                        "source_file": hit.get("source_file"),
                        "query_id": qid,
                        "field_scope": f_scope_q,
                        "year_window": [y_min_q, y_max_q],
                        "evidence_state": "SUPPORTED",
                    }
                    ref_in_window_hits[ref["record_id"]].append(match_rec)
                    query_reference_matrix.append({
                        "query_id": qid,
                        "reference_id": ref["record_id"],
                        "status": "matched_in_window",
                        "match_method": method,
                        "field_scope": f_scope_q,
                        "year_window": [y_min_q, y_max_q],
                        "query_status": q_status,
                        "needs_human_review": False,
                    })
                else:
                    out_of_window_matches.append({
                        "reference_id": ref["record_id"],
                        "hit_id": hit["record_id"],
                        "year": eff_yr,
                        "window_min": y_min_q,
                        "window_max": y_max_q,
                        "match_method": method,
                        "source": hit.get("source"),
                        "source_file": hit.get("source_file"),
                        "query_id": qid,
                    })
                    miss_q = attribute_omission(ref, case_data, [qspec], failed_sources, identity_candidate=identity_candidate)
                    miss_q["query_id"] = qid
                    miss_q["year_window"] = [y_min_q, y_max_q]
                    miss_q["needs_human_review"] = bool(is_complex_bool and miss_q["omission_category"] in {"synonym_gap", "field_restriction_too_narrow"})
                    miss_q["evidence"] = (
                        f"{miss_q['evidence']} (Note: raw snapshot `{hit.get('source_file')}` contained an identifier match via `{method}`, "
                        f"rejected from in-window `{qid}` [{y_min_q}–{y_max_q}] to prevent date-window inflation.)"
                    )
                    miss_q["out_of_window_snapshot_hit"] = {
                        "hit_id": hit["record_id"],
                        "source_file": hit.get("source_file"),
                        "match_method": method,
                    }
                    ref_per_query_omissions[ref["record_id"]].append(miss_q)
                    query_reference_matrix.append({
                        "query_id": qid,
                        "reference_id": ref["record_id"],
                        "status": "rejected_out_of_window",
                        "omission_category": miss_q["omission_category"],
                        "field_scope": f_scope_q,
                        "year_window": [y_min_q, y_max_q],
                        "query_status": q_status,
                        "needs_human_review": miss_q["needs_human_review"],
                    })
            else:
                miss_q = attribute_omission(ref, case_data, [qspec], failed_sources, identity_candidate=identity_candidate)
                miss_q["query_id"] = qid
                miss_q["year_window"] = [y_min_q, y_max_q]
                miss_q["needs_human_review"] = bool(is_complex_bool and miss_q["omission_category"] in {"synonym_gap", "field_restriction_too_narrow"})
                if miss_q["needs_human_review"]:
                    miss_q["evidence"] += " [Flagged needs_human_review: complex nested Boolean/proximity syntax detected in query_string.]"
                ref_per_query_omissions[ref["record_id"]].append(miss_q)
                query_reference_matrix.append({
                    "query_id": qid,
                    "reference_id": ref["record_id"],
                    "status": "missed",
                    "omission_category": miss_q["omission_category"],
                    "field_scope": f_scope_q,
                    "year_window": [y_min_q, y_max_q],
                    "query_status": q_status,
                    "needs_human_review": miss_q["needs_human_review"],
                })

    matched_items: list[dict[str, Any]] = []
    missed_items: list[dict[str, Any]] = []
    for ref in frozen_refs:
        rid = ref["record_id"]
        if ref_in_window_hits.get(rid):
            matched_items.append(ref_in_window_hits[rid][0])
        else:
            q_omissions = ref_per_query_omissions.get(rid) or []
            primary_miss = dict(q_omissions[0]) if q_omissions else attribute_omission(ref, case_data, query_specs, failed_sources)
            if len(q_omissions) > 1:
                primary_miss["per_query_attributions"] = [
                    {"query_id": x["query_id"], "omission_category": x["omission_category"], "field_scope": x["field_scope"], "year_window": x.get("year_window"), "needs_human_review": x.get("needs_human_review", False)}
                    for x in q_omissions
                ]
            missed_items.append(primary_miss)

    n_ref = len(frozen_refs)
    n_hit = len(matched_items)
    n_retracted = sum(1 for m in missed_items if m["omission_category"] == "retracted_or_unavailable")
    n_actionable = max(0, n_ref - n_retracted)

    baseline_cov = round(n_hit / n_ref, 4) if n_ref > 0 else None
    actionable_base_cov = round(n_hit / n_actionable, 4) if n_actionable > 0 else None

    # Per-source / per-query breakdown over raw hits (enforcing identical date-window rule)
    hits_by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for h in raw_hits:
        bucket = f"{h.get('source', 'unknown')}::{h.get('query_id') or q_primary_id}"
        hits_by_bucket[bucket].append(h)

    ref_matched_buckets: dict[str, set[str]] = defaultdict(set)
    bucket_matched_refs: dict[str, set[str]] = defaultdict(set)
    for bucket, bhits in hits_by_bucket.items():
        for ref in frozen_refs:
            h_match, _, _ = match_reference_against_hits(ref, bhits)
            if h_match is not None and _is_within_query_window(ref, h_match)[0]:
                bucket_matched_refs[bucket].add(ref["record_id"])
                ref_matched_buckets[ref["record_id"]].add(bucket)

    per_source_coverage: list[dict[str, Any]] = []
    for bucket in sorted(hits_by_bucket):
        mset = bucket_matched_refs[bucket]
        unique_only = [rid for rid in mset if len(ref_matched_buckets[rid]) == 1]
        per_source_coverage.append({
            "source_or_query": bucket,
            "raw_hit_count": len(hits_by_bucket[bucket]),
            "matched_count": len(mset),
            "unique_matched_count": len(unique_only),
            "coverage_ratio": round(len(mset) / n_ref, 4) if n_ref > 0 else None,
        })

    # Supplemental query marginal-gain audit (per supplemental query_id, reusing strict window + identity validation)
    supp_specs_map: dict[str, dict[str, Any]] = {}
    for item in (case_data.get("supplemental_queries") or case_data.get("queries") or []):
        if isinstance(item, dict) and item.get("query_id"):
            supp_specs_map[str(item["query_id"])] = item

    supp_by_q: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sh in dedup_supp:
        supp_by_q[str(sh.get("query_id") or sh.get("source_file") or "Q_supplemental")].append(sh)

    supp_new_hits: list[dict[str, Any]] = []
    out_of_window_supp_rejected: list[dict[str, Any]] = []
    recovered_ids: set[str] = set()
    marginal_gain_curve: list[dict[str, Any]] = []
    cum_matched = n_hit

    for sqid in sorted(supp_by_q):
        q_hits = supp_by_q[sqid]
        sq_meta = supp_specs_map.get(sqid) or {}
        sq_ymin = sq_meta.get("year_min", q_year_min)
        sq_ymax = sq_meta.get("year_max", q_year_max)
        newly_recovered_here: list[str] = []
        for miss in missed_items:
            rid = miss["record_id"]
            if rid in recovered_ids:
                continue
            ref_obj = next((r for r in frozen_refs if r["record_id"] == rid), miss)
            hit, method, identity_candidate = match_reference_against_hits(ref_obj, q_hits)
            if hit is not None:
                in_win_s, eff_yr_s = _is_within_window(ref_obj, hit, sq_ymin, sq_ymax)
                if not in_win_s:
                    out_of_window_supp_rejected.append({
                        "reference_id": rid,
                        "hit_id": hit.get("record_id") or hit.get("id"),
                        "year": eff_yr_s,
                        "window_min": sq_ymin,
                        "window_max": sq_ymax,
                        "query_id": sqid,
                        "match_method": method,
                        "reason": "supplemental_hit_outside_query_year_window",
                    })
                    continue
                recovered_ids.add(rid)
                newly_recovered_here.append(rid)
                supp_new_hits.append({
                    "reference_id": rid,
                    "hit_id": hit.get("record_id") or hit.get("id"),
                    "title": ref_obj.get("title"),
                    "prior_omission_category": miss["omission_category"],
                    "match_method": method,
                    "source": hit.get("source"),
                    "source_file": hit.get("source_file"),
                    "query_id": sqid,
                    "window_verified": [sq_ymin, sq_ymax],
                })
            elif identity_candidate is not None and "supplemental_candidate_match_evidence" not in miss:
                miss["supplemental_candidate_match_evidence"] = identity_candidate
        cum_matched += len(newly_recovered_here)
        marginal_gain_curve.append({
            "supplemental_query_id": sqid,
            "returned_dedup_records": len(q_hits),
            "net_new_reference_recoveries": len(newly_recovered_here),
            "out_of_window_rejected_count": sum(1 for x in out_of_window_supp_rejected if x["query_id"] == sqid),
            "cumulative_coverage_ratio": round(cum_matched / n_ref, 4) if n_ref > 0 else None,
            "zero_gain_spin": len(newly_recovered_here) == 0,
        })

    still_missed = [m for m in missed_items if m["record_id"] not in recovered_ids]
    post_cov = round((n_hit + len(supp_new_hits)) / n_ref, 4) if n_ref > 0 else None
    actionable_post_cov = round((n_hit + len(supp_new_hits)) / n_actionable, 4) if n_actionable > 0 else None
    net_gain = round((post_cov - baseline_cov), 4) if (post_cov is not None and baseline_cov is not None) else 0.0

    # Deterministic human verification sample (fuzzy hits + identity collisions + stratified omissions)
    verification_sample: list[dict[str, Any]] = []
    for mh in matched_items:
        if not mh["match_method"].startswith("exact_"):
            verification_sample.append({"record_id": mh["reference_id"], "sample_reason": "fuzzy_match_verification", "match_method": mh["match_method"]})
    seen_cats: set[str] = set()
    for ms in missed_items:
        if ms.get("candidate_match_evidence"):
            verification_sample.append({"record_id": ms["record_id"], "sample_reason": "identity_collision_verification", "omission_category": ms["omission_category"]})
        elif ms["omission_category"] not in seen_cats:
            seen_cats.add(ms["omission_category"])
            verification_sample.append({"record_id": ms["record_id"], "sample_reason": f"stratified_omission:{ms['omission_category']}", "omission_category": ms["omission_category"]})

    base_cat_counts = dict(Counter(m["omission_category"] for m in missed_items))
    res_cat_counts = dict(Counter(m["omission_category"] for m in still_missed))
    remediation = build_targeted_remediation(still_missed)

    report = {
        "case_id": case_id,
        "scope_note": "Finite organizer-authorized snapshot only; seed-set recall is not live remote database coverage or global completeness.",
        "reference_set_sha256": ref_fingerprint,
        "reference_denominator_count": n_ref,
        "actionable_reference_count": n_actionable,
        "retracted_or_unavailable_reference_count": n_retracted,
        "baseline_search_dedup_count": len(dedup_hits),
        "baseline_matched_count": n_hit,
        "baseline_coverage_ratio": baseline_cov,
        "actionable_baseline_coverage_ratio": actionable_base_cov,
        "omission_rate": round(1.0 - baseline_cov, 4) if baseline_cov is not None else None,
        "supplemental_net_new_hits_count": len(supp_new_hits),
        "post_supplemental_coverage_ratio": post_cov,
        "actionable_post_supplemental_coverage_ratio": actionable_post_cov,
        "supplemental_marginal_gain": net_gain,
        "supplemental_empty_spin_detected": bool(supp_hits and len(supp_new_hits) == 0),
        "omission_category_counts_baseline": base_cat_counts,
        "omission_category_counts_residual": res_cat_counts,
        "failed_sources_observed": list(failed_sources.values()),
        "per_source_coverage": per_source_coverage,
        "supplemental_marginal_gain_curve": marginal_gain_curve,
        "matched_references": matched_items,
        "query_reference_matrix": query_reference_matrix,
        "complex_boolean_queries_flagged": complex_boolean_queries,
        "out_of_window_snapshot_matches": out_of_window_matches,
        "out_of_window_supplemental_rejected": out_of_window_supp_rejected,
        "missed_references_attributed": missed_items,
        "supplemental_recoveries": supp_new_hits,
        "residual_omissions": still_missed,
        "actionable_residual_omission_count": sum(1 for m in still_missed if m["omission_category"] != "retracted_or_unavailable"),
        "targeted_remediation_actions": remediation,
        "verification_sample": verification_sample,
        "reference_dedup_removed": len(ref_dups),
        "reference_near_duplicates_flagged": ref_near_dups,
        "search_dedup_removed": len(hit_dups),
        "ingest_diagnostics": bundle.get("ingest_diagnostics", []),
    }

    report_path = output_dir / "coverage_audit_report.json"
    result_guard.atomic_write_json(report_path, report)
    md_path = output_dir / "coverage_audit_report.md"
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    append_audit_entry(
        audit_file,
        phase="coverage_audit",
        action="coverage_computed",
        actor="coverage_omission_analyzer.py",
        output={
            "ref_sha256": ref_fingerprint[:16],
            "ref_count": n_ref,
            "actionable_ref_count": n_actionable,
            "baseline_cov": baseline_cov,
            "post_cov": post_cov,
            "net_gain": net_gain,
            "failed_sources": len(failed_sources),
        },
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    if n_ref == 0:
        gaps.append({
            "code": "MISSING_REFERENCE_SET_DENOMINATOR",
            "severity": "error",
            "source": "coverage_omission_analyzer",
            "message": "No reference_set supplied; refused to fabricate coverage denominator from search results.",
        })
    if failed_sources:
        gaps.append({
            "code": "SOURCE_UNREACHABLE_OR_FAILED",
            "severity": "warning",
            "source": "coverage_omission_analyzer",
            "message": f"Retrieval snapshot includes failed/unreachable source(s): {sorted(failed_sources)}; omissions preserved explicitly.",
        })
    if supp_hits and len(supp_new_hits) == 0:
        gaps.append({
            "code": "SUPPLEMENTAL_SEARCH_ZERO_NET_GAIN",
            "severity": "warning",
            "source": "coverage_omission_analyzer",
            "message": "Supplemental search yielded 0 newly recovered reference items; stop synonym spinning and inspect residual omissions.",
        })

    patch = {
        "artifacts": ["result.json", "coverage_audit_report.json", "coverage_audit_report.md"],
        "reference_set_sha256": ref_fingerprint,
        "reference_denominator_count": n_ref,
        "actionable_reference_count": n_actionable,
        "baseline_coverage_ratio": baseline_cov,
        "actionable_baseline_coverage_ratio": actionable_base_cov,
        "post_supplemental_coverage_ratio": post_cov,
        "actionable_post_supplemental_coverage_ratio": actionable_post_cov,
        "supplemental_empty_spin_detected": report["supplemental_empty_spin_detected"],
        "missed_references_attributed": missed_items,
        "residual_omissions": still_missed,
        "targeted_remediation_actions": remediation,
        "metrics": {
            "reference_denominator_count": n_ref,
            "actionable_reference_count": n_actionable,
            "baseline_matched_count": n_hit,
            "omission_count": len(missed_items),
            "supplemental_net_new_count": len(supp_new_hits),
            "residual_omission_count": len(still_missed),
            "baseline_coverage_ratio": baseline_cov if baseline_cov is not None else 0.0,
            "post_supplemental_coverage_ratio": post_cov if post_cov is not None else 0.0,
            "supplemental_marginal_gain": net_gain,
        },
        "metric_provenance": {
            "reference_denominator_count": "coverage_audit_report.json:reference_denominator_count",
            "actionable_reference_count": "coverage_audit_report.json:actionable_reference_count",
            "baseline_matched_count": "coverage_audit_report.json:matched_references",
            "omission_count": "coverage_audit_report.json:missed_references_attributed",
            "supplemental_net_new_count": "coverage_audit_report.json:supplemental_recoveries",
            "residual_omission_count": "coverage_audit_report.json:residual_omissions",
            "baseline_coverage_ratio": "coverage_audit_report.json:baseline_coverage_ratio",
            "post_supplemental_coverage_ratio": "coverage_audit_report.json:post_supplemental_coverage_ratio",
            "supplemental_marginal_gain": "coverage_audit_report.json:supplemental_marginal_gain",
        },
        "findings": [
            f"Audited coverage against frozen {n_ref}-item reference set ({n_actionable} actionable, sha256={ref_fingerprint[:12]}): "
            f"baseline matched={n_hit} (raw={baseline_cov}, actionable={actionable_base_cov}), "
            f"supplemental net_new={len(supp_new_hits)} (spin={report['supplemental_empty_spin_detected']}), "
            f"residual_omissions={len(still_missed)}."
        ],
        "gaps": gaps,
    }
    patch, oc_gaps = align_output_contract(patch, case_data)
    if oc_gaps:
        gaps.extend(oc_gaps)
        patch["gaps"] = gaps
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["coverage_audit_report.json", "coverage_audit_report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor 0467f5db Search Coverage & Omission Analyzer.")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--work", default="")
    args = parser.parse_args(argv)
    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"])
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
