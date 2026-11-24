#!/usr/bin/env python3
"""Gate 7: Independent Numeric Cross-Check Auditor (pure Python 3.12 stdlib).

Independently recomputes raw row counts, deduplicated row counts, target column
means/SDs (Welford's online algorithm), OLS coefficients/R^2 (Householder QR
decomposition — mathematically independent of normal-equation inversion), group
comparison means/eta^2, and SQLite safe-join cardinality against the executed
script's `analysis_summary.json` artifact.

Catches "exit code 0 but silently wrong numbers" (e.g., wrong formula, silent
fillna(0), un-deduplicated aggregation, or 1:N SQLite JOIN row inflation).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

_MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "<lod", "nd", "-999"}


def _rel_err(actual: float, expected: float) -> float:
    if not (math.isfinite(actual) and math.isfinite(expected)):
        return float("inf")
    denom = max(abs(expected), 1.0)
    return abs(actual - expected) / denom


def _welford_mean_sd(values: list[float]) -> tuple[float, float]:
    """Independent numerically stable one-pass Welford mean & sample SD."""
    n = 0
    mean = 0.0
    m2 = 0.0
    for x in values:
        n += 1
        delta = x - mean
        mean += delta / n
        delta2 = x - mean
        m2 += delta * delta2
    if n == 0:
        return float("nan"), float("nan")
    sd = math.sqrt(m2 / (n - 1)) if n > 1 else 0.0
    return mean, sd


def _householder_qr_ols(x_rows: list[list[float]], y: list[float]) -> tuple[list[float], float]:
    """Independent OLS solver via Householder QR decomposition (no normal-equation inversion)."""
    m = len(x_rows)
    n = len(x_rows[0])
    r_mat = [list(map(float, row)) for row in x_rows]
    qty = list(map(float, y))

    for k in range(n):
        norm_x = math.sqrt(sum(r_mat[i][k] ** 2 for i in range(k, m)))
        if norm_x == 0.0:
            raise ValueError(f"Rank-deficient design matrix at column {k}")
        sign = 1.0 if r_mat[k][k] >= 0.0 else -1.0
        u0 = r_mat[k][k] + sign * norm_x
        v = [0.0] * (m - k)
        v[0] = 1.0
        for i in range(k + 1, m):
            v[i - k] = r_mat[i][k] / u0
        beta_h = 2.0 / sum(vi * vi for vi in v)

        for j in range(k, n):
            dot = sum(v[i - k] * r_mat[i][j] for i in range(k, m))
            scale = beta_h * dot
            for i in range(k, m):
                r_mat[i][j] -= scale * v[i - k]

        dot_y = sum(v[i - k] * qty[i] for i in range(k, m))
        scale_y = beta_h * dot_y
        for i in range(k, m):
            qty[i] -= scale_y * v[i - k]

    # Back substitution on upper triangular R[:n, :n]
    beta = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = qty[i] - sum(r_mat[i][j] * beta[j] for j in range(i + 1, n))
        beta[i] = s / r_mat[i][i]

    y_mean, _ = _welford_mean_sd(y)
    tss = sum((yi - y_mean) ** 2 for yi in y)
    rss = sum((y[i] - sum(x_rows[i][j] * beta[j] for j in range(n))) ** 2 for i in range(m))
    r2 = (1.0 - rss / tss) if tss > 0.0 else 0.0
    return beta, r2


def _load_clean_csv(csv_path: Path, *, dedup_policy: str = "pk_bound_dedup", pk_cols: list[str] | None = None) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = [c.strip() for c in (reader.fieldnames or [])]
        raw_rows = [{k.strip(): ("" if v is None else v.strip()) for k, v in r.items() if k is not None} for r in reader]
    if dedup_policy == "report_only_no_pk":
        return cols, raw_rows, list(raw_rows)
    seen: set[tuple[str, ...]] = set()
    clean_rows: list[dict[str, str]] = []
    key_cols = [c for c in (pk_cols or []) if c in cols]
    for r in raw_rows:
        sig = tuple(r.get(c, "") for c in (key_cols if key_cols else cols))
        if sig not in seen:
            seen.add(sig)
            clean_rows.append(r)
    return cols, raw_rows, clean_rows


def _parse_float_col(rows: list[dict[str, Any]], col: str) -> list[float]:
    vals: list[float] = []
    for r in rows:
        raw = r.get(col)
        if raw is None:
            continue
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            fv = float(raw)
            if math.isfinite(fv):
                vals.append(fv)
            continue
        s = str(raw).strip()
        if s.lower() in _MISSING_TOKENS:
            continue
        try:
            fv = float(s)
            if math.isfinite(fv):
                vals.append(fv)
        except ValueError:
            pass
    return vals


def crosscheck_summary(
    summary_path: Path,
    *,
    input_csv: Path | None = None,
    input_db: Path | None = None,
    tolerance: float = 1e-9,
    expected_query_rows: int | None = None,
) -> dict[str, Any]:
    """Run independent numeric cross-checks comparing raw CSV/SQLite data against analysis_summary.json."""
    if not summary_path.is_file():
        return {
            "ok": False,
            "skipped": False,
            "error": f"Summary JSON not found: {summary_path.name}",
            "tolerance": tolerance,
            "checks_total": 0,
            "checks_passed": 0,
            "max_rel_err": None,
            "comparisons": [],
            "mismatches": [f"Missing summary file {summary_path.name}"],
        }

    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    comparisons: list[dict[str, Any]] = []
    mismatches: list[str] = []

    def check_metric(name: str, reported: Any, expected: float, tol: float | None = None) -> None:
        t_use = tolerance if tol is None else tol
        if reported is None or not isinstance(reported, (int, float)) or not math.isfinite(float(reported)):
            mismatches.append(f"{name}: missing or non-finite reported value ({reported!r}) vs expected {expected:.12g}")
            comparisons.append({"metric": name, "reported": reported, "expected": expected, "rel_err": None, "ok": False})
            return
        rep_f = float(reported)
        re_val = _rel_err(rep_f, expected)
        ok_flag = re_val < t_use
        comparisons.append({"metric": name, "reported": rep_f, "expected": expected, "rel_err": re_val, "ok": ok_flag})
        if not ok_flag:
            mismatches.append(f"{name}: reported={rep_f:.12g} vs independent={expected:.12g} (rel_err={re_val:.3e} >= {t_use:.1e})")

    # --- Branch A: CSV Input Verification ---
    if input_csv and input_csv.is_file():
        da = summary.get("dedup_audit") if isinstance(summary.get("dedup_audit"), dict) else {}
        dedup_policy = str(da.get("dedup_policy") or "pk_bound_dedup")
        pk_cols = list(da.get("observation_primary_key") or [])
        cols, raw_rows, clean_rows = _load_clean_csv(input_csv, dedup_policy=dedup_policy, pk_cols=pk_cols)
        if "rows_raw" in summary:
            check_metric("csv.rows_raw", summary["rows_raw"], float(len(raw_rows)))
        if "rows_dedup" in summary:
            check_metric("csv.rows_dedup", summary["rows_dedup"], float(len(clean_rows)))

        # Verify reported column stats (mean & SD via Welford)
        for cs in summary.get("column_stats") or []:
            cname = cs.get("column")
            if cname in cols and cs.get("mean") is not None:
                vals = _parse_float_col(clean_rows, cname)
                if vals:
                    w_mean, w_sd = _welford_mean_sd(vals)
                    # Allow 1e-9 if high-precision stored, else detect rounded-only values strictly
                    check_metric(f"col.{cname}.mean", cs.get("mean"), w_mean)
                    if cs.get("sd") is not None and len(vals) > 1:
                        check_metric(f"col.{cname}.sd", cs.get("sd"), w_sd)

        # Verify OLS regression via independent Householder QR
        ols = summary.get("regression_ols")
        if isinstance(ols, dict) and ols.get("target") and ols.get("predictors"):
            target = ols["target"]
            preds = list(ols["predictors"])
            x_rows: list[list[float]] = []
            y_vec: list[float] = []
            for r in clean_rows:
                try:
                    yv = float(r[target])
                    xv = [float(r[p]) for p in preds]
                    if math.isfinite(yv) and all(math.isfinite(v) for v in xv):
                        x_rows.append([1.0] + xv)
                        y_vec.append(yv)
                except (KeyError, ValueError):
                    continue
            check_metric("ols.n_complete", ols.get("n_complete"), float(len(y_vec)))
            if len(y_vec) > len(preds) + 1:
                qr_beta, qr_r2 = _householder_qr_ols(x_rows, y_vec)
                check_metric("ols.r_squared", ols.get("r_squared"), qr_r2)
                rep_coefs = {c["term"]: c["beta"] for c in (ols.get("coefficients") or []) if "term" in c}
                for term, exp_b in zip(["intercept"] + preds, qr_beta):
                    if term in rep_coefs:
                        check_metric(f"ols.beta.{term}", rep_coefs[term], exp_b)

        # Verify Group Comparison via independent grouping + Welford + eta^2
        gc = summary.get("group_comparison")
        if isinstance(gc, dict) and gc.get("group_column") and gc.get("target_column"):
            gcol = gc["group_column"]
            tcol = gc["target_column"]
            g_buckets: dict[str, list[float]] = {}
            all_v: list[float] = []
            for r in clean_rows:
                gval = r.get(gcol, "")
                t_raw = r.get(tcol, "")
                if not gval or t_raw.lower() in _MISSING_TOKENS:
                    continue
                try:
                    fv = float(t_raw)
                    if math.isfinite(fv):
                        g_buckets.setdefault(gval, []).append(fv)
                        all_v.append(fv)
                except ValueError:
                    continue
            check_metric("group.n_total", gc.get("n_total"), float(len(all_v)))
            for gs in gc.get("group_summaries") or []:
                g = str(gs.get("group"))
                vals = g_buckets.get(g, [])
                check_metric(f"group.{g}.n", gs.get("n"), float(len(vals)))
                if vals and gs.get("mean") is not None:
                    wm, ws = _welford_mean_sd(vals)
                    check_metric(f"group.{g}.mean", gs.get("mean"), wm)
                    if len(vals) > 1 and gs.get("sd") is not None:
                        check_metric(f"group.{g}.sd", gs.get("sd"), ws)
            omn = gc.get("omnibus_tests") or {}
            ca = omn.get("classical_anova") or {}
            if all_v and "eta_squared" in ca:
                gm, _ = _welford_mean_sd(all_v)
                ssb = sum(len(vs) * (_welford_mean_sd(vs)[0] - gm) ** 2 for vs in g_buckets.values() if vs)
                sst = sum((x - gm) ** 2 for x in all_v)
                exp_eta = (ssb / sst) if sst > 0.0 else 0.0
                check_metric("group.classical_anova.eta_squared", ca.get("eta_squared"), exp_eta)

    # --- Branch B: SQLite Read-Only Database Verification ---
    if input_db and input_db.is_file():
        conn = sqlite3.connect(f"file:{input_db.resolve().as_posix()}?mode=ro", uri=True)
        try:
            # Verify table row counts
            for prof in summary.get("profiles") or []:
                tbl = prof.get("table")
                if not tbl:
                    continue
                cnt = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
                check_metric(f"sqlite.{tbl}.rows", prof.get("rows"), float(cnt))

            # Verify safe 1:N join cardinality & non-inflation
            sj = summary.get("safe_join_audit")
            if isinstance(sj, dict) and sj.get("primary_table"):
                ptbl = sj["primary_table"]
                p_cnt = conn.execute(f'SELECT COUNT(*) FROM "{ptbl}"').fetchone()[0]
                check_metric(f"sqlite.safe_join.{ptbl}_rows", sj.get("joined_sample_rows"), float(p_cnt))
                if expected_query_rows is not None:
                    check_metric("sqlite.expected_query_rows", sj.get("joined_sample_rows"), float(expected_query_rows))

            # Verify SQLite group comparison (e.g., wine_samples across cultivars)
            gc = summary.get("group_comparison")
            if isinstance(gc, dict) and gc.get("source_table") and gc.get("group_column") and gc.get("target_column"):
                stbl = gc["source_table"]
                gcol = gc["group_column"]
                tcol = gc["target_column"]
                dim_tbl = gc.get("dimension_table")
                dim_key = gc.get("dimension_key")
                dim_label = gc.get("dimension_label")
                if dim_tbl and dim_key and dim_label:
                    q = f'SELECT d."{dim_label}", s."{tcol}" FROM "{dim_tbl}" d LEFT JOIN "{stbl}" s ON d."{dim_key}" = s."{dim_key}"'
                else:
                    q = f'SELECT "{gcol}", "{tcol}" FROM "{stbl}"'
                g_buckets = {}
                all_v = []
                for g_raw, t_raw in conn.execute(q).fetchall():
                    g_key = str(g_raw)
                    g_buckets.setdefault(g_key, [])
                    if t_raw is not None and isinstance(t_raw, (int, float)) and math.isfinite(float(t_raw)):
                        fv = float(t_raw)
                        g_buckets[g_key].append(fv)
                        all_v.append(fv)
                check_metric("sqlite.group.n_total", gc.get("n_total"), float(len(all_v)))
                for gs in gc.get("group_summaries") or []:
                    g = str(gs.get("group"))
                    vals = g_buckets.get(g, [])
                    check_metric(f"sqlite.group.{g}.n", gs.get("n"), float(len(vals)))
                    if vals and gs.get("mean") is not None:
                        wm, ws = _welford_mean_sd(vals)
                        check_metric(f"sqlite.group.{g}.mean", gs.get("mean"), wm)
                        if len(vals) > 1 and gs.get("sd") is not None:
                            check_metric(f"sqlite.group.{g}.sd", gs.get("sd"), ws)
                omn = gc.get("omnibus_tests") or {}
                ca = omn.get("classical_anova") or {}
                if all_v and "eta_squared" in ca:
                    gm, _ = _welford_mean_sd(all_v)
                    ssb = sum(len(vs) * (_welford_mean_sd(vs)[0] - gm) ** 2 for vs in g_buckets.values() if vs)
                    sst = sum((x - gm) ** 2 for x in all_v)
                    exp_eta = (ssb / sst) if sst > 0.0 else 0.0
                    check_metric("sqlite.group.eta_squared", ca.get("eta_squared"), exp_eta)
        finally:
            conn.close()

    finite_errs = [c["rel_err"] for c in comparisons if c.get("rel_err") is not None and math.isfinite(c["rel_err"])]
    max_err = max(finite_errs) if finite_errs else 0.0
    passed = sum(1 for c in comparisons if c["ok"])
    ok_all = len(mismatches) == 0 and len(comparisons) > 0
    return {
        "ok": ok_all,
        "skipped": False,
        "summary_file": summary_path.name,
        "tolerance": tolerance,
        "checks_total": len(comparisons),
        "checks_passed": passed,
        "max_rel_err": max_err,
        "mismatches": mismatches,
        "comparisons": comparisons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate 7: Independent numeric cross-check for analysis_summary.json.")
    parser.add_argument("--summary", required=True, help="Path to analysis_summary.json produced by script.")
    parser.add_argument("--input-csv", default=None, help="Raw input CSV file for independent recomputation.")
    parser.add_argument("--input-db", default=None, help="Raw input SQLite database for independent recomputation.")
    parser.add_argument("--tolerance", type=float, default=1e-9, help="Maximum allowed relative error (default 1e-9).")
    parser.add_argument("--expected-query-rows", type=int, default=None, help="Expected SQLite safe-join row count.")
    parser.add_argument("--out", default=None, help="Optional JSON output report path.")
    args = parser.parse_args(argv)

    rep = crosscheck_summary(
        Path(args.summary),
        input_csv=Path(args.input_csv) if args.input_csv else None,
        input_db=Path(args.input_db) if args.input_db else None,
        tolerance=args.tolerance,
        expected_query_rows=args.expected_query_rows,
    )
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
