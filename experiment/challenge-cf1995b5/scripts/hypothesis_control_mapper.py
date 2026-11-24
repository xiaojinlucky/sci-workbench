#!/usr/bin/env python3
"""Hypothesis, inference-unit/power, confounder-control estimability, and SOP-pinning auditor."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

VALID_RESEARCH_TYPES = {
    "empirical_causal",
    "engineering_optimization",
    "exploratory_discovery",
    "computational_benchmark",
}


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _approx_two_sample_power(n_per_arm: int, cohen_d: float, alpha: float = 0.05) -> float:
    """Two-sided normal approximation power for independent units per arm."""
    if n_per_arm <= 1 or cohen_d <= 0:
        return 0.0
    # Two-sided z_alpha/2 using rational approximation for common alphas
    if abs(alpha - 0.05) < 1e-6:
        z_crit = 1.959964
    elif abs(alpha - 0.01) < 1e-6:
        z_crit = 2.575829
    elif abs(alpha - 0.10) < 1e-6:
        z_crit = 1.644854
    else:
        z_crit = 1.959964
    ncp = abs(cohen_d) * math.sqrt(n_per_arm / 2.0)
    return round(min(1.0, max(0.0, 1.0 - _norm_cdf(z_crit - ncp) + _norm_cdf(-z_crit - ncp))), 4)


def _min_n_for_power(cohen_d: float, target_power: float = 0.80, alpha: float = 0.05) -> int | None:
    if cohen_d <= 0:
        return None
    for n in range(2, 5000):
        if _approx_two_sample_power(n, cohen_d, alpha=alpha) >= target_power:
            return n
    return None


def _audit_design_estimability(design_cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Check augmented-factorial / incomplete-block incidence matrix for estimable vs confounded contrasts."""
    if not design_cells:
        return {
            "checked": False,
            "structure": "not_provided",
            "estimable_contrasts": [],
            "confounded_or_missing_cells": [],
            "warnings": [],
        }

    treatments: set[str] = set()
    blocks: set[str] = set()
    control_arms: set[str] = set()
    present: set[tuple[str, str]] = set()
    by_block: dict[str, set[str]] = defaultdict(set)

    for cell in design_cells:
        arm = str(cell.get("arm", "")).strip()
        blk = str(cell.get("block_or_timepoint", "ALL")).strip() or "ALL"
        if not arm:
            continue
        treatments.add(arm)
        blocks.add(blk)
        present.add((arm, blk))
        by_block[blk].add(arm)
        if cell.get("is_control"):
            control_arms.add(arm)

    missing_cells: list[dict[str, str]] = []
    for arm in sorted(treatments):
        for blk in sorted(blocks):
            if (arm, blk) not in present:
                missing_cells.append({"arm": arm, "block_or_timepoint": blk})

    estimable: list[str] = []
    confounded: list[str] = []
    warnings: list[str] = []

    if len(blocks) > 1 and control_arms:
        for blk in sorted(blocks):
            blk_ctrls = sorted(control_arms & by_block[blk])
            blk_treats = sorted((treatments - control_arms) & by_block[blk])
            if blk_ctrls and blk_treats:
                for t in blk_treats:
                    estimable.append(f"{t} vs {blk_ctrls[0]} within block/timepoint '{blk}'")
            elif blk_treats and not blk_ctrls:
                for t in blk_treats:
                    confounded.append(
                        f"{t} in block/timepoint '{blk}' has NO concurrent control arm (confounded with block/timepoint shift)"
                    )
        ctrl_blocks = {blk for blk in blocks if control_arms & by_block[blk]}
        if len(ctrl_blocks) < len(blocks):
            warnings.append(
                f"Augmented/incomplete control design detected: control arm present only in {sorted(ctrl_blocks)} "
                f"out of {sorted(blocks)}; do NOT compare treated blocks without concurrent controls directly against another block's control."
            )
    elif missing_cells:
        warnings.append(f"Incomplete factorial grid with {len(missing_cells)} missing arm x block cells.")

    structure = "complete_factorial" if not missing_cells else ("augmented_or_incomplete_control" if confounded else "incomplete_factorial")
    return {
        "checked": True,
        "structure": structure,
        "arm_count": len(treatments),
        "block_count": len(blocks),
        "missing_cell_count": len(missing_cells),
        "missing_cells": missing_cells,
        "estimable_contrasts": estimable,
        "confounded_or_missing_cells": confounded,
        "warnings": warnings,
    }


def _audit_sop_pins(case: dict[str, Any]) -> dict[str, Any]:
    """Verify SOP version pinning and detect coexisting conflicting SOP versions (e.g. v1 vs v2)."""
    sop_versions: dict[str, set[str]] = defaultdict(set)
    unpinned: list[str] = []

    def _collect(item: Any, ctx: str) -> None:
        if not isinstance(item, dict):
            return
        sop_id = str(item.get("sop_id", "")).strip()
        sop_ver = str(item.get("sop_version", "")).strip()
        if sop_id:
            if not sop_ver:
                unpinned.append(f"{ctx}:{sop_id}")
            else:
                sop_versions[sop_id].add(sop_ver)

    for idx, ref in enumerate(case.get("sop_references") or [], start=1):
        _collect(ref, f"sop_references[{idx}]")
    for cand in case.get("finite_candidates") or []:
        ver = str(cand.get("version", "v"))
        for idx, st in enumerate(cand.get("steps") or [], start=1):
            _collect(st, f"{ver}.steps[{idx}]")

    conflicts = [
        {"sop_id": sid, "versions_found": sorted(vers)}
        for sid, vers in sorted(sop_versions.items())
        if len(vers) > 1
    ]
    return {
        "pinned_sops": {sid: sorted(vers) for sid, vers in sorted(sop_versions.items())},
        "unpinned_sops": sorted(set(unpinned)),
        "version_conflicts": conflicts,
        "ok": len(conflicts) == 0 and len(unpinned) == 0,
    }


def audit_controls_and_hypothesis(case: dict[str, Any]) -> dict[str, Any]:
    """Validate hypothesis operationalization, inference unit/power, confounder controls, estimability, and SOP pinning."""
    hyp = case.get("hypothesis") or {}
    ctrls = case.get("controls") or []
    stat_design = case.get("statistical_design") or {}
    eln = case.get("eln_handover") or {}

    errors: list[str] = []
    warnings: list[str] = []

    rtype = str(case.get("research_type", "empirical_causal")).strip()
    if rtype not in VALID_RESEARCH_TYPES:
        warnings.append(f"Unrecognized research_type {rtype!r}; expected one of {sorted(VALID_RESEARCH_TYPES)}")

    if not str(hyp.get("statement", "")).strip():
        errors.append("Missing testable hypothesis statement")
    if not str(hyp.get("iv", "")).strip() or not str(hyp.get("dv", "")).strip():
        errors.append("Hypothesis lacks operationalized independent variable (iv) or dependent variable (dv)")
    if not str(hyp.get("dv_unit", "")).strip() and not str(hyp.get("measurement_method", "")).strip():
        warnings.append("DV measurement_method or dv_unit not explicitly declared; add readout method and unit")
    if not str(hyp.get("falsification_criterion", "")).strip():
        warnings.append("Missing quantitative falsification_criterion defining what outcome refutes the hypothesis")

    # Control arms and confounder mapping
    ctrl_types = {str(c.get("type", "")).lower().strip() for c in ctrls if isinstance(c, dict)}
    if len(ctrls) < 2:
        errors.append(f"Insufficient control arms ({len(ctrls)} < 2 minimum); avoid single-blank-only designs")
    if not (ctrl_types & {"positive", "benchmark", "reference_standard"}):
        warnings.append("No positive/reference-standard control arm declared; assay dynamic floor/ceiling verification weakened")
    if not (ctrl_types & {"negative", "vehicle", "blank", "sham", "untreated"}):
        warnings.append("No negative/vehicle/blank control arm declared; baseline drift and background cannot be separated")

    addressed: set[str] = set()
    for c in ctrls:
        if not isinstance(c, dict):
            continue
        raw_addr = c.get("addresses_confounder")
        if isinstance(raw_addr, list):
            for x in raw_addr:
                if str(x).strip():
                    addressed.add(str(x).strip())
        elif str(raw_addr or "").strip():
            addressed.add(str(raw_addr).strip())
    for b in stat_design.get("blocking_factors") or []:
        if str(b).strip():
            addressed.add(str(b).strip())

    confounders = [str(x).strip() for x in (hyp.get("confounders") or []) if str(x).strip()]
    unmatched = [c for c in confounders if c not in addressed]
    if unmatched:
        errors.append(f"Confounders lack matching control arm or blocking factor: {unmatched}")

    # Inference unit, randomization/blinding, and statistical power audit
    bio_unit = str(stat_design.get("inference_unit", "")).strip()
    tech_unit = str(stat_design.get("technical_unit", "")).strip()
    if not bio_unit:
        warnings.append("statistical_design.inference_unit not declared; risk of confusing technical replicates with independent units")
    if bio_unit and tech_unit and bio_unit.lower() == tech_unit.lower():
        warnings.append(f"inference_unit and technical_unit are identical ({bio_unit!r}); verify no pseudoreplication")

    if not str(stat_design.get("randomization_strategy", "")).strip():
        warnings.append("No randomization_strategy specified (e.g. stratified block randomization by batch/plate)")

    power_audit: dict[str, Any] = {"evaluated": False}
    cohen_d = stat_design.get("expected_cohen_d")
    if cohen_d is None and stat_design.get("expected_delta") is not None and stat_design.get("expected_sd"):
        try:
            cohen_d = abs(float(stat_design["expected_delta"])) / float(stat_design["expected_sd"])
        except (TypeError, ValueError, ZeroDivisionError):
            cohen_d = None
    planned_n = stat_design.get("planned_biological_replicates")
    alpha = float(stat_design.get("alpha", 0.05))
    target_power = float(stat_design.get("target_power", 0.80))
    if cohen_d is not None:
        d_val = float(cohen_d)
        min_n = _min_n_for_power(d_val, target_power=target_power, alpha=alpha)
        ach_power = _approx_two_sample_power(int(planned_n), d_val, alpha=alpha) if planned_n else None
        power_audit = {
            "evaluated": True,
            "method": "two_sided_normal_approximation",
            "cohen_d": round(d_val, 4),
            "alpha": alpha,
            "target_power": target_power,
            "planned_independent_units_per_arm": planned_n,
            "achieved_power_approx": ach_power,
            "recommended_min_units_per_arm": min_n,
            "underpowered": (ach_power is not None and ach_power < target_power),
            "power_method_assumptions_and_limits": [
                "Assumes independent biological/experimental units, equal group variance, and approximate normality of the primary readout.",
                "For small sample sizes (n < 10 per arm), the normal approximation slightly overestimates true non-central t-test power (typically requiring +1 to +2 additional units per arm).",
                "For skewed, bounded, or ordinal endpoints requiring nonparametric tests (e.g., Mann-Whitney U / Kruskal-Wallis), inflate n by ~15% (asymptotic relative efficiency ~0.864-0.955) or confirm via simulation.",
            ],
        }
        if power_audit["underpowered"]:
            warnings.append(
                f"Planned independent units n={planned_n} yields approx power {ach_power} < target {target_power} "
                f"(recommended min n={min_n} for Cohen's d={round(d_val, 3)})"
            )
    if planned_n is not None and int(planned_n) < 3:
        warnings.append(f"planned_biological_replicates={planned_n} < 3; statistical variance estimation will be unstable")

    # Augmented factorial / incomplete block estimability audit
    estimability = _audit_design_estimability(case.get("design_cells") or [])
    warnings.extend(estimability["warnings"])

    # SOP version pinning & ELN traceability audit
    sop_audit = _audit_sop_pins(case)
    if sop_audit["version_conflicts"]:
        errors.append(f"SOP_VERSION_CONFLICT: {sop_audit['version_conflicts']}")
    if sop_audit["unpinned_sops"]:
        warnings.append(f"Unpinned SOP versions (add explicit sop_version): {sop_audit['unpinned_sops']}")

    eln_missing = [k for k in ("experiment_id_pattern", "raw_data_binding", "deviation_log_fields") if not eln.get(k)]
    if eln_missing:
        warnings.append(f"ELN handover traceability incomplete (missing {eln_missing})")

    return {
        "$schema": "hypothesis-control-audit/v2",
        "ok": len(errors) == 0,
        "case_id": case.get("case_id", "unknown"),
        "research_type": rtype,
        "iv": hyp.get("iv"),
        "dv": hyp.get("dv"),
        "dv_unit": hyp.get("dv_unit"),
        "measurement_method": hyp.get("measurement_method"),
        "inference_unit": bio_unit or None,
        "technical_unit": tech_unit or None,
        "control_count": len(ctrls),
        "control_types": sorted(ctrl_types),
        "unmatched_confounders": unmatched,
        "power_audit": power_audit,
        "estimability_audit": estimability,
        "sop_pin_audit": sop_audit,
        "eln_handover_complete": len(eln_missing) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate hypothesis, inference unit/power, confounder controls, estimability, and SOP pinning.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    case = json.loads(Path(args.case).read_text(encoding="utf-8-sig"))
    rep = audit_controls_and_hypothesis(case)
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
