"""Offline Results & Discussion Row-Condition Provenance Auditor (results-discussion-writing).

Pure Python 3.12 stdlib (zero third-party dependencies).
Solves critical failure modes missed by flat number-set checkers:
  1. Row-Condition Numeric Misattribution: binds each value in source CSV/JSON
     tables to its exact condition/group row label and catches values assigned
     to the wrong row (e.g., High-dose value written in a Low-dose sentence).
  2. Error-Bar Definition Fidelity: checks SD vs SEM vs IQR vs 95% CI alignment
     between source table columns/captions and manuscript prose.
  3. Null / Non-Significant Row Retention & Epistemic Calibration: identifies
     every row with p >= 0.05 / FDR >= 0.05, flags silent omission (cherry-picking),
     and blocks promoting non-significant or observational trends into causal or
     significant claims.
  4. Strict Results-vs-Discussion Layering: detects mechanistic speculation inside
     Results, verbatim numeric dumping inside Discussion, missing literature
     comparison / methodological-difference explanation, and missing concrete
     limitations (method/sample/measurement/statistical).
  5. Structured Output Contract Sealing: emits result.json preserving case_id,
     deliverable, preserved_facts, evidence_map, and change_log.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import writing_io_and_invariants as wio  # noqa: E402

RE_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
RE_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")

CONDITION_COL_HINTS = ("group", "condition", "arm", "cohort", "treatment", "dose", "subgroup", "model", "method", "sample", "condition_id", "组别", "条件")
PVAL_COL_HINTS = ("p_value", "pval", "p.value", "p_adj", "padj", "fdr", "q_value", "qval")
ERROR_TYPE_HINTS = {
    "SD": re.compile(r"(?:(?:^|_)sd(?:_|$)|\bsd\b|std_?dev|标准差)", re.I),
    "SEM": re.compile(r"(?:(?:^|_)sem(?:_|$)|\bsem\b|std_?err|标准误)", re.I),
    "IQR": re.compile(r"(?:(?:^|_)iqr(?:_|$)|\biqr\b|四分位距)", re.I),
    "CI95": re.compile(r"(?:95%?\s*ci|ci_?95|ci95|ci_?low|ci_?high|置信区间)", re.I),
}

RESULTS_INTERP_PATTERNS = [
    re.compile(r"\b(?:this\s+indicates?\s+a\s+causal\s+mechanism|likely\s+mediated\s+by|can\s+be\s+explained\s+by\s+previous\s+studies|consistent\s+with\s+prior\s+reports\s+showing\s+mechanism|proves?\s+that)\b", re.I),
    re.compile(r"(?:这(?:可能)?由.*?机制介导|说明其深层机制|证明了.*?因果|由此解释了前人研究)"),
]

SIGNIFICANT_CLAIM_PATTERNS = [
    re.compile(r"\b(?:statistically\s+significant(?:ly)?|significantly\s+(?:increased|decreased|higher|lower|improved|reduced)|proves?|causes?|demonstrates?\s+a\s+causal)\b", re.I),
    re.compile(r"(?:显著(?:升高|降低|增加|减少|改善|高于|低于)|具有统计学意义|证明了|直接导致)"),
]

NEGATED_SIG_PATTERNS = [
    re.compile(r"\b(?:not\s+statistically\s+significant|non-significant|did\s+not\s+reach\s+statistical\s+significance|no\s+significant|failed\s+to\s+reach\s+significance|trend(?:ed|ing)?\s+toward)\b", re.I),
    re.compile(r"(?:未达(?:到)?统计学显著|无显著(?:差异|变化)|不具有统计学意义|仅呈.*?趋势)"),
]

LIMITATION_CONCRETE_SIGNALS = [
    re.compile(r"\b(?:limitation|sample\s+size|underpowered|observational|cross-sectional|confound|measurement\s+error|selection\s+bias|generalizability|external\s+validation|assay\s+sensitivity|missing\s+data|multiple\s+testing)\b", re.I),
    re.compile(r"(?:局限|样本量|统计效力|观察性|横断面|混杂因素|测量误差|选择偏倚|外推性|外部验证|多重比较)"),
]

LIT_COMPARISON_SIGNALS = [
    re.compile(r"\b(?:consistent\s+with|in\s+contrast\s+to|unlike|differ(?:s|ed|ing)?\s+from|compared\s+with\s+prior|previous\s+stud(?:y|ies)|whereas\s+\w+\s+et\s+al\.|methodological\s+difference)\b", re.I),
    re.compile(r"(?:与既往研究|相比之下|不同于|方法(?:学)?差异|一致|存在分歧)"),
]


RE_PANEL_ID = re.compile(
    r"\b((?:Supplementary\s+)?(?:Fig(?:ure|\.)?|Table|Tbl\.?)\s*S?\d+[A-Za-z]?)\b",
    re.I,
)
RE_INTERVAL = re.compile(
    r"(?:95%?\s*CI|IQR)\s*[:=]?\s*[\[\(]\s*([+-]?\d+(?:\.\d+)?)\s*(?:to|,|–|-)\s*([+-]?\d+(?:\.\d+)?)\s*[\]\)]",
    re.I,
)
UNIT_SCALE_GROUPS: list[dict[str, float]] = [
    {"kg": 1e6, "g": 1e3, "mg": 1.0, "µg": 1e-3, "ug": 1e-3, "ng": 1e-6},
    {"l": 1e3, "ml": 1.0, "µl": 1e-3, "ul": 1e-3},
    {"h": 3600.0, "hr": 3600.0, "min": 60.0, "s": 1.0, "ms": 1e-3},
    {"m": 1e3, "cm": 10.0, "mm": 1.0, "µm": 1e-3, "um": 1e-3, "nm": 1e-6},
    {"v": 1e3, "mv": 1.0},
]


def _equivalent_unit_pairs(value: float, unit: str) -> list[tuple[str, str]]:
    """Return (normalized_value, target_unit) pairs for exact SI/metric unit conversions."""
    u = unit.lower().strip()
    out: list[tuple[str, str]] = []
    for grp in UNIT_SCALE_GROUPS:
        if u in grp:
            base = value * grp[u]
            for alt_u, scale in grp.items():
                if alt_u == u:
                    continue
                conv = base / scale
                if 1e-4 <= abs(conv) <= 1e7:
                    out.append((_norm_num(f"{conv:.6g}"), alt_u))
    return out


def _normalize_panel_id(raw: str) -> str:
    """Canonicalize 'Fig. 1A', 'Figure 1A', 'Supplementary Table S1' -> 'fig 1a' / 'table s1'."""
    s = re.sub(r"\s+", " ", raw.strip().lower())
    s = re.sub(r"^supplementary\s+", "", s)
    s = re.sub(r"^fig(?:ure|\.)?\s*", "fig ", s)
    s = re.sub(r"^(?:table|tbl\.?)\s*", "table ", s)
    return s.strip()


def _condition_aliases(cond: str) -> list[str]:
    """Generate strict primary and parenthetical-stripped aliases for matching long condition names."""
    aliases = [cond.strip()]
    base = re.sub(r"\s*\([^)]*\)\s*", " ", cond).strip()
    if base and base != cond.strip() and len(base) >= 4:
        aliases.append(base)
    return aliases


def _extract_markdown_tables(text: str) -> list[list[dict[str, str]]]:
    """Parse Markdown pipe tables inside .md/.xml/.txt sources into row dicts."""
    tables: list[list[dict[str, str]]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and "|" in lines[i].strip()[1:]:
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            parsed = []
            for bl in block:
                cells = [c.strip() for c in bl.strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                    continue
                parsed.append(cells)
            if len(parsed) >= 2:
                headers = parsed[0]
                rows = [
                    {headers[j]: (r[j] if j < len(r) else "") for j in range(len(headers))}
                    for r in parsed[1:]
                ]
                tables.append(rows)
        else:
            i += 1
    return tables


def _safe_float(s: Any) -> float | None:
    if s is None:
        return None
    try:
        cleaned = str(s).strip().replace(",", "").lstrip("<>≤≥= ")
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _parse_pval_cell(raw: Any) -> tuple[str, float] | None:
    """Parse p/FDR cell while PRESERVING comparison operator (<, <=, >, >=, =)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = re.match(r"^(?:[pPqQ]|FDR|padj)?\s*([<>≤≥=]+)?\s*([0-9]*\.?[0-9]+(?:[eE][+-]?\d+)?)$", s.replace(",", ""))
    if not m:
        return None
    op = (m.group(1) or "=").strip()
    try:
        val = float(m.group(2))
    except ValueError:
        return None
    return op, val


def _is_pval_nonsig(op: str, val: float) -> bool:
    """Strictly determine non-significance: 'p < 0.05' is SIGNIFICANT (False); 'p = 0.05' or 'p >= 0.05' is NON-SIGNIFICANT (True)."""
    if op == "<":
        # p < 0.05 is strictly less than 0.05 -> significant! Only e.g. p < 0.20 cannot be declared significant.
        return val > 0.05
    if op in ("<=", "≤"):
        return val > 0.05
    # '=', '>', '>=', '≥'
    return val >= 0.05


def _norm_num(s: Any) -> str:
    return wio._normalize_num_token("" if s is None else str(s).strip())


def load_structured_data(input_dir: Path, exclude_paths: set[Path] | None = None) -> dict[str, Any]:
    """Extract condition-row tuples, error bar conventions, null/nonsig rows, and raw numbers while isolating drafts."""
    excluded_resolved = {p.resolve() for p in (exclude_paths or set())}
    row_records: list[dict[str, Any]] = []
    all_numbers: dict[str, list[str]] = defaultdict(list)
    error_types_declared: set[str] = set()
    condition_to_values: dict[str, set[str]] = defaultdict(set)
    value_to_conditions: dict[str, set[str]] = defaultdict(set)
    nonsig_rows: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    excluded_files: list[str] = []
    raw_observation_tables: list[str] = []
    source_panels: set[str] = set()
    source_panels_norm: set[str] = set()
    source_panel_captions: list[dict[str, str]] = []
    unit_equivalent_pairs: dict[str, list[str]] = defaultdict(list)

    def _ingest_table_rows(rows: list[dict[str, Any]], rel: str) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        cond_col = None
        for c in cols:
            if any(h in c.lower() for h in CONDITION_COL_HINTS):
                cond_col = c
                break
        # Distinguish raw observation tables from categorical condition summary tables!
        # Never treat a numeric measurement column (e.g., cement_kg_m3) as a condition group label.
        if cond_col is None:
            first_vals = [("" if r.get(cols[0]) is None else str(r.get(cols[0])).strip()) for r in rows[:60]]
            non_empty = [v for v in first_vals if v != ""]
            numeric_ratio = sum(1 for v in non_empty if _safe_float(v) is not None) / max(1, len(non_empty))
            if numeric_ratio < 0.5 and len(set(non_empty)) <= 50 and len(rows) <= 100:
                cond_col = cols[0]
            else:
                raw_observation_tables.append(rel)

        for r_idx, row in enumerate(rows, start=2):
            cond_val = "" if (cond_col is None or row.get(cond_col) is None) else str(row.get(cond_col)).strip()
            row_nums: dict[str, str] = {}
            row_pvals: list[dict[str, Any]] = []
            for c, v in row.items():
                # Preserve exact 0 / 0.0 (never use `v or ""` which swallows zero!)
                vs = "" if v is None else str(v).strip()
                if vs == "":
                    continue
                for etype, pat in ERROR_TYPE_HINTS.items():
                    if pat.search(c):
                        error_types_declared.add(etype)
                if any(ph in c.lower() for ph in PVAL_COL_HINTS):
                    pv_parsed = _parse_pval_cell(vs)
                    if pv_parsed is not None:
                        op, pv = pv_parsed
                        row_pvals.append({"col": c, "op": op, "val": pv, "raw": vs, "is_nonsig": _is_pval_nonsig(op, pv)})
                fv = _safe_float(vs)
                if fv is not None:
                    nn = _norm_num(vs)
                    row_nums[c] = nn
                    loc = f"{rel}:row{r_idx}:{cond_val or 'obs'}:{c}"
                    if len(all_numbers[nn]) < 12 and loc not in all_numbers[nn]:
                        all_numbers[nn].append(loc)
                    if cond_col is not None and cond_val and c != cond_col and not any(ph in c.lower() for ph in ("id", "index", "year")):
                        condition_to_values[cond_val].add(nn)
                        value_to_conditions[nn].add(cond_val)
                else:
                    for tok_n in wio.extract_invariants(vs)["bare_numbers"]:
                        loc = f"{rel}:row{r_idx}:{cond_val or 'obs'}:{c}"
                        if len(all_numbers[tok_n]) < 12 and loc not in all_numbers[tok_n]:
                            all_numbers[tok_n].append(loc)
            if cond_col is not None and cond_val:
                is_nonsig = any(p["is_nonsig"] for p in row_pvals)
                rec = {
                    "source": f"{rel}:row{r_idx}",
                    "condition": cond_val,
                    "metrics": row_nums,
                    "pvalues": row_pvals,
                    "is_nonsig": is_nonsig,
                }
                row_records.append(rec)
                if is_nonsig:
                    nonsig_rows.append(rec)

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_dir).as_posix()
        # Strict draft isolation: never ingest draft_path (or adversarial draft files) into source ground truth!
        if path.resolve() in excluded_resolved or re.search(r"(?:^|/|_)draft(?:\.|_|$)", rel.lower()):
            excluded_files.append(rel)
            continue
        suf = path.suffix.lower()
        doc = wio.read_document_text(path)
        text = doc.get("text", "")
        source_files.append({"relpath": rel, "format": doc.get("format", suf.lstrip(".")), "ok": doc.get("ok", False)})
        if not doc.get("ok"):
            continue

        # Detect declared error-bar types and multi-panel/supplementary figure IDs
        for etype, pat in ERROR_TYPE_HINTS.items():
            if pat.search(text):
                error_types_declared.add(etype)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pm in RE_PANEL_ID.finditer(line):
                raw_pid = re.sub(r"\s+", " ", pm.group(1).strip())
                source_panels.add(raw_pid)
                norm_pid = _normalize_panel_id(raw_pid)
                source_panels_norm.add(norm_pid)
                m_par = re.match(r"^(fig|table)\s+(s?\d+)[a-z]$", norm_pid)
                if m_par:
                    source_panels_norm.add(f"{m_par.group(1)} {m_par.group(2)}")
                if re.match(r"^\s*(?:Supplementary\s+)?(?:Fig(?:ure|\.)?|Table|Tbl\.?)\s*S?\d+[A-Za-z]?[\s:.：\-]", line, re.I):
                    source_panel_captions.append({
                        "panel_id": raw_pid,
                        "normalized": norm_pid,
                        "source": f"{rel}:L{lineno}",
                        "caption": line.strip()[:220],
                    })

        if suf == ".csv":
            rows = list(csv.DictReader(text.splitlines()))
            _ingest_table_rows(rows, rel)
        elif suf == ".json":
            try:
                jobj = json.loads(text)
                jrows: list[dict[str, Any]] = []
                if isinstance(jobj, list) and jobj and all(isinstance(x, dict) for x in jobj):
                    jrows = jobj
                elif isinstance(jobj, dict):
                    for k_arr in ("rows", "data", "results", "records", "groups", "strata", "table"):
                        cand = jobj.get(k_arr)
                        if isinstance(cand, list) and cand and all(isinstance(x, dict) for x in cand):
                            jrows = cand
                            break
                if jrows:
                    _ingest_table_rows(jrows, rel)
            except json.JSONDecodeError:
                pass
            for lineno, line in enumerate(text.splitlines(), start=1):
                for n in wio.extract_invariants(line)["bare_numbers"]:
                    loc = f"{rel}:L{lineno}"
                    if len(all_numbers[n]) < 12 and loc not in all_numbers[n]:
                        all_numbers[n].append(loc)
        else:
            for tbl in _extract_markdown_tables(text):
                _ingest_table_rows(tbl, rel)
            for lineno, line in enumerate(text.splitlines(), start=1):
                inv = wio.extract_invariants(line)
                for n in inv["bare_numbers"]:
                    loc = f"{rel}:L{lineno}"
                    if len(all_numbers[n]) < 12 and loc not in all_numbers[n]:
                        all_numbers[n].append(loc)
                for nu in inv["numbers_with_units"]:
                    fv = _safe_float(nu["value"])
                    if fv is not None:
                        for eq_n, eq_u in _equivalent_unit_pairs(fv, nu["unit"]):
                            key = f"{eq_n}|{eq_u}"
                            loc = f"{rel}:L{lineno}:unit_conv_from_{nu['raw']}"
                            if loc not in unit_equivalent_pairs[key]:
                                unit_equivalent_pairs[key].append(loc)

    return {
        "source_files": source_files,
        "excluded_draft_files": excluded_files,
        "raw_observation_tables": raw_observation_tables,
        "row_records": row_records,
        "condition_to_values": {k: sorted(v) for k, v in condition_to_values.items()},
        "value_to_conditions": {k: sorted(v) for k, v in value_to_conditions.items()},
        "nonsig_rows": nonsig_rows,
        "error_types_declared": sorted(error_types_declared),
        "source_panels": sorted(source_panels),
        "source_panels_norm": sorted(source_panels_norm),
        "source_panel_captions": source_panel_captions[:40],
        "unit_equivalent_pairs": dict(unit_equivalent_pairs),
        "all_numbers": dict(all_numbers),
    }


def _split_rd_sections(md_text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]], list[tuple[int, str]]]:
    results_lines: list[tuple[int, str]] = []
    discussion_lines: list[tuple[int, str]] = []
    other_lines: list[tuple[int, str]] = []
    mode = "other"
    mode_level = 0
    for lineno, line in enumerate(md_text.splitlines(), start=1):
        hm = RE_HEADING.match(line)
        if hm:
            lvl = len(hm.group(1))
            title = hm.group(2).strip().lower()
            if ("results" in title or "结果" in title) and not ("discussion" in title or "讨论" in title):
                mode = "results"
                mode_level = lvl
            elif "discussion" in title or "讨论" in title or "limitation" in title or "局限" in title or "conclusion" in title or "结论" in title:
                mode = "discussion"
                mode_level = lvl
            elif mode in ("results", "discussion") and lvl > mode_level:
                # Subheadings inside Results or Discussion inherit the parent section!
                pass
            else:
                mode = "other"
                mode_level = lvl
            continue
        if mode == "results":
            results_lines.append((lineno, line))
        elif mode == "discussion":
            discussion_lines.append((lineno, line))
        else:
            other_lines.append((lineno, line))
    return results_lines, discussion_lines, other_lines


def audit_results_and_discussion(draft_path: Path, input_dir: Path) -> dict[str, Any]:
    """Run deep row-condition, null-retention, error-bar, layering, and epistemic audit with strict draft isolation."""
    data = load_structured_data(input_dir, exclude_paths={draft_path})
    if not data["source_files"]:
        return {"ok": False, "error": f"no readable source data files found under {input_dir}"}
    doc = wio.read_document_text(draft_path)
    if not doc.get("ok"):
        return {"ok": False, "error": f"cannot read draft: {draft_path.name}"}

    full_text = doc["text"]
    res_lines, disc_lines, _ = _split_rd_sections(full_text)
    res_text = "\n".join(t for _, t in res_lines)
    disc_text = "\n".join(t for _, t in disc_lines)

    issues: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []

    if not res_text.strip():
        issues.append({"code": "MISSING_RESULTS_SECTION", "severity": "high", "detail": "No dedicated Results section heading found."})
    if not disc_text.strip():
        issues.append({"code": "MISSING_DISCUSSION_SECTION", "severity": "high", "detail": "No dedicated Discussion section heading found."})

    # 1. Numeric grounding, strict paired unit conversion, dangling panel anchor & row-condition binding audit
    all_nums = data["all_numbers"]
    unit_eq_pairs = data.get("unit_equivalent_pairs", {})
    source_panels_norm = set(data.get("source_panels_norm", []))
    cond_to_vals = {k: set(v) for k, v in data["condition_to_values"].items()}
    val_to_conds = {k: set(v) for k, v in data["value_to_conditions"].items()}
    conditions = sorted(cond_to_vals.keys(), key=len, reverse=True)
    cond_alias_map = {c: _condition_aliases(c) for c in conditions}
    nonsig_cond_names = {r["condition"] for r in data["nonsig_rows"]}

    grounded_count = 0
    unit_converted_grounded = 0
    ungrounded_numbers: list[dict[str, Any]] = []
    row_mismatches: list[dict[str, Any]] = []
    nonsig_overclaims: list[dict[str, Any]] = []
    dangling_anchors: list[dict[str, Any]] = []
    seen_dangling_anchors: set[str] = set()

    for section_name, lines in (("Results", res_lines), ("Discussion", disc_lines)):
        for lineno, line in lines:
            if not line.strip():
                continue
            # Check every Figure/Table/Supplementary panel anchor against /input source_panels_norm
            for pm in RE_PANEL_ID.finditer(line):
                raw_pid = re.sub(r"\s+", " ", pm.group(1).strip())
                norm_pid = _normalize_panel_id(raw_pid)
                if norm_pid not in source_panels_norm and norm_pid not in seen_dangling_anchors:
                    seen_dangling_anchors.add(norm_pid)
                    dangling_anchors.append({
                        "code": "DANGLING_FIGURE_OR_TABLE_ANCHOR",
                        "severity": "high",
                        "section": section_name,
                        "line": lineno,
                        "cited_anchor": raw_pid,
                        "normalized_anchor": norm_pid,
                        "available_source_panels": sorted(source_panels_norm)[:20],
                        "detail": f"Draft cites '{raw_pid}' which is absent from /input captions, tables, and text.",
                    })

            for im in RE_INTERVAL.finditer(line):
                lo, hi = _safe_float(im.group(1)), _safe_float(im.group(2))
                if lo is not None and hi is not None and lo > hi:
                    issues.append({
                        "code": "INTERVAL_ESTIMATE_INCONSISTENT",
                        "severity": "high",
                        "section": section_name,
                        "line": lineno,
                        "matched": im.group(0),
                        "detail": f"Interval lower bound ({lo}) exceeds upper bound ({hi}).",
                    })
            for sent in RE_SENT_SPLIT.split(line):
                sent = sent.strip()
                if not sent:
                    continue
                inv = wio.extract_invariants(sent)
                # Build exact (num -> set of paired units) map in this sentence for strict unit conversion check
                sent_num_units: dict[str, set[str]] = defaultdict(set)
                for nu in inv["numbers_with_units"]:
                    sent_num_units[nu["value"]].add(nu["unit"].lower().strip())

                # Detect which conditions are named in this sentence (supporting exact & parenthetical-stripped alias)
                matched_conds: list[str] = []
                for c in conditions:
                    if any(re.search(rf"(?<!\w){re.escape(al)}(?!\w)", sent, re.I) for al in cond_alias_map[c]):
                        matched_conds.append(c)

                for num in inv["bare_numbers"]:
                    if num in {"0", "1", "2", "3", "4", "5", "95"} and not any(u["value"] == num for u in inv["numbers_with_units"]):
                        continue
                    if num in all_nums:
                        grounded_count += 1
                        evidence_map.append({
                            "section": section_name,
                            "line": lineno,
                            "value": num,
                            "matched_conditions": matched_conds,
                            "source_anchors": all_nums[num][:3],
                        })
                        if len(matched_conds) == 1 and num in val_to_conds:
                            named_c = matched_conds[0]
                            owner_conds = val_to_conds[num]
                            if named_c not in owner_conds:
                                row_mismatches.append({
                                    "code": "ROW_CONDITION_VALUE_MISMATCH",
                                    "severity": "high",
                                    "section": section_name,
                                    "line": lineno,
                                    "named_condition": named_c,
                                    "misattributed_value": num,
                                    "actual_owner_conditions": sorted(owner_conds),
                                    "sentence": sent[:200],
                                })
                    else:
                        # Strict unit conversion check: BOTH converted value AND matching target unit must be explicitly present!
                        matched_unit_anchors: list[str] = []
                        for u_tok in sent_num_units.get(num, set()):
                            pk = f"{num}|{u_tok}"
                            if pk in unit_eq_pairs:
                                matched_unit_anchors.extend(unit_eq_pairs[pk][:3])
                        if matched_unit_anchors:
                            grounded_count += 1
                            unit_converted_grounded += 1
                            evidence_map.append({
                                "section": section_name,
                                "line": lineno,
                                "value": num,
                                "matched_conditions": matched_conds,
                                "source_anchors": matched_unit_anchors[:3],
                                "unit_converted": True,
                            })
                        else:
                            ungrounded_numbers.append({
                                "section": section_name,
                                "line": lineno,
                                "value": num,
                                "sentence": sent[:180],
                            })

                # Non-significant row overclaim check
                if len(matched_conds) == 1 and matched_conds[0] in nonsig_cond_names:
                    has_sig_claim = any(p.search(sent) for p in SIGNIFICANT_CLAIM_PATTERNS)
                    has_neg_hedge = any(p.search(sent) for p in NEGATED_SIG_PATTERNS)
                    if has_sig_claim and not has_neg_hedge:
                        nonsig_overclaims.append({
                            "code": "NONSIG_PROMOTED_TO_SIGNIFICANT_OR_CAUSAL",
                            "severity": "high",
                            "section": section_name,
                            "line": lineno,
                            "condition": matched_conds[0],
                            "sentence": sent[:200],
                        })

    issues.extend(dangling_anchors)
    issues.extend(row_mismatches)
    issues.extend(nonsig_overclaims)
    for ug in ungrounded_numbers:
        issues.append({"code": "UNGROUNDED_VALUE", "severity": "high", **ug})

    # 2. Check omitted negative / non-significant rows AND full condition-row retention coverage
    omitted_nonsig: list[dict[str, Any]] = []
    for nr in data["nonsig_rows"]:
        cname = nr["condition"]
        p_vals_str = [_norm_num(str(p["val"])) for p in nr["pvalues"]]
        mentioned_cond = any(re.search(rf"(?<!\w){re.escape(al)}(?!\w)", full_text, re.I) for al in _condition_aliases(cname))
        mentioned_p = any(pv in full_text for pv in p_vals_str)
        if not mentioned_cond and not mentioned_p:
            omitted_nonsig.append({"condition": cname, "source": nr["source"], "pvalues": nr["pvalues"]})
            issues.append({
                "code": "OMITTED_NULL_OR_NONSIG_ROW",
                "severity": "high",
                "condition": cname,
                "source": nr["source"],
                "detail": f"Non-significant row '{cname}' ({nr['pvalues']}) from {nr['source']} is omitted from Results/Discussion.",
            })

    retained_conditions = [
        c for c in conditions
        if any(re.search(rf"(?<!\w){re.escape(al)}(?!\w)", full_text, re.I) for al in cond_alias_map[c])
    ]
    omitted_conditions = [c for c in conditions if c not in retained_conditions]
    for oc in omitted_conditions:
        if oc not in nonsig_cond_names:
            issues.append({
                "code": "OMITTED_CONDITION_ROW",
                "severity": "medium",
                "condition": oc,
                "detail": f"Condition row '{oc}' from /input was not reported in Results/Discussion.",
            })

    # 3. Error-bar definition conflict check with non-destructive quarantine awareness
    draft_err_types = {k for k, pat in ERROR_TYPE_HINTS.items() if pat.search(full_text)}
    src_err_types = set(data["error_types_declared"])
    if src_err_types and draft_err_types:
        conflicting_err = sorted(draft_err_types - src_err_types)
        if conflicting_err:
            quarantined = bool(re.search(r"\[EVIDENCE_GAP:[^\]]*(?:error|sd|sem|iqr|ci)|unverified\s+in\s+source", full_text, re.I))
            issues.append({
                "code": "ERROR_BAR_DEFINITION_CONFLICT",
                "severity": "medium" if quarantined else "high",
                "quarantined_with_gap_tag": quarantined,
                "source_declared": sorted(src_err_types),
                "draft_used": sorted(draft_err_types),
                "detail": (
                    f"Draft mentions error metric(s) {conflicting_err} not declared in source ({sorted(src_err_types)}); "
                    + ("retained under explicit [EVIDENCE_GAP] quarantine pending author confirmation." if quarantined else "verify error-bar definition or tag with explicit [EVIDENCE_GAP].")
                ),
            })

    # 4. Results-vs-Discussion layering & synthesis quality
    for lineno, line in res_lines:
        for pat in RESULTS_INTERP_PATTERNS:
            m = pat.search(line)
            if m:
                issues.append({
                    "code": "MECHANISM_INTERPRETATION_IN_RESULTS",
                    "severity": "high",
                    "line": lineno,
                    "matched": m.group(0),
                    "detail": "Results must report empirical observations only; move mechanistic or literature interpretation to Discussion.",
                })

    res_nums = {n for n in wio.extract_invariants(res_text)["bare_numbers"] if n not in {"0", "1", "2", "3", "95"}}
    disc_nums = {n for n in wio.extract_invariants(disc_text)["bare_numbers"] if n not in {"0", "1", "2", "3", "95"}}
    if len(res_nums) >= 4 and len(res_nums & disc_nums) / len(res_nums) >= 0.75:
        issues.append({
            "code": "DISCUSSION_VERBATIM_NUMERIC_REPETITION",
            "severity": "medium",
            "overlap_ratio": round(len(res_nums & disc_nums) / len(res_nums), 3),
            "detail": "Discussion repeats >=75% of Results numeric tokens; focus on interpretation, literature comparison, and concrete limitations.",
        })

    if disc_text.strip():
        if not any(p.search(disc_text) for p in LIMITATION_CONCRETE_SIGNALS):
            issues.append({
                "code": "MISSING_CONCRETE_LIMITATIONS_IN_DISCUSSION",
                "severity": "high",
                "detail": "Discussion lacks concrete methodological, sample-size, measurement, or statistical limitations.",
            })
        if not any(p.search(disc_text) for p in LIT_COMPARISON_SIGNALS):
            issues.append({
                "code": "MISSING_LITERATURE_COMPARISON_IN_DISCUSSION",
                "severity": "medium",
                "detail": "Discussion does not explicitly compare findings (agreement/discrepancy/methodological differences) with prior literature.",
            })

    prose_audit = wio.audit_academic_prose(full_text)
    high_issues = [x for x in issues if x.get("severity") == "high"]

    return {
        "ok": len(high_issues) == 0 and prose_audit["high_count"] == 0,
        "high_issue_count": len(high_issues) + prose_audit["high_count"],
        "medium_issue_count": sum(1 for x in issues if x.get("severity") == "medium") + prose_audit["warning_count"],
        "grounded_numbers_count": grounded_count,
        "unit_converted_grounded_count": unit_converted_grounded,
        "ungrounded_numbers_count": len(ungrounded_numbers),
        "dangling_anchors_count": len(dangling_anchors),
        "row_condition_mismatches_count": len(row_mismatches),
        "omitted_nonsig_rows_count": len(omitted_nonsig),
        "nonsig_overclaims_count": len(nonsig_overclaims),
        "condition_rows_retained": len(retained_conditions),
        "total_condition_rows": len(conditions),
        "source_panels_indexed": data.get("source_panels", []),
        "source_error_bar_types": sorted(src_err_types),
        "draft_error_bar_types": sorted(draft_err_types),
        "issues": issues,
        "prose_deai_audit": prose_audit,
        "evidence_map": evidence_map[:160],
        "source_files": data["source_files"],
    }


def build_rd_scaffold(data_dir: Path, out_md: Path) -> dict[str, Any]:
    """Generate a row-bound Results & four-beat Discussion scaffold directly from structured /input data."""
    data = load_structured_data(data_dir)
    err_str = ", ".join(data["error_types_declared"]) if data["error_types_declared"] else "UNSPECIFIED"
    lines = [
        "# Results",
        "",
        f"<!-- Observations only (zero mechanistic speculation). Declared error-bar types in /input: {err_str}. -->",
        "",
    ]
    for rec in data["row_records"]:
        metrics_str = ", ".join(f"{k}={v}" for k, v in rec["metrics"].items())
        tag = "[NON-SIGNIFICANT / MUST RETAIN AS HEDGED TREND]" if rec["is_nonsig"] else "[OBSERVED ROW]"
        lines.append(f"- {tag} Condition `{rec['condition']}` (`{rec['source']}`): {metrics_str}.")
    if not data["row_records"]:
        lines.append("[EVIDENCE_GAP: No structured CSV tables found under /input; transcribe verified text/figure values explicitly]")
    lines.extend([
        "",
        "# Discussion",
        "",
        "## Key Finding Synthesis",
        "[Synthesize how observed outcomes answer the primary research question without copying every Results number verbatim.]",
        "",
        "## Comparison with Prior Literature",
        "[Compare agreement and discrepancies against supplied references, explicitly contrasting study design, population, assay, or statistical methods.]",
        "",
        "## Mechanistic Interpretation (Explicitly Hedged)",
        "[Present candidate mechanisms clearly qualified with 'may reflect' / 'is consistent with'; never upgrade observational or non-significant trends to causality.]",
        "",
        "## Methodological, Sample, Measurement, and Statistical Limitations",
        "[State concrete limitations across design, sample size, measurement precision, and multiple testing.]",
    ])
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "scaffold_path": str(out_md),
        "rows_emitted": len(data["row_records"]),
        "nonsig_rows_flagged": len(data["nonsig_rows"]),
        "error_types_declared": data["error_types_declared"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Results & Discussion Row-Condition Provenance Auditor.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_idx = sub.add_parser("index", help="Inspect source tables, condition rows, error types, and non-significant rows.")
    p_idx.add_argument("--data-dir", required=True)
    p_idx.add_argument("--out-json", required=True)

    p_scf = sub.add_parser("scaffold", help="Generate row-bound Results & four-beat Discussion scaffold from /input.")
    p_scf.add_argument("--data-dir", required=True)
    p_scf.add_argument("--out-md", required=True)

    p_aud = sub.add_parser("audit", help="Audit Results & Discussion draft against source tables and captions.")
    p_aud.add_argument("--data-dir", required=True)
    p_aud.add_argument("--draft", required=True)
    p_aud.add_argument("--out-json", required=True)

    p_seal = sub.add_parser("seal", help="Audit draft and emit output-contract-compliant result.json.")
    p_seal.add_argument("--data-dir", required=True)
    p_seal.add_argument("--draft", required=True)
    p_seal.add_argument("--case-id", required=True)
    p_seal.add_argument("--out-dir", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "index":
        data_dir = Path(args.data_dir)
        if not data_dir.is_dir():
            err_payload = {"ok": False, "error": f"data_dir not found: {data_dir}"}
            print(json.dumps(err_payload, ensure_ascii=False))
            return 1
        data = load_structured_data(data_dir)
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        has_sources = len(data["source_files"]) > 0
        data["ok"] = has_sources
        if not has_sources:
            data["error"] = "empty_data_dir: no source data files found"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": has_sources,
            "source_files": len(data["source_files"]),
            "row_records": len(data["row_records"]),
            "nonsig_rows": len(data["nonsig_rows"]),
            "error_types_declared": data["error_types_declared"],
            **({"error": data["error"]} if not has_sources else {}),
        }, ensure_ascii=False))
        return 0 if has_sources else 1

    if args.cmd == "scaffold":
        res = build_rd_scaffold(Path(args.data_dir), Path(args.out_md))
        print(json.dumps(res, ensure_ascii=False))
        return 0

    if args.cmd == "audit":
        res = audit_results_and_discussion(Path(args.draft), Path(args.data_dir))
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": res.get("ok", False),
            "high_issue_count": res.get("high_issue_count", -1),
            "dangling_anchors": res.get("dangling_anchors_count", 0),
            "row_mismatches": res.get("row_condition_mismatches_count", 0),
            "omitted_nonsig": res.get("omitted_nonsig_rows_count", 0),
            "nonsig_overclaims": res.get("nonsig_overclaims_count", 0),
            "condition_rows_retained": f"{res.get('condition_rows_retained', 0)}/{res.get('total_condition_rows', 0)}",
            **({"error": res["error"]} if "error" in res else {}),
        }, ensure_ascii=False))
        return 0

    if args.cmd == "seal":
        draft_path = Path(args.draft)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        res = audit_results_and_discussion(draft_path, Path(args.data_dir))
        aud_path = out_dir / "rd_audit_report.json"
        aud_path.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if "error" in res and "grounded_numbers_count" not in res:
            err_result = wio.build_writing_result(
                case_id=args.case_id,
                task_id="results-discussion-writing",
                status="error",
                deliverable={"results_and_discussion_markdown": draft_path.name, "audit_report": aud_path.name},
                preserved_facts={},
                evidence_map=[],
                change_log=[{"action": "audit_failed", "error": res["error"]}],
                artifacts=[aud_path.name],
                sources=[],
                gaps=[{"type": "audit_input_error", "detail": res["error"]}],
                limitations=["Seal aborted cleanly due to unreadable draft or empty source directory."],
                verification={"audit_ok": False, "error": res["error"]},
                case_contract=wio.load_case_contract(Path(args.data_dir)),
            )
            res_path = out_dir / "result.json"
            res_path.write_text(json.dumps(err_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"ok": False, "audit_ok": False, "error": res["error"], "result_json": str(res_path)}, ensure_ascii=False))
            return 1
        ev_path = out_dir / "rd_evidence_map.json"
        ev_path.write_text(json.dumps(res.get("evidence_map", []), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        gaps = [
            {"type": iss["code"], "detail": iss.get("detail") or iss.get("sentence", "")}
            for iss in res.get("issues", [])
            if iss.get("severity") == "high" or iss.get("quarantined_with_gap_tag")
        ]
        case_contract = wio.load_case_contract(Path(args.data_dir))
        payload = wio.build_writing_result(
            case_id=args.case_id,
            task_id="results-discussion-writing",
            deliverable={
                "results_and_discussion_markdown": draft_path.name,
                "audit_report": aud_path.name,
                "evidence_map_file": ev_path.name,
            },
            preserved_facts={
                "grounded_numbers_count": res["grounded_numbers_count"],
                "unit_converted_grounded_count": res["unit_converted_grounded_count"],
                "ungrounded_numbers_count": res["ungrounded_numbers_count"],
                "dangling_anchors_count": res["dangling_anchors_count"],
                "row_condition_mismatches_count": res["row_condition_mismatches_count"],
                "omitted_nonsig_rows_count": res["omitted_nonsig_rows_count"],
                "condition_rows_retained": res["condition_rows_retained"],
                "total_condition_rows": res["total_condition_rows"],
                "source_error_bar_types": res["source_error_bar_types"],
                "draft_error_bar_types": res["draft_error_bar_types"],
            },
            evidence_map=res.get("evidence_map", []),
            change_log=[
                {"action": "row_condition_and_layer_audit", "high_issues": res["high_issue_count"], "medium_issues": res["medium_issue_count"], "issues": res["issues"]}
            ],
            artifacts=[draft_path.name, aud_path.name, ev_path.name],
            sources=res.get("source_files", []),
            gaps=gaps,
            limitations=[
                "Row-condition cross-check operates on structured CSV/JSON/Markdown/XML/PDF tables and text inputs supplied under /input; raster-only charts without accompanying numeric tables require explicit manual reading notes.",
            ],
            verification={"audit_ok": res["ok"], "high_issue_count": res["high_issue_count"]},
            case_contract=case_contract,
        )
        res_path = out_dir / "result.json"
        res_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "audit_ok": res["ok"], "result_json": str(res_path)}, ensure_ascii=False))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
