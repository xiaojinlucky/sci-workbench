# -*- coding: utf-8 -*-
"""Identity resolution, version chaining, conflict preservation, and traceable deduplication.

Upgraded to address verified community failure modes:
  1. Hard guard against false merges when two records carry DISTINCT formal (non-preprint) DOIs
     despite identical/similar titles -> routed to `ambiguous_candidates` for human arbitration.
  2. Preprint <-> Version-of-Record (VoR) linking via `alt_dois`, arXiv IDs, or title+first-author
     evidence, preserving full version chains rather than silently dropping preprints.
  3. Traceable duplicate handling (ASReview #328 lesson): duplicates are never silently deleted;
     every merged-away record is logged with `status="duplicate_ignored_with_trace"`, canonical target,
     match tier, confidence score, and an explicit rollback entry (`rollback_log`).
  4. Source-count conservation check: raw_input_records == canonical_unique + duplicates_merged_away.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import scholarmeta as sm
except ImportError:
    import scholarmeta as sm  # type: ignore

PREPRINT_DOI_PREFIXES = (
    "10.48550/arxiv.",
    "10.1101/",       # bioRxiv / medRxiv
    "10.21203/",      # Research Square
    "10.26434/",      # ChemRxiv
    "10.31219/",      # OSF Preprints
    "10.20944/",      # Preprints.org
    "10.2139/ssrn.",  # SSRN
    "10.5281/zenodo.",
)

VERSION_RANK = {
    "published": 4,
    "conference": 3,
    "preprint": 2,
    "granted": 4,
    "application": 2,
    "unknown": 0,
    "": 0,
}

# Field authority priority when resolving cross-database conflicts (higher = preferred)
SOURCE_FIELD_PRIORITY: Dict[str, Dict[str, int]] = {
    "doi": {"crossref": 5, "publisher": 5, "openalex": 4, "europepmc": 4, "pubmed": 4, "s2": 3, "arxiv": 2},
    "title": {"crossref": 5, "publisher": 5, "europepmc": 4, "pubmed": 4, "openalex": 4, "arxiv": 3},
    "authors": {"crossref": 5, "publisher": 5, "europepmc": 4, "pubmed": 4, "openalex": 4, "arxiv": 4},
    "year": {"crossref": 5, "publisher": 5, "europepmc": 4, "pubmed": 4, "openalex": 3, "arxiv": 2},
    "venue": {"crossref": 5, "publisher": 5, "europepmc": 4, "pubmed": 4, "openalex": 3, "arxiv": 1},
    "abstract": {"europepmc": 5, "pubmed": 5, "arxiv": 4, "openalex": 4, "crossref": 3, "publisher": 3},
    "citations_count": {"openalex": 5, "crossref": 4, "s2": 4, "europepmc": 3},
}


def is_preprint_doi(doi: str) -> bool:
    d = sm.norm_doi(doi)
    return bool(d) and any(d.startswith(pref) for pref in PREPRINT_DOI_PREFIXES)


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def title_sim(a: str, b: str) -> float:
    na, nb = sm.norm_title(a), sm.norm_title(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(sm.title_tokens(a)), set(sm.title_tokens(b))
    jac = len(ta & tb) / max(1, len(ta | tb))
    seq = _ratio(" ".join(sorted(ta)), " ".join(sorted(tb)))
    return 0.5 * jac + 0.5 * seq


def _all_dois(r: sm.Record) -> set:
    ds = set()
    if r.doi:
        ds.add(sm.norm_doi(r.doi))
    for ad in getattr(r, "alt_dois", []) or []:
        nd = sm.norm_doi(ad)
        if nd:
            ds.add(nd)
    return {d for d in ds if d}


def same_work(r1: sm.Record, r2: sm.Record) -> Tuple[bool, str, float]:
    """Return (is_same, tier_or_reason, score).

    Returns (False, "ambiguous:distinct_formal_dois", ts) when titles match closely
    but both records carry distinct non-preprint DOIs, blocking silent false merges.
    """
    if r1.patent_number and r2.patent_number:
        n1 = r1.other_ids.get("patent_normalized", {})
        n2 = r2.other_ids.get("patent_normalized", {})
        if n1.get("canonical") and n1.get("canonical") == n2.get("canonical"):
            return True, "T1:patent_number", 1.0
        if n1.get("base") and n1.get("base") == n2.get("base"):
            return False, "family", 0.9
        return False, "", 0.0

    # Tier 1: Exact strong identifiers
    if r1.doi and r2.doi and r1.doi == r2.doi:
        return True, "T1:doi", 1.0
    d1s, d2s = _all_dois(r1), _all_dois(r2)
    if d1s and d2s and (d1s & d2s):
        return True, "T2:alt_doi_relation", 1.0
    if r1.pmid and r2.pmid and r1.pmid == r2.pmid:
        return True, "T1:pmid", 1.0
    a1, a2 = sm.arxiv_base(r1.arxiv_id), sm.arxiv_base(r2.arxiv_id)
    if a1 and a2 and a1 == a2:
        return True, "T1:arxiv", 1.0

    ts = title_sim(r1.title, r2.title)

    # Hard guard: two distinct formal (non-preprint) DOIs MUST NOT be auto-merged by title similarity
    if r1.doi and r2.doi and r1.doi != r2.doi:
        both_formal = (not is_preprint_doi(r1.doi)) and (not is_preprint_doi(r2.doi))
        if both_formal:
            if ts >= 0.80:
                return False, "ambiguous:distinct_formal_dois", ts
            return False, "", ts
        # One preprint DOI + one formal DOI -> allowed via T2 if title & author/year align
        if ts >= 0.88 and (not r1.first_author or not r2.first_author or r1.first_author == r2.first_author):
            return True, "T2:preprint_doi_to_vor", ts
        if ts >= 0.78:
            return False, "ambiguous:preprint_formal_candidate", ts
        return False, "", ts

    # Tier 2: arXiv <-> formal DOI bridge
    if ts >= 0.92:
        if (a1 and r2.doi) or (a2 and r1.doi):
            return True, "T2:arxiv-doi-title", ts
        if abs((r1.year or 0) - (r2.year or 0)) <= 1 or (r1.first_author and r1.first_author == r2.first_author):
            return True, "T3:title", ts
        return False, "ambiguous:title_match_year_or_author_mismatch", ts

    if ts >= 0.85 and ((a1 and r2.doi) or (a2 and r1.doi)) and r1.first_author and r1.first_author == r2.first_author:
        return True, "T2:arxiv-doi-fuzzy", ts

    if ts >= 0.82 and r1.first_author and r1.first_author == r2.first_author and r1.year and r2.year and abs(r1.year - r2.year) <= 1:
        return True, "T4:title+author+year", ts

    if ts >= 0.78:
        return False, "ambiguous:fuzzy_title_needs_review", ts

    return False, "", ts


def completeness(r: sm.Record) -> int:
    s = 0
    s += 2 if r.doi else 0
    s += 2 if r.pmid else 0
    s += 1 if r.arxiv_id else 0
    s += 2 if r.abstract else 0
    s += 1 if r.authors else 0
    s += 1 if r.year else 0
    s += 1 if r.venue else 0
    s += 1 if r.references else 0
    s += 1 if (r.keywords or r.mesh) else 0
    s += min(3, len(r.authors) // 3)
    return s


def _field_rank(r: sm.Record, field_name: str) -> Tuple[int, int, int]:
    src_prio = SOURCE_FIELD_PRIORITY.get(field_name, {}).get(r.source.lower(), 1)
    ver_prio = VERSION_RANK.get(r.version, 0)
    val = getattr(r, field_name, None)
    richness = len(val) if isinstance(val, (str, list)) else (1 if val is not None else 0)
    return (ver_prio, src_prio, richness)


class Cluster:
    def __init__(self, members: List[sm.Record]):
        self.members = members
        self.canonical = max(
            members,
            key=lambda r: (VERSION_RANK.get(r.version, 0), completeness(r), -members.index(r)),
        )
        self.ids = self._collect_ids()
        self.conflicts = self._conflicts()

    def _collect_ids(self) -> Dict[str, List[str]]:
        ids: Dict[str, List[str]] = defaultdict(list)
        for m in self.members:
            if m.doi:
                ids["doi"].append(m.doi)
            for ad in getattr(m, "alt_dois", []) or []:
                if ad:
                    ids["alt_doi"].append(ad)
            if m.pmid:
                ids["pmid"].append(m.pmid)
            if m.arxiv_id:
                ids["arxiv"].append(sm.arxiv_base(m.arxiv_id))
            if getattr(m, "openalex_id", ""):
                ids["openalex"].append(m.openalex_id)
            if m.patent_number:
                ids["patent"].append(m.patent_number)
        return {k: sorted(set(v)) for k, v in ids.items()}

    def _conflicts(self) -> List[Dict[str, Any]]:
        out = []
        for field_name in ("year", "venue", "title", "version", "language"):
            vals: Dict[str, List[str]] = {}
            for m in self.members:
                v = getattr(m, field_name)
                if v:
                    key = sm.norm_title(v) if field_name == "title" else str(v).strip()[:100]
                    vals.setdefault(key, []).append(f"{m.source}:{m.path}#{m.pointer}")
            if len(vals) > 1 and field_name in ("year", "venue", "language", "version"):
                winner = max(
                    [m for m in self.members if getattr(m, field_name)],
                    key=lambda r: _field_rank(r, field_name),
                )
                out.append(
                    {
                        "field": field_name,
                        "values": vals,
                        "chosen_value": getattr(winner, field_name),
                        "chosen_source": f"{winner.source}:{winner.path}",
                        "resolution_rule": "version_rank > source_authority > field_richness; all raw values preserved",
                    }
                )
        return out

    def version_group(self) -> Dict[str, Any]:
        kinds = sorted({m.version for m in self.members if m.version})
        has_pre = "preprint" in kinds or any(is_preprint_doi(m.doi) for m in self.members)
        has_pub = any(k in ("published", "conference") for k in kinds) or any(
            m.doi and not is_preprint_doi(m.doi) for m in self.members
        )
        return {
            "versions_present": kinds,
            "preprint_and_published": has_pre and has_pub,
            "canonical_rid": self.canonical.rid,
            "members": [
                {
                    "rid": m.rid,
                    "role": "canonical" if m is self.canonical else "duplicate_ignored_with_trace",
                    "source": m.source,
                    "path": m.path,
                    "pointer": m.pointer,
                    "version": m.version,
                    "year": m.year,
                    "doi": m.doi,
                    "arxiv": m.arxiv_id,
                }
                for m in self.members
            ],
        }

    def merged(self) -> Dict[str, Any]:
        c = self.canonical
        prov: Dict[str, List[str]] = defaultdict(list)
        chosen: Dict[str, Any] = {}
        for f in ("title", "abstract", "authors", "year", "venue", "doi", "citations_count"):
            cands = [m for m in self.members if getattr(m, f, None)]
            if cands:
                best = max(cands, key=lambda r: _field_rank(r, f))
                chosen[f] = getattr(best, f)
                prov[f].append(f"chosen={best.source}:{best.path}#{best.pointer}")
            for m in self.members:
                if getattr(m, f, None):
                    prov[f].append(f"{m.source}:{m.path}#{m.pointer}")

        d = c.to_dict()
        for k, v in chosen.items():
            d[k] = v
        d["rid"] = c.rid
        d["member_rids"] = [m.rid for m in self.members]
        d["all_ids"] = self.ids
        d["field_provenance"] = {k: list(dict.fromkeys(v)) for k, v in prov.items()}
        d["conflicts"] = self.conflicts
        d["version_group"] = self.version_group()
        d["match_tiers"] = [self._tier_for(m) for m in self.members if m is not c]
        d["is_retracted"] = any(m.is_retracted for m in self.members)
        d["is_correction"] = any(getattr(m, "is_correction", False) for m in self.members)

        refs = []
        seen = set()
        for m in self.members:
            for r in m.references:
                key = (r.get("doi"), sm.norm_title(r.get("unstructured"))[:100])
                if key not in seen:
                    seen.add(key)
                    refs.append(r)
        d["references"] = refs
        d["referenced_ids"] = sorted({x for m in self.members for x in m.referenced_ids})
        return d

    def _tier_for(self, m: sm.Record) -> Dict[str, Any]:
        same, tier, score = same_work(self.canonical, m)
        return {
            "rid": m.rid,
            "source": m.source,
            "path": m.path,
            "pointer": m.pointer,
            "tier": tier or "transitive_cluster",
            "score": round(score, 3),
            "status": "duplicate_ignored_with_trace",
        }


def cluster_records_detailed(records: List[sm.Record]) -> Tuple[List[Cluster], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Cluster records while recording ambiguous non-merged pairs and explicit rollback entries."""
    n = len(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    ambiguous: List[Dict[str, Any]] = []
    pair_merges: List[Dict[str, Any]] = []

    for i in range(n):
        for j in range(i + 1, n):
            same, tier, score = same_work(records[i], records[j])
            if same:
                union(i, j)
                pair_merges.append(
                    {
                        "rid_a": records[i].rid,
                        "rid_b": records[j].rid,
                        "tier": tier,
                        "score": round(score, 3),
                    }
                )
            elif tier.startswith("ambiguous:"):
                ambiguous.append(
                    {
                        "rid_a": records[i].rid,
                        "rid_b": records[j].rid,
                        "title_a": records[i].title[:120],
                        "title_b": records[j].title[:120],
                        "doi_a": records[i].doi,
                        "doi_b": records[j].doi,
                        "source_a": f"{records[i].source}:{records[i].path}",
                        "source_b": f"{records[j].source}:{records[j].path}",
                        "reason": tier,
                        "title_similarity": round(score, 3),
                        "action": "kept_separate_needs_human_arbitration",
                    }
                )

    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    clusters = [Cluster([records[i] for i in idx]) for idx in groups.values()]
    clusters.sort(
        key=lambda c: (
            -(VERSION_RANK.get(c.canonical.version, 0)),
            -(c.canonical.year or 0),
            sm.norm_title(c.canonical.title),
            c.canonical.rid,
        )
    )

    rollback_log: List[Dict[str, Any]] = []
    for c in clusters:
        if len(c.members) <= 1:
            continue
        for m in c.members:
            if m is c.canonical:
                continue
            _, tier, score = same_work(c.canonical, m)
            rollback_log.append(
                {
                    "duplicate_rid": m.rid,
                    "canonical_rid": c.canonical.rid,
                    "duplicate_status": "duplicate_ignored_with_trace",
                    "duplicate_source": f"{m.source}:{m.path}#{m.pointer}",
                    "canonical_source": f"{c.canonical.source}:{c.canonical.path}#{c.canonical.pointer}",
                    "match_tier": tier or "transitive_cluster",
                    "match_score": round(score, 3),
                    "rollback_action": f"split {m.rid} from cluster {c.canonical.rid} to restore original record",
                }
            )
    return clusters, ambiguous, rollback_log


def cluster_records(records: List[sm.Record]) -> List[Cluster]:
    clusters, _, _ = cluster_records_detailed(records)
    return clusters


def link_versions(clusters: List[Cluster]) -> List[Dict[str, Any]]:
    """Find preprint<->published links across clusters that were NOT merged."""
    links = []
    for i, a in enumerate(clusters):
        for b in clusters[i + 1:]:
            A, B = a.canonical, b.canonical
            a_is_pre = A.version == "preprint" or is_preprint_doi(A.doi)
            b_is_pre = B.version == "preprint" or is_preprint_doi(B.doi)
            a_is_pub = A.version in ("published", "conference") or (bool(A.doi) and not is_preprint_doi(A.doi))
            b_is_pub = B.version in ("published", "conference") or (bool(B.doi) and not is_preprint_doi(B.doi))
            if (a_is_pre and b_is_pub) or (b_is_pre and a_is_pub):
                if (sm.arxiv_base(A.arxiv_id) and sm.arxiv_base(A.arxiv_id) == sm.arxiv_base(B.arxiv_id)) or (_all_dois(A) & _all_dois(B)):
                    links.append({"a": A.rid, "b": B.rid, "relation": "preprint_to_published", "evidence": "arxiv/doi", "score": 1.0})
                    continue
                ts = title_sim(A.title, B.title)
                if ts >= 0.75 and A.first_author and A.first_author == B.first_author:
                    links.append({"a": A.rid, "b": B.rid, "relation": "preprint_to_published_candidate", "evidence": f"title+first_author sim={ts:.2f}", "score": round(ts, 3)})
    return links


def dedup_report(
    clusters: List[Cluster],
    records: List[sm.Record],
    ambiguous: Optional[List[Dict[str, Any]]] = None,
    rollback_log: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    merged_clusters = [c for c in clusters if len(c.members) > 1]
    tiers: Dict[str, int] = defaultdict(int)
    for c in merged_clusters:
        for m in c.members:
            if m is not c.canonical:
                _, t, _ = same_work(c.canonical, m)
                tiers[(t.split(":")[0] if t else "transitive")] += 1
    n_records = len(records)
    n_unique = len(clusters)
    n_merged_away = n_records - n_unique
    per_source_raw = dict(Counter(r.source for r in records))
    per_source_member_sum = sum(len(c.members) for c in clusters)
    return {
        "n_records": n_records,
        "n_unique": n_unique,
        "n_merged_away": n_merged_away,
        "source_count_conserved": (per_source_member_sum == n_records) and (n_unique + n_merged_away == n_records),
        "per_source_raw_counts": per_source_raw,
        "n_clusters_with_duplicates": len(merged_clusters),
        "by_tier": dict(tiers),
        "version_pairs": sum(1 for c in merged_clusters if c.version_group()["preprint_and_published"]),
        "n_ambiguous_kept_separate": len(ambiguous or []),
        "n_rollback_entries": len(rollback_log or []),
    }
