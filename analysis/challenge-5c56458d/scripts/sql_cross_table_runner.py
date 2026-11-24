#!/usr/bin/env python3
"""Read-only SQLite cross-table execution runner with:
- Strict read-only SQL fence & URI mode=ro + pre/post DB SHA-256 immutability verification
- EXPLAIN QUERY PLAN capture & schema-aware SQL error self-localization
- True JOIN cardinality inflation guard (detail-level COUNT(*) vs COUNT(DISTINCT base_pk) & aggregate sum vs base rows)
- Coded-integer mis-aggregation detector (AVG/SUM on non-binary coded category columns)
- Legitimate empty group (LEFT JOIN 0-sample dimension) vs anomalous empty query triage
- Independent native Python recalculation reconciliation (max_abs_diff < 1e-9)
- Pure-stdlib editable vector SVG summary chart (plus optional Matplotlib PNG/PDF if installed)
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import html
import json
import math
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sqlite_schema_inspector import inspect_sqlite_db

CELL_PALETTE = ["#547FA3", "#D77C70", "#4E9187", "#D19A45", "#967A9C", "#66A9B4"]
_FORBIDDEN_SQL_KW = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|VACUUM|REINDEX|PRAGMA|BEGIN|COMMIT|ROLLBACK|SAVEPOINT)\b",
    re.IGNORECASE,
)
_COUNT_COL_CANDIDATES = ("n_samples", "sample_count", "n", "row_count", "cnt", "n_records")
_AGG_COL_RE = re.compile(r"\b(AVG|SUM)\s*\(\s*(?:DISTINCT\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\)", re.IGNORECASE)
_WHERE_EQ_RE = re.compile(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'([^']*)'")
_FROM_JOIN_RE = re.compile(r"\b(?:FROM|JOIN)\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _strip_sql_comments_and_literals(sql: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    no_line = re.sub(r"--[^\n]*", " ", no_block)
    no_str = re.sub(r"'(?:''|[^'])*'", "''", no_line)
    return no_str.strip()


def _validate_read_only_sql(sql: str) -> tuple[bool, str]:
    cleaned = _strip_sql_comments_and_literals(sql)
    if not cleaned:
        return False, "Empty SQL statement"
    trimmed = cleaned.rstrip(";").strip()
    if ";" in trimmed:
        return False, "Stacked multi-statement SQL is forbidden"
    upper = trimmed.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return False, "Only read-only SELECT or WITH CTE statements are permitted"
    m = _FORBIDDEN_SQL_KW.search(trimmed)
    if m:
        return False, f"Forbidden write/DDL/control keyword detected: {m.group(0).upper()}"
    return True, "ok"


def _diagnose_sql_error(err_msg: str, schema_rep: dict[str, Any]) -> dict[str, Any]:
    """Provide schema-aware fuzzy suggestions when SQLite raises OperationalError."""
    all_tables = [t["table"] for t in schema_rep["tables"]]
    all_cols_by_table = {t["table"]: [c["name"] for c in t["columns"]] for t in schema_rep["tables"]}
    all_cols_flat = sorted({c for cols in all_cols_by_table.values() for c in cols})

    diag: dict[str, Any] = {"raw_error": err_msg, "error_type": "SQL_OPERATIONAL_ERROR", "suggestions": []}
    m_col = re.search(r"no such column:\s*(?:([A-Za-z0-9_]+)\.)?([A-Za-z0-9_]+)", err_msg, re.I)
    m_tbl = re.search(r"no such table:\s*([A-Za-z0-9_]+)", err_msg, re.I)
    m_amb = re.search(r"ambiguous column name:\s*([A-Za-z0-9_]+)", err_msg, re.I)

    if m_col:
        bad_col = m_col.group(2)
        diag["error_type"] = "NO_SUCH_COLUMN"
        diag["offending_identifier"] = bad_col
        matches = difflib.get_close_matches(bad_col, all_cols_flat, n=5, cutoff=0.45)
        diag["suggestions"] = matches
        diag["remediation"] = f"Replace unknown column '{bad_col}' with a verified column from schema audit: {matches or all_cols_flat[:8]}."
    elif m_tbl:
        bad_tbl = m_tbl.group(1)
        diag["error_type"] = "NO_SUCH_TABLE"
        diag["offending_identifier"] = bad_tbl
        matches = difflib.get_close_matches(bad_tbl, all_tables, n=5, cutoff=0.4)
        diag["suggestions"] = matches
        diag["remediation"] = f"Replace unknown table '{bad_tbl}' with an existing table: {matches or all_tables}."
    elif m_amb:
        amb_col = m_amb.group(1)
        owning = [t for t, cols in all_cols_by_table.items() if amb_col in cols]
        diag["error_type"] = "AMBIGUOUS_COLUMN"
        diag["offending_identifier"] = amb_col
        diag["suggestions"] = [f"{t}.{amb_col}" for t in owning]
        diag["remediation"] = f"Qualify '{amb_col}' with its table alias (present in tables: {owning})."
    else:
        diag["remediation"] = "Inspect SQL syntax and verify all table/column names against sqlite_schema_inspector.py output."
    return diag


def _diagnose_empty_query(conn: sqlite3.Connection, sql: str, schema_rep: dict[str, Any]) -> dict[str, Any]:
    """Probe referenced tables and WHERE equality literals to explain an unexpected 0-row result."""
    table_counts = {t["table"]: t["row_count"] for t in schema_rep["tables"]}
    ref_tables = [t for t in _FROM_JOIN_RE.findall(sql) if t in table_counts]
    non_empty_refs = {t: table_counts[t] for t in dict.fromkeys(ref_tables)}

    literal_checks: list[dict[str, Any]] = []
    for col, lit in _WHERE_EQ_RE.findall(sql):
        for t in non_empty_refs:
            t_cols = {c["name"] for tr in schema_rep["tables"] if tr["table"] == t for c in tr["columns"]}
            if col in t_cols:
                vals = [
                    str(r[0])
                    for r in conn.execute(
                        f"SELECT DISTINCT {_qident(col)} FROM {_qident(t)} WHERE {_qident(col)} IS NOT NULL LIMIT 50"
                    ).fetchall()
                ]
                exists = lit in vals
                close = difflib.get_close_matches(lit, vals, n=3, cutoff=0.4) if not exists else []
                literal_checks.append(
                    {
                        "table": t,
                        "column": col,
                        "queried_literal": lit,
                        "exists_in_column": exists,
                        "closest_actual_values": close,
                        "sample_actual_values": vals[:5],
                    }
                )
    return {
        "referenced_table_row_counts": non_empty_refs,
        "where_literal_probes": literal_checks,
        "remediation": (
            "Referenced tables are non-empty; check WHERE filter spelling/case, leading-zero ID formatting, or overly restrictive INNER JOIN."
            if any(v > 0 for v in non_empty_refs.values())
            else "All referenced source tables are genuinely empty."
        ),
    }


def _check_coded_misaggregation(sql: str, schema_rep: dict[str, Any]) -> list[str]:
    """Flag AVG() or SUM() applied to non-binary coded integer columns."""
    non_binary_coded = {
        c["column"].lower(): c
        for c in schema_rep.get("coded_integer_summary", [])
        if c.get("coded_kind") != "binary_flag_0_1"
    }
    cleaned = _strip_sql_comments_and_literals(sql)
    warnings: list[str] = []
    for fn, col in _AGG_COL_RE.findall(cleaned):
        if col.lower() in non_binary_coded:
            warnings.append(
                f"Coded integer column '{col}' aggregated with {fn.upper()}({col}); coded categories must only be used in GROUP BY/JOIN/COUNT(DISTINCT)."
            )
    return warnings


def _betacf(a: float, b: float, x: float) -> float:
    """Lentz's continued fraction for regularized incomplete beta I_x(a, b)."""
    fpmin = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _reg_inc_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b) in pure stdlib."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return min(1.0, max(0.0, front * _betacf(a, b, x) / a))
    return min(1.0, max(0.0, 1.0 - front * _betacf(b, a, 1.0 - x) / b))


def _reg_upper_gamma(a: float, x: float) -> float:
    """Regularized upper incomplete gamma function Q(a, x) in pure stdlib."""
    if x <= 0.0:
        return 1.0
    if a <= 0.0:
        return math.nan
    gln = math.lgamma(a)
    if x < a + 1.0:
        # Series representation for P(a, x), return 1 - P
        ap = a
        term = 1.0 / a
        total = term
        for _ in range(300):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-13:
                break
        p_val = total * math.exp(-x + a * math.log(x) - gln)
        return min(1.0, max(0.0, 1.0 - p_val))
    # Lentz continued fraction for Q(a, x)
    fpmin = 1e-300
    b = x + 1.0 - a
    c = 1.0 / fpmin
    d = 1.0 / b if abs(b) >= fpmin else 1.0 / fpmin
    h = d
    for i in range(1, 300):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < fpmin:
            d = fpmin
        c = b + an / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-13:
            break
    return min(1.0, max(0.0, math.exp(-x + a * math.log(x) - gln) * h))


def _f_sf_exact(f_stat: float, df1: float, df2: float) -> float:
    if not math.isfinite(f_stat) or f_stat < 0.0 or df1 <= 0.0 or df2 <= 0.0:
        return math.nan
    if f_stat == 0.0:
        return 1.0
    x = df2 / (df2 + df1 * f_stat)
    return _reg_inc_beta(0.5 * df2, 0.5 * df1, x)


def _chi2_sf_exact(x: float, df: float) -> float:
    if not math.isfinite(x) or x <= 0.0:
        return 1.0
    if df <= 0.0:
        return math.nan
    return _reg_upper_gamma(0.5 * df, 0.5 * x)


def _t_two_sided_p_exact(t_abs: float, df: float) -> float:
    if not math.isfinite(t_abs) or df <= 0.0:
        return math.nan
    if t_abs == 0.0:
        return 1.0
    x = df / (df + t_abs * t_abs)
    return _reg_inc_beta(0.5 * df, 0.5, x)


def _t_crit_95_exact(df: float) -> float:
    """Exact two-sided 95% Student t critical value for any continuous df > 0 via bisection."""
    if df <= 0.0:
        return 1.959963984540054
    if df > 1e6:
        return 1.959963984540054
    lo, hi = 1.9599, 20.0 if df >= 1.0 else 200.0
    while _t_two_sided_p_exact(hi, df) > 0.05:
        hi *= 2.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _t_two_sided_p_exact(mid, df) > 0.05:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _wilson_ci_95(p_hat: float, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score 95% CI strictly bounded in [0.0, 1.0] for proportions."""
    if n <= 0:
        return 0.0, 1.0
    p = min(1.0, max(0.0, p_hat))
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt((p * (1.0 - p)) / n + z2 / (4.0 * n * n))
    return max(0.0, center - half), min(1.0, center + half)


def _holm_bonferroni(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [1.0] * m
    prev = 0.0
    for rank, idx in enumerate(order):
        val = min(1.0, max(prev, pvals[idx] * (m - rank)))
        adj[idx] = val
        prev = val
    return adj


def _row_matches_filters(row: dict[str, Any], filters: list[dict[str, Any]]) -> bool:
    for flt in filters:
        col = str(flt["col"])
        op = str(flt["op"]).strip()
        target = flt["value"]
        val = row.get(col)
        if val is None:
            return False
        if isinstance(target, (int, float)):
            try:
                fv = float(val)
            except (TypeError, ValueError):
                return False
            tv = float(target)
            if op == ">=" and not (fv >= tv):
                return False
            elif op == ">" and not (fv > tv):
                return False
            elif op == "<=" and not (fv <= tv):
                return False
            elif op == "<" and not (fv < tv):
                return False
            elif op == "=" and not (fv == tv):
                return False
            elif op in {"!=", "<>"} and not (fv != tv):
                return False
        else:
            sv, tv = str(val), str(target)
            if op == "=" and sv != tv:
                return False
            elif op in {"!=", "<>"} and sv == tv:
                return False
    return True


def _run_independent_python_reconciliation(
    conn: sqlite3.Connection,
    reconcile_cfg: dict[str, Any],
    sql_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Independently load raw tables in Python, perform filtering/grouping/pre-agg/sort/limit, and verify max_abs_diff < 1e-9."""
    base_table = str(reconcile_cfg["base_table"])
    group_col_sql = str(reconcile_cfg["sql_group_col"])
    count_col_sql = str(reconcile_cfg.get("sql_count_col", "n_samples"))
    metrics_cfg: list[dict[str, Any]] = list(reconcile_cfg.get("metrics") or [])
    filters_cfg: list[dict[str, Any]] = list(reconcile_cfg.get("filters") or [])
    order_by_cfg: dict[str, Any] | None = reconcile_cfg.get("order_by")
    limit_cfg: int | None = int(reconcile_cfg["limit"]) if reconcile_cfg.get("limit") is not None else None

    # Load base rows as dicts and mirror parameterized WHERE/ON filter predicates
    cur = conn.execute(f"SELECT * FROM {_qident(base_table)}")
    base_cols = [d[0] for d in cur.description]
    base_rows = [dict(zip(base_cols, row)) for row in cur.fetchall()]
    if filters_cfg:
        base_rows = [r for r in base_rows if _row_matches_filters(r, filters_cfg)]

    # Optional 1:N child pre-aggregation in native Python (grouped by base_pk)
    child_pre_agg = reconcile_cfg.get("child_pre_agg")
    child_by_pk: dict[Any, dict[str, Any]] = {}
    binary_child_aliases: set[str] = set()
    if child_pre_agg:
        ct = str(child_pre_agg["table"])
        join_key = str(child_pre_agg["join_key"])
        c_cur = conn.execute(f"SELECT * FROM {_qident(ct)}")
        c_cols = [d[0] for d in c_cur.description]
        c_grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        all_child_vals: dict[str, set[float]] = defaultdict(set)
        for r in c_cur.fetchall():
            rec = dict(zip(c_cols, r))
            c_grouped[rec[join_key]].append(rec)
            for alias, spec in (child_pre_agg.get("per_key_metrics") or {}).items():
                col = spec["col"]
                if rec.get(col) is not None:
                    all_child_vals[alias].add(float(rec[col]))
        for alias, uniq_vals in all_child_vals.items():
            if uniq_vals and uniq_vals.issubset({0.0, 1.0}):
                binary_child_aliases.add(alias)
        for pk_val, recs in c_grouped.items():
            summary: dict[str, Any] = {"__child_row_count__": len(recs)}
            for alias, spec in (child_pre_agg.get("per_key_metrics") or {}).items():
                col = spec["col"]
                agg = spec["agg"]
                vals = [float(x[col]) for x in recs if x[col] is not None]
                if agg == "mean":
                    summary[alias] = statistics.fmean(vals) if vals else None
                elif agg == "sum":
                    summary[alias] = sum(vals)
                elif agg == "count":
                    summary[alias] = len(vals)
            child_by_pk[pk_val] = summary

    # Optional dimension table (LEFT JOIN preserving legitimate empty groups)
    dim_cfg = reconcile_cfg.get("dimension_table")
    group_rows: dict[Any, list[dict[str, Any]]] = {}
    dim_order: dict[str, Any] = {}
    if dim_cfg:
        dt = str(dim_cfg["table"])
        dim_key = str(dim_cfg["dim_key"])
        base_fk = str(dim_cfg["base_fk"])
        label_col = str(dim_cfg.get("label_col", dim_key))
        d_cur = conn.execute(f"SELECT * FROM {_qident(dt)}")
        d_cols = [d[0] for d in d_cur.description]
        key_to_label: dict[Any, Any] = {}
        for r in d_cur.fetchall():
            drec = dict(zip(d_cols, r))
            lbl = drec[label_col]
            key_to_label[drec[dim_key]] = lbl
            dim_order[str(lbl)] = drec[dim_key]
            group_rows.setdefault(lbl, [])
        for brow in base_rows:
            fk_val = brow.get(base_fk)
            if fk_val in key_to_label:
                merged = dict(brow)
                if child_pre_agg:
                    jk = str(child_pre_agg["join_key"])
                    merged.update(child_by_pk.get(brow.get(jk), {}))
                group_rows[key_to_label[fk_val]].append(merged)
    else:
        bin_spec = reconcile_cfg.get("bin_spec")
        raw_grp = str(reconcile_cfg.get("base_group_col", group_col_sql))
        for brow in base_rows:
            merged = dict(brow)
            if child_pre_agg:
                jk = str(child_pre_agg["join_key"])
                merged.update(child_by_pk.get(brow.get(jk), {}))
            if bin_spec:
                bval = brow.get(str(bin_spec["source_col"]))
                glabel = "__unbinned__"
                if bval is not None:
                    fv = float(bval)
                    for brule in bin_spec.get("bins") or []:
                        ok_lo = True
                        ok_hi = True
                        if "min" in brule and brule["min"] is not None:
                            ok_lo = fv >= float(brule["min"])
                        if "min_exclusive" in brule and brule["min_exclusive"] is not None:
                            ok_lo = ok_lo and (fv > float(brule["min_exclusive"]))
                        if "max" in brule and brule["max"] is not None:
                            ok_hi = fv <= float(brule["max"])
                        if "max_exclusive" in brule and brule["max_exclusive"] is not None:
                            ok_hi = ok_hi and (fv < float(brule["max_exclusive"]))
                        if ok_lo and ok_hi:
                            glabel = str(brule["label"])
                            break
                group_rows.setdefault(glabel, []).append(merged)
            else:
                group_rows.setdefault(brow.get(raw_grp), []).append(merged)

    # Detect binary 0/1 proportion metrics globally across all groups so Wilson score 95% CI is chosen automatically
    binary_metric_cols: set[str] = set()
    for m in metrics_cfg:
        if str(m.get("agg", "")).lower() != "mean":
            continue
        sc = str(m["sql_col"])
        src_c = str(m["source_col"])
        if bool(m.get("is_binary_proportion")) or (src_c in binary_child_aliases):
            binary_metric_cols.add(sc)
            continue
        all_m_vals = [float(r[src_c]) for rows in group_rows.values() for r in rows if r.get(src_c) is not None]
        if all_m_vals and set(all_m_vals).issubset({0.0, 1.0}):
            binary_metric_cols.add(sc)

    py_by_group: dict[str, dict[str, Any]] = {}
    group_uncertainty: dict[str, dict[str, Any]] = {}
    for gkey, rows in group_rows.items():
        g_str = str(gkey)
        entry: dict[str, Any] = {count_col_sql: len(rows)}
        u_entry: dict[str, Any] = {group_col_sql: g_str, count_col_sql: len(rows)}
        for m in metrics_cfg:
            sql_col = str(m["sql_col"])
            src_col = str(m["source_col"])
            agg = str(m["agg"]).lower()
            vals = [float(r[src_col]) for r in rows if r.get(src_col) is not None]
            if agg == "mean":
                mean_v = statistics.fmean(vals) if vals else None
                entry[sql_col] = mean_v
                u_entry[f"{sql_col}_mean"] = round(mean_v, 6) if mean_v is not None else None
                is_bin_prop = sql_col in binary_metric_cols
                u_entry[f"{sql_col}_ci_method"] = "wilson_score_95" if is_bin_prop else "student_t_95"
                if is_bin_prop and vals:
                    w_lo, w_hi = _wilson_ci_95(mean_v, len(vals))  # type: ignore[arg-type]
                    u_entry[f"{sql_col}_sd"] = round(statistics.stdev(vals), 6) if len(vals) >= 2 else None
                    u_entry[f"{sql_col}_min"] = round(min(vals), 6)
                    u_entry[f"{sql_col}_max"] = round(max(vals), 6)
                    u_entry[f"{sql_col}_ci95_lo"] = round(w_lo, 6)
                    u_entry[f"{sql_col}_ci95_hi"] = round(w_hi, 6)
                elif len(vals) >= 2:
                    sd_v = statistics.stdev(vals)
                    se_v = sd_v / math.sqrt(len(vals))
                    tc = _t_crit_95_exact(float(len(vals) - 1))
                    u_entry[f"{sql_col}_sd"] = round(sd_v, 6)
                    u_entry[f"{sql_col}_min"] = round(min(vals), 6)
                    u_entry[f"{sql_col}_max"] = round(max(vals), 6)
                    u_entry[f"{sql_col}_ci95_lo"] = round(mean_v - tc * se_v, 6)  # type: ignore[operator]
                    u_entry[f"{sql_col}_ci95_hi"] = round(mean_v + tc * se_v, 6)  # type: ignore[operator]
                else:
                    u_entry[f"{sql_col}_sd"] = None
                    u_entry[f"{sql_col}_min"] = round(vals[0], 6) if vals else None
                    u_entry[f"{sql_col}_max"] = round(vals[0], 6) if vals else None
                    u_entry[f"{sql_col}_ci95_lo"] = None
                    u_entry[f"{sql_col}_ci95_hi"] = None
            elif agg == "sum":
                zero_if_empty = bool(m.get("coalesce_zero", False))
                entry[sql_col] = sum(vals) if vals else (0.0 if zero_if_empty else None)
            elif agg == "min":
                entry[sql_col] = min(vals) if vals else None
            elif agg == "max":
                entry[sql_col] = max(vals) if vals else None
            elif agg == "count":
                entry[sql_col] = len(vals)
        py_by_group[g_str] = entry
        group_uncertainty[g_str] = u_entry

    # Mirror ORDER BY and LIMIT predicates on grouped Python reference (SQLite puts NULL first in ASC, last in DESC)
    if order_by_cfg or limit_cfg is not None:
        ordered_keys = list(py_by_group.keys())
        if order_by_cfg:
            ocol = str(order_by_cfg.get("col", group_col_sql))
            odir = str(order_by_cfg.get("direction", "ASC")).upper()
            desc = odir == "DESC"

            def _sort_key(gk: str) -> tuple[Any, ...]:
                if ocol == group_col_sql:
                    v = gk
                else:
                    v = py_by_group[gk].get(ocol)
                is_null = v is None
                if desc:
                    # DESC: non-nulls descending, then NULLs last
                    return (1 if is_null else 0, -(float(v)) if (not is_null and isinstance(v, (int, float))) else 0.0, "" if is_null else str(v), str(dim_order.get(gk, gk)))
                # ASC: NULLs first (SQLite default), then ascending
                return (0 if is_null else 1, float(v) if (not is_null and isinstance(v, (int, float))) else 0.0, "" if is_null else str(v), str(dim_order.get(gk, gk)))

            if desc and ocol == group_col_sql:
                ordered_keys.sort(reverse=True)
            else:
                ordered_keys.sort(key=_sort_key)
        if limit_cfg is not None:
            ordered_keys = ordered_keys[:limit_cfg]
        py_by_group = {gk: py_by_group[gk] for gk in ordered_keys}
        group_uncertainty = {gk: group_uncertainty[gk] for gk in ordered_keys}

    sql_by_group = {str(r.get(group_col_sql)): r for r in sql_records}
    py_keys = set(py_by_group.keys())
    sql_keys = set(sql_by_group.keys())

    mismatches: list[str] = []
    if py_keys != sql_keys:
        mismatches.append(f"Group key set mismatch: missing_in_sql={sorted(py_keys - sql_keys)}, extra_in_sql={sorted(sql_keys - py_keys)}")

    max_abs_diff = 0.0
    checked_pairs = 0
    for gk in sorted(py_keys & sql_keys):
        py_r = py_by_group[gk]
        sq_r = sql_by_group[gk]
        # Count check
        if count_col_sql in sq_r:
            checked_pairs += 1
            if int(sq_r[count_col_sql]) != int(py_r[count_col_sql]):
                mismatches.append(f"Group '{gk}' {count_col_sql}: sql={sq_r[count_col_sql]} vs python={py_r[count_col_sql]}")
        # Metric checks (strict None vs float parity + < 1e-9 tolerance)
        for m in metrics_cfg:
            sc = str(m["sql_col"])
            if sc not in sq_r:
                mismatches.append(f"Group '{gk}' missing SQL column '{sc}'")
                continue
            sv, pv = sq_r[sc], py_r[sc]
            checked_pairs += 1
            if sv is None and pv is None:
                continue
            if (sv is None) != (pv is None):
                mismatches.append(f"Group '{gk}' {sc} nullability mismatch: sql={sv!r} vs python={pv!r}")
                continue
            diff = abs(float(sv) - float(pv))
            if diff > max_abs_diff:
                max_abs_diff = diff
            if diff >= 1e-9:
                mismatches.append(f"Group '{gk}' {sc} numeric diff={diff:.3e} >= 1e-9 (sql={sv}, python={pv})")

    # Complete pure-stdlib inferential statistics chain:
    # One-Way ANOVA (exact F p-value) + Welch heteroscedastic ANOVA (exact F p-value)
    # + Kruskal-Wallis H (exact chi2 p-value) + Pairwise Welch t + Holm-Bonferroni + Hedges' g
    stat_assumptions: dict[str, Any] = {"performed": False}
    primary_mean_m = next((m for m in metrics_cfg if str(m.get("agg", "")).lower() == "mean"), None)
    if primary_mean_m is not None:
        src_col = str(primary_mean_m["source_col"])
        active_group_keys = set(py_by_group.keys())
        grp_arrays = [
            (str(gk), [float(r[src_col]) for r in rows if r.get(src_col) is not None])
            for gk, rows in group_rows.items()
            if str(gk) in active_group_keys
        ]
        non_empty = [(gk, arr) for gk, arr in grp_arrays if len(arr) >= 2]
        if len(non_empty) >= 2:
            all_v = [x for _, arr in non_empty for x in arr]
            n_tot = len(all_v)
            k_grp = len(non_empty)
            g_mean = statistics.fmean(all_v)
            ss_between = sum(len(arr) * (statistics.fmean(arr) - g_mean) ** 2 for _, arr in non_empty)
            ss_within = sum(sum((x - statistics.fmean(arr)) ** 2 for x in arr) for _, arr in non_empty)
            ss_total = ss_between + ss_within
            df1_a, df2_a = k_grp - 1, n_tot - k_grp
            f_stat = (ss_between / max(df1_a, 1)) / max(ss_within / max(df2_a, 1), 1e-15)
            anova_p = _f_sf_exact(f_stat, float(df1_a), float(df2_a))
            eta_sq = ss_between / max(ss_total, 1e-15)

            # Variance homogeneity proxy: max/min group variance ratio (Hartley Fmax)
            g_vars = [statistics.variance(arr) for _, arr in non_empty]
            pos_vars = [v for v in g_vars if v > 1e-15]
            var_ratio = (max(pos_vars) / min(pos_vars)) if len(pos_vars) >= 2 else None

            # Welch's heteroscedastic one-way ANOVA
            if all(v > 1e-15 for v in g_vars):
                ns = [len(arr) for _, arr in non_empty]
                means = [statistics.fmean(arr) for _, arr in non_empty]
                ws = [n / v for n, v in zip(ns, g_vars)]
                w_sum = sum(ws)
                x_bar = sum(w * m for w, m in zip(ws, means)) / w_sum
                w_num = sum(w * (m - x_bar) ** 2 for w, m in zip(ws, means)) / (k_grp - 1)
                lam = sum(((1.0 - w / w_sum) ** 2) / (n - 1) for w, n in zip(ws, ns))
                w_den = 1.0 + (2.0 * (k_grp - 2) / (k_grp * k_grp - 1)) * lam
                welch_f = w_num / max(w_den, 1e-15)
                w_df1 = float(k_grp - 1)
                w_df2 = (k_grp * k_grp - 1) / (3.0 * lam) if lam > 1e-15 else float(df2_a)
                welch_p = _f_sf_exact(welch_f, w_df1, w_df2)
                welch_anova = {
                    "estimable": True,
                    "f_stat": round(welch_f, 4),
                    "df1": round(w_df1, 3),
                    "df2": round(w_df2, 3),
                    "p_value": float(f"{welch_p:.6e}") if welch_p < 1e-4 else round(welch_p, 8),
                }
            else:
                welch_anova = {"estimable": False, "reason": "zero_variance_group"}

            # Tie-corrected Kruskal-Wallis H + exact chi2 p-value + epsilon^2
            sorted_idx = sorted(range(n_tot), key=lambda i: all_v[i])
            ranks = [0.0] * n_tot
            tie_sizes: list[int] = []
            ii = 0
            while ii < n_tot:
                jj = ii + 1
                while jj < n_tot and all_v[sorted_idx[jj]] == all_v[sorted_idx[ii]]:
                    jj += 1
                avg_r = 0.5 * ((ii + 1) + jj)
                for kk in range(ii, jj):
                    ranks[sorted_idx[kk]] = avg_r
                if jj - ii > 1:
                    tie_sizes.append(jj - ii)
                ii = jj
            pos = 0
            r2_sum = 0.0
            for _, arr in non_empty:
                r_sum = sum(ranks[pos : pos + len(arr)])
                r2_sum += (r_sum * r_sum) / len(arr)
                pos += len(arr)
            h_raw = (12.0 / (n_tot * (n_tot + 1.0))) * r2_sum - 3.0 * (n_tot + 1.0)
            tie_c = 1.0 - sum(t ** 3 - t for t in tie_sizes) / max(n_tot ** 3 - n_tot, 1)
            kw_h = h_raw / max(tie_c, 1e-15)
            kw_p = _chi2_sf_exact(kw_h, float(k_grp - 1))
            eps_sq = max(0.0, (kw_h - k_grp + 1.0) / max(n_tot - k_grp, 1))

            # Pairwise post-hoc comparisons: Welch t + Holm-Bonferroni + Hedges' g
            raw_pvals: list[float] = []
            pw_rows: list[dict[str, Any]] = []
            for idx_a in range(k_grp):
                for idx_b in range(idx_a + 1, k_grp):
                    ga, arr_a = non_empty[idx_a]
                    gb, arr_b = non_empty[idx_b]
                    na, nb = len(arr_a), len(arr_b)
                    ma, mb = statistics.fmean(arr_a), statistics.fmean(arr_b)
                    va, vb = statistics.variance(arr_a), statistics.variance(arr_b)
                    diff_m = mb - ma
                    se2 = va / na + vb / nb
                    if se2 > 1e-15:
                        se = math.sqrt(se2)
                        df_den = ((va / na) ** 2) / max(na - 1, 1) + ((vb / nb) ** 2) / max(nb - 1, 1)
                        df_w = (se2 * se2) / max(df_den, 1e-15)
                        t_w = diff_m / se
                        p_raw = _t_two_sided_p_exact(abs(t_w), df_w)
                        tc_w = _t_crit_95_exact(df_w)
                        diff_ci = [round(diff_m - tc_w * se, 6), round(diff_m + tc_w * se, 6)]
                    else:
                        t_w, df_w, p_raw, diff_ci = 0.0, float(na + nb - 2), 1.0, [round(diff_m, 6), round(diff_m, 6)]
                    pooled_sd = math.sqrt(((na - 1) * va + (nb - 1) * vb) / max(na + nb - 2, 1))
                    j_corr = 1.0 - 3.0 / max(4 * (na + nb) - 9, 1)
                    hedges_g = (diff_m / pooled_sd * j_corr) if pooled_sd > 1e-12 else None
                    raw_pvals.append(p_raw)
                    pw_rows.append({
                        "pair": f"{ga} vs {gb}",
                        "test": "Welch_t",
                        "mean_diff": round(diff_m, 6),
                        "mean_diff_ci95": diff_ci,
                        "t_stat": round(t_w, 4),
                        "df": round(df_w, 3),
                        "hedges_g": round(hedges_g, 4) if hedges_g is not None else None,
                        "p_raw": float(f"{p_raw:.6e}") if p_raw < 1e-4 else round(p_raw, 8),
                    })
            for row_pw, p_adj in zip(pw_rows, _holm_bonferroni(raw_pvals)):
                row_pw["p_holm"] = float(f"{p_adj:.6e}") if p_adj < 1e-4 else round(p_adj, 8)
                row_pw["significant_holm_05"] = bool(p_adj < 0.05)

            het_flag = bool(var_ratio is not None and var_ratio > 4.0)
            stat_assumptions = {
                "performed": True,
                "metric_column": src_col,
                "p_value_method": "pure_stdlib_exact_incomplete_beta_and_gamma",
                "non_empty_groups": k_grp,
                "empty_groups_excluded_from_omnibus": len(grp_arrays) - k_grp,
                "total_n": n_tot,
                "variance_ratio_fmax": round(var_ratio, 4) if var_ratio is not None else None,
                "heteroscedasticity_flagged": het_flag,
                "anova_f": round(f_stat, 4),
                "anova_df": [df1_a, df2_a],
                "anova_p_value": float(f"{anova_p:.6e}") if anova_p < 1e-4 else round(anova_p, 8),
                "eta_squared": round(eta_sq, 4),
                "welch_anova": welch_anova,
                "kruskal_wallis_h": round(kw_h, 4),
                "kruskal_df": k_grp - 1,
                "kruskal_p_value": float(f"{kw_p:.6e}") if kw_p < 1e-4 else round(kw_p, 8),
                "epsilon_squared": round(eps_sq, 4),
                "pairwise_comparisons": pw_rows,
                "recommended_inference_route": (
                    "Welch ANOVA / Kruskal-Wallis + pairwise Welch t (Holm-Bonferroni) [heteroscedasticity flagged: Fmax > 4.0]"
                    if het_flag
                    else "One-Way ANOVA / Welch ANOVA / Kruskal-Wallis concordant + pairwise Welch t (Holm-Bonferroni)"
                ),
            }

    passed = len(mismatches) == 0 and max_abs_diff < 1e-9 and checked_pairs > 0
    return {
        "performed": True,
        "passed": passed,
        "tolerance": 1e-9,
        "checked_comparisons": checked_pairs,
        "max_abs_diff": max_abs_diff,
        "mismatches": mismatches,
        "filters_mirrored": filters_cfg,
        "order_by_mirrored": order_by_cfg,
        "limit_mirrored": limit_cfg,
        "binary_proportion_wilson_metrics": sorted(binary_metric_cols),
        "python_reference_by_group": py_by_group,
        "group_uncertainty_summary": list(group_uncertainty.values()),
        "statistical_assumptions_check": stat_assumptions,
    }


def _render_stdlib_svg_chart(
    svg_path: Path,
    *,
    title: str,
    group_col: str,
    value_col: str,
    records: list[dict[str, Any]],
    count_col: str | None,
) -> None:
    """Render an editable pure-stdlib SVG bar chart preserving legitimate empty groups (n=0)."""
    w, h = 680, 340
    left, right, top, bottom = 78, 30, 54, 72
    plot_w = w - left - right
    plot_h = h - top - bottom

    numeric_vals = [float(r[value_col]) for r in records if r.get(value_col) is not None]
    y_max = (max(numeric_vals) * 1.22) if numeric_vals else 1.0
    if y_max <= 0:
        y_max = 1.0

    n_bars = max(len(records), 1)
    slot_w = plot_w / n_bars
    bar_w = min(68.0, slot_w * 0.56)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        f'<text x="{left + plot_w / 2:.1f}" y="28" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="13.5" font-weight="bold" fill="#1F2937">{html.escape(title)}</text>',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#222222" stroke-width="1.2"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#222222" stroke-width="1.2"/>',
    ]

    # Y ticks & horizontal grid
    for i in range(5):
        frac = i / 4.0
        yv = y_max * frac
        yy = top + plot_h - frac * plot_h
        if i > 0:
            parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + plot_w}" y2="{yy:.1f}" stroke="#E5E7EB" stroke-width="0.8"/>')
        parts.append(
            f'<text x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end" font-family="DejaVu Sans, Arial, sans-serif" font-size="10" fill="#4B5563">{yv:.2f}</text>'
        )

    # Bars
    for idx, rec in enumerate(records):
        cx = left + (idx + 0.5) * slot_w
        bx = cx - bar_w / 2.0
        lbl = str(rec.get(group_col, ""))
        raw_val = rec.get(value_col)
        cnt_val = rec.get(count_col) if count_col and count_col in rec else None
        color = CELL_PALETTE[idx % len(CELL_PALETTE)]

        if raw_val is None or (cnt_val is not None and int(cnt_val) == 0):
            # Legitimate empty group marker (never plot misleading 0.0 solid bar)
            by = top + plot_h - 14
            parts.extend(
                [
                    f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="14" fill="none" stroke="#9CA3AF" stroke-width="1.2" stroke-dasharray="4,3"/>',
                    f'<text x="{cx:.1f}" y="{by - 6:.1f}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="9.5" fill="#6B7280">n=0 (empty)</text>',
                ]
            )
        else:
            fv = float(raw_val)
            bh = max(1.0, (fv / y_max) * plot_h)
            by = top + plot_h - bh
            note = f"{fv:.2f}" + (f" (n={int(cnt_val)})" if cnt_val is not None else "")
            parts.extend(
                [
                    f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{color}" fill-opacity="0.88" stroke="#222222" stroke-width="0.8"/>',
                    f'<text x="{cx:.1f}" y="{by - 6:.1f}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="9.5" fill="#1F2937">{html.escape(note)}</text>',
                ]
            )
        short_lbl = lbl if len(lbl) <= 22 else lbl[:19] + "..."
        parts.append(
            f'<text x="{cx:.1f}" y="{top + plot_h + 18}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="10" fill="#1F2937">{html.escape(short_lbl)}</text>'
        )

    # Axis titles
    parts.append(
        f'<text x="{left + plot_w / 2:.1f}" y="{h - 18}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="11" fill="#374151">{html.escape(group_col)}</text>'
    )
    parts.append(
        f'<text x="20" y="{top + plot_h / 2:.1f}" text-anchor="middle" transform="rotate(-90 20 {top + plot_h / 2:.1f})" font-family="DejaVu Sans, Arial, sans-serif" font-size="11" fill="#374151">{html.escape(value_col)}</text>'
    )
    parts.append("</svg>")
    svg_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _try_render_matplotlib_charts(
    out_dir: Path,
    stem: str,
    *,
    title: str,
    group_col: str,
    value_col: str,
    records: list[dict[str, Any]],
    count_col: str | None,
) -> list[str]:
    """Optionally export 300-dpi PNG & PDF previews ONLY when Matplotlib is installed."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Liberation Sans"],
            "font.size": 8.5,
            "pdf.fonttype": 42,
        }
    )
    cats = [str(r.get(group_col, "")) for r in records]
    vals = [float(r[value_col]) if r.get(value_col) is not None else 0.0 for r in records]
    is_empty = [r.get(value_col) is None or (count_col in r and int(r[count_col]) == 0) for r in records]
    x = list(range(len(cats)))
    ymax = max([v for v, emp in zip(vals, is_empty) if not emp] or [1.0]) * 1.22

    fig, ax = plt.subplots(figsize=(5.6, 3.3), dpi=300)
    for xi, v, emp, rec in zip(x, vals, is_empty, records):
        if emp:
            ax.bar(xi, ymax * 0.04, width=0.52, color="none", edgecolor="#9CA3AF", linestyle="--", linewidth=1.0)
            ax.text(xi, ymax * 0.06, "n=0\n(empty)", ha="center", va="bottom", fontsize=7.0, color="#6B7280")
        else:
            col = CELL_PALETTE[xi % len(CELL_PALETTE)]
            ax.bar(xi, v, width=0.52, color=col, edgecolor="#222222", linewidth=0.75, alpha=0.88)
            cnt_txt = f"\n(n={int(rec[count_col])})" if count_col and count_col in rec else ""
            ax.text(xi, v + ymax * 0.02, f"{v:.2f}{cnt_txt}", ha="center", va="bottom", fontsize=7.0)

    ax.set_ylim(0, ymax)
    ax.set_xticks(x)
    ax.set_xticklabels(cats, rotation=12 if any(len(c) > 14 for c in cats) else 0)
    ax.set_xlabel(group_col)
    ax.set_ylabel(value_col)
    ax.set_title(title, loc="center", fontsize=9.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.grid(True, color="#E5E7EB", linewidth=0.5)
    ax.set_axisbelow(True)
    fig.tight_layout()

    png_p = out_dir / f"{stem}_chart.png"
    pdf_p = out_dir / f"{stem}_chart.pdf"
    fig.savefig(png_p, format="png", dpi=300)
    fig.savefig(pdf_p, format="pdf")
    plt.close(fig)
    return [png_p.name, pdf_p.name]


def run_sql_analysis(
    db_path: Path,
    query_spec: dict[str, Any],
    out_dir: Path,
    *,
    stem: str = "sql_analysis",
) -> dict[str, Any]:
    """Execute read-only SQL queries with EXPLAIN, inflation guard, empty-group triage, and Python reconciliation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    abs_db = db_path.resolve()
    sha_before = _sha256_file(abs_db)
    schema_rep = inspect_sqlite_db(abs_db)
    table_row_map = {t["table"]: t["row_count"] for t in schema_rep["tables"]}

    uri = abs_db.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")

    default_base_table = str(query_spec.get("base_table", "")).strip()
    default_base_pk = str(query_spec.get("base_pk", "")).strip()

    query_results: list[dict[str, Any]] = []
    errors: list[str] = []
    primary_records: list[dict[str, Any]] | None = None
    primary_count_col: str | None = None

    try:
        for q in query_spec.get("queries") or []:
            qid = str(q.get("id", "query"))
            sql = str(q.get("sql", "")).strip()
            params = q.get("params") or []
            q_base_table = str(q.get("base_table", default_base_table)).strip()
            q_base_pk = str(q.get("base_pk", default_base_pk)).strip()
            base_rows = table_row_map.get(q_base_table) if q_base_table else None
            allow_empty = bool(q.get("allow_empty_result", False))

            # 1. Read-only SQL fence
            ro_ok, ro_reason = _validate_read_only_sql(sql)
            if not ro_ok:
                msg = f"Query '{qid}' blocked by read-only SQL fence: {ro_reason}"
                errors.append(msg)
                query_results.append(
                    {
                        "id": qid,
                        "ok": False,
                        "blocked_by_readonly_fence": True,
                        "error": ro_reason,
                        "sql": sql,
                    }
                )
                continue

            # 2. Coded-integer mis-aggregation check
            coded_warns = _check_coded_misaggregation(sql, schema_rep)

            # 3. Capture EXPLAIN QUERY PLAN and execute query
            try:
                plan_rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
                query_plan = [{"id": r[0], "parent": r[1], "detail": str(r[3])} for r in plan_rows]
                cur = conn.execute(sql, params)
                col_names = [d[0] for d in (cur.description or [])]
                raw_rows = cur.fetchall()
            except Exception as exc:
                diag = _diagnose_sql_error(str(exc), schema_rep)
                errors.append(f"Query '{qid}' failed: {exc}")
                query_results.append(
                    {
                        "id": qid,
                        "ok": False,
                        "error": str(exc),
                        "self_diagnosis": diag,
                        "sql": sql,
                    }
                )
                continue

            records = [dict(zip(col_names, row)) for row in raw_rows]

            # 4. Identify count column (if any) & check JOIN cardinality inflation
            count_col = str(q.get("count_col", "")).strip() or next(
                (c for c in _COUNT_COL_CANDIDATES if c in col_names), ""
            )
            inflation_flag = False
            inflation_reasons: list[str] = []
            inflation_ratio: float | None = None
            distinct_base_pk: int | None = None
            sum_n_samples: int | None = None

            # Detail-level inflation check: returned_rows vs COUNT(DISTINCT base_pk)
            if q_base_pk and q_base_pk in col_names and len(records) > 0:
                pk_vals = [r[q_base_pk] for r in records if r[q_base_pk] is not None]
                distinct_base_pk = len(set(pk_vals))
                if len(pk_vals) > distinct_base_pk and distinct_base_pk > 0:
                    inflation_flag = True
                    inflation_ratio = round(len(pk_vals) / distinct_base_pk, 4)
                    inflation_reasons.append(
                        f"Detail rows ({len(pk_vals)}) exceed distinct {q_base_pk} ({distinct_base_pk}), ratio={inflation_ratio}x."
                    )

            # Aggregate-level inflation check: SUM(count_col) vs base_table_rows
            if count_col and count_col in col_names and records:
                non_null_cnts = [int(r[count_col]) for r in records if r[count_col] is not None]
                sum_n_samples = sum(non_null_cnts)
                if base_rows is not None and sum_n_samples > base_rows:
                    inflation_flag = True
                    inflation_ratio = round(sum_n_samples / base_rows, 4) if base_rows > 0 else None
                    inflation_reasons.append(
                        f"SUM({count_col})={sum_n_samples} exceeds base table '{q_base_table}' row_count={base_rows} (ratio={inflation_ratio}x)."
                    )

            # Optional explicit pre-aggregation probe SQL: SELECT COUNT(*), COUNT(DISTINCT base_pk) ...
            probe_sql = str(q.get("pre_agg_probe_sql", "")).strip()
            probe_info: dict[str, Any] | None = None
            if probe_sql and _validate_read_only_sql(probe_sql)[0]:
                pr = conn.execute(probe_sql).fetchone()
                if pr and len(pr) >= 2:
                    p_total, p_dist = int(pr[0]), int(pr[1])
                    probe_info = {"joined_rows": p_total, "distinct_base_pk": p_dist}
                    if p_dist > 0 and p_total > p_dist:
                        inflation_flag = True
                        inflation_ratio = round(p_total / p_dist, 4)
                        inflation_reasons.append(
                            f"Pre-agg probe detected {p_total} joined rows for {p_dist} distinct '{q_base_pk}' keys ({inflation_ratio}x)."
                        )

            cte_hint = (
                f"Pre-aggregate the 1:N child table in a CTE (WITH child_agg AS (SELECT {q_base_pk or '<join_key>'}, ... FROM <child_table> GROUP BY {q_base_pk or '<join_key>'})) before LEFT JOINing to '{q_base_table or '<base_table>'}'."
                if inflation_flag
                else None
            )
            if inflation_flag:
                errors.append(f"Query '{qid}' triggered JOIN inflation guard: {'; '.join(inflation_reasons)}")

            if coded_warns:
                errors.extend([f"Query '{qid}': {w}" for w in coded_warns])

            # 5. Empty-group vs anomalous empty-result triage
            empty_class = "NON_EMPTY_STANDARD"
            legitimate_empty_groups: list[dict[str, Any]] = []
            empty_diag: dict[str, Any] | None = None

            if len(records) == 0:
                if allow_empty:
                    empty_class = "EXPECTED_EMPTY_AUDIT"
                else:
                    empty_class = "ANOMALOUS_EMPTY_RESULT"
                    empty_diag = _diagnose_empty_query(conn, sql, schema_rep)
                    errors.append(f"Query '{qid}' returned 0 rows (ANOMALOUS_EMPTY_RESULT): {empty_diag['remediation']}")
            elif count_col and count_col in col_names:
                for rec in records:
                    if rec.get(count_col) is not None and int(rec[count_col]) == 0:
                        legitimate_empty_groups.append(rec)
                if legitimate_empty_groups:
                    empty_class = "LEGITIMATE_EMPTY_GROUP"

            # 6. Independent native Python recalculation reconciliation (if configured)
            reconcile_cfg = q.get("reconcile")
            reconcile_rep: dict[str, Any] = {"performed": False}
            if reconcile_cfg:
                reconcile_rep = _run_independent_python_reconciliation(conn, reconcile_cfg, records)
                if not reconcile_rep["passed"]:
                    errors.append(
                        f"Query '{qid}' failed independent Python reconciliation: {reconcile_rep['mismatches']}"
                    )

            # 7. Export verified query CSV + read-back SELECT-to-CSV column parity check
            csv_p = out_dir / f"{stem}_{qid}.csv"
            with csv_p.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=col_names)
                writer.writeheader()
                for rec in records:
                    writer.writerow({k: ("" if v is None else v) for k, v in rec.items()})

            with csv_p.open("r", encoding="utf-8", newline="") as rfh:
                csv_header = next(csv.reader(rfh), [])
            missing_in_csv = [c for c in col_names if c not in csv_header]
            select_csv_parity_ok = len(missing_in_csv) == 0
            if not select_csv_parity_ok:
                errors.append(f"Query '{qid}' SELECT-to-CSV column mismatch: missing {missing_in_csv}")

            # 7b. Export per-group dispersion & 95% t-CI table when Python reconciliation computed it
            uncertainty_csv_name: str | None = None
            u_rows = reconcile_rep.get("group_uncertainty_summary") or []
            if u_rows:
                u_csv = out_dir / f"{stem}_{qid}_uncertainty.csv"
                u_cols = list(dict.fromkeys(k for r in u_rows for k in r.keys()))
                with u_csv.open("w", encoding="utf-8", newline="") as ufh:
                    uw = csv.DictWriter(ufh, fieldnames=u_cols)
                    uw.writeheader()
                    for ur in u_rows:
                        uw.writerow({k: ("" if ur.get(k) is None else ur.get(k)) for k in u_cols})
                uncertainty_csv_name = u_csv.name

            q_ok = (
                not inflation_flag
                and not coded_warns
                and empty_class != "ANOMALOUS_EMPTY_RESULT"
                and select_csv_parity_ok
                and (not reconcile_rep["performed"] or reconcile_rep["passed"])
            )

            if primary_records is None and records:
                primary_records = records
                primary_count_col = count_col or None

            query_results.append(
                {
                    "id": qid,
                    "ok": q_ok,
                    "row_count": len(records),
                    "columns": col_names,
                    "select_csv_column_parity_ok": select_csv_parity_ok,
                    "base_table": q_base_table or None,
                    "base_pk": q_base_pk or None,
                    "base_table_rows": base_rows,
                    "distinct_base_pk": distinct_base_pk,
                    "sum_n_samples": sum_n_samples,
                    "join_inflation_detected": inflation_flag,
                    "inflation_ratio": inflation_ratio,
                    "inflation_reasons": inflation_reasons,
                    "cte_rewrite_hint": cte_hint,
                    "pre_agg_probe": probe_info,
                    "coded_misaggregation_warnings": coded_warns,
                    "empty_set_classification": empty_class,
                    "legitimate_empty_groups": legitimate_empty_groups,
                    "empty_query_diagnosis": empty_diag,
                    "explain_query_plan": query_plan,
                    "python_reconciliation": reconcile_rep,
                    "sql": sql,
                    "csv_artifact": csv_p.name,
                    "uncertainty_csv_artifact": uncertainty_csv_name,
                    "records": records,
                }
            )
    finally:
        conn.close()

    sha_after = _sha256_file(abs_db)
    immutable_ok = sha_before == sha_after
    if not immutable_ok:
        errors.append("CRITICAL: SQLite database SHA-256 changed during read-only execution!")

    # Render pure-stdlib SVG chart per query (plus optional Matplotlib PNG/PDF for primary)
    group_col = str(query_spec.get("group_col", "")).strip()
    value_col = str(query_spec.get("value_col", "")).strip()
    title = str(query_spec.get("title", f"{value_col} by {group_col}"))
    chart_files: list[str] = []
    matplotlib_rendered = False

    for q_idx, qr in enumerate(query_results):
        q_recs = qr.get("records") or []
        if not q_recs:
            continue
        q_gcol = group_col if group_col in q_recs[0] else next((c for c in qr.get("columns", []) if isinstance(q_recs[0].get(c), str)), "")
        q_vcol = value_col if value_col in q_recs[0] else next((c for c in qr.get("columns", []) if c != q_gcol and isinstance(q_recs[0].get(c), (int, float))), "")
        q_cnt = next((c for c in _COUNT_COL_CANDIDATES if c in q_recs[0]), None)
        if q_gcol and q_vcol:
            q_svg = out_dir / (f"{stem}_chart.svg" if q_idx == 0 else f"{stem}_{qr['id']}_chart.svg")
            _render_stdlib_svg_chart(
                q_svg,
                title=title if q_idx == 0 else f"{qr['id']}: {q_vcol} by {q_gcol}",
                group_col=q_gcol,
                value_col=q_vcol,
                records=q_recs,
                count_col=q_cnt,
            )
            chart_files.append(q_svg.name)
            if q_idx == 0:
                extra_charts = _try_render_matplotlib_charts(
                    out_dir,
                    stem,
                    title=title,
                    group_col=q_gcol,
                    value_col=q_vcol,
                    records=q_recs,
                    count_col=q_cnt,
                )
                if extra_charts:
                    matplotlib_rendered = True
                    chart_files.extend(extra_charts)

    rep_payload = {
        "ok": len(errors) == 0 and immutable_ok and all(q.get("ok", False) for q in query_results),
        "db_file": db_path.name,
        "db_sha256_before": sha_before,
        "db_sha256_after": sha_after,
        "db_immutability_verified": immutable_ok,
        "sqlite_version": sqlite3.sqlite_version,
        "base_table": default_base_table or None,
        "base_pk": default_base_pk or None,
        "base_table_rows": table_row_map.get(default_base_table) if default_base_table else None,
        "queries": query_results,
        "chart_artifacts": chart_files,
        "matplotlib_rendered": matplotlib_rendered,
        "errors": errors,
    }

    rep_p = out_dir / f"{stem}_report.json"
    md_p = out_dir / f"{stem}_report.md"
    rep_p.write_text(json.dumps(rep_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md_lines = [
        f"# Read-Only SQLite Cross-Table Report (`{db_path.name}`)",
        "",
        f"- **Overall OK**: `{rep_payload['ok']}` | **DB Immutability Verified**: `{immutable_ok}` (`sha256={sha_before[:16]}...`)",
        f"- **SQLite Version**: `{sqlite3.sqlite_version}` | **Charts**: `{', '.join(chart_files) or 'none'}` (Matplotlib: `{matplotlib_rendered}`)",
        "",
    ]
    for qr in query_results:
        rec_info = qr.get("python_reconciliation") or {}
        rec_txt = (
            f"PASS (max_abs_diff={rec_info.get('max_abs_diff', 0.0):.2e})"
            if rec_info.get("performed") and rec_info.get("passed")
            else ("FAIL" if rec_info.get("performed") else "not_configured")
        )
        md_lines.extend(
            [
                f"## Query `{qr['id']}` (`ok={qr.get('ok')}`)",
                "",
                f"- Rows: `{qr.get('row_count')}` | Inflation: `{qr.get('join_inflation_detected')}` | Empty Class: `{qr.get('empty_set_classification')}` | Python Reconcile: `{rec_txt}`",
                "",
                "```sql",
                qr.get("sql", ""),
                "```",
                "",
            ]
        )
        if qr.get("cte_rewrite_hint"):
            md_lines.append(f"> **CTE Rewrite Hint**: {qr['cte_rewrite_hint']}")
            md_lines.append("")
    md_p.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return rep_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute read-only SQLite cross-table queries with JOIN guard and native Python reconciliation.")
    parser.add_argument("--db", required=True, help="Path to SQLite database snapshot.")
    parser.add_argument("--query-spec", required=True, help="Path to JSON query specification.")
    parser.add_argument("--out-dir", required=True, help="Output directory for reports, CSVs, and charts.")
    parser.add_argument("--stem", default="sql_analysis", help="Filename prefix for generated artifacts.")
    args = parser.parse_args(argv)

    spec = json.loads(Path(args.query_spec).read_text(encoding="utf-8-sig"))
    rep = run_sql_analysis(Path(args.db), spec, Path(args.out_dir), stem=args.stem)
    print(
        json.dumps(
            {
                "ok": rep["ok"],
                "db_immutability_verified": rep["db_immutability_verified"],
                "query_statuses": [
                    {
                        "id": q["id"],
                        "ok": q.get("ok"),
                        "rows": q.get("row_count"),
                        "join_inflation_detected": q.get("join_inflation_detected"),
                        "empty_set_classification": q.get("empty_set_classification"),
                        "python_reconcile_passed": (q.get("python_reconciliation") or {}).get("passed"),
                    }
                    for q in rep["queries"]
                ],
                "chart_artifacts": rep["chart_artifacts"],
                "errors": rep["errors"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
