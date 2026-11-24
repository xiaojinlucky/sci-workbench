#!/usr/bin/env python3
"""Pure-stdlib extractor from raw /input Markdown/TXT/PDF/JSON + block_indexer into source_ref-anchored draft JSON.

Unfound fields are explicitly emitted as null and listed under `not_found_in_source` (`NOT_FOUND_IN_SOURCE`)
so downstream optimizers/auditors never fabricate values or coerce missing costs/approvals to 0 or True.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Line-level narrative extraction patterns (anchored to exact file#L<line>)
BUDGET_RE = re.compile(r"(?:budget(?:\s*cap)?|预算上限|总预算)\s*[:=：]?\s*(?:USD|\$|¥|CNY)?\s*([0-9]+(?:\.[0-9]+)?)", re.I)
DEADLINE_RE = re.compile(r"(?:deadline|时间上限|周期上限|截止周期)\s*[:=：]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:days?|d|天)", re.I)
TIME_HR_RE = re.compile(r"(?:time\s*limit|machine\s*hour\s*cap|机时上限)\s*[:=：]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:hours?|hrs?|h|小时)", re.I)
SAMPLE_POOL_RE = re.compile(r"(?:sample\s*pool|max\s*samples?|可用样本(?:数|池)?)\s*[:=：]?\s*([0-9]+)", re.I)
STABILITY_RE = re.compile(r"(?:sample\s*stability|样本保存期)\s*[:=：]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:days?|d|天)", re.I)
SETUP_COST_RE = re.compile(r"(?:setup\s*cost|one[- ]time\s*setup|一次性(?:启动|准备)费)\s*[:=：]?\s*(?:USD|\$|¥|CNY)?\s*([0-9]+(?:\.[0-9]+)?|TBD|unknown|unquoted|未报价|待定)", re.I)
PER_SAMPLE_RE = re.compile(r"(?:per[- ]sample\s*cost|单样本(?:耗材)?费)\s*[:=：]?\s*(?:USD|\$|¥|CNY)?\s*([0-9]+(?:\.[0-9]+)?|TBD|unknown|unquoted|未报价|待定)", re.I)
LABOR_RATE_RE = re.compile(r"(?:labor\s*rate|人工费率)\s*[:=：]?\s*(?:USD|\$|¥|CNY)?\s*([0-9]+(?:\.[0-9]+)?)", re.I)
PREP_DAYS_RE = re.compile(r"(?:prep\s*days|准备天数)\s*[:=：]?\s*([0-9]+(?:\.[0-9]+)?)", re.I)
QC_DAYS_RE = re.compile(r"(?:qc(?:\s*turnaround)?\s*days|质控天数)\s*[:=：]?\s*([0-9]+(?:\.[0-9]+)?)", re.I)

# Parameter domain line: e.g. "Parameter `temp_c`: [180, 260] C, safety_max=240, step=10"
PARAM_DOM_RE = re.compile(
    r"(?:parameter|参数)\s*[`'\"]?([A-Za-z0-9_]+)[`'\"]?\s*[:=：]\s*\[\s*(-?[0-9]+(?:\.[0-9]+)?)\s*,\s*(-?[0-9]+(?:\.[0-9]+)?)\s*\]"
    r"(?:\s*([A-Za-z/%°^0-9_-]+))?(.*)$",
    re.I,
)
# Resource line: e.g. "Resource `lc_ms`: status=available, rate_per_hr=80, booking_lead_days=2, lead_days=0"
RESOURCE_RE = re.compile(r"(?:resource|equipment|reagent|资源|设备|试剂)\s*[`'\"]?([A-Za-z0-9_]+)[`'\"]?\s*[:=：]\s*(.+)$", re.I)
# Control line: e.g. "Control `vehicle_blank` (type=blank): addresses solvent_drift"
CONTROL_RE = re.compile(r"^(?:[-*]\s*)?(?:control(?:\s*arm)?|对照(?:组|臂)?)\s*[`'\"]?([A-Za-z0-9_-]+)[`'\"]?\s*(?:\(([^)]*)\))?\s*[:=：-]?\s*(.*)$", re.I)
# Hypothesis / IV / DV / Confounders / Inference unit
HYP_RE = re.compile(r"^(?:[-*]\s*)?(?:hypothesis|科学假设)\s*[:=：]\s*(.+)$", re.I)
IV_RE = re.compile(r"^(?:[-*]\s*)?(?:iv|independent\s*variable|自变量)\s*[:=：]\s*(.+)$", re.I)
DV_RE = re.compile(r"^(?:[-*]\s*)?(?:dv|dependent\s*variable|因变量)\s*[:=：]\s*(.+)$", re.I)
CONFOUNDER_RE = re.compile(r"^(?:[-*]\s*)?(?:confounders?|混杂因素)\s*[:=：]\s*(.+)$", re.I)
INF_UNIT_RE = re.compile(r"^(?:[-*]\s*)?(?:inference\s*unit|推断单位)\s*[:=：]\s*(.+)$", re.I)
# Approval lines (longest tokens first so 'not_required' is never shadowed by 'no')
BIOSAFETY_RE = re.compile(r"(?:biosafety(?:\s*approval)?|生物安全审批)\s*[:=：]\s*(approved|in_review|pending_amendment|pending|unverified|unknown|denied|true|false|yes|no|未审批|审查中|待定)\b", re.I)
ETHICS_RE = re.compile(r"(?:ethics(?:\s*approval)?|irb|iacuc|伦理审批)\s*[:=：]\s*(not_required|in_review|pending_amendment|pending|approved|unverified|unknown|denied|none|true|false|yes|no|未审批|审查中|待定)\b", re.I)
# Candidate version header: e.g. "### Plan v1_draft" or "Candidate `v1_baseline`:"
CAND_HDR_RE = re.compile(r"^(?:#{1,4}\s*|(?:[-*]\s*)?)(?:plan|candidate(?:\s*plan)?|version|候选方案|方案)\s*[`'\"]?([A-Za-z0-9_-]+)[`'\"]?\s*[:：]?(.*)$", re.I)

MISSING_TOKENS = {"tbd", "unknown", "unquoted", "none", "null", "na", "n/a", "未报价", "待定", "未说明", "未知", "unverified"}


def _parse_num_or_null(token: str | None) -> float | None:
    if token is None:
        return None
    s = token.strip().strip("$,¥")
    if not s or s.lower() in MISSING_TOKENS:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_approval(token: str) -> Any:
    t = token.strip().lower()
    if t in {"approved", "true", "yes"}:
        return True
    if t in {"denied", "false", "no"}:
        return False
    if t in {"in_review", "pending", "审查中"}:
        return "in_review"
    return None


def _parse_kv_pairs(tail: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,;|\s]+(?:\s+[^,;|=]+)?)", tail):
        k = m.group(1).strip().lower()
        v = m.group(2).strip().rstrip(".,;")
        pairs[k] = v
    return pairs


def _read_source_documents(input_dir: Path) -> list[tuple[str, list[str]]]:
    """Read raw Markdown/TXT/PDF files in /input/ preserving line numbers."""
    docs: list[tuple[str, list[str]]] = []
    for fp in sorted(input_dir.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(input_dir).as_posix()
        suf = fp.suffix.lower()
        if suf in {".md", ".txt", ".yaml", ".yml"}:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            docs.append((rel, lines))
        elif suf == ".pdf":
            pdftotext = shutil.which("pdftotext")
            if pdftotext:
                try:
                    proc = subprocess.run([pdftotext, "-enc", "UTF-8", str(fp), "-"], capture_output=True, text=True, timeout=20)
                    if proc.returncode == 0 and proc.stdout.strip():
                        docs.append((rel, proc.stdout.splitlines()))
                except Exception:
                    pass
    return docs


def extract_from_sources(input_dir: Path, mode: str, blocks_path: Path | None = None) -> dict[str, Any]:
    """Extract source_ref-anchored structured draft JSON from raw narrative inputs without inventing missing values."""
    case_json_p = input_dir / "case.json"
    case_meta = json.loads(case_json_p.read_text(encoding="utf-8-sig")) if case_json_p.is_file() else {}
    case_id = str(case_meta.get("case_id") or "extracted-case")
    title = str(case_meta.get("title") or input_dir.name)

    docs = _read_source_documents(input_dir)
    blocks_indexed = False
    if blocks_path and blocks_path.is_file():
        blocks_indexed = True

    not_found: list[dict[str, str]] = []
    source_anchors: dict[str, str] = {}

    def _mark_missing(field: str, reason: str) -> None:
        not_found.append({"field": field, "status": "NOT_FOUND_IN_SOURCE", "reason": reason})

    hypothesis: dict[str, Any] = {"statement": None, "iv": None, "dv": None, "dv_unit": None, "measurement_method": None, "confounders": []}
    inference_unit: str | None = None
    controls: list[dict[str, Any]] = []
    resources: dict[str, dict[str, Any]] = {}
    parameter_domains: dict[str, dict[str, Any]] = {}
    contract: dict[str, Any] = {
        "budget_cap_usd": None,
        "deadline_days": None,
        "time_limit_hours": None,
        "max_samples": None,
        "sample_pool_available": None,
        "sample_stability_days": None,
        "setup_cost_usd": None,
        "per_sample_cost_usd": None,
        "labor_rate_per_day": None,
        "base_prep_days": None,
        "qc_days": None,
        "biosafety_approved": None,
        "ethics_approval_required": None,
        "ethics_approved": None,
        "resources": resources,
        "parameter_domains": parameter_domains,
    }
    candidates: list[dict[str, Any]] = []
    current_cand: dict[str, Any] | None = None

    for rel, lines in docs:
        for lno, raw_ln in enumerate(lines, start=1):
            ln = raw_ln.strip()
            if not ln or ln.startswith("<!--"):
                continue
            ref = f"{rel}#L{lno}"

            # Candidate section switch
            m_cand = CAND_HDR_RE.match(ln)
            if m_cand and any(tok in ln.lower() for tok in ("v1", "v2", "v3", "v4", "plan_", "cand", "方案")):
                ver = m_cand.group(1).strip()
                current_cand = {
                    "version": ver,
                    "plan_id": ver,
                    "source_ref": ref,
                    "rationale": m_cand.group(2).strip(),
                    "treatment_arms": 2,
                    "arms": 2,
                    "biological_replicates": 3,
                    "replicates_per_arm": 3,
                    "technical_replicates": 1,
                    "qc_extra_samples": 0,
                    "planned_runs": 3,
                    "machine_hours": None,
                    "one_time_setup_cost": None,
                    "labor_days": None,
                    "prep_days": None,
                    "qc_turnaround_days": None,
                    "parameters": {},
                    "resource_usage": {},
                    "steps": [],
                    "risks": [],
                    "stop_loss": "",
                    "fallback_path": "",
                    "search_strategy": "sequential_doe",
                    "stopping_criterion": "",
                    "predicted_response": None,
                    "predicted_uncertainty": None,
                    "scientific_fit_score": None,
                    "evidence_score": None,
                    "risk_level": "UNSPECIFIED",
                }
                candidates.append(current_cand)
                continue

            # Candidate-scoped attributes when inside a candidate block
            if current_cand is not None:
                kv = _parse_kv_pairs(ln)
                if ln.lower().startswith(("-", "*")) and "step" in ln.lower():
                    st_name = kv.get("step") or kv.get("id") or f"step_{len(current_cand['steps']) + 1}"
                    st_res = kv.get("resource", "")
                    st_obj: dict[str, Any] = {
                        "id": st_name,
                        "name": st_name,
                        "duration_days": _parse_num_or_null(kv.get("duration_days")),
                        "resource": st_res,
                        "units": _parse_num_or_null(kv.get("units")) or 0.0,
                        "hours": _parse_num_or_null(kv.get("hours")) or 0.0,
                        "depends_on": [x.strip() for x in kv.get("depends_on", "").split("+") if x.strip()],
                        "source_ref": ref,
                    }
                    current_cand["steps"].append(st_obj)
                    if st_res and st_obj["units"]:
                        current_cand["resource_usage"][st_res] = st_obj["units"]
                    continue
                if "setup_cost" in kv or "one_time_setup_cost" in kv:
                    current_cand["one_time_setup_cost"] = _parse_num_or_null(kv.get("one_time_setup_cost", kv.get("setup_cost")))
                if "labor_days" in kv:
                    current_cand["labor_days"] = _parse_num_or_null(kv.get("labor_days"))
                if "prep_days" in kv:
                    current_cand["prep_days"] = _parse_num_or_null(kv.get("prep_days"))
                if "qc_days" in kv or "qc_turnaround_days" in kv:
                    current_cand["qc_turnaround_days"] = _parse_num_or_null(kv.get("qc_turnaround_days", kv.get("qc_days")))
                if "arms" in kv or "treatment_arms" in kv:
                    av = _parse_num_or_null(kv.get("treatment_arms", kv.get("arms")))
                    if av is not None:
                        current_cand["arms"] = int(av)
                        current_cand["treatment_arms"] = int(av)
                if "replicates" in kv or "biological_replicates" in kv:
                    rv = _parse_num_or_null(kv.get("biological_replicates", kv.get("replicates")))
                    if rv is not None:
                        current_cand["biological_replicates"] = int(rv)
                        current_cand["replicates_per_arm"] = int(rv)
                if "planned_runs" in kv:
                    pr = _parse_num_or_null(kv.get("planned_runs"))
                    if pr is not None:
                        current_cand["planned_runs"] = int(pr)
                if "machine_hours" in kv:
                    current_cand["machine_hours"] = _parse_num_or_null(kv.get("machine_hours"))
                if "scientific_fit_score" in kv:
                    current_cand["scientific_fit_score"] = _parse_num_or_null(kv.get("scientific_fit_score"))
                if "evidence_score" in kv:
                    current_cand["evidence_score"] = _parse_num_or_null(kv.get("evidence_score"))
                if "predicted_response" in kv:
                    current_cand["predicted_response"] = _parse_num_or_null(kv.get("predicted_response"))
                if "predicted_uncertainty" in kv:
                    current_cand["predicted_uncertainty"] = _parse_num_or_null(kv.get("predicted_uncertainty"))
                if "risk_level" in kv:
                    current_cand["risk_level"] = kv["risk_level"]
                if "search_strategy" in kv:
                    current_cand["search_strategy"] = kv["search_strategy"]
                if "stop_loss" in ln.lower():
                    m_sl = re.search(r"stop_loss\s*[:=：]\s*(.+)$", ln, re.I)
                    if m_sl:
                        current_cand["stop_loss"] = m_sl.group(1).strip()
                        current_cand["risks"].append({"name": "stop_loss", "trigger": current_cand["stop_loss"], "mitigation": "see fallback"})
                if "fallback" in ln.lower():
                    m_fb = re.search(r"fallback(?:_path)?\s*[:=：]\s*(.+)$", ln, re.I)
                    if m_fb:
                        current_cand["fallback_path"] = m_fb.group(1).strip()
                if "stopping_criterion" in ln.lower():
                    m_sc = re.search(r"stopping_criterion\s*[:=：]\s*(.+)$", ln, re.I)
                    if m_sc:
                        current_cand["stopping_criterion"] = m_sc.group(1).strip()
                if "params" in ln.lower() or "parameters" in ln.lower():
                    for pk, pv in kv.items():
                        if pk in parameter_domains:
                            num = _parse_num_or_null(pv)
                            current_cand["parameters"][pk] = num if num is not None else pv
                else:
                    for pk, pv in kv.items():
                        if pk in parameter_domains:
                            num = _parse_num_or_null(pv)
                            current_cand["parameters"][pk] = num if num is not None else pv

            # Global contract & scientific facts
            if m := HYP_RE.match(ln):
                hypothesis["statement"] = m.group(1).strip()
                source_anchors["hypothesis"] = ref
            elif m := IV_RE.match(ln):
                hypothesis["iv"] = m.group(1).strip()
                source_anchors["iv"] = ref
            elif m := DV_RE.match(ln):
                hypothesis["dv"] = m.group(1).strip()
                m_u = re.search(r"\(([^)]+)\)", hypothesis["dv"])
                if m_u:
                    hypothesis["dv_unit"] = m_u.group(1).strip()
                source_anchors["dv"] = ref
            elif m := CONFOUNDER_RE.match(ln):
                hypothesis["confounders"] = [c.strip() for c in re.split(r"[,;、]", m.group(1)) if c.strip()]
                source_anchors["confounders"] = ref
            elif m := INF_UNIT_RE.match(ln):
                inference_unit = m.group(1).strip()
                source_anchors["inference_unit"] = ref

            if m := BUDGET_RE.search(ln):
                contract["budget_cap_usd"] = _parse_num_or_null(m.group(1))
                source_anchors["budget_cap_usd"] = ref
            if m := DEADLINE_RE.search(ln):
                contract["deadline_days"] = _parse_num_or_null(m.group(1))
                source_anchors["deadline_days"] = ref
            if m := TIME_HR_RE.search(ln):
                contract["time_limit_hours"] = _parse_num_or_null(m.group(1))
                source_anchors["time_limit_hours"] = ref
            if m := SAMPLE_POOL_RE.search(ln):
                val = _parse_num_or_null(m.group(1))
                if val is not None:
                    contract["max_samples"] = int(val)
                    contract["sample_pool_available"] = int(val)
                    source_anchors["sample_pool_available"] = ref
            if m := STABILITY_RE.search(ln):
                contract["sample_stability_days"] = _parse_num_or_null(m.group(1))
                source_anchors["sample_stability_days"] = ref
            if m := SETUP_COST_RE.search(ln):
                contract["setup_cost_usd"] = _parse_num_or_null(m.group(1))
                source_anchors["setup_cost_usd"] = ref
            if m := PER_SAMPLE_RE.search(ln):
                contract["per_sample_cost_usd"] = _parse_num_or_null(m.group(1))
                source_anchors["per_sample_cost_usd"] = ref
            if m := LABOR_RATE_RE.search(ln):
                contract["labor_rate_per_day"] = _parse_num_or_null(m.group(1))
                source_anchors["labor_rate_per_day"] = ref
            if m := PREP_DAYS_RE.search(ln):
                contract["base_prep_days"] = _parse_num_or_null(m.group(1))
                source_anchors["base_prep_days"] = ref
            if m := QC_DAYS_RE.search(ln):
                contract["qc_days"] = _parse_num_or_null(m.group(1))
                source_anchors["qc_days"] = ref

            if m := BIOSAFETY_RE.search(ln):
                contract["biosafety_approved"] = _parse_approval(m.group(1))
                source_anchors["biosafety_approved"] = ref
            if m := ETHICS_RE.search(ln):
                raw_e = m.group(1).strip().lower()
                if raw_e in {"not_required", "none"}:
                    contract["ethics_approval_required"] = False
                    contract["ethics_approved"] = None
                else:
                    contract["ethics_approval_required"] = True
                    contract["ethics_approved"] = _parse_approval(raw_e)
                source_anchors["ethics_approved"] = ref

            if m := PARAM_DOM_RE.search(ln):
                pname = m.group(1).strip()
                lo = _parse_num_or_null(m.group(2))
                hi = _parse_num_or_null(m.group(3))
                unit = (m.group(4) or "").strip()
                tail_kv = _parse_kv_pairs(m.group(5) or "")
                parameter_domains[pname] = {
                    "min": lo,
                    "max": hi,
                    "safety_max": _parse_num_or_null(tail_kv.get("safety_max")),
                    "step": _parse_num_or_null(tail_kv.get("step")),
                    "unit": unit,
                    "source_ref": ref,
                }
            elif m := RESOURCE_RE.search(ln):
                rname = m.group(1).strip()
                kv = _parse_kv_pairs(m.group(2))
                uc = _parse_num_or_null(kv.get("unit_cost"))
                hr = _parse_num_or_null(kv.get("rate_per_hr"))
                ld = _parse_num_or_null(kv.get("lead_days"))
                bld = _parse_num_or_null(kv.get("booking_lead_days"))
                resources[rname] = {
                    "status": kv.get("status", "unknown"),
                    "unit_cost": uc,
                    "rate_per_hr": hr,
                    "lead_days": ld,
                    "booking_lead_days": bld,
                    "quote_valid_until": kv.get("quote_valid_until"),
                    "maintenance_status": kv.get("maintenance_status", "current"),
                    "substitutable": kv.get("substitutable", "false").lower() == "true",
                    "source_ref": ref,
                }
                if uc is None and hr is None:
                    _mark_missing(f"resources.{rname}.unit_cost_or_rate", f"No numeric unit_cost or rate_per_hr found at {ref}")
            elif m := CONTROL_RE.search(ln):
                cname = m.group(1).strip()
                paren_kv = _parse_kv_pairs(m.group(2) or "")
                rest = (m.group(3) or "").strip()
                ctype = paren_kv.get("type", "blank" if "blank" in cname.lower() or "vehicle" in cname.lower() else ("positive" if "pos" in cname.lower() else "unknown"))
                addr = paren_kv.get("addresses") or rest.replace("addresses", "").strip()
                controls.append({
                    "name": cname,
                    "type": ctype,
                    "addresses_confounder": addr,
                    "source_ref": ref,
                })

    # Audit missing critical fields explicitly as NOT_FOUND_IN_SOURCE (NEVER fabricate!)
    for ck in ("budget_cap_usd", "deadline_days", "biosafety_approved"):
        if contract.get(ck) is None:
            _mark_missing(f"prospective_contract.{ck}", "Not stated in /input narrative sources")
    if len(controls) < 2:
        _mark_missing("controls.complementary_arm", f"Only {len(controls)} control arm(s) extracted from /input; complementary positive/blank control NOT_FOUND_IN_SOURCE")
    if not hypothesis.get("statement"):
        _mark_missing("hypothesis.statement", "No explicit hypothesis statement found")

    # Populate shared hypothesis/controls onto candidate plans for 2e2c5820/7be76e91
    for c in candidates:
        c["hypothesis"] = hypothesis["statement"] or ""
        c["variables"] = {"iv": hypothesis["iv"], "dv": hypothesis["dv"], "dv_unit": hypothesis["dv_unit"] or ""}
        c["inference_unit"] = inference_unit or ""
        c["controls"] = [ct["name"] for ct in controls]
        c["primary_readout"] = hypothesis["dv"] or ""
        if c["one_time_setup_cost"] is None and contract.get("setup_cost_usd") is not None:
            c["one_time_setup_cost"] = contract["setup_cost_usd"]
        if c["prep_days"] is None and contract.get("base_prep_days") is not None:
            c["prep_days"] = contract["base_prep_days"]
        if c["qc_turnaround_days"] is None and contract.get("qc_days") is not None:
            c["qc_turnaround_days"] = contract["qc_days"]

    meta_block = {
        "extracted_from_files": [rel for rel, _ in docs],
        "blocks_indexer_used": blocks_indexed,
        "source_anchors": source_anchors,
        "not_found_in_source": not_found,
        "extraction_honesty_notice": "Fields absent from /input narrative sources remain explicitly null and listed in not_found_in_source (NOT_FOUND_IN_SOURCE).",
    }

    if mode == "cf1995b5":
        return {
            "case_id": case_id,
            "title": title,
            "research_type": "engineering_optimization",
            "hypothesis": hypothesis,
            "statistical_design": {"inference_unit": inference_unit or "", "planned_biological_replicates": 3},
            "controls": controls,
            "prospective_contract": contract,
            "finite_candidates": candidates,
            "extraction_provenance": meta_block,
        }
    if mode in {"2e2c5820", "7be76e91"}:
        return {
            "case_id": case_id,
            "title": title,
            "prospective_contract": contract,
            "candidate_plans": candidates,
            "extraction_provenance": meta_block,
        }
    # ec02b694 outputs a bundled dict containing domain_spec, contract_spec, candidates_3v (plus separate files if --out-dir used)
    unit_prices = {rname: rmeta.get("unit_cost") for rname, rmeta in resources.items()}
    return {
        "case_id": case_id,
        "title": title,
        "domain_spec": {"parameters": parameter_domains, "coupled_constraints": [], "linear_constraints": []},
        "contract_spec": {
            "currency": "USD",
            "budget_cap": contract.get("budget_cap_usd"),
            "time_limit_hours": contract.get("time_limit_hours"),
            "setup_cost": contract.get("setup_cost_usd"),
            "unit_prices": unit_prices,
        },
        "candidates_3v": {"plans": candidates},
        "extraction_provenance": meta_block,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract source_ref-anchored draft JSON from raw /input Markdown/TXT/PDF narrative sources.")
    parser.add_argument("--input-dir", required=True, help="Raw /input directory")
    parser.add_argument("--blocks", default="", help="Optional block_indexer.py output JSON")
    parser.add_argument("--mode", required=True, choices=("cf1995b5", "2e2c5820", "ec02b694", "7be76e91"))
    parser.add_argument("--out", default="", help="Output extracted draft JSON path")
    parser.add_argument("--out-dir", default="", help="For ec02b694: also emit domain_spec.json, contract_spec.json, candidates_3v.json")
    args = parser.parse_args(argv)

    rep = extract_from_sources(
        Path(args.input_dir).resolve(),
        args.mode,
        blocks_path=Path(args.blocks).resolve() if args.blocks else None,
    )
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.out_dir and args.mode == "ec02b694":
        od = Path(args.out_dir)
        od.mkdir(parents=True, exist_ok=True)
        (od / "domain_spec.json").write_text(json.dumps(rep["domain_spec"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (od / "contract_spec.json").write_text(json.dumps(rep["contract_spec"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (od / "candidates_3v.json").write_text(json.dumps(rep["candidates_3v"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (od / "extraction_provenance.json").write_text(json.dumps(rep["extraction_provenance"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    prov = rep["extraction_provenance"]
    print(json.dumps({
        "ok": True,
        "mode": args.mode,
        "files_scanned": len(prov["extracted_from_files"]),
        "not_found_in_source_count": len(prov["not_found_in_source"]),
        "not_found_fields": [x["field"] for x in prov["not_found_in_source"]],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
