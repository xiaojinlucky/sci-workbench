"""Pure-stdlib document identity, version/preprint deduplication, context lock, and scope guard.

Zero third-party dependencies. Supports:
- Exact content-hash duplicate detection (normalized text SHA-256).
- DOI matching and preprint vs. Version-of-Record linkage (preventing double-counting).
- Strict condition-context locking so distinct experimental runs under one DOI are preserved.
- Out-of-scope distractor detection with transparent audit rationale.
- PRISMA 2020 4-stage accounting conservation verification.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import Any

_PREPRINT_DOI_PREFIXES = (
    "10.48550/",
    "10.1101/",
    "10.26434/",
    "10.21203/",
    "10.31219/",
    "10.20944/",
)
_PREPRINT_VENUES = (
    "arxiv",
    "biorxiv",
    "medrxiv",
    "chemrxiv",
    "techrxiv",
    "ssrn",
    "research square",
    "preprints.org",
    "preprint",
)
_VERSION_SUFFIX_RE = re.compile(
    r"(?:\s*[\(\[]\s*(?:preprint|revised|revision|updated|v\d+|version\s*\d+|accepted|manuscript)\s*[\)\]]|\s+v\d+\b)",
    re.I,
)
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over", "under",
    "between", "across", "using", "based", "study", "studies", "paper", "report",
    "compare", "comparison", "document", "documents", "analysis", "results",
    "method", "methods", "data", "performance", "evaluation", "effect", "effects",
    "what", "which", "where", "when", "their", "these", "those", "have", "been",
    "were", "more", "than", "also", "such", "each", "both", "only", "most",
}


def normalize_doi(doi: str) -> str:
    raw = (doi or "").strip().lower()
    raw = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", raw)
    return raw.strip().rstrip(".,;:!?")


def is_preprint_record(record: dict[str, Any]) -> bool:
    doi = normalize_doi(str(record.get("doi", "")))
    if any(doi.startswith(pref) for pref in _PREPRINT_DOI_PREFIXES):
        return True
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    for field in ("version_status", "type", "publication_type", "status"):
        val = str(record.get(field) or meta.get(field) or "").strip().lower()
        if val in {"preprint", "posted-content", "working_paper", "manuscript_preprint"}:
            return True
    venue = str(record.get("venue") or meta.get("venue") or "").casefold()
    source = str(record.get("source") or meta.get("source") or "").casefold()
    title = str(record.get("title") or "").casefold()
    source_file = str(record.get("source_file") or "").casefold()
    if any(v in venue for v in _PREPRINT_VENUES) or any(v in source for v in _PREPRINT_VENUES):
        return True
    if "preprint" in title or "arxiv" in title or "preprint" in source_file:
        return True
    return False


def normalize_title_for_matching(title: str) -> str:
    cleaned = _VERSION_SUFFIX_RE.sub(" ", title or "")
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", cleaned.casefold())
    return " ".join(tokens)


def token_sort_ratio(a: str, b: str) -> float:
    """Stdlib replacement for fuzzy token_sort_ratio (0..100 scale)."""
    ta = " ".join(sorted(re.findall(r"[a-z0-9\u4e00-\u9fff]+", normalize_title_for_matching(a))))
    tb = " ".join(sorted(re.findall(r"[a-z0-9\u4e00-\u9fff]+", normalize_title_for_matching(b))))
    if not ta and not tb:
        return 100.0
    if not ta or not tb:
        return 0.0
    return round(difflib.SequenceMatcher(None, ta, tb).ratio() * 100.0, 2)


def normalized_content_hash(text: str) -> str:
    """Hash normalized document body text while stripping volatile version headers."""
    lines = []
    for ln in (text or "").splitlines():
        low = ln.strip().lower()
        if not low:
            continue
        if re.match(r"^(?:doi|version|status|preprint|published|venue|date|source)\s*[:=]", low):
            continue
        lines.append(ln)
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", " ".join(lines).casefold())
    blob = " ".join(tokens).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _content_token_set(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", (text or "").casefold())
    return {t for t in toks if t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def context_signature(record: dict[str, Any]) -> str:
    """Build strict condition-context signature so distinct experimental runs are NEVER merged."""
    keys = (
        "experiment_arm",
        "substudy_id",
        "material",
        "catalyst",
        "pollutant",
        "substrate",
        "dataset",
        "temperature",
        "ph",
        "experimental_condition",
        "condition_key",
    )
    meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    parts = []
    for k in keys:
        val = record.get(k) if record.get(k) not in (None, "") else meta.get(k)
        if val not in (None, ""):
            parts.append(f"{k}={str(val).strip().lower()}")
    return "|".join(parts)


def _explicit_substudy_key(doc: dict[str, Any]) -> str:
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    keys = ("experiment_arm", "substudy_id", "condition_key")
    parts = [f"{k}={str(meta.get(k)).strip().lower()}" for k in keys if meta.get(k) not in (None, "")]
    return "|".join(parts)


def deduplicate_documents_with_versions(
    docs: list[dict[str, Any]],
    *,
    title_threshold: float = 84.0,
    content_overlap_threshold: float = 0.45,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Identify duplicate manuscripts and preprint-vs-published version pairs at document level.

    Returns:
        canonical_docs: Unique canonical documents (upgraded to Version-of-Record when paired).
        superseded_docs: Duplicate/preprint copies excluded from independent-study counts.
        version_mapping: Traceable mapping from each superseded copy to its canonical document.
        context_conflicts: Same-DOI distinct experimental arms preserved separately.
    """
    canonical: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    version_mapping: list[dict[str, Any]] = []
    context_conflicts: list[dict[str, Any]] = []

    doi_index: dict[str, int] = {}
    hash_index: dict[str, int] = {}
    canonical_link_index: dict[str, int] = {}
    token_cache: list[set[str]] = []

    for doc in docs:
        d = dict(doc)
        meta = dict(d.get("metadata") or {})
        d["metadata"] = meta
        doc_id = d["source_document_id"]
        ndoi = normalize_doi(str(d.get("doi") or meta.get("doi") or ""))
        full_text = "\n".join(p.get("text", "") for p in d.get("paragraphs", []))
        chash = normalized_content_hash(full_text) if full_text.strip() else ""
        d["content_sha256"] = chash
        d["is_preprint"] = is_preprint_record(d)
        sub_key = _explicit_substudy_key(d)
        toks = _content_token_set(f"{d.get('title', '')} {full_text}")

        alt_links = set()
        for lk in ("canonical_id", "version_group", "supersedes_document_id", "version_of"):
            v = meta.get(lk)
            if v:
                alt_links.add(str(v).strip().casefold())
        for ad in meta.get("alt_dois") or []:
            na = normalize_doi(str(ad))
            if na:
                alt_links.add(na)

        matched_idx: int | None = None
        match_method = ""
        match_score = 0.0

        # 1. Exact content hash duplicate
        if chash and chash in hash_index:
            cand_i = hash_index[chash]
            if not sub_key or sub_key == _explicit_substudy_key(canonical[cand_i]):
                matched_idx = cand_i
                match_method = "exact_content_hash"
                match_score = 100.0

        # 2. Exact DOI match (respecting explicit substudy/condition separation)
        if matched_idx is None and ndoi and ndoi in doi_index:
            cand_i = doi_index[ndoi]
            cand_sub = _explicit_substudy_key(canonical[cand_i])
            if not sub_key and not cand_sub or sub_key == cand_sub:
                matched_idx = cand_i
                match_method = "doi_exact"
                match_score = 100.0
            else:
                context_conflicts.append({
                    "code": "CONTEXT_DISTINCT_SAME_DOI",
                    "doc_a": canonical[cand_i]["source_document_id"],
                    "doc_b": doc_id,
                    "doi": ndoi,
                    "reason": "Same DOI with distinct explicit experimental arms; preserved as separate entries.",
                })

        # 3. Explicit version link / alt_dois cross-reference
        if matched_idx is None:
            for lk in alt_links | {doc_id.casefold(), ndoi}:
                if lk and lk in canonical_link_index:
                    matched_idx = canonical_link_index[lk]
                    match_method = "explicit_version_link"
                    match_score = 100.0
                    break

        # 4. Preprint/revision fuzzy title + content overlap check
        if matched_idx is None:
            title = str(d.get("title") or "")
            for ci, existing in enumerate(canonical):
                ex_sub = _explicit_substudy_key(existing)
                if sub_key and ex_sub and sub_key != ex_sub:
                    continue
                t_score = token_sort_ratio(title, str(existing.get("title") or ""))
                c_overlap = _jaccard(toks, token_cache[ci])
                either_preprint = d["is_preprint"] or existing.get("is_preprint", False)
                if t_score >= title_threshold and (either_preprint or c_overlap >= content_overlap_threshold):
                    matched_idx = ci
                    match_method = "preprint_version_pair" if either_preprint else "revision_title_content_match"
                    match_score = round(max(t_score, c_overlap * 100.0), 2)
                    break
                if c_overlap >= 0.85 and len(toks) >= 25:
                    matched_idx = ci
                    match_method = "high_content_overlap_duplicate"
                    match_score = round(c_overlap * 100.0, 2)
                    break

        if matched_idx is None:
            d["alt_dois"] = sorted({normalize_doi(str(x)) for x in (meta.get("alt_dois") or []) if normalize_doi(str(x))})
            d["superseded_versions"] = []
            d["version_status"] = "preprint" if d["is_preprint"] else "version_of_record"
            canonical.append(d)
            token_cache.append(toks)
            idx_new = len(canonical) - 1
            if chash:
                hash_index[chash] = idx_new
            if ndoi:
                doi_index[ndoi] = idx_new
                canonical_link_index[ndoi] = idx_new
            canonical_link_index[doc_id.casefold()] = idx_new
            for lk in alt_links:
                canonical_link_index[lk] = idx_new
        else:
            existing = canonical[matched_idx]
            # Decide whether incoming document upgrades existing preprint to Version-of-Record
            upgrade = bool(existing.get("is_preprint") and not d["is_preprint"])
            if upgrade:
                loser = existing
                winner = d
                winner["alt_dois"] = sorted(set(existing.get("alt_dois", [])) | ({normalize_doi(str(loser.get("doi") or ""))} - {""}))
                winner["superseded_versions"] = list(existing.get("superseded_versions", [])) + [loser["source_document_id"]]
                winner["version_status"] = "version_of_record"
                loser["version_status"] = "duplicate_superseded"
                loser["superseded_by"] = winner["source_document_id"]
                canonical[matched_idx] = winner
                token_cache[matched_idx] = toks
                if chash:
                    hash_index[chash] = matched_idx
                if ndoi:
                    doi_index[ndoi] = matched_idx
                    canonical_link_index[ndoi] = matched_idx
                canonical_link_index[winner["source_document_id"].casefold()] = matched_idx
                superseded.append(loser)
                version_mapping.append({
                    "canonical_document_id": winner["source_document_id"],
                    "canonical_doi": winner.get("doi") or "",
                    "superseded_document_id": loser["source_document_id"],
                    "superseded_doi": loser.get("doi") or "",
                    "superseded_role": "preprint" if loser.get("is_preprint") else "duplicate_copy",
                    "match_method": match_method,
                    "score": match_score,
                    "upgraded_to_version_of_record": True,
                })
            else:
                loser = d
                winner = existing
                if ndoi and ndoi != normalize_doi(str(winner.get("doi") or "")) and ndoi not in winner["alt_dois"]:
                    winner["alt_dois"].append(ndoi)
                winner["superseded_versions"].append(loser["source_document_id"])
                loser["version_status"] = "duplicate_superseded"
                loser["superseded_by"] = winner["source_document_id"]
                superseded.append(loser)
                version_mapping.append({
                    "canonical_document_id": winner["source_document_id"],
                    "canonical_doi": winner.get("doi") or "",
                    "superseded_document_id": loser["source_document_id"],
                    "superseded_doi": loser.get("doi") or "",
                    "superseded_role": "preprint" if loser.get("is_preprint") else "duplicate_copy",
                    "match_method": match_method,
                    "score": match_score,
                    "upgraded_to_version_of_record": False,
                })

    return canonical, superseded, version_mapping, context_conflicts


def detect_out_of_scope_documents(
    docs: list[dict[str, Any]],
    workorder: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate in-scope documents from clearly off-topic distractors with explicit trace rationale."""
    explicit_excl = {str(x).strip() for x in (workorder.get("excluded_document_ids") or workorder.get("out_of_scope_ids") or [])}
    scope_keywords = [str(k).strip().casefold() for k in (workorder.get("scope_keywords") or workorder.get("required_topics") or []) if str(k).strip()]
    exclude_topics = [str(k).strip().casefold() for k in (workorder.get("exclude_topics") or workorder.get("out_of_scope_keywords") or []) if str(k).strip()]

    # Derive weak fallback keywords from research_question ONLY if scope_keywords is absent
    rq_tokens: set[str] = set()
    rq = str(workorder.get("research_question") or workorder.get("target_domain") or "")
    if not scope_keywords and rq:
        rq_tokens = {t for t in re.findall(r"[a-z0-9]{4,}|[\u4e00-\u9fff]{2,}", rq.casefold()) if t not in _STOPWORDS}

    in_scope: list[dict[str, Any]] = []
    out_of_scope: list[dict[str, Any]] = []

    for doc in docs:
        d = dict(doc)
        meta = d.get("metadata") or {}
        did = d["source_document_id"]
        blob = (
            f"{d.get('title', '')}\n"
            f"{meta.get('domain', '')}\n"
            f"{meta.get('topic', '')}\n"
            + "\n".join(p.get("text", "") for p in d.get("paragraphs", []))
        ).casefold()

        if did in explicit_excl or str(meta.get("scope_status") or "").strip().lower() == "out_of_scope":
            d["version_status"] = "out_of_scope_excluded"
            d["exclusion_reason"] = "Explicitly flagged as out-of-scope in workorder or document metadata."
            out_of_scope.append(d)
            continue

        matched_excl = [ek for ek in exclude_topics if ek in blob]
        matched_scope = [sk for sk in scope_keywords if sk in blob]
        if matched_excl and not matched_scope:
            d["version_status"] = "out_of_scope_excluded"
            d["exclusion_reason"] = f"Matched explicit exclusion topic(s) {matched_excl} with zero scope keyword overlap."
            out_of_scope.append(d)
            continue

        if scope_keywords and not matched_scope:
            d["version_status"] = "out_of_scope_excluded"
            d["exclusion_reason"] = f"Zero overlap with workorder scope_keywords ({', '.join(scope_keywords[:8])}); isolated as off-topic distractor."
            out_of_scope.append(d)
            continue

        if not scope_keywords and len(rq_tokens) >= 4:
            doc_toks = _content_token_set(blob)
            if not (doc_toks & rq_tokens):
                d["version_status"] = "out_of_scope_excluded"
                d["exclusion_reason"] = "Zero domain-token overlap with workorder research_question; isolated as off-topic distractor."
                out_of_scope.append(d)
                continue

        d["matched_scope_keywords"] = matched_scope
        in_scope.append(d)

    return in_scope, out_of_scope


def deduplicate_records_with_context_lock(
    records: list[dict[str, Any]],
    *,
    title_threshold: float = 90.0,
    require_same_context: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Deduplicate row-level records by DOI or fuzzy title ONLY when condition context matches."""
    doi_map: dict[str, int] = {}
    canonical: list[dict[str, Any]] = []
    dup_log: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for idx, raw in enumerate(records):
        rec = dict(raw)
        rec.setdefault("_row_id", str(rec.get("row_id") or f"row_{idx + 1}"))
        ndoi = normalize_doi(str(rec.get("doi", "")))
        ctx = context_signature(rec)
        matched_idx: int | None = None
        match_method = ""
        match_score = 0.0

        if ndoi and ndoi in doi_map:
            cand_i = doi_map[ndoi]
            cand_ctx = context_signature(canonical[cand_i])
            if not require_same_context or ctx == cand_ctx:
                matched_idx = cand_i
                match_method = "doi_exact"
                match_score = 100.0
            else:
                conflicts.append({
                    "code": "CONTEXT_DISTINCT_SAME_DOI",
                    "row_a": canonical[cand_i].get("_row_id"),
                    "row_b": rec.get("_row_id"),
                    "reason": "Same DOI but distinct experimental/source context; preserved separately.",
                    "ctx_a": cand_ctx,
                    "ctx_b": ctx,
                })

        if matched_idx is None:
            title = str(rec.get("title", "") or rec.get("entity", ""))
            metric = str(rec.get("metric", "")).strip().lower()
            for ci, existing in enumerate(canonical):
                if require_same_context and ctx != context_signature(existing):
                    continue
                if metric and str(existing.get("metric", "")).strip().lower() != metric:
                    continue
                ex_title = str(existing.get("title", "") or existing.get("entity", ""))
                score = token_sort_ratio(title, ex_title)
                if score >= title_threshold and title:
                    matched_idx = ci
                    match_method = "fuzzy_title_same_context"
                    match_score = score
                    break

        if matched_idx is None:
            rec.setdefault("alt_dois", [])
            rec.setdefault("merged_row_ids", [rec["_row_id"]])
            canonical.append(rec)
            if ndoi:
                doi_map[ndoi] = len(canonical) - 1
        else:
            keeper = canonical[matched_idx]
            keeper.setdefault("merged_row_ids", [keeper["_row_id"]]).append(rec["_row_id"])
            alt = list(keeper.get("alt_dois", []))
            if ndoi and ndoi != normalize_doi(str(keeper.get("doi", ""))) and ndoi not in alt:
                alt.append(ndoi)
            if is_preprint_record(keeper) and not is_preprint_record(rec) and rec.get("doi"):
                old_doi = normalize_doi(str(keeper.get("doi", "")))
                if old_doi and old_doi not in alt:
                    alt.append(old_doi)
                keeper["doi"] = rec["doi"]
                keeper["venue"] = rec.get("venue") or keeper.get("venue", "")
            keeper["alt_dois"] = alt

            v_keep = keeper.get("normalized_value", keeper.get("value"))
            v_new = rec.get("normalized_value", rec.get("value"))
            if v_keep not in (None, "") and v_new not in (None, "") and str(v_keep) != str(v_new):
                conflicts.append({
                    "code": "VALUE_CONFLICT_SAME_CONTEXT",
                    "row_kept": keeper["_row_id"],
                    "row_duplicate": rec["_row_id"],
                    "value_kept": v_keep,
                    "value_duplicate": v_new,
                    "reason": "Identical condition context has conflicting extracted values; requires resolution.",
                })
            dup_log.append({
                "duplicate_row_id": rec["_row_id"],
                "canonical_row_id": keeper["_row_id"],
                "match_method": match_method,
                "score": match_score,
            })

    return canonical, dup_log, conflicts


def verify_prisma_arithmetic(counts: dict[str, int]) -> dict[str, Any]:
    """Assert 4-stage PRISMA 2020 accounting conservation equations."""
    identified = int(counts.get("identified", 0))
    duplicates = int(counts.get("duplicates", 0))
    automation = int(counts.get("automation_ineligible", 0))
    awaiting_screen = int(counts.get("awaiting_screening", 0))
    screened = int(counts.get("screened", 0))
    screen_excl = int(counts.get("screening_excluded", 0))
    sought = int(counts.get("sought_for_retrieval", 0))
    elig_excl = int(counts.get("eligibility_excluded", 0))
    awaiting_elig = int(counts.get("awaiting_eligibility", 0))
    included = int(counts.get("included", 0))

    eq1 = (identified - duplicates - automation - awaiting_screen) == screened
    eq2 = (screened - screen_excl) == sought
    eq3 = (sought - elig_excl - awaiting_elig) == included
    return {
        "balanced": eq1 and eq2 and eq3,
        "eq1_screened_matches": eq1,
        "eq2_sought_matches": eq2,
        "eq3_included_matches": eq3,
    }
