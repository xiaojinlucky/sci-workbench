#!/usr/bin/env python3
"""Seven-category experimental feasibility auditor with strict MISSING_INFO vs FAIL separation and actionable conditions."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from plan_arithmetic_verifier import evaluate_plan_arithmetic

VALID_RESOURCE_STATES = {"available", "outsourceable", "needs_procurement", "unavailable"}


def _check_sop_handover(plan: dict[str, Any]) -> dict[str, Any]:
    sop_vers: dict[str, set[str]] = defaultdict(set)
    unpinned: list[str] = []
    for idx, st in enumerate(plan.get("steps") or [], start=1):
        sid = str(st.get("sop_id", "")).strip()
        sver = str(st.get("sop_version", "")).strip()
        if sid:
            if not sver:
                unpinned.append(f"steps[{idx}]:{sid}")
            else:
                sop_vers[sid].add(sver)
    conflicts = [{"sop_id": k, "versions": sorted(v)} for k, v in sorted(sop_vers.items()) if len(v) > 1]
    return {"conflicts": conflicts, "unpinned": unpinned, "ok": not conflicts and not unpinned}


def _audit_single_plan(plan: dict[str, Any], arith: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    ver = str(plan.get("version") or arith.get("version") or "unknown")
    src = plan.get("source_ref", "")
    items: list[dict[str, Any]] = []
    conditions: list[dict[str, Any]] = []
    missing_info: list[str] = []
    hard_fails: list[str] = []

    # Category 1: Hypothesis, operationalized variables & inference unit
    hyp = str(plan.get("hypothesis", "")).strip()
    vars_obj = plan.get("variables") or {}
    if hyp and vars_obj.get("iv") and vars_obj.get("dv"):
        unit_note = str(vars_obj.get("dv_unit") or "").strip()
        inf_unit = str(plan.get("inference_unit") or "").strip()
        if not unit_note:
            conditions.append({"category": "1_hypothesis_variables", "owner": "PI", "condition": "Specify DV measurement unit and readout assay", "source_ref": src})
            st1 = "CONDITIONAL"
        elif not inf_unit:
            conditions.append({"category": "1_hypothesis_variables", "owner": "PI/Biostat", "condition": "Declare independent inference_unit distinct from technical replicates", "source_ref": src})
            st1 = "CONDITIONAL"
        else:
            st1 = "PASS"
        items.append({"category": "1_hypothesis_variables", "status": st1, "source_ref": src, "detail": f"IV={vars_obj['iv']}, DV={vars_obj['dv']} ({unit_note or 'unit_missing'}), unit={inf_unit or 'unspecified'}"})
    else:
        missing_info.append("Category 1: operationalized IV/DV or testable hypothesis missing")
        items.append({"category": "1_hypothesis_variables", "status": "MISSING_INFO", "source_ref": src, "detail": "Incomplete hypothesis or IV/DV operationalization"})

    # Category 2: Control arms & estimability
    raw_ctrls = plan.get("controls") or []
    ctrls = [c if isinstance(c, dict) else {"name": str(c)} for c in raw_ctrls if str(c).strip()]
    if len(ctrls) >= 2:
        items.append({"category": "2_controls", "status": "PASS", "source_ref": src, "detail": f"{len(ctrls)} control arms declared"})
    elif len(ctrls) == 1:
        conditions.append({"category": "2_controls", "owner": "PI", "condition": f"Add complementary positive/blank control beyond single {ctrls[0].get('name')!r} arm", "source_ref": src})
        items.append({"category": "2_controls", "status": "CONDITIONAL", "source_ref": src, "detail": f"Single control arm only: {ctrls[0].get('name')}"})
    else:
        hard_fails.append("Category 2: zero experimental controls specified")
        items.append({"category": "2_controls", "status": "FAIL", "source_ref": src, "detail": "No controls declared"})

    # Category 3: Equipment, materials, 4-state status, capability & quote validity
    resources = arith.get("resource_breakdown", [])
    res_states = [r.get("status") for r in resources]
    unverified_res = sorted(
        {str(r["resource"]) for r in resources if r.get("status") not in VALID_RESOURCE_STATES}
        | set(arith.get("missing_resources", []))
    )
    cap_fails = [c for c in arith.get("capability_issues", []) if c["severity"] == "fail"]
    cap_missing = [c for c in arith.get("capability_issues", []) if c["severity"] == "missing_info"]
    quote_missing = [q for q in arith.get("quote_issues", []) if q["severity"] == "missing_info"]
    quote_cond = [q for q in arith.get("quote_issues", []) if q["severity"] == "conditional"]

    if arith.get("lead_or_availability_blocks") or cap_fails:
        if arith.get("lead_or_availability_blocks"):
            hard_fails.append(f"Category 3: resource unavailable or lead-time block: {arith['lead_or_availability_blocks']}")
        if cap_fails:
            hard_fails.append(f"Category 3: equipment capability/maintenance failure: {cap_fails}")
        st3 = "FAIL"
    elif unverified_res or cap_missing or quote_missing:
        if unverified_res:
            missing_info.append(f"Category 3: resource availability status unverified: {unverified_res}")
        if cap_missing:
            missing_info.append(f"Category 3: equipment precision/maintenance unverified: {cap_missing}")
        if quote_missing:
            missing_info.append(f"Category 3: procurement/outsource quote date missing: {quote_missing}")
        st3 = "MISSING_INFO"
    elif any(s in {"needs_procurement", "outsourceable"} for s in res_states) or quote_cond:
        for r in resources:
            if r["status"] in {"needs_procurement", "outsourceable"}:
                conditions.append({
                    "category": "3_equipment_materials",
                    "owner": "Lab Manager / Procurement",
                    "condition": f"Confirm prospective {r['status']} lead ({r.get('effective_days')}d) and availability for {r['resource']}",
                    "lead_days": r.get("effective_days"),
                    "source_ref": r.get("source_ref", src),
                })
        for q in quote_cond:
            conditions.append({"category": "3_equipment_materials", "owner": "Procurement", "condition": f"Refresh quote for {q['resource']}: {q['reason']}", "source_ref": src})
        st3 = "CONDITIONAL"
    else:
        st3 = "PASS"
    items.append({"category": "3_equipment_materials", "status": st3, "source_ref": src, "detail": {"resources": resources, "capability_issues": arith.get("capability_issues", []), "quote_issues": arith.get("quote_issues", [])}})

    # Category 4: Time, budget, sample pool, stability & internal arithmetic consistency
    cost_obj = arith["cost_usd"]
    sched_obj = arith["schedule_days"]
    samp_obj = arith["samples"]
    c_ok = cost_obj["within_budget"]
    d_ok = sched_obj["within_deadline"]
    s_ok = samp_obj["within_pool"]
    stab_conflict = arith.get("stability_conflict")
    contradictions = arith.get("internal_contradictions") or []

    if c_ok is False or d_ok is False or s_ok is False or stab_conflict is not None or sched_obj.get("cycle_detected"):
        if c_ok is False:
            hard_fails.append(f"Category 4: computed cost ${cost_obj['total']} > budget_cap ${cost_obj['budget_cap']}")
        if d_ok is False:
            hard_fails.append(f"Category 4: critical path {sched_obj['critical_path_days']}d > deadline {sched_obj['deadline_days']}d")
        if s_ok is False:
            hard_fails.append(f"Category 4: total samples {samp_obj['total']} > available pool {samp_obj['available']}")
        if stab_conflict is not None:
            hard_fails.append(f"Category 4: sample stability violation — {stab_conflict['reason']}")
        if sched_obj.get("cycle_detected"):
            hard_fails.append("Category 4: circular dependency in step schedule")
        st4 = "FAIL"
    elif (not cost_obj["verifiable"]) or sched_obj.get("missing_schedule_inputs") or (None in (c_ok, d_ok, s_ok)):
        if cost_obj.get("missing_cost_fields"):
            missing_info.append(f"Category 4: unverified cost fields (not coerced to 0): {cost_obj['missing_cost_fields']}")
        if sched_obj.get("missing_schedule_inputs"):
            missing_info.append(f"Category 4: missing schedule/lead inputs: {sched_obj['missing_schedule_inputs']}")
        if cost_obj["budget_cap"] is None or sched_obj["deadline_days"] is None or samp_obj["available"] is None:
            missing_info.append("Category 4: contract budget_cap, deadline_days, or sample_pool_available partially unspecified")
        st4 = "MISSING_INFO"
    elif contradictions:
        conditions.append({"category": "4_time_budget_samples", "owner": "Protocol Author", "condition": f"Reconcile internal arithmetic mismatch: {'; '.join(contradictions)}", "source_ref": src})
        st4 = "CONDITIONAL"
    else:
        st4 = "PASS"
    items.append({"category": "4_time_budget_samples", "status": st4, "source_ref": src, "detail": {"cost": cost_obj, "schedule": sched_obj, "samples": samp_obj, "stability_conflict": stab_conflict, "internal_contradictions": contradictions}})

    # Category 5: Finite parameter domains & safety ceilings
    if arith.get("domain_violations"):
        hard_fails.append(f"Category 5: parameter domain/safety violation: {arith['domain_violations']}")
        st5 = "FAIL"
    elif arith.get("domain_missing"):
        missing_info.append(f"Category 5: missing parameter values against domain bounds: {arith['domain_missing']}")
        st5 = "MISSING_INFO"
    else:
        st5 = "PASS"
    items.append({"category": "5_parameter_domains", "status": st5, "source_ref": src, "violations": arith.get("domain_violations", []), "missing": arith.get("domain_missing", [])})

    # Category 6: Risks, quantitative triggers & fallback routes
    risks = plan.get("risks") or []
    if risks and all(str(r.get("trigger", "")).strip() and str(r.get("mitigation", "")).strip() for r in risks):
        st6 = "PASS"
    elif risks:
        conditions.append({"category": "6_risks_fallbacks", "owner": "PI / Safety Lead", "condition": "Complete quantitative failure triggers and fallback/escalation paths for all declared risks", "source_ref": src})
        st6 = "CONDITIONAL"
    else:
        missing_info.append("Category 6: no failure triggers or fallback paths declared")
        st6 = "MISSING_INFO"
    items.append({"category": "6_risks_fallbacks", "status": st6, "source_ref": src, "risk_count": len(risks)})

    # Category 7: Ethics, biosafety, hazardous reagent & data compliance (explicitly separates in_review vs denied vs missing)
    plan_comp = plan.get("compliance") or {}
    bio_ok = plan_comp.get("biosafety_approved", contract.get("biosafety_approved"))
    eth_req = plan_comp.get("ethics_approval_required", contract.get("ethics_approval_required"))
    eth_ok = plan_comp.get("ethics_approved", contract.get("ethics_approved"))
    haz_req = plan_comp.get("hazardous_reagent_permit_required", contract.get("hazardous_reagent_permit_required"))
    haz_ok = plan_comp.get("hazardous_reagent_approved", contract.get("hazardous_reagent_approved"))

    in_review_tokens = {"in_review", "pending", "pending_amendment", "submitted", "under_review"}
    denied_tokens = {"denied", "rejected", "revoked", "failed"}

    def _is_denied(v: Any) -> bool:
        return v is False or (isinstance(v, str) and v.strip().lower() in denied_tokens)

    def _is_in_review(v: Any) -> bool:
        return isinstance(v, str) and v.strip().lower() in in_review_tokens

    in_review_items: list[str] = []
    if _is_in_review(bio_ok):
        in_review_items.append(f"biosafety={bio_ok}")
    if eth_req is True and _is_in_review(eth_ok):
        in_review_items.append(f"ethics={eth_ok}")
    if haz_req is True and _is_in_review(haz_ok):
        in_review_items.append(f"hazardous_reagent={haz_ok}")

    if _is_denied(bio_ok) or (eth_req is True and _is_denied(eth_ok)) or (haz_req is True and _is_denied(haz_ok)):
        hard_fails.append("Category 7: required biosafety, ethics (IRB/IACUC), or hazardous-reagent approval is denied/unapproved")
        st7 = "FAIL"
    elif in_review_items:
        missing_info.append(f"Category 7: approval currently pending review ({', '.join(in_review_items)}) — cannot start until written approval is granted")
        conditions.append({
            "category": "7_ethics_safety",
            "owner": "PI / IRB-IACUC-EHS Office",
            "condition": f"Obtain final written clearance for pending review item(s): {', '.join(in_review_items)}",
            "source_ref": contract.get("compliance_source_ref", src),
        })
        st7 = "MISSING_INFO"
    elif (
        bio_ok is not True
        or not isinstance(eth_req, bool)
        or (eth_req and eth_ok is not True)
        or (haz_req is True and haz_ok is not True)
    ):
        missing_info.append("Category 7: biosafety/ethics/hazardous-reagent requirement or approval status is unverified (cannot pass by omission)")
        st7 = "MISSING_INFO"
    else:
        st7 = "PASS"
    items.append({
        "category": "7_ethics_safety",
        "status": st7,
        "source_ref": contract.get("compliance_source_ref", src),
        "biosafety_approved": bio_ok,
        "ethics_required": eth_req,
        "ethics_approved": eth_ok,
        "hazardous_reagent_required": haz_req,
        "hazardous_reagent_approved": haz_ok,
        "pending_review_items": in_review_items,
    })

    # Advisory handover check: SOP version pinning
    sop_chk = _check_sop_handover(plan)
    if sop_chk["conflicts"]:
        conditions.append({"category": "handover_sop_pin", "owner": "QA / PI", "condition": f"Resolve conflicting SOP versions: {sop_chk['conflicts']}", "source_ref": src})

    if hard_fails:
        overall = "INFEASIBLE"
    elif missing_info:
        overall = "REQUIRES_INFO_OR_CONFIRMATION"
    elif conditions:
        overall = "CONDITIONAL_FEASIBLE"
    else:
        overall = "FEASIBLE_UNDER_PROSPECTIVE_CONTRACT"

    return {
        "version": ver,
        "source_ref": src,
        "overall_verdict": overall,
        "hard_failures": hard_fails,
        "conditions_to_proceed": conditions,
        "missing_information": missing_info,
        "sop_handover_check": sop_chk,
        "checklist_items": items,
    }


def review_case(case: dict[str, Any], arith_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run full 7-category review across all versioned candidate plans in a case."""
    if arith_report is None:
        arith_report = evaluate_plan_arithmetic(case)
    contract = case.get("prospective_contract") or {}
    plans = case.get("candidate_plans") or []
    arith_by_ver = {p["version"]: p for p in arith_report.get("plans", [])}

    reviews: list[dict[str, Any]] = []
    for idx, p in enumerate(plans, start=1):
        ver = str(p.get("version") or f"v{idx}")
        reviews.append(_audit_single_plan(p, arith_by_ver.get(ver, {}), contract))

    # Version progression summary
    verdict_delta = [
        {
            "transition": f"{reviews[i-1]['version']} -> {reviews[i]['version']}",
            "from_verdict": reviews[i - 1]["overall_verdict"],
            "to_verdict": reviews[i]["overall_verdict"],
            "hard_failures_delta": len(reviews[i]["hard_failures"]) - len(reviews[i - 1]["hard_failures"]),
            "missing_info_delta": len(reviews[i]["missing_information"]) - len(reviews[i - 1]["missing_information"]),
        }
        for i in range(1, len(reviews))
    ]

    return {
        "$schema": "feasibility-review-report/v2",
        "case_id": case.get("case_id", "unknown"),
        "title": case.get("title", ""),
        "prospective_boundary_notice": (
            "All feasibility verdicts evaluate organizer prospective contract scenarios only; they do NOT constitute "
            "live lab instrument reservations, confirmed vendor quotes, or granted IRB/IACUC/biosafety approvals."
        ),
        "plan_reviews": reviews,
        "version_progression": verdict_delta,
        "arithmetic": arith_report,
    }


def _render_markdown(rep: dict[str, Any]) -> str:
    lines = [
        f"# Feasibility Review: {rep['title']} ({rep['case_id']})",
        "",
        f"> **Prospective Boundary Notice**: {rep['prospective_boundary_notice']}",
        "",
        "## Version Verdict Summary",
        "",
        "| Plan Version | Verdict | Hard Failures | Missing Info | Actionable Conditions |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rep["plan_reviews"]:
        cond_txt = "; ".join(f"[{c['owner']}] {c['condition']}" for c in r["conditions_to_proceed"]) or "None"
        miss_txt = "; ".join(r["missing_information"]) or "None"
        lines.append(f"| {r['version']} | `{r['overall_verdict']}` | {len(r['hard_failures'])} | {miss_txt} | {cond_txt} |")

    for r in rep["plan_reviews"]:
        lines.extend(["", f"### Detailed 7-Category Audit: `{r['version']}` ({r['overall_verdict']})", ""])
        lines.append("| Category | Status | Source Ref |")
        lines.append("| --- | --- | --- |")
        for it in r["checklist_items"]:
            lines.append(f"| {it['category']} | `{it['status']}` | {it.get('source_ref', '')} |")
        if r["hard_failures"]:
            lines.append("")
            lines.append("**Hard Failures:**")
            for hf in r["hard_failures"]:
                lines.append(f"- {hf}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit versioned experimental plans across 7 feasibility categories.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--arith", default=None)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stem", default="feasibility_review")
    args = parser.parse_args(argv)

    case = json.loads(Path(args.case).read_text(encoding="utf-8-sig"))
    arith = json.loads(Path(args.arith).read_text(encoding="utf-8-sig")) if args.arith else None
    rep = review_case(case, arith_report=arith)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_p = out_dir / f"{args.stem}.json"
    md_p = out_dir / f"{args.stem}.md"
    json_p.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_p.write_text(_render_markdown(rep), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "json": str(json_p),
        "md": str(md_p),
        "verdicts": {r["version"]: r["overall_verdict"] for r in rep["plan_reviews"]},
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
