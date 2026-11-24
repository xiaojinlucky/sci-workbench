#!/usr/bin/env python3
"""Glossary-aware academic anti-translationese and de-AI lexical lint gate.

Combines Chinese translationese markers, structural AI cliché baselines, and academic
anti-filler blacklists while masking locked glossary terms and numeric expressions to
prevent false positives on domain terminology.

Supports Markdown (.md), plain text (.txt/.tex), and structured translation JSON
(scanning bilingual_segments[].target_zh with exact paragraph_id reporting).
Pure Python 3.12 standard library.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

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
    "显著提升了",
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

# Chinese translationese markers (pattern_name -> (regex, severity, rewrite_tip))
TRANSLATIONESE_RULES: dict[str, tuple[re.Pattern[str], str, str]] = {
    "light_verb_nominalization": (
        re.compile(r"(?:进行|作出|给予|予以)了?(?:一个|一项|系统性的|深入的|全面的)?[\u4e00-\u9fff]{0,8}的?(?:分析|调整|优化|支持|评估|检查|讨论|探讨|研究|验证)"),
        "high",
        "Replace light-verb nominalization (进行了…评估) with a direct active scientific verb (评估了…).",
    ),
    "awkward_passive_shell": (
        re.compile(r"被[\u4e00-\u9fffA-Za-z0-9]{1,14}所?(?:证实|证明|认为|视为|称为|设计为|应用于|赋予|看作)"),
        "high",
        "Convert translationese passive (被…所证实) into active subject-verb or natural Chinese phrasing.",
    ),
    "contextual_framing_shell": (
        re.compile(r"在[\u4e00-\u9fffA-Za-z0-9]{2,20}(?:的背景下|的过程中|的情况下|的框架下)"),
        "medium",
        "Tighten framing shell (在…的背景下/过程中) to concise condition or temporal phrasing (…期间 / 在…条件下).",
    ),
    "topic_shell_prefix": (
        re.compile(r"(?:对于[\u4e00-\u9fffA-Za-z0-9]{2,15}来说|对[\u4e00-\u9fffA-Za-z0-9]{2,15}而言|就[\u4e00-\u9fffA-Za-z0-9]{2,15}而言|在[\u4e00-\u9fffA-Za-z0-9]{2,12}方面)"),
        "low",
        "Check if topic shell (对于…来说/而言) can be promoted to a direct grammatical subject.",
    ),
    "manner_literal_calque": (
        re.compile(r"(?:以一种[\u4e00-\u9fff]{2,12}的(?:方式|形式)|通过采用[\u4e00-\u9fff]{2,10}的方式)"),
        "high",
        "Replace literal manner calque (以一种…的方式) with a direct adverb or method clause.",
    ),
    "enable_calque": (
        re.compile(r"使得?[\u4e00-\u9fffA-Za-z0-9]{1,12}(?:能够|可以)"),
        "low",
        "Simplify '使得…能够' to '使…可' or an active verb where appropriate.",
    ),
    "play_role_calque": (
        re.compile(r"(?:扮演|承担)了?[\u4e00-\u9fff]{0,8}角色"),
        "medium",
        "Replace '扮演…角色' with '起…作用' or state the concrete mechanistic function.",
    ),
    "exist_calque": (
        re.compile(r"(?:存在着|有着)"),
        "low",
        "Trim redundant aspect marker: use '存在' or '具有'.",
    ),
    "plural_or_degree_filler": (
        re.compile(r"(?:一系列的|各种各样的|在某种程度上|从某种意义上说|在很大程度上|相对而言)"),
        "medium",
        "Replace vague plural/degree calque with exact count, scope, or measured magnitude.",
    ),
    "chained_attributives": (
        re.compile(r"的[\u4e00-\u9fff]{1,8}的[\u4e00-\u9fff]{1,8}的"),
        "medium",
        "Split triple-chained '的…的…的' modifier into two crisp clauses.",
    ),
    "long_pre_modifier": (
        re.compile(r"(?:一个|一种|一套|这种|这个)[^，。、；：！？\n]{16,}的[\u4e00-\u9fff]{2,6}"),
        "medium",
        "Unpack overly long pre-nominal modifier (>16 chars) into sequential Chinese clauses.",
    ),
    "not_but_template": (
        re.compile(r"(?:不是|并非)[^，。]{1,18}[，,]?而是"),
        "low",
        "Verify '并非…而是' reflects an explicit contrast in source rather than decorative AI rhetoric.",
    ),
    "conditional_calque": (
        re.compile(r"(?:当[\u4e00-\u9fff]{2,18}的时候，|如果[\u4e00-\u9fff]{2,18}的话)"),
        "medium",
        "Tighten '当…的时候' / '如果…的话' to '…时' / '若…'.",
    ),
}

PLAIN_TEXT_MD_RE = re.compile(r"(?:\*\*[^*\n]+\*\*|`[^`\n]+`|^\s{0,3}#{1,6}\s|^\s*---+\s*$)")


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
    # Mask inline LaTeX and citations so formulas/refs are never flagged
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
    plain_text: bool = False,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    masked = _mask_protected(text, whitelist)
    low = masked.lower()

    matched_zh: list[str] = []
    for phrase in sorted(AI_ZH_CLICHES, key=len, reverse=True):
        if phrase in masked and not any(phrase in prev for prev in matched_zh):
            matched_zh.append(phrase)
            findings.append({
                "location": location,
                "severity": "high",
                "rule": "zh_ai_cliche",
                "match": phrase,
                "suggestion": f"Remove or replace AI template filler '{phrase}' with direct scientific evidence.",
            })

    for phrase in AI_EN_CLICHES:
        if phrase in low:
            findings.append({
                "location": location,
                "severity": "high",
                "rule": "en_ai_cliche",
                "match": phrase,
                "suggestion": f"Replace inflated English filler '{phrase}' with a precise domain verb.",
            })

    for rule_name, (rx, sev, tip) in TRANSLATIONESE_RULES.items():
        for m in rx.finditer(masked):
            findings.append({
                "location": location,
                "severity": sev,
                "rule": rule_name,
                "match": m.group(0),
                "suggestion": tip,
            })

    if plain_text and PLAIN_TEXT_MD_RE.search(text):
        findings.append({
            "location": location,
            "severity": "medium",
            "rule": "residual_markdown_in_plain_text",
            "match": text.strip()[:80],
            "suggestion": "Strip raw Markdown syntax markers (**, `, #, ---) for plain-text output.",
        })
    return findings


def _collect_targets(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(
        p
        for p in target.rglob("*")
        if p.is_file() and p.suffix.lower() in {".md", ".txt", ".tex", ".json"}
    )


def lint_academic_text(
    target: Path,
    *,
    glossary_path: Path | None = None,
    plain_text: bool = False,
) -> dict[str, Any]:
    files = _collect_targets(target)
    if not files:
        return {"passed": False, "error": f"No scannable files found under {target}", "findings_count": 0, "findings": []}

    whitelist = _load_whitelist(glossary_path)
    findings: list[dict[str, Any]] = []
    total_zh_chars = 0

    for fp in files:
        rel = fp.name if target.is_file() else fp.relative_to(target).as_posix()
        if fp.suffix.lower() == ".json":
            try:
                obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                return {"passed": False, "error": f"Invalid JSON in {rel}: {exc}", "findings_count": 0, "findings": []}
            segs = obj.get("bilingual_segments") if isinstance(obj, dict) else None
            if isinstance(segs, list):
                for idx, seg in enumerate(segs, start=1):
                    if not isinstance(seg, dict):
                        continue
                    pid = str(seg.get("paragraph_id") or f"P{idx:03d}")
                    zh = str(seg.get("target_zh") or "")
                    total_zh_chars += len(re.findall(r"[\u4e00-\u9fff]", zh))
                    findings.extend(_scan_unit(zh, location=f"{rel}:{pid}", whitelist=whitelist, plain_text=plain_text))
                continue

        text = fp.read_text(encoding="utf-8", errors="replace")
        for lno, raw in enumerate(text.splitlines(), start=1):
            st = raw.strip()
            if not st or st.startswith("**Source ("):
                continue
            total_zh_chars += len(re.findall(r"[\u4e00-\u9fff]", st))
            findings.extend(_scan_unit(st, location=f"{rel}:L{lno}", whitelist=whitelist, plain_text=plain_text))

    high_cnt = sum(1 for f in findings if f["severity"] == "high")
    med_cnt = sum(1 for f in findings if f["severity"] == "medium")
    per_1k = round(len(findings) / max(total_zh_chars / 1000.0, 0.1), 2) if total_zh_chars > 0 else 0.0

    return {
        "passed": high_cnt == 0 and med_cnt == 0,
        "files_scanned": [p.name if target.is_file() else p.relative_to(target).as_posix() for p in files],
        "zh_char_count": total_zh_chars,
        "findings_count": len(findings),
        "high_severity_count": high_cnt,
        "medium_severity_count": med_cnt,
        "density_per_1k_zh_chars": per_1k,
        "whitelist_terms_loaded": len(whitelist),
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Glossary-aware anti-translationese and de-AI academic linter.")
    parser.add_argument("--input", required=True, help="Target file or directory (.md, .txt, .tex, or bilingual JSON).")
    parser.add_argument("--glossary", default="", help="Optional glossary.json to protect locked domain terms.")
    parser.add_argument("--out-json", default="", help="Optional JSON report path.")
    parser.add_argument("--plain-text", action="store_true", help="Flag residual Markdown syntax in plain-text sections.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when any high/medium finding is detected.")
    args = parser.parse_args(argv)

    rep = lint_academic_text(
        Path(args.input),
        glossary_path=Path(args.glossary) if args.glossary else None,
        plain_text=args.plain_text,
    )
    if args.out_json:
        out_p = Path(args.out_json)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": rep["passed"],
        "findings_count": rep.get("findings_count", 0),
        "high_severity_count": rep.get("high_severity_count", 0),
        "medium_severity_count": rep.get("medium_severity_count", 0),
        "density_per_1k_zh_chars": rep.get("density_per_1k_zh_chars", 0.0),
    }, ensure_ascii=False))
    if rep.get("error"):
        return 2
    if args.strict and not rep["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
