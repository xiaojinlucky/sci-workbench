#!/usr/bin/env python3
"""Six-element completeness, confirmation-gate, and anti-boilerplate auditor for optimized experimental plans."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

BOILERPLATE_PATTERNS = (
    re.compile(r"注意安全"),
    re.compile(r"准备样品.{0,6}设置对照.{0,6}检测分析"),
    re.compile(r"^\s*check safety\s*$", re.I),
    re.compile(r"^\s*follow standard protocol\s*$", re.I),
)

EXECUTED_CLAIM_PATTERNS = (
    re.compile(r"\b(?:confirmed vendor quote|irb approved|iacuc approved|experiment was executed)\b", re.I),
)


def verify_plan_report(plan: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Check all 6 required protocol elements, 3-version presence, confirmation items, and boilerplate leaks."""
    issues: list[str] = []
    warnings: list[str] = []

    versions = plan.get("versions_evaluated") or []
    if len(versions) < 3:
        issues.append(f"Expected >= 3 versioned candidate plans, found {len(versions)}")

    ctrl = plan.get("control_audit") or {}
    if not ctrl.get("iv") or not ctrl.get("dv"):
        issues.append("Element 1 (Hypothesis & Variables): missing operationalized IV or DV")
    if int(ctrl.get("control_count", 0)) < 2:
        issues.append("Element 2 (Controls): fewer than 2 control arms")

    contract = case.get("prospective_contract") or {}
    if not (contract.get("resources") or case.get("equipment_materials")):
        warnings.append("Element 3 (Equipment & Materials): no explicit equipment/reagent specification table attached")
    else:
        res_map = contract.get("resources") or {}
        missing_sub = [k for k, v in res_map.items() if isinstance(v, dict) and "substitutable" not in v]
        if missing_sub:
            warnings.append(f"Element 3: resources missing substitutable flag: {missing_sub[:8]}")

    if not any(v.get("estimated_days") is not None for v in versions):
        warnings.append("Element 4 (Time & Cost): no version has verified schedule days")
    if not (contract.get("parameter_domains") or {}):
        issues.append("Element 5 (Parameter Ranges): missing finite parameter_domains with bounds")

    for v in versions:
        sl = str(v.get("stop_loss", ""))
        fb = str(v.get("fallback_path", ""))
        for pat in BOILERPLATE_PATTERNS:
            if pat.search(sl) or pat.search(fb):
                issues.append(f"Element 6 (Risks): generic boilerplate detected in {v.get('version')} stop_loss/fallback")

    if not plan.get("confirmation_items"):
        issues.append("Missing confirmation_items section (safety, ethics, booking, expert sign-off)")
    if not str(plan.get("prospective_boundary_notice", "")).strip():
        issues.append("Missing prospective_boundary_notice distinguishing planning scenarios from actual runs")

    raw_text = json.dumps(plan, ensure_ascii=False)
    for pat in EXECUTED_CLAIM_PATTERNS:
        if pat.search(raw_text):
            warnings.append(f"Possible planning-as-executed phrase matched: {pat.pattern}")

    elements_covered = {
        "1_hypothesis_variables": bool(ctrl.get("iv") and ctrl.get("dv")),
        "2_controls_estimability": int(ctrl.get("control_count", 0)) >= 2 and not ctrl.get("unmatched_confounders"),
        "3_equipment_materials": bool(contract.get("resources") or case.get("equipment_materials")),
        "4_time_cost_arithmetic": bool(versions),
        "5_parameter_domains": bool(contract.get("parameter_domains")),
        "6_risks_stop_loss_fallback": all(bool(v.get("stop_loss")) and bool(v.get("fallback_path")) for v in versions) if versions else False,
    }

    return {
        "$schema": "plan-report-verification/v1",
        "ok": len(issues) == 0,
        "case_id": plan.get("case_id", "unknown"),
        "version_count": len(versions),
        "elements_covered": elements_covered,
        "issues": issues,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify 6-element completeness and anti-boilerplate compliance of an optimized plan.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))
    case = json.loads(Path(args.case).read_text(encoding="utf-8-sig"))
    rep = verify_plan_report(plan, case)
    txt = json.dumps(rep, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt, encoding="utf-8")
    print(txt, end="")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
