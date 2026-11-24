"""Pure-stdlib PRISMA 2020 accounting conservation, identity-safe deduplication, and preprint->VoR merging.

Adapted and rewritten for zero third-party dependencies (stdlib difflib replacing rapidfuzz/numpy)
from Black-Lights/prisma-review-tool (MIT License, Copyright (c) 2026 Mohammad Ammar Mughees):
dedup.py & stats.py.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from evidence_state_guard import fold_unicode, normalize_doi, normalize_first_author_surname

_PREPRINT_DOI_PREFIXES = ("10.48550/", "10.1101/", "10.21203/", "10.26434/", "10.20944/")
_PREPRINT_VENUES = ("arxiv", "biorxiv", "medrxiv", "chemrxiv", "techrxiv", "ssrn", "research square", "preprints.org")
_SOURCE_PRIORITY = {"crossref": 0, "europepmc": 1, "pubmed": 2, "scopus": 3, "openalex": 4, "semantic_scholar": 5, "arxiv": 6}


def is_preprint(rec: dict[str, Any]) -> bool:
    doi = normalize_doi(str(rec.get("doi") or ""))
    if doi.startswith(_PREPRINT_DOI_PREFIXES):
        return True
    if str(rec.get("type") or "").strip().lower() in {"preprint", "posted-content"}:
        return True
    venue = str(rec.get("venue") or rec.get("journal") or "").casefold()
    source = str(rec.get("source") or "").casefold()
    return any(v in venue for v in _PREPRINT_VENUES) or (not venue and source == "arxiv")


def token_sort_ratio(a: str, b: str) -> float:
    ta = " ".join(sorted(re.findall(r"[a-z0-9\u4e00-\u9fff]+", fold_unicode(a or "").casefold())))
    tb = " ".join(sorted(re.findall(r"[a-z0-9\u4e00-\u9fff]+", fold_unicode(b or "").casefold())))
    if not ta and not tb:
        return 100.0
    if not ta or not tb:
        return 0.0
    return round(difflib.SequenceMatcher(None, ta, tb).ratio() * 100.0, 2)


def _pick_canonical(group: list[dict[str, Any]]) -> dict[str, Any]:
    def rank(r: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
        return (
            0 if is_preprint(r) else 1,
            1 if str(r.get("abstract") or "").strip() else 0,
            1 if normalize_doi(str(r.get("doi") or "")) else 0,
            1 if r.get("authors") else 0,
            -_SOURCE_PRIORITY.get(str(r.get("source") or "").lower(), 99),
            len(str(r.get("abstract") or "")),
        )
    return max(group, key=rank)


def deduplicate_bibliography(
    records: list[dict[str, Any]],
    *,
    title_threshold: float = 90.0,
    return_review_candidates: bool = False,
) -> tuple[Any, ...]:
    """Identity-safe cross-database deduplication with preprint->VoR linking and near-duplicate queue.

    Strict anti-false-merge rule:
    - Exact DOI / PMID / arXiv match merges unconditionally.
    - Two records with DIFFERENT non-empty DOIs are NEVER merged unless one is an explicit preprint
      (e.g., arXiv/bioRxiv/medRxiv) upgrading to a published Version of Record with corroborated title + first-author.
    - High title similarity without corroborated author+year (or with conflicting non-preprint DOIs)
      is NOT silently merged; it is logged into near_duplicate_candidates for human review.
    """
    clusters: list[list[dict[str, Any]]] = []
    doi_cluster: dict[str, int] = {}
    pmid_cluster: dict[str, int] = {}
    arxiv_cluster: dict[str, int] = {}
    near_duplicate_candidates: list[dict[str, Any]] = []

    for idx, raw in enumerate(records):
        rec = dict(raw)
        rec.setdefault("record_id", str(rec.get("record_id") or rec.get("id") or f"REC-{idx+1:04d}"))
        ndoi = normalize_doi(str(rec.get("doi") or ""))
        npmid = str(rec.get("pmid") or "").strip()
        narxiv = str(rec.get("arxiv_id") or "").strip().lower()
        if narxiv:
            narxiv = re.sub(r"v\d+$", "", narxiv)

        target_c: int | None = None
        reason = ""
        if ndoi and ndoi in doi_cluster:
            target_c = doi_cluster[ndoi]
            reason = "exact_doi"
        elif npmid and npmid in pmid_cluster:
            target_c = pmid_cluster[npmid]
            reason = "exact_pmid"
        elif narxiv and narxiv in arxiv_cluster:
            target_c = arxiv_cluster[narxiv]
            reason = "exact_arxiv"
        else:
            title = str(rec.get("title") or "").strip()
            yr = rec.get("year")
            fa = normalize_first_author_surname(rec)
            rec_pre = is_preprint(rec)
            for ci, grp in enumerate(clusters):
                rep = grp[0]
                rep_doi = normalize_doi(str(rep.get("doi") or ""))
                rep_pre = is_preprint(rep)
                score = token_sort_ratio(title, str(rep.get("title") or ""))
                if not title or score < 84.0:
                    continue
                rep_yr = rep.get("year")
                rep_fa = normalize_first_author_surname(rep)
                author_ok = bool(fa and rep_fa and fa == rep_fa)
                year_gap = abs(int(yr) - int(rep_yr)) if (yr is not None and rep_yr is not None) else None
                year_ok = year_gap is not None and year_gap <= 2

                # Two distinct non-preprint DOIs must NEVER auto-merge
                if ndoi and rep_doi and ndoi != rep_doi and not (rec_pre ^ rep_pre):
                    near_duplicate_candidates.append({
                        "record_id": rec["record_id"],
                        "candidate_canonical_id": rep["record_id"],
                        "title_similarity": score,
                        "reason": "distinct_non_preprint_dois_blocked",
                        "record_doi": ndoi,
                        "candidate_doi": rep_doi,
                    })
                    continue

                if score >= title_threshold:
                    if author_ok and (year_ok or year_gap is None):
                        target_c = ci
                        reason = f"preprint_vor_title_author({score})" if (rec_pre ^ rep_pre) else f"fuzzy_title_author({score})"
                        break
                    if not fa and not rep_fa and year_ok and not (ndoi and rep_doi and ndoi != rep_doi):
                        target_c = ci
                        reason = f"fuzzy_title_year_no_author({score})"
                        break
                    near_duplicate_candidates.append({
                        "record_id": rec["record_id"],
                        "candidate_canonical_id": rep["record_id"],
                        "title_similarity": score,
                        "reason": "title_similar_identity_uncorroborated",
                        "record_first_author": fa or None,
                        "candidate_first_author": rep_fa or None,
                        "record_year": yr,
                        "candidate_year": rep_yr,
                    })
                elif score >= 84.0 and author_ok and year_ok:
                    if rec_pre ^ rep_pre:
                        target_c = ci
                        reason = f"preprint_vor_borderline_title_author({score})"
                        break
                    near_duplicate_candidates.append({
                        "record_id": rec["record_id"],
                        "candidate_canonical_id": rep["record_id"],
                        "title_similarity": score,
                        "reason": "borderline_title_author_needs_human_check",
                    })

        if target_c is None:
            rec["_dedup_reason"] = "unique"
            clusters.append([rec])
            ci = len(clusters) - 1
            if ndoi:
                doi_cluster[ndoi] = ci
            if npmid:
                pmid_cluster[npmid] = ci
            if narxiv:
                arxiv_cluster[narxiv] = ci
        else:
            rec["_dedup_reason"] = reason
            clusters[target_c].append(rec)
            if ndoi:
                doi_cluster.setdefault(ndoi, target_c)
            if npmid:
                pmid_cluster.setdefault(npmid, target_c)
            if narxiv:
                arxiv_cluster.setdefault(narxiv, target_c)

    canonical_list: list[dict[str, Any]] = []
    dup_log: list[dict[str, Any]] = []
    meta_conflicts: list[dict[str, Any]] = []

    for grp in clusters:
        keeper = dict(_pick_canonical(grp))
        others = [g for g in grp if g["record_id"] != keeper["record_id"]]
        alt_dois: list[str] = []
        sources: list[str] = []
        source_ids: dict[str, str] = {}
        merged_ids: list[str] = [keeper["record_id"]]

        for g in grp:
            src = str(g.get("source") or g.get("database") or "unknown")
            if src not in sources:
                sources.append(src)
            source_ids.setdefault(src, str(g.get("source_record_id") or g["record_id"]))
            gd = normalize_doi(str(g.get("doi") or ""))
            if gd and gd not in alt_dois:
                alt_dois.append(gd)
            for ad in (g.get("alt_dois") or []):
                nad = normalize_doi(str(ad))
                if nad and nad not in alt_dois:
                    alt_dois.append(nad)

        k_doi = normalize_doi(str(keeper.get("doi") or ""))
        if is_preprint(keeper):
            published = [o for o in others if normalize_doi(str(o.get("doi") or "")) and not is_preprint(o)]
            if published:
                vor = published[0]
                keeper["doi"] = vor.get("doi")
                keeper["venue"] = vor.get("venue") or vor.get("journal") or keeper.get("venue", "")
                keeper["version_upgrade"] = "preprint_to_vor_merged"
                k_doi = normalize_doi(str(keeper["doi"] or ""))
        elif any(is_preprint(o) for o in others):
            keeper["version_upgrade"] = "vor_absorbed_preprint"

        for o in others:
            merged_ids.append(o["record_id"])
            for field in ("abstract", "authors", "year", "venue", "journal", "text"):
                if not keeper.get(field) and o.get(field):
                    keeper[field] = o[field]
                elif keeper.get(field) and o.get(field) and field in ("year",) and str(keeper[field]) != str(o[field]):
                    meta_conflicts.append({
                        "canonical_id": keeper["record_id"],
                        "duplicate_id": o["record_id"],
                        "field": field,
                        "kept_value": keeper[field],
                        "other_value": o[field],
                    })
            dup_log.append({
                "duplicate_id": o["record_id"],
                "canonical_id": keeper["record_id"],
                "match_method": o.get("_dedup_reason", "unknown"),
                "duplicate_doi": normalize_doi(str(o.get("doi") or "")) or None,
                "canonical_doi": k_doi or None,
                "is_preprint_merge": bool(is_preprint(o) or keeper.get("version_upgrade")),
                "title": str(o.get("title") or "")[:100],
                "source": o.get("source") or o.get("database") or "unknown",
                "source_file": o.get("source_file"),
            })

        keeper["alt_dois"] = [d for d in alt_dois if d != k_doi]
        keeper["found_in_sources"] = sorted(sources)
        keeper["source_ids"] = source_ids
        keeper["merged_record_ids"] = merged_ids
        keeper.pop("_dedup_reason", None)
        canonical_list.append(keeper)

    if return_review_candidates:
        return canonical_list, dup_log, meta_conflicts, near_duplicate_candidates
    return canonical_list, dup_log, meta_conflicts


def verify_prisma_conservation(
    *,
    n_identified: int,
    n_dedup: int,
    n_automation_excl: int = 0,
    n_awaiting_screen: int = 0,
    n_screened: int,
    n_screen_excl: int = 0,
    n_sought: int | None = None,
    n_not_retrieved: int = 0,
    n_assessed: int | None = None,
    n_elig_excl: int = 0,
    n_awaiting_elig: int = 0,
    n_included: int = 0,
) -> dict[str, Any]:
    """Enforce PRISMA 2020 full-stage accounting equations including full-text not_retrieved separation."""
    eq1_lhs = n_identified - n_dedup - n_automation_excl - n_awaiting_screen
    eq1_ok = eq1_lhs == n_screened

    sought_val = (n_screened - n_screen_excl) if n_sought is None else n_sought
    eq2_ok = (n_screened - n_screen_excl) == sought_val

    assessed_val = (sought_val - n_not_retrieved) if n_assessed is None else n_assessed
    eq2b_ok = (sought_val - n_not_retrieved) == assessed_val

    eq3_ok = (assessed_val - n_elig_excl - n_awaiting_elig) == n_included
    simple_closure_ok = n_identified == (n_screened + n_dedup + n_automation_excl + n_awaiting_screen)

    return {
        "balanced": eq1_ok and eq2_ok and eq2b_ok and eq3_ok and simple_closure_ok,
        "n_identified": n_identified,
        "n_dedup": n_dedup,
        "n_automation_excl": n_automation_excl,
        "n_awaiting_screen": n_awaiting_screen,
        "n_screened": n_screened,
        "n_screen_excl": n_screen_excl,
        "n_sought": sought_val,
        "n_not_retrieved": n_not_retrieved,
        "n_assessed": assessed_val,
        "n_elig_excl": n_elig_excl,
        "n_awaiting_elig": n_awaiting_elig,
        "n_included": n_included,
        "eq1_identified_to_screened_ok": eq1_ok,
        "eq2_screened_to_sought_ok": eq2_ok,
        "eq2b_sought_to_assessed_ok": eq2b_ok,
        "eq3_assessed_to_included_ok": eq3_ok,
        "discrepancy_identified_minus_expected": n_identified - (n_screened + n_dedup + n_automation_excl + n_awaiting_screen),
    }
