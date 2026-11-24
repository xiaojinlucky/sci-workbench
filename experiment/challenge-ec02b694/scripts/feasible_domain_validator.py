#!/usr/bin/env python3
"""Finite-domain parameter bounds, step-grid, coupled/linear/mutex constraint, and infeasibility relaxation validator."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import operator
from pathlib import Path
from typing import Any

OPERATORS = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt, "==": operator.eq}


def _finite_num(val: Any) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _enumerate_param_grid(pdef: dict[str, Any], max_pts: int = 500) -> list[Any] | None:
    if "allowed_values" in pdef:
        vals = list(pdef["allowed_values"])
        return vals if len(vals) <= max_pts else None
    lo = _finite_num(pdef.get("min"))
    hi = _finite_num(pdef.get("max"))
    smax = _finite_num(pdef.get("safety_max"))
    eff_hi = min(hi, smax) if (hi is not None and smax is not None) else (smax if smax is not None else hi)
    step = _finite_num(pdef.get("step"))
    if lo is None or eff_hi is None or step is None or step <= 0:
        return None
    if eff_hi < lo - 1e-9:
        return []
    count = int(math.floor((eff_hi - lo) / step + 1e-9)) + 1
    if count > max_pts:
        return None
    return [round(lo + i * step, 8) for i in range(count)]


def check_candidate_feasibility(domain_spec: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    violations: list[str] = []
    param_defs: dict[str, dict[str, Any]] = domain_spec.get("parameters", {})

    for pname, pdef in param_defs.items():
        if pname not in params or params[pname] is None:
            violations.append(f"missing required parameter: {pname}")
            continue
        val = params[pname]
        if "allowed_values" in pdef:
            if val not in pdef["allowed_values"]:
                violations.append(f"{pname}={val!r} not in allowed_values {pdef['allowed_values']}")
            continue
        fval = _finite_num(val)
        if fval is None:
            violations.append(f"{pname}={val!r} must be a finite numeric setting")
            continue
        lo = _finite_num(pdef.get("min"))
        hi = _finite_num(pdef.get("max"))
        smax = _finite_num(pdef.get("safety_max"))
        eff_hi = min(hi, smax) if (hi is not None and smax is not None) else (smax if smax is not None else hi)
        if lo is not None and eff_hi is not None and eff_hi < lo - 1e-9:
            violations.append(f"INFEASIBLE_CONSTRAINT_CONFLICT on {pname}: min={lo} > effective_max={eff_hi}")
        elif (lo is not None and fval < lo - 1e-9) or (eff_hi is not None and fval > eff_hi + 1e-9):
            violations.append(f"{pname}={fval} outside feasible [{lo}, {eff_hi}] {pdef.get('unit', '')}".strip())
        step = _finite_num(pdef.get("step"))
        if step and step > 0 and lo is not None and (eff_hi is None or eff_hi >= lo - 1e-9):
            rem = abs((fval - lo) / step - round((fval - lo) / step))
            if rem > 1e-4:
                violations.append(f"{pname}={fval} violates step_size={step} from min={lo}")

    # Pairwise coupled constraints: lhs op rhs + offset
    for rule in domain_spec.get("coupled_constraints", []):
        lhs, rhs, op = rule.get("lhs_param"), rule.get("rhs_param"), rule.get("op", "<=")
        evaluate = OPERATORS.get(op)
        if evaluate is None:
            violations.append(f"unsupported coupled constraint operator: {op!r}")
            continue
        if lhs not in params or rhs not in params:
            violations.append(f"coupled constraint missing setting: {lhs!r} {op} {rhs!r}")
            continue
        a, b = _finite_num(params[lhs]), _finite_num(params[rhs])
        offset = _finite_num(rule.get("offset", 0.0))
        if a is None or b is None or offset is None:
            violations.append(f"coupled constraint requires finite numeric settings: {lhs!r} {op} {rhs!r}")
        elif not evaluate(a, b + offset):
            violations.append(f"coupled constraint failed: {lhs}({a}) {op} {rhs}({b}) + {offset}")

    # General linear combination constraints: sum(coeff * param) op rhs
    for lrule in domain_spec.get("linear_constraints", []):
        terms = lrule.get("terms") or {}
        op = lrule.get("op", "<=")
        rhs_val = _finite_num(lrule.get("rhs"))
        evaluate = OPERATORS.get(op)
        if evaluate is None or rhs_val is None or not terms:
            violations.append(f"invalid linear_constraint specification: {lrule}")
            continue
        total = 0.0
        bad = False
        for p, coeff in terms.items():
            pv = _finite_num(params.get(p))
            cv = _finite_num(coeff)
            if pv is None or cv is None:
                violations.append(f"linear_constraint missing/non-numeric param {p}")
                bad = True
                break
            total += pv * cv
        if not bad and not evaluate(total, rhs_val + (1e-9 if op in {"<=", "<"} else (-1e-9 if op in {">=", ">"} else 0.0))):
            label = lrule.get("name") or str(terms)
            violations.append(f"linear_constraint '{label}' failed: lhs={round(total, 6)} {op} {rhs_val}")

    # Mutually exclusive discrete combinations: [{"param_a": val_a, "param_b": val_b, "reason": ...}]
    for mx in domain_spec.get("mutual_exclusions", []):
        conds = mx.get("when") or {}
        if conds and all(params.get(k) == v for k, v in conds.items()):
            violations.append(f"mutual_exclusion violated ({conds}): {mx.get('reason', 'incompatible settings')}")

    return {"feasible": len(violations) == 0, "violations": violations}


def validate_all_candidates(domain_spec: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    domain_conflicts: list[dict[str, Any]] = []
    param_defs: dict[str, dict[str, Any]] = domain_spec.get("parameters", {})

    for pname, pdef in param_defs.items():
        lo = _finite_num(pdef.get("min"))
        hi = _finite_num(pdef.get("max"))
        smax = _finite_num(pdef.get("safety_max"))
        eff_hi = min(hi, smax) if (hi is not None and smax is not None) else (smax if smax is not None else hi)
        if lo is not None and eff_hi is not None and eff_hi < lo - 1e-9:
            min_relax = round(lo - eff_hi, 6)
            domain_conflicts.append({
                "parameter": pname,
                "min": lo,
                "effective_max": eff_hi,
                "min_relaxation_amount": min_relax,
                "gap": min_relax,
                "code": "INFEASIBLE_CONSTRAINT_CONFLICT",
                "relaxation_suggestion": f"Lower {pname}.min by >= {min_relax} (to <= {eff_hi}) OR verify hardware/material upgrade raising safety_max by >= {min_relax} (to >= {lo})",
            })

    # Finite discrete grid enumeration (when total Cartesian size <= 20,000)
    grid_summary: dict[str, Any] = {"enumerated": False}
    grids: dict[str, list[Any]] = {}
    can_enum = bool(param_defs) and not domain_conflicts
    prod_size = 1
    if can_enum:
        for pname, pdef in param_defs.items():
            g = _enumerate_param_grid(pdef)
            if g is None:
                can_enum = False
                break
            grids[pname] = g
            prod_size *= max(1, len(g))
            if prod_size > 20000:
                can_enum = False
                break

    if can_enum and grids:
        names = list(grids.keys())
        feasible_pts = 0
        sample_feasible: list[dict[str, Any]] = []
        for combo in itertools.product(*(grids[n] for n in names)):
            pt = dict(zip(names, combo))
            if check_candidate_feasibility(domain_spec, pt)["feasible"]:
                feasible_pts += 1
                if len(sample_feasible) < 10:
                    sample_feasible.append(pt)
        grid_summary = {
            "enumerated": True,
            "cartesian_size": prod_size,
            "feasible_point_count": feasible_pts,
            "sample_feasible_points": sample_feasible,
        }
        if feasible_pts == 0:
            domain_conflicts.append({
                "parameter": "joint_coupled_domain",
                "code": "INFEASIBLE_CONSTRAINT_CONFLICT",
                "relaxation_suggestion": "Zero finite grid points satisfy all bounds + coupled/linear/mutex constraints simultaneously; inspect binding coupled constraints.",
            })

    results = []
    for idx, cand in enumerate(candidates, start=1):
        cid = str(cand.get("plan_id", f"plan_v{idx}"))
        chk = check_candidate_feasibility(domain_spec, cand.get("parameters", {}))
        results.append({"plan_id": cid, **chk})

    feasible_count = sum(1 for r in results if r["feasible"])
    return {
        "$schema": "feasible-domain-validation/v2",
        "ok": len(domain_conflicts) == 0 and feasible_count > 0,
        "all_candidates_feasible": len(domain_conflicts) == 0 and all(r["feasible"] for r in results),
        "feasible_candidate_count": feasible_count,
        "domain_conflicts": domain_conflicts,
        "finite_grid_summary": grid_summary,
        "candidate_checks": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate finite-domain bounds, steps, coupled/linear/mutex rules, and feasibility conflicts.")
    parser.add_argument("--domain", required=True, help="Domain constraints JSON")
    parser.add_argument("--candidates", required=True, help="Candidate plans JSON")
    parser.add_argument("--out", default="", help="Optional validation JSON output")
    args = parser.parse_args(argv)

    dom = json.loads(Path(args.domain).read_text(encoding="utf-8-sig"))
    raw_c = json.loads(Path(args.candidates).read_text(encoding="utf-8-sig"))
    cands = raw_c if isinstance(raw_c, list) else raw_c.get("plans", [])
    rep = validate_all_candidates(dom, cands)
    encoded = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(encoded, encoding="utf-8")
    print(json.dumps({
        "ok": rep["ok"],
        "domain_conflicts": len(rep["domain_conflicts"]),
        "feasible_candidates": rep["feasible_candidate_count"],
        "checked": len(rep["candidate_checks"]),
    }, ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
