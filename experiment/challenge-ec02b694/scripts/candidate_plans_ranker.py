#!/usr/bin/env python3
"""Pareto-frontier multi-objective ranker, search/stopping validator, and interaction-aware plan synthesizer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

VALID_SEARCH_STRATEGIES = {
    "grid",
    "sequential_doe",
    "bayesian_bo",
    "fractional_factorial",
    "rsm_ccd",
    "screening_then_confirm",
}


def _pareto_nondominated(items: list[dict[str, Any]], direction: str = "maximize") -> set[str]:
    """Return plan_ids on the Pareto frontier over (response, -uncertainty, -cost, -hours)."""
    def _vec(r: dict[str, Any]) -> tuple[float, float, float, float]:
        resp = float(r["predicted_response"])
        resp_obj = resp if direction == "maximize" else -resp
        unc = float(r["predicted_uncertainty"])
        cost = float(r["total_estimated_cost"])
        hrs = float(r["total_machine_hours"])
        return (resp_obj, -unc, -cost, -hrs)

    front: set[str] = set()
    for a in items:
        va = _vec(a)
        dominated = False
        for b in items:
            if a["plan_id"] == b["plan_id"]:
                continue
            vb = _vec(b)
            if all(vb[i] >= va[i] - 1e-9 for i in range(4)) and any(vb[i] > va[i] + 1e-9 for i in range(4)):
                dominated = True
                break
        if not dominated:
            front.add(a["plan_id"])
    return front


def rank_and_validate_plans(
    candidates: list[dict[str, Any]],
    domain_report: dict[str, Any],
    cost_report: dict[str, Any],
    *,
    interaction_report: dict[str, Any] | None = None,
    direction: str = "maximize",
    require_versions: int = 3,
) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    if len(candidates) < require_versions:
        issues.append(f"expected >= {require_versions} versioned candidate plans, got {len(candidates)}")

    feas_by_id = {r["plan_id"]: r for r in domain_report.get("candidate_checks", [])}
    cost_by_id = {r["plan_id"]: r for r in cost_report.get("plans", [])}
    surr_by_id = {
        r["plan_id"]: r
        for r in (interaction_report or {}).get("candidate_surrogate_predictions", [])
        if isinstance(r, dict) and r.get("plan_id")
    }
    inter_rev = bool((interaction_report or {}).get("interaction_reversal_detected"))

    feasible_ranked: list[dict[str, Any]] = []
    infeasible_pruned: list[dict[str, Any]] = []
    unresolved_missing: list[dict[str, Any]] = []

    for idx, cand in enumerate(candidates, start=1):
        pid = str(cand.get("plan_id", f"plan_v{idx}"))
        plan_issues: list[str] = []
        strat = str(cand.get("search_strategy", "")).strip()
        if strat not in VALID_SEARCH_STRATEGIES:
            plan_issues.append(f"search_strategy {strat!r} must be one of {sorted(VALID_SEARCH_STRATEGIES)}")
        if not str(cand.get("stopping_criterion", "")).strip():
            plan_issues.append("missing quantitative stopping_criterion")
        if cand.get("claimed_as_measured_lab_result") or cand.get("claimed_safety_approved"):
            plan_issues.append("policy violation: planning candidate cannot claim measured lab outcome or safety sign-off")

        surr = surr_by_id.get(pid) or {}
        pred_resp = cand.get("predicted_response")
        pred_unc = cand.get("predicted_uncertainty")
        if pred_resp is None and surr.get("predicted"):
            pred_resp = surr.get("surrogate_predicted_response")
        if pred_unc is None and surr.get("predicted"):
            pred_unc = surr.get("surrogate_prediction_se")

        f_rec = feas_by_id.get(pid, {})
        c_rec = cost_by_id.get(pid, {})
        f_ok = bool(f_rec.get("feasible", False))
        c_missing = list(c_rec.get("missing_rate_or_usage_keys") or [])
        c_ok = bool(c_rec.get("arithmetic_ok", False))
        src_conflicts = list(cand.get("source_conflicts") or [])
        if c_rec.get("unverified_machine_hours_assumption"):
            c_missing.append("unverified_machine_hours_source_ref")
        if src_conflicts:
            c_missing.append(f"unresolved_source_conflict:{[sc.get('field') for sc in src_conflicts]}")

        # Warn if interaction reversal was detected and candidate uses naive OFAT strategy
        if inter_rev and strat == "grid" and not cand.get("accounts_for_interaction", True):
            warnings.append(f"{pid}: interaction reversal detected in pilot data; ensure joint/factorial confirmation rather than OFAT.")

        entry = {
            "plan_id": pid,
            "version_label": cand.get("version_label", pid),
            "source_ref": cand.get("source_ref", ""),
            "parameters": cand.get("parameters", {}),
            "search_strategy": strat,
            "stopping_criterion": cand.get("stopping_criterion", ""),
            "confirmation_run_required": bool(cand.get("confirmation_run_required", True)),
            "feasible_domain": f_ok,
            "domain_violations": f_rec.get("violations", []),
            "within_budget_and_time": c_ok,
            "missing_cost_inputs": sorted(set(c_missing)),
            "source_conflicts": src_conflicts,
            "total_estimated_cost": c_rec.get("total_estimated_cost"),
            "total_machine_hours": c_rec.get("total_machine_hours"),
            "predicted_response": pred_resp,
            "predicted_uncertainty": pred_unc,
            "surrogate_extrapolated_factors": surr.get("extrapolated_factors", []),
            "policy_or_schema_issues": plan_issues,
            "planning_disclaimer": "Prospective optimization recommendation; requires independent confirmatory run and EHS sign-off prior to execution.",
        }

        if not f_ok or (c_rec.get("within_budget") is False) or (c_rec.get("within_time_cap") is False) or plan_issues:
            infeasible_pruned.append(entry)
        elif c_missing or src_conflicts or pred_resp is None or pred_unc is None:
            unresolved_missing.append(entry)
        else:
            feasible_ranked.append(entry)

    # Hard conflict gate: if domain_conflicts exist (empty/contradictory feasible domain), block any recommendation!
    dom_conflicts = list(domain_report.get("domain_conflicts") or [])
    domain_conflict_blocker = len(dom_conflicts) > 0
    if domain_conflict_blocker and feasible_ranked:
        for fr in feasible_ranked:
            fr["domain_violations"] = list(fr.get("domain_violations") or []) + ["GLOBAL_DOMAIN_CONFLICT_BLOCKER"]
            infeasible_pruned.append(fr)
        feasible_ranked = []
        warnings.append("Global domain_conflicts detected; blocked all candidate recommendations until conflicting bounds are reconciled.")

    # Compute Pareto front & normalized multi-objective utility over feasible plans
    pareto_ids = _pareto_nondominated(feasible_ranked, direction=direction) if feasible_ranked else set()
    if feasible_ranked:
        resps = [float(r["predicted_response"]) for r in feasible_ranked]
        uncs = [float(r["predicted_uncertainty"]) for r in feasible_ranked]
        costs = [float(r["total_estimated_cost"]) for r in feasible_ranked]
        r_lo, r_hi = min(resps), max(resps)
        u_lo, u_hi = min(uncs), max(uncs)
        c_lo, c_hi = min(costs), max(costs)
        for r in feasible_ranked:
            r_norm = ((float(r["predicted_response"]) - r_lo) / (r_hi - r_lo)) if r_hi > r_lo else 1.0
            if direction == "minimize":
                r_norm = 1.0 - r_norm
            u_norm = 1.0 - (((float(r["predicted_uncertainty"]) - u_lo) / (u_hi - u_lo)) if u_hi > u_lo else 0.0)
            c_norm = 1.0 - (((float(r["total_estimated_cost"]) - c_lo) / (c_hi - c_lo)) if c_hi > c_lo else 0.0)
            r["pareto_optimal"] = r["plan_id"] in pareto_ids
            r["composite_tradeoff_score"] = round(0.55 * r_norm + 0.25 * c_norm + 0.20 * u_norm, 4)

        feasible_ranked.sort(key=lambda x: (not x["pareto_optimal"], -x["composite_tradeoff_score"], x["total_estimated_cost"]))

    recommended = None if domain_conflict_blocker else (feasible_ranked[0]["plan_id"] if feasible_ranked else None)
    all_infeasible = len(feasible_ranked) == 0 and len(unresolved_missing) == 0

    inter_provided = interaction_report is not None
    opt_mode = "pilot_guided_surrogate_mode" if inter_provided else "no_pilot_data_screening_mode"
    if not inter_provided:
        warnings.append(
            "No pilot/historical interaction report provided; operating in no_pilot_data_screening_mode "
            "(recommend bounded screening/fractional-factorial runs first and independent confirmation before claiming optimality)."
        )

    return {
        "$schema": "candidate-optimization-ranking/v2",
        "ok": len(issues) == 0 and (recommended is not None or domain_conflict_blocker or len(unresolved_missing) > 0),
        "optimization_mode": opt_mode,
        "objective_direction": direction,
        "recommended_plan": recommended,
        "domain_conflict_blocker": domain_conflict_blocker,
        "pareto_frontier_ids": sorted(pareto_ids),
        "all_infeasible": all_infeasible,
        "domain_conflicts": dom_conflicts,
        "interaction_reversal_detected": inter_rev if inter_provided else None,
        "issues": issues,
        "warnings": warnings,
        "prospective_boundary_notice": (
            "All candidate parameter plans, surrogate predictions, and cost/hour arithmetic reflect prospective planning scenarios "
            "under organizer contracts — NOT executed laboratory runs, vendor quotes, or granted safety approvals."
        ),
        "feasible_ranked": feasible_ranked,
        "unresolved_missing_info": unresolved_missing,
        "infeasible_pruned": infeasible_pruned,
    }


def _render_markdown(rep: dict[str, Any]) -> str:
    lines = [
        "# Bounded Experimental Parameter Optimization Report",
        "",
        f"- **Recommended Plan**: `{rep['recommended_plan']}`",
        f"- **Pareto Frontier**: `{rep['pareto_frontier_ids']}`",
        f"- **Interaction Reversal Detected**: `{rep['interaction_reversal_detected']}`",
        "",
        f"> **Prospective Boundary Notice**: {rep['prospective_boundary_notice']}",
        "",
        "## Feasible Candidate Trade-Off Table",
        "",
        "| Plan ID | Strategy | Pred. Response | Pred. Unc. | Total Cost | Machine Hrs | Pareto | Trade-Off Score |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rep["feasible_ranked"]:
        lines.append(
            f"| {r['plan_id']} | {r['search_strategy']} | {r['predicted_response']} | {r['predicted_uncertainty']} | "
            f"{r['total_estimated_cost']} | {r['total_machine_hours']} | {r['pareto_optimal']} | {r['composite_tradeoff_score']} |"
        )
    if rep["unresolved_missing_info"]:
        lines.extend(["", "## Unresolved (Missing Cost/Prediction Inputs — Not Coerced to Zero)", ""])
        for u in rep["unresolved_missing_info"]:
            lines.append(f"- `{u['plan_id']}`: missing_cost_inputs={u['missing_cost_inputs']}, pred=({u['predicted_response']}, {u['predicted_uncertainty']})")
    if rep["infeasible_pruned"]:
        lines.extend(["", "## Pruned Infeasible Candidates", ""])
        for p in rep["infeasible_pruned"]:
            reasons = p["domain_violations"] + p["policy_or_schema_issues"]
            if p["within_budget_and_time"] is False:
                reasons.append("exceeds budget or machine-hour cap")
            lines.append(f"- `{p['plan_id']}`: {'; '.join(reasons)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate & Pareto-rank 3 versioned experimental optimization plans.")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--domain-report", required=True)
    parser.add_argument("--cost-report", required=True)
    parser.add_argument("--interaction-report", default="")
    parser.add_argument("--direction", default="maximize", choices=("maximize", "minimize"))
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--stem", default="optimization_plans")
    args = parser.parse_args(argv)

    raw_c = json.loads(Path(args.candidates).read_text(encoding="utf-8-sig"))
    cands = raw_c if isinstance(raw_c, list) else raw_c.get("plans", [])
    dom = json.loads(Path(args.domain_report).read_text(encoding="utf-8-sig"))
    cost = json.loads(Path(args.cost_report).read_text(encoding="utf-8-sig"))
    inter = json.loads(Path(args.interaction_report).read_text(encoding="utf-8-sig")) if (args.interaction_report and Path(args.interaction_report).is_file()) else None

    rep = rank_and_validate_plans(cands, dom, cost, interaction_report=inter, direction=args.direction)

    if args.out_dir:
        od = Path(args.out_dir)
        od.mkdir(parents=True, exist_ok=True)
        jp = od / f"{args.stem}.json"
        mp = od / f"{args.stem}.md"
        jp.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mp.write_text(_render_markdown(rep), encoding="utf-8")
    elif args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": rep["ok"],
        "recommended": rep["recommended_plan"],
        "pareto_frontier": rep["pareto_frontier_ids"],
        "interaction_reversal_detected": rep["interaction_reversal_detected"],
    }, ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
