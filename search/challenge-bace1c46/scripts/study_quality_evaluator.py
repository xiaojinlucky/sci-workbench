"""Scale-Routed, Version-Pinned Study Quality & Risk-of-Bias Appraisal Engine (challenge-bace1c46).

Upgraded offline engine addressing:
1. Study-type classification + version-pinned appraisal scales (RoB 2.0, ROBINS-I V2 (2024),
   NOS/STROBE, AMSTAR-2, and Experimental/Computational Quality Framework) — prevents legacy/current
   domain mismatch (e.g., ROBINS-I 2016 vs V2 2024).
2. Material-tier separation (fulltext_verified | abstract_only | scanned_or_unparsed_pdf | metadata_only)
   with strict abstention (insufficient_evidence + UNKNOWN) on fulltext-only domains; UNKNOWN NEVER
   silently collapses to low risk.
3. Sentence-level verbatim quotes with paragraph_id, section, and page_number anchors.
4. Explicit inference-unit/pseudoreplication check, observational-causality overclaim warning,
   preprint peer-review status flag, and Data/Code Availability check.
5. Transparent tier_derivation_rule, optional human-review reconciliation, synchronized Markdown
   traffic-light matrix, JSON appraisal table, and Harbor result.json.
"""

from __future__ import annotations

import argparse
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
from corpus_loader import load_input_bundle, segment_text_to_paragraphs
from evidence_state_guard import EvidenceState, RETRACTION_CUE_RE, extract_sentence_anchor
from prisma_flow_auditor import is_preprint

# Version-pinned appraisal scale registries
SCALES: dict[str, dict[str, Any]] = {
    "ROB2_2019": {
        "scale_name": "Cochrane RoB 2",
        "scale_version": "2.0 (2019)",
        "applies_to": "randomized_controlled_trial",
        "domains": [
            {
                "dim_id": "RoB2_D1_randomization",
                "label": "Bias arising from the randomization process",
                "requires_fulltext": True,
                "cues": [r"\b(?:random(?:ly|i[sz]ed|i[sz]ation)|allocation\s+concealment|sealed\s+envelope|central\s+randomi[sz]ation|block\s+random|随机分组|分配隐藏)\b"],
                "concern_cues": [r"\b(?:quasi-random|alternat(?:e|ing)\s+allocation|unconcealed|baseline\s+imbalance|open\s+allocation|未随机|基线不平衡)\b"],
            },
            {
                "dim_id": "RoB2_D2_deviations",
                "label": "Bias due to deviations from intended interventions",
                "requires_fulltext": True,
                "cues": [r"\b(?:double[- ]blind|triple[- ]blind|placebo[- ]controlled|intention[- ]to[- ]treat|ITT|adherence|双盲|意向性分析)\b"],
                "concern_cues": [r"\b(?:open[- ]label|unblinded|not\s+blinded|per[- ]protocol\s+only|high\s+crossover|未盲法|开放标签)\b"],
            },
            {
                "dim_id": "RoB2_D3_missing_outcome_data",
                "label": "Bias due to missing outcome data",
                "requires_fulltext": True,
                "cues": [r"\b(?:attrition|dropout|lost\s+to\s+follow[- ]?up|complete\s+follow[- ]?up|multiple\s+imputation|CONSORT\s+flow|失访|脱落)\b"],
                "concern_cues": [r"\b(?:high\s+dropout|high\s+attrition|missing\s+data\s+not\s+explained|differential\s+loss|substantial\s+loss\s+to\s+follow)\b"],
            },
            {
                "dim_id": "RoB2_D4_outcome_measurement",
                "label": "Bias in measurement of the outcome",
                "requires_fulltext": True,
                "cues": [r"\b(?:blinded\s+assessor|masked\s+outcome|validated\s+instrument|calibrated|primary\s+outcome|primary\s+endpoint|盲态评估|主要结局)\b"],
                "concern_cues": [r"\b(?:unblinded\s+assessor|self[- ]reported\s+only|unvalidated|without\s+(?:defining|specifying)\s+(?:the\s+)?primary\s+(?:outcome|endpoint))\b"],
            },
            {
                "dim_id": "RoB2_D5_selective_reporting",
                "label": "Bias in selection of the reported result & transparency",
                "requires_fulltext": True,
                "cues": [r"\b(?:NCT\d{8}|ISRCTN\d+|ChiCTR\d+|pre[- ]?registered|protocol\s+published|statistical\s+analysis\s+plan|code\s+availab|data\s+availab|注册号|数据可得)\b"],
                "concern_cues": [r"\b(?:post[- ]?hoc\s+(?:outcome|subgroup)|selective\s+reporting|unregistered\s+trial|outcome\s+switching|p[- ]?hack)\b"],
            },
        ],
    },
    "ROBINS_I_V2_2024": {
        "scale_name": "ROBINS-I V2",
        "scale_version": "V2 (2024)",
        "applies_to": "non_randomized_intervention",
        "domains": [
            {
                "dim_id": "ROBINS_V2_D1_confounding",
                "label": "Bias due to confounding",
                "requires_fulltext": True,
                "cues": [r"\b(?:propensity\s+score|multivariable\s+adjust|confound(?:er|ing)|DAG|inverse\s+probability|IPTW|stratified\s+by|混杂因素|倾向评分)\b"],
                "concern_cues": [r"\b(?:unadjusted|residual\s+confounding|no\s+adjustment\s+for\s+confound|unmeasured\s+confounding|未校正混杂)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D2_classification",
                "label": "Bias in classification of interventions",
                "requires_fulltext": True,
                "cues": [r"\b(?:intervention\s+definition|exposure\s+ascertainment|registry\s+record|pharmacy\s+dispensing|confirmed\s+dose)\b"],
                "concern_cues": [r"\b(?:misclassification|self[- ]reported\s+exposure|immortal\s+time\s+bias|retrospective\s+recall)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D3_selection",
                "label": "Bias in selection of participants into the study",
                "requires_fulltext": True,
                "cues": [r"\b(?:inclusion\s+criteria|consecutive\s+patients|target\s+trial|inception\s+cohort|eligibility\s+criteria|纳入标准)\b"],
                "concern_cues": [r"\b(?:selection\s+bias|convenience\s+sample|survivor\s+bias|volunteer\s+bias|selected\s+after\s+start)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D4_deviations",
                "label": "Bias due to deviations from intended interventions",
                "requires_fulltext": True,
                "cues": [r"\b(?:adherence|co[- ]?intervention|fidelity|protocol\s+compliance|treatment\s+switching)\b"],
                "concern_cues": [r"\b(?:unbalanced\s+co[- ]?intervention|protocol\s+violation|untracked\s+switching)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D5_missing_data",
                "label": "Bias due to missing data",
                "requires_fulltext": True,
                "cues": [r"\b(?:missing\s+data|multiple\s+imputation|complete[- ]case|sensitivity\s+analysis|follow[- ]?up\s+rate)\b"],
                "concern_cues": [r"\b(?:high\s+missingness|missing\s+data\s+not\s+explained|excluded\s+due\s+to\s+missing|attrition\s+bias)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D6_measurement",
                "label": "Bias in measurement of outcomes",
                "requires_fulltext": True,
                "cues": [r"\b(?:blinded\s+outcome|objective\s+endpoint|ICD[- ]?10|validated\s+scale|adjudicated)\b"],
                "concern_cues": [r"\b(?:unblinded\s+outcome|subjective\s+self[- ]report|unvalidated\s+outcome|detection\s+bias)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D7_reported_result",
                "label": "Bias in selection of the reported result",
                "requires_fulltext": True,
                "cues": [r"\b(?:pre[- ]?specified|statistical\s+analysis\s+plan|registered\s+protocol|sensitivity\s+analyses|data\s+availab|code\s+availab)\b"],
                "concern_cues": [r"\b(?:post[- ]?hoc|selective\s+reporting|multiple\s+comparisons\s+without|no\s+protocol)\b"],
            },
        ],
    },
    "AMSTAR2_2017": {
        "scale_name": "AMSTAR-2",
        "scale_version": "2017",
        "applies_to": "systematic_review_or_meta_analysis",
        "domains": [
            {
                "dim_id": "AMSTAR2_D1_protocol_pico",
                "label": "PICO Components & Prior Protocol Registration",
                "requires_fulltext": False,
                "cues": [r"\b(?:PROSPERO|CRD\d+|PRISMA|PICO|a\s+priori\s+protocol|registered\s+protocol|系统综述注册)\b"],
                "concern_cues": [r"\b(?:not\s+registered|no\s+prior\s+protocol|post[- ]?hoc\s+review|narrative\s+only)\b"],
            },
            {
                "dim_id": "AMSTAR2_D2_search_strategy",
                "label": "Comprehensive Literature Search & Reproducible Queries",
                "requires_fulltext": True,
                "cues": [r"\b(?:PubMed|Embase|Web\s+of\s+Science|Scopus|Cochrane|search\s+strategy|Boolean|MeSH|检索式)\b"],
                "concern_cues": [r"\b(?:single\s+database\s+only|English[- ]only\s+without\s+justification|search\s+string\s+not\s+reported)\b"],
            },
            {
                "dim_id": "AMSTAR2_D3_duplicate_screening",
                "label": "Duplicate Study Selection, Extraction & Excluded-Studies Justification",
                "requires_fulltext": True,
                "cues": [r"\b(?:two\s+independent\s+reviewers|independently\s+screened|kappa|third\s+reviewer|arbitrat|双人独立筛选)\b"],
                "concern_cues": [r"\b(?:single\s+reviewer|no\s+list\s+of\s+excluded|disagreements\s+not\s+reported)\b"],
            },
            {
                "dim_id": "AMSTAR2_D4_rob_and_synthesis",
                "label": "Risk-of-Bias Appraisal & Statistical Synthesis (Heterogeneity/Publication Bias)",
                "requires_fulltext": True,
                "cues": [r"\b(?:RoB\s*2|ROBINS-I|Newcastle[- ]Ottawa|I\^?2|random[- ]effects|funnel\s+plot|Egger|heterogeneity|偏倚风险|异质性)\b"],
                "concern_cues": [r"\b(?:no\s+quality\s+assessment|heterogeneity\s+not\s+assessed|fixed[- ]effect\s+despite\s+high\s+heterogeneity)\b"],
            },
            {
                "dim_id": "AMSTAR2_D5_funding_coi",
                "label": "Funding, COI & Data/Code Transparency",
                "requires_fulltext": True,
                "cues": [r"\b(?:conflict\s+of\s+interest|funding|limitations?|data\s+availab|code\s+availab|利益冲突|局限性)\b"],
                "concern_cues": [r"\b(?:retract|corrigendum|erratum|undeclared\s+conflict)\b"],
            },
        ],
    },
    "GENERAL_SCIENTIFIC_V1": {
        "scale_name": "Structured Scientific Methodological Quality Framework",
        "scale_version": "v1.0 (Design/Sampling/Measurement/Statistics/Transparency)",
        "applies_to": "experimental_observational_or_computational",
        "domains": [
            {
                "dim_id": "D1_study_design",
                "label": "Study Design, Comparator & Causal Scope",
                "requires_fulltext": False,
                "cues": [r"\b(?:randomi[sz]ed|rct|cohort|case[- ]control|cross[- ]sectional|control\s+group|ablation|baseline|benchmark|prospectiv|随机|对照|消融)\b"],
                "concern_cues": [r"\b(?:no\s+control|single[- ]arm|uncontrolled|retrospective\s+only|non[- ]randomized|observational\s+only|无对照|回顾性)\b"],
            },
            {
                "dim_id": "D2_sampling_inference_unit",
                "label": "Sample Size, Biological/Inference Unit & Selection",
                "requires_fulltext": True,
                "cues": [r"\b(?:n\s*=\s*\d+|sample\s+size|participants|donors|biological\s+replicates?|independent\s+experiments?|inclusion\s+criteria|power\s+calculation|样本量|生物学重复)\b"],
                "concern_cues": [r"\b(?:convenience\s+sample|small\s+sample|underpowered|technical\s+replicates?\s+only|high\s+dropout|attrition|n\s*=\s*[12]\b)\b"],
            },
            {
                "dim_id": "D3_measurement_outcomes",
                "label": "Measurement Validity, Calibration & Outcome Definition",
                "requires_fulltext": True,
                "cues": [r"\b(?:blinded|validated|calibrated|primary\s+outcome|endpoint|protocol|standard\s+curve|reference\s+electrode|盲法|校准|主要结局)\b"],
                "concern_cues": [
                    r"\b(?:unblinded|not\s+blinded|unvalidated|self[- ]reported|surrogate\s+endpoint|post[- ]hoc\s+outcome|未盲)\b",
                    r"\b(?:without\s+(?:identifying|defining|specifying)\s+(?:the\s+)?|no\s+)(?:primary\s+)?(?:endpoint|outcome)\b",
                ],
            },
            {
                "dim_id": "D4_statistical_uncertainty",
                "label": "Statistical Analysis, Multiple Testing & Uncertainty Reporting",
                "requires_fulltext": True,
                "cues": [r"(?:p\s*[<=>]\s*0?\.\d+|confidence\s+interval|95%\s*CI|\bSD\b|\bSEM\b|\bIQR\b|\bANOVA\b|regression|bonferroni|\bFDR\b|Benjamini|Mann[- ]Whitney|Wilcoxon|effect\s+size)"],
                "concern_cues": [r"\b(?:multiple\s+comparisons?\s+without|unadjusted\s+p[- ]?value|p[- ]?hack|selective\s+reporting|no\s+uncertainty|error\s+bars?\s+not\s+defined)\b"],
            },
            {
                "dim_id": "D5_transparency_reproducibility",
                "label": "Transparency, Limitations, Ethics & Data/Code Availability",
                "requires_fulltext": True,
                "cues": [r"\b(?:limitations?|conflict\s+of\s+interest|funding|data\s+availab|code\s+availab|github\.com|zenodo|ethics|IRB|ARRIVE|局限|利益冲突|伦理|代码可用)\b"],
                "concern_cues": [r"\b(?:retract|corrigendum|erratum|withdrawn|missing\s+data\s+not\s+explained|code\s+not\s+available|data\s+not\s+available|撤稿)\b"],
            },
        ],
    },
}

OBSERVATIONAL_DESIGN_RE = re.compile(r"\b(?:cross[- ]sectional|retrospective|observational\s+cohort|case[- ]control|ecological\s+study)\b", re.I)
CAUSAL_OVERCLAIM_RE = re.compile(r"\b(?:proves?\s+that|demonstrates?\s+causation|causes?\s+|directly\s+leads?\s+to|causal\s+effect\s+established)\b", re.I)
CODE_DATA_RE = re.compile(r"\b(?:code\s+availab|data\s+availab|github\.com|gitlab\.com|zenodo\.org|figshare|osf\.io|GEO|GSE\d+|SRA|PRJNA\d+|supplementary\s+data)\b", re.I)


def detect_study_type_and_scale(rec: dict[str, Any], case_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Route to explicit requested scale version or infer best-fit scale from study design."""
    req_scale = str(
        rec.get("appraisal_scale")
        or (case_data.get("appraisal_scale") if str(case_data.get("appraisal_scale") or "").lower() != "auto" else "")
        or case_data.get("scale")
        or case_data.get("tool")
        or ""
    ).strip().lower()
    req_ver = str(rec.get("scale_version") or case_data.get("scale_version") or case_data.get("tool_version") or "").strip().lower()

    if "general" in req_scale or "scientific" in req_scale:
        return "experimental_observational_or_computational", dict(SCALES["GENERAL_SCIENTIFIC_V1"])
    if "robins" in req_scale:
        scale = dict(SCALES["ROBINS_I_V2_2024"])
        if req_ver and "2016" in req_ver:
            scale["version_warning"] = "Legacy ROBINS-I 2016 requested; mapped explicitly while flagging V2 (2024) domain updates."
        return "non_randomized_intervention", scale
    if "rob 2" in req_scale or "rob2" in req_scale:
        return "randomized_controlled_trial", dict(SCALES["ROB2_2019"])
    if "amstar" in req_scale:
        return "systematic_review_or_meta_analysis", dict(SCALES["AMSTAR2_2017"])

    blob = f"{rec.get('title', '')} {rec.get('abstract', '')} {str(rec.get('text', ''))[:2500]}".lower()
    if re.search(r"\b(?:systematic\s+review|meta[- ]analysis|PRISMA)\b", blob, re.I):
        return "systematic_review_or_meta_analysis", dict(SCALES["AMSTAR2_2017"])
    if re.search(r"\b(?:randomi[sz]ed\s+controlled\s+trial|double[- ]blind\s+trial|\bRCT\b|clinical\s+trial\s+registration|NCT\d{8})\b", blob, re.I):
        return "randomized_controlled_trial", dict(SCALES["ROB2_2019"])
    if re.search(r"\b(?:non[- ]randomized\s+intervention|propensity[- ]score|target\s+trial|quasi[- ]experimental|interrupted\s+time\s+series)\b", blob, re.I):
        return "non_randomized_intervention", dict(SCALES["ROBINS_I_V2_2024"])
    return "experimental_observational_or_computational", dict(SCALES["GENERAL_SCIENTIFIC_V1"])


def _extract_anchored_segments(rec: dict[str, Any]) -> list[dict[str, Any]]:
    segs: list[dict[str, Any]] = []
    if rec.get("abstract"):
        segs.append({
            "paragraph_id": f"{rec['record_id']}-ABS",
            "section": "abstract",
            "page_number": 1,
            "text": str(rec["abstract"]).strip(),
            "is_abstract": True,
        })
    if isinstance(rec.get("paragraphs"), list) and rec["paragraphs"]:
        for idx, p in enumerate(rec["paragraphs"], 1):
            if isinstance(p, dict):
                pid = str(p.get("paragraph_id") or p.get("id") or f"{rec['record_id']}-P{idx:02d}")
                txt = str(p.get("text") or p.get("content") or "").strip()
                section = str(p.get("section") or p.get("type") or "body").strip().lower()
                page_no = p.get("page_number")
                is_abs = bool(p.get("is_abstract")) or section in {"abstract", "summary", "resumo", "摘要"} or bool(
                    re.search(r"(?:^|[-_])(?:abs|abstract)(?:$|[-_\d])", pid, re.I)
                )
            else:
                pid = f"{rec['record_id']}-P{idx:02d}"
                txt = str(p).strip()
                section = "body"
                page_no = None
                is_abs = False
            if txt:
                segs.append({
                    "paragraph_id": pid,
                    "section": section,
                    "page_number": page_no,
                    "text": txt,
                    "is_abstract": is_abs,
                })
    elif rec.get("text"):
        segs.extend(segment_text_to_paragraphs(str(rec["text"]), record_id=rec["record_id"]))
    return segs


def appraise_single_study(
    rec: dict[str, Any],
    case_data: dict[str, Any],
    human_overrides: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    segs = _extract_anchored_segments(rec)
    body = [seg for seg in segs if not seg["is_abstract"]]
    tl_status = str(rec.get("text_layer_status") or "").lower()
    explicitly_abstract = (
        rec.get("fulltext_available") is False
        or str(rec.get("view_type", "")).casefold().endswith("abstract_only")
    )

    if tl_status == "empty_or_scanned":
        material_tier = "scanned_or_unparsed_pdf"
        has_fulltext = False
    elif body and not explicitly_abstract:
        material_tier = "fulltext_verified"
        has_fulltext = True
    elif segs:
        material_tier = "abstract_only"
        has_fulltext = False
    else:
        material_tier = "metadata_only"
        has_fulltext = False

    study_type, scale = detect_study_type_and_scale(rec, case_data)
    # Allow custom domain list override from case.json while checking for unknown fields
    domains = list(scale["domains"])
    if isinstance(case_data.get("custom_domains"), list) and case_data["custom_domains"]:
        for idx, cd in enumerate(case_data["custom_domains"], 1):
            if isinstance(cd, dict) and cd.get("dim_id") and cd["dim_id"] not in {d["dim_id"] for d in domains}:
                domains.append({
                    "dim_id": str(cd["dim_id"]),
                    "label": str(cd.get("label") or cd["dim_id"]),
                    "requires_fulltext": bool(cd.get("requires_fulltext", True)),
                    "cues": list(cd.get("cues") or []),
                    "concern_cues": list(cd.get("concern_cues") or []),
                })

    full_text_blob = " ".join(s["text"] for s in segs)
    is_retracted = bool(RETRACTION_CUE_RE.search(f"{rec.get('title','')} {rec.get('abstract','')} {rec.get('status','')}"))
    is_pre = is_preprint(rec)
    causal_overclaim = bool(OBSERVATIONAL_DESIGN_RE.search(full_text_blob) and CAUSAL_OVERCLAIM_RE.search(full_text_blob))
    code_data_disclosed = bool(CODE_DATA_RE.search(full_text_blob)) if has_fulltext else None

    dim_results: list[dict[str, Any]] = []
    for dim in domains:
        if dim["requires_fulltext"] and not has_fulltext:
            dim_results.append({
                "dimension_id": dim["dim_id"],
                "label": dim["label"],
                "bias_rating": "insufficient_evidence",
                "evidence_state": EvidenceState.UNKNOWN.value,
                "evidence": [],
                "rationale": f"Material tier is '{material_tier}'; full-text evidence is absent, so this domain is abstained (never defaulted to low risk).",
            })
            continue

        pos_ev: list[dict[str, Any]] = []
        con_ev: list[dict[str, Any]] = []
        search_segs = body if dim["requires_fulltext"] else segs
        for s in search_segs:
            matched_con = next((pat for pat in dim.get("concern_cues", []) if re.search(pat, s["text"], re.I)), None)
            matched_pos = next((pat for pat in dim.get("cues", []) if re.search(pat, s["text"], re.I)), None)
            if matched_con:
                con_ev.append({
                    "paragraph_id": s["paragraph_id"],
                    "section": s["section"],
                    "page_number": s["page_number"],
                    "quote": extract_sentence_anchor(s["text"], matched_con),
                })
            if matched_pos:
                pos_ev.append({
                    "paragraph_id": s["paragraph_id"],
                    "section": s["section"],
                    "page_number": s["page_number"],
                    "quote": extract_sentence_anchor(s["text"], matched_pos),
                })

        is_transparency_dim = any(k in dim["dim_id"].lower() for k in ("d5", "transparency", "funding", "reported_result"))
        if is_retracted and is_transparency_dim:
            bias = "explicitly_present"
            ev_state = EvidenceState.OUTDATED.value
            rationale = "Retraction, withdrawal, or erratum marker detected in source record."
        elif con_ev and not pos_ev:
            bias = "explicitly_present"
            ev_state = EvidenceState.REFUTED.value
            rationale = "Explicit methodological limitation or bias indicator anchored in verbatim sentence quote(s)."
        elif con_ev and pos_ev:
            bias = "plausible_concern"
            ev_state = EvidenceState.PARTIAL.value
            rationale = "Both methodological safeguards and explicit risk/limitation cues were found; inspect opposing quotes."
        elif pos_ev:
            bias = "no_indication_observed"
            ev_state = EvidenceState.SUPPORTED.value
            rationale = "Methodological reporting cues grounded in sentence-level quote(s); no explicit bias cue observed in supplied text."
        else:
            bias = "insufficient_evidence"
            ev_state = EvidenceState.UNKNOWN.value
            rationale = "Domain checkpoint not reported in available text; marked insufficient_evidence (not low risk)."

        if causal_overclaim and ("design" in dim["dim_id"].lower() or "confounding" in dim["dim_id"].lower()):
            if bias == "no_indication_observed":
                bias = "plausible_concern"
                ev_state = EvidenceState.PARTIAL.value
            rationale += " Warning: observational design appears paired with causal wording."

        dim_results.append({
            "dimension_id": dim["dim_id"],
            "label": dim["label"],
            "bias_rating": bias,
            "evidence_state": ev_state,
            "evidence": (con_ev + pos_ev)[:3],
            "rationale": rationale,
        })

    # Reconcile optional human reviewer override while preserving machine-vs-human audit delta
    override = human_overrides.get(rec["record_id"])
    human_delta: list[dict[str, Any]] = []
    if isinstance(override, dict) and isinstance(override.get("domains"), dict):
        for d in dim_results:
            ov = override["domains"].get(d["dimension_id"])
            if isinstance(ov, dict) and ov.get("bias_rating") and ov["bias_rating"] != d["bias_rating"]:
                human_delta.append({
                    "dimension_id": d["dimension_id"],
                    "automated_rating": d["bias_rating"],
                    "human_rating": ov["bias_rating"],
                    "human_rationale": ov.get("rationale", ""),
                })

    states = [d["evidence_state"] for d in dim_results]
    n_dims = len(dim_results)
    n_unk = states.count(EvidenceState.UNKNOWN.value)
    n_ref = states.count(EvidenceState.REFUTED.value)
    n_par = states.count(EvidenceState.PARTIAL.value)

    if EvidenceState.OUTDATED.value in states:
        overall = "high_risk_or_outdated"
        tier_rule = "Rule T1: >=1 domain OUTDATED (retraction/erratum) -> high_risk_or_outdated."
    elif material_tier == "scanned_or_unparsed_pdf":
        overall = "unassessable_scanned_pdf"
        tier_rule = "Rule T2: PDF text layer empty/scanned -> unassessable_scanned_pdf (abstained)."
    elif not has_fulltext and n_unk >= max(2, n_dims // 2):
        overall = "incomplete_material_abstract_only"
        tier_rule = f"Rule T3: material_tier={material_tier} with {n_unk}/{n_dims} UNKNOWN domains -> incomplete_material_abstract_only."
    elif n_ref >= 1 or n_par >= 2:
        overall = "moderate_or_high_concern"
        tier_rule = f"Rule T4: {n_ref} explicitly_present/REFUTED and {n_par} plausible_concern/PARTIAL domains -> moderate_or_high_concern."
    elif n_unk >= max(3, (n_dims + 1) // 2):
        overall = "incomplete_reporting"
        tier_rule = f"Rule T5: {n_unk}/{n_dims} domains unreported (insufficient_evidence) despite full text -> incomplete_reporting."
    elif all(s == EvidenceState.SUPPORTED.value for s in states) and n_dims > 0:
        overall = "low_observed_concern"
        tier_rule = f"Rule T6: all {n_dims}/{n_dims} domains grounded (no_indication_observed / SUPPORTED) -> low_observed_concern."
    else:
        overall = "mixed_needs_review"
        tier_rule = f"Rule T7: mixed grounded ({states.count('SUPPORTED')}), partial ({n_par}), and unknown ({n_unk}) domains -> mixed_needs_review."

    return {
        "record_id": rec["record_id"],
        "title": rec.get("title"),
        "doi": rec.get("doi") or None,
        "year": rec.get("year"),
        "source_file": rec.get("source_file"),
        "study_type_detected": study_type,
        "scale_name": scale["scale_name"],
        "scale_version": scale["scale_version"],
        "material_tier": material_tier,
        "fulltext_available": has_fulltext,
        "publication_status": "retracted_or_erratum" if is_retracted else ("preprint_not_peer_reviewed" if is_pre else "standard_or_unspecified"),
        "data_or_code_availability_detected": code_data_disclosed,
        "observational_causal_overclaim_warning": causal_overclaim,
        "overall_quality_tier": overall,
        "tier_derivation_rule": tier_rule,
        "dimensions": dim_results,
        "human_review_delta": human_delta,
    }


def render_markdown_report(table: dict[str, Any]) -> str:
    icon = {
        "no_indication_observed": "[LOW/OK]",
        "plausible_concern": "[CONCERN]",
        "explicitly_present": "[HIGH]",
        "insufficient_evidence": "[UNKNOWN]",
    }
    lines = [
        f"# Literature Study Quality & Risk-of-Bias Report (`{table['case_id']}`)",
        "",
        f"> Scope: {table['scope_note']}",
        "",
        "## 1. Study Overview & Traffic-Light Matrix",
        "",
        "| Record ID | Scale & Version | Material Tier | Overall Tier | Domain Ratings |",
        "| --- | --- | --- | --- | --- |",
    ]
    for a in table["appraisals"]:
        dom_str = " ".join(f"{d['dimension_id'].split('_')[0]}:{icon.get(d['bias_rating'], '[?]')}" for d in a["dimensions"])
        lines.append(
            f"| `{a['record_id']}` | {a['scale_name']} ({a['scale_version']}) | `{a['material_tier']}` | `{a['overall_quality_tier']}` | {dom_str} |"
        )

    lines.extend(["", "## 2. Itemized Evidence-Anchored Appraisals", ""])
    for a in table["appraisals"]:
        lines.append(f"### `{a['record_id']}` — {a['title']}")
        lines.append(
            f"- Scale: **{a['scale_name']} {a['scale_version']}** | Material: `{a['material_tier']}` | "
            f"Status: `{a['publication_status']}` | Code/Data Disclosed: `{a['data_or_code_availability_detected']}`"
        )
        lines.append(f"- Overall Tier: **`{a['overall_quality_tier']}`** ({a['tier_derivation_rule']})")
        for d in a["dimensions"]:
            ev_txt = ""
            if d["evidence"]:
                e0 = d["evidence"][0]
                loc = f"{e0['paragraph_id']}/sec={e0.get('section')}" + (f"/p.{e0['page_number']}" if e0.get("page_number") else "")
                ev_txt = f" — Anchor `{loc}`: \"{e0['quote'][:160]}\""
            lines.append(
                f"  - `{d['dimension_id']}` ({d['label']}): **{d['bias_rating']}** (`{d['evidence_state']}`) — {d['rationale']}{ev_txt}"
            )
        lines.append("")
    return "\n".join(lines)


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="bace1c46-quality-evaluator")

    bundle = load_input_bundle(input_dir)
    case_id = bundle["case_id"]
    case_data = bundle["case_data"]

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-bace1c46")
    budget_guard.tick_phase(budget_file, "appraise_study_quality")

    human_overrides = case_data.get("human_review_overrides") if isinstance(case_data.get("human_review_overrides"), dict) else {}
    appraisals = [appraise_single_study(rec, case_data, human_overrides) for rec in bundle["records"]]

    scale_counts = dict(Counter(f"{a['scale_name']} ({a['scale_version']})" for a in appraisals))
    tier_counts = dict(Counter(a["overall_quality_tier"] for a in appraisals))

    tier_rank_order = {
        "low_observed_concern": 1,
        "mixed_needs_review": 2,
        "incomplete_reporting": 3,
        "incomplete_material_abstract_only": 4,
        "unassessable_scanned_pdf": 5,
        "moderate_or_high_concern": 6,
        "high_risk_or_outdated": 7,
    }
    tier_triage_action = {
        "low_observed_concern": "retain_primary_evidence",
        "mixed_needs_review": "retain_with_targeted_domain_verification",
        "incomplete_reporting": "retain_with_reporting_caveat",
        "incomplete_material_abstract_only": "hold_pending_fulltext_retrieval",
        "unassessable_scanned_pdf": "hold_pending_ocr_or_clean_pdf",
        "moderate_or_high_concern": "downgrade_or_sensitivity_analysis",
        "high_risk_or_outdated": "exclude_or_flag_retracted_outdated",
    }
    ranked_candidates = sorted(
        appraisals,
        key=lambda a: (
            tier_rank_order.get(a["overall_quality_tier"], 99),
            -sum(1 for d in a["dimensions"] if d["evidence_state"] == "SUPPORTED"),
            a["record_id"],
        ),
    )
    ranked_evidence_candidates = [
        {
            "rank": idx,
            "record_id": a["record_id"],
            "title": a["title"],
            "scale": f"{a['scale_name']} ({a['scale_version']})",
            "material_tier": a["material_tier"],
            "overall_quality_tier": a["overall_quality_tier"],
            "recommended_triage": tier_triage_action.get(a["overall_quality_tier"], "manual_review"),
            "grounded_domains": sum(1 for d in a["dimensions"] if d["evidence_state"] == "SUPPORTED"),
            "concern_domains": sum(1 for d in a["dimensions"] if d["evidence_state"] in {"PARTIAL", "REFUTED", "OUTDATED"}),
            "unknown_domains": sum(1 for d in a["dimensions"] if d["evidence_state"] == "UNKNOWN"),
            "tier_derivation_rule": a["tier_derivation_rule"],
        }
        for idx, a in enumerate(ranked_candidates, start=1)
    ]

    table = {
        "case_id": case_id,
        "scope_note": "Structured, version-pinned dimension appraisal over supplied local records only; abstract-only and scanned inputs abstain (UNKNOWN) on fulltext-only criteria rather than defaulting to low risk.",
        "scales_applied": scale_counts,
        "overall_tier_distribution": tier_counts,
        "ranked_evidence_candidates": ranked_evidence_candidates,
        "appraisals": appraisals,
        "ingest_diagnostics": bundle.get("ingest_diagnostics", []),
    }
    table_path = output_dir / "quality_appraisal_table.json"
    result_guard.atomic_write_json(table_path, table)
    md_path = output_dir / "quality_appraisal_report.md"
    md_path.write_text(render_markdown_report(table), encoding="utf-8")

    append_audit_entry(
        audit_file,
        phase="quality_appraisal",
        action="quality_table_built",
        actor="study_quality_evaluator.py",
        output={"studies": len(appraisals), "scales": scale_counts, "tiers": tier_counts},
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    abstract_only_ids = [a["record_id"] for a in appraisals if a["material_tier"] == "abstract_only"]
    scanned_ids = [a["record_id"] for a in appraisals if a["material_tier"] == "scanned_or_unparsed_pdf"]
    if abstract_only_ids:
        gaps.append({
            "code": "ABSTRACT_ONLY_DIMENSIONS_UNKNOWN",
            "severity": "info",
            "source": "study_quality_evaluator",
            "message": f"Records {abstract_only_ids[:10]} are abstract-only; fulltext-dependent bias domains marked insufficient_evidence/UNKNOWN.",
        })
    if scanned_ids:
        gaps.append({
            "code": "PDF_TEXT_LAYER_UNAVAILABLE",
            "severity": "warning",
            "source": "study_quality_evaluator",
            "message": f"Records {scanned_ids[:10]} have empty/scanned PDF text layers; abstained cleanly without guessing.",
        })
    if not appraisals:
        gaps.append({
            "code": "NO_STUDIES_TO_APPRAISE",
            "severity": "warning",
            "source": "study_quality_evaluator",
            "message": "No study records found in input snapshot.",
        })

    anchored_dims = sum(1 for a in appraisals for d in a["dimensions"] if d["evidence"])
    non_unknown_dims = sum(1 for a in appraisals for d in a["dimensions"] if d["evidence_state"] != "UNKNOWN")
    unknown_dims = sum(1 for a in appraisals for d in a["dimensions"] if d["evidence_state"] == "UNKNOWN")

    patch = {
        "artifacts": ["result.json", "quality_appraisal_table.json", "quality_appraisal_report.md"],
        "scales_applied": scale_counts,
        "overall_tier_distribution": tier_counts,
        "ranked_evidence_candidates": ranked_evidence_candidates,
        "appraisals": appraisals,
        "metrics": {
            "appraised_study_count": len(appraisals),
            "fulltext_study_count": sum(1 for a in appraisals if a["fulltext_available"]),
            "abstract_only_count": len(abstract_only_ids),
            "scanned_unparsed_count": len(scanned_ids),
            "anchored_dimension_count": anchored_dims,
            "non_unknown_dimension_count": non_unknown_dims,
            "unknown_dimension_count": unknown_dims,
        },
        "metric_provenance": {
            "appraised_study_count": "quality_appraisal_table.json:appraisals",
            "fulltext_study_count": "quality_appraisal_table.json:appraisals[].fulltext_available",
            "abstract_only_count": "quality_appraisal_table.json:appraisals[].material_tier",
            "scanned_unparsed_count": "quality_appraisal_table.json:appraisals[].material_tier",
            "anchored_dimension_count": "quality_appraisal_table.json:appraisals[].dimensions[].evidence",
            "non_unknown_dimension_count": "quality_appraisal_table.json:appraisals[].dimensions[].evidence_state",
            "unknown_dimension_count": "quality_appraisal_table.json:appraisals[].dimensions[].evidence_state",
        },
        "findings": [
            f"Appraised {len(appraisals)} study record(s) with version-pinned scales {scale_counts}; "
            f"fulltext={sum(1 for a in appraisals if a['fulltext_available'])}, abstract_only={len(abstract_only_ids)}, "
            f"anchored_dims={anchored_dims}/{non_unknown_dims}, unknown_abstained_dims={unknown_dims}."
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["quality_appraisal_table.json", "quality_appraisal_report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor bace1c46 Scale-Routed Study Quality Evaluator.")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--work", default="")
    args = parser.parse_args(argv)
    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"])
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
