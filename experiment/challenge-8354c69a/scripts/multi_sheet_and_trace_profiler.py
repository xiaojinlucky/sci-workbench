#!/usr/bin/env python3
"""Dual-backend multi-sheet XLSX + batch curve/trace mechanics + MATLAB parameter & ambiguity profiler."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

MISSING_VALS = {"", "na", "n/a", "nan", "null", "none", "?", "-", "--"}
BELOW_LOD_RE = re.compile(r"^(?:<\s*(?:lod|lloq|loq|\d+(?:\.\d+)?)|blq|nd|bql|not\s+detected)$", re.I)
MATLAB_ASSIGN_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*([^;\n%]+)(?:;)?\s*(?:%\s*(.*))?$")
UNIT_RE = re.compile(
    r"\b(MPa|GPa|kPa|Pa|kN|N/mm2|N/mm\^2|mm|um|nm|cm|mg/mL|ug/mL|ng/mL|ug/L|mmol/L|uM|mM|%|rpm|Hz|kHz|ms|min|hr|hrs|sec|degC)\b"
)
THRESH_RE = re.compile(r"(?:threshold|cutoff|limit|lod|lloq|rsd|cv|z[-_ ]?score|iqr)\s*[:=<>\s]+\s*([0-9]+(?:\.[0-9]+)?%?)", re.I)


def _to_float(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    s = str(v).strip()
    if s.lower() in MISSING_VALS:
        return None
    try:
        f = float(s)
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def _summarize_series(vals: list[float]) -> dict[str, Any]:
    s = sorted(vals)
    n = len(s)
    med = statistics.median(s)
    q1 = statistics.median(s[: n // 2]) if n >= 4 else s[0]
    q3 = statistics.median(s[(n + 1) // 2 :]) if n >= 4 else s[-1]
    return {
        "n": n,
        "min": round(s[0], 6),
        "max": round(s[-1], 6),
        "mean": round(statistics.mean(s), 6),
        "std": round(statistics.stdev(s), 6) if n >= 2 else 0.0,
        "median": round(med, 6),
        "iqr": round(q3 - q1, 6),
    }


def _col_letters_to_idx(ref: str) -> int:
    col = "".join(ch for ch in ref if ch.isalpha()).upper()
    idx = 0
    for ch in col:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(0, idx - 1)


def _read_xlsx_stdlib(xlsx_path: Path) -> list[tuple[str, list[list[Any]]]]:
    """Pure-stdlib XLSX reader fallback via zipfile + XML (works without openpyxl)."""
    sheets_data: list[tuple[str, list[list[Any]]]] = []
    ns_main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    ns_rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

    with zipfile.ZipFile(str(xlsx_path), "r") as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            sst_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sst_root.findall(f"{ns_main}si"):
                parts = [t.text or "" for t in si.iter(f"{ns_main}t")]
                shared.append("".join(parts))

        rels: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in zf.namelist():
            rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            for rel in rel_root:
                rid = rel.attrib.get("Id", "")
                target = rel.attrib.get("Target", "")
                if rid and target:
                    rels[rid] = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"

        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        sheet_specs: list[tuple[str, str]] = []
        for idx, sh in enumerate(wb_root.findall(f".//{ns_main}sheet"), start=1):
            sname = sh.attrib.get("name", f"Sheet{idx}")
            rid = sh.attrib.get(f"{ns_rel}id", "")
            target = rels.get(rid, f"xl/worksheets/sheet{idx}.xml")
            sheet_specs.append((sname, target))

        for sname, target in sheet_specs:
            if target not in zf.namelist():
                continue
            s_root = ET.fromstring(zf.read(target))
            rows_out: list[list[Any]] = []
            for row_el in s_root.findall(f".//{ns_main}sheetData/{ns_main}row"):
                row_map: dict[int, Any] = {}
                max_c = -1
                for c_el in row_el.findall(f"{ns_main}c"):
                    ref = c_el.attrib.get("r", "")
                    ci = _col_letters_to_idx(ref) if ref else (max_c + 1)
                    max_c = max(max_c, ci)
                    ctype = c_el.attrib.get("t", "")
                    if ctype == "inlineStr":
                        val = "".join(t.text or "" for t in c_el.iter(f"{ns_main}t"))
                    else:
                        v_el = c_el.find(f"{ns_main}v")
                        raw = v_el.text if v_el is not None else None
                        if raw is None:
                            val = None
                        elif ctype == "s":
                            s_idx = int(raw)
                            val = shared[s_idx] if 0 <= s_idx < len(shared) else raw
                        elif ctype == "b":
                            val = raw == "1"
                        elif ctype == "str":
                            val = raw
                        else:
                            fv = _to_float(raw)
                            val = fv if fv is not None else raw
                    row_map[ci] = val
                if max_c >= 0:
                    rows_out.append([row_map.get(i) for i in range(max_c + 1)])
            sheets_data.append((sname, rows_out))
    return sheets_data


def _read_xlsx_sheets(xlsx_path: Path) -> tuple[str, list[tuple[str, list[list[Any]]]]]:
    try:
        from openpyxl import load_workbook  # type: ignore

        wb = load_workbook(str(xlsx_path), read_only=True, data_only=True)
        out: list[tuple[str, list[list[Any]]]] = []
        try:
            for sname in wb.sheetnames:
                ws = wb[sname]
                out.append((sname, [list(r) for r in ws.iter_rows(values_only=True)]))
        finally:
            wb.close()
        return "openpyxl", out
    except Exception:
        return "stdlib_xml", _read_xlsx_stdlib(xlsx_path)


def _detect_header_row_idx(non_empty: list[list[Any]]) -> int:
    """Detect tabular header row index (skipping single-cell banner/metadata preambles)."""
    if len(non_empty) <= 1:
        return 0
    best_idx = 0
    best_score = -1.0
    for idx in range(min(10, len(non_empty))):
        row = non_empty[idx]
        non_blank = [c for c in row if c is not None and str(c).strip() != ""]
        if len(non_blank) < 2:
            continue
        str_cnt = sum(1 for c in non_blank if _to_float(c) is None)
        # Prefer rows with mostly non-numeric header strings followed by numeric data in next row
        next_num = 0
        if idx + 1 < len(non_empty):
            next_num = sum(1 for c in non_empty[idx + 1] if _to_float(c) is not None)
        score = str_cnt * 2.0 + min(next_num, len(non_blank)) * 1.5 - idx * 0.25
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx


def profile_xlsx_workbook(xlsx_path: Path) -> dict[str, Any]:
    backend, raw_sheets = _read_xlsx_sheets(xlsx_path)
    sheets_out: list[dict[str, Any]] = []
    units_found: set[str] = set()
    thresholds_found: list[str] = []

    for sname, rows in raw_sheets:
        non_empty = [r for r in rows if any(c is not None and str(c).strip() != "" for c in r)]
        hdr_idx = _detect_header_row_idx(non_empty)
        preamble = [
            " | ".join(str(c).strip() for c in r if c is not None and str(c).strip() != "")
            for r in non_empty[:hdr_idx]
        ]
        headers = (
            [str(c).strip() if (c is not None and str(c).strip()) else f"col_{i}" for i, c in enumerate(non_empty[hdr_idx], start=1)]
            if non_empty
            else []
        )
        data_rows = non_empty[hdr_idx + 1 :] if len(non_empty) > hdr_idx + 1 else []

        for txt in [sname] + preamble + headers:
            units_found.update(UNIT_RE.findall(txt))
            for m in THRESH_RE.finditer(txt):
                thresholds_found.append(f"{xlsx_path.name}:{sname} -> {m.group(0).strip()}")

        col_nums: dict[int, list[float]] = {i: [] for i in range(len(headers))}
        zero_cnt: dict[int, int] = {i: 0 for i in range(len(headers))}
        missing_cnt: dict[int, int] = {i: 0 for i in range(len(headers))}
        blq_cnt: dict[int, int] = {i: 0 for i in range(len(headers))}
        non_num_cnt: dict[int, int] = {i: 0 for i in range(len(headers))}

        for r in data_rows:
            for ci in range(len(headers)):
                cell = r[ci] if ci < len(r) else None
                f = _to_float(cell)
                if f is not None:
                    col_nums[ci].append(f)
                    if abs(f) < 1e-12:
                        zero_cnt[ci] += 1
                else:
                    s = "" if cell is None else str(cell).strip()
                    if not s or s.lower() in MISSING_VALS:
                        missing_cnt[ci] += 1
                    elif BELOW_LOD_RE.match(s):
                        blq_cnt[ci] += 1
                    else:
                        non_num_cnt[ci] += 1

        col_summaries = []
        for ci, cname in enumerate(headers):
            entry: dict[str, Any] = {
                "column": cname,
                "numeric_count": len(col_nums[ci]),
                "explicit_zero_count": zero_cnt[ci],
                "missing_count": missing_cnt[ci],
                "below_lod_count": blq_cnt[ci],
                "non_numeric_text_count": non_num_cnt[ci],
            }
            if col_nums[ci]:
                entry["stats"] = _summarize_series(col_nums[ci])
            col_summaries.append(entry)

        sheets_out.append({
            "sheet_name": sname,
            "header_row_detected_idx": hdr_idx,
            "preamble_lines": preamble[:6],
            "row_count": len(data_rows),
            "column_count": len(headers),
            "headers": headers,
            "columns": col_summaries,
        })

    return {
        "file": xlsx_path.name,
        "reader_backend": backend,
        "sheet_count": len(sheets_out),
        "units_detected": sorted(units_found),
        "thresholds_detected": thresholds_found[:20],
        "sheets": sheets_out,
    }


def _infer_prefix(filename: str) -> str:
    stem = Path(filename).stem
    m = re.match(r"^([A-Za-z0-9]+(?:[-_][A-Za-z]+)?)", stem)
    return m.group(1) if m else "ALL"


def profile_trace_file(fp: Path) -> dict[str, Any]:
    raw_lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
    comment_lines = [ln.strip() for ln in raw_lines[:10] if ln.strip().startswith(("#", "%"))]
    lines = [ln.strip() for ln in raw_lines if ln.strip() and not ln.strip().startswith(("#", "%"))]
    if not lines:
        return {"file": fp.name, "ok": False, "reason": "empty_trace"}

    delim = "\t" if "\t" in lines[0] else ("," if "," in lines[0] else (";" if ";" in lines[0] else None))
    rows = [next(csv.reader([ln], delimiter=delim)) if delim else ln.split() for ln in lines]
    start_idx = 1 if all(_to_float(c) is None for c in rows[0]) else 0
    headers = [c.strip() for c in rows[0]] if start_idx == 1 else [f"ch_{i+1}" for i in range(len(rows[0]))]
    data_rows = rows[start_idx:]

    units = sorted(set(UNIT_RE.findall(" ".join(comment_lines + headers))))
    # Parse x (first numeric col if >=2 cols) and y (last numeric col)
    xs: list[float] = []
    ys: list[float] = []
    missing_rows = 0
    for idx, r in enumerate(data_rows):
        if not r:
            continue
        yv = _to_float(r[-1])
        xv = _to_float(r[0]) if len(r) >= 2 else float(idx)
        if yv is None or xv is None:
            missing_rows += 1
            continue
        xs.append(xv)
        ys.append(yv)

    if not ys:
        return {"file": fp.name, "ok": False, "reason": "no_numeric_points", "missing_rows": missing_rows}

    n = len(ys)
    vmax = max(ys)
    vmin = min(ys)
    peak_idx = ys.index(vmax)
    x_at_peak = xs[peak_idx]

    # Initial slope proxy (first 20% of points up to peak)
    init_end = max(2, min(peak_idx + 1, int(max(3, n * 0.2))))
    dx = xs[init_end - 1] - xs[0]
    initial_slope = round((ys[init_end - 1] - ys[0]) / dx, 6) if abs(dx) > 1e-12 else None

    # Trapezoidal AUC (energy proxy)
    auc = 0.0
    for i in range(n - 1):
        auc += 0.5 * (ys[i] + ys[i + 1]) * (xs[i + 1] - xs[i])

    # Post-peak drop percentage (e.g., fracture drop in bending curves)
    post_drop_pct = None
    if peak_idx < n - 1 and abs(vmax) > 1e-12:
        post_min = min(ys[peak_idx + 1 :])
        post_drop_pct = round(100.0 * (vmax - post_min) / abs(vmax), 3)

    inflections = 0
    plateau_tail_cv = None
    saturated_runs = 0
    if n >= 4:
        diffs = [ys[i + 1] - ys[i] for i in range(n - 1)]
        signs = [1 if d > 1e-9 else (-1 if d < -1e-9 else 0) for d in diffs]
        nz = [sg for sg in signs if sg != 0]
        inflections = sum(1 for i in range(len(nz) - 1) if nz[i] != nz[i + 1])
        tail = ys[max(0, int(n * 0.8)) :]
        if len(tail) >= 2 and abs(statistics.mean(tail)) > 1e-9:
            plateau_tail_cv = round(statistics.stdev(tail) / abs(statistics.mean(tail)), 4)
        cur_run = 0
        for v in ys:
            if abs(v - vmax) < 1e-9 or abs(v - vmin) < 1e-9:
                cur_run += 1
                if cur_run == 5:
                    saturated_runs += 1
            else:
                cur_run = 0

    flatline = (vmax - vmin) < 1e-9 and n >= 3
    return {
        "file": fp.name,
        "batch_prefix": _infer_prefix(fp.name),
        "ok": True,
        "points": n,
        "missing_rows": missing_rows,
        "headers": headers,
        "units_detected": units,
        "peak_y": round(vmax, 6),
        "x_at_peak": round(x_at_peak, 6),
        "initial_slope": initial_slope,
        "auc_trapezoid": round(auc, 6),
        "post_peak_drop_pct": post_drop_pct,
        "inflection_count": inflections,
        "plateau_tail_cv": plateau_tail_cv,
        "saturation_flag": saturated_runs > 0,
        "flatline_flag": flatline,
        "summary": _summarize_series(ys),
    }


def _flag_cross_source_ambiguities(
    workbooks: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    matlab_params: list[dict[str, str]],
    text_units: dict[str, list[str]],
) -> list[dict[str, Any]]:
    ambiguities: list[dict[str, Any]] = []
    # Check conflicting stress/pressure or concentration units across files
    unit_families = {
        "stress_pressure": {"MPa", "GPa", "kPa", "Pa", "N/mm2", "N/mm^2"},
        "concentration": {"mg/mL", "ug/mL", "ng/mL", "ug/L", "mmol/L", "uM", "mM"},
        "length": {"mm", "um", "nm", "cm"},
        "time": {"ms", "sec", "min", "hr", "hrs"},
    }
    all_units_by_src: dict[str, set[str]] = defaultdict(set)
    for wb in workbooks:
        for u in wb.get("units_detected", []):
            all_units_by_src[u].add(wb["file"])
    for tr in traces:
        for u in tr.get("units_detected", []):
            all_units_by_src[u].add(tr["file"])
    for mp in matlab_params:
        for u in UNIT_RE.findall(mp.get("comment", "") + " " + mp.get("expr", "")):
            all_units_by_src[u].add(mp["file"])
    for src_name, ulist in text_units.items():
        for u in ulist:
            all_units_by_src[u].add(src_name)

    for fam, members in unit_families.items():
        present = sorted(members & set(all_units_by_src.keys()))
        if len(present) > 1:
            ambiguities.append({
                "type": "unit_discrepancy",
                "family": fam,
                "units_observed": present,
                "sources": {u: sorted(all_units_by_src[u])[:5] for u in present},
                "guidance": "Retain original units per source and verify conversion factor before pooling across files.",
            })

    # Detect saturated / flatline / truncated traces
    sat = [t["file"] for t in traces if t.get("saturation_flag")]
    flat = [t["file"] for t in traces if t.get("flatline_flag")]
    if sat:
        ambiguities.append({
            "type": "sensor_saturation_detected",
            "count": len(sat),
            "files_sample": sat[:15],
            "guidance": "Peak values may be clipped at sensor rail; retain as censored/saturated rather than true peak.",
        })
    if flat:
        ambiguities.append({
            "type": "flatline_trace_detected",
            "count": len(flat),
            "files_sample": flat[:15],
            "guidance": "Check sensor connection or channel gain log before treating zero-variance trace as valid replicate.",
        })
    return ambiguities


def profile_experiment_dir(input_dir: Path) -> dict[str, Any]:
    workbooks = [profile_xlsx_workbook(fp) for fp in sorted(input_dir.rglob("*.xlsx"))]
    traces = [
        profile_trace_file(fp)
        for fp in sorted(input_dir.rglob("*"))
        if fp.is_file() and fp.suffix.lower() in {".csv", ".tsv", ".dat", ".txt"} and fp.name.lower() not in {"task.md", "case.json"}
    ]

    matlab_params: list[dict[str, str]] = []
    for mp in sorted(input_dir.rglob("*.m")):
        for lineno, line in enumerate(mp.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            m = MATLAB_ASSIGN_RE.match(line)
            if m:
                matlab_params.append({
                    "file": mp.name,
                    "line": str(lineno),
                    "var": m.group(1),
                    "expr": m.group(2).strip(),
                    "comment": (m.group(3) or "").strip(),
                })

    text_units: dict[str, list[str]] = {}
    for tp in sorted(input_dir.rglob("*.md")):
        txt = tp.read_text(encoding="utf-8", errors="ignore")
        us = sorted(set(UNIT_RE.findall(txt)))
        if us:
            text_units[tp.name] = us

    # Flag truncated traces relative to median point count when >= 5 traces exist
    ok_traces = [t for t in traces if t.get("ok")]
    if len(ok_traces) >= 5:
        med_pts = statistics.median(t["points"] for t in ok_traces)
        for t in ok_traces:
            t["truncated_flag"] = t["points"] < 0.6 * med_pts

    ambiguities = _flag_cross_source_ambiguities(workbooks, ok_traces, matlab_params, text_units)

    return {
        "$schema": "experiment-trace-workbook-profile/v2",
        "ok": True,
        "workbook_count": len(workbooks),
        "trace_file_count": len(traces),
        "valid_trace_count": len(ok_traces),
        "matlab_param_count": len(matlab_params),
        "detected_ambiguities": ambiguities,
        "workbooks": workbooks,
        "traces": traces[:500],
        "matlab_parameters": matlab_params[:120],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile multi-sheet XLSX workbooks, batch raw curve traces, and MATLAB scripts.")
    parser.add_argument("--input", required=True, help="Input experiment directory")
    parser.add_argument("--out", required=True, help="Output profile JSON path")
    args = parser.parse_args(argv)

    rep = profile_experiment_dir(Path(args.input).resolve())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "workbooks": rep["workbook_count"],
        "traces": rep["trace_file_count"],
        "matlab_params": rep["matlab_param_count"],
        "ambiguities": len(rep["detected_ambiguities"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
