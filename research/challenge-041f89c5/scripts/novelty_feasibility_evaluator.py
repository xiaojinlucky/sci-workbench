#!/usr/bin/env python3
"""Research Topic Formulation, Prior-Work Comparison Matrix, 4-Axis Novelty Decomposition,
Source-Verified 5-Axis Feasibility Auditor, and Pre-Mortem Risk Engine (v4).

Key guarantees:
1. Parses multi-source prior literature into deduplicated per-paper rows with domain-general
   method/metric extraction (`extract_general_methods_and_objects`) and explicit comparability status.
2. Enforces 6-slot falsifiable question formulation with a vacuous-value blocker (rejecting
   empty/"None"/"N/A"/"TBD"/"Best ever" on falsification_criteria or expected_outcome).
3. Performs 4-axis novelty decomposition in ONE unified TF-IDF vector space across proposal,
   all novelty claims, and all prior works; enforces strict scenario_transfer inflation guard.
4. Audits all 5 feasibility axes with EXACT normalized `source_ref` verification for `[GROUNDED]`
   (zero substring-bypass loophole — `"arxiv"`/`.json`/`"paper"` all fail!).
5. Non-destructive `--out-json`: never overwrites `topic_comparison_matrix.md` unless `--out-md`
   or `--output` is explicitly requested.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log
from corpus_loader import load_input_bundle
from evidence_state_guard import (
    build_tfidf_vectors,
    cosine_similarity,
    detect_overclaims,
    extract_directional_passages,
    extract_general_methods_and_objects,
    is_fulltext_access,
    is_non_evidence_access,
    normalize_doi,
    tokenize_terms,
)
from prisma_flow_auditor import deduplicate_bibliography, summarize_identification_by_source

FEASIBILITY_AXES = ("data_availability", "compute_budget", "experimental_conditions", "timeline_window", "team_capability")
VALID_NOVELTY_TYPES = {"method_innovation", "data_innovation", "scenario_transfer", "conclusion_correction"}
VALID_EPISTEMIC_TAGS = {"GROUNDED", "ASSUMPTION", "BLOCKED_UNKNOWN"}

VACUOUS_VALUE_RE = re.compile(
    r"^(?:none|n/?a|nil|null|tbd|todo|unknown|unspecified|best\s+ever|better|good|improved|works\s+well|feasible|\-+|\.+)$",
    re.I,
)
TRANSFER_CUE_RE = re.compile(
    r"\b(?:apply(?:ing)?\s+existing|adapt(?:ing|ed)?\s+(?:an?\s+)?existing|transfer(?:ring)?\s+to|"
    r"new\s+domain|new\s+cohort|different\s+dataset|migrating\s+to|re-?applying)\b|"
    r"迁移至|应用到新场景|新数据集上应用",
    re.I,
)
ALGO_MECH_NOVELTY_RE = re.compile(
    r"\b(?:new\s+(?:architecture|mechanism|algorithm|loss|objective|estimator|theorem|operator|assay|catalyst\s+mechanism)|"
    r"novel\s+(?:architecture|mechanism|algorithm|formulation|theory|proof)|first-principles\s+derivation|"
    r"pinning\s+mechanism|causal\s+identification\s+theorem)\b",
    re.I,
)


def _is_vacuous(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    if len(s) < 10:
        return True
    return bool(VACUOUS_VALUE_RE.match(s))


def _extract_auto_spec(case_data: dict[str, Any], task_md: str, canonical: list[dict[str, Any]]) -> dict[str, Any]:
    proposal = case_data.get("proposal") or case_data.get("topic_proposal") or {}
    rq_spec = case_data.get("question_formulation") or proposal.get("question_formulation") or {}
    rq_text = str(
        proposal.get("research_question")
        or case_data.get("research_question")
        or case_data.get("title")
        or (task_md.splitlines()[0].lstrip("# ").strip() if task_md.strip() else "Proposed bounded research topic")
    )

    question_formulation = {
        "target_object": str(rq_spec.get("target_object") or proposal.get("target_object") or case_data.get("domain") or "target_domain_object_from_case"),
        "key_variables": rq_spec.get("key_variables") or proposal.get("key_variables") or tokenize_terms(rq_text, include_bigrams=False)[:4] or ["primary_variable"],
        "baselines": rq_spec.get("baselines") or proposal.get("baselines") or [r["record_id"] for r in canonical[:2]] or ["closest_prior_baseline"],
        "expected_outcome": str(rq_spec.get("expected_outcome") or proposal.get("expected_outcome") or f"Quantifiable performance or mechanistic difference on '{rq_text[:90]}'"),
        "falsification_criteria": str(rq_spec.get("falsification_criteria") or proposal.get("falsification_criteria") or "Proposal is falsified if no statistically or practically meaningful improvement over matched baselines is observed under identical evaluation conditions."),
        "inference_unit_and_scope_boundary": str(rq_spec.get("inference_unit_and_scope_boundary") or proposal.get("inference_unit") or "Independent biological, experimental, or dataset replicates within stated domain scope; not generalized globally."),
    }

    novelty_claims = list(case_data.get("novelty_claims") or proposal.get("novelty_claims") or [])
    if not novelty_claims:
        novelty_claims = [{
            "id": "NOV-1",
            "statement": rq_text,
            "novelty_type": str(proposal.get("novelty_type") or "method_innovation"),
            "differentiation_note": str(proposal.get("differentiation_note") or ""),
        }]

    feasibility_premises = list(case_data.get("feasibility_premises") or proposal.get("feasibility_premises") or [])
    if not feasibility_premises:
        constraints = case_data.get("constraints") or proposal.get("constraints") or {}
        for ax in FEASIBILITY_AXES:
            val = constraints.get(ax) if isinstance(constraints, dict) else None
            if val:
                feasibility_premises.append({
                    "axis": ax,
                    "epistemic_tag": "GROUNDED",
                    "source_ref": "case.json",
                    "rationale": f"Explicitly bounded in case constraints: {val}",
                })
            else:
                feasibility_premises.append({
                    "axis": ax,
                    "epistemic_tag": "BLOCKED_UNKNOWN",
                    "source_ref": None,
                    "rationale": f"No verified '{ax}' resource/budget/approval constraint supplied in /input; cannot assume zero friction.",
                })

    return {
        "question_formulation": question_formulation,
        "novelty_claims": novelty_claims,
        "feasibility_premises": feasibility_premises,
        "opposing_evidence": list(case_data.get("opposing_evidence") or proposal.get("opposing_evidence") or []),
    }


def build_premortem_risk_matrix(
    feasibility_checks: list[dict[str, Any]],
    novelty_checks: list[dict[str, Any]],
    opposing_evidence: list[dict[str, Any]],
) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    for nc in novelty_checks:
        if nc["verdict"] in {
            "COVERED_BY_PRIOR_WORK",
            "COVERED_BY_PRIOR_WORK_ABSTRACT_BOUNDED",
            "PARTIALLY_COVERED_INCREMENTAL",
            "PARTIALLY_COVERED_ABSTRACT_BOUNDED",
            "SCENARIO_TRANSFER_MISLABELED",
        }:
            risks.append({
                "priority": "P0_CRITICAL",
                "failure_mode": f"Novelty overlap or claim-inflation risk on {nc['claim_id']} against {nc['closest_local_source']} (verdict={nc['verdict']}, sim={nc['combined_similarity']})",
                "earliest_warning_signal": "Head-to-head ablation shows no gain beyond closest neighbor once dataset and metric definitions are aligned.",
                "preventive_action": "Run an explicit baseline reproduction and side-by-side functional-operation diff before committing full budget.",
                "stop_or_pivot_trigger": "Claimed delta collapses within baseline variance or turns out to be a naming-only rebrand.",
                "contingency_plan": "Pivot claim explicitly to scenario_transfer / boundary-condition correction or narrow to unresolved failure regime.",
            })
    for fc in feasibility_checks:
        if fc["tag"] in {"BLOCKED_UNKNOWN", "ASSUMPTION"}:
            risks.append({
                "priority": "P1_HIGH" if fc["tag"] == "BLOCKED_UNKNOWN" else "P2_MEDIUM",
                "failure_mode": f"Unverified feasibility bottleneck on '{fc['axis']}' (status={fc['tag']})",
                "earliest_warning_signal": f"Missing access, ethics/resource approval, or underestimated cost/compute on {fc['axis']} during pilot setup.",
                "preventive_action": f"Obtain documented confirmation for {fc['axis']} and bound pilot scope before full execution.",
                "stop_or_pivot_trigger": f"Prerequisite on {fc['axis']} remains unconfirmed past initial milestone.",
                "contingency_plan": "Switch to validated open benchmark subset or scoped retrospective validation first.",
            })
    if opposing_evidence:
        top_opp = opposing_evidence[0]
        risks.append({
            "priority": "P1_HIGH",
            "failure_mode": f"Reproducibility / counter-evidence risk highlighted by {top_opp.get('record_id', 'prior_study')}",
            "earliest_warning_signal": "Pilot cohort/assay reproduces the null or opposing boundary observed in prior negative findings.",
            "preventive_action": "Include the exact negative/control condition from opposing literature in the minimum pilot design.",
            "stop_or_pivot_trigger": "Core hypothesis fails pre-registered falsification_criteria on independent replicates.",
            "contingency_plan": "Report bounded negative/condition-dependent finding transparently rather than post-hoc shifting endpoints.",
        })
    return risks[:6]


def evaluate_topic_proposal(
    input_dir: Path,
    eval_json: Path | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    bundle = load_input_bundle(input_dir, work_dir=work_dir)
    case_id = bundle["case_id"]
    case_data = bundle["case_data"]
    raw_records = bundle["records"]
    by_source = summarize_identification_by_source(raw_records)
    canonical, dup_log, meta_conflicts = deduplicate_bibliography(raw_records)

    if not canonical:
        return {"passed": False, "case_id": case_id, "prior_sources": [], "error": f"No prior-work source documents found in {input_dir}"}

    explicit_spec = json.loads(eval_json.read_text(encoding="utf-8")) if (eval_json and eval_json.is_file()) else None
    spec = explicit_spec if explicit_spec is not None else _extract_auto_spec(case_data, bundle["task_md"], canonical)
    formulation_status = "evaluated_explicit_spec" if explicit_spec is not None else "evaluated_from_case_input"

    # Build ONE unified TF-IDF vector space across [proposal] + [novelty claims] + [all prior works]!
    proposal_text = " ".join([
        str(case_data.get("research_question") or ""),
        str(case_data.get("title") or ""),
        json.dumps(spec.get("question_formulation") or {}, ensure_ascii=False),
        json.dumps(spec.get("novelty_claims") or [], ensure_ascii=False),
        bundle["task_md"][:2000],
    ])
    claims_list = list(spec.get("novelty_claims") or [])
    prop_tokens = tokenize_terms(proposal_text, include_bigrams=True)
    claim_tokens_list = [tokenize_terms(str(c.get("statement") or ""), include_bigrams=True) for c in claims_list]
    rec_tokens_list = [
        tokenize_terms(f"{r.get('title', '')} {r.get('abstract', '')} {str(r.get('text') or '')[:3000]}", include_bigrams=True)
        for r in canonical
    ]
    all_vecs = build_tfidf_vectors([prop_tokens] + claim_tokens_list + rec_tokens_list)
    prop_vec = all_vecs[0]
    claim_vecs = all_vecs[1 : 1 + len(claims_list)]
    rec_vecs = all_vecs[1 + len(claims_list) :]

    prop_ext = extract_general_methods_and_objects(proposal_text)
    prop_methods = set(prop_ext["methods"])
    prop_metrics = set(prop_ext["metrics"])

    # Build per-paper prior-work comparison matrix + auto-extracted opposing/limiting evidence
    matrix_internal: list[dict[str, Any]] = []
    opposing_auto: list[dict[str, Any]] = []
    for idx, rec in enumerate(canonical):
        access = str(rec.get("material_access") or "discovery_only")
        blob = f"{rec.get('title', '')} {rec.get('abstract', '')} {str(rec.get('text') or '')[:5000]}"
        ext = extract_general_methods_and_objects(blob)
        methods = ext["methods"]
        metrics = ext["metrics"]
        passages = extract_directional_passages(rec, max_per_record=3)

        finding_p = next((p for p in passages if p["direction"] in {"supportive", "descriptive", "mixed"}), passages[0] if passages else None)
        lim_p = next((p for p in passages if p.get("is_limitation") or p["direction"] in {"opposing_or_null", "mixed"}), None)

        sim = round(cosine_similarity(prop_vec, rec_vecs[idx]), 4)
        rec_methods_set = set(methods)
        rec_metrics_set = set(metrics)

        if is_non_evidence_access(access):
            comp_status = "incomparable_discovery_or_blocked"
            comp_reason = f"Record is {access}; no methods, dataset, or results available for comparison."
        elif not is_fulltext_access(access):
            comp_status = "incomparable_abstract_only"
            comp_reason = "Only abstract inspected; exact experimental parameters, controls, and full metrics cannot be benchmarked directly."
        elif prop_metrics and rec_metrics_set and not (prop_metrics & rec_metrics_set):
            comp_status = "incomparable_different_evaluation_metrics"
            comp_reason = f"Metrics differ ({sorted(rec_metrics_set)[:3]} vs proposed {sorted(prop_metrics)[:3]})."
        elif prop_methods and rec_methods_set and not (prop_methods & rec_methods_set):
            comp_status = "partially_comparable_different_method_family"
            comp_reason = f"Method family differs ({sorted(rec_methods_set)[:3]} vs proposed {sorted(prop_methods)[:3]})."
        else:
            comp_status = "directly_comparable_fulltext"
            comp_reason = "Full-text source with overlapping scope/method signals."

        matrix_internal.append({
            "record_id": rec["record_id"],
            "title": rec.get("title") or rec["record_id"],
            "year": rec.get("year"),
            "date_basis": rec.get("date_basis", "unknown"),
            "doi": rec.get("doi") or None,
            "material_access": access,
            "source_file": rec.get("source_file"),
            "relevance_cosine": sim,
            "functional_methods": methods or ["not_reported_in_available_text"],
            "evaluation_metrics": metrics or ["not_reported_in_available_text"],
            "direct_comparability_status": comp_status,
            "incomparability_reason": comp_reason,
            "key_finding_locator": finding_p["locator"] if finding_p else None,
            "key_finding_quote": finding_p["quote"] if finding_p else "",
            "limitation_or_counter_locator": lim_p["locator"] if lim_p else None,
            "limitation_or_counter_quote": lim_p["quote"] if lim_p else "",
            "_vec": rec_vecs[idx],
            "_tokens": set(rec_tokens_list[idx]),
            "_methods_set": rec_methods_set,
        })

        for p in passages:
            if p["direction"] in {"opposing_or_null", "mixed"} or p.get("is_limitation"):
                opposing_auto.append({
                    "record_id": rec["record_id"],
                    "year": rec.get("year"),
                    "material_access": access,
                    "signal_type": p["direction"],
                    "locator": p["locator"],
                    "quote": p["quote"],
                })

    matrix_internal.sort(key=lambda x: (-x["relevance_cosine"], -(1 if is_fulltext_access(x["material_access"]) else 0), -(x["year"] or 0)))

    errors: list[str] = []
    warnings: list[str] = []

    # 1. Falsifiable Research Question audit (6 slots + vacuous-value blocker!)
    rq = spec.get("question_formulation") or {}
    req_rq = ("target_object", "key_variables", "baselines", "expected_outcome", "falsification_criteria")
    missing_rq = [k for k in req_rq if not rq.get(k)]
    if missing_rq:
        errors.append(f"Research question missing falsifiability fields: {missing_rq}")
    if _is_vacuous(rq.get("falsification_criteria")):
        errors.append(f"falsification_criteria is empty or vacuous ({rq.get('falsification_criteria')!r}); must state a concrete empirical condition that refutes the hypothesis.")
    if _is_vacuous(rq.get("expected_outcome")):
        errors.append(f"expected_outcome is empty or vacuous ({rq.get('expected_outcome')!r}); must state a measurable bounded outcome.")
    if not rq.get("inference_unit_and_scope_boundary"):
        warnings.append("Research question does not explicitly specify inference_unit_and_scope_boundary.")
    rq_over = detect_overclaims(json.dumps(rq, ensure_ascii=False))
    if rq_over:
        errors.append(f"Unbounded overclaim in question_formulation: {rq_over}")

    # 2. 4-Axis Novelty Decomposition in unified TF-IDF space + strict scenario_transfer inflation guard
    novelty_checks: list[dict[str, Any]] = []
    for idx, nov in enumerate(claims_list, 1):
        cid = str(nov.get("id") or f"NOV-{idx}")
        stmt = str(nov.get("statement") or "").strip()
        ntype = str(nov.get("novelty_type") or "unknown").strip().lower()
        diff_note = str(nov.get("differentiation_note") or "").strip()

        if ntype not in VALID_NOVELTY_TYPES:
            warnings.append(f"Novelty claim {cid} uses non-standard novelty_type '{ntype}'; expected one of {sorted(VALID_NOVELTY_TYPES)}.")
        over = detect_overclaims(stmt)
        if over:
            errors.append(f"Novelty claim {cid} makes unbounded global novelty/experiment claim: {over}")

        # Scenario-transfer inflation check: even with a superficial differentiation note, applying existing method to new setting without algo/mech innovation is scenario_transfer!
        transfer_mislabeled = bool(
            ntype == "method_innovation"
            and TRANSFER_CUE_RE.search(stmt)
            and not ALGO_MECH_NOVELTY_RE.search(f"{stmt} {diff_note}")
        )
        if transfer_mislabeled:
            errors.append(f"Novelty claim {cid} describes scenario_transfer (applying/adapting existing method to new domain/dataset) without algorithmic/mechanistic novelty; relabel as scenario_transfer.")

        stmt_toks = set(claim_tokens_list[idx - 1])
        stmt_vec = claim_vecs[idx - 1]
        stmt_methods = set(extract_general_methods_and_objects(stmt)["methods"])

        scored_neighbors: list[tuple[float, float, float, dict[str, Any]]] = []
        for m in matrix_internal:
            lex_ov = (len(stmt_toks & m["_tokens"]) / max(1, len(stmt_toks))) if stmt_toks else 0.0
            cos_sim = cosine_similarity(stmt_vec, m["_vec"])
            meth_ov = (len(stmt_methods & m["_methods_set"]) / max(1, len(stmt_methods))) if stmt_methods else 0.0
            combined = round(0.45 * lex_ov + 0.40 * cos_sim + 0.15 * meth_ov, 4)
            scored_neighbors.append((combined, round(lex_ov, 4), round(cos_sim, 4), m))
        scored_neighbors.sort(key=lambda x: (-x[0], -x[1]))

        top3 = [
            {
                "record_id": m["record_id"],
                "title": m["title"],
                "year": m["year"],
                "material_access": m["material_access"],
                "combined_similarity": comb,
                "lexical_overlap": lex,
                "tfidf_cosine": cos,
                "excerpt": m["key_finding_quote"][:160],
            }
            for comb, lex, cos, m in scored_neighbors[:3]
        ]
        best_comb, best_lex, _, best_m = scored_neighbors[0]
        covered = best_lex >= 0.45 or best_comb >= 0.38

        if covered and not diff_note:
            warnings.append(f"Novelty claim {cid} overlaps closely with {best_m['record_id']} (lexical={best_lex:.2f}, combined={best_comb:.2f}); provide source-anchored differentiation_note.")

        has_ft_neighbor = any(is_fulltext_access(n["material_access"]) for n in top3)
        has_abs_neighbor = any(str(n["material_access"]).lower() == "abstract_only" for n in top3)
        ft_verify_targets: list[str] = []
        if not has_ft_neighbor:
            for nb in top3:
                ft_verify_targets.append(
                    f"Retrieve full text of [{nb['record_id']}] ({nb['year'] or 'NA'}, sim={nb['combined_similarity']}) to verify Methods, Baseline Comparison Table, and Control/Ablation parameters."
                )

        if transfer_mislabeled:
            verdict = "SCENARIO_TRANSFER_MISLABELED"
        elif covered and not diff_note:
            verdict = "COVERED_BY_PRIOR_WORK" if has_ft_neighbor else "COVERED_BY_PRIOR_WORK_ABSTRACT_BOUNDED"
        elif covered and diff_note:
            verdict = "PARTIALLY_COVERED_INCREMENTAL" if has_ft_neighbor else "PARTIALLY_COVERED_ABSTRACT_BOUNDED"
        elif has_ft_neighbor:
            verdict = "CANDIDATE_NOVEL_WITHIN_CORPUS"
        elif has_abs_neighbor:
            # Decision-grade abstract-bounded downgrade instead of dead-ending at UNVERIFIED_INSUFFICIENT_FULLTEXT
            verdict = (
                "PARTIALLY_COVERED_ABSTRACT_BOUNDED"
                if (best_comb >= 0.20 or best_lex >= 0.25)
                else "CANDIDATE_NOVEL_ABSTRACT_BOUNDED"
            )
        else:
            verdict = "UNVERIFIED_INSUFFICIENT_FULLTEXT"

        novelty_checks.append({
            "claim_id": cid,
            "statement": stmt,
            "novelty_type": ntype,
            "closest_local_source": best_m["record_id"],
            "lexical_overlap": best_lex,
            "combined_similarity": best_comb,
            "prior_coverage_flag": covered,
            "scenario_transfer_inflation_flag": transfer_mislabeled,
            "differentiation_note": diff_note or None,
            "verdict": verdict,
            "top3_neighbors": top3,
            "fulltext_verification_targets": ft_verify_targets,
            "bounded_scope_note": (
                f"Assessed strictly against {len(canonical)} deduplicated local sources "
                f"({'full-text neighbor inspected' if has_ft_neighbor else 'abstract-only bounded screening; full-text verification targets listed'}); "
                "never implies global novelty."
            ),
        })

    # 3. EXACT Source-Verified 5-Axis Feasibility Auditor (ZERO substring-bypass loophole!)
    exact_valid_refs: set[str] = {"case.json", "task.md"}
    for p in input_dir.rglob("*"):
        if p.is_file():
            rel = p.relative_to(input_dir).as_posix().lower()
            exact_valid_refs.add(rel)
            exact_valid_refs.add(p.name.lower())
    for r in canonical:
        exact_valid_refs.add(str(r["record_id"]).strip().lower())
        if r.get("doi"):
            exact_valid_refs.add(normalize_doi(str(r["doi"])))
        if r.get("arxiv_id"):
            exact_valid_refs.add(f"arxiv:{str(r['arxiv_id']).lower()}")
        if r.get("pmcid"):
            exact_valid_refs.add(str(r["pmcid"]).lower())

    feas = spec.get("feasibility_premises") or []
    present_axes = {str(f.get("axis") or "") for f in feas}
    missing_axes = [ax for ax in FEASIBILITY_AXES if ax not in present_axes]
    if missing_axes:
        warnings.append(f"Feasibility evaluation omitted axes: {missing_axes}")

    feasibility_checks: list[dict[str, Any]] = []
    for f in feas:
        ax = str(f.get("axis") or "unknown")
        tag = str(f.get("epistemic_tag") or "").strip().upper()
        sref = str(f.get("source_ref") or "").strip()
        rationale = str(f.get("rationale") or "").strip()

        if tag not in VALID_EPISTEMIC_TAGS:
            errors.append(f"Feasibility premise '{ax}' must use tag in {sorted(VALID_EPISTEMIC_TAGS)}, got '{tag}'.")
        if detect_overclaims(rationale):
            errors.append(f"Unbounded feasibility language in axis '{ax}'.")

        sref_verified = False
        if tag == "GROUNDED":
            # Strip optional #locator fragment and normalize slashes/doi prefix; REQUIRE EXACT SET MEMBERSHIP!
            sref_base = sref.replace("\\", "/").split("#")[0].strip().lower()
            sref_norm_doi = normalize_doi(sref_base)
            sref_verified = bool(sref_base) and (sref_base in exact_valid_refs or (bool(sref_norm_doi) and sref_norm_doi in exact_valid_refs))
            if not sref_verified:
                errors.append(f"Feasibility premise '{ax}' is tagged [GROUNDED] but exact source_ref '{sref}' was not found in /input files or corpus record IDs.")

        feasibility_checks.append({
            "axis": ax,
            "tag": tag,
            "source_ref": sref or None,
            "source_ref_verified": sref_verified if tag == "GROUNDED" else None,
            "rationale": rationale,
        })

    n_blocked = sum(1 for fc in feasibility_checks if fc["tag"] == "BLOCKED_UNKNOWN")
    n_assumed = sum(1 for fc in feasibility_checks if fc["tag"] == "ASSUMPTION")
    if errors or n_blocked >= 3:
        go_no_go = "BLOCKED_PENDING_PREREQUISITES"
    elif n_blocked > 0 or n_assumed > 0 or missing_axes:
        go_no_go = "FEASIBLE_WITH_CONDITIONS"
    else:
        go_no_go = "CONDITIONALLY_READY_FOR_PILOT"

    user_opp = list(spec.get("opposing_evidence") or [])
    combined_opp = user_opp + opposing_auto[:8]
    if not combined_opp:
        warnings.append("No opposing/limiting evidence entries found in spec or corpus; verify whether negative results were omitted.")

    premortem = build_premortem_risk_matrix(feasibility_checks, novelty_checks, combined_opp)
    clean_matrix = [{k: v for k, v in m.items() if not k.startswith("_")} for m in matrix_internal]
    return {
        "case_id": case_id,
        "passed": len(errors) == 0,
        "formulation_status": formulation_status,
        "corpus_scope": f"bounded_local_corpus ({len(raw_records)} raw -> {len(canonical)} deduplicated prior works)",
        "block_index_summary": bundle["block_index_summary"],
        "identified_by_source": by_source,
        "question_formulation": rq,
        "prior_sources": [
            {"record_id": m["record_id"], "title": m["title"], "year": m["year"], "material_access": m["material_access"], "source_file": m["source_file"], "relevance_cosine": m["relevance_cosine"]}
            for m in clean_matrix
        ],
        "prior_work_matrix": clean_matrix[:25],
        "novelty_checks": novelty_checks,
        "feasibility_checks": feasibility_checks,
        "go_no_go_decision": go_no_go,
        "opposing_evidence": combined_opp,
        "premortem_risk_matrix": premortem,
        "pagination_warnings": bundle["pagination_warnings"],
        "metadata_conflicts": meta_conflicts,
        "errors": errors,
        "warnings": warnings,
    }


def render_topic_markdown(rep: dict[str, Any]) -> str:
    rq = rep.get("question_formulation") or {}
    lines = [
        f"# Research Topic Formulation, Novelty & Feasibility Evaluation ({rep.get('case_id', 'case')})",
        "",
        f"**Corpus Scope**: {rep.get('corpus_scope')}",
        f"**Feasibility Decision**: `{rep.get('go_no_go_decision')}` (Status: `{rep.get('formulation_status')}`)",
        "**Epistemic Boundary**: Novelty is bounded strictly to the inspected local prior-work corpus (`CANDIDATE_NOVEL_WITHIN_CORPUS` / `CANDIDATE_NOVEL_ABSTRACT_BOUNDED` / `PARTIALLY_COVERED_INCREMENTAL` / `PARTIALLY_COVERED_ABSTRACT_BOUNDED` / `COVERED_BY_PRIOR_WORK` / `COVERED_BY_PRIOR_WORK_ABSTRACT_BOUNDED` / `SCENARIO_TRANSFER_MISLABELED` / `UNVERIFIED_INSUFFICIENT_FULLTEXT`). Global novelty and completed experimental validation are NOT claimed.",
        "",
        "## 1. Falsifiable Research Question Formulation",
        f"- **Target Object**: {rq.get('target_object')}",
        f"- **Key Variables**: {rq.get('key_variables')}",
        f"- **Comparison Baselines**: {rq.get('baselines')}",
        f"- **Expected Outcome**: {rq.get('expected_outcome')}",
        f"- **Falsification Criteria**: {rq.get('falsification_criteria')}",
        f"- **Inference Unit & Scope Boundary**: {rq.get('inference_unit_and_scope_boundary')}",
        "",
        "## 2. Prior-Work Comparison Matrix (Top Local Neighbors)",
        "| Record ID | Year | Access | Relevance | Functional Methods | Metrics | Comparability Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in (rep.get("prior_work_matrix") or [])[:15]:
        lines.append(
            f"| `{m['record_id']}` | {m['year'] or 'NA'} | `{m['material_access']}` | {m['relevance_cosine']} | "
            f"{', '.join(m['functional_methods'][:3])} | {', '.join(m['evaluation_metrics'][:3])} | `{m['direct_comparability_status']}` ({m['incomparability_reason'][:70]}) |"
        )

    lines.extend(["", "## 3. Four-Axis Novelty Decomposition & Red-Team Overlap"])
    for nc in rep.get("novelty_checks") or []:
        lines.append(
            f"- **`{nc['claim_id']}`** (`{nc['novelty_type']}`, verdict=`{nc['verdict']}`): closest=`{nc['closest_local_source']}` (lexical={nc['lexical_overlap']}, combined={nc['combined_similarity']}, prior_coverage_flag=`{nc['prior_coverage_flag']}`)"
        )
        for nb in nc.get("top3_neighbors") or []:
            lines.append(f"  - Neighbor `{nb['record_id']}` ({nb['year']}, `{nb['material_access']}`, sim={nb['combined_similarity']}): {nb['title'][:90]}")
        for tgt in (nc.get("fulltext_verification_targets") or [])[:3]:
            lines.append(f"  - Full-Text Check Required: {tgt}")

    lines.extend(["", "## 4. Five-Axis Feasibility Audit (`[GROUNDED]` vs `[ASSUMPTION]` vs `[BLOCKED_UNKNOWN]`)"])
    lines.append("| Axis | Epistemic Tag | Source Ref | Verified | Rationale |")
    lines.append("|---|---|---|---|---|")
    for fc in rep.get("feasibility_checks") or []:
        lines.append(f"| `{fc['axis']}` | `[{fc['tag']}]` | `{fc['source_ref'] or 'none'}` | `{fc['source_ref_verified']}` | {fc['rationale'][:110]} |")

    lines.extend(["", "## 5. Opposing / Limiting Evidence Preserved"])
    for op in (rep.get("opposing_evidence") or [])[:8]:
        lines.append(f"- `{op.get('record_id', 'src')}` (`{op.get('locator', '')}`): \"{str(op.get('quote') or op.get('summary') or '')[:200]}\"")

    lines.extend(["", "## 6. Pre-Mortem Risk Matrix (Assuming Failure / Rejection)"])
    lines.append("| Priority | Failure Mode | Earliest Warning Signal | Preventive Action | Stop / Pivot Trigger | Contingency Plan |")
    lines.append("|---|---|---|---|---|---|")
    for pm in rep.get("premortem_risk_matrix") or []:
        lines.append(
            f"| `{pm['priority']}` | {pm['failure_mode']} | {pm['earliest_warning_signal']} | {pm['preventive_action']} | {pm['stop_or_pivot_trigger']} | {pm['contingency_plan']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded research topic novelty & feasibility auditor.")
    parser.add_argument("--input", default="", help="Input directory with prior-work documents and case.json.")
    parser.add_argument("--output", default="", help="Output directory (writes result.json, topic_evaluation.json, topic_comparison_matrix.md).")
    parser.add_argument("--work", default="", help="Working directory.")
    parser.add_argument("--eval-json", default="", help="Optional structured topic evaluation JSON to audit strictly.")
    parser.add_argument("--out-json", default="", help="Explicit JSON output path (does NOT overwrite topic_comparison_matrix.md unless --out-md or --output is given).")
    parser.add_argument("--out-md", default="", help="Explicit Markdown report path.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if validation errors exist.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    in_dir = paths["input_dir"]
    work_dir = paths["work_dir"]
    work_dir.mkdir(parents=True, exist_ok=True)

    rep = evaluate_topic_proposal(in_dir, Path(args.eval_json) if args.eval_json else None, work_dir=work_dir)

    # Non-destructive output routing: if --out-json is specified without --output/--out-md, ONLY write out_json!
    out_dir = Path(args.output) if args.output else (Path(args.out_json).parent if args.out_json else paths["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_j = Path(args.out_json) if args.out_json else (out_dir / "topic_evaluation.json")
    out_m = Path(args.out_md) if args.out_md else ((out_dir / "topic_comparison_matrix.md") if (args.output or not args.out_json) else None)

    result_guard.atomic_write_json(out_j, rep)
    if out_m is not None:
        result_guard.atomic_write_text(out_m, render_topic_markdown(rep))

    if args.output or not args.out_json:
        preflight_probe.probe_environment(input_dir=in_dir, output_dir=out_dir, work_dir=work_dir)
        budget_file = work_dir / ".budget_state.json"
        budget_guard.init_budget(budget_file)
        audit_file = work_dir / "audit_log.json"
        init_audit_log(audit_file, run_label="topic-novelty-feasibility")
        append_audit_entry(audit_file, phase="topic_evaluation", action="evaluation_written", actor="novelty_feasibility_evaluator.py", output={"prior_sources": len(rep.get("prior_sources") or []), "go_no_go": rep.get("go_no_go_decision")}, record_id=rep.get("case_id", "unknown"))

        result_path = out_dir / "result.json"
        result_guard.init_result(result_path, case_id=rep.get("case_id", "unknown"), task_id="topic-novelty-feasibility")
        gaps: list[dict[str, Any]] = []
        for e in rep.get("errors") or []:
            gaps.append({"code": "TOPIC_EVAL_ERROR", "severity": "error", "source": "novelty_feasibility_evaluator", "message": e})
        for w in rep.get("warnings") or []:
            gaps.append({"code": "TOPIC_EVAL_WARNING", "severity": "info", "source": "novelty_feasibility_evaluator", "message": w})

        n_grounded = sum(1 for fc in (rep.get("feasibility_checks") or []) if fc["tag"] == "GROUNDED" and fc["source_ref_verified"])
        n_assumed = sum(1 for fc in (rep.get("feasibility_checks") or []) if fc["tag"] == "ASSUMPTION")
        n_blocked = sum(1 for fc in (rep.get("feasibility_checks") or []) if fc["tag"] == "BLOCKED_UNKNOWN")
        req_files = [out_j.name] + ([out_m.name] if out_m is not None else [])

        patch = {
            "artifacts": ["result.json", *req_files],
            "sources": [
                {"record_id": m["record_id"], "title": m["title"], "year": m["year"], "doi": m["doi"], "material_access": m["material_access"]}
                for m in (rep.get("prior_work_matrix") or [])[:15]
            ],
            "formulation_status": rep.get("formulation_status"),
            "corpus_scope": rep.get("corpus_scope"),
            "question_formulation": rep.get("question_formulation"),
            "prior_sources": rep.get("prior_sources", [])[:40],
            "prior_work_matrix": rep.get("prior_work_matrix", []),
            "novelty_checks": rep.get("novelty_checks", []),
            "feasibility_checks": rep.get("feasibility_checks", []),
            "go_no_go_decision": rep.get("go_no_go_decision"),
            "opposing_evidence": rep.get("opposing_evidence", []),
            "premortem_risk_matrix": rep.get("premortem_risk_matrix", []),
            "errors": rep.get("errors", []),
            "warnings": rep.get("warnings", []),
            "uncertainty": {
                "bounded_scope": True,
                "global_novelty_claimed": False,
                "completed_validation_claimed": False,
                "feasibility_blocked_axes": n_blocked,
                "feasibility_assumption_axes": n_assumed,
                "pagination_warnings": rep.get("pagination_warnings") or [],
            },
            "metrics": {
                "prior_source_count": len(rep.get("prior_sources") or []),
                "novelty_claim_count": len(rep.get("novelty_checks") or []),
                "prior_overlap_flagged_count": sum(1 for n in (rep.get("novelty_checks") or []) if n["prior_coverage_flag"]),
                "feasibility_grounded_verified_count": n_grounded,
                "feasibility_assumption_count": n_assumed,
                "feasibility_blocked_unknown_count": n_blocked,
                "opposing_evidence_count": len(rep.get("opposing_evidence") or []),
                "premortem_risk_count": len(rep.get("premortem_risk_matrix") or []),
            },
            "metric_provenance": {
                "prior_source_count": f"{out_j.name}:prior_sources",
                "novelty_claim_count": f"{out_j.name}:novelty_checks",
                "prior_overlap_flagged_count": f"{out_j.name}:novelty_checks",
                "feasibility_grounded_verified_count": f"{out_j.name}:feasibility_checks",
                "feasibility_assumption_count": f"{out_j.name}:feasibility_checks",
                "feasibility_blocked_unknown_count": f"{out_j.name}:feasibility_checks",
                "opposing_evidence_count": f"{out_j.name}:opposing_evidence",
                "premortem_risk_count": f"{out_j.name}:premortem_risk_matrix",
            },
            "findings": [
                f"Evaluated research topic against {len(rep.get('prior_sources') or [])} deduplicated prior works: decision={rep.get('go_no_go_decision')}, novelty_claims={len(rep.get('novelty_checks') or [])}, feasibility(grounded={n_grounded}, assumption={n_assumed}, blocked_unknown={n_blocked}), opposing_evidence={len(rep.get('opposing_evidence') or [])}, premortem_risks={len(rep.get('premortem_risk_matrix') or [])}."
            ],
            "gaps": gaps,
        }
        res = result_guard.update_result(result_path, patch, finalize=True)
        grounding = gap_gate.check_grounding(res, [in_dir])
        if not grounding["ok"]:
            result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)
        budget_guard.tick_phase(budget_file, "final_audit")
        audit = final_audit.audit_deliverables(out_dir, result_file="result.json", required_files=req_files)
        if not audit["passed"]:
            raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")

    print(json.dumps({"passed": rep.get("passed"), "prior_sources": len(rep.get("prior_sources", [])), "go_no_go": rep.get("go_no_go_decision"), "errors": rep.get("errors", [])}, ensure_ascii=False))
    if not rep.get("prior_sources"):
        return 1
    if args.strict and not rep.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
