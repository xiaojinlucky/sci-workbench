#!/usr/bin/env python3
"""Pure-stdlib OLS interaction-reversal detector, missing-cell estimability auditor, and uncertainty-aware surrogate."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _to_float(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(str(v).strip())
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def _mat_transpose(A: list[list[float]]) -> list[list[float]]:
    return [list(col) for col in zip(*A)] if A else []


def _mat_mul(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    if not A or not B:
        return []
    bt = _mat_transpose(B)
    return [[sum(a * b for a, b in zip(row, col)) for col in bt] for row in A]


def _mat_vec(A: list[list[float]], x: list[float]) -> list[float]:
    return [sum(a * xi for a, xi in zip(row, x)) for row in A]


def _invert_matrix(M: list[list[float]], ridge: float = 1e-8) -> list[list[float]] | None:
    """Gauss-Jordan inversion with partial pivoting and tiny ridge stabilization."""
    n = len(M)
    if n == 0:
        return []
    aug = [
        [M[i][j] + (ridge if i == j else 0.0) for j in range(n)] + [1.0 if i == j else 0.0 for j in range(n)]
        for i in range(n)
    ]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            return None
        aug[col], aug[piv] = aug[piv], aug[col]
        div = aug[col][col]
        aug[col] = [v / div for v in aug[col]]
        for r in range(n):
            if r != col:
                fac = aug[r][col]
                if abs(fac) > 1e-15:
                    aug[r] = [aug[r][k] - fac * aug[col][k] for k in range(2 * n)]
    return [row[n:] for row in aug]


def _fit_ols(X: list[list[float]], y: list[float]) -> dict[str, Any] | None:
    n = len(y)
    if n == 0 or not X:
        return None
    p = len(X[0])
    raw_dof = n - p
    Xt = _mat_transpose(X)
    XtX = _mat_mul(Xt, X)
    XtX_inv = _invert_matrix(XtX, ridge=1e-7)
    if XtX_inv is None:
        return None
    Xty = _mat_vec(Xt, y)
    beta = _mat_vec(XtX_inv, Xty)
    preds = _mat_vec(X, beta)
    resid = [yi - pi for yi, pi in zip(y, preds)]
    sse = sum(r * r for r in resid)
    y_mean = statistics.mean(y)
    sst = sum((yi - y_mean) ** 2 for yi in y)
    r2 = 1.0 - (sse / sst) if sst > 1e-12 else 0.0
    underdetermined = raw_dof <= 0
    rmse = math.sqrt(sse / raw_dof) if raw_dof > 0 else None
    mse = (sse / raw_dof) if raw_dof > 0 else None
    # Diagonal hat-matrix leverages h_ii, exact analytical LOOCV PRESS / LOOCV-R^2, and Cook's D proxy
    leverages: list[float] = []
    cooks: list[float] = []
    loocv_sq_errs: list[float] = []
    loocv_valid = True
    for xi, ei in zip(X, resid):
        hx = _mat_vec(XtX_inv, xi)
        h_ii = max(0.0, min(0.9999, sum(a * b for a, b in zip(xi, hx))))
        leverages.append(round(h_ii, 4))
        if h_ii >= 0.995:
            loocv_valid = False
        else:
            e_loo = ei / (1.0 - h_ii)
            loocv_sq_errs.append(e_loo * e_loo)
        if mse and mse > 1e-12 and h_ii < 0.999:
            cd = (ei * ei / (p * mse)) * (h_ii / ((1.0 - h_ii) ** 2))
            cooks.append(round(cd, 4))
    max_lev = max(leverages) if leverages else 0.0
    max_cook = max(cooks) if cooks else None
    press = round(sum(loocv_sq_errs), 6) if (loocv_valid and loocv_sq_errs) else None
    loocv_r2 = round(1.0 - (press / sst), 4) if (press is not None and sst > 1e-12) else None
    loocv_rmse = round(math.sqrt(press / n), 6) if (press is not None and n > 0) else None
    adj_r2 = round(1.0 - (1.0 - r2) * (n - 1) / raw_dof, 4) if raw_dof > 0 else None
    overfit_gap = round(r2 - loocv_r2, 4) if loocv_r2 is not None else None
    return {
        "beta": beta, "xtx_inv": XtX_inv, "r2": round(r2, 4), "adj_r2": adj_r2,
        "loocv_r2": loocv_r2, "press": press, "loocv_rmse": loocv_rmse, "r2_minus_loocv_r2": overfit_gap,
        "rmse": round(rmse, 6) if rmse is not None else None,
        "n": n, "p": p, "dof": raw_dof,
        "underdetermined_model": underdetermined,
        "small_sample_warning": raw_dof < 5,
        "overfit_warning": bool(raw_dof < 5 or (loocv_r2 is None) or (loocv_r2 < 0.30) or (overfit_gap is not None and overfit_gap > 0.25)),
        "max_leverage": max_lev,
        "high_leverage_threshold": round(2.0 * p / max(n, 1), 4),
        "max_cooks_d": max_cook,
    }


RESPONSE_ALIASES = ("response", "yield", "overpotential", "activity", "metric", "score", "purity", "efficiency", "output", "value", "y")
META_COLS = {"run_id", "id", "run", "index", "batch", "notes", "source_ref", "comment", "operator"}


def _read_xlsx_first_table(xlsx_path: Path) -> list[dict[str, Any]]:
    import xml.etree.ElementTree as ET
    import zipfile
    ns_m = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(str(xlsx_path), "r") as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall(f"{ns_m}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns_m}t")))
        sheet_files = sorted(n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not sheet_files:
            return []
        rows: list[list[str]] = []
        for row_el in ET.fromstring(zf.read(sheet_files[0])).findall(f".//{ns_m}sheetData/{ns_m}row"):
            vals: list[str] = []
            for c_el in row_el.findall(f"{ns_m}c"):
                ct = c_el.attrib.get("t", "")
                if ct == "inlineStr":
                    v = "".join(t.text or "" for t in c_el.iter(f"{ns_m}t"))
                else:
                    ve = c_el.find(f"{ns_m}v")
                    raw = ve.text if ve is not None else ""
                    v = shared[int(raw)] if ct == "s" and raw.isdigit() and int(raw) < len(shared) else (raw or "")
                vals.append(v.strip())
            if any(vals):
                rows.append(vals)
    if len(rows) < 2:
        return []
    hdr = rows[0]
    return [{hdr[i]: (r[i] if i < len(r) else "") for i in range(len(hdr))} for r in rows[1:]]


def _infer_resp_and_factors(rows: list[dict[str, Any]], resp_hint: str = "", factors_hint: list[str] | None = None) -> tuple[list[str], str]:
    headers = list(rows[0].keys()) if rows else []
    resp_col = resp_hint
    if not resp_col or resp_col not in headers:
        for h in headers:
            if h.lower() in RESPONSE_ALIASES or any(a in h.lower() for a in RESPONSE_ALIASES[:-1]):
                resp_col = h
                break
        if not resp_col and headers:
            resp_col = headers[-1]
    factors = list(factors_hint or [])
    if not factors and rows:
        factors = [
            h for h in headers
            if h != resp_col and h.lower() not in META_COLS and any(_to_float(r.get(h)) is not None for r in rows[:10])
        ]
    return factors, resp_col or "response"


def _load_pilot_rows(pilot_path: Path) -> tuple[list[dict[str, Any]], list[str], str, str]:
    suf = pilot_path.suffix.lower()
    if suf in {".csv", ".tsv"}:
        delim = "\t" if suf == ".tsv" else ","
        with pilot_path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter=delim))
        factors, resp_col = _infer_resp_and_factors(rows)
        return rows, factors, resp_col, "maximize"
    if suf == ".xlsx":
        rows = _read_xlsx_first_table(pilot_path)
        factors, resp_col = _infer_resp_and_factors(rows)
        return rows, factors, resp_col, "maximize"
    doc = json.loads(pilot_path.read_text(encoding="utf-8-sig"))
    if isinstance(doc, list):
        rows = doc
        factors, resp_col = _infer_resp_and_factors(rows)
        return rows, factors, resp_col, "maximize"
    rows = (
        doc.get("runs")
        or doc.get("observations")
        or doc.get("historical_runs")
        or doc.get("trials")
        or doc.get("data")
        or (doc.get("historical_observations") or {}).get("runs")
        or []
    )
    resp_hint = str(doc.get("response_metric") or doc.get("response_col") or doc.get("objective_metric") or "")
    direction = str(doc.get("objective_direction") or doc.get("direction") or "maximize").lower()
    factors, resp_col = _infer_resp_and_factors(rows, resp_hint=resp_hint, factors_hint=list(doc.get("factors") or []))
    return rows, factors, resp_col, direction


def analyze_interactions_and_surrogate(
    rows: list[dict[str, Any]],
    factors: list[str],
    response_col: str,
    *,
    direction: str = "maximize",
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Detect OFAT interaction reversals, missing factor cells, and predict candidate response + uncertainty."""
    valid_pts: list[tuple[dict[str, float], float]] = []
    skipped_rows = 0
    for r in rows:
        y = _to_float(r.get(response_col))
        if y is None:
            skipped_rows += 1
            continue
        fvals: dict[str, float] = {}
        ok = True
        for f in factors:
            fv = _to_float(r.get(f))
            if fv is None:
                ok = False
                break
            fvals[f] = fv
        if ok:
            valid_pts.append((fvals, y))
        else:
            skipped_rows += 1

    if len(valid_pts) < 3 or not factors:
        return {
            "$schema": "interaction-surrogate-report/v1",
            "ok": False,
            "reason": "insufficient_numeric_pilot_runs",
            "valid_run_count": len(valid_pts),
            "skipped_rows": skipped_rows,
        }

    # Standardize factors to [-1, 1] for stable OLS and interpretable interaction magnitudes
    stats_f: dict[str, tuple[float, float]] = {}
    levels_by_f: dict[str, list[float]] = {}
    for f in factors:
        vals = [pt[0][f] for pt in valid_pts]
        lo, hi = min(vals), max(vals)
        mid = 0.5 * (lo + hi)
        half = 0.5 * (hi - lo) if hi > lo else 1.0
        stats_f[f] = (mid, half)
        levels_by_f[f] = sorted(set(vals))

    def _encode_main(fvals: dict[str, float]) -> list[float]:
        return [1.0] + [(fvals[f] - stats_f[f][0]) / stats_f[f][1] for f in factors]

    pair_names = [(factors[i], factors[j]) for i in range(len(factors)) for j in range(i + 1, len(factors))]

    def _encode_inter(fvals: dict[str, float]) -> list[float]:
        z = [(fvals[f] - stats_f[f][0]) / stats_f[f][1] for f in factors]
        inter = [z[i] * z[j] for i in range(len(factors)) for j in range(i + 1, len(factors))]
        return [1.0] + z + inter

    y_vec = [pt[1] for pt in valid_pts]
    X_main = [_encode_main(pt[0]) for pt in valid_pts]
    X_int = [_encode_inter(pt[0]) for pt in valid_pts]
    fit_main = _fit_ols(X_main, y_vec)
    fit_int = _fit_ols(X_int, y_vec) if len(valid_pts) > len(X_int[0]) else None

    # 1. Empirical OFAT marginal optimum vs conditional optimum (exact interaction reversal test!)
    better = (lambda a, b: a > b) if direction == "maximize" else (lambda a, b: a < b)
    marginal_best: dict[str, float] = {}
    for f in factors:
        by_lvl: dict[float, list[float]] = defaultdict(list)
        for fv, y in valid_pts:
            by_lvl[fv[f]].append(y)
        best_lvl = max(by_lvl.keys(), key=lambda lv: statistics.mean(by_lvl[lv]) if direction == "maximize" else -statistics.mean(by_lvl[lv]))
        marginal_best[f] = best_lvl

    reversals: list[dict[str, Any]] = []
    missing_pair_cells: list[dict[str, Any]] = []
    for f1, f2 in pair_names:
        cell_means: dict[tuple[float, float], float] = {}
        by_pair: dict[tuple[float, float], list[float]] = defaultdict(list)
        for fv, y in valid_pts:
            by_pair[(fv[f1], fv[f2])].append(y)
        for k, ys in by_pair.items():
            cell_means[k] = statistics.mean(ys)

        # Detect missing cells in observed level grid
        for l1 in levels_by_f[f1]:
            for l2 in levels_by_f[f2]:
                if (l1, l2) not in cell_means:
                    missing_pair_cells.append({f1: l1, f2: l2})

        # Check if best level of f1 reverses across levels of f2
        cond_bests: dict[float, float] = {}
        for l2 in levels_by_f[f2]:
            avail_l1 = [l1 for l1 in levels_by_f[f1] if (l1, l2) in cell_means]
            if len(avail_l1) >= 2:
                cond_bests[l2] = max(avail_l1, key=lambda l1: cell_means[(l1, l2)] if direction == "maximize" else -cell_means[(l1, l2)])
        if len(set(cond_bests.values())) > 1:
            reversals.append({
                "factor": f1,
                "conditioning_factor": f2,
                "ofat_marginal_best": marginal_best[f1],
                "conditional_best_by_level": {str(k): v for k, v in sorted(cond_bests.items())},
                "warning": f"Direction of {f1} flips across levels of {f2}; concatenating OFAT marginal optima is invalid.",
            })

    # Check whether OFAT concatenated point is strictly worse than observed joint best
    obs_best_pt, obs_best_y = max(valid_pts, key=lambda pt: pt[1] if direction == "maximize" else -pt[1])
    ofat_cells = [y for fv, y in valid_pts if all(abs(fv[f] - marginal_best[f]) < 1e-9 for f in factors)]
    ofat_obs_mean = round(statistics.mean(ofat_cells), 4) if ofat_cells else None
    ofat_is_suboptimal = (ofat_obs_mean is not None and better(obs_best_y, ofat_obs_mean))

    # Extract fitted interaction coefficients
    interaction_terms: list[dict[str, Any]] = []
    if fit_int is not None:
        offset_idx = 1 + len(factors)
        for idx, (f1, f2) in enumerate(pair_names):
            coef = fit_int["beta"][offset_idx + idx]
            rmse_val = fit_int["rmse"]
            se = (rmse_val * math.sqrt(max(0.0, fit_int["xtx_inv"][offset_idx + idx][offset_idx + idx]))) if rmse_val is not None else None
            t_stat = (coef / se) if (se is not None and se > 1e-12) else None
            interaction_terms.append({
                "pair": f"{f1}:{f2}",
                "std_coeff": round(coef, 4),
                "approx_se": round(se, 4) if se is not None else None,
                "approx_t": round(t_stat, 3) if t_stat is not None else None,
                "strong_interaction": (abs(t_stat) >= 2.0) if t_stat is not None else False,
            })

    # Predict candidate responses + prediction uncertainty + extrapolation flag
    use_model = fit_int if (fit_int is not None and not fit_int.get("underdetermined_model") and (fit_main is None or fit_int["r2"] >= fit_main["r2"] + 0.05)) else fit_main
    use_inter = (use_model is fit_int and fit_int is not None)
    cand_preds: list[dict[str, Any]] = []
    if use_model is not None and candidates:
        for idx, cand in enumerate(candidates, start=1):
            pid = str(cand.get("plan_id", f"plan_v{idx}"))
            pm = cand.get("parameters") or {}
            if not all(_to_float(pm.get(f)) is not None for f in factors):
                cand_preds.append({"plan_id": pid, "predicted": False, "reason": "missing_factor_values"})
                continue
            fv = {f: float(pm[f]) for f in factors}
            extrap = [f for f in factors if fv[f] < min(levels_by_f[f]) - 1e-9 or fv[f] > max(levels_by_f[f]) + 1e-9]
            x_row = _encode_inter(fv) if use_inter else _encode_main(fv)
            pred_y = sum(b * xi for b, xi in zip(use_model["beta"], x_row))
            v_x = _mat_vec(use_model["xtx_inv"], x_row)
            lev = max(0.0, sum(a * b for a, b in zip(x_row, v_x)))
            pred_se = (use_model["rmse"] * math.sqrt(1.0 + lev)) if use_model.get("rmse") is not None else None
            obs_matches = [pt[1] for pt in valid_pts if all(abs(pt[0][f] - fv[f]) < 1e-9 for f in factors)]
            already_obs = len(obs_matches) > 0
            obs_anchor_mean = round(statistics.mean(obs_matches), 4) if obs_matches else None
            pred_minus_obs = round(pred_y - obs_anchor_mean, 4) if obs_anchor_mean is not None else None
            cand_preds.append({
                "plan_id": pid,
                "predicted": True,
                "model_used": "ols_main_plus_2way_interaction" if use_inter else "ols_main_effects",
                "surrogate_predicted_response": round(pred_y, 4),
                "surrogate_prediction_se": round(pred_se, 4) if pred_se is not None else None,
                "observed_anchor_mean": obs_anchor_mean,
                "prediction_minus_observed": pred_minus_obs,
                "reconciliation_note": (
                    f"Surrogate smooths observed cell {obs_anchor_mean} to {round(pred_y, 4)} (delta={pred_minus_obs:+.4f}, linear-interaction residual); treat observed anchor as empirical reference and confirm via replicate run."
                    if obs_anchor_mean is not None
                    else "Untested combination; surrogate estimate is prospective only and requires confirmatory run."
                ),
                "small_sample_se_caution": bool(use_model.get("small_sample_warning")),
                "overfit_warning": bool(use_model.get("overfit_warning")),
                "extrapolated_factors": extrap,
                "previously_untested_cell": not already_obs,
                "independent_confirmation_required": True,
            })

    r2_lift = round(fit_int["r2"] - fit_main["r2"], 4) if (fit_int and fit_main) else None
    raw_inter_detected = bool(reversals) or any(t["strong_interaction"] for t in interaction_terms) or (r2_lift is not None and r2_lift >= 0.10)
    int_overfit = bool(fit_int and fit_int.get("overfit_warning"))
    if not raw_inter_detected:
        inter_status = "not_detected"
    elif int_overfit:
        inter_status = "provisional_small_sample_or_overfit_risk"
    else:
        inter_status = "confirmed_cross_validated"

    # Lack-of-fit check when replicate cells exist
    cell_groups: dict[tuple[float, ...], list[float]] = defaultdict(list)
    for fv, y in valid_pts:
        cell_groups[tuple(fv[f] for f in factors)].append(y)
    pure_err_ss = sum(sum((y - statistics.mean(ys)) ** 2 for y in ys) for ys in cell_groups.values() if len(ys) >= 2)
    pure_err_dof = sum(len(ys) - 1 for ys in cell_groups.values() if len(ys) >= 2)
    lack_of_fit = {"evaluated": False, "reason": "no_replicate_cells_in_pilot_data"}
    if pure_err_dof > 0 and use_model is not None and use_model["dof"] > pure_err_dof:
        tot_sse = (use_model["rmse"] ** 2) * use_model["dof"] if use_model.get("rmse") is not None else 0.0
        lof_ss = max(0.0, tot_sse - pure_err_ss)
        lof_dof = use_model["dof"] - pure_err_dof
        ms_pe = pure_err_ss / pure_err_dof
        ms_lof = lof_ss / lof_dof if lof_dof > 0 else 0.0
        lack_of_fit = {
            "evaluated": True, "pure_error_dof": pure_err_dof, "lof_dof": lof_dof,
            "lof_f_stat": round(ms_lof / ms_pe, 4) if ms_pe > 1e-12 else None,
        }

    def _fit_summary(fit: dict[str, Any] | None) -> dict[str, Any] | None:
        if not fit:
            return None
        return {
            "r2": fit["r2"], "adj_r2": fit["adj_r2"], "loocv_r2": fit["loocv_r2"],
            "press": fit["press"], "loocv_rmse": fit["loocv_rmse"], "r2_minus_loocv_r2": fit["r2_minus_loocv_r2"],
            "rmse": fit["rmse"], "n": fit["n"], "p": fit["p"], "dof": fit["dof"],
            "underdetermined_model": fit["underdetermined_model"],
            "small_sample_warning": fit["small_sample_warning"],
            "overfit_warning": fit["overfit_warning"],
            "max_leverage": fit["max_leverage"], "max_cooks_d": fit["max_cooks_d"],
        }

    return {
        "$schema": "interaction-surrogate-report/v2",
        "ok": True,
        "response_metric": response_col,
        "objective_direction": direction,
        "factors": factors,
        "valid_run_count": len(valid_pts),
        "skipped_rows": skipped_rows,
        "main_effects_model": _fit_summary(fit_main),
        "interaction_model": _fit_summary(fit_int),
        "lack_of_fit_test": lack_of_fit,
        "r2_lift_from_interactions": r2_lift,
        "interaction_reversal_detected": raw_inter_detected,
        "interaction_reversal_status": inter_status,
        "empirical_sign_reversals": reversals,
        "interaction_terms": interaction_terms,
        "ofat_marginal_optimum": marginal_best,
        "ofat_observed_mean": ofat_obs_mean,
        "observed_joint_best": {"parameters": obs_best_pt, "response": obs_best_y},
        "ofat_strictly_suboptimal": ofat_is_suboptimal,
        "missing_factorial_cells_count": len(missing_pair_cells),
        "missing_factorial_cells_sample": missing_pair_cells[:20],
        "candidate_surrogate_predictions": cand_preds,
        "method_notice": "Surrogate predictions and UCB/SE estimates guide prospective confirmatory runs only; they are NOT measured experimental outcomes.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect OFAT interaction reversals, missing factorial cells, and predict surrogate uncertainty.")
    parser.add_argument("--pilot", required=True, help="Pilot/historical observations JSON or CSV")
    parser.add_argument("--domain", default="", help="Optional domain_spec.json")
    parser.add_argument("--candidates", default="", help="Optional candidates_3v.json")
    parser.add_argument("--out", default="", help="Output JSON report path")
    args = parser.parse_args(argv)

    rows, factors, resp_col, direction = _load_pilot_rows(Path(args.pilot))
    cands = None
    if args.candidates and Path(args.candidates).is_file():
        raw_c = json.loads(Path(args.candidates).read_text(encoding="utf-8-sig"))
        cands = raw_c if isinstance(raw_c, list) else raw_c.get("plans", [])

    rep = analyze_interactions_and_surrogate(rows, factors, resp_col, direction=direction, candidates=cands)
    encoded = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(encoded, encoding="utf-8")
    print(json.dumps({
        "ok": rep["ok"],
        "interaction_reversal_detected": rep.get("interaction_reversal_detected"),
        "r2_lift": rep.get("r2_lift_from_interactions"),
        "missing_cells": rep.get("missing_factorial_cells_count"),
    }, ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
