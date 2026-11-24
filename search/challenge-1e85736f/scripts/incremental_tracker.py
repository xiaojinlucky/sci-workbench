# -*- coding: utf-8 -*-
"""Incremental Literature Discovery, Watermark Delta, 5-Way Triage & Idempotent Alert Tracker (challenge-1e85736f).

Pure Python 3.12 stdlib offline pipeline built on `search_core` and `common_core`:
- Parses subscription_config.json, previous_snapshot.json, current_snapshot.json (or embedded case.json
  fields, or multi-source api/ fallback when no explicit current snapshot exists).
- Normalizes UTC window boundaries [window_start, window_end] and flags date/timezone anomalies
  and PyAlex #103 pagination/rate-limit error snapshots explicitly.
- Runs strict intra-window cross-source deduplication via `search_core.dedup.cluster_records_detailed()`
  and matches canonical clusters against `previous_snapshot` via normalized DOI / alt_dois / arXiv /
  PMID / title+first_author (with distinct-formal-DOI false-merge protection).
- Partitions every raw current record with 100% conservation into 5 categories:
  1. genuinely_new
  2. version_upgrade
  3. retraction_or_correction
  4. delayed_indexed_archival
  5. duplicate_suppressed
- Enforces SHA-256 alert ledger idempotency via `--state-file` (`alert_ledger.json`): re-running the
  same window guarantees `idempotent_rerun: true`, `new_alerts_emitted: 0`, `suppressed_already_notified > 0`.
- Outputs `incremental_delta.csv`, `alert_digest.json`, `watermark_state.json`, `report.md`,
  `audit_log.json`, `run_manifest.json`, and Harbor-compliant `result.json` verified by M2/M5/M6 gates.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from search_core import caseio
from search_core import contract as ct
from search_core import dedup as dd
from search_core import pipeline as pl
from search_core import scholarmeta as sm

RETRACTION_RE = re.compile(r"\b(retract(?:ed|ion)?|withdraw(?:n|al)?)\b", re.I)
CORRECTION_RE = re.compile(r"\b(errat(?:um|a)|corrigend(?:um|a)|correction|expression of concern)\b", re.I)


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_utc(raw: Any, field_name: str, anomalies: List[Dict[str, Any]]) -> Optional[dt.datetime]:
    """Parse ISO date/timestamp into timezone-aware UTC datetime, logging naive/malformed anomalies."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # Date-only YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            d = dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            return d
        except ValueError:
            anomalies.append({"code": "invalid_date_format", "field": field_name, "raw": s})
            return None

    # Year-month YYYY-MM
    if re.fullmatch(r"\d{4}-\d{2}", s):
        try:
            d = dt.datetime.strptime(s + "-01", "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
            anomalies.append({"code": "partial_date_day_assumed_01", "field": field_name, "raw": s})
            return d
        except ValueError:
            anomalies.append({"code": "invalid_date_format", "field": field_name, "raw": s})
            return None

    # Year-only YYYY
    if re.fullmatch(r"\d{4}", s):
        anomalies.append({"code": "partial_date_year_only", "field": field_name, "raw": s})
        return dt.datetime(int(s), 1, 1, tzinfo=dt.timezone.utc)

    # Full ISO-8601 timestamp
    norm_s = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    try:
        parsed = dt.datetime.fromisoformat(norm_s)
    except ValueError:
        anomalies.append({"code": "unparseable_timestamp", "field": field_name, "raw": s})
        return None

    if parsed.tzinfo is None:
        anomalies.append(
            {
                "code": "timezone_missing_assumed_utc",
                "field": field_name,
                "raw": s,
                "detail": "Timestamp lacked explicit timezone offset; normalized assuming UTC.",
            }
        )
        return parsed.replace(tzinfo=dt.timezone.utc)

    if parsed.utcoffset() != dt.timedelta(0):
        anomalies.append(
            {
                "code": "timezone_converted_to_utc",
                "field": field_name,
                "raw": s,
                "normalized_utc": parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    return parsed.astimezone(dt.timezone.utc)


def _fmt_utc(d: Optional[dt.datetime]) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d is not None else ""


def _dict_to_record(raw: Dict[str, Any], *, default_rid: str, source_file: str, pointer: str) -> sm.Record:
    """Convert snapshot record dict into a canonical search_core Record preserving alt_dois & retraction flags."""
    r = sm.parse_record_from(raw, path=source_file, pointer=pointer)
    if r is None:
        r = sm.parse_generic(raw, path=source_file, pointer=pointer)
    rid = str(raw.get("id") or raw.get("record_id") or raw.get("rid") or default_rid).strip()
    r.rid = rid
    if raw.get("source"):
        r.source = str(raw["source"]).strip().lower()
    if raw.get("venue") or raw.get("journal") or raw.get("container-title"):
        r.venue = sm.strip_html(raw.get("venue") or raw.get("journal") or raw.get("container-title"))
    elif r.venue and r.venue.lower() == r.source:
        r.venue = ""
    if raw.get("date"):
        r.date = str(raw["date"]).strip()
    if raw.get("indexed_date"):
        r.indexed_date = str(raw["indexed_date"]).strip()
    if raw.get("updated_date"):
        r.updated_date = str(raw["updated_date"]).strip()
    if raw.get("year") and not r.year:
        r.year = sm._year_from(raw.get("year"))
    elif raw.get("year"):
        r.year = sm._year_from(raw.get("year"))
    if raw.get("doi") or raw.get("DOI"):
        r.doi = sm.norm_doi(raw.get("doi") or raw.get("DOI"))
    alt_raw = raw.get("alt_dois") or raw.get("alternate_dois") or []
    if isinstance(alt_raw, str):
        alt_raw = [alt_raw]
    r.alt_dois = sorted({sm.norm_doi(x) for x in alt_raw if sm.norm_doi(x)})
    if raw.get("arxiv_id") or raw.get("arxiv"):
        r.arxiv_id = sm.norm_arxiv(raw.get("arxiv_id") or raw.get("arxiv"))
    if raw.get("pmid"):
        r.pmid = sm.norm_pmid(raw.get("pmid"))
    if raw.get("version"):
        r.version = str(raw["version"]).strip().lower()
    elif dd.is_preprint_doi(r.doi) or r.source == "arxiv":
        r.version = "preprint"

    blob = f"{r.title} {raw.get('status', '')} {raw.get('note', '')} {raw.get('update_type', '')}"
    r.is_retracted = bool(raw.get("is_retracted")) or bool(RETRACTION_RE.search(blob))
    r.is_correction = bool(raw.get("is_correction")) or (not r.is_retracted and bool(CORRECTION_RE.search(blob)))
    if raw.get("status"):
        r.other_ids["raw_status"] = str(raw["status"])
    if raw.get("note"):
        r.other_ids["raw_note"] = str(raw["note"])
    return r


def _canonical_dedup_key(r: sm.Record) -> str:
    if r.doi:
        return f"doi:{r.doi}"
    if r.pmid:
        return f"pmid:{r.pmid}"
    ab = sm.arxiv_base(r.arxiv_id)
    if ab:
        return f"arxiv:{ab}"
    nt = sm.norm_title(r.title)
    fa = r.first_author
    if nt and fa:
        return f"title_author:{nt[:120]}|{fa}"
    if nt:
        return f"title:{nt[:120]}"
    return f"rid:{r.rid}"


def _all_record_dois(r: sm.Record) -> set[str]:
    out = set()
    if r.doi:
        out.add(sm.norm_doi(r.doi))
    for ad in r.alt_dois or []:
        nd = sm.norm_doi(ad)
        if nd:
            out.add(nd)
    return {x for x in out if x}


def _is_preprint_record(r: sm.Record) -> bool:
    if (r.version or "").lower() == "preprint":
        return True
    if r.doi and dd.is_preprint_doi(r.doi):
        return True
    if r.source == "arxiv" and not (r.doi and not dd.is_preprint_doi(r.doi)):
        return True
    return False


def _is_formal_vor_record(r: sm.Record) -> bool:
    if (r.version or "").lower() in ("published", "conference"):
        if r.doi and dd.is_preprint_doi(r.doi):
            return False
        return True
    return bool(r.doi and not dd.is_preprint_doi(r.doi))


def _load_subscription_and_snapshots(
    bundle: caseio.CaseBundle,
) -> Tuple[Dict[str, Any], List[sm.Record], List[sm.Record], Dict[str, Any]]:
    """Extract subscription config, previous_snapshot records, and current_snapshot records cleanly."""
    parsed_map: Dict[str, Any] = bundle.extra.get("parsed", {}) or {}
    sub_cfg: Dict[str, Any] = {}
    snap_meta: Dict[str, Any] = {
        "mode": "explicit_snapshot_pair",
        "subscription_file": None,
        "previous_snapshot_file": None,
        "current_snapshot_file": None,
        "previous_snapshot_id": None,
        "current_snapshot_id": None,
    }

    # 1. Subscription config from dedicated file or case.json
    for f in bundle.files:
        low = os.path.basename(f.rel).lower()
        if f.ext in (".json", ".jsonl") and any(k in low for k in ("subscription", "config", "schedule", "watermark")):
            obj = parsed_map.get(f.rel)
            if isinstance(obj, dict) and any(k in obj for k in ("subscription_id", "window_start", "window_end", "topic", "schedule")):
                sub_cfg.update(obj)
                snap_meta["subscription_file"] = f.rel
                break

    # Merge any window metadata discovered in case.json or snapshot headers
    if isinstance(bundle.case, dict):
        if isinstance(bundle.case.get("subscription"), dict):
            for k, v in bundle.case["subscription"].items():
                sub_cfg.setdefault(k, v)
        if isinstance(bundle.case.get("window"), dict):
            for k, v in bundle.case["window"].items():
                sub_cfg.setdefault(k, v)
        for k in ("subscription_id", "topic", "window_start", "window_end", "timezone", "previous_watermark", "schedule", "year_min"):
            if k in bundle.case and k not in sub_cfg:
                sub_cfg[k] = bundle.case[k]

    for k, v in (bundle.window_meta or {}).items():
        sub_cfg.setdefault(k, v)

    # 2. Previous and current snapshots from standalone files first
    prev_raw: List[Dict[str, Any]] = []
    curr_raw: List[Dict[str, Any]] = []

    def _extract_list_from_snap(obj: Any) -> List[Dict[str, Any]]:
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        if isinstance(obj, dict):
            for k in ("records", "items", "papers", "documents", "entries"):
                if isinstance(obj.get(k), list):
                    return [x for x in obj[k] if isinstance(x, dict)]
        return []

    for f in bundle.files:
        if f.ext not in (".json", ".jsonl"):
            continue
        low = os.path.basename(f.rel).lower()
        obj = parsed_map.get(f.rel)
        if not isinstance(obj, (dict, list)):
            continue
        if any(k in low for k in ("previous", "prev", "prior", "baseline", "before")) and "snapshot" in low:
            lst = _extract_list_from_snap(obj)
            if lst or isinstance(obj, dict):
                prev_raw = lst
                snap_meta["previous_snapshot_file"] = f.rel
                if isinstance(obj, dict):
                    snap_meta["previous_snapshot_id"] = obj.get("snapshot_id") or f.rel
                    if obj.get("captured_at") and not sub_cfg.get("previous_watermark"):
                        sub_cfg["previous_watermark"] = obj["captured_at"]
        elif any(k in low for k in ("current", "latest", "new", "after")) and "snapshot" in low:
            lst = _extract_list_from_snap(obj)
            if lst or isinstance(obj, dict):
                curr_raw = lst
                snap_meta["current_snapshot_file"] = f.rel
                if isinstance(obj, dict):
                    snap_meta["current_snapshot_id"] = obj.get("snapshot_id") or f.rel
                    for wk in ("window_start", "window_end", "timezone", "captured_at"):
                        if obj.get(wk) and not sub_cfg.get(wk):
                            sub_cfg[wk] = obj[wk]

    # 3. Embedded case.json previous_snapshot / current_snapshot fallback
    if not prev_raw and isinstance(bundle.case, dict) and bundle.case.get("previous_snapshot"):
        prev_raw = _extract_list_from_snap(bundle.case["previous_snapshot"])
        snap_meta["previous_snapshot_file"] = "case.json:previous_snapshot"
        snap_meta["previous_snapshot_id"] = "case.json:previous_snapshot"
    if not curr_raw and isinstance(bundle.case, dict) and bundle.case.get("current_snapshot"):
        curr_raw = _extract_list_from_snap(bundle.case["current_snapshot"])
        snap_meta["current_snapshot_file"] = "case.json:current_snapshot"
        snap_meta["current_snapshot_id"] = "case.json:current_snapshot"

    prev_records = [
        _dict_to_record(
            item,
            default_rid=f"PREV_{idx:03d}",
            source_file=snap_meta["previous_snapshot_file"] or "previous_snapshot",
            pointer=f"/records/{idx - 1}",
        )
        for idx, item in enumerate(prev_raw, 1)
    ]

    if snap_meta["current_snapshot_file"] is not None:
        curr_records = [
            _dict_to_record(
                item,
                default_rid=f"CURR_{idx:03d}",
                source_file=snap_meta["current_snapshot_file"],
                pointer=f"/records/{idx - 1}",
            )
            for idx, item in enumerate(curr_raw, 1)
        ]
    else:
        # Mode 3 fallback: no explicit current/previous snapshot files -> partition real multi-source API records by window_start watermark
        snap_meta["mode"] = "api_corpus_watermark_partition"
        snap_meta["current_snapshot_file"] = "api_corpus"
        snap_meta["current_snapshot_id"] = "api_corpus_window_slice"
        ws_str = str(sub_cfg.get("window_start") or sub_cfg.get("previous_watermark") or "").strip()
        if not ws_str:
            yrs = sorted({r.year for r in bundle.records if r.year and 1990 <= r.year <= 2035})
            cutoff_yr = yrs[-1] if yrs else 2025
            ws_str = f"{cutoff_yr}-01-01T00:00:00Z"
            sub_cfg["window_start"] = ws_str
            sub_cfg.setdefault("previous_watermark", ws_str)
            sub_cfg.setdefault("window_end", f"{cutoff_yr}-12-31T23:59:59Z")
        ws_cut = ws_str[:10]
        if not prev_records:
            snap_meta["previous_snapshot_file"] = "api_corpus_pre_watermark"
            snap_meta["previous_snapshot_id"] = f"pre_{ws_cut}"
            prev_split: List[sm.Record] = []
            curr_split: List[sm.Record] = []
            for r in bundle.records:
                d_eff = (getattr(r, "indexed_date", "") or r.date or (f"{r.year}-06-15" if r.year else ""))[:10]
                if d_eff and d_eff < ws_cut:
                    prev_split.append(r)
                else:
                    curr_split.append(r)
            prev_records = prev_split
            curr_records = curr_split
        else:
            curr_records = list(bundle.records)

    sub_cfg.setdefault("subscription_id", bundle.case_id or "sub-default")
    sub_cfg.setdefault("topic", bundle.question or "")
    sub_cfg.setdefault("timezone", "UTC")
    sub_cfg.setdefault("schedule", "unspecified")
    return sub_cfg, prev_records, curr_records, snap_meta


def _match_cluster_to_prior(
    cluster: dd.Cluster,
    prev_records: List[sm.Record],
) -> Tuple[Optional[sm.Record], str]:
    """Match a deduplicated current cluster against previous_snapshot records safely."""
    c_dois = set()
    c_pmids = set()
    c_arxivs = set()
    for m in cluster.members:
        c_dois |= _all_record_dois(m)
        if m.pmid:
            c_pmids.add(sm.norm_pmid(m.pmid))
        ab = sm.arxiv_base(m.arxiv_id)
        if ab:
            c_arxivs.add(ab)

    # Tier 1: Exact DOI / alt_dois bridge / PMID / arXiv base
    for p in prev_records:
        p_dois = _all_record_dois(p)
        shared_dois = c_dois & p_dois
        if shared_dois:
            if cluster.canonical.doi and p.doi and cluster.canonical.doi == p.doi:
                return p, "exact_doi"
            return p, "alt_doi_bridge"
        if p.pmid and sm.norm_pmid(p.pmid) in c_pmids:
            return p, "exact_pmid"
        p_ab = sm.arxiv_base(p.arxiv_id)
        if p_ab and p_ab in c_arxivs:
            return p, "exact_arxiv_base"

    # Tier 2+: search_core.dedup.same_work (enforces distinct-formal-DOI false-merge guard)
    for p in prev_records:
        same, tier, score = dd.same_work(cluster.canonical, p)
        if same:
            return p, f"{tier}({score:.2f})"
    return None, ""


def _make_csv_row(
    rec: sm.Record,
    category: str,
    alert_action: str,
    idem_key: str,
    alt_dois: List[str],
    sources: List[str],
    matched_prior_id: str,
    dedup_key: str,
    rationale: str,
) -> Dict[str, Any]:
    return {
        "record_id": rec.rid,
        "triage_category": category,
        "alert_action": alert_action,
        "idempotency_key": idem_key,
        "title": rec.title,
        "doi": rec.doi,
        "alt_dois": ";".join(alt_dois),
        "venue": rec.venue,
        "pub_date": rec.date,
        "indexed_date": rec.indexed_date,
        "source": ";".join(sources) if sources else rec.source,
        "version": rec.version,
        "matched_prior_id": matched_prior_id,
        "dedup_key": dedup_key,
        "rationale": rationale,
    }


def _make_idempotency_key(
    subscription_id: str,
    window_start_utc: str,
    window_end_utc: str,
    category: str,
    dedup_key: str,
) -> str:
    raw = f"{subscription_id}|{window_start_utc}|{window_end_utc}|{category}|{dedup_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_alert_ledger(state_file: Path) -> Dict[str, Any]:
    if state_file.is_file():
        try:
            obj = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                obj.setdefault("notified_keys", {})
                obj.setdefault("window_runs", [])
                return obj
        except Exception:
            pass
    return {"schema_version": "1.0", "notified_keys": {}, "window_runs": []}


def _audit_supplementary_api_corpus(
    bundle: caseio.CaseBundle,
    window_start_dt: Optional[dt.datetime],
    window_end_dt: Optional[dt.datetime],
    used_as_primary: bool,
) -> Dict[str, Any]:
    detected_issues: List[Dict[str, Any]] = []
    parsed_map = bundle.extra.get("parsed", {}) or {}
    for rel_path, obj in parsed_map.items():
        low_p = rel_path.lower()
        if isinstance(obj, dict) and isinstance(obj.get("meta"), dict) and isinstance(obj.get("results"), list):
            total_c = obj["meta"].get("count")
            returned_c = len(obj["results"])
            per_page = obj["meta"].get("per_page")
            page = obj["meta"].get("page", 1)
            if isinstance(total_c, int) and total_c > returned_c:
                detected_issues.append(
                    {
                        "source": "openalex" if "openalex" in low_p else Path(rel_path).stem,
                        "file": rel_path,
                        "status": "PAGINATION_TRUNCATED",
                        "error": f"Snapshot page={page} returned {returned_c}/{total_c} total works (per_page={per_page}); remaining pages unretrieved.",
                        "returned": returned_c,
                        "total_reported": total_c,
                    }
                )
        elif isinstance(obj, dict) and "hitCount" in obj and isinstance(obj.get("resultList"), dict):
            hc = obj.get("hitCount")
            ret = len((obj.get("resultList") or {}).get("result") or [])
            if isinstance(hc, int) and hc > ret:
                detected_issues.append(
                    {
                        "source": "europepmc",
                        "file": rel_path,
                        "status": "PAGINATION_TRUNCATED",
                        "error": f"EuropePMC snapshot returned {ret}/{hc} hits; remaining pages unretrieved.",
                        "returned": ret,
                        "total_reported": hc,
                    }
                )
    # Check arXiv XML truncation or out-of-window feed coverage
    for f in bundle.files:
        if f.ext == ".xml" and "arxiv" in f.rel.lower():
            try:
                raw_xml = Path(f.abs_path).read_text(encoding="utf-8", errors="replace")
                m_tot = re.search(r"totalResults[^>]*>(\d+)<", raw_xml)
                n_ent = len(re.findall(r"<entry\b", raw_xml))
                tot_n = int(m_tot.group(1)) if m_tot else n_ent
                if tot_n > n_ent:
                    detected_issues.append(
                        {
                            "source": "arxiv",
                            "file": f.rel,
                            "status": "PAGINATION_TRUNCATED",
                            "error": f"arXiv feed returned {n_ent}/{tot_n} entries; remaining pages unretrieved.",
                            "returned": n_ent,
                            "total_reported": tot_n,
                        }
                    )
            except Exception:
                pass

    if used_as_primary or not bundle.records:
        return {
            "present": bool(bundle.records),
            "used_as_primary_snapshot": used_as_primary,
            "raw_api_records": len(bundle.records),
            "unique_api_clusters": 0,
            "records_by_source": dict(Counter(r.source for r in bundle.records)),
            "in_window_dated_count": 0,
            "retracted_api_count": sum(1 for r in bundle.records if r.is_retracted),
            "detected_pagination_or_window_issues": detected_issues,
        }
    clusters, ambiguous, _ = dd.cluster_records_detailed(bundle.records)
    in_win = 0
    out_win_by_source: Counter = Counter()
    for c in clusters:
        d_str = c.canonical.date or (f"{c.canonical.year}-01-01" if c.canonical.year else "")
        if d_str and window_start_dt and window_end_dt:
            tmp_anom: List[Dict[str, Any]] = []
            dt_val = _parse_iso_utc(d_str[:10], "api_date", tmp_anom)
            if dt_val and window_start_dt <= dt_val <= window_end_dt:
                in_win += 1
            elif dt_val:
                out_win_by_source[c.canonical.source or "unknown"] += 1
    for src_name, cnt in sorted(out_win_by_source.items()):
        detected_issues.append(
            {
                "source": src_name,
                "file": f"api/{src_name}",
                "status": "OUT_OF_WINDOW_FEED",
                "error": f"{cnt} cluster(s) from {src_name} fall outside active UTC window; feed does not cover full tracking window.",
                "out_of_window_count": cnt,
            }
        )
    return {
        "present": True,
        "used_as_primary_snapshot": False,
        "raw_api_records": len(bundle.records),
        "unique_api_clusters": len(clusters),
        "ambiguous_api_pairs": len(ambiguous),
        "records_by_source": dict(Counter(r.source for r in bundle.records)),
        "in_window_dated_count": in_win,
        "out_of_window_by_source": dict(out_win_by_source),
        "retracted_api_count": sum(1 for c in clusters if any(m.is_retracted for m in c.members)),
        "detected_pagination_or_window_issues": detected_issues,
        "note": "Supplementary multi-database API pagination/window defects are promoted into main gaps and watermark freeze decisions.",
    }


def _render_report_md(
    case_id: str,
    sub_cfg: Dict[str, Any],
    utc_window: Dict[str, Any],
    triage: Dict[str, List[Dict[str, Any]]],
    idempotency: Dict[str, Any],
    emitted_alerts: List[Dict[str, Any]],
    suppressed_rerun_items: List[Dict[str, Any]],
    date_anomalies: List[Dict[str, Any]],
    pagination_issues: List[Dict[str, Any]],
    conservation: Dict[str, Any],
    supp_api: Dict[str, Any],
) -> str:
    lines: List[str] = [
        f"# Incremental Literature Tracking & Alert Digest (`{case_id}`)",
        "",
        "## 1. Subscription Configuration & Fixed UTC Window",
        f"- **Subscription ID**: `{sub_cfg.get('subscription_id')}` | **Schedule**: `{sub_cfg.get('schedule')}`",
        f"- **Topic**: {sub_cfg.get('topic')}",
        f"- **UTC Window**: `{utc_window['window_start_utc'] or 'unspecified'}` to `{utc_window['window_end_utc'] or 'unspecified'}` (Previous Watermark: `{utc_window['previous_watermark_utc'] or 'none'}`)",
        "- **Coverage Boundary**: Strictly computed over finite local snapshot files; no live remote crawling or global latest-publication completeness is claimed.",
        "",
        "## 2. Five-Category Incremental Triage Summary (100% Conservation)",
        f"- **Previous Snapshot Records**: {conservation['prior_snapshot_count']}",
        f"- **Current Snapshot Raw Records**: {conservation['current_raw_count']} (Conserved: `{conservation['triage_count_conserved']}`)",
        f"- **1. Genuinely New (`genuinely_new`)**: {len(triage['genuinely_new'])}",
        f"- **2. Version Upgrades (`version_upgrade`)**: {len(triage['version_upgrade'])}",
        f"- **3. Retractions / Corrections (`retraction_or_correction`)**: {len(triage['retraction_or_correction'])}",
        f"- **4. Delayed-Indexed Archival (`delayed_indexed_archival`)**: {len(triage['delayed_indexed_archival'])}",
        f"- **5. Duplicate Suppressed (`duplicate_suppressed`)**: {len(triage['duplicate_suppressed'])}",
        "",
        "## 3. Idempotent Alert Dispatch & Noise Suppression",
        f"- **Run Sequence for Window**: `{idempotency['run_sequence_for_window']}`",
        f"- **Idempotent Rerun Detected**: `{idempotency['idempotent_rerun']}`",
        f"- **New Alerts Emitted This Run**: `{idempotency['new_alerts_emitted']}`",
        f"- **Suppressed (Already Notified in Ledger)**: `{idempotency['suppressed_already_notified']}`",
        f"- **Noise Suppressed (Duplicates + Delayed Archival)**: `{len(triage['duplicate_suppressed']) + len(triage['delayed_indexed_archival'])}`",
        "",
    ]

    if emitted_alerts:
        lines.append("### Emitted Push Alerts (Priority Ordered)")
        lines.append(
            ct.md_table(
                emitted_alerts,
                ["priority", "category", "record_id", "doi", "venue", "title", "idempotency_key"],
                widths={"idempotency_key": 16, "title": 65},
            )
        )
    elif suppressed_rerun_items:
        lines.append("### Idempotent Rerun: Zero New Push Notifications Emitted")
        lines.append(
            ct.md_table(
                suppressed_rerun_items,
                ["priority", "category", "record_id", "doi", "title", "idempotency_key"],
                widths={"idempotency_key": 16, "title": 65},
            )
        )
    else:
        lines.append("- Zero push alerts emitted for this window.")

    lines.append("## 4. Detailed Triage Breakdown")
    lines.append("### 4.1 Retraction / Correction Alerts (Critical)")
    lines.append(ct.md_table(triage["retraction_or_correction"], ["record_id", "event_type", "state_transition", "doi", "matched_prior_id", "title"]))

    lines.append("### 4.2 Preprint-to-VoR Version Upgrades (High)")
    lines.append(ct.md_table(triage["version_upgrade"], ["record_id", "prev_record_id", "prev_doi", "new_doi", "new_venue", "match_basis", "title"]))

    lines.append("### 4.3 Genuinely New Publications (Normal)")
    lines.append(ct.md_table(triage["genuinely_new"], ["record_id", "doi", "venue", "pub_date", "indexed_date", "merged_sources", "title"]))

    lines.append("### 4.4 Delayed-Indexed Archival Entries (Muted from Push)")
    lines.append(ct.md_table(triage["delayed_indexed_archival"], ["record_id", "year", "pub_date", "indexed_date", "indexing_lag_days", "doi", "title"]))

    lines.append("### 4.5 Duplicate Suppressed Entries (With Dedup Key & Replay Trace)")
    lines.append(ct.md_table(triage["duplicate_suppressed"], ["record_id", "suppression_reason", "matched_target_id", "source", "dedup_key", "title"]))

    lines.extend(["", "## 5. Date, Pagination & Supplementary Corpus Diagnostics"])
    if date_anomalies:
        lines.append("### Date / Timezone Anomalies")
        lines.append(ct.md_table(date_anomalies, ["code", "field", "raw", "detail"]))
    else:
        lines.append("- Zero date or timezone anomalies detected across window boundaries and snapshot records.")

    if pagination_issues:
        lines.append("### Source Pagination / Access Issues (PyAlex #103 Guard)")
        lines.append(ct.md_table(pagination_issues, ["file", "source", "status", "error"]))
    else:
        lines.append("- Zero API pagination or rate-limit failure markers detected in supplied files.")

    if supp_api.get("present") and not supp_api.get("used_as_primary_snapshot"):
        lines.append(
            f"- **Supplementary API Corpus Audited**: {supp_api['raw_api_records']} raw records -> "
            f"{supp_api['unique_api_clusters']} unique clusters across {json.dumps(supp_api['records_by_source'], ensure_ascii=False)} "
            f"({supp_api['in_window_dated_count']} dated inside window, {supp_api['retracted_api_count']} retracted)."
        )

    return "\n".join(lines).strip() + "\n"


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    *,
    state_file: Optional[Path] = None,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)

    bundle = caseio.load_case(str(input_dir))
    case_id = bundle.case_id or "challenge-1e85736f"
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-1e85736f")

    ledger_path = state_file.resolve() if state_file else (work_dir / "alert_ledger.json").resolve()
    builder = ct.ResultBuilder(
        bundle,
        str(work_dir),
        str(output_dir),
        "incremental-literature-tracker",
        {"state_file": ledger_path.name},
    )

    budget_guard.tick_phase(budget_file, "parse_window_and_snapshots")
    sub_cfg, prev_records, curr_records, snap_meta = _load_subscription_and_snapshots(bundle)

    date_anomalies: List[Dict[str, Any]] = []
    win_start_dt = _parse_iso_utc(sub_cfg.get("window_start"), "window_start", date_anomalies)
    win_end_dt = _parse_iso_utc(sub_cfg.get("window_end"), "window_end", date_anomalies)
    prev_wm_dt = _parse_iso_utc(sub_cfg.get("previous_watermark"), "previous_watermark", date_anomalies)

    if win_start_dt and win_end_dt and win_start_dt > win_end_dt:
        date_anomalies.append(
            {
                "code": "inverted_window_bounds",
                "field": "window_start/window_end",
                "raw": f"{sub_cfg.get('window_start')} > {sub_cfg.get('window_end')}",
                "detail": "window_start occurs after window_end.",
            }
        )

    window_start_utc = _fmt_utc(win_start_dt)
    window_end_utc = _fmt_utc(win_end_dt)
    prev_wm_utc = _fmt_utc(prev_wm_dt)
    window_start_year = win_start_dt.year if win_start_dt else (int(sub_cfg["year_min"]) if sub_cfg.get("year_min") else 0)

    utc_window = {
        "window_start_raw": sub_cfg.get("window_start"),
        "window_end_raw": sub_cfg.get("window_end"),
        "window_start_utc": window_start_utc,
        "window_end_utc": window_end_utc,
        "previous_watermark_utc": prev_wm_utc,
        "timezone_declared": sub_cfg.get("timezone", "UTC"),
        "window_start_year": window_start_year,
    }
    builder.log(
        "window",
        "normalized_utc",
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        prior_count=len(prev_records),
        current_count=len(curr_records),
        mode=snap_meta["mode"],
    )

    # Audit per-record date anomalies
    for rec in curr_records:
        p_dt = _parse_iso_utc(rec.date, f"{rec.rid}.date", date_anomalies) if rec.date else None
        i_dt = _parse_iso_utc(rec.indexed_date, f"{rec.rid}.indexed_date", date_anomalies) if rec.indexed_date else None
        if not rec.date and not rec.year:
            date_anomalies.append(
                {"code": "record_missing_pub_date", "field": f"{rec.rid}.date", "raw": "", "detail": "Missing both date and publication year."}
            )
        if p_dt and i_dt and i_dt < p_dt:
            date_anomalies.append(
                {
                    "code": "indexed_before_published",
                    "field": f"{rec.rid}.indexed_date",
                    "raw": f"indexed={rec.indexed_date} < pub={rec.date}",
                    "detail": "Indexed date precedes publication date (possible early-access or metadata clock skew).",
                }
            )

    budget_guard.tick_phase(budget_file, "dedup_and_5way_triage")
    if curr_records:
        curr_clusters, ambiguous_pairs, intra_rollback = dd.cluster_records_detailed(curr_records)
    else:
        curr_clusters, ambiguous_pairs, intra_rollback = [], [], []
    dedup_stats = dd.dedup_report(curr_clusters, curr_records, ambiguous=ambiguous_pairs, rollback_log=intra_rollback)

    triage: Dict[str, List[Dict[str, Any]]] = {
        "genuinely_new": [],
        "version_upgrade": [],
        "retraction_or_correction": [],
        "delayed_indexed_archival": [],
        "duplicate_suppressed": [],
    }
    csv_rows: List[Dict[str, Any]] = []
    alert_candidates: List[Dict[str, Any]] = []

    sub_id = str(sub_cfg.get("subscription_id") or case_id)

    def _base_meta(rec: sm.Record, alt_dois: List[str], m_sources: List[str], m_rids: List[str], dk: str) -> Dict[str, Any]:
        return {
            "record_id": rec.rid, "title": rec.title, "authors": rec.authors, "year": rec.year,
            "pub_date": rec.date, "indexed_date": rec.indexed_date, "doi": rec.doi, "alt_dois": alt_dois,
            "venue": rec.venue, "merged_sources": m_sources, "merged_record_ids": m_rids,
            "version": rec.version, "dedup_key": dk,
        }

    def _alert_cand(priority: str, rank: int, cat: str, rec: sm.Record, dk: str, ik: str, summary: str) -> Dict[str, Any]:
        return {
            "priority": priority, "priority_rank": rank, "category": cat, "record_id": rec.rid,
            "title": rec.title, "doi": rec.doi, "venue": rec.venue, "dedup_key": dk,
            "idempotency_key": ik, "summary": summary,
        }

    for cluster in curr_clusters:
        canon = cluster.canonical
        dkey = _canonical_dedup_key(canon)
        merged_sources = sorted({m.source for m in cluster.members if m.source})
        merged_rids = [m.rid for m in cluster.members]
        all_alt_dois = sorted((_all_record_dois(canon) | {d for m in cluster.members for d in _all_record_dois(m)}) - ({canon.doi} if canon.doi else set()))

        # Record intra-window cross-source duplicates first (conserving raw count)
        for m in cluster.members:
            if m is canon:
                continue
            _, tier, score = dd.same_work(canon, m)
            dup_entry = {
                "record_id": m.rid,
                "triage_category": "duplicate_suppressed",
                "suppression_reason": "intra_window_cross_source_duplicate",
                "matched_target_id": canon.rid,
                "match_tier": tier or "transitive_cluster",
                "match_score": round(score, 3),
                "dedup_key": dkey,
                "title": m.title,
                "doi": m.doi,
                "source": m.source,
                "venue": m.venue,
                "pub_date": m.date,
                "indexed_date": m.indexed_date,
                "version": m.version,
            }
            triage["duplicate_suppressed"].append(dup_entry)
            csv_rows.append(
                _make_csv_row(
                    m,
                    "duplicate_suppressed",
                    "silent_suppress",
                    "",
                    m.alt_dois,
                    [m.source],
                    canon.rid,
                    dkey,
                    f"Intra-window cross-source duplicate of {canon.rid} via {tier or 'transitive_cluster'}",
                )
            )

        # Match canonical cluster against previous_snapshot
        prior_match, match_basis = _match_cluster_to_prior(cluster, prev_records)
        is_retracted = any(m.is_retracted for m in cluster.members)
        is_correction = any(m.is_correction for m in cluster.members)

        # Category 3 (highest priority): Retraction or Correction
        if is_retracted or is_correction:
            ev_type = "retraction" if is_retracted else "correction"
            prev_state = (prior_match.version or "published") if prior_match else "unseen_in_prior"
            idem_key = _make_idempotency_key(sub_id, window_start_utc, window_end_utc, "retraction_or_correction", dkey)
            entry = {
                **_base_meta(canon, all_alt_dois, merged_sources, merged_rids, dkey),
                "triage_category": "retraction_or_correction",
                "event_type": ev_type,
                "state_transition": f"{prev_state} -> {ev_type}",
                "matched_prior_id": prior_match.rid if prior_match else None,
                "match_basis": match_basis or None,
                "idempotency_key": idem_key,
                "priority": "critical",
            }
            triage["retraction_or_correction"].append(entry)
            alert_candidates.append(
                _alert_cand(
                    "critical", 0, "retraction_or_correction", canon, dkey, idem_key,
                    f"{ev_type.upper()} detected ({entry['state_transition']}) for {canon.doi or canon.rid}.",
                )
            )
            csv_rows.append(
                _make_csv_row(
                    canon,
                    "retraction_or_correction",
                    "pending_idempotency_check",
                    idem_key,
                    all_alt_dois,
                    merged_sources,
                    prior_match.rid if prior_match else "",
                    dkey,
                    f"Safety override: {ev_type} ({entry['state_transition']})",
                )
            )
            continue

        # Matched in prior snapshot -> Version Upgrade vs Duplicate Suppressed
        if prior_match is not None:
            if _is_preprint_record(prior_match) and _is_formal_vor_record(canon):
                idem_key = _make_idempotency_key(sub_id, window_start_utc, window_end_utc, "version_upgrade", dkey)
                combined_alt = sorted((set(all_alt_dois) | _all_record_dois(prior_match)) - ({canon.doi} if canon.doi else set()))
                entry = {
                    **_base_meta(canon, combined_alt, merged_sources, merged_rids, dkey),
                    "triage_category": "version_upgrade",
                    "prev_record_id": prior_match.rid,
                    "prev_doi": prior_match.doi,
                    "prev_arxiv": prior_match.arxiv_id,
                    "prev_source": prior_match.source,
                    "prev_version": prior_match.version,
                    "new_doi": canon.doi,
                    "new_venue": canon.venue,
                    "new_source": canon.source,
                    "new_version": canon.version,
                    "match_basis": match_basis,
                    "version_chain": (
                        f"{prior_match.doi or prior_match.arxiv_id or prior_match.rid} ({prior_match.version}/{prior_match.source}) "
                        f"-> {canon.doi or canon.rid} ({canon.version}/{canon.venue or canon.source})"
                    ),
                    "idempotency_key": idem_key,
                    "priority": "high",
                }
                triage["version_upgrade"].append(entry)
                alert_candidates.append(
                    _alert_cand("high", 1, "version_upgrade", canon, dkey, idem_key, f"Preprint upgraded to VoR: {entry['version_chain']}.")
                )
                csv_rows.append(
                    _make_csv_row(
                        canon,
                        "version_upgrade",
                        "pending_idempotency_check",
                        idem_key,
                        combined_alt,
                        merged_sources,
                        prior_match.rid,
                        dkey,
                        f"Preprint-to-VoR upgrade linked via {match_basis}: {entry['version_chain']}",
                    )
                )
            else:
                dup_entry = {
                    "record_id": canon.rid,
                    "triage_category": "duplicate_suppressed",
                    "suppression_reason": "already_seen_in_previous_snapshot",
                    "matched_target_id": prior_match.rid,
                    "match_basis": match_basis,
                    "dedup_key": dkey,
                    "title": canon.title,
                    "doi": canon.doi,
                    "source": canon.source,
                    "merged_sources": merged_sources,
                    "venue": canon.venue,
                    "pub_date": canon.date,
                    "indexed_date": canon.indexed_date,
                    "version": canon.version,
                }
                triage["duplicate_suppressed"].append(dup_entry)
                csv_rows.append(
                    _make_csv_row(
                        canon,
                        "duplicate_suppressed",
                        "silent_suppress",
                        "",
                        all_alt_dois,
                        merged_sources,
                        prior_match.rid,
                        dkey,
                        f"Already seen in previous_snapshot ({prior_match.rid}) via {match_basis}",
                    )
                )
            continue

        # Unseen in prior snapshot -> Delayed-Indexed Archival vs Genuinely New
        tmp_anom: List[Dict[str, Any]] = []
        pub_dt = _parse_iso_utc(canon.date, "pub_date", tmp_anom) if canon.date else None
        idx_dt = _parse_iso_utc(canon.indexed_date, "indexed_date", tmp_anom) if canon.indexed_date else None
        lag_days = (idx_dt - pub_dt).days if (pub_dt and idx_dt) else None

        is_delayed_archival = False
        if window_start_year and canon.year and canon.year < window_start_year:
            is_delayed_archival = True
        elif win_start_dt and pub_dt and (win_start_dt - pub_dt).days > 365:
            is_delayed_archival = True

        if is_delayed_archival:
            arch_entry = {
                **_base_meta(canon, all_alt_dois, merged_sources, merged_rids, dkey),
                "triage_category": "delayed_indexed_archival",
                "indexing_lag_days": lag_days,
                "alert_action": "archival_log_only_not_pushed",
                "rationale": f"Published in {canon.year or canon.date} (before window year {window_start_year}); newly indexed in window.",
            }
            triage["delayed_indexed_archival"].append(arch_entry)
            csv_rows.append(
                _make_csv_row(
                    canon,
                    "delayed_indexed_archival",
                    "archival_log_only",
                    "",
                    all_alt_dois,
                    merged_sources,
                    "",
                    dkey,
                    arch_entry["rationale"],
                )
            )
        else:
            idem_key = _make_idempotency_key(sub_id, window_start_utc, window_end_utc, "genuinely_new", dkey)
            new_entry = {
                **_base_meta(canon, all_alt_dois, merged_sources, merged_rids, dkey),
                "triage_category": "genuinely_new",
                "n_merged_sources": len(merged_sources),
                "idempotency_key": idem_key,
                "priority": "normal",
            }
            triage["genuinely_new"].append(new_entry)
            alert_candidates.append(
                _alert_cand(
                    "normal", 2, "genuinely_new", canon, dkey, idem_key,
                    f"New publication in window ({canon.venue or canon.source}, {canon.date or canon.year}); consolidated from {len(merged_rids)} source record(s).",
                )
            )
            csv_rows.append(
                _make_csv_row(
                    canon,
                    "genuinely_new",
                    "pending_idempotency_check",
                    idem_key,
                    all_alt_dois,
                    merged_sources,
                    "",
                    dkey,
                    f"Genuinely new in window; deduplicated across {merged_sources}",
                )
            )

    # Conservation check
    total_triaged = sum(len(v) for v in triage.values())
    conservation = {
        "prior_snapshot_count": len(prev_records),
        "current_raw_count": len(curr_records),
        "current_unique_clusters": len(curr_clusters),
        "total_triaged_records": total_triaged,
        "triage_count_conserved": total_triaged == len(curr_records),
    }

    # Idempotent alert ledger reconciliation
    budget_guard.tick_phase(budget_file, "idempotent_alert_dispatch")
    alert_candidates.sort(key=lambda a: (a["priority_rank"], a["record_id"]))
    ledger = _load_alert_ledger(ledger_path)
    window_key = f"{sub_id}|{window_start_utc}|{window_end_utc}"
    prior_runs_for_window = sum(1 for r in ledger["window_runs"] if r.get("window_key") == window_key)

    emitted_alerts: List[Dict[str, Any]] = []
    suppressed_rerun_items: List[Dict[str, Any]] = []
    now_iso = _utc_now_iso()
    action_by_key: Dict[str, str] = {}

    for cand in alert_candidates:
        ikey = cand["idempotency_key"]
        if ikey in ledger["notified_keys"]:
            suppressed_rerun_items.append(
                {
                    **{k: v for k, v in cand.items() if k != "priority_rank"},
                    "alert_action": "suppressed_already_notified",
                    "first_emitted_utc": ledger["notified_keys"][ikey].get("first_emitted_utc"),
                }
            )
            action_by_key[ikey] = "suppressed_already_notified"
        else:
            emitted_entry = {
                **{k: v for k, v in cand.items() if k != "priority_rank"},
                "alert_action": "emitted",
                "emitted_utc": now_iso,
            }
            emitted_alerts.append(emitted_entry)
            ledger["notified_keys"][ikey] = {
                "first_emitted_utc": now_iso,
                "window_key": window_key,
                "category": cand["category"],
                "record_id": cand["record_id"],
                "dedup_key": cand["dedup_key"],
                "doi": cand["doi"],
                "title": cand["title"],
            }
            action_by_key[ikey] = "emitted"

    # Update CSV rows with final alert action
    for row in csv_rows:
        ik = row.get("idempotency_key")
        if ik and ik in action_by_key:
            row["alert_action"] = action_by_key[ik]

    run_seq = prior_runs_for_window + 1
    idempotent_rerun = prior_runs_for_window > 0 and len(emitted_alerts) == 0
    ledger["window_runs"].append(
        {
            "run_utc": now_iso,
            "window_key": window_key,
            "run_sequence_for_window": run_seq,
            "new_alerts_emitted": len(emitted_alerts),
            "suppressed_already_notified": len(suppressed_rerun_items),
            "idempotent_rerun": idempotent_rerun,
        }
    )
    ledger["last_updated_utc"] = now_iso
    result_guard.atomic_write_json(ledger_path, ledger)

    idempotency_summary = {
        "window_key": window_key,
        "run_sequence_for_window": run_seq,
        "idempotent_rerun": idempotent_rerun,
        "alert_candidates_total": len(alert_candidates),
        "new_alerts_emitted": len(emitted_alerts),
        "suppressed_already_notified": len(suppressed_rerun_items),
        "ledger_total_keys": len(ledger["notified_keys"]),
        "state_file_name": ledger_path.name,
    }

    supp_api = _audit_supplementary_api_corpus(
        bundle,
        win_start_dt,
        win_end_dt,
        used_as_primary=(snap_meta["mode"] == "api_corpus_fallback"),
    )

    builder.log(
        "triage",
        "completed",
        genuinely_new=len(triage["genuinely_new"]),
        version_upgrade=len(triage["version_upgrade"]),
        retraction_or_correction=len(triage["retraction_or_correction"]),
        delayed_indexed_archival=len(triage["delayed_indexed_archival"]),
        duplicate_suppressed=len(triage["duplicate_suppressed"]),
        idempotent_rerun=idempotent_rerun,
        new_alerts_emitted=len(emitted_alerts),
    )

    # Write domain artifacts
    csv_fields = [
        "record_id", "triage_category", "alert_action", "idempotency_key", "title", "doi",
        "alt_dois", "venue", "pub_date", "indexed_date", "source", "version",
        "matched_prior_id", "dedup_key", "rationale",
    ]
    builder.write_csv("incremental_delta.csv", csv_rows, fields=csv_fields)

    all_pagination_issues = list(bundle.pagination_issues) + list(supp_api.get("detected_pagination_or_window_issues") or [])
    frozen_sources = sorted({str(pi.get("source") or pi.get("file") or "unknown") for pi in all_pagination_issues})
    has_coverage_block = bool(all_pagination_issues or bundle.source_load_errors)
    watermark_committed = not has_coverage_block
    effective_new_watermark = (prev_wm_utc or window_start_utc or now_iso) if has_coverage_block else (window_end_utc or prev_wm_utc or now_iso)

    alert_digest = {
        "case_id": case_id,
        "scope_note": "Organizer-local incremental snapshot only; no live remote polling or global latest-publication claim.",
        "subscription_config": sub_cfg,
        "snapshot_metadata": snap_meta,
        "utc_window": utc_window,
        "conservation": conservation,
        "triage": triage,
        "emitted_alerts": emitted_alerts,
        "suppressed_already_notified_items": suppressed_rerun_items,
        "idempotency": idempotency_summary,
        "noise_suppression_summary": {
            "duplicate_suppressed_count": len(triage["duplicate_suppressed"]),
            "delayed_indexed_archival_muted_count": len(triage["delayed_indexed_archival"]),
            "already_notified_rerun_suppressed_count": len(suppressed_rerun_items),
            "total_noise_suppressed": len(triage["duplicate_suppressed"]) + len(triage["delayed_indexed_archival"]) + len(suppressed_rerun_items),
        },
        "ambiguous_candidates": ambiguous_pairs,
        "intra_window_rollback_log": intra_rollback,
        "date_anomalies": date_anomalies,
        "pagination_issues": all_pagination_issues,
        "watermark_committed": watermark_committed,
        "frozen_sources": frozen_sources,
        "supplementary_api_audit": supp_api,
    }
    builder.write_json("alert_digest.json", alert_digest)

    per_source_wm = {}
    for src_k in sorted(set(list((supp_api.get("records_by_source") or {}).keys()) + frozen_sources)):
        if src_k in frozen_sources:
            per_source_wm[src_k] = {
                "watermark_committed": False,
                "status": "FROZEN_INCOMPLETE_COVERAGE",
                "committed_watermark_utc": prev_wm_utc,
                "retry_window": {"start_utc": window_start_utc, "end_utc": window_end_utc},
            }
        else:
            per_source_wm[src_k] = {
                "watermark_committed": True,
                "status": "ADVANCED",
                "committed_watermark_utc": window_end_utc or prev_wm_utc or now_iso,
            }

    watermark_state = {
        "case_id": case_id,
        "subscription_id": sub_id,
        "topic": sub_cfg.get("topic", ""),
        "schedule": sub_cfg.get("schedule", "unspecified"),
        "previous_watermark_utc": prev_wm_utc,
        "window_start_utc": window_start_utc,
        "window_end_utc": window_end_utc,
        "watermark_committed": watermark_committed,
        "watermark_freeze_reason": (
            f"Frozen due to {len(all_pagination_issues)} pagination/out-of-window issue(s) across {frozen_sources}; retry window required before advancing."
            if has_coverage_block
            else "All snapshot sources complete; safe to advance watermark."
        ),
        "frozen_sources": frozen_sources,
        "retry_window": {"start_utc": window_start_utc, "end_utc": window_end_utc} if has_coverage_block else None,
        "per_source_watermark_status": per_source_wm,
        "new_watermark_utc": effective_new_watermark,
        "previous_snapshot_id": snap_meta["previous_snapshot_id"],
        "current_snapshot_id": snap_meta["current_snapshot_id"],
        "run_sequence_for_window": run_seq,
        "idempotent_rerun": idempotent_rerun,
        "new_alerts_emitted": len(emitted_alerts),
        "suppressed_already_notified": len(suppressed_rerun_items),
        "alert_ledger_keys": sorted(ledger["notified_keys"].keys()),
        "window_run_history": ledger["window_runs"][-20:],
        "updated_at_utc": now_iso,
    }
    builder.write_json("watermark_state.json", watermark_state)

    report_md = _render_report_md(
        case_id=case_id,
        sub_cfg=sub_cfg,
        utc_window=utc_window,
        triage=triage,
        idempotency=idempotency_summary,
        emitted_alerts=emitted_alerts,
        suppressed_rerun_items=suppressed_rerun_items,
        date_anomalies=date_anomalies,
        pagination_issues=all_pagination_issues,
        conservation=conservation,
        supp_api=supp_api,
    )
    builder.write_markdown("report.md", report_md)

    # Audit log & run manifest
    builder.log("finalize", "write_audit_and_manifest")
    audit_obj = {"entries": builder.audit, "chain_head": builder.prev_hash, "verified": ct._verify_chain(builder.audit)}
    builder.write_json("audit_log.json", audit_obj)
    manifest_obj = {
        "case_id": case_id,
        "files": bundle.inventory(),
        "records_by_file": bundle.record_files,
        "source_load_errors": bundle.source_load_errors,
        "pagination_issues": all_pagination_issues,
        "notes": bundle.notes,
    }
    builder.write_json("run_manifest.json", manifest_obj)

    gaps: List[Dict[str, Any]] = []
    if not curr_records:
        gaps.append(
            {
                "code": "EMPTY_CURRENT_SNAPSHOT",
                "severity": "info",
                "source": "incremental_tracker",
                "message": "Current tracking window contains 0 records; emitted 0 alerts honestly without backfill.",
            }
        )
    for pi in all_pagination_issues:
        gaps.append(
            {
                "code": "SOURCE_PAGINATION_OR_ACCESS_ERROR",
                "severity": "warning",
                "source": str(pi.get("file", "api")),
                "message": f"{pi.get('status')}: {pi.get('error')}",
            }
        )
    if has_coverage_block:
        gaps.append(
            {
                "code": "WATERMARK_FROZEN_INCOMPLETE_COVERAGE",
                "severity": "warning",
                "source": "watermark_state",
                "message": f"Watermark NOT advanced (watermark_committed=False) for sources {frozen_sources}; retry_window={window_start_utc}..{window_end_utc}.",
            }
        )
    for err in bundle.source_load_errors:
        gaps.append(
            {
                "code": "SOURCE_LOAD_ERROR",
                "severity": "warning",
                "source": "caseio",
                "message": f"{err['file']}: {err['error_type']} - {err['detail']}",
            }
        )

    warnings_list: List[str] = list(bundle.notes)
    if triage["retraction_or_correction"]:
        warnings_list.append(f"High-priority retraction/correction alerts detected: {len(triage['retraction_or_correction'])}.")
    if date_anomalies:
        warnings_list.append(f"Detected {len(date_anomalies)} date/timezone anomaly(ies); see alert_digest.json:date_anomalies.")
    if idempotent_rerun:
        warnings_list.append(
            f"Idempotent rerun #{run_seq} over window {window_key}: 0 duplicate push notifications emitted ({len(suppressed_rerun_items)} suppressed)."
        )

    metrics = {
        "prior_snapshot_count": len(prev_records),
        "current_raw_count": len(curr_records),
        "current_unique_clusters": len(curr_clusters),
        "genuinely_new_count": len(triage["genuinely_new"]),
        "version_upgrade_count": len(triage["version_upgrade"]),
        "retraction_or_correction_count": len(triage["retraction_or_correction"]),
        "delayed_indexed_archival_count": len(triage["delayed_indexed_archival"]),
        "duplicate_suppressed_count": len(triage["duplicate_suppressed"]),
        "new_alerts_emitted": len(emitted_alerts),
        "suppressed_already_notified": len(suppressed_rerun_items),
        "run_sequence_for_window": run_seq,
        "date_anomaly_count": len(date_anomalies),
        "pagination_issue_count": len(all_pagination_issues),
        "watermark_committed": watermark_committed,
        "ambiguous_identity_count": len(ambiguous_pairs),
        "supplementary_api_records": supp_api.get("raw_api_records", 0),
    }
    metric_provenance = {
        "prior_snapshot_count": "alert_digest.json:conservation.prior_snapshot_count",
        "current_raw_count": "alert_digest.json:conservation.current_raw_count",
        "current_unique_clusters": "alert_digest.json:conservation.current_unique_clusters",
        "genuinely_new_count": "alert_digest.json:triage.genuinely_new",
        "version_upgrade_count": "alert_digest.json:triage.version_upgrade",
        "retraction_or_correction_count": "alert_digest.json:triage.retraction_or_correction",
        "delayed_indexed_archival_count": "alert_digest.json:triage.delayed_indexed_archival",
        "duplicate_suppressed_count": "alert_digest.json:triage.duplicate_suppressed",
        "new_alerts_emitted": "alert_digest.json:emitted_alerts",
        "suppressed_already_notified": "alert_digest.json:suppressed_already_notified_items",
        "run_sequence_for_window": "watermark_state.json:run_sequence_for_window",
        "date_anomaly_count": "alert_digest.json:date_anomalies",
        "pagination_issue_count": "alert_digest.json:pagination_issues",
        "ambiguous_identity_count": "alert_digest.json:ambiguous_candidates",
        "supplementary_api_records": "alert_digest.json:supplementary_api_audit.raw_api_records",
    }

    artifacts_list = ["result.json"] + [a["path"] for a in builder.artifacts]
    findings = [
        (
            f"Partitioned {len(curr_records)} current record(s) against {len(prev_records)} prior record(s) "
            f"(conserved={conservation['triage_count_conserved']}): {len(triage['genuinely_new'])} genuinely_new, "
            f"{len(triage['version_upgrade'])} version_upgrade, {len(triage['retraction_or_correction'])} retraction_or_correction, "
            f"{len(triage['delayed_indexed_archival'])} delayed_indexed_archival, {len(triage['duplicate_suppressed'])} duplicate_suppressed."
        ),
        (
            f"Idempotency run #{run_seq} (idempotent_rerun={idempotent_rerun}): emitted {len(emitted_alerts)} alert(s), "
            f"suppressed {len(suppressed_rerun_items)} already-notified alert(s), muted "
            f"{len(triage['duplicate_suppressed']) + len(triage['delayed_indexed_archival'])} duplicate/archival noise record(s)."
        ),
    ]

    patch: Dict[str, Any] = {
        "skill": "incremental-literature-tracker",
        "results": {
            "genuinely_new": triage["genuinely_new"],
            "version_upgrade": triage["version_upgrade"],
            "retraction_or_correction": triage["retraction_or_correction"],
            "delayed_indexed_archival": triage["delayed_indexed_archival"],
            "duplicate_suppressed": triage["duplicate_suppressed"],
            "emitted_alerts": emitted_alerts,
            "triage_count_conserved": conservation["triage_count_conserved"],
        },
        "evidence": {
            "subscription_config": sub_cfg,
            "utc_window": utc_window,
            "snapshot_metadata": snap_meta,
            "incremental_delta_csv": "incremental_delta.csv",
            "alert_digest_json": "alert_digest.json",
            "watermark_state_json": "watermark_state.json",
            "dedup_summary": dedup_stats,
            "supplementary_api_audit": supp_api,
            "audit_chain": {"log": "audit_log.json", "entries": len(builder.audit), "verified": audit_obj["verified"]},
        },
        "idempotency": idempotency_summary,
        "status_details": {
            "finite_snapshot_only": True,
            "live_remote_coverage_claimed": False,
            "completed": [
                "utc_window_normalization",
                "intra_window_cross_source_deduplication",
                "watermark_5way_triage_with_conservation",
                "preprint_to_vor_version_chain_linking",
                "sha256_idempotent_alert_dispatch",
            ],
            "not_completed": [],
            "date_anomalies": date_anomalies,
            "pagination_issues": list(bundle.pagination_issues),
            "source_load_errors": list(bundle.source_load_errors),
            "ambiguous_identity_candidates": ambiguous_pairs,
            "idempotent_rerun": idempotent_rerun,
            "new_alerts_emitted": len(emitted_alerts),
            "suppressed_already_notified": len(suppressed_rerun_items),
        },
        "artifacts": artifacts_list,
        "metrics": metrics,
        "metric_provenance": metric_provenance,
        "findings": findings,
        "warnings": warnings_list,
        "gaps": gaps,
    }

    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"] or grounding["ungrounded_metrics"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit_res = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=[
            "incremental_delta.csv",
            "alert_digest.json",
            "watermark_state.json",
            "report.md",
            "audit_log.json",
            "run_manifest.json",
        ],
    )
    if not audit_res["passed"]:
        raise RuntimeError(f"final_audit failed: {audit_res['blocking_failures']}")
    return res


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor 1e85736f Incremental Literature Discovery Tracker.")
    parser.add_argument("--input", "-i", default="", help="Input case directory (default: /input).")
    parser.add_argument("--output", "-o", default="", help="Output directory (default: /app/output).")
    parser.add_argument("--work", "-w", default="", help="Work directory (default: /app/work).")
    parser.add_argument("--state-file", default="", help="Optional alert_ledger.json path for idempotent reruns.")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout JSON summary.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    sf = Path(args.state_file) if args.state_file else None
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], state_file=sf)
    if not args.quiet:
        print(
            json.dumps(
                {
                    "ok": True,
                    "case_id": res["case_id"],
                    "status": res["status"],
                    "idempotency": res.get("idempotency"),
                    "metrics": res["metrics"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
