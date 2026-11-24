"""Multi-context deduplication and scientific conflict stratification guard (challenge-f4bca185).

Pure Python 3.12 standard library.
Enforces strict order-independent 3-way stratification under R04:
1. Explicit grouping key `(source_doi, experiment_context, sample_id, metric, extraction_id)`:
   - Cross-DOI auto-merging is strictly FORBIDDEN (different DOIs -> emitted as candidate only, never merged).
   - Missing critical identity (missing `metric`, or missing both `sample_id` and `entity`/`title`)
     -> NEVER auto-merged; emitted only as human-review candidate.
2. Exact Decimal relative-tolerance numeric equivalence (`rel_tol=1e-6`, NO fixed `abs_tol=1e-9`),
   preventing low-concentration order-of-magnitude differences (e.g., `1e-12` vs `9e-10`) from being swallowed.
3. Full-group equivalence clustering / transitive closure (NO greedy first-match `break`),
   eliminating input-order dependence (e.g., `[1, 2, 2]`, `[2, 1, 2]`, `[2, 2, 1]` produce identical
   canonical clusters, merge counts, and conflict counts).
4. Per-row provenance via `raw_row_index` so duplicate-merge counts and checklist statuses stay 1:1 exact.
"""

from __future__ import annotations

import difflib
import re
from decimal import Decimal, InvalidOperation
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

# Case-sensitive units where lowercasing would conflate distinct physical dimensions (e.g., mM vs mm, M vs m)
_CASE_SENSITIVE_UNITS = {"M", "mM", "uM", "µM", "nM", "pM", "m", "mm", "um", "µm", "nm", "A", "mA", "uA", "µA", "nA"}


def normalize_doi(doi: str) -> str:
    raw = (doi or "").strip().lower()
    raw = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", raw)
    return raw.strip().rstrip(".,;")


def extract_source_doi(record: dict[str, Any]) -> str:
    return normalize_doi(str(record.get("source_doi") or record.get("doi") or ""))


def is_preprint_record(record: dict[str, Any]) -> bool:
    doi = extract_source_doi(record)
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


def build_identity_tuple(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Build explicit 5-tuple grouping key: (source_doi, experiment_context, sample_id, metric, extraction_id)."""
    source_doi = extract_source_doi(record)
    exp_ctx = context_signature(record)
    sample_id_raw = str(
        record.get("sample_id")
        or record.get("entity")
        or record.get("compound_id")
        or record.get("title")
        or ""
    ).strip()
    sample_norm = " ".join(sample_id_raw.casefold().split())
    metric_norm = " ".join(str(record.get("metric") or "").strip().casefold().split())
    extraction_id_norm = " ".join(str(record.get("extraction_id") or "").strip().casefold().split())
    return (source_doi, exp_ctx, sample_norm, metric_norm, extraction_id_norm)


def _norm_unit_for_compare(unit_raw: Any) -> str:
    u = " ".join(str(unit_raw or "").strip().split())
    if u in _CASE_SENSITIVE_UNITS:
        return u
    return u.lower()


def _to_decimal(val: Any) -> Decimal | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, Decimal):
        return val if val.is_finite() else None
    if isinstance(val, (int, float)):
        try:
            d = Decimal(str(val))
            return d if d.is_finite() else None
        except InvalidOperation:
            return None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            d = Decimal(s)
            return d if d.is_finite() else None
        except InvalidOperation:
            return None
    return None


def _values_equivalent(
    rec_a: dict[str, Any],
    rec_b: dict[str, Any],
    rel_tol: Decimal | float = Decimal("1e-6"),
) -> bool:
    """Compare normalized values, units, and censor operators using Decimal + relative tolerance (NO abs_tol)."""
    if str(rec_a.get("censor_operator") or "") != str(rec_b.get("censor_operator") or ""):
        return False
    ua = _norm_unit_for_compare(rec_a.get("normalized_unit") or rec_a.get("raw_unit") or "")
    ub = _norm_unit_for_compare(rec_b.get("normalized_unit") or rec_b.get("raw_unit") or "")
    if ua != ub:
        return False

    va = rec_a.get("normalized_value")
    vb = rec_b.get("normalized_value")
    if va is None and vb is None:
        return str(rec_a.get("raw_value") or "").strip().lower() == str(rec_b.get("raw_value") or "").strip().lower()
    if va is None or vb is None:
        return False

    da = _to_decimal(va)
    db = _to_decimal(vb)
    if da is not None and db is not None:
        if da == Decimal(0) and db == Decimal(0):
            return True
        if da == Decimal(0) or db == Decimal(0):
            return False
        tol = rel_tol if isinstance(rel_tol, Decimal) else Decimal(str(rel_tol))
        scale = max(abs(da), abs(db))
        return abs(da - db) <= tol * scale

    return str(va).strip().lower() == str(vb).strip().lower()


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def deduplicate_records_detailed(
    records: list[dict[str, Any]],
    *,
    title_threshold: float = 92.0,
    require_same_context: bool = True,
    rel_tol: Decimal | float = Decimal("1e-6"),
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Order-independent 3-way stratification with strict cross-DOI and missing-identity guards.

    Returns (canonical_records, duplicate_log, context_preserved_log, scientific_conflicts, dedup_candidates).
    """
    n = len(records)
    enriched: list[dict[str, Any]] = []
    identities: list[tuple[str, str, str, str, str]] = []
    complete_indices: list[int] = []
    incomplete_indices: list[int] = []

    for idx, raw in enumerate(records):
        rec = dict(raw)
        rid = str(rec.get("row_id") or f"row_{idx + 1}")
        raw_idx = int(rec.get("_raw_row_index", idx))
        rec["_row_id"] = rid
        rec["_raw_row_index"] = raw_idx
        rec.setdefault("conflict_status", "none")
        rec.setdefault("alt_dois", [])

        id_tuple = build_identity_tuple(rec)
        source_doi, exp_ctx, sample_norm, metric_norm, extraction_id_norm = id_tuple
        rec["context_signature"] = exp_ctx
        rec["dedup_group_key"] = {
            "source_doi": source_doi,
            "experiment_context": exp_ctx,
            "sample_id": sample_norm,
            "metric": metric_norm,
            "extraction_id": extraction_id_norm,
        }
        enriched.append(rec)
        identities.append(id_tuple)

        # Missing critical identity (metric OR sample_id/entity) -> NEVER auto-merge!
        if not metric_norm or not sample_norm:
            incomplete_indices.append(idx)
        else:
            complete_indices.append(idx)

    canonical: list[dict[str, Any]] = []
    dup_log: list[dict[str, Any]] = []
    context_preserved_log: list[dict[str, Any]] = []
    scientific_conflicts: list[dict[str, Any]] = []
    dedup_candidates: list[dict[str, Any]] = []

    # 1. Quarantine incomplete-identity records as non-merged canonical rows + candidate warnings
    for idx in incomplete_indices:
        rec = enriched[idx]
        source_doi, exp_ctx, sample_norm, metric_norm, _ = identities[idx]
        missing_parts = [p for p, val in (("metric", metric_norm), ("sample_id/entity", sample_norm)) if not val]
        rec["merged_row_ids"] = [rec["_row_id"]]
        rec["merged_raw_row_indices"] = [rec["_raw_row_index"]]
        canonical.append(rec)
        dedup_candidates.append({
            "code": "DEDUP_CANDIDATE_MISSING_IDENTITY",
            "row_id": rec["_row_id"],
            "raw_row_index": rec["_raw_row_index"],
            "missing_fields": missing_parts,
            "source_doi": source_doi,
            "context_signature": exp_ctx,
            "reason": f"Missing critical identity field(s) {missing_parts}; kept unmerged as review candidate only.",
        })

    # 2. Cluster complete-identity records into order-independent identity+context groups via Union-Find.
    #    STRICT INVARIANT: Records with DIFFERENT source_doi (or one with DOI and one without) NEVER union!
    uf_group = _UnionFind(n)
    # Index complete records by (source_doi, exp_ctx_key, metric_norm, extraction_id_norm)
    buckets: dict[tuple[str, str, str, str], list[int]] = {}
    for idx in complete_indices:
        source_doi, exp_ctx, sample_norm, metric_norm, ext_id = identities[idx]
        ctx_key = exp_ctx if require_same_context else ""
        bkey = (source_doi, ctx_key, metric_norm, ext_id)
        buckets.setdefault(bkey, []).append(idx)

    for bkey, idxs in buckets.items():
        m = len(idxs)
        for a_pos in range(m):
            ia = idxs[a_pos]
            sa = identities[ia][2]
            has_explicit_sid_a = bool(str(enriched[ia].get("sample_id") or "").strip())
            for b_pos in range(a_pos + 1, m):
                ib = idxs[b_pos]
                sb = identities[ib][2]
                has_explicit_sid_b = bool(str(enriched[ib].get("sample_id") or "").strip())
                if sa == sb:
                    uf_group.union(ia, ib)
                elif not has_explicit_sid_a and not has_explicit_sid_b and token_sort_ratio(sa, sb) >= title_threshold:
                    uf_group.union(ia, ib)

    groups: dict[int, list[int]] = {}
    for idx in complete_indices:
        root = uf_group.find(idx)
        groups.setdefault(root, []).append(idx)

    # 3. Within each identity+context group, perform FULL value-equivalence clustering (transitive closure, NO break!)
    for root in sorted(groups.keys()):
        members = sorted(groups[root])
        k = len(members)
        uf_val = _UnionFind(k)
        for i in range(k):
            for j in range(i + 1, k):
                if _values_equivalent(enriched[members[i]], enriched[members[j]], rel_tol=rel_tol):
                    uf_val.union(i, j)

        val_clusters: dict[int, list[int]] = {}
        for pos in range(k):
            vr = uf_val.find(pos)
            val_clusters.setdefault(vr, []).append(members[pos])

        cluster_keepers: list[dict[str, Any]] = []
        for vr in sorted(val_clusters.keys()):
            c_indices = val_clusters[vr]
            # Deterministic canonical keeper selection: lowest raw_row_index (stable across equivalent rows)
            keeper_idx = c_indices[0]
            keeper = enriched[keeper_idx]
            keeper["merged_row_ids"] = [enriched[ci]["_row_id"] for ci in c_indices]
            keeper["merged_raw_row_indices"] = [enriched[ci]["_raw_row_index"] for ci in c_indices]

            for dup_idx in c_indices[1:]:
                dup_rec = enriched[dup_idx]
                dup_log.append({
                    "code": "DUPLICATE_MERGED",
                    "duplicate_row_id": dup_rec["_row_id"],
                    "duplicate_raw_row_index": dup_rec["_raw_row_index"],
                    "canonical_row_id": keeper["_row_id"],
                    "canonical_raw_row_index": keeper["_raw_row_index"],
                    "match_method": "group_key_and_decimal_rel_tol_cluster",
                    "group_key": keeper["dedup_group_key"],
                })
            cluster_keepers.append(keeper)

        # If >1 distinct value clusters exist in the same (source_doi, context, sample, metric, extraction_id) group:
        # mark ALL distinct clusters as VALUE_CONFLICT_SAME_CONTEXT without silent overwrite!
        if len(cluster_keepers) > 1:
            for ck in cluster_keepers:
                ck["conflict_status"] = "VALUE_CONFLICT_SAME_CONTEXT"
            for i in range(len(cluster_keepers)):
                for j in range(i + 1, len(cluster_keepers)):
                    ka, kb = cluster_keepers[i], cluster_keepers[j]
                    ent_display = str(
                        ka.get("entity") or kb.get("entity") or ka.get("sample_id") or kb.get("sample_id") or ""
                    ).strip()
                    scientific_conflicts.append({
                        "code": "VALUE_CONFLICT_SAME_CONTEXT",
                        "row_a": ka["_row_id"],
                        "row_b": kb["_row_id"],
                        "row_a_raw_index": ka["_raw_row_index"],
                        "row_b_raw_index": kb["_raw_row_index"],
                        "entity": ent_display,
                        "metric": str(ka.get("metric") or ""),
                        "source_doi": identities[ka["_raw_row_index"]][0] if ka["_raw_row_index"] < n else extract_source_doi(ka),
                        "context_signature": ka.get("context_signature", ""),
                        "value_a": f"{ka.get('censor_operator') or ''}{ka.get('normalized_value', ka.get('raw_value'))} {ka.get('normalized_unit', ka.get('raw_unit')) or ''}".strip(),
                        "value_b": f"{kb.get('censor_operator') or ''}{kb.get('normalized_value', kb.get('raw_value'))} {kb.get('normalized_unit', kb.get('raw_unit')) or ''}".strip(),
                        "reason": "Identical (source_doi, experiment_context, sample_id, metric) group has conflicting measurements; all distinct clusters preserved for human adjudication.",
                    })

        canonical.extend(cluster_keepers)

    # Restore deterministic order by raw_row_index
    canonical.sort(key=lambda r: int(r.get("_raw_row_index", 0)))

    # 4. Detect CONTEXT_DISTINCT_PRESERVED (same source_doi + sample + metric, DIFFERENT experiment_context)
    #    and CROSS_DOI_CANDIDATE_NOT_MERGED (same sample + metric + context, DIFFERENT source_doi)
    by_doi_sample_metric: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    by_sample_metric_ctx: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for rec in canonical:
        gk = rec.get("dedup_group_key") or {}
        s_doi = gk.get("source_doi", "")
        s_ctx = gk.get("experiment_context", "")
        s_samp = gk.get("sample_id", "")
        s_met = gk.get("metric", "")
        if not s_samp or not s_met:
            continue
        by_doi_sample_metric.setdefault((s_doi, s_samp, s_met), []).append(rec)
        by_sample_metric_ctx.setdefault((s_samp, s_met, s_ctx), []).append(rec)

    for (s_doi, s_samp, s_met), recs in by_doi_sample_metric.items():
        if len(recs) < 2:
            continue
        seen_ctxs: list[tuple[str, str]] = []
        for rec in recs:
            ctx = rec.get("context_signature", "")
            peers = [rid for pctx, rid in seen_ctxs if pctx != ctx]
            if peers:
                context_preserved_log.append({
                    "code": "CONTEXT_DISTINCT_PRESERVED",
                    "row_id": rec["_row_id"],
                    "raw_row_index": rec["_raw_row_index"],
                    "distinct_from_rows": peers,
                    "entity": str(rec.get("entity") or s_samp),
                    "metric": s_met,
                    "source_doi": s_doi,
                    "context_signature": ctx,
                    "reason": "Same source_doi/sample and metric but distinct experimental context; preserved as separate row.",
                })
            seen_ctxs.append((ctx, rec["_row_id"]))

    for (s_samp, s_met, s_ctx), recs in by_sample_metric_ctx.items():
        distinct_dois = sorted({(r.get("dedup_group_key") or {}).get("source_doi", "") for r in recs})
        if len(distinct_dois) > 1:
            dedup_candidates.append({
                "code": "CROSS_DOI_CANDIDATE_NOT_MERGED",
                "row_ids": [r["_row_id"] for r in recs],
                "raw_row_indices": [r["_raw_row_index"] for r in recs],
                "dois": distinct_dois,
                "entity": str(recs[0].get("entity") or s_samp),
                "metric": s_met,
                "context_signature": s_ctx,
                "reason": "Cross-DOI auto-merging is strictly forbidden; records kept separate and surfaced as review candidates.",
            })

    return canonical, dup_log, context_preserved_log, scientific_conflicts, dedup_candidates


def deduplicate_records_with_context_lock(
    records: list[dict[str, Any]],
    *,
    title_threshold: float = 92.0,
    require_same_context: bool = True,
    rel_tol: Decimal | float = Decimal("1e-6"),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Backward-compatible 4-tuple wrapper around `deduplicate_records_detailed`."""
    canonical, dup_log, ctx_log, conflicts, _ = deduplicate_records_detailed(
        records,
        title_threshold=title_threshold,
        require_same_context=require_same_context,
        rel_tol=rel_tol,
    )
    return canonical, dup_log, ctx_log, conflicts
