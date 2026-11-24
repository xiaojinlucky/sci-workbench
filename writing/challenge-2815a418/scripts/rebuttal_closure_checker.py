#!/usr/bin/env python3
"""Deterministic peer-review rebuttal closure, /input review-item extractor, triple-bound edit,
re-anchoring, multi-round commitment, evidence, and tone auditor.

Pure Python 3.12 stdlib (zero third-party dependencies). Supports:
  - Native .pdf/.docx/.md/.txt reading via scripts/doc_extract.py (physical page preservation, OOXML)
  - Composite comment headings prioritized over pure reviewer section headers
    (e.g. '### Reviewer #1 - Comment 1' parsed directly without being swallowed)
  - Composite unique key (round, reviewer_id, local_comment_id) preventing cross-reviewer Q1/Comment 1 collision
  - Full-line coverage state machine capturing mixed explicit IDs + unnumbered requests (unassigned_requests),
    single & multi sub-items, and intra-paragraph multi-request decomposition with separate parent/atomic counts
  - Shared-anchor collision detection (SHARED_ANCHOR_RISK) and substantive CLARIFY action gate
    (CLARIFY_NO_SUBSTANTIVE_ACTION requiring manuscript diff OR verifiable quote/anchor + explicit no_manuscript_edit_justification)
  - Triple-bound edit verification, quote_sha1 re-anchoring, multi-round commitment continuity,
    substantive 3-element missing-evidence closure, DOI/stat preservation, and 5-field contract patch
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from doc_extract import extract_document

COMPOSITE_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|[-*]\s+|\*{2})?"
    r"(Reviewer\s*#?\s*(\d+)\s*[-–—:,]\s*(?:Comment|Point|Major|Minor|Question|Q)\s*#?\s*(\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?))"
    r"(?:\*{2})?\s*(?:[:：—–\-]\s*|\s+|$)(.*)$",
    re.I,
)

COMMENT_ID_PATTERN = (
    r"(?:"
    r"(?:E|R\d+)\.\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?"
    r"|R\d+:(?:Q|W|C|Comment|Point|Major|Minor)\s*#?\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?"
    r"|Reviewer\s*#?\s*\d+\s*[-–—:,]\s*(?:Comment|Point|Major|Minor|Question|Q)\s*#?\s*\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?"
    r"|(?:Comment|Major|Minor|Point|Item|Question)\s*#?\s*\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?"
    r"|(?:Q|W|C)\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?"
    r"|(?:UNASSIGNED|AUTO)-\d+"
    r"|(?:审稿人\s*\d+\s*意见|主编意见|编辑意见|意见|问题)\s*(?:\d+(?:\.\d+)?|[一二三四五六七八九十]+)(?:\([a-z0-9]+\)|[a-z])?"
    r")"
)
COMMENT_ID_RE = re.compile(r"(?<![A-Za-z0-9_])(" + COMMENT_ID_PATTERN + r")(?![A-Za-z0-9_])", re.I)
HEADER_ID_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|[-*]\s+|\*{2})?(" + COMMENT_ID_PATTERN + r")(?:\*{2})?\s*(?:[:：—–\-]|$|\s)(.*)$",
    re.I,
)
REVIEWER_SECTION_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|\*{2})?(?:Reviewer\s*#?\s*(\d+)|审稿人\s*#?\s*(\d+)|(Editor|Associate\s+Editor|主编|编辑))(?:\*{2})?\s*(?:[:：\-—–]\s*.*)?$",
    re.I,
)
BARE_NUM_ITEM_RE = re.compile(r"^\s*(\d{1,2})\.\s+(.{12,})$")
SUB_BULLET_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\(([a-z]|[1-9])\)|([a-z])\)|([①②③④⑤⑥]))\s+(.+)$", re.I)
REQUEST_SIGNAL_RE = re.compile(
    r"\?|？|\b(?:please|provide|clarify|add|explain|justify|revise|report|discuss|soften|include|address|"
    r"specify|perform|conduct|validate|compare|remove|replace|expand|verify|state|define|justify|should\s+be|"
    r"must\s+be|need\s+to|needs\s+to|recommend|suggest)\b"
    r"|请|补充|说明|修改|解释|讨论|弱化|提供|验证|对比|删除|明确|建议|需要",
    re.I,
)
UNNUMBERED_STANDALONE_LEAD_RE = re.compile(
    r"^\s*(?:[-*•]\s+)?(?:Also|Additionally|In\s+addition|Finally|Furthermore|Moreover|Another\s+(?:point|concern|issue|request)|"
    r"Separately|Please\s+|We\s+also\s+ask|此外|另外|还请|同时请|最后请)\b",
    re.I,
)

CANONICAL_DISPOSITIONS = {
    "ACCEPT_TEXT", "CLARIFY_EXISTING", "SOFTEN_CLAIM",
    "PARTIAL", "DISAGREE_WITH_EVIDENCE", "AUTHOR_INPUT_NEEDED",
}
DISPOSITION_ALIASES = {"DISAGREE": "DISAGREE_WITH_EVIDENCE", "CLARIFY": "CLARIFY_EXISTING"}
DISPOSITION_RE = re.compile(
    r"\b(ACCEPT_TEXT|CLARIFY_EXISTING|CLARIFY|SOFTEN_CLAIM|PARTIAL|DISAGREE_WITH_EVIDENCE|DISAGREE|AUTHOR_INPUT_NEEDED)\b"
)
UNFEASIBLE_SIGNAL_RE = re.compile(
    r"\b(?:AUTHOR_INPUT_NEEDED|NOT_FEASIBLE|OUT_OF_SCOPE|EVIDENCE_GAP)\b|未到位|无法补做|超出范围", re.I
)
EDIT_CLAIM_PROSE_RE = re.compile(
    r"\b(?:we\s+(?:have\s+)?(?:revised|updated|modified|added|rewrote|softened|clarified\s+in\s+the\s+revised)|revised\s+text|manuscript\s+(?:now\s+)?states)\b"
    r"|已(?:修改|修订|补充|增补|改写|弱化|调整)|修订稿(?:中)?已",
    re.I,
)
NO_EDIT_JUSTIFICATION_RE = re.compile(
    r"(?:no_manuscript_edit_justification|no\s+manuscript\s+edit(?:\s+is)?\s+(?:needed|required)|"
    r"no\s+text\s+(?:change|revision)\s+(?:is\s+)?(?:needed|required)|"
    r"text\s+(?:was\s+)?kept\s+unchanged\s+because|no\s+revision\s+to\s+(?:the\s+)?manuscript\s+text\s+is\s+(?:needed|required)|"
    r"unchanged\s+in\s+manuscript\s+because|"
    r"未修改正文理由|无需修改正文|正文无需改动|保留原文理由|正文已明确表述无需改动)\s*[:：\-]?\s*(.{15,})",
    re.I,
)
ANCHOR_BRACKET_RE = re.compile(r"\[([^\[\]\n]{2,95})\]")
LINE_SPEC_RE = re.compile(
    r"(?:(?:\b(?:Lines?|L)\s*(\d+)(?:\s*[-–—~]\s*(?:L|Lines?\s*)?(\d+))?)|(?:第\s*(\d+)(?:\s*[-–—~到至]\s*(\d+))?\s*行))",
    re.I,
)
SECTION_SPEC_RE = re.compile(r"(?:§\s*|Section\s+)([^,;\]]+?)(?=\s*(?:,|\bL\d|\bLine|\bLines|第\s*\d+\s*行|$))", re.I)
INLINE_QUOTE_RE = re.compile(r"\"([^\"]{12,420})\"|「([^」]{10,420})」|“([^”]{10,420})”")
BEFORE_OR_REVIEWER_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*{2})?(?:Reviewer(?:\s+#?\d+)?\s*Comment|Original\s+Comment|Comment\s+Summary|"
    r"Original\s+text|Baseline(?:\s+text)?|Before(?:\s+revision)?|Previous(?:ly)?|"
    r"审稿意见|原意见|意见摘要|修改前|原文|基线原文)(?:\*{2})?\s*[:：]",
    re.I,
)

FABRICATION_RE = re.compile(
    r"\b(?:accepted\s+for\s+publication|editor\s+(?:has\s+)?approved|manuscript\s+(?:is\s+)?accepted|"
    r"completed\s+(?:all\s+)?(?:the\s+)?(?:additional\s+)?wet-?lab\s+experiments?|"
    r"performed\s+(?:the\s+)?(?:additional|new)\s+(?:wet-?lab|animal|clinical|validation)\s+(?:experiment|cohort|trial)s?|"
    r"new\s+(?:animal|patient|clinical)\s+cohort\s+(?:was\s+|has\s+been\s+)?collected|"
    r"full\s+manuscript\s+(?:revision\s+)?(?:is\s+)?(?:complete|completed|accepted)|"
    r"authors?\s+have\s+already\s+revised\s+the\s+full\s+manuscript)\b"
    r"|编辑部已接收|已完成(?:全部)?真实补实验|已补做(?:新)?(?:湿实验|动物实验|临床队列)|整篇(?:全文)?返修(?:已)?完成",
    re.I,
)
OVERPROMISE_RE = re.compile(
    r"\b(?:we\s+will\s+(?:definitely\s+|certainly\s+)?(?:perform|complete|conduct|add)\s+(?:the\s+)?(?:(?:new|additional|wet-?lab|animal|clinical|validation|external|multicenter)\s+){1,3}(?:experiment|cohort|validation|study|assay|trial)s?\s+(?:before|prior\s+to)\s+(?:publication|acceptance)|"
    r"guaranteed\s+to\s+(?:add|complete)\s+(?:the\s+)?(?:(?:new|additional|validation)\s+){0,2}(?:experiment|data|cohort)s?)\b"
    r"|承诺在(?:发表|接收)前补(?:完|做)(?:新)?(?:验证)?实验|保证后续补做(?:全部)?(?:新)?实验",
    re.I,
)
REBUKE_RE = re.compile(
    r"\b(?:as\s+we\s+already\s+stated|reviewer\s+failed\s+to\s+read|reviewer\s+misunderstood|"
    r"the\s+reviewer\s+is\s+wrong|already\s+clearly\s+(?:explained|described|stated)\s+in\s+the\s+(?:original\s+)?manuscript|"
    r"should\s+have\s+noticed)\b"
    r"|如前文早已清楚说明|审稿人(?:未仔细阅读|没有仔细看|误解了|理解有误)|原文早已明确写明",
    re.I,
)
GENERIC_CROSS_REVIEWER_RE = re.compile(
    r"\b(?:as\s+(?:the\s+)?other\s+reviewer\s+(?:noted|mentioned|pointed\s+out|requested)|"
    r"see\s+our\s+response\s+to\s+(?:Reviewer|R)\s*#?\d+|"
    r"the\s+other\s+reviewer\s+requested\s+the\s+opposite|"
    r"as\s+Reviewer\s+#?\d+\s+also\s+(?:noted|suggested))\b"
    r"|请参见(?:我们)?对(?:审稿人\s*\d+|另一位审稿人)的回复|正如(?:另一位审稿人|审稿人\s*\d+)(?:所)?指出|另一位审稿人提出了相反",
    re.I,
)
EMPTY_FILLER_RE = re.compile(
    r"^(?:we\s+thank\s+the\s+reviewer[^.\n]*\.?\s*)?(?:we\s+have\s+revised\s+accordingly\.?|thank\s+you\s+for\s+(?:this|the)\s+comment\.?|已按意见修改。?|感谢审稿人的意见。?)$",
    re.I,
)
AI_STYLE_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("AI模板总结语", re.compile(r"综上所述|总而言之|由此可见|不难发现|众所周知"), "删套话，直陈修改动作与依据"),
    ("AI洞察路标", re.compile(r"值得注意的是|需要指出的是|有必要指出的是|从某种意义上说"), "直接陈述核心事实"),
    ("翻案腔", re.compile(r"(?:不是|并非)[^，。\n]{1,24}[，,]?而是"), "改为正面直接陈述判断与依据"),
    ("营销/空泛拔高词", re.compile(r"显著赋能|深刻揭示|开启新的篇章|意义深远|颠覆性|重磅"), "换为克制学术实证表述"),
    ("破折号滥用", re.compile(r"——"), "改用逗号、括号或短句"),
    ("英文AI套话", re.compile(r"\b(?:it\s+is\s+worth\s+noting\s+that|plays\s+a\s+pivotal\s+role|delve\s+into|tapestry\s+of|underscores\s+the\s+importance\s+of)\b", re.I), "Replace stock phrasing with direct domain prose"),
    ("空洞回复套话", re.compile(r"\bwe\s+have\s+revised\s+accordingly\b", re.I), "Specify the exact edit and location"),
]
ME_BOUNDARY_RE = re.compile(
    r"boundary\s+rationale|scope\s+boundary|not\s+feasible|out\s+of\s+scope|unavailable|cannot\s+(?:perform|conduct|collect|run)|"
    r"受控(?:修订)?边界|边界(?:理由|说明)|无法补做|超出(?:当前)?范围|不可行|缺乏(?:原始|外部)",
    re.I,
)
ME_ALTERNATIVE_RE = re.compile(
    r"existing[- ]material\s+alternative|alternative\s+clarification|claim\s+soften|limitation|clarified\s+in|revised\s+\[|"
    r"现有材料|替代(?:澄清|说明|方案)|局限性?|弱化(?:了)?(?:结论|表述)|边界说明",
    re.I,
)
ME_AUTHOR_CONFIRM_RE = re.compile(
    r"author\s+confirmation|required\s+input|confirm\s+whether|author\s+(?:must|to|should)\s+(?:supply|confirm|provide|approve)|"
    r"人工确认|作者确认|待(?:作者)?(?:确认|补充|提供)|需(?:作者)?提供",
    re.I,
)
ME_SPECIFICITY_RE = re.compile(
    r"\[(?:§|L\d|第\s*\d+)|§\w+|Table\s*\d+|Fig(?:ure)?\.?\s*\d+|(?:dataset|cohort|sample|raw\s+data|p-?value|ethics|IRB|clinical|gene|assay|table|section|原始数据|样本|队列|伦理|表\s*\d+|图\s*\d+|方法|局限)",
    re.I,
)

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
STAT_NUM_RE = re.compile(r"(?:\bp\s*[<=>≤≥]\s*0?\.\d+|\bn\s*=\s*\d+(?:,\d{3})*|\b\d+(?:,\d{3})*(?:\.\d+)?%)", re.I)
NUM_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z0-9_])")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "by", "at", "from", "as",
    "is", "are", "was", "were", "be", "been", "being", "that", "this", "these", "those", "it", "we",
    "our", "their", "has", "have", "had", "also", "very", "more", "than", "into", "such", "may", "can",
    "please", "should", "must", "would", "could", "section", "manuscript", "paper", "study", "authors",
}


def canonicalize_id(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip())
    s = re.sub(
        r"Reviewer\s*#?\s*(\d+)\s*[-–—:,]\s*(?:Comment|Point|Major|Minor|Question|Q)\s*#?\s*(\d+(?:\.\d+)?)",
        r"R\1.\2",
        s,
        flags=re.I,
    )
    s = re.sub(r"\(([a-z0-9]+)\)", r"\1", s, flags=re.I)
    s = re.sub(r"审稿人\s*(\d+)\s*意见\s*", r"审稿人\1意见", s)
    s = re.sub(r"(?:主编意见|编辑意见|意见|问题)\s*", lambda m: m.group(0).strip(), s)
    return s.lower()


def decompose_comment_identity(raw_id: str, current_reviewer: str | None = None, round_id: str = "R1") -> tuple[str, str, str, str]:
    """Return (reviewer_id, local_comment_id, comment_key, display_id) using composite (round, reviewer_id, local_comment_id)."""
    clean_raw = re.sub(r"\s+", " ", raw_id.strip())
    rnd = round_id.strip().upper() or "R1"

    # 1. Explicit R1:Q1 / R2:Comment 1 form
    m = re.match(r"^(R\d+|E):(.+)$", clean_raw, re.I)
    if m:
        rev = m.group(1).upper()
        loc_raw = m.group(2).strip()
        loc = re.sub(r"\s+", "", canonicalize_id(loc_raw))
        return rev, loc, f"{rnd.lower()}::{rev.lower()}::{loc}", f"{rev}:{loc_raw}"

    # 2. Composite Reviewer #1 - Comment 1 / Reviewer 1, Q2
    m = re.match(
        r"^Reviewer\s*#?\s*(\d+)\s*[-–—:,]\s*(Comment|Point|Major|Minor|Question|Q)\s*#?\s*(\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?)$",
        clean_raw,
        re.I,
    )
    if m:
        rev = f"R{m.group(1)}"
        kind = m.group(2).lower()
        num = re.sub(r"\(([a-z0-9]+)\)", r"\1", m.group(3), flags=re.I).lower()
        loc = f"q{num}" if kind in ("q", "question") else num
        return rev, loc, f"{rnd.lower()}::{rev.lower()}::{loc}", clean_raw

    # 3. Standard R1.1 / E.1
    m = re.match(r"^(R\d+|E)\.(\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?)$", clean_raw, re.I)
    if m:
        rev = m.group(1).upper()
        loc = re.sub(r"\(([a-z0-9]+)\)", r"\1", m.group(2), flags=re.I).lower()
        return rev, loc, f"{rnd.lower()}::{rev.lower()}::{loc}", clean_raw

    # 4. Chinese 审稿人3意见1 / 主编意见1
    m = re.match(r"^审稿人\s*(\d+)\s*意见\s*([0-9一二三四五六七八九十]+(?:\([a-z0-9]+\)|[a-z])?)$", clean_raw)
    if m:
        rev = f"R{m.group(1)}"
        loc = re.sub(r"\(([a-z0-9]+)\)", r"\1", m.group(2), flags=re.I).lower()
        return rev, loc, f"{rnd.lower()}::{rev.lower()}::{loc}", clean_raw
    m = re.match(r"^(?:主编意见|编辑意见)\s*([0-9一二三四五六七八九十]+(?:\([a-z0-9]+\)|[a-z])?)$", clean_raw)
    if m:
        rev = "E"
        loc = re.sub(r"\(([a-z0-9]+)\)", r"\1", m.group(1), flags=re.I).lower()
        return rev, loc, f"{rnd.lower()}::{rev.lower()}::{loc}", clean_raw

    # 5. Scoped local ID (Q1, Comment 1, Major 1, W1, 问题1, UNASSIGNED-1, AUTO-1)
    rev = (current_reviewer or "G").upper()
    m_c = re.match(r"^(?:Comment|Point|Major|Minor|Item)\s*#?\s*(\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?)$", clean_raw, re.I)
    if m_c:
        loc = f"c{re.sub(r'\\(([a-z0-9]+)\\)', r'\\1', m_c.group(1), flags=re.I).lower()}"
    else:
        loc = re.sub(r"\s+", "", canonicalize_id(clean_raw))
    if rev != "G" and not clean_raw.upper().startswith(("UNASSIGNED-", "AUTO-")):
        disp = f"{rev}:{clean_raw}"
    else:
        disp = clean_raw
    return rev, loc, f"{rnd.lower()}::{rev.lower()}::{loc}", disp


def reviewer_group_of(cid: str, reviewer_id: str | None = None) -> str | None:
    if reviewer_id:
        ru = reviewer_id.upper()
        if ru == "E":
            return "editor"
        if ru.startswith("R") and ru[1:].isdigit():
            return ru.lower()
    low = canonicalize_id(cid)
    m = re.match(r"^(r\d+)[\.:]", low)
    if m:
        return m.group(1)
    m = re.match(r"^(审稿人\d+)意见", low)
    if m:
        return m.group(1)
    if low.startswith(("e.", "e:", "主编", "编辑")):
        return "editor"
    return None


def quote_sha1(text: str) -> str:
    norm = " ".join(text.split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10]


def _classify_comment_meta(text: str) -> tuple[str, str, bool]:
    low = text.lower()
    needs_ext = bool(re.search(
        r"wet-?lab|new\s+(?:cohort|experiment|assay|data|validation|prospective|multicenter)|"
        r"external\s+cohort|raw\s+data|re-?collect|individual\s+patient\s+data|prospective\s+trial|"
        r"补做.*实验|补充.*队列|原始数据|外部验证|前瞻性",
        low,
    ))
    if needs_ext:
        cat = "experiment_or_external_data"
    elif re.search(r"p-?value|confidence\s+interval|ci|regression|statist|sample\s+size|power|joinpoint|aapc|统计|显著性|样本量|回归", low):
        cat = "data_stats"
    elif re.search(r"method|icd-?10|prisma|strobe|protocol|inclusion|exclusion|ethic|方法|纳入|排除|伦理|口径", low):
        cat = "methods"
    elif re.search(r"citation|reference|doi|bibliograph|引用|参考文献|文献", low):
        cat = "citations"
    else:
        cat = "writing_and_claims"
    sev = "major" if (needs_ext or re.search(r"major|causal|overclaim|fatal|serious|主要|重大|因果|夸大", low)) else "minor"
    return cat, sev, needs_ext


def _split_multi_requests_in_paragraph(text: str) -> list[str]:
    """Detect >=2 independent imperative/interrogative requests inside one paragraph."""
    cleaned = re.sub(r"^\s*(?:#{1,6}\s+|[-*]\s+|\*{2})?" + COMMENT_ID_PATTERN + r"(?:\*{2})?\s*[:：—–\-]?\s*", "", text.strip(), flags=re.I)
    if len(cleaned) < 40:
        return []
    # Split on sentence boundaries and semicolons
    raw_clauses = [c.strip() for c in re.split(r"(?<=[.?？!！;；])\s+|(?<=[。？！；])", cleaned) if c.strip()]
    req_clauses = [c for c in raw_clauses if len(c) >= 18 and REQUEST_SIGNAL_RE.search(c)]
    if len(req_clauses) >= 2:
        return req_clauses
    # Also check explicit 'First, ... Second, ...' or '(1) ... (2) ...' inline markers inside a single paragraph
    inline_num_parts = [p.strip() for p in re.split(r"(?:^|\s)(?:\([1-9a-d]\)|[①②③④⑤])\s+", cleaned) if len(p.strip()) >= 15]
    if len(inline_num_parts) >= 2 and sum(1 for p in inline_num_parts if REQUEST_SIGNAL_RE.search(p)) >= 2:
        return inline_num_parts
    return []


def parse_comments_structure(comments_txt: str, round_id: str = "R1") -> tuple[list[dict[str, Any]], list[str]]:
    """Full-line state-machine comment parser.

    Fixes:
      1. Composite comment headers ('### Reviewer #1 - Comment 1') matched BEFORE pure reviewer section headers.
      2. Composite key (round, reviewer_id, local_comment_id) prevents cross-reviewer Q1/Comment 1 collisions.
      3. Mixed explicit IDs + unnumbered standalone requests are both captured (unassigned_requests), zero silent drops.
      4. Single and multiple sub-bullets plus intra-paragraph multi-requests are decomposed into atomic sub-items
         with parent_id and source_span while keeping parent vs atomic counts strictly separated.
    """
    lines = comments_txt.splitlines()
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    key_to_idx: dict[str, int] = {}

    current_reviewer: str | None = None
    current_parent_idx: int | None = None
    pending_subs: list[tuple[str, str, int]] = []
    unassigned_counter = 0
    saw_blank_since_parent = False
    circled_map = {"①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5", "⑥": "6"}

    def _flush_parent() -> None:
        nonlocal pending_subs, current_parent_idx
        if current_parent_idx is None:
            pending_subs = []
            return
        parent = items[current_parent_idx]
        p_id = parent["id"]
        p_key = parent["comment_key"]
        p_rev = parent["reviewer_id"]
        p_loc = parent["local_comment_id"]

        # 1) Explicit sub-bullets: register even if ONLY 1 sub-item exists!
        if pending_subs:
            parent["is_parent"] = True
            sub_ids: list[str] = []
            for sub_lbl, sub_txt, sub_ln in pending_subs:
                sub_id = f"{p_id}({sub_lbl})"
                sub_loc = f"{p_loc}({sub_lbl})"
                sub_key = f"{round_id.lower()}::{p_rev.lower()}::{sub_loc}"
                sub_ids.append(sub_id)
                if sub_key not in seen_keys:
                    seen_keys.add(sub_key)
                    cat, sev, ext = _classify_comment_meta(sub_txt)
                    items.append({
                        "id": sub_id,
                        "display_id": sub_id,
                        "raw_id": f"({sub_lbl})",
                        "comment_key": sub_key,
                        "canonical_id": canonicalize_id(sub_id),
                        "round": round_id,
                        "reviewer_id": p_rev,
                        "local_comment_id": sub_loc,
                        "parent_id": p_id,
                        "parent_key": p_key,
                        "sub_label": sub_lbl,
                        "is_parent": False,
                        "is_atomic_subitem": True,
                        "is_unassigned": False,
                        "sub_item_ids": [],
                        "id_source": "explicit_sub_bullet",
                        "source_span": {"start_line": sub_ln, "end_line": sub_ln},
                        "category": cat,
                        "severity": sev,
                        "requires_external_evidence_hint": ext,
                        "excerpt": sub_txt[:180],
                        "full_text": sub_txt,
                    })
            parent["sub_item_ids"] = sub_ids
        else:
            # 2) Intra-paragraph multi-request decomposition when no explicit sub-bullets
            multi_reqs = _split_multi_requests_in_paragraph(parent.get("full_text", ""))
            if len(multi_reqs) >= 2:
                parent["is_parent"] = True
                sub_ids = []
                for idx_s, req_txt in enumerate(multi_reqs, 1):
                    sub_lbl = f"s{idx_s}"
                    sub_id = f"{p_id}[{sub_lbl}]"
                    sub_loc = f"{p_loc}[{sub_lbl}]"
                    sub_key = f"{round_id.lower()}::{p_rev.lower()}::{sub_loc}"
                    sub_ids.append(sub_id)
                    if sub_key not in seen_keys:
                        seen_keys.add(sub_key)
                        cat, sev, ext = _classify_comment_meta(req_txt)
                        items.append({
                            "id": sub_id,
                            "display_id": sub_id,
                            "raw_id": f"[{sub_lbl}]",
                            "comment_key": sub_key,
                            "canonical_id": canonicalize_id(sub_id),
                            "round": round_id,
                            "reviewer_id": p_rev,
                            "local_comment_id": sub_loc,
                            "parent_id": p_id,
                            "parent_key": p_key,
                            "sub_label": sub_lbl,
                            "is_parent": False,
                            "is_atomic_subitem": True,
                            "is_unassigned": False,
                            "sub_item_ids": [],
                            "id_source": "multi_request_split",
                            "source_span": dict(parent["source_span"]),
                            "category": cat,
                            "severity": sev,
                            "requires_external_evidence_hint": ext,
                            "excerpt": req_txt[:180],
                            "full_text": req_txt,
                        })
                parent["sub_item_ids"] = sub_ids

        # Refresh parent metadata from accumulated full_text
        cat, sev, ext = _classify_comment_meta(parent.get("full_text", ""))
        parent["category"] = cat
        parent["severity"] = sev
        parent["requires_external_evidence_hint"] = ext
        pending_subs = []
        current_parent_idx = None

    def _register_parent(
        raw_id: str,
        line_no: int,
        body_text: str,
        id_source: str,
        rev_override: str | None = None,
        is_unassigned: bool = False,
    ) -> None:
        nonlocal current_parent_idx, saw_blank_since_parent
        rev, loc, ckey, disp = decompose_comment_identity(raw_id, rev_override or current_reviewer, round_id)
        if ckey in seen_keys:
            # Duplicate exact composite key under same reviewer: merge text rather than crashing
            idx = key_to_idx[ckey]
            items[idx]["full_text"] = (items[idx]["full_text"] + "\n" + body_text).strip()
            items[idx]["source_span"]["end_line"] = line_no
            current_parent_idx = idx
            saw_blank_since_parent = False
            return
        seen_keys.add(ckey)
        cat, sev, ext = _classify_comment_meta(body_text)
        idx = len(items)
        key_to_idx[ckey] = idx
        items.append({
            "id": disp,
            "display_id": disp,
            "raw_id": raw_id,
            "comment_key": ckey,
            "canonical_id": canonicalize_id(disp),
            "round": round_id,
            "reviewer_id": rev,
            "local_comment_id": loc,
            "parent_id": None,
            "parent_key": None,
            "sub_label": None,
            "is_parent": False,
            "is_atomic_subitem": False,
            "is_unassigned": is_unassigned,
            "needs_review_queue": is_unassigned,
            "sub_item_ids": [],
            "id_source": id_source,
            "source_span": {"start_line": line_no, "end_line": line_no},
            "category": cat,
            "severity": sev,
            "requires_external_evidence_hint": ext,
            "excerpt": body_text.strip()[:180],
            "full_text": body_text.strip(),
        })
        current_parent_idx = idx
        saw_blank_since_parent = False

    for line_no, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()
        if not stripped:
            saw_blank_since_parent = True
            continue

        # PRIORITY 1: Composite comment header ('### Reviewer #1 - Comment 1: ...') BEFORE pure reviewer section
        cm = COMPOSITE_HEADER_RE.match(raw_line)
        if cm:
            _flush_parent()
            raw_cid = re.sub(r"\s+", " ", cm.group(1).strip())
            current_reviewer = f"R{cm.group(2)}"
            rest = (cm.group(4) or "").strip()
            _register_parent(raw_cid, line_no, rest or raw_cid, "composite_comment_header", rev_override=current_reviewer)
            continue

        # PRIORITY 2: Standard explicit comment header ('### R1.1', '### Q1', '**Comment 1**:', '### 审稿人2意见1')
        hm = HEADER_ID_RE.match(raw_line)
        if hm:
            _flush_parent()
            raw_cid = re.sub(r"\s+", " ", hm.group(1).strip())
            rest = (hm.group(2) or "").strip()
            # Update current_reviewer if ID itself names a reviewer
            rev_inf, _, _, _ = decompose_comment_identity(raw_cid, current_reviewer, round_id)
            if rev_inf != "G":
                current_reviewer = rev_inf
            _register_parent(raw_cid, line_no, rest or stripped, "explicit_header", rev_override=current_reviewer)
            continue

        # PRIORITY 3: Pure reviewer / editor section header ('## Reviewer #1', '## Editor', '## 审稿人 2')
        rsec = REVIEWER_SECTION_RE.match(raw_line)
        if rsec:
            _flush_parent()
            rnum = rsec.group(1) or rsec.group(2)
            if rnum:
                current_reviewer = f"R{rnum}"
            elif rsec.group(3):
                current_reviewer = "E"
            saw_blank_since_parent = False
            continue

        # Ignore pure document title like '# Reviewer Comments' if not a request
        if stripped.startswith("#") and not REQUEST_SIGNAL_RE.search(stripped):
            _flush_parent()
            continue

        # PRIORITY 4: Explicit sub-bullet under active parent
        if current_parent_idx is not None:
            sm = SUB_BULLET_RE.match(raw_line)
            if sm:
                lbl = sm.group(1) or sm.group(2) or circled_map.get(sm.group(3) or "", "")
                if lbl:
                    sub_txt = sm.group(4).strip()
                    pending_subs.append((lbl.lower(), sub_txt, line_no))
                    items[current_parent_idx]["source_span"]["end_line"] = line_no
                    items[current_parent_idx]["full_text"] = (items[current_parent_idx]["full_text"] + "\n" + sub_txt).strip()
                    saw_blank_since_parent = False
                    continue

        # PRIORITY 5: Bare numbered list under a Reviewer/Editor section ('1. Please clarify...')
        bm = BARE_NUM_ITEM_RE.match(raw_line)
        if bm and current_reviewer:
            _flush_parent()
            cid = f"{current_reviewer}.{bm.group(1)}"
            _register_parent(cid, line_no, bm.group(2).strip(), "numbered_under_section", rev_override=current_reviewer)
            continue

        # PRIORITY 6: Unnumbered standalone request detection (covers mixed numbered + unnumbered!)
        is_unassigned_candidate = False
        if current_parent_idx is None:
            if len(stripped) >= 15 and REQUEST_SIGNAL_RE.search(stripped):
                is_unassigned_candidate = True
        elif saw_blank_since_parent and len(stripped) >= 18 and REQUEST_SIGNAL_RE.search(stripped):
            # A new paragraph after a blank line that starts as an independent bullet or request lead
            if UNNUMBERED_STANDALONE_LEAD_RE.match(stripped) or stripped.startswith(("- ", "* ", "• ")):
                is_unassigned_candidate = True

        if is_unassigned_candidate:
            _flush_parent()
            unassigned_counter += 1
            raw_uid = f"UNASSIGNED-{unassigned_counter}"
            _register_parent(
                raw_uid,
                line_no,
                re.sub(r"^[-*•]\s+", "", stripped),
                "unassigned_unnumbered_request",
                rev_override=current_reviewer or "G",
                is_unassigned=True,
            )
            continue

        # PRIORITY 7: Continuation line of active parent comment
        if current_parent_idx is not None:
            items[current_parent_idx]["source_span"]["end_line"] = line_no
            items[current_parent_idx]["full_text"] = (items[current_parent_idx]["full_text"] + "\n" + stripped).strip()
            if len(items[current_parent_idx]["excerpt"]) < 60:
                items[current_parent_idx]["excerpt"] = items[current_parent_idx]["full_text"][:180]
            saw_blank_since_parent = False

    _flush_parent()

    # Fallback if document had zero items (pure prose without headers)
    if not items:
        paras = [p.strip() for p in re.split(r"\n\s*\n", comments_txt) if len(p.strip()) >= 15]
        for idx_p, p in enumerate(paras, 1):
            if REQUEST_SIGNAL_RE.search(p):
                raw_uid = f"AUTO-{idx_p}"
                rev, loc, ckey, disp = decompose_comment_identity(raw_uid, "G", round_id)
                cat, sev, ext = _classify_comment_meta(p)
                items.append({
                    "id": disp, "display_id": disp, "raw_id": raw_uid, "comment_key": ckey,
                    "canonical_id": canonicalize_id(disp), "round": round_id, "reviewer_id": rev,
                    "local_comment_id": loc, "parent_id": None, "parent_key": None, "sub_label": None,
                    "is_parent": False, "is_atomic_subitem": False, "is_unassigned": True,
                    "needs_review_queue": True, "sub_item_ids": [], "id_source": "heuristic",
                    "source_span": {"start_line": 1, "end_line": len(lines)},
                    "category": cat, "severity": sev, "requires_external_evidence_hint": ext,
                    "excerpt": p[:180], "full_text": p,
                })

    # Independent underflow self-check on line-starting explicit headers (excludes inline cross-refs like 'see R1.1')
    header_keys: set[str] = set()
    scan_rev: str | None = None
    for raw_line in lines:
        cm = COMPOSITE_HEADER_RE.match(raw_line)
        if cm:
            scan_rev = f"R{cm.group(2)}"
            _, _, ck, _ = decompose_comment_identity(cm.group(1), scan_rev, round_id)
            header_keys.add(ck)
            continue
        hm = HEADER_ID_RE.match(raw_line)
        if hm:
            r_inf, _, ck, _ = decompose_comment_identity(hm.group(1), scan_rev, round_id)
            if r_inf != "G":
                scan_rev = r_inf
            header_keys.add(ck)
            continue
        rsec = REVIEWER_SECTION_RE.match(raw_line)
        if rsec:
            rnum = rsec.group(1) or rsec.group(2)
            scan_rev = f"R{rnum}" if rnum else ("E" if rsec.group(3) else scan_rev)

    parsed_keys = {it["comment_key"] for it in items}
    underflow = sorted(header_keys - parsed_keys)
    return items, underflow


def split_response_sections(resp_txt: str, expected_items: list[dict[str, Any]], round_id: str = "R1") -> dict[str, dict[str, Any]]:
    """Split response letter keyed by composite comment_key (round, reviewer_id, local_comment_id)."""
    lines = resp_txt.splitlines()
    raw_blocks: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    current_reviewer: str | None = None

    for idx, line in enumerate(lines, 1):
        # Composite comment header first
        cm = COMPOSITE_HEADER_RE.match(line)
        if cm:
            if cur is not None:
                cur["end_line"] = idx - 1
                raw_blocks.append(cur)
            raw_cid = re.sub(r"\s+", " ", cm.group(1).strip())
            current_reviewer = f"R{cm.group(2)}"
            rev, loc, ckey, disp = decompose_comment_identity(raw_cid, current_reviewer, round_id)
            cur = {
                "id": disp, "raw_id": raw_cid, "comment_key": ckey, "canonical_id": canonicalize_id(disp),
                "reviewer_id": rev, "local_comment_id": loc, "start_line": idx, "end_line": idx, "lines": [line],
            }
            continue

        # Explicit comment header second
        hm = HEADER_ID_RE.match(line)
        if hm:
            if cur is not None:
                cur["end_line"] = idx - 1
                raw_blocks.append(cur)
            raw_cid = re.sub(r"\s+", " ", hm.group(1).strip())
            rev, loc, ckey, disp = decompose_comment_identity(raw_cid, current_reviewer, round_id)
            if rev != "G":
                current_reviewer = rev
            cur = {
                "id": disp, "raw_id": raw_cid, "comment_key": ckey, "canonical_id": canonicalize_id(disp),
                "reviewer_id": rev, "local_comment_id": loc, "start_line": idx, "end_line": idx, "lines": [line],
            }
            continue

        # Pure reviewer section header terminates active block and switches reviewer scope
        rsec = REVIEWER_SECTION_RE.match(line)
        if rsec:
            if cur is not None:
                cur["end_line"] = idx - 1
                raw_blocks.append(cur)
                cur = None
            rnum = rsec.group(1) or rsec.group(2)
            if rnum:
                current_reviewer = f"R{rnum}"
            elif rsec.group(3):
                current_reviewer = "E"
            continue

        if cur is not None:
            cur["lines"].append(line)
            cur["end_line"] = idx
    if cur is not None:
        raw_blocks.append(cur)

    by_key: dict[str, dict[str, Any]] = {}
    for blk in raw_blocks:
        blk["text"] = "\n".join(blk["lines"])
        ck = blk["comment_key"]
        if ck not in by_key:
            by_key[ck] = blk
        else:
            by_key[ck]["lines"].extend(blk["lines"])
            by_key[ck]["text"] = "\n".join(by_key[ck]["lines"])
            by_key[ck]["end_line"] = blk["end_line"]

    # Map sub-items inside parent blocks
    for item in expected_items:
        ck = item["comment_key"]
        if ck in by_key:
            continue
        p_key = item.get("parent_key")
        sub_lbl = item.get("sub_label")
        if p_key and sub_lbl and p_key in by_key:
            p_blk = by_key[p_key]
            parent_disp = item.get("parent_id", "")
            sub_pat = re.compile(
                rf"(?:^|\n)\s*(?:[-*]\s*)?(?:\*{2})?(?:\({re.escape(sub_lbl)}\)|\[{re.escape(sub_lbl)}\]|{re.escape(sub_lbl)}\)|{re.escape(parent_disp)}\s*[\(\[]?{re.escape(sub_lbl)}[\)\]]?)",
                re.I,
            )
            m = sub_pat.search(p_blk["text"])
            if m:
                tail = p_blk["text"][m.start():]
                next_m = re.search(r"\n\s*(?:[-*]\s*)?(?:\*{2})?(?:\([a-z1-9]\)|\[s\d+\]|[a-z]\))", tail[1:], re.I)
                sub_text = tail[: next_m.start() + 1] if next_m else tail
                by_key[ck] = {
                    "id": item["id"], "comment_key": ck, "canonical_id": item["canonical_id"],
                    "start_line": p_blk["start_line"], "end_line": p_blk["end_line"],
                    "lines": sub_text.splitlines(), "text": sub_text,
                    "inherited_parent_text": p_blk["text"],
                }
            elif item.get("id_source") == "multi_request_split":
                # Verify parent block actually addresses this specific atomic sub-request via content token overlap
                req_toks = set(_substantive_tokens(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", item.get("full_text", ""))))
                resp_toks = set(_substantive_tokens(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", p_blk["text"])))
                overlap = req_toks & resp_toks
                if len(overlap) >= min(2, max(1, len(req_toks))):
                    by_key[ck] = {
                        "id": item["id"], "comment_key": ck, "canonical_id": item["canonical_id"],
                        "start_line": p_blk["start_line"], "end_line": p_blk["end_line"],
                        "lines": p_blk["lines"], "text": p_blk["text"],
                        "inherited_parent_text": p_blk["text"],
                        "covered_via_parent_prose": True,
                        "matched_request_tokens": sorted(overlap)[:8],
                    }
    return by_key


def _substantive_tokens(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for t in tokens:
        clean = re.sub(r"^[^\w\u4e00-\u9fff%]+|[^\w\u4e00-\u9fff%]+$", "", t.lower())
        if len(clean) >= 2 and clean not in STOPWORDS:
            out.append(clean)
    return out


def build_token_diff(base_txt: str, rev_txt: str) -> dict[str, Any]:
    base_tokens = [tok for line in base_txt.splitlines() for tok in line.split()]
    rev_tokens = [(tok, ln) for ln, line in enumerate(rev_txt.splitlines(), 1) for tok in line.split()]
    rev_tok_strs = [t for t, _ in rev_tokens]
    matcher = difflib.SequenceMatcher(None, base_tokens, rev_tok_strs, autojunk=False)

    changed_lines: set[int] = set()
    touched_lines: set[int] = set()
    line_sub_tokens: dict[int, int] = {}
    line_numeric_edit: set[int] = set()
    hunks: list[dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "insert"):
            span_lines = sorted({rev_tokens[j][1] for j in range(j1, j2)})
            changed_lines.update(span_lines)
            touched_lines.update(span_lines)
            added_sub = _substantive_tokens(rev_tok_strs[j1:j2])
            removed_sub = _substantive_tokens(base_tokens[i1:i2])
            has_num_edit = any(re.search(r"\d", t) for t in rev_tok_strs[j1:j2] + base_tokens[i1:i2])
            for j in range(j1, j2):
                tok_s, ln = rev_tokens[j]
                if _substantive_tokens([tok_s]):
                    line_sub_tokens[ln] = line_sub_tokens.get(ln, 0) + 1
                if has_num_edit:
                    line_numeric_edit.add(ln)
            hunks.append({
                "op": tag, "revised_lines": span_lines,
                "substantive_changed_tokens": max(len(added_sub), len(removed_sub)),
                "has_numeric_or_identifier_edit": has_num_edit,
                "removed_excerpt": " ".join(base_tokens[i1:i2])[:140],
                "added_excerpt": " ".join(rev_tok_strs[j1:j2])[:140],
            })
        elif tag == "delete":
            adj_line = rev_tokens[j1][1] if j1 < len(rev_tokens) else (rev_tokens[-1][1] if rev_tokens else 1)
            touched_lines.add(adj_line)
            rem_sub = _substantive_tokens(base_tokens[i1:i2])
            line_sub_tokens[adj_line] = line_sub_tokens.get(adj_line, 0) + len(rem_sub)
            if any(re.search(r"\d", t) for t in base_tokens[i1:i2]):
                line_numeric_edit.add(adj_line)
            hunks.append({
                "op": "delete", "revised_lines": [adj_line],
                "substantive_changed_tokens": len(rem_sub),
                "has_numeric_or_identifier_edit": any(re.search(r"\d", t) for t in base_tokens[i1:i2]),
                "removed_excerpt": " ".join(base_tokens[i1:i2])[:140], "added_excerpt": "",
            })

    unified = list(difflib.unified_diff(base_txt.splitlines(), rev_txt.splitlines(), fromfile="baseline", tofile="revised", lineterm=""))
    return {
        "has_substantive_edits": len(hunks) > 0,
        "changed_revised_lines": sorted(changed_lines),
        "touched_revised_lines": sorted(touched_lines),
        "line_substantive_tokens": line_sub_tokens,
        "line_numeric_edit": sorted(line_numeric_edit),
        "token_hunks": hunks,
        "unified_diff_lines": len(unified),
    }


def build_revised_index(rev_txt: str) -> dict[str, Any]:
    rev_lines = rev_txt.splitlines()
    norm_chars: list[str] = []
    char_lines: list[int] = []
    for ln, line in enumerate(rev_lines, 1):
        for tok in line.split():
            if norm_chars:
                norm_chars.append(" ")
                char_lines.append(ln)
            for ch in tok:
                norm_chars.append(ch)
                char_lines.append(ln)
    raw_headings = [(hm.group(2).strip(), ln) for ln, line in enumerate(rev_lines, 1) if (hm := HEADING_RE.match(line))]
    sections = []
    for idx, (title, start_ln) in enumerate(raw_headings):
        end_ln = raw_headings[idx + 1][1] - 1 if idx + 1 < len(raw_headings) else len(rev_lines)
        sections.append({"title": title, "norm_title": re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.lower()), "start_line": start_ln, "end_line": end_ln})
    return {"lines": rev_lines, "line_count": len(rev_lines), "norm_text": "".join(norm_chars), "char_lines": char_lines, "sections": sections}


def locate_quote_spans(quote_norm: str, rev_index: dict[str, Any]) -> list[tuple[int, int]]:
    norm_text, char_lines = rev_index["norm_text"], rev_index["char_lines"]
    spans: list[tuple[int, int]] = []
    if not quote_norm:
        return spans
    pos = 0
    while (idx := norm_text.find(quote_norm, pos)) != -1:
        spans.append((char_lines[idx], char_lines[idx + len(quote_norm) - 1]))
        pos = idx + 1
    return spans


def extract_anchors(block_text: str) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for m in ANCHOR_BRACKET_RE.finditer(block_text):
        inner = m.group(1).strip()
        lm, sm = LINE_SPEC_RE.search(inner), SECTION_SPEC_RE.search(inner)
        if not lm and not sm:
            continue
        start_ln = end_ln = None
        if lm:
            s_str = lm.group(1) or lm.group(3)
            e_str = lm.group(2) or lm.group(4) or s_str
            start_ln, end_ln = int(s_str), int(e_str)
        anchors.append({"raw": m.group(0), "section": sm.group(1).strip() if sm else None, "start_line": start_ln, "end_line": end_ln})
    return anchors


def extract_manuscript_quotes(block_lines: list[str], comments_norm: str, base_norm: str, rev_norm: str) -> tuple[list[str], list[str]]:
    quotes: list[str] = []
    skipped_meta: list[str] = []
    bq_buf: list[str] = []
    prev_was_meta = False

    def _flush_bq() -> None:
        nonlocal bq_buf
        if not bq_buf:
            return
        raw_items = [b.strip("*\"'「」“” ") for b in bq_buf if b.strip()]
        joined = " ".join(" ".join(raw_items).split())
        bq_buf = []
        if len(joined) < 10:
            return
        if prev_was_meta or (joined in comments_norm and joined not in rev_norm):
            skipped_meta.append(joined[:100])
            return
        if joined in rev_norm or len(raw_items) == 1:
            quotes.append(joined)
            return
        split_parts = [" ".join(part.split()) for part in raw_items if len(" ".join(part.split())) >= 10]
        if split_parts and all(part in rev_norm for part in split_parts):
            quotes.extend(split_parts)
        else:
            quotes.append(joined)

    for line in block_lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            content = re.sub(r"^>+\s*", "", stripped)
            if BEFORE_OR_REVIEWER_LINE_RE.match(content):
                _flush_bq()
                prev_was_meta = True
                continue
            bq_buf.append(content)
            continue
        _flush_bq()
        if BEFORE_OR_REVIEWER_LINE_RE.match(stripped):
            prev_was_meta = True
            continue
        if stripped:
            prev_was_meta = False
        for qm in INLINE_QUOTE_RE.finditer(stripped):
            q = " ".join((qm.group(1) or qm.group(2) or qm.group(3) or "").strip("*\"'「」“” ").split())
            if len(q) < 10:
                continue
            if q in comments_norm and q not in rev_norm:
                skipped_meta.append(q[:100])
                continue
            prefix = stripped[: qm.start()].lower()
            if any(k in prefix[-35:] for k in ("original:", "before:", "previously", "from ", "修改前", "原文", "由")) and q in base_norm and q not in rev_norm:
                skipped_meta.append(q[:100])
                continue
            if q not in quotes:
                quotes.append(q)
    _flush_bq()
    return quotes, skipped_meta


def classify_tone_tier(disposition: str | None, block_text: str) -> str:
    low = block_text.lower()
    if re.search(r"\bappeal\b|申诉|editorial\s+adjudication", low):
        return "APPEAL"
    if disposition == "DISAGREE_WITH_EVIDENCE":
        return "POLITE_DISAGREE"
    if disposition == "CLARIFY_EXISTING":
        return "CLARIFY"
    if disposition in ("PARTIAL", "AUTHOR_INPUT_NEEDED"):
        return "POLITE_DISAGREE" if re.search(r"respectfully\s+disagree|outside\s+the\s+scope|beyond\s+the\s+scope|超出(?:当前)?范围|无法补做", low) else "CLARIFY"
    return "ACCEPT"


def parse_missing_evidence_sections(me_txt: str, needed_items: list[dict[str, Any]]) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    cur_keys: list[str] = []
    for line in me_txt.splitlines():
        found = [canonicalize_id(m.group(1)) for m in COMMENT_ID_RE.finditer(line)]
        if bool(re.match(r"^\s*(?:#{1,6}\s+|[-*]\s+|\|\s*)", line)) and found:
            cur_keys = list(dict.fromkeys(found))
            for k in cur_keys:
                blocks.setdefault(k, []).append(line)
        elif cur_keys:
            for k in cur_keys:
                blocks[k].append(line)
    result: dict[str, str] = {}
    for item in needed_items:
        ck = item["comment_key"]
        cid = item["id"]
        c_disp = canonicalize_id(cid)
        c_raw = canonicalize_id(item.get("raw_id", cid))
        if c_disp in blocks:
            result[ck] = "\n".join(blocks[c_disp])
        elif c_raw in blocks:
            result[ck] = "\n".join(blocks[c_raw])
        elif re.search(re.escape(cid), me_txt, re.I):
            result[ck] = me_txt
    return result


def extract_review_items_from_input(
    input_path: Path,
    work_dir: Path,
    comments_override: Path | None = None,
    baseline_override: Path | None = None,
    round_id: str = "R1",
) -> dict[str, Any]:
    """Auto-discover manuscript baseline and reviewer comments under /input (PDF/DOCX/MD/TXT) and extract review_items.json."""
    work_dir.mkdir(parents=True, exist_ok=True)
    supported = {".pdf", ".docx", ".md", ".txt"}
    skip = {"case.json", "task.md"}
    all_files = [input_path] if input_path.is_file() else [
        p for p in sorted(input_path.rglob("*")) if p.is_file() and p.suffix.lower() in supported and p.name.lower() not in skip
    ]
    c_file = comments_override
    b_file = baseline_override
    if c_file is None:
        c_file = next((p for p in all_files if re.search(r"comment|review|referee|decision|审稿|意见", p.name, re.I)), None)
    if b_file is None:
        b_file = next((p for p in all_files if p != c_file and re.search(r"manuscript|baseline|paper|draft|article|原稿|基线", p.name, re.I)), None)
    if b_file is None and all_files:
        b_file = next((p for p in all_files if p != c_file), all_files[0])
    if c_file is None and len(all_files) >= 2:
        c_file = next((p for p in all_files if p != b_file), None)
    if c_file is None:
        raise FileNotFoundError(f"Could not locate reviewer comments file under {input_path}")

    c_doc = extract_document(c_file, fmt="auto")
    items, underflow = parse_comments_structure(c_doc["text"], round_id=round_id)
    (work_dir / "comments_extracted.md").write_text(c_doc["text"] + "\n", encoding="utf-8")

    b_meta: dict[str, Any] = {}
    baseline_empty = False
    if b_file and b_file.is_file():
        b_doc = extract_document(b_file, fmt="auto")
        (work_dir / "baseline_extracted.md").write_text(b_doc["text"] + "\n", encoding="utf-8")
        baseline_empty = b_doc.get("extraction_status") in ("empty_or_scanned", "empty", "error")
        b_meta = {
            "baseline_file": str(b_file), "format": b_doc["format"], "engine": b_doc["engine"],
            "extraction_status": b_doc.get("extraction_status", "ok"),
            "low_confidence_fallback": b_doc.get("low_confidence_fallback", False),
            "blank_pages": b_doc.get("blank_pages", []),
            "pages_count": b_doc["pages_count"], "line_count": len(b_doc["text"].splitlines()),
            "dois_found": sorted(set(DOI_RE.findall(b_doc["text"]))),
            "stat_tokens_count": len(STAT_NUM_RE.findall(b_doc["text"])),
            "warning": b_doc.get("warning", ""),
        }

    parent_items = [it for it in items if not it.get("is_atomic_subitem")]
    atomic_subitems = [it for it in items if it.get("is_atomic_subitem")]
    unassigned_requests = [it for it in items if it.get("is_unassigned")]
    comments_empty = c_doc.get("extraction_status") in ("empty_or_scanned", "empty", "error")

    out_payload = {
        "ok": len(items) > 0 and not underflow and not comments_empty and not baseline_empty,
        "round": round_id,
        "comments_file": str(c_file),
        "comments_extraction": {k: v for k, v in c_doc.items() if k != "text"},
        "baseline_extraction": b_meta,
        "parent_items_total": len(parent_items),
        "atomic_subitems_total": len(atomic_subitems),
        "unassigned_requests_total": len(unassigned_requests),
        "items_count": len(items),
        "parse_underflow_keys": underflow,
        "unassigned_requests": unassigned_requests,
        "items": items,
        "outputs": {
            "review_items_json": str(work_dir / "review_items.json"),
            "comments_extracted": str(work_dir / "comments_extracted.md"),
            "baseline_extracted": str(work_dir / "baseline_extracted.md") if b_meta else "",
        },
    }
    (work_dir / "review_items.json").write_text(json.dumps(out_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_payload


def extract_revised_anchors(baseline_path: Path, revised_path: Path) -> dict[str, Any]:
    """Extract deterministic [§Section Lstart-Lend] anchors + quote_sha1 from baseline->revised diff."""
    base_doc = extract_document(baseline_path, fmt="auto")
    rev_doc = extract_document(revised_path, fmt="auto")
    diff_info = build_token_diff(base_doc["text"], rev_doc["text"])
    rev_idx = build_revised_index(rev_doc["text"])
    rev_lines, sections = rev_idx["lines"], rev_idx["sections"]

    def _sec(ln: int) -> str:
        return next((s["title"] for s in sections if s["start_line"] <= ln <= s["end_line"]), "Document")

    anchors = []
    for idx, hunk in enumerate(diff_info["token_hunks"], 1):
        span = hunk.get("revised_lines") or []
        if not span:
            continue
        l_start, l_end = min(span), max(span)
        sec_title = _sec(l_start)
        loc_tag = f"[§{sec_title} L{l_start}]" if l_start == l_end else f"[§{sec_title} L{l_start}-L{l_end}]"
        verbatim = "\n".join(rev_lines[l_start - 1 : l_end]).strip()
        anchors.append({
            "hunk_id": f"H{idx}", "op": hunk["op"], "section": sec_title,
            "start_line": l_start, "end_line": l_end, "anchor_tag": loc_tag,
            "quote_sha1": quote_sha1(verbatim),
            "substantive_changed_tokens": hunk["substantive_changed_tokens"],
            "verbatim_revised_excerpt": verbatim[:1200],
            "added_excerpt": hunk.get("added_excerpt", ""),
            "removed_excerpt": hunk.get("removed_excerpt", ""),
        })
    return {
        "has_substantive_edits": diff_info["has_substantive_edits"],
        "revised_line_count": rev_idx["line_count"],
        "changed_revised_lines": diff_info["changed_revised_lines"],
        "anchors": anchors,
    }


def reanchor_response_letter(revised_path: Path, response_path: Path, out_response: Path | None = None) -> dict[str, Any]:
    """Recompute drifted [§Section Lstart-Lend] anchors in response-letter.md using verbatim quote matching."""
    rev_doc = extract_document(revised_path, fmt="auto")
    resp_doc = extract_document(response_path, fmt="auto")
    rev_idx = build_revised_index(rev_doc["text"])
    sections = rev_idx["sections"]

    def _sec(ln: int) -> str:
        return next((s["title"] for s in sections if s["start_line"] <= ln <= s["end_line"]), "Document")

    resp_lines = resp_doc["text"].splitlines()
    hdr_positions = [i for i, ln in enumerate(resp_lines) if COMPOSITE_HEADER_RE.match(ln) or HEADER_ID_RE.match(ln)] + [len(resp_lines)]
    reanchored: list[dict[str, Any]] = []
    if len(hdr_positions) <= 1:
        hdr_positions = [0, len(resp_lines)]

    updated_lines = list(resp_lines)
    for start_i, end_i in zip(hdr_positions[:-1], hdr_positions[1:]):
        blk_lines = updated_lines[start_i:end_i]
        quotes, _ = extract_manuscript_quotes(blk_lines, "", "", rev_idx["norm_text"])
        if not quotes:
            continue
        q_targets: list[tuple[int, int, str, str, str]] = []
        for q in quotes:
            spans = locate_quote_spans(q, rev_idx)
            if spans:
                s_ln, e_ln = spans[0]
                sec = _sec(s_ln)
                tag = f"[§{sec} L{s_ln}]" if s_ln == e_ln else f"[§{sec} L{s_ln}-L{e_ln}]"
                q_targets.append((s_ln, e_ln, sec, tag, quote_sha1(q)))
        if not q_targets:
            continue
        for rel_i in range(len(blk_lines)):
            ln_txt = blk_lines[rel_i]
            ancs = extract_anchors(ln_txt)
            if not ancs:
                continue
            for anc in ancs:
                if anc["start_line"] is None:
                    continue
                # Pick matching target by section name first, otherwise closest line
                anc_sec_norm = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (anc["section"] or "").lower())
                matched = [
                    t for t in q_targets
                    if anc_sec_norm and anc_sec_norm in re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", t[2].lower())
                ]
                cand_list = matched or q_targets
                best = min(cand_list, key=lambda t: abs(t[0] - anc["start_line"]))
                s_ln, e_ln, _, new_tag, qsha = best
                if anc["start_line"] != s_ln or anc["end_line"] != e_ln:
                    blk_lines[rel_i] = blk_lines[rel_i].replace(anc["raw"], new_tag, 1)
                    reanchored.append({
                        "line_in_response": start_i + rel_i + 1,
                        "old_anchor": anc["raw"], "new_anchor": new_tag,
                        "quote_sha1": qsha,
                    })
        updated_lines[start_i:end_i] = blk_lines

    if out_response is not None:
        out_response.parent.mkdir(parents=True, exist_ok=True)
        out_response.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    return {"ok": True, "reanchored_count": len(reanchored), "updates": reanchored}


def audit_rebuttal_closure(
    baseline_path: Path,
    comments_path: Path,
    revised_path: Path,
    response_path: Path,
    missing_evidence_path: Path | None = None,
    previous_ledger_path: Path | None = None,
    *,
    case_id: str = "",
    round_id: str = "R1",
    min_substantive_tokens: int = 3,
    allow_shared_anchor: bool = False,
) -> dict[str, Any]:
    base_doc = extract_document(baseline_path, fmt="auto")
    comments_doc = extract_document(comments_path, fmt="auto")
    rev_doc = extract_document(revised_path, fmt="auto")
    resp_doc = extract_document(response_path, fmt="auto")
    base_txt, comments_txt, rev_txt, resp_txt = base_doc["text"], comments_doc["text"], rev_doc["text"], resp_doc["text"]

    me_file = missing_evidence_path
    if me_file is None and (response_path.parent / "missing_evidence_list.md").is_file():
        me_file = response_path.parent / "missing_evidence_list.md"
    me_txt = extract_document(me_file, fmt="auto")["text"] if (me_file and me_file.is_file()) else ""

    expected_items, parse_underflow = parse_comments_structure(comments_txt, round_id=round_id)
    expected_ids = [it["id"] for it in expected_items]
    resp_sections = split_response_sections(resp_txt, expected_items, round_id=round_id)

    diff_info = build_token_diff(base_txt, rev_txt)
    rev_index = build_revised_index(rev_txt)
    base_index = build_revised_index(base_txt)
    touched_set = set(diff_info["touched_revised_lines"])
    changed_set = set(diff_info["changed_revised_lines"])
    line_sub_tokens: dict[int, int] = diff_info["line_substantive_tokens"]
    line_numeric_edit: set[int] = set(diff_info.get("line_numeric_edit", []))

    comments_norm = " ".join(comments_txt.split())
    base_norm = base_index["norm_text"]
    rev_norm = rev_index["norm_text"]

    missing_responses: list[str] = []
    missing_dispositions: list[str] = []
    empty_responses: list[str] = []
    unmatched_quotes: list[dict[str, Any]] = []
    invalid_line_refs: list[dict[str, Any]] = []
    invalid_section_refs: list[dict[str, Any]] = []
    anchor_quote_mismatches: list[dict[str, Any]] = []
    fake_edit_violations: list[dict[str, Any]] = []
    clarify_no_substantive_action: list[dict[str, Any]] = []
    cross_reviewer_leaks: list[dict[str, Any]] = []
    tone_alignment_warnings: list[dict[str, Any]] = []
    disposition_alias_warnings: list[str] = []

    verified_quotes_total = triple_bound_verified_count = 0
    per_comment_ledger: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []
    change_log: list[dict[str, Any]] = []
    missing_evidence_required_items: list[dict[str, Any]] = []

    # Track parent-to-child keys for synthesis when parent header omitted but all children answered
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for it in expected_items:
        if it.get("parent_key"):
            children_by_parent.setdefault(it["parent_key"], []).append(it)

    for item in expected_items:
        cid, c_key = item["id"], item["comment_key"]
        blk = resp_sections.get(c_key)

        if blk is None and item.get("is_parent") and c_key in children_by_parent:
            child_items = children_by_parent[c_key]
            child_blks = [resp_sections.get(ch["comment_key"]) for ch in child_items]
            if child_blks and all(cb is not None for cb in child_blks):
                merged_lines = [ln for cb in child_blks if cb for ln in cb["lines"]]
                blk = {
                    "id": cid, "comment_key": c_key, "canonical_id": item["canonical_id"],
                    "start_line": child_blks[0]["start_line"], "end_line": child_blks[-1]["end_line"],
                    "lines": merged_lines, "text": "\n".join(merged_lines), "covered_via_children": True,
                }

        if blk is None:
            missing_responses.append(cid)
            per_comment_ledger.append({
                "comment_id": cid, "comment_key": c_key, "reviewer_id": item["reviewer_id"],
                "local_comment_id": item["local_comment_id"], "source_span": item.get("source_span"),
                "answered": False, "disposition": None, "diff_verified": False,
                "is_parent": bool(item.get("is_parent")), "is_atomic_subitem": bool(item.get("is_atomic_subitem")),
                "is_unassigned": bool(item.get("is_unassigned")), "parent_id": item.get("parent_id"),
            })
            continue

        btxt, blines = blk["text"], blk["lines"]

        # 1. Explicit disposition
        dm = DISPOSITION_RE.search(btxt) or (DISPOSITION_RE.search(blk["inherited_parent_text"]) if "inherited_parent_text" in blk else None)
        raw_disp = dm.group(1) if dm else None
        if raw_disp is None:
            disposition = None
            missing_dispositions.append(cid)
        elif raw_disp in DISPOSITION_ALIASES:
            disposition = DISPOSITION_ALIASES[raw_disp]
            disposition_alias_warnings.append(f"{cid}: alias '{raw_disp}' normalized to '{disposition}'")
        else:
            disposition = raw_disp

        # 2. Empty / thanks-only check
        prose_lines = []
        for ln in blines[1:]:
            s = ln.strip()
            if not s or s.startswith(">") or BEFORE_OR_REVIEWER_LINE_RE.match(s) or re.match(r"^\s*[-*]?\s*\*{0,2}(?:Disposition|Decision|Status|Tone|处置|决策|语气)\*{0,2}\s*[:：]", s, re.I):
                continue
            s = re.sub(r"^\s*[-*]?\s*\*{0,2}(?:Response|Reply|Answer|Author\s+Response|回复|答复)\*{0,2}\s*[:：]\s*", "", s, flags=re.I)
            if s:
                prose_lines.append(s)
        prose_clean = re.sub(r"\b(?:" + "|".join(CANONICAL_DISPOSITIONS) + r"|CLARIFY|DISAGREE)\b", "", " ".join(prose_lines)).strip()
        prose_no_thanks = re.sub(r"(?:we\s+thank\s+the\s+reviewer[^.\n]*\.?|thank\s+you\s+for[^.\n]*\.?|we\s+have\s+revised\s+accordingly\.?|感谢审稿人[^。\n]*。?|已按意见修改。?)", "", prose_clean, flags=re.I).strip()
        if not blk.get("covered_via_children") and not blk.get("covered_via_parent_prose") and (len(prose_no_thanks) < 12 or EMPTY_FILLER_RE.match(prose_clean)):
            empty_responses.append(cid)

        # 3. Location anchors
        anchors = extract_anchors(btxt)
        valid_line_anchors: list[tuple[int, int, str]] = []
        for anc in anchors:
            s_ln, e_ln, sec_name = anc["start_line"], anc["end_line"], anc["section"]
            if s_ln is not None and e_ln is not None:
                if s_ln < 1 or e_ln < 1 or s_ln > e_ln or e_ln > rev_index["line_count"]:
                    invalid_line_refs.append({"comment_id": cid, "anchor": anc["raw"], "reason": f"Line range {s_ln}-{e_ln} out of revised bounds (1..{rev_index['line_count']})"})
                else:
                    valid_line_anchors.append((s_ln, e_ln, anc["raw"]))
            if sec_name:
                norm_sec = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", sec_name.lower())
                matched_secs = [s for s in rev_index["sections"] if norm_sec and (norm_sec in s["norm_title"] or s["norm_title"] in norm_sec)]
                if not matched_secs:
                    invalid_section_refs.append({"comment_id": cid, "anchor": anc["raw"], "reason": f"Section '{sec_name}' not found in revised headings"})
                elif s_ln is not None and e_ln is not None and e_ln <= rev_index["line_count"]:
                    if not any(s["start_line"] <= e_ln and s_ln <= s["end_line"] for s in matched_secs):
                        invalid_section_refs.append({"comment_id": cid, "anchor": anc["raw"], "reason": f"Lines L{s_ln}-L{e_ln} fall outside section '{matched_secs[0]['title']}'"})

        # 4. Verbatim quotes + anchor positional binding + diff overlap + quote_sha1
        cand_quotes, _ = extract_manuscript_quotes(blines, comments_norm, base_norm, rev_norm)
        verified_quote_records: list[dict[str, Any]] = []
        for q in cand_quotes:
            spans = locate_quote_spans(q, rev_index)
            if not spans:
                unmatched_quotes.append({"comment_id": cid, "quote": q[:160], "reason": "Quoted excerpt not found verbatim in revised manuscript"})
                continue
            verified_quotes_total += 1
            overlapping_changed = [sp for sp in spans if any(ln in touched_set for ln in range(sp[0], sp[1] + 1))]
            sub_tok_count = max((sum(line_sub_tokens.get(ln, 0) for ln in range(sp[0], sp[1] + 1)) for sp in spans), default=0)
            has_num_edit = any(ln in line_numeric_edit for sp in spans for ln in range(sp[0], sp[1] + 1))
            anchor_matched = True
            if valid_line_anchors:
                anchor_matched = any(sp[0] <= a_end and a_start <= sp[1] for sp in spans for (a_start, a_end, _) in valid_line_anchors)
                if not anchor_matched:
                    anchor_quote_mismatches.append({
                        "comment_id": cid, "quote": q[:120], "actual_lines": [f"L{s}-L{e}" for s, e in spans],
                        "claimed_anchors": [raw for _, _, raw in valid_line_anchors],
                        "reason": "Verbatim quote exists in revised manuscript, but NOT inside claimed line anchor span",
                    })
            verified_quote_records.append({
                "quote": q[:140], "quote_sha1": quote_sha1(q), "actual_spans": spans,
                "overlaps_diff": len(overlapping_changed) > 0, "substantive_diff_tokens": sub_tok_count,
                "has_numeric_edit": has_num_edit, "anchor_matched": anchor_matched,
            })

        # 5. Substantive diff binding + CLARIFY substantive action gate
        claims_edit = disposition in ("ACCEPT_TEXT", "SOFTEN_CLAIM") or (disposition == "PARTIAL" and bool(EDIT_CLAIM_PROSE_RE.search(btxt)))
        anchor_overlaps_diff = any(any(ln in touched_set for ln in range(a_s, a_e + 1)) for (a_s, a_e, _) in valid_line_anchors)
        anchor_sub_tokens = max((sum(line_sub_tokens.get(ln, 0) for ln in range(a_s, a_e + 1)) for (a_s, a_e, _) in valid_line_anchors), default=0)
        anchor_num_edit = any(ln in line_numeric_edit for (a_s, a_e, _) in valid_line_anchors for ln in range(a_s, a_e + 1))
        quote_overlaps_diff = any(qr["overlaps_diff"] for qr in verified_quote_records)
        quote_sub_tokens = max((qr["substantive_diff_tokens"] for qr in verified_quote_records), default=0)
        quote_num_edit = any(qr["has_numeric_edit"] for qr in verified_quote_records)
        quote_anchor_ok = all(qr["anchor_matched"] for qr in verified_quote_records)
        best_sub_tokens = max(quote_sub_tokens, anchor_sub_tokens)
        exact_numeric_fix = (quote_num_edit or anchor_num_edit) and best_sub_tokens >= 1

        no_edit_just_match = NO_EDIT_JUSTIFICATION_RE.search(btxt)
        no_edit_justification = no_edit_just_match.group(1).strip() if no_edit_just_match else ""

        diff_verified = False
        if claims_edit and not blk.get("covered_via_children"):
            if not verified_quote_records and not valid_line_anchors:
                fake_edit_violations.append({"comment_id": cid, "disposition": disposition, "reason": "Claims edit but provides neither verbatim revised quote nor valid line anchor"})
            elif verified_quote_records and not quote_overlaps_diff:
                fake_edit_violations.append({"comment_id": cid, "disposition": disposition, "reason": "Quoted passage is unchanged baseline text with zero baseline->revised diff overlap"})
            elif not verified_quote_records and valid_line_anchors and not anchor_overlaps_diff:
                fake_edit_violations.append({"comment_id": cid, "disposition": disposition, "reason": "Claimed anchor span has zero substantive change in baseline->revised diff"})
            elif best_sub_tokens < min_substantive_tokens and not exact_numeric_fix:
                fake_edit_violations.append({"comment_id": cid, "disposition": disposition, "reason": f"Trivial edit below minimum substantive diff quota ({best_sub_tokens} < {min_substantive_tokens} non-stopword tokens)"})
            elif quote_anchor_ok and (quote_overlaps_diff or anchor_overlaps_diff):
                diff_verified = True
                triple_bound_verified_count += 1
        elif disposition in ("CLARIFY_EXISTING", "DISAGREE_WITH_EVIDENCE") and not blk.get("covered_via_children"):
            # Gate: either genuine manuscript diff OR (verifiable quote/anchor + explicit no_manuscript_edit_justification)
            has_manuscript_diff = (quote_overlaps_diff or anchor_overlaps_diff) and best_sub_tokens >= 1 and quote_anchor_ok
            has_grounded_clarification = (bool(verified_quote_records) or bool(valid_line_anchors)) and len(prose_no_thanks) >= 25
            has_explicit_no_edit_reason = len(no_edit_justification) >= 15
            if has_manuscript_diff:
                diff_verified = True
                triple_bound_verified_count += 1
            elif has_grounded_clarification and has_explicit_no_edit_reason:
                diff_verified = True
            else:
                reason_parts = []
                if not has_manuscript_diff:
                    reason_parts.append("no substantive manuscript diff (line+token diff)")
                if not has_grounded_clarification:
                    reason_parts.append("missing verifiable verbatim quote/line anchor")
                if not has_explicit_no_edit_reason:
                    reason_parts.append("missing explicit no_manuscript_edit_justification")
                clarify_no_substantive_action.append({
                    "code": "CLARIFY_NO_SUBSTANTIVE_ACTION",
                    "comment_id": cid,
                    "disposition": disposition,
                    "reason": f"{disposition} failed substantive action gate: {'; '.join(reason_parts)}",
                })
                diff_verified = False
        else:
            diff_verified = True

        # 6. Cross-reviewer isolation
        own_group = reviewer_group_of(cid, item.get("reviewer_id"))
        if GENERIC_CROSS_REVIEWER_RE.search(btxt):
            cross_reviewer_leaks.append({"comment_id": cid, "reason": "Cross-reviewer phrasing detected", "match": GENERIC_CROSS_REVIEWER_RE.search(btxt).group(0)})  # type: ignore[union-attr]
        elif own_group and own_group.startswith(("r", "审稿人")):
            own_num = re.sub(r"\D+", "", own_group)
            for ref_m in re.finditer(r"\b(?:Reviewer\s*#?\s*(\d+)|R(\d+)[\.:]\w+)\b|审稿人\s*(\d+)", btxt, re.I):
                other_num = ref_m.group(1) or ref_m.group(2) or ref_m.group(3)
                if other_num and other_num != own_num:
                    cross_reviewer_leaks.append({"comment_id": cid, "reason": f"Reply to {own_group} references another reviewer ({ref_m.group(0)})", "match": ref_m.group(0)})
                    break

        # 7. Tone & missing-evidence tracking
        tone_tier = classify_tone_tier(disposition, btxt)
        if disposition == "DISAGREE_WITH_EVIDENCE" and not re.search(r"respectfully|evidence|scope|data|table|figure|methods|依据|证据|范围|审慎|不同意|商榷", btxt, re.I):
            tone_alignment_warnings.append({"comment_id": cid, "tone_tier": tone_tier, "reason": "Cite explicit evidence/scope rationale with polite academic framing"})

        needs_me = (not blk.get("covered_via_children")) and (not item.get("is_atomic_subitem") or disposition in ("AUTHOR_INPUT_NEEDED", "PARTIAL")) and (
            disposition in ("AUTHOR_INPUT_NEEDED", "PARTIAL") or bool(UNFEASIBLE_SIGNAL_RE.search(btxt))
        )
        if needs_me and not item.get("is_atomic_subitem"):
            missing_evidence_required_items.append(item)

        linked_lines: set[int] = set()
        for qr in verified_quote_records:
            for s, e in qr["actual_spans"]:
                linked_lines.update(range(s, e + 1))
        for a_s, a_e, _ in valid_line_anchors:
            linked_lines.update(range(a_s, a_e + 1))

        per_comment_ledger.append({
            "comment_id": cid, "comment_key": c_key, "reviewer_id": item["reviewer_id"],
            "local_comment_id": item["local_comment_id"], "source_span": item.get("source_span"),
            "is_parent": bool(item.get("is_parent")), "is_atomic_subitem": bool(item.get("is_atomic_subitem")),
            "is_unassigned": bool(item.get("is_unassigned")), "parent_id": item.get("parent_id"),
            "id_source": item.get("id_source", "explicit"), "answered": True, "disposition": disposition,
            "tone_tier": tone_tier, "anchors": [a["raw"] for a in anchors],
            "verified_quotes": [qr["quote"] for qr in verified_quote_records],
            "verified_quote_hashes": [qr["quote_sha1"] for qr in verified_quote_records],
            "no_manuscript_edit_justification": no_edit_justification or None,
            "substantive_diff_tokens": best_sub_tokens, "diff_verified": diff_verified,
            "needs_missing_evidence": needs_me, "linked_revised_lines": sorted(linked_lines),
        })

    # 7b. Shared-anchor collision detection across DISTINCT top-level comments
    shared_anchor_risks: list[dict[str, Any]] = []
    top_rows = [r for r in per_comment_ledger if not r.get("is_atomic_subitem") and r.get("answered") and r.get("linked_revised_lines")]
    line_to_cids: dict[int, list[str]] = {}
    cid_to_lines: dict[str, set[int]] = {}
    for row in top_rows:
        lset = set(row["linked_revised_lines"])
        cid_to_lines[row["comment_id"]] = lset
        for ln in lset:
            line_to_cids.setdefault(ln, []).append(row["comment_id"])

    # Group collisions by pair/set of colliding comments
    seen_collision_groups: set[tuple[str, ...]] = set()
    for ln, cids in sorted(line_to_cids.items()):
        if len(cids) < 2:
            continue
        grp = tuple(sorted(set(cids)))
        if grp in seen_collision_groups:
            continue
        seen_collision_groups.add(grp)
        shared_lns = sorted(set.intersection(*(cid_to_lines[c] for c in grp)))
        full_overlap_pairs = [
            f"{a}=={b}" for i, a in enumerate(grp) for b in grp[i + 1:]
            if cid_to_lines[a] == cid_to_lines[b]
        ]
        # Blocking if two distinct comments have 100% identical anchor line sets or a comment has zero exclusive lines
        zero_exclusive = [
            c for c in grp
            if not (cid_to_lines[c] - set().union(*(cid_to_lines[o] for o in grp if o != c)))
        ]
        is_blocking = (not allow_shared_anchor) and (bool(full_overlap_pairs) or len(zero_exclusive) >= 2)
        shared_anchor_risks.append({
            "code": "SHARED_ANCHOR_RISK",
            "comment_ids": list(grp),
            "shared_lines": shared_lns,
            "full_overlap_pairs": full_overlap_pairs,
            "comments_without_exclusive_lines": zero_exclusive,
            "severity": "blocking" if is_blocking else "warning",
            "reason": (
                f"Distinct comments {list(grp)} collide on revised line(s) {shared_lns}; "
                "assign distinct per-comment revision anchors or pass --allow-shared-anchor with justification."
            ),
        })

    # 8. Missing-evidence tri-partite closure + minimum specificity anti-stuffing guard
    missing_evidence_required_ids = [it["id"] for it in missing_evidence_required_items]
    missing_evidence_gaps: list[dict[str, Any]] = []
    missing_evidence_closed: list[str] = []
    if missing_evidence_required_items:
        inline_me = ""
        m_inline = re.search(r"^#{1,3}\s*(?:Missing\s+Evidence|Unresolved\s+Evidence|未到位证据).*$", resp_txt, re.I | re.M)
        if m_inline:
            inline_me = resp_txt[m_inline.start():]
        combined_me_src = me_txt if me_txt.strip() else inline_me
        me_entries = parse_missing_evidence_sections(combined_me_src, missing_evidence_required_items)
        for item in missing_evidence_required_items:
            cid, c_key = item["id"], item["comment_key"]
            entry_txt = me_entries.get(c_key, "")
            if not entry_txt.strip():
                missing_evidence_gaps.append({"comment_id": cid, "missing_elements": ["ledger_entry", "boundary_rationale", "existing_material_alternative", "author_confirmation"], "reason": f"{cid} missing from missing_evidence_list.md"})
                continue
            check_blob = entry_txt + "\n" + resp_sections.get(c_key, {}).get("text", "")
            missing_elems = []
            if not ME_BOUNDARY_RE.search(check_blob):
                missing_elems.append("boundary_rationale")
            if not ME_ALTERNATIVE_RE.search(check_blob):
                missing_elems.append("existing_material_alternative")
            if not ME_AUTHOR_CONFIRM_RE.search(check_blob):
                missing_elems.append("author_confirmation")
            if len(entry_txt.strip()) < 80 or not ME_SPECIFICITY_RE.search(entry_txt):
                missing_elems.append("concrete_anchor_or_data_item_specificity")
            if missing_elems:
                missing_evidence_gaps.append({"comment_id": cid, "missing_elements": missing_elems, "reason": f"{cid} missing-evidence entry incomplete: {missing_elems}"})
            else:
                missing_evidence_closed.append(cid)

    # 9. Multi-round prior commitment continuity check (--previous-ledger)
    prior_commitment_unmet: list[dict[str, Any]] = []
    prior_commitments_checked = 0
    if previous_ledger_path and previous_ledger_path.is_file():
        prev = json.loads(previous_ledger_path.read_text(encoding="utf-8"))
        for prow in prev.get("per_comment_ledger", []):
            if not prow.get("diff_verified"):
                continue
            for pq in prow.get("verified_quotes", []):
                if len(pq.strip()) < 15:
                    continue
                prior_commitments_checked += 1
                pq_norm = " ".join(pq.split())
                if pq_norm not in rev_norm:
                    pcid = str(prow.get("comment_id", ""))
                    if not (pcid and re.search(rf"{re.escape(pcid)}.{{0,120}}(?:supersed|updated\s+in\s+round|re-?revised|本轮进一步修改)", resp_txt, re.I | re.S)):
                        prior_commitment_unmet.append({
                            "prior_comment_id": pcid, "lost_quote": pq[:120],
                            "reason": "Previously verified revision quote disappeared in current revised manuscript without explicit multi-round update explanation",
                        })

    # 10. Fabrication, overpromise, rebuke, AI style, and baseline DOI/numeric preservation audit
    combined_out_txt = resp_txt + ("\n" + me_txt if me_txt else "")
    fabrication_hits = [m.group(0) for m in FABRICATION_RE.finditer(combined_out_txt)]
    overpromise_hits = [m.group(0) for m in OVERPROMISE_RE.finditer(combined_out_txt)]
    rebuke_hits = [m.group(0) for m in REBUKE_RE.finditer(resp_txt)]
    style_warnings = [
        {"line": ln_no, "category": rname, "match": m.group(0), "suggestion": adv}
        for ln_no, line in enumerate(resp_txt.splitlines(), 1)
        if not (line.strip().startswith(">") or BEFORE_OR_REVIEWER_LINE_RE.match(line.strip()))
        for rname, rx, adv in AI_STYLE_RULES
        for m in rx.finditer(line)
    ]

    base_dois, rev_dois = sorted(set(DOI_RE.findall(base_txt))), sorted(set(DOI_RE.findall(rev_txt)))
    dropped_dois = [d for d in base_dois if d not in rev_dois]
    base_stats = sorted(set(m.group(0).strip() for m in STAT_NUM_RE.finditer(base_txt)))
    rev_norm_compact = re.sub(r"\s+", "", rev_txt.lower())
    resp_norm_compact = re.sub(r"\s+", "", resp_txt.lower())
    unexplained_stat_drops = [
        st for st in base_stats
        if re.sub(r"\s+", "", st.lower()) not in rev_norm_compact and re.sub(r"\s+", "", st.lower()) not in resp_norm_compact
    ]
    base_nums, rev_nums = sorted(set(NUM_TOKEN_RE.findall(base_txt))), sorted(set(NUM_TOKEN_RE.findall(rev_txt)))
    preserved_nums = [n for n in base_nums if n in rev_nums]

    # Separate parent vs atomic sub-item vs unassigned coverage metrics (never conflate!)
    parent_rows = [r for r in per_comment_ledger if not r.get("is_atomic_subitem")]
    atomic_rows = [r for r in per_comment_ledger if r.get("is_atomic_subitem")]
    unassigned_rows = [r for r in per_comment_ledger if r.get("is_unassigned")]
    parent_items_total = len(parent_rows)
    parent_items_covered = sum(1 for r in parent_rows if r.get("answered"))
    atomic_subitems_total = len(atomic_rows)
    atomic_subitems_covered = sum(1 for r in atomic_rows if r.get("answered"))
    unassigned_requests_total = len(unassigned_rows)
    unassigned_requests_covered = sum(1 for r in unassigned_rows if r.get("answered"))

    blocking_shared_anchors = [s for s in shared_anchor_risks if s["severity"] == "blocking"]

    errors: list[str] = []
    if base_doc.get("extraction_status") in ("empty_or_scanned", "empty", "error"):
        errors.append(f"baseline_extraction_failed: {base_doc.get('warning') or base_doc.get('extraction_status')}")
    if comments_doc.get("extraction_status") in ("empty_or_scanned", "empty", "error"):
        errors.append(f"comments_extraction_failed: {comments_doc.get('warning') or comments_doc.get('extraction_status')}")
    if parse_underflow:
        errors.append(f"comment_parse_underflow: explicit comment keys missed by parser: {parse_underflow}")
    if missing_responses:
        errors.append(f"Unanswered reviewer comment/sub-item IDs: {missing_responses}")
    if missing_dispositions:
        errors.append(f"Missing canonical disposition tag on comment(s): {missing_dispositions}")
    if empty_responses:
        errors.append(f"Empty or thanks-only response detected for: {empty_responses}")
    if unmatched_quotes:
        errors.append(f"{len(unmatched_quotes)} response quote(s) not found verbatim in revised manuscript")
    if invalid_line_refs:
        errors.append(f"{len(invalid_line_refs)} out-of-range/invalid line anchor(s)")
    if invalid_section_refs:
        errors.append(f"{len(invalid_section_refs)} invalid section anchor(s) or section-line mismatch(es)")
    if anchor_quote_mismatches:
        errors.append(f"{len(anchor_quote_mismatches)} anchor-to-quote positional mismatch(es)")
    if fake_edit_violations:
        errors.append(f"{len(fake_edit_violations)} fake-edit or trivial-edit violation(s)")
    if clarify_no_substantive_action:
        errors.append(f"CLARIFY_NO_SUBSTANTIVE_ACTION: {len(clarify_no_substantive_action)} CLARIFY/DISAGREE response(s) lacked manuscript diff or explicit no_manuscript_edit_justification")
    if blocking_shared_anchors:
        errors.append(f"SHARED_ANCHOR_RISK: {len(blocking_shared_anchors)} blocking anchor collision(s) across distinct comments")
    if missing_evidence_gaps:
        errors.append(f"{len(missing_evidence_gaps)} incomplete or unlogged missing-evidence item(s): {[g['comment_id'] for g in missing_evidence_gaps]}")
    if prior_commitment_unmet:
        errors.append(f"prior_commitment_unmet: {len(prior_commitment_unmet)} verified prior-round revision(s) lost in current manuscript")
    if fabrication_hits:
        errors.append(f"Forbidden fabricated compliance phrasing: {fabrication_hits}")
    if overpromise_hits:
        errors.append(f"Forbidden overpromising on unfeasible work: {overpromise_hits}")
    if rebuke_hits:
        errors.append(f"Hostile/rebuke phrasing detected: {rebuke_hits}")
    if cross_reviewer_leaks:
        errors.append(f"{len(cross_reviewer_leaks)} cross-reviewer isolation leak(s)")
    if dropped_dois:
        errors.append(f"Baseline DOI(s) silently dropped in revised manuscript: {dropped_dois}")
    if unexplained_stat_drops:
        errors.append(f"unexplained_baseline_numeric_drop: baseline statistical tokens removed without mention in response letter: {unexplained_stat_drops}")

    me_closed_set = set(missing_evidence_closed)
    for row in per_comment_ledger:
        cid = row["comment_id"]
        me_ok = (not row.get("needs_missing_evidence")) or (cid in me_closed_set) or bool(row.get("is_atomic_subitem"))
        row["missing_evidence_closed"] = me_ok
        evidence_map.append({
            "comment_id": cid, "comment_key": row.get("comment_key"),
            "is_atomic_subitem": row.get("is_atomic_subitem", False),
            "disposition": row.get("disposition"), "tone_tier": row.get("tone_tier"),
            "anchors": row.get("anchors", []), "verified_quotes": row.get("verified_quotes", []),
            "verified_quote_hashes": row.get("verified_quote_hashes", []),
            "no_manuscript_edit_justification": row.get("no_manuscript_edit_justification"),
            "diff_verified": row.get("diff_verified", False), "missing_evidence_closed": me_ok,
        })
        work_status = "UNRESOLVED_CLOSURE" if (not row.get("answered") or not row.get("diff_verified")) else ("PENDING_AUTHOR_CONFIRM" if row.get("needs_missing_evidence") else "VERIFIED_DONE")
        change_log.append({
            "comment_id": cid, "comment_key": row.get("comment_key"),
            "action": row.get("disposition") or "UNCLASSIFIED",
            "work_status": work_status, "revised_lines": row.get("linked_revised_lines", []),
            "requires_author_confirmation": bool(row.get("needs_missing_evidence")),
        })

    package_readiness = "blocked" if errors else ("needs_author_input" if missing_evidence_required_ids else "ready_to_submit")
    gaps_list = [
        {"code": "AUTHOR_EVIDENCE_PENDING", "severity": "warning", "source": f"missing_evidence_list.md:{cid}", "message": f"Comment {cid} requires author confirmation or external data outside controlled baseline scope.", "fallback": "claim_softening_and_limitation_logged"}
        for cid in missing_evidence_required_ids
    ]
    for risk in shared_anchor_risks:
        gaps_list.append({
            "code": "SHARED_ANCHOR_RISK",
            "severity": risk["severity"],
            "source": response_path.name,
            "message": risk["reason"],
            "fallback": "separate_per_comment_anchors_recommended",
        })

    contract_patch: dict[str, Any] = {
        "case_id": case_id or "UNSET_CASE_ID",
        "deliverable": {"revised_manuscript": revised_path.name, "response_letter": response_path.name, "missing_evidence_list": me_file.name if me_file else "missing_evidence_list.md", "package_readiness": package_readiness},
        "preserved_facts": {
            "baseline_file": baseline_path.name, "baseline_extraction_engine": base_doc["engine"],
            "comments_extraction_engine": comments_doc["engine"],
            "preserved_dois": [d for d in base_dois if d in rev_dois], "dropped_baseline_dois": dropped_dois,
            "baseline_stat_tokens_count": len(base_stats), "unexplained_stat_drops": unexplained_stat_drops,
            "preserved_numeric_tokens_count": len(preserved_nums), "preserved_numeric_tokens_sample": preserved_nums[:25],
        },
        "evidence_map": evidence_map, "change_log": change_log,
        "metrics": {
            "parent_items_total": parent_items_total,
            "parent_items_covered": parent_items_covered,
            "atomic_subitems_total": atomic_subitems_total,
            "atomic_subitems_covered": atomic_subitems_covered,
            "unassigned_requests_total": unassigned_requests_total,
            "unassigned_requests_covered": unassigned_requests_covered,
            "triple_bound_edits_verified": triple_bound_verified_count,
            "verified_quotes_count": verified_quotes_total,
            "changed_revised_lines_count": len(changed_set),
            "shared_anchor_risks_count": len(shared_anchor_risks),
            "clarify_no_substantive_action_count": len(clarify_no_substantive_action),
            "missing_evidence_required": len(missing_evidence_required_ids),
            "missing_evidence_closed": len(missing_evidence_closed),
            "prior_commitments_checked": prior_commitments_checked,
            "hard_errors_count": len(errors),
            "style_warnings_count": len(style_warnings),
        },
        "metric_provenance": {
            "parent_items_total": comments_path.name,
            "parent_items_covered": response_path.name,
            "atomic_subitems_total": comments_path.name,
            "atomic_subitems_covered": response_path.name,
            "unassigned_requests_total": comments_path.name,
            "unassigned_requests_covered": response_path.name,
            "triple_bound_edits_verified": "rebuttal_ledger.json:triple_bound_edits_verified",
            "verified_quotes_count": "rebuttal_ledger.json:verified_quotes_count",
            "changed_revised_lines_count": f"diff:{baseline_path.name}->{revised_path.name}",
            "shared_anchor_risks_count": "rebuttal_ledger.json:shared_anchor_risks",
            "clarify_no_substantive_action_count": "rebuttal_ledger.json:clarify_no_substantive_action",
            "missing_evidence_required": response_path.name,
            "missing_evidence_closed": me_file.name if me_file else response_path.name,
            "prior_commitments_checked": "rebuttal_ledger.json:prior_commitments_checked",
            "hard_errors_count": "rebuttal_ledger.json:errors",
            "style_warnings_count": "rebuttal_ledger.json:style_warnings",
        },
        "gaps": gaps_list,
    }
    return {
        "passed": len(errors) == 0, "package_readiness": package_readiness,
        "round": round_id,
        "extraction_engines": {"baseline": base_doc["engine"], "comments": comments_doc["engine"], "revised": rev_doc["engine"]},
        "parse_underflow_keys": parse_underflow,
        "parent_items_total": parent_items_total,
        "parent_items_covered": parent_items_covered,
        "atomic_subitems_total": atomic_subitems_total,
        "atomic_subitems_covered": atomic_subitems_covered,
        "unassigned_requests_total": unassigned_requests_total,
        "unassigned_requests_covered": unassigned_requests_covered,
        "expected_comment_ids": expected_ids,
        "answered_comment_ids": [cid for cid in expected_ids if cid not in missing_responses],
        "missing_responses": missing_responses, "missing_dispositions": missing_dispositions, "empty_responses": empty_responses,
        "verified_quotes_count": verified_quotes_total, "triple_bound_edits_verified": triple_bound_verified_count,
        "unmatched_quotes": unmatched_quotes, "invalid_line_refs": invalid_line_refs, "invalid_section_refs": invalid_section_refs,
        "anchor_quote_mismatches": anchor_quote_mismatches, "fake_edit_violations": fake_edit_violations,
        "clarify_no_substantive_action": clarify_no_substantive_action,
        "shared_anchor_risks": shared_anchor_risks,
        "missing_evidence_required_ids": missing_evidence_required_ids,
        "missing_evidence_closed_ids": missing_evidence_closed, "missing_evidence_gaps": missing_evidence_gaps,
        "prior_commitments_checked": prior_commitments_checked, "prior_commitment_unmet": prior_commitment_unmet,
        "fabrication_hits": fabrication_hits, "overpromise_hits": overpromise_hits, "rebuke_hits": rebuke_hits,
        "cross_reviewer_leaks": cross_reviewer_leaks, "dropped_baseline_dois": dropped_dois,
        "unexplained_stat_drops": unexplained_stat_drops,
        "diff_summary": {
            "has_substantive_edits": diff_info["has_substantive_edits"],
            "changed_revised_lines": diff_info["changed_revised_lines"],
            "touched_revised_lines": diff_info["touched_revised_lines"],
            "token_hunks_count": len(diff_info["token_hunks"]),
            "unified_diff_lines": diff_info["unified_diff_lines"],
        },
        "per_comment_ledger": per_comment_ledger, "tone_alignment_warnings": tone_alignment_warnings,
        "disposition_alias_warnings": disposition_alias_warnings, "style_warnings": style_warnings,
        "errors": errors, "contract_patch": contract_patch,
    }


def render_markdown_report(rep: dict[str, Any]) -> str:
    status = "PASS" if rep["passed"] else "FAIL"
    lines = [
        f"# Rebuttal Closure Audit Report ({status})", "",
        f"- **Package Readiness**: `{rep['package_readiness']}` | **Round**: `{rep.get('round', 'R1')}` | **Engines**: `{rep.get('extraction_engines', {})}`",
        f"- **Parent Comments Covered**: {rep['parent_items_covered']}/{rep['parent_items_total']} | **Atomic Sub-Items Covered**: {rep['atomic_subitems_covered']}/{rep['atomic_subitems_total']} | **Unassigned Requests Covered**: {rep['unassigned_requests_covered']}/{rep['unassigned_requests_total']}",
        f"- **Triple-Bound Edits**: {rep['triple_bound_edits_verified']} | **Verified Quotes**: {rep['verified_quotes_count']} | **Shared-Anchor Risks**: {len(rep.get('shared_anchor_risks', []))}",
        f"- **Substantive Changed Lines**: {len(rep['diff_summary']['changed_revised_lines'])} | **Missing-Evidence Closed**: {len(rep['missing_evidence_closed_ids'])}/{len(rep['missing_evidence_required_ids'])} | **Prior-Round Checks**: {rep.get('prior_commitments_checked', 0)}",
        f"- **Hard Errors**: {len(rep['errors'])} | **Style Warnings**: {len(rep['style_warnings'])}", "",
        "## Per-Comment Closure Matrix", "",
        "| Comment ID | Composite Key | Level | Disposition | Anchors | Diff Verified | Sub-Tokens | Missing-Evidence Closed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rep["per_comment_ledger"]:
        anc = ", ".join(row.get("anchors") or []) or "-"
        lvl = "atomic_sub" if row.get("is_atomic_subitem") else ("unassigned" if row.get("is_unassigned") else "parent")
        lines.append(
            f"| {row['comment_id']} | `{row.get('comment_key', '-')}` | `{lvl}` | `{row.get('disposition') or 'MISSING'}` | "
            f"{anc} | {'Yes' if row.get('diff_verified') else 'No'} | {row.get('substantive_diff_tokens', 0)} | "
            f"{'Yes' if row.get('missing_evidence_closed') else 'No'} |"
        )
    if rep.get("shared_anchor_risks"):
        lines.extend(["", "## Shared Anchor Risk Audit", ""])
        for sr in rep["shared_anchor_risks"]:
            lines.append(f"- [{sr['severity'].upper()}] `{sr['code']}`: comments={sr['comment_ids']} shared_lines={sr['shared_lines']} ({sr['reason']})")
    if rep["errors"]:
        lines.extend(["", "## Blocking Errors", ""])
        lines.extend(f"- {err}" for err in rep["errors"])
    if rep["style_warnings"]:
        lines.extend(["", "## Prose & Anti-AI Style Warnings", ""])
        lines.extend(f"- Line {w['line']} [{w['category']}]: `{w['match']}` -> {w['suggestion']}" for w in rep["style_warnings"][:30])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Peer-review rebuttal extractor, anchor/reanchor tool, and triple-bound closure auditor.")
    parser.add_argument("--extract-review-items", action="store_true", help="Auto-discover baseline/comments under --from-input (PDF/DOCX/MD/TXT) and extract review_items.json.")
    parser.add_argument("--from-input", default="", help="Input directory or comments file for --extract-review-items.")
    parser.add_argument("--work-dir", default="/app/work", help="Output directory for --extract-review-items.")
    parser.add_argument("--round", default="R1", help="Revision round identifier for composite comment keys (default: R1).")
    parser.add_argument("--extract-anchors", action="store_true", help="Extract [§Section Lstart-Lend] + quote_sha1 from baseline->revised diff.")
    parser.add_argument("--require-edits", action="store_true", help="With --extract-anchors, exit non-zero if baseline->revised has no substantive diff.")
    parser.add_argument("--reanchor", action="store_true", help="Recompute drifted line anchors in --response against latest --revised.")
    parser.add_argument("--out-response", default="", help="Optional output path for reanchored response letter.")
    parser.add_argument("--baseline", default="", help="Path to manuscript baseline (.pdf/.docx/.md/.txt).")
    parser.add_argument("--comments", default="", help="Path to reviewer comments (.pdf/.docx/.md/.txt).")
    parser.add_argument("--revised", default="", help="Path to revised manuscript.")
    parser.add_argument("--response", default="", help="Path to point-by-point response letter.")
    parser.add_argument("--missing-evidence", default="", help="Optional path to missing_evidence_list.md.")
    parser.add_argument("--previous-ledger", default="", help="Optional prior-round rebuttal_ledger.json for multi-round commitment continuity.")
    parser.add_argument("--min-substantive-tokens", type=int, default=3, help="Minimum non-stopword diff tokens required per edit-claiming comment (default: 3).")
    parser.add_argument("--allow-shared-anchor", action="store_true", help="Downgrade SHARED_ANCHOR_RISK full collisions from blocking error to warning.")
    parser.add_argument("--case-id", default="", help="Optional case_id embedded in contract patch.")
    parser.add_argument("--out-json", default="", help="Output JSON path.")
    parser.add_argument("--out-report", default="", help="Optional Markdown closure report path.")
    parser.add_argument("--out-contract-patch", default="", help="Optional result.json contract patch JSON path.")
    args = parser.parse_args(argv)

    if args.extract_review_items:
        in_p = Path(args.from_input or args.comments)
        if not str(in_p):
            parser.error("--from-input or --comments is required with --extract-review-items")
        ext = extract_review_items_from_input(
            in_p, Path(args.work_dir),
            comments_override=Path(args.comments) if args.comments else None,
            baseline_override=Path(args.baseline) if args.baseline else None,
            round_id=args.round,
        )
        if args.out_json:
            op = Path(args.out_json)
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(json.dumps(ext, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in ext.items() if k not in ("items", "unassigned_requests")}, ensure_ascii=False, indent=2))
        return 0 if ext["ok"] else 1

    if args.extract_anchors:
        if not args.baseline or not args.revised or not args.out_json:
            parser.error("--baseline, --revised, and --out-json are required with --extract-anchors")
        ext = extract_revised_anchors(Path(args.baseline), Path(args.revised))
        op = Path(args.out_json)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(ext, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"has_substantive_edits": ext["has_substantive_edits"], "anchors_count": len(ext["anchors"])}, ensure_ascii=False, indent=2))
        if args.require_edits and not ext["has_substantive_edits"]:
            return 1
        return 0

    if args.reanchor:
        if not args.revised or not args.response:
            parser.error("--revised and --response are required with --reanchor")
        ra = reanchor_response_letter(Path(args.revised), Path(args.response), Path(args.out_response) if args.out_response else None)
        if args.out_json:
            op = Path(args.out_json)
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(json.dumps(ra, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(ra, ensure_ascii=False, indent=2))
        return 0

    if not args.baseline or not args.comments or not args.revised or not args.response or not args.out_json:
        parser.error("--baseline, --comments, --revised, --response, and --out-json are required in audit mode")

    rep = audit_rebuttal_closure(
        Path(args.baseline), Path(args.comments), Path(args.revised), Path(args.response),
        Path(args.missing_evidence) if args.missing_evidence else None,
        Path(args.previous_ledger) if args.previous_ledger else None,
        case_id=args.case_id, round_id=args.round,
        min_substantive_tokens=args.min_substantive_tokens,
        allow_shared_anchor=args.allow_shared_anchor,
    )
    op = Path(args.out_json)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.out_report:
        rp = Path(args.out_report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(render_markdown_report(rep), encoding="utf-8")
    if args.out_contract_patch:
        cp = Path(args.out_contract_patch)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(rep["contract_patch"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "passed": rep["passed"], "package_readiness": rep["package_readiness"],
        "parent_items_covered": f"{rep['parent_items_covered']}/{rep['parent_items_total']}",
        "atomic_subitems_covered": f"{rep['atomic_subitems_covered']}/{rep['atomic_subitems_total']}",
        "unassigned_requests_covered": f"{rep['unassigned_requests_covered']}/{rep['unassigned_requests_total']}",
        "triple_bound_edits_verified": rep["triple_bound_edits_verified"],
        "shared_anchor_risks": len(rep["shared_anchor_risks"]),
        "clarify_no_substantive_action": len(rep["clarify_no_substantive_action"]),
        "missing_evidence_closed": f"{len(rep['missing_evidence_closed_ids'])}/{len(rep['missing_evidence_required_ids'])}",
        "prior_commitments_checked": rep["prior_commitments_checked"],
        "errors": rep["errors"], "style_warnings_count": len(rep["style_warnings"]),
    }, ensure_ascii=False, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
