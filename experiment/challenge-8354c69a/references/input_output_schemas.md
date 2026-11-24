# Input & Output JSON Schemas (`challenge-8354c69a`)

## Output Schema: `/app/output/interpretation_report.json`

```json
{
  "case_id": "case-01",
  "trends": [
    {
      "metric": "Peak flexural load (N)",
      "summary": "Batch A peaks cluster at median 412.5 N (IQR 18.2 N); Batch B shows a systematic -14.8% downward shift (median 351.4 N).",
      "evidence_source": "trace_workbook_profile.json; batch_trace_anomalies.json",
      "baseline_or_interval": "Across 0-8 mm crosshead displacement vs Batch A reference median"
    }
  ],
  "anomalies": [
    {
      "source_file": "B2_trace_044.csv",
      "location": "points=18 (truncated) / peak_y=198.2 N",
      "target_id": "B2_trace_044",
      "dimension": "sample_and_batch",
      "observed_value": "198.2 N (18 pts)",
      "reference_baseline": "Batch B median 351.4 N (median 100 pts)",
      "threshold_rule": "points < 0.6*median and |MAD_Z| = 6.12 > 3.5",
      "deviation_magnitude": "-153.2 N (-43.6%)"
    }
  ],
  "causal_hypotheses": [
    {
      "rank": 1,
      "attribution_layer": "instrument_or_reagent_failure",
      "hypothesis": "Premature load-cell slip or DAQ trigger cutoff terminated B2_trace_044 early.",
      "supporting_evidence": ["Trace has 18 points vs 100-point cohort median", "Abrupt drop to zero at 1.4 mm"],
      "excluding_evidence": ["No similar truncation in adjacent B2_trace_043 or B2_trace_045"],
      "correlation_vs_causation_caveat": "Trace length truncation is an observational signature; fixture slip requires verification against raw machine log.",
      "falsification_test": "Inspect fixture slip marks and raw DAQ timestamp log for B2_trace_044."
    }
  ],
  "verification_checklist": [
    {
      "target_file_or_log": "B2_trace_044.csv & ELN deviation log entry B2-044",
      "action": "Verify raw timestamp spacing and fixture grip pressure record for specimen B2_trace_044",
      "decision_rule": "If grip pressure dropped or DAQ stopped before fracture, classify as invalid mechanical acquisition; else retain as brittle failure."
    }
  ],
  "next_experiments": [
    {
      "target_hypothesis_or_gap": "Batch B -14.8% systematic median shift (F=18.4)",
      "change": "Run 3 paired reference calibration bars on both Batch A and Batch B fixtures before testing new lots."
    }
  ],
  "retained_ambiguities": [
    "Workbook Sheet 1 labels load in N while MATLAB script comment mentions MPa without specimen thickness formula confirmation."
  ],
  "data_gaps_summary": {
    "analysis_status": "partial",
    "valid_reading_count": 195,
    "explicit_zero_count": 0,
    "below_lod_count": 0,
    "saturated_count": 1,
    "data_gap_count": 2
  },
  "six_element_traceability": {
    "hypothesis_variables": "Flexural load vs crosshead displacement across batches",
    "controls": "Batch A reference standard bars",
    "equipment_materials": "Universal testing frame + Batch A/B specimen lots",
    "time_cost": "3 calibration bars (~1.5 hr frame time)",
    "parameter_ranges": "Crosshead rate 2 mm/min; load-cell rail 500 N",
    "risks_anomalies": "Fixture slip truncation (1 trace) and Batch B systematic offset"
  }
}
```
