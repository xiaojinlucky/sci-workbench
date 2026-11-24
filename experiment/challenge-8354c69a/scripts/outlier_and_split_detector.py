#!/usr/bin/env python3
"""Stratified IQR/MAD outlier detector (CSV & XLSX), batch-shift analyzer, paired split comparator, and batch-trace auditor."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

from multi_sheet_and_trace_profiler import _detect_header_row_idx, _read_xlsx_sheets

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
    if file_path.suffix.lower() == ".xlsx":
        _, sheets = _read_xlsx_sheets(file_path)
        target_name = sheet_name.strip()
        chosen = None
        for sname, raw_rows in sheets:
            if not target_name or sname == target_name:
                chosen = (sname, raw_rows)
                break
        if chosen is None:
            return [], f"sheet_not_found:{target_name}"
        sname, raw_rows = chosen
        non_empty = [r for r in raw_rows if any(c is not None and str(c).strip() != "" for c in r)]
        if not non_empty:
            return [], sname
        hdr_idx = _detect_header_row_idx(non_empty)
        headers = [str(c).strip() if (c is not None and str(c).strip()) else f"col_{i}" for i, c in enumerate(non_empty[hdr_idx], start=1)]
        rows: list[dict[str, Any]] = []
        for r in non_empty[hdr_idx + 1 :]:
            rows.append({headers[i]: (r[i] if i < len(r) else None) for i in range(len(headers))})
        return rows, sname

    with file_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(2048)
        fh.seek(0)
        delim = "\t" if ("\t" in sample.splitlines()[0] if sample.splitlines() else False) else ","
        return list(csv.DictReader(fh, delimiter=delim)), file_path.name


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
) -> dict[str, Any]:
    """Compute raw one-way F, outlier-excluded one-way F, nonparametric Kruskal-Wallis H, and median spread ratio."""
    valid_groups = {g: [x[2] for x in pts] for g, pts in grouped.items() if len(pts) >= 2}
    if len(valid_groups) < 2:
        return {"evaluated": False, "reason": "fewer_than_2_groups_with_n>=2"}

    all_vals = [v for vals in valid_groups.values() for v in vals]
    k = len(valid_groups)
    n_tot = len(all_vals)
    f_stat, eta_sq = _one_way_anova_stats(valid_groups)
    kw_h = _kruskal_wallis_h(valid_groups)

    # Outlier-excluded F-stat so a single extreme point outlier (e.g. S_007) cannot mask systematic batch drift
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

    # Robust systematic batch-shift gate combining raw F, outlier-excluded F, Kruskal-Wallis H, and median/IQR ratio
    kw_thresh = 3.84 if k == 2 else 5.99
    shift_flag = (
        (f_stat >= 4.0 and eta_sq >= 0.25)
        or (excl_f_stat >= 4.0 and excl_eta_sq >= 0.25)
        or (kw_h >= kw_thresh and (shift_ratio is None or shift_ratio >= 1.2))
        or (shift_ratio is not None and shift_ratio >= 1.5 and excl_eta_sq >= 0.20)
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
        "outlier_excluded_f_stat": excl_f_stat,
        "outlier_excluded_eta_squared": excl_eta_sq,
        "excluded_point_outlier_count": len(excl_set),
        "kruskal_wallis_h": kw_h,
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

    grouped: dict[str, list[tuple[int, str, float]]] = {}
    data_gaps: list[dict[str, Any]] = []
    valid_reading_count = 0
    explicit_zero_count = 0
    below_lod_count = 0
    saturated_count = 0

    for ri, row in enumerate(rows, start=1):
        grp = str(row.get(group_col, "ALL")).strip() if group_col else "ALL"
        sid = str(row.get(id_col, f"row_{ri}")).strip() if id_col else f"row_{ri}"
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
                "row_idx": ri, "sample_id": sid, "group": grp, "column": value_col,
                "raw_value": raw_value, "status": status,
            })
            continue

        if upper_rail is not None and val >= upper_rail - 1e-9:
            saturated_count += 1
            data_gaps.append({
                "row_idx": ri, "sample_id": sid, "group": grp, "column": value_col,
                "raw_value": val, "status": "saturated_at_upper_rail",
            })
        if abs(val) < 1e-12:
            explicit_zero_count += 1
        valid_reading_count += 1
        grouped.setdefault(grp, []).append((ri, sid, val))

    outliers: list[dict[str, Any]] = []
    group_stats: list[dict[str, Any]] = []

    for grp, pts in sorted(grouped.items()):
        vals = sorted(x[2] for x in pts)
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

        for ri, sid, v in pts:
            iqr_flag = (v < lo or v > hi) if iqr > 0 else False
            mad_z = round(0.6745 * (v - med) / mad, 3) if mad > 1e-12 else 0.0
            mad_flag = abs(mad_z) > mad_z_thresh
            if iqr_flag or mad_flag:
                outliers.append({
                    "row_idx": ri,
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

    excl_ids = {(o["group"], o["row_idx"]) for o in outliers}
    batch_shift = _analyze_batch_shift(grouped, excluded_row_ids=excl_ids) if group_col else {"evaluated": False, "reason": "no_group_col_supplied"}
    analysis_status = "no_valid_numeric_readings" if valid_reading_count == 0 else ("partial" if data_gaps else "complete")
    return {
        "$schema": "stratified-outlier-report/v2",
        "ok": valid_reading_count > 0,
        "analysis_status": analysis_status,
        "file": file_path.name,
        "sheet_or_source": src_label,
        "value_col": value_col,
        "group_col": group_col or None,
        "input_row_count": len(rows),
        "valid_reading_count": valid_reading_count,
        "explicit_zero_count": explicit_zero_count,
        "below_lod_count": below_lod_count,
        "saturated_count": saturated_count,
        "data_gap_count": len(data_gaps),
        "data_gaps": data_gaps,
        "outlier_count": len(outliers),
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
            pairwise.append({
                "pair": f"{e1} - {e2}",
                "shared_splits": len(shared),
                "mean_paired_delta": round(mean_d, 6),
                "sd_paired_delta": round(sd_d, 6),
                "approx_95ci": [round(mean_d - 1.96 * se_d, 6), round(mean_d + 1.96 * se_d, 6)],
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
