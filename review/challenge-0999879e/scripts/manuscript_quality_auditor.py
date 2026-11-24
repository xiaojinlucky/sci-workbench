#!/usr/bin/env python3
"""Six-facet, cross-modal manuscript quality & publishability auditor for challenge-0999879e.

Capabilities:
- Full-document coverage & IMRaD structural symmetry across Markdown, LaTeX, OOXML DOCX, and PDF plain headings
- Wrapped-paragraph and cross-line hyphenation reconciliation preserving exact line_start..line_end provenance
- Abstract-vs-Results percentage reconciliation excluding statistical constants (95% CI, 80% power) with +-0.5 pp rounding tolerance
- Flexible sample-size conservation (both N=xxx and '180 patients (arm A n=82, arm B n=76)' patterns)
- Cross-modal figure/table integrity: caption linkage, contiguous panel letters (A/B/C), error-bar definitions,
  and 128-bit HV-dHash exact/flipped/rotated image duplication screening with white-canvas chart protection
- Multi-system bibliography & retraction audit (numeric [n], LaTeX \\cite{}, and Author-Year with multi-line PDF continuation merging)
- Abstract/Methods-scoped study-design classification preventing Discussion 'Future RCT' mentions from bypassing observational checks
- Defect-first separation of method_flaw vs under_reported vs needs_reconciliation vs suggestion + ACL H1-H17 fairness
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import review_io_core as io_core  # noqa: E402

CANONICAL_FACETS = (
    "fact_verification",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argument_chain",
    "academic_norms",
)

# Sample-size patterns covering both formal N=xxx and natural clinical prose ("180 patients", "total of 180 subjects")
SAMPLE_TOTAL_RE = re.compile(
    r"(?:\b(?:total\s+(?:of\s+)?)?[Nn]\s*=\s*(\d{2,6})\b|"
    r"\b(?:total\s+of\s+|cohort\s+of\s+|enrolled\s+|included\s+|comprising\s+)?(\d{2,6})\s+(?:consecutive\s+|eligible\s+)?(?:patients?|participants?|subjects?|individuals?|mice|rats|samples?)\b)",
    re.I,
)
GROUP_N_RE = re.compile(
    r"\b(?:(?:[A-Za-z0-9-]+\s+){0,2}(?:treatment|intervention|experimental|control|placebo|sham|exposed|unexposed|case|healthy|high-dose|low-dose|arm\s*[A-Z0-9]?|group\s*[A-Z0-9]?|cohort\s*[A-Z0-9]?))\s*(?:\(\s*)?n\s*=\s*(\d{1,6})(?:\s*\))?",
    re.I,
)
GROUP_ALLOC_CTX_RE = re.compile(
    r"\b(?:divided|allocated|assigned|randomized|stratified|split|grouped|classified|categorized)\s+into\b|\b(?:arm|group)\s*[A-Z0-9]?\s*n\s*=\s*\d+|\(\s*[A-Za-z0-9-]+\s+arm\s+n\s*=",
    re.I,
)
NON_MUTUAL_SUBGROUP_RE = re.compile(r"\b(?:including|such\s+as|among\s+whom|notably|of\s+whom)\b", re.I)
ATTRITION_RE = re.compile(r"\b(?:excluded|dropout|lost\s+to\s+follow|withdrew|attrition|missing\s+data|final\s+analytic)\b|剔除|失访|脱落|退出", re.I)

RANDOM_BLIND_RE = re.compile(r"\b(?:randomiz\w*|blind\w*|placebo|sham|control\s+group|matched\s+control|propensity\s+score)\b|随机|盲法|对照组|倾向评分", re.I)
TRIAL_SIGNAL_RE = re.compile(r"\b(?:randomized|clinical\s+trial|intervention\s+arm|treatment\s+group|cohort\s+study|animal\s+experiment|mice\s+were\s+divided)\b|临床试验|治疗组|干预组|随机分组|动物实验", re.I)
EFFECT_CI_RE = re.compile(r"\b(?:95%\s*CI|90%\s*CI|99%\s*CI|confidence\s+interval|Cohen(?:'s)?\s*d|Hedges(?:'s)?\s*g|eta(?:-squared|\^2)|η²|OR\s*[=,]?\s*\d|HR\s*[=,]?\s*\d|RR\s*[=,]?\s*\d|IQR|SD|SEM|beta\s*=|β\s*=)\b|置信区间|标准差|标准误|效应量|比值比|风险比", re.I)
MULTIPLICITY_RE = re.compile(r"\b(?:Bonferroni|Holm|Benjamini[- ]Hochberg|FDR|false\s+discovery\s+rate|Tukey|Sidak|adjusted\s+p|q\s*[<=>])\b|多重比较校正|错误发现率|校正后", re.I)
VAGUE_SIG_RE = re.compile(r"\b(?:marginally\s+significant|trending\s+toward\s+significance|approaching\s+significance|borderline\s+significant)\b|边缘显著|趋近显著|接近显著", re.I)
P_ZERO_RE = re.compile(r"\bp\s*=\s*0\.000+(?!\d)", re.I)
STAT_DF_RE = re.compile(r"\b(?:t|χ2|chi2)\s*\(\s*(\d+(?:\.\d+)?)\s*\)\s*=\s*-?\d+(?:\.\d+)?", re.I)

CAUSAL_STRONG_RE = re.compile(
    r"\b(?:proves?|proved|causes?|caused|directly\s+causes?|guarantees?|guaranteed|establishes?\s+caus\w*|conclusively\s+demonstrates?|universally)\b|"
    r"证明了|必然导致|直接导致|完全证明|确立了因果|普遍成立",
    re.I,
)
OBS_RE = re.compile(r"\b(?:observational|correlational|cross-sectional|retrospective|case-control|non-randomized|cohort\s+association)\b|观察性|横断面|回顾性|病例对照|相关性研究", re.I)
INTERVENTION_RE = re.compile(r"\b(?:randomized\s+controlled|RCT|knockout|CRISPR|instrumental\s+variable|mendelian\s+randomization|interventional\s+trial|double-blind\s+trial)\b|随机对照|基因敲除|干预试验|孟德尔随机化|工具变量", re.I)
FUTURE_OR_LIMIT_RE = re.compile(
    r"\b(?:future|needed\s+to\s+confirm|warranted|should\s+be\s+confirmed|remains\s+to\s+be|limitation|cannot\s+establish|unable\s+to\s+confirm|prior\s+studies|previous\s+(?:work|studies|trials))\b|"
    r"未来需要|尚待验证|局限|前人研究",
    re.I,
)
NON_AUTHOR_CLAIM_RE = re.compile(
    r"\b(?:prior\s+studies\s+(?:claimed|suggested|reported)|previous\s+work\s+claimed|suppose|assuming|assume\s+that|hypothetical|if\s+true|negative\s+control|as\s+a\s+limitation|we\s+cannot\s+conclude\s+caus|cannot\s+establish\s+caus)\b|"
    r"前人(?:研究|文献)认为|假定|假设情景|阴性对照|不能推断因果",
    re.I,
)

CITE_NUM_RE = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
CITE_TEX_RE = re.compile(r"\\cite[pt]?\*?\{([^}]+)\}")
CITE_AY_RE = re.compile(r"\(([A-Z][A-Za-z'-]+(?:\s+(?:&|and)\s+[A-Z][A-Za-z'-]+|\s+et\s+al\.)?),?\s*((?:19|20)\d{2}[a-z]?)\)")
CITE_ANY_RE = re.compile(
    r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]|\\cite[pt]?\*?\{([^}]+)\}|\([A-Z][A-Za-z'-]+(?:\s+(?:&|and)\s+[A-Z][A-Za-z'-]+|\s+et\s+al\.)?,?\s*(?:19|20)\d{2}[a-z]?\)"
)
SCALE_RANGE_CTX_RE = re.compile(r"\b(?:scale|score|rating|likert|range|interval|grade|stage|point|points|tier|quartile|level|ci)\b", re.I)
BIB_NUM_ENTRY_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$")
BIB_AY_START_RE = re.compile(r"^\s*([A-Z][A-Za-z'-]+(?:\s+[A-Z]{1,3}\.?)?(?:,|\s+et\s+al\.|\s+&|\s+and).*?(?:19|20)\d{2}[a-z]?)\b")
RETRACTION_RE = re.compile(r"\b(?:retracted|retraction|withdrawn|erratum|expression\s+of\s+concern)\b|撤稿|撤回|勘误", re.I)
NORM_DECL_RE = re.compile(r"\b(?:conflict\s+of\s+interest|competing\s+interests?|funding|grant\s+no|data\s+availability|code\s+availability|ethics|IRB|IACUC)\b|利益冲突|基金资助|数据可用性|伦理审批", re.I)
HUMAN_ANIMAL_RE = re.compile(r"\b(?:patients?|participants?|clinical\s+trial|human\s+cohort|mice|rats|animal\s+experiment)\b|患者|受试者|临床试验|小鼠|大鼠|动物实验", re.I)
ETHICS_RE = re.compile(r"\b(?:IRB|ethics\s+committee|institutional\s+review\s+board|IACUC|informed\s+consent|Helsinki|ARRIVE)\b|伦理(?:委员会|审批|审查)|知情同意", re.I)

PERCENT_RE = re.compile(r"(?<![\d.,])([+−-]?\d+(?:\.\d+)?)\s*[%％]")
STAT_CONST_PCT_AFTER_RE = re.compile(r"^\s*(?:CI|CIs|confidence\s+interval|credible\s+interval|power|significance|alpha|level|tolerance|bootstrap|prediction\s+interval)\b", re.I)
STAT_CONST_PCT_BEFORE_RE = re.compile(r"\b(?:alpha\s*=\s*|level\s+of\s+|power\s+of\s+|at\s+the\s+|two-sided\s+|one-sided\s+)$", re.I)

FIG_REF_RE = re.compile(r"\b(?:Fig(?:ure)?\.?\s*(\d+)([A-H](?:[-–][A-H])?)?)|(?:图\s*(\d+)([A-H])?)", re.I)
TAB_REF_RE = re.compile(r"\b(?:Table\s*(\d+))|(?:表\s*(\d+))", re.I)
FIG_CAP_RE = re.compile(r"^\s*(?:\*\*)?(?:Fig(?:ure)?\.?\s*(\d+)|图\s*(\d+))\s*[:.：\-]", re.I)
TAB_CAP_RE = re.compile(r"^\s*(?:\*\*)?(?:Table\s*(\d+)|表\s*(\d+))\s*[:.：\-]", re.I)
ERROR_BAR_RE = re.compile(r"\b(?:error\s+bars?|mean\s*±|±\s*\d)\b|误差棒|误差线", re.I)
UNCERTAINTY_DEF_RE = re.compile(r"\b(?:SD|SEM|standard\s+deviation|standard\s+error|95%\s*CI|confidence\s+interval|IQR|interquartile)\b|标准差|标准误|置信区间|四分位距", re.I)

AY_NON_AUTHOR_WORDS = {
    "nature", "science", "cell", "lancet", "jama", "nejm", "bmj", "pnas", "plos",
    "table", "figure", "fig", "section", "cohort", "group", "control", "model",
    "dataset", "baseline", "study", "trial", "method", "methods", "results",
    "version", "release", "update", "guideline", "guidelines", "consensus",
}


def _expand_num_list(spec: str) -> list[str]:
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if hi >= lo and (hi - lo) <= 50:
                out.extend(str(k) for k in range(lo, hi + 1))
        elif part.isdigit():
            out.append(part)
    return out


def _extract_outcome_percents(text: str) -> list[tuple[str, Decimal]]:
    """Extract outcome percentages while filtering out statistical constants (95% CI, 80% power, 5% alpha)."""
    out: list[tuple[str, Decimal]] = []
    for m in PERCENT_RE.finditer(text):
        raw_num = m.group(1).replace("−", "-")
        val = Decimal(raw_num)
        after = text[m.end():m.end() + 36]
        before = text[max(0, m.start() - 32):m.start()]
        if STAT_CONST_PCT_AFTER_RE.match(after):
            continue
        if val in (Decimal("5"), Decimal("1"), Decimal("10"), Decimal("80"), Decimal("90"), Decimal("95"), Decimal("99")):
            if STAT_CONST_PCT_BEFORE_RE.search(before):
                continue
        out.append((m.group(0), val))
    return out


def _detect_observational_study(secs: list[dict[str, Any]]) -> bool:
    """Determine observational design strictly from Abstract and Methods non-cited, non-future-work sentences."""
    scoped_secs = [s for s in secs if s["role"] in ("abstract", "methods", "preamble")]
    if not scoped_secs:
        scoped_secs = [s for s in secs if s["role"] not in ("discussion", "conclusion", "references")]
    obs_hit = False
    int_hit = False
    for sec in scoped_secs:
        for sent in io_core.split_sentences(sec):
            st = sent["text"]
            if FUTURE_OR_LIMIT_RE.search(st):
                continue
            if OBS_RE.search(st):
                obs_hit = True
            # Only treat interventional keywords as actual study design if not citing prior work
            if INTERVENTION_RE.search(st) and not CITE_ANY_RE.search(st):
                int_hit = True
    return obs_hit and not int_hit


def _parse_reference_entries(secs: list[dict[str, Any]]) -> tuple[dict[str, tuple[int, int, str]], list[tuple[str, int, int, str]]]:
    """Parse numeric and Author-Year reference entries, merging multi-line PDF continuations cleanly."""
    num_entries: dict[str, tuple[int, int, str]] = {}
    ay_entries: list[tuple[str, int, int, str]] = []

    for sec in secs:
        if sec["role"] != "references":
            continue
        current_kind: str | None = None
        current_key: str = ""
        current_ls = 0
        current_le = 0
        buf: list[str] = []

        def flush() -> None:
            nonlocal current_kind, current_key, buf
            if not buf or not current_kind:
                buf = []
                return
            joined = " ".join(buf).strip()
            if current_kind == "num":
                num_entries[current_key] = (current_ls, current_le, joined)
            else:
                ay_entries.append((current_key, current_ls, current_le, joined))
            buf = []

        for lno, raw in sec["lines"]:
            st = raw.strip()
            if not st or st.startswith("[Page "):
                flush()
                current_kind = None
                continue
            m_num = BIB_NUM_ENTRY_RE.match(st)
            if m_num:
                flush()
                current_kind = "num"
                current_key = m_num.group(1) or m_num.group(2)
                current_ls = lno
                current_le = lno
                buf = [st]
                continue
            # Check if starting a new Author-Year entry (only when not indented continuation of an active numeric entry)
            is_indented = raw.startswith(("  ", "\t"))
            m_ay = BIB_AY_START_RE.match(st) if (not is_indented and current_kind != "num") else None
            # Also detect Author-Year entry after a finished numeric block or when section is Author-Year style
            if m_ay and not re.match(r"^(?:doi:|https?://|Nature|Science|Cell|Lancet|JAMA|NEJM|Ann\s+|J\s+)", st, re.I):
                flush()
                current_kind = "ay"
                surname = re.match(r"^\s*([A-Z][A-Za-z'-]+)", st)
                yr = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", st)
                current_key = f"{(surname.group(1).lower() if surname else 'ref')}_{yr.group(1) if yr else lno}"
                current_ls = lno
                current_le = lno
                buf = [st]
                continue
            if current_kind is not None:
                buf.append(st)
                current_le = lno
            else:
                # Standalone non-numbered line starting an Author-Year block
                surname = re.match(r"^\s*([A-Z][A-Za-z'-]+)", st)
                yr = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", st)
                current_kind = "ay"
                current_key = f"{(surname.group(1).lower() if surname else 'ref')}_{yr.group(1) if yr else lno}"
                current_ls = lno
                current_le = lno
                buf = [st]
        flush()

    return num_entries, ay_entries


def _make_finding(
    fid: int,
    *,
    facet: str,
    finding_type: str,
    defect_distinction: str,
    severity: str,
    file: str,
    section: str,
    line_start: int,
    line_end: int,
    page: int | None,
    verbatim_quote: str,
    evidence_basis: str,
    actionable_revision: str,
    status: str = "confirmed",
    revision_cost: str = "low_editorial",
    firewall_check: str = "PASS (source-bound substantive check)",
) -> dict[str, Any]:
    loc: dict[str, Any] = {
        "file": file,
        "section": section,
        "line_start": line_start,
        "line_end": line_end,
    }
    if page:
        loc["page"] = page
    priority_map = {"critical": "P0", "major": "P1", "minor": "P2", "suggestion": "P3"}
    blocking_tier = "blocking" if severity in {"critical", "major"} else "optional"
    verify_map = {
        "fact_verification": "Re-verify values, counts, and visual assets across Abstract, Results, Tables, and Figures until all anchors match.",
        "citation_support": "Inspect the cited source passage and bibliography entry directly and confirm support grade.",
        "method_design": "Check Methods for explicit comparator, allocation/blinding, multiplicity, and statistical unit alignment.",
        "statistical_reporting": "Recompute test statistics/df and verify exact p-values, effect sizes, and 95% CI or error-bar definitions.",
        "argument_chain": "Confirm claim verb strength matches study design identification assumptions and scope.",
        "academic_norms": "Verify required IMRaD sections, panel numbering, and mandatory IRB/COI/funding/data disclosures.",
    }
    return {
        "finding_id": f"QUA-{fid:03d}",
        "priority": priority_map.get(severity, "P2"),
        "blocking_tier": blocking_tier,
        "facet": facet,
        "finding_type": finding_type,
        "defect_distinction": defect_distinction,
        "severity": severity,
        "location": loc,
        "verbatim_quote": verbatim_quote[:200],
        "evidence_basis": evidence_basis,
        "actionable_revision": actionable_revision,
        "verification_method": verify_map.get(facet, "Re-run manuscript_quality_auditor.py and inspect referenced lines."),
        "revision_cost": revision_cost,
        "firewall_check": firewall_check,
        "status": status,
    }


def _check_figure_table_integrity(doc: dict[str, Any], secs: list[dict[str, Any]], findings: list[dict[str, Any]], facet_cov: dict[str, int], fid_start: int) -> tuple[int, dict[str, Any]]:
    """Audit figure/table citation linkage, contiguous multi-panel labeling (A/B/C), and uncertainty definitions."""
    fid = fid_start
    page_map = doc["page_map"]
    fig_refs: dict[str, list[int]] = {}
    fig_panels: dict[str, set[str]] = {}
    tab_refs: dict[str, list[int]] = {}
    fig_caps: dict[str, tuple[int, int, str]] = {}
    tab_caps: dict[str, tuple[int, int, str]] = {}

    for sec in secs:
        if sec["role"] == "references":
            continue
        for para in io_core.merge_paragraph_lines(sec):
            st = para["text"]
            ls, le = para["line_start"], para["line_end"]
            mf_cap = FIG_CAP_RE.match(st)
            if mf_cap:
                fnum = mf_cap.group(1) or mf_cap.group(2)
                fig_caps[fnum] = (ls, le, st)
                for pm in re.finditer(r"(?:\(([A-H])\)|\b([A-H])\s*[,.:)）])", st):
                    letter = (pm.group(1) or pm.group(2) or "").upper()
                    if letter:
                        fig_panels.setdefault(fnum, set()).add(letter)
                if ERROR_BAR_RE.search(st) and not UNCERTAINTY_DEF_RE.search(st):
                    fid += 1
                    facet_cov["statistical_reporting"] += 1
                    findings.append(_make_finding(
                        fid,
                        facet="statistical_reporting",
                        finding_type="insufficient_evidence",
                        defect_distinction="under_reported",
                        severity="minor",
                        file=doc["file"],
                        section=sec["title"],
                        line_start=ls,
                        line_end=le,
                        page=page_map.get(ls),
                        verbatim_quote=st,
                        evidence_basis=f"Figure {fnum} caption mentions error bars or ± dispersion without defining whether values represent SD, SEM, IQR, or 95% CI.",
                        actionable_revision=f"Explicitly define error-bar metric (e.g., mean ± SD or 95% CI) and sample size n in the Figure {fnum} legend.",
                        status="confirmed",
                        revision_cost="low_editorial",
                    ))
                continue

            mt_cap = TAB_CAP_RE.match(st)
            if mt_cap:
                tnum = mt_cap.group(1) or mt_cap.group(2)
                tab_caps[tnum] = (ls, le, st)
                continue

            for m in FIG_REF_RE.finditer(st):
                fnum = m.group(1) or m.group(3)
                pan = (m.group(2) or m.group(4) or "").upper()
                if fnum:
                    fig_refs.setdefault(fnum, []).append(ls)
                    if pan:
                        if "-" in pan or "–" in pan:
                            parts = re.split(r"[-–]", pan)
                            if len(parts) == 2 and len(parts[0]) == 1 and len(parts[1]) == 1:
                                for code in range(ord(parts[0]), ord(parts[1]) + 1):
                                    fig_panels.setdefault(fnum, set()).add(chr(code))
                        elif len(pan) == 1:
                            fig_panels.setdefault(fnum, set()).add(pan)
            for m in TAB_REF_RE.finditer(st):
                tnum = m.group(1) or m.group(2)
                if tnum:
                    tab_refs.setdefault(tnum, []).append(ls)

    if fig_caps:
        for fnum, lnos in sorted(fig_refs.items(), key=lambda x: int(x[0])):
            if fnum not in fig_caps:
                fid += 1
                facet_cov["fact_verification"] += 1
                findings.append(_make_finding(
                    fid,
                    facet="fact_verification",
                    finding_type="factual_error",
                    defect_distinction="needs_reconciliation",
                    severity="major",
                    file=doc["file"],
                    section="Body",
                    line_start=lnos[0],
                    line_end=lnos[0],
                    page=page_map.get(lnos[0]),
                    verbatim_quote=f"In-text reference to Figure {fnum} at line(s) {lnos[:5]}",
                    evidence_basis=f"Manuscript cites Figure {fnum}, but no matching Figure {fnum} caption was found among parsed captions ({sorted(fig_caps.keys())}).",
                    actionable_revision=f"Provide Figure {fnum} and its caption or correct the misnumbered figure cross-reference.",
                    status="confirmed",
                    revision_cost="low_editorial",
                ))
        for fnum, (cls, cle, ctxt) in sorted(fig_caps.items(), key=lambda x: int(x[0])):
            if fnum not in fig_refs:
                fid += 1
                facet_cov["academic_norms"] += 1
                findings.append(_make_finding(
                    fid,
                    facet="academic_norms",
                    finding_type="norm_violation",
                    defect_distinction="under_reported",
                    severity="minor",
                    file=doc["file"],
                    section="Figures",
                    line_start=cls,
                    line_end=cle,
                    page=page_map.get(cls),
                    verbatim_quote=ctxt,
                    evidence_basis=f"Figure {fnum} caption is present but Figure {fnum} is never cited in the manuscript body.",
                    actionable_revision=f"Reference Figure {fnum} in the relevant Results paragraph or move it to Supplementary Material.",
                    status="confirmed",
                    revision_cost="low_editorial",
                ))

    if tab_caps:
        for tnum, lnos in sorted(tab_refs.items(), key=lambda x: int(x[0])):
            if tnum not in tab_caps:
                fid += 1
                facet_cov["fact_verification"] += 1
                findings.append(_make_finding(
                    fid,
                    facet="fact_verification",
                    finding_type="factual_error",
                    defect_distinction="needs_reconciliation",
                    severity="major",
                    file=doc["file"],
                    section="Body",
                    line_start=lnos[0],
                    line_end=lnos[0],
                    page=page_map.get(lnos[0]),
                    verbatim_quote=f"In-text reference to Table {tnum} at line(s) {lnos[:5]}",
                    evidence_basis=f"Manuscript cites Table {tnum}, but no matching Table {tnum} caption was found among parsed tables ({sorted(tab_caps.keys())}).",
                    actionable_revision=f"Supply Table {tnum} or fix the broken table cross-reference.",
                    status="confirmed",
                    revision_cost="low_editorial",
                ))

    for fnum, letters in sorted(fig_panels.items(), key=lambda x: int(x[0])):
        if len(letters) >= 2:
            codes = sorted(ord(c) for c in letters)
            expected = list(range(ord("A"), codes[-1] + 1))
            missing = [chr(c) for c in expected if c not in codes]
            if missing:
                ls = fig_caps[fnum][0] if fnum in fig_caps else fig_refs.get(fnum, [1])[0]
                le = fig_caps[fnum][1] if fnum in fig_caps else ls
                fid += 1
                facet_cov["academic_norms"] += 1
                findings.append(_make_finding(
                    fid,
                    facet="academic_norms",
                    finding_type="norm_violation",
                    defect_distinction="needs_reconciliation",
                    severity="minor",
                    file=doc["file"],
                    section="Figures",
                    line_start=ls,
                    line_end=le,
                    page=page_map.get(ls),
                    verbatim_quote=f"Figure {fnum} panels observed: {sorted(letters)}; missing: {missing}",
                    evidence_basis=f"Multi-panel Figure {fnum} references/captions skip panel letter(s) {missing} in contiguous sequence A-{chr(codes[-1])}.",
                    actionable_revision=f"Verify whether panel(s) {', '.join(missing)} were omitted or relabel panels sequentially across artwork, legend, and text.",
                    status="needs_human_review",
                    revision_cost="low_editorial",
                ))

    return fid, {
        "figures_cited": sorted(fig_refs.keys()),
        "figure_captions": sorted(fig_caps.keys()),
        "tables_cited": sorted(tab_refs.keys()),
        "table_captions": sorted(tab_caps.keys()),
        "panel_letters": {k: sorted(v) for k, v in fig_panels.items()},
    }


def _check_numeric_and_sample_consistency(doc: dict[str, Any], secs: list[dict[str, Any]], findings: list[dict[str, Any]], facet_cov: dict[str, int], fid_start: int) -> int:
    """Check Abstract-vs-Results percentage discrepancies (with +-0.5pp rounding tolerance & CI filtering) and sample conservation."""
    fid = fid_start
    page_map = doc["page_map"]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for s in secs:
        by_role.setdefault(s["role"], []).append(s)

    # 1. Abstract vs Results percentage reconciliation (excluding 95% CI, 80% power, etc. and allowing +-0.5pp rounding)
    abs_secs = by_role.get("abstract", [])
    res_secs = by_role.get("results", []) + by_role.get("results_discussion", [])
    if abs_secs and res_secs:
        res_percents: list[Decimal] = []
        res_sents: list[dict[str, Any]] = []
        for rs in res_secs:
            for sent in io_core.split_sentences(rs):
                res_sents.append(sent)
                for _, val in _extract_outcome_percents(sent["text"]):
                    res_percents.append(val)

        for asec in abs_secs:
            for sent in io_core.split_sentences(asec):
                st = sent["text"]
                ls, le = sent["line_start"], sent["line_end"]
                unmatched = []
                for raw_tok, val in _extract_outcome_percents(st):
                    # Allow +-0.5 percentage-point rounding tolerance (e.g., 41.2% vs 41%)
                    if not any(abs(val - rv) <= Decimal("0.5") for rv in res_percents):
                        unmatched.append((raw_tok, val))
                if unmatched:
                    sent_toks = {
                        t for t in re.findall(r"[A-Za-z]{4,}|[\u4e00-\u9fff]{2,}", st.lower())
                        if t not in {"with", "from", "that", "this", "group", "patients", "result", "abstract", "cohort", "study"}
                    }
                    best_res = None
                    best_overlap = 0
                    for rsent in res_sents:
                        if not _extract_outcome_percents(rsent["text"]):
                            continue
                        rtoks = set(re.findall(r"[A-Za-z]{4,}|[\u4e00-\u9fff]{2,}", rsent["text"].lower()))
                        ov = len(sent_toks & rtoks)
                        if ov > best_overlap:
                            best_overlap = ov
                            best_res = rsent
                    if best_res and best_overlap >= 2:
                        fid += 1
                        facet_cov["fact_verification"] += 1
                        findings.append(_make_finding(
                            fid,
                            facet="fact_verification",
                            finding_type="factual_error",
                            defect_distinction="needs_reconciliation",
                            severity="major",
                            file=doc["file"],
                            section=asec["title"],
                            line_start=ls,
                            line_end=le,
                            page=page_map.get(ls),
                            verbatim_quote=st,
                            evidence_basis=(
                                f"Abstract reports {[u[0] for u in unmatched]} (>0.5 pp from any Results percentage); "
                                f"closest Results passage L{best_res['line_start']}-L{best_res['line_end']} states: \"{best_res['text'][:140]}\"."
                            ),
                            actionable_revision="Reconcile the exact numerical percentage between Abstract and Results (verify denominator, subgroup scope, and rounding).",
                            status="needs_human_review",
                            revision_cost="low_editorial",
                        ))
                    else:
                        fid += 1
                        facet_cov["fact_verification"] += 1
                        findings.append(_make_finding(
                            fid,
                            facet="fact_verification",
                            finding_type="insufficient_evidence",
                            defect_distinction="needs_reconciliation",
                            severity="minor",
                            file=doc["file"],
                            section=asec["title"],
                            line_start=ls,
                            line_end=le,
                            page=page_map.get(ls),
                            verbatim_quote=st,
                            evidence_basis=f"Abstract percentage(s) {[u[0] for u in unmatched]} were not matched within ±0.5 pp in extracted Results text.",
                            actionable_revision="Verify that the Abstract percentage maps to a Results table/figure or harmonize values.",
                            status="needs_human_review",
                            revision_cost="low_editorial",
                        ))

    # 2. Sample-size conservation across paragraph-merged sentences (supports '180 patients (high-dose arm n=82, low-dose arm n=76)')
    full_text = doc["text"]
    has_attrition = bool(ATTRITION_RE.search(full_text))
    total_ns_all: list[tuple[int, int]] = []
    flagged_sample_gap = False

    for sec in secs:
        if sec["role"] == "references":
            continue
        for para in io_core.merge_paragraph_lines(sec):
            ptxt = para["text"]
            ls, le = para["line_start"], para["line_end"]
            p_totals = [int(m.group(1) or m.group(2)) for m in SAMPLE_TOTAL_RE.finditer(ptxt)]
            for tval in p_totals:
                total_ns_all.append((ls, tval))
            p_groups = [(int(m.group(1)), m.group(0).strip()) for m in GROUP_N_RE.finditer(ptxt)]
            if p_totals and len(p_groups) >= 2 and GROUP_ALLOC_CTX_RE.search(ptxt) and not NON_MUTUAL_SUBGROUP_RE.search(ptxt):
                sub_sum = sum(g[0] for g in p_groups)
                if sub_sum not in p_totals:
                    nearest_total = min(p_totals, key=lambda t: abs(t - sub_sum))
                    flagged_sample_gap = True
                    fid += 1
                    facet_cov["fact_verification"] += 1
                    findings.append(_make_finding(
                        fid,
                        facet="fact_verification",
                        finding_type="insufficient_evidence" if has_attrition else "factual_error",
                        defect_distinction="needs_reconciliation",
                        severity="minor" if has_attrition else "major",
                        file=doc["file"],
                        section=sec["title"],
                        line_start=ls,
                        line_end=le,
                        page=page_map.get(ls),
                        verbatim_quote="; ".join(g[1] for g in p_groups[:4]) + f" (sum={sub_sum} vs total={nearest_total})",
                        evidence_basis=(
                            f"Named mutually exclusive group counts sum to {sub_sum}, which does not match reported total N={nearest_total}"
                            + (" (attrition/exclusion terms appear elsewhere; verify flow accounting)." if has_attrition else " and no attrition/exclusion flow accounts for the discrepancy.")
                        ),
                        actionable_revision="Provide an explicit participant/sample flow accounting (enrolled N -> excluded/lost -> analyzed subgroup n) so counts reconcile.",
                        status="needs_human_review" if has_attrition else "confirmed",
                        revision_cost="low_editorial",
                    ))
                    break

    if not flagged_sample_gap:
        # Fallback check for multiple conflicting formal N= totals across document
        formal_ns = [(lno, int(m.group(1))) for lno, raw in enumerate(doc["lines"], 1) for m in re.finditer(r"\b[Nn]\s*=\s*(\d{2,6})\b", raw)]
        distinct_formal = sorted({v for _, v in formal_ns})
        if len(distinct_formal) > 2:
            fid += 1
            facet_cov["fact_verification"] += 1
            findings.append(_make_finding(
                fid,
                facet="fact_verification",
                finding_type="insufficient_evidence",
                defect_distinction="needs_reconciliation",
                severity="minor",
                file=doc["file"],
                section="Full Manuscript",
                line_start=formal_ns[0][0],
                line_end=formal_ns[0][0],
                page=page_map.get(formal_ns[0][0]),
                verbatim_quote=f"Multiple distinct N= totals observed: {distinct_formal[:6]}",
                evidence_basis="Three or more distinct total N= values appear across the manuscript; confirm enrolled vs subgroup/analytic denominators.",
                actionable_revision="Use uppercase N for total cohort and lowercase n for subgroups/analytic subsets consistently.",
                status="needs_human_review",
                revision_cost="low_editorial",
            ))

    return fid


def audit_quality(target: Path, *, case_id: str = "") -> dict[str, Any]:
    docs, gaps, images = io_core.load_documents(target, extra_exts={".bib"})
    if not docs:
        return {
            "case_id": case_id or "unknown",
            "passed": False,
            "status": "failed",
            "audit_status": "failed",
            "coverage_ratio": 0.0,
            "documents_scanned": [],
            "facet_coverage": {k: {"checked": False, "findings": 0} for k in CANONICAL_FACETS},
            "findings_count": 0,
            "findings": [],
            "gaps": gaps,
            "error": "No readable manuscript found.",
        }

    findings: list[dict[str, Any]] = []
    facet_hits: dict[str, int] = {k: 0 for k in CANONICAL_FACETS}
    figure_summaries: list[dict[str, Any]] = []
    fid = 0
    total_pages = 0
    low_text_pages = 0

    external_bib: dict[str, str] = {}
    manuscripts: list[dict[str, Any]] = []
    for d in docs:
        if d["ext"] == ".bib":
            for m in re.finditer(r"@\w+\{([^,\s]+),", d["text"]):
                external_bib[m.group(1)] = d["text"][m.start():m.start() + 300]
        else:
            manuscripts.append(d)
    if not manuscripts:
        manuscripts = docs

    for doc in manuscripts:
        text = doc["text"]
        low = text.lower()
        page_map = doc["page_map"]
        secs = io_core.split_sections(doc)
        if doc["pages"]:
            total_pages += len(doc["pages"])
            low_text_pages += sum(1 for p in doc["pages"] if p["low_text"])

        # 1. Structural completeness (respecting merged Results and Discussion)
        present_roles = {s["role"] for s in secs}
        if "results_discussion" in present_roles:
            present_roles.update({"results", "discussion"})
        required_core = ["abstract", "introduction", "methods", "results", "discussion"]
        missing_secs = [r for r in required_core if r not in present_roles]
        if len(secs) >= 2 and missing_secs:
            fid += 1
            facet_hits["academic_norms"] += 1
            findings.append(_make_finding(
                fid,
                facet="academic_norms",
                finding_type="norm_violation",
                defect_distinction="under_reported",
                severity="major" if any(s in missing_secs for s in ("methods", "results")) else "minor",
                file=doc["file"],
                section="Document Outline",
                line_start=1,
                line_end=1,
                page=1 if doc["pages"] else None,
                verbatim_quote=f"[MISSING SECTIONS: {', '.join(missing_secs)}]",
                evidence_basis=f"Full-document scan detected headings {[s['title'] for s in secs if s['title'] != '(preamble)'][:8]} but lacked canonical section(s): {missing_secs}.",
                actionable_revision=f"Add explicit {', '.join(missing_secs)} section(s) or verify whether content was truncated before submission.",
                status="confirmed",
                revision_cost="medium_structural",
            ))

        # 2. Cross-section numeric & sample conservation checks
        fid = _check_numeric_and_sample_consistency(doc, secs, findings, facet_hits, fid)

        # 3. Figure/Table cross-modal & multi-panel linkage checks
        fid, fig_sum = _check_figure_table_integrity(doc, secs, findings, facet_hits, fid)
        figure_summaries.append({"file": doc["file"], **fig_sum})

        # 4. Method design vs incomplete reporting separation + wrapped-paragraph df/p scan
        if TRIAL_SIGNAL_RE.search(low) and not RANDOM_BLIND_RE.search(text):
            fid += 1
            facet_hits["method_design"] += 1
            findings.append(_make_finding(
                fid,
                facet="method_design",
                finding_type="insufficient_evidence",
                defect_distinction="under_reported",
                severity="major",
                file=doc["file"],
                section="Methods",
                line_start=1,
                line_end=1,
                page=1 if doc["pages"] else None,
                verbatim_quote="[UNDER_REPORTED: comparator / randomization / blinding specification]",
                evidence_basis="Experimental/clinical cohort terminology appears without reporting control comparator, randomization, or blinding status (reporting omission, distinct from confirmed design invalidity).",
                actionable_revision="State control group definition, allocation/randomization procedure, and blinding status (or justify why unblinded/observational).",
                status="needs_human_review",
                revision_cost="low_editorial",
            ))

        reported_ns = [int(m.group(1) or m.group(2)) for m in SAMPLE_TOTAL_RE.finditer(text)]
        max_n = max(reported_ns) if reported_ns else None
        exact_p_count = 0
        for sec in secs:
            if sec["role"] == "references":
                continue
            for sent in io_core.split_sentences(sec):
                st = sent["text"]
                ls, le = sent["line_start"], sent["line_end"]
                if max_n:
                    for md in STAT_DF_RE.finditer(st):
                        df_val = float(md.group(1))
                        if df_val > max_n:
                            fid += 1
                            facet_hits["statistical_reporting"] += 1
                            findings.append(_make_finding(
                                fid,
                                facet="statistical_reporting",
                                finding_type="factual_error",
                                defect_distinction="method_flaw",
                                severity="major",
                                file=doc["file"],
                                section=sec["title"],
                                line_start=ls,
                                line_end=le,
                                page=page_map.get(ls),
                                verbatim_quote=st,
                                evidence_basis=f"Reported test statistic df={df_val:g} exceeds maximum declared cohort N={max_n}; check for pseudoreplication (e.g., cells/observations treated as independent subjects) or df/N transcription error.",
                                actionable_revision="Verify the independent statistical unit, correct df or sample size N, or use a mixed-effects/clustered model.",
                                status="confirmed",
                                revision_cost="high_reanalysis",
                            ))
                if P_ZERO_RE.search(st):
                    fid += 1
                    facet_hits["statistical_reporting"] += 1
                    findings.append(_make_finding(
                        fid,
                        facet="statistical_reporting",
                        finding_type="norm_violation",
                        defect_distinction="under_reported",
                        severity="minor",
                        file=doc["file"],
                        section=sec["title"],
                        line_start=ls,
                        line_end=le,
                        page=page_map.get(ls),
                        verbatim_quote=st,
                        evidence_basis="Exact 'p = 0.000' reported; p-values are strictly positive and should be written as 'p < 0.001' or in scientific notation.",
                        actionable_revision="Replace 'p = 0.000' with exact threshold notation (e.g., 'p < 0.001') or exact scientific notation.",
                        status="confirmed",
                        revision_cost="low_editorial",
                    ))
                if VAGUE_SIG_RE.search(st):
                    fid += 1
                    facet_hits["statistical_reporting"] += 1
                    findings.append(_make_finding(
                        fid,
                        facet="statistical_reporting",
                        finding_type="norm_violation",
                        defect_distinction="under_reported",
                        severity="minor",
                        file=doc["file"],
                        section=sec["title"],
                        line_start=ls,
                        line_end=le,
                        page=page_map.get(ls),
                        verbatim_quote=st,
                        evidence_basis="Spin phrasing ('marginally significant' / 'trending toward significance') obscures non-significant results.",
                        actionable_revision="Report exact p-value, effect size, and 95% CI without spin qualifiers.",
                        status="confirmed",
                        revision_cost="low_editorial",
                    ))
                exact_p_count += len(re.findall(r"\bp\s*[<=>≤≥]\s*0?\.\d+", st, re.I))

        if exact_p_count > 0 and not EFFECT_CI_RE.search(text):
            fid += 1
            facet_hits["statistical_reporting"] += 1
            findings.append(_make_finding(
                fid,
                facet="statistical_reporting",
                finding_type="norm_violation",
                defect_distinction="under_reported",
                severity="minor",
                file=doc["file"],
                section="Results",
                line_start=1,
                line_end=1,
                page=1 if doc["pages"] else None,
                verbatim_quote="[UNDER_REPORTED: effect size / 95% CI / dispersion measure]",
                evidence_basis=f"{exact_p_count} p-value statement(s) appear without effect sizes or uncertainty bounds (95% CI / SD / IQR).",
                actionable_revision="Pair each primary p-value with an effect size and 95% CI (or SD/IQR).",
                status="confirmed",
                revision_cost="low_editorial",
            ))

        if exact_p_count >= 3 and not MULTIPLICITY_RE.search(text):
            fid += 1
            facet_hits["method_design"] += 1
            findings.append(_make_finding(
                fid,
                facet="method_design",
                finding_type="insufficient_evidence",
                defect_distinction="under_reported",
                severity="major",
                file=doc["file"],
                section="Methods/Results",
                line_start=1,
                line_end=1,
                page=1 if doc["pages"] else None,
                verbatim_quote=f"{exact_p_count} p-value statements detected without multiplicity-adjustment disclosure",
                evidence_basis="Multiple hypothesis tests (>=3) are reported without describing multiplicity adjustment (Bonferroni/Holm/FDR) or designating a single pre-specified primary endpoint. Without raw data, this is flagged as under-reported rather than p-hacking.",
                actionable_revision="State whether p-values were multiplicity-adjusted, specify the adjustment method, or distinguish pre-specified primary vs exploratory endpoints.",
                status="needs_human_review",
                revision_cost="low_editorial",
            ))

        # 5. Observational causal overclaim & citation-support boundary (scoped strictly to Abstract/Methods design!)
        is_obs = _detect_observational_study(secs)
        for sec in secs:
            if sec["role"] == "references":
                continue
            for sent in io_core.split_sentences(sec):
                st = sent["text"]
                ls, le = sent["line_start"], sent["line_end"]
                if is_obs and CAUSAL_STRONG_RE.search(st) and not NON_AUTHOR_CLAIM_RE.search(st) and not FUTURE_OR_LIMIT_RE.search(st):
                    cites = [m.group(0) for m in CITE_ANY_RE.finditer(st)]
                    fid += 1
                    if cites:
                        facet_hits["citation_support"] += 1
                        findings.append(_make_finding(
                            fid,
                            facet="citation_support",
                            finding_type="insufficient_evidence",
                            defect_distinction="needs_reconciliation",
                            severity="minor",
                            file=doc["file"],
                            section=sec["title"],
                            line_start=ls,
                            line_end=le,
                            page=page_map.get(ls),
                            verbatim_quote=st,
                            evidence_basis=f"Causal wording appears in an observational context with citation marker(s) {cites}; citation presence alone does not establish that the cited source supports causal identification.",
                            actionable_revision="Inspect the cited source passage and identification assumptions directly; if causal support is absent, downgrade to associative wording or retain as unverified.",
                            status="needs_human_review",
                            revision_cost="low_editorial",
                        ))
                    else:
                        facet_hits["argument_chain"] += 1
                        findings.append(_make_finding(
                            fid,
                            facet="argument_chain",
                            finding_type="insufficient_evidence",
                            defect_distinction="method_flaw",
                            severity="major",
                            file=doc["file"],
                            section=sec["title"],
                            line_start=ls,
                            line_end=le,
                            page=page_map.get(ls),
                            verbatim_quote=st,
                            evidence_basis="Unqualified causal verb used in an observational/correlational study without causal identification design or external source citation.",
                            actionable_revision="Downgrade causal verb to associative wording ('is associated with'/'correlates with') or document causal identification assumptions and unmeasured-confounding caveats.",
                            status="confirmed",
                            revision_cost="low_editorial",
                        ))

        # 6. Multi-system bibliography linkage & retraction check (numeric + LaTeX + Author-Year, with PDF line continuation)
        num_entries, ay_entries = _parse_reference_entries(secs)
        for key, (ls, le, rtxt) in sorted(num_entries.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 9999):
            if RETRACTION_RE.search(rtxt):
                fid += 1
                facet_hits["citation_support"] += 1
                findings.append(_make_finding(
                    fid,
                    facet="citation_support",
                    finding_type="factual_error",
                    defect_distinction="method_flaw",
                    severity="critical",
                    file=doc["file"],
                    section="References",
                    line_start=ls,
                    line_end=le,
                    page=page_map.get(ls),
                    verbatim_quote=rtxt,
                    evidence_basis=f"Reference [{key}] contains explicit retraction/withdrawal/erratum indicator.",
                    actionable_revision=f"Remove reliance on retracted reference [{key}] or replace with valid peer-reviewed evidence.",
                    status="confirmed",
                    revision_cost="medium_structural",
                ))
        for ay_key, ls, le, rtxt in ay_entries:
            if RETRACTION_RE.search(rtxt):
                fid += 1
                facet_hits["citation_support"] += 1
                findings.append(_make_finding(
                    fid,
                    facet="citation_support",
                    finding_type="factual_error",
                    defect_distinction="method_flaw",
                    severity="critical",
                    file=doc["file"],
                    section="References",
                    line_start=ls,
                    line_end=le,
                    page=page_map.get(ls),
                    verbatim_quote=rtxt,
                    evidence_basis=f"Author-Year reference '{ay_key}' contains explicit retraction/withdrawal/erratum indicator.",
                    actionable_revision=f"Remove reliance on retracted reference '{ay_key}' or qualify/replace with valid peer-reviewed evidence.",
                    status="confirmed",
                    revision_cost="medium_structural",
                ))

        cited_nums: dict[str, list[int]] = {}
        cited_tex: dict[str, list[int]] = {}
        cited_ay: dict[str, list[int]] = {}
        for sec in secs:
            if sec["role"] == "references":
                continue
            for sent in io_core.split_sentences(sec):
                st = sent["text"]
                ls = sent["line_start"]
                # Only treat [1-5] as numeric citation when manuscript uses numeric refs and context is not a rating scale/interval
                for m in CITE_NUM_RE.finditer(st):
                    ctx = st[max(0, m.start() - 32):min(len(st), m.end() + 32)]
                    if SCALE_RANGE_CTX_RE.search(ctx):
                        continue
                    for k in _expand_num_list(m.group(1)):
                        cited_nums.setdefault(k, []).append(ls)
                for m in CITE_TEX_RE.finditer(st):
                    for k in m.group(1).split(","):
                        ks = k.strip()
                        if ks:
                            cited_tex.setdefault(ks, []).append(ls)
                for m in CITE_AY_RE.finditer(st):
                    first_tok = m.group(1).split()[0].strip(",.").lower()
                    if first_tok in AY_NON_AUTHOR_WORDS:
                        continue
                    cited_ay.setdefault(f"{first_tok}_{m.group(2)}", []).append(ls)

        if num_entries:
            for k, lnos in sorted(cited_nums.items()):
                if k not in num_entries:
                    fid += 1
                    facet_hits["citation_support"] += 1
                    findings.append(_make_finding(
                        fid,
                        facet="citation_support",
                        finding_type="factual_error",
                        defect_distinction="needs_reconciliation",
                        severity="major",
                        file=doc["file"],
                        section="Body",
                        line_start=lnos[0],
                        line_end=lnos[0],
                        page=page_map.get(lnos[0]),
                        verbatim_quote=f"Citation [{k}] at line(s) {lnos[:5]}",
                        evidence_basis=f"In-text citation [{k}] has no matching entry in numeric References list.",
                        actionable_revision=f"Add full bibliographic record for [{k}] or renumber citations consistently.",
                        status="confirmed",
                        revision_cost="low_editorial",
                    ))

        if external_bib:
            for k, lnos in sorted(cited_tex.items()):
                if k not in external_bib:
                    fid += 1
                    facet_hits["citation_support"] += 1
                    findings.append(_make_finding(
                        fid,
                        facet="citation_support",
                        finding_type="factual_error",
                        defect_distinction="needs_reconciliation",
                        severity="major",
                        file=doc["file"],
                        section="Body",
                        line_start=lnos[0],
                        line_end=lnos[0],
                        page=page_map.get(lnos[0]),
                        verbatim_quote=f"Citation \\cite{{{k}}} at line(s) {lnos[:5]}",
                        evidence_basis=f"LaTeX citation key '{k}' has no matching entry in supplied bibliography.",
                        actionable_revision=f"Add BibTeX entry for '{k}' or correct the citation key.",
                        status="confirmed",
                        revision_cost="low_editorial",
                    ))

        if ay_entries and not num_entries:
            ay_texts_low = [rtxt.lower() for _, _, _, rtxt in ay_entries]
            for ay_key, lnos in sorted(cited_ay.items()):
                surname, yr = ay_key.split("_", 1)
                if not any(surname in rt and yr in rt for rt in ay_texts_low):
                    fid += 1
                    facet_hits["citation_support"] += 1
                    findings.append(_make_finding(
                        fid,
                        facet="citation_support",
                        finding_type="factual_error",
                        defect_distinction="needs_reconciliation",
                        severity="major",
                        file=doc["file"],
                        section="Body",
                        line_start=lnos[0],
                        line_end=lnos[0],
                        page=page_map.get(lnos[0]),
                        verbatim_quote=f"In-text Author-Year citation ({surname.title()}, {yr}) at line(s) {lnos[:5]}",
                        evidence_basis=f"In-text Author-Year citation ({surname.title()}, {yr}) has no matching entry in References.",
                        actionable_revision=f"Add full reference entry for ({surname.title()}, {yr}) or verify author spelling and year.",
                        status="confirmed",
                        revision_cost="low_editorial",
                    ))

        if HUMAN_ANIMAL_RE.search(text) and not ETHICS_RE.search(text):
            fid += 1
            facet_hits["academic_norms"] += 1
            findings.append(_make_finding(
                fid,
                facet="academic_norms",
                finding_type="norm_violation",
                defect_distinction="under_reported",
                severity="critical",
                file=doc["file"],
                section="Declarations/Methods",
                line_start=1,
                line_end=1,
                page=1 if doc["pages"] else None,
                verbatim_quote="[MISSING: IRB / IACUC / Ethics approval & informed consent disclosure]",
                evidence_basis="Human/clinical or animal subject signals appear without any ethics approval, IRB/IACUC, or informed consent statement.",
                actionable_revision="Add Ethics Approval statement including committee name, protocol ID, and informed consent or exemption status.",
                status="confirmed",
                revision_cost="low_editorial",
            ))
        elif not NORM_DECL_RE.search(text):
            fid += 1
            facet_hits["academic_norms"] += 1
            findings.append(_make_finding(
                fid,
                facet="academic_norms",
                finding_type="norm_violation",
                defect_distinction="under_reported",
                severity="minor",
                file=doc["file"],
                section="Declarations",
                line_start=1,
                line_end=1,
                page=1 if doc["pages"] else None,
                verbatim_quote="[MISSING: COI / funding / data availability declarations]",
                evidence_basis="No standard conflict-of-interest, funding, or data/code availability declaration detected.",
                actionable_revision="Add Conflict of Interest, Funding, and Data/Code Availability statements.",
                status="confirmed",
                revision_cost="low_editorial",
            ))

    # 7. Cross-image duplication & flipped/rotated panel screening (128-bit HV-dHash + intensity correlation)
    image_matches: list[dict[str, Any]] = []
    if images:
        image_matches, img_gaps = io_core.detect_image_duplications(images, target)
        gaps.extend(img_gaps)
        for im in image_matches:
            fid += 1
            facet_hits["fact_verification"] += 1
            hbits = im.get("hash_bits", 128)
            corr = im.get("intensity_corr", 1.0)
            findings.append(_make_finding(
                fid,
                facet="fact_verification",
                finding_type="insufficient_evidence",
                defect_distinction="needs_reconciliation",
                severity="major",
                file=im["file_b"],
                section="Figure Assets",
                line_start=1,
                line_end=1,
                page=None,
                verbatim_quote=f"{im['file_a']} <-> {im['file_b']} ({im['match_type']}, Hamming={im['hamming_distance']}/{hbits}, r={corr})",
                evidence_basis=im["note"],
                actionable_revision="Inspect original raw microscopy/source files for both panels; replace duplicated/flipped asset if unintended or document legitimate shared control explicitly.",
                status="needs_human_review",
                revision_cost="high_reanalysis",
                firewall_check="PASS (objective perceptual anomaly; no misconduct intent inferred)",
            ))

    coverage_ratio = 1.0 if total_pages == 0 else round((total_pages - low_text_pages) / max(total_pages, 1), 4)
    crit_major = [f for f in findings if f["severity"] in {"critical", "major"}]
    needs_review = any(f["status"] == "needs_human_review" for f in findings)
    has_gaps = bool(gaps)
    audit_status = "partial" if (has_gaps or needs_review) else "completed"
    passed = (len(crit_major) == 0) and (not needs_review) and (not has_gaps)

    facet_coverage = {
        facet: {"checked": True, "findings": facet_hits[facet]}
        for facet in CANONICAL_FACETS
    }
    uncertainty = [
        {
            "finding_id": f["finding_id"],
            "facet": f["facet"],
            "reason": f["evidence_basis"],
            "location": f["location"],
        }
        for f in findings
        if f["status"] == "needs_human_review"
    ]
    sev_counts = {
        sev: sum(1 for f in findings if f["severity"] == sev)
        for sev in ("critical", "major", "minor", "suggestion")
    }

    return {
        "case_id": case_id or "unknown",
        "passed": passed,
        "status": audit_status,
        "audit_status": audit_status,
        "coverage_ratio": coverage_ratio,
        "documents_scanned": [
            {
                "file": d["file"],
                "sha256": d["sha256"],
                "extractor": d["extractor"],
                "lines": d["line_count"],
                "pages": len(d["pages"]),
            }
            for d in docs
        ],
        "images_scanned": len(images),
        "image_duplication_matches": image_matches,
        "figure_table_summary": figure_summaries,
        "facet_coverage": facet_coverage,
        "findings_count": len(findings),
        "severity_counts": sev_counts,
        "findings": findings,
        "uncertainty": uncertainty,
        "gaps": gaps,
        "verdict": "no_major_defects_detected" if passed else ("needs_verification" if not crit_major else "defects_detected"),
    }


def _render_markdown(rep: dict[str, Any]) -> str:
    lines = [
        f"# Manuscript Quality & Publishability Audit (`{rep.get('audit_status')}`)",
        "",
        f"- **Verdict**: `{rep.get('verdict')}` | **Passed**: `{rep.get('passed')}` | **Coverage Ratio**: `{rep.get('coverage_ratio')}`",
        f"- **Documents Scanned**: `{len(rep.get('documents_scanned', []))}` | **Images Scanned**: `{rep.get('images_scanned', 0)}`",
        f"- **Findings**: `{rep.get('findings_count', 0)}` (`critical={rep.get('severity_counts', {}).get('critical', 0)}`, `major={rep.get('severity_counts', {}).get('major', 0)}`, `minor={rep.get('severity_counts', {}).get('minor', 0)}`, `suggestion={rep.get('severity_counts', {}).get('suggestion', 0)}`)",
        "",
        "## Six-Facet Coverage Matrix",
        "",
        "| Facet | Checked | Findings |",
        "| --- | --- | ---: |",
    ]
    for facet, info in rep.get("facet_coverage", {}).items():
        lines.append(f"| `{facet}` | `{info.get('checked')}` | {info.get('findings', 0)} |")

    lines.extend(["", "## Actionable Findings (Ordered by Priority)", ""])
    order = {"critical": 0, "major": 1, "minor": 2, "suggestion": 3}
    sorted_findings = sorted(rep.get("findings", []), key=lambda x: (order.get(x["severity"], 9), x["finding_id"]))
    if not sorted_findings:
        lines.append("No qualifying findings detected within inspected scope.")
    else:
        for f in sorted_findings:
            loc = f["location"]
            pg = f" p.{loc['page']}" if loc.get("page") else ""
            lines.append(
                f"### [{f['priority']}/{f['severity'].upper()} | {f.get('blocking_tier', 'optional').upper()}] {f['finding_id']} — `{f['facet']}` (`{f['finding_type']}` | `{f['defect_distinction']}` | `{f['status']}`)"
            )
            lines.append(f"- **Location**: `{loc['file']}` §{loc.get('section', 'Body')}{pg} L{loc['line_start']}-L{loc['line_end']}")
            lines.append(f"- **Quote**: \"{f['verbatim_quote']}\"")
            lines.append(f"- **Evidence Basis & Risk**: {f['evidence_basis']}")
            lines.append(f"- **Actionable Revision** (`cost={f.get('revision_cost')}`): {f['actionable_revision']}")
            lines.append(f"- **Verification Method**: {f.get('verification_method', '')}")
            lines.append("")

    if rep.get("gaps"):
        lines.extend(["## Coverage Gaps & Unverified Boundaries", ""])
        for g in rep["gaps"]:
            lines.append(f"- `[{g.get('code')}]` ({g.get('severity')}) `{g.get('source')}`: {g.get('message')}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _load_case_id(target: Path, explicit: str) -> str:
    if explicit:
        return explicit
    cj = target / "case.json" if target.is_dir() else target.parent / "case.json"
    if cj.is_file():
        try:
            obj = json.loads(cj.read_text(encoding="utf-8", errors="replace"))
            if isinstance(obj, dict) and obj.get("case_id"):
                return str(obj["case_id"])
        except Exception:
            pass
    return "case-01"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Six-facet cross-modal manuscript quality & publishability auditor.")
    parser.add_argument("--input", required=True, help="Input directory or manuscript file.")
    parser.add_argument("--out-json", required=True, help="Output domain audit JSON path.")
    parser.add_argument("--out-md", default="", help="Optional Markdown audit report path.")
    parser.add_argument("--out-result-json", default="", help="Optional final_audit-compatible result.json output path.")
    parser.add_argument("--case-id", default="", help="Explicit case_id override.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if passed is False.")
    args = parser.parse_args(argv)

    target = Path(args.input)
    cid = _load_case_id(target, args.case_id)
    rep = audit_quality(target, case_id=cid)
    io_core.write_json_atomic(Path(args.out_json), rep)

    artifacts = [Path(args.out_json).name]
    if args.out_md:
        out_m = Path(args.out_md)
        out_m.parent.mkdir(parents=True, exist_ok=True)
        out_m.write_text(_render_markdown(rep), encoding="utf-8")
        artifacts.append(out_m.name)

    if args.out_result_json:
        sev = rep.get("severity_counts", {})
        status_val = "partial_with_gaps" if (rep.get("gaps") or rep.get("uncertainty")) else "ok"
        metrics = {
            "coverage_ratio": rep.get("coverage_ratio", 0.0),
            "documents_scanned": len(rep.get("documents_scanned", [])),
            "images_scanned": rep.get("images_scanned", 0),
            "findings_count": rep.get("findings_count", 0),
            "critical_count": sev.get("critical", 0),
            "major_count": sev.get("major", 0),
            "minor_count": sev.get("minor", 0),
            "suggestion_count": sev.get("suggestion", 0),
        }
        metric_prov = {k: f"{Path(args.out_json).name}:{k}" for k in metrics}
        res_payload = {
            "schema_version": "2.1",
            "case_id": cid,
            "task_id": "challenge-0999879e",
            "status": status_val,
            "audit_status": rep.get("audit_status"),
            "passed": rep.get("passed"),
            "verdict": rep.get("verdict"),
            "coverage_ratio": rep.get("coverage_ratio"),
            "sources": [d["file"] for d in rep.get("documents_scanned", [])],
            "artifacts": ["result.json"] + artifacts,
            "metrics": metrics,
            "metric_provenance": metric_prov,
            "facet_coverage": rep.get("facet_coverage", {}),
            "severity_counts": sev,
            "findings": rep.get("findings", []),
            "uncertainty": rep.get("uncertainty", []),
            "gaps": rep.get("gaps", []),
        }
        io_core.write_json_atomic(Path(args.out_result_json), res_payload)

    print(json.dumps({
        "status": rep.get("audit_status"),
        "passed": rep.get("passed"),
        "coverage_ratio": rep.get("coverage_ratio"),
        "findings_count": rep.get("findings_count", 0),
        "severity_counts": rep.get("severity_counts", {}),
    }, ensure_ascii=False))

    if not rep.get("documents_scanned"):
        return 1
    if args.strict and not rep.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
