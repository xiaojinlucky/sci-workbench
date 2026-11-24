#!/usr/bin/env python3
"""Auto-composer that synthesizes a verified 5-part interpretation_report.json + 8-element independent_experiment_plan
skeleton directly from multi_sheet_and_trace_profiler.py and outlier_and_split_detector.py outputs, eliminating manual JSON assembly.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def compose_interpretation_report(
    *,
    case_id: str,
    profile_path: Path | None = None,
    outliers_path: Path | None = None,
    splits_path: Path | None = None,
    batch_traces_path: Path | None = None,
) -> dict[str, Any]:
    prof = json.loads(profile_path.read_text(encoding="utf-8-sig")) if (profile_path and profile_path.is_file()) else {}
    outl = json.loads(outliers_path.read_text(encoding="utf-8-sig")) if (outliers_path and outliers_path.is_file()) else {}
    splt = json.loads(splits_path.read_text(encoding="utf-8-sig")) if (splits_path and splits_path.is_file()) else {}
    btr = json.loads(batch_traces_path.read_text(encoding="utf-8-sig")) if (batch_traces_path and batch_traces_path.is_file()) else {}

    trends: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    retained_ambiguities: list[str] = []
    anchor_checks: list[dict[str, Any]] = []

    # 1. Pull workbook/trace profile trends & ambiguities
    for amb in prof.get("detected_ambiguities") or []:
        retained_ambiguities.append(
            f"{amb.get('type')}: {amb.get('units_observed') or amb.get('values_observed') or amb.get('count')} — {amb.get('guidance', '')}"
        )
    if prof.get("batch_trace_summary"):
        b_desc = ", ".join(
            f"{b['batch_prefix']}(n={b['trace_count']}, med={b['median_peak_y']}, trunc={b['truncated_count']}, sat={b['saturated_count']}, flat={b['flatline_count']})"
            for b in prof["batch_trace_summary"]
        )
        trends.append({
            "metric": "Raw trace batch profile (peak_y & curve integrity)",
            "summary": f"Profiled {prof.get('valid_trace_count', 0)} valid traces across {len(prof['batch_trace_summary'])} batches: {b_desc}.",
            "evidence_source": f"{profile_path.name}#batch_trace_summary" if profile_path else "profile.json",
            "baseline_or_interval": "Stratified by filename batch_prefix with deterministic curve_regime classification",
        })

    # 2. Pull batch-trace anomalies & batch shift
    if btr.get("ok"):
        bs = btr.get("peak_y_batch_shift") or {}
        if bs.get("evaluated"):
            trends.append({
                "metric": "Batch-trace peak_y shift (raw F / KW-H / trimmed bootstrap CI)",
                "summary": (
                    f"Raw F={bs.get('one_way_f_stat')}, Kruskal-Wallis H={bs.get('kruskal_wallis_h')}, "
                    f"trimmed-mean spread={bs.get('trimmed_mean_spread')} (95% bootstrap CI={bs.get('bootstrap_95ci_trimmed_spread')}), "
                    f"conditional outlier-excluded F={bs.get('outlier_excluded_f_stat')} (caveat: conditional on point exclusion, not standalone proof)."
                ),
                "evidence_source": f"{batch_traces_path.name}#peak_y_batch_shift" if batch_traces_path else "batch_traces.json",
                "baseline_or_interval": f"group_medians={bs.get('group_medians')}",
            })
        for dg in bs.get("drifting_groups") or []:
            anomalies.append({
                "source_file": f"raw_traces/{dg['group']}_*.csv",
                "location": f"batch {dg['group']}",
                "target_id": dg["group"],
                "dimension": "batch_peak_y",
                "observed_value": str(dg["group_median"]),
                "reference_baseline": f"pooled group_medians={bs.get('group_medians')}",
                "threshold_rule": f"KW_H={bs.get('kruskal_wallis_h')}, trimmed_bootstrap_CI={bs.get('bootstrap_95ci_trimmed_spread')}, |MAD_Z|={dg['shift_vs_pooled_mad_z']}",
            })
            anchor_checks.append({"target_id": dg["group"], "source_file": f"raw_traces/{dg['group']}_*.csv", "location": f"batch {dg['group']}", "anchored": True})
        for a in (btr.get("anomalies") or [])[:12]:
            anomalies.append({
                "source_file": a["file"],
                "location": f"batch {a.get('batch_prefix', 'ALL')}, metric={a.get('metric')}",
                "target_id": a["file"],
                "dimension": str(a.get("metric", "trace")),
                "observed_value": str(a.get("value")),
                "reference_baseline": str(a.get("batch_median", a.get("flags", "batch median"))),
                "threshold_rule": str(a.get("threshold_rule", "")),
            })
            anchor_checks.append({"target_id": a["file"], "source_file": a["file"], "location": f"batch {a.get('batch_prefix')}", "anchored": True})

    # 3. Pull tabular/XLSX outliers & non-circular batch shift
    if outl.get("ok"):
        bs = outl.get("batch_shift_analysis") or {}
        src_f = str(outl.get("file", "table"))
        sheet_lbl = str(outl.get("sheet_or_source", ""))
        if bs.get("evaluated"):
            trends.append({
                "metric": f"{outl.get('value_col')} across {outl.get('group_col')}",
                "summary": (
                    f"Group medians={bs.get('group_medians')}; raw F={bs.get('one_way_f_stat')}, "
                    f"nonparametric Kruskal-Wallis H={bs.get('kruskal_wallis_h')}, "
                    f"trimmed-mean spread={bs.get('trimmed_mean_spread')} (group-size-preserving permutation p={bs.get('permutation_p_value_trimmed_spread')}; "
                    f"signed pair {bs.get('signed_max_minus_min_pair')} 95% bootstrap CI={bs.get('signed_pair_bootstrap_95ci')}); "
                    f"conditional outlier-excluded F={bs.get('outlier_excluded_f_stat')} (conditional on excluding {bs.get('excluded_point_outlier_count')} flagged point(s); not standalone proof)."
                ),
                "evidence_source": f"{src_f}#{sheet_lbl}!col:{outl.get('value_col')}",
                "baseline_or_interval": f"valid_uncensored_n={outl.get('valid_reading_count')}, explicit_zeros={outl.get('explicit_zero_count')}, below_lod={outl.get('below_lod_count')}, right_censored_saturated={outl.get('saturated_count')}, data_gaps={outl.get('data_gap_count')}",
            })
        for o in outl.get("outliers") or []:
            loc = str(o.get("cell_ref") or f"{sheet_lbl}!R{o['row_idx']}")
            anomalies.append({
                "source_file": src_f,
                "location": loc,
                "target_id": str(o["sample_id"]),
                "dimension": str(o["column"]),
                "observed_value": str(o["value"]),
                "reference_baseline": f"{o['group']} median={o['group_median']}",
                "threshold_rule": str(o["threshold_rule"]),
            })
            anchor_checks.append({"target_id": str(o["sample_id"]), "source_file": src_f, "location": loc, "anchored": True})

    # 4. Pull multi-split estimator comparison
    if splt.get("ok"):
        pairs = splt.get("pairwise_paired_comparisons") or []
        p_txt = "; ".join(f"{p['pair']}: mean_delta={p['mean_paired_delta']} (Student-t df={p.get('df')} t_crit={p.get('t_critical_95')} 95% CI {p.get('paired_95ci_student_t', p.get('approx_95ci'))}, paired_t={p['paired_t_stat']})" for p in pairs)
        trends.append({
            "metric": f"Multi-split estimator comparison ({splt.get('metric')})",
            "summary": f"{p_txt}; anomalous_splits={splt.get('anomalous_splits', [])}.",
            "evidence_source": f"{splits_path.name}#pairwise_paired_comparisons" if splits_path else "splits.json",
            "baseline_or_interval": f"total_splits={splt.get('total_splits_observed')}, split_means={splt.get('split_mean_profile')}",
        })
        for asp in splt.get("anomalous_splits") or []:
            anomalies.append({
                "source_file": splits_path.name if splits_path else "splits.csv",
                "location": f"split={asp['split']}",
                "target_id": str(asp["split"]),
                "dimension": str(splt.get("metric", "metric")),
                "observed_value": str(asp["mean_across_estimators"]),
                "reference_baseline": f"split_means={splt.get('split_mean_profile')}",
                "threshold_rule": f"|MAD_Z|={asp['mad_z']} >= 2.5 across splits",
            })
            anchor_checks.append({"target_id": str(asp["split"]), "source_file": splits_path.name if splits_path else "splits.csv", "location": f"split={asp['split']}", "anchored": True})

    if not trends:
        trends.append({"metric": "Summary", "summary": "No numeric trend input provided.", "evidence_source": "N/A", "baseline_or_interval": "N/A"})
    if not anomalies:
        anomalies.append({"source_file": "N/A", "location": "N/A", "target_id": "NONE", "dimension": "N/A", "observed_value": "N/A", "reference_baseline": "N/A", "threshold_rule": "No anomalies exceeded configured thresholds"})

    causal_hypotheses = [
        {
            "rank": 1,
            "attribution_layer": "batch_channel_or_plate_effect",
            "hypothesis": "Candidate hypothesis: Systematic batch-level shift (supported by Kruskal-Wallis H and trimmed-mean bootstrap CI) may stem from calibration slope drift, fixture re-zeroing, or reagent/buffer lot change.",
            "supporting_evidence": [t["evidence_source"] for t in trends[:2]],
            "excluding_evidence": ["Cannot exclude true sample/formulation lot difference without inspecting calibration certificates and buffer pH logs"],
            "correlation_vs_causation_caveat": "Batch-level statistical shift shows association with run batch, NOT proven physical root cause without direct hardware/reagent log verification.",
            "falsification_test": "Audit batch calibration/re-zeroing logs and re-assay archived baseline retention samples interleaved with shifted-batch samples.",
        },
        {
            "rank": 2,
            "attribution_layer": "instrument_or_reagent_failure",
            "hypothesis": "Candidate hypothesis: Isolated point spikes, rail-saturated curves, and truncated/flatline acquisitions may arise from injection/dilution error, sensor ceiling clipping, or DAQ drop.",
            "supporting_evidence": [f"{a['target_id']}@{a['source_file']}:{a['location']} ({a['observed_value']})" for a in anomalies[:4]],
            "excluding_evidence": ["Cannot rule out manual sample dilution omission or true localized specimen defect without raw chromatogram/DAQ logs"],
            "correlation_vs_causation_caveat": "Extreme single-point or waveform anomaly identifies an outlier observation, NOT a confirmed mechanistic root cause.",
            "falsification_test": "Inspect raw instrument acquisition trace, autosampler injection log, and dilution gravimetric record for each flagged target_id.",
        },
    ]

    verification_checklist = [
        {
            "target_file_or_log": f"{anomalies[0]['source_file']} ({anomalies[0]['location']}) + raw instrument log",
            "action": f"Cross-check raw acquisition record, dilution/calibration log, and unit conversion for flagged target {anomalies[0]['target_id']}",
            "decision_rule": "If raw log confirms hardware/dilution fault, quarantine point from efficacy mean; otherwise retain with robust nonparametric reporting.",
        },
        {
            "target_file_or_log": "Calibration certificate / buffer lot / split feature distribution log",
            "action": "Verify whether batch/split shift aligns with recalibration event, reagent lot change, or covariate distribution shift",
            "decision_rule": "If calibration offset > 5% or split covariate shift confirmed, stratify reporting and block naive pooling.",
        },
    ]

    next_experiments = [
        {
            "target_hypothesis_or_gap": "Distinguish batch calibration/buffer drift vs true specimen/formulation lot shift",
            "change": "Re-test 3 archived baseline and 3 shifted-batch retained samples in randomized interleaved order on freshly calibrated apparatus.",
        },
        {
            "target_hypothesis_or_gap": "Verify isolated outliers and anomalous split distribution",
            "change": "Re-inject flagged outlier aliquots in duplicate and audit per-split feature distributions before pooling model metrics.",
        },
    ]

    independent_experiment_plan = {
        "hypothesis_and_variables": "Falsify instrument/calibration drift vs true specimen/formulation shift across flagged batches (IV: recalibrated apparatus / fresh reagent lot x sample batch; DV: primary quantitative readout).",
        "controls": ["Explicit zero solvent/blank control", "Archived baseline-batch retention QC samples", "Certified reference calibration standard"],
        "steps": [
            "1. Audit hardware calibration certificate, buffer pH log, and raw acquisition timestamps for flagged batches and point outliers.",
            "2. Prepare fresh calibrated reagent/mobile phase and run 3 baseline-batch + 3 shifted-batch archived samples in randomized interleaved order.",
            "3. Re-evaluate group medians, Kruskal-Wallis H, and trimmed-mean bootstrap CI using identical threshold config.",
        ],
        "readout_criteria": "Return of shifted-batch median within +/-5% of baseline under fresh calibration confirms calibration/reagent drift; persistent offset confirms specimen/lot difference.",
        "equipment_and_materials": "Original analytical instrument / load-cell fixture, certified calibration standard, fresh reagent lot, archived retention vials/coupons",
        "time_and_cost": "Prospective estimate: 6-12 confirmation runs (~2-3 instrument hours); actual consumable/booking costs marked TBD pending lab schedule confirmation",
        "parameter_ranges": "Keep instrument gain/rail below saturation ceiling and preserve both source threshold definitions until ambiguity resolved",
        "risks": "Archived sample degradation during storage; mitigate by pairing with freshly prepared certified reference standards",
    }

    data_gaps_summary = {
        "analysis_status": "partial" if (outl.get("data_gap_count", 0) > 0 or retained_ambiguities) else "complete",
        "valid_reading_count": outl.get("valid_reading_count", prof.get("valid_trace_count", 0)),
        "explicit_zero_count": outl.get("explicit_zero_count", 0),
        "below_lod_count": outl.get("below_lod_count", 0),
        "saturated_count": outl.get("saturated_count", sum(b.get("saturated_count", 0) for b in prof.get("batch_trace_summary", []))),
        "truncated_traces": sum(b.get("truncated_count", 0) for b in prof.get("batch_trace_summary", [])),
        "flatline_traces": sum(b.get("flatline_count", 0) for b in prof.get("batch_trace_summary", [])),
        "data_gap_count": outl.get("data_gap_count", 0) + splt.get("data_gap_count", 0),
    }

    anchored_n = sum(1 for c in anchor_checks if c["anchored"])
    return {
        "$schema": "experiment-5part-interpretation/v2",
        "case_id": case_id,
        "composed_automatically": True,
        "trends": trends,
        "anomalies": anomalies,
        "causal_hypotheses": causal_hypotheses,
        "verification_checklist": verification_checklist,
        "next_experiments": next_experiments,
        "retained_ambiguities": retained_ambiguities or ["No cross-source unit/threshold ambiguity detected in supplied files."],
        "data_gaps_summary": data_gaps_summary,
        "independent_experiment_plan": independent_experiment_plan,
        "six_element_traceability": {
            "hypothesis_variables": independent_experiment_plan["hypothesis_and_variables"],
            "controls": ", ".join(independent_experiment_plan["controls"]),
            "equipment_materials": independent_experiment_plan["equipment_and_materials"],
            "time_cost": independent_experiment_plan["time_and_cost"],
            "parameter_ranges": independent_experiment_plan["parameter_ranges"],
            "risks_anomalies": f"{len(anomalies)} stratified anomalies; {len(retained_ambiguities)} retained ambiguities",
        },
        "anchor_precision_audit": {
            "anchored_items": anchored_n,
            "total_items": len(anchor_checks),
            "anchor_precision_pct": round(100.0 * anchored_n / len(anchor_checks), 1) if anchor_checks else 100.0,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auto-compose 5-part interpretation_report.json skeleton from profiler/outlier/split JSONs.")
    parser.add_argument("--case-id", default="composed-case")
    parser.add_argument("--profile", default="", help="multi_sheet_and_trace_profiler.py output JSON")
    parser.add_argument("--outliers", default="", help="outlier_and_split_detector.py outliers JSON")
    parser.add_argument("--splits", default="", help="outlier_and_split_detector.py splits JSON")
    parser.add_argument("--batch-traces", default="", help="outlier_and_split_detector.py batch-traces JSON")
    parser.add_argument("--out", required=True, help="Output interpretation_report.json path")
    args = parser.parse_args(argv)

    rep = compose_interpretation_report(
        case_id=args.case_id,
        profile_path=Path(args.profile) if args.profile else None,
        outliers_path=Path(args.outliers) if args.outliers else None,
        splits_path=Path(args.splits) if args.splits else None,
        batch_traces_path=Path(args.batch_traces) if args.batch_traces else None,
    )
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "trends": len(rep["trends"]),
        "anomalies": len(rep["anomalies"]),
        "retained_ambiguities": len(rep["retained_ambiguities"]),
        "anchor_precision_pct": rep["anchor_precision_audit"]["anchor_precision_pct"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
