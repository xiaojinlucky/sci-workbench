#!/usr/bin/env python3
"""Strict null-vs-zero sample, DAG critical-path, stability, capability, and contradiction verifier."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


def _finite_nonneg(val: Any) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < 0:
        return None
    return f


def _finite_num(val: Any) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse_iso_date(text: Any) -> dt.date | None:
    if not text or not isinstance(text, str):
        return None
    try:
        return dt.date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def _compute_schedule(
    steps: list[dict[str, Any]],
    resources: dict[str, dict[str, Any]],
    *,
    prep_days: float,
    qc_days: float,
    deadline_days: float | None,
) -> dict[str, Any]:
    """Compute critical-path makespan via DAG (if depends_on present) or parallel_group buckets."""
    missing_sched: list[str] = []
    lead_blocks: list[dict[str, Any]] = []
    uses_dag = any(isinstance(st.get("depends_on"), list) and len(st.get("depends_on") or []) > 0 for st in steps)

    if not steps:
        return {
            "mode": "empty",
            "critical_path_days": round(prep_days + qc_days, 2),
            "critical_path_steps": [],
            "missing_schedule_inputs": [],
            "lead_blocks": [],
            "cycle_detected": False,
        }

    if uses_dag:
        by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for idx, st in enumerate(steps, start=1):
            sid = str(st.get("id") or st.get("name") or f"step_{idx}").strip()
            by_id[sid] = st
            order.append(sid)
        indeg = {sid: 0 for sid in order}
        children: dict[str, list[str]] = {sid: [] for sid in order}
        for sid in order:
            for d in by_id[sid].get("depends_on") or []:
                ds = str(d).strip()
                if ds in by_id:
                    indeg[sid] += 1
                    children[ds].append(sid)
                elif ds:
                    missing_sched.append(f"unknown_dependency:{sid}->{ds}")

        q = deque([sid for sid in order if indeg[sid] == 0])
        topo: list[str] = []
        while q:
            u = q.popleft()
            topo.append(u)
            for v in children[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    q.append(v)

        if len(topo) != len(order):
            return {
                "mode": "dag",
                "critical_path_days": None,
                "critical_path_steps": [],
                "missing_schedule_inputs": ["DAG_CYCLE_DETECTED"],
                "lead_blocks": [{"resource": "schedule", "reason": "Circular step dependency"}],
                "cycle_detected": True,
            }

        ef: dict[str, float] = {}
        pred: dict[str, str | None] = {}
        for sid in topo:
            st = by_id[sid]
            dur = _finite_nonneg(st.get("duration_days"))
            if dur is None:
                missing_sched.append(f"{sid}.duration_days")
                dur_eff = 0.0
            else:
                dur_eff = dur

            rid = str(st.get("resource", "")).strip()
            rm = resources.get(rid) if rid else None
            lead_wait = 0.0
            if rid and rm is None:
                missing_sched.append(f"resource:{rid}")
            elif rm is not None:
                status = str(rm.get("status", "unknown")).lower()
                bl = _finite_nonneg(rm.get("booking_lead_days"))
                pl = _finite_nonneg(rm.get("lead_days"))
                if status in {"needs_procurement", "outsourceable"} and pl is None:
                    missing_sched.append(f"{rid}.lead_days")
                lead_wait = max(bl or 0.0, pl or 0.0)
                if status == "unavailable":
                    lead_blocks.append({"resource": rid, "reason": "status=unavailable"})
                elif deadline_days is not None and (lead_wait + dur_eff) > deadline_days + 1e-9:
                    lead_blocks.append({"resource": rid, "reason": f"lead+duration {round(lead_wait + dur_eff, 2)}d > deadline {deadline_days}d"})

            deps = [str(d).strip() for d in (st.get("depends_on") or []) if str(d).strip() in ef]
            if deps:
                best_dep = max(deps, key=lambda d: ef[d])
                start_d = max(ef[best_dep], lead_wait)
                pred[sid] = best_dep
            else:
                start_d = max(prep_days, lead_wait)
                pred[sid] = None
            ef[sid] = round(start_d + dur_eff, 3)

        last = max(topo, key=lambda s: ef[s])
        crit: list[str] = []
        cur: str | None = last
        while cur is not None:
            crit.append(cur)
            cur = pred.get(cur)
        crit.reverse()
        makespan = None if missing_sched else round(ef[last] + qc_days, 2)
        return {
            "mode": "dag",
            "critical_path_days": makespan,
            "critical_path_steps": crit,
            "missing_schedule_inputs": sorted(set(missing_sched)),
            "lead_blocks": lead_blocks,
            "cycle_detected": False,
        }

    # Parallel group bucket mode (backward compatible)
    buckets: dict[str, float] = defaultdict(float)
    for idx, st in enumerate(steps, start=1):
        sname = str(st.get("name") or st.get("id") or f"step_{idx}")
        pgroup = str(st.get("parallel_group") or sname)
        dur = _finite_nonneg(st.get("duration_days"))
        if dur is None:
            missing_sched.append(f"{sname}.duration_days")
            dur_eff = 0.0
        else:
            dur_eff = dur

        rid = str(st.get("resource", "")).strip()
        rm = resources.get(rid) if rid else None
        lead_wait = 0.0
        if rid and rm is None:
            missing_sched.append(f"resource:{rid}")
        elif rm is not None:
            status = str(rm.get("status", "unknown")).lower()
            bl = _finite_nonneg(rm.get("booking_lead_days"))
            pl = _finite_nonneg(rm.get("lead_days"))
            if status in {"needs_procurement", "outsourceable"} and pl is None:
                missing_sched.append(f"{rid}.lead_days")
            lead_wait = max(bl or 0.0, pl or 0.0)
            if status == "unavailable":
                lead_blocks.append({"resource": rid, "reason": "status=unavailable"})
            elif deadline_days is not None and max(dur_eff, lead_wait) > deadline_days + 1e-9:
                lead_blocks.append({"resource": rid, "reason": f"effective_wait {max(dur_eff, lead_wait)}d > deadline {deadline_days}d"})

        buckets[pgroup] = max(buckets[pgroup], max(dur_eff, lead_wait))

    makespan = None if missing_sched else round(prep_days + sum(buckets.values()) + qc_days, 2)
    return {
        "mode": "parallel_groups",
        "parallel_group_days": dict(buckets),
        "critical_path_days": makespan,
        "critical_path_steps": list(buckets.keys()),
        "missing_schedule_inputs": sorted(set(missing_sched)),
        "lead_blocks": lead_blocks,
        "cycle_detected": False,
    }


def evaluate_plan_arithmetic(case: dict[str, Any]) -> dict[str, Any]:
    """Verify strict null-vs-zero samples, cost, DAG schedule, capability, stability, and internal consistency."""
    contract = case.get("prospective_contract") or {}
    resources = contract.get("resources") or {}
    domains = contract.get("parameter_domains") or {}
    budget_cap = _finite_nonneg(contract.get("budget_cap_usd"))
    deadline_days = _finite_nonneg(contract.get("deadline_days"))
    sample_pool = int(contract["sample_pool_available"]) if contract.get("sample_pool_available") is not None else None
    eval_date = _parse_iso_date(contract.get("evaluation_date"))
    approval_lead_days = _finite_nonneg(contract.get("approval_lead_days"))
    sample_stability_days = _finite_nonneg(contract.get("sample_stability_days"))
    labor_rate = _finite_nonneg(contract.get("labor_rate_per_day"))

    plan_reports: list[dict[str, Any]] = []
    for idx, plan in enumerate(case.get("candidate_plans") or [], start=1):
        ver = str(plan.get("version") or f"v{idx}")
        arms = int(plan.get("arms") or 0)
        bio_reps = int(plan.get("biological_replicates") or plan.get("replicates_per_arm") or 0)
        tech_reps_raw = plan.get("technical_replicates")
        tech_reps = int(tech_reps_raw) if tech_reps_raw is not None else 1
        qc_extra_raw = plan.get("qc_extra_samples")
        qc_extra = int(qc_extra_raw) if qc_extra_raw is not None else 0
        # Separate independent biological sample pool demand from total assay measurements (technical replicates + QC)
        biological_units_total = arms * bio_reps
        total_samples = biological_units_total * tech_reps + qc_extra

        missing_cost_fields: list[str] = []
        explicit_zero_fields: list[str] = []
        missing_resources: list[str] = []
        capability_issues: list[dict[str, Any]] = []
        quote_issues: list[dict[str, Any]] = []
        resource_breakdown: list[dict[str, Any]] = []

        # Strict null-vs-zero for one-time setup cost
        if "one_time_setup_cost" in plan:
            one_time = _finite_nonneg(plan.get("one_time_setup_cost"))
            if one_time is None:
                missing_cost_fields.append("one_time_setup_cost")
            elif one_time == 0.0:
                explicit_zero_fields.append("one_time_setup_cost")
        else:
            one_time = None
            missing_cost_fields.append("one_time_setup_cost")

        recurring_cost = 0.0
        max_proc_lead = 0.0
        for s_idx, step in enumerate(plan.get("steps") or [], start=1):
            sname = str(step.get("name") or step.get("id") or f"step_{s_idx}")
            rid = str(step.get("resource", "")).strip()
            rm = resources.get(rid) if rid else None

            if rid and rm is None:
                missing_resources.append(rid)
                missing_cost_fields.append(f"resource:{rid}")
                resource_breakdown.append({
                    "step": sname, "resource": rid, "status": "UNSPECIFIED", "cost_usd": None,
                })
                continue

            step_cost = 0.0
            step_cost_ok = True
            if rm is not None:
                status = str(rm.get("status", "unknown")).lower()
                pl = _finite_nonneg(rm.get("lead_days"))
                bl = _finite_nonneg(rm.get("booking_lead_days"))
                if pl is not None:
                    max_proc_lead = max(max_proc_lead, pl)
                if bl is not None and bl == 0.0:
                    explicit_zero_fields.append(f"{rid}.booking_lead_days")

                # Billing-mode aware strict null-vs-zero:
                # - Hourly resource with hours given does NOT require units.
                # - Per-unit resource with units given does NOT require hours.
                # - Missing both quantities (or missing required billable rate) flags missing_cost_fields.
                units = _finite_nonneg(step.get("units"))
                hours = _finite_nonneg(step.get("hours"))
                uc = _finite_nonneg(rm.get("unit_cost"))
                hr = _finite_nonneg(rm.get("rate_per_hr"))
                if units is None and hours is None:
                    missing_cost_fields.append(f"{sname}.units_or_hours")
                    step_cost_ok = False
                else:
                    if units is not None and units > 0:
                        if uc is None:
                            missing_cost_fields.append(f"{rid}.unit_cost")
                            step_cost_ok = False
                        else:
                            if uc == 0.0:
                                explicit_zero_fields.append(f"{rid}.unit_cost")
                            step_cost += units * uc
                    if hours is not None and hours > 0:
                        if hr is None:
                            missing_cost_fields.append(f"{rid}.rate_per_hr")
                            step_cost_ok = False
                        else:
                            if hr == 0.0:
                                explicit_zero_fields.append(f"{rid}.rate_per_hr")
                            step_cost += hours * hr
                    if (units == 0.0) and (hours is None or hours == 0.0):
                        explicit_zero_fields.append(f"{sname}.units")

                # Equipment capability / precision / maintenance checks
                req_prec = _finite_nonneg(step.get("required_precision"))
                spec_prec = _finite_nonneg(rm.get("spec_precision"))
                if req_prec is not None:
                    if spec_prec is None:
                        capability_issues.append({"resource": rid, "step": sname, "severity": "missing_info", "reason": "instrument spec_precision unspecified"})
                    elif spec_prec > req_prec + 1e-12:
                        capability_issues.append({"resource": rid, "step": sname, "severity": "fail", "reason": f"instrument precision {spec_prec} worse than required {req_prec}"})

                maint = str(rm.get("maintenance_status", "")).lower().strip()
                if maint in {"overdue", "failed", "down"}:
                    capability_issues.append({"resource": rid, "step": sname, "severity": "fail", "reason": f"maintenance_status={maint}"})
                elif maint in {"unverified", "unknown"}:
                    capability_issues.append({"resource": rid, "step": sname, "severity": "missing_info", "reason": f"maintenance_status={maint}"})

                # Quote expiry / price date check for procurement/outsource
                if status in {"needs_procurement", "outsourceable"}:
                    q_exp = _parse_iso_date(rm.get("quote_valid_until"))
                    p_date = _parse_iso_date(rm.get("price_date"))
                    if q_exp is None and p_date is None:
                        quote_issues.append({"resource": rid, "severity": "missing_info", "reason": "missing quote_valid_until / price_date"})
                    elif eval_date is not None and q_exp is not None and q_exp < eval_date:
                        quote_issues.append({"resource": rid, "severity": "conditional", "reason": f"quote expired on {q_exp.isoformat()} (eval {eval_date.isoformat()})"})

                resource_breakdown.append({
                    "step": sname,
                    "resource": rid,
                    "status": status,
                    "effective_days": max(_finite_nonneg(step.get("duration_days")) or 0.0, bl or 0.0, pl or 0.0),
                    "cost_usd": round(step_cost, 2) if step_cost_ok else None,
                    "source_ref": rm.get("source_ref", ""),
                })
                if step_cost_ok:
                    recurring_cost += step_cost

        # Optional labor cost check
        labor_cost = 0.0
        if "labor_days" in plan and plan.get("labor_days") is not None:
            ld = _finite_nonneg(plan.get("labor_days"))
            if ld is None or labor_rate is None:
                missing_cost_fields.append("labor_cost_or_rate")
            else:
                labor_cost = ld * labor_rate

        cost_verifiable = len(missing_cost_fields) == 0
        total_cost = round((one_time or 0.0) + recurring_cost + labor_cost, 2) if cost_verifiable else None

        prep_d = _finite_nonneg(plan.get("prep_days", contract.get("base_prep_days", 0.0))) or 0.0
        qc_d = _finite_nonneg(plan.get("qc_days", contract.get("qc_days", 0.0))) or 0.0
        sched = _compute_schedule(plan.get("steps") or [], resources, prep_days=prep_d, qc_days=qc_d, deadline_days=deadline_days)
        critical_days = sched["critical_path_days"]

        # Sample stability vs approval + procurement wait cross-check
        stability_conflict = None
        if sample_stability_days is not None:
            pre_run_wait = (approval_lead_days or 0.0) + max_proc_lead
            if pre_run_wait > sample_stability_days + 1e-9:
                stability_conflict = {
                    "sample_stability_days": sample_stability_days,
                    "pre_run_wait_days": round(pre_run_wait, 2),
                    "reason": f"pre-run wait ({round(pre_run_wait, 2)}d = approval+procurement) exceeds sample_stability_days ({sample_stability_days}d)",
                }

        # Internal plan contradiction check (stated prose numbers vs computed arithmetic)
        contradictions: list[str] = []
        if plan.get("declared_total_samples") is not None and int(plan["declared_total_samples"]) != total_samples:
            contradictions.append(f"declared_total_samples={plan['declared_total_samples']} != computed {total_samples}")
        if total_cost is not None and plan.get("declared_total_cost_usd") is not None:
            if abs(float(plan["declared_total_cost_usd"]) - total_cost) > max(1.0, 0.02 * total_cost):
                contradictions.append(f"declared_total_cost_usd={plan['declared_total_cost_usd']} != computed {total_cost}")
        if critical_days is not None and plan.get("declared_duration_days") is not None:
            if abs(float(plan["declared_duration_days"]) - critical_days) > 0.5:
                contradictions.append(f"declared_duration_days={plan['declared_duration_days']} != computed {critical_days}")

        # Parameter domain checks (including safety_max & step)
        domain_violations: list[dict[str, Any]] = []
        domain_missing: list[str] = []
        plan_params = plan.get("parameters") or {}
        for pname, bounds in domains.items():
            if pname not in plan_params or plan_params[pname] is None:
                domain_missing.append(pname)
                continue
            val = plan_params[pname]
            if "allowed_values" in bounds:
                if val not in bounds["allowed_values"]:
                    domain_violations.append({"parameter": pname, "value": val, "allowed_values": bounds["allowed_values"], "source_ref": bounds.get("source_ref", "")})
                continue
            fval = _finite_num(val)
            if fval is None:
                domain_violations.append({"parameter": pname, "value": val, "reason": "non-numeric", "source_ref": bounds.get("source_ref", "")})
                continue
            lo = _finite_num(bounds.get("min"))
            hi = _finite_num(bounds.get("max"))
            smax = _finite_num(bounds.get("safety_max"))
            eff_hi = min(hi, smax) if (hi is not None and smax is not None) else (smax if smax is not None else hi)
            if (lo is not None and fval < lo - 1e-9) or (eff_hi is not None and fval > eff_hi + 1e-9):
                domain_violations.append({
                    "parameter": pname, "value": fval, "min": lo, "effective_max": eff_hi,
                    "unit": bounds.get("unit", ""), "source_ref": bounds.get("source_ref", ""),
                })
            step = _finite_num(bounds.get("step"))
            if step and step > 0 and lo is not None:
                rem = abs((fval - lo) / step - round((fval - lo) / step))
                if rem > 1e-4:
                    domain_violations.append({
                        "parameter": pname, "value": fval, "reason": f"violates step={step} from min={lo}", "source_ref": bounds.get("source_ref", ""),
                    })

        budget_ok = None if (budget_cap is None or total_cost is None) else (total_cost <= budget_cap + 1e-9)
        deadline_ok = None if (deadline_days is None or critical_days is None) else (critical_days <= deadline_days + 1e-9)
        # Biological sample pool constrains independent biological units (not technical replicate injections)
        sample_ok = None if sample_pool is None else (biological_units_total <= sample_pool)

        plan_reports.append({
            "version": ver,
            "source_doc": plan.get("source_doc", ""),
            "source_ref": plan.get("source_ref", ""),
            "samples": {
                "arms": arms,
                "biological_replicates": bio_reps,
                "biological_units_total": biological_units_total,
                "technical_replicates_declared": tech_reps_raw,
                "qc_extra_declared": qc_extra_raw,
                "total_assay_measurements": total_samples,
                "technical_replicates": tech_reps,
                "qc_extra": qc_extra,
                "total": total_samples,
                "available": sample_pool,
                "within_pool": sample_ok,
            },
            "cost_usd": {
                "verifiable": cost_verifiable,
                "one_time": round(one_time, 2) if one_time is not None else None,
                "recurring": round(recurring_cost, 2) if cost_verifiable else None,
                "labor": round(labor_cost, 2) if cost_verifiable else None,
                "total": total_cost,
                "budget_cap": budget_cap,
                "within_budget": budget_ok,
                "missing_cost_fields": sorted(set(missing_cost_fields)),
                "explicit_zero_fields": sorted(set(explicit_zero_fields)),
            },
            "schedule_days": {
                **sched,
                "deadline_days": deadline_days,
                "within_deadline": deadline_ok,
            },
            "stability_conflict": stability_conflict,
            "internal_contradictions": contradictions,
            "capability_issues": capability_issues,
            "quote_issues": quote_issues,
            "domain_violations": domain_violations,
            "domain_missing": domain_missing,
            "missing_resources": sorted(set(missing_resources)),
            "lead_or_availability_blocks": sched["lead_blocks"],
            "resource_breakdown": resource_breakdown,
        })

    return {
        "$schema": "feasibility-arithmetic-report/v2",
        "case_id": case.get("case_id", "unknown"),
        "prospective_notice": "Planning arithmetic estimate against organizer contract; NOT an executed lab reservation, vendor quote, or measured outcome.",
        "plan_count": len(plan_reports),
        "plans": plan_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify strict null-vs-zero feasibility arithmetic across candidate plans.")
    parser.add_argument("--case", required=True, help="Parsed case JSON")
    parser.add_argument("--out", default=None, help="Output arithmetic report JSON")
    args = parser.parse_args(argv)

    case = json.loads(Path(args.case).read_text(encoding="utf-8-sig"))
    rep = evaluate_plan_arithmetic(case)
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
