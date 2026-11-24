#!/usr/bin/env python3
"""Dual-Backend pre-inference statistical diagnostics.

Runs natively on pure Python 3.12 stdlib (zero external dependencies) and
automatically enhances normality/variance computations when SciPy/NumPy/Pandas
are installed. Audits:
  - read-only CSV / JSON / SQLite table loading with source SHA-256
  - exact row duplicates and optional subject-key duplicates
  - missing vs true-zero separation (never silently fills NaN with 0)
  - encoded integer / ID column blocking (e.g. cultivar_code, species_code)
  - per-group sample size (n < 3), zero-variance degeneracy, and IQR/MAD outliers
  - normality (SciPy Shapiro-Wilk or stdlib Royston Shapiro-Francia)
  - variance homogeneity (Brown-Forsythe median-centered Levene test)
  - design-aware test routing and estimability status
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Any

# Optional scientific backend detection (never crash on python:3.12-slim)
try:
    import numpy as _np  # type: ignore
except Exception:
    _np = None

try:
    import scipy.stats as _sps  # type: ignore
except Exception:
    _sps = None

MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "nd", ".", "-"}
CODE_NAME_RE = re.compile(
    r"(?:^|_)(?:code|id|idx|key|flag|pass|status|label|class|type|batch|group|"
    r"cluster|cultivar|species|region|site|arm|stratum|tier|category|indicator)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pure Python stdlib statistical distribution functions (high-precision)
# ---------------------------------------------------------------------------

def norm_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def norm_ppf(p: float) -> float:
    """Acklam's rational approximation to standard normal quantile (err < 1.15e-9)."""
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
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
    )


def _betacf(a: float, b: float, x: float) -> float:
    max_iter = 200
    eps = 3.0e-12
    fpmin = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
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
    bt = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def f_sf(f_stat: float, df1: float, df2: float) -> float:
    """Upper-tail survival function P(F >= f_stat) for F(df1, df2)."""
    if not math.isfinite(f_stat) or f_stat <= 0.0:
        return 1.0
    if df1 <= 0.0 or df2 <= 0.0:
        return math.nan
    x = df2 / (df2 + df1 * f_stat)
    return max(0.0, min(1.0, betainc(df2 / 2.0, df1 / 2.0, x)))


def t_sf_two_sided(t_stat: float, df: float) -> float:
    """Two-sided p-value for Student/Welch t-statistic with df degrees of freedom."""
    if not math.isfinite(t_stat) or df <= 0.0:
        return math.nan
    if t_stat == 0.0:
        return 1.0
    x = df / (df + t_stat * t_stat)
    return max(0.0, min(1.0, betainc(df / 2.0, 0.5, x)))


def t_ppf(p: float, df: float) -> float:
    """Upper quantile of Student's t distribution via exact bisection on betainc."""
    if df <= 0.0 or not (0.0 < p < 1.0):
        return math.nan
    if p == 0.5:
        return 0.0
    sign = 1.0 if p > 0.5 else -1.0
    target_two_sided = 2.0 * (1.0 - p) if p > 0.5 else 2.0 * p
    lo, hi = 0.0, max(2.0, abs(norm_ppf(max(p, 1.0 - p))) * 2.0 + 4.0)
    while t_sf_two_sided(hi, df) > target_two_sided and hi < 1e7:
        hi *= 2.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if t_sf_two_sided(mid, df) > target_two_sided:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-10:
            break
    return sign * 0.5 * (lo + hi)


def chi2_sf(chi2_stat: float, df: float) -> float:
    """Upper-tail survival function P(Chi2 >= chi2_stat) = Q(df/2, chi2/2)."""
    if not math.isfinite(chi2_stat) or chi2_stat <= 0.0:
        return 1.0
    if df <= 0.0:
        return math.nan
    a = 0.5 * df
    x = 0.5 * chi2_stat
    if x < a + 1.0:
        # Series for P(a, x), return 1 - P
        ap = a
        term = 1.0 / a
        total = term
        for _ in range(300):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-13:
                break
        p_lower = total * math.exp(-x + a * math.log(x) - math.lgamma(a))
        return max(0.0, min(1.0, 1.0 - p_lower))
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
    q_upper = math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
    return max(0.0, min(1.0, q_upper))


def quantile_linear(values: list[float], q: float) -> float:
    """Linear-interpolated percentile matching NumPy method='linear'."""
    s = sorted(values)
    n = len(s)
    if n == 0:
        return math.nan
    if n == 1:
        return s[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    frac = pos - lo
    return s[lo] * (1.0 - frac) + s[hi] * frac


def sample_mean_var(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return math.nan, math.nan
    m = sum(values) / n
    if n < 2:
        return m, 0.0
    v = sum((x - m) ** 2 for x in values) / (n - 1)
    return m, v


def _stdlib_shapiro_francia(values: list[float]) -> tuple[float, float, str]:
    """Royston (1993) Shapiro-Francia normality test in pure Python stdlib."""
    n = len(values)
    if n < 3:
        return math.nan, math.nan, "insufficient_n"
    m, v = sample_mean_var(values)
    if v <= 1e-15:
        return 1.0, 0.0, "degenerate_zero_variance"
    xs = sorted(values)
    ms = [norm_ppf((i + 1 - 0.375) / (n + 0.25)) for i in range(n)]
    num = sum(mi * xi for mi, xi in zip(ms, xs))
    den_m = sum(mi * mi for mi in ms)
    den_x = sum((xi - m) ** 2 for xi in xs)
    w_prime = (num * num) / max(den_m * den_x, 1e-30)
    w_prime = max(0.0, min(1.0, w_prime))
    if n < 5:
        # For n=3,4 exact Shapiro-Francia/Wilk approximation via skewness/order ratio
        if w_prime >= 0.999999:
            return w_prime, 1.0, "stdlib_shapiro_francia_small_n"
        # Conservative monotone mapping for n=3..4 calibrated to Shapiro criticals
        p_approx = max(0.0, min(1.0, (w_prime - 0.75) / 0.25))
        return w_prime, p_approx, "stdlib_shapiro_francia_small_n"
    u = math.log(n)
    v_log = math.log(u) - u
    mu = -1.2725 + 1.0521 * v_log
    sigma = 1.0308 - 0.26758 * (math.log(u) + 2.0 / u)
    y = math.log(max(1.0 - w_prime, 1e-15))
    z = (y - mu) / max(sigma, 1e-9)
    p = max(0.0, min(1.0, 1.0 - norm_cdf(z)))
    return w_prime, p, "stdlib_shapiro_francia_royston"


def normality_test(values: list[float]) -> dict[str, Any]:
    """Run SciPy Shapiro-Wilk if available, else stdlib Royston Shapiro-Francia."""
    n = len(values)
    if n < 3:
        return {"w": None, "p_value": None, "method": "skipped_n_lt_3", "normal_at_05": None}
    _, v = sample_mean_var(values)
    if v <= 1e-15:
        return {"w": None, "p_value": None, "method": "degenerate_zero_variance", "normal_at_05": False}
    if _sps is not None:
        res = _sps.shapiro(values)
        w = float(res.statistic)
        p = float(res.pvalue)
        return {"w": round(w, 6), "p_value": round(p, 6), "method": "scipy_shapiro_wilk", "normal_at_05": p >= 0.05}
    w, p, method = _stdlib_shapiro_francia(values)
    return {
        "w": round(w, 6) if math.isfinite(w) else None,
        "p_value": round(p, 6) if math.isfinite(p) else None,
        "method": method,
        "normal_at_05": (p >= 0.05) if math.isfinite(p) else None,
    }


def brown_forsythe_levene(groups_vals: list[list[float]]) -> dict[str, Any]:
    """Median-centered Levene (Brown-Forsythe) test for homogeneity of variance."""
    valid = [g for g in groups_vals if len(g) >= 2]
    k = len(valid)
    if k < 2:
        return {"statistic": None, "df1": None, "df2": None, "p_value": None, "method": "skipped_lt_2_groups"}
    n_tot = sum(len(g) for g in valid)
    if n_tot <= k:
        return {"statistic": None, "df1": None, "df2": None, "p_value": None, "method": "skipped_zero_df"}
    if _sps is not None:
        res = _sps.levene(*valid, center="median")
        stat = float(res.statistic)
        pv = float(res.pvalue)
        if not math.isfinite(stat) or not math.isfinite(pv):
            return {"statistic": None, "df1": k - 1, "df2": n_tot - k, "p_value": None, "method": "degenerate_constant_deviations"}
        return {
            "statistic": round(stat, 6),
            "df1": k - 1,
            "df2": n_tot - k,
            "p_value": round(pv, 6),
            "method": "scipy_levene_median",
        }
    z_groups = []
    for g in valid:
        med = statistics.median(g)
        z_groups.append([abs(x - med) for x in g])
    all_z = [z for zg in z_groups for z in zg]
    grand_mean = sum(all_z) / len(all_z)
    ss_between = sum(len(zg) * (sum(zg) / len(zg) - grand_mean) ** 2 for zg in z_groups)
    ss_within = sum(sum((z - sum(zg) / len(zg)) ** 2 for z in zg) for zg in z_groups)
    df1 = k - 1
    df2 = n_tot - k
    if ss_within <= 1e-15:
        if ss_between <= 1e-15:
            return {"statistic": 0.0, "df1": df1, "df2": df2, "p_value": 1.0, "method": "stdlib_brown_forsythe"}
        return {"statistic": None, "df1": df1, "df2": df2, "p_value": 0.0, "method": "stdlib_brown_forsythe_zero_within"}
    f_val = (ss_between / df1) / (ss_within / df2)
    pv = f_sf(f_val, df1, df2)
    return {
        "statistic": round(f_val, 6),
        "df1": df1,
        "df2": df2,
        "p_value": round(pv, 6),
        "method": "stdlib_brown_forsythe",
    }


# ---------------------------------------------------------------------------
# Pure stdlib table loading (CSV / JSON / SQLite read-only)
# ---------------------------------------------------------------------------

def is_missing_raw(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and (math.isnan(val) or not math.isfinite(val)):
        return True
    s = str(val).strip()
    return s.lower() in MISSING_TOKENS


def try_parse_float(val: Any) -> float | None:
    if is_missing_raw(val):
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        f = float(val)
        return f if math.isfinite(f) else None
    s = str(val).strip().replace(",", "")
    try:
        f = float(s)
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def load_table_ro(path: Path, table: str | None = None) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    """Read CSV, JSON, or read-only SQLite table using stdlib only."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Input file not found: {p}")
    sha256 = hashlib.sha256(p.read_bytes()).hexdigest()
    ext = p.suffix.lower()
    meta: dict[str, Any] = {"file": p.name, "sha256": sha256, "format": ext.lstrip(".")}

    if ext in {".sqlite", ".sqlite3", ".db"}:
        conn = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            if not tables:
                raise ValueError(f"SQLite database has no user tables: {p.name}")
            chosen = table or tables[0]
            if chosen not in tables:
                raise ValueError(f"Table {chosen!r} not found in {p.name}; available={tables}")
            cur = conn.execute(f'SELECT * FROM "{chosen}"')
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            meta["table"] = chosen
            meta["available_tables"] = tables
            return cols, rows, meta
        finally:
            conn.close()

    if ext == ".json":
        obj = json.loads(p.read_text(encoding="utf-8-sig"))
        if isinstance(obj, dict):
            for key in ("rows", "data", "records"):
                if isinstance(obj.get(key), list):
                    obj = obj[key]
                    break
        if not isinstance(obj, list) or (obj and not isinstance(obj[0], dict)):
            raise ValueError("JSON input must be a list of record objects or contain a 'rows'/'data' list")
        cols = list(dict.fromkeys(k for row in obj for k in row.keys()))
        return cols, obj, meta

    delimiter = "\t" if ext == ".tsv" else ","
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        cols = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    return cols, rows, meta


def detect_encoded_integer_columns(cols: list[str], rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Identify numeric columns that actually encode discrete IDs/categories/flags."""
    flagged: dict[str, dict[str, Any]] = {}
    n_rows = len(rows)
    if n_rows == 0:
        return flagged

    # Precompute string columns to detect 1-to-1 label bijections (e.g. cultivar_code <-> cultivar_name)
    str_cols: dict[str, list[str]] = {}
    for c in cols:
        vals = [str(r.get(c, "")).strip() for r in rows]
        non_empty = [v for v in vals if v.lower() not in MISSING_TOKENS]
        if non_empty and any(try_parse_float(v) is None for v in non_empty):
            str_cols[c] = vals

    for c in cols:
        raw_vals = [r.get(c) for r in rows]
        non_missing_raw = [str(v).strip() for v in raw_vals if not is_missing_raw(v)]
        if not non_missing_raw:
            continue
        parsed = [try_parse_float(v) for v in non_missing_raw]
        if any(v is None for v in parsed):
            continue
        floats = [v for v in parsed if v is not None]
        # Leading-zero numeric string IDs like "0001"
        has_leading_zero_id = any(len(s) >= 2 and s.startswith("0") and s.isdigit() for s in non_missing_raw)
        all_integers = all(abs(f - round(f)) < 1e-12 for f in floats)
        if not all_integers and not has_leading_zero_id:
            continue

        uniq = sorted({int(round(f)) for f in floats})
        n_uniq = len(uniq)
        reasons: list[str] = []
        if has_leading_zero_id:
            reasons.append("leading_zero_identifier_strings")
        if CODE_NAME_RE.search(c):
            reasons.append("column_name_matches_code_or_id_pattern")

        # Check 1-to-1 bijection with any string label column
        bijection_with: list[str] = []
        if 2 <= n_uniq <= min(20, max(2, n_rows // 2)):
            for sc, svals in str_cols.items():
                pairs = set()
                code_to_str: dict[int, set[str]] = {}
                str_to_code: dict[str, set[int]] = {}
                for rv, sv in zip(raw_vals, svals):
                    f = try_parse_float(rv)
                    if f is None or sv.lower() in MISSING_TOKENS:
                        continue
                    iv = int(round(f))
                    pairs.add((iv, sv))
                    code_to_str.setdefault(iv, set()).add(sv)
                    str_to_code.setdefault(sv, set()).add(iv)
                if code_to_str and all(len(v) == 1 for v in code_to_str.values()) and all(
                    len(v) == 1 for v in str_to_code.values()
                ):
                    bijection_with.append(sc)
            if bijection_with:
                reasons.append(f"one_to_one_bijection_with_label_column:{','.join(bijection_with)}")

        # Binary 0/1 flag or small consecutive code set
        is_binary_01 = uniq == [0, 1] and n_rows >= 6
        if is_binary_01 and "column_name_matches_code_or_id_pattern" not in reasons:
            reasons.append("binary_0_1_indicator")

        if reasons:
            flagged[c] = {
                "column": c,
                "unique_count": n_uniq,
                "sample_values": uniq[:10],
                "reasons": reasons,
                "bijection_label_columns": bijection_with,
                "action": "BLOCK_CONTINUOUS_MEAN_AND_ANOVA",
            }
    return flagged


def detect_outliers_iqr_mad(values: list[float]) -> dict[str, Any]:
    """Identify outliers via both Tukey 1.5*IQR and Iglewicz-Hoaglin modified Z-score."""
    n = len(values)
    if n < 4:
        return {"iqr_outliers": [], "mad_outliers": [], "iqr_count": 0, "mad_count": 0}
    q1 = quantile_linear(values, 0.25)
    q3 = quantile_linear(values, 0.75)
    iqr = q3 - q1
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    iqr_out = [round(x, 6) for x in values if (iqr > 0 and (x < lo or x > hi))]

    med = statistics.median(values)
    abs_devs = [abs(x - med) for x in values]
    mad = statistics.median(abs_devs)
    mad_out = []
    if mad > 1e-12:
        for x in values:
            mz = 0.6745 * (x - med) / mad
            if abs(mz) > 3.5:
                mad_out.append(round(x, 6))
    return {
        "q1": round(q1, 6),
        "q3": round(q3, 6),
        "iqr": round(iqr, 6),
        "iqr_fence": [round(lo, 6), round(hi, 6)],
        "iqr_outliers": iqr_out,
        "iqr_count": len(iqr_out),
        "mad_outliers": mad_out,
        "mad_count": len(mad_out),
    }


def check_assumptions(
    path: Path,
    *,
    group_col: str | None = None,
    value_col: str | None = None,
    x_col: str | None = None,
    subject_col: str | None = None,
    design: str = "independent",
    table: str | None = None,
    allow_encoded_integer: bool = False,
) -> dict[str, Any]:
    """Run comprehensive pre-inference data hygiene and statistical assumption diagnostics."""
    cols, raw_rows, source_meta = load_table_ro(path, table=table)
    encoded_int_cols = detect_encoded_integer_columns(cols, raw_rows)

    # Exact row deduplication audit (preserving order)
    seen_tuples: set[tuple[str, ...]] = set()
    dedup_rows: list[dict[str, Any]] = []
    exact_dups = 0
    for r in raw_rows:
        key = tuple(str(r.get(c, "")) for c in cols)
        if key in seen_tuples:
            exact_dups += 1
        else:
            seen_tuples.add(key)
            dedup_rows.append(r)

    warnings: list[str] = []
    blockers: list[str] = []
    if exact_dups > 0:
        warnings.append(f"Detected and removed {exact_dups} exact duplicate row(s) before inference (raw={len(raw_rows)}, deduplicated={len(dedup_rows)}).")

    design_norm = design.strip().lower()
    if design_norm not in {"independent", "paired", "correlation", "contingency"}:
        blockers.append(f"Unsupported design {design!r}; choose from independent, paired, correlation, contingency.")

    # Column existence checks
    req_cols = [c for c in (group_col, value_col, x_col, subject_col) if c]
    missing_cols = [c for c in req_cols if c not in cols]
    if missing_cols:
        blockers.append(f"Required column(s) missing from table: {missing_cols}; available={cols}")

    # Encoded integer column guard on continuous outcome/predictor
    if design_norm in {"independent", "paired", "correlation"}:
        for col_name, role in ((value_col, "value_col"), (x_col, "x_col")):
            if col_name and col_name in encoded_int_cols and not allow_encoded_integer:
                info = encoded_int_cols[col_name]
                blockers.append(
                    f"ENCODED_INTEGER_COLUMN: {role}={col_name!r} appears to be a discrete code/ID column "
                    f"(reasons={info['reasons']}, unique={info['sample_values']}). Do not average or run continuous ANOVA/t-test on coded integers; "
                    f"use a continuous outcome column or --design contingency (or pass --allow-encoded-integer if verified ordinal)."
                )

    result: dict[str, Any] = {
        "ok": len(blockers) == 0,
        "backend": "scipy_enhanced" if (_sps is not None and _np is not None) else "python_stdlib_pure",
        "source": source_meta,
        "design": design_norm,
        "columns": cols,
        "rows_raw": len(raw_rows),
        "exact_duplicates_removed": exact_dups,
        "rows_after_dedup": len(dedup_rows),
        "encoded_integer_columns": encoded_int_cols,
        "blockers": blockers,
        "warnings": warnings,
    }
    if blockers:
        result["recommended_route"] = "BLOCKED_PRECHECK"
        result["estimability_status"] = "BLOCKED"
        return result

    # Design-specific diagnostics
    if design_norm == "contingency":
        if not group_col or not value_col:
            result["ok"] = False
            result["blockers"].append("Contingency design requires both --group-col and --value-col.")
            result["recommended_route"] = "BLOCKED_PRECHECK"
            return result
        missing_count = 0
        row_cats: list[str] = []
        col_cats: list[str] = []
        pairs: list[tuple[str, str]] = []
        for r in dedup_rows:
            gv, vv = r.get(group_col), r.get(value_col)
            if is_missing_raw(gv) or is_missing_raw(vv):
                missing_count += 1
                continue
            gs, vs = str(gv).strip(), str(vv).strip()
            pairs.append((gs, vs))
            if gs not in row_cats:
                row_cats.append(gs)
            if vs not in col_cats:
                col_cats.append(vs)
        counts = [[0] * len(col_cats) for _ in range(len(row_cats))]
        r_idx = {k: i for i, k in enumerate(row_cats)}
        c_idx = {k: j for j, k in enumerate(col_cats)}
        for gs, vs in pairs:
            counts[r_idx[gs]][c_idx[vs]] += 1
        n_tot = len(pairs)
        row_sums = [sum(row) for row in counts]
        col_sums = [sum(counts[i][j] for i in range(len(row_cats))) for j in range(len(col_cats))]
        min_expected = math.inf
        cells_lt_5 = 0
        total_cells = len(row_cats) * len(col_cats)
        for i in range(len(row_cats)):
            for j in range(len(col_cats)):
                exp_ij = (row_sums[i] * col_sums[j]) / max(n_tot, 1)
                min_expected = min(min_expected, exp_ij)
                if exp_ij < 5.0:
                    cells_lt_5 += 1
        if len(row_cats) < 2 or len(col_cats) < 2:
            result["estimability_status"] = "UNESTIMABLE_SINGLE_CATEGORY"
            result["recommended_route"] = "descriptive_only_single_level"
            warnings.append("Contingency table has <2 non-empty row or column categories; association test not estimable.")
        elif len(row_cats) == 2 and len(col_cats) == 2 and min_expected < 5.0:
            result["estimability_status"] = "ESTIMABLE_EXACT_SMALL_EXPECTED"
            result["recommended_route"] = "contingency_fisher_exact_2x2"
            warnings.append(f"Minimum expected cell count is {min_expected:.2f} < 5; routing 2x2 table to exact Fisher test.")
        else:
            result["estimability_status"] = "ESTIMABLE" if cells_lt_5 == 0 else "DEGRADED_SPARSE_CELLS"
            result["recommended_route"] = "contingency_chi_square_cramers_v"
            if cells_lt_5 > 0:
                warnings.append(f"{cells_lt_5}/{total_cells} cells have expected count < 5 (min={min_expected:.2f}); interpret asymptotic chi-square cautiously.")
        result.update({
            "missing_dropped": missing_count,
            "rows_clean": n_tot,
            "row_categories": row_cats,
            "col_categories": col_cats,
            "min_expected_count": round(min_expected, 4) if math.isfinite(min_expected) else None,
            "cells_expected_lt_5": cells_lt_5,
        })
        return result

    if design_norm == "correlation":
        if not x_col or not value_col:
            result["ok"] = False
            result["blockers"].append("Correlation design requires --x-col and --value-col.")
            result["recommended_route"] = "BLOCKED_PRECHECK"
            return result
        xs: list[float] = []
        ys: list[float] = []
        missing_count = 0
        true_zero_x = 0
        true_zero_y = 0
        for r in dedup_rows:
            xv = try_parse_float(r.get(x_col))
            yv = try_parse_float(r.get(value_col))
            if xv is None or yv is None:
                missing_count += 1
                continue
            if xv == 0.0:
                true_zero_x += 1
            if yv == 0.0:
                true_zero_y += 1
            xs.append(xv)
            ys.append(yv)
        n = len(xs)
        nx = normality_test(xs)
        ny = normality_test(ys)
        ox = detect_outliers_iqr_mad(xs)
        oy = detect_outliers_iqr_mad(ys)
        _, vx = sample_mean_var(xs)
        _, vy = sample_mean_var(ys)
        if n < 3:
            est = "UNESTIMABLE_SMALL_N"
            route = "descriptive_only_small_n"
            warnings.append(f"Only n={n} complete (x, y) pairs; correlation inference requires n >= 3.")
        elif vx <= 1e-15 or vy <= 1e-15:
            est = "DEGRADED_ZERO_VARIANCE"
            route = "descriptive_only_zero_variance"
            warnings.append("Zero variance in x or y; correlation is undefined.")
        else:
            est = "ESTIMABLE"
            both_norm = bool(nx.get("normal_at_05")) and bool(ny.get("normal_at_05"))
            heavy_outliers = (ox["mad_count"] + oy["mad_count"]) > 0
            if both_norm and not heavy_outliers:
                route = "correlation_pearson_fisher_z"
            else:
                route = "correlation_spearman_rank"
                if not both_norm:
                    warnings.append("Non-normality detected in at least one variable; routing primary correlation to Spearman rho (with Pearson sensitivity).")
                if heavy_outliers:
                    warnings.append("Extreme MAD outliers detected; Spearman rank correlation preferred over Pearson r.")
        result.update({
            "x_col": x_col,
            "value_col": value_col,
            "missing_dropped": missing_count,
            "rows_clean": n,
            "true_zero_counts": {x_col: true_zero_x, value_col: true_zero_y},
            "normality": {x_col: nx, value_col: ny},
            "outliers": {x_col: ox, value_col: oy},
            "estimability_status": est,
            "recommended_route": route,
        })
        return result

    if design_norm == "paired":
        if not group_col or not value_col or not subject_col:
            result["ok"] = False
            result["blockers"].append("Paired design requires --group-col, --value-col, and --subject-col.")
            result["recommended_route"] = "BLOCKED_PRECHECK"
            return result
        missing_count = 0
        true_zero_count = 0
        groups_order: list[str] = []
        subj_map: dict[str, dict[str, list[float]]] = {}
        for r in dedup_rows:
            gv, vv, sv = r.get(group_col), r.get(value_col), r.get(subject_col)
            fv = try_parse_float(vv)
            if is_missing_raw(gv) or is_missing_raw(sv) or fv is None:
                missing_count += 1
                continue
            if fv == 0.0:
                true_zero_count += 1
            gs, ss = str(gv).strip(), str(sv).strip()
            if gs not in groups_order:
                groups_order.append(gs)
            subj_map.setdefault(ss, {}).setdefault(gs, []).append(fv)

        # Find pairable groups (first two groups with overlapping subjects if >2 groups present)
        pair_groups: list[str] = []
        if len(groups_order) == 2:
            pair_groups = groups_order
        else:
            best_pair: tuple[str, str] | None = None
            best_overlap = 0
            for i in range(len(groups_order)):
                for j in range(i + 1, len(groups_order)):
                    g1, g2 = groups_order[i], groups_order[j]
                    ov = sum(1 for sm in subj_map.values() if g1 in sm and g2 in sm)
                    if ov > best_overlap:
                        best_overlap = ov
                        best_pair = (g1, g2)
            if best_pair:
                pair_groups = list(best_pair)
                if len(groups_order) > 2:
                    warnings.append(
                        f"Table has {len(groups_order)} groups ({groups_order}); paired diagnostics focus on overlapping pair {pair_groups} (n_pairs={best_overlap})."
                    )
        diffs: list[float] = []
        complete_subjects: list[str] = []
        unpaired_subjects: list[str] = []
        multi_obs_warnings = 0
        if len(pair_groups) == 2:
            g1, g2 = pair_groups
            for ss, sm in subj_map.items():
                if g1 in sm and g2 in sm:
                    if len(sm[g1]) > 1 or len(sm[g2]) > 1:
                        multi_obs_warnings += 1
                    v1 = sum(sm[g1]) / len(sm[g1])
                    v2 = sum(sm[g2]) / len(sm[g2])
                    diffs.append(v2 - v1)
                    complete_subjects.append(ss)
                elif g1 in sm or g2 in sm:
                    unpaired_subjects.append(ss)
        if unpaired_subjects:
            warnings.append(f"{len(unpaired_subjects)} subject(s) have incomplete pairs and were excluded from paired differences without imputation: {unpaired_subjects[:10]}")
        if multi_obs_warnings > 0:
            warnings.append(f"{multi_obs_warnings} subject(s) had multiple technical observations per condition; averaged within subject to preserve inference unit.")

        nd = len(diffs)
        diff_norm = normality_test(diffs)
        diff_out = detect_outliers_iqr_mad(diffs)
        _, vdiff = sample_mean_var(diffs)
        if nd < 3:
            est = "UNESTIMABLE_SMALL_N"
            route = "descriptive_only_small_n"
            warnings.append(f"Only n={nd} complete pairs; paired inferential testing requires n >= 3.")
        elif vdiff <= 1e-15:
            est = "DEGRADED_ZERO_VARIANCE"
            route = "paired_degenerate_zero_diff_variance"
            warnings.append("Paired differences have zero variance; parametric t-statistic is degenerate.")
        elif diff_norm.get("normal_at_05") and diff_out["mad_count"] == 0:
            est = "ESTIMABLE"
            route = "paired_t_test"
        else:
            est = "ESTIMABLE"
            route = "paired_wilcoxon_signed_rank"
            warnings.append("Paired differences deviate from normality or show extreme outliers; routing to Wilcoxon signed-rank + Hodges-Lehmann pseudomedian.")
        result.update({
            "group_col": group_col,
            "value_col": value_col,
            "subject_col": subject_col,
            "paired_groups": pair_groups,
            "n_complete_pairs": nd,
            "n_unpaired_excluded": len(unpaired_subjects),
            "missing_dropped": missing_count,
            "true_zero_count": true_zero_count,
            "diff_normality": diff_norm,
            "diff_outliers": diff_out,
            "estimability_status": est,
            "recommended_route": route,
        })
        return result

    # Independent group comparison diagnostics
    if not group_col or not value_col:
        result["ok"] = False
        result["blockers"].append("Independent design requires --group-col and --value-col.")
        result["recommended_route"] = "BLOCKED_PRECHECK"
        return result

    groups_order = []
    grouped: dict[str, list[float]] = {}
    missing_by_group: dict[str, int] = {}
    missing_total = 0
    true_zero_total = 0
    for r in dedup_rows:
        gv = r.get(group_col)
        vv = r.get(value_col)
        if is_missing_raw(gv):
            missing_total += 1
            continue
        gs = str(gv).strip()
        if gs not in groups_order:
            groups_order.append(gs)
            grouped[gs] = []
            missing_by_group[gs] = 0
        fv = try_parse_float(vv)
        if fv is None:
            missing_total += 1
            missing_by_group[gs] += 1
            continue
        if fv == 0.0:
            true_zero_total += 1
        grouped[gs].append(fv)

    if missing_total > 0:
        warnings.append(
            f"Excluded {missing_total} missing/non-numeric value(s) without zero-imputation "
            f"(by group: { {k: v for k, v in missing_by_group.items() if v > 0} }; true zeros retained={true_zero_total})."
        )

    group_diag: dict[str, dict[str, Any]] = {}
    small_n_groups: list[str] = []
    zero_var_groups: list[str] = []
    empty_groups: list[str] = []
    non_normal_groups: list[str] = []
    outlier_groups: list[str] = []

    for g in groups_order:
        vals = grouped[g]
        n = len(vals)
        m, v = sample_mean_var(vals)
        sd = math.sqrt(v) if (n >= 2 and math.isfinite(v)) else 0.0
        norm_rep = normality_test(vals)
        out_rep = detect_outliers_iqr_mad(vals)
        status = "OK"
        if n == 0:
            status = "UNESTIMABLE_EMPTY_CELL"
            empty_groups.append(g)
            warnings.append(f"Group {g!r} has n=0 valid observations (missing cell).")
        elif n == 1:
            status = "UNESTIMABLE_SINGLETON"
            small_n_groups.append(g)
            warnings.append(f"Group {g!r} has n=1; variance and inferential tests are unestimable.")
        elif n < 3:
            status = "DEGRADED_SMALL_N"
            small_n_groups.append(g)
            warnings.append(f"Group {g!r} has n={n} < 3; normality test is underpowered and parametric inference is unreliable.")
        if n >= 2 and sd <= 1e-12:
            status = "DEGRADED_ZERO_VARIANCE" if status == "OK" else f"{status}_AND_ZERO_VARIANCE"
            zero_var_groups.append(g)
            warnings.append(f"Group {g!r} has zero within-group variance (constant value={vals[0]}); Welch/Student variance denominators degenerate.")
        if norm_rep.get("normal_at_05") is False and n >= 3:
            non_normal_groups.append(g)
            warnings.append(f"Group {g!r} deviates from normality ({norm_rep['method']} p={norm_rep['p_value']}).")
        if out_rep["mad_count"] > 0:
            outlier_groups.append(g)
            warnings.append(f"Group {g!r} contains {out_rep['mad_count']} extreme MAD outlier(s): {out_rep['mad_outliers']}.")

        group_diag[g] = {
            "n": n,
            "missing": missing_by_group[g],
            "mean": round(m, 6) if math.isfinite(m) else None,
            "sd": round(sd, 6) if n >= 2 else None,
            "median": round(statistics.median(vals), 6) if n > 0 else None,
            "normality": norm_rep,
            "outliers": out_rep,
            "group_status": status,
        }

    # Check if paired subject column also exists in table while running independent mode
    if subject_col and subject_col in cols:
        subj_groups: dict[str, set[str]] = {}
        for r in dedup_rows:
            sv, gv = r.get(subject_col), r.get(group_col)
            if not is_missing_raw(sv) and not is_missing_raw(gv):
                subj_groups.setdefault(str(sv).strip(), set()).add(str(gv).strip())
        repeated_subjs = [s for s, gs in subj_groups.items() if len(gs) > 1]
        if repeated_subjs:
            warnings.append(
                f"Design alert: {len(repeated_subjs)} subject(s) in {subject_col!r} appear across multiple groups ({repeated_subjs[:5]}). "
                "Verify whether --design paired is more appropriate to avoid pseudoreplication."
            )

    estimable_groups = [g for g in groups_order if len(grouped[g]) >= 2]
    levene_rep = brown_forsythe_levene([grouped[g] for g in estimable_groups])
    equal_var = levene_rep["p_value"] is not None and levene_rep["p_value"] >= 0.05
    if levene_rep["p_value"] is not None and levene_rep["p_value"] < 0.05:
        warnings.append(
            f"Heteroscedasticity detected ({levene_rep['method']} F={levene_rep['statistic']}, p={levene_rep['p_value']}); "
            "equal-variance ANOVA/Student-t is invalid."
        )

    # Routing decision tree
    k_total = len(groups_order)
    k_est = len(estimable_groups)
    all_normal = all(group_diag[g]["normality"].get("normal_at_05") is True for g in groups_order)
    any_degraded = bool(small_n_groups or zero_var_groups or empty_groups)

    if k_est < 2:
        est_status = "UNESTIMABLE_INSUFFICIENT_GROUPS"
        route = "descriptive_only_insufficient_groups"
    elif any_degraded:
        est_status = "DEGRADED_SMALL_N_OR_ZERO_VARIANCE"
        # Non-parametric rank tests still run cleanly across groups with >=2 obs, while flagging degraded groups
        route = "nonparametric_kruskal_mannwhitney" if k_est >= 3 else "nonparametric_mannwhitney"
    elif not all_normal or bool(outlier_groups):
        est_status = "ESTIMABLE"
        route = "nonparametric_kruskal_mannwhitney" if k_est >= 3 else "nonparametric_mannwhitney"
    elif equal_var:
        est_status = "ESTIMABLE"
        route = "parametric_anova_student" if k_est >= 3 else "parametric_student_t"
    else:
        est_status = "ESTIMABLE"
        route = "parametric_welch_anova" if k_est >= 3 else "parametric_welch_t"

    result.update({
        "group_col": group_col,
        "value_col": value_col,
        "missing_dropped": missing_total,
        "true_zero_count": true_zero_total,
        "rows_clean": sum(len(v) for v in grouped.values()),
        "groups": groups_order,
        "group_ns": {g: len(grouped[g]) for g in groups_order},
        "group_diagnostics": group_diag,
        "small_n_groups": small_n_groups,
        "zero_variance_groups": zero_var_groups,
        "empty_groups": empty_groups,
        "shapiro_p": {g: group_diag[g]["normality"]["p_value"] for g in groups_order},
        "levene_brown_forsythe": levene_rep,
        "levene_p": levene_rep["p_value"],
        "estimability_status": est_status,
        "recommended_route": route,
    })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dual-Backend statistical assumption diagnostics.")
    parser.add_argument("--input", required=True, help="CSV, JSON, or SQLite file path.")
    parser.add_argument("--table", default=None, help="Table name for SQLite inputs.")
    parser.add_argument("--design", default="independent", choices=["independent", "paired", "correlation", "contingency"])
    parser.add_argument("--group-col", default=None, help="Grouping column (independent/paired/contingency row).")
    parser.add_argument("--value-col", default=None, help="Outcome column (or contingency col).")
    parser.add_argument("--x-col", default=None, help="Predictor/X column for correlation design.")
    parser.add_argument("--subject-col", default=None, help="Subject/block ID column for paired design or pseudoreplication check.")
    parser.add_argument("--allow-encoded-integer", action="store_true", help="Explicitly override encoded-integer column block.")
    parser.add_argument("--out", default=None, help="Optional JSON output path.")
    args = parser.parse_args(argv)

    rep = check_assumptions(
        Path(args.input),
        group_col=args.group_col,
        value_col=args.value_col,
        x_col=args.x_col,
        subject_col=args.subject_col,
        design=args.design,
        table=args.table,
        allow_encoded_integer=args.allow_encoded_integer,
    )
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0 if rep["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
