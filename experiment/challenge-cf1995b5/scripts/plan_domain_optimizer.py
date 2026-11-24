#!/usr/bin/env python3
"""Domain-agnostic finite-domain experimental plan optimizer with DAG schedule, strict null-vs-zero cost math, and 3-version diff."""

from __future__ import annotations

import argparse
import json
import math
import operator
from collections import deque
from pathlib import Path
from typing import Any

from hypothesis_control_mapper import audit_controls_and_hypothesis

OPS = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt, "==": operator.eq}


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


def _compute_dag_schedule(
    steps: list[dict[str, Any]],
    resources: dict[str, dict[str, Any]],
    *,
    prep_days: float,
    qc_days: float,
) -> dict[str, Any]:
    """Compute exact topological DAG critical-path makespan including booking & procurement leads."""
    if not steps:
        return {
            "mode": "fallback_prep_qc",
            "critical_path_days": round(prep_days + qc_days, 2),
            "critical_path_steps": [],
            "missing_schedule_inputs": [],
            "cycle_detected": False,
            "step_timings": [],
        }

    step_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for idx, st in enumerate(steps, start=1):
        sid = str(st.get("id") or st.get("name") or f"step_{idx}").strip()
        step_by_id[sid] = st
        order.append(sid)

    indeg: dict[str, int] = {sid: 0 for sid in order}
    children: dict[str, list[str]] = {sid: [] for sid in order}
    missing_deps: list[str] = []

    for sid in order:
        deps = step_by_id[sid].get("depends_on") or []
        for d in deps:
            ds = str(d).strip()
            if ds in step_by_id:
                indeg[sid] += 1
                children[ds].append(sid)
            elif ds:
                missing_deps.append(f"{sid}->{ds}")

    q: deque[str] = deque([sid for sid in order if indeg[sid] == 0])
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
            "cycle_detected": True,
            "step_timings": [],
        }

    missing_sched: list[str] = []
    es: dict[str, float] = {}
    ef: dict[str, float] = {}
    pred_crit: dict[str, str | None] = {}
    timings: list[dict[str, Any]] = []

    for sid in topo:
        st = step_by_id[sid]
        dur = _finite_nonneg(st.get("duration_days"))
        if dur is None:
            hr = _finite_nonneg(st.get("duration_hours"))
            dur = (hr / 24.0) if hr is not None else None
        if dur is None:
            missing_sched.append(f"{sid}.duration_days")
            dur_eff = 0.0
        else:
            dur_eff = dur

        res_id = str(st.get("resource", "")).strip()
        rm = resources.get(res_id) if res_id else None
        lead_wait = 0.0
        if res_id and rm is None:
            missing_sched.append(f"resource:{res_id}")
        elif rm is not None:
            bl = rm.get("booking_lead_days")
            pl = rm.get("lead_days")
            # Explicit 0 is valid; only missing/null when status implies procurement/outsource is flagged
            status = str(rm.get("status", "unknown")).lower()
            if status in {"needs_procurement", "outsourceable"} and _finite_nonneg(pl) is None:
                missing_sched.append(f"{res_id}.lead_days")
            lead_wait = max(_finite_nonneg(bl) or 0.0, _finite_nonneg(pl) or 0.0)

        deps = [str(d).strip() for d in (st.get("depends_on") or []) if str(d).strip() in ef]
        if deps:
            best_dep = max(deps, key=lambda d: ef[d])
            start_day = max(ef[best_dep], lead_wait)
            pred_crit[sid] = best_dep
        else:
            start_day = max(prep_days, lead_wait)
            pred_crit[sid] = None

        finish_day = start_day + dur_eff
        es[sid] = round(start_day, 3)
        ef[sid] = round(finish_day, 3)
        timings.append({
            "step_id": sid,
            "resource": res_id or None,
            "earliest_start_day": es[sid],
            "duration_days": round(dur_eff, 3),
            "lead_ready_day": round(lead_wait, 3),
            "earliest_finish_day": ef[sid],
        })

    last_step = max(topo, key=lambda s: ef[s])
    crit_chain: list[str] = []
    cur: str | None = last_step
    while cur is not None:
        crit_chain.append(cur)
        cur = pred_crit.get(cur)
    crit_chain.reverse()

    makespan = None if missing_sched else round(ef[last_step] + qc_days, 2)
    return {
        "mode": "dag",
        "critical_path_days": makespan,
        "critical_path_steps": crit_chain,
        "missing_schedule_inputs": sorted(set(missing_sched + missing_deps)),
        "cycle_detected": False,
        "step_timings": timings,
    }


def _evaluate_candidate_cost(
    cand: dict[str, Any],
    contract: dict[str, Any],
    total_samples: int,
) -> dict[str, Any]:
    """Strict null-vs-zero cost arithmetic supporting both step-level resources and flat contract rates."""
    resources = contract.get("resources") or {}
    unverified: list[str] = []
    explicit_zeros: list[str] = []
    steps = cand.get("steps") or []

    # One-time setup cost
    raw_setup = cand.get("one_time_setup_cost", contract.get("setup_cost_usd"))
    setup_val = _finite_nonneg(raw_setup)
    if setup_val is None:
        unverified.append("setup_cost_usd")
    elif setup_val == 0.0:
        explicit_zeros.append("setup_cost_usd")

    consumables = 0.0
    machine_cost = 0.0
    labor_cost = 0.0
    has_step_resources = any(str(s.get("resource", "")).strip() for s in steps)

    if has_step_resources:
        for idx, st in enumerate(steps, start=1):
            rid = str(st.get("resource", "")).strip()
            if not rid:
                continue
            rm = resources.get(rid)
            if rm is None:
                unverified.append(f"resource:{rid}")
                continue
            if rm is not None:
                units = _finite_nonneg(st.get("units"))
                hours = _finite_nonneg(st.get("hours"))
                uc = _finite_nonneg(rm.get("unit_cost"))
                hr_rate = _finite_nonneg(rm.get("rate_per_hr"))
                if units is None and hours is None:
                    unverified.append(f"steps[{idx}].units_or_hours")
                else:
                    if units is not None and units > 0:
                        if uc is None:
                            unverified.append(f"{rid}.unit_cost")
                        else:
                            if uc == 0.0:
                                explicit_zeros.append(f"{rid}.unit_cost")
                            consumables += units * uc
                    if hours is not None and hours > 0:
                        if hr_rate is None:
                            unverified.append(f"{rid}.rate_per_hr")
                        else:
                            if hr_rate == 0.0:
                                explicit_zeros.append(f"{rid}.rate_per_hr")
                            machine_cost += hours * hr_rate
        # Optional per-sample consumable addon if declared in contract
        ps_raw = contract.get("per_sample_cost_usd")
        if ps_raw is not None:
            ps_val = _finite_nonneg(ps_raw)
            if ps_val is None:
                unverified.append("per_sample_cost_usd")
            else:
                consumables += total_samples * ps_val
    else:
        ps_val = _finite_nonneg(contract.get("per_sample_cost_usd"))
        if ps_val is None:
            unverified.append("per_sample_cost_usd")
        else:
            if ps_val == 0.0:
                explicit_zeros.append("per_sample_cost_usd")
            consumables = total_samples * ps_val

    labor_days_raw = cand.get("labor_days")
    labor_rate_raw = contract.get("labor_rate_per_day")
    if labor_days_raw is not None:
        ld = _finite_nonneg(labor_days_raw)
        lr = _finite_nonneg(labor_rate_raw)
        if ld is None or lr is None:
            unverified.append("labor_cost_inputs")
        else:
            labor_cost = ld * lr

    rework_rate = _finite_nonneg(contract.get("rework_reserve_rate", 0.0)) or 0.0
    if unverified:
        return {
            "verifiable": False,
            "one_time_setup_usd": setup_val,
            "consumables_usd": None,
            "machine_hours_usd": None,
            "labor_usd": None,
            "rework_reserve_usd": None,
            "total_usd": None,
            "unverified_cost_inputs": sorted(set(unverified)),
            "explicit_zero_fields": sorted(set(explicit_zeros)),
        }

    base = (setup_val or 0.0) + consumables + machine_cost + labor_cost
    rework = base * rework_rate
    total = round(base + rework, 2)
    return {
        "verifiable": True,
        "one_time_setup_usd": round(setup_val or 0.0, 2),
        "consumables_usd": round(consumables, 2),
        "machine_hours_usd": round(machine_cost, 2),
        "labor_usd": round(labor_cost, 2),
        "rework_reserve_usd": round(rework, 2),
        "total_usd": total,
        "unverified_cost_inputs": [],
        "explicit_zero_fields": sorted(set(explicit_zeros)),
    }


def _check_parameters_and_coupled(
    cand: dict[str, Any],
    domains: dict[str, dict[str, Any]],
    coupled: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Validate finite parameter bounds, step grid, safety_max, allowed_values, and coupled rules."""
    param_map = dict(cand.get("parameters") or {})
    for pname in domains:
        if pname not in param_map and pname in cand:
            param_map[pname] = cand[pname]

    violations: list[str] = []
    for pname, bnd in domains.items():
        if pname not in param_map or param_map[pname] is None:
            violations.append(f"missing required domain parameter: {pname}")
            continue
        val = param_map[pname]
        if "allowed_values" in bnd:
            if val not in bnd["allowed_values"]:
                violations.append(f"{pname}={val!r} not in allowed_values {bnd['allowed_values']}")
            continue
        fval = _finite_num(val)
        if fval is None:
            violations.append(f"{pname}={val!r} must be a finite number")
            continue
        lo = _finite_num(bnd.get("min"))
        hi = _finite_num(bnd.get("max"))
        smax = _finite_num(bnd.get("safety_max"))
        eff_hi = min(hi, smax) if (hi is not None and smax is not None) else (smax if smax is not None else hi)
        if lo is not None and eff_hi is not None and eff_hi < lo:
            violations.append(f"INFEASIBLE_CONSTRAINT_CONFLICT on {pname}: min={lo} > effective_max={eff_hi}")
        elif (lo is not None and fval < lo - 1e-9) or (eff_hi is not None and fval > eff_hi + 1e-9):
            violations.append(f"{pname}={fval} outside feasible [{lo}, {eff_hi}] {bnd.get('unit', '')}".strip())
        step = _finite_num(bnd.get("step"))
        if step and step > 0 and lo is not None and (eff_hi is None or eff_hi >= lo):
            rem = abs((fval - lo) / step - round((fval - lo) / step))
            if rem > 1e-4:
                violations.append(f"{pname}={fval} violates step={step} from min={lo}")

    for rule in coupled:
        lhs, rhs, op = rule.get("lhs_param"), rule.get("rhs_param"), rule.get("op", "<=")
        fn = OPS.get(op)
        if fn is None or lhs not in param_map or rhs not in param_map:
            violations.append(f"coupled constraint unresolved: {lhs} {op} {rhs}")
            continue
        a, b = _finite_num(param_map[lhs]), _finite_num(param_map[rhs])
        off = _finite_num(rule.get("offset", 0.0))
        if a is None or b is None or off is None:
            violations.append(f"coupled constraint non-numeric: {lhs} {op} {rhs}")
        elif not fn(a, b + off):
            violations.append(f"coupled constraint failed: {lhs}({a}) {op} {rhs}({b}) + {off}")

    return {k: param_map[k] for k in domains if k in param_map}, violations


def _score_domain_objectives(
    cand: dict[str, Any],
    objectives: list[dict[str, Any]],
    total_cost: float | None,
    budget_cap: float | None,
    est_days: float | None,
    deadline: float | None,
) -> tuple[float | None, list[str]]:
    """Domain-agnostic weighted objective scorer (zero hard-coded biology keys)."""
    if total_cost is None or est_days is None:
        return None, []
    obs_metrics = cand.get("predicted_metrics") or {}
    threshold_fails: list[str] = []

    if objectives:
        total_w = sum(max(0.0, float(o.get("weight", 1.0))) for o in objectives) or 1.0
        weighted_sum = 0.0
        for obj in objectives:
            name = str(obj.get("name", "")).strip()
            w = max(0.0, float(obj.get("weight", 1.0))) / total_w
            direction = str(obj.get("direction", "maximize")).lower()
            raw = obs_metrics.get(name, cand.get(name))
            val = _finite_num(raw)
            if val is None:
                threshold_fails.append(f"missing predicted metric: {name}")
                continue
            min_acc = _finite_num(obj.get("min_acceptable"))
            max_acc = _finite_num(obj.get("max_acceptable"))
            if min_acc is not None and val < min_acc:
                threshold_fails.append(f"predicted {name}={val} < min_acceptable {min_acc}")
            if max_acc is not None and val > max_acc:
                threshold_fails.append(f"predicted {name}={val} > max_acceptable {max_acc}")
            lo = _finite_num(obj.get("scale_min"))
            hi = _finite_num(obj.get("scale_max"))
            if lo is not None and hi is not None and hi > lo:
                norm = max(0.0, min(1.0, (val - lo) / (hi - lo)))
            else:
                norm = max(0.0, min(1.0, val)) if (0.0 <= val <= 1.0) else val / (abs(val) + 1.0)
            weighted_sum += w * (norm if direction == "maximize" else (1.0 - norm))
        cost_eff = (1.0 - min(1.0, total_cost / budget_cap)) if (budget_cap and budget_cap > 0) else 0.5
        time_eff = (1.0 - min(1.0, est_days / deadline)) if (deadline and deadline > 0) else 0.5
        score = round(0.80 * weighted_sum + 0.12 * cost_eff + 0.08 * time_eff, 4)
        return score, threshold_fails

    # Fallback generic efficiency score when case supplies only cost/time constraints
    cost_eff = (1.0 - min(1.0, total_cost / budget_cap)) if (budget_cap and budget_cap > 0) else 0.5
    time_eff = (1.0 - min(1.0, est_days / deadline)) if (deadline and deadline > 0) else 0.5
    return round(0.55 * cost_eff + 0.45 * time_eff, 4), threshold_fails


def _build_version_diffs(evaluated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for i in range(1, len(evaluated)):
        prev, cur = evaluated[i - 1], evaluated[i]
        param_changes = {}
        all_p = sorted(set(prev["parameters"]) | set(cur["parameters"]))
        for p in all_p:
            if prev["parameters"].get(p) != cur["parameters"].get(p):
                param_changes[p] = {"from": prev["parameters"].get(p), "to": cur["parameters"].get(p)}
        cost_delta = (
            round(cur["cost_breakdown"]["total_usd"] - prev["cost_breakdown"]["total_usd"], 2)
            if cur["cost_breakdown"]["total_usd"] is not None and prev["cost_breakdown"]["total_usd"] is not None
            else None
        )
        day_delta = (
            round(cur["estimated_days"] - prev["estimated_days"], 2)
            if cur["estimated_days"] is not None and prev["estimated_days"] is not None
            else None
        )
        diffs.append({
            "transition": f"{prev['version']} -> {cur['version']}",
            "sample_delta": cur["total_samples"] - prev["total_samples"],
            "cost_delta_usd": cost_delta,
            "days_delta": day_delta,
            "parameter_changes": param_changes,
            "violations_resolved": [v for v in prev["violations"] if v not in cur["violations"]],
            "new_violations": [v for v in cur["violations"] if v not in prev["violations"]],
        })
    return diffs


def optimize_plan_versions(case: dict[str, Any], control_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate 3+ versioned candidate plans over finite domains, DAG schedules, and strict prospective cost contracts."""
    if control_audit is None:
        control_audit = audit_controls_and_hypothesis(case)

    contract = case.get("prospective_contract") or {}
    resources = contract.get("resources") or {}
    domains = contract.get("parameter_domains") or {}
    coupled = contract.get("coupled_constraints") or []
    objectives = case.get("objectives") or []

    budget_cap = _finite_nonneg(contract.get("budget_cap_usd"))
    deadline = _finite_nonneg(contract.get("deadline_days"))
    max_samples = int(contract["max_samples"]) if contract.get("max_samples") is not None else None
    prep_days = _finite_nonneg(contract.get("base_prep_days", 0.0)) or 0.0
    qc_days = _finite_nonneg(contract.get("qc_days", 0.0)) or 0.0
    ctrl_count = max(1, int(control_audit.get("control_count", len(case.get("controls") or []))))

    # Check ethics/biosafety contract gates
    confirmation_items: list[dict[str, Any]] = []
    for gate_key, label in (
        ("biosafety_approved", "Biosafety approval"),
        ("ethics_approved", "Ethics (IRB/IACUC) approval"),
        ("equipment_booking_confirmed", "Instrument booking confirmation"),
        ("vendor_quote_confirmed", "Vendor pricing & lead-time confirmation"),
    ):
        val = contract.get(gate_key)
        if gate_key == "ethics_approved" and contract.get("ethics_approval_required") is False:
            continue
        status = "CONFIRMED_IN_CONTRACT" if val is True else ("BLOCKING_DENIED" if val is False else "UNVERIFIED_REQUIRED")
        confirmation_items.append({"item": label, "contract_key": gate_key, "status": status})

    if control_audit.get("sop_pin_audit", {}).get("version_conflicts"):
        confirmation_items.append({
            "item": "Resolve SOP version conflict before execution",
            "contract_key": "sop_version_pin",
            "status": "BLOCKING_CONFLICT",
            "detail": control_audit["sop_pin_audit"]["version_conflicts"],
        })

    evaluated: list[dict[str, Any]] = []
    for idx, cand in enumerate(case.get("finite_candidates") or [], start=1):
        ver = str(cand.get("version") or f"v{idx}")
        # Per-candidate SOP version pinning conflict check (eLabFTW #3815 paradigm: never mix two versions of the same SOP!)
        sop_conflicts_cand: list[str] = []
        by_sop_id: dict[str, set[str]] = {}
        for sref in cand.get("sop_versions") or []:
            if isinstance(sref, dict):
                sid_k = str(sref.get("sop_id", "")).strip()
                sver_k = str(sref.get("version", "")).strip().lower()
                if sid_k:
                    by_sop_id.setdefault(sid_k, set()).add(sver_k or "unpinned")
        for sid_k, vers in sorted(by_sop_id.items()):
            if len(vers) > 1:
                sop_conflicts_cand.append(f"SOP_VERSION_CONFLICT: {sid_k} mixes versions {sorted(vers)}")
            elif "latest" in vers or "unpinned" in vers:
                sop_conflicts_cand.append(f"SOP_UNPINNED_VERSION: {sid_k} uses {sorted(vers)} instead of pinned vN")

        arms = int(cand.get("treatment_arms") if cand.get("treatment_arms") is not None else (cand.get("dose_arms") if cand.get("dose_arms") is not None else 1))
        bio_reps = int(cand.get("biological_replicates") if cand.get("biological_replicates") is not None else (cand.get("replicates") if cand.get("replicates") is not None else 3))
        tech_reps = int(cand.get("technical_replicates") if cand.get("technical_replicates") is not None else 1)
        qc_extra = int(cand.get("qc_extra_samples") or 0)
        total_arms = arms + ctrl_count
        total_independent_units = total_arms * bio_reps
        total_samples = total_independent_units * tech_reps + qc_extra

        cost_bk = _evaluate_candidate_cost(cand, contract, total_samples)
        if cand.get("steps"):
            sched = _compute_dag_schedule(cand["steps"], resources, prep_days=prep_days, qc_days=qc_days)
            est_days = sched["critical_path_days"]
        else:
            hr = _finite_nonneg(cand.get("harvest_hr", 24.0)) or 24.0
            est_days = round(prep_days + math.ceil(hr / 24.0) + qc_days, 2)
            sched = {
                "mode": "simple_harvest_hours",
                "critical_path_days": est_days,
                "critical_path_steps": [],
                "missing_schedule_inputs": [],
                "cycle_detected": False,
                "step_timings": [],
            }

        params, violations = _check_parameters_and_coupled(cand, domains, coupled)
        violations.extend(sop_conflicts_cand)

        # Resource availability check for step-bound resources
        for st in cand.get("steps") or []:
            rid = str(st.get("resource", "")).strip()
            rm = resources.get(rid)
            if rm is not None and str(rm.get("status", "")).lower() == "unavailable":
                violations.append(f"resource {rid} unavailable (no active substitute bound)")

        if not cost_bk["verifiable"]:
            violations.append(f"UNVERIFIED_COST_INPUT: {', '.join(cost_bk['unverified_cost_inputs'])}")
        elif budget_cap is not None and cost_bk["total_usd"] > budget_cap + 1e-9:
            violations.append(f"cost ${cost_bk['total_usd']} > budget_cap ${budget_cap}")

        if sched["missing_schedule_inputs"]:
            violations.append(f"unverified schedule inputs: {', '.join(sched['missing_schedule_inputs'])}")
        elif deadline is not None and est_days is not None and est_days > deadline + 1e-9:
            violations.append(f"critical_path {est_days}d > deadline {deadline}d")

        if max_samples is not None and total_samples > max_samples:
            violations.append(f"samples {total_samples} > max_samples {max_samples}")

        if not str(cand.get("stop_loss", "")).strip():
            violations.append("missing quantitative stop_loss trigger")
        if not str(cand.get("fallback_path", "")).strip():
            violations.append("missing fallback_path for failure recovery")

        score, obj_fails = _score_domain_objectives(cand, objectives, cost_bk["total_usd"], budget_cap, est_days, deadline)
        violations.extend(obj_fails)

        feasible = len(violations) == 0 and control_audit.get("ok", True)
        evaluated.append({
            "version": ver,
            "rationale": cand.get("rationale", ""),
            "treatment_arms": arms,
            "control_arms": ctrl_count,
            "total_arms_with_controls": total_arms,
            "biological_replicates": bio_reps,
            "technical_replicates": tech_reps,
            "qc_extra_samples": qc_extra,
            "total_independent_units": total_independent_units,
            "total_samples": total_samples,
            "cost_breakdown": cost_bk,
            "estimated_cost_usd": cost_bk["total_usd"],
            "schedule": sched,
            "estimated_days": est_days,
            "parameters": params,
            "predicted_metrics": cand.get("predicted_metrics") or {},
            "stop_loss": cand.get("stop_loss", ""),
            "fallback_path": cand.get("fallback_path", ""),
            "feasible": feasible,
            "violations": violations,
            "optimization_score": score if feasible else None,
        })

    feasible_list = sorted([e for e in evaluated if e["feasible"]], key=lambda r: (-(r["optimization_score"] or -1e9), r["estimated_cost_usd"] or 1e9))
    recommended = feasible_list[0]["version"] if feasible_list else None
    version_diffs = _build_version_diffs(evaluated)

    # Synthesize actionable minimal_feasible_revision when all_infeasible=True (never stop at bare rejection)
    minimal_feasible_revision: dict[str, Any] | None = None
    if len(feasible_list) == 0 and evaluated:
        # Rank candidates by fewest violations to choose closest base plan
        closest = min(evaluated, key=lambda e: (len(e["violations"]), e["estimated_cost_usd"] if e["estimated_cost_usd"] is not None else 1e9))
        design_additions: list[str] = []
        if not control_audit.get("ok", True):
            for err in control_audit.get("errors") or []:
                design_additions.append(f"Resolve audit gate: {err}")
        for seg in control_audit.get("secondary_endpoint_gaps") or []:
            design_additions.append(seg["remediation"])
        for fm in control_audit.get("factorial_label_mismatches") or []:
            design_additions.append(fm["remediation"])
        constraint_revisions: list[str] = []
        for v in closest["violations"]:
            constraint_revisions.append(f"Fix on {closest['version']}: {v}")
        if budget_cap is not None and closest["estimated_cost_usd"] is not None and closest["estimated_cost_usd"] > budget_cap:
            over = round(closest["estimated_cost_usd"] - budget_cap, 2)
            constraint_revisions.append(f"Reduce cost by >= ${over} (or expand budget_cap from ${budget_cap} to ${closest['estimated_cost_usd']})")
        if deadline is not None and closest["estimated_days"] is not None and closest["estimated_days"] > deadline:
            over_d = round(closest["estimated_days"] - deadline, 2)
            constraint_revisions.append(f"Compress critical path by >= {over_d}d (or relax deadline from {deadline}d to {closest['estimated_days']}d)")
        minimal_feasible_revision = {
            "closest_base_version": closest["version"],
            "closest_base_cost_usd": closest["estimated_cost_usd"],
            "closest_base_days": closest["estimated_days"],
            "required_design_and_audit_revisions": design_additions,
            "required_constraint_revisions": constraint_revisions,
            "status": "ACTIONABLE_REVISION_REQUIRED_BEFORE_EXECUTION",
        }

    return {
        "$schema": "optimized-experimental-plan/v2",
        "case_id": case.get("case_id", "unknown"),
        "title": case.get("title", ""),
        "research_type": case.get("research_type", "empirical_causal"),
        "recommended_plan_version": recommended,
        "all_infeasible": len(feasible_list) == 0,
        "minimal_feasible_revision": minimal_feasible_revision,
        "control_audit": control_audit,
        "confirmation_items": confirmation_items,
        "version_diffs": version_diffs,
        "prospective_boundary_notice": (
            "Candidate optimization evaluates organizer prospective resource/price/booking contracts over finite parameter domains; "
            "it is a planning scenario only — NOT an executed lab run, confirmed reservation, vendor quote, granted safety/ethics approval, or measured response."
        ),
        "versions_evaluated": evaluated,
    }


def _render_markdown(rep: dict[str, Any]) -> str:
    lines = [
        f"# Optimized Experimental Plan: {rep['title']} ({rep['case_id']})",
        "",
        f"- **Research Type**: `{rep['research_type']}`",
        f"- **Recommended Version**: `{rep['recommended_plan_version']}`",
        f"- **Control & Hypothesis Audit**: `{'PASS' if rep['control_audit']['ok'] else 'FAIL'}`",
        "",
        f"> **Prospective Boundary Notice**: {rep['prospective_boundary_notice']}",
        "",
        "## Version Comparison",
        "",
        "| Version | Indep. Units | Total Samples | Cost (USD) | Critical Path (d) | Feasible | Score | Violations / Stop-Loss |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in rep["versions_evaluated"]:
        cost_s = str(e["estimated_cost_usd"]) if e["estimated_cost_usd"] is not None else "UNVERIFIED"
        days_s = str(e["estimated_days"]) if e["estimated_days"] is not None else "UNVERIFIED"
        score_s = str(e["optimization_score"]) if e["optimization_score"] is not None else "-"
        note = "; ".join(e["violations"]) if e["violations"] else f"Stop-loss: {e['stop_loss']}"
        lines.append(
            f"| {e['version']} | {e['total_independent_units']} | {e['total_samples']} | {cost_s} | {days_s} | {e['feasible']} | {score_s} | {note} |"
        )

    if rep["version_diffs"]:
        lines.extend(["", "## Version-to-Version Revisions", ""])
        for d in rep["version_diffs"]:
            lines.append(
                f"- **{d['transition']}**: samples `{d['sample_delta']:+d}`, cost_delta `{d['cost_delta_usd']}`, "
                f"days_delta `{d['days_delta']}`, resolved={len(d['violations_resolved'])}, param_changes={d['parameter_changes']}"
            )

    lines.extend(["", "## Required Confirmation Items (Pre-Execution)", ""])
    for ci in rep["confirmation_items"]:
        lines.append(f"- **{ci['item']}** (`{ci['contract_key']}`): `{ci['status']}`")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optimize 3 versioned experimental plans over finite domains and DAG schedules.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--controls", default=None)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stem", default="optimized_plan")
    args = parser.parse_args(argv)

    case = json.loads(Path(args.case).read_text(encoding="utf-8-sig"))
    ctrl = json.loads(Path(args.controls).read_text(encoding="utf-8-sig")) if args.controls else None
    rep = optimize_plan_versions(case, control_audit=ctrl)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_p = out_dir / f"{args.stem}.json"
    md_p = out_dir / f"{args.stem}.md"
    json_p.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_p.write_text(_render_markdown(rep), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "recommended": rep["recommended_plan_version"],
        "all_infeasible": rep["all_infeasible"],
        "json": str(json_p),
        "md": str(md_p),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
