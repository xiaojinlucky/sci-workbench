#!/usr/bin/env python3
"""Unified horizontal field normalizer with strict null-vs-zero cost/approval firewall, DAG schedule, and comparability audit."""

from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path
from typing import Any


def _finite_nonneg(val: Any) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if (math.isfinite(f) and f >= 0) else None


def _finite_num(val: Any) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _compute_step_span(
    steps: list[dict[str, Any]],
    resources: dict[str, dict[str, Any]],
) -> tuple[float | None, list[str], list[str], bool]:
    """Compute step span via DAG (if depends_on used) or max concurrent span, preserving missing lead errors."""
    if not steps:
        return 0.0, [], [], False

    missing: list[str] = []
    hard_viols: list[str] = []
    uses_dag = any(isinstance(s.get("depends_on"), list) and len(s.get("depends_on") or []) > 0 for s in steps)

    if uses_dag:
        by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for idx, s in enumerate(steps, start=1):
            sid = str(s.get("id") or s.get("name") or f"step_{idx}").strip()
            by_id[sid] = s
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
                    missing.append(f"dependency:{sid}->{ds}")
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
            hard_viols.append("schedule DAG cycle detected")
            return None, missing, hard_viols, True

        ef: dict[str, float] = {}
        for sid in topo:
            st = by_id[sid]
            dur = _finite_nonneg(st.get("duration_days"))
            if dur is None:
                missing.append(f"{sid}.duration_days")
                dur_eff = 0.0
            else:
                dur_eff = dur
            rid = str(st.get("resource", "")).strip()
            rm = resources.get(rid) if rid else None
            lead = 0.0
            if rid and rm is None:
                missing.append(f"resource:{rid}")
            elif rm is not None:
                status = str(rm.get("status", "unknown")).lower()
                if status == "unavailable":
                    hard_viols.append(f"resource {rid} unavailable")
                pl = _finite_nonneg(rm.get("lead_days"))
                bl = _finite_nonneg(rm.get("booking_lead_days"))
                if status in {"needs_procurement", "outsourceable"} and pl is None:
                    missing.append(f"{rid}.lead_days")
                lead = max(pl or 0.0, bl or 0.0)
            deps = [str(d).strip() for d in (st.get("depends_on") or []) if str(d).strip() in ef]
            start = max(max((ef[d] for d in deps), default=0.0), lead)
            ef[sid] = start + dur_eff
        span = None if missing else round(max(ef.values(), default=0.0), 2)
        return span, missing, hard_viols, False

    max_span = 0.0
    for idx, st in enumerate(steps, start=1):
        sname = str(st.get("name") or st.get("id") or f"step_{idx}")
        dur = _finite_nonneg(st.get("duration_days"))
        if dur is None:
            missing.append(f"{sname}.duration_days")
            dur_eff = 0.0
        else:
            dur_eff = dur
        rid = str(st.get("resource", "")).strip()
        rm = resources.get(rid) if rid else None
        if rid and rm is None:
            missing.append(f"resource:{rid}")
            continue
        if rm is not None:
            status = str(rm.get("status", "unknown")).lower()
            if status == "unavailable":
                hard_viols.append(f"resource {rid} unavailable")
            pl = _finite_nonneg(rm.get("lead_days"))
            bl = _finite_nonneg(rm.get("booking_lead_days"))
            if status in {"needs_procurement", "outsourceable"} and pl is None:
                missing.append(f"{rid}.lead_days")
            max_span = max(max_span, dur_eff, pl or 0.0, bl or 0.0)
        else:
            max_span = max(max_span, dur_eff)
    return (None if missing else round(max_span, 2)), missing, hard_viols, False


def normalize_plans_matrix(case: dict[str, Any]) -> dict[str, Any]:
    """Normalize heterogeneous candidate plans onto aligned schema with strict null-vs-zero firewall."""
    contract = case.get("prospective_contract") or {}
    resources = contract.get("resources") or {}
    domains = contract.get("parameter_domains") or {}
    labor_rate = _finite_nonneg(contract.get("labor_rate_per_day"))
    rework_rate = _finite_nonneg(contract.get("rework_reserve_rate"))

    rows: list[dict[str, Any]] = []
    readouts_seen: set[str] = set()
    units_seen: set[str] = set()

    for idx, plan in enumerate(case.get("candidate_plans") or [], start=1):
        ver = str(plan.get("version") or f"v{idx}")
        missing_cost: list[str] = []
        missing_sched: list[str] = []
        missing_other: list[str] = []
        explicit_zeros: list[str] = []
        hard_violations: list[str] = []

        # Strict null-vs-zero for one-time setup cost
        if "one_time_setup_cost" in plan:
            one_time = _finite_nonneg(plan.get("one_time_setup_cost"))
            if one_time is None:
                missing_cost.append("one_time_setup_cost")
            elif one_time == 0.0:
                explicit_zeros.append("one_time_setup_cost")
        else:
            one_time = None
            missing_cost.append("one_time_setup_cost")

        # Strict null-vs-zero for labor, prep, qc
        if "labor_days" in plan:
            labor_days = _finite_nonneg(plan.get("labor_days"))
            if labor_days is None:
                missing_cost.append("labor_days")
            elif labor_days == 0.0:
                explicit_zeros.append("labor_days")
        else:
            labor_days = None
            missing_cost.append("labor_days")

        if labor_days is not None and labor_days > 0 and labor_rate is None:
            missing_cost.append("contract.labor_rate_per_day")

        prep_days = _finite_nonneg(plan.get("prep_days"))
        if prep_days is None:
            missing_sched.append("prep_days")
        elif prep_days == 0.0:
            explicit_zeros.append("prep_days")

        qc_days = _finite_nonneg(plan.get("qc_turnaround_days", plan.get("qc_days")))
        if qc_days is None:
            missing_sched.append("qc_turnaround_days")
        elif qc_days == 0.0:
            explicit_zeros.append("qc_turnaround_days")

        consumables_cost = 0.0
        machine_cost = 0.0
        steps = plan.get("steps") or []
        for s_idx, step in enumerate(steps, start=1):
            sname = str(step.get("name") or step.get("id") or f"step_{s_idx}")
            rid = str(step.get("resource", "")).strip()
            rm = resources.get(rid) if rid else None
            if rid and rm is None:
                missing_cost.append(f"resource:{rid}")
                continue
            if rm is not None:
                units = _finite_nonneg(step.get("units", 0.0))
                hours = _finite_nonneg(step.get("hours", 0.0))
                if units is None:
                    missing_cost.append(f"{sname}.units")
                elif units > 0:
                    uc = _finite_nonneg(rm.get("unit_cost"))
                    if uc is None:
                        missing_cost.append(f"{rid}.unit_cost")
                    else:
                        if uc == 0.0:
                            explicit_zeros.append(f"{rid}.unit_cost")
                        consumables_cost += units * uc
                if hours is None:
                    missing_cost.append(f"{sname}.hours")
                elif hours > 0:
                    hr = _finite_nonneg(rm.get("rate_per_hr"))
                    if hr is None:
                        missing_cost.append(f"{rid}.rate_per_hr")
                    else:
                        if hr == 0.0:
                            explicit_zeros.append(f"{rid}.rate_per_hr")
                        machine_cost += hours * hr

        step_span, span_missing, span_viols, _ = _compute_step_span(steps, resources)
        missing_sched.extend(span_missing)
        hard_violations.extend(span_viols)

        cost_comparable = len(missing_cost) == 0
        if cost_comparable:
            l_cost = (labor_days or 0.0) * (labor_rate or 0.0)
            base_cost = (one_time or 0.0) + consumables_cost + machine_cost + l_cost
            r_reserve = base_cost * (rework_rate or 0.0)
            total_cost = round(base_cost + r_reserve, 2)
        else:
            l_cost = None
            r_reserve = None
            total_cost = None

        sched_comparable = len(missing_sched) == 0 and step_span is not None
        total_days = round((prep_days or 0.0) + (step_span or 0.0) + (qc_days or 0.0), 2) if sched_comparable else None

        # Parameter domain & safety_max checks
        params = plan.get("parameters") or {}
        for pname, bnd in domains.items():
            if pname not in params or params[pname] is None:
                missing_other.append(f"parameter:{pname}")
                continue
            pval = params[pname]
            if "allowed_values" in bnd:
                if pval not in bnd["allowed_values"]:
                    hard_violations.append(f"{pname}={pval!r} not in allowed {bnd['allowed_values']}")
                continue
            fval = _finite_num(pval)
            lo = _finite_num(bnd.get("min"))
            hi = _finite_num(bnd.get("max"))
            smax = _finite_num(bnd.get("safety_max"))
            eff_hi = min(hi, smax) if (hi is not None and smax is not None) else (smax if smax is not None else hi)
            if fval is None or (lo is not None and fval < lo - 1e-9) or (eff_hi is not None and fval > eff_hi + 1e-9):
                hard_violations.append(f"{pname}={pval} outside feasible [{lo}, {eff_hi}]")

        # Approval / compliance firewall (per-plan or contract; handles explicit 'in_review'/'pending' strings)
        comp = plan.get("compliance") or {}
        bio_ok = comp.get("biosafety_approved", contract.get("biosafety_approved"))
        eth_req = comp.get("ethics_approval_required", contract.get("ethics_approval_required"))
        eth_ok = comp.get("ethics_approved", contract.get("ethics_approved"))
        raw_app_str = str(plan.get("approval_status") or comp.get("approval_status") or "").strip().lower()
        in_review_states = {"in_review", "pending", "pending_amendment", "submitted", "under_review"}
        denied_states = {"denied", "rejected", "revoked", "failed"}

        if raw_app_str in denied_states or bio_ok is False or (eth_req is True and eth_ok is False):
            hard_violations.append("required biosafety/ethics approval denied")
            approval_status = "DENIED"
        elif (
            raw_app_str in in_review_states
            or str(bio_ok).strip().lower() in in_review_states
            or (eth_req is True and str(eth_ok).strip().lower() in in_review_states)
        ):
            missing_other.append("approval_status(in_review)")
            approval_status = "IN_REVIEW_UNVERIFIED"
        elif bio_ok is not True or (eth_req is True and eth_ok is not True):
            missing_other.append("approval_status")
            approval_status = "UNVERIFIED"
        else:
            approval_status = "CONFIRMED_IN_CONTRACT"

        # Controls & comparability metadata
        ctrls = plan.get("controls") or []
        if not ctrls:
            missing_other.append("controls")
        pr = str(plan.get("primary_readout") or "").strip()
        iu = str(plan.get("inference_unit") or "").strip()
        if pr:
            readouts_seen.add(pr)
        if iu:
            units_seen.add(iu)

        # Evidence score / fit score (preserve explicit 0.0!)
        fit_score = _finite_num(plan.get("scientific_fit_score"))
        ev_score = _finite_num(plan.get("evidence_score"))
        if fit_score == 0.0:
            explicit_zeros.append("scientific_fit_score")
        if ev_score == 0.0:
            explicit_zeros.append("evidence_score")

        # Source provenance completeness
        src_checks = [
            bool(str(plan.get("source_ref", "")).strip()),
            bool(ctrls),
            bool(str(plan.get("risk_trigger", "") or (plan.get("risks") or "")).strip()),
            cost_comparable,
            sched_comparable,
        ]
        prov_pct = round(100.0 * sum(src_checks) / len(src_checks), 1)

        all_missing = sorted(set(missing_cost + missing_sched + missing_other))
        rows.append({
            "version": ver,
            "source_ref": plan.get("source_ref", ""),
            "primary_readout": pr or "UNSPECIFIED",
            "inference_unit": iu or "UNSPECIFIED",
            "control_count": len(ctrls),
            "approval_status": approval_status,
            "cost_comparable": cost_comparable,
            "cost_breakdown_usd": {
                "one_time_setup": round(one_time, 2) if one_time is not None else None,
                "consumables": round(consumables_cost, 2) if cost_comparable else None,
                "machine_hours": round(machine_cost, 2) if cost_comparable else None,
                "labor": round(l_cost, 2) if l_cost is not None else None,
                "rework_reserve": round(r_reserve, 2) if r_reserve is not None else None,
                "total_usd": total_cost,
            },
            "schedule_comparable": sched_comparable,
            "schedule_breakdown_days": {
                "prep": prep_days,
                "critical_step_or_lead": step_span,
                "qc_turnaround": qc_days,
                "total_days": total_days,
            },
            "risk_level": str(plan.get("risk_level", "UNSPECIFIED")),
            "risk_trigger": plan.get("risk_trigger", ""),
            "fallback_plan": plan.get("fallback_plan", ""),
            "evidence_score": ev_score,
            "scientific_fit_score": fit_score,
            "evidence_provenance_pct": prov_pct,
            "missing_cost_fields": sorted(set(missing_cost)),
            "missing_schedule_fields": sorted(set(missing_sched)),
            "missing_fields": all_missing,
            "explicit_zero_fields": sorted(set(explicit_zeros)),
            "hard_domain_or_resource_violations": sorted(set(hard_violations)),
        })

    incomparable_dims: list[str] = []
    if len(readouts_seen) > 1:
        incomparable_dims.append(f"heterogeneous primary_readout across plans: {sorted(readouts_seen)}")
    if len(units_seen) > 1:
        incomparable_dims.append(f"heterogeneous inference_unit across plans: {sorted(units_seen)}")

    return {
        "$schema": "aligned-plan-matrix/v2",
        "case_id": case.get("case_id", "unknown"),
        "aligned_columns": [
            "primary_readout",
            "inference_unit",
            "control_count",
            "approval_status",
            "cost_breakdown_usd.total_usd",
            "schedule_breakdown_days.total_days",
            "risk_level",
            "evidence_score",
            "scientific_fit_score",
        ],
        "comparability_audit": {
            "readouts_aligned": len(readouts_seen) <= 1,
            "inference_units_aligned": len(units_seen) <= 1,
            "incomparable_dimensions": incomparable_dims,
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize candidate plans onto an aligned comparable matrix with strict null-vs-zero checks.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    case = json.loads(Path(args.case).read_text(encoding="utf-8-sig"))
    rep = normalize_plans_matrix(case)
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
