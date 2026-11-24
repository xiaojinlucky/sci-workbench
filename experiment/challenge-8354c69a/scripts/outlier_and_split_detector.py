#!/usr/bin/env python3
"""Stratified IQR/MAD outlier detector (CSV & XLSX), batch-shift analyzer, paired split comparator, and batch-trace auditor."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import statistics
from pathlib import Path
from typing import Any

from multi_sheet_and_trace_profiler import _detect_header_row_idx, _idx_to_col_letters, _read_xlsx_sheets_indexed

# Exact two-sided Student-t critical values for 95% CI (alpha=0.05, t_{0.975, df})
STUDENT_T_975: dict[int, float] = {
    1: 12.7062, 2: 4.3027, 3: 3.1824, 4: 2.7764, 5: 2.5706,
    6: 2.4469, 7: 2.3646, 8: 2.3060, 9: 2.2622, 10: 2.2281,
    11: 2.2010, 12: 2.1788, 13: 2.1604, 14: 2.1448, 15: 2.1314,
    16: 2.1199, 17: 2.1098, 18: 2.1009, 19: 2.0930, 20: 2.0860,
    25: 2.0595, 30: 2.0423, 40: 2.0211, 60: 2.0003, 120: 1.9799,
}


def _student_t_crit_95(df: int) -> float:
    if df <= 0:
        return float("nan")
    if df in STUDENT_T_975:
        return STUDENT_T_975[df]
    if df > 120:
        return 1.9600
    keys = sorted(STUDENT_T_975.keys())
    for i in range(len(keys) - 1):
        if keys[i] < df < keys[i + 1]:
            lo, hi = keys[i], keys[i + 1]
            w = (df - lo) / (hi - lo)
            return round(STUDENT_T_975[lo] * (1.0 - w) + STUDENT_T_975[hi] * w, 4)
    return 1.9600

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "iqr_multiplier": 1.5,
    "mad_z_threshold": 3.5,
    "mad_z_sensitivity_grid": [2.5, 3.5, 4.5],
    "kw_chi2_2grp": 3.84,
    "kw_chi2_multi": 5.99,
    "raw_f_threshold": 4.0,
    "eta_sq_threshold": 0.25,
    "shift_to_within_iqr_threshold": 1.5,
    "trimmed_fraction": 0.15,
    "bootstrap_resamples": 500,
    "bootstrap_seed": 20261124,
    "min_group_n_for_fencing": 4,
}


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULT_THRESHOLDS)
    cand = path or (Path(__file__).resolve().parent.parent / "config" / "thresholds.json")
    if cand and cand.is_file():
        try:
            loaded = json.loads(cand.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except Exception:
            pass
    return cfg


def _trimmed_mean(vals: list[float], frac: float = 0.15) -> float:
    s = sorted(vals)
    n = len(s)
    k = int(math.floor(n * frac))
    core = s[k : n - k] if (n - 2 * k) >= 1 else s
    return statistics.mean(core)


def _bootstrap_trimmed_spread_ci(
    valid_groups: dict[str, list[float]],
    *,
    trim_frac: float = 0.15,
    n_boot: int = 500,
    seed: int = 20261124,
) -> dict[str, Any]:
    """Outlier-deletion-free batch-shift inference:
    (1) Group-size-preserving permutation test on trimmed-mean spread (valid null distribution for non-negative range),
    (2) Signed pairwise trimmed-mean difference (max_group - min_group) with seeded bootstrap 95% CI.
    """
    if len(valid_groups) < 2:
        return {"evaluated": False}
    keys = sorted(valid_groups.keys())
    tmeans = {g: round(_trimmed_mean(valid_groups[g], trim_frac), 6) for g in keys}
    max_g = max(keys, key=lambda g: tmeans[g])
    min_g = min(keys, key=lambda g: tmeans[g])
    obs_spread = round(tmeans[max_g] - tmeans[min_g], 6)

    rng = random.Random(seed)
    # 1. Exact group-size-preserving permutation null test for non-negative spread
    pooled = [v for g in keys for v in valid_groups[g]]
    sizes = [len(valid_groups[g]) for g in keys]
    ge_count = 0
    for _ in range(n_boot):
        perm = list(pooled)
        rng.shuffle(perm)
        pos = 0
        p_means: list[float] = []
        for sz in sizes:
            p_means.append(_trimmed_mean(perm[pos : pos + sz], trim_frac))
            pos += sz
        if (max(p_means) - min(p_means)) >= obs_spread - 1e-12:
            ge_count += 1
    perm_p = round((1 + ge_count) / (1 + n_boot), 6)

    # 2. Signed pairwise trimmed-mean difference bootstrap 95% CI (can cross zero!)
    vs_hi = valid_groups[max_g]
    vs_lo = valid_groups[min_g]
    signed_diffs: list[float] = []
    for _ in range(n_boot):
        s_hi = [vs_hi[rng.randrange(len(vs_hi))] for _ in range(len(vs_hi))]
        s_lo = [vs_lo[rng.randrange(len(vs_lo))] for _ in range(len(vs_lo))]
        signed_diffs.append(_trimmed_mean(s_hi, trim_frac) - _trimmed_mean(s_lo, trim_frac))
    signed_diffs.sort()
    lo_idx = max(0, int(0.025 * len(signed_diffs)))
    hi_idx = min(len(signed_diffs) - 1, int(0.975 * len(signed_diffs)))
    s_ci_lo, s_ci_hi = round(signed_diffs[lo_idx], 6), round(signed_diffs[hi_idx], 6)
    signed_excludes_zero = (s_ci_lo > 1e-9) or (s_ci_hi < -1e-9)

    return {
        "evaluated": True,
        "trim_fraction": trim_frac,
        "permutation_resamples": n_boot,
        "bootstrap_resamples": n_boot,
        "group_trimmed_means": tmeans,
        "trimmed_mean_spread": obs_spread,
        "permutation_p_value_trimmed_spread": perm_p,
        "signed_max_minus_min_pair": f"{max_g} - {min_g}",
        "signed_pair_trimmed_diff": obs_spread,
        "signed_pair_bootstrap_95ci": [s_ci_lo, s_ci_hi],
        "signed_pair_ci_excludes_zero": signed_excludes_zero,
        # Backward-compat alias (explicitly populated from valid signed pairwise CI, never non-negative range!)
        "bootstrap_95ci_trimmed_spread": [s_ci_lo, s_ci_hi],
        "bootstrap_ci_excludes_zero": bool(perm_p < 0.05 and signed_excludes_zero),
    }

BELOW_LOD_RE = re.compile(r"^(?:<\s*(?:lod|lloq|loq|\d+(?:\.\d+)?)|blq|nd|bql|not\s+detected)$", re.I)
SAT_RE = re.compile(r"^(?:>?\s*(?:ulq|uloq|ovr|over|sat|saturated|inf)|>\s*\d+(?:\.\d+)?)$", re.I)


def _float_or_none(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(str(v).strip())
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _load_table_rows(file_path: Path, sheet_name: str = "") -> tuple[list[dict[str, Any]], str]:
    """Load CSV/XLSX rows preserving TRUE 1-based physical Excel row numbers and column letters."""
    if file_path.suffix.lower() == ".xlsx":
        _, sheets = _read_xlsx_sheets_indexed(file_path)
        target_name = sheet_name.strip()
        chosen = None
        for sname, raw_pairs in sheets:
            if not target_name or sname == target_name:
                chosen = (sname, raw_pairs)
                break
        if chosen is None:
            return [], f"sheet_not_found:{target_name}"
        sname, raw_pairs = chosen
        non_empty_pairs = [(rno, r) for rno, r in raw_pairs if any(c is not None and str(c).strip() != "" for c in r)]
        if not non_empty_pairs:
            return [], sname
        hdr_idx = _detect_header_row_idx([r for _, r in non_empty_pairs])
        _, hdr_cells = non_empty_pairs[hdr_idx]
        headers = [str(c).strip() if (c is not None and str(c).strip()) else f"col_{i}" for i, c in enumerate(hdr_cells, start=1)]
        col_letters = {headers[i]: _idx_to_col_letters(i) for i in range(len(headers))}
        rows: list[dict[str, Any]] = []
        for phys_r, r in non_empty_pairs[hdr_idx + 1 :]:
            rec: dict[str, Any] = {headers[i]: (r[i] if i < len(r) else None) for i in range(len(headers))}
            rec["_physical_row"] = phys_r
            rec["_col_letters"] = col_letters
            rec["_is_xlsx"] = True
            rows.append(rec)
        return rows, sname

    with file_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(2048)
        fh.seek(0)
        delim = "\t" if ("\t" in sample.splitlines()[0] if sample.splitlines() else False) else ","
        reader = csv.DictReader(fh, delimiter=delim)
        fnames = list(reader.fieldnames or [])
        col_letters = {fn: _idx_to_col_letters(i) for i, fn in enumerate(fnames)}
        rows_csv: list[dict[str, Any]] = []
        for lno, row in enumerate(reader, start=2):
            rec_c: dict[str, Any] = dict(row)
            rec_c["_physical_row"] = lno
            rec_c["_col_letters"] = col_letters
            rec_c["_is_xlsx"] = False
            rows_csv.append(rec_c)
        return rows_csv, file_path.name


def _one_way_anova_stats(valid_groups: dict[str, list[float]]) -> tuple[float, float]:
    """Compute one-way ANOVA F-statistic and eta-squared."""
    all_vals = [v for vals in valid_groups.values() for v in vals]
    if len(valid_groups) < 2 or len(all_vals) <= len(valid_groups):
        return 0.0, 0.0
    grand_mean = statistics.mean(all_vals)
    k = len(valid_groups)
    n_tot = len(all_vals)
    ss_between = sum(len(vals) * (statistics.mean(vals) - grand_mean) ** 2 for vals in valid_groups.values())
    ss_within = sum(sum((v - statistics.mean(vals)) ** 2 for v in vals) for vals in valid_groups.values())
    ms_b = ss_between / (k - 1)
    ms_w = ss_within / (n_tot - k)
    f_stat = round(ms_b / ms_w, 4) if ms_w > 1e-12 else (999.0 if ms_b > 1e-12 else 0.0)
    eta_sq = round(ss_between / (ss_between + ss_within), 4) if (ss_between + ss_within) > 1e-12 else 0.0
    return f_stat, eta_sq


def _kruskal_wallis_h(valid_groups: dict[str, list[float]]) -> float:
    """Exact pure-stdlib nonparametric Kruskal-Wallis H statistic with tie correction."""
    pooled: list[tuple[float, str]] = []
    for g, vals in valid_groups.items():
        for v in vals:
            pooled.append((v, g))
    n = len(pooled)
    if n < 3 or len(valid_groups) < 2:
        return 0.0
    pooled.sort(key=lambda x: x[0])

    rank_sum: dict[str, float] = {g: 0.0 for g in valid_groups}
    tie_corr_sum = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(pooled[j + 1][0] - pooled[i][0]) < 1e-12:
            j += 1
        avg_rank = 0.5 * (i + 1 + j + 1)
        t_cnt = j - i + 1
        if t_cnt > 1:
            tie_corr_sum += (t_cnt ** 3 - t_cnt)
        for idx in range(i, j + 1):
            rank_sum[pooled[idx][1]] += avg_rank
        i = j + 1

    h_raw = (12.0 / (n * (n + 1))) * sum((rank_sum[g] ** 2) / len(vals) for g, vals in valid_groups.items()) - 3.0 * (n + 1)
    denom = 1.0 - (tie_corr_sum / (n ** 3 - n)) if n > 1 else 1.0
    h_adj = (h_raw / denom) if denom > 1e-12 else h_raw
    return round(max(0.0, h_adj), 4)


def _analyze_batch_shift(
    grouped: dict[str, list[tuple[int, str, float]]],
    *,
    excluded_row_ids: set[tuple[str, int]] | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute raw F, nonparametric Kruskal-Wallis H, outlier-free trimmed-mean bootstrap CI, and conditional outlier-excluded F."""
    th = cfg or load_thresholds()
    valid_groups = {g: [x[2] for x in pts] for g, pts in grouped.items() if len(pts) >= 2}
    if len(valid_groups) < 2:
        return {"evaluated": False, "reason": "fewer_than_2_groups_with_n>=2"}

    all_vals = [v for vals in valid_groups.values() for v in vals]
    k = len(valid_groups)
    n_tot = len(all_vals)
    f_stat, eta_sq = _one_way_anova_stats(valid_groups)
    kw_h = _kruskal_wallis_h(valid_groups)
    boot_trim = _bootstrap_trimmed_spread_ci(
        valid_groups,
        trim_frac=float(th.get("trimmed_fraction", 0.15)),
        n_boot=int(th.get("bootstrap_resamples", 500)),
        seed=int(th.get("bootstrap_seed", 20261124)),
    )

    # Conditional outlier-excluded F-stat (strictly annotated as conditional; NEVER standalone proof of batch shift)
    excl_set = excluded_row_ids or set()
    clean_groups = {
        g: [x[2] for x in pts if (g, x[0]) not in excl_set]
        for g, pts in grouped.items()
    }
    clean_valid = {g: vals for g, vals in clean_groups.items() if len(vals) >= 2}
    if len(clean_valid) >= 2:
        excl_f_stat, excl_eta_sq = _one_way_anova_stats(clean_valid)
    else:
        excl_f_stat, excl_eta_sq = f_stat, eta_sq

    group_medians = {g: round(statistics.median(vals), 6) for g, vals in sorted(valid_groups.items())}
    med_vals = list(group_medians.values())
    med_spread = round(max(med_vals) - min(med_vals), 6)
    within_iqrs = []
    for vals in valid_groups.values():
        s = sorted(vals)
        q1 = statistics.median(s[: len(s) // 2]) if len(s) >= 4 else s[0]
        q3 = statistics.median(s[(len(s) + 1) // 2 :]) if len(s) >= 4 else s[-1]
        within_iqrs.append(q3 - q1)
    med_within_iqr = statistics.median(within_iqrs) if within_iqrs else 0.0
    shift_ratio = round(med_spread / med_within_iqr, 3) if med_within_iqr > 1e-12 else None

    # Primary systematic batch-shift gate uses valid permutation null p-value + Kruskal-Wallis H + raw F (never non-negative range CI!)
    kw_thresh = float(th.get("kw_chi2_2grp", 3.84)) if k == 2 else float(th.get("kw_chi2_multi", 5.99))
    raw_f_th = float(th.get("raw_f_threshold", 4.0))
    eta_th = float(th.get("eta_sq_threshold", 0.25))
    iqr_ratio_th = float(th.get("shift_to_within_iqr_threshold", 1.5))
    perm_p = boot_trim.get("permutation_p_value_trimmed_spread")
    shift_flag = (
        (f_stat >= raw_f_th and eta_sq >= eta_th)
        or (kw_h >= kw_thresh and (shift_ratio is None or shift_ratio >= 1.2))
        or (perm_p is not None and perm_p < 0.05 and bool(boot_trim.get("signed_pair_ci_excludes_zero")) and shift_ratio is not None and shift_ratio >= iqr_ratio_th)
    )
    drifting_groups = []
    overall_med = statistics.median(all_vals)
    overall_mad = statistics.median([abs(v - overall_med) for v in all_vals]) or 1e-9
    for g, gm in group_medians.items():
        mz = round(0.6745 * (gm - overall_med) / overall_mad, 3)
        if abs(mz) >= 2.0:
            drifting_groups.append({"group": g, "group_median": gm, "shift_vs_pooled_mad_z": mz})

    return {
        "evaluated": True,
        "group_count": k,
        "total_n": n_tot,
        "one_way_f_stat": f_stat,
        "eta_squared": eta_sq,
        "kruskal_wallis_h": kw_h,
        "trimmed_mean_spread": boot_trim.get("trimmed_mean_spread"),
        "group_trimmed_means": boot_trim.get("group_trimmed_means"),
        "permutation_p_value_trimmed_spread": perm_p,
        "signed_max_minus_min_pair": boot_trim.get("signed_max_minus_min_pair"),
        "signed_pair_trimmed_diff": boot_trim.get("signed_pair_trimmed_diff"),
        "signed_pair_bootstrap_95ci": boot_trim.get("signed_pair_bootstrap_95ci"),
        "signed_pair_ci_excludes_zero": boot_trim.get("signed_pair_ci_excludes_zero"),
        "bootstrap_95ci_trimmed_spread": boot_trim.get("bootstrap_95ci_trimmed_spread"),
        "bootstrap_ci_excludes_zero": boot_trim.get("bootstrap_ci_excludes_zero"),
        "outlier_excluded_f_stat": excl_f_stat,
        "outlier_excluded_eta_squared": excl_eta_sq,
        "excluded_point_outlier_count": len(excl_set),
        "outlier_excluded_f_caveat": "Conditional on excluding flagged point outliers; reported alongside raw F, Kruskal-Wallis H, and permutation p-value / signed pairwise bootstrap CI only — NOT standalone proof of systematic batch shift.",
        "group_medians": group_medians,
        "between_median_spread": med_spread,
        "median_within_iqr": round(med_within_iqr, 6),
        "shift_to_within_iqr_ratio": shift_ratio,
        "systematic_batch_shift_detected": shift_flag,
        "drifting_groups": drifting_groups,
    }


def detect_outliers_table(
    file_path: Path,
    *,
    value_col: str,
    group_col: str = "",
    id_col: str = "",
    sheet_name: str = "",
    mad_z_thresh: float = 3.5,
    upper_rail: float | None = None,
) -> dict[str, Any]:
    rows, src_label = _load_table_rows(file_path, sheet_name=sheet_name)
    if src_label.startswith("sheet_not_found:"):
        return {"$schema": "stratified-outlier-report/v2", "ok": False, "analysis_status": src_label, "file": file_path.name}

    # Tuple: (physical_row, cell_ref, sample_id, val)
    grouped_uncensored: dict[str, list[tuple[int, str, str, float]]] = {}
    grouped_all_numeric: dict[str, list[tuple[int, str, str, float]]] = {}
    data_gaps: list[dict[str, Any]] = []
    valid_reading_count = 0
    explicit_zero_count = 0
    below_lod_count = 0
    saturated_count = 0

    for seq_i, row in enumerate(rows, start=1):
        phys_r = int(row.get("_physical_row") or seq_i)
        col_map = row.get("_col_letters") or {}
        col_let = col_map.get(value_col, "")
        is_xlsx = bool(row.get("_is_xlsx"))
        cell_ref = f"{src_label}!{col_let}{phys_r}" if (is_xlsx and col_let) else (f"{src_label}!R{phys_r}" if is_xlsx else f"{file_path.name}#L{phys_r}")

        grp = str(row.get(group_col, "ALL")).strip() if group_col else "ALL"
        sid = str(row.get(id_col, f"row_{phys_r}")).strip() if id_col else f"row_{phys_r}"
        raw_value = row.get(value_col)
        val = _float_or_none(raw_value)
        if val is None:
            raw_str = "" if raw_value is None else str(raw_value).strip()
            if not raw_str:
                status = "missing"
            elif BELOW_LOD_RE.match(raw_str):
                status = "below_lod"
                below_lod_count += 1
            elif SAT_RE.match(raw_str):
                status = "saturated_over_range"
                saturated_count += 1
            else:
                status = "invalid_or_non_finite"
            data_gaps.append({
                "row_idx": phys_r, "cell_ref": cell_ref, "sample_id": sid, "group": grp, "column": value_col,
                "raw_value": raw_value, "status": status,
            })
            continue

        grouped_all_numeric.setdefault(grp, []).append((phys_r, cell_ref, sid, val))
        if upper_rail is not None and val >= upper_rail - 1e-9:
            saturated_count += 1
            data_gaps.append({
                "row_idx": phys_r, "cell_ref": cell_ref, "sample_id": sid, "group": grp, "column": value_col,
                "raw_value": val, "status": "saturated_at_upper_rail",
                "censoring_action": "excluded_from_uncensored_mean_sd_reported_in_censored_sensitivity",
            })
            # Exclude right-censored saturated observations from uncensored point estimates!
            continue
        if abs(val) < 1e-12:
            explicit_zero_count += 1
        valid_reading_count += 1
        grouped_uncensored.setdefault(grp, []).append((phys_r, cell_ref, sid, val))

    # Build censored sensitivity summary comparing uncensored vs all-numeric (if any right-censored saturated points exist)
    censored_sensitivity: dict[str, Any] = {"has_right_censored_points": saturated_count > 0, "per_group": []}
    if saturated_count > 0:
        for grp in sorted(grouped_all_numeric.keys()):
            unc_v = [x[3] for x in grouped_uncensored.get(grp, [])]
            all_v = [x[3] for x in grouped_all_numeric[grp]]
            censored_sensitivity["per_group"].append({
                "group": grp,
                "uncensored_n": len(unc_v),
                "all_numeric_n": len(all_v),
                "right_censored_count": len(all_v) - len(unc_v),
                "uncensored_mean": round(statistics.mean(unc_v), 6) if unc_v else None,
                "uncensored_median": round(statistics.median(unc_v), 6) if unc_v else None,
                "including_saturated_rail_mean": round(statistics.mean(all_v), 6) if all_v else None,
                "including_saturated_rail_median": round(statistics.median(all_v), 6) if all_v else None,
            })

    outliers: list[dict[str, Any]] = []
    group_stats: list[dict[str, Any]] = []

    for grp, pts in sorted(grouped_uncensored.items()):
        vals = sorted(x[3] for x in pts)
        n = len(vals)
        if n < 4:
            group_stats.append({
                "group": grp, "n": n, "median": round(statistics.median(vals), 6),
                "note": "n<4; insufficient replicates for robust IQR/MAD outlier fencing",
            })
            continue
        med = statistics.median(vals)
        q1 = statistics.median(vals[: n // 2])
        q3 = statistics.median(vals[(n + 1) // 2 :])
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        abs_devs = sorted(abs(v - med) for v in vals)
        mad = statistics.median(abs_devs)

        group_stats.append({
            "group": grp, "n": n, "mean": round(statistics.mean(vals), 6),
            "sd": round(statistics.stdev(vals), 6), "median": round(med, 6),
            "q1": round(q1, 6), "q3": round(q3, 6), "iqr_fence": [round(lo, 6), round(hi, 6)], "mad": round(mad, 6),
        })

        for phys_r, cell_ref, sid, v in pts:
            iqr_flag = (v < lo or v > hi) if iqr > 0 else False
            mad_z = round(0.6745 * (v - med) / mad, 3) if mad > 1e-12 else 0.0
            mad_flag = abs(mad_z) > mad_z_thresh
            if iqr_flag or mad_flag:
                outliers.append({
                    "row_idx": phys_r,
                    "cell_ref": cell_ref,
                    "sample_id": sid,
                    "group": grp,
                    "column": value_col,
                    "value": v,
                    "group_median": round(med, 6),
                    "deviation_from_median": round(v - med, 6),
                    "iqr_fence_violated": iqr_flag,
                    "mad_z_score": mad_z,
                    "threshold_rule": f"IQR_1.5=[{lo:.4f}, {hi:.4f}] or |MAD_Z|>{mad_z_thresh}",
                })

    # MAD-Z threshold sensitivity table across configurable grid (default {2.5, 3.5, 4.5})
    th_cfg = load_thresholds()
    sens_grid = [float(x) for x in th_cfg.get("mad_z_sensitivity_grid", [2.5, 3.5, 4.5])]
    mad_sensitivity: list[dict[str, Any]] = []
    for thr in sens_grid:
        hits = []
        for grp, pts in sorted(grouped_uncensored.items()):
            vals = sorted(x[3] for x in pts)
            if len(vals) < int(th_cfg.get("min_group_n_for_fencing", 4)):
                continue
            med = statistics.median(vals)
            mad = statistics.median([abs(v - med) for v in vals])
            if mad <= 1e-12:
                continue
            for _, _, sid, v in pts:
                if abs(0.6745 * (v - med) / mad) > thr:
                    hits.append(sid)
        mad_sensitivity.append({"mad_z_threshold": thr, "flagged_count": len(hits), "flagged_ids": hits[:25]})

    excl_ids = {(o["group"], o["row_idx"]) for o in outliers}
    grouped_for_shift = {g: [(r, sid, v) for r, _, sid, v in pts] for g, pts in grouped_uncensored.items()}
    batch_shift = _analyze_batch_shift(grouped_for_shift, excluded_row_ids=excl_ids, cfg=th_cfg) if group_col else {"evaluated": False, "reason": "no_group_col_supplied"}
    analysis_status = "no_valid_numeric_readings" if valid_reading_count == 0 else ("partial" if data_gaps else "complete")
    return {
        "$schema": "stratified-outlier-report/v2",
        "ok": valid_reading_count > 0,
        "analysis_status": analysis_status,
        "thresholds_used": {
            "iqr_multiplier": th_cfg.get("iqr_multiplier", 1.5),
            "mad_z_threshold": mad_z_thresh,
            "trimmed_fraction": th_cfg.get("trimmed_fraction", 0.15),
        },
        "file": file_path.name,
        "sheet_or_source": src_label,
        "value_col": value_col,
        "group_col": group_col or None,
        "input_row_count": len(rows),
        "valid_reading_count": valid_reading_count,
        "explicit_zero_count": explicit_zero_count,
        "below_lod_count": below_lod_count,
        "saturated_count": saturated_count,
        "censored_sensitivity_summary": censored_sensitivity,
        "data_gap_count": len(data_gaps),
        "data_gaps": data_gaps,
        "outlier_count": len(outliers),
        "mad_z_sensitivity_table": mad_sensitivity,
        "batch_shift_analysis": batch_shift,
        "group_stats": group_stats,
        "outliers": outliers,
    }


def summarize_multi_split_estimators(csv_path: Path, split_col: str, estimator_col: str, metric_col: str) -> dict[str, Any]:
    rows, _ = _load_table_rows(csv_path)
    by_est: dict[str, dict[str, float]] = {}
    all_splits: set[str] = set()
    data_gaps: list[dict[str, Any]] = []

    for ri, row in enumerate(rows, start=1):
        est = str(row.get(estimator_col, "")).strip()
        sp = str(row.get(split_col, "")).strip()
        raw_v = row.get(metric_col)
        val = _float_or_none(raw_v)
        if sp:
            all_splits.add(sp)
        if not est or not sp or val is None:
            data_gaps.append({"row_idx": ri, "estimator": est, "split": sp, "raw_value": raw_v})
            continue
        by_est.setdefault(est, {})[sp] = val

    summaries: list[dict[str, Any]] = []
    for est, sp_map in sorted(by_est.items()):
        vals = [sp_map[s] for s in sorted(sp_map)]
        sv = sorted(vals)
        n = len(sv)
        q1 = statistics.median(sv[: n // 2]) if n >= 4 else sv[0]
        q3 = statistics.median(sv[(n + 1) // 2 :]) if n >= 4 else sv[-1]
        missing_sp = sorted(all_splits - set(sp_map.keys()))
        summaries.append({
            "estimator": est,
            "split_count": n,
            "missing_splits": missing_sp,
            "mean": round(statistics.mean(vals), 6),
            "sd": round(statistics.stdev(vals), 6) if n >= 2 else 0.0,
            "median": round(statistics.median(vals), 6),
            "iqr": round(q3 - q1, 6),
            "min": min(vals),
            "max": max(vals),
        })

    # Pairwise paired-split comparisons across shared splits
    est_names = sorted(by_est.keys())
    pairwise: list[dict[str, Any]] = []
    for i in range(len(est_names)):
        for j in range(i + 1, len(est_names)):
            e1, e2 = est_names[i], est_names[j]
            shared = sorted(set(by_est[e1]) & set(by_est[e2]))
            if not shared:
                continue
            deltas = [by_est[e1][s] - by_est[e2][s] for s in shared]
            mean_d = statistics.mean(deltas)
            sd_d = statistics.stdev(deltas) if len(deltas) >= 2 else 0.0
            se_d = sd_d / math.sqrt(len(deltas)) if len(deltas) >= 2 else 0.0
            t_stat = round(mean_d / se_d, 4) if se_d > 1e-12 else (999.0 if abs(mean_d) > 1e-12 else 0.0)
            wins_1 = sum(1 for d in deltas if d > 1e-12)
            wins_2 = sum(1 for d in deltas if d < -1e-12)
            ties = len(deltas) - wins_1 - wins_2
            df_sp = len(deltas) - 1
            t_crit = _student_t_crit_95(df_sp) if df_sp >= 1 else None
            ci_t = [round(mean_d - t_crit * se_d, 6), round(mean_d + t_crit * se_d, 6)] if (t_crit is not None and math.isfinite(t_crit)) else [round(mean_d, 6), round(mean_d, 6)]
            pairwise.append({
                "pair": f"{e1} - {e2}",
                "shared_splits": len(shared),
                "df": df_sp,
                "t_critical_95": t_crit,
                "mean_paired_delta": round(mean_d, 6),
                "sd_paired_delta": round(sd_d, 6),
                "paired_95ci_student_t": ci_t,
                "approx_95ci": ci_t,
                "paired_t_stat": t_stat,
                "wins_first": wins_1,
                "ties": ties,
                "wins_second": wins_2,
                "per_split_deltas": {s: round(by_est[e1][s] - by_est[e2][s], 6) for s in shared},
            })

    # Split-level difficulty profiling (detect anomalous splits dragging all estimators down)
    split_means: dict[str, float] = {}
    for sp in sorted(all_splits):
        vals = [by_est[e][sp] for e in est_names if sp in by_est[e]]
        if vals:
            split_means[sp] = round(statistics.mean(vals), 6)
    anomalous_splits: list[dict[str, Any]] = []
    if len(split_means) >= 4:
        sm_vals = list(split_means.values())
        med_s = statistics.median(sm_vals)
        mad_s = statistics.median([abs(v - med_s) for v in sm_vals]) or 1e-12
        for sp, mv in split_means.items():
            mz = round(0.6745 * (mv - med_s) / mad_s, 3)
            if abs(mz) >= 2.5:
                anomalous_splits.append({"split": sp, "mean_across_estimators": mv, "mad_z": mz})

    return {
        "$schema": "multi-split-estimator-summary/v2",
        "ok": bool(summaries),
        "metric": metric_col,
        "total_splits_observed": len(all_splits),
        "data_gap_count": len(data_gaps),
        "data_gaps": data_gaps,
        "estimators": summaries,
        "pairwise_paired_comparisons": pairwise,
        "split_mean_profile": split_means,
        "anomalous_splits": anomalous_splits,
    }


def detect_batch_trace_anomalies(profile_path: Path, mad_z_thresh: float = 3.5) -> dict[str, Any]:
    """Run batch-prefix-stratified IQR/MAD anomaly detection across all profiled raw traces."""
    prof = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    traces = [t for t in prof.get("traces", []) if t.get("ok")]
    if not traces:
        return {"$schema": "batch-trace-anomaly-report/v1", "ok": False, "reason": "no_valid_traces"}

    metrics = ("peak_y", "initial_slope", "auc_trapezoid", "points", "plateau_tail_cv")
    by_batch: dict[str, list[dict[str, Any]]] = {}
    for t in traces:
        by_batch.setdefault(str(t.get("batch_prefix") or "ALL"), []).append(t)

    anomalies: list[dict[str, Any]] = []
    for batch, grp_traces in sorted(by_batch.items()):
        for t in grp_traces:
            if t.get("saturation_flag") or t.get("flatline_flag") or t.get("truncated_flag"):
                flags = [k for k in ("saturation_flag", "flatline_flag", "truncated_flag") if t.get(k)]
                anomalies.append({
                    "file": t["file"], "batch_prefix": batch, "metric": "trace_integrity",
                    "value": t.get("points"), "flags": flags,
                    "threshold_rule": "saturation>=5_rail_pts | flatline | points<0.6*median",
                })
        if len(grp_traces) < 4:
            continue
        for m in metrics:
            pts = [(t["file"], float(t[m])) for t in grp_traces if t.get(m) is not None and math.isfinite(float(t[m]))]
            if len(pts) < 4:
                continue
            vals = sorted(v for _, v in pts)
            n = len(vals)
            med = statistics.median(vals)
            q1 = statistics.median(vals[: n // 2])
            q3 = statistics.median(vals[(n + 1) // 2 :])
            iqr = q3 - q1
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            mad = statistics.median([abs(v - med) for v in vals])
            for fname, v in pts:
                iqr_flag = (v < lo or v > hi) if iqr > 0 else False
                mz = round(0.6745 * (v - med) / mad, 3) if mad > 1e-12 else 0.0
                if iqr_flag or abs(mz) > mad_z_thresh:
                    anomalies.append({
                        "file": fname, "batch_prefix": batch, "metric": m, "value": round(v, 6),
                        "batch_median": round(med, 6), "mad_z_score": mz,
                        "threshold_rule": f"IQR_1.5=[{lo:.4f}, {hi:.4f}] or |MAD_Z|>{mad_z_thresh}",
                    })

    # Check systematic peak_y shift across batches
    peak_grouped = {
        b: [(i, t["file"], float(t["peak_y"])) for i, t in enumerate(gt, start=1) if t.get("peak_y") is not None]
        for b, gt in by_batch.items()
    }
    batch_shift = _analyze_batch_shift(peak_grouped)

    return {
        "$schema": "batch-trace-anomaly-report/v1",
        "ok": True,
        "valid_trace_count": len(traces),
        "batch_count": len(by_batch),
        "anomaly_count": len(anomalies),
        "peak_y_batch_shift": batch_shift,
        "anomalies": anomalies[:200],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stratified CSV/XLSX outlier & batch-shift detector, paired split auditor, and batch-trace anomaly scanner.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_out = sub.add_parser("outliers")
    p_out.add_argument("--file", "--csv", dest="file", required=True)
    p_out.add_argument("--sheet", default="")
    p_out.add_argument("--value-col", required=True)
    p_out.add_argument("--group-col", default="")
    p_out.add_argument("--id-col", default="")
    p_out.add_argument("--upper-rail", type=float, default=None)
    p_out.add_argument("--out", required=True)

    p_sp = sub.add_parser("splits")
    p_sp.add_argument("--csv", required=True)
    p_sp.add_argument("--split-col", required=True)
    p_sp.add_argument("--estimator-col", required=True)
    p_sp.add_argument("--metric-col", required=True)
    p_sp.add_argument("--out", required=True)

    p_bt = sub.add_parser("batch-traces")
    p_bt.add_argument("--profile", required=True)
    p_bt.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "outliers":
        rep = detect_outliers_table(
            Path(args.file).resolve(),
            value_col=args.value_col,
            group_col=args.group_col,
            id_col=args.id_col,
            sheet_name=args.sheet,
            upper_rail=args.upper_rail,
        )
    elif args.cmd == "splits":
        rep = summarize_multi_split_estimators(Path(args.csv).resolve(), args.split_col, args.estimator_col, args.metric_col)
    else:
        rep = detect_batch_trace_anomalies(Path(args.profile).resolve())

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": rep["ok"],
        "cmd": args.cmd,
        "status": rep.get("analysis_status", "ok"),
        "anomalies_or_outliers": rep.get("outlier_count", rep.get("anomaly_count", len(rep.get("anomalous_splits", [])))),
    }, ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
