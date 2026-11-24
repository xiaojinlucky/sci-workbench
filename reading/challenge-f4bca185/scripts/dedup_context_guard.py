"""Multi-context deduplication and scientific conflict stratification guard (challenge-f4bca185).

Pure Python 3.12 standard library.
Enforces strict 3-way stratification under R04:
1. DUPLICATE_MERGED: identical entity/DOI + metric + experimental context + equivalent normalized value
   -> merge into canonical row preserving merged_row_ids & alt_dois (upgrading preprint -> VoR when present).
2. CONTEXT_DISTINCT_PRESERVED: same DOI/entity/metric under DIFFERENT experimental conditions
   (temperature, pH, material, catalyst, pollutant, substrate, dataset, replicate_id, solvent, etc.)
   -> strictly preserved as separate canonical rows and logged in context_preserved_log (never merged!).
3. VALUE_CONFLICT_SAME_CONTEXT: identical entity/DOI + metric + identical experimental context,
   but CONFLICTING values or censoring operators
   -> NEVER silently overwrite or drop! Both rows are preserved with conflict_status='VALUE_CONFLICT_SAME_CONTEXT'
      and logged into scientific_conflicts for human adjudication.
"""

from __future__ import annotations

import difflib
import math
import re
from typing import Any

_PREPRINT_DOI_PREFIXES = ("10.48550/",)
_PREPRINT_VENUES = (
    "arxiv", "biorxiv", "medrxiv", "chemrxiv", "techrxiv", "ssrn", "research square", "preprints.org",
)

CONTEXT_KEYS = (
    "source_document_id",
    "paper_id",
    "material",
    "catalyst",
    "pollutant",
    "substrate",
    "dataset",
    "temperature",
    "ph",
    "solvent",
    "atmosphere",
    "experimental_condition",
    "condition_key",
    "replicate_id",
)


def normalize_doi(doi: str) -> str:
    raw = (doi or "").strip().lower()
    raw = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", raw)
    return raw.strip().rstrip(".,;")


def is_preprint_record(record: dict[str, Any]) -> bool:
    doi = normalize_doi(str(record.get("doi", "")))
    if doi.startswith(_PREPRINT_DOI_PREFIXES):
        return True
    rtype = str(record.get("type", "")).strip().lower()
    if rtype in {"preprint", "posted-content"}:
        return True
    venue = str(record.get("venue", "")).casefold()
    source = str(record.get("source", "")).casefold()
    return any(v in venue for v in _PREPRINT_VENUES) or (not venue and source == "arxiv")


def token_sort_ratio(a: str, b: str) -> float:
    ta = " ".join(sorted(re.findall(r"[a-z0-9\u4e00-\u9fff]+", (a or "").casefold())))
    tb = " ".join(sorted(re.findall(r"[a-z0-9\u4e00-\u9fff]+", (b or "").casefold())))
    if not ta and not tb:
        return 100.0
    if not ta or not tb:
        return 0.0
    return round(difflib.SequenceMatcher(None, ta, tb).ratio() * 100.0, 2)


def context_signature(record: dict[str, Any]) -> str:
    """Build deterministic condition-context lock signature."""
    parts = [
        f"{k}={' '.join(str(record.get(k, '')).strip().lower().split())}"
        for k in CONTEXT_KEYS
        if record.get(k) not in (None, "")
    ]
    return "|".join(parts)


def _values_equivalent(rec_a: dict[str, Any], rec_b: dict[str, Any], rel_tol: float = 1e-4) -> bool:
    """Compare normalized values, units, and censor operators strictly."""
    if str(rec_a.get("censor_operator") or "") != str(rec_b.get("censor_operator") or ""):
        return False
    ua = str(rec_a.get("normalized_unit") or rec_a.get("raw_unit") or "").strip().lower()
    ub = str(rec_b.get("normalized_unit") or rec_b.get("raw_unit") or "").strip().lower()
    if ua != ub:
        return False

    va = rec_a.get("normalized_value")
    vb = rec_b.get("normalized_value")
    if va is None and vb is None:
        return str(rec_a.get("raw_value") or "").strip().lower() == str(rec_b.get("raw_value") or "").strip().lower()
    if va is None or vb is None:
        return False
    if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
        return math.isclose(float(va), float(vb), rel_tol=rel_tol, abs_tol=1e-9)
    return str(va).strip().lower() == str(vb).strip().lower()


def deduplicate_records_with_context_lock(
    records: list[dict[str, Any]],
    *,
    title_threshold: float = 92.0,
    require_same_context: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Stratify duplicates, distinct-context rows, and same-context scientific conflicts.

    Returns (canonical_records, duplicate_log, context_preserved_log, scientific_conflicts).
    """
    canonical: list[dict[str, Any]] = []
    dup_log: list[dict[str, Any]] = []
    context_preserved_log: list[dict[str, Any]] = []
    scientific_conflicts: list[dict[str, Any]] = []

    for idx, raw in enumerate(records):
        rec = dict(raw)
        rid = str(rec.get("row_id") or f"row_{idx + 1}")
        rec["_row_id"] = rid
        rec.setdefault("conflict_status", "none")

        ndoi = normalize_doi(str(rec.get("doi", "")))
        ctx = context_signature(rec)
        rec["context_signature"] = ctx
        entity = str(rec.get("entity") or rec.get("title") or "").strip()
        metric = str(rec.get("metric") or "").strip().lower()

        same_ctx_match_idx: int | None = None
        match_method = ""
        match_score = 0.0
        distinct_ctx_peer_ids: list[str] = []

        for ci, existing in enumerate(canonical):
            ex_doi = normalize_doi(str(existing.get("doi", "")))
            ex_ctx = existing.get("context_signature", context_signature(existing))
            ex_entity = str(existing.get("entity") or existing.get("title") or "").strip()
            ex_metric = str(existing.get("metric") or "").strip().lower()

            if metric and ex_metric and metric != ex_metric:
                continue

            # Check identity match: exact DOI (when entity matches or is absent) or exact/high-similarity entity
            doi_same = bool(ndoi and ex_doi and ndoi == ex_doi)
            ent_score = token_sort_ratio(entity, ex_entity) if (entity and ex_entity) else (100.0 if doi_same else 0.0)
            entity_same = (entity.casefold() == ex_entity.casefold() and bool(entity)) or (ent_score >= title_threshold and bool(entity))

            # Do not conflate two distinct materials/entities under the same DOI!
            if doi_same and entity and ex_entity and not entity_same:
                continue
            if not (doi_same or entity_same):
                continue

            if require_same_context and ctx != ex_ctx:
                distinct_ctx_peer_ids.append(existing["_row_id"])
                continue

            same_ctx_match_idx = ci
            match_method = "doi_and_context_exact" if doi_same else "entity_and_context_match"
            match_score = 100.0 if doi_same else ent_score
            break

        if same_ctx_match_idx is None:
            rec.setdefault("alt_dois", [])
            rec.setdefault("merged_row_ids", [rid])
            canonical.append(rec)
            if distinct_ctx_peer_ids:
                context_preserved_log.append({
                    "code": "CONTEXT_DISTINCT_PRESERVED",
                    "row_id": rid,
                    "distinct_from_rows": distinct_ctx_peer_ids,
                    "entity": entity,
                    "metric": metric,
                    "context_signature": ctx,
                    "reason": "Same DOI/entity and metric but distinct experimental context; preserved as separate row.",
                })
            continue

        keeper = canonical[same_ctx_match_idx]
        if _values_equivalent(keeper, rec):
            # True duplicate extraction under identical context -> safe merge
            keeper.setdefault("merged_row_ids", [keeper["_row_id"]]).append(rid)
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
            dup_log.append({
                "code": "DUPLICATE_MERGED",
                "duplicate_row_id": rid,
                "canonical_row_id": keeper["_row_id"],
                "match_method": match_method,
                "score": match_score,
            })
        else:
            # Same entity + metric + identical experimental context, but conflicting values!
            # NEVER silently overwrite or drop: preserve BOTH rows with explicit conflict markers.
            keeper["conflict_status"] = "VALUE_CONFLICT_SAME_CONTEXT"
            rec["conflict_status"] = "VALUE_CONFLICT_SAME_CONTEXT"
            rec.setdefault("alt_dois", [])
            rec.setdefault("merged_row_ids", [rid])
            canonical.append(rec)
            scientific_conflicts.append({
                "code": "VALUE_CONFLICT_SAME_CONTEXT",
                "row_a": keeper["_row_id"],
                "row_b": rid,
                "entity": entity,
                "metric": metric,
                "context_signature": ctx,
                "value_a": f"{keeper.get('censor_operator') or ''}{keeper.get('normalized_value', keeper.get('raw_value'))} {keeper.get('normalized_unit', keeper.get('raw_unit')) or ''}".strip(),
                "value_b": f"{rec.get('censor_operator') or ''}{rec.get('normalized_value', rec.get('raw_value'))} {rec.get('normalized_unit', rec.get('raw_unit')) or ''}".strip(),
                "reason": "Identical experimental context has conflicting measurements; both rows preserved without silent overwrite for human adjudication.",
            })

    return canonical, dup_log, context_preserved_log, scientific_conflicts
