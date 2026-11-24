#!/usr/bin/env python3
"""Checkpointed multi-step scientific data analysis pipeline (dual-backend: pure stdlib + Scientific Python).

Executes 5 explicit, resumable stages with persisted disk artifacts & SHA-256 hashes:
  S1_intake -> S2_clean -> S3_stats -> S4_viz -> S5_report
Guarantees:
  - Resuming (`--resume`) verifies upstream input_sha256 & persisted artifact hashes before skipping
    completed stages; never treats unpersisted in-memory text as completed output.
  - Any stage failure persists `FAILED` status with diagnostics and stops downstream stages honestly.
  - Runs 100% offline on pure Python 3.12 stdlib (including Welch ANOVA, Welch-Satterthwaite t-test,
    Kruskal-Wallis, Mann-Whitney U, Hedges' g, Cliff's delta, Holm-Bonferroni, and Cell-styled SVG);
    automatically upgrades to SciPy exact distributions & Matplotlib PNG/PDF/SVG when installed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import itertools
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dataset_intake_profiler import _MISSING_TOKENS, _quantile_sorted, load_records_ro, profile_loaded

try:
    from scipy import stats as _SCIPY_STATS
except Exception:
    _SCIPY_STATS = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _PLT
except Exception:
    _PLT = None

CELL_PALETTE = ["#547FA3", "#D77C70", "#4E9187", "#D19A45", "#967A9C", "#66A9B4"]
STAGES = ("S1_intake", "S2_clean", "S3_stats", "S4_viz", "S5_report")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, obj: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return _file_sha256(path)


# ---------- Pure-stdlib statistical primitives (used when SciPy absent) ----------

def _normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _chi2_sf(x: float, df: int) -> float:
    """Upper tail survival function of chi-squared distribution (exact series/Wilson-Hilferty fallback)."""
    if x <= 0.0:
        return 1.0
    if df <= 0:
        return math.nan
    if _SCIPY_STATS is not None:
        return float(_SCIPY_STATS.chi2.sf(x, df))
    if df == 1:
        return min(1.0, max(0.0, 2.0 * _normal_sf(math.sqrt(x))))
    if df == 2:
        return math.exp(-x / 2.0)
    # Wilson-Hilferty cube-root normal approximation (accurate for df >= 3)
    k = float(df)
    z = ((x / k) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * k))) / math.sqrt(2.0 / (9.0 * k))
    return min(1.0, max(0.0, _normal_sf(z)))


def _t_crit_95(df: float) -> float:
    if _SCIPY_STATS is not None and df > 0:
        return float(_SCIPY_STATS.t.ppf(0.975, df=df))
    # Cornish-Fisher approximation for two-sided 95% t quantile (z = 1.959964)
    if df <= 1.0:
        return 12.7062
    z = 1.95996398454
    return z + (z**3 + z) / (4.0 * df) + (5.0 * z**5 + 16.0 * z**3 + 3.0 * z) / (96.0 * df * df)


def _t_two_sided_p(t_abs: float, df: float) -> float:
    if not math.isfinite(t_abs) or df <= 0:
        return math.nan
    if _SCIPY_STATS is not None:
        return float(2.0 * _SCIPY_STATS.t.sf(t_abs, df=df))
    # Accurate Wallace/normal mapping for moderate/large df, exact df=1 Cauchy
    if df <= 1.05:
        return float(1.0 - (2.0 / math.pi) * math.atan(t_abs))
    z = t_abs * (1.0 - 1.0 / (4.0 * df)) / math.sqrt(1.0 + (t_abs * t_abs) / (2.0 * df))
    return min(1.0, max(0.0, 2.0 * _normal_sf(z)))


def _f_sf(f_stat: float, df1: float, df2: float) -> float:
    if not math.isfinite(f_stat) or f_stat < 0 or df1 <= 0 or df2 <= 0:
        return math.nan
    if _SCIPY_STATS is not None:
        return float(_SCIPY_STATS.f.sf(f_stat, df1, df2))
    # Paulson-Camp normal approximation to F distribution
    a = 2.0 / (9.0 * df1)
    b = 2.0 / (9.0 * df2)
    num = (1.0 - b) * (f_stat ** (1.0 / 3.0)) - (1.0 - a)
    den = math.sqrt(b * (f_stat ** (2.0 / 3.0)) + a)
    if den <= 0:
        return 0.0
    return min(1.0, max(0.0, _normal_sf(num / den)))


def _mean_var(vals: list[float]) -> tuple[float, float]:
    n = len(vals)
    if n == 0:
        return math.nan, math.nan
    m = sum(vals) / n
    if n == 1:
        return m, 0.0
    v = sum((x - m) ** 2 for x in vals) / (n - 1)
    return m, v


def _normality_check(vals: list[float]) -> dict[str, Any]:
    n = len(vals)
    if n < 3:
        return {"test": "none", "p_value": None, "status": "underpowered_n_lt_3"}
    m, v = _mean_var(vals)
    if v <= 1e-15:
        return {"test": "none", "p_value": None, "status": "zero_variance_degenerate"}
    if _SCIPY_STATS is not None and n <= 5000:
        res = _SCIPY_STATS.shapiro(vals)
        p = float(res.pvalue)
        return {"test": "Shapiro_Wilk", "stat": round(float(res.statistic), 6), "p_value": round(p, 6), "status": "pass" if p >= 0.05 else "non_normal"}
    # Pure stdlib Jarque-Bera skewness/kurtosis normality screen
    sd = math.sqrt(v * (n - 1) / n)
    skew = sum(((x - m) / sd) ** 3 for x in vals) / n
    kurt = sum(((x - m) / sd) ** 4 for x in vals) / n
    jb = (n / 6.0) * (skew * skew + 0.25 * (kurt - 3.0) ** 2)
    p = _chi2_sf(jb, 2)
    return {"test": "Jarque_Bera_stdlib", "stat": round(jb, 6), "p_value": round(p, 6), "status": "pass" if p >= 0.05 else "non_normal"}


def _brown_forsythe_levene(groups_vals: list[list[float]]) -> dict[str, Any]:
    if len(groups_vals) < 2 or any(len(g) < 2 for g in groups_vals):
        return {"test": "Brown_Forsythe_Levene", "p_value": None, "status": "underpowered_group_lt_2"}
    # Median-centered absolute deviations
    z_groups = []
    for g in groups_vals:
        med = _quantile_sorted(sorted(g), 0.5)
        z_groups.append([abs(x - med) for x in g])
    all_z = [z for zg in z_groups for z in zg]
    if max(all_z) - min(all_z) <= 1e-15:
        return {"test": "Brown_Forsythe_Levene", "p_value": 1.0, "status": "degenerate_zero_spread"}
    if _SCIPY_STATS is not None:
        res = _SCIPY_STATS.levene(*groups_vals, center="median")
        p = float(res.pvalue)
        return {"test": "Brown_Forsythe_Levene", "stat": round(float(res.statistic), 6), "p_value": round(p, 6), "status": "equal_var" if p >= 0.05 else "unequal_var"}
    k = len(z_groups)
    n_tot = len(all_z)
    grand = sum(all_z) / n_tot
    ss_b = sum(len(zg) * ((sum(zg) / len(zg)) - grand) ** 2 for zg in z_groups)
    ss_w = sum(sum((z - (sum(zg) / len(zg))) ** 2 for z in zg) for zg in z_groups)
    if ss_w <= 1e-15:
        return {"test": "Brown_Forsythe_Levene", "p_value": 0.0, "status": "unequal_var"}
    f_val = (ss_b / (k - 1)) / (ss_w / (n_tot - k))
    p = _f_sf(f_val, k - 1, n_tot - k)
    return {"test": "Brown_Forsythe_Levene", "stat": round(f_val, 6), "p_value": round(p, 6), "status": "equal_var" if p >= 0.05 else "unequal_var"}


def _welch_anova(groups_vals: list[list[float]]) -> dict[str, Any]:
    """Exact Welch's heteroscedastic one-way ANOVA."""
    k = len(groups_vals)
    ns = [len(g) for g in groups_vals]
    means, vars_ = zip(*(_mean_var(g) for g in groups_vals))
    if any(v <= 1e-15 for v in vars_):
        return {"test": "Welch_ANOVA", "estimable": False, "reason": "zero_variance_group_prevents_inverse_variance_weighting"}
    ws = [n / v for n, v in zip(ns, vars_)]
    w_sum = sum(ws)
    x_bar = sum(w * m for w, m in zip(ws, means)) / w_sum
    num = sum(w * (m - x_bar) ** 2 for w, m in zip(ws, means)) / (k - 1)
    lam = sum(((1.0 - w / w_sum) ** 2) / (n - 1) for w, n in zip(ws, ns))
    den = 1.0 + (2.0 * (k - 2) / (k * k - 1)) * lam
    f_stat = num / den
    df1 = float(k - 1)
    df2 = (k * k - 1) / (3.0 * lam) if lam > 1e-15 else float(sum(ns) - k)
    p = _f_sf(f_stat, df1, df2)
    return {"test": "Welch_ANOVA", "estimable": True, "statistic": round(f_stat, 6), "df1": round(df1, 3), "df2": round(df2, 3), "p_value": p}


def _ranks_with_ties(values: list[float]) -> tuple[list[float], float]:
    n = len(values)
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * n
    tie_corr_sum = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = 0.5 * (i + j) + 1.0
        for idx in range(i, j + 1):
            ranks[indexed[idx][0]] = avg_rank
        t = j - i + 1
        if t > 1:
            tie_corr_sum += t**3 - t
        i = j + 1
    return ranks, tie_corr_sum


def _kruskal_wallis(groups_vals: list[list[float]]) -> dict[str, Any]:
    all_vals = [x for g in groups_vals for x in g]
    n_tot = len(all_vals)
    k = len(groups_vals)
    if _SCIPY_STATS is not None:
        res = _SCIPY_STATS.kruskal(*groups_vals)
        h_stat, p = float(res.statistic), float(res.pvalue)
    else:
        ranks, tie_sum = _ranks_with_ties(all_vals)
        offset = 0
        ss = 0.0
        for g in groups_vals:
            ng = len(g)
            r_sum = sum(ranks[offset : offset + ng])
            ss += (r_sum * r_sum) / ng
            offset += ng
        h_raw = (12.0 / (n_tot * (n_tot + 1))) * ss - 3.0 * (n_tot + 1)
        denom = 1.0 - (tie_sum / (n_tot**3 - n_tot)) if n_tot > 1 else 1.0
        h_stat = h_raw / max(denom, 1e-12)
        p = _chi2_sf(h_stat, k - 1)
    eps_sq = max(0.0, (h_stat - k + 1) / max(n_tot - k, 1))
    return {"test": "Kruskal_Wallis", "estimable": True, "statistic": round(h_stat, 6), "df": k - 1, "p_value": p, "effect_size_name": "epsilon_squared", "effect_size": round(eps_sq, 4)}


def _mann_whitney_u(a: list[float], b: list[float]) -> tuple[float, float]:
    if _SCIPY_STATS is not None:
        res = _SCIPY_STATS.mannwhitneyu(a, b, alternative="two-sided")
        return float(res.statistic), float(res.pvalue)
    na, nb = len(a), len(b)
    n = na + nb
    ranks, tie_sum = _ranks_with_ties(a + b)
    r_a = sum(ranks[:na])
    u_a = r_a - na * (na + 1) / 2.0
    mu = na * nb / 2.0
    var_u = (na * nb / 12.0) * ((n + 1.0) - tie_sum / (n * (n - 1))) if n > 1 else 0.0
    if var_u <= 1e-15:
        return u_a, 1.0
    z = (abs(u_a - mu) - 0.5) / math.sqrt(var_u)
    return u_a, min(1.0, max(0.0, 2.0 * _normal_sf(max(0.0, z))))


def _cliffs_delta(a: list[float], b: list[float]) -> float:
    """Non-parametric dominance effect size P(b > a) - P(b < a) in [-1, 1]."""
    gt = sum(1 for x in a for y in b if y > x)
    lt = sum(1 for x in a for y in b if y < x)
    return (gt - lt) / max(len(a) * len(b), 1)


def _hodges_lehmann_shift(a: list[float], b: list[float]) -> float:
    diffs = sorted(y - x for x in a for y in b)
    return _quantile_sorted(diffs, 0.5)


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


# ---------- Cell-styled SVG renderer (pure stdlib) + optional Matplotlib ----------

def _render_cell_svg(
    out_svg: Path,
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    group_stats: list[dict[str, Any]],
    arrays: list[list[float]],
    width_mm: float = 114.0,
    height_mm: float = 82.0,
) -> dict[str, Any]:
    """Render publication-grade Cell-styled vector SVG without external dependencies."""
    # 1 mm = 3.779527559 px at 96 dpi SVG user units (declared in mm on root <svg>)
    w_px = width_mm * 3.779527559
    h_px = height_mm * 3.779527559
    m_left, m_right, m_top, m_bottom = 64.0, 20.0, 42.0, 54.0
    plot_w = w_px - m_left - m_right
    plot_h = h_px - m_top - m_bottom

    all_pts = [v for arr in arrays for v in arr]
    lows = [gs["ci95_low"] for gs in group_stats] + all_pts
    highs = [gs["ci95_high"] for gs in group_stats] + all_pts
    data_min, data_max = min(all_pts), max(all_pts)
    plotted_min, plotted_max = min(lows), max(highs)

    if plotted_min < 0 < plotted_max:
        pad = max((plotted_max - plotted_min) * 0.10, 0.05)
        y_min, y_max = plotted_min - pad, plotted_max + pad
    elif plotted_min < 0:
        pad = max(abs(plotted_min) * 0.10, 0.05)
        y_min, y_max = plotted_min - pad, 0.0
    else:
        y_min = 0.0
        y_max = max(plotted_max * 1.15, 0.05)

    def y_to_px(y: float) -> float:
        return m_top + plot_h * (1.0 - (y - y_min) / max(y_max - y_min, 1e-12))

    k = len(group_stats)
    step = plot_w / max(k, 1)
    bar_w = min(step * 0.54, 48.0)
    rng = random.Random(42)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm" height="{height_mm}mm" viewBox="0 0 {w_px:.2f} {h_px:.2f}">',
        '  <rect x="0" y="0" width="100%" height="100%" fill="#FFFFFF"/>',
        f'  <text x="{m_left + plot_w / 2:.2f}" y="22" text-anchor="middle" font-family="Arial, DejaVu Sans, sans-serif" font-size="11" fill="#111111">{html.escape(title)}</text>',
    ]

    # Y ticks & light horizontal grid
    n_ticks = 5
    for ti in range(n_ticks + 1):
        yv = y_min + (y_max - y_min) * (ti / n_ticks)
        yp = y_to_px(yv)
        lines.append(f'  <line x1="{m_left:.2f}" y1="{yp:.2f}" x2="{m_left + plot_w:.2f}" y2="{yp:.2f}" stroke="#E5E7EB" stroke-width="0.6"/>')
        lines.append(f'  <line x1="{m_left - 4:.2f}" y1="{yp:.2f}" x2="{m_left:.2f}" y2="{yp:.2f}" stroke="#111111" stroke-width="0.75"/>')
        lines.append(f'  <text x="{m_left - 7:.2f}" y="{yp + 3.2:.2f}" text-anchor="end" font-family="Arial, DejaVu Sans, sans-serif" font-size="9" fill="#222222">{yv:.2g}</text>')

    # Zero reference line when crossing zero
    y_zero = y_to_px(0.0)
    if y_min <= 0.0 <= y_max:
        lines.append(f'  <line x1="{m_left:.2f}" y1="{y_zero:.2f}" x2="{m_left + plot_w:.2f}" y2="{y_zero:.2f}" stroke="#333333" stroke-width="0.9"/>')

    # Bars (mean) + 95% CI error bars + jittered raw replicate points
    for idx, (gs, arr) in enumerate(zip(group_stats, arrays)):
        cx = m_left + step * (idx + 0.5)
        col = CELL_PALETTE[idx % len(CELL_PALETTE)]
        ym = y_to_px(gs["mean"])
        y0 = y_to_px(0.0) if y_min <= 0.0 <= y_max else y_to_px(y_min)
        rect_y = min(ym, y0)
        rect_h = abs(y0 - ym)
        lines.append(
            f'  <rect x="{cx - bar_w / 2:.2f}" y="{rect_y:.2f}" width="{bar_w:.2f}" height="{rect_h:.2f}" fill="{col}" fill-opacity="0.82" stroke="#222222" stroke-width="0.8"/>'
        )
        # 95% CI whisker
        y_lo, y_hi = y_to_px(gs["ci95_low"]), y_to_px(gs["ci95_high"])
        cap = 5.0
        lines.append(f'  <line x1="{cx:.2f}" y1="{y_lo:.2f}" x2="{cx:.2f}" y2="{y_hi:.2f}" stroke="#111111" stroke-width="1.0"/>')
        lines.append(f'  <line x1="{cx - cap:.2f}" y1="{y_lo:.2f}" x2="{cx + cap:.2f}" y2="{y_lo:.2f}" stroke="#111111" stroke-width="1.0"/>')
        lines.append(f'  <line x1="{cx - cap:.2f}" y1="{y_hi:.2f}" x2="{cx + cap:.2f}" y2="{y_hi:.2f}" stroke="#111111" stroke-width="1.0"/>')
        # Raw observations overlay
        for pt in arr:
            px = cx + rng.uniform(-bar_w * 0.28, bar_w * 0.28)
            py = y_to_px(pt)
            lines.append(f'  <circle cx="{px:.2f}" cy="{py:.2f}" r="2.2" fill="#1F2937" fill-opacity="0.72" stroke="#FFFFFF" stroke-width="0.5"/>')
        # X label + n annotation
        lbl = f"{gs['group']} (n={gs['n']})"
        lines.append(f'  <text x="{cx:.2f}" y="{m_top + plot_h + 15:.2f}" text-anchor="middle" font-family="Arial, DejaVu Sans, sans-serif" font-size="8.5" fill="#111111">{html.escape(lbl)}</text>')

    # Clean axes (top/right spines omitted)
    lines.append(f'  <line x1="{m_left:.2f}" y1="{m_top:.2f}" x2="{m_left:.2f}" y2="{m_top + plot_h:.2f}" stroke="#111111" stroke-width="0.9"/>')
    lines.append(f'  <line x1="{m_left:.2f}" y1="{m_top + plot_h:.2f}" x2="{m_left + plot_w:.2f}" y2="{m_top + plot_h:.2f}" stroke="#111111" stroke-width="0.9"/>')
    lines.append(f'  <text x="{m_left + plot_w / 2:.2f}" y="{h_px - 12:.2f}" text-anchor="middle" font-family="Arial, DejaVu Sans, sans-serif" font-size="10" fill="#111111">{html.escape(xlabel)}</text>')
    lines.append(f'  <text x="16" y="{m_top + plot_h / 2:.2f}" text-anchor="middle" transform="rotate(-90 16 {m_top + plot_h / 2:.2f})" font-family="Arial, DejaVu Sans, sans-serif" font-size="10" fill="#111111">{html.escape(ylabel)}</text>')
    lines.append("</svg>")

    out_svg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "backend": "pure_stdlib_svg",
        "width_mm": width_mm,
        "height_mm": height_mm,
        "data_value_range": [round(data_min, 6), round(data_max, 6)],
        "y_axis_limits": [round(y_min, 6), round(y_max, 6)],
        "value_range_covered": bool(y_min <= plotted_min + 1e-9 and y_max >= plotted_max - 1e-9),
        "title_aligned_to_panel_center": True,
        "raw_points_overlaid": True,
    }


def _render_matplotlib_outputs(
    out_dir: Path,
    stem: str,
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    group_stats: list[dict[str, Any]],
    arrays: list[list[float]],
    width_mm: float = 114.0,
    height_mm: float = 82.0,
) -> list[str]:
    if _PLT is None:
        return []
    _PLT.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
        "font.size": 8.0,
        "axes.linewidth": 0.75,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })
    fig, ax = _PLT.subplots(figsize=(width_mm / 25.4, height_mm / 25.4), dpi=300)
    xs = list(range(len(group_stats)))
    means = [g["mean"] for g in group_stats]
    yerr = [[max(0.0, g["mean"] - g["ci95_low"]) for g in group_stats], [max(0.0, g["ci95_high"] - g["mean"]) for g in group_stats]]
    colors = [CELL_PALETTE[i % len(CELL_PALETTE)] for i in xs]
    ax.bar(xs, means, yerr=yerr, width=0.54, color=colors, edgecolor="#222222", linewidth=0.75, capsize=3.0, alpha=0.85, zorder=2)
    rng = random.Random(42)
    for xi, arr in enumerate(arrays):
        ax.scatter([xi + rng.uniform(-0.14, 0.14) for _ in arr], arr, s=14, color="#1F2937", edgecolor="#FFFFFF", linewidth=0.4, alpha=0.75, zorder=4)
    all_pts = [v for arr in arrays for v in arr]
    p_lo = min(all_pts + [g["ci95_low"] for g in group_stats])
    p_hi = max(all_pts + [g["ci95_high"] for g in group_stats])
    if p_lo < 0 < p_hi:
        pad = max((p_hi - p_lo) * 0.1, 0.05)
        ax.set_ylim(p_lo - pad, p_hi + pad)
        ax.axhline(0.0, color="#333333", linewidth=0.8, zorder=1)
    elif p_lo < 0:
        ax.set_ylim(p_lo - max(abs(p_lo) * 0.1, 0.05), 0.0)
    else:
        ax.set_ylim(0.0, max(p_hi * 1.15, 0.05))
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{g['group']}\n(n={g['n']})" for g in group_stats], fontsize=7.5)
    ax.set_xlabel(xlabel, fontsize=8.5)
    ax.set_ylabel(ylabel, fontsize=8.5)
    ax.set_title(title, loc="center", fontsize=9.0, fontweight="normal")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.grid(True, color="#E5E7EB", linewidth=0.5)
    ax.set_axisbelow(True)
    fig.subplots_adjust(left=0.16, right=0.96, top=0.88, bottom=0.18)
    png_p, pdf_p = out_dir / f"{stem}_chart.png", out_dir / f"{stem}_chart.pdf"
    fig.savefig(png_p, format="png", dpi=300, bbox_inches=None)
    fig.savefig(pdf_p, format="pdf", bbox_inches=None)
    _PLT.close(fig)
    return [png_p.name, pdf_p.name]


# ---------- Checkpoint state helpers ----------

def _load_or_init_checkpoint(cp_path: Path, case_id: str, input_sha: str, group_col: str | None, value_col: str | None, resume: bool) -> dict[str, Any]:
    if resume and cp_path.is_file():
        old = json.loads(cp_path.read_text(encoding="utf-8"))
        # Only reuse stages if input SHA-256 and target columns match verbatim
        if (
            old.get("input_sha256") == input_sha
            and old.get("group_col") == group_col
            and old.get("value_col") == value_col
        ):
            old["resume_invocations"] = int(old.get("resume_invocations", 0)) + 1
            return old
    return {
        "schema_version": "2.1",
        "case_id": case_id,
        "input_sha256": input_sha,
        "group_col": group_col,
        "value_col": value_col,
        "resume_invocations": 0,
        "overall_status": "RUNNING",
        "stages": {st: {"status": "PENDING", "artifacts": {}, "error": None} for st in STAGES},
    }


def _stage_verified_on_disk(cp: dict[str, Any], stage: str, out_dir: Path) -> bool:
    st_info = (cp.get("stages") or {}).get(stage) or {}
    if st_info.get("status") != "COMPLETED":
        return False
    arts = st_info.get("artifacts") or {}
    if not arts:
        return False
    for rel, expected_sha in arts.items():
        fp = out_dir / rel
        if not fp.is_file() or _file_sha256(fp) != expected_sha:
            return False
    return True


def run_multistep_pipeline(
    input_path: Path,
    *,
    group_col: str | None = None,
    value_col: str | None = None,
    out_dir: Path,
    stem: str = "analysis_report",
    case_id: str = "case-unknown",
    table: str | None = None,
    unit: str = "",
    resume: bool = False,
    fail_at_stage: str | None = None,
    case_json_path: Path | None = None,
) -> dict[str, Any]:
    """Execute 5-stage checkpointed pipeline (auto-routing grouped vs continuous-multivariate)."""
    from continuous_ols_branch import infer_column_roles, render_ols_svg, run_continuous_multivariate_stats

    out_dir.mkdir(parents=True, exist_ok=True)
    input_sha = _file_sha256(input_path)
    cp_path = out_dir / f"{stem}_checkpoint.json"
    cp = _load_or_init_checkpoint(cp_path, case_id, input_sha, group_col, value_col, resume)

    def save_cp() -> None:
        _write_json(cp_path, cp)

    resumed_stages: list[str] = []
    executed_stages: list[str] = []

    def maybe_inject_fail(stage: str) -> None:
        if fail_at_stage == stage:
            raise RuntimeError(f"Simulated interruption injected at {stage} before artifact persistence")

    # ---- Stage 1: Read-Only Intake Profiling & Auto Role Inference ----
    s1_file = out_dir / f"{stem}_s1_intake.json"
    if resume and _stage_verified_on_disk(cp, "S1_intake", out_dir):
        intake = json.loads(s1_file.read_text(encoding="utf-8"))
        roles = intake.get("inferred_roles") or {"mode": "group_comparison", "group_col": group_col, "value_col": value_col, "x_cols": []}
        resumed_stages.append("S1_intake")
    else:
        cp["stages"]["S1_intake"] = {"status": "RUNNING", "started_at": _now_iso(), "artifacts": {}, "error": None}
        save_cp()
        try:
            maybe_inject_fail("S1_intake")
            loaded = load_records_ro(input_path, table=table)
            intake = profile_loaded(loaded)
            if group_col and group_col not in loaded["columns"]:
                raise KeyError(f"Specified group_col {group_col!r} missing from {loaded['columns']}")
            if value_col and value_col not in loaded["columns"]:
                raise KeyError(f"Specified value_col {value_col!r} missing from {loaded['columns']}")
            if value_col and value_col in intake["coded_integer_warnings"]:
                raise ValueError(
                    f"Refused continuous aggregation on coded categorical integer column {value_col!r}; confirm field dictionary."
                )
            roles = infer_column_roles(intake, group_col=group_col, value_col=value_col)
            intake["inferred_roles"] = roles
            sha1 = _write_json(s1_file, intake)
            cp["stages"]["S1_intake"].update({"status": "COMPLETED", "completed_at": _now_iso(), "artifacts": {s1_file.name: sha1}})
            executed_stages.append("S1_intake")
            save_cp()
        except Exception as exc:
            cp["stages"]["S1_intake"].update({"status": "FAILED", "failed_at": _now_iso(), "error": str(exc)})
            cp["overall_status"] = "FAILED_AT_S1_intake"
            save_cp()
            return {"ok": False, "failed_stage": "S1_intake", "error": str(exc), "checkpoint": cp_path.name}

    analysis_mode = roles["mode"]
    eff_group_col = roles.get("group_col")
    eff_value_col = str(roles["value_col"])
    eff_x_cols = list(roles.get("x_cols") or [])
    group_col = eff_group_col
    value_col = eff_value_col

    # ---- Stage 2: Rule-Logged Non-Silent Cleaning ----
    s2_csv = out_dir / f"{stem}_s2_cleaned.csv"
    s2_ledger = out_dir / f"{stem}_s2_ledger.json"
    if resume and _stage_verified_on_disk(cp, "S2_clean", out_dir):
        ledger_obj = json.loads(s2_ledger.read_text(encoding="utf-8"))
        with s2_csv.open("r", encoding="utf-8") as fh:
            cleaned_rows = list(csv.DictReader(fh))
        resumed_stages.append("S2_clean")
    else:
        cp["stages"]["S2_clean"] = {"status": "RUNNING", "started_at": _now_iso(), "artifacts": {}, "error": None}
        save_cp()
        try:
            maybe_inject_fail("S2_clean")
            loaded = load_records_ro(input_path, table=table)
            cols, raw_rows = loaded["columns"], loaded["rows"]
            ledger: list[dict[str, Any]] = []
            ledger.append({"step": "raw_intake_snapshot", "rows_before": len(raw_rows), "rows_after": len(raw_rows), "affected": 0, "rule": "read-only string load preserving leading zeros"})

            seen_sig: set[tuple[str, ...]] = set()
            dedup_rows: list[dict[str, str]] = []
            for r in raw_rows:
                sig = tuple(r.get(c, "") for c in cols)
                if sig in seen_sig:
                    continue
                seen_sig.add(sig)
                dedup_rows.append(r)
            ledger.append({"step": "deduplicate_exact", "rows_before": len(raw_rows), "rows_after": len(dedup_rows), "affected": len(raw_rows) - len(dedup_rows), "rule": "drop exact duplicate rows keeping first occurrence"})

            cleaned_rows = []
            excluded_rows: list[dict[str, Any]] = []
            req_num_cols = ([eff_value_col] + eff_x_cols) if analysis_mode == "continuous_multivariate" else [eff_value_col]
            for idx, r in enumerate(dedup_rows, start=2):
                if analysis_mode == "group_comparison":
                    gv = r.get(eff_group_col, "").strip()  # type: ignore[arg-type]
                    if gv.lower() in _MISSING_TOKENS:
                        excluded_rows.append({"row_index": idx, "reason": "missing_group", "group": gv})
                        continue
                clean_r = dict(r)
                if analysis_mode == "group_comparison" and eff_group_col:
                    clean_r[eff_group_col] = r.get(eff_group_col, "").strip()
                bad_num = False
                for nc in req_num_cols:
                    rv = r.get(nc, "").strip()
                    if rv.lower() in _MISSING_TOKENS:
                        excluded_rows.append({"row_index": idx, "reason": f"missing_or_sentinel:{nc}", "value_raw": rv})
                        bad_num = True
                        break
                    try:
                        fv = float(rv)
                        if not math.isfinite(fv):
                            raise ValueError("non-finite")
                        clean_r[nc] = f"{fv:.8f}".rstrip("0").rstrip(".") if fv != 0.0 else "0"
                    except ValueError:
                        excluded_rows.append({"row_index": idx, "reason": f"unparseable_numeric:{nc}", "value_raw": rv})
                        bad_num = True
                        break
                if not bad_num:
                    cleaned_rows.append(clean_r)

            ledger.append({
                "step": "filter_missing_analysis_fields",
                "rows_before": len(dedup_rows),
                "rows_after": len(cleaned_rows),
                "affected": len(excluded_rows),
                "rule": f"explicit exclusion of missing/unparseable ({eff_group_col or 'continuous'}, {req_num_cols}); zero imputation",
            })
            if not cleaned_rows:
                raise ValueError("Zero valid rows remain after explicit missing/numeric filtering")

            with s2_csv.open("w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                w.writerows(cleaned_rows)
            ledger_obj = {
                "analysis_mode": analysis_mode,
                "rows_raw": len(raw_rows),
                "rows_cleaned": len(cleaned_rows),
                "imputation_performed": False,
                "steps": ledger,
                "excluded_records": excluded_rows[:50],
            }
            sha_csv = _file_sha256(s2_csv)
            sha_led = _write_json(s2_ledger, ledger_obj)
            cp["stages"]["S2_clean"].update({"status": "COMPLETED", "completed_at": _now_iso(), "artifacts": {s2_csv.name: sha_csv, s2_ledger.name: sha_led}})
            executed_stages.append("S2_clean")
            save_cp()
        except Exception as exc:
            cp["stages"]["S2_clean"].update({"status": "FAILED", "failed_at": _now_iso(), "error": str(exc)})
            cp["overall_status"] = "FAILED_AT_S2_clean"
            save_cp()
            return {"ok": False, "failed_stage": "S2_clean", "error": str(exc), "checkpoint": cp_path.name}

    # ---- Stage 3: Assumption-Verified Statistical Inference ----
    s3_file = out_dir / f"{stem}_s3_stats.json"
    _ols_plot_pts: list[tuple[float, float]] = []
    if resume and _stage_verified_on_disk(cp, "S3_stats", out_dir):
        stats_obj = json.loads(s3_file.read_text(encoding="utf-8"))
        resumed_stages.append("S3_stats")
    else:
        cp["stages"]["S3_stats"] = {"status": "RUNNING", "started_at": _now_iso(), "artifacts": {}, "error": None}
        save_cp()
        try:
            maybe_inject_fail("S3_stats")
            if analysis_mode == "continuous_multivariate":
                stats_raw = run_continuous_multivariate_stats(cleaned_rows, y_col=eff_value_col, x_cols=eff_x_cols, unit=unit)
                _ols_plot_pts = list(stats_raw.pop("_plot_points", []))
                stats_obj = stats_raw
            else:
                groups = list(dict.fromkeys(r[group_col] for r in cleaned_rows))  # type: ignore[index]
                arrays = [[float(r[value_col]) for r in cleaned_rows if r[group_col] == g] for g in groups]  # type: ignore[index]

                desc: list[dict[str, Any]] = []
                normality: dict[str, Any] = {}
                warnings: list[str] = []
                for g, arr in zip(groups, arrays):
                    sarr = sorted(arr)
                    n = len(sarr)
                    m, v = _mean_var(sarr)
                    sd = math.sqrt(v) if n > 1 else 0.0
                    sem = sd / math.sqrt(n) if n > 1 else 0.0
                    half = _t_crit_95(n - 1) * sem if (n > 1 and sem > 0) else 0.0
                    q1, med, q3 = _quantile_sorted(sarr, 0.25), _quantile_sorted(sarr, 0.50), _quantile_sorted(sarr, 0.75)
                    nchk = _normality_check(sarr)
                    normality[g] = nchk
                    if nchk["status"] != "pass":
                        warnings.append(f"Group {g!r} (n={n}): {nchk['status']}")
                    desc.append({
                        "group": g, "n": n, "mean": round(m, 6), "sd": round(sd, 6), "sem": round(sem, 6),
                        "ci95_low": round(m - half, 6), "ci95_high": round(m + half, 6),
                        "median": round(med, 6), "q1": round(q1, 6), "q3": round(q3, 6), "iqr": round(q3 - q1, 6),
                    })

                levene = _brown_forsythe_levene(arrays)
                if levene["status"] not in {"equal_var"}:
                    warnings.append(f"Variance homogeneity check: {levene['status']} (p={levene.get('p_value')})")

                all_normal = all(normality[g]["status"] == "pass" for g in groups)
                equal_var = levene["status"] == "equal_var"
                has_degenerate = any(normality[g]["status"] in {"underpowered_n_lt_3", "zero_variance_degenerate"} for g in groups)

                if has_degenerate or not all_normal:
                    route = "nonparametric_kruskal_mannwhitney"
                elif equal_var:
                    route = "parametric_anova_student"
                else:
                    route = "parametric_welch"

                omnibus: dict[str, Any] = {"route": route}
                pairwise: list[dict[str, Any]] = []
                eligible = [i for i, a in enumerate(arrays) if len(a) >= 2]
                if len(eligible) >= 2:
                    el_arrays = [arrays[i] for i in eligible]
                    if route == "parametric_anova_student":
                        all_v = [x for a in el_arrays for x in a]
                        gm = sum(all_v) / len(all_v)
                        ss_b = sum(len(a) * ((sum(a) / len(a)) - gm) ** 2 for a in el_arrays)
                        ss_w = sum(sum((x - sum(a) / len(a)) ** 2 for x in a) for a in el_arrays)
                        df1, df2 = len(el_arrays) - 1, len(all_v) - len(el_arrays)
                        f_val = (ss_b / df1) / max(ss_w / df2, 1e-15)
                        p_val = _f_sf(f_val, df1, df2)
                        eta_sq = ss_b / max(ss_b + ss_w, 1e-15)
                        omnibus.update({"test": "one_way_ANOVA", "estimable": True, "statistic": round(f_val, 6), "df1": df1, "df2": df2, "p_value": p_val, "effect_size_name": "eta_squared", "effect_size": round(eta_sq, 4)})
                    elif route == "parametric_welch":
                        w_res = _welch_anova(el_arrays)
                        if w_res.get("estimable"):
                            omnibus.update(w_res)
                        else:
                            route = "nonparametric_kruskal_mannwhitney_fallback"
                            omnibus["route"] = route
                            omnibus.update(_kruskal_wallis(el_arrays))
                    else:
                        omnibus.update(_kruskal_wallis(el_arrays))

                    raw_p: list[float] = []
                    meta: list[dict[str, Any]] = []
                    for i, j in itertools.combinations(eligible, 2):
                        a, b = arrays[i], arrays[j]
                        na, nb = len(a), len(b)
                        ma, va = _mean_var(a)
                        mb, vb = _mean_var(b)
                        diff_mean = mb - ma
                        pooled_sd = math.sqrt(((na - 1) * va + (nb - 1) * vb) / max(na + nb - 2, 1))
                        j_corr = 1.0 - 3.0 / max(4 * (na + nb) - 9, 1)
                        hedges_g = (diff_mean / pooled_sd * j_corr) if pooled_sd > 1e-12 else None
                        cliffs_d = _cliffs_delta(a, b)
                        hl_shift = _hodges_lehmann_shift(a, b)

                        if route.startswith("parametric") and (va > 1e-15 or vb > 1e-15):
                            if route == "parametric_anova_student":
                                se = pooled_sd * math.sqrt(1.0 / na + 1.0 / nb)
                                df_t = float(na + nb - 2)
                                tname = "Student_t"
                            else:
                                se2 = va / na + vb / nb
                                se = math.sqrt(se2)
                                denom_df = ((va / na) ** 2) / max(na - 1, 1) + ((vb / nb) ** 2) / max(nb - 1, 1)
                                df_t = (se2 * se2) / max(denom_df, 1e-15)
                                tname = "Welch_t"
                            t_stat = diff_mean / max(se, 1e-15)
                            p0 = _t_two_sided_p(abs(t_stat), df_t)
                            tcrit = _t_crit_95(df_t)
                            ci = [round(diff_mean - tcrit * se, 6), round(diff_mean + tcrit * se, 6)]
                        else:
                            u_stat, p0 = _mann_whitney_u(a, b)
                            tname = "Mann_Whitney_U"
                            t_stat = u_stat
                            df_t = None
                            ci = None

                        raw_p.append(p0)
                        meta.append({
                            "pair": f"{groups[i]} vs {groups[j]}",
                            "test": tname,
                            "statistic": round(t_stat, 6),
                            "df": round(df_t, 3) if df_t is not None else None,
                            "diff_mean": round(diff_mean, 6),
                            "diff_mean_ci95": ci,
                            "hodges_lehmann_median_shift": round(hl_shift, 6),
                            "hedges_g": round(hedges_g, 4) if hedges_g is not None else None,
                            "cliffs_delta": round(cliffs_d, 4),
                            "p_raw": round(p0, 8),
                        })
                    for row_m, ap in zip(meta, _holm_bonferroni(raw_p)):
                        row_m["p_holm"] = round(ap, 8)
                        row_m["significant_holm_05"] = ap < 0.05
                        pairwise.append(row_m)
                else:
                    omnibus.update({"test": None, "estimable": False, "reason": "fewer_than_two_groups_with_n_ge_2"})

                stats_obj = {
                    "ok": True,
                    "analysis_mode": "group_comparison",
                    "stats_backend": "scipy" if _SCIPY_STATS is not None else "pure_stdlib",
                    "group_col": group_col,
                    "value_col": value_col,
                    "unit": unit,
                    "rows_analyzed": len(cleaned_rows),
                    "normality_checks": normality,
                    "variance_homogeneity": levene,
                    "assumption_warnings": warnings,
                    "descriptive": desc,
                    "omnibus_test": omnibus,
                    "pairwise_comparisons": pairwise,
                }
            sha3 = _write_json(s3_file, stats_obj)
            cp["stages"]["S3_stats"].update({"status": "COMPLETED", "completed_at": _now_iso(), "artifacts": {s3_file.name: sha3}})
            executed_stages.append("S3_stats")
            save_cp()
        except Exception as exc:
            cp["stages"]["S3_stats"].update({"status": "FAILED", "failed_at": _now_iso(), "error": str(exc)})
            cp["overall_status"] = "FAILED_AT_S3_stats"
            save_cp()
            return {"ok": False, "failed_stage": "S3_stats", "error": str(exc), "checkpoint": cp_path.name}

    # ---- Stage 4: Publication-Grade Visualization (Dual-Backend) ----
    s4_svg = out_dir / f"{stem}_chart.svg"
    s4_audit = out_dir / f"{stem}_s4_viz_audit.json"
    if resume and _stage_verified_on_disk(cp, "S4_viz", out_dir):
        viz_audit = json.loads(s4_audit.read_text(encoding="utf-8"))
        resumed_stages.append("S4_viz")
    else:
        cp["stages"]["S4_viz"] = {"status": "RUNNING", "started_at": _now_iso(), "artifacts": {}, "error": None}
        save_cp()
        try:
            maybe_inject_fail("S4_viz")
            ylabel = f"{value_col} ({unit})" if unit else value_col
            extra_mpl: list[str] = []
            if analysis_mode == "continuous_multivariate":
                if not _ols_plot_pts:
                    coefs = {c["term"]: c["estimate"] for c in stats_obj["ols_model"]["coefficients"]}
                    b0 = coefs.get("(Intercept)", 0.0)
                    _ols_plot_pts = [
                        (b0 + sum(coefs.get(xc, 0.0) * float(r[xc]) for xc in eff_x_cols), float(r[eff_value_col]))
                        for r in cleaned_rows
                    ]
                viz_audit = render_ols_svg(
                    s4_svg,
                    plot_points=_ols_plot_pts,
                    y_label=ylabel,
                    r2=float(stats_obj["ols_model"]["r_squared"]),
                    rmse=float(stats_obj["ols_model"]["rmse"]),
                )
            else:
                groups = [d["group"] for d in stats_obj["descriptive"]]
                arrays = [[float(r[value_col]) for r in cleaned_rows if r[group_col] == g] for g in groups]  # type: ignore[index]
                title = f"{value_col} across {group_col}"
                viz_audit = _render_cell_svg(s4_svg, title=title, xlabel=str(group_col), ylabel=ylabel, group_stats=stats_obj["descriptive"], arrays=arrays)
                extra_mpl = _render_matplotlib_outputs(out_dir, stem, title=title, xlabel=str(group_col), ylabel=ylabel, group_stats=stats_obj["descriptive"], arrays=arrays)
            viz_audit["matplotlib_exports"] = extra_mpl
            viz_audit["svg_file"] = s4_svg.name
            art_map = {s4_svg.name: _file_sha256(s4_svg)}
            for fn in extra_mpl:
                art_map[fn] = _file_sha256(out_dir / fn)
            art_map[s4_audit.name] = _write_json(s4_audit, viz_audit)
            cp["stages"]["S4_viz"].update({"status": "COMPLETED", "completed_at": _now_iso(), "artifacts": art_map})
            executed_stages.append("S4_viz")
            save_cp()
        except Exception as exc:
            cp["stages"]["S4_viz"].update({"status": "FAILED", "failed_at": _now_iso(), "error": str(exc)})
            cp["overall_status"] = "FAILED_AT_S4_viz"
            save_cp()
            return {"ok": False, "failed_stage": "S4_viz", "error": str(exc), "checkpoint": cp_path.name}

    # ---- Stage 5: Single-Source Report Backfill & Reconciliation ----
    s5_md = out_dir / f"{stem}.md"
    s5_rec = out_dir / f"{stem}_reconciliation.json"
    s5_repro = out_dir / f"reproduce_{stem}.py"
    cp["stages"]["S5_report"] = {"status": "RUNNING", "started_at": _now_iso(), "artifacts": {}, "error": None}
    save_cp()
    try:
        maybe_inject_fail("S5_report")
        with s2_csv.open("r", encoding="utf-8") as fh:
            disk_rows = list(csv.DictReader(fh))
        row_match = len(disk_rows) == stats_obj["rows_analyzed"] == ledger_obj["rows_cleaned"]
        rec_obj = {
            "ok": bool(row_match and viz_audit.get("value_range_covered")),
            "analysis_mode": analysis_mode,
            "disk_cleaned_rows": len(disk_rows),
            "stats_rows_analyzed": stats_obj["rows_analyzed"],
            "ledger_rows_cleaned": ledger_obj["rows_cleaned"],
            "row_counts_reconciled": row_match,
            "viz_range_covered": bool(viz_audit.get("value_range_covered")),
            "resumed_stages": resumed_stages,
            "executed_stages": executed_stages + ["S5_report"],
            "input_sha256": input_sha,
        }

        if analysis_mode == "continuous_multivariate":
            ols = stats_obj["ols_model"]
            md = [
                f"# Multi-Step Scientific Analysis Report: Multivariable Regression of {eff_value_col}",
                "",
                "## 1. Key Findings & Statistical Scope",
                "",
                f"- Source dataset: `{intake['source_file']}` (SHA-256 `{input_sha[:16]}...`, raw rows: **{intake['rows_raw']}**, analyzed rows: **{stats_obj['rows_analyzed']}**; auto-routed to `continuous_multivariate` with zero silent imputation).",
                f"- OLS fit: **R^2 = {ols['r_squared']}**, **Adj. R^2 = {ols['adj_r_squared']}**, F({ols['df_model']}, {ols['df_resid']}) = {ols['f_statistic']} (`p={ols['f_p_value']}`), RMSE = {ols['rmse']}, residual Jarque-Bera `p={ols['jarque_bera_p']}`.",
                f"- Visualization artifact: `{s4_svg.name}` (`{viz_audit['width_mm']} x {viz_audit['height_mm']} mm`).",
                "- Interpretation boundary: Regression coefficients capture conditional linear associations within the observed covariate range and do not establish causality.",
                "",
                "## 2. Data Cleaning Audit Ledger (Single-Source)",
                "",
                "| Step | Rows Before | Rows After | Affected | Rule |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
            for st in ledger_obj["steps"]:
                md.append(f"| `{st['step']}` | {st['rows_before']} | {st['rows_after']} | {st['affected']} | {st['rule']} |")
            md.extend(["", "## 3. Bivariate Correlations with Outcome", "", "| Predictor | Pearson r | Pearson p | Spearman rho | Spearman p |", "| --- | ---: | ---: | ---: | ---: |"])
            for cr in stats_obj["correlations"]:
                md.append(f"| `{cr['predictor']}` | {cr['pearson_r']} | {cr['pearson_p']} | {cr['spearman_rho']} | {cr['spearman_p']} |")
            md.extend(["", "## 4. Multivariable OLS Coefficients", "", "| Term | Estimate | SE | 95% CI | t | p-value |", "| --- | ---: | ---: | --- | ---: | ---: |"])
            for cf in ols["coefficients"]:
                md.append(f"| `{cf['term']}` | {cf['estimate']} | {cf['se']} | {cf['ci95']} | {cf['t_stat']} | {cf['p_value']} |")
        else:
            omni = stats_obj["omnibus_test"]
            md = [
                f"# Multi-Step Scientific Analysis Report: {value_col} by {group_col}",
                "",
                "## 1. Key Findings & Statistical Scope",
                "",
                f"- Source dataset: `{intake['source_file']}` (SHA-256 `{input_sha[:16]}...`, raw rows: **{intake['rows_raw']}**, analyzed rows: **{stats_obj['rows_analyzed']}**; zero silent imputation).",
                f"- Selected statistical route: `{omni.get('route')}` -> omnibus `{omni.get('test')}` (`stat={omni.get('statistic')}`, `p={omni.get('p_value')}`)",
                f"- Visualization artifact: `{s4_svg.name}` (`{viz_audit['width_mm']} x {viz_audit['height_mm']} mm`, full range covered: `{viz_audit['value_range_covered']}`).",
                "- Interpretation boundary: Observed group differences reflect distributional shifts within this dataset; observational or unrandomized comparisons do not by themselves establish causality.",
                "",
                "## 2. Data Cleaning Audit Ledger (Single-Source)",
                "",
                "| Step | Rows Before | Rows After | Affected | Rule |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
            for st in ledger_obj["steps"]:
                md.append(f"| `{st['step']}` | {st['rows_before']} | {st['rows_after']} | {st['affected']} | {st['rule']} |")
            md.extend(["", "## 3. Group Descriptive Summary", "", "| Group | n | Mean | SD | 95% CI | Median | IQR |", "| --- | ---: | ---: | ---: | --- | ---: | ---: |"])
            for d in stats_obj["descriptive"]:
                md.append(f"| {d['group']} | {d['n']} | {d['mean']} | {d['sd']} | [{d['ci95_low']}, {d['ci95_high']}] | {d['median']} | {d['iqr']} |")
            md.extend(["", "## 4. Pairwise Comparisons (Holm-Bonferroni Adjusted)", "", "| Pair | Test | Mean Diff | Hodges-Lehmann Shift | Hedges' g | Cliff's delta | p_raw | p_holm |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
            for pw in stats_obj["pairwise_comparisons"]:
                md.append(f"| {pw['pair']} | {pw['test']} | {pw['diff_mean']} | {pw['hodges_lehmann_median_shift']} | {pw['hedges_g']} | {pw['cliffs_delta']} | {pw['p_raw']} | {pw['p_holm']} |")

        if stats_obj.get("assumption_warnings"):
            md.extend(["", "## 5. Assumption Diagnostics & Methodological Limits", ""])
            for w in stats_obj["assumption_warnings"]:
                md.append(f"- {w}")

        md.extend([
            "",
            "## 6. Stage Checkpoint & Reconciliation Provenance",
            "",
            f"- Row count reconciliation across cleaned CSV, ledger, and stats JSON: **{'PASS' if row_match else 'FAIL'}**",
            f"- Stages reused from verified disk checkpoints: `{resumed_stages}` | Stages executed this run: `{executed_stages + ['S5_report']}`",
        ])
        s5_md.write_text("\n".join(md) + "\n", encoding="utf-8")

        repro_args = [f'"--input", str(INPUT_FILE)', f'"--out-dir", str(OUT_DIR)', f'"--stem", "{stem}"']
        if group_col:
            repro_args.append(f'"--group-col", "{group_col}"')
        if value_col:
            repro_args.append(f'"--value-col", "{value_col}"')
        repro_lines = [
            "#!/usr/bin/env python3",
            f'"""Linear reproduction script for multi-step analysis of {input_path.name}."""',
            "",
            "import subprocess, sys",
            "from pathlib import Path",
            "",
            f'INPUT_FILE = Path("{input_path.name}")',
            'OUT_DIR = Path(".")',
            'SCRIPTS = Path("/skills/weekly-skill/scripts")',
            "",
            'cmd = [sys.executable, "-B", str(SCRIPTS / "multistep_pipeline_engine.py"), ' + ", ".join(repro_args) + "]",
            "subprocess.run(cmd, check=True)",
        ]
        s5_repro.write_text("\n".join(repro_lines) + "\n", encoding="utf-8")

        sha_md = _file_sha256(s5_md)
        sha_rec = _write_json(s5_rec, rec_obj)
        sha_rep = _file_sha256(s5_repro)
        cp["stages"]["S5_report"].update({
            "status": "COMPLETED",
            "completed_at": _now_iso(),
            "artifacts": {s5_md.name: sha_md, s5_rec.name: sha_rec, s5_repro.name: sha_rep},
        })
        cp["overall_status"] = "COMPLETED" if rec_obj["ok"] else "COMPLETED_WITH_WARNINGS"
        save_cp()
        executed_stages.append("S5_report")

        all_arts = [s1_file.name, s2_csv.name, s2_ledger.name, s3_file.name, s4_svg.name, s4_audit.name, s5_md.name, s5_rec.name, s5_repro.name, cp_path.name] + viz_audit.get("matplotlib_exports", [])
        if case_json_path and case_json_path.is_file():
            from case_contract_sealer import seal_result_contract
            seal_result_contract(
                case_json_path=case_json_path,
                result_json_path=out_dir / "result.json",
                task_id="agent-6aefb2fd",
                status="ok" if rec_obj["ok"] else "partial",
                payload_path=s3_file,
                artifacts=all_arts,
                sources=[input_path.name],
                uncertainty=list(stats_obj.get("assumption_warnings") or []),
            )
    except Exception as exc:
        cp["stages"]["S5_report"].update({"status": "FAILED", "failed_at": _now_iso(), "error": str(exc)})
        cp["overall_status"] = "FAILED_AT_S5_report"
        save_cp()
        return {"ok": False, "failed_stage": "S5_report", "error": str(exc), "checkpoint": cp_path.name}

    return {
        "ok": bool(rec_obj["ok"]),
        "analysis_mode": analysis_mode,
        "overall_status": cp["overall_status"],
        "checkpoint": cp_path.name,
        "resumed_stages": resumed_stages,
        "executed_stages": executed_stages,
        "artifacts": all_arts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checkpointed multi-step scientific data analysis pipeline.")
    parser.add_argument("--input", default=None, help="Path to CSV, JSON, or SQLite input file.")
    parser.add_argument("--input-dir", default=None, help="Optional /input directory for automatic table and case.json discovery.")
    parser.add_argument("--table", default=None)
    parser.add_argument("--group-col", default=None, help="Optional group column (auto-inferred when omitted; continuous-only tables route to OLS).")
    parser.add_argument("--value-col", default=None, help="Optional outcome/value column (auto-inferred when omitted).")
    parser.add_argument("--unit", default="")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stem", default="analysis_report")
    parser.add_argument("--case-id", default="case-unknown")
    parser.add_argument("--case-json", default=None, help="Optional /input/case.json path to seal output_contract into result.json.")
    parser.add_argument("--resume", action="store_true", help="Resume from verified disk checkpoints when input hash matches")
    parser.add_argument("--fail-at-stage", default=None, choices=list(STAGES), help="Fault injection hook for checkpoint recovery testing")
    args = parser.parse_args(argv)

    in_path = Path(args.input) if args.input else None
    case_json_p = Path(args.case_json) if args.case_json else None
    case_id = args.case_id
    if args.input_dir:
        from case_contract_sealer import discover_input_dir
        disc = discover_input_dir(Path(args.input_dir))
        if in_path is None and disc.get("primary_table_candidate"):
            in_path = Path(disc["primary_table_candidate"])
        if case_json_p is None and disc.get("case_json_found"):
            case_json_p = Path(args.input_dir) / "case.json"
        if case_id == "case-unknown" and disc.get("case_id"):
            case_id = disc["case_id"]
    if in_path is None:
        parser.error("Provide --input or --input-dir containing a data table or SQLite database.")

    rep = run_multistep_pipeline(
        in_path,
        group_col=args.group_col,
        value_col=args.value_col,
        out_dir=Path(args.out_dir),
        stem=args.stem,
        case_id=case_id,
        table=args.table,
        unit=args.unit,
        resume=args.resume,
        fail_at_stage=args.fail_at_stage,
        case_json_path=case_json_p,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
