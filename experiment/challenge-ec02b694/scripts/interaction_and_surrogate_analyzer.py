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
    dof = max(1, n - p)
    rmse = math.sqrt(sse / dof)
    return {"beta": beta, "xtx_inv": XtX_inv, "r2": round(r2, 4), "rmse": round(rmse, 6), "n": n, "p": p, "dof": dof}


def _load_pilot_rows(pilot_path: Path) -> tuple[list[dict[str, Any]], list[str], str, str]:
    if pilot_path.suffix.lower() == ".csv":
        with pilot_path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        headers = list(rows[0].keys()) if rows else []
        resp_col = headers[-1] if headers else "response"
        factors = [h for h in headers[:-1] if h.lower() not in {"run_id", "id", "batch", "notes"}]
        return rows, factors, resp_col, "maximize"
    doc = json.loads(pilot_path.read_text(encoding="utf-8-sig"))
    rows = doc.get("runs") or doc.get("observations") or []
    factors = list(doc.get("factors") or [])
    resp_col = str(doc.get("response_metric") or "response")
    direction = str(doc.get("objective_direction") or "maximize").lower()
    if not factors and rows:
        factors = [k for k in rows[0] if k not in {resp_col, "run_id", "id", "batch", "notes"} and _to_float(rows[0][k]) is not None]
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
            se = fit_int["rmse"] * math.sqrt(max(0.0, fit_int["xtx_inv"][offset_idx + idx][offset_idx + idx]))
            t_stat = (coef / se) if se > 1e-12 else 0.0
            interaction_terms.append({
                "pair": f"{f1}:{f2}",
                "std_coeff": round(coef, 4),
                "approx_se": round(se, 4),
                "approx_t": round(t_stat, 3),
                "strong_interaction": abs(t_stat) >= 2.0,
            })

    # Predict candidate responses + prediction uncertainty + extrapolation flag
    use_model = fit_int if (fit_int is not None and (fit_main is None or fit_int["r2"] >= fit_main["r2"] + 0.05)) else fit_main
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
            # Prediction SE: rmse * sqrt(1 + x^T (X^T X)^-1 x)
            v_x = _mat_vec(use_model["xtx_inv"], x_row)
            lev = max(0.0, sum(a * b for a, b in zip(x_row, v_x)))
            pred_se = use_model["rmse"] * math.sqrt(1.0 + lev)
            # Check if exact combination was already observed
            already_obs = any(all(abs(pt[0][f] - fv[f]) < 1e-9 for f in factors) for pt in valid_pts)
            cand_preds.append({
                "plan_id": pid,
                "predicted": True,
                "model_used": "ols_main_plus_2way_interaction" if use_inter else "ols_main_effects",
                "surrogate_predicted_response": round(pred_y, 4),
                "surrogate_prediction_se": round(pred_se, 4),
                "extrapolated_factors": extrap,
                "previously_untested_cell": not already_obs,
                "independent_confirmation_required": True,
            })

    r2_lift = round(fit_int["r2"] - fit_main["r2"], 4) if (fit_int and fit_main) else None
    inter_detected = bool(reversals) or any(t["strong_interaction"] for t in interaction_terms) or (r2_lift is not None and r2_lift >= 0.10)

    return {
        "$schema": "interaction-surrogate-report/v1",
        "ok": True,
        "response_metric": response_col,
        "objective_direction": direction,
        "factors": factors,
        "valid_run_count": len(valid_pts),
        "skipped_rows": skipped_rows,
        "main_effects_model": {"r2": fit_main["r2"], "rmse": fit_main["rmse"]} if fit_main else None,
        "interaction_model": {"r2": fit_int["r2"], "rmse": fit_int["rmse"]} if fit_int else None,
        "r2_lift_from_interactions": r2_lift,
        "interaction_reversal_detected": inter_detected,
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
