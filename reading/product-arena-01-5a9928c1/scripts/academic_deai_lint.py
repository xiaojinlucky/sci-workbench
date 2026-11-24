#!/usr/bin/env python3
"""Glossary-aware, source-target comparative academic style & de-AI advisory linter.

Operates in **source-target comparative advisory mode**:
- Emits non-blocking style/translationese `advisories` (never blocks empirical scientific
  conclusions such as "显著提升了" / "significantly increased" that reflect source statistics).
- Supports Markdown (.md with **Source (`Pxxx`)** / **Translation (`Pxxx`)** pairing),
  plain text (.txt), and structured bilingual JSON (pairing source_en with target_zh).
- Masks locked glossary terms, inline formulas, and citations to prevent domain false positives.

Pure Python 3.12 standard library.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

# Pure rhetorical / bureaucratic AI clichés (empirical statistical verbs like "显著提升了" are intentionally EXCLUDED)
AI_ZH_CLICHES: tuple[str, ...] = (
    "综上所述",
    "总而言之",
    "值得注意的是",
    "有必要指出的是",
    "需要指出的是",
    "显而易见",
    "毋庸置疑",
    "显著赋能",
    "赋能",
    "抓手",
    "卡点",
    "无缝衔接",
    "全方位",
    "深层次",
    "不可或缺的",
    "至关重要的作用",
    "不仅如此",
    "深刻揭示了",
    "值得一提",
)

AI_EN_CLICHES: tuple[str, ...] = (
    "delve into",
    "tapestry of",
    "testament to",
    "pivotal role",
    "in the realm of",
    "underscores the importance",
    "a comprehensive understanding",
    "sheds light on",
    "it is worth noting that",
    "in today's rapidly evolving",
    "multifaceted",
    "game-changer",
)

# Empirical statistical phrasing in target Chinese & source English (protected from false-positive deletion)
EMPIRICAL_STAT_ZH_RE = re.compile(
    r"(?:显著(?:提升|上升|增加|提高|下降|降低|减少|改善|高于|低于|差异|相关)了?|具有统计学意义|统计学显著)"
)
EMPIRICAL_STAT_EN_RE = re.compile(
    r"(?:\bsignificant(?:ly)?\s+(?:increase[sd]?|improv(?:ed|es|ing)|enhanc(?:ed|es|ing)|higher|lower|reduc(?:ed|es|ing)|decrease[sd]?|differ(?:ence|ed|s)?|associat(?:ed|ion))\b|"
    r"\bstatistically\s+significant\b|\bp\s*[<=>≤≥]\s*0?\.\d+)",
    re.I,
)

# Chinese translationese markers (pattern_name -> (regex, priority, rewrite_tip))
TRANSLATIONESE_RULES: dict[str, tuple[re.Pattern[str], str, str]] = {
    "light_verb_nominalization": (
        re.compile(r"(?:进行|作出|给予|予以)了?(?:一个|一项|系统性的|深入的|全面的)?[\u4e00-\u9fff]{0,8}的?(?:分析|调整|优化|支持|评估|检查|讨论|探讨|研究|验证)"),
        "recommended",
        "Advisory: consider replacing light-verb nominalization (进行了…评估) with a direct active scientific verb (评估了…) if it preserves source nuance.",
    ),
    "awkward_passive_shell": (
        re.compile(r"被[\u4e00-\u9fffA-Za-z0-9]{1,14}所?(?:证实|证明|认为|视为|称为|设计为|应用于|赋予|看作)"),
        "recommended",
        "Advisory: consider converting translationese passive (被…所证实) into natural active Chinese phrasing without altering voice/attribution.",
    ),
    "contextual_framing_shell": (
        re.compile(r"在[\u4e00-\u9fffA-Za-z0-9]{2,20}(?:的背景下|的过程中|的情况下|的框架下)"),
        "optional",
        "Advisory: consider tightening framing shell (在…的背景下/过程中) to concise condition/temporal phrasing (…期间 / 在…条件下).",
    ),
    "topic_shell_prefix": (
        re.compile(r"(?:对于[\u4e00-\u9fffA-Za-z0-9]{2,15}来说|对[\u4e00-\u9fffA-Za-z0-9]{2,15}而言|就[\u4e00-\u9fffA-Za-z0-9]{2,15}而言|在[\u4e00-\u9fffA-Za-z0-9]{2,12}方面)"),
        "optional",
        "Advisory: check if topic shell (对于…来说/而言) can be promoted to a direct grammatical subject.",
    ),
    "manner_literal_calque": (
        re.compile(r"(?:以一种[\u4e00-\u9fff]{2,12}的(?:方式|形式)|通过采用[\u4e00-\u9fff]{2,10}的方式)"),
        "recommended",
        "Advisory: consider replacing literal manner calque (以一种…的方式) with a direct adverb or method clause.",
    ),
    "enable_calque": (
        re.compile(r"使得?[\u4e00-\u9fffA-Za-z0-9]{1,12}(?:能够|可以)"),
        "optional",
        "Advisory: consider simplifying '使得…能够' to '使…可' or an active verb where appropriate.",
    ),
    "play_role_calque": (
        re.compile(r"(?:扮演|承担)了?[\u4e00-\u9fff]{0,8}角色"),
        "optional",
        "Advisory: consider replacing '扮演…角色' with '起…作用' or stating the concrete mechanistic function.",
    ),
    "exist_calque": (
        re.compile(r"(?:存在着|有着)"),
        "optional",
        "Advisory: consider trimming redundant aspect marker to '存在' or '具有'.",
    ),
    "plural_or_degree_filler": (
        re.compile(r"(?:一系列的|各种各样的|在某种程度上|从某种意义上说|在很大程度上|相对而言)"),
        "optional",
        "Advisory: verify against source whether vague degree filler can be replaced with exact scope or measured magnitude.",
    ),
    "chained_attributives": (
        re.compile(r"的[\u4e00-\u9fff]{1,8}的[\u4e00-\u9fff]{1,8}的"),
        "optional",
        "Advisory: consider splitting triple-chained '的…的…的' modifier into two crisp clauses.",
    ),
    "long_pre_modifier": (
        re.compile(r"(?:一个|一种|一套|这种|这个)[^，。、；：！？\n]{16,}的[\u4e00-\u9fff]{2,6}"),
        "optional",
        "Advisory: consider unpacking long pre-nominal modifier (>16 chars) into sequential Chinese clauses.",
    ),
    "not_but_template": (
        re.compile(r"(?:不是|并非)[^，。]{1,18}[，,]?而是"),
        "optional",
        "Advisory: verify '并非…而是' reflects an explicit contrast in source rather than decorative rhetoric.",
    ),
    "conditional_calque": (
        re.compile(r"(?:当[\u4e00-\u9fff]{2,18}的时候，|如果[\u4e00-\u9fff]{2,18}的话)"),
        "optional",
        "Advisory: consider tightening '当…的时候' / '如果…的话' to '…时' / '若…'.",
    ),
}

PLAIN_TEXT_MD_RE = re.compile(r"(?:\*\*[^*\n]+\*\*|`[^`\n]+`|^\s{0,3}#{1,6}\s|^\s*---+\s*$)")
_MD_SOURCE_LINE_RE = re.compile(r"^\*\*Source\s*\(`([A-Za-z0-9_.-]+)`\)\*\*:\s*(.*)$")
_MD_TRANS_LINE_RE = re.compile(r"^\*\*Translation\s*\(`([A-Za-z0-9_.-]+)`\)\*\*:\s*(.*)$")


def _load_whitelist(glossary_path: Path | None) -> list[str]:
    if not glossary_path or not glossary_path.is_file():
        return []
    try:
        data = json.loads(glossary_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    entries = data.get("terms", data) if isinstance(data, dict) else data
    terms: list[str] = []
    if isinstance(entries, list):
        for item in entries:
            if isinstance(item, dict):
                for k in ("target", "source", "first_mention_anchor"):
                    val = str(item.get(k) or "").strip()
                    if len(val) >= 2:
                        terms.append(val)
    return sorted(set(terms), key=len, reverse=True)


def _mask_protected(text: str, whitelist: list[str]) -> str:
    masked = text
    masked = re.sub(r"\$[^$\n]+\$", " [FORMULA] ", masked)
    masked = re.sub(r"\[(?:\d+(?:\s*[-,]\s*\d+)*)\]", " ", masked)
    for term in whitelist:
        masked = masked.replace(term, " [TERM] ")
    return masked


def _scan_unit(
    text: str,
    *,
    location: str,
    whitelist: list[str],
    source_text: str = "",
    plain_text: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Scan one target segment against its paired source text; returns (advisories, preserved_empirical_stats)."""
    advisories: list[dict[str, Any]] = []
    preserved_stats: list[dict[str, Any]] = []
    masked = _mask_protected(text, whitelist)
    low = masked.lower()
    src_has_stat = bool(EMPIRICAL_STAT_EN_RE.search(source_text or ""))

    # Record empirical statistical phrasing preserved faithfully from source (never flagged)
    for m_stat in EMPIRICAL_STAT_ZH_RE.finditer(text):
        preserved_stats.append({
            "location": location,
            "expression": m_stat.group(0),
            "source_grounded_statistical_cue": src_has_stat,
            "disposition": "preserved_empirical_finding",
        })

    matched_zh: list[str] = []
    for phrase in sorted(AI_ZH_CLICHES, key=len, reverse=True):
        if phrase in masked and not any(phrase in prev for prev in matched_zh):
            matched_zh.append(phrase)
            advisories.append({
                "location": location,
                "severity": "advisory",
                "priority": "recommended",
                "blocking": False,
                "rule": "zh_ai_cliche_advisory",
                "match": phrase,
                "source_excerpt": (source_text or "")[:140],
                "suggestion": (
                    f"Source-target advisory: verify whether '{phrase}' is backed by source text; "
                    "if added as filler, replace with direct scientific phrasing."
                ),
            })

    for phrase in AI_EN_CLICHES:
        if phrase in low:
            # Skip if the exact phrase literally appears in the source English text
            if source_text and phrase in source_text.lower():
                continue
            advisories.append({
                "location": location,
                "severity": "advisory",
                "priority": "recommended",
                "blocking": False,
                "rule": "en_ai_cliche_advisory",
                "match": phrase,
                "source_excerpt": (source_text or "")[:140],
                "suggestion": f"Source-target advisory: consider replacing inflated English filler '{phrase}' with a precise domain verb.",
            })

    for rule_name, (rx, prio, tip) in TRANSLATIONESE_RULES.items():
        for m in rx.finditer(masked):
            advisories.append({
                "location": location,
                "severity": "advisory",
                "priority": prio,
                "blocking": False,
                "rule": rule_name,
                "match": m.group(0),
                "source_excerpt": (source_text or "")[:140],
                "suggestion": tip,
            })

    if plain_text and PLAIN_TEXT_MD_RE.search(text):
        advisories.append({
            "location": location,
            "severity": "advisory",
            "priority": "recommended",
            "blocking": False,
            "rule": "residual_markdown_in_plain_text",
            "match": text.strip()[:80],
            "source_excerpt": (source_text or "")[:140],
            "suggestion": "Strip raw Markdown syntax markers (**, `, #, ---) if plain-text output is required.",
        })
    return advisories, preserved_stats


def _collect_targets(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(
        p
        for p in target.rglob("*")
        if p.is_file() and p.suffix.lower() in {".md", ".txt", ".json"}
    )


def lint_academic_text(
    target: Path,
    *,
    glossary_path: Path | None = None,
    source_path: Path | None = None,
    plain_text: bool = False,
) -> dict[str, Any]:
    files = _collect_targets(target)
    if not files:
        return {
            "passed": False,
            "mode": "source_target_advisory",
            "error": f"No scannable files found under {target}",
            "advisories_count": 0,
            "advisories": [],
            "findings_count": 0,
            "findings": [],
        }

    whitelist = _load_whitelist(glossary_path)
    external_source_text = (
        source_path.read_text(encoding="utf-8", errors="replace")
        if (source_path and source_path.is_file())
        else ""
    )
    advisories: list[dict[str, Any]] = []
    preserved_stats: list[dict[str, Any]] = []
    total_zh_chars = 0

    for fp in files:
        rel = fp.name if target.is_file() else fp.relative_to(target).as_posix()
        if fp.suffix.lower() == ".json":
            try:
                obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                return {
                    "passed": False,
                    "mode": "source_target_advisory",
                    "error": f"Invalid JSON in {rel}: {exc}",
                    "advisories_count": 0,
                    "advisories": [],
                    "findings_count": 0,
                    "findings": [],
                }
            segs = obj.get("bilingual_segments") if isinstance(obj, dict) else None
            if isinstance(segs, list):
                for idx, seg in enumerate(segs, start=1):
                    if not isinstance(seg, dict):
                        continue
                    pid = str(seg.get("paragraph_id") or f"P{idx:03d}")
                    src = str(seg.get("source_en") or external_source_text)
                    zh = str(seg.get("target_zh") or "")
                    total_zh_chars += len(re.findall(r"[\u4e00-\u9fff]", zh))
                    adv, pstat = _scan_unit(zh, location=f"{rel}:{pid}", whitelist=whitelist, source_text=src, plain_text=plain_text)
                    advisories.extend(adv)
                    preserved_stats.extend(pstat)
                continue

        text = fp.read_text(encoding="utf-8", errors="replace")
        paired_sources: dict[str, str] = {}
        for lno, raw in enumerate(text.splitlines(), start=1):
            st = raw.strip()
            if not st:
                continue
            m_src = _MD_SOURCE_LINE_RE.match(st)
            if m_src:
                paired_sources[m_src.group(1)] = m_src.group(2).strip()
                continue
            m_tr = _MD_TRANS_LINE_RE.match(st)
            if m_tr:
                pid, zh_body = m_tr.group(1), m_tr.group(2).strip()
                src_pair = paired_sources.get(pid, external_source_text)
                total_zh_chars += len(re.findall(r"[\u4e00-\u9fff]", zh_body))
                adv, pstat = _scan_unit(zh_body, location=f"{rel}:{pid}", whitelist=whitelist, source_text=src_pair, plain_text=plain_text)
                advisories.extend(adv)
                preserved_stats.extend(pstat)
                continue
            total_zh_chars += len(re.findall(r"[\u4e00-\u9fff]", st))
            adv, pstat = _scan_unit(st, location=f"{rel}:L{lno}", whitelist=whitelist, source_text=external_source_text, plain_text=plain_text)
            advisories.extend(adv)
            preserved_stats.extend(pstat)

    rec_cnt = sum(1 for a in advisories if a.get("priority") == "recommended")
    opt_cnt = sum(1 for a in advisories if a.get("priority") == "optional")
    per_1k = round(len(advisories) / max(total_zh_chars / 1000.0, 0.1), 2) if total_zh_chars > 0 else 0.0

    # Advisory mode: empirical scientific wording is never hard-blocked; blocking_errors is always empty for style notes.
    return {
        "passed": True,
        "mode": "source_target_advisory",
        "blocking_errors_count": 0,
        "blocking_errors": [],
        "files_scanned": [p.name if target.is_file() else p.relative_to(target).as_posix() for p in files],
        "zh_char_count": total_zh_chars,
        "advisories_count": len(advisories),
        "recommended_advisories_count": rec_cnt,
        "optional_advisories_count": opt_cnt,
        "findings_count": len(advisories),
        "high_severity_count": 0,
        "medium_severity_count": 0,
        "density_per_1k_zh_chars": per_1k,
        "whitelist_terms_loaded": len(whitelist),
        "source_grounded_statistical_preserved": preserved_stats,
        "advisories": advisories,
        "findings": advisories,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Glossary-aware source-target comparative academic style advisory linter.")
    parser.add_argument("--input", required=True, help="Target file or directory (.md, .txt, or bilingual JSON).")
    parser.add_argument("--source", default="", help="Optional source text file for source-target comparative context.")
    parser.add_argument("--glossary", default="", help="Optional glossary.json to protect locked domain terms.")
    parser.add_argument("--out-json", default="", help="Optional JSON report path.")
    parser.add_argument("--plain-text", action="store_true", help="Emit advisories on residual Markdown syntax in plain-text sections.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 only on blocking errors (style advisories never block empirical conclusions).")
    args = parser.parse_args(argv)

    rep = lint_academic_text(
        Path(args.input),
        glossary_path=Path(args.glossary) if args.glossary else None,
        source_path=Path(args.source) if args.source else None,
        plain_text=args.plain_text,
    )
    if args.out_json:
        out_p = Path(args.out_json)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": rep["passed"],
        "mode": rep.get("mode", "source_target_advisory"),
        "advisories_count": rep.get("advisories_count", 0),
        "recommended_advisories_count": rep.get("recommended_advisories_count", 0),
        "blocking_errors_count": rep.get("blocking_errors_count", 0),
        "source_grounded_statistical_preserved": len(rep.get("source_grounded_statistical_preserved") or []),
        "density_per_1k_zh_chars": rep.get("density_per_1k_zh_chars", 0.0),
    }, ensure_ascii=False))
    if rep.get("error"):
        return 2
    if args.strict and rep.get("blocking_errors_count", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
