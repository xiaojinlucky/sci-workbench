#!/usr/bin/env python3
"""Pure-stdlib statistical modeling, assumption diagnostics, and group hypothesis testing.

Zero external dependencies (runs natively on Python 3.12 stdlib).
Provides:
  - Exact pure-stdlib distribution tail probabilities: Normal, Student-t, F, Chi-square
  - Multivariate OLS via Gauss-Jordan with partial pivoting: beta, classical SE, HC3 robust SE,
    standardized beta*, 95% CI, t/p values, R^2, R^2_adj, overall F, per-predictor VIF
  - Regression residual diagnostics: Jarque-Bera normality, Shapiro-Francia W', Breusch-Pagan (Koenker) LM
  - Per-variable descriptive statistics (n, mean, SD, median, Q1, Q3, IQR, min, max)
  - Grouped hypothesis testing: per-group Shapiro-Francia normality, Brown-Forsythe homoscedasticity,
    Welch's ANOVA, Kruskal-Wallis H, pairwise Welch's t & Mann-Whitney U with Holm-Bonferroni correction,
    Hedges' g, and 95% CI for mean differences
"""

from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# Pure-stdlib special functions & distribution tail probabilities
# ---------------------------------------------------------------------------

def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _norm_ppf(p: float) -> float:
    """Acklam rational approximation to standard normal quantile (accuracy ~1e-9)."""
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for regularized incomplete beta (Lentz's method)."""
    max_iter = 200
    eps = 3.0e-12
    fpmin = 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    bt = math.exp(max(ln_bt, -700.0))
    if x < (a + 1.0) / (a + b + 2.0):
        return min(1.0, max(0.0, bt * _betacf(a, b, x) / a))
    return min(1.0, max(0.0, 1.0 - bt * _betacf(b, a, 1.0 - x) / b))


def gammaincc(a: float, x: float) -> float:
    """Upper regularized incomplete gamma Q(a, x)."""
    if x <= 0.0:
        return 1.0
    if a <= 0.0:
        return 0.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        ap = a
        term = 1.0 / a
        total = term
        for _ in range(300):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-13:
                break
        p_val = total * math.exp(-x + a * math.log(x) - gln)
        return min(1.0, max(0.0, 1.0 - p_val))
    # Lentz continued fraction for Q(a, x)
    fpmin = 1.0e-300
    b = x + 1.0 - a
    c = 1.0 / fpmin
    d = 1.0 / b
    h = d
    for i in range(1, 300):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < fpmin:
            d = fpmin
        c = b + an / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-13:
            break
    return min(1.0, max(0.0, math.exp(-x + a * math.log(x) - gln) * h))


def t_sf_two_sided(t_stat: float, df: float) -> float:
    if not math.isfinite(t_stat) or df <= 0:
        return math.nan
    x = df / (df + t_stat * t_stat)
    return min(1.0, max(0.0, betainc(0.5 * df, 0.5, x)))


def f_sf(f_stat: float, df1: float, df2: float) -> float:
    if not math.isfinite(f_stat) or f_stat < 0 or df1 <= 0 or df2 <= 0:
        return math.nan
    if f_stat == 0.0:
        return 1.0
    x = df2 / (df2 + df1 * f_stat)
    return min(1.0, max(0.0, betainc(0.5 * df2, 0.5 * df1, x)))


def chi2_sf(chi2_stat: float, df: float) -> float:
    if not math.isfinite(chi2_stat) or chi2_stat < 0 or df <= 0:
        return math.nan
    return gammaincc(0.5 * df, 0.5 * chi2_stat)


def t_crit_95(df: float) -> float:
    if df <= 0:
        return 0.0
    table = {
        1: 12.7062, 2: 4.3027, 3: 3.1824, 4: 2.7764, 5: 2.5706,
        6: 2.4469, 7: 2.3646, 8: 2.3060, 9: 2.2622, 10: 2.2281,
        11: 2.2010, 12: 2.1788, 13: 2.1604, 14: 2.1448, 15: 2.1314,
        16: 2.1199, 17: 2.1098, 18: 2.1009, 19: 2.0930, 20: 2.0860,
        21: 2.0796, 22: 2.0739, 23: 2.0687, 24: 2.0639, 25: 2.0595,
        26: 2.0555, 27: 2.0518, 28: 2.0484, 29: 2.0452, 30: 2.0423,
    }
    idf = int(round(df))
    if idf in table and abs(df - idf) < 1e-6:
        return table[idf]
    if df < 30:
        # Bisection on t_sf_two_sided for fractional Welch df
        lo, hi = 1.95, 13.0 if df < 2 else 4.5
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if t_sf_two_sided(mid, df) > 0.05:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)
    z = 1.959963984540054
    return z + (z**3 + z) / (4.0 * df) + (5.0 * z**5 + 16.0 * z**3 + 3.0 * z) / (96.0 * df * df)


def sig_stars(p: float | None) -> str:
    if p is None or not math.isfinite(p):
        return "n/a"
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


def fmt_p(p: float | None) -> str:
    if p is None or not math.isfinite(p):
        return "p=n/a"
    if p < 1e-4:
        return "p<0.0001"
    if p < 0.001:
        return "p<0.001"
    return f"p={p:.3g}"


def _round_p(p: float | None) -> float | None:
    if p is None or not math.isfinite(p):
        return None
    if p == 0.0:
        return 0.0
    if p < 1e-4:
        return float(f"{p:.4e}")
    return round(p, 6)


# ---------------------------------------------------------------------------
# Descriptive statistics & matrix algebra (pure stdlib)
# ---------------------------------------------------------------------------

def _quantile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return math.nan
    if n == 1:
        return s[0]
    pos = q * (n - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return s[lo] if lo == hi else s[lo] * (1.0 - (pos - lo)) + s[hi] * (pos - lo)


def describe_vector(vals: list[float], *, unit: str = "") -> dict[str, Any]:
    n = len(vals)
    if n == 0:
        return {"n": 0, "unit": unit or "unresolved"}
    m = sum(vals) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in vals) / (n - 1)) if n >= 2 else 0.0
    q1, med, q3 = _quantile(vals, 0.25), _quantile(vals, 0.50), _quantile(vals, 0.75)
    return {
        "n": n,
        "mean": round(m, 6),
        "sd": round(sd, 6),
        "median": round(med, 6),
        "q1": round(q1, 6),
        "q3": round(q3, 6),
        "iqr": round(q3 - q1, 6),
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
        "unit": unit or "unresolved",
    }


def _invert_matrix(mat: list[list[float]]) -> list[list[float]] | None:
    """Gauss-Jordan matrix inversion with partial pivoting."""
    n = len(mat)
    aug = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(mat)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-13:
            return None
        if piv != col:
            aug[col], aug[piv] = aug[piv], aug[col]
        scale = aug[col][col]
        aug[col] = [v / scale for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                factor = aug[r][col]
                aug[r] = [aug[r][j] - factor * aug[col][j] for j in range(2 * n)]
    return [row[n:] for row in aug]


def _mat_vec(mat: list[list[float]], vec: list[float]) -> list[float]:
    return [sum(row[j] * vec[j] for j in range(len(vec))) for row in mat]


def _mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    rows, inner, cols = len(a), len(b), len(b[0])
    return [[sum(a[i][k] * b[k][j] for k in range(inner)) for j in range(cols)] for i in range(rows)]


# ---------------------------------------------------------------------------
# Normality tests: Shapiro-Francia W' & Jarque-Bera
# ---------------------------------------------------------------------------

def shapiro_francia_test(vals: list[float]) -> dict[str, Any]:
    """Shapiro-Francia W' normality test (Royston 1993 normal approximation)."""
    n = len(vals)
    if n < 5:
        return {"test": "shapiro_francia", "n": n, "status": "unestimable_small_n", "W_prime": None, "p_value": None, "normal_ok": None}
    xs = sorted(vals)
    mx = sum(xs) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-15:
        return {"test": "shapiro_francia", "n": n, "status": "zero_variance", "W_prime": None, "p_value": None, "normal_ok": False}
    ms = [_norm_ppf((i + 1 - 0.375) / (n + 0.25)) for i in range(n)]
    mm = sum(ms) / n
    smm = sum((m - mm) ** 2 for m in ms)
    sxm = sum((x - mx) * (m - mm) for x, m in zip(xs, ms))
    w_prime = min(1.0, max(0.0, (sxm * sxm) / (sxx * smm)))
    # Royston (1993) transformation for W'
    u = math.log(n)
    v = math.log(u) - u
    mu = -1.2725 + 1.0521 * v
    w_v = math.log(u) + 2.0 / u
    sigma = 1.0308 - 0.26758 * w_v
    y = math.log(max(1.0 - w_prime, 1e-15))
    z = (y - mu) / max(sigma, 1e-9)
    p = min(1.0, max(0.0, _norm_sf(z)))
    return {
        "test": "shapiro_francia",
        "n": n,
        "status": "ok",
        "W_prime": round(w_prime, 6),
        "z_stat": round(z, 4),
        "p_value": _round_p(p),
        "normal_ok": bool(p >= 0.05),
    }


def jarque_bera_test(resid: list[float]) -> dict[str, Any]:
    n = len(resid)
    if n < 4:
        return {"test": "jarque_bera", "n": n, "status": "unestimable_small_n", "jb_stat": None, "p_value": None, "normal_ok": None}
    m = sum(resid) / n
    m2 = sum((e - m) ** 2 for e in resid) / n
    if m2 <= 1e-18:
        return {"test": "jarque_bera", "n": n, "status": "zero_variance", "jb_stat": 0.0, "p_value": 1.0, "normal_ok": True}
    m3 = sum((e - m) ** 3 for e in resid) / n
    m4 = sum((e - m) ** 4 for e in resid) / n
    skew = m3 / (m2 ** 1.5)
    ex_kurt = m4 / (m2 * m2) - 3.0
    jb = (n / 6.0) * (skew * skew + 0.25 * ex_kurt * ex_kurt)
    p = math.exp(-0.5 * min(jb, 700.0))  # exact chi2(df=2) survival function
    return {
        "test": "jarque_bera",
        "n": n,
        "status": "ok",
        "skewness": round(skew, 4),
        "excess_kurtosis": round(ex_kurt, 4),
        "jb_stat": round(jb, 4),
        "p_value": _round_p(p),
        "normal_ok": bool(p >= 0.05),
    }


# ---------------------------------------------------------------------------
# Multivariate OLS + HC3 + VIF + Breusch-Pagan + Jarque-Bera
# ---------------------------------------------------------------------------

def _aux_r2(y_vec: list[float], x_mat: list[list[float]]) -> float:
    """Compute R^2 for y_vec regressed on design matrix x_mat (with intercept)."""
    n = len(y_vec)
    k = len(x_mat[0])
    xtx = [[sum(x_mat[i][a] * x_mat[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    inv = _invert_matrix(xtx)
    if inv is None:
        return math.nan
    xty = [sum(x_mat[i][a] * y_vec[i] for i in range(n)) for a in range(k)]
    beta = _mat_vec(inv, xty)
    ym = sum(y_vec) / n
    sst = sum((v - ym) ** 2 for v in y_vec)
    if sst <= 1e-15:
        return 0.0
    sse = sum((y_vec[i] - sum(x_mat[i][j] * beta[j] for j in range(k))) ** 2 for i in range(n))
    return max(0.0, min(1.0, 1.0 - sse / sst))


def fit_multivariate_ols(
    y: list[float],
    predictors: list[list[float]],
    predictor_names: list[str],
    y_name: str,
    *,
    y_unit: str = "",
    predictor_units: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fit full multivariate OLS with classical & HC3 robust SE, VIF, JB, SF, and Breusch-Pagan LM."""
    n = len(y)
    p = len(predictors)
    k = p + 1
    if n <= k + 1 or p < 1:
        return {"ok": False, "error": f"Insufficient observations (n={n}) for {p} predictors."}

    p_units = predictor_units or {}
    X = [[1.0] + [predictors[j][i] for j in range(p)] for i in range(n)]
    xtx = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    xtx_inv = _invert_matrix(xtx)
    if xtx_inv is None:
        return {"ok": False, "error": "Singular design matrix (perfect collinearity among predictors)."}

    xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    beta = _mat_vec(xtx_inv, xty)
    y_hat = [sum(X[i][j] * beta[j] for j in range(k)) for i in range(n)]
    resid = [y[i] - y_hat[i] for i in range(n)]

    df_resid = n - k
    y_mean = sum(y) / n
    sst = sum((v - y_mean) ** 2 for v in y)
    sse = sum(e * e for e in resid)
    r2 = max(0.0, min(1.0, 1.0 - sse / sst)) if sst > 1e-15 else 0.0
    r2_adj = 1.0 - (1.0 - r2) * (n - 1) / df_resid
    s2 = sse / df_resid
    rmse = math.sqrt(s2)

    f_stat = ((sst - sse) / p) / max(s2, 1e-18)
    f_p = f_sf(f_stat, p, df_resid)

    # HC3 heteroscedasticity-consistent covariance: (X'X)^-1 X' diag(e_i^2 / (1-h_ii)^2) X (X'X)^-1
    meat = [[0.0] * k for _ in range(k)]
    for i in range(n):
        xi = X[i]
        h_ii = sum(xi[a] * xtx_inv[a][b] * xi[b] for a in range(k) for b in range(k))
        denom = max(1.0 - h_ii, 1e-5)
        wi = (resid[i] * resid[i]) / (denom * denom)
        for a in range(k):
            wa = wi * xi[a]
            for b in range(k):
                meat[a][b] += wa * xi[b]
    v_hc3 = _mat_mul(_mat_mul(xtx_inv, meat), xtx_inv)

    t_crit = t_crit_95(df_resid)
    y_sd = math.sqrt(sst / (n - 1)) if n >= 2 else 0.0

    # VIF for each predictor
    vifs: dict[str, float] = {}
    for j, pname in enumerate(predictor_names):
        if p == 1:
            vifs[pname] = 1.0
        else:
            other_x = [[1.0] + [predictors[m][i] for m in range(p) if m != j] for i in range(n)]
            r2_j = _aux_r2(predictors[j], other_x)
            vifs[pname] = round(1.0 / max(1.0 - r2_j, 1e-6), 4) if math.isfinite(r2_j) else 999.0

    terms = ["Intercept"] + list(predictor_names)
    coef_table: list[dict[str, Any]] = []
    for idx, term in enumerate(terms):
        b_val = beta[idx]
        se_ols = math.sqrt(max(s2 * xtx_inv[idx][idx], 0.0))
        se_hc3 = math.sqrt(max(v_hc3[idx][idx], 0.0))
        t_ols = b_val / se_ols if se_ols > 1e-15 else math.nan
        t_hc3 = b_val / se_hc3 if se_hc3 > 1e-15 else math.nan
        p_ols = t_sf_two_sided(t_ols, df_resid) if math.isfinite(t_ols) else math.nan
        p_hc3 = t_sf_two_sided(t_hc3, df_resid) if math.isfinite(t_hc3) else math.nan

        std_beta = None
        std_se_hc3 = None
        std_ci_lo = None
        std_ci_hi = None
        if idx > 0 and y_sd > 1e-15:
            xj = predictors[idx - 1]
            xj_m = sum(xj) / n
            xj_sd = math.sqrt(sum((v - xj_m) ** 2 for v in xj) / (n - 1)) if n >= 2 else 0.0
            scale = xj_sd / y_sd
            std_beta = round(b_val * scale, 6)
            std_se_hc3 = round(se_hc3 * scale, 6)
            std_ci_lo = round((b_val - t_crit * se_hc3) * scale, 6)
            std_ci_hi = round((b_val + t_crit * se_hc3) * scale, 6)

        coef_table.append({
            "term": term,
            "beta": round(b_val, 6),
            "se_ols": round(se_ols, 6),
            "se_hc3": round(se_hc3, 6),
            "t_ols": round(t_ols, 4) if math.isfinite(t_ols) else None,
            "p_ols": _round_p(p_ols),
            "t_hc3": round(t_hc3, 4) if math.isfinite(t_hc3) else None,
            "p_hc3": _round_p(p_hc3),
            "ci95_hc3": [round(b_val - t_crit * se_hc3, 6), round(b_val + t_crit * se_hc3, 6)],
            "std_beta": std_beta,
            "std_se_hc3": std_se_hc3,
            "std_ci95_hc3": [std_ci_lo, std_ci_hi] if std_beta is not None else None,
            "sig_hc3": sig_stars(p_hc3),
            "vif": vifs.get(term),
            "unit": p_units.get(term, "unresolved") if idx > 0 else (y_unit or "unresolved"),
        })

    # Residual diagnostics: Jarque-Bera, Shapiro-Francia, Breusch-Pagan (Koenker LM)
    jb = jarque_bera_test(resid)
    sf = shapiro_francia_test(resid)
    e2 = [e * e for e in resid]
    r2_bp = _aux_r2(e2, X)
    lm_bp = n * r2_bp if math.isfinite(r2_bp) else math.nan
    p_bp = chi2_sf(lm_bp, p) if math.isfinite(lm_bp) else math.nan
    bp = {
        "test": "breusch_pagan_koenker",
        "lm_stat": round(lm_bp, 4) if math.isfinite(lm_bp) else None,
        "df": p,
        "p_value": _round_p(p_bp),
        "homoscedastic_ok": bool(p_bp >= 0.05) if math.isfinite(p_bp) else None,
    }
    max_vif = max(vifs.values(), default=1.0)

    violations: list[str] = []
    if jb.get("normal_ok") is False:
        violations.append(f"Residual non-normality detected (Jarque-Bera={jb['jb_stat']}, {fmt_p(jb['p_value'])}); large-sample CLT + HC3 inference applied.")
    if bp.get("homoscedastic_ok") is False:
        violations.append(f"Heteroscedasticity detected (Breusch-Pagan LM={bp['lm_stat']}, {fmt_p(bp['p_value'])}); HC3 heteroscedasticity-consistent SEs reported as primary.")
    if max_vif > 5.0:
        violations.append(f"Elevated multicollinearity detected (max VIF={max_vif:.2f} > 5.0).")

    desc: dict[str, Any] = {y_name: describe_vector(y, unit=y_unit)}
    for j, pname in enumerate(predictor_names):
        desc[pname] = describe_vector(predictors[j], unit=p_units.get(pname, ""))

    return {
        "ok": True,
        "n": n,
        "n_predictors": p,
        "df_resid": df_resid,
        "y_col": y_name,
        "x_cols": list(predictor_names),
        "r_squared": round(r2, 6),
        "r_squared_adj": round(r2_adj, 6),
        "rmse": round(rmse, 6),
        "f_stat": round(f_stat, 4),
        "f_p_value": _round_p(f_p),
        "coefficients": coef_table,
        "vif": vifs,
        "max_vif": round(max_vif, 4),
        "diagnostics": {
            "jarque_bera": jb,
            "shapiro_francia_resid": sf,
            "breusch_pagan": bp,
            "primary_se_type": "HC3" if bp.get("homoscedastic_ok") is False else "OLS_and_HC3",
            "assumption_violations": violations,
        },
        "descriptive_stats": desc,
        "y_observed": y,
        "y_predicted": y_hat,
    }


# ---------------------------------------------------------------------------
# Grouped comparison tests: Shapiro-Francia + Welch ANOVA + Kruskal-Wallis + Pairwise
# ---------------------------------------------------------------------------

def _rank_average(vals: list[float]) -> tuple[list[float], float]:
    """Return 1-based average ranks and tie correction sum(t^3 - t)."""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    tie_sum = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg_r = 0.5 * (i + j) + 1.0
        t = j - i + 1
        if t > 1:
            tie_sum += t * (t * t - 1)
        for m in range(i, j + 1):
            ranks[order[m]] = avg_r
        i = j + 1
    return ranks, tie_sum


def _welch_t_pair(a: list[float], b: list[float]) -> dict[str, Any]:
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"status": "unestimable_small_n", "t_stat": None, "df": None, "p_value": None, "mean_diff": None, "ci95": None, "hedges_g": None}
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se2 = va / na + vb / nb
    diff = ma - mb
    if se2 <= 1e-18:
        return {"status": "zero_variance", "t_stat": 0.0 if abs(diff) < 1e-12 else math.inf, "df": float(na + nb - 2), "p_value": 1.0 if abs(diff) < 1e-12 else 0.0, "mean_diff": round(diff, 6), "ci95": [round(diff, 6), round(diff, 6)], "hedges_g": 0.0}
    se = math.sqrt(se2)
    t_stat = diff / se
    df = (se2 * se2) / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    p = t_sf_two_sided(t_stat, df)
    tc = t_crit_95(df)
    sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / max(na + nb - 2, 1))
    j_corr = 1.0 - 3.0 / max(4 * (na + nb) - 9, 1)
    g = (diff / sp) * j_corr if sp > 1e-15 else 0.0
    return {
        "status": "ok",
        "t_stat": round(t_stat, 4),
        "df": round(df, 2),
        "p_value": _round_p(p),
        "mean_diff": round(diff, 6),
        "ci95": [round(diff - tc * se, 6), round(diff + tc * se, 6)],
        "hedges_g": round(g, 4),
    }


def _mann_whitney_pair(a: list[float], b: list[float]) -> dict[str, Any]:
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"status": "unestimable_small_n", "u_stat": None, "p_value": None, "rank_biserial_r": None}
    combined = a + b
    ranks, tie_sum = _rank_average(combined)
    ra = sum(ranks[:na])
    u1 = ra - na * (na + 1) / 2.0
    u2 = na * nb - u1
    u = min(u1, u2)
    n = na + nb
    mu = na * nb / 2.0
    var_u = (na * nb / 12.0) * ((n + 1.0) - tie_sum / max(n * (n - 1), 1))
    if var_u <= 1e-18:
        return {"status": "zero_variance", "u_stat": round(u, 2), "p_value": 1.0, "rank_biserial_r": 0.0}
    z = (abs(u1 - mu) - 0.5) / math.sqrt(var_u)
    p = min(1.0, max(0.0, 2.0 * _norm_sf(max(z, 0.0))))
    rb_r = (2.0 * u1) / (na * nb) - 1.0
    return {"status": "ok", "u_stat": round(u1, 2), "z_stat": round(z, 4), "p_value": _round_p(p), "rank_biserial_r": round(rb_r, 4)}


def run_group_comparison_tests(
    groups: list[str],
    arrays: list[list[float]],
    comparison_pairs: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Run Shapiro-Francia per group, Brown-Forsythe, Welch ANOVA, Kruskal-Wallis, and Holm pairwise tests."""
    k = len(groups)
    valid = [(g, a) for g, a in zip(groups, arrays) if len(a) >= 2]
    norm_checks = {g: shapiro_francia_test(a) for g, a in zip(groups, arrays)}
    all_normal = bool(valid) and all(norm_checks[g].get("normal_ok") is True for g, _ in valid)

    # Brown-Forsythe variance homogeneity test (ANOVA on |x - median|)
    bf_res: dict[str, Any] = {"test": "brown_forsythe", "status": "insufficient_groups", "f_stat": None, "p_value": None, "equal_var_ok": None}
    if len(valid) >= 2:
        z_groups = []
        for _, a in valid:
            med = _quantile(a, 0.5)
            z_groups.append([abs(x - med) for x in a])
        n_tot = sum(len(z) for z in z_groups)
        if n_tot > len(valid):
            grand_m = sum(sum(z) for z in z_groups) / n_tot
            ssb = sum(len(z) * (sum(z) / len(z) - grand_m) ** 2 for z in z_groups)
            ssw = sum(sum((v - sum(z) / len(z)) ** 2 for v in z) for z in z_groups)
            df1, df2 = len(valid) - 1, n_tot - len(valid)
            if ssw <= 1e-18:
                if ssb <= 1e-18:
                    bf_res = {
                        "test": "brown_forsythe",
                        "status": "degenerate_zero_dispersion_all_groups",
                        "f_stat": None,
                        "df1": df1,
                        "df2": df2,
                        "p_value": None,
                        "equal_var_ok": None,
                        "note": "All groups have zero within-group absolute deviation from median; F-ratio undefined (0/0).",
                    }
                else:
                    bf_res = {
                        "test": "brown_forsythe",
                        "status": "degenerate_zero_within_dispersion",
                        "f_stat": None,
                        "df1": df1,
                        "df2": df2,
                        "p_value": 0.0,
                        "equal_var_ok": False,
                        "note": "Zero within-group deviation with non-zero between-group dispersion difference; homoscedasticity rejected.",
                    }
            else:
                f_bf = (ssb / df1) / (ssw / df2)
                p_bf = f_sf(f_bf, df1, df2)
                bf_res = {
                    "test": "brown_forsythe",
                    "status": "ok",
                    "f_stat": round(f_bf, 4),
                    "df1": df1,
                    "df2": df2,
                    "p_value": _round_p(p_bf),
                    "equal_var_ok": bool(p_bf >= 0.05),
                }

    # Welch's ANOVA
    welch_res: dict[str, Any] = {"test": "welch_anova", "status": "insufficient_groups", "f_stat": None, "p_value": None}
    welch_valid = [(g, a) for g, a in valid if len(a) >= 2 and (sum((x - sum(a)/len(a))**2 for x in a) / (len(a) - 1)) > 1e-15]
    if len(welch_valid) >= 2:
        kv = len(welch_valid)
        ws, ms, ns = [], [], []
        for _, a in welch_valid:
            na = len(a)
            ma = sum(a) / na
            va = sum((x - ma) ** 2 for x in a) / (na - 1)
            ws.append(na / va)
            ms.append(ma)
            ns.append(na)
        w_sum = sum(ws)
        m_bar = sum(w * m for w, m in zip(ws, ms)) / w_sum
        num = sum(w * (m - m_bar) ** 2 for w, m in zip(ws, ms)) / (kv - 1)
        lam = sum((1.0 - w / w_sum) ** 2 / (na - 1) for w, na in zip(ws, ns))
        denom = 1.0 + (2.0 * (kv - 2) / (kv * kv - 1)) * lam
        f_w = num / max(denom, 1e-12)
        df1 = kv - 1
        df2 = (kv * kv - 1) / max(3.0 * lam, 1e-12)
        p_w = f_sf(f_w, df1, df2)
        # Eta-squared effect size across valid groups
        all_v = [x for _, a in welch_valid for x in a]
        gm = sum(all_v) / len(all_v)
        sst = sum((x - gm) ** 2 for x in all_v)
        ssb = sum(len(a) * (sum(a) / len(a) - gm) ** 2 for _, a in welch_valid)
        eta2 = ssb / sst if sst > 1e-15 else 0.0
        welch_res = {
            "test": "welch_anova",
            "status": "ok",
            "f_stat": round(f_w, 4),
            "df1": df1,
            "df2": round(df2, 2),
            "p_value": _round_p(p_w),
            "eta_squared": round(eta2, 4),
            "sig": sig_stars(p_w),
        }

    # Kruskal-Wallis H test
    kw_res: dict[str, Any] = {"test": "kruskal_wallis", "status": "insufficient_groups", "h_stat": None, "p_value": None}
    if len(valid) >= 2:
        combined = [x for _, a in valid for x in a]
        n_tot = len(combined)
        ranks, tie_sum = _rank_average(combined)
        pos = 0
        h_sum = 0.0
        for _, a in valid:
            r_g = sum(ranks[pos : pos + len(a)])
            pos += len(a)
            h_sum += (r_g * r_g) / len(a)
        h_raw = (12.0 / (n_tot * (n_tot + 1.0))) * h_sum - 3.0 * (n_tot + 1.0)
        denom = 1.0 - tie_sum / max(n_tot * (n_tot * n_tot - 1.0), 1.0)
        h_stat = max(0.0, h_raw / max(denom, 1e-12))
        df_kw = len(valid) - 1
        p_kw = chi2_sf(h_stat, df_kw)
        eps2 = max(0.0, (h_stat - df_kw) / max(n_tot - len(valid), 1))
        kw_res = {
            "test": "kruskal_wallis",
            "status": "ok",
            "h_stat": round(h_stat, 4),
            "df": df_kw,
            "p_value": _round_p(p_kw),
            "epsilon_squared": round(eps2, 4),
            "sig": sig_stars(p_kw),
        }

    # Select primary route transparently
    primary_omnibus = "welch_anova" if (all_normal and welch_res["status"] == "ok") else "kruskal_wallis"
    primary_pairwise = "welch_t" if primary_omnibus == "welch_anova" else "mann_whitney_u"

    # Pairwise comparisons with Holm-Bonferroni correction (never silently truncate family)
    pairs: list[dict[str, Any]] = []
    if comparison_pairs is not None:
        idx_pairs = [(i, j) for (i, j) in comparison_pairs if 0 <= i < k and 0 <= j < k and i != j]
        family_mode = "explicit_configured_subset"
    else:
        idx_pairs = [(i, j) for i in range(k) for j in range(i + 1, k)]
        family_mode = "all_pairwise"
    for i, j in idx_pairs:
        wt = _welch_t_pair(arrays[i], arrays[j])
        mw = _mann_whitney_pair(arrays[i], arrays[j])
        raw_p = wt["p_value"] if primary_pairwise == "welch_t" else mw["p_value"]
        pairs.append({
            "group_a": groups[i],
            "group_b": groups[j],
            "index_a": i,
            "index_b": j,
            "primary_test": primary_pairwise,
            "p_raw": raw_p,
            "welch_t": wt,
            "mann_whitney_u": mw,
        })

    # Holm-Bonferroni step-down adjustment
    valid_idx = [idx for idx, pr in enumerate(pairs) if pr["p_raw"] is not None]
    valid_idx.sort(key=lambda idx: pairs[idx]["p_raw"])
    m_tests = len(valid_idx)
    running_max = 0.0
    for rank_i, idx in enumerate(valid_idx):
        adj = min(1.0, pairs[idx]["p_raw"] * (m_tests - rank_i))
        running_max = max(running_max, adj)
        pairs[idx]["p_holm"] = _round_p(running_max)
        pairs[idx]["sig"] = sig_stars(running_max)
    for pr in pairs:
        if "p_holm" not in pr:
            pr["p_holm"] = None
            pr["sig"] = "n/a"

    violations: list[str] = []
    non_norm_groups = [g for g, nc in norm_checks.items() if nc.get("normal_ok") is False]
    small_n_groups = [g for g, nc in norm_checks.items() if nc.get("status") == "unestimable_small_n"]
    if non_norm_groups:
        violations.append(f"Shapiro-Francia normality rejected in group(s) {non_norm_groups}; non-parametric Kruskal-Wallis + Mann-Whitney U selected as primary.")
    if small_n_groups:
        violations.append(f"Normality test unestimable for small-n group(s) {small_n_groups} (n < 5); non-parametric route retained.")
    if bf_res.get("equal_var_ok") is False:
        violations.append(f"Brown-Forsythe homoscedasticity rejected (status={bf_res['status']}, F={bf_res['f_stat']}, {fmt_p(bf_res['p_value'])}); Welch/non-parametric inference applied.")
    elif str(bf_res.get("status", "")).startswith("degenerate_"):
        violations.append(f"Brown-Forsythe test degenerate ({bf_res['status']}): {bf_res.get('note', '')}")

    return {
        "primary_omnibus": primary_omnibus,
        "primary_pairwise": primary_pairwise,
        "comparison_family": family_mode,
        "total_pairs_tested": len(idx_pairs),
        "all_pairs_tested_without_truncation": comparison_pairs is None,
        "normality_by_group": norm_checks,
        "homogeneity_of_variance": bf_res,
        "welch_anova": welch_res,
        "kruskal_wallis": kw_res,
        "pairwise_comparisons": pairs,
        "multiple_testing_correction": "holm_bonferroni",
        "assumption_violations": violations,
    }
