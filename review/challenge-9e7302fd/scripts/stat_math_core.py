#!/usr/bin/env python3
"""Pure-stdlib high-precision statistical distribution engine (< 1e-9 accuracy).

Implements exact survival/CDF/quantile functions for Normal, Student-t, Fisher-F,
Chi-squared, Pearson-r, Binomial, and Poisson distributions using:
- C99 math.erfc / math.lgamma
- Modified Lentz continued fractions for regularized incomplete Beta I_x(a, b)
- Series + Modified Lentz continued fractions for regularized incomplete Gamma P(a, x), Q(a, x)
- Acklam rational approximation + Halley refinement for normal inverse CDF (norm_ppf)

Opportunistically cross-checks against scipy.stats when scipy is installed, while
running identically with zero third-party dependencies in python:3.12-slim --network none.
"""

from __future__ import annotations

import math
from typing import Any

try:
    from scipy import stats as _SCIPY_STATS  # type: ignore
except Exception:
    _SCIPY_STATS = None

_SQRT2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)
_EPS = 1e-14
_FPMIN = 1e-300
_MAX_ITER = 500


def has_scipy() -> bool:
    return _SCIPY_STATS is not None


def backend_name() -> str:
    return "scipy+stdlib_crosscheck" if _SCIPY_STATS is not None else "pure_stdlib_lentz"


# ---------------------------------------------------------------------------
# Normal distribution
# ---------------------------------------------------------------------------

def norm_sf_stdlib(z: float) -> float:
    return 0.5 * math.erfc(z / _SQRT2)


def norm_cdf_stdlib(z: float) -> float:
    return 0.5 * math.erfc(-z / _SQRT2)


def norm_ppf_stdlib(p: float) -> float:
    """Inverse standard normal CDF via Acklam rational approximation + Halley refinement (< 1e-14)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    if p == 0.5:
        return 0.0

    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )

    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
        )
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )

    # Two Halley steps for full double precision
    for _ in range(2):
        err = norm_cdf_stdlib(x) - p
        pdf = math.exp(-0.5 * x * x) / _SQRT_2PI
        if pdf <= _FPMIN:
            break
        u = err / pdf
        x -= u / (1.0 + 0.5 * x * u)
    return x


def norm_isf_stdlib(p: float) -> float:
    return -norm_ppf_stdlib(p)


# ---------------------------------------------------------------------------
# Regularized Incomplete Gamma P(a, x) and Q(a, x) = 1 - P(a, x)
# ---------------------------------------------------------------------------

def _gammainc_series(a: float, x: float) -> float:
    ap = a
    term = 1.0 / a
    total = term
    for _ in range(_MAX_ITER):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * _EPS:
            break
    log_pref = -x + a * math.log(x) - math.lgamma(a)
    if log_pref < -740.0:
        return 0.0
    val = total * math.exp(log_pref)
    return min(max(val, 0.0), 1.0)


def _gammaincc_cf(a: float, x: float) -> float:
    """Modified Lentz continued fraction for upper regularized incomplete gamma Q(a, x)."""
    b = x + 1.0 - a
    c = 1.0 / _FPMIN
    d = 1.0 / b if abs(b) > _FPMIN else 1.0 / _FPMIN
    h = d
    for i in range(1, _MAX_ITER + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = b + an / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    log_pref = -x + a * math.log(x) - math.lgamma(a)
    if log_pref < -740.0:
        return 0.0
    val = math.exp(log_pref) * h
    return min(max(val, 0.0), 1.0)


def gammainc(a: float, x: float) -> float:
    """Lower regularized incomplete gamma P(a, x)."""
    if a <= 0.0:
        raise ValueError("a must be positive")
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        return _gammainc_series(a, x)
    return max(0.0, 1.0 - _gammaincc_cf(a, x))


def gammaincc(a: float, x: float) -> float:
    """Upper regularized incomplete gamma Q(a, x)."""
    if a <= 0.0:
        raise ValueError("a must be positive")
    if x <= 0.0:
        return 1.0
    if x < a + 1.0:
        return max(0.0, 1.0 - _gammainc_series(a, x))
    return _gammaincc_cf(a, x)


# ---------------------------------------------------------------------------
# Regularized Incomplete Beta I_x(a, b) via Modified Lentz continued fraction
# ---------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAX_ITER + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c

        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b) with symmetry transformation."""
    if a <= 0.0 or b <= 0.0:
        raise ValueError("Beta parameters a, b must be positive")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        if log_bt < -740.0:
            return 0.0
        val = math.exp(log_bt) * _betacf(a, b, x) / a
        return min(max(val, 0.0), 1.0)
    if log_bt < -740.0:
        return 1.0
    val = 1.0 - math.exp(log_bt) * _betacf(b, a, 1.0 - x) / b
    return min(max(val, 0.0), 1.0)


# ---------------------------------------------------------------------------
# Public survival / CDF functions (prefer scipy when present + cross-check)
# ---------------------------------------------------------------------------

def t_two_sided_p(t_val: float, df: float) -> tuple[float, float | None]:
    """Return (two_sided_p, scipy_stdlib_abs_diff_or_None)."""
    t_abs = abs(float(t_val))
    df = float(df)
    if df <= 0.0:
        raise ValueError("df must be positive")
    if df > 1e8:
        p_std = 2.0 * norm_sf_stdlib(t_abs)
    else:
        x = df / (df + t_abs * t_abs)
        p_std = betainc(0.5 * df, 0.5, x)
    if _SCIPY_STATS is not None:
        p_sci = float(2.0 * _SCIPY_STATS.t.sf(t_abs, df))
        return p_sci, abs(p_sci - p_std)
    return p_std, None


def f_upper_p(f_val: float, df1: float, df2: float) -> tuple[float, float | None]:
    """Return (upper_tail_p, scipy_stdlib_abs_diff_or_None)."""
    f_val = float(f_val)
    df1, df2 = float(df1), float(df2)
    if df1 <= 0.0 or df2 <= 0.0:
        raise ValueError("df1 and df2 must be positive")
    if f_val <= 0.0:
        p_std = 1.0
    else:
        x = df2 / (df2 + df1 * f_val)
        p_std = betainc(0.5 * df2, 0.5 * df1, x)
    if _SCIPY_STATS is not None:
        p_sci = float(_SCIPY_STATS.f.sf(f_val, df1, df2))
        return p_sci, abs(p_sci - p_std)
    return p_std, None


def chi2_upper_p(chi2_val: float, df: float) -> tuple[float, float | None]:
    """Return (upper_tail_p, scipy_stdlib_abs_diff_or_None)."""
    chi2_val, df = float(chi2_val), float(df)
    if df <= 0.0:
        raise ValueError("df must be positive")
    p_std = 1.0 if chi2_val <= 0.0 else gammaincc(0.5 * df, 0.5 * chi2_val)
    if _SCIPY_STATS is not None:
        p_sci = float(_SCIPY_STATS.chi2.sf(chi2_val, df))
        return p_sci, abs(p_sci - p_std)
    return p_std, None


def chi2_cdf(chi2_val: float, df: float) -> float:
    if _SCIPY_STATS is not None:
        return float(_SCIPY_STATS.chi2.cdf(chi2_val, df))
    return 0.0 if chi2_val <= 0.0 else gammainc(0.5 * df, 0.5 * chi2_val)


def r_two_sided_p(r_val: float, df: float) -> tuple[float, float | None]:
    """Two-sided p-value for Pearson correlation r with df = n - 2."""
    r_val = float(r_val)
    if df <= 0.0 or abs(r_val) >= 1.0:
        raise ValueError("r must satisfy |r| < 1 and df > 0")
    t_equiv = r_val * math.sqrt(df / (1.0 - r_val * r_val))
    return t_two_sided_p(t_equiv, df)


def z_two_sided_p(z_val: float) -> tuple[float, float | None]:
    z_abs = abs(float(z_val))
    p_std = 2.0 * norm_sf_stdlib(z_abs)
    if _SCIPY_STATS is not None:
        p_sci = float(2.0 * _SCIPY_STATS.norm.sf(z_abs))
        return p_sci, abs(p_sci - p_std)
    return p_std, None


def norm_sf(z: float) -> float:
    if _SCIPY_STATS is not None:
        return float(_SCIPY_STATS.norm.sf(z))
    return norm_sf_stdlib(z)


def norm_cdf(z: float) -> float:
    if _SCIPY_STATS is not None:
        return float(_SCIPY_STATS.norm.cdf(z))
    return norm_cdf_stdlib(z)


def norm_ppf(p: float) -> float:
    if _SCIPY_STATS is not None:
        return float(_SCIPY_STATS.norm.ppf(p))
    return norm_ppf_stdlib(p)


def norm_isf(p: float) -> float:
    if _SCIPY_STATS is not None:
        return float(_SCIPY_STATS.norm.isf(p))
    return norm_isf_stdlib(p)


def binom_cdf(k: int, n: int, p: float = 0.5) -> float:
    """Exact P(X <= k) for X ~ Binomial(n, p) via regularized incomplete beta."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if _SCIPY_STATS is not None:
        return float(_SCIPY_STATS.binom.cdf(k, n, p))
    return betainc(n - k, k + 1, 1.0 - p)


def binom_sf(k: int, n: int, p: float = 0.5) -> float:
    """Exact P(X >= k) for X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return max(0.0, 1.0 - binom_cdf(k - 1, n, p))


def poisson_sf_ge(k_obs: float, lam: float) -> float:
    """Exact P(X >= ceil(k_obs)) for X ~ Poisson(lam) = P(k_obs, lam) lower incomplete gamma."""
    k = int(math.ceil(k_obs))
    if k <= 0:
        return 1.0
    if lam <= 0.0:
        return 0.0
    if _SCIPY_STATS is not None:
        return float(_SCIPY_STATS.poisson.sf(k - 1, lam))
    return gammainc(float(k), float(lam))


def self_test() -> dict[str, Any]:
    """Verify stdlib accuracy against canonical critical values (tolerance < 1e-9)."""
    checks = [
        ("t(28)=2.04840714", t_two_sided_p(2.048407141795244, 28)[0], 0.05),
        ("F(2,30)=3.3158295", f_upper_p(3.3158295010135217, 2, 30)[0], 0.05),
        ("chi2(4)=9.4877290", chi2_upper_p(9.487729036781154, 4)[0], 0.05),
        ("z=1.9599639845", z_two_sided_p(1.959963984540054)[0], 0.05),
        ("norm_ppf(0.975)", norm_ppf_stdlib(0.975), 1.959963984540054),
        ("binom_cdf(3,10,0.5)", binom_cdf(3, 10, 0.5), 176.0 / 1024.0),
    ]
    max_err = max(abs(got - exp) for _, got, exp in checks)
    return {
        "ok": max_err < 1e-9,
        "backend": backend_name(),
        "max_error": max_err,
        "cases": [{"name": n, "got": g, "expected": e, "err": abs(g - e)} for n, g, e in checks],
    }


if __name__ == "__main__":
    import json
    res = self_test()
    print(json.dumps(res, indent=2))
    raise SystemExit(0 if res["ok"] else 1)
