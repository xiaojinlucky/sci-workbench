#!/usr/bin/env python3
"""Pure-stdlib intermediate input schema/source_ref pre-validator and /input/case.json output_contract auto-syncer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from result_guard import init_result, update_result


def _extract_contract_keys(case_obj: dict[str, Any]) -> list[str]:
    oc = case_obj.get("output_contract")
    if isinstance(oc, list):
        return [str(k).strip() for k in oc if str(k).strip()]
    if isinstance(oc, dict):
        if isinstance(oc.get("required_fields"), list):
            return [str(k).strip() for k in oc["required_fields"] if str(k).strip()]
        if isinstance(oc.get("fields"), dict):
            return [str(k).strip() for k in oc["fields"] if str(k).strip()]
        if isinstance(oc.get("fields"), list):
            return [str(k).strip() for k in oc["fields"] if str(k).strip()]
        return [str(k).strip() for k in oc if str(k).strip() not in {"schema_version", "description"}]
    return []


def validate_input_payload(doc: dict[str, Any], mode: str) -> dict[str, Any]:
    """Fail-loud structural and source_ref anchor validator for intermediate skill JSON inputs."""
    issues: list[str] = []
    warnings: list[str] = []
    src_refs_checked = 0
    src_refs_present = 0

    def _chk_src(item: Any, ctx: str, *, required: bool = True) -> None:
        nonlocal src_refs_checked, src_refs_present
        src_refs_checked += 1
        val = str(item.get("source_ref", "")).strip() if isinstance(item, dict) else ""
        if val:
            src_refs_present += 1
        elif required:
            issues.append(f"{ctx}: missing non-empty source_ref anchor")
        else:
            warnings.append(f"{ctx}: missing source_ref anchor")

    if mode == "cf1995b5":
        for req in ("case_id", "hypothesis", "controls", "prospective_contract", "finite_candidates"):
            if req not in doc or not doc[req]:
                issues.append(f"parsed_goal missing required key: {req}")
        for i, c in enumerate(doc.get("controls") or [], start=1):
            _chk_src(c, f"controls[{i}]", required=True)
        contract = doc.get("prospective_contract") or {}
        for pname, bnd in (contract.get("parameter_domains") or {}).items():
            _chk_src(bnd, f"parameter_domains.{pname}", required=True)
        for rname, rmeta in (contract.get("resources") or {}).items():
            _chk_src(rmeta, f"resources.{rname}", required=False)
        if len(doc.get("finite_candidates") or []) < 3:
            issues.append("finite_candidates must contain at least 3 versioned candidate plans")

    elif mode == "2e2c5820":
        for req in ("case_id", "prospective_contract", "candidate_plans"):
            if req not in doc or not doc[req]:
                issues.append(f"parsed_plans missing required key: {req}")
        contract = doc.get("prospective_contract") or {}
        for rname, rmeta in (contract.get("resources") or {}).items():
            _chk_src(rmeta, f"resources.{rname}", required=True)
        for pname, bnd in (contract.get("parameter_domains") or {}).items():
            _chk_src(bnd, f"parameter_domains.{pname}", required=True)
        for i, p in enumerate(doc.get("candidate_plans") or [], start=1):
            _chk_src(p, f"candidate_plans[{i}]", required=True)

    elif mode == "ec02b694":
        if "parameters" in doc:
            for pname, pdef in (doc.get("parameters") or {}).items():
                _chk_src(pdef, f"domain.parameters.{pname}", required=True)
        elif "plans" in doc or isinstance(doc, list):
            plans = doc if isinstance(doc, list) else doc.get("plans", [])
            if not plans:
                issues.append("candidates_3v contains zero candidate plans")
            for i, p in enumerate(plans, start=1):
                for rk in ("plan_id", "parameters", "search_strategy", "stopping_criterion"):
                    if not p.get(rk):
                        issues.append(f"plans[{i}] missing required key: {rk}")
        else:
            issues.append("Unrecognized ec02b694 input payload; expected domain_spec ('parameters') or candidates_3v ('plans')")

    elif mode == "7be76e91":
        for req in ("case_id", "prospective_contract", "candidate_plans"):
            if req not in doc or not doc[req]:
                issues.append(f"parsed_plans missing required key: {req}")
        for i, p in enumerate(doc.get("candidate_plans") or [], start=1):
            _chk_src(p, f"candidate_plans[{i}]", required=True)
            if "version" not in p:
                issues.append(f"candidate_plans[{i}] missing 'version'")

    elif mode == "8354c69a":
        for req in ("case_id", "trends", "anomalies", "causal_hypotheses", "verification_checklist", "next_experiments", "retained_ambiguities"):
            if req not in doc:
                issues.append(f"interpretation_report missing required key: {req}")
        for i, a in enumerate(doc.get("anomalies") or [], start=1):
            for rk in ("source_file", "location", "threshold_rule", "observed_value"):
                if not str(a.get(rk, "")).strip():
                    issues.append(f"anomalies[{i}] missing required field: {rk}")
    else:
        issues.append(f"Unsupported validation mode: {mode!r}")

    cov = round(100.0 * src_refs_present / src_refs_checked, 1) if src_refs_checked else 100.0
    return {
        "$schema": "input-schema-source-ref-audit/v1",
        "ok": len(issues) == 0,
        "mode": mode,
        "source_ref_coverage_pct": cov,
        "source_refs_checked": src_refs_checked,
        "issues": issues,
        "warnings": warnings,
    }


def sync_result_with_contract(
    *,
    case_json_path: Path,
    report_path: Path,
    result_path: Path,
    task_id: str,
    artifacts: list[str],
) -> dict[str, Any]:
    """Read /input/case.json output_contract and deterministically seal /app/output/result.json."""
    case_obj = json.loads(case_json_path.read_text(encoding="utf-8-sig"))
    report_obj = json.loads(report_path.read_text(encoding="utf-8-sig"))
    case_id = str(case_obj.get("case_id") or report_obj.get("case_id") or "unknown")
    contract_keys = _extract_contract_keys(case_obj)

    if not result_path.is_file():
        init_result(result_path, case_id=case_id, task_id=task_id)

    prospective_notice = (
        report_obj.get("prospective_boundary_notice")
        or report_obj.get("prospective_notice")
        or "Evaluated strictly from supplied input sources and prospective organizer contracts; not claimed live lab execution, vendor quotes, or granted approvals."
    )

    # Derive universal 3-pillar deliverable summary required across all 5 experiment challenges
    experimental_plan = {
        "primary_report_artifact": report_path.name,
        "recommended_or_selected": (
            report_obj.get("recommended_plan_version")
            or report_obj.get("selected_version")
            or report_obj.get("recommended_plan")
        ),
        "verdicts": (
            {r["version"]: r["overall_verdict"] for r in report_obj.get("plan_reviews", [])}
            if isinstance(report_obj.get("plan_reviews"), list)
            else None
        ),
        "summary_counts": {
            "versions_or_plans": len(
                report_obj.get("versions_evaluated")
                or report_obj.get("plan_reviews")
                or report_obj.get("ranked_feasible")
                or report_obj.get("feasible_ranked")
                or []
            ),
            "trends": len(report_obj.get("trends") or []),
            "anomalies": len(report_obj.get("anomalies") or []),
        },
    }
    rationale_and_risks = {
        "prospective_boundary_notice": prospective_notice,
        "retained_ambiguities": report_obj.get("retained_ambiguities", []),
        "domain_conflicts": report_obj.get("domain_conflicts", []),
        "interaction_reversal_detected": report_obj.get("interaction_reversal_detected"),
        "sensitivity_analysis": report_obj.get("sensitivity_analysis"),
        "six_element_traceability": report_obj.get("six_element_traceability"),
    }
    confirmation_items = (
        report_obj.get("confirmation_items")
        or [
            {"item": "Pre-execution safety, ethics, equipment availability, and expert sign-off", "status": "UNVERIFIED_REQUIRED"}
        ]
    )

    gaps: list[dict[str, Any]] = []
    if report_obj.get("all_infeasible"):
        gaps.append({"code": "ALL_CANDIDATES_INFEASIBLE", "severity": "warning", "message": "All candidate plans failed hard constraints; review relaxation options."})
    if report_obj.get("domain_conflicts"):
        gaps.append({"code": "INFEASIBLE_CONSTRAINT_CONFLICT", "severity": "warning", "message": f"{len(report_obj['domain_conflicts'])} domain conflict(s) detected."})
    if report_obj.get("unresolved_with_reasons") or report_obj.get("unresolved_missing_info"):
        gaps.append({"code": "INCOMPLETE_CONTRACT_EVIDENCE", "severity": "warning", "message": "One or more candidate plans lack verified cost, schedule, or approval evidence (missing != 0)."})
    if isinstance(report_obj.get("data_gaps_summary"), dict) and int(report_obj["data_gaps_summary"].get("data_gap_count", 0)) > 0:
        gaps.append({"code": "DATA_GAPS_PRESENT", "severity": "warning", "message": f"{report_obj['data_gaps_summary']['data_gap_count']} non-numeric/missing/BLQ/saturated reading(s) preserved separately."})

    patch: dict[str, Any] = {
        "case_id": case_id,
        "task_id": task_id,
        "artifacts": sorted(set(artifacts or [report_path.name])),
        "experimental_plan": experimental_plan,
        "rationale_and_risks": rationale_and_risks,
        "confirmation_items": confirmation_items,
        "prospective_boundary_notice": prospective_notice,
        "output_contract_keys_synced": contract_keys,
        "gaps": gaps,
        "metrics": {
            "artifact_count": len(set(artifacts or [report_path.name])),
            "output_contract_keys_bound": len(contract_keys),
            "gap_count": len(gaps),
        },
        "metric_provenance": {
            "artifact_count": report_path.name,
            "output_contract_keys_bound": case_json_path.name,
            "gap_count": report_path.name,
        },
        "findings": [f"Deterministic output_contract sync completed for case {case_id} from {report_path.name}."],
    }

    # Map every declared output_contract key directly from report_obj or structured sections
    fallback_lookup: dict[str, Any] = {
        "case_id": case_id,
        "experimental_plan": experimental_plan,
        "plan": experimental_plan,
        "rationale_and_risks": rationale_and_risks,
        "evidence_and_risks": rationale_and_risks,
        "confirmation_items": confirmation_items,
        "confirmations": confirmation_items,
        "prospective_boundary_notice": prospective_notice,
        "recommended_plan_version": experimental_plan["recommended_or_selected"],
        "selected_version": experimental_plan["recommended_or_selected"],
        "recommended_plan": experimental_plan["recommended_or_selected"],
        "verdicts": experimental_plan["verdicts"],
        "retained_ambiguities": report_obj.get("retained_ambiguities", []),
        "trends": report_obj.get("trends"),
        "anomalies": report_obj.get("anomalies"),
        "causal_hypotheses": report_obj.get("causal_hypotheses"),
        "verification_checklist": report_obj.get("verification_checklist"),
        "next_experiments": report_obj.get("next_experiments"),
        "gaps": gaps,
    }
    missing_contract_keys: list[str] = []
    for ck in contract_keys:
        if ck in patch:
            continue
        if ck in report_obj:
            patch[ck] = report_obj[ck]
        elif ck in fallback_lookup and fallback_lookup[ck] is not None:
            patch[ck] = fallback_lookup[ck]
        else:
            patch[ck] = {"status": "UNSPECIFIED_IN_REPORT", "source_report": report_path.name}
            missing_contract_keys.append(ck)

    if missing_contract_keys:
        patch["gaps"].append({
            "code": "OUTPUT_CONTRACT_KEY_UNPOPULATED",
            "severity": "warning",
            "fields": missing_contract_keys,
        })

    final_doc = update_result(result_path, patch, finalize=True)
    return {
        "$schema": "output-contract-sync-report/v1",
        "ok": True,
        "case_id": case_id,
        "status": final_doc["status"],
        "result_path": str(result_path),
        "output_contract_keys_synced": contract_keys,
        "unpopulated_contract_keys": missing_contract_keys,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate intermediate input schema/source_ref and auto-sync /input/case.json output_contract into result.json.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate-input")
    p_val.add_argument("--input", required=True)
    p_val.add_argument("--mode", required=True, choices=("cf1995b5", "2e2c5820", "ec02b694", "7be76e91", "8354c69a"))
    p_val.add_argument("--out", default="")

    p_sync = sub.add_parser("sync-result")
    p_sync.add_argument("--case-json", required=True)
    p_sync.add_argument("--report", required=True)
    p_sync.add_argument("--result-json", required=True)
    p_sync.add_argument("--task-id", required=True)
    p_sync.add_argument("--artifact", action="append", default=[])
    p_sync.add_argument("--out", default="")

    args = parser.parse_args(argv)
    if args.cmd == "validate-input":
        doc = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        rep = validate_input_payload(doc, args.mode)
        txt = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(txt, encoding="utf-8")
        print(txt, end="")
        return 0 if rep["ok"] else 1

    rep = sync_result_with_contract(
        case_json_path=Path(args.case_json),
        report_path=Path(args.report),
        result_path=Path(args.result_json),
        task_id=args.task_id,
        artifacts=args.artifact,
    )
    txt = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(txt, encoding="utf-8")
    print(txt, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
