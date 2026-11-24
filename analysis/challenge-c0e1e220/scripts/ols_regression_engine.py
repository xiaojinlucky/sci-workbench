#!/usr/bin/env python3
"""Pure-stdlib Ordinary Least Squares (OLS) multivariable linear regression engine.

Supports continuous multivariable datasets without natural group columns (e.g., UCI Concrete 1030):
  - Exact OLS coefficients via Gauss-Jordan normal equations (with multicollinearity rank guard)
  - Per-predictor SE, t-statistic, two-sided p-value, 95% CI, standardized beta, and VIF
  - Overall R^2, adjusted R^2, omnibus F-statistic, RMSE, MAE
  - Residual diagnostics: Jarque-Bera / Shapiro normality + Breusch-Pagan heteroscedasticity test
  - Optional continuous stratification (e.g., curing age strata)
  - Pure stdlib vector SVG diagnostic plot (Observed vs Predicted + Residuals) + optional Matplotlib PNG/PDF
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from assumption_diagnostics import (  # noqa: E402
    chi2_sf,
    f_sf,
    is_missing_raw,
    load_table_ro,
    sample_mean_var,
    t_ppf,
    t_sf_two_sided,
    try_parse_float,
)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
except Exception:
    _plt = None


def _invert_matrix(mat: list[list[float]]) -> list[list[float]] | None:
    """Invert a square matrix via Gauss-Jordan elimination with partial pivoting."""
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


def fit_ols(X_rows: list[list[float]], y_vals: list[float], x_names: list[str]) -> dict[str, Any]:
    """Fit multivariable OLS with intercept and compute full inference + residual diagnostics."""
    n = len(y_vals)
    p = len(x_names)
    k = p + 1  # intercept + p predictors
    if n <= k + 1:
        return {"ok": False, "estimable": False, "reason": f"Insufficient observations (n={n}) for {p} predictors + intercept"}

    # Build design matrix with leading intercept column 1.0
    D = [[1.0] + row for row in X_rows]
    # Compute X^T X and X^T y
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for i in range(n):
        di = D[i]
        yi = y_vals[i]
        for a in range(k):
            xty[a] += di[a] * yi
            da = di[a]
            for b in range(a, k):
                xtx[a][b] += da * di[b]
    for a in range(k):
        for b in range(a):
            xtx[a][b] = xtx[b][a]

    xtx_inv = _invert_matrix(xtx)
    if xtx_inv is None:
        return {"ok": False, "estimable": False, "reason": "Singular or perfectly collinear design matrix (X^T X not invertible)"}

    beta = [sum(xtx_inv[a][b] * xty[b] for b in range(k)) for a in range(k)]
    y_hat = [sum(D[i][j] * beta[j] for j in range(k)) for i in range(n)]
    resid = [y_vals[i] - y_hat[i] for i in range(n)]

    y_mean, y_var = sample_mean_var(y_vals)
    y_sd = math.sqrt(y_var) if y_var > 0 else 0.0
    sst = sum((y - y_mean) ** 2 for y in y_vals)
    sse = sum(r * r for r in resid)
    ssr = max(0.0, sst - sse)
    df_model = p
    df_resid = n - k
    mse = sse / df_resid
    rmse = math.sqrt(sse / n)
    mae = sum(abs(r) for r in resid) / n
    r2 = 1.0 - (sse / sst) if sst > 1e-15 else 0.0
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / max(df_resid, 1)
    f_stat = (ssr / max(df_model, 1)) / max(mse, 1e-15)
    f_p = f_sf(f_stat, float(df_model), float(df_resid))
    t_crit = t_ppf(0.975, float(df_resid))

    # Predictor correlation matrix inverse for VIF when p >= 2
    vifs: dict[str, float | None] = {}
    if p == 1:
        vifs[x_names[0]] = 1.0
    else:
        col_stats = [sample_mean_var([row[j] for row in X_rows]) for j in range(p)]
        corr = [[0.0] * p for _ in range(p)]
        for a in range(p):
            ma, va = col_stats[a]
            sda = math.sqrt(va) if va > 0 else 0.0
            for b in range(p):
                mb, vb = col_stats[b]
                sdb = math.sqrt(vb) if vb > 0 else 0.0
                if a == b:
                    corr[a][b] = 1.0
                elif sda > 1e-15 and sdb > 1e-15:
                    cov = sum((X_rows[i][a] - ma) * (X_rows[i][b] - mb) for i in range(n)) / (n - 1)
                    corr[a][b] = cov / (sda * sdb)
        corr_inv = _invert_matrix(corr)
        for j, name in enumerate(x_names):
            vifs[name] = round(float(corr_inv[j][j]), 4) if corr_inv is not None else None

    coef_rows: list[dict[str, Any]] = []
    terms = ["(Intercept)"] + x_names
    for idx, term in enumerate(terms):
        b_val = beta[idx]
        se_val = math.sqrt(max(mse * xtx_inv[idx][idx], 0.0))
        t_val = b_val / se_val if se_val > 1e-15 else math.nan
        p_val = t_sf_two_sided(abs(t_val), float(df_resid)) if math.isfinite(t_val) else math.nan
        ci_lo = b_val - t_crit * se_val
        ci_hi = b_val + t_crit * se_val
        std_beta = None
        if idx > 0 and y_sd > 1e-15:
            _, xv = sample_mean_var([row[idx - 1] for row in X_rows])
            std_beta = round(b_val * math.sqrt(max(xv, 0.0)) / y_sd, 4)
        coef_rows.append({
            "term": term,
            "estimate": round(b_val, 6),
            "std_error": round(se_val, 6),
            "t_statistic": round(t_val, 4) if math.isfinite(t_val) else None,
            "p_value": round(p_val, 8) if math.isfinite(p_val) else None,
            "ci95": [round(ci_lo, 6), round(ci_hi, 6)],
            "standardized_beta": std_beta,
            "vif": vifs.get(term),
            "significant_05": bool(math.isfinite(p_val) and p_val < 0.05),
        })

    # Residual diagnostics: Jarque-Bera normality + Breusch-Pagan heteroscedasticity
    rm, rv = sample_mean_var(resid)
    rsd = math.sqrt(max(rv * (n - 1) / n, 1e-15))
    skew = sum(((r - rm) / rsd) ** 3 for r in resid) / n
    kurt = sum(((r - rm) / rsd) ** 4 for r in resid) / n
    jb_stat = (n / 6.0) * (skew * skew + 0.25 * (kurt - 3.0) ** 2)
    jb_p = chi2_sf(jb_stat, 2)

    # Breusch-Pagan auxiliary regression of r^2 on X
    r2_vals = [r * r for r in resid]
    r2_mean, _ = sample_mean_var(r2_vals)
    r2_sst = sum((v - r2_mean) ** 2 for v in r2_vals)
    aux_xty = [sum(D[i][a] * r2_vals[i] for i in range(n)) for a in range(k)]
    aux_b = [sum(xtx_inv[a][b] * aux_xty[b] for b in range(k)) for a in range(k)]
    aux_hat = [sum(D[i][j] * aux_b[j] for j in range(k)) for i in range(n)]
    aux_sse = sum((r2_vals[i] - aux_hat[i]) ** 2 for i in range(n))
    aux_r2 = max(0.0, 1.0 - aux_sse / max(r2_sst, 1e-15))
    bp_lm = n * aux_r2
    bp_p = chi2_sf(bp_lm, p)

    warnings: list[str] = []
    if jb_p < 0.05:
        warnings.append(f"Residuals deviate from normality (Jarque-Bera p={jb_p:.6f}); inspect nonlinear terms or robust SEs.")
    if bp_p < 0.05:
        warnings.append(f"Heteroscedasticity detected (Breusch-Pagan p={bp_p:.6f}); interpret classical OLS SEs with caution.")
    high_vif = [c["term"] for c in coef_rows if c["vif"] is not None and c["vif"] > 5.0]
    if high_vif:
        warnings.append(f"Elevated collinearity (VIF > 5) detected in: {high_vif}")

    return {
        "ok": True,
        "estimable": True,
        "n": n,
        "n_predictors": p,
        "df_model": df_model,
        "df_resid": df_resid,
        "r_squared": round(r2, 6),
        "adj_r_squared": round(adj_r2, 6),
        "f_statistic": round(f_stat, 4),
        "f_p_value": round(f_p, 8),
        "rmse": round(rmse, 6),
        "mae": round(mae, 6),
        "coefficients": coef_rows,
        "residual_diagnostics": {
            "jarque_bera_stat": round(jb_stat, 4),
            "jarque_bera_p": round(jb_p, 8),
            "normality_ok": bool(jb_p >= 0.05),
            "breusch_pagan_lm": round(bp_lm, 4),
            "breusch_pagan_p": round(bp_p, 8),
            "homoscedasticity_ok": bool(bp_p >= 0.05),
        },
        "warnings": warnings,
        "y_observed": y_vals,
        "y_predicted": y_hat,
        "residuals": resid,
    }


def render_ols_diagnostic_svg(
    out_svg: Path,
    *,
    y_obs: list[float],
    y_hat: list[float],
    y_label: str,
    r2: float,
    rmse: float,
    width_mm: float = 114.0,
    height_mm: float = 86.0,
) -> dict[str, Any]:
    """Render publication-grade Observed vs Predicted OLS diagnostic SVG in pure stdlib."""
    w_px = width_mm * 3.779527559
    h_px = height_mm * 3.779527559
    ml, mr, mt, mb = 58.0, 20.0, 38.0, 48.0
    pw, ph = w_px - ml - mr, h_px - mt - mb

    lo = min(min(y_obs), min(y_hat))
    hi = max(max(y_obs), max(y_hat))
    pad = max((hi - lo) * 0.06, 0.5)
    vmin, vmax = lo - pad, hi + pad

    def map_x(v: float) -> float:
        return ml + pw * (v - vmin) / max(vmax - vmin, 1e-12)

    def map_y(v: float) -> float:
        return mt + ph * (1.0 - (v - vmin) / max(vmax - vmin, 1e-12))

    # Downsample plotted points visually if > 600? No, keep all up to 1500 cleanly with low opacity
    title = f"OLS Predicted vs Observed: {y_label} (R^2={r2:.3f}, RMSE={rmse:.2f}, n={len(y_obs)})"
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm" height="{height_mm}mm" viewBox="0 0 {w_px:.2f} {h_px:.2f}">',
        '  <rect x="0" y="0" width="100%" height="100%" fill="#FFFFFF"/>',
        f'  <text x="{ml + pw / 2:.2f}" y="22" text-anchor="middle" font-family="Arial, DejaVu Sans, sans-serif" font-size="10" fill="#111111">{html.escape(title)}</text>',
    ]
    for ti in range(5):
        tv = vmin + (vmax - vmin) * (ti / 4.0)
        xp, yp = map_x(tv), map_y(tv)
        lines.append(f'  <line x1="{ml:.2f}" y1="{yp:.2f}" x2="{ml + pw:.2f}" y2="{yp:.2f}" stroke="#E5E7EB" stroke-width="0.5"/>')
        lines.append(f'  <text x="{ml - 6:.2f}" y="{yp + 3:.2f}" text-anchor="end" font-family="Arial, DejaVu Sans, sans-serif" font-size="8.5" fill="#222222">{tv:.1f}</text>')
        lines.append(f'  <text x="{xp:.2f}" y="{mt + ph + 14:.2f}" text-anchor="middle" font-family="Arial, DejaVu Sans, sans-serif" font-size="8.5" fill="#222222">{tv:.1f}</text>')

    # 1:1 identity reference line
    lines.append(
        f'  <line x1="{map_x(lo):.2f}" y1="{map_y(lo):.2f}" x2="{map_x(hi):.2f}" y2="{map_y(hi):.2f}" stroke="#D97757" stroke-width="1.2" stroke-dasharray="4,3"/>'
    )
    for yo, yh in zip(y_obs, y_hat):
        lines.append(f'  <circle cx="{map_x(yh):.2f}" cy="{map_y(yo):.2f}" r="1.8" fill="#4C78A8" fill-opacity="0.45"/>')

    lines.append(f'  <line x1="{ml:.2f}" y1="{mt:.2f}" x2="{ml:.2f}" y2="{mt + ph:.2f}" stroke="#111111" stroke-width="0.9"/>')
    lines.append(f'  <line x1="{ml:.2f}" y1="{mt + ph:.2f}" x2="{ml + pw:.2f}" y2="{mt + ph:.2f}" stroke="#111111" stroke-width="0.9"/>')
    lines.append(f'  <text x="{ml + pw / 2:.2f}" y="{h_px - 10:.2f}" text-anchor="middle" font-family="Arial, DejaVu Sans, sans-serif" font-size="9.5" fill="#111111">Predicted {html.escape(y_label)}</text>')
    lines.append(f'  <text x="15" y="{mt + ph / 2:.2f}" text-anchor="middle" transform="rotate(-90 15 {mt + ph / 2:.2f})" font-family="Arial, DejaVu Sans, sans-serif" font-size="9.5" fill="#111111">Observed {html.escape(y_label)}</text>')
    lines.append("</svg>")
    out_svg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"svg": out_svg.name, "width_mm": width_mm, "height_mm": height_mm, "points_plotted": len(y_obs)}


def run_ols_analysis(
    input_path: Path,
    *,
    y_col: str,
    x_cols: list[str],
    out_dir: Path,
    stem: str = "ols_report",
    table: str | None = None,
    stratify_col: str | None = None,
    unit: str = "",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cols, rows, _meta = load_table_ro(input_path, table=table)
    if y_col not in cols:
        raise KeyError(f"Outcome column {y_col!r} not in {cols}")
    if not x_cols:
        x_cols = [c for c in cols if c != y_col and not c.lower().endswith("_id")]
    missing_x = [c for c in x_cols if c not in cols]
    if missing_x:
        raise KeyError(f"Predictor columns missing: {missing_x}")

    X_clean: list[list[float]] = []
    y_clean: list[float] = []
    strata_vals: list[str] = []
    dropped = 0
    for r in rows:
        yv = try_parse_float(r.get(y_col, ""))
        xvs = [try_parse_float(r.get(xc, "")) for xc in x_cols]
        if yv is None or any(v is None for v in xvs):
            dropped += 1
            continue
        X_clean.append([float(v) for v in xvs])  # type: ignore[arg-type]
        y_clean.append(float(yv))
        if stratify_col and stratify_col in cols:
            strata_vals.append(str(r.get(stratify_col, "")).strip())

    fit = fit_ols(X_clean, y_clean, x_cols)
    if not fit.get("ok"):
        return fit

    # Optional stratified summary (e.g., curing age bins <=7, 8-28, >28 days)
    strata_report: list[dict[str, Any]] = []
    if stratify_col and strata_vals:
        parsed_s = [try_parse_float(s) for s in strata_vals]
        if all(v is not None for v in parsed_s):
            bins = [("early (<=7)", lambda v: v <= 7), ("standard (8-28)", lambda v: 7 < v <= 28), ("mature (>28)", lambda v: v > 28)]
            for blabel, pred in bins:
                sub_y = [y_clean[i] for i, v in enumerate(parsed_s) if pred(float(v))]  # type: ignore[arg-type]
                if len(sub_y) >= 2:
                    m, var = sample_mean_var(sub_y)
                    strata_report.append({"stratum": blabel, "n": len(sub_y), "mean": round(m, 4), "sd": round(math.sqrt(var), 4)})
        else:
            for sv in dict.fromkeys(strata_vals):
                sub_y = [y_clean[i] for i, s in enumerate(strata_vals) if s == sv]
                if len(sub_y) >= 2:
                    m, var = sample_mean_var(sub_y)
                    strata_report.append({"stratum": sv, "n": len(sub_y), "mean": round(m, 4), "sd": round(math.sqrt(var), 4)})

    ylabel = f"{y_col} ({unit})" if unit else y_col
    svg_p = out_dir / f"{stem}_ols_plot.svg"
    plot_meta = render_ols_diagnostic_svg(
        svg_p, y_obs=fit["y_observed"], y_hat=fit["y_predicted"], y_label=ylabel, r2=fit["r_squared"], rmse=fit["rmse"]
    )

    mpl_exports: list[str] = []
    if _plt is not None:
        fig, ax = _plt.subplots(figsize=(114.0 / 25.4, 86.0 / 25.4), dpi=300)
        ax.scatter(fit["y_predicted"], fit["y_observed"], s=10, color="#4C78A8", alpha=0.45, edgecolors="none")
        lo, hi = min(fit["y_observed"]), max(fit["y_observed"])
        ax.plot([lo, hi], [lo, hi], linestyle="--", color="#D97757", linewidth=1.1)
        ax.set_xlabel(f"Predicted {ylabel}", fontsize=8.5)
        ax.set_ylabel(f"Observed {ylabel}", fontsize=8.5)
        ax.set_title(f"OLS Fit: {y_col} (R^2={fit['r_squared']:.3f}, n={fit['n']})", loc="center", fontsize=9.0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.subplots_adjust(left=0.16, right=0.96, top=0.88, bottom=0.16)
        png_p, pdf_p = out_dir / f"{stem}_ols_plot.png", out_dir / f"{stem}_ols_plot.pdf"
        fig.savefig(png_p, format="png", dpi=300, bbox_inches=None)
        fig.savefig(pdf_p, format="pdf", bbox_inches=None)
        _plt.close(fig)
        mpl_exports = [png_p.name, pdf_p.name]

    payload = {
        "ok": True,
        "design": "ols_regression",
        "source": input_path.name,
        "outcome": y_col,
        "predictors": x_cols,
        "rows_raw": len(rows),
        "rows_dropped_missing": dropped,
        "n_analyzed": fit["n"],
        "r_squared": fit["r_squared"],
        "adj_r_squared": fit["adj_r_squared"],
        "f_statistic": fit["f_statistic"],
        "f_p_value": fit["f_p_value"],
        "rmse": fit["rmse"],
        "mae": fit["mae"],
        "coefficients": fit["coefficients"],
        "residual_diagnostics": fit["residual_diagnostics"],
        "stratified_summary": strata_report,
        "warnings": fit["warnings"],
        "plot": {**plot_meta, "matplotlib_exports": mpl_exports},
        "interpretation_boundary": "Regression coefficients represent conditional linear associations within the observed covariate range and do not by themselves establish causal effects.",
    }
    jp = out_dir / f"{stem}.json"
    jp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md = [
        f"# Multivariable OLS Regression Report ({input_path.name})",
        "",
        f"- Outcome: `{ylabel}` | Analyzed n: **{fit['n']}** (dropped missing: {dropped})",
        f"- Model fit: **R^2 = {fit['r_squared']}**, **Adj. R^2 = {fit['adj_r_squared']}**, F({fit['df_model']}, {fit['df_resid']}) = {fit['f_statistic']} (`p = {fit['f_p_value']}`), RMSE = {fit['rmse']}",
        f"- Residual diagnostics: Jarque-Bera p = `{fit['residual_diagnostics']['jarque_bera_p']}` | Breusch-Pagan p = `{fit['residual_diagnostics']['breusch_pagan_p']}`",
        "",
        "| Term | Estimate | SE | 95% CI | t | p-value | Std. Beta | VIF |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for c in fit["coefficients"]:
        md.append(
            f"| `{c['term']}` | {c['estimate']} | {c['std_error']} | {c['ci95']} | {c['t_statistic']} | {c['p_value']} | {c['standardized_beta']} | {c['vif']} |"
        )
    if strata_report:
        md.extend(["", f"## Stratified Outcome Summary by `{stratify_col}`", "", "| Stratum | n | Mean | SD |", "| --- | ---: | ---: | ---: |"])
        for sr in strata_report:
            md.append(f"| {sr['stratum']} | {sr['n']} | {sr['mean']} | {sr['sd']} |")
    if fit["warnings"]:
        md.extend(["", "## Diagnostic Warnings", ""])
        for w in fit["warnings"]:
            md.append(f"- {w}")
    mdp = out_dir / f"{stem}.md"
    mdp.write_text("\n".join(md) + "\n", encoding="utf-8")
    return {"ok": True, "json": jp.name, "md": mdp.name, "svg": svg_p.name, "r_squared": fit["r_squared"], "adj_r_squared": fit["adj_r_squared"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pure-stdlib multivariable OLS regression with 95%% CI, VIF, and residual diagnostics.")
    parser.add_argument("--input", required=True, help="CSV, JSON, or SQLite file path.")
    parser.add_argument("--table", default=None, help="SQLite table name.")
    parser.add_argument("--y-col", required=True, help="Continuous outcome column.")
    parser.add_argument("--x-cols", default="", help="Comma-separated predictor columns (empty = all other numeric columns).")
    parser.add_argument("--stratify-col", default=None, help="Optional stratification column (e.g. age_days).")
    parser.add_argument("--unit", default="", help="Outcome measurement unit (e.g. MPa, mg/L).")
    parser.add_argument("--out-dir", required=True, help="Output directory.")
    parser.add_argument("--stem", default="ols_report", help="Output filename stem.")
    args = parser.parse_args(argv)

    x_list = [c.strip() for c in args.x_cols.split(",") if c.strip()]
    rep = run_ols_analysis(
        Path(args.input),
        y_col=args.y_col,
        x_cols=x_list,
        out_dir=Path(args.out_dir),
        stem=args.stem,
        table=args.table,
        stratify_col=args.stratify_col,
        unit=args.unit,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
