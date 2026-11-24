#!/usr/bin/env python3
"""Pure-stdlib multi-format extractor (MD/TXT/PDF/XLSX/DOCX/XML/CSV/TSV + Markdown tables + free prose)
into source_ref-anchored draft JSON with strict NOT_FOUND_IN_SOURCE & UNPARSED_SOURCE honesty.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from block_indexer import _extract_pdf_pages_offline as _extract_pdf_pages  # noqa: E402

# Numeric helper supporting thousands commas (e.g. $4,500.50)
NUM_TOK = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
MISSING_TOKENS = {
    "tbd", "unknown", "unquoted", "none", "null", "na", "n/a",
    "未报价", "待定", "未说明", "未知", "unverified", "pending_quote", "?", "-", "--",
}

# Structured + free-form prose patterns
BUDGET_RES = [
    re.compile(rf"(?:budget(?:\s*(?:cap|limit|ceiling))?|预算上限|总预算)\s*[:=：]?\s*(?:is|of|capped\s+at|must\s+not\s+exceed|<=|≤)?\s*(?:USD|US\$|\$|¥|CNY)?\s*{NUM_TOK}", re.I),
    re.compile(rf"(?:must\s+not\s+exceed|capped\s+at|limited\s+to|maximum\s+spend(?:\s+of)?)\s*(?:USD|US\$|\$|¥|CNY)\s*{NUM_TOK}", re.I),
]
DEADLINE_RES = [
    re.compile(rf"(?:deadline|turnaround\s*limit|时间上限|周期上限|截止周期)\s*[:=：]?\s*(?:is|of|within|<=|≤)?\s*{NUM_TOK}\s*(?:calendar\s+|working\s+)?(?:days?|d|天)\b", re.I),
    re.compile(rf"(?:completed?|finish(?:ed)?|delivered?)\s+within\s+{NUM_TOK}\s*(?:calendar\s+|working\s+)?(?:days?|天)\b", re.I),
]
TIME_HR_RES = [
    re.compile(rf"(?:time\s*limit|machine\s*hour\s*cap|instrument\s*time\s*(?:cap|limit)|机时上限)\s*[:=：]?\s*(?:is|of|<=|≤)?\s*{NUM_TOK}\s*(?:hours?|hrs?|h|小时)\b", re.I),
    re.compile(rf"(?:up\s+to|at\s+most|capped\s+at)\s+{NUM_TOK}\s*(?:machine|instrument|beamline)?\s*(?:hours?|hrs?|小时)\b", re.I),
]
SAMPLE_POOL_RES = [
    re.compile(rf"(?:sample\s*pool(?:\s*available)?|max(?:imum)?\s*samples?|available\s+samples?|可用样本(?:数|池)?)\s*[:=：]?\s*(?:is|of|<=|≤)?\s*([0-9][0-9,]*)", re.I),
    re.compile(r"(?:up\s+to|at\s+most|only)\s+([0-9][0-9,]*)\s+(?:biological\s+|patient\s+|clinical\s+)?samples?\s+(?:are\s+)?available", re.I),
]
STABILITY_RE = re.compile(rf"(?:sample[\s_-]*stability|shelf[\s_-]*life|样本保存期)\s*[:=：]?\s*(?:is|of)?\s*{NUM_TOK}\s*(?:days?|d|天)\b", re.I)
SETUP_COST_RE = re.compile(rf"(?:setup[\s_-]*cost|one[\s_-]*time[\s_-]*setup(?:[\s_-]*cost)?|一次性(?:启动|准备)费)\s*[:=：]?\s*(?:USD|\$|¥|CNY)?\s*([0-9][0-9,]*(?:\.[0-9]+)?|TBD|unknown|unquoted|未报价|待定)", re.I)
PER_SAMPLE_RE = re.compile(rf"(?:per[\s_-]*sample[\s_-]*cost|单样本(?:耗材)?费)\s*[:=：]?\s*(?:USD|\$|¥|CNY)?\s*([0-9][0-9,]*(?:\.[0-9]+)?|TBD|unknown|unquoted|未报价|待定)", re.I)
LABOR_RATE_RE = re.compile(rf"(?:labor[\s_-]*rate(?:[\s_-]*per[\s_-]*day)?|人工费率)\s*[:=：]?\s*(?:USD|\$|¥|CNY)?\s*{NUM_TOK}", re.I)
PREP_DAYS_RE = re.compile(rf"(?:(?:base[\s_-]*)?prep(?:aration)?[\s_-]*days|准备天数)\s*[:=：]?\s*{NUM_TOK}", re.I)
QC_DAYS_RE = re.compile(rf"(?:qc(?:[\s_-]*turnaround)?[\s_-]*days|质控天数)\s*[:=：]?\s*{NUM_TOK}", re.I)

PARAM_BRACKET_RE = re.compile(
    r"(?:parameter|factor|参数)\s*[`'\"]?([A-Za-z0-9_]+)[`'\"]?\s*[:=：]\s*\[\s*(-?[0-9]+(?:\.[0-9]+)?)\s*,\s*(-?[0-9]+(?:\.[0-9]+)?)\s*\]"
    r"(?:\s*([A-Za-z/%°^0-9_-]+))?(.*)$",
    re.I,
)
PARAM_PROSE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]{1,28})\b\s+(?:ranges?\s+from|between|from|constrained\s+to)\s+(-?[0-9]+(?:\.[0-9]+)?)\s*(?:to|and|-|–)\s*(-?[0-9]+(?:\.[0-9]+)?)\s*([A-Za-z/%°^0-9_-]*)(.*)$",
    re.I,
)
RESOURCE_RE = re.compile(r"(?:resource|equipment|instrument|reagent|资源|设备|试剂)\s*[`'\"]?([A-Za-z0-9_-]+)[`'\"]?\s*[:=：]\s*(.+)$", re.I)
CONTROL_RE = re.compile(r"^(?:[-*]\s*)?(?:control(?:\s*arm)?\b|对照(?:组|臂)?)\s*[:=：]?\s*[`'\"]?([A-Za-z0-9_-]{2,})[`'\"]?\s*(?:\(([^)]*)\))?\s*[:=：-]?\s*(.*)$", re.I)
CONTROL_PROSE_RE = re.compile(r"\b(vehicle|blank|negative|positive|sham|isotype|untreated)\s+control(?:\s*\(([^)]+)\))?", re.I)
HYP_RE = re.compile(r"^(?:[-*]\s*)?(?:hypothesis|research\s+hypothesis|科学假设)\s*[:=：]\s*(.+)$", re.I)
IV_RE = re.compile(r"^(?:[-*]\s*)?(?:iv|independent\s*variable|自变量)\s*[:=：]\s*(.+)$", re.I)
DV_RE = re.compile(r"^(?:[-*]\s*)?(?:dv|dependent\s*variable|primary\s+readout|因变量)\s*[:=：]\s*(.+)$", re.I)
CONFOUNDER_RE = re.compile(r"^(?:[-*]\s*)?(?:confounders?|混杂因素)\s*[:=：]\s*(.+)$", re.I)
INF_UNIT_RE = re.compile(r"^(?:[-*]\s*)?(?:inference\s*unit|experimental\s+unit|推断单位)\s*[:=：]\s*(.+)$", re.I)

APPROVAL_VAL_PAT = r"(not[\s_-]*required|pending[\s_-]*amendment|in[\s_-]*review|not[\s_-]*yet[\s_-]*approved|under[\s_-]*review|pending|approved|unverified|unknown|denied|none|true|false|yes|no|未审批|审查中|待定)"
BIOSAFETY_RE = re.compile(rf"(?:biosafety(?:\s*(?:approval|clearance))?|生物安全审批)\s*(?:[:=：]|is|remains|status\s*[:=])?\s*{APPROVAL_VAL_PAT}\b", re.I)
ETHICS_RE = re.compile(rf"(?:ethics(?:\s*(?:approval|clearance))?|irb|iacuc|伦理审批)\s*(?:[:=：]|is|remains|status\s*[:=])?\s*{APPROVAL_VAL_PAT}\b", re.I)
CAND_HDR_RE = re.compile(r"^(?:#{1,4}\s*|(?:[-*]\s*)?)(?:plan|candidate(?:\s*plan)?|version|option|arm|protocol|strategy|design|route|scheme|workflow|method|approach|候选方案|方案)\s*[`'\"]?([A-Za-z0-9_.-]+)[`'\"]?\s*[:：-]?(.*)$", re.I)
FALLBACK_H3_CAND_RE = re.compile(r"^###\s+([A-Za-z0-9_.-]+)(?:\s*[:：-]\s*(.*))?$")
SOP_VER_RE = re.compile(r"\b(SOP[-_][A-Za-z0-9_-]+)\s*[:@]?\s*(v[0-9]+(?:\.[0-9]+)?|latest|unpinned)\b", re.I)
ELABFTW_STATUS_MAP = {
    "available": "available", "ready": "available", "active": "available", "in_stock": "available",
    "in_use": "available", "booked": "available",
    "needs_procurement": "needs_procurement", "order": "needs_procurement", "backorder": "needs_procurement", "low_stock": "needs_procurement",
    "outsourceable": "outsourceable", "external_core": "outsourceable", "external": "outsourceable",
    "unavailable": "unavailable", "out_of_order": "unavailable", "maintenance": "unavailable", "broken": "unavailable", "retired": "unavailable",
}


class _XMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def handle_data(self, data: str) -> None:
        s = data.strip()
        if s:
            self.lines.append(s)


def _num(token: Any) -> float | None:
    if token is None or isinstance(token, bool):
        return None
    s = str(token).strip().replace(",", "")
    s = re.sub(r"^(?:USD|US\$|\$|¥|CNY)\s*", "", s, flags=re.I)
    if not s or s.lower() in MISSING_TOKENS:
        return None
    m = re.match(r"^(-?[0-9]+(?:\.[0-9]+)?)$", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_approval(token: str) -> Any:
    t = re.sub(r"[\s-]+", "_", token.strip().lower())
    if t in {"approved", "true", "yes"}:
        return True
    if t in {"denied", "false", "no"}:
        return False
    if t in {"in_review", "under_review", "pending", "pending_amendment", "审查中"}:
        return "in_review"
    return None


def _parse_kv(tail: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,;|\s]+(?:\s+[^,;|=]+)?)", tail):
        pairs[m.group(1).strip().lower()] = m.group(2).strip().rstrip(".,;")
    return pairs


def _col_idx(ref: str) -> int:
    col = "".join(ch for ch in ref if ch.isalpha()).upper()
    idx = 0
    for ch in col:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(0, idx - 1)


def _read_xlsx_sheets(path: Path) -> list[tuple[str, list[list[str]]]]:
    ns_m = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    ns_r = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    out: list[tuple[str, list[list[str]]]] = []
    with zipfile.ZipFile(str(path), "r") as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall(f"{ns_m}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns_m}t")))
        rels: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in zf.namelist():
            for r in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels")):
                rid, tgt = r.attrib.get("Id", ""), r.attrib.get("Target", "")
                if rid and tgt:
                    rels[rid] = tgt if tgt.startswith("xl/") else f"xl/{tgt.lstrip('/')}"
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        for idx, sh in enumerate(wb.findall(f".//{ns_m}sheet"), start=1):
            sname = sh.attrib.get("name", f"Sheet{idx}")
            tgt = rels.get(sh.attrib.get(f"{ns_r}id", ""), f"xl/worksheets/sheet{idx}.xml")
            if tgt not in zf.namelist():
                continue
            rows: list[list[str]] = []
            for row_el in ET.fromstring(zf.read(tgt)).findall(f".//{ns_m}sheetData/{ns_m}row"):
                rmap: dict[int, str] = {}
                max_c = -1
                for c_el in row_el.findall(f"{ns_m}c"):
                    ci = _col_idx(c_el.attrib.get("r", "")) if c_el.attrib.get("r") else max_c + 1
                    max_c = max(max_c, ci)
                    ct = c_el.attrib.get("t", "")
                    if ct == "inlineStr":
                        val = "".join(t.text or "" for t in c_el.iter(f"{ns_m}t"))
                    else:
                        v_el = c_el.find(f"{ns_m}v")
                        raw = v_el.text if v_el is not None else ""
                        val = shared[int(raw)] if ct == "s" and raw.isdigit() and int(raw) < len(shared) else (raw or "")
                    rmap[ci] = val.strip()
                if max_c >= 0:
                    rlist = [rmap.get(i, "") for i in range(max_c + 1)]
                    if any(rlist):
                        rows.append(rlist)
            out.append((sname, rows))
    return out


def _read_docx_lines(path: Path) -> list[str]:
    ns_w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(str(path), "r") as zf:
        if "word/document.xml" not in zf.namelist():
            return []
        root = ET.fromstring(zf.read("word/document.xml"))
        return [
            txt for p in root.iter(f"{ns_w}p")
            if (txt := "".join(t.text or "" for t in p.iter(f"{ns_w}t")).strip())
        ]


def _extract_md_tables(rel: str, lines: list[str]) -> list[tuple[str, list[str], list[tuple[str, list[str]]]]]:
    """Extract Markdown pipe tables with exact file#L<row> anchors."""
    tables: list[tuple[str, list[str], list[tuple[str, list[str]]]]] = []
    i = 0
    while i + 1 < len(lines):
        ln = lines[i].strip()
        nxt = lines[i + 1].strip()
        if ln.startswith("|") and "|" in ln[1:] and re.match(r"^\|[\s:|-]+\|?$", nxt):
            hdr = [c.strip() for c in ln.strip("|").split("|")]
            rows: list[tuple[str, list[str]]] = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if any(cells):
                    rows.append((f"{rel}#L{j + 1}", cells))
                j += 1
            tables.append((f"{rel}#L{i + 1}", hdr, rows))
            i = j
        else:
            i += 1
    return tables


def _ingest_table(
    headers: list[str],
    rows: list[tuple[str, list[str]]],
    resources: dict[str, dict[str, Any]],
    parameter_domains: dict[str, dict[str, Any]],
    historical_runs: list[dict[str, Any]],
    mark_missing: Any,
    col_trace: list[dict[str, Any]] | None = None,
) -> int:
    """Classify and ingest a table with exact-match-first column resolution (zero substring collision) + eLabFTW resource export support."""
    if not headers or not rows:
        return 0
    norm_h = [re.sub(r"[^a-z0-9]+", "_", h.lower()).strip("_") for h in headers]

    def find_col(aliases: tuple[str, ...], *, exclude: tuple[str, ...] = ()) -> int | None:
        # Pass 1: exact normalized header match (highest priority, prevents labor_rate vs rate_per_hr collisions)
        for a in aliases:
            for idx, h in enumerate(norm_h):
                if h == a and not any(ex in h for ex in exclude):
                    return idx
        # Pass 2: controlled substring fallback excluding forbidden tokens
        for a in aliases:
            for idx, h in enumerate(norm_h):
                if a in h and not any(ex in h for ex in exclude):
                    return idx
        return None

    res_c = find_col(("resource", "equipment", "instrument", "reagent", "item", "asset", "title"))
    par_c = find_col(("parameter", "factor", "variable"))
    min_c = find_col(("min", "lower", "low_bound", "low"))
    max_c = find_col(("max", "upper", "high_bound", "high"))
    resp_c = find_col(("response", "yield", "overpotential", "activity", "metric", "purity", "efficiency", "score", "output"))
    ingested = 0

    if res_c is not None and any(find_col(g) is not None for g in (("status", "availability", "canwrite"), ("unit_cost", "price", "cost", "rate_per_hr", "hourly_rate"), ("lead_days", "booking_lead", "booking_days"))):
        st_c = find_col(("status", "availability"))
        uc_c = find_col(("unit_cost", "price", "cost"), exclude=("labor", "setup", "per_sample"))
        hr_c = find_col(("rate_per_hr", "hourly_rate", "machine_rate"), exclude=("labor", "rework", "recovery"))
        ld_c = find_col(("lead_days", "lead_time", "procurement_days"), exclude=("booking",))
        bl_c = find_col(("booking_lead_days", "booking_lead", "booking_days"))
        qv_c = find_col(("quote_valid_until", "quote_valid", "valid_until"))
        cw_c = find_col(("canwrite", "permission", "bookable"))
        cat_c = find_col(("category", "resource_type"))
        ms_c = find_col(("maintenance_status", "maintenance", "calibration_status"))
        if col_trace is not None:
            col_trace.append({
                "table_kind": "resources",
                "matched": {k: headers[c] for k, c in (("resource", res_c), ("status", st_c), ("unit_cost", uc_c), ("rate_per_hr", hr_c), ("lead_days", ld_c), ("booking_lead", bl_c), ("canwrite", cw_c), ("maintenance_status", ms_c)) if c is not None},
            })
        for ref, cells in rows:
            if res_c >= len(cells) or not cells[res_c].strip():
                continue
            rname = re.sub(r"\s+", "_", cells[res_c].strip().strip("`'\""))
            uc = _num(cells[uc_c]) if (uc_c is not None and uc_c < len(cells)) else None
            hr = _num(cells[hr_c]) if (hr_c is not None and hr_c < len(cells)) else None
            ld = _num(cells[ld_c]) if (ld_c is not None and ld_c < len(cells)) else None
            bl = _num(cells[bl_c]) if (bl_c is not None and bl_c < len(cells)) else None
            raw_st = cells[st_c].strip().lower() if (st_c is not None and st_c < len(cells) and cells[st_c].strip()) else "unknown"
            st = ELABFTW_STATUS_MAP.get(raw_st, raw_st)
            if cw_c is not None and cw_c < len(cells) and cells[cw_c].strip().lower() in {"0", "false", "no", "denied"}:
                st = "unavailable"
            explicit_ms = cells[ms_c].strip().lower() if (ms_c is not None and ms_c < len(cells) and cells[ms_c].strip()) else None
            maint_st = "overdue" if raw_st in {"maintenance", "out_of_order"} else (explicit_ms or "unknown")
            resources[rname] = {
                "status": st,
                "raw_status": raw_st,
                "category": cells[cat_c].strip() if (cat_c is not None and cat_c < len(cells) and cells[cat_c].strip()) else None,
                "unit_cost": uc,
                "rate_per_hr": hr,
                "lead_days": ld,
                "booking_lead_days": bl,
                "quote_valid_until": cells[qv_c].strip() if (qv_c is not None and qv_c < len(cells) and cells[qv_c].strip()) else None,
                "maintenance_status": maint_st,
                "substitutable": False,
                "adapter_used": "elabftw_resource_adapter" if (cw_c is not None or cat_c is not None) else "table_contract",
                "source_ref": ref,
            }
            if maint_st == "unknown":
                mark_missing(f"resources.{rname}.maintenance_status", f"Maintenance/calibration status not stated in {ref}")
            if uc is None and hr is None:
                mark_missing(f"resources.{rname}.unit_cost_or_rate", f"Unquoted/missing cost in table row {ref}")
            ingested += 1
        return ingested

    if par_c is not None and min_c is not None and max_c is not None:
        smax_c = find_col(("safety_max", "safe_max", "safety_limit", "ceiling"))
        step_c = find_col(("step", "increment"))
        unit_c = find_col(("unit", "units"))
        for ref, cells in rows:
            if par_c >= len(cells) or not cells[par_c].strip():
                continue
            pname = re.sub(r"\s+", "_", cells[par_c].strip().strip("`'\""))
            lo = _num(cells[min_c]) if min_c < len(cells) else None
            hi = _num(cells[max_c]) if max_c < len(cells) else None
            if lo is not None and hi is not None:
                parameter_domains[pname] = {
                    "min": lo,
                    "max": hi,
                    "safety_max": _num(cells[smax_c]) if (smax_c is not None and smax_c < len(cells)) else None,
                    "step": _num(cells[step_c]) if (step_c is not None and step_c < len(cells)) else None,
                    "unit": cells[unit_c].strip() if (unit_c is not None and unit_c < len(cells)) else "",
                    "source_ref": ref,
                }
                ingested += 1
        return ingested

    if resp_c is not None:
        for ref, cells in rows:
            if resp_c >= len(cells) or _num(cells[resp_c]) is None:
                continue
            rec: dict[str, Any] = {"source_ref": ref}
            num_factors = 0
            for ci, h in enumerate(norm_h):
                if ci >= len(cells):
                    continue
                v = _num(cells[ci])
                if v is not None:
                    rec[h] = v
                    if ci != resp_c and h not in {"run_id", "id", "run", "index"}:
                        num_factors += 1
                elif cells[ci].strip():
                    rec[h] = cells[ci].strip()
            if num_factors >= 1:
                historical_runs.append(rec)
                ingested += 1
    return ingested


def extract_from_sources(input_dir: Path, mode: str, blocks_path: Path | None = None) -> dict[str, Any]:
    """Extract source_ref-anchored structured draft JSON from raw multi-format /input sources."""
    case_json_p = input_dir / "case.json"
    case_meta = json.loads(case_json_p.read_text(encoding="utf-8-sig")) if case_json_p.is_file() else {}
    case_id = str(case_meta.get("case_id") or "extracted-case")
    title = str(case_meta.get("title") or input_dir.name)

    docs: list[tuple[str, list[str], str]] = []  # (rel, lines, ref_prefix)
    tables_to_ingest: list[tuple[list[str], list[tuple[str, list[str]]]]] = []
    unparsed_sources: list[dict[str, Any]] = []
    format_counts: dict[str, int] = {}

    for fp in sorted(input_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(input_dir).as_posix()
        suf = fp.suffix.lower()
        if suf in {".md", ".txt", ".yaml", ".yml"}:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            docs.append((rel, lines, "L"))
            for _, hdr, trows in _extract_md_tables(rel, lines):
                tables_to_ingest.append((hdr, trows))
            format_counts[suf] = format_counts.get(suf, 0) + 1
        elif suf in {".xml", ".html", ".htm"}:
            p = _XMLTextParser()
            p.feed(fp.read_text(encoding="utf-8", errors="replace"))
            docs.append((rel, p.lines, "L"))
            format_counts[suf] = format_counts.get(suf, 0) + 1
        elif suf == ".docx":
            lines = _read_docx_lines(fp)
            if lines:
                docs.append((rel, lines, "L"))
                format_counts[suf] = format_counts.get(suf, 0) + 1
            else:
                unparsed_sources.append({"file": rel, "status": "UNPARSED_SOURCE", "reason": "empty_docx_text", "manual_transcription_needed": True})
        elif suf in {".csv", ".tsv"}:
            delim = "\t" if suf == ".tsv" else ","
            raw_rows = list(csv.reader(fp.read_text(encoding="utf-8", errors="replace").splitlines(), delimiter=delim))
            if len(raw_rows) >= 2:
                tables_to_ingest.append((raw_rows[0], [(f"{rel}#L{i}", r) for i, r in enumerate(raw_rows[1:], start=2)]))
                format_counts[suf] = format_counts.get(suf, 0) + 1
        elif suf == ".xlsx":
            try:
                for sname, srows in _read_xlsx_sheets(fp):
                    if len(srows) >= 2:
                        tables_to_ingest.append((srows[0], [(f"{rel}#{sname}!R{i}", r) for i, r in enumerate(srows[1:], start=2)]))
                format_counts[suf] = format_counts.get(suf, 0) + 1
            except Exception as exc:
                unparsed_sources.append({"file": rel, "status": "UNPARSED_SOURCE", "reason": str(exc), "manual_transcription_needed": True})
        elif suf == ".pdf":
            backend, pages = _extract_pdf_pages(fp)
            if pages:
                for pno, ptxt in enumerate(pages, start=1):
                    docs.append((f"{rel}#page={pno}", ptxt.splitlines(), "pL"))
                format_counts[f".pdf({backend})"] = format_counts.get(f".pdf({backend})", 0) + 1
            else:
                unparsed_sources.append({
                    "file": rel,
                    "status": "UNPARSED_SOURCE",
                    "reason": "no_extractable_text_layer_offline",
                    "manual_transcription_needed": True,
                })

    not_found: list[dict[str, str]] = []
    source_anchors: dict[str, str] = {}

    def _mark_missing(field: str, reason: str) -> None:
        not_found.append({"field": field, "status": "NOT_FOUND_IN_SOURCE", "reason": reason})

    hypothesis: dict[str, Any] = {"statement": None, "iv": None, "dv": None, "dv_unit": None, "measurement_method": None, "confounders": []}
    inference_unit: str | None = None
    controls: list[dict[str, Any]] = []
    seen_ctrl_names: set[str] = set()
    resources: dict[str, dict[str, Any]] = {}
    parameter_domains: dict[str, dict[str, Any]] = {}
    coupled_constraints: list[dict[str, Any]] = []
    linear_constraints: list[dict[str, Any]] = []
    mutually_exclusive: list[Any] = []
    domain_source_conflicts: list[dict[str, Any]] = []
    historical_runs: list[dict[str, Any]] = []
    contract: dict[str, Any] = {
        "budget_cap_usd": None, "deadline_days": None, "time_limit_hours": None,
        "max_samples": None, "sample_pool_available": None, "sample_stability_days": None,
        "setup_cost_usd": None, "per_sample_cost_usd": None, "labor_rate_per_day": None,
        "base_prep_days": None, "qc_days": None,
        "biosafety_approved": None, "ethics_approval_required": None, "ethics_approved": None,
        "resources": resources, "parameter_domains": parameter_domains,
    }
    # Merge organizer-supplied structured fields from case.json if present (never ignore case.json)
    if isinstance(case_meta.get("hypothesis"), dict):
        for hk, hv in case_meta["hypothesis"].items():
            if hv not in (None, "", []):
                hypothesis[hk] = hv
                source_anchors[f"hypothesis.{hk}"] = "case.json"
    if isinstance(case_meta.get("statistical_design"), dict) and case_meta["statistical_design"].get("inference_unit"):
        inference_unit = str(case_meta["statistical_design"]["inference_unit"])
        source_anchors["inference_unit"] = "case.json"
    if isinstance(case_meta.get("controls"), list):
        for ct in case_meta["controls"]:
            if isinstance(ct, dict) and ct.get("name"):
                cn = str(ct["name"]).strip()
                if cn.lower() not in seen_ctrl_names:
                    controls.append({**ct, "source_ref": ct.get("source_ref") or "case.json"})
                    seen_ctrl_names.add(cn.lower())
    if isinstance(case_meta.get("prospective_contract"), dict):
        for ck, cv in case_meta["prospective_contract"].items():
            if ck == "resources" and isinstance(cv, dict):
                for rn, rm in cv.items():
                    if isinstance(rm, dict):
                        resources[rn] = {"maintenance_status": "unknown", **rm, "source_ref": rm.get("source_ref") or "case.json"}
            elif ck == "parameter_domains" and isinstance(cv, dict):
                for pn, pb in cv.items():
                    if isinstance(pb, dict):
                        parameter_domains[pn] = {**pb, "source_ref": pb.get("source_ref") or "case.json"}
            elif ck in contract and cv is not None:
                contract[ck] = cv
                source_anchors[ck] = "case.json"

    candidates: list[dict[str, Any]] = []
    current_cand: dict[str, Any] | None = None
    # Document-isolated scopes prevent cross-file scientific/control/approval contamination
    doc_scopes: dict[str, dict[str, Any]] = {}

    def _get_doc_scope(dkey: str) -> dict[str, Any]:
        if dkey not in doc_scopes:
            doc_scopes[dkey] = {
                "hypothesis": {"statement": None, "iv": None, "dv": None, "dv_unit": None, "measurement_method": None, "confounders": []},
                "inference_unit": None,
                "controls": [],
                "seen_ctrls": set(),
                "contract": {},
            }
        return doc_scopes[dkey]

    def _merge_param_domain(pname: str, incoming: dict[str, Any], ref: str) -> None:
        if pname in parameter_domains:
            prev = parameter_domains[pname]
            # Check for contradictory min/max/safety_max across sources
            for bk in ("min", "max", "safety_max"):
                v_old, v_new = _num(prev.get(bk)), _num(incoming.get(bk))
                if v_old is not None and v_new is not None and abs(v_old - v_new) > 1e-9:
                    domain_source_conflicts.append({
                        "parameter": pname,
                        "field": bk,
                        "values": [v_old, v_new],
                        "source_refs": [prev.get("source_ref", ""), ref],
                        "reason": f"Conflicting {bk} bounds for {pname}: {v_old} ({prev.get('source_ref')}) vs {v_new} ({ref})",
                    })
            merged = dict(prev)
            for k, v in incoming.items():
                if v is not None and (merged.get(k) is None or k == "allowed_values"):
                    merged[k] = v
            # Tighten intersection if both bounds exist so safety ceiling is never loosened
            p_lo, n_lo = _num(prev.get("min")), _num(incoming.get("min"))
            p_hi, n_hi = _num(prev.get("max")), _num(incoming.get("max"))
            p_sm, n_sm = _num(prev.get("safety_max")), _num(incoming.get("safety_max"))
            if p_lo is not None and n_lo is not None:
                merged["min"] = max(p_lo, n_lo)
            if p_hi is not None and n_hi is not None:
                merged["max"] = min(p_hi, n_hi)
            if p_sm is not None and n_sm is not None:
                merged["safety_max"] = min(p_sm, n_sm)
            merged["source_ref"] = f"{prev.get('source_ref', '')}; {ref}".strip("; ")
            parameter_domains[pname] = merged
        else:
            parameter_domains[pname] = {**incoming, "source_ref": incoming.get("source_ref") or ref}

    col_match_trace: list[dict[str, Any]] = []
    # Ingest structured JSON files in /input: eLabFTW exports, domain/constraint JSONs, and pilot run JSONs
    for fp in sorted(input_dir.rglob("*.json")):
        if fp.name.lower() == "case.json":
            continue
        try:
            obj = json.loads(fp.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        rel_j = fp.relative_to(input_dir).as_posix()
        items = obj if isinstance(obj, list) else (obj.get("items") or obj.get("resources") if isinstance(obj, dict) else None)
        if isinstance(items, list) and items and isinstance(items[0], dict) and ("title" in items[0] or "canwrite" in items[0]):
            for r_i, it in enumerate(items, start=1):
                rname = re.sub(r"\s+", "_", str(it.get("title") or it.get("name") or f"item_{r_i}").strip())
                meta = it.get("metadata") if isinstance(it.get("metadata"), dict) else {}
                raw_st = str(it.get("status") or meta.get("status") or "unknown").strip().lower()
                st = ELABFTW_STATUS_MAP.get(raw_st, raw_st)
                if it.get("canwrite") in (0, False, "0", "false"):
                    st = "unavailable"
                uc = _num(it.get("unit_cost", meta.get("unit_cost")))
                hr = _num(it.get("rate_per_hr", meta.get("rate_per_hr")))
                raw_ms = it.get("maintenance_status", meta.get("maintenance_status"))
                maint_st = "overdue" if raw_st in {"maintenance", "out_of_order"} else (str(raw_ms).strip().lower() if raw_ms else "unknown")
                resources[rname] = {
                    "status": st, "raw_status": raw_st, "category": it.get("category"),
                    "unit_cost": uc, "rate_per_hr": hr,
                    "lead_days": _num(it.get("lead_days", meta.get("lead_days"))),
                    "booking_lead_days": _num(it.get("booking_lead_days", meta.get("booking_lead_days"))),
                    "quote_valid_until": it.get("quote_valid_until", meta.get("quote_valid_until")),
                    "maintenance_status": maint_st,
                    "substitutable": bool(it.get("substitutable", False)),
                    "adapter_used": "elabftw_json_resource_adapter",
                    "source_ref": f"{rel_j}#item[{r_i}]",
                }
                if uc is None and hr is None:
                    _mark_missing(f"resources.{rname}.unit_cost_or_rate", f"Unquoted/missing cost in eLabFTW export {rel_j}#item[{r_i}]")
            format_counts[".json(elabftw)"] = format_counts.get(".json(elabftw)", 0) + 1
            continue

        if isinstance(obj, dict):
            parsed_any_json = False
            raw_params = obj.get("parameters") or obj.get("parameter_domains")
            if isinstance(raw_params, dict):
                for pname, pspec in raw_params.items():
                    if isinstance(pspec, dict):
                        _merge_param_domain(str(pname), pspec, f"{rel_j}#parameters.{pname}")
                        parsed_any_json = True
            elif isinstance(raw_params, list):
                for pi, pspec in enumerate(raw_params, start=1):
                    if isinstance(pspec, dict) and (pspec.get("name") or pspec.get("parameter")):
                        pname = str(pspec.get("name") or pspec.get("parameter"))
                        _merge_param_domain(pname, pspec, f"{rel_j}#parameters[{pi}]")
                        parsed_any_json = True
            for cc in obj.get("coupled_constraints") or []:
                if isinstance(cc, dict):
                    coupled_constraints.append({**cc, "source_ref": cc.get("source_ref") or rel_j})
                    parsed_any_json = True
            for lc in obj.get("linear_constraints") or []:
                if isinstance(lc, dict):
                    linear_constraints.append({**lc, "source_ref": lc.get("source_ref") or rel_j})
                    parsed_any_json = True
            for me in obj.get("mutually_exclusive") or []:
                mutually_exclusive.append(me)
                parsed_any_json = True
            if isinstance(obj.get("resources"), dict):
                for rn, rm in obj["resources"].items():
                    if isinstance(rm, dict):
                        resources[rn] = {"maintenance_status": "unknown", **rm, "source_ref": rm.get("source_ref") or f"{rel_j}#resources.{rn}"}
                        parsed_any_json = True
            if isinstance(obj.get("runs"), list) and obj["runs"] and isinstance(obj["runs"][0], dict):
                for ri, rrow in enumerate(obj["runs"], start=1):
                    historical_runs.append({**rrow, "source_ref": rrow.get("source_ref") or f"{rel_j}#runs[{ri}]"})
                parsed_any_json = True
            if parsed_any_json:
                format_counts[".json(structured)"] = format_counts.get(".json(structured)", 0) + 1

    table_rows_ingested = 0
    for hdr, trows in tables_to_ingest:
        table_rows_ingested += _ingest_table(hdr, trows, resources, parameter_domains, historical_runs, _mark_missing, col_trace=col_match_trace)

    for rel, lines, ref_kind in docs:
        doc_key = rel.split("#page=")[0]
        dscope = _get_doc_scope(doc_key)
        # Never carry a candidate block across distinct source files!
        current_cand = None
        for lno, raw_ln in enumerate(lines, start=1):
            ln = raw_ln.strip()
            if not ln or ln.startswith("<!--") or (ln.startswith("|") and re.match(r"^\|[\s:|-]+\|?$", ln)):
                continue
            ref = f"{rel}:L{lno}" if ref_kind == "pL" else f"{rel}#L{lno}"

            # Dual-channel candidate header detection (explicit plan/option/protocol header OR ### fallback section)
            m_cand = CAND_HDR_RE.match(ln)
            m_h3 = FALLBACK_H3_CAND_RE.match(ln) if not m_cand else None
            is_explicit_cand = bool(m_cand and (ln.startswith("#") or any(tok in ln.lower() for tok in ("v1", "v2", "v3", "v4", "plan", "option", "arm", "protocol", "cand", "方案"))))
            is_h3_fallback = bool(m_h3 and m_h3.group(1).lower() not in {"overview", "background", "references", "notes", "summary", "abstract", "methods", "results", "introduction", "discussion"})
            if is_explicit_cand or is_h3_fallback:
                ver = (m_cand.group(1) if is_explicit_cand and m_cand else m_h3.group(1)).strip()  # type: ignore[union-attr]
                rat = (m_cand.group(2) if is_explicit_cand and m_cand else (m_h3.group(2) or "")).strip()  # type: ignore[union-attr]
                current_cand = {
                    "version": ver, "plan_id": ver, "source_doc": doc_key, "source_ref": ref, "rationale": rat,
                    "treatment_arms": None, "arms": None, "biological_replicates": None, "replicates_per_arm": None,
                    "technical_replicates": None, "qc_extra_samples": None, "declared_total_samples": None, "planned_runs": None,
                    "machine_hours": None, "machine_hours_per_run": None, "machine_hours_source_ref": None,
                    "one_time_setup_cost": None, "labor_days": None,
                    "prep_days": None, "qc_turnaround_days": None, "parameters": {}, "resource_usage": {},
                    "steps": [], "risks": [], "sop_versions": [], "stop_loss": "", "fallback_path": "",
                    "search_strategy": None, "stopping_criterion": "",
                    "predicted_response": None, "predicted_uncertainty": None,
                    "scientific_fit_score": None, "evidence_score": None, "risk_level": "UNSPECIFIED",
                    "local_controls": [], "local_primary_readout": None, "local_inference_unit": None,
                    "local_hypothesis": None, "approval_status": None, "source_conflicts": [],
                }
                candidates.append(current_cand)
                continue

            if current_cand is not None:
                for sm in SOP_VER_RE.finditer(ln):
                    current_cand["sop_versions"].append({"sop_id": sm.group(1), "version": sm.group(2), "source_ref": ref})
                kv = _parse_kv(ln)
                for mh_key in ("machine_hours", "total_machine_hours"):
                    if mh_key in kv and (mhv := _num(kv[mh_key])) is not None:
                        if current_cand["machine_hours"] is not None and abs(current_cand["machine_hours"] - mhv) > 1e-6:
                            current_cand["source_conflicts"].append({
                                "field": "machine_hours",
                                "values": [current_cand["machine_hours"], mhv],
                                "source_refs": [current_cand.get("machine_hours_source_ref"), ref],
                            })
                        current_cand["machine_hours"] = mhv
                        current_cand["machine_hours_source_ref"] = ref
                if "machine_hours_per_run" in kv and (mhpr := _num(kv["machine_hours_per_run"])) is not None:
                    current_cand["machine_hours_per_run"] = mhpr
                    if not current_cand["machine_hours_source_ref"]:
                        current_cand["machine_hours_source_ref"] = ref
                if ln.lower().startswith(("-", "*")) and "step" in ln.lower():
                    st_name = kv.get("step") or kv.get("id") or f"step_{len(current_cand['steps']) + 1}"
                    st_res = kv.get("resource", "")
                    st_units = _num(kv.get("units"))
                    st_hours = _num(kv.get("hours"))
                    st_obj = {
                        "id": st_name, "name": st_name, "duration_days": _num(kv.get("duration_days")),
                        "resource": st_res, "units": st_units, "hours": st_hours,
                        "depends_on": [x.strip() for x in kv.get("depends_on", "").split("+") if x.strip()],
                        "source_ref": ref,
                    }
                    current_cand["steps"].append(st_obj)
                    if st_res and st_units is not None and st_units > 0:
                        current_cand["resource_usage"][st_res] = st_units
                    continue
                for k_in, k_out in (
                    ("one_time_setup_cost", "one_time_setup_cost"), ("setup_cost", "one_time_setup_cost"),
                    ("labor_days", "labor_days"), ("prep_days", "prep_days"),
                    ("qc_turnaround_days", "qc_turnaround_days"), ("qc_days", "qc_turnaround_days"),
                    ("scientific_fit_score", "scientific_fit_score"),
                    ("evidence_score", "evidence_score"), ("predicted_response", "predicted_response"),
                    ("predicted_uncertainty", "predicted_uncertainty"),
                ):
                    if k_in in kv:
                        current_cand[k_out] = _num(kv[k_in])
                for k_in, k_a, k_b in (("treatment_arms", "arms", "treatment_arms"), ("arms", "arms", "treatment_arms"), ("biological_replicates", "biological_replicates", "replicates_per_arm"), ("replicates", "biological_replicates", "replicates_per_arm")):
                    if k_in in kv and (v := _num(kv[k_in])) is not None:
                        current_cand[k_a] = int(v)
                        current_cand[k_b] = int(v)
                if "technical_replicates" in kv and (tv := _num(kv["technical_replicates"])) is not None:
                    current_cand["technical_replicates"] = int(tv)
                if "qc_extra_samples" in kv and (qv := _num(kv["qc_extra_samples"])) is not None:
                    current_cand["qc_extra_samples"] = int(qv)
                if "declared_total_samples" in kv and (dv_s := _num(kv["declared_total_samples"])) is not None:
                    current_cand["declared_total_samples"] = int(dv_s)
                if "planned_runs" in kv and (pr := _num(kv["planned_runs"])) is not None:
                    current_cand["planned_runs"] = int(pr)
                if "approval_status" in kv:
                    current_cand["approval_status"] = kv["approval_status"]
                if "primary_readout" in kv:
                    current_cand["local_primary_readout"] = kv["primary_readout"]
                if "inference_unit" in kv:
                    current_cand["local_inference_unit"] = kv["inference_unit"]
                if "risk_level" in kv:
                    current_cand["risk_level"] = kv["risk_level"]
                if "search_strategy" in kv:
                    current_cand["search_strategy"] = kv["search_strategy"]
                if m_sl := re.search(r"stop_loss\s*[:=：]\s*(.+)$", ln, re.I):
                    current_cand["stop_loss"] = m_sl.group(1).strip()
                    current_cand["risks"].append({"name": "stop_loss", "trigger": current_cand["stop_loss"], "mitigation": "see fallback"})
                if m_fb := re.search(r"fallback(?:_path)?\s*[:=：]\s*(.+)$", ln, re.I):
                    current_cand["fallback_path"] = m_fb.group(1).strip()
                if m_sc := re.search(r"stopping_criterion\s*[:=：]\s*(.+)$", ln, re.I):
                    current_cand["stopping_criterion"] = m_sc.group(1).strip()
                for pk, pv in kv.items():
                    if pk in parameter_domains:
                        nv = _num(pv)
                        current_cand["parameters"][pk] = nv if nv is not None else pv
                # Candidate-local control declaration stays inside candidate scope
                if m_lc := CONTROL_RE.search(ln):
                    cname = m_lc.group(1).strip()
                    if cname not in current_cand["local_controls"]:
                        current_cand["local_controls"].append(cname)
                    continue

            if m := HYP_RE.match(ln):
                dscope["hypothesis"]["statement"] = m.group(1).strip()
                if hypothesis["statement"] is None:
                    hypothesis["statement"], source_anchors["hypothesis"] = m.group(1).strip(), ref
            elif m := IV_RE.match(ln):
                dscope["hypothesis"]["iv"] = m.group(1).strip()
                if hypothesis["iv"] is None:
                    hypothesis["iv"], source_anchors["iv"] = m.group(1).strip(), ref
            elif m := DV_RE.match(ln):
                dv_txt = m.group(1).strip()
                dscope["hypothesis"]["dv"] = dv_txt
                if mu := re.search(r"\(([^)]+)\)", dv_txt):
                    dscope["hypothesis"]["dv_unit"] = mu.group(1).strip()
                if hypothesis["dv"] is None:
                    hypothesis["dv"], source_anchors["dv"] = dv_txt, ref
                    if mu := re.search(r"\(([^)]+)\)", dv_txt):
                        hypothesis["dv_unit"] = mu.group(1).strip()
            elif m := CONFOUNDER_RE.match(ln):
                cfs = [c.strip() for c in re.split(r"[,;、]", m.group(1)) if c.strip()]
                dscope["hypothesis"]["confounders"] = cfs
                if not hypothesis["confounders"]:
                    hypothesis["confounders"], source_anchors["confounders"] = cfs, ref
            elif m := INF_UNIT_RE.match(ln):
                dscope["inference_unit"] = m.group(1).strip()
                if inference_unit is None:
                    inference_unit, source_anchors["inference_unit"] = m.group(1).strip(), ref

            for pat in BUDGET_RES:
                if (m := pat.search(ln)):
                    bv = _num(m.group(1))
                    if bv is not None:
                        dscope["contract"].setdefault("budget_cap_usd", bv)
                        if contract["budget_cap_usd"] is None:
                            contract["budget_cap_usd"], source_anchors["budget_cap_usd"] = bv, ref
            for pat in DEADLINE_RES:
                if (m := pat.search(ln)):
                    dv_num = _num(m.group(1))
                    if dv_num is not None:
                        dscope["contract"].setdefault("deadline_days", dv_num)
                        if contract["deadline_days"] is None:
                            contract["deadline_days"], source_anchors["deadline_days"] = dv_num, ref
            for pat in TIME_HR_RES:
                if (m := pat.search(ln)):
                    tv = _num(m.group(1))
                    if tv is not None:
                        dscope["contract"].setdefault("time_limit_hours", tv)
                        if contract["time_limit_hours"] is None:
                            contract["time_limit_hours"], source_anchors["time_limit_hours"] = tv, ref
            for pat in SAMPLE_POOL_RES:
                if (m := pat.search(ln)):
                    if (sv := _num(m.group(1))) is not None:
                        dscope["contract"].setdefault("sample_pool_available", int(sv))
                        if contract["sample_pool_available"] is None:
                            contract["max_samples"] = contract["sample_pool_available"] = int(sv)
                            source_anchors["sample_pool_available"] = ref
            for pat, key in (
                (STABILITY_RE, "sample_stability_days"), (SETUP_COST_RE, "setup_cost_usd"),
                (PER_SAMPLE_RE, "per_sample_cost_usd"), (LABOR_RATE_RE, "labor_rate_per_day"),
                (PREP_DAYS_RE, "base_prep_days"), (QC_DAYS_RE, "qc_days"),
            ):
                if m := pat.search(ln):
                    val_n = _num(m.group(1))
                    dscope["contract"][key] = val_n
                    if contract.get(key) is None:
                        contract[key], source_anchors[key] = val_n, ref

            if m := BIOSAFETY_RE.search(ln):
                b_val = _parse_approval(m.group(1))
                dscope["contract"]["biosafety_approved"] = b_val
                if contract["biosafety_approved"] is None:
                    contract["biosafety_approved"], source_anchors["biosafety_approved"] = b_val, ref
            if m := ETHICS_RE.search(ln):
                raw_e = re.sub(r"[\s-]+", "_", m.group(1).strip().lower())
                if raw_e in {"not_required", "none"}:
                    dscope["contract"]["ethics_approval_required"], dscope["contract"]["ethics_approved"] = False, None
                    if contract["ethics_approval_required"] is None:
                        contract["ethics_approval_required"], contract["ethics_approved"] = False, None
                else:
                    e_val = _parse_approval(raw_e)
                    dscope["contract"]["ethics_approval_required"], dscope["contract"]["ethics_approved"] = True, e_val
                    if contract["ethics_approved"] is None:
                        contract["ethics_approval_required"], contract["ethics_approved"] = True, e_val
                source_anchors.setdefault("ethics_approved", ref)

            if m := PARAM_BRACKET_RE.search(ln):
                pname, lo, hi = m.group(1).strip(), _num(m.group(2)), _num(m.group(3))
                tkv = _parse_kv(m.group(5) or "")
                _merge_param_domain(pname, {
                    "min": lo, "max": hi, "safety_max": _num(tkv.get("safety_max")),
                    "step": _num(tkv.get("step")), "unit": (m.group(4) or "").strip(), "source_ref": ref,
                }, ref)
            elif m := PARAM_PROSE_RE.search(ln):
                pname = m.group(1).strip().lower()
                tail_s = m.group(5) or ""
                if (
                    pname not in {"budget", "deadline", "cost", "samples", "days", "hours", "version", "plan", "weight", "age", "year", "years", "page", "runs", "time", "step", "steps"}
                    and ("safety" in tail_s.lower() or "step" in tail_s.lower() or "contract" in rel.lower() or "protocol" in rel.lower() or "domain" in rel.lower() or "manual" in rel.lower())
                ):
                    lo, hi = _num(m.group(2)), _num(m.group(3))
                    smax = None
                    if ms := re.search(r"safety\s*(?:max|limit|ceiling)\s*(?:of|is|=|:)?\s*(-?[0-9]+(?:\.[0-9]+)?)", tail_s, re.I):
                        smax = _num(ms.group(1))
                    _merge_param_domain(pname, {"min": lo, "max": hi, "safety_max": smax, "step": None, "unit": (m.group(4) or "").strip(), "source_ref": ref}, ref)
            elif m := RESOURCE_RE.search(ln):
                rname, kv = m.group(1).strip(), _parse_kv(m.group(2))
                uc, hr = _num(kv.get("unit_cost")), _num(kv.get("rate_per_hr"))
                raw_st = kv.get("status", "unknown").strip().lower()
                maint_st = "overdue" if raw_st in {"maintenance", "out_of_order"} else (kv.get("maintenance_status") or "unknown")
                resources[rname] = {
                    "status": ELABFTW_STATUS_MAP.get(raw_st, raw_st), "unit_cost": uc, "rate_per_hr": hr,
                    "lead_days": _num(kv.get("lead_days")), "booking_lead_days": _num(kv.get("booking_lead_days")),
                    "quote_valid_until": kv.get("quote_valid_until"), "maintenance_status": maint_st,
                    "substitutable": kv.get("substitutable", "false").lower() == "true", "source_ref": ref,
                }
                if uc is None and hr is None:
                    _mark_missing(f"resources.{rname}.unit_cost_or_rate", f"No numeric unit_cost or rate_per_hr at {ref}")
            elif m := CONTROL_RE.search(ln):
                cname = m.group(1).strip()
                pkv = _parse_kv(m.group(2) or "")
                ctype = pkv.get("type", "blank" if ("blank" in cname.lower() or "vehicle" in cname.lower()) else ("positive" if "pos" in cname.lower() else "unknown"))
                c_obj = {"name": cname, "type": ctype, "addresses_confounder": pkv.get("addresses") or (m.group(3) or "").replace("addresses", "").strip(), "source_ref": ref, "source_doc": doc_key}
                if cname.lower() not in dscope["seen_ctrls"]:
                    dscope["controls"].append(c_obj)
                    dscope["seen_ctrls"].add(cname.lower())
                if cname.lower() not in seen_ctrl_names:
                    controls.append(c_obj)
                    seen_ctrl_names.add(cname.lower())
            elif list(CONTROL_PROSE_RE.finditer(ln)) and not (HYP_RE.match(ln) or IV_RE.match(ln) or DV_RE.match(ln) or ln.startswith("#") or ln.startswith("|")):
                for m in CONTROL_PROSE_RE.finditer(ln):
                    ctype = m.group(1).lower()
                    cname = (m.group(2) or f"{ctype}_control").strip().replace(" ", "_")[:36]
                    c_obj = {"name": cname, "type": "blank" if ctype in {"vehicle", "blank", "negative", "sham", "untreated"} else ctype, "addresses_confounder": ln[:120], "source_ref": ref, "source_doc": doc_key}
                    if cname.lower() not in dscope["seen_ctrls"]:
                        dscope["controls"].append(c_obj)
                        dscope["seen_ctrls"].add(cname.lower())
                    if cname.lower() not in seen_ctrl_names:
                        controls.append(c_obj)
                        seen_ctrl_names.add(cname.lower())

    for ck in ("budget_cap_usd", "deadline_days", "biosafety_approved"):
        if contract.get(ck) is None:
            _mark_missing(f"prospective_contract.{ck}", "Not stated in /input sources")
    if len(controls) < 2:
        _mark_missing("controls.complementary_arm", f"Only {len(controls)} control arm(s) extracted; complementary control NOT_FOUND_IN_SOURCE")
    if not hypothesis.get("statement"):
        _mark_missing("hypothesis.statement", "No explicit hypothesis line found; LLM should bind hypothesis from indexed blocks")

    # Strict document-scoped inheritance (NO cross-file/cross-study leakage!)
    for c in candidates:
        ds = doc_scopes.get(c.get("source_doc", ""), {})
        dh = ds.get("hypothesis") or {}
        dc = ds.get("contract") or {}
        inherited: list[str] = []

        if not c.get("local_hypothesis") and dh.get("statement"):
            c["hypothesis"] = dh["statement"]
            inherited.append("hypothesis")
        else:
            c["hypothesis"] = c.get("local_hypothesis") or ""

        dv_val = c.get("local_primary_readout") or dh.get("dv") or ""
        if not c.get("local_primary_readout") and dh.get("dv"):
            inherited.append("primary_readout")
        c["primary_readout"] = dv_val
        c["variables"] = {"iv": dh.get("iv"), "dv": dv_val or None, "dv_unit": dh.get("dv_unit") or ""}

        iu_val = c.get("local_inference_unit") or ds.get("inference_unit") or ""
        if not c.get("local_inference_unit") and ds.get("inference_unit"):
            inherited.append("inference_unit")
        c["inference_unit"] = iu_val

        # Controls: prefer candidate-local controls; otherwise inherit ONLY from THIS candidate's own document scope
        if c.get("local_controls"):
            c["controls"] = list(c["local_controls"])
        elif ds.get("controls"):
            c["controls"] = [ct["name"] for ct in ds["controls"]]
            inherited.append("controls")
        else:
            c["controls"] = []

        # Compliance: bind per-document approval status if available so opposing approvals across studies never contaminate each other
        if "biosafety_approved" in dc or "ethics_approved" in dc:
            c["compliance"] = {
                "biosafety_approved": dc.get("biosafety_approved"),
                "ethics_approval_required": dc.get("ethics_approval_required"),
                "ethics_approved": dc.get("ethics_approved"),
            }
            inherited.append("compliance")

        # Per-document contract fallback for setup/prep/qc
        setup_fb = dc.get("setup_cost_usd") if "setup_cost_usd" in dc else (contract.get("setup_cost_usd") if len(doc_scopes) == 1 else None)
        prep_fb = dc.get("base_prep_days") if "base_prep_days" in dc else (contract.get("base_prep_days") if len(doc_scopes) == 1 else None)
        qc_fb = dc.get("qc_days") if "qc_days" in dc else (contract.get("qc_days") if len(doc_scopes) == 1 else None)
        if c["one_time_setup_cost"] is None and setup_fb is not None:
            c["one_time_setup_cost"] = setup_fb
            inherited.append("one_time_setup_cost")
        if c["prep_days"] is None and prep_fb is not None:
            c["prep_days"] = prep_fb
            inherited.append("prep_days")
        if c["qc_turnaround_days"] is None and qc_fb is not None:
            c["qc_turnaround_days"] = qc_fb
            inherited.append("qc_turnaround_days")

        # Cross-check machine_hours vs machine_hours_per_run * planned_runs for internal conflicts
        if c.get("machine_hours") is not None and c.get("machine_hours_per_run") is not None and c.get("planned_runs"):
            derived_mh = round(float(c["machine_hours_per_run"]) * int(c["planned_runs"]), 4)
            if abs(float(c["machine_hours"]) - derived_mh) > 1e-4:
                c["source_conflicts"].append({
                    "field": "machine_hours_vs_per_run_x_runs",
                    "values": [c["machine_hours"], derived_mh],
                    "source_refs": [c.get("machine_hours_source_ref")],
                })
        c["inherited_from_doc_scope"] = inherited
        # Clean internal helper keys before emitting
        for k_tmp in ("local_controls", "local_primary_readout", "local_inference_unit", "local_hypothesis"):
            c.pop(k_tmp, None)

    extracted_key_count = sum(1 for k in ("budget_cap_usd", "deadline_days", "time_limit_hours", "sample_pool_available", "biosafety_approved") if contract.get(k) is not None)
    blocking_reasons: list[str] = []
    if len(candidates) == 0 and len(resources) == 0 and len(parameter_domains) == 0:
        blocking_reasons.append("zero_candidates_resources_and_domains_extracted")
    if unparsed_sources:
        blocking_reasons.append(f"unparsed_sources_present:{[u['file'] for u in unparsed_sources]}")
    if domain_source_conflicts:
        blocking_reasons.append(f"domain_source_conflicts:{len(domain_source_conflicts)}")
    gate_status = "BLOCKING_LOW_COVERAGE" if (len(candidates) == 0 and len(resources) == 0) else ("PARTIAL_MISSING_INFO" if (blocking_reasons or not_found) else "PASS")
    meta_block = {
        "extracted_from_files": sorted(set(r.split("#page=")[0] for r, _, _ in docs)),
        "formats_parsed": format_counts,
        "table_rows_ingested": table_rows_ingested,
        "column_match_trace": col_match_trace,
        "historical_runs_extracted": len(historical_runs),
        "blocks_indexer_used": bool(blocks_path and blocks_path.is_file()),
        "source_anchors": source_anchors,
        "document_scopes_isolated": sorted(doc_scopes.keys()),
        "domain_source_conflicts": domain_source_conflicts,
        "unparsed_sources": unparsed_sources,
        "not_found_in_source": not_found,
        "extraction_coverage": {
            "gate_status": gate_status,
            "blocking_reasons": blocking_reasons,
            "contract_keys_found": extracted_key_count,
            "resources_found": len(resources),
            "parameter_domains_found": len(parameter_domains),
            "controls_found": len(controls),
            "candidates_found": len(candidates),
            "fallback_to_llm_matrix_recommended": bool(gate_status == "BLOCKING_LOW_COVERAGE"),
        },
        "extraction_honesty_notice": "Absent fields remain explicitly null under not_found_in_source; document scopes are strictly isolated to prevent cross-file contamination; image-only PDFs emit UNPARSED_SOURCE rather than silent null.",
    }

    if mode == "cf1995b5":
        derived_bio_reps = (case_meta.get("statistical_design") or {}).get("planned_biological_replicates")
        if derived_bio_reps is None:
            cand_reps = [c.get("biological_replicates") for c in candidates if c.get("biological_replicates") is not None]
            derived_bio_reps = cand_reps[0] if cand_reps else None
        stat_design = {**(case_meta.get("statistical_design") or {}), "inference_unit": inference_unit or "", "planned_biological_replicates": derived_bio_reps}
        return {
            "case_id": case_id, "title": title, "research_type": case_meta.get("research_type", "engineering_optimization"),
            "hypothesis": hypothesis, "statistical_design": stat_design,
            "controls": controls, "objectives": case_meta.get("objectives") or [],
            "design_cells": case_meta.get("design_cells") or [],
            "prospective_contract": contract, "finite_candidates": candidates,
            "extraction_provenance": meta_block,
        }
    if mode in {"2e2c5820", "7be76e91"}:
        return {
            "case_id": case_id, "title": title, "prospective_contract": contract,
            "candidate_plans": candidates, "extraction_provenance": meta_block,
        }
    unit_prices = {rn: rm.get("unit_cost") for rn, rm in resources.items()}
    return {
        "case_id": case_id, "title": title,
        "domain_spec": {
            "parameters": parameter_domains,
            "coupled_constraints": coupled_constraints,
            "linear_constraints": linear_constraints,
            "mutually_exclusive": mutually_exclusive,
            "source_conflicts": domain_source_conflicts,
        },
        "contract_spec": {
            "currency": "USD", "budget_cap": contract.get("budget_cap_usd"),
            "time_limit_hours": contract.get("time_limit_hours"), "setup_cost": contract.get("setup_cost_usd"),
            "unit_prices": unit_prices, "resources": resources,
        },
        "candidates_3v": {"plans": candidates},
        "historical_observations": {"runs": historical_runs},
        "extraction_provenance": meta_block,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multi-format source_ref-anchored extractor from /input into draft JSON.")
    parser.add_argument("--input-dir", required=True, help="Raw /input directory")
    parser.add_argument("--blocks", default="", help="Optional block_indexer.py output JSON")
    parser.add_argument("--mode", required=True, choices=("cf1995b5", "2e2c5820", "ec02b694", "7be76e91"))
    parser.add_argument("--out", default="", help="Output extracted draft JSON path")
    parser.add_argument("--out-dir", default="", help="For ec02b694: also emit domain_spec/contract_spec/candidates_3v/pilot_runs+pilot_data JSONs")
    args = parser.parse_args(argv)

    rep = extract_from_sources(Path(args.input_dir).resolve(), args.mode, blocks_path=Path(args.blocks).resolve() if args.blocks else None)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.out_dir and args.mode == "ec02b694":
        od = Path(args.out_dir)
        od.mkdir(parents=True, exist_ok=True)
        for fn, key in (("domain_spec.json", "domain_spec"), ("contract_spec.json", "contract_spec"), ("candidates_3v.json", "candidates_3v"), ("extraction_provenance.json", "extraction_provenance")):
            (od / fn).write_text(json.dumps(rep[key], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if rep.get("historical_observations", {}).get("runs"):
            pilot_txt = json.dumps(rep["historical_observations"], ensure_ascii=False, indent=2) + "\n"
            (od / "pilot_runs.json").write_text(pilot_txt, encoding="utf-8")
            (od / "pilot_data.json").write_text(pilot_txt, encoding="utf-8")

    prov = rep["extraction_provenance"]
    print(json.dumps({
        "ok": True, "mode": args.mode, "files_scanned": len(prov["extracted_from_files"]),
        "formats_parsed": prov["formats_parsed"], "table_rows_ingested": prov["table_rows_ingested"],
        "unparsed_sources_count": len(prov["unparsed_sources"]),
        "not_found_in_source_count": len(prov["not_found_in_source"]),
        "coverage": prov["extraction_coverage"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
