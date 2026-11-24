#!/usr/bin/env python3
"""Dual-Backend statistical inference, effect size, 95% CI, multiple testing, and plot runner.

Runs natively in pure Python 3.12 stdlib (zero third-party dependencies) and
automatically enables SciPy/Matplotlib enhanced outputs when installed.
Supports:
  - independent (2 or >=3 groups): Student t / Welch t (exact Welch-Satterthwaite df),
    One-way ANOVA, exact Welch's ANOVA (Welch 1951), Kruskal-Wallis H (tie-corrected),
    Mann-Whitney U, Hodges-Lehmann median shift + 95% CI, Cliff's delta & rank-biserial r,
    Hedges' g, eta^2 / omega^2 / epsilon^2, Holm-Bonferroni & Benjamini-Hochberg FDR
  - paired (2 conditions): Paired t-test (Hedges' g_z + 95% CI) and Wilcoxon signed-rank
    (Hodges-Lehmann pseudomedian + 95% CI + matched-pairs rank-biserial r)
  - correlation: Pearson r (exact Fisher-z 95% CI, R^2) and Spearman rho (tie-corrected + 95% CI)
  - contingency: Pearson chi-square, exact 2x2 Fisher's exact test, Cramer's V, Odds Ratio 95% CI
  - estimability & degradation tracking for n < 3, zero-variance groups, missing cells,
    and encoded integer column blocking
  - linear imperative reproduce_stats.py generation + stdlib vector SVG (+ Matplotlib PNG/PDF/SVG)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import itertools
import json
import math
import platform
import random
import statistics
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from assumption_diagnostics import (  # noqa: E402
    check_assumptions,
    chi2_sf,
    f_sf,
    is_missing_raw,
    load_table_ro,
    norm_cdf,
    norm_ppf,
    quantile_linear,
    sample_mean_var,
    t_ppf,
    t_sf_two_sided,
    try_parse_float,
)

# Optional enhanced scientific stack (strictly guarded)
try:
    import scipy.stats as _sps  # type: ignore
except Exception:
    _sps = None

try:
    import matplotlib  # type: ignore
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt  # type: ignore
except Exception:
    _plt = None

# Cell-tendency muted CVD-safe categorical palette
CELL_PALETTE = ["#4C78A8", "#D97757", "#4E9A8E", "#D4A03E", "#8E7AA8", "#5FA8B4", "#B86B82", "#7C8C54"]


# ---------------------------------------------------------------------------
# Pure Python statistical primitives (ranks, multiple testing, effect sizes)
# ---------------------------------------------------------------------------

def average_ranks(values: list[float]) -> tuple[list[float], list[int]]:
    """Compute 1-based average ranks and tie group sizes."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    tie_sizes: list[int] = []
    i = 0
    while i < n:
        j = i + 1
        while j < n and values[order[j]] == values[order[i]]:
            j += 1
        avg_r = 0.5 * ((i + 1) + j)
        for k in range(i, j):
            ranks[order[k]] = avg_r
        if j - i > 1:
            tie_sizes.append(j - i)
        i = j
    return ranks, tie_sizes


def holm_adjust(pvals: list[float | None]) -> list[float | None]:
    valid_idx = [i for i, p in enumerate(pvals) if p is not None and math.isfinite(p)]
    m = len(valid_idx)
    out: list[float | None] = [None] * len(pvals)
    if m == 0:
        return out
    order = sorted(valid_idx, key=lambda i: float(pvals[i]))  # type: ignore[arg-type]
    prev = 0.0
    for r, idx in enumerate(order):
        adj = min(1.0, max(prev, float(pvals[idx]) * (m - r)))  # type: ignore[arg-type]
        out[idx] = round(adj, 8)
        prev = adj
    return out


def bh_fdr_adjust(pvals: list[float | None]) -> list[float | None]:
    valid_idx = [i for i, p in enumerate(pvals) if p is not None and math.isfinite(p)]
    m = len(valid_idx)
    out: list[float | None] = [None] * len(pvals)
    if m == 0:
        return out
    order = sorted(valid_idx, key=lambda i: float(pvals[i]))  # type: ignore[arg-type]
    adj_ordered = [0.0] * m
    running_min = 1.0
    for rev_pos in range(m - 1, -1, -1):
        idx = order[rev_pos]
        rank = rev_pos + 1
        val = min(running_min, float(pvals[idx]) * m / rank)  # type: ignore[arg-type]
        running_min = val
        adj_ordered[rev_pos] = min(1.0, val)
    for pos, idx in enumerate(order):
        out[idx] = round(adj_ordered[pos], 8)
    return out


def hedges_g_pair(a: list[float], b: list[float]) -> tuple[float | None, str]:
    """Small-sample bias-corrected Hedges' g (direction: mean(b) - mean(a))."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None, "UNESTIMABLE_SMALL_N"
    ma, va = sample_mean_var(a)
    mb, vb = sample_mean_var(b)
    df = na + nb - 2
    pooled_var = ((na - 1) * va + (nb - 1) * vb) / max(df, 1)
    if pooled_var <= 1e-15:
        if abs(mb - ma) <= 1e-15:
            return 0.0, "DEGRADED_ZERO_VARIANCE_IDENTICAL"
        return None, "DEGRADED_ZERO_VARIANCE_UNDEFINED_G"
    j_corr = 1.0 - 3.0 / max(4.0 * df - 1.0, 1.0)
    g = ((mb - ma) / math.sqrt(pooled_var)) * j_corr
    return round(g, 4), "ESTIMABLE"


def cliffs_delta_and_hodges_lehmann(a: list[float], b: list[float]) -> dict[str, Any]:
    """Compute Hodges-Lehmann shift (B - A), Moses 95% CI, Cliff's delta, and rank-biserial r."""
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return {
            "hodges_lehmann_diff": None,
            "hl_ci95": [None, None],
            "hl_exact": False,
            "cliffs_delta": None,
            "rank_biserial_r": None,
            "cliffs_magnitude": "unestimable",
        }
    total_pairs = na * nb
    # Exact Cliff's delta using sorted scan (O(n log n))
    a_sorted = sorted(a)
    b_sorted = sorted(b)
    gt = 0
    lt = 0
    # Two-pointer exact count of b > a and b < a
    i_lt = 0
    i_le = 0
    for bv in b_sorted:
        while i_lt < na and a_sorted[i_lt] < bv:
            i_lt += 1
        while i_le < na and a_sorted[i_le] <= bv:
            i_le += 1
        gt += i_lt
        lt += na - i_le
    delta = (gt - lt) / total_pairs
    abs_d = abs(delta)
    if abs_d < 0.147:
        mag = "negligible"
    elif abs_d < 0.33:
        mag = "small"
    elif abs_d < 0.474:
        mag = "medium"
    else:
        mag = "large"

    # Pairwise differences B - A for Hodges-Lehmann shift & Moses 95% CI
    exact = total_pairs <= 50000
    if exact:
        diffs = sorted(bv - av for bv in b for av in a)
    else:
        rng = random.Random(42)
        diffs = sorted(b[rng.randrange(nb)] - a[rng.randrange(na)] for _ in range(50000))

    hl = statistics.median(diffs)
    m = len(diffs)
    if exact and na >= 2 and nb >= 2:
        z = 1.959963984540054
        sigma_u = math.sqrt(na * nb * (na + nb + 1.0) / 12.0)
        k_idx = int(math.floor(0.5 * total_pairs - z * sigma_u))
        k_idx = max(0, min(m - 1, k_idx))
        hi_idx = max(0, min(m - 1, m - 1 - k_idx))
        ci_lo, ci_hi = diffs[k_idx], diffs[hi_idx]
    elif m >= 2:
        ci_lo = quantile_linear(diffs, 0.025)
        ci_hi = quantile_linear(diffs, 0.975)
    else:
        ci_lo, ci_hi = hl, hl

    return {
        "hodges_lehmann_diff": round(hl, 4),
        "hl_ci95": [round(ci_lo, 4), round(ci_hi, 4)],
        "hl_exact": exact,
        "cliffs_delta": round(delta, 4),
        "rank_biserial_r": round(delta, 4),
        "cliffs_magnitude": mag,
    }


def welch_anova(arrays: list[list[float]]) -> dict[str, Any]:
    """Exact Welch's one-way ANOVA (Welch 1951) for heteroscedastic groups."""
    k = len(arrays)
    if k < 2:
        return {"estimable": False, "reason": "requires >=2 groups"}
    ns = [len(a) for a in arrays]
    if any(n < 2 for n in ns):
        return {"estimable": False, "reason": "group with n < 2 cannot estimate variance"}
    stats_list = [sample_mean_var(a) for a in arrays]
    if any(v <= 1e-15 for _, v in stats_list):
        return {"estimable": False, "reason": "zero-variance group makes Welch weights infinite"}

    ws = [n / v for n, (_, v) in zip(ns, stats_list)]
    w_sum = sum(ws)
    x_bar_w = sum(w * m for w, (m, _) in zip(ws, stats_list)) / w_sum
    num = sum(w * (m - x_bar_w) ** 2 for w, (m, _) in zip(ws, stats_list)) / (k - 1)
    lam_sum = sum(((1.0 - w / w_sum) ** 2) / (n - 1) for w, n in zip(ws, ns))
    denom = 1.0 + (2.0 * (k - 2) / (k * k - 1)) * lam_sum
    f_welch = num / max(denom, 1e-15)
    df1 = float(k - 1)
    df2 = (k * k - 1.0) / max(3.0 * lam_sum, 1e-15)
    p_val = f_sf(f_welch, df1, df2)

    # Approximate omega-squared for Welch ANOVA (Carroll & Nordholm / Hays)
    n_tot = sum(ns)
    omega_sq = max(0.0, (df1 * (f_welch - 1.0)) / max(df1 * (f_welch - 1.0) + n_tot, 1e-15))
    return {
        "estimable": True,
        "test": "Welch_ANOVA",
        "statistic": round(f_welch, 6),
        "df1": round(df1, 4),
        "df2": round(df2, 4),
        "p_value": float(p_val),
        "effect_size_name": "omega_squared_est",
        "effect_size": round(omega_sq, 4),
    }


def one_way_anova(arrays: list[list[float]]) -> dict[str, Any]:
    """Classic equal-variance one-way ANOVA with eta^2 and omega^2."""
    k = len(arrays)
    all_vals = [x for a in arrays for x in a]
    n_tot = len(all_vals)
    if k < 2 or n_tot <= k:
        return {"estimable": False, "reason": "insufficient groups or residual df"}
    grand_mean = sum(all_vals) / n_tot
    ss_between = sum(len(a) * ((sum(a) / len(a)) - grand_mean) ** 2 for a in arrays)
    ss_within = sum(sum((x - sum(a) / len(a)) ** 2 for x in a) for a in arrays)
    ss_total = ss_between + ss_within
    df1 = k - 1
    df2 = n_tot - k
    ms_between = ss_between / df1
    ms_within = ss_within / df2
    if ms_within <= 1e-15:
        return {"estimable": False, "reason": "zero residual within-group variance"}
    f_val = ms_between / ms_within
    p_val = f_sf(f_val, df1, df2)
    eta_sq = ss_between / max(ss_total, 1e-15)
    omega_sq = max(0.0, (ss_between - df1 * ms_within) / max(ss_total + ms_within, 1e-15))
    return {
        "estimable": True,
        "test": "One_Way_ANOVA",
        "statistic": round(f_val, 6),
        "df1": float(df1),
        "df2": float(df2),
        "p_value": float(p_val),
        "effect_size_name": "eta_squared",
        "effect_size": round(eta_sq, 4),
        "omega_squared": round(omega_sq, 4),
    }


def kruskal_wallis_test(arrays: list[list[float]]) -> dict[str, Any]:
    """Tie-corrected Kruskal-Wallis H test + epsilon^2."""
    k = len(arrays)
    all_vals = [x for a in arrays for x in a]
    n_tot = len(all_vals)
    if k < 2 or n_tot <= k:
        return {"estimable": False, "reason": "insufficient observations"}
    ranks, tie_sizes = average_ranks(all_vals)
    if len(set(all_vals)) <= 1:
        return {
            "estimable": False,
            "test": "Kruskal_Wallis",
            "reason": "all observations across all groups are identical",
        }
    idx = 0
    sum_r2_over_n = 0.0
    for a in arrays:
        na = len(a)
        r_sum = sum(ranks[idx : idx + na])
        sum_r2_over_n += (r_sum * r_sum) / na
        idx += na
    h_raw = (12.0 / (n_tot * (n_tot + 1.0))) * sum_r2_over_n - 3.0 * (n_tot + 1.0)
    tie_corr = 1.0 - sum(t ** 3 - t for t in tie_sizes) / max(n_tot ** 3 - n_tot, 1)
    h_stat = h_raw / max(tie_corr, 1e-15)
    df = k - 1
    p_val = chi2_sf(h_stat, df)
    eps_sq = max(0.0, (h_stat - k + 1.0) / max(n_tot - k, 1))
    return {
        "estimable": True,
        "test": "Kruskal_Wallis",
        "statistic": round(h_stat, 6),
        "df": float(df),
        "p_value": float(p_val),
        "effect_size_name": "epsilon_squared",
        "effect_size": round(eps_sq, 4),
    }


def two_sample_t_test(a: list[float], b: list[float], *, equal_var: bool) -> dict[str, Any]:
    """Two-sample Student or exact Welch-Satterthwaite t-test (direction: B - A)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"estimable": False, "reason": "n < 2 in at least one group"}
    ma, va = sample_mean_var(a)
    mb, vb = sample_mean_var(b)
    diff = mb - ma
    if equal_var:
        df = float(na + nb - 2)
        sp2 = ((na - 1) * va + (nb - 1) * vb) / df
        se = math.sqrt(sp2 * (1.0 / na + 1.0 / nb))
        tname = "Student_t"
    else:
        v1 = va / na
        v2 = vb / nb
        se = math.sqrt(v1 + v2)
        num = (v1 + v2) ** 2
        den = (v1 * v1) / (na - 1) + (v2 * v2) / (nb - 1)
        df = (num / den) if den > 1e-30 else float(na + nb - 2)
        tname = "Welch_t"

    if se <= 1e-15:
        if abs(diff) <= 1e-15:
            return {
                "estimable": False,
                "test": tname,
                "diff_mean": 0.0,
                "diff_ci95": [0.0, 0.0],
                "statistic": 0.0,
                "df": round(df, 4),
                "p_value": 1.0,
                "reason": "zero_variance_identical_groups",
            }
        return {
            "estimable": False,
            "test": tname,
            "diff_mean": round(diff, 4),
            "diff_ci95": [None, None],
            "statistic": None,
            "df": round(df, 4),
            "p_value": None,
            "reason": "zero_variance_non_identical_groups",
        }
    t_stat = diff / se
    p_val = t_sf_two_sided(t_stat, df)
    t_crit = t_ppf(0.975, df)
    return {
        "estimable": True,
        "test": tname,
        "diff_mean": round(diff, 4),
        "diff_ci95": [round(diff - t_crit * se, 4), round(diff + t_crit * se, 4)],
        "statistic": round(t_stat, 6),
        "df": round(df, 4),
        "p_value": float(p_val),
    }


def mann_whitney_u_test(a: list[float], b: list[float]) -> dict[str, Any]:
    """Two-sided Mann-Whitney U test (SciPy when present, tie-corrected asymptotic stdlib)."""
    na, nb = len(a), len(b)
    if na < 1 or nb < 1:
        return {"estimable": False, "reason": "empty group"}
    if _sps is not None:
        res = _sps.mannwhitneyu(b, a, alternative="two-sided")
        return {
            "estimable": True,
            "test": "Mann_Whitney_U",
            "statistic": round(float(res.statistic), 4),
            "p_value": float(res.pvalue),
            "method": "scipy_mannwhitneyu",
        }
    combined = b + a
    n_tot = na + nb
    ranks, tie_sizes = average_ranks(combined)
    r_b = sum(ranks[:nb])
    u_b = r_b - nb * (nb + 1.0) / 2.0
    mu_u = 0.5 * na * nb
    tie_term = sum(t ** 3 - t for t in tie_sizes)
    var_u = (na * nb / 12.0) * ((n_tot + 1.0) - tie_term / max(n_tot * (n_tot - 1.0), 1.0))
    if var_u <= 1e-15:
        return {
            "estimable": False,
            "test": "Mann_Whitney_U",
            "statistic": round(u_b, 4),
            "p_value": 1.0,
            "reason": "all_values_tied",
        }
    # Continuity-corrected z
    z = (abs(u_b - mu_u) - 0.5) / math.sqrt(var_u)
    z = max(0.0, z)
    p_val = max(0.0, min(1.0, 2.0 * (1.0 - norm_cdf(z))))
    return {
        "estimable": True,
        "test": "Mann_Whitney_U",
        "statistic": round(u_b, 4),
        "p_value": float(p_val),
        "method": "stdlib_tie_corrected_asymptotic",
    }


def wilcoxon_signed_rank_test(diffs: list[float]) -> dict[str, Any]:
    """Wilcoxon signed-rank test + Hodges-Lehmann pseudomedian + matched-pairs rank-biserial r."""
    nz = [d for d in diffs if abs(d) > 1e-15]
    n_nz = len(nz)
    # Walsh averages for Hodges-Lehmann pseudomedian
    n_all = len(diffs)
    if n_all == 0:
        return {"estimable": False, "reason": "no paired differences"}
    if n_all <= 400:
        walsh = sorted(0.5 * (diffs[i] + diffs[j]) for i in range(n_all) for j in range(i, n_all))
    else:
        rng = random.Random(42)
        walsh = sorted(0.5 * (diffs[rng.randrange(n_all)] + diffs[rng.randrange(n_all)]) for _ in range(50000))
    pseudomedian = statistics.median(walsh)

    if n_nz == 0:
        return {
            "estimable": False,
            "test": "Wilcoxon_Signed_Rank",
            "statistic": 0.0,
            "p_value": 1.0,
            "pseudomedian": 0.0,
            "pseudomedian_ci95": [0.0, 0.0],
            "rank_biserial_r": 0.0,
            "reason": "all_paired_differences_zero",
        }

    abs_vals = [abs(d) for d in nz]
    ranks, tie_sizes = average_ranks(abs_vals)
    w_pos = sum(r for d, r in zip(nz, ranks) if d > 0)
    w_neg = sum(r for d, r in zip(nz, ranks) if d < 0)
    w_stat = min(w_pos, w_neg)
    r_rb = (w_pos - w_neg) / max(w_pos + w_neg, 1e-15)

    if _sps is not None and n_nz >= 2:
        res = _sps.wilcoxon(nz, alternative="two-sided")
        p_val = float(res.pvalue)
        method = "scipy_wilcoxon"
    elif n_nz <= 12:
        # Exact sign-enumeration p-value for small n_nz <= 12 (2^12 = 4096)
        total = 1 << n_nz
        extreme = 0
        obs = min(w_pos, w_neg)
        for mask in range(total):
            wp = sum(ranks[i] for i in range(n_nz) if (mask >> i) & 1)
            wn = sum(ranks) - wp
            if min(wp, wn) <= obs + 1e-9:
                extreme += 1
        p_val = min(1.0, extreme / total)
        method = "stdlib_exact_sign_enumeration"
    else:
        mu = n_nz * (n_nz + 1.0) / 4.0
        tie_corr = sum(t ** 3 - t for t in tie_sizes) / 48.0
        var = n_nz * (n_nz + 1.0) * (2.0 * n_nz + 1.0) / 24.0 - tie_corr
        z = max(0.0, (abs(w_pos - mu) - 0.5) / math.sqrt(max(var, 1e-15)))
        p_val = max(0.0, min(1.0, 2.0 * (1.0 - norm_cdf(z))))
        method = "stdlib_tie_corrected_asymptotic"

    # Approximate 95% CI for pseudomedian via Walsh order statistics
    nw = len(walsh)
    if n_all >= 4 and n_all <= 400:
        z = 1.959963984540054
        k_idx = int(math.floor(nw / 2.0 - z * math.sqrt(n_all * (n_all + 1.0) * (2.0 * n_all + 1.0) / 24.0)))
        k_idx = max(0, min(nw - 1, k_idx))
        ci = [round(walsh[k_idx], 4), round(walsh[nw - 1 - k_idx], 4)]
    else:
        ci = [round(quantile_linear(walsh, 0.025), 4), round(quantile_linear(walsh, 0.975), 4)]

    return {
        "estimable": True,
        "test": "Wilcoxon_Signed_Rank",
        "statistic": round(w_stat, 4),
        "n_nonzero_pairs": n_nz,
        "p_value": float(p_val),
        "pseudomedian": round(pseudomedian, 4),
        "pseudomedian_ci95": ci,
        "rank_biserial_r": round(r_rb, 4),
        "method": method,
    }


def pearson_and_spearman(xs: list[float], ys: list[float]) -> dict[str, Any]:
    """Compute Pearson r (with Fisher-z 95% CI & R^2) and Spearman rho (with 95% CI)."""
    n = len(xs)
    if n < 3:
        return {"estimable": False, "reason": "n < 3"}

    def _corr_core(u: list[float], v: list[float]) -> tuple[float, float, float]:
        mu, vu = sample_mean_var(u)
        mv, vv = sample_mean_var(v)
        if vu <= 1e-15 or vv <= 1e-15:
            return math.nan, math.nan, math.nan
        cov = sum((a - mu) * (b - mv) for a, b in zip(u, v)) / (n - 1)
        r = max(-1.0, min(1.0, cov / math.sqrt(vu * vv)))
        if abs(r) >= 1.0 - 1e-12:
            return r, math.inf if r > 0 else -math.inf, 0.0
        t_stat = r * math.sqrt((n - 2.0) / max(1.0 - r * r, 1e-15))
        p_val = t_sf_two_sided(t_stat, n - 2.0)
        return r, t_stat, p_val

    r_p, t_p, p_p = _corr_core(xs, ys)
    if math.isfinite(r_p) and n >= 4 and abs(r_p) < 1.0 - 1e-12:
        z_r = 0.5 * math.log((1.0 + r_p) / (1.0 - r_p))
        se_z = 1.0 / math.sqrt(n - 3.0)
        z_c = 1.959963984540054
        p_ci = [round(math.tanh(z_r - z_c * se_z), 4), round(math.tanh(z_r + z_c * se_z), 4)]
    else:
        p_ci = [None, None]

    rx, _ = average_ranks(xs)
    ry, _ = average_ranks(ys)
    r_s, t_s, p_s = _corr_core(rx, ry)
    if math.isfinite(r_s) and n >= 4 and abs(r_s) < 1.0 - 1e-12:
        z_rs = 0.5 * math.log((1.0 + r_s) / (1.0 - r_s))
        # Bonett-Wright standard error for Spearman rho
        se_rs = math.sqrt((1.0 + 0.5 * r_s * r_s) / (n - 3.0))
        z_c = 1.959963984540054
        s_ci = [round(math.tanh(z_rs - z_c * se_rs), 4), round(math.tanh(z_rs + z_c * se_rs), 4)]
    else:
        s_ci = [None, None]

    return {
        "estimable": math.isfinite(r_p) and math.isfinite(r_s),
        "n": n,
        "df": n - 2,
        "pearson": {
            "r": round(r_p, 4) if math.isfinite(r_p) else None,
            "r_squared": round(r_p * r_p, 4) if math.isfinite(r_p) else None,
            "ci95_fisher_z": p_ci,
            "t_statistic": round(t_p, 6) if math.isfinite(t_p) else None,
            "p_value": float(p_p) if math.isfinite(p_p) else None,
        },
        "spearman": {
            "rho": round(r_s, 4) if math.isfinite(r_s) else None,
            "ci95_bonett_wright": s_ci,
            "t_statistic": round(t_s, 6) if math.isfinite(t_s) else None,
            "p_value": float(p_s) if math.isfinite(p_s) else None,
        },
    }


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> dict[str, Any]:
    """Exact two-sided Fisher's exact test + Odds Ratio with 95% CI in pure stdlib."""
    r1 = a + b
    r2 = c + d
    c1 = a + c
    n = r1 + r2
    if n == 0:
        return {"estimable": False, "reason": "empty 2x2 table"}

    def _hyper_p(k: int) -> float:
        return (math.comb(r1, k) * math.comb(r2, c1 - k)) / math.comb(n, c1)

    p_obs = _hyper_p(a)
    lo = max(0, c1 - r2)
    hi = min(r1, c1)
    p_two = sum(_hyper_p(k) for k in range(lo, hi + 1) if _hyper_p(k) <= p_obs + 1e-12)
    p_two = min(1.0, p_two)

    haldane = (a == 0 or b == 0 or c == 0 or d == 0)
    aa, bb, cc, dd = (a + 0.5, b + 0.5, c + 0.5, d + 0.5) if haldane else (float(a), float(b), float(c), float(d))
    or_val = (aa * dd) / (bb * cc)
    se_log = math.sqrt(1.0 / aa + 1.0 / bb + 1.0 / cc + 1.0 / dd)
    z = 1.959963984540054
    or_ci = [round(math.exp(math.log(or_val) - z * se_log), 4), round(math.exp(math.log(or_val) + z * se_log), 4)]
    return {
        "estimable": True,
        "test": "Fisher_Exact_2x2",
        "p_value": float(p_two),
        "odds_ratio": round(or_val, 4),
        "odds_ratio_ci95": or_ci,
        "haldane_anscombe_0_5_applied": haldane,
    }


def contingency_chi_square(counts: list[list[int]]) -> dict[str, Any]:
    """Pearson chi-square, Cramer's V, bias-corrected Cramer's V, and 2x2 Fisher exact."""
    r = len(counts)
    c = len(counts[0]) if r > 0 else 0
    row_sums = [sum(row) for row in counts]
    col_sums = [sum(counts[i][j] for i in range(r)) for j in range(c)]
    n = sum(row_sums)
    if r < 2 or c < 2 or n == 0:
        return {"estimable": False, "reason": "requires >= 2x2 non-empty contingency table"}

    chi2 = 0.0
    expected = [[0.0] * c for _ in range(r)]
    for i in range(r):
        for j in range(c):
            exp_ij = (row_sums[i] * col_sums[j]) / n
            expected[i][j] = round(exp_ij, 4)
            if exp_ij > 0:
                chi2 += ((counts[i][j] - exp_ij) ** 2) / exp_ij
    df = (r - 1) * (c - 1)
    p_val = chi2_sf(chi2, df)
    min_dim = min(r - 1, c - 1)
    cramers_v = math.sqrt(chi2 / max(n * min_dim, 1e-15))
    # Bergsma (2013) bias-corrected Cramer's V
    phi2_corr = max(0.0, (chi2 / n) - ((r - 1) * (c - 1)) / max(n - 1, 1))
    r_corr = r - ((r - 1) ** 2) / max(n - 1, 1)
    c_corr = c - ((c - 1) ** 2) / max(n - 1, 1)
    v_corr = math.sqrt(phi2_corr / max(min(r_corr - 1.0, c_corr - 1.0), 1e-15))

    out: dict[str, Any] = {
        "estimable": True,
        "test": "Pearson_Chi_Square",
        "statistic": round(chi2, 6),
        "df": df,
        "p_value": float(p_val),
        "cramers_v": round(cramers_v, 4),
        "cramers_v_bias_corrected": round(v_corr, 4),
        "expected_counts": expected,
    }
    if r == 2 and c == 2:
        out["fisher_exact_2x2"] = fisher_exact_2x2(counts[0][0], counts[0][1], counts[1][0], counts[1][1])
    return out


# ---------------------------------------------------------------------------
# Pure Python stdlib Vector SVG renderer + Optional Matplotlib PNG/PDF/SVG
# ---------------------------------------------------------------------------

def _nice_ticks(vmin: float, vmax: float, n_ticks: int = 5) -> list[float]:
    if not math.isfinite(vmin) or not math.isfinite(vmax):
        return [0.0, 1.0]
    if abs(vmax - vmin) < 1e-12:
        return [vmin - 1.0, vmin, vmin + 1.0]
    raw_step = (vmax - vmin) / max(n_ticks - 1, 1)
    mag = 10.0 ** math.floor(math.log10(abs(raw_step)))
    norm = raw_step / mag
    step = (1.0 if norm <= 1.2 else 2.0 if norm <= 2.5 else 5.0 if norm <= 6.0 else 10.0) * mag
    start = math.floor(vmin / step) * step
    ticks = []
    cur = start
    for _ in range(30):
        if cur > vmax + step * 1e-6:
            break
        if cur >= vmin - step * 1e-6:
            ticks.append(round(cur, 6))
        cur += step
    return ticks if len(ticks) >= 2 else [round(vmin, 4), round(vmax, 4)]


def _fmt_tick(v: float) -> str:
    if abs(v) < 1e-9:
        return "0"
    if abs(v) >= 1000 or abs(v) < 0.01:
        return f"{v:.2g}"
    return f"{v:.4g}"


def _render_group_plot_dual(
    *,
    groups: list[str],
    arrays: list[list[float]],
    desc: list[dict[str, Any]],
    group_col: str,
    value_col: str,
    unit: str,
    use_median: bool,
    out_dir: Path,
    stem: str,
) -> dict[str, Any]:
    """Render publication-ready vector SVG natively (stdlib), plus Matplotlib PNG/PDF/SVG if installed."""
    width_mm, height_mm = 114.0, 82.0
    # 96 dpi viewBox pixels matching 114 x 82 mm (~431 x 310 px)
    W = round(width_mm * 96.0 / 25.4, 2)
    H = round(height_mm * 96.0 / 25.4, 2)
    margin_l, margin_r, margin_t, margin_b = 62.0, 18.0, 34.0, 52.0
    plot_w = W - margin_l - margin_r
    plot_h = H - margin_t - margin_b

    all_pts = [x for a in arrays for x in a]
    ci_vals = []
    for d in desc:
        for k in ("ci95_low", "ci95_high", "q1", "q3"):
            if d.get(k) is not None and math.isfinite(d[k]):
                ci_vals.append(float(d[k]))
    extrema = all_pts + ci_vals
    if not extrema:
        extrema = [0.0, 1.0]
    raw_min = min(extrema)
    raw_max = max(extrema)
    has_neg = raw_min < 0.0
    has_pos = raw_max > 0.0
    span = max(raw_max - raw_min, 1e-6)
    if has_neg and has_pos:
        y_lo = raw_min - 0.12 * span
        y_hi = raw_max + 0.14 * span
    elif has_neg:
        y_lo = raw_min - 0.14 * span
        y_hi = max(0.0, raw_max + 0.12 * span)
    else:
        y_lo = 0.0 if (raw_min >= 0.0 and not use_median) else raw_min - 0.12 * span
        y_hi = raw_max + 0.14 * span
    # Guarantee 100% coverage of all raw points and CIs
    y_lo = min(y_lo, raw_min - 0.05 * span)
    y_hi = max(y_hi, raw_max + 0.05 * span)

    def _y_px(v: float) -> float:
        return margin_t + plot_h * (1.0 - (v - y_lo) / max(y_hi - y_lo, 1e-12))

    k = max(len(groups), 1)
    step_x = plot_w / k
    ylabel = f"{value_col} ({unit})" if unit and unit not in value_col else value_col
    summary_label = "Median and IQR" if use_median else "Mean and 95% CI"
    title_text = f"{value_col} across {group_col} ({summary_label})"
    panel_cx = margin_l + plot_w / 2.0

    # Deterministic jitter per group
    rng = random.Random(19)
    jittered: list[list[tuple[float, float]]] = []
    for gi, arr in enumerate(arrays):
        cx = margin_l + (gi + 0.5) * step_x
        pts = [(cx + rng.uniform(-min(14.0, step_x * 0.18), min(14.0, step_x * 0.18)), v) for v in arr]
        jittered.append(pts)

    # Pure stdlib SVG construction
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm" height="{height_mm}mm" viewBox="0 0 {W} {H}">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#FFFFFF"/>',
        '<style>text { font-family: Arial, "DejaVu Sans", sans-serif; fill: #111827; }</style>',
        f'<text x="{panel_cx:.2f}" y="20" text-anchor="middle" font-size="11" font-weight="normal">{html.escape(title_text)}</text>',
    ]
    # Axes lines (left & bottom spines only)
    svg_lines.append(f'<line x1="{margin_l:.2f}" y1="{margin_t:.2f}" x2="{margin_l:.2f}" y2="{margin_t + plot_h:.2f}" stroke="#111827" stroke-width="1"/>')
    svg_lines.append(f'<line x1="{margin_l:.2f}" y1="{margin_t + plot_h:.2f}" x2="{margin_l + plot_w:.2f}" y2="{margin_t + plot_h:.2f}" stroke="#111827" stroke-width="1"/>')

    # Zero reference line when data crosses zero or has negative values
    if y_lo < 0.0 < y_hi:
        y0 = _y_px(0.0)
        svg_lines.append(f'<line x1="{margin_l:.2f}" y1="{y0:.2f}" x2="{margin_l + plot_w:.2f}" y2="{y0:.2f}" stroke="#6B7280" stroke-width="0.9" stroke-dasharray="4,3"/>')

    # Y ticks
    for tv in _nice_ticks(y_lo, y_hi, 5):
        yp = _y_px(tv)
        if margin_t - 1 <= yp <= margin_t + plot_h + 1:
            svg_lines.append(f'<line x1="{margin_l - 4:.2f}" y1="{yp:.2f}" x2="{margin_l:.2f}" y2="{yp:.2f}" stroke="#111827" stroke-width="0.9"/>')
            svg_lines.append(f'<text x="{margin_l - 7:.2f}" y="{yp + 3:.2f}" text-anchor="end" font-size="9">{html.escape(_fmt_tick(tv))}</text>')

    # Group bars/boxes + error bars + raw points
    bar_w = min(38.0, step_x * 0.46)
    y_zero = _y_px(0.0 if (y_lo <= 0.0 <= y_hi) else y_lo)
    for gi, (g, d, arr) in enumerate(zip(groups, desc, arrays)):
        cx = margin_l + (gi + 0.5) * step_x
        col = CELL_PALETTE[gi % len(CELL_PALETTE)]
        if arr:
            if use_median:
                y_q1 = _y_px(float(d["q1"]))
                y_q3 = _y_px(float(d["q3"]))
                y_med = _y_px(float(d["median"]))
                box_top = min(y_q1, y_q3)
                box_h = max(1.5, abs(y_q1 - y_q3))
                svg_lines.append(f'<rect x="{cx - bar_w / 2:.2f}" y="{box_top:.2f}" width="{bar_w:.2f}" height="{box_h:.2f}" fill="{col}" fill-opacity="0.65" stroke="#1F2937" stroke-width="1"/>')
                svg_lines.append(f'<line x1="{cx - bar_w / 2:.2f}" y1="{y_med:.2f}" x2="{cx + bar_w / 2:.2f}" y2="{y_med:.2f}" stroke="#111827" stroke-width="1.8"/>')
            else:
                ym = _y_px(float(d["mean"]))
                top_y = min(ym, y_zero)
                bh = max(1.0, abs(ym - y_zero))
                svg_lines.append(f'<rect x="{cx - bar_w / 2:.2f}" y="{top_y:.2f}" width="{bar_w:.2f}" height="{bh:.2f}" fill="{col}" fill-opacity="0.72" stroke="#1F2937" stroke-width="0.9"/>')
                if d.get("ci95_low") is not None and d.get("ci95_high") is not None:
                    ylo_p = _y_px(float(d["ci95_low"]))
                    yhi_p = _y_px(float(d["ci95_high"]))
                    svg_lines.append(f'<line x1="{cx:.2f}" y1="{yhi_p:.2f}" x2="{cx:.2f}" y2="{ylo_p:.2f}" stroke="#111827" stroke-width="1.1"/>')
                    svg_lines.append(f'<line x1="{cx - 5:.2f}" y1="{yhi_p:.2f}" x2="{cx + 5:.2f}" y2="{yhi_p:.2f}" stroke="#111827" stroke-width="1.1"/>')
                    svg_lines.append(f'<line x1="{cx - 5:.2f}" y1="{ylo_p:.2f}" x2="{cx + 5:.2f}" y2="{ylo_p:.2f}" stroke="#111827" stroke-width="1.1"/>')
            # Raw individual observations
            for px, pv in jittered[gi]:
                svg_lines.append(f'<circle cx="{px:.2f}" cy="{_y_px(pv):.2f}" r="2.6" fill="#1F2937" stroke="#FFFFFF" stroke-width="0.6"/>')
        # X tick & label with n
        svg_lines.append(f'<line x1="{cx:.2f}" y1="{margin_t + plot_h:.2f}" x2="{cx:.2f}" y2="{margin_t + plot_h + 4:.2f}" stroke="#111827" stroke-width="0.9"/>')
        lbl = g if len(g) <= 16 else g[:14] + ".."
        svg_lines.append(f'<text x="{cx:.2f}" y="{margin_t + plot_h + 15:.2f}" text-anchor="middle" font-size="8.5">{html.escape(lbl)}</text>')
        svg_lines.append(f'<text x="{cx:.2f}" y="{margin_t + plot_h + 26:.2f}" text-anchor="middle" font-size="8" fill="#4B5563">n={d["n"]}</text>')

    # Axis titles
    svg_lines.append(f'<text x="{panel_cx:.2f}" y="{H - 8:.2f}" text-anchor="middle" font-size="9.5">{html.escape(group_col)}</text>')
    y_mid = margin_t + plot_h / 2.0
    svg_lines.append(f'<text x="15" y="{y_mid:.2f}" text-anchor="middle" font-size="9.5" transform="rotate(-90 15 {y_mid:.2f})">{html.escape(ylabel)}</text>')
    svg_lines.append("</svg>")

    svg_p = out_dir / f"{stem}_plot.svg"
    svg_p.write_text("\n".join(svg_lines) + "\n", encoding="utf-8")
    charts = [svg_p.name]
    render_backend = "python_stdlib_svg"

    # Optional Matplotlib 300 dpi PNG + editable PDF + SVG enhancement
    if _plt is not None:
        render_backend = "matplotlib_plus_stdlib_svg"
        _plt.rcParams.update({
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
            "font.size": 8.0,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.linewidth": 0.8,
        })
        fig, ax = _plt.subplots(figsize=(width_mm / 25.4, height_mm / 25.4), dpi=300)
        xs = list(range(len(groups)))
        colors = [CELL_PALETTE[i % len(CELL_PALETTE)] for i in range(len(groups))]
        if use_median:
            non_empty = [(i, a) for i, a in enumerate(arrays) if len(a) > 0]
            if non_empty:
                bp = ax.boxplot(
                    [a for _, a in non_empty],
                    positions=[i for i, _ in non_empty],
                    widths=0.46,
                    patch_artist=True,
                    showfliers=False,
                    medianprops={"color": "#111827", "linewidth": 1.3},
                    boxprops={"edgecolor": "#1F2937", "linewidth": 0.8},
                    whiskerprops={"color": "#1F2937", "linewidth": 0.8},
                    capprops={"color": "#1F2937", "linewidth": 0.8},
                )
                for patch, (gi, _) in zip(bp["boxes"], non_empty):
                    patch.set_facecolor(colors[gi])
                    patch.set_alpha(0.68)
        else:
            means = [float(d["mean"]) if d["mean"] is not None else 0.0 for d in desc]
            yerr_lo = [max(0.0, float(d["mean"]) - float(d["ci95_low"])) if (d["mean"] is not None and d["ci95_low"] is not None) else 0.0 for d in desc]
            yerr_hi = [max(0.0, float(d["ci95_high"]) - float(d["mean"])) if (d["mean"] is not None and d["ci95_high"] is not None) else 0.0 for d in desc]
            ax.bar(xs, means, yerr=[yerr_lo, yerr_hi], width=0.52, color=colors, edgecolor="#1F2937", linewidth=0.75, capsize=3.0, alpha=0.78, zorder=2)

        rng_m = random.Random(19)
        for gi, arr in enumerate(arrays):
            if arr:
                jx = [gi + rng_m.uniform(-0.15, 0.15) for _ in arr]
                ax.scatter(jx, arr, s=16, color="#1F2937", edgecolor="#FFFFFF", linewidth=0.45, zorder=5)

        if y_lo < 0.0 < y_hi:
            ax.axhline(0.0, color="#6B7280", linewidth=0.8, linestyle="--", zorder=1)
        ax.set_ylim(y_lo, y_hi)
        ax.set_xticks(xs)
        ax.set_xticklabels([f"{g}\n(n={d['n']})" for g, d in zip(groups, desc)], fontsize=7.5)
        ax.set_xlabel(group_col, fontsize=8.5)
        ax.set_ylabel(ylabel, fontsize=8.5)
        ax.set_title(title_text, loc="center", fontsize=9.0, fontweight="normal", pad=8.0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.subplots_adjust(left=0.17, right=0.96, top=0.87, bottom=0.20)

        png_p = out_dir / f"{stem}_plot.png"
        pdf_p = out_dir / f"{stem}_plot.pdf"
        fig.savefig(png_p, format="png", dpi=300, bbox_inches=None)
        fig.savefig(pdf_p, format="pdf", bbox_inches=None)
        fig.savefig(svg_p, format="svg", bbox_inches=None)
        _plt.close(fig)
        charts = [png_p.name, svg_p.name, pdf_p.name]

    return {
        "backend": render_backend,
        "charts": charts,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "actual_ylim": [round(y_lo, 4), round(y_hi, 4)],
        "all_raw_points_covered": all(y_lo <= v <= y_hi for v in all_pts),
    }


def _render_scatter_or_bar_svg(
    *,
    title_text: str,
    xlabel: str,
    ylabel: str,
    points: list[tuple[float, float]] | None = None,
    bars: list[tuple[str, float]] | None = None,
    out_dir: Path,
    stem: str,
) -> dict[str, Any]:
    """Minimal Cell-tendency stdlib SVG (+ optional Matplotlib PNG/PDF) for correlation/contingency."""
    width_mm, height_mm = 114.0, 82.0
    W = round(width_mm * 96.0 / 25.4, 2)
    H = round(height_mm * 96.0 / 25.4, 2)
    ml, mr, mt, mb = 60.0, 18.0, 34.0, 46.0
    pw, ph = W - ml - mr, H - mt - mb
    panel_cx = ml + pw / 2.0

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm" height="{height_mm}mm" viewBox="0 0 {W} {H}">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#FFFFFF"/>',
        '<style>text { font-family: Arial, "DejaVu Sans", sans-serif; fill: #111827; }</style>',
        f'<text x="{panel_cx:.2f}" y="20" text-anchor="middle" font-size="11" font-weight="normal">{html.escape(title_text)}</text>',
        f'<line x1="{ml:.2f}" y1="{mt:.2f}" x2="{ml:.2f}" y2="{mt + ph:.2f}" stroke="#111827" stroke-width="1"/>',
        f'<line x1="{ml:.2f}" y1="{mt + ph:.2f}" x2="{ml + pw:.2f}" y2="{mt + ph:.2f}" stroke="#111827" stroke-width="1"/>',
    ]
    if points:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        xlo, xhi = min(xs), max(xs)
        ylo, yhi = min(ys), max(ys)
        xsp, ysp = max(xhi - xlo, 1e-6), max(yhi - ylo, 1e-6)
        xlo -= 0.08 * xsp; xhi += 0.08 * xsp
        ylo -= 0.08 * ysp; yhi += 0.08 * ysp
        for tv in _nice_ticks(ylo, yhi, 5):
            yp = mt + ph * (1.0 - (tv - ylo) / (yhi - ylo))
            if mt - 1 <= yp <= mt + ph + 1:
                svg.append(f'<text x="{ml - 6:.2f}" y="{yp + 3:.2f}" text-anchor="end" font-size="8.5">{html.escape(_fmt_tick(tv))}</text>')
        for tv in _nice_ticks(xlo, xhi, 5):
            xp = ml + pw * ((tv - xlo) / (xhi - xlo))
            if ml - 1 <= xp <= ml + pw + 1:
                svg.append(f'<text x="{xp:.2f}" y="{mt + ph + 14:.2f}" text-anchor="middle" font-size="8.5">{html.escape(_fmt_tick(tv))}</text>')
        for xv, yv in points:
            xp = ml + pw * ((xv - xlo) / (xhi - xlo))
            yp = mt + ph * (1.0 - (yv - ylo) / (yhi - ylo))
            svg.append(f'<circle cx="{xp:.2f}" cy="{yp:.2f}" r="2.8" fill="{CELL_PALETTE[0]}" fill-opacity="0.82" stroke="#FFFFFF" stroke-width="0.5"/>')
    elif bars:
        vals = [v for _, v in bars]
        ylo = 0.0
        yhi = max(max(vals, default=1.0) * 1.15, 1.0)
        step = pw / max(len(bars), 1)
        bw = min(36.0, step * 0.52)
        for i, (lbl, v) in enumerate(bars):
            cx = ml + (i + 0.5) * step
            yp = mt + ph * (1.0 - (v - ylo) / (yhi - ylo))
            bh = (mt + ph) - yp
            svg.append(f'<rect x="{cx - bw / 2:.2f}" y="{yp:.2f}" width="{bw:.2f}" height="{bh:.2f}" fill="{CELL_PALETTE[i % len(CELL_PALETTE)]}" stroke="#1F2937" stroke-width="0.8"/>')
            svg.append(f'<text x="{cx:.2f}" y="{mt + ph + 14:.2f}" text-anchor="middle" font-size="8">{html.escape(lbl[:16])}</text>')

    svg.append(f'<text x="{panel_cx:.2f}" y="{H - 8:.2f}" text-anchor="middle" font-size="9">{html.escape(xlabel)}</text>')
    ym = mt + ph / 2.0
    svg.append(f'<text x="14" y="{ym:.2f}" text-anchor="middle" font-size="9" transform="rotate(-90 14 {ym:.2f})">{html.escape(ylabel)}</text>')
    svg.append("</svg>")
    svg_p = out_dir / f"{stem}_plot.svg"
    svg_p.write_text("\n".join(svg) + "\n", encoding="utf-8")
    charts = [svg_p.name]
    backend = "python_stdlib_svg"

    if _plt is not None:
        backend = "matplotlib_plus_stdlib_svg"
        _plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["Arial", "DejaVu Sans"], "font.size": 8.0, "pdf.fonttype": 42, "svg.fonttype": "none"})
        fig, ax = _plt.subplots(figsize=(width_mm / 25.4, height_mm / 25.4), dpi=300)
        if points:
            ax.scatter([p[0] for p in points], [p[1] for p in points], s=18, color=CELL_PALETTE[0], edgecolor="#FFFFFF", linewidth=0.45)
        elif bars:
            ax.bar(range(len(bars)), [v for _, v in bars], color=[CELL_PALETTE[i % len(CELL_PALETTE)] for i in range(len(bars))], edgecolor="#1F2937", linewidth=0.75)
            ax.set_xticks(range(len(bars)))
            ax.set_xticklabels([b[0] for b in bars], fontsize=7.5)
        ax.set_xlabel(xlabel, fontsize=8.5)
        ax.set_ylabel(ylabel, fontsize=8.5)
        ax.set_title(title_text, loc="center", fontsize=9.0, fontweight="normal", pad=8.0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.subplots_adjust(left=0.17, right=0.96, top=0.87, bottom=0.18)
        png_p = out_dir / f"{stem}_plot.png"
        pdf_p = out_dir / f"{stem}_plot.pdf"
        fig.savefig(png_p, format="png", dpi=300, bbox_inches=None)
        fig.savefig(pdf_p, format="pdf", bbox_inches=None)
        fig.savefig(svg_p, format="svg", bbox_inches=None)
        _plt.close(fig)
        charts = [png_p.name, svg_p.name, pdf_p.name]
    return {"backend": backend, "charts": charts, "width_mm": width_mm, "height_mm": height_mm}


# ---------------------------------------------------------------------------
# Linear Imperative Reproducible Script Emitter (bio-code research style)
# ---------------------------------------------------------------------------

def _emit_reproduce_script(
    *,
    out_dir: Path,
    input_name: str,
    source_sha256: str,
    design: str,
    group_col: str | None,
    value_col: str | None,
    x_col: str | None,
    subject_col: str | None,
    table: str | None,
    route: str,
) -> str:
    """Emit a concise, linear, researcher-readable standalone reproduction script."""
    script_name = "reproduce_stats.py"
    lines = [
        "#!/usr/bin/env python3",
        f"# Reproducible statistical analysis for {input_name} (design: {design}, route: {route}).",
        "# Linear imperative script: reads raw table read-only, verifies SHA-256, deduplicates exact rows,",
        "# excludes missing values without zero-imputation, and prints group summaries for independent audit.",
        "",
        "import csv, hashlib, json, math, sqlite3, statistics",
        "from pathlib import Path",
        "",
        "# 1. Input parameters (adjust path if relocated)",
        f"DATA_PATH = Path({input_name!r})",
        f"EXPECTED_SHA256 = {source_sha256!r}",
        f"SQLITE_TABLE = {table!r}",
        f"DESIGN = {design!r}",
        f"GROUP_COL = {group_col!r}",
        f"VALUE_COL = {value_col!r}",
        f"X_COL = {x_col!r}",
        f"SUBJECT_COL = {subject_col!r}",
        "MISSING_TOKENS = {'', 'na', 'n/a', 'nan', 'null', 'none', 'nd', '.', '-'}",
        "",
        "# 2. Verify source fingerprint and load read-only",
        "actual_sha = hashlib.sha256(DATA_PATH.read_bytes()).hexdigest()",
        "print(f'Source SHA-256 match: {actual_sha == EXPECTED_SHA256} ({actual_sha[:16]}...)')",
        "if DATA_PATH.suffix.lower() in {'.sqlite', '.sqlite3', '.db'}:",
        "    conn = sqlite3.connect(f'file:{DATA_PATH.resolve()}?mode=ro', uri=True)",
        "    tbl = SQLITE_TABLE or conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name\").fetchone()[0]",
        "    cur = conn.execute(f'SELECT * FROM \"{tbl}\"')",
        "    cols = [d[0] for d in cur.description]",
        "    raw_rows = [dict(zip(cols, r)) for r in cur.fetchall()]",
        "    conn.close()",
        "else:",
        "    with DATA_PATH.open('r', encoding='utf-8-sig', newline='') as fh:",
        "        reader = csv.DictReader(fh)",
        "        cols = list(reader.fieldnames or [])",
        "        raw_rows = list(reader)",
        "print(f'Loaded raw rows: {len(raw_rows)}, columns: {len(cols)}')",
        "",
        "# 3. Remove exact duplicate rows while preserving original row order",
        "seen, rows = set(), []",
        "for r in raw_rows:",
        "    k = tuple(str(r.get(c, '')) for c in cols)",
        "    if k not in seen:",
        "        seen.add(k)",
        "        rows.append(r)",
        "print(f'Exact duplicates removed: {len(raw_rows) - len(rows)}, retained: {len(rows)}')",
        "",
        "# 4. Inspect cleaned numeric distributions (missing excluded; true zeros kept)",
        "if DESIGN == 'independent' and GROUP_COL and VALUE_COL:",
        "    groups = {}",
        "    for r in rows:",
        "        g, raw_v = str(r.get(GROUP_COL, '')).strip(), str(r.get(VALUE_COL, '')).strip()",
        "        if g.lower() in MISSING_TOKENS or raw_v.lower() in MISSING_TOKENS:",
        "            continue",
        "        groups.setdefault(g, []).append(float(raw_v))",
        "    for g, vals in groups.items():",
        "        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0",
        "        print(f'{g}: n={len(vals)}, mean={statistics.mean(vals):.4f}, sd={sd:.4f}, median={statistics.median(vals):.4f}')",
        "elif DESIGN == 'correlation' and X_COL and VALUE_COL:",
        "    pairs = [(float(r[X_COL]), float(r[VALUE_COL])) for r in rows if str(r.get(X_COL, '')).strip().lower() not in MISSING_TOKENS and str(r.get(VALUE_COL, '')).strip().lower() not in MISSING_TOKENS]",
        "    print(f'Complete (x, y) pairs: {len(pairs)}')",
        "elif DESIGN == 'paired' and GROUP_COL and VALUE_COL and SUBJECT_COL:",
        "    print(f'Paired subjects checked via {SUBJECT_COL}; re-run inference_effect_ci_runner.py --design paired for full CI table.')",
        "elif DESIGN == 'contingency' and GROUP_COL and VALUE_COL:",
        "    print(f'Contingency counts verified across {GROUP_COL} x {VALUE_COL}; re-run inference_effect_ci_runner.py --design contingency.')",
    ]
    (out_dir / script_name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return script_name


# ---------------------------------------------------------------------------
# Main statistical report orchestrator
# ---------------------------------------------------------------------------

def run_statistical_report(
    input_path: Path,
    *,
    design: str = "independent",
    group_col: str | None = None,
    value_col: str | None = None,
    x_col: str | None = None,
    subject_col: str | None = None,
    unit: str = "",
    out_dir: Path,
    stem: str = "stats_report",
    table: str | None = None,
    allow_encoded_integer: bool = False,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    diag = check_assumptions(
        input_path,
        group_col=group_col,
        value_col=value_col,
        x_col=x_col,
        subject_col=subject_col,
        design=design,
        table=table,
        allow_encoded_integer=allow_encoded_integer,
    )

    if not diag["ok"]:
        blocked_report = {
            "ok": False,
            "estimability_status": diag.get("estimability_status", "BLOCKED"),
            "assumptions": diag,
            "blockers": diag["blockers"],
            "runtime_env": {
                "python": platform.python_version(),
                "scipy_available": _sps is not None,
                "matplotlib_available": _plt is not None,
            },
        }
        jp = out_dir / f"{stem}.json"
        jp.write_text(json.dumps(blocked_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"ok": False, "blocked": True, "blockers": diag["blockers"], "json": jp.name}

    cols, raw_rows, source_meta = load_table_ro(input_path, table=table)
    seen: set[tuple[str, ...]] = set()
    rows: list[dict[str, Any]] = []
    for r in raw_rows:
        key = tuple(str(r.get(c, "")) for c in cols)
        if key not in seen:
            seen.add(key)
            rows.append(r)

    design_norm = diag["design"]
    route = diag["recommended_route"]
    repro_script = _emit_reproduce_script(
        out_dir=out_dir,
        input_name=input_path.name,
        source_sha256=source_meta["sha256"],
        design=design_norm,
        group_col=group_col,
        value_col=value_col,
        x_col=x_col,
        subject_col=subject_col,
        table=table,
        route=route,
    )

    report: dict[str, Any] = {
        "ok": True,
        "design": design_norm,
        "backend": diag["backend"],
        "estimability_status": diag["estimability_status"],
        "recommended_route": route,
        "unit": unit or "unspecified (verify against data dictionary)",
        "runtime_env": {
            "python": platform.python_version(),
            "scipy_available": _sps is not None,
            "matplotlib_available": _plt is not None,
        },
        "assumptions": diag,
        "reproduce_script": repro_script,
    }
    md_lines: list[str] = [f"# Statistical Analysis Report ({input_path.name})", ""]

    if design_norm == "independent":
        assert group_col and value_col
        groups: list[str] = diag["groups"]
        grouped: dict[str, list[float]] = {g: [] for g in groups}
        for r in rows:
            gv = r.get(group_col)
            fv = try_parse_float(r.get(value_col))
            if is_missing_raw(gv) or fv is None:
                continue
            gs = str(gv).strip()
            if gs in grouped:
                grouped[gs].append(fv)
        arrays = [grouped[g] for g in groups]

        desc: list[dict[str, Any]] = []
        for g, arr in zip(groups, arrays):
            n = len(arr)
            if n == 0:
                desc.append({
                    "group": g, "n": 0, "missing": diag["group_diagnostics"][g]["missing"],
                    "mean": None, "sd": None, "sem": None, "ci95_low": None, "ci95_high": None,
                    "median": None, "q1": None, "q3": None, "iqr": None, "min": None, "max": None,
                    "group_status": diag["group_diagnostics"][g]["group_status"],
                })
                continue
            m, v = sample_mean_var(arr)
            sd = math.sqrt(v) if n >= 2 else 0.0
            sem = sd / math.sqrt(n) if n >= 2 else 0.0
            if n >= 2 and sem > 1e-15:
                tc = t_ppf(0.975, n - 1)
                lo, hi = m - tc * sem, m + tc * sem
            else:
                lo, hi = m, m
            q1 = quantile_linear(arr, 0.25)
            med = quantile_linear(arr, 0.50)
            q3 = quantile_linear(arr, 0.75)
            desc.append({
                "group": g,
                "n": n,
                "missing": diag["group_diagnostics"][g]["missing"],
                "mean": round(m, 4),
                "sd": round(sd, 4),
                "sem": round(sem, 4),
                "ci95_low": round(lo, 4),
                "ci95_high": round(hi, 4),
                "median": round(med, 4),
                "q1": round(q1, 4),
                "q3": round(q3, 4),
                "iqr": round(q3 - q1, 4),
                "min": round(min(arr), 4),
                "max": round(max(arr), 4),
                "group_status": diag["group_diagnostics"][g]["group_status"],
            })

        est_indices = [i for i, a in enumerate(arrays) if len(a) >= 2]
        est_arrays = [arrays[i] for i in est_indices]
        omnibus: dict[str, Any] = {"route": route, "estimability_status": diag["estimability_status"]}
        if len(est_arrays) >= 2:
            if route == "parametric_anova_student":
                omnibus.update(one_way_anova(est_arrays))
            elif route == "parametric_welch_anova":
                w_res = welch_anova(est_arrays)
                if w_res.get("estimable"):
                    omnibus.update(w_res)
                else:
                    omnibus.update(kruskal_wallis_test(est_arrays))
                    omnibus["fallback_reason"] = w_res.get("reason")
            elif route == "parametric_student_t":
                omnibus.update(two_sample_t_test(est_arrays[0], est_arrays[1], equal_var=True))
            elif route == "parametric_welch_t":
                omnibus.update(two_sample_t_test(est_arrays[0], est_arrays[1], equal_var=False))
            else:
                if len(est_arrays) >= 3:
                    omnibus.update(kruskal_wallis_test(est_arrays))
                else:
                    omnibus.update(mann_whitney_u_test(est_arrays[0], est_arrays[1]))
        else:
            omnibus.update({"estimable": False, "test": "none_insufficient_groups"})

        # Pairwise comparisons with both parametric (exact Welch df) and non-parametric (Hodges-Lehmann + Cliff's delta)
        raw_pvals: list[float | None] = []
        pairwise: list[dict[str, Any]] = []
        use_nonparam = route.startswith("nonparametric")
        eq_var = route in {"parametric_anova_student", "parametric_student_t"}
        for i, j in itertools.combinations(range(len(groups)), 2):
            ga, gb = groups[i], groups[j]
            a, b = arrays[i], arrays[j]
            row_entry: dict[str, Any] = {
                "pair": f"{gb} minus {ga}",
                "group_a": ga,
                "group_b": gb,
                "n_a": len(a),
                "n_b": len(b),
            }
            if len(a) < 2 or len(b) < 2:
                row_entry.update({
                    "contrast_estimability": "UNESTIMABLE_SMALL_N",
                    "primary_test": "skipped_n_lt_2",
                    "p_raw": None,
                })
                raw_pvals.append(None)
                pairwise.append(row_entry)
                continue

            np_eff = cliffs_delta_and_hodges_lehmann(a, b)
            hg, hg_status = hedges_g_pair(a, b)
            t_res = two_sample_t_test(a, b, equal_var=eq_var)
            mw_res = mann_whitney_u_test(a, b)

            # Determine contrast estimability status
            sa_zero = diag["group_diagnostics"][ga]["group_status"] != "OK"
            sb_zero = diag["group_diagnostics"][gb]["group_status"] != "OK"
            c_status = "DEGRADED_SMALL_N_OR_ZERO_VAR" if (sa_zero or sb_zero) else "ESTIMABLE"

            if use_nonparam or not t_res.get("estimable"):
                prim_test = mw_res.get("test", "Mann_Whitney_U")
                p_raw = mw_res.get("p_value")
                prim_eff_name = "cliffs_delta"
                prim_eff_val = np_eff["cliffs_delta"]
                prim_diff_name = "hodges_lehmann_diff"
                prim_diff_val = np_eff["hodges_lehmann_diff"]
                prim_ci = np_eff["hl_ci95"]
            else:
                prim_test = t_res["test"]
                p_raw = t_res["p_value"]
                prim_eff_name = "hedges_g"
                prim_eff_val = hg
                prim_diff_name = "diff_mean"
                prim_diff_val = t_res["diff_mean"]
                prim_ci = t_res["diff_ci95"]

            row_entry.update({
                "contrast_estimability": c_status,
                "primary_test": prim_test,
                "primary_diff_name": prim_diff_name,
                "primary_diff": prim_diff_val,
                "primary_diff_ci95": prim_ci,
                "primary_effect_name": prim_eff_name,
                "primary_effect": prim_eff_val,
                "diff_mean": t_res.get("diff_mean"),
                "diff_mean_ci95": t_res.get("diff_ci95"),
                "t_df": t_res.get("df"),
                "hedges_g": hg,
                "hedges_g_status": hg_status,
                "hodges_lehmann_diff": np_eff["hodges_lehmann_diff"],
                "hl_ci95": np_eff["hl_ci95"],
                "cliffs_delta": np_eff["cliffs_delta"],
                "rank_biserial_r": np_eff["rank_biserial_r"],
                "cliffs_magnitude": np_eff["cliffs_magnitude"],
                "p_raw": round(float(p_raw), 8) if (p_raw is not None and math.isfinite(p_raw)) else None,
            })
            raw_pvals.append(float(p_raw) if (p_raw is not None and math.isfinite(p_raw)) else None)
            pairwise.append(row_entry)

        for pw, ph, qbh in zip(pairwise, holm_adjust(raw_pvals), bh_fdr_adjust(raw_pvals)):
            pw["p_holm"] = ph
            pw["q_bh_fdr"] = qbh
            pw["statistically_significant_holm_05"] = (ph is not None and ph < 0.05)

        plot_info = _render_group_plot_dual(
            groups=groups,
            arrays=arrays,
            desc=desc,
            group_col=group_col,
            value_col=value_col,
            unit=unit,
            use_median=use_nonparam,
            out_dir=out_dir,
            stem=stem,
        )
        report.update({
            "descriptive_summary": desc,
            "omnibus_test": omnibus,
            "pairwise_comparisons": pairwise,
            "plot_audit": plot_info,
        })

        # Domain-language restrained scientific narrative (no backend jargon leakage)
        md_lines.extend([
            f"**Outcome**: {value_col}" + (f" ({unit})" if unit else "") + f" across {group_col} (n={diag['rows_clean']} clean observations; {diag['exact_duplicates_removed']} exact duplicates removed; {diag['missing_dropped']} missing values excluded without zero-imputation).",
            "",
            "## Group Descriptive Summary",
            "",
            "| Group | n | Missing | Mean | SD | 95% CI (Mean) | Median | IQR | Min | Max | Note |",
            "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |",
        ])
        for d in desc:
            ci_s = f"[{d['ci95_low']}, {d['ci95_high']}]" if d["ci95_low"] is not None else "NA"
            note = "OK" if d["group_status"] == "OK" else d["group_status"].replace("_", " ").lower()
            md_lines.append(
                f"| {d['group']} | {d['n']} | {d['missing']} | {d['mean']} | {d['sd']} | {ci_s} | {d['median']} | {d['iqr']} | {d['min']} | {d['max']} | {note} |"
            )
        md_lines.extend([
            "",
            "## Global Comparison & Multiple-Testing-Adjusted Contrasts",
            "",
            f"- Global test: **{omnibus.get('test')}** (statistic={omnibus.get('statistic')}, p={omnibus.get('p_value')}, {omnibus.get('effect_size_name')}={omnibus.get('effect_size')}).",
            "",
            "| Contrast (B - A) | Test | Primary Shift (95% CI) | Effect Size | p_raw | p_holm | q_BH | Status |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ])
        for pw in pairwise:
            eff_s = f"{pw.get('primary_effect_name')}={pw.get('primary_effect')}"
            md_lines.append(
                f"| {pw['pair']} | {pw['primary_test']} | {pw.get('primary_diff')} {pw.get('primary_diff_ci95')} | {eff_s} | {pw.get('p_raw')} | {pw.get('p_holm')} | {pw.get('q_bh_fdr')} | {pw.get('contrast_estimability')} |"
            )

    elif design_norm == "paired":
        assert group_col and value_col and subject_col
        g1, g2 = diag["paired_groups"][0], diag["paired_groups"][1]
        subj_map: dict[str, dict[str, list[float]]] = {}
        for r in rows:
            gv, sv, fv = r.get(group_col), r.get(subject_col), try_parse_float(r.get(value_col))
            if is_missing_raw(gv) or is_missing_raw(sv) or fv is None:
                continue
            subj_map.setdefault(str(sv).strip(), {}).setdefault(str(gv).strip(), []).append(fv)
        v1s, v2s, diffs = [], [], []
        for ss, sm in subj_map.items():
            if g1 in sm and g2 in sm:
                a1 = sum(sm[g1]) / len(sm[g1])
                a2 = sum(sm[g2]) / len(sm[g2])
                v1s.append(a1)
                v2s.append(a2)
                diffs.append(a2 - a1)
        nd = len(diffs)
        md_diff, vd_diff = sample_mean_var(diffs)
        sd_diff = math.sqrt(vd_diff) if nd >= 2 else 0.0
        se_diff = sd_diff / math.sqrt(nd) if nd >= 2 else 0.0
        if nd >= 2 and se_diff > 1e-15:
            t_stat = md_diff / se_diff
            p_t = t_sf_two_sided(t_stat, nd - 1)
            tc = t_ppf(0.975, nd - 1)
            ci_t = [round(md_diff - tc * se_diff, 4), round(md_diff + tc * se_diff, 4)]
            j_corr = 1.0 - 3.0 / max(4.0 * (nd - 1) - 1.0, 1.0)
            hedges_gz = round((md_diff / sd_diff) * j_corr, 4)
        else:
            t_stat, p_t, ci_t, hedges_gz = None, None, [None, None], None
        wilc = wilcoxon_signed_rank_test(diffs)
        paired_res = {
            "contrast": f"{g2} minus {g1}",
            "n_complete_pairs": nd,
            "n_unpaired_excluded": diag["n_unpaired_excluded"],
            "mean_diff": round(md_diff, 4) if math.isfinite(md_diff) else None,
            "sd_diff": round(sd_diff, 4),
            "paired_t": {
                "t_statistic": round(t_stat, 6) if t_stat is not None else None,
                "df": nd - 1 if nd >= 2 else None,
                "p_value": float(p_t) if p_t is not None else None,
                "diff_ci95": ci_t,
                "hedges_gz": hedges_gz,
            },
            "wilcoxon_signed_rank": wilc,
        }
        plot_info = _render_scatter_or_bar_svg(
            title_text=f"Paired comparison: {g2} vs {g1} ({value_col})",
            xlabel=f"{g1} ({value_col})",
            ylabel=f"{g2} ({value_col})",
            points=list(zip(v1s, v2s)),
            out_dir=out_dir,
            stem=stem,
        )
        report.update({"paired_inference": paired_res, "plot_audit": plot_info})
        md_lines.extend([
            f"**Paired contrast**: {g2} minus {g1} across n={nd} complete subjects ({diag['n_unpaired_excluded']} incomplete subjects excluded without imputation).",
            f"- Paired t-test: mean diff = {paired_res['mean_diff']} (95% CI {ci_t}), t = {paired_res['paired_t']['t_statistic']}, df = {paired_res['paired_t']['df']}, p = {paired_res['paired_t']['p_value']}, Hedges' g_z = {hedges_gz}.",
            f"- Wilcoxon signed-rank: pseudomedian = {wilc.get('pseudomedian')} (95% CI {wilc.get('pseudomedian_ci95')}), W = {wilc.get('statistic')}, p = {wilc.get('p_value')}, matched-pairs rank-biserial r = {wilc.get('rank_biserial_r')}.",
        ])

    elif design_norm == "correlation":
        assert x_col and value_col
        xs, ys = [], []
        for r in rows:
            xv, yv = try_parse_float(r.get(x_col)), try_parse_float(r.get(value_col))
            if xv is not None and yv is not None:
                xs.append(xv)
                ys.append(yv)
        corr_res = pearson_and_spearman(xs, ys)
        plot_info = _render_scatter_or_bar_svg(
            title_text=f"Association between {value_col} and {x_col} (n={len(xs)})",
            xlabel=x_col,
            ylabel=f"{value_col} ({unit})" if unit else value_col,
            points=list(zip(xs, ys)),
            out_dir=out_dir,
            stem=stem,
        )
        report.update({"correlation_inference": corr_res, "plot_audit": plot_info})
        md_lines.extend([
            f"**Bivariate association** between `{x_col}` and `{value_col}` (n={len(xs)} complete pairs).",
            f"- Pearson r = {corr_res.get('pearson', {}).get('r')} (R^2 = {corr_res.get('pearson', {}).get('r_squared')}, 95% Fisher-z CI = {corr_res.get('pearson', {}).get('ci95_fisher_z')}, p = {corr_res.get('pearson', {}).get('p_value')}).",
            f"- Spearman rho = {corr_res.get('spearman', {}).get('rho')} (95% CI = {corr_res.get('spearman', {}).get('ci95_bonett_wright')}, p = {corr_res.get('spearman', {}).get('p_value')}).",
        ])

    elif design_norm == "contingency":
        assert group_col and value_col
        r_cats: list[str] = diag["row_categories"]
        c_cats: list[str] = diag["col_categories"]
        r_idx = {k: i for i, k in enumerate(r_cats)}
        c_idx = {k: j for j, k in enumerate(c_cats)}
        counts = [[0] * len(c_cats) for _ in range(len(r_cats))]
        for r in rows:
            gv, vv = r.get(group_col), r.get(value_col)
            if is_missing_raw(gv) or is_missing_raw(vv):
                continue
            counts[r_idx[str(gv).strip()]][c_idx[str(vv).strip()]] += 1
        chi_res = contingency_chi_square(counts)
        chi_res["row_categories"] = r_cats
        chi_res["col_categories"] = c_cats
        chi_res["observed_counts"] = counts
        plot_info = _render_scatter_or_bar_svg(
            title_text=f"Category counts across {group_col} (total n={diag['rows_clean']})",
            xlabel=group_col,
            ylabel="Observation count",
            bars=[(rc, float(sum(counts[i]))) for i, rc in enumerate(r_cats)],
            out_dir=out_dir,
            stem=stem,
        )
        report.update({"contingency_inference": chi_res, "plot_audit": plot_info})
        md_lines.extend([
            f"**Categorical association**: `{group_col}` x `{value_col}` (n={diag['rows_clean']}).",
            f"- Pearson chi-square = {chi_res.get('statistic')} (df={chi_res.get('df')}, p={chi_res.get('p_value')}), Cramer's V = {chi_res.get('cramers_v')} (bias-corrected V = {chi_res.get('cramers_v_bias_corrected')}).",
        ])
        if "fisher_exact_2x2" in chi_res:
            fe = chi_res["fisher_exact_2x2"]
            md_lines.append(f"- Exact 2x2 Fisher test: p = {fe['p_value']}, Odds Ratio = {fe['odds_ratio']} (95% CI {fe['odds_ratio_ci95']}).")

    # Restrained scientific interpretation & uncertainty boundaries
    limits = list(diag.get("warnings", []))
    report["scientific_interpretation"] = {
        "causality_boundary": "Statistical association or group difference does not by itself establish causal mechanism; causal claims require randomized/interventional design evidence.",
        "practical_significance_boundary": "Evaluate standardized effect sizes and 95% confidence intervals against domain minimal important differences rather than relying on p < 0.05 alone.",
        "missing_vs_zero_audit": f"Excluded {diag.get('missing_dropped', 0)} missing/non-numeric entries without zero imputation; exact duplicates removed = {diag.get('exact_duplicates_removed', 0)}.",
        "residual_limitations": limits,
    }
    md_lines.extend([
        "",
        "## Scientific Interpretation & Limitations",
        "",
        "- **Statistical vs. practical magnitude**: Interpret adjusted p-values strictly together with effect sizes and 95% confidence intervals; absence of statistical significance is not proof of equivalence.",
        "- **Causality boundary**: Inferential tests quantify compatibility with the null hypothesis under the observed design and do not establish causality on their own.",
        f"- **Data integrity & provenance**: Source `{input_path.name}` (SHA-256 `{source_meta['sha256'][:16]}...`), backend `{diag['backend']}`, reproducible script `{repro_script}`.",
    ])
    if limits:
        md_lines.append("- **Recorded diagnostics & caveats**:")
        for w in limits:
            md_lines.append(f"  - {w}")

    jp = out_dir / f"{stem}.json"
    mdp = out_dir / f"{stem}.md"
    jp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    mdp.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "backend": diag["backend"],
        "estimability_status": diag["estimability_status"],
        "recommended_route": route,
        "json": jp.name,
        "md": mdp.name,
        "reproduce_script": repro_script,
        "charts": report["plot_audit"]["charts"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dual-Backend statistical inference, effect size, CI, and interpretation runner.")
    parser.add_argument("--input", required=True, help="CSV, JSON, or SQLite file path.")
    parser.add_argument("--table", default=None, help="Table name for SQLite inputs.")
    parser.add_argument(
        "--design",
        default="independent",
        choices=["independent", "paired", "correlation", "contingency", "ols_regression"],
        help="Statistical study design.",
    )
    parser.add_argument("--group-col", default=None)
    parser.add_argument("--value-col", default=None)
    parser.add_argument("--x-col", default=None)
    parser.add_argument("--y-col", default=None, help="Continuous outcome column for ols_regression design.")
    parser.add_argument("--x-cols", default="", help="Comma-separated predictor columns for ols_regression design.")
    parser.add_argument("--stratify-col", default=None, help="Optional stratification column (e.g., age_days) for ols_regression.")
    parser.add_argument("--subject-col", default=None)
    parser.add_argument("--unit", default="", help="Measurement unit from data dictionary (e.g. mg/L, cm, %%).")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stem", default="stats_report")
    parser.add_argument("--allow-encoded-integer", action="store_true")
    args = parser.parse_args(argv)

    if args.design == "ols_regression":
        from ols_regression_engine import run_ols_analysis
        y_target = args.y_col or args.value_col
        if not y_target:
            parser.error("--design ols_regression requires --y-col (or --value-col)")
        x_list = [c.strip() for c in args.x_cols.split(",") if c.strip()]
        rep = run_ols_analysis(
            Path(args.input),
            y_col=y_target,
            x_cols=x_list,
            out_dir=Path(args.out_dir),
            stem=args.stem,
            table=args.table,
            stratify_col=args.stratify_col,
            unit=args.unit,
        )
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0 if rep.get("ok") else 2

    rep = run_statistical_report(
        Path(args.input),
        design=args.design,
        group_col=args.group_col,
        value_col=args.value_col,
        x_col=args.x_col,
        subject_col=args.subject_col,
        unit=args.unit,
        out_dir=Path(args.out_dir),
        stem=args.stem,
        table=args.table,
        allow_encoded_integer=args.allow_encoded_integer,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
