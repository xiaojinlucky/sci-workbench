#!/usr/bin/env python3
"""Deep 5-part experimental interpretation, 4-layer causal rigor, gap honesty, and Markdown renderer."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REQUIRED_SECTIONS = ("trends", "anomalies", "causal_hypotheses", "verification_checklist", "next_experiments")
VAGUE_CHECK_PATTERNS = (
    re.compile(r"^\s*(?:检查数据质量|复核结果|检查异常|核对数据)\s*$"),
    re.compile(r"^\s*(?:check data quality|verify data|inspect results|re-?check data)\s*$", re.I),
)
VALID_ATTRIBUTION_LAYERS = {
    "operator_or_protocol_deviation",
    "batch_channel_or_plate_effect",
    "instrument_or_reagent_failure",
    "true_domain_signal_or_mechanism",
}


def validate_interpretation(doc: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []

    for sec in REQUIRED_SECTIONS:
        val = doc.get(sec)
        if not isinstance(val, list) or not val:
            issues.append(f"section {sec!r} must be a non-empty list")

    # 1. Trends audit
    for idx, tr in enumerate(doc.get("trends") or [], start=1):
        if not str(tr.get("summary", "")).strip() or not str(tr.get("evidence_source", "")).strip():
            issues.append(f"trends[{idx}] missing summary or evidence_source")
        if not str(tr.get("baseline_or_interval", "")).strip():
            warnings.append(f"trends[{idx}] should specify baseline_or_interval and fitting rule")

    # 2. Anomalies audit (fail-loud on missing source_file, location, target_id, dimension, threshold_rule, observed_value)
    for idx, ano in enumerate(doc.get("anomalies") or [], start=1):
        for fld in ("source_file", "location", "target_id", "dimension", "threshold_rule", "observed_value"):
            if not str(ano.get(fld, "")).strip():
                issues.append(f"anomalies[{idx}] missing required locator/metric field {fld!r}")
        if not str(ano.get("reference_baseline", "")).strip():
            warnings.append(f"anomalies[{idx}] missing reference_baseline or control deviation context")

    # 3. Ranked causal hypotheses audit (fail-loud on supporting/excluding/caveat/falsification_test & anti-conflation)
    OVERCLAIM_RE = re.compile(r"\b(?:proves?\s+that|definitely\s+caused\s+by|confirmed\s+root\s+cause\s+without|100%\s+caused)\b|必然导致|完全证实根因", re.I)
    layers_seen: set[str] = set()
    for idx, hyp in enumerate(doc.get("causal_hypotheses") or [], start=1):
        if hyp.get("rank") is None:
            warnings.append(f"causal_hypotheses[{idx}] should declare explicit rank (1..k)")
        htxt = str(hyp.get("hypothesis", ""))
        if hyp.get("confirmed_root_cause") is True or OVERCLAIM_RE.search(htxt):
            issues.append(f"causal_hypotheses[{idx}] conflates statistical anomaly with proven root cause; must frame as falsifiable candidate hypothesis")
        layer = str(hyp.get("attribution_layer", "")).strip()
        if layer in VALID_ATTRIBUTION_LAYERS:
            layers_seen.add(layer)
        elif layer:
            warnings.append(f"causal_hypotheses[{idx}] unrecognized attribution_layer {layer!r}")
        if not hyp.get("supporting_evidence"):
            issues.append(f"causal_hypotheses[{idx}] must cite supporting_evidence")
        if not hyp.get("excluding_evidence"):
            issues.append(f"causal_hypotheses[{idx}] must record excluding_evidence or competing confounders")
        if not str(hyp.get("correlation_vs_causation_caveat", "")).strip():
            issues.append(f"causal_hypotheses[{idx}] must state correlation_vs_causation_caveat")
        if not str(hyp.get("falsification_test", "")).strip():
            issues.append(f"causal_hypotheses[{idx}] must specify a concrete falsification_test")

    if len(layers_seen) < 2 and len(doc.get("causal_hypotheses") or []) >= 2:
        warnings.append("Causal hypotheses cover fewer than 2 diagnostic layers; consider operator deviation, batch/channel shift, instrument/reagent failure, and true signal.")

    # 4. Executable verification checklist audit
    for idx, chk in enumerate(doc.get("verification_checklist") or [], start=1):
        action = str(chk.get("action", "")).strip()
        target = str(chk.get("target_file_or_log", "")).strip()
        if not action or not target:
            issues.append(f"verification_checklist[{idx}] missing concrete action or target_file_or_log")
        elif any(p.match(action) for p in VAGUE_CHECK_PATTERNS):
            issues.append(f"verification_checklist[{idx}] uses vague non-executable wording: {action!r}")
        if not str(chk.get("decision_rule", "")).strip():
            warnings.append(f"verification_checklist[{idx}] should state explicit pass/fail decision_rule")

    # 5. Next experiments audit
    for idx, nxt in enumerate(doc.get("next_experiments") or [], start=1):
        if not str(nxt.get("change", "")).strip() or not str(nxt.get("target_hypothesis_or_gap", "")).strip():
            issues.append(f"next_experiments[{idx}] must specify concrete change and target_hypothesis_or_gap")

    # 6. Ambiguity retention, data-gap honesty, and 6-element traceability
    if "retained_ambiguities" not in doc:
        issues.append("Missing retained_ambiguities list to preserve source unit/method/threshold uncertainties")
    gaps_sum = doc.get("data_gaps_summary")
    if not isinstance(gaps_sum, dict):
        warnings.append("Missing data_gaps_summary distinguishing missing, BLQ, saturated, and explicit-zero readings")
    else:
        if gaps_sum.get("analysis_status") == "no_valid_numeric_readings":
            warnings.append("Analysis status is no_valid_numeric_readings — do NOT report zero outliers as a clean run.")
        if int(gaps_sum.get("data_gap_count", 0)) > 0 and gaps_sum.get("analysis_status") == "complete":
            issues.append("data_gaps_summary reports status='complete' despite nonzero data_gap_count")

    # 7. Independent experiment plan object (steps + readout_criteria + controls + equipment + cost_time + parameters + risks)
    iep = doc.get("independent_experiment_plan")
    if not isinstance(iep, dict):
        issues.append("Missing required independent_experiment_plan object (must include steps, readout_criteria, controls, equipment_and_materials, time_and_cost, parameter_ranges, risks)")
    else:
        for req_k in ("hypothesis_and_variables", "controls", "steps", "readout_criteria", "equipment_and_materials", "time_and_cost", "parameter_ranges", "risks"):
            val = iep.get(req_k)
            if not val:
                issues.append(f"independent_experiment_plan missing non-empty field {req_k!r}")

    if not isinstance(doc.get("six_element_traceability"), dict):
        warnings.append("Recommended: include six_element_traceability linking findings to hypothesis/controls/equipment/cost/parameters/risks")

    return {
        "$schema": "experiment-5part-audit/v2",
        "ok": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "attribution_layers_covered": sorted(layers_seen),
        "counts": {sec: len(doc.get(sec) or []) for sec in REQUIRED_SECTIONS},
        "retained_ambiguities_count": len(doc.get("retained_ambiguities") or []),
    }


def render_markdown(doc: dict[str, Any]) -> str:
    lines = [
        f"# Experimental Result Interpretation & Anomaly Analysis ({doc.get('case_id', 'unknown')})",
        "",
        "## 1. Result Trends",
        "",
    ]
    for i, tr in enumerate(doc.get("trends") or [], start=1):
        lines.append(f"{i}. **{tr.get('metric', 'Metric')}**: {tr.get('summary', '')} _(Source: `{tr.get('evidence_source', '')}`; Baseline/Interval: {tr.get('baseline_or_interval', '')})_")

    lines.extend(["", "## 2. Stratified Anomalies & Batch Shifts", "", "| Target ID | Dimension | Source File & Location | Observed | Baseline | Threshold Rule |", "| --- | --- | --- | --- | --- | --- |"])
    for a in doc.get("anomalies") or []:
        loc = f"{a.get('source_file', '')} ({a.get('location', '')})".strip()
        lines.append(f"| {a.get('target_id', '')} | {a.get('dimension', '')} | {loc} | {a.get('observed_value', '')} | {a.get('reference_baseline', '')} | {a.get('threshold_rule', '')} |")

    lines.extend(["", "## 3. Ranked Causal Hypotheses (Observation != Confirmed Cause)", ""])
    for h in doc.get("causal_hypotheses") or []:
        lines.append(f"- **Rank {h.get('rank', '?')} [{h.get('attribution_layer', 'unspecified')}]**: {h.get('hypothesis', '')}")
        lines.append(f"  - Supporting: {h.get('supporting_evidence')}")
        lines.append(f"  - Excluding/Confounders: {h.get('excluding_evidence')}")
        lines.append(f"  - Caveat: {h.get('correlation_vs_causation_caveat')}")
        if h.get("falsification_test"):
            lines.append(f"  - Falsification Test: {h.get('falsification_test')}")

    lines.extend(["", "## 4. Executable Verification Checklist", ""])
    for i, c in enumerate(doc.get("verification_checklist") or [], start=1):
        lines.append(f"{i}. `{c.get('target_file_or_log', '')}` — {c.get('action', '')} (Decision rule: {c.get('decision_rule', 'N/A')})")

    lines.extend(["", "## 5. Next-Experiment Recommendations", ""])
    for i, n in enumerate(doc.get("next_experiments") or [], start=1):
        lines.append(f"{i}. **Target**: {n.get('target_hypothesis_or_gap', '')} -> **Action**: {n.get('change', '')}")

    lines.extend(["", "## 6. Retained Source Ambiguities & Data Gaps", ""])
    for amb in doc.get("retained_ambiguities") or []:
        lines.append(f"- {amb}")
    if isinstance(doc.get("data_gaps_summary"), dict):
        lines.append(f"- **Data Gaps Summary**: `{json.dumps(doc['data_gaps_summary'], ensure_ascii=False)}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate 5-part experimental interpretation & render Markdown summary.")
    parser.add_argument("--report", required=True, help="Input interpretation JSON path")
    parser.add_argument("--out", default="", help="Optional validation JSON path")
    parser.add_argument("--md-out", default="", help="Optional rendered Markdown output path")
    args = parser.parse_args(argv)

    doc = json.loads(Path(args.report).read_text(encoding="utf-8-sig"))
    rep = validate_interpretation(doc)
    encoded = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(encoded, encoding="utf-8")
    if args.md_out:
        Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md_out).write_text(render_markdown(doc), encoding="utf-8")
    print(encoded, end="")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
