#!/usr/bin/env python3
"""Temporal Trend Slicer, Terminology-Drift Normalizer, Stage-Balanced Representative Selector,
and Evidence-Backed Research Gap Analyzer (v4).

Key design guarantees:
1. Full multi-source ingestion (OpenAlex, Crossref, EuropePMC JSON/XML, arXiv Atom XML, PDF, BibTeX, CSV, MD)
   + preprint-to-VoR deduplication BEFORE counting so a study never double-counts across preprint and journal years.
2. Strict date-basis disclosure (`published`, `online_first`, `preprint`, `unknown`) + dual-timeline sensitivity
   (VoR-preferred primary timeline vs. preprint-first-appearance timeline) + API pagination truncation detection.
3. Source-bias-free topic evolution using ONLY universal `title + abstract` text (never OpenAlex-only concepts),
   stratified candidate selection (core + rising/emerging + declining), TF-IDF-weighted `CONVERGING_MERGING`,
   and genuine `SPLITTING_SPECIALIZING` branching detection.
4. Domain-agnostic 4-role stage-balanced representative selector (foundational origin, long-tail methodological
   pioneer detected via downstream bigram/method adoption, phase-transition bridge, recent frontier anchor).
5. Strict read-only `--verify-report` auditing overclaims, cited record IDs, yearly count accuracy, and
   date-basis/pagination disclosure without overwriting report deliverables.
"""

from __future__ import annotations

import argparse
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
    build_tfidf_vectors,
    detect_overclaims,
    extract_directional_passages,
    extract_general_methods_and_objects,
    is_fulltext_access,
    is_non_evidence_access,
    tokenize_terms,
)
from prisma_flow_auditor import deduplicate_bibliography, summarize_identification_by_source

ACRONYM_DEF_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9 -]{6,60}?)\s*\(([A-Z][A-Za-z0-9-]{1,10})\)")
REPORT_CITE_RE = re.compile(r"\[([A-Za-z0-9_.:/-]+)\]|\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+|PMC\d{5,9}|arXiv:\d{4}\.\d{4,5})\b", re.I)
YEAR_ROW_RE = re.compile(r"^\|\s*(\d{4})\s*\|\s*(\d+)\s*\|")
IGNORE_BRACKET_TAGS = {
    "supported", "partial", "refuted", "outdated", "unknown", "emerging_frontier", "rising_expansion",
    "sustained_core", "declining_or_matured", "declining_or_shifted", "single_window_active",
    "converging_merging", "splitting_specializing",
}


def detect_corpus_aliases(records: list[dict[str, Any]], user_aliases: dict[str, str] | None = None) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Combine explicit user aliases with in-corpus acronym-expansion mappings to prevent terminology-drift illusions."""
    alias_map: dict[str, str] = {}
    merges: list[dict[str, Any]] = []
    for k, v in (user_aliases or {}).items():
        lk, lv = k.strip().lower(), v.strip().lower()
        if lk and lv and lk != lv:
            alias_map[lk] = lv
            merges.append({"alias": lk, "canonical": lv, "source": "user_aliases_json"})

    candidate_pairs: Counter[tuple[str, str]] = Counter()
    for r in records:
        blob = f"{r.get('title', '')} {r.get('abstract', '')}"
        for m in ACRONYM_DEF_RE.finditer(blob):
            phrase = " ".join(m.group(1).strip().lower().split())
            acro = m.group(2).strip().lower()
            words = [w for w in re.split(r"[\s-]+", phrase) if w]
            if 2 <= len(words) <= 6 and 2 <= len(acro) <= 8:
                initials = "".join(w[0] for w in words if w)
                if acro[0] == initials[0] and sum(c in initials for c in acro) >= max(2, len(acro) - 1):
                    candidate_pairs[(acro, phrase)] += 1

    seen_acros: set[str] = set(alias_map.keys())
    for (acro, phrase), cnt in candidate_pairs.most_common(20):
        if acro not in seen_acros and cnt >= 2:
            alias_map[acro] = phrase
            alias_map[f"{acro}s"] = phrase
            seen_acros.add(acro)
            merges.append({"alias": acro, "canonical": phrase, "source": f"auto_acronym_detection(n={cnt})"})

    return alias_map, merges


def _partition_phases(sorted_years: list[int]) -> list[dict[str, Any]]:
    if not sorted_years:
        return []
    if len(sorted_years) == 1:
        return [{"phase": "single_year_snapshot", "years": sorted_years}]
    if len(sorted_years) == 2:
        return [
            {"phase": "early_phase", "years": [sorted_years[0]]},
            {"phase": "recent_phase", "years": [sorted_years[1]]},
        ]
    n = len(sorted_years)
    c1 = max(1, n // 3)
    c2 = max(c1 + 1, (2 * n) // 3)
    return [
        {"phase": "early_phase", "years": sorted_years[:c1]},
        {"phase": "transition_phase", "years": sorted_years[c1:c2]},
        {"phase": "recent_phase", "years": sorted_years[c2:]},
    ]


def track_topic_evolution(
    dated_records: list[dict[str, Any]],
    phases: list[dict[str, Any]],
    alias_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Track canonical themes across phases using universal title+abstract text, stratified selection,
    TF-IDF-weighted CONVERGING_MERGING, and genuine SPLITTING_SPECIALIZING detection."""
    if not dated_records or not phases:
        return [], []

    year_to_phase: dict[int, str] = {}
    for ph in phases:
        for y in ph["years"]:
            year_to_phase[y] = ph["phase"]

    # Source-bias-free text: ONLY universal title + abstract (never OpenAlex-only concepts!)
    docs_tokens: list[list[str]] = []
    rec_phases: list[str] = []
    for rec in dated_records:
        ph = year_to_phase.get(int(rec["year"]), "")
        rec_phases.append(ph)
        universal_text = f"{rec.get('title', '')} {rec.get('abstract', '')}".strip()
        toks = tokenize_terms(universal_text, include_bigrams=True, aliases=alias_map)
        docs_tokens.append([t for t in toks if (" " in t or len(t) >= 5)])

    tfidf_vecs = build_tfidf_vectors(docs_tokens)
    phase_doc_counts: Counter[str] = Counter()
    term_phase_recs: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    cooccur_by_phase: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    bigram_recs_by_phase: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for rec, ph, toks, vec in zip(dated_records, rec_phases, docs_tokens, tfidf_vecs):
        if not ph:
            continue
        phase_doc_counts[ph] += 1
        doc_terms = set(toks)
        for t in doc_terms:
            term_phase_recs[t][ph].append(rec["record_id"])
            if " " in t:
                bigram_recs_by_phase[ph][t].add(rec["record_id"])
        # Pick Top-10 bigrams ranked by TF-IDF weight (NOT alphabetical order!)
        ranked_bigrams = sorted(
            (t for t in doc_terms if " " in t),
            key=lambda t: (-vec.get(t, 0.0), t),
        )[:10]
        for i in range(len(ranked_bigrams)):
            for j in range(i + 1, len(ranked_bigrams)):
                pair = tuple(sorted((ranked_bigrams[i], ranked_bigrams[j])))
                cooccur_by_phase[ph][pair] += 1

    phase_names = [p["phase"] for p in phases]
    first_ph = phase_names[0]
    last_ph = phase_names[-1]

    # Compute phase-normalized statistics for every candidate term with DF >= 2
    scored_terms: list[dict[str, Any]] = []
    for term, pmap in term_phase_recs.items():
        total_docs = sum(len(v) for v in pmap.values())
        if total_docs < 2:
            continue
        early_n = len(pmap.get(first_ph, []))
        recent_n = len(pmap.get(last_ph, []))
        early_share = round(early_n / max(1, phase_doc_counts[first_ph]), 4)
        recent_share = round(recent_n / max(1, phase_doc_counts[last_ph]), 4)
        share_delta = round(recent_share - early_share, 4)

        if len(phase_names) == 1:
            traj = "SINGLE_WINDOW_ACTIVE"
        elif early_n == 0 and recent_n >= 2:
            traj = "EMERGING_FRONTIER"
        elif early_n >= 2 and recent_n == 0:
            traj = "DECLINING_OR_SHIFTED"
        elif recent_n >= 2 and (share_delta >= 0.04 or recent_share >= early_share * 1.3):
            traj = "RISING_EXPANSION"
        elif early_n >= 2 and (share_delta <= -0.05 and early_share >= recent_share * 1.35):
            traj = "DECLINING_OR_MATURED"
        else:
            traj = "SUSTAINED_CORE"

        scored_terms.append({
            "canonical_theme": term,
            "total_records": total_docs,
            "trajectory": traj,
            "early_n": early_n,
            "recent_n": recent_n,
            "share_delta": share_delta,
            "phase_counts": {p: len(pmap.get(p, [])) for p in phase_names},
            "phase_shares": {p: round(len(pmap.get(p, [])) / max(1, phase_doc_counts[p]), 4) for p in phase_names},
            "example_record_ids": sorted({rid for rlist in pmap.values() for rid in rlist})[:6],
        })

    # Stratified selection: emerging/rising + sustained core + declining (prevents total-DF truncation!)
    emerging_rising = sorted(
        [x for x in scored_terms if x["trajectory"] in {"EMERGING_FRONTIER", "RISING_EXPANSION"}],
        key=lambda x: (-x["share_delta"], -x["recent_n"], -int(" " in x["canonical_theme"]), x["canonical_theme"]),
    )[:8]
    sustained = sorted(
        [x for x in scored_terms if x["trajectory"] in {"SUSTAINED_CORE", "SINGLE_WINDOW_ACTIVE"}],
        key=lambda x: (-x["total_records"], -int(" " in x["canonical_theme"]), x["canonical_theme"]),
    )[:6]
    declining = sorted(
        [x for x in scored_terms if x["trajectory"] in {"DECLINING_OR_MATURED", "DECLINING_OR_SHIFTED"}],
        key=lambda x: (x["share_delta"], -x["early_n"], x["canonical_theme"]),
    )[:4]

    selected: list[dict[str, Any]] = []
    seen_t: set[str] = set()
    for grp in (emerging_rising, sustained, declining):
        for item in grp:
            if item["canonical_theme"] not in seen_t:
                seen_t.add(item["canonical_theme"])
                selected.append({k: v for k, v in item.items() if k not in {"early_n", "recent_n"}})

    structural_shifts: list[dict[str, Any]] = []
    if len(phase_names) >= 2:
        # 1. CONVERGING_MERGING: TF-IDF top bigrams absent together early, co-occurring >=2 in recent phase
        for pair, r_cnt in cooccur_by_phase[last_ph].most_common(20):
            e_cnt = cooccur_by_phase[first_ph].get(pair, 0)
            # Exclude trivial word-overlap pairs like ("graph neural", "neural network")
            if set(pair[0].split()) & set(pair[1].split()):
                continue
            if r_cnt >= 2 and e_cnt == 0:
                structural_shifts.append({
                    "shift_type": "CONVERGING_MERGING",
                    "terms": list(pair),
                    "early_cooccurrence": e_cnt,
                    "recent_cooccurrence": r_cnt,
                    "basis": f"TF-IDF distinctive themes '{pair[0]}' and '{pair[1]}' began co-occurring in {r_cnt} recent records after 0 co-occurrences in {first_ph}.",
                })
                if len(structural_shifts) >= 4:
                    break

        # 2. SPLITTING_SPECIALIZING: an early core unigram branches into >=2 distinct specialized recent bigrams with low mutual overlap
        early_unigrams = [
            t for t, pmap in term_phase_recs.items()
            if " " not in t and len(t) >= 5 and len(pmap.get(first_ph, [])) >= 2
        ]
        early_unigrams.sort(key=lambda t: -len(term_phase_recs[t][first_ph]))
        for parent in early_unigrams[:15]:
            recent_subs = [
                (bg, rset)
                for bg, rset in bigram_recs_by_phase[last_ph].items()
                if parent in bg.split() and len(rset) >= 2 and len(bigram_recs_by_phase[first_ph].get(bg, set())) == 0
            ]
            recent_subs.sort(key=lambda x: (-len(x[1]), x[0]))
            if len(recent_subs) >= 2:
                # Find 2 sub-themes with low document overlap (distinct branches)
                chosen_pair = None
                for i in range(len(recent_subs)):
                    for j in range(i + 1, len(recent_subs)):
                        b1, s1 = recent_subs[i]
                        b2, s2 = recent_subs[j]
                        if len(s1 & s2) <= 1:
                            chosen_pair = (b1, s1, b2, s2)
                            break
                    if chosen_pair:
                        break
                if chosen_pair:
                    b1, s1, b2, s2 = chosen_pair
                    structural_shifts.append({
                        "shift_type": "SPLITTING_SPECIALIZING",
                        "parent_term": parent,
                        "specialized_branches": [
                            {"branch": b1, "recent_records": sorted(s1)[:5]},
                            {"branch": b2, "recent_records": sorted(s2)[:5]},
                        ],
                        "basis": f"Early core concept '{parent}' specialized in {last_ph} into distinct sub-branches '{b1}' (n={len(s1)}) and '{b2}' (n={len(s2)}) with minimal overlap (shared={len(s1 & s2)}).",
                    })
                    if sum(1 for x in structural_shifts if x["shift_type"] == "SPLITTING_SPECIALIZING") >= 3:
                        break

    return selected, structural_shifts


def select_stage_representatives(
    dated_records: list[dict[str, Any]],
    phases: list[dict[str, Any]],
    alias_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Domain-agnostic 4-role representative selector balancing origin, long-tail adoption, bridge, and recent frontier."""
    if not dated_records:
        return []

    sorted_recs = sorted(
        dated_records,
        key=lambda r: (int(r["year"]), -(1 if is_fulltext_access(str(r.get("material_access") or "")) else 0), -len(str(r.get("abstract") or "")), r["record_id"]),
    )
    reps: list[dict[str, Any]] = []
    picked_ids: set[str] = set()

    def _add(role: str, rec: dict[str, Any], criterion: str, rationale: str) -> None:
        picked_ids.add(rec["record_id"])
        reps.append({
            "phase": role,
            "record_id": rec["record_id"],
            "title": rec.get("title") or rec["record_id"],
            "year": rec["year"],
            "date_basis": rec.get("date_basis", "unknown"),
            "citations": rec.get("citations", 0),
            "material_access": rec.get("material_access", "unknown"),
            "doi": rec.get("doi") or None,
            "selection_criterion": criterion,
            "reason": rationale,
        })

    # 1. Foundational Origin Anchor (earliest dated record with substantive title/abstract)
    origin_pool = [r for r in sorted_recs if int(r["year"]) <= int(sorted_recs[0]["year"]) + 1]
    origin = max(origin_pool, key=lambda r: (1 if not is_non_evidence_access(str(r.get("material_access") or "")) else 0, -int(r["year"]), len(str(r.get("abstract") or ""))))
    _add(
        "foundational_origin",
        origin,
        "earliest_substantive_anchor",
        f"Earliest substantive record in bounded corpus ({origin['year']}, date_basis={origin['date_basis']}); anchors baseline framing.",
    )

    # 2. Domain-Agnostic Long-Tail Methodological Pioneer:
    #    Low/moderate citations (<= median), introduces distinctive bigrams or general methods reused in >=2 LATER records!
    remaining = [r for r in sorted_recs if r["record_id"] not in picked_ids and not is_non_evidence_access(str(r.get("material_access") or ""))]
    if remaining:
        cites_sorted = sorted(int(r.get("citations") or 0) for r in remaining)
        median_cite = cites_sorted[len(cites_sorted) // 2]
        rec_bigrams = {
            r["record_id"]: {t for t in tokenize_terms(f"{r.get('title', '')} {r.get('abstract', '')}", include_bigrams=True, aliases=alias_map) if " " in t}
            for r in dated_records
        }
        pioneer_scores: list[tuple[int, int, dict[str, Any], list[str]]] = []
        for r in remaining:
            yr = int(r["year"])
            later_recs = [lr for lr in dated_records if int(lr["year"]) > yr]
            if not later_recs:
                continue
            r_bgs = rec_bigrams.get(r["record_id"], set())
            adopted = [bg for bg in r_bgs if sum(1 for lr in later_recs if bg in rec_bigrams.get(lr["record_id"], set())) >= 2]
            gen_methods = extract_general_methods_and_objects(f"{r.get('title', '')} {r.get('abstract', '')}")["methods"]
            is_long_tail = int(r.get("citations") or 0) <= max(median_cite, 25)
            score = len(adopted) * 2 + len(gen_methods) * 2 + (4 if is_long_tail else 0) + (2 if is_fulltext_access(str(r.get("material_access") or "")) else 0)
            if adopted or gen_methods:
                pioneer_scores.append((score, -yr, r, (gen_methods[:3] + sorted(adopted)[:3])[:4]))
        if pioneer_scores:
            pioneer_scores.sort(key=lambda x: (-x[0], x[1]))
            _, _, pioneer, cues = pioneer_scores[0]
            _add(
                "methodological_pioneer_long_tail",
                pioneer,
                "downstream_adopted_methods_non_top_cited",
                f"Low/moderate citation work (citations={pioneer.get('citations', 0)}) introducing methods/concepts {cues} adopted in subsequent years.",
            )

    # 3. Phase-Transition Bridge
    remaining = [r for r in sorted_recs if r["record_id"] not in picked_ids and not is_non_evidence_access(str(r.get("material_access") or ""))]
    if remaining and len(phases) >= 2:
        early_yrs = set(phases[0]["years"])
        recent_yrs = set(phases[-1]["years"])
        early_vocab = set().union(*(set(tokenize_terms(f"{r.get('title', '')} {r.get('abstract', '')}", include_bigrams=False, aliases=alias_map)) for r in dated_records if int(r["year"]) in early_yrs))
        recent_vocab = set().union(*(set(tokenize_terms(f"{r.get('title', '')} {r.get('abstract', '')}", include_bigrams=False, aliases=alias_map)) for r in dated_records if int(r["year"]) in recent_yrs))
        shared_bridge = early_vocab & recent_vocab
        if shared_bridge:
            bridge = max(
                remaining,
                key=lambda r: (
                    len(set(tokenize_terms(f"{r.get('title', '')} {r.get('abstract', '')}", include_bigrams=False, aliases=alias_map)) & shared_bridge),
                    1 if is_fulltext_access(str(r.get("material_access") or "")) else 0,
                ),
            )
            _add(
                "phase_transition_bridge",
                bridge,
                "cross_phase_vocabulary_bridge",
                f"Bridges early ({min(early_yrs)}-{max(early_yrs)}) and recent ({min(recent_yrs)}-{max(recent_yrs)}) themes with strong shared terminology.",
            )

    # 4. Recent Frontier Anchor (prefer fulltext VoR, disclose preprint transparently)
    remaining = [r for r in sorted_recs if r["record_id"] not in picked_ids and not is_non_evidence_access(str(r.get("material_access") or ""))]
    if remaining:
        recent_max_y = max(int(r["year"]) for r in remaining)
        recent_pool = [r for r in remaining if int(r["year"]) >= recent_max_y - 1]
        best_recent = max(
            recent_pool,
            key=lambda r: (
                1 if is_fulltext_access(str(r.get("material_access") or "")) else 0,
                1 if r.get("date_basis") == "published" else 0,
                int(r.get("citations") or 0),
                int(r["year"]),
            ),
        )
        _add(
            "recent_frontier_anchor",
            best_recent,
            "recent_stage_fulltext_and_vor_priority",
            f"Recent-stage anchor ({best_recent['year']}, date_basis={best_recent['date_basis']}, access={best_recent['material_access']}, citations={best_recent.get('citations', 0)}).",
        )

    return reps


def extract_evidence_backed_gaps(canonical: list[dict[str, Any]], pagination_warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive bounded research gaps from verified limitation passages, opposing conflicts, and retrieval boundaries."""
    gaps: list[dict[str, Any]] = []
    lim_rows: list[dict[str, Any]] = []
    supp_ids: list[str] = []
    opp_ids: list[str] = []

    for r in canonical:
        if is_non_evidence_access(str(r.get("material_access") or "")):
            continue
        for p in extract_directional_passages(r, max_per_record=3):
            if p.get("is_limitation"):
                lim_rows.append({"record_id": r["record_id"], "year": r.get("year"), "locator": p["locator"], "material_access": p["material_access"], "quote": p["quote"]})
            if p["direction"] == "supportive":
                supp_ids.append(r["record_id"])
            elif p["direction"] in {"opposing_or_null", "mixed"}:
                opp_ids.append(r["record_id"])

    if lim_rows:
        gaps.append({
            "gap_id": "GAP-01",
            "gap_type": "repeatedly_reported_limitation",
            "evidence_count": len(lim_rows),
            "supporting_passages": lim_rows[:6],
            "exclusion_and_scope_note": "Extracted from genuine limitation passages in inspected local sources; does not assert absence of solutions outside this bounded snapshot.",
        })
    if supp_ids and opp_ids:
        gaps.append({
            "gap_id": f"GAP-{len(gaps) + 1:02d}",
            "gap_type": "unresolved_directional_conflict",
            "supportive_records": sorted(set(supp_ids))[:6],
            "opposing_or_mixed_records": sorted(set(opp_ids))[:6],
            "exclusion_and_scope_note": "Opposing/null results coexist with positive claims within the bounded snapshot; controlled cross-condition reconciliation remains open.",
        })
    if pagination_warnings:
        gaps.append({
            "gap_id": f"GAP-{len(gaps) + 1:02d}",
            "gap_type": "denominator_pagination_boundary",
            "details": pagination_warnings,
            "exclusion_and_scope_note": "API snapshot is a paginated slice of total hits; apparent absences or sharp tail drops may reflect pagination truncation rather than true field decline.",
        })
    return gaps


def verify_trend_report(report_path: Path, rep: dict[str, Any], canonical: list[dict[str, Any]]) -> dict[str, Any]:
    """Read-only comprehensive audit of a candidate trend report (overclaims, citations, yearly counts, disclosures)."""
    txt = report_path.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []

    over = detect_overclaims(txt)
    if over:
        errors.append(f"Unbounded global absence/completeness overclaim detected: {over}")

    known_tokens: set[str] = set()
    for r in canonical:
        for tok in (r["record_id"], r.get("doi") or "", f"arxiv:{r['arxiv_id']}" if r.get("arxiv_id") else "", r.get("pmcid") or ""):
            if tok and str(tok).strip():
                known_tokens.add(str(tok).strip().lower())

    ungrounded: list[str] = []
    for m in REPORT_CITE_RE.finditer(txt):
        raw = (m.group(1) or m.group(2) or "").strip()
        tok = raw.lower()
        if not tok or tok in IGNORE_BRACKET_TAGS or tok.startswith(("gap-", "http")) or bool(re.match(r"^\d{1,3}(?:\s*[-–,]\s*\d{1,3})*$", tok)):
            continue
        # Only audit citation-like bracket tokens (alphanumeric IDs / AuthorYear / DOIs / arXiv / PMC)
        if m.group(1) and not re.match(r"^(?:[a-z]+[-_:]?\d+|10\.\d+|pmc\d+|arxiv:|w\d+|[a-z]+\d{4}|rec|src)", tok, re.I):
            continue
        if tok not in known_tokens:
            ungrounded.append(raw)
    if ungrounded:
        errors.append(f"Ungrounded record/citation identifier(s) in trend report: {sorted(set(ungrounded))}")

    # Verify yearly counts in Markdown table against computed yearly_distribution
    expected_years = {int(y["year"]): int(y["count"]) for y in rep.get("yearly_distribution") or []}
    year_mismatches: list[str] = []
    for line in txt.splitlines():
        ym = YEAR_ROW_RE.match(line.strip())
        if ym:
            yr, cnt = int(ym.group(1)), int(ym.group(2))
            if yr in expected_years and expected_years[yr] != cnt:
                year_mismatches.append(f"Year {yr}: report={cnt} vs computed={expected_years[yr]}")
    if year_mismatches:
        errors.append(f"Yearly count mismatch between report table and computed distribution: {year_mismatches}")

    # Verify date-basis disclosure when multiple bases or preprints exist
    db = rep.get("date_basis_breakdown") or {}
    if len(db) > 1 and not any(k in txt.lower() for k in ("date-basis", "date_basis", "preprint", "发表口径", "预印本")):
        errors.append("Report omits required date-basis disclosure (published vs online_first vs preprint).")
    if rep.get("pagination_warnings") and not any(k in txt.lower() for k in ("pagination", "paginated", "truncated", "分页", "截断")):
        errors.append("Corpus has API pagination warnings, but report omits denominator truncation disclosure.")

    return {
        "passed": len(errors) == 0,
        "overclaims": over,
        "ungrounded_refs": sorted(set(ungrounded)),
        "year_mismatches": year_mismatches,
        "errors": errors,
    }


def analyze_trends(
    input_dir: Path,
    aliases_path: Path | None = None,
    work_dir: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bundle = load_input_bundle(input_dir, work_dir=work_dir)
    case_id = bundle["case_id"]
    raw_records = bundle["records"]
    by_source = summarize_identification_by_source(raw_records)
    canonical, dup_log, meta_conflicts = deduplicate_bibliography(raw_records)

    if not canonical:
        return {"passed": False, "case_id": case_id, "records_total": 0, "error": f"No parseable records found in {input_dir}"}, []

    user_aliases: dict[str, str] = {}
    if aliases_path and aliases_path.is_file():
        user_aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
    elif isinstance(bundle["case_data"].get("aliases"), dict):
        user_aliases = {str(k): str(v) for k, v in bundle["case_data"]["aliases"].items()}

    alias_map, alias_merges = detect_corpus_aliases(canonical, user_aliases)

    by_year: Counter[int] = Counter()
    preprint_first_year_series: Counter[int] = Counter()
    date_basis_counts: Counter[str] = Counter()
    date_basis_by_year: dict[int, Counter[str]] = defaultdict(Counter)
    topic_by_year: dict[int, Counter[str]] = defaultdict(Counter)
    missing_year_ids: list[str] = []
    preprint_vor_upgrades = 0

    for rec in canonical:
        db = str(rec.get("date_basis") or "unknown")
        date_basis_counts[db] += 1
        if rec.get("version_upgrade"):
            preprint_vor_upgrades += 1
        yr = rec.get("year")
        if yr is None:
            missing_year_ids.append(rec["record_id"])
            continue
        yi = int(yr)
        by_year[yi] += 1
        date_basis_by_year[yi][db] += 1

        first_yr = int(rec["preprint_year"]) if rec.get("preprint_year") else yi
        preprint_first_year_series[first_yr] += 1

        # Universal title + abstract ONLY for year-topic profiling
        blob = f"{rec.get('title', '')} {rec.get('abstract', '')}"
        toks = set(tokenize_terms(blob, include_bigrams=True, aliases=alias_map))
        for t in toks:
            if " " in t or len(t) >= 5:
                topic_by_year[yi][t] += 1

    sorted_years = sorted(by_year.keys())
    total_dated = sum(by_year.values())
    yearly_series: list[dict[str, Any]] = []
    turning_point = None
    prev_cnt = None
    max_delta = 0
    for y in sorted_years:
        cnt = by_year[y]
        delta = (cnt - prev_cnt) if prev_cnt is not None else 0
        yoy_rate = round(delta / prev_cnt, 4) if (prev_cnt is not None and prev_cnt > 0) else None
        if prev_cnt is not None and abs(delta) > max_delta:
            max_delta = abs(delta)
            turning_point = y
        prev_cnt = cnt
        yearly_series.append({
            "year": y,
            "count": cnt,
            "share": round(cnt / max(1, total_dated), 4),
            "yoy_delta": delta,
            "yoy_growth_rate": yoy_rate,
            "date_basis_counts": dict(sorted(date_basis_by_year[y].items())),
            "top_terms": [w for w, _ in topic_by_year[y].most_common(6)],
        })

    sensitivity_series = [
        {"year": y, "vor_preferred_count": by_year.get(y, 0), "preprint_first_count": preprint_first_year_series[y], "delta": preprint_first_year_series[y] - by_year.get(y, 0)}
        for y in sorted(set(by_year.keys()) | set(preprint_first_year_series.keys()))
    ]

    dated_recs = [r for r in canonical if r.get("year") is not None]
    phases = _partition_phases(sorted_years)
    topic_evolution, structural_shifts = track_topic_evolution(dated_recs, phases, alias_map)
    representatives = select_stage_representatives(dated_recs, phases, alias_map)
    research_gaps = extract_evidence_backed_gaps(canonical, bundle["pagination_warnings"])

    warnings: list[str] = []
    if len(canonical) < 15:
        warnings.append(f"small_corpus_warning: Bounded corpus has only {len(canonical)} deduplicated records (<15); step changes reflect local sample fluctuations, not global bibliometric inflections.")
    if missing_year_ids:
        warnings.append(f"{len(missing_year_ids)} record(s) lack verifiable publication/preprint year metadata and were excluded from yearly distribution.")
    if date_basis_counts.get("preprint", 0) > 0:
        warnings.append(f"{date_basis_counts['preprint']} record(s) use preprint date_basis; compare VoR-preferred vs preprint-first sensitivity timelines before interpreting recent surges.")
    if bundle["pagination_warnings"]:
        warnings.append(f"{len(bundle['pagination_warnings'])} API source(s) are paginated slices of larger hit counts; trend denominators are bounded to the local snapshot.")

    rep: dict[str, Any] = {
        "case_id": case_id,
        "passed": True,
        "corpus_scope": f"bounded_local_corpus ({len(raw_records)} raw -> {len(canonical)} deduplicated, {len(dup_log)} merged including {preprint_vor_upgrades} preprint->VoR upgrades)",
        "identified_by_source": by_source,
        "block_index_summary": bundle["block_index_summary"],
        "records_raw_total": len(raw_records),
        "records_total": len(canonical),
        "duplicates_merged": len(dup_log),
        "preprint_vor_upgrades": preprint_vor_upgrades,
        "dated_records": len(dated_recs),
        "missing_year_records": missing_year_ids,
        "date_basis_breakdown": dict(sorted(date_basis_counts.items())),
        "date_basis_by_year": {str(year): dict(sorted(counts.items())) for year, counts in sorted(date_basis_by_year.items())},
        "yearly_distribution": yearly_series,
        "preprint_vs_vor_sensitivity_timeline": sensitivity_series,
        "largest_step_year": turning_point,
        "phases": phases,
        "alias_merges_applied": alias_merges,
        "topic_evolution": topic_evolution,
        "structural_shifts": structural_shifts,
        "stage_representatives": representatives,
        "research_gaps": research_gaps,
        "pagination_warnings": bundle["pagination_warnings"],
        "metadata_conflicts": meta_conflicts,
        "errors": [],
        "warnings": warnings,
    }
    return rep, canonical


def render_trend_markdown(rep: dict[str, Any]) -> str:
    lines = [
        f"# Research Frontiers & Temporal Trend Evolution ({rep.get('case_id', 'case')})",
        "",
        f"**Corpus Scope**: {rep.get('corpus_scope')}",
        f"**Date-Basis Breakdown**: `{json.dumps(rep.get('date_basis_breakdown', {}), ensure_ascii=False)}`",
        f"**Largest Step Year (Bounded Sample)**: `{rep.get('largest_step_year')}`",
        "**Denominator & Epistemic Boundary**: Counts reflect deduplicated local snapshot records only (preprint-to-VoR pairs merged prior to counting). Missing years are excluded rather than inferred from body citations; topic evolution uses universal `title + abstract` text only to avoid database-specific metadata bias.",
        "",
        "## 1. Yearly Distribution & Dual Date-Basis Sensitivity",
        "### 1.1 Primary Yearly Distribution (VoR-Preferred)",
        "| Year | Count | Share | YoY Delta | Date-Basis Split | Top Normalized Terms |",
        "|---|---|---|---|---|---|",
        *[
            f"| {y['year']} | {y['count']} | {y['share']} | {y['yoy_delta']:+d} | `{json.dumps(y['date_basis_counts'], ensure_ascii=False)}` | {', '.join(y['top_terms'][:4])} |"
            for y in rep.get("yearly_distribution", [])
        ],
        "",
        "### 1.2 Preprint-First vs. VoR-Preferred Sensitivity Timeline",
        "| Year | VoR-Preferred Count | Preprint-First Count | Shift (Preprint - VoR) |",
        "|---|---|---|---|",
        *[
            f"| {s['year']} | {s['vor_preferred_count']} | {s['preprint_first_count']} | {s['delta']:+d} |"
            for s in rep.get("preprint_vs_vor_sensitivity_timeline", [])
        ],
        "",
        "## 2. Terminology-Drift Normalizations Applied",
    ]
    merges = rep.get("alias_merges_applied") or []
    if merges:
        for m in merges:
            lines.append(f"- `{m['alias']}` -> `{m['canonical']}` ({m['source']})")
    else:
        lines.append("- No synonym/acronym drift merges triggered.")

    lines.extend(["", "## 3. Topic Evolution Across Temporal Phases (Stratified: Emerging / Core / Declining)"])
    lines.append("| Canonical Theme | Total Docs | Trajectory | Share Delta | Phase Counts | Example Records |")
    lines.append("|---|---|---|---|---|---|")
    for te in (rep.get("topic_evolution") or []):
        lines.append(
            f"| {te['canonical_theme']} | {te['total_records']} | `{te['trajectory']}` | {te.get('share_delta', 0):+.4f} | `{json.dumps(te['phase_counts'], ensure_ascii=False)}` | {', '.join(te['example_record_ids'][:3])} |"
        )
    if rep.get("structural_shifts"):
        lines.append("")
        lines.append("### Structural Evolution Signals (Merging & Splitting)")
        for sh in rep["structural_shifts"]:
            lines.append(f"- `{sh['shift_type']}`: {sh['basis']}")

    lines.extend(["", "## 4. Stage-Balanced Representative Works (Non-Citation-Only Criteria)"])
    for r in rep.get("stage_representatives") or []:
        lines.append(
            f"- **`{r['phase']}`** — [{r['record_id']}] ({r['year']}, `{r['date_basis']}`, access=`{r['material_access']}`, cites={r['citations']}): {r['title']} — *Criterion*: `{r['selection_criterion']}` ({r['reason']})"
        )

    lines.extend(["", "## 5. Evidence-Backed Research Gaps & Denominator Boundaries"])
    for g in rep.get("research_gaps") or []:
        lines.append(f"### {g['gap_id']} (`{g['gap_type']}`)")
        lines.append(f"- **Scope Boundary**: {g['exclusion_and_scope_note']}")
        for p in (g.get("supporting_passages") or [])[:4]:
            lines.append(f"  - [{p['record_id']}] (`{p['locator']}`, `{p['material_access']}`): \"{p['quote']}\"")
        if g.get("supportive_records") and g.get("opposing_or_mixed_records"):
            lines.append(f"  - Supportive: `{g['supportive_records']}` vs Opposing/Mixed: `{g['opposing_or_mixed_records']}`")
        for pw in (g.get("details") or [])[:4]:
            lines.append(f"  - Pagination: `{pw['source_file']}` loaded `{pw['loaded_records']}` of `{pw['total_hits']}` reported hits ({pw['provider']}).")
    if rep.get("warnings"):
        lines.append("")
        lines.append("### Boundary Warnings")
        for w in rep["warnings"]:
            lines.append(f"- {w}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Temporal trend & topic evolution analyzer.")
    parser.add_argument("--input", default="", help="Input directory containing multi-source literature files.")
    parser.add_argument("--output", default="", help="Output directory (writes result.json, trend_analysis.json, trend_evolution_report.md).")
    parser.add_argument("--work", default="", help="Working directory.")
    parser.add_argument("--aliases-json", default="", help="Optional JSON mapping synonym/drift terms to canonical labels.")
    parser.add_argument("--verify-report", default="", help="Read-only audit of a candidate trend Markdown report (never overwrites report or result.json).")
    parser.add_argument("--out-json", default="", help="Explicit JSON output path.")
    parser.add_argument("--out-md", default="", help="Explicit Markdown report path.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on validation errors.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    in_dir = paths["input_dir"]
    work_dir = paths["work_dir"]
    work_dir.mkdir(parents=True, exist_ok=True)

    rep, canonical = analyze_trends(
        in_dir,
        Path(args.aliases_json) if args.aliases_json else None,
        work_dir,
    )

    # STRICT READ-ONLY VERIFICATION MODE: never overwrite trend_evolution_report.md or result.json!
    if args.verify_report:
        vpath = Path(args.verify_report)
        vrep = verify_trend_report(vpath, rep, canonical)
        out_payload = {"case_id": rep.get("case_id"), "report_verified": str(vpath), **vrep}
        if args.out_json:
            result_guard.atomic_write_json(Path(args.out_json), out_payload)
        print(json.dumps(out_payload, ensure_ascii=False))
        return 0 if (vrep["passed"] or not args.strict) else 1

    out_dir = Path(args.output) if args.output else (Path(args.out_json).parent if args.out_json else paths["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    preflight_probe.probe_environment(input_dir=in_dir, output_dir=out_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="trend-evolution-pipeline")

    out_j = Path(args.out_json) if args.out_json else (out_dir / "trend_analysis.json")
    out_m = Path(args.out_md) if args.out_md else (None if args.out_json and not args.output else (out_dir / "trend_evolution_report.md"))
    result_guard.atomic_write_json(out_j, rep)
    if out_m is not None:
        result_guard.atomic_write_text(out_m, render_trend_markdown(rep))
        vcheck = verify_trend_report(out_m, rep, canonical)
        rep["report_verification"] = vcheck
        rep["passed"] = vcheck["passed"]
        rep["errors"] = vcheck["errors"]
        result_guard.atomic_write_json(out_j, rep)

    append_audit_entry(
        audit_file,
        phase="trend_analysis",
        action="trend_report_written",
        actor="trend_evolution_analyzer.py",
        output={"records_total": rep.get("records_total", 0), "years": len(rep.get("yearly_distribution", []))},
        record_id=rep.get("case_id", "unknown"),
    )

    if args.output or not args.out_json:
        result_path = out_dir / "result.json"
        result_guard.init_result(result_path, case_id=rep.get("case_id", "unknown"), task_id="research-trend-evolution")
        gaps: list[dict[str, Any]] = []
        for w in rep.get("warnings") or []:
            gaps.append({"code": "TREND_BOUNDARY_WARNING", "severity": "info", "source": "trend_evolution_analyzer", "message": w})
        for e in rep.get("errors") or []:
            gaps.append({"code": "TREND_VALIDATION_ERROR", "severity": "error", "source": "trend_evolution_analyzer", "message": e})
        req_files = [out_j.name] + ([out_m.name] if out_m is not None else [])
        patch = {
            "artifacts": ["result.json", *req_files],
            "sources": [
                {"record_id": r["record_id"], "title": r["title"], "year": r["year"], "date_basis": r["date_basis"], "role": r["phase"], "doi": r["doi"]}
                for r in (rep.get("stage_representatives") or [])
            ],
            "records_total": rep.get("records_total", 0),
            "date_basis_breakdown": rep.get("date_basis_breakdown", {}),
            "date_basis_by_year": rep.get("date_basis_by_year", {}),
            "yearly_distribution": rep.get("yearly_distribution", []),
            "preprint_vs_vor_sensitivity_timeline": rep.get("preprint_vs_vor_sensitivity_timeline", []),
            "largest_step_year": rep.get("largest_step_year"),
            "alias_merges_applied": rep.get("alias_merges_applied", []),
            "topic_evolution": rep.get("topic_evolution", []),
            "structural_shifts": rep.get("structural_shifts", []),
            "stage_representatives": rep.get("stage_representatives", []),
            "research_gaps": rep.get("research_gaps", []),
            "missing_year_records": rep.get("missing_year_records", []),
            "warnings": rep.get("warnings", []),
            "uncertainty": {
                "bounded_scope": True,
                "small_corpus": rep.get("records_total", 0) < 15,
                "missing_year_count": len(rep.get("missing_year_records") or []),
                "preprint_records_count": (rep.get("date_basis_breakdown") or {}).get("preprint", 0),
                "pagination_warnings": rep.get("pagination_warnings") or [],
            },
            "metrics": {
                "records_raw_total": rep.get("records_raw_total", 0),
                "records_total": rep.get("records_total", 0),
                "duplicates_merged": rep.get("duplicates_merged", 0),
                "preprint_vor_upgrades": rep.get("preprint_vor_upgrades", 0),
                "dated_records": rep.get("dated_records", 0),
                "year_span_count": len(rep.get("yearly_distribution") or []),
                "emerging_or_rising_theme_count": sum(1 for t in (rep.get("topic_evolution") or []) if t["trajectory"] in {"EMERGING_FRONTIER", "RISING_EXPANSION"}),
                "structural_shift_count": len(rep.get("structural_shifts") or []),
                "representative_count": len(rep.get("stage_representatives") or []),
                "research_gap_count": len(rep.get("research_gaps") or []),
            },
            "metric_provenance": {
                "records_raw_total": f"{out_j.name}:records_raw_total",
                "records_total": f"{out_j.name}:records_total",
                "duplicates_merged": f"{out_j.name}:duplicates_merged",
                "preprint_vor_upgrades": f"{out_j.name}:preprint_vor_upgrades",
                "dated_records": f"{out_j.name}:dated_records",
                "year_span_count": f"{out_j.name}:yearly_distribution",
                "emerging_or_rising_theme_count": f"{out_j.name}:topic_evolution",
                "structural_shift_count": f"{out_j.name}:structural_shifts",
                "representative_count": f"{out_j.name}:stage_representatives",
                "research_gap_count": f"{out_j.name}:research_gaps",
            },
            "findings": [
                f"Analyzed {rep.get('records_raw_total', 0)} raw -> {rep.get('records_total', 0)} deduplicated records ({rep.get('preprint_vor_upgrades', 0)} preprint->VoR merged) across {len(rep.get('yearly_distribution') or [])} year(s); date_basis={rep.get('date_basis_breakdown')}; tracked {len(rep.get('topic_evolution') or [])} stratified themes ({sum(1 for t in (rep.get('topic_evolution') or []) if t['trajectory'] in {'EMERGING_FRONTIER', 'RISING_EXPANSION'})} emerging/rising) and {len(rep.get('structural_shifts') or [])} merging/splitting shifts."
            ],
            "gaps": gaps,
        }
        res = result_guard.update_result(result_path, patch, finalize=True)
        grounding = gap_gate.check_grounding(res, [in_dir])
        if not grounding["ok"]:
            result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)
        budget_guard.tick_phase(budget_file, "final_audit")
        audit = final_audit.audit_deliverables(out_dir, result_file="result.json", required_files=req_files)
        if not audit["passed"]:
            raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")

    print(json.dumps({
        "passed": rep.get("passed"),
        "records_total": rep.get("records_total", 0),
        "years": len(rep.get("yearly_distribution", [])),
        "emerging_or_rising": sum(1 for t in (rep.get("topic_evolution") or []) if t["trajectory"] in {"EMERGING_FRONTIER", "RISING_EXPANSION"}),
        "structural_shifts": len(rep.get("structural_shifts") or []),
        "representatives": len(rep.get("stage_representatives", [])),
    }, ensure_ascii=False))
    if not rep.get("records_total"):
        return 1
    if args.strict and not rep.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
