#!/usr/bin/env python3
"""Six-dimension source-grounded structural logic diagnosis and edit-action-card engine.

Target task: paper-structure-optimization (Manuscript Structural and Logical Optimization).
Zero third-party dependencies (Python 3.12+ stdlib + sibling writing_io_and_invariants.py).
Produces:
  - structural_diagnosis.json (full six-dimension audit + edit action cards)
  - editing_action_plan.md    (executable paragraph/sentence-anchored edit plan)
  - result.json               (Harbor/structured output contract with preserved_facts,
                               evidence_map, change_log, sources, gaps, verification)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import writing_io_and_invariants as wio  # noqa: E402

# ---------------------------------------------------------------------------
# Regex & structural patterns (bilingual EN + ZH)
# ---------------------------------------------------------------------------

MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TEX_HEADING_RE = re.compile(r"^\\((?:sub)*section)\*?\{(.+?)\}")
NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+([A-Z\u4e00-\u9fff][^\n]{1,80})$")
SENT_SPLIT_RE = re.compile(
    r"(?<!\bal\.)(?<!\bFig\.)(?<!\bFigs\.)(?<!\bEq\.)(?<!\bTab\.)(?<!\bTbl\.)(?<!\bvs\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bNo\.)(?<!\bDr\.)(?<!\bRef\.)(?<!\bSec\.)(?<=[。！？.!?])\s+",
    re.I,
)

CANONICAL_ROLES: list[tuple[str, tuple[str, ...]]] = [
    ("abstract", ("abstract", "摘要")),
    ("introduction", ("introduction", "background", "引言", "绪论", "背景")),
    ("related_work", ("related work", "literature review", "prior work", "background and related", "相关工作", "文献综述", "研究现状")),
    ("methods", ("method", "materials", "experimental", "methodology", "protocol", "setup", "方法", "实验设计", "材料与方法")),
    ("results", ("result", "finding", "evaluation", "experiment", "结果", "实验结果")),
    ("discussion", ("discussion", "limitation", "讨论", "局限")),
    ("conclusion", ("conclusion", "concluding", "summary", "结论", "总结")),
    ("references", ("reference", "bibliography", "参考文献")),
]

ALLOWED_ACTION_VERBS = {
    "MOVE",
    "MERGE",
    "SPLIT",
    "NARROW_CLAIM",
    "ADD_BRIDGE",
    "REGROUP_BY_DIMENSION",
    "DEFER_TO_AUTHOR",
}

VAGUE_ADVICE_PATTERNS = [
    r"加强逻辑", r"逻辑不够清晰", r"建议优化结构", r"提升流畅度",
    r"\bstrengthen\s+logic\b", r"\bimprove\s+flow\b", r"\bbetter\s+structure\b",
]

# Year-anchored study opener for chronological laundry-list detection
YEAR_STUDY_RE = re.compile(
    r"(?:(?:[A-Z][A-Za-z\-]+(?:\s+et\s+al\.|\s+and\s+[A-Z][A-Za-z\-]+)?\s*\(\s*((?:19|20)\d{2})[a-z]?\s*\))"
    r"|(?:(?:In|By)\s+((?:19|20)\d{2})[, ]+\s*[A-Z][A-Za-z\-]+)"
    r"|(?:((?:19|20)\d{2})\s*年[^。！？\n]{0,20}(?:研究|学者|团队|报道|提出|发现|表明|等)))"
)
SYNTHESIS_CONNECTOR_RE = re.compile(
    r"\b(?:in\s+contrast|whereas|however|unlike|compared\s+(?:with|to)|by\s+comparison|"
    r"reconcil(?:e|ing)|across\s+these\s+studies|collectively|converg(?:e|ing)|diverg(?:e|ing)|"
    r"shared\s+limitation|common\s+bottleneck|under\s+different\s+conditions|two\s+main\s+lines)\b"
    r"|相比之下|然而|与此相反|分歧在于|共同局限|不同之处|按.{1,10}(?:维度|路线|机制)划分|归因于",
    re.I,
)

# Same-metric extraction for contradiction detection
METRIC_VALUE_RE = re.compile(
    r"\b(accuracy|AUC|AUROC|F1(?:-score)?|precision|recall|sensitivity|specificity|RMSE|MAE|R2|R\^2|"
    r"survival\s+rate|response\s+rate|efficiency|selectivity|yield|conversion|mortality|reduction|improvement|error\s+rate|"
    r"准确率|精度|召回率|灵敏度|特异度|效率|选择性|转化率|产率|存活率|有效率|降幅|提升幅度|相关性|相关系数)\b"
    r"[^.;。\n]{0,45}?"
    r"(?:(increas(?:ed|es|ing)|improv(?:ed|es|ing)|higher|outperform(?:ed|s)?|positive|elevat(?:ed|es)|gained|提升|提高|增加|正相关|优于|上升)"
    r"|(decreas(?:ed|es|ing)|reduc(?:ed|es|ing)|lower|declin(?:ed|es)|degrad(?:ed|es)|negative|drop(?:ped|s)?|failed\s+to\s+improve|no\s+significant|下降|降低|减少|负相关|劣于|未显著|无明显变化))?"
    r"[^.;。\n]{0,25}?([+-]?\d+(?:\.\d+)?)\s*(%|pp|percentage\s+points?|fold|ms|s|K|°C)?",
    re.I,
)
POS_DIR_RE = re.compile(r"\b(?:increas|improv|higher|outperform|positive|elevat|gain|better|superior|enhanc)\w*\b|提升|提高|增加|正相关|优于|上升|显著高于", re.I)
NEG_DIR_RE = re.compile(r"\b(?:decreas|reduc|lower|declin|degrad|negative|drop|worse|inferior|failed\s+to|no\s+significant|null|ineffective)\w*\b|下降|降低|减少|负相关|劣于|未显著|无明显|未能改善", re.I)

# Research Question & Contribution extractors
RQ_PATTERNS = [
    re.compile(r"(?:research\s+question|we\s+(?:investigate|ask|examine|address|test)\s+whether|whether\s+[^.?!]{15,180}\?|how\s+[^.?!]{15,180}\?)", re.I),
    re.compile(r"(?:研究问题|本文(?:旨在|试图|探究|验证|回答)|核心问题(?:在于|是))[^。！？\n]{8,160}[。！？?]?"),
]
CONTRIB_PATTERNS = [
    re.compile(r"(?:our\s+(?:main\s+|key\s+)?contributions?\s+(?:are|include)|we\s+(?:propose|introduce|present|develop|demonstrate|design|construct|validate)\s+[^.?!\n]{15,200})", re.I),
    re.compile(r"(?:本文(?:的)?(?:主要)?贡献|我们(?:提出|构建|设计|开发|验证)了)[^。！？\n]{10,180}"),
]

# Inference leap & evidence anchors
LEAP_MARKER_RE = re.compile(
    r"\b(?:therefore|thus|hence|consequently|this\s+proves|this\s+demonstrates\s+that|clearly\s+shows\s+that|"
    r"it\s+follows\s+that|undoubtedly|inevitably)\b|因此|由此可见|这证明了|充分说明|必然表明|显然",
    re.I,
)
EVIDENCE_ANCHOR_RE = re.compile(
    r"(?:\bp\s*[<=>≤≥]\s*0?\.\d+|\b[nN]\s*=\s*\d+|\b(?:Fig(?:ure)?\.?|Table|Tbl\.?|Eq\.?|Section|§)\s*S?\d+|"
    r"\[\d+(?:\s*[-–,]\s*\d+)*\]|\\cite[a-z]*\{[^}]+\}|\([A-Z][A-Za-z\-]+(?:\s+et\s+al\.)?,\s*(?:19|20)\d{2}[a-z]?\)|"
    r"10\.\d{4,9}/|图\s*S?\d+|表\s*S?\d+|\d+(?:\.\d+)?%)",
    re.I,
)
QUANT_CLAIM_RE = re.compile(
    r"(?:\b(?:outperform(?:s|ed)?|surpass(?:es|ed)?|superior\s+to|significant(?:ly)?\s+(?:higher|lower|better|worse|improv|reduc)|"
    r"state-of-the-art|SOTA|dramatically|substantially|doubles?|triples?)\b|"
    r"显著(?:高于|低于|优于|提升|降低)|大幅(?:提升|降低)|领先于|达到最优|翻倍)",
    re.I,
)

# Observational vs Causal & Scope Extrapolation
OBSERVATIONAL_DESIGN_RE = re.compile(
    r"\b(?:observational|retrospective|cross-sectional|correlational|cohort\s+study|case-control|"
    r"association\s+study|spearman|pearson\s+r|non-randomized|uncontrolled|single-center|in\s+vitro|cell\s+line|mouse\s+model)\b"
    r"|观察性|回顾性|横断面|相关性分析|队列研究|病例对照|单中心|体外细胞|小鼠模型|未随机",
    re.I,
)
UNIVERSAL_EXTRAPOLATION_RE = re.compile(
    r"\b(?:universally|in\s+all\s+(?:cases|patients|domains|settings|species)|across\s+all\s+(?:populations|datasets|tasks)|"
    r"completely\s+solves?|definitively\s+eliminates?|guarantees?\s+(?:clinical|general)|ready\s+for\s+(?:immediate\s+)?clinical\s+deployment)\b"
    r"|适用于所有|普遍适用|在一切条件下|彻底解决|完全消除|可直接用于临床|放之四海",
    re.I,
)
LIMITATION_SIGNAL_RE = re.compile(
    r"\b(?:limitation|caveat|constraint|shortcoming|restricted\s+to|scoped\s+to|cannot\s+establish\s+causality|"
    r"further\s+(?:prospective|external|independent)\s+validation|small\s+sample\s+size|single-center)\b"
    r"|局限性?|不足之处|仅适用于|受限于|尚不能确立因果|仍需(?:外部|前瞻性|独立)验证|样本量有限",
    re.I,
)

# Misplaced content checks (e.g., results/speculation in wrong section)
METHOD_IN_RESULTS_RE = re.compile(
    r"\b(?:we\s+configured|hyperparameters?\s+were\s+set|learning\s+rate\s+was|samples?\s+were\s+centrifuged|buffer\s+contained)\b"
    r"|实验参数设置为|离心条件为|缓冲液配方为",
    re.I,
)
BG_IN_RESULTS_RE = re.compile(
    r"\b(?:historically|for\s+decades|in\s+the\s+literature,\s+many\s+studies\s+have\s+explored)\b"
    r"|长期以来，文献中已有大量研究",
    re.I,
)
TRIAL_ERROR_LOG_RE = re.compile(
    r"\b(?:first\s+we\s+tried|initially\s+we\s+attempted.*?(?:but|however)\s+(?:it\s+)?failed|then\s+we\s+tried)\b"
    r"|起初我们尝试.{1,30}但失败|随后我们又试了",
    re.I,
)


def _classify_role(title: str) -> str:
    low = title.lower().strip()
    for role, kws in CANONICAL_ROLES:
        if any(k in low for k in kws):
            return role
    return "other"


def _tokenize(text: str) -> list[str]:
    en = re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    zh_bigrams: list[str] = []
    for chunk in zh_chunks:
        if len(chunk) == 2:
            zh_bigrams.append(chunk)
        else:
            for k in range(len(chunk) - 1):
                zh_bigrams.append(chunk[k : k + 2])
    stop = {
        "the", "and", "that", "this", "with", "from", "for", "are", "was", "were",
        "have", "has", "been", "into", "which", "their", "there", "these", "those",
        "also", "such", "than", "more", "most", "can", "may", "our", "not", "but",
        "using", "used", "study", "paper", "results", "method", "methods", "data",
        "我们", "本文", "研究", "结果", "方法", "表明", "显示", "进行", "通过", "以及",
    }
    return [t for t in en + zh_bigrams if t not in stop]


def _word_ngrams(tokens: list[str], n: int = 3) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _parse_manuscript(text: str) -> list[dict[str, Any]]:
    """Parse manuscript into structured sections and indexed paragraphs with line ranges."""
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    cur_title = "(Preamble)"
    cur_level = 0
    cur_start = 1
    buf: list[tuple[int, str]] = []
    sec_counter = 0

    def flush(end_line: int) -> None:
        nonlocal sec_counter
        non_empty = [x for _, x in buf if x.strip()]
        if not non_empty and cur_title == "(Preamble)":
            return
        sec_counter += 1
        sec_id = f"S{sec_counter}"
        paras: list[dict[str, Any]] = []
        p_buf: list[tuple[int, str]] = []
        p_idx = 0

        def flush_para() -> None:
            nonlocal p_idx
            if not p_buf:
                return
            p_idx += 1
            joined = " ".join(s for _, s in p_buf)
            paras.append({
                "paragraph_id": f"{sec_id}.P{p_idx}",
                "section_id": sec_id,
                "section": cur_title,
                "role": _classify_role(cur_title),
                "start_line": p_buf[0][0],
                "end_line": p_buf[-1][0],
                "line_range": f"L{p_buf[0][0]}-L{p_buf[-1][0]}",
                "text": joined,
                "char_len": len(joined),
            })

        for lno, ltxt in buf:
            st = ltxt.strip()
            if not st:
                flush_para()
                p_buf = []
            else:
                p_buf.append((lno, st))
        flush_para()

        sections.append({
            "section_id": sec_id,
            "title": cur_title,
            "level": cur_level,
            "role": _classify_role(cur_title),
            "start_line": cur_start,
            "end_line": max(cur_start, end_line),
            "paragraph_count": len(paras),
            "char_len": sum(p["char_len"] for p in paras),
            "paragraphs": paras,
        })

    for lineno, raw in enumerate(lines, start=1):
        st = raw.strip()
        m_md = MD_HEADING_RE.match(st)
        m_tex = TEX_HEADING_RE.match(st)
        m_num = NUM_HEADING_RE.match(st) if not m_md and not m_tex and len(st) < 75 and not st.endswith((".", "。")) else None
        if m_md:
            flush(lineno - 1)
            cur_level = len(m_md.group(1))
            cur_title = m_md.group(2).strip()
            cur_start = lineno
            buf = []
        elif m_tex:
            flush(lineno - 1)
            cmd = m_tex.group(1)
            cur_level = 1 + cmd.count("sub")
            cur_title = m_tex.group(2).strip()
            cur_start = lineno
            buf = []
        elif m_num and _classify_role(m_num.group(2)) != "other":
            flush(lineno - 1)
            cur_level = 1 + m_num.group(1).count(".")
            cur_title = f"{m_num.group(1)} {m_num.group(2).strip()}"
            cur_start = lineno
            buf = []
        else:
            buf.append((lineno, raw))
    flush(len(lines))
    return sections


def _make_card(
    *,
    card_num: int,
    dimension: str,
    severity: str,
    action_verb: str,
    section: str,
    paragraph_id: str,
    line_range: str,
    quote_excerpt: str,
    diagnosis: str,
    evidence_basis: str,
    executable_instruction: str,
    target_location: str = "",
    author_decision_required: bool = False,
) -> dict[str, Any]:
    if action_verb not in ALLOWED_ACTION_VERBS:
        raise ValueError(f"Disallowed action verb: {action_verb}")
    return {
        "card_id": f"EDIT-{card_num:03d}",
        "dimension": dimension,
        "severity": severity,
        "action_verb": action_verb,
        "anchor": {
            "section": section,
            "paragraph_id": paragraph_id,
            "line_range": line_range,
            "quote_excerpt": quote_excerpt[:160].strip(),
        },
        "diagnosis": diagnosis,
        "evidence_basis": evidence_basis,
        "executable_instruction": executable_instruction,
        "target_location": target_location or f"{section} ({paragraph_id}, {line_range})",
        "author_decision_required": author_decision_required,
        "author_decision_options": ["Accept", "Partially Accept", "Reject"],
    }


def _extract_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _detect_same_metric_conflicts(all_paras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find paragraphs reporting conflicting directions or widely divergent values for the same metric."""
    metric_mentions: dict[str, list[dict[str, Any]]] = {}
    for p in all_paras:
        if p["role"] in ("references",):
            continue
        for sent in _extract_sentences(p["text"]):
            for m in METRIC_VALUE_RE.finditer(sent):
                metric_raw = m.group(1).lower().strip()
                metric_norm = re.sub(r"\s+", " ", metric_raw)
                val_str = m.group(4)
                unit = (m.group(5) or "").strip()
                pos_hit = bool(m.group(2)) or bool(POS_DIR_RE.search(sent))
                neg_hit = bool(m.group(3)) or bool(NEG_DIR_RE.search(sent))
                direction = "ambiguous"
                if pos_hit and not neg_hit:
                    direction = "positive"
                elif neg_hit and not pos_hit:
                    direction = "negative"
                elif m.group(2):
                    direction = "positive"
                elif m.group(3):
                    direction = "negative"
                try:
                    val_num = float(val_str)
                except ValueError:
                    val_num = None
                has_reconcile = bool(SYNTHESIS_CONNECTOR_RE.search(p["text"]))
                metric_mentions.setdefault(metric_norm, []).append({
                    "metric": metric_norm,
                    "value": val_str,
                    "val_num": val_num,
                    "unit": unit,
                    "direction": direction,
                    "section": p["section"],
                    "paragraph_id": p["paragraph_id"],
                    "line_range": p["line_range"],
                    "sentence": sent[:180],
                    "has_reconcile": has_reconcile,
                })

    conflicts: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for metric, items in metric_mentions.items():
        if len(items) < 2:
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if a["paragraph_id"] == b["paragraph_id"]:
                    continue
                pair_key = (metric, a["paragraph_id"], b["paragraph_id"])
                if pair_key in seen_pairs:
                    continue
                opposite_dir = (
                    a["direction"] in ("positive", "negative")
                    and b["direction"] in ("positive", "negative")
                    and a["direction"] != b["direction"]
                )
                large_pct_gap = (
                    a["unit"] == "%"
                    and b["unit"] == "%"
                    and a["val_num"] is not None
                    and b["val_num"] is not None
                    and abs(a["val_num"] - b["val_num"]) >= 15.0
                )
                if opposite_dir or large_pct_gap:
                    seen_pairs.add(pair_key)
                    conflicts.append({
                        "metric": metric,
                        "conflict_type": "opposite_direction" if opposite_dir else "large_magnitude_divergence",
                        "reconciled_in_text": a["has_reconcile"] or b["has_reconcile"],
                        "entry_a": {k: a[k] for k in ("section", "paragraph_id", "line_range", "value", "unit", "direction", "sentence")},
                        "entry_b": {k: b[k] for k in ("section", "paragraph_id", "line_range", "value", "unit", "direction", "sentence")},
                    })
    return conflicts


def diagnose_manuscript(
    manuscript_path: Path,
    *,
    case_id: str = "case-01",
    task_id: str = "paper-structure-optimization",
) -> dict[str, Any]:
    """Run full six-dimension source-grounded structural diagnosis."""
    read_res = wio.read_document_text(manuscript_path)
    if not read_res["ok"]:
        raise RuntimeError(f"Failed to read manuscript {manuscript_path}: {read_res.get('meta')}")
    raw_text = read_res["text"]
    lines = raw_text.splitlines()
    sections = _parse_manuscript(raw_text)
    all_paras = [p for s in sections for p in s["paragraphs"]]

    by_role: dict[str, list[dict[str, Any]]] = {}
    for s in sections:
        by_role.setdefault(s["role"], []).append(s)

    cards: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    def add_card(**kwargs: Any) -> dict[str, Any]:
        card = _make_card(card_num=len(cards) + 1, **kwargs)
        cards.append(card)
        evidence_map.append({
            "card_id": card["card_id"],
            "dimension": card["dimension"],
            "source_file": manuscript_path.name,
            "section": card["anchor"]["section"],
            "paragraph_id": card["anchor"]["paragraph_id"],
            "line_range": card["anchor"]["line_range"],
            "quote_excerpt": card["anchor"]["quote_excerpt"],
        })
        return card

    # =========================================================================
    # Dimension 1: Research Question & Contribution Cross-Chapter Alignment
    # =========================================================================
    intro_secs = by_role.get("introduction", []) + by_role.get("abstract", [])
    method_secs = by_role.get("methods", [])
    result_secs = by_role.get("results", [])
    concl_secs = by_role.get("conclusion", []) + by_role.get("discussion", [])

    rq_claims: list[dict[str, Any]] = []
    contrib_claims: list[dict[str, Any]] = []
    for sec in intro_secs:
        for p in sec["paragraphs"]:
            for sent in _extract_sentences(p["text"]):
                if any(pat.search(sent) for pat in RQ_PATTERNS):
                    rq_claims.append({"paragraph_id": p["paragraph_id"], "section": p["section"], "line_range": p["line_range"], "text": sent})
                if any(pat.search(sent) for pat in CONTRIB_PATTERNS):
                    contrib_claims.append({"paragraph_id": p["paragraph_id"], "section": p["section"], "line_range": p["line_range"], "text": sent})

    downstream_text = " ".join(p["text"] for s in (method_secs + result_secs + concl_secs) for p in s["paragraphs"])
    downstream_toks = set(_tokenize(downstream_text))
    rq_alignment_trace: list[dict[str, Any]] = []

    for claim in contrib_claims + rq_claims:
        ctoks = set(_tokenize(claim["text"]))
        if len(ctoks) < 3 or not downstream_toks:
            continue
        overlap_ratio = len(ctoks & downstream_toks) / max(1, len(ctoks))
        fulfilled = overlap_ratio >= 0.25
        rq_alignment_trace.append({
            "paragraph_id": claim["paragraph_id"],
            "section": claim["section"],
            "line_range": claim["line_range"],
            "claim_excerpt": claim["text"][:150],
            "downstream_token_coverage": round(overlap_ratio, 3),
            "fulfilled": fulfilled,
        })
        if not fulfilled:
            add_card(
                dimension="D1_rq_contribution_alignment",
                severity="blocking",
                action_verb="NARROW_CLAIM",
                section=claim["section"],
                paragraph_id=claim["paragraph_id"],
                line_range=claim["line_range"],
                quote_excerpt=claim["text"],
                diagnosis=(
                    f"Contribution or research question promised in {claim['section']} has low downstream fulfillment "
                    f"(token coverage={overlap_ratio:.2f} across Methods/Results/Conclusion)."
                ),
                evidence_basis="Cross-chapter lexical/concept closure check between Introduction promises and Methods/Results/Conclusion body.",
                executable_instruction=(
                    "Either add the missing empirical verification in Methods/Results or narrow this contribution/RQ "
                    "statement in Introduction so every promised deliverable maps 1:1 to presented evidence."
                ),
                author_decision_required=True,
            )

    if by_role.get("introduction") and not rq_claims and not contrib_claims:
        s0 = by_role["introduction"][0]
        p0 = s0["paragraphs"][-1] if s0["paragraphs"] else {"paragraph_id": f"{s0['section_id']}.P0", "line_range": f"L{s0['start_line']}-L{s0['end_line']}", "text": s0["title"]}
        add_card(
            dimension="D1_rq_contribution_alignment",
            severity="major",
            action_verb="ADD_BRIDGE",
            section=s0["title"],
            paragraph_id=p0["paragraph_id"],
            line_range=p0["line_range"],
            quote_excerpt=p0["text"],
            diagnosis="Introduction lacks an explicit, falsifiable research question or itemized contribution statement linking problem to Methods/Results.",
            evidence_basis="No explicit RQ/hypothesis or contribution anchor matched in Introduction paragraphs.",
            executable_instruction="Append a concise closing paragraph to Introduction stating the concrete research question, approach, and bounded contributions.",
            author_decision_required=True,
        )

    # Check Intro <-> Conclusion overall conceptual closure
    if by_role.get("introduction") and concl_secs:
        intro_toks = set(_tokenize(" ".join(p["text"] for s in by_role["introduction"] for p in s["paragraphs"])))
        concl_toks = set(_tokenize(" ".join(p["text"] for s in concl_secs for p in s["paragraphs"])))
        if intro_toks and concl_toks:
            closure = len(intro_toks & concl_toks) / max(1, min(len(intro_toks), len(concl_toks)))
            if closure < 0.16:
                s0 = by_role["introduction"][0]
                c0 = concl_secs[0]
                cp0 = c0["paragraphs"][0] if c0["paragraphs"] else {"paragraph_id": f"{c0['section_id']}.P0", "line_range": f"L{c0['start_line']}-L{c0['end_line']}", "text": c0["title"]}
                add_card(
                    dimension="D1_rq_contribution_alignment",
                    severity="blocking",
                    action_verb="ADD_BRIDGE",
                    section=c0["title"],
                    paragraph_id=cp0["paragraph_id"],
                    line_range=cp0["line_range"],
                    quote_excerpt=cp0["text"],
                    diagnosis=f"Low conceptual closure ({closure:.2f}) between Introduction ({s0['title']}) and {c0['title']}; conclusion drifts from posed problem.",
                    evidence_basis=f"Shared domain concept overlap={closure:.2f} (<0.16 threshold) between Intro and Conclusion/Discussion.",
                    executable_instruction=f"Rewrite the opening sentence of {c0['title']} ({cp0['paragraph_id']}) to directly answer the research question posed in {s0['title']}.",
                    author_decision_required=False,
                )

    # =========================================================================
    # Dimension 2: Section Structure, Narrative Order & Chronological Laundry List
    # =========================================================================
    chronological_clusters: list[dict[str, Any]] = []
    for sec in sections:
        if sec["role"] in ("references", "methods"):
            continue
        # Detect consecutive paragraphs ordered chronologically by study year without comparative synthesis
        year_paras: list[tuple[int, dict[str, Any], bool]] = []
        for p in sec["paragraphs"]:
            ym = YEAR_STUDY_RE.search(p["text"])
            if ym:
                yr_str = ym.group(1) or ym.group(2) or ym.group(3)
                if yr_str and yr_str.isdigit():
                    has_synth = bool(SYNTHESIS_CONNECTOR_RE.search(p["text"]))
                    year_paras.append((int(yr_str), p, has_synth))

        # Check within a single paragraph too (multi-sentence chronological list)
        for p in sec["paragraphs"]:
            sents = _extract_sentences(p["text"])
            sent_years: list[int] = []
            for st in sents:
                ym = YEAR_STUDY_RE.search(st)
                if ym:
                    yr_str = ym.group(1) or ym.group(2) or ym.group(3)
                    if yr_str and yr_str.isdigit():
                        sent_years.append(int(yr_str))
            if len(sent_years) >= 3 and sent_years == sorted(sent_years) and not SYNTHESIS_CONNECTOR_RE.search(p["text"]):
                chronological_clusters.append({
                    "section": sec["title"],
                    "scope": "single_paragraph",
                    "paragraph_ids": [p["paragraph_id"]],
                    "years": sent_years,
                })
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="blocking",
                    action_verb="REGROUP_BY_DIMENSION",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis=(
                        f"Chronological study-by-study laundry list detected ({sent_years}) without shared comparative dimension "
                        "or synthesis connecting prior studies to the current research question."
                    ),
                    evidence_basis=f"Detected {len(sent_years)} monotonically ordered year-anchored study summaries ({sent_years}) with 0 comparative synthesis connectors.",
                    executable_instruction=(
                        "Split and regroup these studies by shared comparison axes (problem setting, experimental condition, "
                        "evaluation metric, or mechanism) and add an explicit topic sentence stating the central dispute."
                    ),
                    author_decision_required=False,
                )

        if len(year_paras) >= 3:
            yrs = [x[0] for x in year_paras]
            non_synth = sum(1 for x in year_paras if not x[2])
            is_mono = yrs == sorted(yrs)
            if is_mono and non_synth >= max(2, len(year_paras) - 1):
                pids = [x[1]["paragraph_id"] for x in year_paras]
                lstart = year_paras[0][1]["start_line"]
                lend = year_paras[-1][1]["end_line"]
                chronological_clusters.append({
                    "section": sec["title"],
                    "scope": "multi_paragraph",
                    "paragraph_ids": pids,
                    "years": yrs,
                })
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="blocking",
                    action_verb="REGROUP_BY_DIMENSION",
                    section=sec["title"],
                    paragraph_id=",".join(pids),
                    line_range=f"L{lstart}-L{lend}",
                    quote_excerpt=year_paras[0][1]["text"],
                    diagnosis=(
                        f"{len(year_paras)} consecutive paragraphs ({', '.join(pids)}) in '{sec['title']}' are arranged "
                        f"chronologically by year ({yrs}) as isolated per-paper summaries without comparative synthesis."
                    ),
                    evidence_basis=f"Monotonic year progression {yrs} across {len(year_paras)} paragraphs with {non_synth}/{len(year_paras)} lacking comparative synthesis connectors.",
                    executable_instruction=(
                        f"Regroup paragraphs {', '.join(pids)} into thematic comparison blocks by experimental condition/method axis, "
                        "open each block with an author-owned claim, and reconcile divergent metric outcomes explicitly."
                    ),
                    author_decision_required=False,
                )

    # Check canonical section order inversions (e.g., Results before Methods, Conclusion before Results)
    role_order_rank = {"abstract": 1, "introduction": 2, "related_work": 3, "methods": 4, "results": 5, "discussion": 6, "conclusion": 7, "references": 8}
    seen_ranks: list[tuple[int, dict[str, Any]]] = []
    for s in sections:
        rk = role_order_rank.get(s["role"])
        if rk is None:
            continue
        if seen_ranks and rk < seen_ranks[-1][0] and not (seen_ranks[-1][1]["role"] == "results" and s["role"] == "methods"):
            # Note: Nature/Cell puts Methods after Discussion, so only flag severe inversions like Conclusion before Results or Intro after Methods
            if (s["role"] in ("introduction", "abstract") and seen_ranks[-1][0] >= 4) or (seen_ranks[-1][1]["role"] == "conclusion" and s["role"] in ("methods", "results")):
                p0 = s["paragraphs"][0] if s["paragraphs"] else {"paragraph_id": f"{s['section_id']}.P0", "line_range": f"L{s['start_line']}-L{s['end_line']}", "text": s["title"]}
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="major",
                    action_verb="MOVE",
                    section=s["title"],
                    paragraph_id=p0["paragraph_id"],
                    line_range=p0["line_range"],
                    quote_excerpt=p0["text"],
                    diagnosis=f"Section '{s['title']}' (role={s['role']}) appears after '{seen_ranks[-1][1]['title']}' (role={seen_ranks[-1][1]['role']}), breaking standard argument progression.",
                    evidence_basis=f"Section order trace: '{seen_ranks[-1][1]['title']}' at L{seen_ranks[-1][1]['start_line']} precedes '{s['title']}' at L{s['start_line']}.",
                    executable_instruction=f"MOVE section '{s['title']}' before '{seen_ranks[-1][1]['title']}' (unless journal explicitly mandates Methods-at-end).",
                    target_location=f"Before {seen_ranks[-1][1]['title']} (L{seen_ranks[-1][1]['start_line']})",
                    author_decision_required=False,
                )
        seen_ranks.append((rk, s))

    # Misplaced content inside Results (procedural config details or generic background or trial-and-error log)
    for sec in result_secs:
        for p in sec["paragraphs"]:
            if METHOD_IN_RESULTS_RE.search(p["text"]):
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="major",
                    action_verb="MOVE",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="Procedural setup / parameter configuration detail is embedded inside Results instead of Methods.",
                    evidence_basis="Matched procedural configuration wording inside a Results paragraph.",
                    executable_instruction=f"MOVE procedural setup sentences from {p['paragraph_id']} into Methods, leaving only a concise condition reference in Results.",
                    target_location="Methods section",
                )
            if BG_IN_RESULTS_RE.search(p["text"]):
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="minor",
                    action_verb="MOVE",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="General literature background appears inside Results, diluting empirical focus.",
                    evidence_basis="Matched generic literature history phrase inside Results.",
                    executable_instruction=f"MOVE background framing from {p['paragraph_id']} to Introduction or Discussion.",
                    target_location="Introduction or Discussion",
                )
            if TRIAL_ERROR_LOG_RE.search(p["text"]):
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="major",
                    action_verb="SPLIT",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="Chronological trial-and-error worklog narration ('first we tried... failed') violates press-release argument-first narrative.",
                    evidence_basis="Matched trial-and-error chronological narration inside Results.",
                    executable_instruction=f"SPLIT {p['paragraph_id']}: lead with the core empirical finding and relocate exploratory negative trials to a structured Ablation/Limitations table.",
                )

    # =========================================================================
    # Dimension 3: Argument Chain (Claim -> Evidence -> Warrant) & Conflicts
    # =========================================================================
    same_metric_conflicts = _detect_same_metric_conflicts(all_paras)
    for cf in same_metric_conflicts:
        if not cf["reconciled_in_text"]:
            ea, eb = cf["entry_a"], cf["entry_b"]
            add_card(
                dimension="D3_argument_evidence_chain",
                severity="blocking",
                action_verb="DEFER_TO_AUTHOR",
                section=f"{ea['section']} <-> {eb['section']}",
                paragraph_id=f"{ea['paragraph_id']},{eb['paragraph_id']}",
                line_range=f"{ea['line_range']} & {eb['line_range']}",
                quote_excerpt=f"{ea['sentence'][:75]} || {eb['sentence'][:75]}",
                diagnosis=(
                    f"Unreconciled same-metric contradiction on '{cf['metric']}' ({cf['conflict_type']}): "
                    f"{ea['paragraph_id']} reports {ea['value']}{ea['unit']} ({ea['direction']}) whereas "
                    f"{eb['paragraph_id']} reports {eb['value']}{eb['unit']} ({eb['direction']})."
                ),
                evidence_basis="Cross-paragraph metric-direction/magnitude audit found contradictory outcomes without comparative reconciliation.",
                executable_instruction=(
                    "Insert an explicit warrant paragraph comparing experimental conditions, cohort definitions, or measurement "
                    "protocols that explain why the two reports diverge; author must confirm which condition governs the study claim."
                ),
                author_decision_required=True,
            )

    for sec in sections:
        if sec["role"] in ("abstract", "references"):
            continue
        for p in sec["paragraphs"]:
            sents = _extract_sentences(p["text"])
            # 3a. Reasoning leap: inferential conclusion sentence lacking evidence anchor in paragraph
            for st in sents:
                if LEAP_MARKER_RE.search(st) and not EVIDENCE_ANCHOR_RE.search(p["text"]):
                    add_card(
                        dimension="D3_argument_evidence_chain",
                        severity="blocking",
                        action_verb="ADD_BRIDGE",
                        section=sec["title"],
                        paragraph_id=p["paragraph_id"],
                        line_range=p["line_range"],
                        quote_excerpt=st,
                        diagnosis="Inferential conclusion/warrant ('therefore/thus/因此/证明了') lacks any evidence anchor (data, citation, Fig/Table, or statistic) in the supporting paragraph.",
                        evidence_basis=f"Inference marker matched in {p['paragraph_id']} with 0 numeric/citation/figure anchors across {p['char_len']} chars.",
                        executable_instruction=(
                            f"ADD_BRIDGE in {p['paragraph_id']}: insert the explicit premise (cited source, quantitative result, or Table/Fig reference) "
                            "and intermediate warrant before drawing this conclusion, or downgrade the claim."
                        ),
                        author_decision_required=True,
                    )
                    break
            # 3b. Quantitative/comparative superiority assertion without numeric/citation anchor
            if QUANT_CLAIM_RE.search(p["text"]) and not EVIDENCE_ANCHOR_RE.search(p["text"]):
                add_card(
                    dimension="D3_argument_evidence_chain",
                    severity="major",
                    action_verb="ADD_BRIDGE",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="Quantitative superiority or statistical significance claim has no supporting number, p-value, citation, or Fig/Table pointer.",
                    evidence_basis="Matched comparative/significance assertion with 0 verifiable evidence anchors in paragraph.",
                    executable_instruction=f"Attach exact source metric, sample size, statistical test, or citation to {p['paragraph_id']}, or remove unsubstantiated superlative wording.",
                    author_decision_required=True,
                )

    # =========================================================================
    # Dimension 4: Paragraph Relations & Cross-Section Redundancy
    # =========================================================================
    redundancy_pairs: list[dict[str, Any]] = []
    candidate_paras = [p for p in all_paras if p["role"] != "references" and p["char_len"] >= 70]
    para_tok_cache = [(p, set(_tokenize(p["text"])), _word_ngrams(_tokenize(p["text"]), 3)) for p in candidate_paras]

    for i in range(len(para_tok_cache)):
        pa, ta, nga = para_tok_cache[i]
        if len(ta) < 6:
            continue
        for j in range(i + 1, len(para_tok_cache)):
            pb, tb, ngb = para_tok_cache[j]
            if len(tb) < 6:
                continue
            jacc = len(ta & tb) / max(1, len(ta | tb))
            ng_jacc = len(nga & ngb) / max(1, len(nga | ngb)) if (nga and ngb) else 0.0
            if jacc >= 0.48 or ng_jacc >= 0.30:
                # Distinguish legitimate Abstract summary restatement vs true cross-section duplication
                is_legit_summary = (
                    (pa["role"] == "abstract" and pb["role"] in ("results", "conclusion", "introduction"))
                    or (pb["role"] == "abstract" and pa["role"] in ("results", "conclusion", "introduction"))
                ) and jacc < 0.80
                pair_info = {
                    "para_a": f"{pa['section']} ({pa['paragraph_id']}, {pa['line_range']})",
                    "para_b": f"{pb['section']} ({pb['paragraph_id']}, {pb['line_range']})",
                    "token_jaccard": round(jacc, 3),
                    "trigram_jaccard": round(ng_jacc, 3),
                    "legitimate_abstract_restatement": is_legit_summary,
                }
                redundancy_pairs.append(pair_info)
                if not is_legit_summary:
                    sev = "major" if jacc >= 0.60 or ng_jacc >= 0.40 else "minor"
                    add_card(
                        dimension="D4_paragraph_relations_redundancy",
                        severity=sev,
                        action_verb="MERGE",
                        section=pb["section"],
                        paragraph_id=f"{pa['paragraph_id']} <-> {pb['paragraph_id']}",
                        line_range=f"{pa['line_range']} & {pb['line_range']}",
                        quote_excerpt=pb["text"],
                        diagnosis=(
                            f"Cross-paragraph/cross-section synonymous duplication between {pa['section']} ({pa['paragraph_id']}) "
                            f"and {pb['section']} ({pb['paragraph_id']}) (token Jaccard={jacc:.2f}, 3-gram Jaccard={ng_jacc:.2f})."
                        ),
                        evidence_basis=f"Lexical overlap exceeds redundancy threshold and is not a high-level Abstract restatement.",
                        executable_instruction=(
                            f"MERGE or deduplicate: retain the primary detailed statement in {pa['section']} ({pa['paragraph_id']}) "
                            f"and condense {pb['paragraph_id']} into a single interpretive cross-reference."
                        ),
                        target_location=f"{pa['section']} ({pa['paragraph_id']})",
                    )

    # Flag overly long monolithic paragraphs (>= 900 chars or >= 7 sentences mixing multiple ideas)
    for sec in sections:
        if sec["role"] in ("abstract", "references"):
            continue
        for p in sec["paragraphs"]:
            sents = _extract_sentences(p["text"])
            if p["char_len"] >= 900 or len(sents) >= 8:
                add_card(
                    dimension="D4_paragraph_relations_redundancy",
                    severity="minor",
                    action_verb="SPLIT",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis=f"Overloaded paragraph ({p['char_len']} chars, {len(sents)} sentences) bundles multiple argumentative units without structural separation.",
                    evidence_basis="Paragraph length/sentence-count exceeds single-claim readability threshold.",
                    executable_instruction=f"SPLIT {p['paragraph_id']} at the topic pivot so each paragraph carries one claim + evidence + transition.",
                )

    # =========================================================================
    # Dimension 5: Conclusion Scope & Epistemic Scale
    # =========================================================================
    full_methods_results = " ".join(p["text"] for s in (method_secs + result_secs) for p in s["paragraphs"])
    is_observational_study = bool(OBSERVATIONAL_DESIGN_RE.search(full_methods_results or raw_text))

    for sec in concl_secs + by_role.get("abstract", []) + result_secs:
        for p in sec["paragraphs"]:
            epi = wio.classify_epistemic_strength(p["text"])
            if epi["strong_causal_count"] > 0 and (is_observational_study or epi["hedged_count"] == 0):
                add_card(
                    dimension="D5_conclusion_boundary_epistemic_scale",
                    severity="blocking",
                    action_verb="NARROW_CLAIM",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis=(
                        "Causal overclaim: strong causal verb ('proves/causes/drives/直接导致/证明了因果') used "
                        + ("despite observational/correlational/non-interventional design cues in manuscript." if is_observational_study else "without explicit experimental boundary or epistemic hedge.")
                    ),
                    evidence_basis=f"Epistemic classifier={epi['level']} (strong_causal={epi['strong_causal_count']}, hedged={epi['hedged_count']}, observational_design={is_observational_study}).",
                    executable_instruction=(
                        f"NARROW_CLAIM in {p['paragraph_id']}: replace causal verb with associative/conditional wording "
                        "('is associated with', 'suggests under tested conditions') and bind to the actual study cohort/model."
                    ),
                    author_decision_required=True,
                )
            if UNIVERSAL_EXTRAPOLATION_RE.search(p["text"]):
                add_card(
                    dimension="D5_conclusion_boundary_epistemic_scale",
                    severity="blocking",
                    action_verb="NARROW_CLAIM",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="Out-of-sample universal extrapolation extends findings beyond the tested cohort, dataset, or experimental domain.",
                    evidence_basis="Matched universal generalization phrase ('universally/all patients/适用于所有/彻底解决').",
                    executable_instruction=f"NARROW_CLAIM in {p['paragraph_id']}: explicitly qualify scope to the evaluated dataset, species/cohort, and experimental parameters.",
                    author_decision_required=True,
                )

    # Check missing Limitations in Discussion/Conclusion
    concl_blob = " ".join(p["text"] for s in concl_secs for p in s["paragraphs"])
    if concl_secs and not LIMITATION_SIGNAL_RE.search(concl_blob):
        c_last = concl_secs[-1]
        p_last = c_last["paragraphs"][-1] if c_last["paragraphs"] else {"paragraph_id": f"{c_last['section_id']}.P0", "line_range": f"L{c_last['start_line']}-L{c_last['end_line']}", "text": c_last["title"]}
        add_card(
            dimension="D5_conclusion_boundary_epistemic_scale",
            severity="major",
            action_verb="ADD_BRIDGE",
            section=c_last["title"],
            paragraph_id=p_last["paragraph_id"],
            line_range=p_last["line_range"],
            quote_excerpt=p_last["text"],
            diagnosis="Discussion/Conclusion contains no explicit scope limitations, failure modes, or boundary conditions.",
            evidence_basis="Zero limitation/caveat signals found across Discussion and Conclusion sections.",
            executable_instruction=f"Add a concise, concrete Limitations paragraph in {c_last['title']} specifying sample/dataset bounds, potential confounders, and conditions where conclusions do not hold.",
            author_decision_required=True,
        )

    # =========================================================================
    # Dimension 6: Structural Integrity, Truncation & Missing Elements
    # =========================================================================
    required_core_roles = ["introduction", "methods", "results", "conclusion"]
    missing_roles = [r for r in required_core_roles if r not in by_role and not (r == "conclusion" and "discussion" in by_role)]
    for mr in missing_roles:
        gaps.append({"code": f"MISSING_SECTION_{mr.upper()}", "severity": "major", "detail": f"Canonical section '{mr}' not found in manuscript headings."})
        add_card(
            dimension="D6_structural_integrity_anomalies",
            severity="major",
            action_verb="DEFER_TO_AUTHOR",
            section="[MISSING]",
            paragraph_id="MISSING_SECTION",
            line_range="L0-L0",
            quote_excerpt=f"[MISSING: {mr}]",
            diagnosis=f"Canonical section role '{mr}' is absent from the supplied manuscript.",
            evidence_basis=f"Parsed {len(sections)} section heading(s); none mapped to role '{mr}'.",
            executable_instruction=f"Confirm whether '{mr}' is intentionally omitted by target venue/excerpt scope or supply the missing section before final structural sign-off.",
            author_decision_required=True,
        )

    # Check inline citations vs References section
    invariants = wio.extract_invariants(raw_text)
    total_inline_cites = (
        len(invariants["bracket_citations"])
        + len(invariants["latex_citations"])
        + len(invariants["author_year_citations"])
    )
    if total_inline_cites > 0 and "references" not in by_role:
        gaps.append({"code": "MISSING_REFERENCES_SECTION", "severity": "major", "detail": f"Detected {total_inline_cites} inline citation marker(s) but no References/Bibliography section."})
        add_card(
            dimension="D6_structural_integrity_anomalies",
            severity="major",
            action_verb="DEFER_TO_AUTHOR",
            section="[MISSING]",
            paragraph_id="MISSING_REFERENCES",
            line_range="L0-L0",
            quote_excerpt=f"[MISSING: References for {total_inline_cites} inline citations]",
            diagnosis=f"Manuscript contains {total_inline_cites} inline citation marker(s) but lacks a References/Bibliography section.",
            evidence_basis="Extracted inline citation markers via writing_io_and_invariants without a corresponding References heading.",
            executable_instruction="Attach the complete References section or bibliography file so citation coverage and claim-source grounding can be verified.",
            author_decision_required=True,
        )

    # Check truncation / abrupt ending
    stripped_tail = raw_text.rstrip()
    truncated = False
    trunc_reasons: list[str] = []
    if stripped_tail:
        last_line = lines[-1].strip() if lines else ""
        if last_line and not re.search(r"[.!?。！？\"'”’\)\]\}]|^\s*\|", last_line) and not MD_HEADING_RE.match(last_line):
            truncated = True
            trunc_reasons.append(f"Final line ends abruptly without terminal punctuation: '{last_line[-60:]}'")
        if raw_text.count("(") - raw_text.count(")") >= 2 or raw_text.count("[") - raw_text.count("]") >= 2:
            truncated = True
            trunc_reasons.append("Unbalanced parentheses/brackets indicate possible mid-text truncation.")
    if truncated:
        last_sec = sections[-1]["title"] if sections else "(Preamble)"
        last_pid = sections[-1]["paragraphs"][-1]["paragraph_id"] if sections and sections[-1]["paragraphs"] else "S0.P0"
        gaps.append({"code": "POSSIBLE_TRUNCATION", "severity": "blocking", "detail": "; ".join(trunc_reasons)})
        add_card(
            dimension="D6_structural_integrity_anomalies",
            severity="blocking",
            action_verb="DEFER_TO_AUTHOR",
            section=last_sec,
            paragraph_id=last_pid,
            line_range=f"L{len(lines)}-L{len(lines)}",
            quote_excerpt=stripped_tail[-120:],
            diagnosis="Manuscript appears truncated or cut off before completion.",
            evidence_basis="; ".join(trunc_reasons),
            executable_instruction="Provide the un-truncated manuscript tail before applying final structural edits; current diagnosis covers only the received lines.",
            author_decision_required=True,
        )

    # =========================================================================
    # Bilingual Academic De-AI & Press-Release Prose Audit (advisory, non-style-bias)
    # =========================================================================
    prose_audit = wio.audit_academic_prose(raw_text)
    for pf in prose_audit["findings"]:
        if pf["category"] == "defensive_self_undermining":
            # Map line to paragraph
            matched_p = next((p for p in all_paras if p["start_line"] <= pf["line"] <= p["end_line"]), None)
            sec_t = matched_p["section"] if matched_p else "(Body)"
            pid_t = matched_p["paragraph_id"] if matched_p else f"L{pf['line']}"
            lr_t = matched_p["line_range"] if matched_p else f"L{pf['line']}-L{pf['line']}"
            add_card(
                dimension="D5_conclusion_boundary_epistemic_scale",
                severity="minor",
                action_verb="MOVE",
                section=sec_t,
                paragraph_id=pid_t,
                line_range=lr_t,
                quote_excerpt=matched_p["text"] if matched_p else pf["matched"],
                diagnosis=f"Defensive self-undermining phrasing ('{pf['matched']}') weakens main argument instead of stating objective scope boundaries.",
                evidence_basis="Press-release narrative principle: move genuine scope caveats to Limitations; avoid apologetic framing in primary claims.",
                executable_instruction=f"Remove apologetic qualifier '{pf['matched']}' from {pid_t}; if it reflects a real scientific constraint, relocate it as an explicit boundary condition in Limitations.",
            )

    # Verify anti-vague-advice firewall
    vague_leaks = [
        c["card_id"]
        for c in cards
        if any(re.search(vp, c["executable_instruction"], re.I) for vp in VAGUE_ADVICE_PATTERNS)
    ]

    dim_counts = {
        dim: sum(1 for c in cards if c["dimension"] == dim)
        for dim in (
            "D1_rq_contribution_alignment",
            "D2_section_structure_narrative_order",
            "D3_argument_evidence_chain",
            "D4_paragraph_relations_redundancy",
            "D5_conclusion_boundary_epistemic_scale",
            "D6_structural_integrity_anomalies",
        )
    }

    diagnosis_payload = {
        "case_id": case_id,
        "task_id": task_id,
        "manuscript": manuscript_path.name,
        "line_count": len(lines),
        "section_count": len(sections),
        "paragraph_count": len(all_paras),
        "sections_overview": [
            {
                "section_id": s["section_id"],
                "title": s["title"],
                "role": s["role"],
                "lines": f"L{s['start_line']}-L{s['end_line']}",
                "paragraphs": s["paragraph_count"],
                "chars": s["char_len"],
            }
            for s in sections
        ],
        "six_dimension_counts": dim_counts,
        "rq_and_contribution_trace": rq_alignment_trace,
        "chronological_laundry_lists": chronological_clusters,
        "same_metric_conflicts": same_metric_conflicts,
        "redundancy_pairs": redundancy_pairs,
        "prose_audit_summary": {
            "clean": prose_audit["clean"],
            "high_count": prose_audit["high_count"],
            "warning_count": prose_audit["warning_count"],
            "info_count": prose_audit["info_count"],
        },
        "edit_action_cards": cards,
        "firewall_verification": {
            "all_cards_anchored": all(bool(c["anchor"]["paragraph_id"]) and bool(c["anchor"]["line_range"]) for c in cards),
            "all_verbs_in_closed_set": all(c["action_verb"] in ALLOWED_ACTION_VERBS for c in cards),
            "vague_advice_leaks": vague_leaks,
            "acl_h1_h17_anti_style_bias": "enforced",
            "unsolicited_full_rewrite": False,
        },
    }

    return {
        "diagnosis": diagnosis_payload,
        "invariants": invariants,
        "evidence_map": evidence_map,
        "gaps": gaps,
        "prose_audit": prose_audit,
    }


def render_editing_action_plan_md(diag: dict[str, Any]) -> str:
    """Render an actionable, author-ready Markdown editing plan with exact anchors."""
    lines = [
        f"# Structural and Logical Editing Action Plan ({diag['case_id']})",
        "",
        f"- **Source Manuscript**: `{diag['manuscript']}` ({diag['line_count']} lines, {diag['section_count']} sections, {diag['paragraph_count']} paragraphs)",
        f"- **Review Scope**: Six-dimension source-grounded structural diagnosis (editable plan only; no unsolicited full rewrite; author retains all scientific stance decisions).",
        f"- **Total Action Cards**: {len(diag['edit_action_cards'])} "
        f"(Blocking: {sum(1 for c in diag['edit_action_cards'] if c['severity'] == 'blocking')}, "
        f"Major: {sum(1 for c in diag['edit_action_cards'] if c['severity'] == 'major')}, "
        f"Minor: {sum(1 for c in diag['edit_action_cards'] if c['severity'] == 'minor')})",
        "",
        "## 1. Six-Dimension Coverage Summary",
        "",
        "| Dimension | Findings | Key Mechanism Checked |",
        "| --- | ---: | --- |",
        f"| D1. RQ & Contribution Alignment | {diag['six_dimension_counts']['D1_rq_contribution_alignment']} | Cross-chapter closure from Intro RQ/contributions to Methods/Results/Conclusion |",
        f"| D2. Section Structure & Narrative Order | {diag['six_dimension_counts']['D2_section_structure_narrative_order']} | Chronological literature laundry lists, IMRaD ordering, misplaced content, press-release flow |",
        f"| D3. Argument Chain (Claim -> Evidence -> Warrant) | {diag['six_dimension_counts']['D3_argument_evidence_chain']} | Same-metric contradictions, unsupported numerical claims, missing inferential bridges |",
        f"| D4. Paragraph Relations & Redundancy | {diag['six_dimension_counts']['D4_paragraph_relations_redundancy']} | Cross-section duplication (excluding legitimate Abstract restatement), overloaded paragraphs |",
        f"| D5. Conclusion Scope & Epistemic Scale | {diag['six_dimension_counts']['D5_conclusion_boundary_epistemic_scale']} | Observational-to-causal escalation, universal extrapolation, missing limitations |",
        f"| D6. Structural Integrity & Anomalies | {diag['six_dimension_counts']['D6_structural_integrity_anomalies']} | Missing canonical sections, missing References, truncation/cutoff |",
        "",
        "## 2. Executable Editing Action Cards",
        "",
    ]

    sev_rank = {"blocking": 0, "major": 1, "minor": 2}
    sorted_cards = sorted(diag["edit_action_cards"], key=lambda c: (sev_rank.get(c["severity"], 3), c["card_id"]))
    if not sorted_cards:
        lines.append("No structural defects triggered automated rules; perform manual verification of argument transitions against domain evidence.")
    else:
        for c in sorted_cards:
            anc = c["anchor"]
            lines.extend([
                f"### [{c['severity'].upper()}] {c['card_id']} — `{c['action_verb']}` ({c['dimension']})",
                f"- **Location**: Section `{anc['section']}` | Paragraph `{anc['paragraph_id']}` | Lines `{anc['line_range']}`",
                f"- **Quote Excerpt**: \"{anc['quote_excerpt']}\"",
                f"- **Diagnosis**: {c['diagnosis']}",
                f"- **Evidence Basis**: {c['evidence_basis']}",
                f"- **Executable Edit Action**: {c['executable_instruction']}",
                f"- **Target Location**: {c['target_location']}",
                f"- **Author Decision**: `{'REQUIRED (do not override author stance)' if c['author_decision_required'] else 'Optional confirmation'}` — Options: `Accept` / `Partially Accept` / `Reject`",
                "",
            ])

    if diag["same_metric_conflicts"]:
        lines.extend([
            "## 3. Detected Same-Metric Contradictions Requiring Reconciliation",
            "",
            "| Metric | Conflict Type | Entry A | Entry B | Reconciled in Text? |",
            "| --- | --- | --- | --- | --- |",
        ])
        for cf in diag["same_metric_conflicts"]:
            ea, eb = cf["entry_a"], cf["entry_b"]
            lines.append(
                f"| {cf['metric']} | {cf['conflict_type']} | "
                f"{ea['paragraph_id']} ({ea['line_range']}): {ea['value']}{ea['unit']} [{ea['direction']}] | "
                f"{eb['paragraph_id']} ({eb['line_range']}): {eb['value']}{eb['unit']} [{eb['direction']}] | "
                f"{'Yes' if cf['reconciled_in_text'] else 'No'} |"
            )
        lines.append("")

    if diag["chronological_laundry_lists"]:
        lines.extend([
            "## 4. Chronological Literature Laundry Lists Detected",
            "",
        ])
        for cl in diag["chronological_laundry_lists"]:
            lines.append(
                f"- Section `{cl['section']}` ({cl['scope']}): paragraphs `{', '.join(cl['paragraph_ids'])}` ordered strictly by year `{cl['years']}` without comparative synthesis -> apply `REGROUP_BY_DIMENSION`."
            )
        lines.append("")

    lines.extend([
        "## 5. Preservation & Fairness Boundaries",
        "- All numeric results, p-values, sample sizes, citations, and domain facts from the original manuscript remain untouched.",
        "- No stylistic preferences (vocabulary taste, passive vs active voice) were flagged as structural defects (ACL H1-H17 firewall).",
        "- Full unsolicited manuscript rewrite was intentionally omitted per Concrete Structural Review Scope.",
        "",
    ])
    return "\n".join(lines)


def _discover_manuscript(input_dir: Path) -> tuple[Path, str]:
    case_id = "case-01"
    case_json = input_dir / "case.json"
    if case_json.is_file():
        try:
            cobj = json.loads(case_json.read_text(encoding="utf-8"))
            case_id = str(cobj.get("case_id") or case_id)
        except Exception:
            pass
    exts = (".md", ".tex", ".docx", ".txt", ".pdf")
    candidates = [
        p for p in sorted(input_dir.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in exts
        and p.name.lower() not in {"task.md", "case.json", "readme.md"}
    ]
    if not candidates:
        raise FileNotFoundError(f"No manuscript file found under {input_dir}")
    # Prefer largest manuscript text file
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0], case_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Six-dimension source-grounded structural logic diagnosis engine.")
    parser.add_argument("--manuscript", default="", help="Path to manuscript (.md/.tex/.txt/.docx/.pdf).")
    parser.add_argument("--input-dir", default="", help="Alternative input directory containing case.json and manuscript.")
    parser.add_argument("--out-dir", required=True, help="Output directory for structural_diagnosis.json, editing_action_plan.md, and result.json.")
    parser.add_argument("--case-id", default="", help="Override case_id.")
    args = parser.parse_args(argv)

    if args.manuscript:
        ms_path = Path(args.manuscript)
        case_id = args.case_id or ms_path.stem
    elif args.input_dir:
        ms_path, detected_cid = _discover_manuscript(Path(args.input_dir))
        case_id = args.case_id or detected_cid
    else:
        parser.error("Provide either --manuscript or --input-dir.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    res = diagnose_manuscript(ms_path, case_id=case_id)
    diag = res["diagnosis"]
    plan_md = render_editing_action_plan_md(diag)

    diag_path = out_dir / "structural_diagnosis.json"
    plan_path = out_dir / "editing_action_plan.md"
    result_path = out_dir / "result.json"

    diag_path.write_text(json.dumps(diag, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_path.write_text(plan_md, encoding="utf-8")

    change_log = [
        {
            "card_id": c["card_id"],
            "action_verb": c["action_verb"],
            "dimension": c["dimension"],
            "location": f"{c['anchor']['section']} {c['anchor']['paragraph_id']} ({c['anchor']['line_range']})",
            "status": "proposed_for_author_review",
        }
        for c in diag["edit_action_cards"]
    ]
    limitations = [
        "Automated structural diagnostics rely on explicit textual markers, section headers, and lexical/metric patterns; nuanced domain-specific logical warrants require human scholar confirmation.",
        "No unsolicited full manuscript rewrite is produced; author retains authority to Accept, Partially Accept, or Reject each action card.",
    ]
    result_payload = wio.build_writing_result(
        case_id=case_id,
        task_id="paper-structure-optimization",
        deliverable={
            "type": "six_dimension_structural_diagnosis_and_editing_action_plan",
            "diagnosis_json": "structural_diagnosis.json",
            "editing_plan_md": "editing_action_plan.md",
            "total_action_cards": len(diag["edit_action_cards"]),
            "six_dimension_counts": diag["six_dimension_counts"],
        },
        preserved_facts={
            "source_manuscript": ms_path.name,
            "numbers_with_units_count": len(res["invariants"]["numbers_with_units"]),
            "pvalues_count": len(res["invariants"]["pvalues"]),
            "sample_sizes": res["invariants"]["sample_sizes"],
            "dois": res["invariants"]["dois"],
            "figure_table_refs": res["invariants"]["figure_table_refs"],
            "manuscript_mutated": False,
        },
        evidence_map=res["evidence_map"],
        change_log=change_log,
        artifacts=["result.json", "structural_diagnosis.json", "editing_action_plan.md"],
        sources=[{"file": ms_path.name, "role": "primary_manuscript", "lines": diag["line_count"]}],
        gaps=res["gaps"],
        limitations=limitations,
        verification={
            "engine": "structural_logic_engine.py",
            "firewall": diag["firewall_verification"],
            "blocking_cards": sum(1 for c in diag["edit_action_cards"] if c["severity"] == "blocking"),
            "chronological_laundry_lists_found": len(diag["chronological_laundry_lists"]),
            "same_metric_conflicts_found": len(diag["same_metric_conflicts"]),
            "cross_section_redundancy_pairs": sum(1 for r in diag["redundancy_pairs"] if not r["legitimate_abstract_restatement"]),
        },
    )
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "total_action_cards": len(diag["edit_action_cards"]),
        "six_dimension_counts": diag["six_dimension_counts"],
        "chronological_laundry_lists": len(diag["chronological_laundry_lists"]),
        "same_metric_conflicts": len(diag["same_metric_conflicts"]),
        "gaps_count": len(res["gaps"]),
        "artifacts": ["result.json", "structural_diagnosis.json", "editing_action_plan.md"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
