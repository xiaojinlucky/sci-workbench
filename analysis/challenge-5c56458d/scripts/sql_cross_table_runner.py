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


def _run_independent_python_reconciliation(
    conn: sqlite3.Connection,
    reconcile_cfg: dict[str, Any],
    sql_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Independently load raw tables in Python, perform grouping/pre-agg, and verify max_abs_diff < 1e-9."""
    base_table = str(reconcile_cfg["base_table"])
    group_col_sql = str(reconcile_cfg["sql_group_col"])
    count_col_sql = str(reconcile_cfg.get("sql_count_col", "n_samples"))
    metrics_cfg: list[dict[str, Any]] = list(reconcile_cfg.get("metrics") or [])

    # Load base rows as dicts
    cur = conn.execute(f"SELECT * FROM {_qident(base_table)}")
    base_cols = [d[0] for d in cur.description]
    base_rows = [dict(zip(base_cols, row)) for row in cur.fetchall()]

    # Optional 1:N child pre-aggregation in native Python (grouped by base_pk)
    child_pre_agg = reconcile_cfg.get("child_pre_agg")
    child_by_pk: dict[Any, dict[str, Any]] = {}
    if child_pre_agg:
        ct = str(child_pre_agg["table"])
        join_key = str(child_pre_agg["join_key"])
        c_cur = conn.execute(f"SELECT * FROM {_qident(ct)}")
        c_cols = [d[0] for d in c_cur.description]
        c_grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for r in c_cur.fetchall():
            rec = dict(zip(c_cols, r))
            c_grouped[rec[join_key]].append(rec)
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

    # Compute independent Python aggregates per group
    py_by_group: dict[str, dict[str, Any]] = {}
    for gkey, rows in group_rows.items():
        g_str = str(gkey)
        entry: dict[str, Any] = {count_col_sql: len(rows)}
        for m in metrics_cfg:
            sql_col = str(m["sql_col"])
            src_col = str(m["source_col"])
            agg = str(m["agg"]).lower()
            vals = [float(r[src_col]) for r in rows if r.get(src_col) is not None]
            if agg == "mean":
                entry[sql_col] = statistics.fmean(vals) if vals else None
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

    passed = len(mismatches) == 0 and max_abs_diff < 1e-9 and checked_pairs > 0
    return {
        "performed": True,
        "passed": passed,
        "tolerance": 1e-9,
        "checked_comparisons": checked_pairs,
        "max_abs_diff": max_abs_diff,
        "mismatches": mismatches,
        "python_reference_by_group": py_by_group,
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

            # 7. Export verified query CSV
            csv_p = out_dir / f"{stem}_{qid}.csv"
            with csv_p.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=col_names)
                writer.writeheader()
                for rec in records:
                    writer.writerow({k: ("" if v is None else v) for k, v in rec.items()})

            q_ok = (
                not inflation_flag
                and not coded_warns
                and empty_class != "ANOMALOUS_EMPTY_RESULT"
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
                    "records": records,
                }
            )
    finally:
        conn.close()

    sha_after = _sha256_file(abs_db)
    immutable_ok = sha_before == sha_after
    if not immutable_ok:
        errors.append("CRITICAL: SQLite database SHA-256 changed during read-only execution!")

    # Render pure-stdlib SVG chart (and optional Matplotlib PNG/PDF if installed)
    group_col = str(query_spec.get("group_col", "")).strip()
    value_col = str(query_spec.get("value_col", "")).strip()
    title = str(query_spec.get("title", f"{value_col} by {group_col}"))
    chart_files: list[str] = []
    matplotlib_rendered = False

    if primary_records and group_col and value_col and group_col in primary_records[0] and value_col in primary_records[0]:
        svg_p = out_dir / f"{stem}_chart.svg"
        _render_stdlib_svg_chart(
            svg_p,
            title=title,
            group_col=group_col,
            value_col=value_col,
            records=primary_records,
            count_col=primary_count_col,
        )
        chart_files.append(svg_p.name)
        extra_charts = _try_render_matplotlib_charts(
            out_dir,
            stem,
            title=title,
            group_col=group_col,
            value_col=value_col,
            records=primary_records,
            count_col=primary_count_col,
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
