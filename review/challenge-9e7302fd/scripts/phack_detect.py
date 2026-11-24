#!/usr/bin/env python3
"""Zero-dependency pure-stdlib p-curve distributional diagnostics (desensitized refactor).

Implements cross-study p-curve shape and threshold probes (Binomial, Fisher, Stouffer,
Elliott-Kudrin-Wuethrich Least Concave Majorant monotonicity, smooth counterfactual
discontinuity, Gerber-Malhotra caliper, and Simonsohn p-curve power estimate) using
pure Python + stat_math_core.py so it runs cleanly in python:3.12-slim --network none.

IMPORTANT EPISTEMIC BOUNDARY:
These diagnostics test the distribution across a collection of independent tests/studies.
They NEVER convict a single manuscript of p-hacking or scientific misconduct and should
only be invoked when n_significant >= 5 as a neutral distributional diagnostic.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import stat_math_core as smc  # noqa: E402

__all__ = [
    "binomial_test",
    "fisher_test",
    "stouffer_test",
    "lcm_test",
    "discontinuity_test",
    "caliper_test",
    "pcurve_power",
    "report",
]


def _finite_floats(vals: Sequence[float]) -> list[float]:
    return [float(x) for x in vals if isinstance(x, (int, float)) and math.isfinite(x)]


def _significant(pvals: Sequence[float], alpha: float) -> list[float]:
    return [p for p in _finite_floats(pvals) if 0.0 < p < alpha]


def _pp(ps: list[float], alpha: float) -> list[float]:
    return [min(max(p / alpha, 1e-12), 1.0 - 1e-12) for p in ps]


def _solve_linear(A: list[list[float]], b: list[float]) -> list[float] | None:
    """Small dense linear solver (Gaussian elimination with partial pivoting)."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        div = M[col][col]
        for j in range(col, n + 1):
            M[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            factor = M[r][col]
            for j in range(col, n + 1):
                M[r][j] -= factor * M[col][j]
    return [M[i][n] for i in range(n)]


def binomial_test(pvals: Sequence[float], alpha: float = 0.05) -> dict[str, Any]:
    ps = _significant(pvals, alpha)
    n = len(ps)
    if n < 5:
        return {"test": "binomial", "n": n, "note": "too few significant results (<5)"}
    k = sum(1 for p in ps if p < alpha / 2.0)
    p_left = smc.binom_cdf(k, n, 0.5)
    return {
        "test": "binomial",
        "n": n,
        "n_below_half_alpha": k,
        "share_below_half_alpha": round(k / n, 4),
        "p_value": round(p_left, 6),
        "reads": "small p_value => unusually low share below alpha/2 (left-skewed p-curve)",
    }


def fisher_test(pvals: Sequence[float], alpha: float = 0.05) -> dict[str, Any]:
    ps = _significant(pvals, alpha)
    n = len(ps)
    if n < 5:
        return {"test": "fisher", "n": n, "note": "too few significant results (<5)"}
    pp = _pp(ps, alpha)
    chi = -2.0 * sum(math.log(u) for u in pp)
    right, _ = smc.chi2_upper_p(chi, 2 * n)
    left = smc.chi2_cdf(chi, 2 * n)
    return {
        "test": "fisher",
        "n": n,
        "chi2": round(chi, 4),
        "p_right_skew": round(right, 6),
        "p_left_skew": round(left, 6),
        "reads": "small p_right_skew => evidential right-skew; small p_left_skew => left-skewed distribution",
    }


def stouffer_test(pvals: Sequence[float], alpha: float = 0.05) -> dict[str, Any]:
    ps = _significant(pvals, alpha)
    n = len(ps)
    if n < 5:
        return {"test": "stouffer", "n": n, "note": "too few significant results (<5)"}
    zs = [smc.norm_ppf(u) for u in _pp(ps, alpha)]
    Z = sum(zs) / math.sqrt(n)
    return {
        "test": "stouffer",
        "n": n,
        "Z": round(Z, 4),
        "p_right_skew": round(smc.norm_cdf(Z), 6),
        "p_left_skew": round(smc.norm_sf(Z), 6),
    }


def _lcm_max_gap(sorted_u: list[float]) -> float:
    m = len(sorted_u)
    ecdf = [(i + 1) / m for i in range(m)]
    pts: list[tuple[float, float]] = [(sorted_u[0], ecdf[0])]
    for i in range(1, m):
        xi, yi = sorted_u[i], ecdf[i]
        while len(pts) >= 2:
            (x0, y0), (x1, y1) = pts[-2], pts[-1]
            if (y1 - y0) * (xi - x0) <= (yi - y0) * (x1 - x0):
                pts.pop()
            else:
                break
        pts.append((xi, yi))

    # Piecewise-linear interpolation of LCM at sorted_u
    max_diff = 0.0
    seg = 0
    n_pts = len(pts)
    for i, x in enumerate(sorted_u):
        while seg + 1 < n_pts and pts[seg + 1][0] < x:
            seg += 1
        x0, y0 = pts[seg]
        if seg + 1 < n_pts and pts[seg + 1][0] > x0:
            x1, y1 = pts[seg + 1]
            y_lcm = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        else:
            y_lcm = y0
        diff = y_lcm - ecdf[i]
        if diff > max_diff:
            max_diff = diff
    return max_diff * math.sqrt(m)


def lcm_test(pvals: Sequence[float], alpha: float = 0.05, B: int = 1000, seed: int = 0) -> dict[str, Any]:
    ps = sorted(_significant(pvals, alpha))
    n = len(ps)
    if n < 15:
        return {"test": "lcm_monotonicity", "n": n, "note": "too few significant results (<15)"}
    u = [p / alpha for p in ps]
    obs = _lcm_max_gap(u)
    rng = random.Random(seed)
    ge_cnt = 0
    for _ in range(B):
        draw = sorted(rng.random() for _ in range(n))
        if _lcm_max_gap(draw) >= obs:
            ge_cnt += 1
    return {
        "test": "lcm_monotonicity",
        "n": n,
        "stat": round(obs, 4),
        "p_value": round((1 + ge_cnt) / (B + 1), 6),
        "reads": "small p_value => empirical CDF departs from concavity (non-decreasing p-density)",
    }


def _counterfactual_counts(
    vals: list[float],
    cutoff: float,
    lo: float,
    hi: float,
    nbins: int,
    donut: float,
    degree: int = 2,
) -> tuple[list[int], list[float], list[float]] | None:
    sub = [x for x in vals if lo <= x <= hi]
    if not sub:
        return None
    step = (hi - lo) / nbins
    counts = [0] * nbins
    for x in sub:
        idx = int((x - lo) / step)
        if idx == nbins:
            idx = nbins - 1
        if 0 <= idx < nbins:
            counts[idx] += 1
    centers = [lo + (i + 0.5) * step for i in range(nbins)]
    outside = [i for i, c in enumerate(centers) if abs(c - cutoff) > donut]
    if len(outside) < 4 or sum(counts[i] for i in outside) < 10:
        return None

    k = degree + 1
    Z = [[(centers[i] - cutoff) ** j for j in range(k)] for i in outside]
    y = [float(counts[i]) for i in outside]
    mean_y = sum(y) / len(y)
    beta = [0.0] * k
    beta[0] = math.log(max(mean_y, 1e-6))

    for _ in range(40):
        mu = [max(math.exp(min(max(sum(Z[r][j] * beta[j] for j in range(k)), -20.0), 20.0)), 1e-8) for r in range(len(outside))]
        H = [[1e-8 if a == b else 0.0 for b in range(k)] for a in range(k)]
        g = [0.0] * k
        for r in range(len(outside)):
            w = mu[r]
            diff = y[r] - mu[r]
            for a in range(k):
                g[a] += Z[r][a] * diff
                for b in range(k):
                    H[a][b] += Z[r][a] * w * Z[r][b]
        delta = _solve_linear(H, g)
        if delta is None:
            return None
        for j in range(k):
            beta[j] += delta[j]
        if max(abs(d) for d in delta) < 1e-8:
            break

    pred = [
        math.exp(min(max(sum(((c - cutoff) ** j) * beta[j] for j in range(k)), -20.0), 20.0))
        for c in centers
    ]
    return counts, pred, centers


def discontinuity_test(
    pvals: Sequence[float],
    cutoff: float = 0.05,
    window: float = 0.04,
    nbins: int = 16,
    donut: float = 0.005,
) -> dict[str, Any]:
    ps = _finite_floats(pvals)
    res = _counterfactual_counts(ps, cutoff, max(cutoff - window, 1e-6), cutoff + window, nbins, donut)
    if res is None:
        return {"test": "discontinuity", "note": "too few results near the cutoff"}
    counts, pred, centers = res
    obs = float(sum(counts[i] for i, c in enumerate(centers) if c < cutoff and abs(c - cutoff) <= donut))
    exp = float(sum(pred[i] for i, c in enumerate(centers) if c < cutoff and abs(c - cutoff) <= donut))
    if exp < 5.0:
        return {"test": "discontinuity", "note": "counterfactual too thin to test"}
    pval = smc.poisson_sf_ge(obs, exp)
    return {
        "test": "discontinuity",
        "n": sum(counts),
        "cutoff": cutoff,
        "observed_just_below": obs,
        "counterfactual_just_below": round(exp, 2),
        "excess_mass_ratio": round(obs / exp, 3),
        "p_value": round(pval, 6),
        "reads": "excess_mass_ratio >> 1 with small p_value => local clustering just below threshold",
    }


def caliper_test(
    zstats: Sequence[float],
    center: float = 1.96,
    caliper: float = 0.20,
    window: float = 1.0,
    nbins: int = 20,
) -> dict[str, Any]:
    zs = [abs(z) for z in _finite_floats(zstats)]
    band = [z for z in zs if center - caliper <= z <= center + caliper]
    n = len(band)
    out: dict[str, Any] = {"test": "caliper", "n": n, "center": center, "caliper": caliper}
    if n < 20:
        out["note"] = "too few statistics in the caliper (<20)"
        return out
    above = sum(1 for z in band if z > center)
    out["naive_share_above"] = round(above / n, 4)
    out["naive_p_value"] = round(smc.binom_sf(above, n, 0.5), 6)
    res = _counterfactual_counts(zs, center, center - window, center + window, nbins, caliper)
    if res is None:
        out["p_value"] = out["naive_p_value"]
        out["note"] = "counterfactual unavailable; naive share reported"
        return out
    counts, pred, centers = res
    obs = float(sum(counts[i] for i, c in enumerate(centers) if c > center and abs(c - center) <= caliper))
    exp = float(sum(pred[i] for i, c in enumerate(centers) if c > center and abs(c - center) <= caliper))
    if exp < 5.0:
        out["p_value"] = out["naive_p_value"]
        out["note"] = "counterfactual too thin; naive share reported"
        return out
    out.update({
        "observed_just_above": obs,
        "counterfactual_just_above": round(exp, 2),
        "excess_mass_ratio": round(obs / exp, 3),
        "p_value": round(smc.poisson_sf_ge(obs, exp), 6),
    })
    return out


def pcurve_power(pvals: Sequence[float], alpha: float = 0.05) -> dict[str, Any]:
    ps = _significant(pvals, alpha)
    n = len(ps)
    if n < 5:
        return {"test": "pcurve_power", "n": n, "note": "too few significant results (<5)"}
    zc = smc.norm_isf(alpha / 2.0)
    z_obs = [smc.norm_isf(p / 2.0) for p in ps]

    best_ncp = 0.0
    best_loss = math.inf
    for step in range(301):
        ncp = step * 0.02
        pp = smc.norm_sf(zc - ncp) + smc.norm_cdf(-zc - ncp)
        if pp <= 1e-12:
            continue
        vals = [
            min(max((smc.norm_sf(zi - ncp) + smc.norm_cdf(-zi - ncp)) / pp, 1e-9), 1.0)
            for zi in z_obs
        ]
        loss = (sum(vals) / n - 0.5) ** 2
        if loss < best_loss:
            best_loss = loss
            best_ncp = ncp
    power = smc.norm_sf(zc - best_ncp) + smc.norm_cdf(-zc - best_ncp)
    return {
        "test": "pcurve_power",
        "n": n,
        "implied_ncp": round(best_ncp, 3),
        "estimated_power": round(power, 4),
        "reads": "estimated average power implied by significant p-curve shape",
    }


def report(
    pvals: Sequence[float] | None = None,
    zstats: Sequence[float] | None = None,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict[str, Any]:
    if pvals is None and zstats is None:
        raise ValueError("supply pvals or zstats")
    if pvals is None:
        p_list = [smc.z_two_sided_p(z)[0] for z in _finite_floats(zstats or [])]
    else:
        p_list = _finite_floats(pvals)
    if zstats is None:
        z_list = [smc.norm_isf(min(max(p / 2.0, 1e-15), 0.5)) for p in p_list if 0.0 < p < 1.0]
    else:
        z_list = _finite_floats(zstats)

    sig = _significant(p_list, alpha)
    if len(sig) < 5:
        return {
            "ran": False,
            "backend": smc.backend_name(),
            "n_total": len(p_list),
            "n_significant": len(sig),
            "tests": [],
            "verdict": "insufficient_significant_tests_for_pcurve",
            "caveat": (
                "Fewer than 5 exact significant p-values extracted; p-curve diagnostics are suppressed to prevent "
                "underpowered false positives. Never infer p-hacking from single-manuscript p-value counts."
            ),
        }

    tests = [
        binomial_test(p_list, alpha),
        fisher_test(p_list, alpha),
        stouffer_test(p_list, alpha),
        lcm_test(p_list, alpha, seed=seed),
        discontinuity_test(p_list),
        caliper_test(z_list),
        pcurve_power(p_list, alpha),
    ]
    shape_names = {"binomial", "fisher", "stouffer", "lcm_monotonicity"}

    def _flagged(t: dict[str, Any]) -> bool:
        return (
            (isinstance(t.get("p_value"), (int, float)) and t["p_value"] < 0.05)
            or (isinstance(t.get("p_left_skew"), (int, float)) and t["p_left_skew"] < 0.05)
        )

    flags = [t["test"] for t in tests if _flagged(t)]
    shape_flags = [f for f in flags if f in shape_names]
    bunch_flags = [f for f in flags if f not in shape_names]

    if len(shape_flags) >= 2:
        verdict = "distributional_left_skew_signal_requires_protocol_review"
    elif shape_flags and bunch_flags:
        verdict = "moderate_distributional_anomaly_requires_protocol_review"
    elif shape_flags or bunch_flags:
        verdict = "weak_distributional_signal_inconclusive"
    else:
        verdict = "no_distributional_anomaly_detected"

    return {
        "ran": True,
        "backend": smc.backend_name(),
        "n_total": len(p_list),
        "n_significant": len(sig),
        "tests": tests,
        "n_tests_flagging": len(flags),
        "flagged_by": flags,
        "shape_tests_flagging": shape_flags,
        "bunching_tests_flagging": bunch_flags,
        "verdict": verdict,
        "caveat": (
            "Distributional p-curve diagnostics evaluate collections of tests across studies and CANNOT establish "
            "intentional p-hacking or misconduct in a single manuscript; treat any signal strictly as a prompt "
            "to inspect pre-registration, multiplicity reporting, and raw analysis plans."
        ),
    }
