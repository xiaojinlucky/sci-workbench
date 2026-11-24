#!/usr/bin/env python3
"""Zero-manual end-to-end /input -> /app/output orchestrator for scientific data cleaning (challenge-a0e888fa).

Automatically discovers raw tables, data dictionaries, cleaning workorders, and case.json
under --input-dir, executes read-only fault scan -> reversible cleaning -> independent
disk read-back reconciliation -> downstream statistics_assumptions & chart_choice_record
audit (without performing inferential tests or publication plotting) -> seals result.json.
Pure Python 3.12 stdlib.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from case_contract_sealer import discover_input_dir, seal_result_contract
from dirty_table_fault_scanner import scan_dirty_table
from quality_reconciler import reconcile_cleaning_outputs
from reversible_table_cleaner import clean_table_by_workorder


def _build_downstream_assumptions_and_chart_records(
    cleaned_csv: Path,
    workorder: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive downstream statistics_assumptions & chart_choice_record strictly from cleaned disk CSV.

    Does NOT perform hypothesis testing or publication rendering; records observable scale,
    distribution shape, valid n, and recommended downstream statistical/chart checks required
    by the data cleaning output specification.
    """
    with cleaned_csv.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return [], []

    metric_col = str(workorder.get("metric_column") or "").strip() or None
    val_col = str(workorder.get("value_column") or "").strip() or None
    unit_col = str(workorder.get("unit_column") or workorder.get("unit_col") or "").strip() or None
    num_cols = list(workorder.get("numeric_cols") or [])

    buckets: dict[str, list[float]] = defaultdict(list)
    units: dict[str, set[str]] = defaultdict(set)
    null_counts: dict[str, int] = defaultdict(int)
    qc_flags: dict[str, int] = defaultdict(int)

    if metric_col and val_col and metric_col in rows[0] and val_col in rows[0]:
        for r in rows:
            mname = r.get(metric_col, "").strip() or val_col
            if unit_col and r.get(unit_col, "").strip():
                units[mname].add(r[unit_col].strip())
            if r.get("value_state") == "qc_out_of_bounds":
                qc_flags[mname] += 1
            raw_s = r.get(val_col, "").strip()
            if raw_s == "":
                null_counts[mname] += 1
                continue
            try:
                fv = float(raw_s)
                if math.isfinite(fv):
                    buckets[mname].append(fv)
            except ValueError:
                null_counts[mname] += 1
    else:
        targets = num_cols or [
            c for c in rows[0].keys()
            if not c.endswith("_raw") and c not in {"value_state", "audit_flags", "source_line", "unit"}
        ]
        for r in rows:
            for c in targets:
                raw_s = r.get(c, "").strip()
                if raw_s == "":
                    null_counts[c] += 1
                    continue
                try:
                    fv = float(raw_s)
                    if math.isfinite(fv):
                        buckets[c].append(fv)
                        if unit_col and r.get(unit_col, "").strip():
                            units[c].add(r[unit_col].strip())
                except ValueError:
                    pass

    stats_assumptions: list[dict[str, Any]] = []
    chart_records: list[dict[str, Any]] = []
    is_aggregate = str(workorder.get("table_kind", "")).lower().startswith("published_aggregate") or bool(workorder.get("forbid_population_inference"))

    all_metrics = sorted(set(list(buckets.keys()) + list(null_counts.keys())))
    for mname in all_metrics:
        vals = sorted(buckets.get(mname, []))
        n = len(vals)
        n_null = null_counts.get(mname, 0)
        u_list = sorted(units.get(mname, set()))
        has_neg = any(v < 0.0 for v in vals)
        has_zero = any(v == 0.0 for v in vals)
        skew_est = None
        if n >= 3:
            mean = sum(vals) / n
            var = sum((x - mean) ** 2 for x in vals) / (n - 1)
            if var > 1e-15:
                sd = math.sqrt(var)
                skew_est = round(sum(((x - mean) / sd) ** 3 for x in vals) / n, 4)

        assump_notes = []
        if is_aggregate:
            assump_notes.append("Published aggregate transcription: do NOT infer individual-level population variance or impute missing source cells.")
        if n < 3:
            assump_notes.append(f"Valid n={n} < 3: parametric normality tests are underpowered; report exact values or descriptive summaries only.")
        elif skew_est is not None and abs(skew_est) > 1.0:
            assump_notes.append(f"Substantial skewness observed (skew={skew_est}); verify normality/heteroscedasticity or prefer rank-based methods downstream.")
        else:
            assump_notes.append("Verify normality (Shapiro-Wilk) and equal-variance (Brown-Forsythe Levene) before selecting parametric vs Welch/non-parametric tests.")
        if qc_flags.get(mname, 0) > 0:
            assump_notes.append(f"{qc_flags[mname]} value(s) flagged qc_out_of_bounds; adjudicate physical validity before parametric mean estimation.")

        stats_assumptions.append({
            "metric": mname,
            "units_observed": u_list,
            "valid_numeric_n": n,
            "null_or_sentinel_n": n_null,
            "qc_out_of_bounds_n": qc_flags.get(mname, 0),
            "contains_true_zero": has_zero,
            "contains_negative": has_neg,
            "skewness_estimate": skew_est,
            "aggregate_table_restriction": is_aggregate,
            "downstream_assumption_guidance": assump_notes,
        })

        # Chart choice dependency record (recommendation + anti-pattern guard only; no rendering)
        if n == 0:
            rec_chart = "none_all_missing"
            rationale = "Column/metric has 0 valid numeric observations after cleaning; do not plot fabricated zeros."
            avoid = ["bar_chart", "boxplot"]
        elif has_neg:
            rec_chart = "dot_strip_or_signed_lollipop_with_zero_baseline"
            rationale = f"Metric contains signed negative values (min={vals[0]:g}, max={vals[-1]:g}); preserve explicit y=0 reference line and overlay all points."
            avoid = ["pie_chart", "log_scale_without_offset", "truncated_positive_bar"]
        elif n <= 10:
            rec_chart = "strip_dot_plot_with_exact_values"
            rationale = f"Small valid sample size (n={n}); show every individual cleaned value rather than hiding sparsity behind summary bars."
            avoid = ["mean_only_bar_chart", "violin_plot", "pie_chart"]
        else:
            rec_chart = "boxplot_or_violin_with_raw_points"
            rationale = f"Continuous metric (n={n}, unit={u_list or ['unspecified']}); display full distribution plus individual points with explicit unit and n."
            avoid = ["pie_chart", "truncated_bar_baseline", "3d_bar"]

        chart_records.append({
            "metric": mname,
            "units": u_list,
            "valid_n": n,
            "value_range": [vals[0], vals[-1]] if vals else None,
            "recommended_downstream_chart": rec_chart,
            "rationale": rationale,
            "anti_patterns_to_avoid": avoid,
        })

    return stats_assumptions, chart_records


def run_auto_cleaning(
    input_dir: Path,
    out_dir: Path,
    *,
    stem: str = "cleaned_table",
    input_file: Path | None = None,
    workorder_file: Path | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    disc = discover_input_dir(input_dir)
    table_p = input_file or (Path(disc["primary_table_candidate"]) if disc.get("primary_table_candidate") else None)
    wo_p = workorder_file or (Path(disc["primary_workorder_candidate"]) if disc.get("primary_workorder_candidate") else None)
    case_json_p = (input_dir / "case.json") if (input_dir / "case.json").is_file() else None

    if table_p is None or not table_p.is_file():
        return {"ok": False, "error": f"No tabular data file discovered under {input_dir}"}

    wo = json.loads(wo_p.read_text(encoding="utf-8-sig")) if (wo_p and wo_p.is_file()) else {}

    scan = scan_dirty_table(table_p, workorder=wo)
    scan_p = out_dir / f"{stem}_fault_scan.json"
    scan_p.write_text(json.dumps(scan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    clean_res = clean_table_by_workorder(table_p, wo, out_dir, stem=stem)
    csv_p = out_dir / f"{stem}.csv"
    man_p = out_dir / f"{stem}_manifest.json"
    quar_p = out_dir / f"{stem}_quarantine_conflicts.csv"
    md_p = out_dir / f"{stem}_quality.md"
    rec_p = out_dir / f"{stem}_reconciliation.json"

    rec = reconcile_cleaning_outputs(csv_p, man_p, quarantine_path=quar_p, out_md=md_p, out_json=rec_p)
    stats_assump, chart_recs = _build_downstream_assumptions_and_chart_records(csv_p, wo)

    # Enrich reconciliation JSON & quality MD with downstream statistics_assumptions & chart_choice_record
    rec["statistics_assumptions"] = stats_assump
    rec["chart_choice_record"] = chart_recs
    rec["elapsed_seconds"] = round(time.monotonic() - t0, 4)
    rec_p.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if stats_assump or chart_recs:
        extra_md = ["", "## 7. Downstream Statistical Assumptions & Chart Choice Records (Dependency Hand-off)", ""]
        extra_md.append("| Metric | Units | Valid n | Null/Sentinel | Skewness | Recommended Downstream Chart | Anti-Patterns Blocked |")
        extra_md.append("| --- | --- | ---: | ---: | ---: | --- | --- |")
        by_m = {c["metric"]: c for c in chart_recs}
        for sa in stats_assump:
            cr = by_m.get(sa["metric"], {})
            extra_md.append(
                f"| `{sa['metric']}` | {', '.join(sa['units_observed']) or 'n/a'} | {sa['valid_numeric_n']} | {sa['null_or_sentinel_n']} | {sa['skewness_estimate']} | `{cr.get('recommended_downstream_chart')}` | {', '.join(cr.get('anti_patterns_to_avoid', []))} |"
            )
        md_p.write_text(md_p.read_text(encoding="utf-8").rstrip() + "\n" + "\n".join(extra_md) + "\n", encoding="utf-8")

    arts = [
        csv_p.name, quar_p.name, man_p.name, scan_p.name,
        rec_p.name, md_p.name, f"reproduce_{stem}.py",
    ]
    sources = [table_p.name] + ([wo_p.name] if wo_p and wo_p.is_file() else [])
    uncertainties = []
    gaps_list = []
    pk_audit = scan.get("primary_key_audit") or {}
    if pk_audit.get("status") == "MISSING_DECLARED_PK":
        msg = pk_audit.get("warning") or f"Missing declared primary_key columns: {pk_audit.get('missing_primary_key_columns')}"
        uncertainties.append(msg)
        gaps_list.append(f"MISSING_DECLARED_PK: {msg}")
    if clean_res["row_accounting"]["rows_quarantined_key_conflicts"] > 0:
        uncertainties.append(f"{clean_res['row_accounting']['rows_quarantined_key_conflicts']} conflicting primary-key row(s) quarantined for expert review.")
    if clean_res["cell_accounting"]["qc_out_of_bounds_cells"] > 0:
        uncertainties.append(f"{clean_res['cell_accounting']['qc_out_of_bounds_cells']} value(s) flagged qc_out_of_bounds.")
    if clean_res["row_accounting"]["rows_raw"] == 0:
        gaps_list.append("Input table contained 0 data rows.")

    seal_status = "partial_with_gaps" if gaps_list else ("ok" if rec["ok"] else "partial")
    seal = seal_result_contract(
        case_json_path=case_json_p,
        result_json_path=out_dir / "result.json",
        task_id="challenge-a0e888fa",
        status=seal_status,
        payload_path=rec_p,
        artifacts=arts,
        sources=sources,
        uncertainty=uncertainties,
        gaps=gaps_list,
    )

    return {
        "ok": bool(rec["ok"] and seal["ok"] and pk_audit.get("status") != "MISSING_DECLARED_PK"),
        "status": seal_status,
        "elapsed_seconds": rec["elapsed_seconds"],
        "source_table": table_p.name,
        "workorder": wo_p.name if wo_p else None,
        "primary_key_audit": pk_audit,
        "row_accounting": clean_res["row_accounting"],
        "statistics_assumptions_count": len(stats_assump),
        "chart_choice_record_count": len(chart_recs),
        "reconciliation_passed": rec["reconciliation_passed"],
        "result_json": "result.json",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-manual /input -> /app/output scientific data cleaning orchestrator.")
    parser.add_argument("--input-dir", required=True, help="Case input directory containing raw table, workorder/dictionary, and case.json.")
    parser.add_argument("--out-dir", required=True, help="Output directory (e.g. /app/output).")
    parser.add_argument("--stem", default="cleaned_table", help="Output filename stem.")
    parser.add_argument("--input", default=None, help="Optional explicit raw table override.")
    parser.add_argument("--workorder", default=None, help="Optional explicit workorder override.")
    args = parser.parse_args(argv)

    rep = run_auto_cleaning(
        Path(args.input_dir),
        Path(args.out_dir),
        stem=args.stem,
        input_file=Path(args.input) if args.input else None,
        workorder_file=Path(args.workorder) if args.workorder else None,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
