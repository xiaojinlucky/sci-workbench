#!/usr/bin/env python3
"""Auditable 5-Stage Systematic Literature Review Pipeline (v5).

Key guarantees:
1. Frozen PICO/PECO Protocol & Search Strategy Lock (`protocol_sha256`) with non-placeholder PICO extraction.
2. Multi-Source Deduplication with Non-Destructive Audit Trail & Preprint->VoR Lineage.
3. Word-Boundary Clause-Priority Two-Stage Screening (Stage 1: Title/Abstract ONLY; Stage 2: Full-Text ONLY)
   with PRISMA 2020 Item 16a `not_retrieved` separation & dual-reviewer disagreement queue.
4. Negation-Guarded 5-Domain Study Quality & Risk-of-Bias Appraisal (`SLR-Appraisal-v2-MultiDomain`):
   - Negative/risk contexts (`no control group`, `severe confounding`, `high risk of bias`) score `HIGH_RISK` (NEVER `LOW_RISK`!).
   - Secondary reviews/perspectives are explicitly downgraded (`SECONDARY_REVIEW_NOT_PRIMARY_TRIAL`) rather than receiving `HIGH`.
   - Every domain binds to the exact passage locator (`#page=N` / `#sec=...`) where the cue occurred.
5. True opposing-evidence passage binding (only opposing/null/mixed passages enter `opposing_evidence`, never positive `passages[0]`)
   plus read-only `--verify-report --strict` checking PRISMA number accuracy, excluded-study leakage, and overclaims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
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
    PROSPECTIVE_CUE_RE,
    RETRACTION_CUE_RE,
    classify_five_state_support,
    detect_overclaims,
    extract_directional_passages,
    is_fulltext_access,
    normalize_doi,
    tokenize_terms,
)
from prisma_flow_auditor import (
    deduplicate_bibliography,
    summarize_identification_by_source,
    verify_prisma_conservation,
)

APPRAISAL_INSTRUMENT = "SLR-Appraisal-v2-MultiDomain"
APPRAISAL_VERSION = "2.1.0"

DESIGN_CUE_RE = re.compile(
    r"\b(?:randomi[sz]ed\s+controlled\s+trial|double-blind|cohort\s+study|case-control|cross-sectional|"
    r"in\s+vivo|in\s+vitro|clinical\s+trial|benchmark|ablation|experimental\s+study|longitudinal\s+study)\b",
    re.I,
)
SECONDARY_REVIEW_RE = re.compile(
    r"\b(?:systematic\s+review|narrative\s+review|scoping\s+review|meta-analysis|review-article|"
    r"literature\s+review|perspective|commentary|editorial|minireview|overview\s+of)\b",
    re.I,
)
SAMPLE_CUE_RE = re.compile(
    r"\b(?:n\s*=\s*\d+|\d+\s+(?:patients?|participants?|subjects?|mice|rats?|samples?|datasets?|cohorts?|hospitals?|firms?)|"
    r"sample\s+size\s+of\s+\d+|biological\s+replicates?)\b",
    re.I,
)
SAMPLE_NEG_RE = re.compile(r"\b(?:no\s+sample\s+size|without\s+replicates?|single\s+case\s+only|n\s*=\s*1\b|unreplicated)\b", re.I)
COMPARATOR_CUE_RE = re.compile(
    r"\b(?:control\s+group|placebo\s+(?:group|control)|baseline\s+(?:model|method|group)|sham\s+control|"
    r"wild-type\s+control|vehicle\s+control|compared\s+(?:with|to)\s+(?:control|baseline|placebo)|benchmark(?:ed)?\s+against)\b",
    re.I,
)
COMPARATOR_NEG_RE = re.compile(
    r"\b(?:no\s+(?:control|comparison|placebo|baseline)\s+(?:group|arm|condition)|"
    r"without\s+(?:a\s+)?(?:control|placebo|comparator|baseline)|lack(?:ed|ing|s)?\s+(?:a\s+)?(?:control|comparator|baseline)|"
    r"uncontrolled\s+(?:study|trial|design|series))\b",
    re.I,
)
BIAS_SAFEGUARD_RE = re.compile(
    r"\b(?:randomi[sz](?:ed|ation)|double-blind|blinded\s+assessment|intention-to-treat|pre-?registered|"
    r"adjusted\s+for\s+confound|multivariable\s+adjustment|sensitivity\s+analysis|propensity\s+score|"
    r"conflict\s+of\s+interest|competing\s+interest|data\s+availability|code\s+availability)\b",
    re.I,
)
BIAS_RISK_RE = re.compile(
    r"\b(?:severe\s+(?:confounding|bias)|high\s+risk\s+of\s+bias|unadjusted\s+confound|major\s+confounding|"
    r"substantial\s+selection\s+bias|no\s+blinding|unblinded|high\s+attrition|selective\s+reporting)\b",
    re.I,
)
REPORT_CITE_RE = re.compile(r"\[([A-Za-z0-9_.:/-]+)\]", re.I)


def _extract_pico_from_rq(rq: str, proto: dict[str, Any], case_data: dict[str, Any], req_terms: list[str]) -> dict[str, Any]:
    rq_terms = tokenize_terms(rq, include_bigrams=False)
    pop = str(proto.get("population") or case_data.get("domain") or (" ".join(rq_terms[:3]) if rq_terms else "specified_study_population"))
    interv = str(proto.get("intervention") or proto.get("exposure") or (", ".join(req_terms[:3]) if req_terms else " ".join(rq_terms[1:4])))
    comp = str(proto.get("comparator") or "control, placebo, or standard baseline arm reported in eligible studies")
    outc = str(proto.get("outcome") or (" ".join(rq_terms[-3:]) if len(rq_terms) >= 3 else "primary empirical outcomes stated in research question"))
    return {
        "population_or_object": pop,
        "intervention_or_exposure": interv,
        "comparator": comp,
        "outcome": outc,
        "time_window": f"{proto.get('year_min') or 'unbounded'}..{proto.get('year_max') or 'unbounded'}",
        "languages": proto.get("languages") or proto.get("allowed_languages") or ["unrestricted"],
    }


def build_frozen_protocol(case_data: dict[str, Any], queries: list[Any], raw_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build and SHA-256 freeze PICO/PECO search strategy and clause-prioritized eligibility rules."""
    proto = case_data.get("protocol") or case_data.get("criteria") or {}
    rq = str(case_data.get("research_question") or (queries[0] if queries else case_data.get("title") or "systematic review question")).strip()
    databases = sorted({str(d).lower() for d in (proto.get("databases") or [str(r.get("source") or "local") for r in raw_records])}) or ["openalex", "crossref", "europepmc", "arxiv"]
    req_terms = [str(t).strip().lower() for t in (proto.get("required_terms") or proto.get("include_keywords") or []) if str(t).strip()]
    excl_terms = [str(t).strip().lower() for t in (proto.get("exclude_terms") or proto.get("exclude_keywords") or []) if str(t).strip()]
    ft_excl_terms = [str(t).strip().lower() for t in (proto.get("fulltext_exclude_terms") or []) if str(t).strip()]
    excl_types = [str(t).strip().lower() for t in (proto.get("exclude_study_types") or []) if str(t).strip()]
    lang_allow = [str(l).strip().lower() for l in (proto.get("languages") or proto.get("allowed_languages") or []) if str(l).strip()]
    y_min = proto.get("year_min")
    y_max = proto.get("year_max")
    ft_req = proto.get("fulltext_required") is True

    rq_terms = tokenize_terms(rq, include_bigrams=False)[:6]
    concept_block = " OR ".join(f'"{t}"' for t in (req_terms or rq_terms[:3] or ["topic"]))
    excl_block = (" NOT (" + " OR ".join(f'"{t}"' for t in excl_terms[:4]) + ")") if excl_terms else ""
    year_clause = f" AND PY:[{y_min or '*'} TO {y_max or '*'}]" if (y_min or y_max) else ""
    boolean_query = f"TIAB:({concept_block}){excl_block}{year_clause}"

    clause_priority = [
        {"priority": 1, "clause_id": "EXC_OUT_OF_YEAR_WINDOW", "stage": "stage1_title_abstract", "rule": f"Year outside [{y_min}, {y_max}]"},
        {"priority": 2, "clause_id": "EXC_LANGUAGE_RESTRICTION", "stage": "stage1_title_abstract", "rule": f"Language outside {lang_allow or ['any']}"},
        {"priority": 3, "clause_id": "EXC_STUDY_TYPE", "stage": "stage1_title_abstract", "rule": f"Study type in {excl_types or ['none']}"},
        {"priority": 4, "clause_id": "EXC_EXCLUSION_CLAUSE", "stage": "stage1_title_abstract", "rule": f"Title/Abstract word-boundary hit on exclude_terms={excl_terms}"},
        {"priority": 5, "clause_id": "EXC_MISSING_REQUIRED_CONCEPT", "stage": "stage1_title_abstract", "rule": f"Title/Abstract misses required_terms={req_terms}"},
        {"priority": 6, "clause_id": "NOT_RETRIEVED_FULLTEXT_REQUIRED", "stage": "stage2_fulltext", "rule": f"fulltext_required={ft_req} and no retrieved full text"},
        {"priority": 7, "clause_id": "EXC_FULLTEXT_EXCLUSION_TERM", "stage": "stage2_fulltext", "rule": f"Full text word-boundary hit on exclude_terms + fulltext_exclude_terms={ft_excl_terms}"},
    ]

    protocol_core = {
        "protocol_version": str(proto.get("version") or "v2_frozen"),
        "research_question": rq,
        "pico_peco_framework": _extract_pico_from_rq(rq, proto, case_data, req_terms),
        "search_strategy": {
            "databases": databases,
            "fields_searched": ["title", "abstract", "keywords"],
            "boolean_query": str(proto.get("boolean_query") or boolean_query),
            "query_blocks": [
                {"block": "core_concept", "intent": "Capture target domain and intervention/exposure terms", "terms": req_terms or rq_terms[:4]},
                {"block": "exclusion_filter", "intent": "Remove declared out-of-scope populations or document types", "terms": excl_terms},
            ],
            "search_date_snapshot": str(case_data.get("search_date") or "bounded_local_snapshot"),
        },
        "databases": databases,
        "required_terms": req_terms,
        "exclude_terms": excl_terms,
        "fulltext_exclude_terms": ft_excl_terms,
        "exclude_study_types": excl_types,
        "allowed_languages": lang_allow,
        "fulltext_required": ft_req,
        "year_min": y_min,
        "year_max": y_max,
        "clause_priority": clause_priority,
        "appraisal_instrument": {"name": APPRAISAL_INSTRUMENT, "version": APPRAISAL_VERSION},
        "frozen": True,
    }
    proto_hash = hashlib.sha256(json.dumps(protocol_core, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    protocol_core["protocol_sha256"] = proto_hash
    return protocol_core


def _find_passage_match(passages: list[dict[str, str]], pattern: re.Pattern[str]) -> tuple[str, str] | None:
    for p in passages:
        txt = str(p.get("text") or "")
        m = pattern.search(txt)
        if m:
            return str(p.get("locator") or "body"), m.group(0)
    return None


def appraise_study_five_domains(rec: dict[str, Any], has_full: bool) -> dict[str, Any]:
    """Negation-guarded 5-domain quality appraisal with exact passage locators and review downgrade."""
    access = str(rec.get("material_access") or ("fulltext_local_text" if has_full else "abstract_only"))
    src_loc = str(rec.get("source_file") or rec["record_id"])
    passages = list(rec.get("passages") or [])
    if not passages:
        if has_full and rec.get("text"):
            passages = [{"locator": f"{src_loc}#body", "text": str(rec["text"])[:16000]}]
        elif rec.get("abstract"):
            passages = [{"locator": f"{src_loc}#abstract", "text": str(rec["abstract"])}]
    if not has_full:
        passages = [p for p in passages if "abstract" in str(p.get("locator") or "").lower()] or [{"locator": f"{src_loc}#abstract", "text": f"{rec.get('title', '')} {rec.get('abstract', '')}"}]

    title_type = f"{rec.get('title', '')} {rec.get('type', '')}"
    is_secondary_review = bool(SECONDARY_REVIEW_RE.search(title_type))

    def _eval_domain(
        pos_re: re.Pattern[str],
        neg_re: re.Pattern[str] | None,
        *,
        abstract_assessable: bool,
        low_msg: str,
        high_msg: str,
        missing_msg: str,
    ) -> dict[str, str]:
        if neg_re is not None:
            neg_hit = _find_passage_match(passages, neg_re)
            if neg_hit:
                loc, cue = neg_hit
                return {"judgment": "HIGH_RISK", "locator": loc, "rationale": f"{high_msg} (risk_cue='{cue}')."}
        pos_hit = _find_passage_match(passages, pos_re)
        if pos_hit:
            loc, cue = pos_hit
            return {
                "judgment": "LOW_RISK" if has_full else "SOME_CONCERNS",
                "locator": loc,
                "rationale": f"{low_msg} (cue='{cue}')" + ("" if has_full else "; abstract-only pending full-text audit"),
            }
        if not has_full and not abstract_assessable:
            return {
                "judgment": "NOT_ASSESSABLE_FROM_ABSTRACT",
                "locator": f"{src_loc}#abstract",
                "rationale": "Cannot verify this domain from title/abstract alone; requires full-text Methods inspection.",
            }
        return {
            "judgment": "NO_INFORMATION",
            "locator": passages[0]["locator"] if passages else src_loc,
            "rationale": missing_msg,
        }

    if is_secondary_review:
        d1 = {
            "judgment": "SOME_CONCERNS",
            "locator": passages[0]["locator"] if passages else src_loc,
            "rationale": f"Secondary review/perspective article (type='{rec.get('type') or 'review'}'), not a primary empirical trial.",
        }
    else:
        d1 = _eval_domain(
            DESIGN_CUE_RE, None, abstract_assessable=True,
            low_msg="Primary study design explicitly stated",
            high_msg="",
            missing_msg="No explicit primary study design cue detected in inspected passages.",
        )

    d2 = _eval_domain(
        SAMPLE_CUE_RE, SAMPLE_NEG_RE, abstract_assessable=False,
        low_msg="Sample size or analytical replicate unit reported",
        high_msg="Deficient or unreplicated sample size explicitly flagged",
        missing_msg="Sample size / biological or analytical replicate unit not stated in inspected passages.",
    )
    d3 = _eval_domain(
        COMPARATOR_CUE_RE, COMPARATOR_NEG_RE, abstract_assessable=False,
        low_msg="Control group, placebo, or baseline comparator reported",
        high_msg="Absence of control/comparator arm explicitly stated",
        missing_msg="No explicit control or baseline comparator detected in inspected passages.",
    )
    d4 = _eval_domain(
        BIAS_SAFEGUARD_RE, BIAS_RISK_RE, abstract_assessable=False,
        low_msg="Bias/confounding safeguard or transparency disclosure present",
        high_msg="High risk of bias or unadjusted confounding explicitly reported",
        missing_msg="No explicit bias/confounding safeguard detected in inspected passages.",
    )
    d5 = {
        "judgment": "LOW_RISK" if has_full else "HIGH_RISK",
        "locator": passages[0]["locator"] if passages else src_loc,
        "rationale": f"Full-text access verified ({access})." if has_full else f"Limited to {access}; full methods/results not independently verifiable.",
    }

    domains = {
        "D1_study_design_and_protocol": d1,
        "D2_sample_and_inference_unit": d2,
        "D3_comparator_and_controls": d3,
        "D4_bias_confounding_and_transparency": d4,
        "D5_fulltext_access_and_verifiability": d5,
    }
    judgments = [d["judgment"] for d in domains.values()]
    if not has_full:
        overall = "INCOMPLETE_ABSTRACT_ONLY"
    elif "HIGH_RISK" in judgments:
        overall = "LOW"
    elif is_secondary_review:
        overall = "SECONDARY_REVIEW_NOT_PRIMARY_TRIAL"
    elif judgments.count("LOW_RISK") >= 4:
        overall = "HIGH"
    elif judgments.count("LOW_RISK") >= 2:
        overall = "MODERATE"
    else:
        overall = "LOW"

    return {
        "instrument": APPRAISAL_INSTRUMENT,
        "instrument_version": APPRAISAL_VERSION,
        "is_secondary_review": is_secondary_review,
        "overall_quality_tier": overall,
        "domains": domains,
    }


def _compile_word_patterns(terms: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    return [(t, re.compile(rf"(?i)\b{re.escape(t)}\b")) for t in terms if t.strip()]


def two_stage_screen_and_appraise(
    canonical: list[dict[str, Any]],
    proto: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute word-boundary clause-priority two-stage screening, 5-domain appraisal, and true opposing-passage binding."""
    screening_ledger: list[dict[str, Any]] = []
    included_appraisals: list[dict[str, Any]] = []
    disagreement_queue: list[dict[str, Any]] = []
    conflict_rows: list[dict[str, Any]] = []

    req_pats = _compile_word_patterns(proto["required_terms"])
    excl_pats = _compile_word_patterns(proto["exclude_terms"])
    ft_excl_pats = _compile_word_patterns(proto["exclude_terms"] + proto["fulltext_exclude_terms"])
    excl_types = proto["exclude_study_types"]
    lang_allow = proto["allowed_languages"]
    y_min, y_max = proto["year_min"], proto["year_max"]
    ft_req = proto["fulltext_required"]

    for rec in canonical:
        # Stage 1 strictly inspects title + abstract ONLY!
        screening_blob = f"{rec.get('title', '')} {rec.get('abstract', '')}".strip()
        fulltext_blob = str(rec.get("text") or "").strip()
        access = str(rec.get("material_access") or "discovery_only")
        has_full = bool(fulltext_blob) and is_fulltext_access(access)
        yr = rec.get("year")
        rtype = str(rec.get("type") or "").strip().lower()
        lang = str(rec.get("language") or "en").strip().lower()

        stage1_dec = "include"
        reason_code = "INC_PROTOCOL_MATCH"
        matched_clause = "INC-01"
        reviewer_status = "single_deterministic"
        disagreement_note = ""

        excl_hit = next((t for t, p in excl_pats if p.search(screening_blob)), None)
        req_hit = any(p.search(screening_blob) for _, p in req_pats) if req_pats else True

        if y_min is not None and yr is not None and int(yr) < int(y_min):
            stage1_dec, reason_code, matched_clause = "exclude", "EXC_OUT_OF_YEAR_WINDOW", "EXC-YEAR-MIN"
        elif y_max is not None and yr is not None and int(yr) > int(y_max):
            stage1_dec, reason_code, matched_clause = "exclude", "EXC_OUT_OF_YEAR_WINDOW", "EXC-YEAR-MAX"
        elif lang_allow and lang and lang not in lang_allow:
            stage1_dec, reason_code, matched_clause = "exclude", "EXC_LANGUAGE_RESTRICTION", "EXC-LANG"
        elif excl_types and rtype and any(et in rtype for et in excl_types):
            stage1_dec, reason_code, matched_clause = "exclude", "EXC_STUDY_TYPE", f"EXC-TYPE:{rtype}"
        elif excl_hit is not None:
            stage1_dec, reason_code, matched_clause = "exclude", "EXC_EXCLUSION_CLAUSE", f"EXC-TERM:{excl_hit}"
            if req_pats and req_hit:
                reviewer_status = "dual_disagreement_flagged"
                disagreement_note = f"Matched required concept AND exclusion term '{excl_hit}' in title/abstract; held for dual-reviewer check (exclusion priority applied)."
        elif req_pats and not req_hit:
            stage1_dec, reason_code, matched_clause = "exclude", "EXC_MISSING_REQUIRED_CONCEPT", "EXC-CONCEPT"
        elif (y_min is not None or y_max is not None) and yr is None:
            reviewer_status = "dual_disagreement_flagged"
            disagreement_note = "Publication year missing while year window is active; tentatively retained for Stage 2 verification rather than silent exclusion."

        # Stage 2 Full-Text Eligibility
        stage2_dec = "not_sought"
        stage2_reason_code = "NOT_SOURCED_TITLE_ABSTRACT_EXCLUDED"
        stage2_exclusion_reason = ""
        if stage1_dec == "include":
            if has_full:
                matched_ft = [t for t, p in ft_excl_pats if p.search(fulltext_blob)]
                if matched_ft:
                    stage2_dec = "excluded_fulltext"
                    stage2_reason_code = "EXC_FULLTEXT_EXCLUSION_TERM"
                    stage2_exclusion_reason = f"Full text matched predeclared exclusion term: {matched_ft[0]}"
                else:
                    stage2_dec = "included_fulltext"
                    stage2_reason_code = "INC_FULLTEXT_ELIGIBLE"
            elif ft_req:
                stage2_dec = "not_retrieved"
                stage2_reason_code = "NOT_RETRIEVED_FULLTEXT_REQUIRED"
            elif access in {"discovery_only", "blocked_or_unavailable"}:
                stage2_dec = "not_retrieved"
                stage2_reason_code = "NOT_RETRIEVED_NO_TEXT_OR_ABSTRACT"
            else:
                stage2_dec = "included_abstract_only_caveat"
                stage2_reason_code = "INC_ABSTRACT_ONLY_CAVEAT"

        evidence_blob = f"{screening_blob} {fulltext_blob}".strip()
        row = {
            "record_id": rec["record_id"],
            "title": rec.get("title") or rec["record_id"],
            "year": yr,
            "date_basis": rec.get("date_basis", "unknown"),
            "doi": normalize_doi(str(rec.get("doi") or "")) or None,
            "alt_dois": rec.get("alt_dois", []),
            "found_in_sources": rec.get("found_in_sources", [rec.get("source", "local")]),
            "material_access": access,
            "screened_fields_stage1": "title+abstract",
            "stage1_title_abstract": stage1_dec,
            "stage2_fulltext": stage2_dec,
            "reason_code": reason_code,
            "matched_clause": matched_clause,
            "stage1_reason_code": reason_code,
            "stage2_reason_code": stage2_reason_code,
            "stage2_exclusion_reason": stage2_exclusion_reason,
            "reviewer_status": reviewer_status,
            "quote": " ".join(evidence_blob.split())[:180],
        }
        screening_ledger.append(row)

        if reviewer_status == "dual_disagreement_flagged":
            disagreement_queue.append({
                "record_id": rec["record_id"],
                "stage1_decision": stage1_dec,
                "matched_clause": matched_clause,
                "note": disagreement_note,
            })

        if stage1_dec == "include" and stage2_dec in ("included_fulltext", "included_abstract_only_caveat"):
            is_prosp = bool(PROSPECTIVE_CUE_RE.search(evidence_blob[:6000]))
            is_retracted = bool(RETRACTION_CUE_RE.search(evidence_blob[:6000]))
            passages = extract_directional_passages(rec, max_per_record=4)
            opp_passages = [p for p in passages if p["direction"] in {"opposing_or_null", "mixed"}]
            pos_passages = [p for p in passages if p["direction"] in {"supportive", "mixed"}]

            has_pos = bool(pos_passages)
            has_neg = bool(opp_passages)
            ev_state = classify_five_state_support(
                has_direct_support=has_pos or has_full or bool(rec.get("abstract")),
                has_opposing_evidence=has_neg,
                missing_critical_context=(not has_full or is_prosp),
                is_superseded_or_retracted=is_retracted,
                source_unavailable=False,
                material_access=access,
            )
            appraisal = appraise_study_five_domains(rec, has_full)
            direction = "mixed" if (has_pos and has_neg) else ("opposing_or_null" if has_neg else ("supportive" if has_pos else "descriptive"))
            top_p = passages[0] if passages else None
            top_loc = top_p["locator"] if top_p else str(rec.get("source_file") or rec["record_id"])
            top_quote = top_p["quote"] if top_p else " ".join(evidence_blob.split())[:200]

            included_appraisals.append({
                "record_id": rec["record_id"],
                "title": rec.get("title") or rec["record_id"],
                "year": yr,
                "date_basis": rec.get("date_basis", "unknown"),
                "doi": normalize_doi(str(rec.get("doi") or "")) or None,
                "material_access": access,
                "fulltext_available": has_full,
                "is_prospective_protocol": is_prosp,
                "is_retracted_or_erratum": is_retracted,
                "direction": direction,
                "evidence_state": ev_state,
                "quality_appraisal": appraisal,
                "locator": top_loc,
                "quote": top_quote,
            })
            # STRICT: bind ONLY true opposing/null/mixed passages into conflict_rows (NEVER positive passages[0]!)
            for op in opp_passages[:2]:
                conflict_rows.append({
                    "record_id": rec["record_id"],
                    "direction": op["direction"],
                    "overall_quality_tier": appraisal["overall_quality_tier"],
                    "locator": op["locator"],
                    "quote": op["quote"],
                })

    return screening_ledger, included_appraisals, disagreement_queue, conflict_rows


def verify_slr_report(report_path: Path, review_bundle: dict[str, Any]) -> dict[str, Any]:
    """Read-only verification of systematic_review_report.md: PRISMA counts, excluded leakage, and overclaims."""
    txt = report_path.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []

    over = detect_overclaims(txt)
    if over:
        errors.append(f"Overclaim detected in systematic review report: {over}")

    prisma = review_bundle["prisma_flow"]
    count_checks = [
        ("n_identified", r"Records identified across sources:\s*\*\*(\d+)\*\*"),
        ("n_dedup", r"Duplicates removed.*?:\s*\*\*(\d+)\*\*"),
        ("n_screened", r"Records screened.*?:\s*\*\*(\d+)\*\*"),
        ("n_screen_excl", r"Excluded at Title/Abstract:\s*\*\*(\d+)\*\*"),
        ("n_not_retrieved", r"Reports not retrieved.*?:\s*\*\*(\d+)\*\*"),
        ("n_elig_excl", r"Excluded at Full-Text.*?:\s*\*\*(\d+)\*\*"),
        ("n_included", r"Studies included in synthesis:\s*\*\*(\d+)\*\*"),
    ]
    mismatches: list[str] = []
    for key, pat in count_checks:
        m = re.search(pat, txt)
        if m and int(m.group(1)) != int(prisma[key]):
            mismatches.append(f"{key}: report={m.group(1)} vs ledger={prisma[key]}")
    if mismatches:
        errors.append(f"PRISMA flow counts in report diverge from ledger: {mismatches}")

    included_ids = {a["record_id"].lower() for a in review_bundle["included_study_appraisals"]}
    excluded_ids = {
        s["record_id"].lower()
        for s in review_bundle["screening_ledger"]
        if s["stage1_title_abstract"] == "exclude" or s["stage2_fulltext"] in {"excluded_fulltext", "not_retrieved"}
    }
    leaked_excluded: list[str] = []
    unknown_cites: list[str] = []
    for line in txt.splitlines():
        low_l = line.lower()
        if any(k in low_l for k in ("excluded", "not retrieved", "disagreement", "queue")):
            continue
        for m in REPORT_CITE_RE.finditer(line):
            raw = m.group(1).strip()
            tok = raw.lower()
            if tok in {"supported", "partial", "refuted", "outdated", "unknown"}:
                continue
            if tok in excluded_ids:
                leaked_excluded.append(raw)
            elif tok not in included_ids and re.match(r"^(?:[a-z]+[-_:]?\d+|10\.\d+|pmc\d+|arxiv:|w\d+)", tok, re.I):
                unknown_cites.append(raw)
    if leaked_excluded:
        errors.append(f"Excluded or not-retrieved study cited in included synthesis: {sorted(set(leaked_excluded))}")
    if unknown_cites:
        errors.append(f"Unknown citation ID in report: {sorted(set(unknown_cites))}")

    return {
        "passed": len(errors) == 0,
        "overclaims": over,
        "prisma_mismatches": mismatches,
        "leaked_excluded_ids": sorted(set(leaked_excluded)),
        "unknown_cites": sorted(set(unknown_cites)),
        "errors": errors,
    }


def render_systematic_review_report(
    case_id: str,
    proto: dict[str, Any],
    prisma: dict[str, Any],
    appraisals: list[dict[str, Any]],
    disagreements: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    sensitivity_note: str,
) -> str:
    q_tiers = Counter(a["quality_appraisal"]["overall_quality_tier"] for a in appraisals)
    lines = [
        f"# Auditable Systematic Literature Review Report ({case_id})",
        "",
        f"**Research Question**: {proto['research_question']}",
        f"**Frozen Protocol Lock**: `{proto['protocol_version']}` (SHA-256: `{proto['protocol_sha256']}`)",
        f"**Search Query**: `{proto['search_strategy']['boolean_query']}` across `{', '.join(proto['databases'])}`",
        "**Scope Boundary**: Bounded to the finite local snapshot; global literature completeness and completed prospective experiments are NOT claimed.",
        "",
        "## 1. PRISMA 2020 Flow Accounting (Conserved from Screening Ledger)",
        f"- Records identified across sources: **{prisma['n_identified']}** (`{json.dumps(prisma['identified_by_source'], ensure_ascii=False)}`)",
        f"- Duplicates removed (with lineage log): **{prisma['n_dedup']}**",
        f"- Records screened (Stage 1 Title/Abstract ONLY): **{prisma['n_screened']}**",
        f"- Excluded at Title/Abstract: **{prisma['n_screen_excl']}** (`{json.dumps(prisma.get('stage1_exclusion_reason_counts', {}), ensure_ascii=False)}`)",
        f"- Reports sought for retrieval: **{prisma['n_sought']}**",
        f"- Reports not retrieved (kept separate from exclusions): **{prisma['n_not_retrieved']}**",
        f"- Reports assessed for full-text eligibility: **{prisma['n_assessed_for_eligibility']}**",
        f"- Excluded at Full-Text (with clause reasons): **{prisma['n_elig_excl']}** (`{json.dumps(prisma.get('fulltext_exclusion_reason_counts', {}), ensure_ascii=False)}`)",
        f"- Studies included in synthesis: **{prisma['n_included']}** (PRISMA balanced=`{prisma['balanced']}`)",
        "",
        "## 2. Five-Domain Study Quality Appraisal (`SLR-Appraisal-v2-MultiDomain`)",
        f"- Quality Tier Distribution: `{json.dumps(dict(sorted(q_tiers.items())), ensure_ascii=False)}`",
        f"- **Sensitivity Impact on Synthesis**: {sensitivity_note}",
        "",
        "| Record ID | Access | Quality Tier | D1 Design | D2 Sample/Unit | D3 Comparator | D4 Bias/Safeguards | D5 Verifiability | Direction |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for a in appraisals[:35]:
        d = a["quality_appraisal"]["domains"]
        lines.append(
            f"| `{a['record_id']}` | `{a['material_access']}` | `{a['quality_appraisal']['overall_quality_tier']}` | "
            f"{d['D1_study_design_and_protocol']['judgment']} | {d['D2_sample_and_inference_unit']['judgment']} | "
            f"{d['D3_comparator_and_controls']['judgment']} | {d['D4_bias_confounding_and_transparency']['judgment']} | "
            f"{d['D5_fulltext_access_and_verifiability']['judgment']} | {a['direction']} |"
        )

    lines.extend(["", "## 3. Opposing / Mixed Evidence Preserved"])
    if conflicts:
        for c in conflicts[:12]:
            lines.append(f"- **[{c['record_id']}]** (`{c['direction']}`, quality=`{c['overall_quality_tier']}`, locator `{c['locator']}`): \"{c['quote']}\"")
    else:
        lines.append("- No direct empirical opposing/null passages detected among included studies.")

    lines.extend(["", "## 4. Dual-Reviewer Disagreement / Borderline Queue"])
    if disagreements:
        for dg in disagreements[:15]:
            lines.append(f"- `{dg['record_id']}` ({dg['stage1_decision']}, `{dg['matched_clause']}`): {dg['note']}")
    else:
        lines.append("- Zero clause-conflict disagreements flagged.")

    lines.extend(["", "## 5. Evidence-Grounded Research Gaps"])
    if prisma["n_not_retrieved"] > 0:
        lines.append(f"- **Full-text retrieval gap**: {prisma['n_not_retrieved']} Stage-1 eligible record(s) lacked full text at this snapshot (`n_not_retrieved`).")
    low_q = sum(1 for a in appraisals if a["quality_appraisal"]["overall_quality_tier"] in {"LOW", "INCOMPLETE_ABSTRACT_ONLY", "SECONDARY_REVIEW_NOT_PRIMARY_TRIAL"})
    if low_q:
        lines.append(f"- **Methodological appraisal boundary**: {low_q}/{len(appraisals)} included study/studies are LOW quality, abstract-only, or secondary reviews rather than low-risk primary empirical trials.")
    if conflicts:
        lines.append(f"- **Unresolved directional divergence**: {len(conflicts)} opposing/null passage(s) require controlled replication across standardized endpoints.")
    return "\n".join(lines) + "\n"


def _compute_bundle(input_dir: Path, work_dir: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = load_input_bundle(input_dir, work_dir=work_dir)
    case_id = bundle["case_id"]
    raw_records = bundle["records"]
    by_source = summarize_identification_by_source(raw_records)
    proto = build_frozen_protocol(bundle["case_data"], bundle["queries"], raw_records)
    canonical, dup_log, meta_conflicts = deduplicate_bibliography(raw_records)
    screening_ledger, appraisals, disagreements, conflicts = two_stage_screen_and_appraise(canonical, proto)

    n_screen_excl = sum(1 for s in screening_ledger if s["stage1_title_abstract"] == "exclude")
    n_sought = sum(1 for s in screening_ledger if s["stage1_title_abstract"] == "include")
    n_not_retrieved = sum(1 for s in screening_ledger if s["stage2_fulltext"] == "not_retrieved")
    n_fulltext_excl = sum(1 for s in screening_ledger if s["stage2_fulltext"] == "excluded_fulltext")
    n_awaiting_elig = sum(1 for s in screening_ledger if s["stage2_fulltext"] == "awaiting_eligibility")
    n_included = len(appraisals)

    stage1_reason_counts = dict(sorted(Counter(s["stage1_reason_code"] for s in screening_ledger if s["stage1_title_abstract"] == "exclude").items()))
    fulltext_exclusion_reason_counts = dict(sorted(Counter(str(s["stage2_exclusion_reason"]) for s in screening_ledger if s["stage2_fulltext"] == "excluded_fulltext").items()))

    prisma = verify_prisma_conservation(
        n_identified=len(raw_records),
        n_dedup=len(dup_log),
        n_screened=len(canonical),
        n_screen_excl=n_screen_excl,
        n_sought=n_sought,
        n_not_retrieved=n_not_retrieved,
        n_elig_excl=n_fulltext_excl,
        n_awaiting_elig=n_awaiting_elig,
        n_included=n_included,
        identified_by_source=by_source,
    )
    prisma["stage1_exclusion_reason_counts"] = stage1_reason_counts
    prisma["fulltext_exclusion_reason_counts"] = fulltext_exclusion_reason_counts
    prisma["not_retrieved_record_ids"] = [s["record_id"] for s in screening_ledger if s["stage2_fulltext"] == "not_retrieved"]

    high_mod_n = sum(1 for a in appraisals if a["quality_appraisal"]["overall_quality_tier"] in {"HIGH", "MODERATE"})
    sec_rev_n = sum(1 for a in appraisals if a["quality_appraisal"]["overall_quality_tier"] == "SECONDARY_REVIEW_NOT_PRIMARY_TRIAL")
    abs_only_n = sum(1 for a in appraisals if not a["fulltext_available"])
    sensitivity_note = (
        f"Of {n_included} included studies, {high_mod_n} achieved HIGH/MODERATE primary-study full-text quality, {sec_rev_n} are secondary reviews, and {abs_only_n} are INCOMPLETE_ABSTRACT_ONLY; "
        + ("anchor primary conclusions on HIGH/MODERATE primary full-text studies while treating secondary reviews and abstract-only rows as caveated." if high_mod_n else "zero HIGH/MODERATE primary full-text studies exist in this run, so conclusions remain provisional.")
    )

    review_bundle = {
        "case_id": case_id,
        "scope_disclaimer": "Exact-source bounded systematic review; neither global completeness/novelty nor completed future experiments are claimed.",
        "block_index_summary": bundle["block_index_summary"],
        "protocol": proto,
        "prisma_flow": prisma,
        "screening_ledger": screening_ledger,
        "dual_reviewer_disagreement_queue": disagreements,
        "included_study_appraisals": appraisals,
        "opposing_evidence": conflicts,
        "sensitivity_impact_summary": sensitivity_note,
        "deduplication_log": dup_log,
        "metadata_conflicts": meta_conflicts,
    }
    return bundle, review_bundle


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="systematic-review-pipeline")

    bundle, review_bundle = _compute_bundle(input_dir, work_dir=work_dir)
    case_id = review_bundle["case_id"]
    proto = review_bundle["protocol"]
    prisma = review_bundle["prisma_flow"]
    screening_ledger = review_bundle["screening_ledger"]
    disagreements = review_bundle["dual_reviewer_disagreement_queue"]
    appraisals = review_bundle["included_study_appraisals"]
    conflicts = review_bundle["opposing_evidence"]
    sensitivity_note = review_bundle["sensitivity_impact_summary"]

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="systematic-review-pipeline")
    budget_guard.tick_phase(budget_file, "execute_systematic_review")

    bundle_path = output_dir / "systematic_review_bundle.json"
    result_guard.atomic_write_json(bundle_path, review_bundle)

    report_md = render_systematic_review_report(case_id, proto, prisma, appraisals, disagreements, conflicts, sensitivity_note)
    report_path = output_dir / "systematic_review_report.md"
    result_guard.atomic_write_text(report_path, report_md)
    report_check = verify_slr_report(report_path, review_bundle)

    append_audit_entry(
        audit_file,
        phase="systematic_review",
        action="pipeline_completed",
        actor="systematic_review_runner.py",
        output={"identified": prisma["n_identified"], "included": prisma["n_included"], "balanced": prisma["balanced"]},
        record_id=case_id,
    )

    gaps = list(bundle["ingest_gaps"])
    if not prisma["balanced"]:
        gaps.append({"code": "PRISMA_FLOW_IMBALANCE", "severity": "error", "source": "systematic_review_runner", "message": "PRISMA conservation equations failed."})
    if prisma["n_not_retrieved"]:
        gaps.append({
            "code": "FULLTEXT_NOT_RETRIEVED",
            "severity": "info",
            "source": "systematic_review_runner",
            "record_ids": prisma["not_retrieved_record_ids"],
            "message": f"{prisma['n_not_retrieved']} report(s) sought for retrieval lacked available full text; kept in n_not_retrieved.",
        })
    abs_only_n = sum(1 for a in appraisals if not a["fulltext_available"])
    if abs_only_n:
        gaps.append({"code": "ABSTRACT_ONLY_INCLUDED_CAVEAT", "severity": "info", "source": "systematic_review_runner", "message": f"{abs_only_n} included study/studies have abstract only -> graded INCOMPLETE_ABSTRACT_ONLY / PARTIAL."})
    if disagreements:
        gaps.append({"code": "DUAL_REVIEWER_DISAGREEMENTS_FLAGGED", "severity": "info", "source": "systematic_review_runner", "message": f"{len(disagreements)} borderline record(s) queued for dual-reviewer adjudication."})

    high_mod_n = sum(1 for a in appraisals if a["quality_appraisal"]["overall_quality_tier"] in {"HIGH", "MODERATE"})
    patch = {
        "artifacts": ["result.json", "systematic_review_bundle.json", "systematic_review_report.md"],
        "sources": [
            {"record_id": a["record_id"], "title": a["title"], "doi": a["doi"], "year": a["year"], "material_access": a["material_access"], "quality_tier": a["quality_appraisal"]["overall_quality_tier"]}
            for a in appraisals
        ],
        "protocol": proto,
        "prisma_flow": prisma,
        "screening_ledger": screening_ledger,
        "dual_reviewer_disagreement_queue": disagreements,
        "included_study_appraisals": appraisals,
        "opposing_evidence": conflicts,
        "sensitivity_impact_summary": sensitivity_note,
        "report_verification": report_check,
        "uncertainty": {
            "bounded_scope": True,
            "protocol_sha256": proto["protocol_sha256"],
            "n_not_retrieved": prisma["n_not_retrieved"],
            "abstract_only_included_count": abs_only_n,
            "dual_reviewer_disagreements_count": len(disagreements),
            "opposing_evidence_count": len(conflicts),
            "pagination_warnings": bundle["pagination_warnings"],
        },
        "metrics": {
            "n_identified": prisma["n_identified"],
            "n_dedup": prisma["n_dedup"],
            "n_screened": prisma["n_screened"],
            "n_excluded": prisma["n_screen_excl"],
            "n_not_retrieved": prisma["n_not_retrieved"],
            "n_fulltext_excluded": prisma["n_elig_excl"],
            "n_awaiting_elig": prisma["n_awaiting_elig"],
            "n_excluded_total": prisma["n_screen_excl"] + prisma["n_elig_excl"],
            "n_included": prisma["n_included"],
            "n_high_or_moderate_quality": high_mod_n,
            "n_opposing_evidence": len(conflicts),
            "prisma_balanced": 1 if prisma["balanced"] else 0,
        },
        "metric_provenance": {
            "n_identified": "systematic_review_bundle.json:prisma_flow.n_identified",
            "n_dedup": "systematic_review_bundle.json:deduplication_log",
            "n_screened": "systematic_review_bundle.json:screening_ledger",
            "n_excluded": "systematic_review_bundle.json:screening_ledger",
            "n_not_retrieved": "systematic_review_bundle.json:prisma_flow.n_not_retrieved",
            "n_fulltext_excluded": "systematic_review_bundle.json:screening_ledger",
            "n_awaiting_elig": "systematic_review_bundle.json:prisma_flow.n_awaiting_elig",
            "n_excluded_total": "systematic_review_bundle.json:screening_ledger",
            "n_included": "systematic_review_bundle.json:included_study_appraisals",
            "n_high_or_moderate_quality": "systematic_review_bundle.json:included_study_appraisals",
            "n_opposing_evidence": "systematic_review_bundle.json:opposing_evidence",
            "prisma_balanced": "systematic_review_bundle.json:prisma_flow.balanced",
        },
        "findings": [
            f"Completed 5-stage systematic review (protocol {proto['protocol_sha256']}) over {prisma['n_identified']} identified -> {prisma['n_dedup']} duplicates -> {prisma['n_screened']} screened -> {prisma['n_screen_excl']} Stage-1 exclusions, {prisma['n_not_retrieved']} not retrieved, {prisma['n_elig_excl']} Stage-2 exclusions, {prisma['n_included']} included ({high_mod_n} HIGH/MODERATE primary, {len(conflicts)} opposing/mixed passages); PRISMA balanced={prisma['balanced']}."
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(output_dir, result_file="result.json", required_files=["systematic_review_bundle.json", "systematic_review_report.md"])
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auditable Systematic Literature Review Pipeline.")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--work", default="")
    parser.add_argument("--verify-report", default="", help="Read-only audit of systematic_review_report.md against ledger & PRISMA counts.")
    parser.add_argument("--out-json", default="", help="Optional JSON output path for --verify-report.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if report verification fails.")
    args = parser.parse_args(argv)
    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )

    if args.verify_report:
        _, review_bundle = _compute_bundle(paths["input_dir"])
        vrep = verify_slr_report(Path(args.verify_report), review_bundle)
        if args.out_json:
            result_guard.atomic_write_json(Path(args.out_json), {"case_id": review_bundle["case_id"], "report_verified": args.verify_report, **vrep})
        print(json.dumps({"case_id": review_bundle["case_id"], **vrep}, ensure_ascii=False))
        return 0 if (vrep["passed"] or not args.strict) else 1

    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"])
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"], "report_passed": res["report_verification"]["passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
