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
from evidence_state_guard import (
    EvidenceState,
    RETRACTION_CUE_RE,
    align_output_contract,
    extract_sentence_anchor,
    extract_sentence_anchor_detail,
)
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
        "scale_version": "V2 (2024, 6-domain official riskofbias.info order)",
        "applies_to": "non_randomized_intervention",
        "version_migration_note": (
            "Per riskofbias.info ROBINS-I V2 (2024), V2 streamlines ROBINS-I V1 (2016) from 7 to 6 domains "
            "(absorbing standalone deviations from intended interventions into confounding/per-protocol) and "
            "explicitly reorders D2 (classification of interventions) ahead of D3 (selection of participants "
            "into the study). Classic 7-domain ROBINS-I V1 (2016: D2=Selection, D3=Classification, D4=Deviations) "
            "is supported separately via scale='ROBINS-I-V1'."
        ),
        "domains": [
            {
                "dim_id": "ROBINS_V2_D1_confounding",
                "label": "D1: Bias due to confounding (incl. per-protocol/time-varying confounding)",
                "requires_fulltext": True,
                "cues": [r"\b(?:propensity\s+score|multivariable\s+adjust|confound(?:er|ing)|DAG|inverse\s+probability|IPTW|stratified\s+by|混杂因素|倾向评分)\b"],
                "concern_cues": [r"\b(?:unadjusted|residual\s+confounding|no\s+adjustment\s+for\s+confound|unmeasured\s+confounding|未校正混杂)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D2_classification",
                "label": "D2: Bias in classification of interventions (reordered from V1 D3)",
                "requires_fulltext": True,
                "cues": [r"\b(?:intervention\s+definition|exposure\s+ascertainment|registry\s+record|pharmacy\s+dispensing|confirmed\s+dose)\b"],
                "concern_cues": [r"\b(?:misclassification|self[- ]reported\s+exposure|immortal\s+time\s+bias|retrospective\s+recall)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D3_selection",
                "label": "D3: Bias in selection of participants into the study (reordered from V1 D2)",
                "requires_fulltext": True,
                "cues": [r"\b(?:inclusion\s+criteria|consecutive\s+patients|target\s+trial|inception\s+cohort|eligibility\s+criteria|纳入标准)\b"],
                "concern_cues": [r"\b(?:selection\s+bias|convenience\s+sample|survivor\s+bias|volunteer\s+bias|selected\s+after\s+start)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D4_missing_data",
                "label": "D4: Bias due to missing data (renumbered from V1 D5)",
                "requires_fulltext": True,
                "cues": [r"\b(?:missing\s+data|multiple\s+imputation|complete[- ]case|sensitivity\s+analysis|follow[- ]?up\s+rate)\b"],
                "concern_cues": [r"\b(?:high\s+missingness|missing\s+data\s+not\s+explained|excluded\s+due\s+to\s+missing|attrition\s+bias)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D5_measurement",
                "label": "D5: Bias in measurement of outcomes (renumbered from V1 D6)",
                "requires_fulltext": True,
                "cues": [r"\b(?:blinded\s+outcome|objective\s+endpoint|ICD[- ]?10|validated\s+scale|adjudicated)\b"],
                "concern_cues": [r"\b(?:unblinded\s+outcome|subjective\s+self[- ]report|unvalidated\s+outcome|detection\s+bias)\b"],
            },
            {
                "dim_id": "ROBINS_V2_D6_reported_result",
                "label": "D6: Bias in selection of the reported result (renumbered from V1 D7)",
                "requires_fulltext": True,
                "cues": [r"\b(?:pre[- ]?specified|statistical\s+analysis\s+plan|registered\s+protocol|sensitivity\s+analyses|data\s+availab|code\s+availab)\b"],
                "concern_cues": [r"\b(?:post[- ]?hoc|selective\s+reporting|multiple\s+comparisons\s+without|no\s+protocol)\b"],
            },
        ],
    },
    "ROBINS_I_V1_2016": {
        "scale_name": "ROBINS-I",
        "scale_version": "V1 (2016, 7-domain classic order)",
        "applies_to": "non_randomized_intervention",
        "version_migration_note": "Classic 7-domain ROBINS-I (Sterne et al., BMJ 2016: D2=Selection, D3=Classification, D4=Deviations). Superseding 6-domain V2 (2024) is default unless V1/2016 is explicitly requested.",
        "domains": [
            {
                "dim_id": "ROBINS_V1_D1_confounding",
                "label": "D1: Bias due to confounding",
                "requires_fulltext": True,
                "cues": [r"\b(?:propensity\s+score|multivariable\s+adjust|confound(?:er|ing)|DAG|inverse\s+probability|IPTW|stratified\s+by|混杂因素|倾向评分)\b"],
                "concern_cues": [r"\b(?:unadjusted|residual\s+confounding|no\s+adjustment\s+for\s+confound|unmeasured\s+confounding|未校正混杂)\b"],
            },
            {
                "dim_id": "ROBINS_V1_D2_selection",
                "label": "D2: Bias in selection of participants into the study",
                "requires_fulltext": True,
                "cues": [r"\b(?:inclusion\s+criteria|consecutive\s+patients|target\s+trial|inception\s+cohort|eligibility\s+criteria|纳入标准)\b"],
                "concern_cues": [r"\b(?:selection\s+bias|convenience\s+sample|survivor\s+bias|volunteer\s+bias|selected\s+after\s+start)\b"],
            },
            {
                "dim_id": "ROBINS_V1_D3_classification",
                "label": "D3: Bias in classification of interventions",
                "requires_fulltext": True,
                "cues": [r"\b(?:intervention\s+definition|exposure\s+ascertainment|registry\s+record|pharmacy\s+dispensing|confirmed\s+dose)\b"],
                "concern_cues": [r"\b(?:misclassification|self[- ]reported\s+exposure|immortal\s+time\s+bias|retrospective\s+recall)\b"],
            },
            {
                "dim_id": "ROBINS_V1_D4_deviations",
                "label": "D4: Bias due to deviations from intended interventions",
                "requires_fulltext": True,
                "cues": [r"\b(?:adherence|co[- ]?intervention|fidelity|protocol\s+compliance|treatment\s+switching)\b"],
                "concern_cues": [r"\b(?:unbalanced\s+co[- ]?intervention|protocol\s+violation|untracked\s+switching)\b"],
            },
            {
                "dim_id": "ROBINS_V1_D5_missing_data",
                "label": "D5: Bias due to missing data",
                "requires_fulltext": True,
                "cues": [r"\b(?:missing\s+data|multiple\s+imputation|complete[- ]case|sensitivity\s+analysis|follow[- ]?up\s+rate)\b"],
                "concern_cues": [r"\b(?:high\s+missingness|missing\s+data\s+not\s+explained|excluded\s+due\s+to\s+missing|attrition\s+bias)\b"],
            },
            {
                "dim_id": "ROBINS_V1_D6_measurement",
                "label": "D6: Bias in measurement of outcomes",
                "requires_fulltext": True,
                "cues": [r"\b(?:blinded\s+outcome|objective\s+endpoint|ICD[- ]?10|validated\s+scale|adjudicated)\b"],
                "concern_cues": [r"\b(?:unblinded\s+outcome|subjective\s+self[- ]report|unvalidated\s+outcome|detection\s+bias)\b"],
            },
            {
                "dim_id": "ROBINS_V1_D7_reported_result",
                "label": "D7: Bias in selection of the reported result",
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
        if "v1" in req_scale or "2016" in req_scale or (req_ver and ("v1" in req_ver or "2016" in req_ver)):
            return "non_randomized_intervention", dict(SCALES["ROBINS_I_V1_2016"])
        return "non_randomized_intervention", dict(SCALES["ROBINS_I_V2_2024"])
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


NEGATION_GUARD_RE = re.compile(
    r"\b(?:not\s+(?:been\s+)?|no\s+|never\s+|without\s+|failed\s+to\s+|did\s+not\s+|were\s+not\s+|was\s+not\s+|"
    r"unable\s+to\s+|lack(?:ed|ing|s)?\s+(?:of\s+)?|absent\s+|unadjusted|non[- ]randomi[sz]ed|unblinded|not\s+blinded)\b",
    re.I,
)
MODALITY_HEDGE_RE = re.compile(
    r"\b(?:may\s+have|might\s+have|could\s+have|planned\s+to|will\s+be|should\s+be|unclear\s+whether|assumed\s+to|potentially)\b",
    re.I,
)
ADEQUACY_DETAIL_RE = re.compile(
    r"(?:\b(?:n\s*=\s*\d+|\d+(?:\.\d+)?\s*%|p\s*[<=>]\s*0?\.\d+|95%\s*CI|FDR|Bonferroni|Benjamini|IPTW|propensity\s+score\s+match|"
    r"multivariable\s+(?:regression|logistic|Cox|model|adjust)|adjusted\s+for\s+[a-z]+|sealed\s+envelope|central\s+randomi[sz]ation|"
    r"NCT\d{8}|ISRCTN\d+|ChiCTR\d+|PROSPERO|CRD\d+|github\.com|zenodo\.org|doi\.org|multiple\s+imputation|"
    r"complete[- ]case|blinded\s+(?:assessor|adjudicat|outcome)|validated\s+(?:scale|instrument)|calibrated\s+(?:with|against|using)|"
    r"triplicate|biological\s+replicate|IRB|ethics\s+committee|approval\s+no|ARRIVE|CONSORT|PRISMA|inter[- ]rater|kappa)\b)",
    re.I,
)
NON_SUBSTANTIVE_SECTIONS = {"references", "reference", "bibliography", "acknowledgments", "acknowledgements"}


def _evaluate_cue_sentence_4layer(quote: str, matched_pattern: str) -> tuple[str, str]:
    """Apply Layer 2 (Negation/Modality) and Layer 3 (Methodological Adequacy) to a nominated positive sentence."""
    q_low = (quote or "").lower()
    m = re.search(matched_pattern, q_low, re.I)
    prefix = q_low[max(0, (m.start() - 48) if m else 0):(m.end() + 24 if m else 80)]
    if NEGATION_GUARD_RE.search(prefix):
        return "negated_positive_cue", "explicitly_present"
    if MODALITY_HEDGE_RE.search(prefix):
        return "hedged_or_modal_cue", "plausible_concern"
    if ADEQUACY_DETAIL_RE.search(quote or ""):
        return "adequacy_detail_verified", "no_indication_observed"
    return "reporting_present_adequacy_unverified", "plausible_concern"


def appraise_single_study(
    rec: dict[str, Any],
    case_data: dict[str, Any],
    human_overrides: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    segs = _extract_anchored_segments(rec)
    # Exclude bibliography/references sections from substantive methodological appraisal
    substantive_segs = [s for s in segs if s.get("section", "body").lower() not in NON_SUBSTANTIVE_SECTIONS]
    body = [seg for seg in substantive_segs if not seg["is_abstract"]]
    body_chars = sum(len(seg["text"]) for seg in body)
    distinct_body_secs = {seg.get("section", "body") for seg in body}
    page_count = int(rec.get("page_count") or 0)
    is_truncated = bool(rec.get("truncated") or rec.get("partial_text_only"))
    tl_status = str(rec.get("text_layer_status") or "").lower()
    explicitly_abstract = (
        rec.get("fulltext_available") is False
        or str(rec.get("view_type", "")).casefold().endswith("abstract_only")
    )

    # Strict material-tier separation: short/truncated body excerpts MUST be marked 'partial_text', NEVER 'fulltext_verified'
    explicit_fulltext_structured = bool(rec.get("fulltext_available") is True and len(body) >= 3 and body_chars >= 450 and not is_truncated)
    if tl_status == "empty_or_scanned":
        material_tier = "scanned_or_unparsed_pdf"
        has_fulltext = False
    elif explicitly_abstract:
        material_tier = "abstract_only"
        has_fulltext = False
    elif body and not is_truncated and (
        (body_chars >= 1200 and (len(distinct_body_secs) >= 2 or page_count >= 2 or len(body) >= 4))
        or explicit_fulltext_structured
    ):
        material_tier = "fulltext_verified"
        has_fulltext = True
    elif body:
        material_tier = "partial_text"
        has_fulltext = False
    elif substantive_segs:
        material_tier = "abstract_only"
        has_fulltext = False
    else:
        material_tier = "metadata_only"
        has_fulltext = False

    study_type, scale = detect_study_type_and_scale(rec, case_data)
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

    full_text_blob = " ".join(s["text"] for s in substantive_segs)
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
                "gate_trace": {
                    "layer1_reporting_presence": "abstained_non_fulltext_tier",
                    "layer2_negation_modality": "skipped_abstained",
                    "layer3_adequacy_check": "skipped_abstained",
                    "layer4_abstention_triggered": True,
                },
                "evidence": [],
                "rationale": f"Material tier is '{material_tier}'; verified full-text evidence is absent, so this domain is abstained (never defaulted to low risk).",
            })
            continue

        verified_pos_ev: list[dict[str, Any]] = []
        partial_pos_ev: list[dict[str, Any]] = []
        con_ev: list[dict[str, Any]] = []
        gate_notes: list[str] = []
        search_segs = body if dim["requires_fulltext"] else substantive_segs
        for s in search_segs:
            matched_con = next((pat for pat in dim.get("concern_cues", []) if re.search(pat, s["text"], re.I)), None)
            matched_pos = next((pat for pat in dim.get("cues", []) if re.search(pat, s["text"], re.I)), None)
            if matched_con:
                det = extract_sentence_anchor_detail(s["text"], matched_con)
                con_ev.append({
                    "paragraph_id": s["paragraph_id"],
                    "section": s["section"],
                    "page_number": s["page_number"],
                    "sentence_index": det["sentence_index"],
                    "char_span": [det["char_start"], det["char_end"]],
                    "anchor_granularity": det["anchor_granularity"],
                    "quote": det["quote"],
                    "gate_verdict": "explicit_concern_cue",
                })
            if matched_pos:
                det = extract_sentence_anchor_detail(s["text"], matched_pos)
                g_label, g_rating = _evaluate_cue_sentence_4layer(det["quote"], matched_pos)
                gate_notes.append(g_label)
                ev_item = {
                    "paragraph_id": s["paragraph_id"],
                    "section": s["section"],
                    "page_number": s["page_number"],
                    "sentence_index": det["sentence_index"],
                    "char_span": [det["char_start"], det["char_end"]],
                    "anchor_granularity": det["anchor_granularity"],
                    "quote": det["quote"],
                    "gate_verdict": g_label,
                }
                if g_rating == "explicitly_present":
                    con_ev.append(ev_item)
                elif g_rating == "plausible_concern":
                    partial_pos_ev.append(ev_item)
                else:
                    verified_pos_ev.append(ev_item)

        is_transparency_dim = any(k in dim["dim_id"].lower() for k in ("d5", "d6", "transparency", "funding", "reported_result"))
        if is_retracted and is_transparency_dim:
            bias = "explicitly_present"
            ev_state = EvidenceState.OUTDATED.value
            rationale = "Retraction, withdrawal, or erratum marker detected in source record."
        elif con_ev and not verified_pos_ev:
            bias = "explicitly_present"
            ev_state = EvidenceState.REFUTED.value
            rationale = "Explicit methodological limitation, negated safeguard, or bias indicator anchored in verbatim sentence quote(s)."
        elif con_ev and verified_pos_ev:
            bias = "plausible_concern"
            ev_state = EvidenceState.PARTIAL.value
            rationale = "Both verified methodological safeguards and explicit risk/limitation cues were found; inspect opposing quotes."
        elif verified_pos_ev:
            bias = "no_indication_observed"
            ev_state = EvidenceState.SUPPORTED.value
            rationale = "Methodological safeguard verified through 4-layer gate (reporting presence + non-negated + implementation detail) with verbatim sentence anchor(s)."
        elif partial_pos_ev:
            bias = "plausible_concern"
            ev_state = EvidenceState.PARTIAL.value
            rationale = "Reporting cue detected but lacked substantive adequacy detail or carried hedged/modal phrasing; held at plausible_concern rather than false low risk."
        else:
            bias = "insufficient_evidence"
            ev_state = EvidenceState.UNKNOWN.value
            rationale = "Domain checkpoint not reported in substantive text; marked insufficient_evidence (not low risk)."

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
            "gate_trace": {
                "layer1_reporting_presence": bool(con_ev or verified_pos_ev or partial_pos_ev),
                "layer2_negation_modality": "negated_or_hedged_intercepted" if any(x in {"negated_positive_cue", "hedged_or_modal_cue"} for x in gate_notes) else "clear",
                "layer3_adequacy_check": "verified" if verified_pos_ev else ("unverified_bare_mention" if partial_pos_ev else "n/a"),
                "layer4_abstention_triggered": ev_state == EvidenceState.UNKNOWN.value,
            },
            "evidence": (con_ev + verified_pos_ev + partial_pos_ev)[:3],
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
        "pdf_extraction_backend": rec.get("pdf_extractor") or ("text_field_structured" if has_fulltext else "n/a"),
        "text_layer_chars": len(full_text_blob),
        "study_type_detected": study_type,
        "scale_name": scale["scale_name"],
        "scale_version": scale["scale_version"],
        "scale_version_migration_note": scale.get("version_migration_note"),
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
        "| Record ID | Scale & Version | Material Tier | Extractor | Overall Tier | Domain Ratings |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for a in table["appraisals"]:
        dom_str = " ".join(f"{d['dimension_id'].split('_')[0]}:{icon.get(d['bias_rating'], '[?]')}" for d in a["dimensions"])
        lines.append(
            f"| `{a['record_id']}` | {a['scale_name']} ({a['scale_version']}) | `{a['material_tier']}` | `{a.get('pdf_extraction_backend', 'n/a')}` | `{a['overall_quality_tier']}` | {dom_str} |"
        )

    migration_notes = sorted({a["scale_version_migration_note"] for a in table["appraisals"] if a.get("scale_version_migration_note")})
    if migration_notes:
        lines.extend(["", "### Scale Version Governance Notes", ""])
        for note in migration_notes:
            lines.append(f"- {note}")

    if table.get("ranking_policy_note"):
        lines.extend(["", f"**Ranking Policy**: {table['ranking_policy_note']}"])

    lines.extend(["", "## 2. Itemized Evidence-Anchored Appraisals", ""])
    for a in table["appraisals"]:
        lines.append(f"### `{a['record_id']}` — {a['title']}")
        lines.append(
            f"- Scale: **{a['scale_name']} {a['scale_version']}** | Material: `{a['material_tier']}` | "
            f"Extractor: `{a.get('pdf_extraction_backend', 'n/a')}` ({a.get('text_layer_chars', 0)} chars) | "
            f"Status: `{a['publication_status']}` | Code/Data Disclosed: `{a['data_or_code_availability_detected']}`"
        )
        lines.append(f"- Overall Tier: **`{a['overall_quality_tier']}`** ({a['tier_derivation_rule']})")
        for d in a["dimensions"]:
            ev_txt = ""
            if d["evidence"]:
                e0 = d["evidence"][0]
                loc = f"{e0['paragraph_id']}/sec={e0.get('section')}" + (f"/p.{e0['page_number']}" if e0.get("page_number") else "") + (f"/sent={e0.get('sentence_index')}" if e0.get("sentence_index") is not None else "")
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
        "moderate_or_high_concern": 4,
        "incomplete_material_abstract_only": 5,
        "unassessable_scanned_pdf": 6,
        "high_risk_or_outdated": 7,
    }
    tier_triage_action = {
        "low_observed_concern": "retain_primary_evidence",
        "mixed_needs_review": "retain_with_targeted_domain_verification",
        "incomplete_reporting": "retain_with_reporting_caveat",
        "moderate_or_high_concern": "downgrade_or_sensitivity_analysis",
        "incomplete_material_abstract_only": "hold_pending_fulltext_retrieval",
        "unassessable_scanned_pdf": "hold_pending_ocr_or_clean_pdf",
        "high_risk_or_outdated": "exclude_or_flag_retracted_outdated",
    }
    ranking_policy_note = (
        "Appraised full-text studies (tiers 1-4: low_observed_concern -> mixed_needs_review -> "
        "incomplete_reporting -> moderate_or_high_concern, tie-broken by grounded SUPPORTED domain count) "
        "always rank ahead of unappraised/abstained incomplete material (tiers 5-6: abstract_only / scanned PDF, "
        "which are deferred pending full-text retrieval) and retracted/outdated records (tier 7)."
    )
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
            "stable_source_id": a.get("doi") or a.get("source_file") or a["record_id"],
            "doi": a.get("doi"),
            "source_file": a.get("source_file"),
            "title": a["title"],
            "scale": f"{a['scale_name']} ({a['scale_version']})",
            "material_tier": a["material_tier"],
            "comparable_fulltext_appraised": a["material_tier"] == "fulltext_verified",
            "overall_quality_tier": a["overall_quality_tier"],
            "recommended_triage": tier_triage_action.get(a["overall_quality_tier"], "manual_review"),
            "screening_rationale": (
                f"Material={a['material_tier']}; Scale={a['scale_name']} {a['scale_version']}; "
                f"SUPPORTED={sum(1 for d in a['dimensions'] if d['evidence_state'] == 'SUPPORTED')}, "
                f"CONCERN={sum(1 for d in a['dimensions'] if d['evidence_state'] in {'PARTIAL', 'REFUTED', 'OUTDATED'})}, "
                f"UNKNOWN={sum(1 for d in a['dimensions'] if d['evidence_state'] == 'UNKNOWN')}."
            ),
            "locator_anchors": [
                f"{d['evidence'][0]['paragraph_id']}/sec={d['evidence'][0].get('section')}" + (f"/p.{d['evidence'][0]['page_number']}" if d['evidence'][0].get("page_number") else "")
                for d in a["dimensions"] if d.get("evidence")
            ][:5],
            "grounded_domains": sum(1 for d in a["dimensions"] if d["evidence_state"] == "SUPPORTED"),
            "concern_domains": sum(1 for d in a["dimensions"] if d["evidence_state"] in {"PARTIAL", "REFUTED", "OUTDATED"}),
            "unknown_domains": sum(1 for d in a["dimensions"] if d["evidence_state"] == "UNKNOWN"),
            "tier_derivation_rule": a["tier_derivation_rule"],
        }
        for idx, a in enumerate(ranked_candidates, start=1)
    ]

    research_question = str(case_data.get("research_question") or case_data.get("topic") or bundle.get("task_md", "").splitlines()[0] if bundle.get("task_md") else f"Methodological quality & risk-of-bias screening for {case_id}")
    query_and_screening_log = {
        "snapshot_scope": "finite_local_authorized_corpus_only",
        "sources_ingested": [d.get("file") for d in (bundle.get("ingest_diagnostics") or [])],
        "candidates_screened_count": len(appraisals),
        "fulltext_verified_count": sum(1 for a in appraisals if a["material_tier"] == "fulltext_verified"),
        "partial_text_count": sum(1 for a in appraisals if a["material_tier"] == "partial_text"),
        "abstract_only_count": sum(1 for a in appraisals if a["material_tier"] == "abstract_only"),
        "scanned_unparsed_count": sum(1 for a in appraisals if a["material_tier"] == "scanned_or_unparsed_pdf"),
        "scales_routed": scale_counts,
    }
    coverage_and_omission_disclosure = {
        "fulltext_missing_or_incomplete_records": [
            {"record_id": a["record_id"], "material_tier": a["material_tier"], "source_file": a.get("source_file")}
            for a in appraisals if a["material_tier"] != "fulltext_verified"
        ],
        "offline_permission_and_scope_boundary": (
            "Offline appraisal over supplied local snapshot only; paywalled full texts, supplemental appendices, "
            "or trial registry records not included in /input are NOT fetched and their dependent domains remain UNKNOWN."
        ),
        "abstained_domain_total": sum(1 for a in appraisals for d in a["dimensions"] if d["evidence_state"] == "UNKNOWN"),
    }

    table = {
        "case_id": case_id,
        "research_question": research_question,
        "scope_note": "Structured, version-pinned 4-layer gated quality appraisal & candidate triage over supplied local records only; partial/abstract-only/scanned inputs abstain (UNKNOWN) on fulltext-only criteria rather than defaulting to low risk.",
        "query_and_screening_log": query_and_screening_log,
        "ranking_policy_note": ranking_policy_note,
        "scales_applied": scale_counts,
        "overall_tier_distribution": tier_counts,
        "ranked_evidence_candidates": ranked_evidence_candidates,
        "deferred_incomplete_material": [c for c in ranked_evidence_candidates if not c["comparable_fulltext_appraised"]],
        "coverage_and_omission_disclosure": coverage_and_omission_disclosure,
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
        "research_question": research_question,
        "query_and_screening_log": query_and_screening_log,
        "scales_applied": scale_counts,
        "overall_tier_distribution": tier_counts,
        "ranked_evidence_candidates": ranked_evidence_candidates,
        "deferred_incomplete_material": [c for c in ranked_evidence_candidates if not c["comparable_fulltext_appraised"]],
        "coverage_and_omission_disclosure": coverage_and_omission_disclosure,
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
    patch, oc_gaps = align_output_contract(patch, case_data)
    if oc_gaps:
        gaps.extend(oc_gaps)
        patch["gaps"] = gaps
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
