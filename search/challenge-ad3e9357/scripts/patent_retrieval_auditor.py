"""Patent & Technical Literature Auditor: WIPO ST.16 Normalization, Dual DOCDB/INPADOC Families,
CPC/IPC Slash-Safe Queries, Claim Decomposition, WO OCR Boundary & 3-Perspective Comparison (challenge-ad3e9357).

Pure-stdlib offline implementation addressing concrete patent source boundaries:
- Parses WIPO ST.16 patent numbers strictly separating publication_number, application_number,
  grant_number (B/B1/B2/C), published applications (A1/A2/A3), utility models (U/U1/Y), and
  reissues/corrections (E/A4), alongside filing/priority/publication/grant dates, assignees, inventors.
- Computes dual family counts side-by-side (Espacenet 118 vs 71):
    1) publication_document_count (distinct publication docs including A1/A4/B1/B2)
    2) docdb_simple_family_count (exact identical normalized priority set equivalence classes)
    3) inpadoc_extended_family_count (Union-Find connected components sharing >=1 priority or parent link)
  Explicitly tags observed_links_only_not_exhaustive and never merges unrelated keyword-similar patents.
- Normalizes CPC/IPC codes and emits slash-safe query variants (pyUSPTO #101) strictly separated from
  scientific/technical literature queries.
- Extracts source-located Claim 1 & dependent claim counts with equal_claim_scope_assumed=False,
  detects WO merged/OCR claim boundaries (including HTML class="claims ocr" and scanned PDFs),
  keeps current_official_legal_status=UNKNOWN offline, and renders patent_landscape_report.md.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log
from citation_graph_dedup import (
    extract_and_normalize_cpc_ipc,
    normalize_patent_number,
    normalize_priority_key,
)
from corpus_loader import load_input_bundle
from evidence_state_guard import EvidenceState

PATENT_HEADER_RE = re.compile(
    r"^(?:(?:publication|patent)\s+(?:number|no\.?)|公开(?:号|编号)|专利(?:号|编号))\s*[:：]\s*"
    r"((?:US|EP|WO|CN|JP|KR|NL|DE|FR|AU|CA)[ /-]?\d{4,12}(?:[ /-]?[A-Z]\d?)?)\b",
    re.IGNORECASE,
)
PATENT_FILENAME_RE = re.compile(r"^((?:US|EP|WO|CN|JP|KR|NL|DE|FR|AU|CA)\d{4,12}[A-Z]\d?)$", re.I)
DEPENDENT_CLAIM_CUE_RE = re.compile(
    r"\b(?:of|to|in|according\s+to|as\s+(?:claimed|defined|set\s+forth)\s+in)\s+claims?\s+\d+|"
    r"如?权利要求\s*\d+|根据?权利要求\s*\d+",
    re.I,
)


class UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def _as_str_list(val: Any) -> list[str]:
    if not val:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip() for x in re.split(r"[;|]", val) if x.strip()]
    return [str(val).strip()]


def _decompose_claims(claims_text: str, source_label: str) -> dict[str, Any]:
    """Extract source-located Claim 1 excerpt and independent vs dependent claim counts."""
    clean = " ".join((claims_text or "").split())
    if not clean:
        return {
            "claim_version_source": source_label,
            "claims_present_in_source": False,
            "observed_claim_count": 0,
            "independent_claim_count": 0,
            "dependent_claim_count": 0,
            "independent_claim_1_excerpt": None,
            "equal_claim_scope_assumed": False,
        }

    # Remove leading header like "Claims ( 10 )" or "Claims CLAIMS."
    body = re.sub(r"^(?:Claims\s*(?:\(\s*\d+\s*\))?\s*(?:CLAIMS\.?)?\s*)", "", clean, flags=re.I)
    # Split numbered claims: "1. ...", "2. ..."
    matches = list(re.finditer(r"(?:^|\s)(\d{1,3})\.\s+", body))
    claims_list: list[tuple[int, str]] = []
    expected_num = 1
    filtered_starts: list[tuple[int, int, int]] = []
    for m in matches:
        num = int(m.group(1))
        if num == expected_num or (filtered_starts and num == filtered_starts[-1][0] + 1):
            filtered_starts.append((num, m.start(), m.end()))
            expected_num = num + 1
    for i, (num, _st, end_pos) in enumerate(filtered_starts):
        nxt_st = filtered_starts[i + 1][1] if i + 1 < len(filtered_starts) else len(body)
        ctext = body[end_pos:nxt_st].strip()
        if ctext:
            claims_list.append((num, ctext))

    if not claims_list:
        # Fallback: check single Claim 1 mention
        m1 = re.search(r"claim\s+1\b(.{10,260})", clean, re.I)
        excerpt = f"Claim 1{m1.group(1)}".strip()[:260] if m1 else clean[:260]
        return {
            "claim_version_source": source_label,
            "claims_present_in_source": True,
            "observed_claim_count": 1,
            "independent_claim_count": 1,
            "dependent_claim_count": 0,
            "independent_claim_1_excerpt": excerpt,
            "equal_claim_scope_assumed": False,
        }

    indep_cnt = 0
    dep_cnt = 0
    claim1_excerpt = None
    for num, ctext in claims_list:
        if num == 1 and claim1_excerpt is None:
            claim1_excerpt = f"1. {ctext}"[:280]
        if num > 1 and DEPENDENT_CLAIM_CUE_RE.search(ctext[:220]):
            dep_cnt += 1
        else:
            indep_cnt += 1

    return {
        "claim_version_source": source_label,
        "claims_present_in_source": True,
        "observed_claim_count": len(claims_list),
        "independent_claim_count": indep_cnt,
        "dependent_claim_count": dep_cnt,
        "independent_claim_1_excerpt": claim1_excerpt,
        "equal_claim_scope_assumed": False,
    }


def _is_patent_record(r: dict[str, Any]) -> tuple[bool, str]:
    header = PATENT_HEADER_RE.match(str(r.get("title") or "").lstrip())
    header = header or PATENT_HEADER_RE.match(str(r.get("text") or "").lstrip())
    fname_m = PATENT_FILENAME_RE.match(Path(str(r.get("source_file") or "")).stem)
    is_pat = bool(
        r.get("patent_number")
        or r.get("publication_number")
        or r.get("application_number")
        or r.get("grant_number")
        or str(r.get("kind", r.get("type", ""))).casefold() == "patent"
        or str(r.get("source", "")).casefold() in {"patent", "patents", "google_patents_html", "patents_list"}
        or header
        or fname_m
    )
    inferred_num = (header.group(1) if header else "") or (fname_m.group(1) if fname_m else "")
    return is_pat, inferred_num


def parse_patent_records(bundle: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Parse and consolidate multi-format views of the same normalized publication number while keeping distinct kind codes separate."""
    raw_patents: list[tuple[dict[str, Any], str]] = []
    scientific_papers: list[dict[str, Any]] = []

    for r in bundle["records"]:
        is_pat, inferred_num = _is_patent_record(r)
        if is_pat:
            raw_patents.append((r, inferred_num))
        else:
            scientific_papers.append(r)

    # Consolidate multi-format views of the EXACT same normalized publication_number (e.g. HTML + JSON list + PDF facsimile)
    # while keeping DIFFERENT kind codes (e.g. A1 vs A4, A vs B) strictly separate!
    by_pub_key: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    ordered_keys: list[str] = []
    for idx, (p, inf_num) in enumerate(raw_patents, start=1):
        pub_raw = str(p.get("publication_number") or p.get("patent_number") or inf_num or "")
        norm_pub = normalize_patent_number(pub_raw) if pub_raw else None
        key = norm_pub["normalized"] if (norm_pub and norm_pub["normalized"]) else f"UNNUMBERED-{idx:03d}"
        if key not in by_pub_key:
            ordered_keys.append(key)
        by_pub_key[key].append((p, inf_num))

    parsed: list[dict[str, Any]] = []
    for idx, pub_key in enumerate(ordered_keys, start=1):
        views = by_pub_key[pub_key]
        # Prefer richer HTML/structured record as primary metadata view
        views_sorted = sorted(
            views,
            key=lambda x: (
                0 if x[0].get("source") == "google_patents_html" else (1 if x[0].get("claims_text") else (2 if x[0].get("source") != "pdf" else 3)),
                -len(str(x[0].get("text") or "")),
            ),
        )
        primary = views_sorted[0][0]
        source_files = list(dict.fromkeys(str(v[0].get("source_file") or "") for v in views if v[0].get("source_file")))
        sources = list(dict.fromkeys(str(v[0].get("source") or "") for v in views if v[0].get("source")))

        pub_raw = next((str(v[0].get("publication_number") or v[0].get("patent_number") or v[1]) for v in views_sorted if (v[0].get("publication_number") or v[0].get("patent_number") or v[1])), "")
        app_raw = next((str(v[0].get("application_number")) for v in views_sorted if v[0].get("application_number")), "")
        grant_raw = next((str(v[0].get("grant_number")) for v in views_sorted if v[0].get("grant_number")), "")

        norm_pub = normalize_patent_number(pub_raw) if pub_raw else None
        norm_app = normalize_patent_number(app_raw) if app_raw else None
        norm_grant = normalize_patent_number(grant_raw) if grant_raw else None
        if not norm_grant and norm_pub and norm_pub["is_grant"]:
            norm_grant = dict(norm_pub)

        # Merge priority numbers, parent apps, family members, CPC/IPC, dates, assignees, inventors across views
        priorities_raw: list[str] = []
        parent_apps_raw: list[str] = []
        docdb_members_raw: list[str] = []
        obs_fam_members_raw: list[str] = []
        ipc_raw: list[str] = []
        assignees: list[str] = []
        inventors: list[str] = []
        text_chunks: list[str] = []
        claims_text = ""
        claims_source = "snapshot_abstract_only"
        wo_ocr_reasons: list[str] = []

        priority_date = None
        filing_date = None
        publication_date = None
        grant_date = None
        observed_status = None
        status_date = None
        license_tier = None
        declared_docdb_id = None
        declared_inpadoc_id = None

        for v, _ in views_sorted:
            for pk in ("priority_numbers", "priority_number", "priorities"):
                priorities_raw.extend(_as_str_list(v.get(pk)))
            for pk in ("parent_applications", "parent_application"):
                parent_apps_raw.extend(_as_str_list(v.get(pk)))
            docdb_members_raw.extend(_as_str_list(v.get("docdb_family_members")))
            obs_fam_members_raw.extend(_as_str_list(v.get("observed_family_members")))
            ipc_raw.extend(_as_str_list(v.get("ipc_cpc") or v.get("classification")))
            assignees.extend(_as_str_list(v.get("assignee") or v.get("assignees")))
            inventors.extend(_as_str_list(v.get("inventors") or v.get("inventor")))
            if v.get("priority_date") and not priority_date:
                priority_date = str(v["priority_date"])
            if v.get("filing_date") and not filing_date:
                filing_date = str(v["filing_date"])
            if v.get("publication_date") and not publication_date:
                publication_date = str(v["publication_date"])
            if v.get("grant_date") and not grant_date:
                grant_date = str(v["grant_date"])
            if v.get("legal_status") and not observed_status:
                observed_status = str(v["legal_status"]).strip()
            if v.get("legal_status_date") and not status_date:
                status_date = str(v["legal_status_date"]).strip()
            if v.get("license_tier") and not license_tier:
                license_tier = str(v["license_tier"]).strip()
            if v.get("docdb_family_id") and not declared_docdb_id:
                declared_docdb_id = str(v["docdb_family_id"]).strip()
            if (v.get("inpadoc_family_id") or v.get("family_id")) and not declared_inpadoc_id:
                declared_inpadoc_id = str(v.get("inpadoc_family_id") or v.get("family_id")).strip()

            if v.get("claims_text") and not claims_text:
                claims_text = str(v["claims_text"])
                claims_source = f"{v.get('source_file') or v.get('record_id')}:claims"
            elif not claims_text and "claim 1" in str(v.get("text") or "").lower():
                claims_text = str(v["text"])
                claims_source = f"{v.get('source_file') or v.get('record_id')}:text"

            if v.get("claims_ocr_detected"):
                wo_ocr_reasons.append(f"html_claims_ocr_class:{v.get('source_file')}")
            if v.get("ocr_boundary"):
                wo_ocr_reasons.append(f"explicit_ocr_boundary_flag:{v.get('record_id')}")
            if v.get("source") == "pdf" and v.get("text_layer_status") in {"empty_or_scanned", "sparse_text"}:
                wo_ocr_reasons.append(f"pdf_{v.get('text_layer_status')}:{v.get('source_file')}")

            text_chunks.append(" ".join(str(v.get(k) or "") for k in ("title", "abstract", "text", "claims_text")))

        if norm_pub and norm_pub.get("jurisdiction") == "WO":
            wo_ocr_reasons.append("wo_pct_publication_ocr_caveat")
        if "ocr" in " ".join(text_chunks).lower() and "wo" in (pub_raw.lower() + " " + " ".join(text_chunks).lower()[:500]):
            if not any("ocr" in r for r in wo_ocr_reasons):
                wo_ocr_reasons.append("text_wo_ocr_reference")

        # Never populate grant_date for non-grant published applications (A1/A2/A3) unless explicitly granted
        if norm_pub and norm_pub["is_published_application"] and not norm_grant:
            grant_date = None

        all_text_blob = " ".join(text_chunks)
        cpc_ipc_structured = extract_and_normalize_cpc_ipc(ipc_raw + [all_text_blob])

        norm_priorities = sorted({normalize_priority_key(p) for p in priorities_raw if normalize_priority_key(p)})
        norm_parents = sorted({normalize_priority_key(p) for p in parent_apps_raw if normalize_priority_key(p)})

        claims_info = _decompose_claims(claims_text, claims_source)
        title = next((str(v[0].get("title")).strip() for v in views_sorted if v[0].get("title") and str(v[0].get("title")).strip() != Path(str(v[0].get("source_file") or "")).stem), str(primary.get("title") or pub_key))
        abstract = next((str(v[0].get("abstract")).strip() for v in views_sorted if v[0].get("abstract")), "")

        pid = (norm_pub["normalized"] if (norm_pub and norm_pub["normalized"]) else str(primary.get("record_id") or f"PAT-{idx:03d}"))

        parsed.append({
            "record_id": pid,
            "title": title,
            "abstract": abstract[:500],
            "publication_number": norm_pub["normalized"] if norm_pub else (pub_raw or None),
            "application_number": norm_app["normalized"] if norm_app else (app_raw or None),
            "grant_number": norm_grant["normalized"] if norm_grant else None,
            "kind_code": norm_pub["kind_code"] if norm_pub else None,
            "stage": norm_pub["stage"] if norm_pub else "unknown",
            "wipo_st16_meaning": norm_pub["wipo_st16_meaning"] if norm_pub else "Unknown",
            "jurisdiction": norm_pub["jurisdiction"] if norm_pub else None,
            "publication_parsed": norm_pub,
            "grant_parsed": norm_grant,
            "application_parsed": norm_app,
            "priority_date": priority_date,
            "filing_date": filing_date,
            "publication_date": publication_date,
            "grant_date": grant_date,
            "assignee": list(dict.fromkeys(assignees)),
            "inventors": list(dict.fromkeys(inventors)),
            "priority_numbers_raw": sorted(set(priorities_raw)),
            "priority_numbers_normalized": norm_priorities,
            "parent_applications_normalized": norm_parents,
            "declared_docdb_family_id": declared_docdb_id,
            "declared_inpadoc_family_id": declared_inpadoc_id,
            "docdb_family_members_observed": sorted(set(docdb_members_raw)),
            "observed_family_members": sorted(set(obs_fam_members_raw)),
            "ipc_cpc_codes": [c["canonical"] for c in cpc_ipc_structured],
            "ipc_cpc_query_variants": cpc_ipc_structured,
            "claims_analysis": claims_info,
            "claim_version_source": claims_info["claim_version_source"],
            "equal_claim_scope_assumed": False,
            "observed_snapshot_legal_status": observed_status,
            "legal_status_snapshot_date": status_date or publication_date,
            "legal_status_source": ", ".join(sources) if sources else "local_snapshot",
            "current_official_legal_status": EvidenceState.UNKNOWN.value,
            "legal_status_note": "Current live official register status is UNKNOWN in offline finite snapshot; observed_snapshot_legal_status reflects static source snapshot only.",
            "wo_merged_ocr_boundary": bool(wo_ocr_reasons),
            "wo_ocr_reasons": sorted(set(wo_ocr_reasons)),
            "source_license_tier": license_tier or "green_local_authorized_snapshot",
            "source_files": source_files,
            "sources": sources,
            "_search_blob": all_text_blob,
        })

    return parsed, scientific_papers, len(raw_patents)


def group_dual_patent_families(patents: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute DOCDB simple families (exact priority set) vs INPADOC extended families (Union-Find) side by side."""
    # 1. DOCDB Simple Families: strict equivalence on exact normalized priority set (or declared_docdb_family_id / base_number when no priority set)
    docdb_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pat in patents:
        pnorm = pat["priority_numbers_normalized"]
        if pat.get("declared_docdb_family_id"):
            dkey = f"DOCDB:{pat['declared_docdb_family_id']}"
        elif pnorm:
            dkey = "DOCDB_PRIO:" + "+".join(pnorm)
        elif pat.get("publication_parsed") and pat["publication_parsed"].get("base_number"):
            dkey = f"DOCDB_BASE:{pat['publication_parsed']['base_number']}"
        else:
            dkey = f"DOCDB_SINGLETON:{pat['record_id']}"
        docdb_buckets[dkey].append(pat)

    docdb_families: list[dict[str, Any]] = []
    for idx, (dkey, members) in enumerate(sorted(docdb_buckets.items()), start=1):
        fid = f"DOCDB-FAM-{idx:02d}"
        prio_set = sorted({p for m in members for p in m["priority_numbers_normalized"]})
        docdb_families.append({
            "family_id": fid,
            "family_definition": "DOCDB_simple_exact_priority_set",
            "family_key": dkey,
            "exact_priority_set": prio_set,
            "member_count": len(members),
            "member_ids": [m["record_id"] for m in members],
            "jurisdictions": sorted({m["jurisdiction"] for m in members if m.get("jurisdiction")}),
            "observed_links_only_not_exhaustive": True,
            "equal_claim_scope_assumed": False,
        })

    # 2. INPADOC Extended Families: Union-Find connected components sharing >=1 priority, parent application, base number, or explicit observed family link
    rec_ids = [p["record_id"] for p in patents]
    uf = UnionFind(rec_ids)

    key_to_recs: dict[str, list[str]] = defaultdict(list)
    pub_norm_to_rec: dict[str, str] = {}
    for pat in patents:
        rid = pat["record_id"]
        if pat.get("publication_number"):
            pub_norm_to_rec[pat["publication_number"]] = rid
        if pat.get("publication_parsed") and pat["publication_parsed"].get("base_number"):
            key_to_recs[f"BASE:{pat['publication_parsed']['base_number']}"].append(rid)
        if pat.get("application_number"):
            an = normalize_priority_key(pat["application_number"])
            if an:
                key_to_recs[f"APP:{an}"].append(rid)
        if pat.get("declared_inpadoc_family_id"):
            key_to_recs[f"INPADOC_ID:{pat['declared_inpadoc_family_id']}"].append(rid)
        if pat.get("declared_docdb_family_id"):
            key_to_recs[f"DOCDB_ID:{pat['declared_docdb_family_id']}"].append(rid)
        for pr in pat["priority_numbers_normalized"]:
            key_to_recs[f"PRIO:{pr}"].append(rid)
        for pa in pat["parent_applications_normalized"]:
            key_to_recs[f"APP:{pa}"].append(rid)
            key_to_recs[f"PRIO:{pa}"].append(rid)

    link_reasons: dict[tuple[str, str], set[str]] = defaultdict(set)
    for k, rlist in key_to_recs.items():
        if len(rlist) > 1:
            first = rlist[0]
            for other in rlist[1:]:
                uf.union(first, other)
                link_reasons[tuple(sorted([first, other]))].add(k)

    # Also connect if patent A lists patent B explicitly in docdb_family_members or observed_family_members
    for pat in patents:
        rid = pat["record_id"]
        for obs_mem in set(pat.get("docdb_family_members_observed", [])) | set(pat.get("observed_family_members", [])):
            nm = normalize_patent_number(obs_mem)["normalized"]
            if nm in pub_norm_to_rec and pub_norm_to_rec[nm] != rid:
                other = pub_norm_to_rec[nm]
                uf.union(rid, other)
                link_reasons[tuple(sorted([rid, other]))].add(f"OBSERVED_MEMBER:{nm}")

    inpadoc_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pat in patents:
        inpadoc_buckets[uf.find(pat["record_id"])].append(pat)

    inpadoc_families: list[dict[str, Any]] = []
    rec_to_inpadoc: dict[str, str] = {}
    for idx, (_root, members) in enumerate(sorted(inpadoc_buckets.items()), start=1):
        fid = f"INPADOC-FAM-{idx:02d}"
        m_ids = [m["record_id"] for m in members]
        for mid in m_ids:
            rec_to_inpadoc[mid] = fid
        all_prios = sorted({p for m in members for p in m["priority_numbers_normalized"]})
        reasons = sorted({
            r
            for i in range(len(m_ids))
            for j in range(i + 1, len(m_ids))
            for r in link_reasons.get(tuple(sorted([m_ids[i], m_ids[j]])), set())
        })
        inpadoc_families.append({
            "family_id": fid,
            "family_definition": "INPADOC_extended_connected_priority_component",
            "member_count": len(members),
            "member_ids": m_ids,
            "union_priority_keys": all_prios,
            "linking_keys_observed": reasons,
            "jurisdictions": sorted({m["jurisdiction"] for m in members if m.get("jurisdiction")}),
            "grouping_scope": "observed_links_only_not_exhaustive" if len(members) > 1 else "singleton_unlinked_in_snapshot",
            "observed_links_only_not_exhaustive": True,
            "equal_claim_scope_assumed": False,
        })

    # Audit: verify keyword-similar patents WITHOUT shared priority/family links were NOT erroneously merged
    stop = {"a", "an", "the", "of", "for", "and", "in", "to", "with", "method", "thereof", "based", "using", "kind", "preparation"}
    keyword_separated: list[dict[str, Any]] = []
    for i in range(len(patents)):
        for j in range(i + 1, len(patents)):
            pa, pb = patents[i], patents[j]
            if rec_to_inpadoc[pa["record_id"]] == rec_to_inpadoc[pb["record_id"]]:
                continue
            ta = {w for w in re.findall(r"[a-z]{3,}", (pa["title"] or "").lower()) if w not in stop}
            tb = {w for w in re.findall(r"[a-z]{3,}", (pb["title"] or "").lower()) if w not in stop}
            shared_kw = sorted(ta & tb)
            shared_cpc_sub = sorted({c.split()[0] for c in pa["ipc_cpc_codes"]} & {c.split()[0] for c in pb["ipc_cpc_codes"]})
            if len(shared_kw) >= 2 or (shared_kw and shared_cpc_sub):
                keyword_separated.append({
                    "patent_pair": [pa["record_id"], pb["record_id"]],
                    "inpadoc_families": [rec_to_inpadoc[pa["record_id"]], rec_to_inpadoc[pb["record_id"]]],
                    "shared_topic_keywords": shared_kw[:8],
                    "shared_cpc_subclasses": shared_cpc_sub,
                    "action": "kept_separate_families",
                    "reason": "no_shared_priority_or_family_link_observed",
                })

    return {
        "publication_document_count": len(patents),
        "docdb_simple_family_count": len(docdb_families),
        "inpadoc_extended_family_count": len(inpadoc_families),
        "docdb_simple_families": docdb_families,
        "inpadoc_extended_families": inpadoc_families,
        "keyword_only_distinct_patents_separated": keyword_separated,
        "dual_metric_explanation": (
            f"Corpus contains {len(patents)} distinct publication document(s), consolidating into "
            f"{len(docdb_families)} DOCDB simple family(ies) (exact priority set) and "
            f"{len(inpadoc_families)} INPADOC extended family(ies) (shared direct/indirect priority component). "
            "Observed links are non-exhaustive and never imply equal claim scope."
        ),
    }


def build_dual_track_queries(
    patents: list[dict[str, Any]],
    bundle_queries: list[Any],
) -> dict[str, Any]:
    """Generate slash-safe CPC/IPC patent queries strictly separated from scientific literature queries."""
    all_cpc_map: dict[str, dict[str, str]] = {}
    for p in patents:
        for c in p["ipc_cpc_query_variants"]:
            all_cpc_map.setdefault(c["canonical"], c)
    all_cpcs = [all_cpc_map[k] for k in sorted(all_cpc_map)]

    patent_queries: list[dict[str, Any]] = []
    sci_queries: list[dict[str, Any]] = []

    for idx, q in enumerate(bundle_queries, start=1):
        qtxt = q.get("question") or q.get("query") or str(q) if isinstance(q, dict) else str(q)
        qid = str(q.get("query_id") or q.get("id") or f"Q{idx}") if isinstance(q, dict) else f"Q{idx}"
        q_cpcs = extract_and_normalize_cpc_ipc(qtxt)
        q_pats = re.findall(r"\b((?:US|EP|WO|CN|JP|KR|NL)\d{4,12}[A-Z]?\d?)\b", qtxt.upper())
        q_type = "number_lookup" if q_pats else ("classification_topic" if q_cpcs else "technical_topic")

        cpc_used = q_cpcs if q_cpcs else all_cpcs[:4]
        escaped_clause = " OR ".join(c["lucene_escaped_query"] for c in cpc_used[:5]) if cpc_used else ""
        quoted_clause = " OR ".join(c["uspto_safe_query"] for c in cpc_used[:5]) if cpc_used else ""

        patent_queries.append({
            "query_id": qid,
            "query_type": q_type,
            "raw_query": qtxt,
            "detected_patent_numbers": [normalize_patent_number(n) for n in q_pats],
            "cpc_ipc_normalized": cpc_used,
            "slash_safe_uspto_query": quoted_clause or qtxt,
            "slash_safe_lucene_query": escaped_clause or qtxt,
            "pyuspto_slash_escape_applied": bool(cpc_used),
        })
        # Scientific literature query strips CPC/patent numbers to avoid zero-recall in PubMed/OpenAlex/Crossref
        sci_clean = re.sub(r"\b[A-HY]\d{2}[A-Z]\s*\d{1,4}/\d{2,6}\b", " ", qtxt, flags=re.I)
        sci_clean = re.sub(r"\b(?:US|EP|WO|CN|JP|KR|NL)\d{4,12}[A-Z]?\d?\b", " ", sci_clean, flags=re.I)
        sci_clean = " ".join(sci_clean.split())
        sci_queries.append({
            "query_id": qid,
            "scientific_query": sci_clean or qtxt,
            "rationale": "Strips CPC/IPC codes and patent publication numbers so academic index retrieval targets mechanisms, materials, and experimental metrics.",
        })

    if not patent_queries and all_cpcs:
        patent_queries.append({
            "query_id": "AUTO-CPC-01",
            "query_type": "classification_corpus_derived",
            "raw_query": "Corpus-observed CPC/IPC classes",
            "detected_patent_numbers": [],
            "cpc_ipc_normalized": all_cpcs[:8],
            "slash_safe_uspto_query": " OR ".join(c["uspto_safe_query"] for c in all_cpcs[:6]),
            "slash_safe_lucene_query": " OR ".join(c["lucene_escaped_query"] for c in all_cpcs[:6]),
            "pyuspto_slash_escape_applied": True,
        })

    return {
        "corpus_cpc_ipc_catalog": all_cpcs,
        "patent_search_queries": patent_queries,
        "scientific_literature_queries": sci_queries,
    }


def _render_landscape_markdown(report: dict[str, Any]) -> str:
    fam = report["family_consolidation"]
    lines: list[str] = [
        f"# Patent & Technical Literature Landscape Report ({report['case_id']})",
        "",
        f"> **Scope Disclaimer**: {report['scope_disclaimer']}",
        f"> **Legal Boundary**: {report['legal_boundary']}",
        "",
        "## 1. Dual-Metric Count Summary (Espacenet Publication vs DOCDB vs INPADOC)",
        "",
        f"- **Raw Input Patent Records**: {report['summary']['raw_patent_records_ingested']}",
        f"- **Distinct Publication Documents**: **{fam['publication_document_count']}** (separates A1/A2/A3/A4, B/B1/B2, U/Y)",
        f"- **DOCDB Simple Families (Exact Priority Set)**: **{fam['docdb_simple_family_count']}**",
        f"- **INPADOC Extended Families (Connected Priority Component)**: **{fam['inpadoc_extended_family_count']}**",
        f"- **Comparison Scientific Papers (Separate Track)**: {report['summary']['scientific_paper_count']}",
        f"- **WO / Merged OCR Boundary Flagged**: {report['summary']['wo_ocr_boundary_count']}",
        f"- **Keyword-Similar Unlinked Patent Pairs Kept Separate**: {len(fam['keyword_only_distinct_patents_separated'])}",
        "",
        "## 2. Normalized Patent Inventory (WIPO ST.16 & Offline Legal Status)",
        "",
        "| Pub. No. | App. No. | Grant No. | Kind / Stage | Filing / Pub. / Grant Dates | Snapshot Status (Live = UNKNOWN) | Claims (Indep/Dep) | WO/OCR Caveat |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in report["patents"]:
        dates_s = f"{p.get('filing_date') or '-'} / {p.get('publication_date') or '-'} / {p.get('grant_date') or '-'}"
        st_s = f"{p.get('observed_snapshot_legal_status') or 'none'} (live: `{p['current_official_legal_status']}`)"
        ca = p["claims_analysis"]
        cl_s = f"{ca['observed_claim_count']} ({ca['independent_claim_count']}/{ca['dependent_claim_count']})" if ca["claims_present_in_source"] else "none"
        ocr_s = "YES" if p["wo_merged_ocr_boundary"] else "no"
        lines.append(
            f"| `{p.get('publication_number') or p['record_id']}` | `{p.get('application_number') or '-'}` | "
            f"`{p.get('grant_number') or '-'}` | `{p.get('kind_code') or '-'}` ({p['stage']}) | {dates_s} | {st_s} | {cl_s} | {ocr_s} |"
        )

    lines.extend(["", "## 3. Dual Family Reconciliation (DOCDB Simple vs INPADOC Extended)", ""])
    lines.append("### 3.1 DOCDB Simple Families (Exact Priority Set Equivalence)")
    for df in fam["docdb_simple_families"]:
        lines.append(f"- **{df['family_id']}** ({df['member_count']} doc): {', '.join(df['member_ids'])} | priorities: {df['exact_priority_set'] or ['unreported']}")
    lines.append("")
    lines.append("### 3.2 INPADOC Extended Families (Union-Find Connected Priority Components)")
    for inf in fam["inpadoc_extended_families"]:
        lines.append(f"- **{inf['family_id']}** ({inf['member_count']} doc, `{inf['grouping_scope']}`): {', '.join(inf['member_ids'])} | linking keys: {inf['linking_keys_observed'][:6]}")

    lines.extend(["", "## 4. Source-Located Claim 1 Excerpts & Three-Perspective Comparison", ""])
    for p in report["patents"]:
        ca = p["claims_analysis"]
        if ca.get("independent_claim_1_excerpt"):
            lines.append(f"- **{p['record_id']}** (`{ca['claim_version_source']}` | `equal_claim_scope_assumed=false`): {ca['independent_claim_1_excerpt']}")
    lines.append("")
    lines.append("### Comparison Scientific Papers (Strictly Separated from Patents)")
    if report["comparison_scientific_papers"]:
        for sp in report["comparison_scientific_papers"]:
            lines.append(f"- **{sp['record_id']}** (`{sp.get('doi') or 'no-doi'}` | year: {sp.get('year') or '-'}): {sp.get('title')}")
    else:
        lines.append("- No non-patent scientific comparison papers present in this snapshot.")

    lines.extend(["", "## 5. CPC/IPC Slash-Safe Queries (pyUSPTO #101 Compliant)", ""])
    for pq in report["dual_track_queries"]["patent_search_queries"]:
        lines.append(f"- **{pq['query_id']}** (`{pq['query_type']}`):")
        lines.append(f"  - USPTO-safe quoted CPC: `{pq['slash_safe_uspto_query']}`")
        lines.append(f"  - Lucene slash-escaped CPC: `{pq['slash_safe_lucene_query']}`")

    lines.extend([
        "",
        "## 6. Authorization Tiers & Honest Limitations",
        "",
        "- **Green (Authorized Local Snapshot)**: Supplied HTML/PDF/JSON records analyzed strictly within provided scope.",
        "- **Yellow (Secondary / Static Aggregator Snapshots)**: Snapshot legal status labels (e.g., Active/Ceased/Expired) are NEVER equated to current live official patent office register status (`current_official_legal_status = UNKNOWN`).",
        "- **Red (Prohibited)**: No paywall bypass, no unauthorized live scraping, no FTO or patent infringement opinion.",
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
    init_audit_log(audit_file, run_label="ad3e9357-patent-retriever")

    bundle = load_input_bundle(input_dir)
    case_id = bundle["case_id"]
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-ad3e9357")
    budget_guard.tick_phase(budget_file, "normalize_patents_and_dual_families")

    patents, scientific_papers, raw_patent_count = parse_patent_records(bundle)
    family_consolidation = group_dual_patent_families(patents)
    dual_track_queries = build_dual_track_queries(patents, bundle["queries"])

    wo_ocr_count = sum(1 for p in patents if p["wo_merged_ocr_boundary"])
    claims_extracted_count = sum(1 for p in patents if p["claims_analysis"]["claims_present_in_source"])
    kind_breakdown: dict[str, int] = defaultdict(int)
    for p in patents:
        kind_breakdown[p["stage"]] += 1

    clean_patents = [{k: v for k, v in p.items() if not k.startswith("_")} for p in patents]
    comp_papers_out = [
        {
            "record_id": s["record_id"],
            "title": s.get("title"),
            "doi": s.get("doi") or None,
            "year": s.get("year"),
            "venue": s.get("venue") or None,
            "source_file": s.get("source_file"),
            "abstract_excerpt": str(s.get("abstract") or s.get("text") or "")[:240] or None,
        }
        for s in scientific_papers
    ]

    summary_metrics = {
        "raw_patent_records_ingested": raw_patent_count,
        "publication_document_count": family_consolidation["publication_document_count"],
        "patent_count": family_consolidation["publication_document_count"],
        "docdb_simple_family_count": family_consolidation["docdb_simple_family_count"],
        "inpadoc_extended_family_count": family_consolidation["inpadoc_extended_family_count"],
        "family_count": family_consolidation["docdb_simple_family_count"],
        "scientific_paper_count": len(comp_papers_out),
        "wo_ocr_boundary_count": wo_ocr_count,
        "claims_extracted_count": claims_extracted_count,
        "cpc_ipc_unique_count": len(dual_track_queries["corpus_cpc_ipc_catalog"]),
        "keyword_only_distinct_pairs_separated": len(family_consolidation["keyword_only_distinct_patents_separated"]),
    }

    three_perspective_summary = {
        "applicant_claim_scope_perspective": f"Extracted source-located claims for {claims_extracted_count}/{len(clean_patents)} patent documents across stages {dict(sorted(kind_breakdown.items()))}; equal_claim_scope_assumed=False.",
        "technical_implementer_perspective": f"Indexed {len(dual_track_queries['corpus_cpc_ipc_catalog'])} normalized CPC/IPC classes with slash-safe query syntax separated from academic literature queries.",
        "independent_novelty_evaluator_perspective": (
            f"Separated {len(comp_papers_out)} scientific comparison paper(s) from {len(clean_patents)} patent document(s), "
            f"reconciled {family_consolidation['docdb_simple_family_count']} DOCDB simple vs {family_consolidation['inpadoc_extended_family_count']} INPADOC extended families, "
            f"and retained live_legal_status=UNKNOWN and {wo_ocr_count} WO/OCR boundary caveat(s)."
        ),
    }

    report = {
        "case_id": case_id,
        "scope_disclaimer": "Finite local snapshot corpus only. Current official legal status is UNKNOWN; observed links are non-exhaustive families and never imply equal claim scope; WO merged OCR boundaries retained.",
        "legal_boundary": "Informational technical retrieval only — does NOT constitute Freedom-to-Operate (FTO), infringement analysis, patentability opinion, or legal advice.",
        "summary": summary_metrics,
        "stage_breakdown": dict(sorted(kind_breakdown.items())),
        "three_perspective_summary": three_perspective_summary,
        "patents": clean_patents,
        "family_consolidation": family_consolidation,
        "patent_families": family_consolidation["inpadoc_extended_families"],
        "dual_track_queries": dual_track_queries,
        "comparison_scientific_papers": comp_papers_out,
    }
    report_path = output_dir / "patent_search_report.json"
    result_guard.atomic_write_json(report_path, report)

    landscape_md = _render_landscape_markdown(report)
    landscape_path = output_dir / "patent_landscape_report.md"
    landscape_path.write_text(landscape_md, encoding="utf-8")

    append_audit_entry(
        audit_file,
        phase="patent_audit",
        action="dual_family_and_claim_report_written",
        actor="patent_retrieval_auditor.py",
        output=summary_metrics,
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = [
        {
            "code": "LIVE_LEGAL_STATUS_UNKNOWN",
            "severity": "info",
            "source": "patent_retrieval_auditor",
            "message": "Current official legal status is strictly marked UNKNOWN in offline snapshot; observed_snapshot_legal_status reflects static source date only.",
        },
        {
            "code": "OBSERVED_FAMILY_LINKS_NON_EXHAUSTIVE",
            "severity": "info",
            "source": "patent_retrieval_auditor",
            "message": "DOCDB simple and INPADOC extended family counts reflect observed priority/family links within the finite snapshot only and do not imply equal claim scope.",
        },
    ]
    if wo_ocr_count > 0:
        gaps.append({
            "code": "WO_MERGED_OCR_BOUNDARY_RETAINED",
            "severity": "info",
            "source": "patent_retrieval_auditor",
            "message": f"{wo_ocr_count} patent document(s) carry WO/OCR boundary caveat requiring facsimile verification before claim interpretation.",
        })
    if not clean_patents:
        gaps.append({
            "code": "NO_PATENT_RECORDS_PARSED",
            "severity": "warning",
            "source": "patent_retrieval_auditor",
            "message": "No structured patent records found in input directory.",
        })

    patch = {
        "artifacts": ["result.json", "patent_search_report.json", "patent_landscape_report.md"],
        "three_perspective_summary": three_perspective_summary,
        "patents": clean_patents,
        "family_consolidation": family_consolidation,
        "patent_families": family_consolidation["inpadoc_extended_families"],
        "dual_track_queries": dual_track_queries,
        "comparison_scientific_papers": comp_papers_out,
        "metrics": summary_metrics,
        "metric_provenance": {
            "raw_patent_records_ingested": "input_records:patent_subset",
            "publication_document_count": "patent_search_report.json:family_consolidation.publication_document_count",
            "patent_count": "patent_search_report.json:patents",
            "docdb_simple_family_count": "patent_search_report.json:family_consolidation.docdb_simple_family_count",
            "inpadoc_extended_family_count": "patent_search_report.json:family_consolidation.inpadoc_extended_family_count",
            "family_count": "patent_search_report.json:family_consolidation.docdb_simple_family_count",
            "scientific_paper_count": "patent_search_report.json:comparison_scientific_papers",
            "wo_ocr_boundary_count": "patent_search_report.json:patents",
            "claims_extracted_count": "patent_search_report.json:patents",
            "cpc_ipc_unique_count": "patent_search_report.json:dual_track_queries.corpus_cpc_ipc_catalog",
            "keyword_only_distinct_pairs_separated": "patent_search_report.json:family_consolidation.keyword_only_distinct_patents_separated",
        },
        "findings": [
            (
                f"Normalized {summary_metrics['publication_document_count']} distinct patent publication document(s) "
                f"into {summary_metrics['docdb_simple_family_count']} DOCDB simple family(ies) and "
                f"{summary_metrics['inpadoc_extended_family_count']} INPADOC extended family(ies), "
                f"extracted source-located claims for {claims_extracted_count} patent(s), separated "
                f"{len(comp_papers_out)} scientific comparison paper(s), and retained live_legal_status=UNKNOWN "
                f"plus {wo_ocr_count} WO/OCR boundary caveat(s)."
            )
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["patent_search_report.json", "patent_landscape_report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor ad3e9357 Patent & Technical Literature Auditor.")
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
