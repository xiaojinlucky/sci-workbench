#!/usr/bin/env python3
"""Deterministic peer-review rebuttal closure, triple-bound edit, evidence, and tone auditor.

Pure Python 3.12 standard library implementation with zero external dependencies.
Verifies:
  1. 1:1:1 comment and sub-item closure (E.1, R1.1, R1.1a, R1.1(b), Comment 1, Chinese IDs)
  2. Explicit canonical disposition tags (ACCEPT_TEXT / CLARIFY_EXISTING / SOFTEN_CLAIM /
     PARTIAL / DISAGREE_WITH_EVIDENCE / AUTHOR_INPUT_NEEDED)
  3. Triple-bound edit verification: verbatim quote in revised manuscript + line/section
     anchor containment + genuine token-level diff overlap (blocks reflow and fake edits)
  4. Tri-partite missing-evidence closure (boundary rationale + existing-material alternative
     + concrete author confirmation)
  5. Four-tier tone calibration, anti-fabrication, anti-overpromise, anti-rebuke,
     cross-reviewer isolation, and anti-AI/empty-filler prose scanning
  6. Preserved-facts integrity check and automatic result.json contract patch + Markdown report
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
from typing import Any

# Canonical comment ID formats across English and Chinese peer-review conventions
COMMENT_ID_PATTERN = (
    r"(?:"
    r"(?:E|R\d+)\.\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?"
    r"|(?:Comment|Major|Minor)\s+\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?"
    r"|(?:审稿人\s*\d+\s*意见|主编意见|编辑意见|意见)\s*\d+(?:\.\d+)?(?:\([a-z0-9]+\)|[a-z])?"
    r")"
)
COMMENT_ID_RE = re.compile(r"(?<![A-Za-z0-9_])(" + COMMENT_ID_PATTERN + r")(?![A-Za-z0-9_])", re.I)

HEADER_ID_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|[-*]\s+|\*{2})?(" + COMMENT_ID_PATTERN + r")(?:\*{2})?\s*(?:[:：—–\-]|$|\s)",
    re.I,
)

SUB_BULLET_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\(([a-z]|[1-9])\)|([a-z])\)|([①②③④⑤⑥]))\s+(.+)$",
    re.I,
)

CANONICAL_DISPOSITIONS = {
    "ACCEPT_TEXT",
    "CLARIFY_EXISTING",
    "SOFTEN_CLAIM",
    "PARTIAL",
    "DISAGREE_WITH_EVIDENCE",
    "AUTHOR_INPUT_NEEDED",
}

DISPOSITION_ALIASES = {
    "DISAGREE": "DISAGREE_WITH_EVIDENCE",
}

DISPOSITION_RE = re.compile(
    r"\b(ACCEPT_TEXT|CLARIFY_EXISTING|SOFTEN_CLAIM|PARTIAL|DISAGREE_WITH_EVIDENCE|DISAGREE|AUTHOR_INPUT_NEEDED)\b"
)

UNFEASIBLE_SIGNAL_RE = re.compile(
    r"\b(?:AUTHOR_INPUT_NEEDED|NOT_FEASIBLE|OUT_OF_SCOPE|EVIDENCE_GAP)\b|未到位|无法补做|超出范围",
    re.I,
)

EDIT_CLAIM_PROSE_RE = re.compile(
    r"\b(?:we\s+(?:have\s+)?(?:revised|updated|modified|added|rewrote|softened|clarified\s+in\s+the\s+revised)|revised\s+text|manuscript\s+(?:now\s+)?states)\b"
    r"|已(?:修改|修订|补充|增补|改写|弱化|调整)|修订稿(?:中)?已",
    re.I,
)

# Location anchors: [L12], [L12-L18], [L12-18], [Line 12], [Lines 12-18],
# [§Methods L12-L18], [Section Methods, L12-15], [§Methods], [第12行], [第12-18行]
ANCHOR_BRACKET_RE = re.compile(r"\[([^\[\]\n]{2,90})\]")
LINE_SPEC_RE = re.compile(
    r"(?:(?:\b(?:Lines?|L)\s*(\d+)(?:\s*[-–—~]\s*(?:L|Lines?\s*)?(\d+))?)|(?:第\s*(\d+)(?:\s*[-–—~到至]\s*(\d+))?\s*行))",
    re.I,
)
SECTION_SPEC_RE = re.compile(r"(?:§\s*|Section\s+)([^,;\]]+?)(?=\s*(?:,|\bL\d|\bLine|\bLines|第\s*\d+\s*行|$))", re.I)

# Quote extractors
INLINE_QUOTE_RE = re.compile(
    r"\"([^\"]{12,400})\"|「([^」]{10,400})」|“([^”]{10,400})”"
)
BEFORE_OR_REVIEWER_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*{2})?(?:Reviewer(?:\s+\d+)?\s*Comment|Original\s+Comment|Comment\s+Summary|"
    r"Original\s+text|Baseline(?:\s+text)?|Before(?:\s+revision)?|Previous(?:ly)?|"
    r"审稿意见|原意见|意见摘要|修改前|原文|基线原文)(?:\*{2})?\s*[:：]",
    re.I,
)

# Anti-fabrication, overpromise, rebuke, cross-reviewer, and AI-filler patterns
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
    r"see\s+our\s+response\s+to\s+(?:Reviewer|R)\s*\d+|"
    r"the\s+other\s+reviewer\s+requested\s+the\s+opposite|"
    r"as\s+Reviewer\s+\d+\s+also\s+(?:noted|suggested))\b"
    r"|请参见(?:我们)?对(?:审稿人\s*\d+|另一位审稿人)的回复|正如(?:另一位审稿人|审稿人\s*\d+)(?:所)?指出|另一位审稿人提出了相反",
    re.I,
)

EMPTY_FILLER_RE = re.compile(
    r"^(?:we\s+thank\s+the\s+reviewer[^.\n]*\.?\s*)?(?:we\s+have\s+revised\s+accordingly\.?|thank\s+you\s+for\s+(?:this|the)\s+comment\.?|已按意见修改。?|感谢审稿人的意见。?)$",
    re.I,
)

AI_STYLE_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("AI模板总结语", re.compile(r"综上所述|总而言之|由此可见|不难发现|众所周知"), "删除套话，直接给修改动作与事实依据"),
    ("AI洞察路标", re.compile(r"值得注意的是|需要指出的是|有必要指出的是|从某种意义上说"), "直接陈述核心事实，去掉形式主语路标"),
    ("翻案腔", re.compile(r"(?:不是|并非)[^，。\n]{1,24}[，,]?而是"), "改为正面直接陈述判断与依据"),
    ("营销/空泛拔高词", re.compile(r"显著赋能|深刻揭示|开启新的篇章|意义深远|颠覆性|重磅"), "换为克制、可核验的学术实证表述"),
    ("破折号滥用", re.compile(r"——"), "减少双破折号连接，改为逗号、括号或拆句"),
    ("英文AI套话", re.compile(r"\b(?:it\s+is\s+worth\s+noting\s+that|plays\s+a\s+pivotal\s+role|delve\s+into|tapestry\s+of|underscores\s+the\s+importance\s+of)\b", re.I), "Replace stock phrasing with direct domain prose"),
    ("空洞回复套话", re.compile(r"\bwe\s+have\s+revised\s+accordingly\b", re.I), "Specify the exact edit and location instead of generic 'revised accordingly'"),
]

# Missing-evidence 3-element semantic detectors
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

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
NUM_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z0-9_])")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def canonicalize_id(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip())
    s = re.sub(r"\(([a-z0-9]+)\)", r"\1", s, flags=re.I)
    s = re.sub(r"审稿人\s*(\d+)\s*意见\s*", r"审稿人\1意见", s)
    s = re.sub(r"(?:主编意见|编辑意见|意见)\s*", lambda m: m.group(0).strip(), s)
    return s.lower()


def reviewer_group_of(cid: str) -> str | None:
    low = canonicalize_id(cid)
    m = re.match(r"^(r\d+)\.", low)
    if m:
        return m.group(1)
    m = re.match(r"^(审稿人\d+)意见", low)
    if m:
        return m.group(1)
    if low.startswith("e.") or low.startswith("主编") or low.startswith("编辑"):
        return "editor"
    return None


def parse_comments_structure(comments_txt: str) -> list[dict[str, Any]]:
    """Extract top-level comment IDs and nested sub-requirements from reviewer-comments.md."""
    lines = comments_txt.splitlines()
    items: list[dict[str, Any]] = []
    seen_canon: set[str] = set()
    current_parent: str | None = None
    pending_subs: list[tuple[str, str]] = []

    def _flush_subs() -> None:
        nonlocal pending_subs
        if current_parent and len(pending_subs) >= 2:
            # Replace bare parent with explicit sub-items if sub-items aren't already explicit
            parent_canon = canonicalize_id(current_parent)
            # Remove bare parent if it was added right before its sub-bullets
            if items and items[-1]["canonical_id"] == parent_canon:
                items.pop()
                seen_canon.discard(parent_canon)
            for sub_lbl, sub_txt in pending_subs:
                sub_id = f"{current_parent}({sub_lbl})"
                sc = canonicalize_id(sub_id)
                if sc not in seen_canon:
                    seen_canon.add(sc)
                    items.append({
                        "id": sub_id,
                        "canonical_id": sc,
                        "parent_id": current_parent,
                        "sub_label": sub_lbl,
                        "excerpt": sub_txt[:120],
                    })
        pending_subs = []

    circled_map = {"①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5", "⑥": "6"}

    for line in lines:
        hm = HEADER_ID_RE.match(line)
        if hm:
            _flush_subs()
            cid = re.sub(r"\s+", " ", hm.group(1).strip())
            c = canonicalize_id(cid)
            current_parent = cid
            if c not in seen_canon:
                seen_canon.add(c)
                items.append({
                    "id": cid,
                    "canonical_id": c,
                    "parent_id": None,
                    "sub_label": None,
                    "excerpt": line.strip()[:120],
                })
            continue
        if current_parent:
            sm = SUB_BULLET_RE.match(line)
            if sm:
                lbl = sm.group(1) or sm.group(2) or circled_map.get(sm.group(3) or "", "")
                if lbl:
                    pending_subs.append((lbl.lower(), sm.group(4).strip()))
                continue
        # Also catch inline comment IDs on non-header lines if no headers were used
        if not items:
            for im in COMMENT_ID_RE.finditer(line):
                cid = re.sub(r"\s+", " ", im.group(1).strip())
                c = canonicalize_id(cid)
                if c not in seen_canon:
                    seen_canon.add(c)
                    items.append({
                        "id": cid,
                        "canonical_id": c,
                        "parent_id": None,
                        "sub_label": None,
                        "excerpt": line.strip()[:120],
                    })
    _flush_subs()

    # Fallback: scan full text if line-header scan missed anything
    if not items:
        for im in COMMENT_ID_RE.finditer(comments_txt):
            cid = re.sub(r"\s+", " ", im.group(1).strip())
            c = canonicalize_id(cid)
            if c not in seen_canon:
                seen_canon.add(c)
                items.append({
                    "id": cid,
                    "canonical_id": c,
                    "parent_id": None,
                    "sub_label": None,
                    "excerpt": cid,
                })
    return items


def split_response_sections(resp_txt: str, expected_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Split response-letter.md into per-comment-ID blocks (preserving 1-based line ranges)."""
    lines = resp_txt.splitlines()
    raw_blocks: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None

    for idx, line in enumerate(lines, 1):
        hm = HEADER_ID_RE.match(line)
        if hm:
            cid = re.sub(r"\s+", " ", hm.group(1).strip())
            if cur is not None:
                cur["end_line"] = idx - 1
                raw_blocks.append(cur)
            cur = {
                "id": cid,
                "canonical_id": canonicalize_id(cid),
                "start_line": idx,
                "end_line": idx,
                "lines": [line],
            }
        elif cur is not None:
            cur["lines"].append(line)
            cur["end_line"] = idx

    if cur is not None:
        raw_blocks.append(cur)

    by_canon: dict[str, dict[str, Any]] = {}
    for blk in raw_blocks:
        blk["text"] = "\n".join(blk["lines"])
        c = blk["canonical_id"]
        if c not in by_canon:
            by_canon[c] = blk
        else:
            # Merge duplicate headings for same ID
            by_canon[c]["lines"].extend(blk["lines"])
            by_canon[c]["text"] = "\n".join(by_canon[c]["lines"])
            by_canon[c]["end_line"] = blk["end_line"]

    # Support nested (a)/(b) sub-items answered inside a parent block (e.g. R1.2 containing (a) and (b))
    for item in expected_items:
        c = item["canonical_id"]
        if c in by_canon:
            continue
        parent = item.get("parent_id")
        sub_lbl = item.get("sub_label")
        if parent and sub_lbl:
            p_blk = by_canon.get(canonicalize_id(parent))
            if p_blk:
                sub_pat = re.compile(
                    rf"(?:^|\n)\s*(?:[-*]\s*)?(?:\*{2})?(?:\({re.escape(sub_lbl)}\)|{re.escape(sub_lbl)}\)|{re.escape(parent)}\s*\(?{re.escape(sub_lbl)}\)?)",
                    re.I,
                )
                m = sub_pat.search(p_blk["text"])
                if m:
                    # Slice from this sub-label until next sub-label or end of parent block
                    tail = p_blk["text"][m.start():]
                    next_m = re.search(r"\n\s*(?:[-*]\s*)?(?:\*{2})?(?:\([a-z1-9]\)|[a-z]\))", tail[1:], re.I)
                    sub_text = tail[: next_m.start() + 1] if next_m else tail
                    by_canon[c] = {
                        "id": item["id"],
                        "canonical_id": c,
                        "start_line": p_blk["start_line"],
                        "end_line": p_blk["end_line"],
                        "lines": sub_text.splitlines(),
                        "text": sub_text,
                        "inherited_parent_text": p_blk["text"],
                    }
    return by_canon


def build_token_diff(base_txt: str, rev_txt: str) -> dict[str, Any]:
    """Compute token-level diff between baseline and revised, mapping changes to 1-based revised lines."""
    base_tokens: list[str] = []
    for line in base_txt.splitlines():
        base_tokens.extend(line.split())

    rev_tokens: list[tuple[str, int]] = []
    for ln, line in enumerate(rev_txt.splitlines(), 1):
        for tok in line.split():
            rev_tokens.append((tok, ln))

    rev_tok_strs = [t for t, _ in rev_tokens]
    matcher = difflib.SequenceMatcher(None, base_tokens, rev_tok_strs, autojunk=False)

    changed_lines: set[int] = set()
    touched_lines: set[int] = set()
    hunks: list[dict[str, Any]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "insert"):
            span_lines = sorted({rev_tokens[j][1] for j in range(j1, j2)})
            changed_lines.update(span_lines)
            touched_lines.update(span_lines)
            hunks.append({
                "op": tag,
                "revised_lines": span_lines,
                "removed_excerpt": " ".join(base_tokens[i1:i2])[:140],
                "added_excerpt": " ".join(rev_tok_strs[j1:j2])[:140],
            })
        elif tag == "delete":
            adj_line = rev_tokens[j1][1] if j1 < len(rev_tokens) else (rev_tokens[-1][1] if rev_tokens else 1)
            touched_lines.add(adj_line)
            hunks.append({
                "op": "delete",
                "revised_lines": [adj_line],
                "removed_excerpt": " ".join(base_tokens[i1:i2])[:140],
                "added_excerpt": "",
            })

    unified = list(difflib.unified_diff(
        base_txt.splitlines(),
        rev_txt.splitlines(),
        fromfile="manuscript-baseline.md",
        tofile="manuscript-revised.md",
        lineterm="",
    ))

    return {
        "has_substantive_edits": len(hunks) > 0,
        "changed_revised_lines": sorted(changed_lines),
        "touched_revised_lines": sorted(touched_lines),
        "token_hunks": hunks,
        "unified_diff_lines": len(unified),
    }


def build_revised_index(rev_txt: str) -> dict[str, Any]:
    """Build normalized-text-to-line index and Markdown heading span map for revised manuscript."""
    rev_lines = rev_txt.splitlines()
    norm_chars: list[str] = []
    char_lines: list[int] = []

    for ln, line in enumerate(rev_lines, 1):
        toks = line.split()
        for tok in toks:
            if norm_chars:
                norm_chars.append(" ")
                char_lines.append(ln)
            for ch in tok:
                norm_chars.append(ch)
                char_lines.append(ln)

    norm_text = "".join(norm_chars)

    # Parse Markdown headings into section line spans
    raw_headings: list[tuple[str, int]] = []
    for ln, line in enumerate(rev_lines, 1):
        hm = HEADING_RE.match(line)
        if hm:
            raw_headings.append((hm.group(2).strip(), ln))

    sections: list[dict[str, Any]] = []
    for idx, (title, start_ln) in enumerate(raw_headings):
        end_ln = raw_headings[idx + 1][1] - 1 if idx + 1 < len(raw_headings) else len(rev_lines)
        sections.append({
            "title": title,
            "norm_title": re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.lower()),
            "start_line": start_ln,
            "end_line": end_ln,
        })

    return {
        "lines": rev_lines,
        "line_count": len(rev_lines),
        "norm_text": norm_text,
        "char_lines": char_lines,
        "sections": sections,
    }


def locate_quote_spans(quote_norm: str, rev_index: dict[str, Any]) -> list[tuple[int, int]]:
    """Return all 1-based (start_line, end_line) spans where normalized quote matches revised text."""
    norm_text: str = rev_index["norm_text"]
    char_lines: list[int] = rev_index["char_lines"]
    spans: list[tuple[int, int]] = []
    if not quote_norm:
        return spans
    pos = 0
    while True:
        idx = norm_text.find(quote_norm, pos)
        if idx == -1:
            break
        end_idx = idx + len(quote_norm) - 1
        spans.append((char_lines[idx], char_lines[end_idx]))
        pos = idx + 1
    return spans


def extract_anchors(block_text: str) -> list[dict[str, Any]]:
    """Extract location anchors like [L12], [L12-L16], [§Methods L12-L16], [第12-16行]."""
    anchors: list[dict[str, Any]] = []
    for m in ANCHOR_BRACKET_RE.finditer(block_text):
        inner = m.group(1).strip()
        lm = LINE_SPEC_RE.search(inner)
        sm = SECTION_SPEC_RE.search(inner)
        if not lm and not sm:
            continue
        start_ln: int | None = None
        end_ln: int | None = None
        if lm:
            s_str = lm.group(1) or lm.group(3)
            e_str = lm.group(2) or lm.group(4) or s_str
            start_ln = int(s_str)
            end_ln = int(e_str)
        section_name = sm.group(1).strip() if sm else None
        anchors.append({
            "raw": m.group(0),
            "section": section_name,
            "start_line": start_ln,
            "end_line": end_ln,
        })
    return anchors


def extract_manuscript_quotes(block_lines: list[str], comments_norm: str, base_norm: str, rev_norm: str) -> tuple[list[str], list[str]]:
    """Extract candidate revised-manuscript quotes from a response block, skipping reviewer/before quotes."""
    quotes: list[str] = []
    skipped_meta: list[str] = []

    # 1. Collect merged Markdown blockquotes (> ...)
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
        # If consecutive '>' lines quote separate non-adjacent passages, validate per line
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
        # 2. Inline quotes on non-meta lines
        for qm in INLINE_QUOTE_RE.finditer(stripped):
            raw_q = qm.group(1) or qm.group(2) or qm.group(3) or ""
            q = " ".join(raw_q.strip("*\"'「」“” ").split())
            if len(q) < 10:
                continue
            # Ignore quotes explicitly verbatim from reviewer comments
            if q in comments_norm and q not in rev_norm:
                skipped_meta.append(q[:100])
                continue
            # Ignore explicit "before -> after" baseline quotation when labeled on the same line
            prefix = stripped[: qm.start()].lower()
            if any(k in prefix[-35:] for k in ("original:", "before:", "previously", "from ", "修改前", "原文", "由")):
                if q in base_norm and q not in rev_norm:
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
        if re.search(r"respectfully\s+disagree|outside\s+the\s+scope|beyond\s+the\s+scope|超出(?:当前)?范围|无法补做", low):
            return "POLITE_DISAGREE"
        return "CLARIFY"
    return "ACCEPT"


def parse_missing_evidence_sections(me_txt: str, needed_ids: list[str]) -> dict[str, str]:
    """Map comment IDs to their corresponding entries in missing_evidence_list.md."""
    lines = me_txt.splitlines()
    blocks: dict[str, list[str]] = {}
    cur_keys: list[str] = []

    for line in lines:
        found = [canonicalize_id(m.group(1)) for m in COMMENT_ID_RE.finditer(line)]
        is_entry_start = bool(re.match(r"^\s*(?:#{1,6}\s+|[-*]\s+|\|\s*)", line)) and bool(found)
        if is_entry_start:
            cur_keys = list(dict.fromkeys(found))
            for k in cur_keys:
                blocks.setdefault(k, []).append(line)
        elif cur_keys:
            for k in cur_keys:
                blocks[k].append(line)

    result: dict[str, str] = {}
    for cid in needed_ids:
        c = canonicalize_id(cid)
        if c in blocks:
            result[c] = "\n".join(blocks[c])
        else:
            # Fallback: check if parent ID or exact token appears anywhere in the ledger
            if re.search(re.escape(cid), me_txt, re.I):
                result[c] = me_txt
    return result


def audit_rebuttal_closure(
    baseline_path: Path,
    comments_path: Path,
    revised_path: Path,
    response_path: Path,
    missing_evidence_path: Path | None = None,
    *,
    case_id: str = "",
) -> dict[str, Any]:
    base_txt = baseline_path.read_text(encoding="utf-8", errors="replace")
    comments_txt = comments_path.read_text(encoding="utf-8", errors="replace")
    rev_txt = revised_path.read_text(encoding="utf-8", errors="replace")
    resp_txt = response_path.read_text(encoding="utf-8", errors="replace")

    # Auto-discover sibling missing_evidence_list.md if not explicitly supplied
    me_file: Path | None = missing_evidence_path
    if me_file is None:
        candidate = response_path.parent / "missing_evidence_list.md"
        if candidate.is_file():
            me_file = candidate
    me_txt = me_file.read_text(encoding="utf-8", errors="replace") if (me_file and me_file.is_file()) else ""

    expected_items = parse_comments_structure(comments_txt)
    expected_ids = [it["id"] for it in expected_items]
    resp_sections = split_response_sections(resp_txt, expected_items)

    diff_info = build_token_diff(base_txt, rev_txt)
    rev_index = build_revised_index(rev_txt)
    touched_set = set(diff_info["touched_revised_lines"])
    changed_set = set(diff_info["changed_revised_lines"])

    comments_norm = " ".join(comments_txt.split())
    base_norm = " ".join(base_txt.split())
    rev_norm = rev_index["norm_text"]

    # Collections for detailed audit output
    missing_responses: list[str] = []
    missing_dispositions: list[str] = []
    empty_responses: list[str] = []
    unmatched_quotes: list[dict[str, Any]] = []
    invalid_line_refs: list[dict[str, Any]] = []
    invalid_section_refs: list[dict[str, Any]] = []
    anchor_quote_mismatches: list[dict[str, Any]] = []
    fake_edit_violations: list[dict[str, Any]] = []
    cross_reviewer_leaks: list[dict[str, Any]] = []
    tone_alignment_warnings: list[dict[str, Any]] = []
    disposition_alias_warnings: list[str] = []

    verified_quotes_total = 0
    triple_bound_verified_count = 0
    per_comment_ledger: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []
    change_log: list[dict[str, Any]] = []
    missing_evidence_required_ids: list[str] = []

    for item in expected_items:
        cid = item["id"]
        c_key = item["canonical_id"]
        blk = resp_sections.get(c_key)

        if blk is None:
            # Fallback: check if ID appears anywhere in response text
            if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(cid)}(?![A-Za-z0-9_])", resp_txt, re.I):
                missing_responses.append(cid)
                per_comment_ledger.append({
                    "comment_id": cid,
                    "answered": False,
                    "disposition": None,
                    "diff_verified": False,
                })
                continue
            # Synthesize block around mention
            blk = {"id": cid, "canonical_id": c_key, "start_line": 1, "end_line": len(resp_txt.splitlines()), "lines": [resp_txt], "text": resp_txt}

        btxt = blk["text"]
        blines = blk["lines"]

        # 1. Explicit disposition check
        dm = DISPOSITION_RE.search(btxt)
        raw_disp = dm.group(1) if dm else None
        if raw_disp is None and "inherited_parent_text" in blk:
            dm2 = DISPOSITION_RE.search(blk["inherited_parent_text"])
            raw_disp = dm2.group(1) if dm2 else None

        if raw_disp is None:
            disposition = None
            missing_dispositions.append(cid)
        else:
            if raw_disp in DISPOSITION_ALIASES:
                disposition = DISPOSITION_ALIASES[raw_disp]
                disposition_alias_warnings.append(f"{cid}: alias '{raw_disp}' normalized to '{disposition}'")
            else:
                disposition = raw_disp

        # 2. Empty / thanks-only response check
        prose_lines = []
        for ln in blines[1:]:
            s = ln.strip()
            if (
                not s
                or s.startswith(">")
                or BEFORE_OR_REVIEWER_LINE_RE.match(s)
                or re.match(r"^\s*[-*]?\s*\*{0,2}(?:Disposition|Decision|Status|Tone|处置|决策|语气)\*{0,2}\s*[:：]", s, re.I)
            ):
                continue
            s = re.sub(r"^\s*[-*]?\s*\*{0,2}(?:Response|Reply|Answer|Author\s+Response|回复|答复)\*{0,2}\s*[:：]\s*", "", s, flags=re.I)
            if s:
                prose_lines.append(s)
        prose_clean = re.sub(r"\b(?:" + "|".join(CANONICAL_DISPOSITIONS) + r")\b", "", " ".join(prose_lines)).strip()
        prose_no_thanks = re.sub(
            r"(?:we\s+thank\s+the\s+reviewer[^.\n]*\.?|thank\s+you\s+for[^.\n]*\.?|we\s+have\s+revised\s+accordingly\.?|感谢审稿人[^。\n]*。?|已按意见修改。?)",
            "",
            prose_clean,
            flags=re.I,
        ).strip()
        if len(prose_no_thanks) < 12 or EMPTY_FILLER_RE.match(prose_clean):
            empty_responses.append(cid)

        # 3. Extract and validate location anchors
        anchors = extract_anchors(btxt)
        valid_line_anchors: list[tuple[int, int, str]] = []
        for anc in anchors:
            s_ln = anc["start_line"]
            e_ln = anc["end_line"]
            sec_name = anc["section"]

            if s_ln is not None and e_ln is not None:
                if s_ln < 1 or e_ln < 1 or s_ln > e_ln or e_ln > rev_index["line_count"]:
                    invalid_line_refs.append({
                        "comment_id": cid,
                        "anchor": anc["raw"],
                        "reason": f"Line range {s_ln}-{e_ln} out of revised manuscript bounds (1..{rev_index['line_count']})",
                    })
                else:
                    valid_line_anchors.append((s_ln, e_ln, anc["raw"]))

            if sec_name:
                norm_sec = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", sec_name.lower())
                matched_secs = [
                    s for s in rev_index["sections"]
                    if norm_sec and (norm_sec in s["norm_title"] or s["norm_title"] in norm_sec)
                ]
                if not matched_secs:
                    invalid_section_refs.append({
                        "comment_id": cid,
                        "anchor": anc["raw"],
                        "reason": f"Section '{sec_name}' not found among revised headings {[s['title'] for s in rev_index['sections']]}",
                    })
                elif s_ln is not None and e_ln is not None and e_ln <= rev_index["line_count"]:
                    in_any = any(s["start_line"] <= e_ln and s_ln <= s["end_line"] for s in matched_secs)
                    if not in_any:
                        invalid_section_refs.append({
                            "comment_id": cid,
                            "anchor": anc["raw"],
                            "reason": f"Claimed lines L{s_ln}-L{e_ln} fall outside section '{matched_secs[0]['title']}' (L{matched_secs[0]['start_line']}-L{matched_secs[0]['end_line']})",
                        })

        # 4. Extract and verify verbatim quotes + anchor positional binding + diff overlap
        cand_quotes, _ = extract_manuscript_quotes(blines, comments_norm, base_norm, rev_norm)
        verified_quote_records: list[dict[str, Any]] = []

        for q in cand_quotes:
            spans = locate_quote_spans(q, rev_index)
            if not spans:
                unmatched_quotes.append({
                    "comment_id": cid,
                    "quote": q[:160],
                    "reason": "Quoted excerpt not found verbatim in revised manuscript",
                })
                continue

            verified_quotes_total += 1
            # Check whether any occurrence overlaps changed/touched revised lines
            overlapping_changed = [
                sp for sp in spans
                if any(ln in touched_set for ln in range(sp[0], sp[1] + 1))
            ]
            # Check anchor-to-quote binding when line anchors are declared in this comment
            anchor_matched = True
            if valid_line_anchors:
                anchor_matched = any(
                    sp[0] <= a_end and a_start <= sp[1]
                    for sp in spans
                    for (a_start, a_end, _) in valid_line_anchors
                )
                if not anchor_matched:
                    anchor_quote_mismatches.append({
                        "comment_id": cid,
                        "quote": q[:120],
                        "actual_lines": [f"L{s}-L{e}" for s, e in spans],
                        "claimed_anchors": [raw for _, _, raw in valid_line_anchors],
                        "reason": "Verbatim quote exists in revised manuscript, but NOT inside the claimed line anchor range",
                    })

            verified_quote_records.append({
                "quote": q[:140],
                "actual_spans": spans,
                "overlaps_diff": len(overlapping_changed) > 0,
                "anchor_matched": anchor_matched,
            })

        # 5. Substantive diff binding check (anti-fake-edit gate)
        claims_edit = disposition in ("ACCEPT_TEXT", "SOFTEN_CLAIM") or (
            disposition == "PARTIAL" and bool(EDIT_CLAIM_PROSE_RE.search(btxt))
        )
        anchor_overlaps_diff = any(
            any(ln in touched_set for ln in range(a_start, a_end + 1))
            for (a_start, a_end, _) in valid_line_anchors
        )
        quote_overlaps_diff = any(qr["overlaps_diff"] for qr in verified_quote_records)
        quote_anchor_ok = all(qr["anchor_matched"] for qr in verified_quote_records)

        diff_verified = False
        if claims_edit:
            if not verified_quote_records and not valid_line_anchors:
                fake_edit_violations.append({
                    "comment_id": cid,
                    "disposition": disposition,
                    "reason": "Response claims manuscript edit but provides neither a verbatim revised quote nor a valid line anchor",
                })
            elif verified_quote_records and not quote_overlaps_diff:
                fake_edit_violations.append({
                    "comment_id": cid,
                    "disposition": disposition,
                    "reason": "Response claims manuscript edit, but quoted passage is unchanged baseline text with zero overlap in baseline->revised diff",
                })
            elif not verified_quote_records and valid_line_anchors and not anchor_overlaps_diff:
                fake_edit_violations.append({
                    "comment_id": cid,
                    "disposition": disposition,
                    "reason": "Response claims manuscript edit at line anchor(s) that have zero substantive change in baseline->revised diff",
                })
            elif quote_anchor_ok and (quote_overlaps_diff or anchor_overlaps_diff):
                diff_verified = True
                triple_bound_verified_count += 1
        else:
            # Non-edit dispositions (CLARIFY_EXISTING / DISAGREE_WITH_EVIDENCE / AUTHOR_INPUT_NEEDED)
            diff_verified = True

        # 6. Cross-reviewer isolation check
        own_group = reviewer_group_of(cid)
        if GENERIC_CROSS_REVIEWER_RE.search(btxt):
            cross_reviewer_leaks.append({
                "comment_id": cid,
                "reason": "Cross-reviewer phrasing detected inside reviewer-specific reply",
                "match": GENERIC_CROSS_REVIEWER_RE.search(btxt).group(0),  # type: ignore[union-attr]
            })
        elif own_group and own_group.startswith(("r", "审稿人")):
            # Check if another reviewer ID (e.g. R2 or Reviewer 2 inside R1 block) is referenced
            own_num = re.sub(r"\D+", "", own_group)
            for ref_m in re.finditer(r"\b(?:Reviewer\s+(\d+)|R(\d+)\.\d+)\b|审稿人\s*(\d+)", btxt, re.I):
                other_num = ref_m.group(1) or ref_m.group(2) or ref_m.group(3)
                if other_num and other_num != own_num:
                    cross_reviewer_leaks.append({
                        "comment_id": cid,
                        "reason": f"Reply to {own_group} references another reviewer ({ref_m.group(0)})",
                        "match": ref_m.group(0),
                    })
                    break

        # 7. Four-tier tone classification & alignment
        tone_tier = classify_tone_tier(disposition, btxt)
        if disposition == "DISAGREE_WITH_EVIDENCE" and not re.search(
            r"respectfully|evidence|scope|data|table|figure|methods|依据|证据|范围|审慎|不同意|商榷",
            btxt,
            re.I,
        ):
            tone_alignment_warnings.append({
                "comment_id": cid,
                "tone_tier": tone_tier,
                "reason": "DISAGREE_WITH_EVIDENCE response should cite explicit evidence/scope rationale with polite academic framing",
            })

        # Track missing-evidence obligation
        needs_me = disposition in ("AUTHOR_INPUT_NEEDED", "PARTIAL") or bool(UNFEASIBLE_SIGNAL_RE.search(btxt))
        if needs_me:
            missing_evidence_required_ids.append(cid)

        # Collect revised line numbers linked to this comment
        linked_lines: set[int] = set()
        for qr in verified_quote_records:
            for s, e in qr["actual_spans"]:
                linked_lines.update(range(s, e + 1))
        for a_s, a_e, _ in valid_line_anchors:
            linked_lines.update(range(a_s, a_e + 1))

        per_comment_ledger.append({
            "comment_id": cid,
            "answered": True,
            "disposition": disposition,
            "tone_tier": tone_tier,
            "anchors": [a["raw"] for a in anchors],
            "verified_quotes": [qr["quote"] for qr in verified_quote_records],
            "diff_verified": diff_verified,
            "needs_missing_evidence": needs_me,
            "linked_revised_lines": sorted(linked_lines),
        })

    # 8. Missing-evidence tri-partite closure verification
    missing_evidence_gaps: list[dict[str, Any]] = []
    missing_evidence_closed: list[str] = []
    if missing_evidence_required_ids:
        # Also allow inline Missing Evidence section inside response-letter.md if separate file omitted
        inline_me = ""
        m_inline = re.search(r"^#{1,3}\s*(?:Missing\s+Evidence|Unresolved\s+Evidence|未到位证据).*$", resp_txt, re.I | re.M)
        if m_inline:
            inline_me = resp_txt[m_inline.start():]
        combined_me_src = me_txt if me_txt.strip() else inline_me
        me_entries = parse_missing_evidence_sections(combined_me_src, missing_evidence_required_ids)

        for cid in missing_evidence_required_ids:
            c_key = canonicalize_id(cid)
            entry_txt = me_entries.get(c_key, "")
            if not entry_txt.strip():
                missing_evidence_gaps.append({
                    "comment_id": cid,
                    "missing_elements": ["ledger_entry", "boundary_rationale", "existing_material_alternative", "author_confirmation"],
                    "reason": f"{cid} requires missing-evidence logging in missing_evidence_list.md (or Missing Evidence section)",
                })
                continue
            # Combine ledger entry with its response block for alternative-clarification verification
            resp_blk_txt = resp_sections.get(c_key, {}).get("text", "")
            check_blob = entry_txt + "\n" + resp_blk_txt
            missing_elems: list[str] = []
            if not ME_BOUNDARY_RE.search(check_blob):
                missing_elems.append("boundary_rationale")
            if not ME_ALTERNATIVE_RE.search(check_blob):
                missing_elems.append("existing_material_alternative")
            if not ME_AUTHOR_CONFIRM_RE.search(check_blob):
                missing_elems.append("author_confirmation")
            if missing_elems:
                missing_evidence_gaps.append({
                    "comment_id": cid,
                    "missing_elements": missing_elems,
                    "reason": f"{cid} missing-evidence entry incomplete; missing {missing_elems}",
                })
            else:
                missing_evidence_closed.append(cid)

    # 9. Global fabrication, overpromise, rebuke, and AI-style scan
    combined_out_txt = resp_txt + ("\n" + me_txt if me_txt else "")
    fabrication_hits = [m.group(0) for m in FABRICATION_RE.finditer(combined_out_txt)]
    overpromise_hits = [m.group(0) for m in OVERPROMISE_RE.finditer(combined_out_txt)]
    rebuke_hits = [m.group(0) for m in REBUKE_RE.finditer(resp_txt)]

    style_warnings: list[dict[str, Any]] = []
    for ln_no, line in enumerate(resp_txt.splitlines(), 1):
        if line.strip().startswith(">") or BEFORE_OR_REVIEWER_LINE_RE.match(line.strip()):
            continue
        for rule_name, rx, advice in AI_STYLE_RULES:
            for m in rx.finditer(line):
                style_warnings.append({
                    "line": ln_no,
                    "category": rule_name,
                    "match": m.group(0),
                    "suggestion": advice,
                })

    # 10. Preserved-facts baseline vs revised integrity check
    base_dois = sorted(set(DOI_RE.findall(base_txt)))
    rev_dois = sorted(set(DOI_RE.findall(rev_txt)))
    dropped_dois = [d for d in base_dois if d not in rev_dois]
    base_nums = sorted(set(NUM_TOKEN_RE.findall(base_txt)))
    rev_nums = sorted(set(NUM_TOKEN_RE.findall(rev_txt)))
    preserved_nums = [n for n in base_nums if n in rev_nums]

    # Assemble hard errors
    errors: list[str] = []
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
        errors.append(f"{len(anchor_quote_mismatches)} anchor-to-quote positional mismatch(es) (quote not inside claimed line span)")
    if not diff_info["has_substantive_edits"]:
        errors.append("Revised manuscript matches baseline after token normalization (no substantive edits)")
    if fake_edit_violations:
        errors.append(f"{len(fake_edit_violations)} fake-edit violation(s) (claimed edit lacks diff overlap or quotes unchanged baseline)")
    if missing_evidence_gaps:
        errors.append(f"{len(missing_evidence_gaps)} incomplete or unlogged missing-evidence item(s): {[g['comment_id'] for g in missing_evidence_gaps]}")
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

    # Build result.json-ready evidence_map, change_log, and contract_patch
    me_closed_set = set(missing_evidence_closed)
    for row in per_comment_ledger:
        cid = row["comment_id"]
        me_ok = (not row.get("needs_missing_evidence")) or (cid in me_closed_set)
        row["missing_evidence_closed"] = me_ok
        evidence_map.append({
            "comment_id": cid,
            "disposition": row.get("disposition"),
            "tone_tier": row.get("tone_tier"),
            "anchors": row.get("anchors", []),
            "verified_quotes": row.get("verified_quotes", []),
            "diff_verified": row.get("diff_verified", False),
            "missing_evidence_closed": me_ok,
        })
        work_status = "VERIFIED_DONE"
        if not row.get("answered") or not row.get("diff_verified"):
            work_status = "UNRESOLVED_CLOSURE"
        elif row.get("needs_missing_evidence"):
            work_status = "PENDING_AUTHOR_CONFIRM"
        change_log.append({
            "comment_id": cid,
            "action": row.get("disposition") or "UNCLASSIFIED",
            "work_status": work_status,
            "revised_lines": row.get("linked_revised_lines", []),
            "requires_author_confirmation": bool(row.get("needs_missing_evidence")),
        })

    package_readiness = (
        "blocked" if errors else ("needs_author_input" if missing_evidence_required_ids else "ready_to_submit")
    )

    gaps_list: list[dict[str, Any]] = []
    for cid in missing_evidence_required_ids:
        gaps_list.append({
            "code": "AUTHOR_EVIDENCE_PENDING",
            "severity": "warning",
            "source": f"missing_evidence_list.md:{cid}",
            "message": f"Comment {cid} requires author confirmation or external data outside controlled baseline scope.",
            "fallback": "claim_softening_and_limitation_logged",
        })

    contract_patch: dict[str, Any] = {
        "case_id": case_id or "UNSET_CASE_ID",
        "deliverable": {
            "revised_manuscript": revised_path.name,
            "response_letter": response_path.name,
            "missing_evidence_list": me_file.name if me_file else "missing_evidence_list.md",
            "package_readiness": package_readiness,
        },
        "preserved_facts": {
            "baseline_file": baseline_path.name,
            "preserved_dois": [d for d in base_dois if d in rev_dois],
            "dropped_baseline_dois": dropped_dois,
            "preserved_numeric_tokens_count": len(preserved_nums),
            "preserved_numeric_tokens_sample": preserved_nums[:25],
        },
        "evidence_map": evidence_map,
        "change_log": change_log,
        "metrics": {
            "expected_comments": len(expected_ids),
            "answered_comments": len(expected_ids) - len(missing_responses),
            "triple_bound_edits_verified": triple_bound_verified_count,
            "verified_quotes_count": verified_quotes_total,
            "changed_revised_lines_count": len(changed_set),
            "missing_evidence_required": len(missing_evidence_required_ids),
            "missing_evidence_closed": len(missing_evidence_closed),
            "hard_errors_count": len(errors),
            "style_warnings_count": len(style_warnings),
        },
        "metric_provenance": {
            "expected_comments": comments_path.name,
            "answered_comments": response_path.name,
            "triple_bound_edits_verified": "rebuttal_ledger.json:triple_bound_edits_verified",
            "verified_quotes_count": "rebuttal_ledger.json:verified_quotes_count",
            "changed_revised_lines_count": f"diff:{baseline_path.name}->{revised_path.name}",
            "missing_evidence_required": response_path.name,
            "missing_evidence_closed": me_file.name if me_file else response_path.name,
            "hard_errors_count": "rebuttal_ledger.json:errors",
            "style_warnings_count": "rebuttal_ledger.json:style_warnings",
        },
        "gaps": gaps_list,
    }

    return {
        "passed": len(errors) == 0,
        "package_readiness": package_readiness,
        "expected_comment_ids": expected_ids,
        "answered_comment_ids": [cid for cid in expected_ids if cid not in missing_responses],
        "missing_responses": missing_responses,
        "missing_dispositions": missing_dispositions,
        "empty_responses": empty_responses,
        "verified_quotes_count": verified_quotes_total,
        "triple_bound_edits_verified": triple_bound_verified_count,
        "unmatched_quotes": unmatched_quotes,
        "invalid_line_refs": invalid_line_refs,
        "invalid_section_refs": invalid_section_refs,
        "anchor_quote_mismatches": anchor_quote_mismatches,
        "fake_edit_violations": fake_edit_violations,
        "missing_evidence_required_ids": missing_evidence_required_ids,
        "missing_evidence_closed_ids": missing_evidence_closed,
        "missing_evidence_gaps": missing_evidence_gaps,
        "fabrication_hits": fabrication_hits,
        "overpromise_hits": overpromise_hits,
        "rebuke_hits": rebuke_hits,
        "cross_reviewer_leaks": cross_reviewer_leaks,
        "dropped_baseline_dois": dropped_dois,
        "diff_summary": {
            "has_substantive_edits": diff_info["has_substantive_edits"],
            "changed_revised_lines": diff_info["changed_revised_lines"],
            "touched_revised_lines": diff_info["touched_revised_lines"],
            "token_hunks_count": len(diff_info["token_hunks"]),
            "unified_diff_lines": diff_info["unified_diff_lines"],
        },
        "per_comment_ledger": per_comment_ledger,
        "tone_alignment_warnings": tone_alignment_warnings,
        "disposition_alias_warnings": disposition_alias_warnings,
        "style_warnings": style_warnings,
        "errors": errors,
        "contract_patch": contract_patch,
    }


def render_markdown_report(rep: dict[str, Any]) -> str:
    """Render concise human-readable rebuttal_closure_report.md."""
    status = "PASS" if rep["passed"] else "FAIL"
    lines: list[str] = [
        f"# Rebuttal Closure Audit Report ({status})",
        "",
        f"- **Package Readiness**: `{rep['package_readiness']}`",
        f"- **Comments Answered**: {len(rep['answered_comment_ids'])}/{len(rep['expected_comment_ids'])}",
        f"- **Triple-Bound Edits Verified**: {rep['triple_bound_edits_verified']}",
        f"- **Verified Verbatim Quotes**: {rep['verified_quotes_count']}",
        f"- **Substantive Changed Lines (Revised)**: {len(rep['diff_summary']['changed_revised_lines'])}",
        f"- **Missing-Evidence Closed**: {len(rep['missing_evidence_closed_ids'])}/{len(rep['missing_evidence_required_ids'])}",
        f"- **Hard Errors**: {len(rep['errors'])} | **Style Warnings**: {len(rep['style_warnings'])}",
        "",
        "## Per-Comment Closure Matrix",
        "",
        "| Comment ID | Disposition | Tone Tier | Anchors | Diff Verified | Missing-Evidence Closed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rep["per_comment_ledger"]:
        anc = ", ".join(row.get("anchors") or []) or "-"
        lines.append(
            f"| {row['comment_id']} | `{row.get('disposition') or 'MISSING'}` | `{row.get('tone_tier') or '-'}` | "
            f"{anc} | {'Yes' if row.get('diff_verified') else 'No'} | "
            f"{'Yes' if row.get('missing_evidence_closed') else 'No'} |"
        )

    if rep["errors"]:
        lines.extend(["", "## Blocking Errors", ""])
        for err in rep["errors"]:
            lines.append(f"- {err}")

    if rep["style_warnings"]:
        lines.extend(["", "## Prose & Anti-AI Style Warnings", ""])
        for w in rep["style_warnings"][:30]:
            lines.append(f"- Line {w['line']} [{w['category']}]: `{w['match']}` -> {w['suggestion']}")

    lines.append("")
    return "\n".join(lines)


def extract_revised_anchors(baseline_path: Path, revised_path: Path) -> dict[str, Any]:
    """Deterministically extract exact [§Section Lstart-Lend] anchors & verbatim snippets from baseline->revised diff."""
    base_txt = baseline_path.read_text(encoding="utf-8", errors="replace")
    rev_txt = revised_path.read_text(encoding="utf-8", errors="replace")
    diff_info = build_token_diff(base_txt, rev_txt)
    rev_idx = build_revised_index(rev_txt)
    rev_lines = rev_idx["lines"]
    sections = rev_idx["sections"]

    def _section_for_line(ln: int) -> str:
        for sec in sections:
            if sec["start_line"] <= ln <= sec["end_line"]:
                return sec["title"]
        return "Document"

    anchors: list[dict[str, Any]] = []
    for idx, hunk in enumerate(diff_info["token_hunks"], 1):
        span = hunk.get("revised_lines") or []
        if not span:
            continue
        l_start, l_end = min(span), max(span)
        sec_title = _section_for_line(l_start)
        loc_tag = f"[§{sec_title} L{l_start}]" if l_start == l_end else f"[§{sec_title} L{l_start}-L{l_end}]"
        verbatim_lines = "\n".join(rev_lines[l_start - 1 : l_end]).strip()
        anchors.append({
            "hunk_id": f"H{idx}",
            "op": hunk["op"],
            "section": sec_title,
            "start_line": l_start,
            "end_line": l_end,
            "anchor_tag": loc_tag,
            "verbatim_revised_excerpt": verbatim_lines[:300],
            "removed_excerpt": hunk.get("removed_excerpt", ""),
        })
    return {
        "has_substantive_edits": diff_info["has_substantive_edits"],
        "revised_line_count": rev_idx["line_count"],
        "changed_revised_lines": diff_info["changed_revised_lines"],
        "anchors": anchors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit 1:1:1 peer-review rebuttal closure, triple-bound edits, missing evidence, and tone."
    )
    parser.add_argument("--extract-anchors", action="store_true", help="Extract exact [§Section Lstart-Lend] anchors from baseline->revised diff (no manual line counting).")
    parser.add_argument("--baseline", required=True, help="Path to materials/manuscript-baseline.md")
    parser.add_argument("--comments", default="", help="Path to materials/reviewer-comments.md (required unless --extract-anchors)")
    parser.add_argument("--revised", required=True, help="Path to revised manuscript markdown.")
    parser.add_argument("--response", default="", help="Path to point-by-point response letter (required unless --extract-anchors)")
    parser.add_argument("--missing-evidence", default="", help="Optional path to missing_evidence_list.md.")
    parser.add_argument("--case-id", default="", help="Optional case_id embedded in contract patch.")
    parser.add_argument("--out-json", required=True, help="Output closure ledger or extracted anchors JSON path.")
    parser.add_argument("--out-report", default="", help="Optional Markdown closure report path.")
    parser.add_argument("--out-contract-patch", default="", help="Optional result.json contract patch JSON path.")
    args = parser.parse_args(argv)

    if args.extract_anchors:
        ext = extract_revised_anchors(Path(args.baseline), Path(args.revised))
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(ext, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"has_substantive_edits": ext["has_substantive_edits"], "anchors_count": len(ext["anchors"])}, ensure_ascii=False, indent=2))
        return 0 if ext["has_substantive_edits"] else 1

    if not args.comments or not args.response:
        parser.error("--comments and --response are required unless --extract-anchors is specified.")

    rep = audit_rebuttal_closure(
        Path(args.baseline),
        Path(args.comments),
        Path(args.revised),
        Path(args.response),
        Path(args.missing_evidence) if args.missing_evidence else None,
        case_id=args.case_id,
    )

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.out_report:
        rp = Path(args.out_report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(render_markdown_report(rep), encoding="utf-8")

    if args.out_contract_patch:
        cp = Path(args.out_contract_patch)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(rep["contract_patch"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "passed": rep["passed"],
        "package_readiness": rep["package_readiness"],
        "expected_ids": len(rep["expected_comment_ids"]),
        "answered_ids": len(rep["answered_comment_ids"]),
        "triple_bound_edits_verified": rep["triple_bound_edits_verified"],
        "missing_evidence_closed": f"{len(rep['missing_evidence_closed_ids'])}/{len(rep['missing_evidence_required_ids'])}",
        "errors": rep["errors"],
        "style_warnings_count": len(rep["style_warnings"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
