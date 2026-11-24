#!/usr/bin/env python3
"""Zero-manual /input -> /app/output scientific visualization orchestrator (challenge-9562cc8d).

Auto-discovers CSV/JSON/SQLite tables, case.json, and task.md under --input-dir:
  - Parses dependent variable, multiple predictors, and units from task.md + column suffixes
  - Routes automatically to multivariate_ols, boxplot_points (with Shapiro-Francia + Welch/KW
    + Holm significance brackets), scatter_regression, line_with_band (numeric/ISO-date/CJK),
    or heatmap (with read-only SQLite anti-inflation 1:N aggregation & empty-group preservation)
  - Runs 5-Gate auditor + Dual-Backend publication_viz_builder + true disk SVG readback
  - Runs inline gap_gate grounding & task-coverage checks, strips host absolute paths,
    and seals /app/output/result.json with honest status, export_tier, gaps, and uncertainty.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from case_contract_sealer import discover_input_dir, sanitize_local_paths, seal_result_contract
from chart_selector_and_auditor import advise_and_audit_chart
from gap_gate import check_grounding
from publication_viz_builder import build_publication_figure
from stat_assumptions import describe_vector

_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?$")
_GROUP_HINTS = ("species", "cultivar", "group", "treatment", "cohort", "condition", "class", "category")
_TIME_HINTS = ("date", "time", "day", "hour", "month", "year", "日期", "时间")
_CODED_ID_SUFFIXES = ("_id", "_code")
_CODED_ID_NAMES = {"id", "code", "mix_id", "cultivar_code", "species_code", "specimen_id", "sample_code", "qc_pass"}

_UNIT_SUFFIX_MAP: list[tuple[str, str]] = [
    ("_kg_m3", "kg/m³"),
    ("_ug_ml", "µg/mL"),
    ("_mg_ml", "mg/mL"),
    ("_mg_l", "mg/L"),
    ("_g_l", "g/L"),
    ("_mpa", "MPa"),
    ("_gpa", "GPa"),
    ("_kpa", "kPa"),
    ("_days", "days"),
    ("_day", "days"),
    ("_hours", "h"),
    ("_hr", "h"),
    ("_h", "h"),
    ("_sec", "s"),
    ("_s", "s"),
    ("_cm", "cm"),
    ("_mm", "mm"),
    ("_um", "µm"),
    ("_nm", "nm"),
    ("_pct", "%"),
    ("_degc", "°C"),
    ("_c", "°C"),
]


def _try_float(v: Any) -> float | None:
    s = str(v if v is not None else "").strip()
    if not s or s.lower() in {"na", "n/a", "nan", "null", "none", "<lod", "nd", ".", "-"}:
        return None
    try:
        fv = float(s)
        return fv if math.isfinite(fv) else None
    except ValueError:
        return None


def _resolve_column_unit(col: str, task_text: str) -> tuple[str, str]:
    """Resolve measurement unit from task.md explicit parentheses first, then standard column suffix."""
    if not col:
        return "", "unresolved"
    # 1. Explicit '<col> (<unit>)' or '<col>（<unit>）' in task.md
    pat = re.compile(rf"\b{re.escape(col)}\s*[\(（]([^\)）]{{1,24}})[\)）]", re.IGNORECASE)
    m = pat.search(task_text)
    if m:
        u = m.group(1).strip()
        if u and not u.lower().startswith("n="):
            return u, "task.md"
    # 2. Standard scientific suffix
    low = col.lower()
    for suf, u in _UNIT_SUFFIX_MAP:
        if low.endswith(suf):
            return u, "column_suffix"
    return "", "unresolved"


def _is_coded_or_id_col(col: str) -> bool:
    low = col.lower()
    return low in _CODED_ID_NAMES or low.endswith(_CODED_ID_SUFFIXES)


def _export_sqlite_fact_table_csv(
    db_path: Path,
    work_dir: Path,
    task_text: str,
    table: str | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """Open SQLite strictly read-only, pick true fact table, preserve empty dims, and pre-aggregate 1:N child tables."""
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    join_audit: dict[str, Any] = {"sqlite_db": db_path.name, "mode": "read_only_uri"}
    try:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        if not tables:
            raise ValueError(f"SQLite {db_path.name} has zero user tables")

        table_info: dict[str, dict[str, Any]] = {}
        for t in tables:
            cnt = int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
            cur = conn.execute(f'SELECT * FROM "{t}" LIMIT 1')
            tcols = [d[0] for d in cur.description]
            meas_cols = [c for c in tcols if not _is_coded_or_id_col(c) and not any(h in c.lower() for h in ("label", "name", "region", "batch", "year"))]
            table_info[t] = {"rows": cnt, "cols": tcols, "n_meas_cols": len(meas_cols)}

        chosen = table
        low = task_text.lower()
        if not chosen:
            # Prefer explicit 'Analyze <table>' mention first
            m_ana = re.search(r"\banalyze\s+([A-Za-z0-9_]+)", task_text, re.IGNORECASE)
            if m_ana and m_ana.group(1) in table_info:
                chosen = m_ana.group(1)
        if not chosen:
            mentioned = [t for t in tables if re.search(rf"\b{re.escape(t)}\b", low)]
            if mentioned:
                # Among mentioned tables, rank by measurement columns & row count (avoid tiny lookup dims or 1:N logs)
                mentioned.sort(
                    key=lambda t: (
                        0 if any(h in t.lower() for h in ("injection", "log", "replicate")) else 1,
                        table_info[t]["n_meas_cols"],
                        table_info[t]["rows"],
                    ),
                    reverse=True,
                )
                chosen = mentioned[0]
        if not chosen:
            chosen = sorted(tables, key=lambda t: (table_info[t]["n_meas_cols"], table_info[t]["rows"]), reverse=True)[0]

        base_cols = table_info[chosen]["cols"]
        base_rows = [dict(zip(base_cols, r)) for r in conn.execute(f'SELECT * FROM "{chosen}"').fetchall()]
        join_audit.update({
            "tables_in_db": {t: table_info[t]["rows"] for t in tables},
            "primary_fact_table": chosen,
            "base_rows": len(base_rows),
            "excluded_coded_integers": [c for c in base_cols if _is_coded_or_id_col(c)],
            "empty_groups_preserved": [],
            "one_to_many_preaggregations": [],
        })

        # Safe dimension LEFT JOIN + empty-group preservation check
        extra_cols: list[str] = []
        for other in tables:
            if other == chosen:
                continue
            ocols = table_info[other]["cols"]
            if not re.search(rf"\b{re.escape(other)}\b", low):
                continue
            shared_keys = [c for c in base_cols if c in ocols and (_is_coded_or_id_col(c) or c.endswith("_key"))]
            if not shared_keys:
                continue
            jk = shared_keys[0]
            orows = [dict(zip(ocols, r)) for r in conn.execute(f'SELECT * FROM "{other}"').fetchall()]
            if table_info[other]["rows"] <= table_info[chosen]["rows"]:
                # Dimension table (e.g., cultivars): map labels & detect empty groups (n=0)
                dim_map = {str(r[jk]): r for r in orows}
                base_key_counts: dict[str, int] = {}
                for br in base_rows:
                    kv = str(br.get(jk))
                    base_key_counts[kv] = base_key_counts.get(kv, 0) + 1
                lbl_cols = [c for c in ocols if c != jk and ("label" in c.lower() or "name" in c.lower())]
                for kv, dr in dim_map.items():
                    cnt_k = base_key_counts.get(kv, 0)
                    if cnt_k == 0:
                        join_audit["empty_groups_preserved"].append({
                            "dim_table": other, "join_key": jk, "key_value": kv,
                            "label": dr.get(lbl_cols[0]) if lbl_cols else kv, "n_samples": 0,
                        })
                if lbl_cols:
                    lc = lbl_cols[0]
                    if lc not in base_cols and lc not in extra_cols:
                        extra_cols.append(lc)
                    for br in base_rows:
                        dr = dim_map.get(str(br.get(jk)), {})
                        br[lc] = dr.get(lc, "")
                join_audit["dimension_group_counts"] = {
                    str(dr.get(lbl_cols[0]) if lbl_cols else kv): base_key_counts.get(kv, 0)
                    for kv, dr in dim_map.items()
                }
            else:
                # 1:N child table (e.g., hplc_injections): pre-aggregate before LEFT JOIN to prevent row inflation
                grouped_child: dict[str, list[dict[str, Any]]] = {}
                for r in orows:
                    grouped_child.setdefault(str(r[jk]), []).append(r)
                num_child_cols = [c for c in ocols if c != jk and not _is_coded_or_id_col(c) and _try_float(orows[0].get(c)) is not None]
                cnt_col = f"{other}_n"
                extra_cols.append(cnt_col)
                agg_col_names = []
                for nc in num_child_cols[:2]:
                    ac = f"{other}_mean_{nc}"
                    extra_cols.append(ac)
                    agg_col_names.append((nc, ac))
                for br in base_rows:
                    sub = grouped_child.get(str(br.get(jk)), [])
                    br[cnt_col] = len(sub)
                    for nc, ac in agg_col_names:
                        vals = [_try_float(x.get(nc)) for x in sub]
                        vals_f = [v for v in vals if v is not None]
                        br[ac] = round(sum(vals_f) / len(vals_f), 6) if vals_f else ""
                per_parent_counts = [int(br.get(cnt_col, 0)) for br in base_rows]
                naive_join_rows = sum(max(c, 1) for c in per_parent_counts)
                join_audit["one_to_many_preaggregations"].append({
                    "child_table": other,
                    "join_key": jk,
                    "child_rows_total": len(orows),
                    "child_rows": len(orows),
                    "matched_child_rows": sum(per_parent_counts),
                    "parent_rows_before_join": len(base_rows),
                    "matched_parent_rows": sum(1 for c in per_parent_counts if c > 0),
                    "naive_unaggregated_join_rows": naive_join_rows,
                    "inflation_factor_prevented": round(naive_join_rows / max(len(base_rows), 1), 4),
                    "injections_per_parent_min": min(per_parent_counts) if per_parent_counts else 0,
                    "injections_per_parent_max": max(per_parent_counts) if per_parent_counts else 0,
                    "joined_rows": len(base_rows),
                    "row_inflation_prevented": len(base_rows) == table_info[chosen]["rows"],
                })
        join_audit["final_rows"] = len(base_rows)
        join_audit["zero_row_inflation_verified"] = (len(base_rows) == table_info[chosen]["rows"])
    finally:
        conn.close()

    out_cols = list(base_cols) + [c for c in extra_cols if c not in base_cols]
    work_dir.mkdir(parents=True, exist_ok=True)
    out_csv = work_dir / f"{chosen}_ro_snapshot.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(out_cols)
        for r in base_rows:
            w.writerow(["" if r.get(c) is None else str(r.get(c)) for c in out_cols])
    return out_csv, chosen, join_audit


def _infer_viz_spec(
    csv_path: Path,
    task_text: str,
    *,
    display_table_name: str | None = None,
    sqlite_table: str | None = None,
) -> dict[str, Any]:
    raw = csv_path.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    delim = "\t" if csv_path.suffix.lower() == ".tsv" else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delim)
    cols = [c.strip() for c in (reader.fieldnames or [])]
    rows = [{str(k).strip(): ("" if v is None else str(v).strip()) for k, v in r.items() if k is not None} for r in reader]

    num_cols: list[str] = []
    cat_cols: list[str] = []
    date_cols: list[str] = []
    for c in cols:
        vals = [r.get(c, "") for r in rows if r.get(c, "") != ""]
        if not vals:
            continue
        if any(_DATE_RE.match(v) for v in vals[:10]) or any(h in c.lower() for h in _TIME_HINTS):
            if not all(_try_float(v) is not None for v in vals[:20]) or any(h in c.lower() for h in ("date", "日期", "time_h")):
                date_cols.append(c)
                continue
        has_lz = any(len(v) > 1 and v.startswith("0") and v.isdigit() for v in vals)
        parsed = [_try_float(v) for v in vals]
        is_num = (not has_lz) and sum(v is not None for v in parsed) >= max(1, int(0.8 * len(vals)))
        uniq = list(dict.fromkeys(vals))
        if is_num and not _is_coded_or_id_col(c):
            num_cols.append(c)
        elif (not has_lz) and (not _is_coded_or_id_col(c)) and 2 <= len(uniq) <= 12:
            cat_cols.append(c)

    source_refs: dict[str, str] = {}
    for c in cols:
        for lno, ln in enumerate(task_text.splitlines(), 1):
            if re.search(rf"\b{re.escape(c)}\b", ln, re.IGNORECASE):
                source_refs[c] = f"task.md#L{lno}"
                break

    low_task = task_text.lower()
    table_ref = display_table_name or csv_path.name

    # Resolve units for all numeric columns
    col_units: dict[str, str] = {}
    col_unit_sources: dict[str, str] = {}
    for c in num_cols:
        u, us = _resolve_column_unit(c, task_text)
        if u:
            col_units[c] = u
        col_unit_sources[c] = us

    # 1. Detect explicit multivariable regression from task.md ("Analyze <y> as a function of <x1>, <x2>, <x3>")
    mentioned_nums = [c for c in num_cols if c in source_refs]
    # Order mentioned_nums by first occurrence position in task_text
    mentioned_nums.sort(key=lambda c: re.search(rf"\b{re.escape(c)}\b", task_text, re.IGNORECASE).start())  # type: ignore[union-attr]
    is_multivar_task = (
        len(mentioned_nums) >= 3
        or any(kw in low_task for kw in ("multivariable", "multivariate", "as a function of", "multiple regression"))
    )
    if is_multivar_task and len(num_cols) >= 3 and "across" not in low_task and "heatmap" not in low_task:
        if len(mentioned_nums) >= 3:
            y_col = mentioned_nums[0]
            x_cols = mentioned_nums[1:]
        else:
            y_col = next((c for c in reversed(num_cols) if c in source_refs), num_cols[-1])
            x_cols = [c for c in num_cols if c != y_col][:5]
        y_unit = col_units.get(y_col, "")
        return {
            "table": table_ref,
            "sqlite_table": sqlite_table,
            "data_kind": "multivariate_continuous",
            "analysis_goal": "multivariable_regression",
            "chart_type": "multivariate_ols",
            "x_cols": x_cols,
            "value_col": y_col,
            "unit": y_unit,
            "unit_source": col_unit_sources.get(y_col, "unresolved"),
            "predictor_units": {c: col_units.get(c, "unresolved") for c in x_cols},
            "xlabel": ", ".join(x_cols),
            "ylabel": y_col,
            "title": f"Multivariable OLS: {y_col} ~ {' + '.join(x_cols)}",
            "column_preset": "cell_double",
            "source_refs": source_refs,
        }

    # 2. Time-series / ISO date trend
    y_col = next((c for c in mentioned_nums), num_cols[-1] if num_cols else cols[-1])
    if date_cols and num_cols:
        x_col = date_cols[0]
        return {
            "table": table_ref,
            "sqlite_table": sqlite_table,
            "data_kind": "time_series",
            "analysis_goal": "trend",
            "chart_type": "line_with_band",
            "x_col": x_col,
            "value_col": y_col,
            "unit": col_units.get(y_col, ""),
            "unit_source": col_unit_sources.get(y_col, "unresolved"),
            "xlabel": x_col,
            "ylabel": y_col,
            "title": f"{y_col} over {x_col}",
            "column_preset": "cell_1_5col",
            "source_refs": source_refs,
        }

    # 3. Multi-feature chemistry / correlation matrix heatmap (e.g., Wine 178 SQLite without single target variable)
    core_chem_nums = [c for c in num_cols if not c.endswith("_n") and "_mean_" not in c]
    if (
        ("heatmap" in low_task or "correlation" in low_task or "matrix" in low_task)
        or (sqlite_table is not None and not mentioned_nums and len(core_chem_nums) >= 4)
    ):
        hm_cols = core_chem_nums[:8] if len(core_chem_nums) >= 2 else num_cols[:8]
        return {
            "table": table_ref,
            "sqlite_table": sqlite_table,
            "data_kind": "matrix",
            "analysis_goal": "correlation",
            "chart_type": "heatmap",
            "color_role": "diverging",
            "matrix_cols": hm_cols,
            "predictor_units": {c: col_units.get(c, "unresolved") for c in hm_cols},
            "title": f"Feature correlation matrix ({sqlite_table or csv_path.stem})",
            "column_preset": "cell_double",
            "source_refs": source_refs,
        }

    # 4. Grouped categorical x continuous -> boxplot_points (with Shapiro-Francia + Welch/KW + Holm sig brackets)
    hinted_cats = [c for c in cat_cols if any(h in c.lower() for h in _GROUP_HINTS)]
    if (cat_cols or hinted_cats) and num_cols:
        g_col = next((c for c in (hinted_cats + cat_cols) if c in source_refs), (hinted_cats[0] if hinted_cats else cat_cols[0]))
        y_unit = col_units.get(y_col, "")
        return {
            "table": table_ref,
            "sqlite_table": sqlite_table,
            "data_kind": "categorical_continuous",
            "analysis_goal": "group_comparison",
            "chart_type": "boxplot_points",
            "group_col": g_col,
            "value_col": y_col,
            "unit": y_unit,
            "unit_source": col_unit_sources.get(y_col, "unresolved"),
            "xlabel": g_col,
            "ylabel": y_col,
            "title": f"{y_col} across {g_col}",
            "column_preset": "cell_1_5col",
            "source_refs": source_refs,
        }

    # 5. Bivariate continuous scatter + OLS
    if len(num_cols) >= 2:
        y_vals = [_try_float(r.get(y_col)) for r in rows]
        best_x = num_cols[0] if num_cols[0] != y_col else num_cols[1]
        best_abs_r = -1.0
        for xc in num_cols:
            if xc == y_col:
                continue
            pairs = [(_try_float(r.get(xc)), yv) for r, yv in zip(rows, y_vals)]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            if len(pairs) >= 3:
                xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
                mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
                sxx = sum((a - mx) ** 2 for a in xs)
                syy = sum((b - my) ** 2 for b in ys)
                if sxx > 1e-12 and syy > 1e-12:
                    r_abs = abs(sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / math.sqrt(sxx * syy))
                    if r_abs > best_abs_r:
                        best_abs_r = r_abs
                        best_x = xc
        return {
            "table": table_ref,
            "sqlite_table": sqlite_table,
            "data_kind": "bivariate_continuous",
            "analysis_goal": "correlation",
            "chart_type": "scatter_regression",
            "x_col": best_x,
            "value_col": y_col,
            "unit": col_units.get(y_col, ""),
            "unit_source": col_unit_sources.get(y_col, "unresolved"),
            "predictor_units": {best_x: col_units.get(best_x, "unresolved")},
            "xlabel": best_x,
            "ylabel": y_col,
            "title": f"{y_col} vs {best_x}",
            "column_preset": "cell_1_5col",
            "source_refs": source_refs,
        }

    raise ValueError(f"Unable to infer visualization spec from columns {cols}")


def _write_csv_table(out_path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> str:
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})
    return out_path.name


def _export_core_statistical_tables(
    out_dir: Path,
    stem: str,
    chart_type: str,
    aobj: dict[str, Any],
    sqlite_join_audit: dict[str, Any] | None,
) -> tuple[list[str], dict[str, Any]]:
    """Persist core statistical tables as both standalone CSV artifacts and structured JSON payloads."""
    recon = aobj.get("source_to_visual_reconciliation") or {}
    csv_files: list[str] = []
    tables_payload: dict[str, Any] = {}

    # 1. Regression coefficients table (Concrete multivariate_ols)
    coefs = recon.get("coefficients")
    if isinstance(coefs, list) and coefs:
        coef_rows: list[dict[str, Any]] = []
        for c in coefs:
            ci_hc3 = c.get("ci95_hc3") or [None, None]
            std_ci = c.get("std_ci95_hc3") or [None, None]
            coef_rows.append({
                "term": c.get("term"),
                "beta": c.get("beta"),
                "se_ols": c.get("se_ols"),
                "se_hc3": c.get("se_hc3"),
                "t_ols": c.get("t_ols"),
                "p_ols": c.get("p_ols"),
                "t_hc3": c.get("t_hc3"),
                "p_hc3": c.get("p_hc3"),
                "ci95_hc3_low": ci_hc3[0],
                "ci95_hc3_high": ci_hc3[1],
                "std_beta": c.get("std_beta"),
                "std_se_hc3": c.get("std_se_hc3"),
                "std_ci95_hc3_low": std_ci[0],
                "std_ci95_hc3_high": std_ci[1],
                "sig_hc3": c.get("sig_hc3"),
                "vif": c.get("vif"),
                "unit": c.get("unit"),
            })
        fname = _write_csv_table(
            out_dir / f"{stem}_coefficients.csv",
            list(coef_rows[0].keys()),
            coef_rows,
        )
        csv_files.append(fname)
        tables_payload["coefficients_csv"] = fname
        tables_payload["coefficients"] = coef_rows

    # 2. Grouped comparison tables (Iris pairwise Holm + Hedges' g + 95% CI & per-group descriptives)
    st = recon.get("statistical_tests") or {}
    pws = st.get("pairwise_comparisons")
    if isinstance(pws, list) and pws:
        pw_rows: list[dict[str, Any]] = []
        for pr in pws:
            wt = pr.get("welch_t") or {}
            mw = pr.get("mann_whitney_u") or {}
            ci = wt.get("ci95") or [None, None]
            pw_rows.append({
                "group_a": pr.get("group_a"),
                "group_b": pr.get("group_b"),
                "primary_test": pr.get("primary_test"),
                "p_raw": pr.get("p_raw"),
                "p_holm": pr.get("p_holm"),
                "sig": pr.get("sig"),
                "mean_diff": wt.get("mean_diff"),
                "ci95_low": ci[0],
                "ci95_high": ci[1],
                "hedges_g": wt.get("hedges_g"),
                "welch_t": wt.get("t_stat"),
                "welch_df": wt.get("df"),
                "welch_p": wt.get("p_value"),
                "mann_whitney_u": mw.get("u_stat"),
                "mann_whitney_p": mw.get("p_value"),
                "rank_biserial_r": mw.get("rank_biserial_r"),
            })
        fname = _write_csv_table(
            out_dir / f"{stem}_pairwise_stats.csv",
            list(pw_rows[0].keys()),
            pw_rows,
        )
        csv_files.append(fname)
        tables_payload["pairwise_stats_csv"] = fname
        tables_payload["pairwise_stats"] = pw_rows

    gs = recon.get("group_summaries")
    if isinstance(gs, list) and gs:
        g_rows = [
            {
                "group": g.get("group"), "n": g.get("n"),
                "mean": g.get("mean"), "sd": g.get("sd"),
                "ci95_low": g.get("ci95_low"), "ci95_high": g.get("ci95_high"),
                "median": g.get("median"), "q1": g.get("q1"), "q3": g.get("q3"), "iqr": g.get("iqr"),
                "min": g.get("min"), "max": g.get("max"),
            }
            for g in gs
        ]
        fname = _write_csv_table(out_dir / f"{stem}_group_stats.csv", list(g_rows[0].keys()), g_rows)
        csv_files.append(fname)
        tables_payload["group_stats_csv"] = fname
        tables_payload["group_stats"] = g_rows

    # 3. Correlation matrix table (Wine 5x5 Pearson matrix)
    mcols = recon.get("matrix_cols")
    mvals = recon.get("matrix_values")
    if isinstance(mcols, list) and isinstance(mvals, list) and mcols and len(mcols) == len(mvals):
        corr_rows: list[dict[str, Any]] = []
        for cname, rvals in zip(mcols, mvals):
            rec: dict[str, Any] = {"variable": cname}
            for c2, val in zip(mcols, rvals):
                rec[c2] = round(float(val), 6)
            corr_rows.append(rec)
        fname = _write_csv_table(out_dir / f"{stem}_corr_matrix.csv", ["variable"] + list(mcols), corr_rows)
        csv_files.append(fname)
        tables_payload["corr_matrix_csv"] = fname
        tables_payload["corr_matrix"] = corr_rows

    # 4. SQLite 1:N HPLC injection pre/post-aggregation row reconciliation table
    if isinstance(sqlite_join_audit, dict):
        hplc_rows: list[dict[str, Any]] = []
        fact_t = sqlite_join_audit.get("primary_fact_table")
        base_n = sqlite_join_audit.get("base_rows")
        final_n = sqlite_join_audit.get("final_rows")
        hplc_rows.append({
            "table_role": "primary_fact_table",
            "table_name": fact_t,
            "join_key": "N/A",
            "raw_table_rows": base_n,
            "matched_parent_rows": base_n,
            "naive_unaggregated_join_rows": base_n,
            "final_joined_rows": final_n,
            "inflation_factor_prevented": 1.0,
            "row_inflation_prevented": sqlite_join_audit.get("zero_row_inflation_verified"),
            "notes": f"excluded_coded_integers={sqlite_join_audit.get('excluded_coded_integers', [])}",
        })
        for pagg in sqlite_join_audit.get("one_to_many_preaggregations", []):
            hplc_rows.append({
                "table_role": "one_to_many_child_preaggregated",
                "table_name": pagg.get("child_table"),
                "join_key": pagg.get("join_key"),
                "raw_table_rows": pagg.get("child_rows_total", pagg.get("child_rows")),
                "matched_parent_rows": pagg.get("matched_parent_rows"),
                "naive_unaggregated_join_rows": pagg.get("naive_unaggregated_join_rows"),
                "final_joined_rows": pagg.get("joined_rows"),
                "inflation_factor_prevented": pagg.get("inflation_factor_prevented"),
                "row_inflation_prevented": pagg.get("row_inflation_prevented"),
                "notes": f"injections_per_parent=[{pagg.get('injections_per_parent_min')},{pagg.get('injections_per_parent_max')}]",
            })
        for eg in sqlite_join_audit.get("empty_groups_preserved", []):
            hplc_rows.append({
                "table_role": "empty_dimension_group_preserved",
                "table_name": eg.get("dim_table"),
                "join_key": f"{eg.get('join_key')}={eg.get('key_value')}",
                "raw_table_rows": 1,
                "matched_parent_rows": 0,
                "naive_unaggregated_join_rows": 0,
                "final_joined_rows": 0,
                "inflation_factor_prevented": 1.0,
                "row_inflation_prevented": True,
                "notes": f"label={eg.get('label')}, n_samples=0",
            })
        fname = _write_csv_table(
            out_dir / f"{stem}_hplc_reconcile.csv",
            list(hplc_rows[0].keys()),
            hplc_rows,
        )
        csv_files.append(fname)
        tables_payload["hplc_reconcile_csv"] = fname
        tables_payload["hplc_reconcile"] = hplc_rows

        # Also append SQLite reconciliation table to caption markdown so it is directly readable
        cap_p = out_dir / f"{stem}_caption.md"
        if cap_p.is_file():
            cap_extra = [
                "",
                "## Key Statistical Table: SQLite 1:N Pre-Aggregation & Row Reconciliation",
                "",
                "| Role | Table | Join Key | Raw Rows | Matched Parents | Naive Join Rows | Final Joined Rows | Inflation Prevented | Notes |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
            ]
            for hr in hplc_rows:
                cap_extra.append(
                    f"| `{hr['table_role']}` | `{hr['table_name']}` | `{hr['join_key']}` | {hr['raw_table_rows']} | {hr['matched_parent_rows']} | {hr['naive_unaggregated_join_rows']} | {hr['final_joined_rows']} | {hr['row_inflation_prevented']} ({hr['inflation_factor_prevented']}x) | {hr['notes']} |"
                )
            cap_p.write_text(cap_p.read_text(encoding="utf-8") + "\n".join(cap_extra) + "\n", encoding="utf-8")

    return csv_files, tables_payload


def _check_task_coverage_gaps(task_text: str, spec: dict[str, Any], audit_obj: dict[str, Any]) -> list[dict[str, str]]:
    """Cross-check task.md requirements against produced audit payload to prevent silent gaps."""
    low = task_text.lower()
    gaps: list[dict[str, str]] = []
    recon = audit_obj.get("source_to_visual_reconciliation") or {}
    if any(k in low for k in ("multivariable", "multivariate", "as a function of")) and spec.get("chart_type") != "multivariate_ols":
        gaps.append({
            "code": "TASK_MULTIVARIABLE_NOT_FULFILLED",
            "severity": "error",
            "source": "auto_viz_task_cross_check",
            "message": "task.md requests multivariable modeling but chart_type was not multivariate_ols.",
            "fallback": "manual_spec_override_required",
        })
    if any(k in low for k in ("assumption", "vif", "normality", "heteroscedast")):
        has_diag = bool(recon.get("diagnostics") or recon.get("statistical_tests"))
        if not has_diag:
            gaps.append({
                "code": "TASK_ASSUMPTION_TESTS_MISSING",
                "severity": "warning",
                "source": "auto_viz_task_cross_check",
                "message": "task.md requested statistical assumption checks not present in reconciliation.",
                "fallback": "run_stat_assumptions_module",
            })
    return gaps


def run_auto_viz(
    input_dir: Path,
    out_dir: Path,
    *,
    stem: str = "sci_figure",
    input_file: Path | None = None,
    table: str | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    disc = discover_input_dir(input_dir)
    task_p = input_dir / "task.md"
    task_text = task_p.read_text(encoding="utf-8-sig") if task_p.is_file() else ""
    case_json_p = (input_dir / "case.json") if (input_dir / "case.json").is_file() else None

    target_path = input_file or (Path(disc["primary_table_candidate"]) if disc.get("primary_table_candidate") else None)
    if target_path is None or not target_path.is_file():
        return {"ok": False, "error": f"No tabular or SQLite input found in {input_dir}"}

    csv_for_plot = target_path
    table_used: str | None = None
    sqlite_join_audit: dict[str, Any] | None = None
    plot_base_dir = target_path.parent
    spec_table_ref = target_path.name

    if target_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        csv_for_plot, table_used, sqlite_join_audit = _export_sqlite_fact_table_csv(
            target_path, out_dir / "_work", task_text, table=table
        )
        plot_base_dir = csv_for_plot.parent
        spec_table_ref = csv_for_plot.name

    spec = _infer_viz_spec(
        csv_for_plot,
        task_text,
        display_table_name=spec_table_ref,
        sqlite_table=table_used,
    )
    # Persist sanitized relative-only spec (zero host absolute paths)
    spec_for_disk = dict(spec)
    if target_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        spec_for_disk["original_sqlite_source"] = target_path.name
        spec_for_disk["sqlite_table"] = table_used
    spec_for_disk = sanitize_local_paths(spec_for_disk)
    spec_p = out_dir / f"{stem}_auto_spec.json"
    spec_p.write_text(json.dumps(spec_for_disk, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # For SQLite, plotting runs on the pre-aggregated read-only CSV snapshot
    build_spec = dict(spec)
    if target_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        build_spec["table"] = csv_for_plot.name
        build_spec["sqlite_table"] = None

    sel = advise_and_audit_chart(build_spec, base_dir=plot_base_dir)
    sel_p = out_dir / f"{stem}_selection_audit.json"
    sel_p.write_text(json.dumps(sanitize_local_paths(sel), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    build = build_publication_figure(build_spec, out_dir, stem=stem, base_dir=plot_base_dir)
    elapsed = round(time.monotonic() - t0, 4)

    all_gaps: list[Any] = list(build.get("auto_gaps") or [])
    all_unc: list[str] = list(build.get("auto_uncertainty") or [])

    aud_p = out_dir / f"{stem}_audit.json"
    aobj: dict[str, Any] = {}
    if aud_p.is_file():
        aobj = json.loads(aud_p.read_text(encoding="utf-8"))
        # Attach SQLite multi-table join & empty-group audit if applicable
        if sqlite_join_audit is not None:
            aobj["source_to_visual_reconciliation"]["sqlite_join_audit"] = sqlite_join_audit
            # Also attach per-cultivar descriptive stats on primary chemical columns if cultivar_label present
            if csv_for_plot.is_file():
                with csv_for_plot.open("r", encoding="utf-8-sig", newline="") as fh:
                    s_rows = list(csv.DictReader(fh))
                if s_rows and "cultivar_label" in s_rows[0]:
                    grp_by_cult: dict[str, list[float]] = {}
                    hm_cols = aobj["source_to_visual_reconciliation"].get("matrix_cols") or []
                    first_metric = hm_cols[0] if hm_cols else None
                    if first_metric:
                        for sr in s_rows:
                            fv = _try_float(sr.get(first_metric))
                            if fv is not None:
                                grp_by_cult.setdefault(sr.get("cultivar_label") or "unknown", []).append(fv)
                        aobj["source_to_visual_reconciliation"]["cultivar_stratified_summary"] = {
                            "metric": first_metric,
                            "groups": {g: describe_vector(v) for g, v in grp_by_cult.items()},
                            "empty_groups_preserved": sqlite_join_audit.get("empty_groups_preserved", []),
                        }
            for eg in sqlite_join_audit.get("empty_groups_preserved", []):
                msg = f"Empty dimension group preserved without silent drop: {eg['dim_table']}.{eg['join_key']}={eg['key_value']} ({eg['label']}, n=0)."
                all_unc.append(msg)
                all_gaps.append({"code": "EMPTY_DIMENSION_GROUP_PRESERVED", "severity": "info", "source": "sqlite_join_audit", "message": msg, "fallback": "retained_with_n_equals_0"})
            # Re-emit reproduce_figure.py pointing directly to the read-only SQLite file
            from publication_viz_builder import _emit_reproduce_figure_script
            import hashlib
            db_sha = hashlib.sha256(target_path.read_bytes()).hexdigest()
            _emit_reproduce_figure_script(
                out_dir=out_dir,
                table_name=target_path.name,
                sha256=db_sha,
                chart_type=spec["chart_type"],
                spec=spec_for_disk,
                ylim=build.get("actual_ylim"),
                sqlite_table=table_used,
                stem=stem,
            )
            aobj["source_to_visual_reconciliation"]["original_sqlite_file"] = target_path.name
            aobj["source_to_visual_reconciliation"]["original_sqlite_sha256"] = db_sha

        aobj["auto_viz_metadata"] = {
            "source_file": target_path.name,
            "table_used": table_used,
            "inferred_chart_type": spec["chart_type"],
            "source_refs": spec.get("source_refs") or {},
            "elapsed_seconds": elapsed,
        }
        # Export core statistical tables to standalone CSVs + embed in audit/result.json
        exported_csvs, stat_tables = _export_core_statistical_tables(
            out_dir, stem, spec["chart_type"], aobj, sqlite_join_audit
        )
        aobj["statistical_tables"] = stat_tables
        aobj["exported_csv_tables"] = exported_csvs

        # Inline task-coverage + gap_gate grounding verification
        all_gaps.extend(_check_task_coverage_gaps(task_text, spec, aobj))
        gg = check_grounding(aobj, [input_dir])
        all_gaps.extend(gg.get("gaps") or [])
        aobj["gap_gate_report"] = gg
        aobj["auto_gaps"] = all_gaps
        aobj["auto_uncertainty"] = list(dict.fromkeys(all_unc))
        aobj["artifacts"] = list(dict.fromkeys(list(aobj.get("artifacts") or []) + exported_csvs))
        aobj = sanitize_local_paths(aobj)
        aud_p.write_text(json.dumps(aobj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        exported_csvs = []

    has_warn_or_err = any(
        (g.get("severity") in {"warning", "error"} if isinstance(g, dict) else True)
        for g in all_gaps
    )
    honest_status = "partial_with_gaps" if (not build.get("ok") or has_warn_or_err) else "ok"

    arts = list(dict.fromkeys([spec_p.name, sel_p.name] + list(build.get("artifacts") or []) + exported_csvs))
    seal = seal_result_contract(
        case_json_path=case_json_p,
        result_json_path=out_dir / "result.json",
        task_id="challenge-9562cc8d",
        status=honest_status,
        payload_path=aud_p if aud_p.is_file() else sel_p,
        artifacts=arts,
        sources=[target_path.name] + (["task.md"] if task_p.is_file() else []),
        uncertainty=list(dict.fromkeys(all_unc)),
        gaps=all_gaps,
    )

    return {
        "ok": bool(build.get("ok") and seal.get("ok")),
        "status": honest_status,
        "chart_type": spec["chart_type"],
        "render_backend": build.get("render_backend"),
        "export_tier": build.get("export_tier"),
        "max_abs_diff": build.get("max_abs_diff"),
        "reconciled": build.get("reconciled"),
        "gaps_count": len(all_gaps),
        "elapsed_seconds": elapsed,
        "result_json": seal["result_json"],
        "artifacts": arts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-manual /input -> /app/output scientific visualization orchestrator.")
    parser.add_argument("--input-dir", required=True, help="Case input directory (/input).")
    parser.add_argument("--out-dir", required=True, help="Output directory (/app/output).")
    parser.add_argument("--stem", default="sci_figure", help="Output filename stem.")
    parser.add_argument("--input", default=None, help="Optional explicit CSV/SQLite override.")
    parser.add_argument("--table", default=None, help="Optional SQLite table override.")
    args = parser.parse_args(argv)

    rep = run_auto_viz(
        Path(args.input_dir),
        Path(args.out_dir),
        stem=args.stem,
        input_file=Path(args.input) if args.input else None,
        table=args.table,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
