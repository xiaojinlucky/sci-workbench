#!/usr/bin/env python3
"""Strict null-vs-zero resource, price, machine-hour, and throughput evaluator over prospective contracts."""

from __future__ import annotations

import argparse
import json
import math
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


def evaluate_plan_costs(contract: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    raw_prices = contract.get("unit_prices") or {}
    rates: dict[str, float | None] = {k: _finite_nonneg(v) for k, v in raw_prices.items()}
    budget_cap = _finite_nonneg(contract.get("budget_cap"))
    time_cap_hr = _finite_nonneg(contract.get("time_limit_hours"))
    hourly_rate = _finite_nonneg(contract.get("machine_hour_rate"))
    labor_rate = _finite_nonneg(contract.get("labor_rate_per_hour"))
    currency = str(contract.get("currency", "USD"))

    evaluated: list[dict[str, Any]] = []
    for idx, cand in enumerate(candidates, start=1):
        pid = str(cand.get("plan_id", f"plan_v{idx}"))
        n_runs = int(cand.get("planned_runs", 1))
        missing_inputs: list[str] = []
        explicit_zeros: list[str] = []

        # One-time setup fee: prefer explicit candidate override, else contract.setup_cost (preserving null if both null when declared)
        if cand.get("one_time_setup_cost") is not None:
            setup_cost = _finite_nonneg(cand.get("one_time_setup_cost"))
            if setup_cost is None:
                missing_inputs.append("one_time_setup_cost")
            elif setup_cost == 0.0:
                explicit_zeros.append("one_time_setup_cost")
        elif "setup_cost" in contract:
            setup_cost = _finite_nonneg(contract.get("setup_cost"))
            if setup_cost is None:
                missing_inputs.append("contract.setup_cost")
            elif setup_cost == 0.0:
                explicit_zeros.append("contract.setup_cost")
        elif "one_time_setup_cost" in cand:
            setup_cost = None
            missing_inputs.append("one_time_setup_cost")
        else:
            setup_cost = 0.0

        # Machine hours check: support explicit total_machine_hours OR per-run machine_hours * n_runs
        if cand.get("total_machine_hours") is not None:
            tot_mh = _finite_nonneg(cand.get("total_machine_hours"))
            if tot_mh is None:
                missing_inputs.append("total_machine_hours")
                slot_hours = None
            else:
                if tot_mh == 0.0:
                    explicit_zeros.append("total_machine_hours")
                slot_hours = round(tot_mh, 4)
        else:
            raw_mh = cand.get("machine_hours_per_run", cand.get("machine_hours"))
            mh_per_run = _finite_nonneg(raw_mh)
            if mh_per_run is None:
                missing_inputs.append("machine_hours")
                slot_hours = None
            else:
                if mh_per_run == 0.0:
                    explicit_zeros.append("machine_hours")
                slot_hours = round(mh_per_run * n_runs, 4)

        # Resource consumption line items
        per_run_total = 0.0
        line_items: list[dict[str, Any]] = []
        usage = cand.get("resource_usage") or {}
        for rkey, raw_qty in sorted(usage.items()):
            qty = _finite_nonneg(raw_qty)
            if qty is None:
                missing_inputs.append(f"usage:{rkey}")
                continue
            if qty == 0.0:
                explicit_zeros.append(f"usage:{rkey}")
            if rkey not in rates or rates[rkey] is None:
                missing_inputs.append(f"rate:{rkey}")
                continue
            rate_val = rates[rkey]
            assert rate_val is not None
            if rate_val == 0.0:
                explicit_zeros.append(f"rate:{rkey}")
            sub = round(qty * rate_val * n_runs, 4)
            per_run_total += sub
            line_items.append({
                "resource": rkey,
                "qty_per_run": qty,
                "runs": n_runs,
                "unit_price": rate_val,
                "subtotal": sub,
            })

        # Machine hour charge if hourly_rate specified and not already in usage
        machine_charge = 0.0
        if hourly_rate is not None and slot_hours is not None and "machine_hour" not in usage:
            machine_charge = round(slot_hours * hourly_rate, 4)

        # Optional labor hours
        labor_charge = 0.0
        if "labor_hours" in cand and cand.get("labor_hours") is not None:
            lh = _finite_nonneg(cand.get("labor_hours"))
            if lh is None or labor_rate is None:
                missing_inputs.append("labor_hours_or_rate")
            else:
                labor_charge = round(lh * labor_rate * n_runs, 4)

        verifiable = len(missing_inputs) == 0
        total_cost = round((setup_cost or 0.0) + per_run_total + machine_charge + labor_charge, 4) if verifiable else None
        within_budget = None if (budget_cap is None or total_cost is None) else (total_cost <= budget_cap + 1e-9)
        within_time = None if (time_cap_hr is None or slot_hours is None) else (slot_hours <= time_cap_hr + 1e-9)
        arith_ok = verifiable and (within_budget is not False) and (within_time is not False)

        # Machine hours provenance audit: bind explicit source_ref (never guess missing run duration)
        mh_src_ref = str(cand.get("machine_hours_source_ref") or cand.get("source_ref") or "").strip() or None
        unverified_mh = (slot_hours is not None and mh_src_ref is None)

        evaluated.append({
            "plan_id": pid,
            "planned_runs": n_runs,
            "total_machine_hours": slot_hours,
            "machine_hours_source_ref": mh_src_ref,
            "unverified_machine_hours_assumption": unverified_mh,
            "one_time_setup_cost": setup_cost if verifiable else None,
            "consumables_cost": round(per_run_total, 4) if verifiable else None,
            "machine_charge": machine_charge if verifiable else None,
            "labor_charge": labor_charge if verifiable else None,
            "total_estimated_cost": total_cost,
            "currency": currency,
            "budget_utilization": round(total_cost / budget_cap, 4) if (total_cost is not None and budget_cap and budget_cap > 0) else None,
            "within_budget": within_budget,
            "within_time_cap": within_time,
            "missing_rate_or_usage_keys": sorted(set(missing_inputs)),
            "explicit_zero_fields": sorted(set(explicit_zeros)),
            "line_items": line_items,
            "arithmetic_ok": arith_ok,
        })

    return {
        "$schema": "prospective-resource-cost-audit/v2",
        "ok": any(e["arithmetic_ok"] for e in evaluated),
        "all_plans_arithmetic_ok": all(e["arithmetic_ok"] for e in evaluated),
        "estimate_scope": "prospective_planning_arithmetic_not_vendor_quote_or_measured_run",
        "budget_cap": budget_cap,
        "time_limit_hours": time_cap_hr,
        "plans": evaluated,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute strict null-vs-zero prospective cost and booking hours.")
    parser.add_argument("--contract", required=True, help="Resource/price/booking contract JSON")
    parser.add_argument("--candidates", required=True, help="Candidate plans JSON")
    parser.add_argument("--out", default="", help="Optional cost report JSON")
    args = parser.parse_args(argv)

    contract = json.loads(Path(args.contract).read_text(encoding="utf-8-sig"))
    raw_c = json.loads(Path(args.candidates).read_text(encoding="utf-8-sig"))
    cands = raw_c if isinstance(raw_c, list) else raw_c.get("plans", [])
    rep = evaluate_plan_costs(contract, cands)
    encoded = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(encoded, encoding="utf-8")
    print(json.dumps({"ok": rep["ok"], "plan_count": len(rep["plans"]), "estimate_scope": rep["estimate_scope"]}, ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
