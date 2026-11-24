#!/usr/bin/env python3
"""Hard-constraint pruner, Pareto dominance selector, 5-profile sensitivity analyzer, and null-vs-zero comparator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aligned_matrix_normalizer import normalize_plans_matrix

_RISK_PENALTY = {"low": 0.0, "medium": 0.15, "high": 0.35, "unspecified": 0.25}

WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    "balanced": {"fit": 0.40, "ev": 0.25, "cost": 0.18, "time": 0.12, "risk": 0.05},
    "scientific_first": {"fit": 0.55, "ev": 0.30, "cost": 0.07, "time": 0.05, "risk": 0.03},
    "cost_first": {"fit": 0.25, "ev": 0.15, "cost": 0.40, "time": 0.12, "risk": 0.08},
    "speed_first": {"fit": 0.25, "ev": 0.15, "cost": 0.15, "time": 0.38, "risk": 0.07},
    "risk_averse": {"fit": 0.30, "ev": 0.25, "cost": 0.12, "time": 0.08, "risk": 0.25},
}


def _compute_pareto(feasible: list[dict[str, Any]]) -> tuple[set[str], list[dict[str, str]]]:
    """Identify Pareto frontier & explicit dominance pairs across (fit, evidence, -cost, -days, -risk)."""
    def _vec(r: dict[str, Any]) -> tuple[float, float, float, float, float]:
        rpen = _RISK_PENALTY.get(str(r.get("risk_level", "unspecified")).lower(), 0.25)
        return (
            float(r["scientific_fit_score"]),
            float(r["evidence_score"]),
            -float(r["cost_breakdown_usd"]["total_usd"]),
            -float(r["schedule_breakdown_days"]["total_days"]),
            -rpen,
        )

    front: set[str] = set()
    dominance_pairs: list[dict[str, str]] = []
    for a in feasible:
        va = _vec(a)
        dominated = False
        for b in feasible:
            if a["version"] == b["version"]:
                continue
            vb = _vec(b)
            if all(vb[i] >= va[i] - 1e-9 for i in range(5)) and any(vb[i] > va[i] + 1e-9 for i in range(5)):
                dominated = True
                dominance_pairs.append({
                    "dominant": b["version"],
                    "dominated": a["version"],
                    "reason": f"{b['version']} is >= {a['version']} on all 5 criteria and strictly better on at least one",
                })
        if not dominated:
            front.add(a["version"])
    return front, dominance_pairs


def _unit_scale(score: float) -> float:
    """Normalize either [0, 1] or [0, 100] scores onto a common [0, 1] utility scale."""
    return max(0.0, min(1.0, score / 100.0 if score > 1.0 else score))


def _dimension_utilities(
    row: dict[str, Any],
    budget_cap: float | None,
    deadline: float | None,
    max_cost: float,
    max_days: float,
) -> dict[str, float]:
    fit_u = _unit_scale(float(row["scientific_fit_score"]))
    ev_u = _unit_scale(float(row["evidence_score"]))
    tcost = float(row["cost_breakdown_usd"]["total_usd"])
    tdays = float(row["schedule_breakdown_days"]["total_days"])
    rpen = _RISK_PENALTY.get(str(row.get("risk_level", "unspecified")).lower(), 0.25)

    cost_denom = float(budget_cap) if (budget_cap and budget_cap > 0) else max(max_cost, 1.0)
    day_denom = float(deadline) if (deadline and deadline > 0) else max(max_days, 1.0)
    c_util = max(0.0, 1.0 - tcost / cost_denom)
    d_util = max(0.0, 1.0 - tdays / day_denom)
    # Zero schedule buffer penalty on risk utility when running right on the deadline ceiling
    zero_slack_pen = 0.15 if (deadline is not None and (float(deadline) - tdays) <= 1e-9) else 0.0
    r_util = max(0.0, 1.0 - rpen - zero_slack_pen)
    return {
        "fit": round(fit_u, 4),
        "ev": round(ev_u, 4),
        "cost": round(c_util, 4),
        "time": round(d_util, 4),
        "risk": round(r_util, 4),
    }


def _score_under_profile(
    row: dict[str, Any],
    w: dict[str, float],
    budget_cap: float | None,
    deadline: float | None,
    max_cost: float,
    max_days: float,
) -> float:
    u = _dimension_utilities(row, budget_cap, deadline, max_cost, max_days)
    return round(100.0 * sum(w[k] * u[k] for k in ("fit", "ev", "cost", "time", "risk")), 4)


def evaluate_plan_choice(case: dict[str, Any], matrix: dict[str, Any] | None = None) -> dict[str, Any]:
    """Prune infeasible plans, firewall incomplete plans from forced ranking, run Pareto & 5-profile sensitivity."""
    if matrix is None:
        matrix = normalize_plans_matrix(case)
    contract = case.get("prospective_contract") or {}
    budget_cap = contract.get("budget_cap_usd")
    deadline = contract.get("deadline_days")

    feasible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for row in matrix.get("rows", []):
        reasons = list(row.get("hard_domain_or_resource_violations") or [])
        tcost = row["cost_breakdown_usd"]["total_usd"]
        tdays = row["schedule_breakdown_days"]["total_days"]

        if budget_cap is not None and tcost is not None and float(tcost) > float(budget_cap) + 1e-9:
            reasons.append(f"Total normalized cost ${tcost} > budget_cap ${budget_cap}")
        if deadline is not None and tdays is not None and float(tdays) > float(deadline) + 1e-9:
            reasons.append(f"Total normalized schedule {tdays}d > deadline {deadline}d")

        if reasons:
            rejected.append({
                "version": row["version"],
                "disposition": "rejected_hard_constraints",
                "rejection_reasons": reasons,
                "total_usd": tcost,
                "total_days": tdays,
            })
            continue

        # Strict completeness gate: NEVER rank plans with missing cost, schedule, approval, or comparison scores!
        needed: list[str] = []
        needed.extend(row.get("missing_cost_fields") or [])
        needed.extend(row.get("missing_schedule_fields") or [])
        if row.get("approval_status") in {"UNVERIFIED", "IN_REVIEW_UNVERIFIED"}:
            needed.append(f"approval_status({row.get('approval_status')})")
        for score_key in ("scientific_fit_score", "evidence_score"):
            if row.get(score_key) is None:
                needed.append(score_key)

        if needed:
            unresolved.append({
                "version": row["version"],
                "disposition": "unresolved_incomplete_evidence",
                "unresolved_fields": sorted(set(needed)),
                "explicit_zero_fields": row.get("explicit_zero_fields", []),
                "reason": "Kept out of forced ranking until missing cost, schedule, approval, or comparison scores are verified (missing != zero).",
            })
            continue

        feasible.append(dict(row))

    pareto_front, dominance_pairs = _compute_pareto(feasible)
    max_cost = max((float(r["cost_breakdown_usd"]["total_usd"]) for r in feasible), default=1.0)
    max_days = max((float(r["schedule_breakdown_days"]["total_days"]) for r in feasible), default=1.0)

    for r in feasible:
        r["pareto_optimal"] = r["version"] in pareto_front
        r["normalized_dimension_utilities"] = _dimension_utilities(r, budget_cap, deadline, max_cost, max_days)
        r["profile_scores"] = {
            pname: _score_under_profile(r, w, budget_cap, deadline, max_cost, max_days)
            for pname, w in WEIGHT_PROFILES.items()
        }
        r["composite_utility"] = r["profile_scores"]["balanced"]

    feasible.sort(key=lambda r: (not r["pareto_optimal"], -r["composite_utility"], r["cost_breakdown_usd"]["total_usd"]))
    selected = feasible[0]["version"] if feasible else None

    for alt in feasible[1:]:
        diff_cost = round(alt["cost_breakdown_usd"]["total_usd"] - feasible[0]["cost_breakdown_usd"]["total_usd"], 2)
        diff_days = round(alt["schedule_breakdown_days"]["total_days"] - feasible[0]["schedule_breakdown_days"]["total_days"], 2)
        rejected.append({
            "version": alt["version"],
            "disposition": "feasible_alternative_not_selected",
            "rejection_reasons": [
                f"Lower balanced utility ({alt['composite_utility']} vs {feasible[0]['composite_utility']}); "
                f"pareto_optimal={alt['pareto_optimal']}; delta vs selected: cost {diff_cost:+.2f} USD, schedule {diff_days:+.2f}d"
            ],
            "total_usd": alt["cost_breakdown_usd"]["total_usd"],
            "total_days": alt["schedule_breakdown_days"]["total_days"],
        })

    # Multi-profile rank stability + 1-factor budget/deadline flip sensitivity
    sensitivity: dict[str, Any] = {}
    if feasible:
        win = feasible[0]
        profile_winners: dict[str, str] = {}
        for pname in WEIGHT_PROFILES:
            best_p = max(feasible, key=lambda r: (r["profile_scores"][pname], -r["cost_breakdown_usd"]["total_usd"]))
            profile_winners[pname] = best_p["version"]
        agree_cnt = sum(1 for v in profile_winners.values() if v == win["version"])
        sensitivity = {
            "selected_version": win["version"],
            "weight_profile_winners": profile_winners,
            "rank_stability_rate": f"{agree_cnt}/{len(WEIGHT_PROFILES)}",
            "budget_slack_usd": round(float(budget_cap) - win["cost_breakdown_usd"]["total_usd"], 2) if budget_cap is not None else None,
            "deadline_slack_days": round(float(deadline) - win["schedule_breakdown_days"]["total_days"], 2) if deadline is not None else None,
            "flip_condition": (
                f"Selection becomes infeasible if budget_cap < ${win['cost_breakdown_usd']['total_usd']} "
                f"or deadline < {win['schedule_breakdown_days']['total_days']}d"
            ),
        }

    # All-infeasible or incomplete remediation guidance
    remediation: list[str] = []
    all_infeasible = bool(rejected) and not feasible and not unresolved
    if all_infeasible:
        remediation.append("All candidate plans violate confirmed hard constraints; do NOT force a winner.")
        for rej in rejected:
            remediation.append(f"To rescue {rej['version']}, relax or resolve: {'; '.join(rej['rejection_reasons'])}")
    elif not feasible and unresolved:
        remediation.append("No fully verifiable feasible plan exists yet; collect missing contract evidence before issuing a final total ranking.")
        for u in unresolved:
            remediation.append(f"Resolve for {u['version']}: {', '.join(u['unresolved_fields'])}")

    # Capacity adequacy sensitivity (check max_samples vs planned_samples, consumables/kit & machine_hours per sample)
    max_samples_cap = contract.get("sample_pool_available") or contract.get("max_samples")
    samples_per_kit = contract.get("samples_per_unit") or contract.get("samples_per_kit") or 24
    hours_per_sample = contract.get("machine_hours_per_sample") or 0.5
    capacity_sensitivity: list[dict[str, Any]] = []
    for row in matrix.get("rows", []):
        ps = row.get("planned_samples")
        units_alloc = float(row.get("allocated_consumable_units") or 0.0)
        hrs_alloc = float(row.get("allocated_machine_hours") or 0.0)
        cap_issues: list[str] = []
        if max_samples_cap is not None and ps is not None and int(ps) > int(max_samples_cap):
            cap_issues.append(f"CAPACITY_SHORTFALL: planned_samples({ps}) > max_samples({max_samples_cap})")
        if max_samples_cap is not None and units_alloc > 0 and (units_alloc * float(samples_per_kit)) < int(max_samples_cap):
            cap_issues.append(
                f"SCALE_UP_KIT_WARNING: {units_alloc} kit units cover ~{int(units_alloc * float(samples_per_kit))} samples (< max_samples {max_samples_cap}); scaling to full pool requires additional procurement"
            )
        if max_samples_cap is not None and hrs_alloc > 0 and hrs_alloc < int(max_samples_cap) * float(hours_per_sample):
            cap_issues.append(
                f"SCALE_UP_MACHINE_HOUR_WARNING: {hrs_alloc}h covers ~{int(hrs_alloc / float(hours_per_sample))} samples at {hours_per_sample}h/sample (< max_samples {max_samples_cap})"
            )
        capacity_sensitivity.append({
            "version": row["version"],
            "planned_samples": ps,
            "max_samples_contract": max_samples_cap,
            "allocated_consumable_units": units_alloc,
            "allocated_machine_hours": hrs_alloc,
            "capacity_status": "SUFFICIENT_AT_PLANNED_SCALE_WITH_SCALEUP_NOTE" if any("SCALE_UP" in x for x in cap_issues) else ("CAPACITY_SHORTFALL" if cap_issues else "ADEQUATE"),
            "notes": cap_issues,
        })

    selected_decision_criteria: dict[str, Any] = {}
    if feasible:
        win = feasible[0]
        sc = str(win.get("stopping_criterion") or "").strip()
        rt = str(win.get("risk_trigger") or "").strip()
        fb = str(win.get("fallback_plan") or "").strip()
        selected_decision_criteria = {
            "selected_version": win["version"],
            "primary_readout": win.get("primary_readout", "UNSPECIFIED"),
            "inference_unit": win.get("inference_unit", "UNSPECIFIED"),
            "stopping_criterion": sc or "MISSING_STOPPING_CRITERION_DEFINE_BEFORE_RUN",
            "risk_trigger": rt or "MISSING_RISK_TRIGGER_DEFINE_BEFORE_RUN",
            "fallback_plan": fb or "MISSING_FALLBACK_PLAN_DEFINE_BEFORE_RUN",
            "schedule_formula": win["schedule_breakdown_days"].get("schedule_formula", ""),
        }

    return {
        "$schema": "aligned-plan-choice-report/v2",
        "case_id": case.get("case_id", "unknown"),
        "title": case.get("title", ""),
        "selected_version": selected,
        "selected_plan_decision_criteria": selected_decision_criteria,
        "all_infeasible": all_infeasible,
        "incomplete_comparison_warning": bool(unresolved),
        "pareto_frontier_versions": sorted(pareto_front),
        "pareto_dominance_pairs": dominance_pairs,
        "comparability_audit": matrix.get("comparability_audit", {}),
        "capacity_sensitivity": capacity_sensitivity,
        "prospective_boundary_notice": (
            "Normalized cost/schedule rankings evaluate organizer prospective contracts only; "
            "they are not confirmed vendor quotes, booked instrument slots, or granted approvals."
        ),
        "ranked_feasible": feasible,
        "unresolved_with_reasons": unresolved,
        "rejected_with_reasons": rejected,
        "sensitivity_analysis": sensitivity,
        "remediation_actions": remediation,
        "aligned_matrix": matrix,
    }


def _render_markdown(rep: dict[str, Any]) -> str:
    lines = [
        f"# Aligned Plan Comparison & Choice: {rep['title']} ({rep['case_id']})",
        "",
        f"- **Selected Plan**: `{rep['selected_version']}`",
        f"- **Pareto Frontier**: `{rep['pareto_frontier_versions']}`",
        f"- **Rank Stability (5 Weight Profiles)**: `{rep['sensitivity_analysis'].get('rank_stability_rate', 'N/A')}`",
        "",
        f"> **Prospective Boundary Notice**: {rep['prospective_boundary_notice']}",
        "",
        "## Horizontal Aligned Matrix",
        "",
        "| Version | Total Cost (USD) | Total Days | Approval | Fit | Evidence | Risk | Disposition / Rationale |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rej_map = {r["version"]: "; ".join(r["rejection_reasons"]) for r in rep["rejected_with_reasons"]}
    unres_map = {r["version"]: ", ".join(r["unresolved_fields"]) for r in rep["unresolved_with_reasons"]}
    for row in rep["aligned_matrix"]["rows"]:
        v = row["version"]
        c_str = str(row["cost_breakdown_usd"]["total_usd"]) if row["cost_comparable"] else "UNVERIFIED"
        d_str = str(row["schedule_breakdown_days"]["total_days"]) if row["schedule_comparable"] else "UNVERIFIED"
        f_str = str(row["scientific_fit_score"]) if row["scientific_fit_score"] is not None else "UNSPECIFIED"
        e_str = str(row["evidence_score"]) if row["evidence_score"] is not None else "UNSPECIFIED"
        if v in unres_map:
            st = f"UNRESOLVED (missing != 0): {unres_map[v]}"
        elif v == rep["selected_version"]:
            st = "SELECTED (Pareto optimal)"
        else:
            st = f"DROP: {rej_map.get(v, 'Ranked lower')}"
        lines.append(f"| {v} | {c_str} | {d_str} | {row['approval_status']} | {f_str} | {e_str} | {row['risk_level']} | {st} |")

    if rep["pareto_dominance_pairs"]:
        lines.extend(["", "## Pareto Dominance", ""])
        for dp in rep["pareto_dominance_pairs"]:
            lines.append(f"- {dp['reason']}")
    if rep["sensitivity_analysis"]:
        sa = rep["sensitivity_analysis"]
        lines.extend([
            "", "## Sensitivity Analysis", "",
            f"- **Weight Profile Winners**: `{sa.get('weight_profile_winners')}`",
            f"- **Budget Slack (USD)**: `{sa.get('budget_slack_usd')}` | **Deadline Slack (d)**: `{sa.get('deadline_slack_days')}`",
            f"- **Flip Condition**: {sa.get('flip_condition')}",
        ])
    if rep.get("selected_plan_decision_criteria"):
        dc = rep["selected_plan_decision_criteria"]
        lines.extend([
            "", "## Selected Plan Executable Decision & Stopping Criteria", "",
            f"- **Selected Version**: `{dc.get('selected_version')}` | **Schedule Formula**: `{dc.get('schedule_formula')}`",
            f"- **Primary Readout / Inference Unit**: `{dc.get('primary_readout')}` / `{dc.get('inference_unit')}`",
            f"- **Stopping Criterion**: {dc.get('stopping_criterion')}",
            f"- **Risk Trigger**: {dc.get('risk_trigger')} | **Fallback Plan**: {dc.get('fallback_plan')}",
        ])
    if rep.get("capacity_sensitivity"):
        lines.extend(["", "## Sample & Resource Capacity Sensitivity", ""])
        for cs in rep["capacity_sensitivity"]:
            lines.append(f"- `{cs['version']}`: status=`{cs['capacity_status']}` (planned={cs['planned_samples']}, max_contract={cs['max_samples_contract']}, units={cs['allocated_consumable_units']}, hours={cs['allocated_machine_hours']}) {'; '.join(cs['notes'])}")
    if rep["remediation_actions"]:
        lines.extend(["", "## Remediation / Missing-Evidence Actions", ""])
        for rem in rep["remediation_actions"]:
            lines.append(f"- {rem}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate and select best aligned experimental plan with Pareto & sensitivity analysis.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--matrix", default=None)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stem", default="plan_comparison")
    args = parser.parse_args(argv)

    case = json.loads(Path(args.case).read_text(encoding="utf-8-sig"))
    mat = json.loads(Path(args.matrix).read_text(encoding="utf-8-sig")) if args.matrix else None
    rep = evaluate_plan_choice(case, matrix=mat)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_p = out_dir / f"{args.stem}.json"
    md_p = out_dir / f"{args.stem}.md"
    json_p.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_p.write_text(_render_markdown(rep), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "selected_version": rep["selected_version"],
        "pareto_frontier": rep["pareto_frontier_versions"],
        "unresolved_count": len(rep["unresolved_with_reasons"]),
        "json": str(json_p),
        "md": str(md_p),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
