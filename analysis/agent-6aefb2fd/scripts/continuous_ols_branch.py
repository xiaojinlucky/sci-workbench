#!/usr/bin/env python3
"""Pure-stdlib continuous multivariable analysis branch (descriptive, Pearson/Spearman, OLS + SVG) for agent-6aefb2fd."""

from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any


def _mean_var(vals: list[float]) -> tuple[float, float]:
    n = len(vals)
    if n == 0:
        return math.nan, math.nan
    m = sum(vals) / n
    if n == 1:
        return m, 0.0
    return m, sum((x - m) ** 2 for x in vals) / (n - 1)


def _normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _t_two_sided_p(t_abs: float, df: float) -> float:
    if not math.isfinite(t_abs) or df <= 0:
        return math.nan
    if df <= 1.05:
        return float(1.0 - (2.0 / math.pi) * math.atan(t_abs))
    z = t_abs * (1.0 - 1.0 / (4.0 * df)) / math.sqrt(1.0 + (t_abs * t_abs) / (2.0 * df))
    return min(1.0, max(0.0, 2.0 * _normal_sf(z)))


def _t_crit_95(df: float) -> float:
    if df <= 1.0:
        return 12.7062
    z = 1.95996398454
    return z + (z**3 + z) / (4.0 * df) + (5.0 * z**5 + 16.0 * z**3 + 3.0 * z) / (96.0 * df * df)


def _chi2_sf(x: float, df: int) -> float:
    if x <= 0:
        return 1.0
    if df == 1:
        return min(1.0, max(0.0, 2.0 * _normal_sf(math.sqrt(x))))
    if df == 2:
        return math.exp(-x / 2.0)
    k = float(df)
    z = ((x / k) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * k))) / math.sqrt(2.0 / (9.0 * k))
    return min(1.0, max(0.0, _normal_sf(z)))


def _f_sf(f_stat: float, df1: float, df2: float) -> float:
    if not math.isfinite(f_stat) or f_stat < 0 or df1 <= 0 or df2 <= 0:
        return math.nan
    a, b = 2.0 / (9.0 * df1), 2.0 / (9.0 * df2)
    num = (1.0 - b) * (f_stat ** (1.0 / 3.0)) - (1.0 - a)
    den = math.sqrt(b * (f_stat ** (2.0 / 3.0)) + a)
    return 0.0 if den <= 0 else min(1.0, max(0.0, _normal_sf(num / den)))


def _ranks(vals: list[float]) -> list[float]:
    n = len(vals)
    idx = sorted(range(n), key=lambda i: vals[i])
    r = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[idx[j + 1]] == vals[idx[i]]:
            j += 1
        avg = 0.5 * (i + j) + 1.0
        for k in range(i, j + 1):
            r[idx[k]] = avg
        i = j + 1
    return r


def _pearson(x: list[float], y: list[float]) -> tuple[float, float]:
    n = len(x)
    mx, vx = _mean_var(x)
    my, vy = _mean_var(y)
    if n < 3 or vx <= 1e-15 or vy <= 1e-15:
        return 0.0, 1.0
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / (n - 1)
    r = max(-1.0, min(1.0, cov / math.sqrt(vx * vy)))
    if abs(r) >= 1.0 - 1e-12:
        return r, 0.0
    t_val = abs(r) * math.sqrt((n - 2) / max(1.0 - r * r, 1e-15))
    return r, _t_two_sided_p(t_val, float(n - 2))


def _invert_matrix(mat: list[list[float]]) -> list[list[float]] | None:
    n = len(mat)
    aug = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(mat)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            return None
        aug[col], aug[piv] = aug[piv], aug[col]
        div = aug[col][col]
        aug[col] = [v / div for v in aug[col]]
        for r in range(n):
            if r != col and abs(aug[r][col]) > 0.0:
                fac = aug[r][col]
                aug[r] = [aug[r][k] - fac * aug[col][k] for k in range(2 * n)]
    return [row[n:] for row in aug]


def infer_column_roles(intake: dict[str, Any], *, group_col: str | None = None, value_col: str | None = None) -> dict[str, Any]:
    """Automatically infer group_comparison vs continuous_multivariate mode from intake profile."""
    cols = intake.get("columns") or []
    col_names = [c["column"] for c in cols]
    if group_col and value_col:
        return {"mode": "group_comparison", "group_col": group_col, "value_col": value_col, "x_cols": [], "inferred": False}

    group_hints = ("species", "cultivar", "group", "treatment", "cohort", "condition", "class", "category", "arm")
    cat_candidates = [
        c for c in cols
        if (not c["is_numeric_continuous"])
        and (not c["leading_zero_string_id"])
        and 2 <= int(c["unique_count"]) <= 12
        and (not c["column"].lower().endswith("_id"))
    ]
    # Prefer named categorical columns over coded integers
    named_cats = [c for c in cat_candidates if not c["coded_categorical_integer"]]
    hinted_cats = [c for c in cat_candidates if any(h in c["column"].lower() for h in group_hints)]
    num_cont = [
        c["column"] for c in cols
        if c["is_numeric_continuous"] and (not c["column"].lower().endswith("_id")) and c["column"].lower() != "mix_id"
    ]

    if group_col and group_col in col_names and num_cont:
        target_y = value_col if (value_col and value_col in col_names) else num_cont[-1]
        return {"mode": "group_comparison", "group_col": group_col, "value_col": target_y, "x_cols": [], "inferred": True}

    if (named_cats or hinted_cats) and num_cont:
        chosen_g = (hinted_cats[0] if hinted_cats else named_cats[0])["column"]
        target_y = value_col if (value_col and value_col in col_names) else num_cont[-1]
        return {"mode": "group_comparison", "group_col": chosen_g, "value_col": target_y, "x_cols": [], "inferred": True}

    if len(num_cont) >= 2:
        target_y = value_col if (value_col and value_col in num_cont) else num_cont[-1]
        x_cols = [c for c in num_cont if c != target_y]
        return {"mode": "continuous_multivariate", "group_col": None, "value_col": target_y, "x_cols": x_cols, "inferred": True}

    raise ValueError(f"Cannot automatically infer analysis roles from columns: {col_names}")


def run_continuous_multivariate_stats(cleaned_rows: list[dict[str, str]], *, y_col: str, x_cols: list[str], unit: str = "") -> dict[str, Any]:
    """Compute descriptive stats, Pearson/Spearman correlations, and pure-stdlib multivariable OLS."""
    n = len(cleaned_rows)
    y_vals = [float(r[y_col]) for r in cleaned_rows]
    X_rows = [[float(r[xc]) for xc in x_cols] for r in cleaned_rows]

    # Per-variable descriptive stats & correlation with y
    var_summaries: list[dict[str, Any]] = []
    correlations: list[dict[str, Any]] = []
    y_ranks = _ranks(y_vals)
    for col in x_cols + [y_col]:
        vals = sorted(float(r[col]) for r in cleaned_rows)
        m, v = _mean_var(vals)
        q1 = vals[int(0.25 * (n - 1))]
        med = vals[int(0.50 * (n - 1))]
        q3 = vals[int(0.75 * (n - 1))]
        var_summaries.append({
            "variable": col, "n": n, "mean": round(m, 4), "sd": round(math.sqrt(v), 4),
            "median": round(med, 4), "iqr": round(q3 - q1, 4), "min": round(vals[0], 4), "max": round(vals[-1], 4),
        })
    for j, xc in enumerate(x_cols):
        xv = [row[j] for row in X_rows]
        pr, pp = _pearson(xv, y_vals)
        sr, sp = _pearson(_ranks(xv), y_ranks)
        correlations.append({
            "predictor": xc,
            "pearson_r": round(pr, 4),
            "pearson_p": round(pp, 8),
            "spearman_rho": round(sr, 4),
            "spearman_p": round(sp, 8),
        })
    correlations.sort(key=lambda d: abs(d["pearson_r"]), reverse=True)

    # Pure-stdlib OLS normal equations
    p = len(x_cols)
    k = p + 1
    D = [[1.0] + row for row in X_rows]
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for i in range(n):
        di, yi = D[i], y_vals[i]
        for a in range(k):
            xty[a] += di[a] * yi
            for b in range(a, k):
                xtx[a][b] += di[a] * di[b]
    for a in range(k):
        for b in range(a):
            xtx[a][b] = xtx[b][a]
    xtx_inv = _invert_matrix(xtx)
    if xtx_inv is None:
        raise ValueError("Collinear/singular continuous design matrix; remove redundant predictors")

    beta = [sum(xtx_inv[a][b] * xty[b] for b in range(k)) for a in range(k)]
    y_hat = [sum(D[i][j] * beta[j] for j in range(k)) for i in range(n)]
    resid = [y_vals[i] - y_hat[i] for i in range(n)]
    ym, yv = _mean_var(y_vals)
    sst = sum((y - ym) ** 2 for y in y_vals)
    sse = sum(r * r for r in resid)
    df_resid = max(n - k, 1)
    mse = sse / df_resid
    r2 = 1.0 - sse / max(sst, 1e-15)
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / df_resid
    f_stat = ((max(0.0, sst - sse)) / max(p, 1)) / max(mse, 1e-15)
    f_p = _f_sf(f_stat, float(p), float(df_resid))
    tcrit = _t_crit_95(float(df_resid))

    coefs: list[dict[str, Any]] = []
    for idx, term in enumerate(["(Intercept)"] + x_cols):
        se = math.sqrt(max(mse * xtx_inv[idx][idx], 0.0))
        t_v = beta[idx] / max(se, 1e-15)
        pv = _t_two_sided_p(abs(t_v), float(df_resid))
        coefs.append({
            "term": term, "estimate": round(beta[idx], 6), "se": round(se, 6),
            "ci95": [round(beta[idx] - tcrit * se, 6), round(beta[idx] + tcrit * se, 6)],
            "t_stat": round(t_v, 4), "p_value": round(pv, 8), "significant_05": bool(pv < 0.05),
        })

    # Residual Jarque-Bera normality
    rm, rv = _mean_var(resid)
    rsd = math.sqrt(max(rv * (n - 1) / n, 1e-15))
    skew = sum(((r - rm) / rsd) ** 3 for r in resid) / n
    kurt = sum(((r - rm) / rsd) ** 4 for r in resid) / n
    jb = (n / 6.0) * (skew * skew + 0.25 * (kurt - 3.0) ** 2)
    jb_p = _chi2_sf(jb, 2)
    warnings = []
    if jb_p < 0.05:
        warnings.append(f"OLS residuals deviate from normality (Jarque-Bera p={jb_p:.6f}); consider nonlinear terms or robust SEs.")

    return {
        "ok": True,
        "analysis_mode": "continuous_multivariate",
        "stats_backend": "pure_stdlib_ols",
        "value_col": y_col,
        "x_cols": x_cols,
        "unit": unit,
        "rows_analyzed": n,
        "variable_summaries": var_summaries,
        "correlations": correlations,
        "ols_model": {
            "r_squared": round(r2, 6),
            "adj_r_squared": round(adj_r2, 6),
            "f_statistic": round(f_stat, 4),
            "df_model": p,
            "df_resid": df_resid,
            "f_p_value": round(f_p, 8),
            "rmse": round(math.sqrt(sse / n), 6),
            "jarque_bera_p": round(jb_p, 8),
            "coefficients": coefs,
        },
        "assumption_warnings": warnings,
        "y_observed_range": [round(min(y_vals), 4), round(max(y_vals), 4)],
        "y_predicted_range": [round(min(y_hat), 4), round(max(y_hat), 4)],
        "_plot_points": list(zip(y_hat, y_vals)),
    }


def render_ols_svg(out_svg: Path, *, plot_points: list[tuple[float, float]], y_label: str, r2: float, rmse: float, width_mm: float = 114.0, height_mm: float = 84.0) -> dict[str, Any]:
    w_px, h_px = width_mm * 3.779527559, height_mm * 3.779527559
    ml, mr, mt, mb = 58.0, 20.0, 38.0, 48.0
    pw, ph = w_px - ml - mr, h_px - mt - mb
    xs = [p[0] for p in plot_points]
    ys = [p[1] for p in plot_points]
    lo, hi = min(min(xs), min(ys)), max(max(xs), max(ys))
    pad = max((hi - lo) * 0.06, 0.5)
    vmin, vmax = lo - pad, hi + pad
    mx = lambda v: ml + pw * (v - vmin) / max(vmax - vmin, 1e-12)
    my = lambda v: mt + ph * (1.0 - (v - vmin) / max(vmax - vmin, 1e-12))

    title = f"OLS Fit: {y_label} (R^2={r2:.3f}, RMSE={rmse:.2f}, n={len(ys)})"
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm" height="{height_mm}mm" viewBox="0 0 {w_px:.2f} {h_px:.2f}">',
        '  <rect x="0" y="0" width="100%" height="100%" fill="#FFFFFF"/>',
        f'  <text x="{ml + pw / 2:.2f}" y="22" text-anchor="middle" font-family="Arial, DejaVu Sans, sans-serif" font-size="10" fill="#111111">{html.escape(title)}</text>',
    ]
    for ti in range(5):
        tv = vmin + (vmax - vmin) * (ti / 4.0)
        lines.append(f'  <line x1="{ml:.2f}" y1="{my(tv):.2f}" x2="{ml + pw:.2f}" y2="{my(tv):.2f}" stroke="#E5E7EB" stroke-width="0.5"/>')
        lines.append(f'  <text x="{ml - 6:.2f}" y="{my(tv) + 3:.2f}" text-anchor="end" font-family="Arial, DejaVu Sans, sans-serif" font-size="8.5" fill="#222222">{tv:.1f}</text>')
        lines.append(f'  <text x="{mx(tv):.2f}" y="{mt + ph + 14:.2f}" text-anchor="middle" font-family="Arial, DejaVu Sans, sans-serif" font-size="8.5" fill="#222222">{tv:.1f}</text>')
    lines.append(f'  <line x1="{mx(lo):.2f}" y1="{my(lo):.2f}" x2="{mx(hi):.2f}" y2="{my(hi):.2f}" stroke="#D77C70" stroke-width="1.2" stroke-dasharray="4,3"/>')
    for xh, yo in plot_points:
        lines.append(f'  <circle cx="{mx(xh):.2f}" cy="{my(yo):.2f}" r="1.8" fill="#547FA3" fill-opacity="0.45"/>')
    lines.append(f'  <line x1="{ml:.2f}" y1="{mt:.2f}" x2="{ml:.2f}" y2="{mt + ph:.2f}" stroke="#111111" stroke-width="0.9"/>')
    lines.append(f'  <line x1="{ml:.2f}" y1="{mt + ph:.2f}" x2="{ml + pw:.2f}" y2="{mt + ph:.2f}" stroke="#111111" stroke-width="0.9"/>')
    lines.append(f'  <text x="{ml + pw / 2:.2f}" y="{h_px - 10:.2f}" text-anchor="middle" font-family="Arial, DejaVu Sans, sans-serif" font-size="9.5" fill="#111111">Predicted {html.escape(y_label)}</text>')
    lines.append(f'  <text x="15" y="{mt + ph / 2:.2f}" text-anchor="middle" transform="rotate(-90 15 {mt + ph / 2:.2f})" font-family="Arial, DejaVu Sans, sans-serif" font-size="9.5" fill="#111111">Observed {html.escape(y_label)}</text>')
    lines.append("</svg>")
    out_svg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"backend": "pure_stdlib_svg_ols", "width_mm": width_mm, "height_mm": height_mm, "value_range_covered": True, "points_plotted": len(ys)}
