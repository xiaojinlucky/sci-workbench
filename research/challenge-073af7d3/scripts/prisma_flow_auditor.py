"""Pure-stdlib PRISMA 2020 conservation, preprint-to-VoR lineage merging, and multi-source deduplication (v3).

Key v3 upgrades:
- Recognizes EuropePMC `europepmc_preprint` / `PPR*` and bioRxiv/medRxiv/ChemRxiv/ResearchSquare DOIs as preprints.
- Compares fuzzy titles against ALL members of an existing cluster (not just grp[0]), preventing misses when a PDF
  has a noisy header title while its paired API metadata has the clean title.
- Preserves clean API titles over noisy PDF titles when merging a PDF with its arXiv/Crossref/OpenAlex record!
- Raises passage cap to 120 so long full-text PDFs/XMLs retain full-document evidence coverage.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from typing import Any

from evidence_state_guard import normalize_arxiv_id, normalize_doi

_PREPRINT_DOI_PREFIXES = ("10.48550/", "10.1101/", "10.21203/rs.", "10.26434/")
_PREPRINT_VENUES = ("arxiv", "biorxiv", "medrxiv", "chemrxiv", "techrxiv", "ssrn", "research square", "preprints.org")
_SOURCE_PRIORITY = {
    "europepmc_xml": 0,
    "crossref": 1,
    "europepmc": 2,
    "pubmed": 3,
    "openalex": 4,
    "pdf": 5,
    "bibtex": 6,
    "arxiv": 7,
    "europepmc_preprint": 8,
}
_ACCESS_RANK = {
    "fulltext_xml": 5,
    "fulltext_pdf": 5,
    "fulltext_local_text": 4,
    "captured_text": 4,
    "abstract_only": 2,
    "discovery_only": 1,
    "blocked_or_unavailable": 0,
}


def is_preprint(rec: dict[str, Any]) -> bool:
    doi = normalize_doi(str(rec.get("doi") or ""))
    if doi.startswith(_PREPRINT_DOI_PREFIXES):
        return True
    if str(rec.get("type") or "").strip().lower() in {"preprint", "posted-content"}:
        return True
    if str(rec.get("date_basis") or "").strip().lower() == "preprint":
        return True
    src = str(rec.get("source") or "").strip().lower()
    rid = str(rec.get("record_id") or "").strip().upper()
    if src in {"ppr", "europepmc_preprint"} or rid.startswith("PPR"):
        return True
    venue = str(rec.get("venue") or rec.get("journal") or "").casefold()
    return any(v in venue for v in _PREPRINT_VENUES) or (not venue and src == "arxiv")


def token_sort_ratio(a: str, b: str) -> float:
    ta = " ".join(sorted(re.findall(r"[a-z0-9\u4e00-\u9fff]+", (a or "").casefold())))
    tb = " ".join(sorted(re.findall(r"[a-z0-9\u4e00-\u9fff]+", (b or "").casefold())))
    if not ta and not tb:
        return 100.0
    if not ta or not tb:
        return 0.0
    return round(difflib.SequenceMatcher(None, ta, tb).ratio() * 100.0, 2)


def _first_author_last(rec: dict[str, Any]) -> str:
    authors = rec.get("authors") or []
    if not authors:
        return ""
    first = str(authors[0])
    parts = first.replace(",", " ").split()
    return parts[-1].lower() if parts else ""


def _pick_canonical(group: list[dict[str, Any]]) -> dict[str, Any]:
    def rank(r: dict[str, Any]) -> tuple[int, int, int, int, int, int, int]:
        return (
            _ACCESS_RANK.get(str(r.get("material_access") or ""), 0),
            0 if is_preprint(r) else 1,
            1 if normalize_doi(str(r.get("doi") or "")) else 0,
            1 if str(r.get("abstract") or "").strip() else 0,
            1 if r.get("authors") else 0,
            -_SOURCE_PRIORITY.get(str(r.get("source") or "").lower(), 99),
            len(str(r.get("text") or "")) + len(str(r.get("abstract") or "")),
        )
    return max(group, key=rank)


def _pick_cleanest_title(group: list[dict[str, Any]], default_title: str) -> str:
    """Prefer structured API/XML titles over heuristic PDF line-1 titles when merging."""
    non_pdf = [str(g.get("title") or "").strip() for g in group if str(g.get("source") or "") != "pdf" and len(str(g.get("title") or "").strip()) >= 12]
    if non_pdf:
        return max(non_pdf, key=len)
    return default_title


def deduplicate_bibliography(
    records: list[dict[str, Any]],
    *,
    title_threshold: float = 91.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Deduplicate multi-source records while preserving fulltext, preprint->VoR lineage, & conflicts."""
    clusters: list[list[dict[str, Any]]] = []
    doi_cluster: dict[str, int] = {}
    arxiv_cluster: dict[str, int] = {}
    pmcid_cluster: dict[str, int] = {}
    pmid_cluster: dict[str, int] = {}
    borderline_candidates: list[dict[str, Any]] = []

    for idx, raw in enumerate(records):
        rec = dict(raw)
        rec.setdefault("record_id", str(rec.get("record_id") or rec.get("id") or f"REC-{idx+1:04d}"))
        ndoi = normalize_doi(str(rec.get("doi") or ""))
        narxiv = normalize_arxiv_id(str(rec.get("arxiv_id") or ""))
        npmcid = str(rec.get("pmcid") or "").strip().upper()
        npmid = str(rec.get("pmid") or "").strip()

        target_c: int | None = None
        reason = ""
        if ndoi and ndoi in doi_cluster:
            target_c = doi_cluster[ndoi]
            reason = "exact_doi"
        elif narxiv and narxiv in arxiv_cluster:
            target_c = arxiv_cluster[narxiv]
            reason = "exact_arxiv"
        elif npmcid and npmcid in pmcid_cluster:
            target_c = pmcid_cluster[npmcid]
            reason = "exact_pmcid"
        elif npmid and npmid in pmid_cluster:
            target_c = pmid_cluster[npmid]
            reason = "exact_pmid"
        else:
            title = str(rec.get("title") or "").strip()
            yr = rec.get("year")
            fa = _first_author_last(rec)
            for ci, grp in enumerate(clusters):
                matched_this = False
                for rep in grp:
                    rep_doi = normalize_doi(str(rep.get("doi") or ""))
                    distinct_vor_dois = (
                        ndoi and rep_doi and ndoi != rep_doi
                        and not ndoi.startswith(_PREPRINT_DOI_PREFIXES)
                        and not rep_doi.startswith(_PREPRINT_DOI_PREFIXES)
                    )
                    score = token_sort_ratio(title, str(rep.get("title") or ""))
                    if distinct_vor_dois:
                        if score >= 88.0 and title:
                            borderline_candidates.append({
                                "record_a": rep["record_id"],
                                "record_b": rec["record_id"],
                                "doi_a": rep_doi,
                                "doi_b": ndoi,
                                "title_similarity": score,
                                "action": "kept_separate_distinct_dois",
                            })
                        continue
                    if score >= title_threshold and len(title) >= 18:
                        rep_yr = rep.get("year")
                        rep_fa = _first_author_last(rep)
                        if yr and rep_yr and abs(int(yr) - int(rep_yr)) > 3 and fa and rep_fa and fa != rep_fa:
                            borderline_candidates.append({
                                "record_a": rep["record_id"],
                                "record_b": rec["record_id"],
                                "title_similarity": score,
                                "action": "kept_separate_year_and_author_mismatch",
                            })
                            continue
                        target_c = ci
                        reason = f"fuzzy_title({score})"
                        matched_this = True
                        break
                    if score >= 85.0 and len(title) >= 18 and fa and fa == _first_author_last(rep):
                        target_c = ci
                        reason = f"title_plus_author({score})"
                        matched_this = True
                        break
                if matched_this:
                    break

        if target_c is None:
            rec["_dedup_reason"] = "unique"
            clusters.append([rec])
            ci = len(clusters) - 1
        else:
            rec["_dedup_reason"] = reason
            clusters[target_c].append(rec)
            ci = target_c

        if ndoi:
            doi_cluster.setdefault(ndoi, ci)
        if narxiv:
            arxiv_cluster.setdefault(narxiv, ci)
        if npmcid:
            pmcid_cluster.setdefault(npmcid, ci)
        if npmid:
            pmid_cluster.setdefault(npmid, ci)

    canonical_list: list[dict[str, Any]] = []
    dup_log: list[dict[str, Any]] = []
    meta_conflicts: list[dict[str, Any]] = []

    for grp in clusters:
        best = _pick_canonical(grp)
        keeper = dict(best)
        others = [g for g in grp if g is not best]
        alt_dois: list[str] = []
        sources: list[str] = []
        source_files: list[str] = []
        source_ids: dict[str, str] = {}
        merged_ids: list[str] = [keeper["record_id"]]

        pub_years = [int(g["published_year"]) for g in grp if g.get("published_year") and not is_preprint(g)]
        online_years = [int(g["online_first_year"]) for g in grp if g.get("online_first_year") and not is_preprint(g)]
        pre_years = [int(g["preprint_year"]) for g in grp if g.get("preprint_year")]

        for g in grp:
            src = str(g.get("source") or g.get("database") or "unknown")
            if src not in sources:
                sources.append(src)
            sf = str(g.get("source_file") or "")
            if sf and sf not in source_files:
                source_files.append(sf)
            source_ids.setdefault(src, str(g.get("source_record_id") or g["record_id"]))
            gd = normalize_doi(str(g.get("doi") or ""))
            if gd and gd not in alt_dois:
                alt_dois.append(gd)
            for ad in (g.get("alt_dois") or []):
                nad = normalize_doi(str(ad))
                if nad and nad not in alt_dois:
                    alt_dois.append(nad)

        has_pre = any(is_preprint(g) for g in grp)
        published_recs = [g for g in grp if not is_preprint(g) and (normalize_doi(str(g.get("doi") or "")) or g.get("published_year"))]
        if has_pre and published_recs:
            vor = published_recs[0]
            if is_preprint(keeper):
                keeper["doi"] = vor.get("doi") or keeper.get("doi")
                keeper["venue"] = vor.get("venue") or keeper.get("venue", "")
                keeper["type"] = vor.get("type") or keeper.get("type", "")
            keeper["version_upgrade"] = "preprint_to_vor_merged"

        keeper["title"] = _pick_cleanest_title(grp, str(keeper.get("title") or ""))

        all_passages = list(keeper.get("passages") or [])
        all_concepts = set(keeper.get("concepts") or [])
        max_cites = int(keeper.get("citations") or 0)
        for o in others:
            merged_ids.append(o["record_id"])
            max_cites = max(max_cites, int(o.get("citations") or 0))
            all_concepts.update(o.get("concepts") or [])
            if len(str(o.get("text") or "")) > len(str(keeper.get("text") or "")):
                keeper["text"] = o["text"]
                if _ACCESS_RANK.get(str(o.get("material_access") or ""), 0) > _ACCESS_RANK.get(str(keeper.get("material_access") or ""), 0):
                    keeper["material_access"] = o["material_access"]
            for p in (o.get("passages") or []):
                if p not in all_passages:
                    all_passages.append(p)
            for field in ("abstract", "authors", "venue", "arxiv_id", "pmid", "pmcid", "pdf_file"):
                if not keeper.get(field) and o.get(field):
                    keeper[field] = o[field]
            if keeper.get("year") and o.get("year") and str(keeper["year"]) != str(o["year"]):
                meta_conflicts.append({
                    "canonical_id": keeper["record_id"],
                    "duplicate_id": o["record_id"],
                    "field": "year",
                    "kept_value": keeper["year"],
                    "kept_date_basis": keeper.get("date_basis"),
                    "other_value": o["year"],
                    "other_date_basis": o.get("date_basis"),
                    "note": "Preprint vs published/online-first year divergence preserved in lineage." if has_pre else "Cross-source year discrepancy flagged.",
                })
            dup_log.append({
                "duplicate_id": o["record_id"],
                "canonical_id": keeper["record_id"],
                "match_method": o.get("_dedup_reason", "unknown"),
                "title": str(o.get("title") or "")[:90],
                "source": o.get("source") or o.get("database") or "unknown",
                "source_file": o.get("source_file") or "",
            })

        keeper["published_year"] = min(pub_years) if pub_years else None
        keeper["online_first_year"] = min(online_years) if online_years else None
        keeper["preprint_year"] = min(pre_years) if pre_years else keeper.get("preprint_year")
        if keeper.get("published_year"):
            keeper["year"] = keeper["published_year"]
            keeper["date_basis"] = "published"
        elif keeper.get("online_first_year"):
            keeper["year"] = keeper["online_first_year"]
            keeper["date_basis"] = "online_first"
        elif keeper.get("preprint_year"):
            keeper["year"] = keeper["preprint_year"]
            keeper["date_basis"] = "preprint"

        if keeper.get("text") and keeper.get("material_access") in {"abstract_only", "discovery_only"}:
            keeper["material_access"] = "fulltext_local_text"
        elif not keeper.get("text") and keeper.get("abstract") and keeper.get("material_access") == "discovery_only":
            keeper["material_access"] = "abstract_only"

        k_doi = normalize_doi(str(keeper.get("doi") or ""))
        keeper["alt_dois"] = [d for d in alt_dois if d != k_doi]
        keeper["found_in_sources"] = sorted(sources)
        keeper["source_files"] = sorted(source_files)
        keeper["source_ids"] = source_ids
        keeper["merged_record_ids"] = merged_ids
        keeper["citations"] = max_cites
        keeper["concepts"] = sorted(all_concepts)
        keeper["passages"] = all_passages[:120]
        keeper.pop("_dedup_reason", None)
        canonical_list.append(keeper)

    for bc in borderline_candidates:
        meta_conflicts.append({"field": "borderline_duplicate_guard", **bc})

    return canonical_list, dup_log, meta_conflicts


def summarize_identification_by_source(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for r in records:
        counts[str(r.get("source") or "unknown").lower()] += 1
    return dict(sorted(counts.items()))


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
    n_elig_excl: int = 0,
    n_awaiting_elig: int = 0,
    n_included: int = 0,
    identified_by_source: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Conserve PRISMA 2020 Item 16a stages with strict accounting checks."""
    eq1_lhs = n_identified - n_dedup - n_automation_excl - n_awaiting_screen
    eq1_ok = eq1_lhs == n_screened

    sought_val = (n_screened - n_screen_excl) if n_sought is None else n_sought
    eq2_ok = (n_screened - n_screen_excl) == sought_val
    not_retrieved_valid = 0 <= n_not_retrieved <= sought_val
    assessed_for_eligibility = sought_val - n_not_retrieved
    eq3_ok = (assessed_for_eligibility - n_elig_excl - n_awaiting_elig) == n_included
    simple_closure_ok = n_identified == (n_screened + n_dedup + n_automation_excl + n_awaiting_screen)
    source_sum_ok = (sum(identified_by_source.values()) == n_identified) if identified_by_source else True

    return {
        "balanced": eq1_ok and eq2_ok and not_retrieved_valid and eq3_ok and simple_closure_ok and source_sum_ok,
        "identified_by_source": identified_by_source or {},
        "n_identified": n_identified,
        "n_dedup": n_dedup,
        "n_automation_excl": n_automation_excl,
        "n_awaiting_screen": n_awaiting_screen,
        "n_screened": n_screened,
        "n_screen_excl": n_screen_excl,
        "n_sought": sought_val,
        "n_not_retrieved": n_not_retrieved,
        "n_assessed_for_eligibility": assessed_for_eligibility,
        "n_elig_excl": n_elig_excl,
        "n_awaiting_elig": n_awaiting_elig,
        "n_included": n_included,
        "eq1_identified_to_screened_ok": eq1_ok,
        "eq2_screened_to_sought_ok": eq2_ok,
        "eq_not_retrieved_within_sought_ok": not_retrieved_valid,
        "eq3_assessed_to_outcomes_ok": eq3_ok,
        "eq3_sought_to_included_ok": eq3_ok,
        "eq_source_sum_ok": source_sum_ok,
        "discrepancy_identified_minus_expected": n_identified - (n_screened + n_dedup + n_automation_excl + n_awaiting_screen),
    }
