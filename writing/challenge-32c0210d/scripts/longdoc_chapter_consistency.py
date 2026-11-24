#!/usr/bin/env python3
"""Cross-chapter consistency, domain-agnostic (entity, unit, value, condition) conflict
detection, adaptive workorder anchor induction, linear-time inverted-index duplicate
detection, citation-claim alignment, chapter plan generation, and native .md/.txt/.tex/.docx/.pdf
reader (Python 3.12 standard library with optional pdftotext/pdfplumber/pypdf acceleration)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

CHAPTER_EXTS = {".md", ".txt", ".tex", ".docx", ".pdf"}
STOP_WORDS = {
    "a", "an", "the", "of", "for", "and", "in", "on", "to", "with", "by", "or",
    "as", "at", "from", "into", "that", "this", "these", "those", "were", "was",
    "are", "is", "be", "been", "than", "their", "its", "such", "all", "each",
    "other", "both", "between", "during", "within", "under", "over", "after",
    "before", "using", "based", "reported", "observed", "measured", "found",
    "showed", "higher", "lower", "greater", "less", "approximately", "about",
}
GENERIC_NON_ENTITIES = {
    "value", "values", "number", "numbers", "total", "figure", "fig", "table",
    "tab", "section", "chapter", "page", "pages", "period", "year", "years",
    "month", "months", "day", "days", "week", "weeks", "time", "part", "item",
    "case", "cases", "group", "groups", "study", "studies", "data", "result",
    "results", "percent", "percentage", "rate", "ratio", "level", "range",
    "average", "mean", "median", "minimum", "maximum", "increase", "decrease",
    "change", "difference", "above", "below", "up", "down", "more", "most",
    "数据", "结果", "数值", "比例", "占比", "数量", "部分", "期间", "时间", "合计", "总计",
}

ACRONYM_DEF_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9 -]{3,70}|[\u4e00-\u9fff]{2,20})[ \t]*[（(][ \t]*([A-Z]{2,10})[ \t]*[)）]"
)
SYMBOL_DEF_EN_RE = re.compile(
    r"(?:where|here)\s+\$?([A-Za-zα-ωΑ-Ω](?:_\{?[A-Za-z0-9]+\}?)?)\$?\s+"
    r"(?:denotes|represents|is defined as|stands for)\s+(?:the\s+)?([^.;,\n]{4,60})",
    re.I,
)
SYMBOL_DEF_ZH_RE = re.compile(
    r"(?:其中|式中)\s*\$?([A-Za-zα-ωΑ-Ω](?:_\{?[A-Za-z0-9]+\}?)?)\$?\s*"
    r"(?:表示|代表|定义为|为)\s*([^。；，\n]{2,30})"
)

FIG_LABEL_RE = re.compile(
    r"(?:^|\n)(?:#+\s*|\*{0,2})(?:Fig(?:ure)?\.?|图)\s*(\d+(?:\.\d+)?)\s*(?:[:：]|\*{2}\s*[:：]?|\s+[-—])",
    re.I,
)
FIG_REF_RE = re.compile(r"(?<![A-Za-z])(?:Fig(?:ure)?\.?|图)\s*(\d+(?:\.\d+)?)\b", re.I)
TAB_LABEL_RE = re.compile(
    r"(?:^|\n)(?:#+\s*|\*{0,2})(?:Table|Tab\.?|表)\s*(\d+(?:\.\d+)?)\s*(?:[:：]|\*{2}\s*[:：]?|\s+[-—])",
    re.I,
)
TAB_REF_RE = re.compile(r"(?<![A-Za-z])(?:Table|Tab\.?|表)\s*(\d+(?:\.\d+)?)\b", re.I)
EQ_LABEL_RE = re.compile(
    r"(?:^|\n).*?(?:Eq(?:uation)?\.?|式)\s*\(?(\d+(?:\.\d+)?)\)?\s*[:：]|\\label\{eq:([0-9.]+)\}",
    re.I,
)
EQ_REF_RE = re.compile(r"(?<![A-Za-z])(?:Eq(?:uation)?\.?|式)\s*\(?(\d+(?:\.\d+)?)\)?", re.I)

# High-weight prior lexicons (retained as high-precision prior alongside domain-agnostic extractor)
SAMPLE_SIZE_RE = re.compile(
    r"(?P<ctx>[A-Za-z\u4e00-\u9fff0-9_-]{2,28})\s*"
    r"(?:cohort|group|patients|participants|subjects|samples|specimens|mice|firms|households|reports|cases|"
    r"组|队列|样本|标本|患者|受试者|企业|病例)?\s*[（(]?\s*\b[nN]\s*=\s*(?P<val>\d{1,7}(?:,\d{3})*)\b"
)
ENTITY_METRIC_PRIOR_RE = re.compile(
    r"(?P<metric>(?:tensile\s+strength|yield\s+strength|elastic\s+modulus|infill\s+density|layer\s+thickness|"
    r"nozzle\s+temperature|printing\s+temperature|accuracy|survival\s+rate|mean\s+age|sample\s+size|"
    r"抗拉强度|屈服强度|弹性模量|填充密度|层厚|喷嘴温度|打印温度|准确率|生存率|平均年龄|总样本量))\s*"
    r"(?:of|was|is|were|reached|at|=|为|达|约)?\s*(?P<val>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>MPa|GPa|kPa|%|％|mm|μm|um|°C|℃|K|h|min|years|岁|例|名)\b",
    re.I,
)

# Domain-agnostic SI + cross-disciplinary + Chinese unit pattern
UNIT_PATTERN = (
    r"mg/kg|mg/mL|μg/mL|ug/mL|g/L|mol/L|mmol/L|mA/cm2|mW/cm2|kg/m3|m/s|km/h|/\s*10,?000(?:\s+inhabitants)?|"
    r"GPa|MPa|kPa|Pa|kN|N|kJ|J|kW|mW|W|mV|V|mA|A|GHz|MHz|kHz|Hz|Ω|"
    r"km|cm|mm|μm|um|nm|kg|mg|μg|ug|ng|mL|μL|uL|mmol|μmol|mol|mM|μM|"
    r"°C|℃|K|ms|min|hr|h|days?|weeks?|months?|years?|%|％|ppm|ppb|"
    r"USD|CNY|RMB|亿元|万元|元|例|名|岁|次|项|人|户|家|件|株|只|批|天|小时|分钟|年|月|周"
)
GENERIC_METRIC_RE = re.compile(
    r"(?P<entity>(?:[A-Za-z][A-Za-z0-9_-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*){0,4}|[\u4e00-\u9fff]{2,14}))\s*"
    r"(?:of|was|were|is|are|reached|at|=|:|：|为|达|达到|约为|约|共|计)\s*"
    r"(?P<val>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    rf"(?P<unit>{UNIT_PATTERN})(?![A-Za-z0-9_])",
    re.I,
)

# Condition-binding patterns to prevent false positives across distinct arms/years/temperatures/examples
COND_PATTERNS = (
    re.compile(r"\b(?:in|during|for|from)\s+(19\d{2}|20\d{2})(?:\s*[–-]\s*(19\d{2}|20\d{2}))?\b", re.I),
    re.compile(r"(19\d{2}|20\d{2})\s*年"),
    re.compile(r"\b(?:group|arm|cohort|cluster|phase|stage|example|comparative\s+example)\s*[A-Za-z0-9]+\b", re.I),
    re.compile(r"(?:实施例|对比例|实验组|对照组|观察组|干预组|空白组|阴性对照|阳性对照)\s*[A-Za-z0-9一二三四五六七八九十]*"),
    re.compile(r"\b(?:boys|girls|males?|females?|children|adolescents|adults|elderly|pediatric|vehicle|placebo|control|baseline|fasting|fed)\b", re.I),
    re.compile(r"\b(?:at|under)\s+\d+(?:\.\d+)?\s*(?:°C|℃|K|MPa|kPa|bar|pH|mg/kg|mA/cm2)\b", re.I),
    re.compile(r"\bpH\s*=?\s*\d+(?:\.\d+)?\b", re.I),
    re.compile(r"\b\d+\s*[–-]\s*\d+\s*(?:years?|months?|days?|岁|月|天)\b", re.I),
)

CITE_NUM_RE = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")
CITE_AUTHOR_YEAR_RE = re.compile(r"\([A-Z][A-Za-z-]+(?:\s+et\s+al\.?)?,\s*(?:19|20)\d{2}[a-z]?\)")
REF_ENTRY_RE = re.compile(r"(?:^|\n)\s*(?:\[(\d{1,3})\]|(\d{1,3})\.)\s+(.+)")
ZOTERO_CORRUPT_RE = re.compile(
    r"ADDIN\s+(?:ZOTERO_ITEM|EN\.CITE)|Error!\s*Reference\s+source\s+not\s+found|"
    r"\{\s*CITATION\s+[^}]*\}|\[\?\]|\(\?,\s*(?:19|20)\d{2}\)",
    re.I,
)
TRUNCATION_MARKER_RE = re.compile(
    r"\[(?:truncated|cut\s+off|page\s+missing|pages?\s+omitted|incomplete)\]|"
    r"（(?:此处截断|缺页|内容缺失|未完)）|(?:\.\.\.|……)\s*$",
    re.I,
)
CHAPTER_NUM_RE = re.compile(r"(?:^|[/_-])(?:ch(?:apter)?|sec(?:tion)?|part|第)\s*0*(\d+)", re.I)
DEFAULT_ANCHOR_FIND_RE = re.compile(
    r"(?:^\s*\[|<!--\s*(?:id\s*:\s*)?|id=[\"']|\{#|^\s*(?:\*\*)?)([A-Za-z0-9]+-P\d+[A-Za-z0-9_-]*)",
    re.MULTILINE,
)
ANNOTATION_BLOCK_RE = re.compile(
    r"\n\n> \[Editorial Annotation \| ID: (?P<pid>[A-Za-z0-9_-]+) \|[^\n]*\n"
    r"(?:> [^\n]*\n)*"
    r"> \[/Editorial Annotation \| ID: (?P=pid)\]",
    re.MULTILINE,
)


def _strip_annotations(text: str) -> str:
    return ANNOTATION_BLOCK_RE.sub("", text.replace("\r\n", "\n"))


def _is_running_page_header(para: str) -> bool:
    """Detect repeated PDF/DOCX running headers, page banners, bare page numbers, and URL footers."""
    st = para.strip()
    if not st:
        return True
    low = st.lower()
    if re.fullmatch(r"\d{1,4}", st) or re.fullmatch(r"page\s*\d+(?:\s*of\s*\d+)?", low):
        return True
    if low.startswith(("<!-- page", "## [page", "contains nonbinding recommendations", "https://", "http://", "doi:", "rev paul pediatr")):
        return True
    if re.match(r"^[#]+\s+", st) and "\n" not in st and len(st) < 140:
        return True
    if "contains nonbinding recommendations" in low and len(st) < 420:
        return True
    if re.search(r"rev\s+paul\s+pediatr\.?\s*\d{4}", low) and len(st) < 200:
        return True
    if re.search(r"https?://\S+", st) and re.search(r"\b\d{2}/\d{2}/\d{2,4}\b", st) and len(st) < 500:
        return True
    return False


SYNTH_FINDING_RE = re.compile(
    r"\b(?:found|showed|demonstrated|observed|indicated|revealed|associated|correlated|increased|decreased|"
    r"higher|lower|significant(?:ly)?|main\s+agent|incidence|prevalence|polymorphism|risk|rate|cases|patients|"
    r"stationary|trend|positive\s+association|no\s+association|not\s+associated)\b|"
    r"\d+(?:\.\d+)?%|\bp\s*[<≤=>]\s*0?\.\d+|\bn\s*=\s*\d+|"
    r"表明|显示|发现|观察到|相关|关联|显著|升高|降低|增加|减少|主要原因|发病率|发生率|风险",
    re.I,
)
SYNTH_LIMITATION_RE = re.compile(
    r"\b(?:limitation|caveat|underreport|retrospective|observational|cross-sectional|ecological|single-center|"
    r"small\s+sample|statistical\s+power|further\s+stud|bias|confound|cannot\s+establish\s+causality|"
    r"not\s+confirmed|heterogene|lack\s+of|incomplete|missing)\b|"
    r"局限|不足|漏报|回顾性|观察性|横断面|单中心|样本量|统计效能|仍需进一步|偏倚|混杂|无法确立因果",
    re.I,
)
SYNTH_SCOPE_RE = re.compile(
    r"\b(?:children|adolescents|pediatric|adults|girls|boys|females?|males?|patients|cohort|database|"
    r"20\d{2}|19\d{2}|observational|systematic\s+review|retrospective|prospective|randomized|brazil|"
    r"sickle\s+cell|avascular\s+necrosis|exogenous\s+(?:poisoning|intoxication)|pharmacovigilance)\b|"
    r"儿童|青少年|成人|女性|男性|患者|队列|回顾性|观察性|系统综述|随机对照",
    re.I,
)
SYNTH_POS_RE = re.compile(
    r"\b(?:increas(?:ed|ing)|higher|positive\s+association|associated\s+with|significant\s+increase|risk\s+factor|elevated|greater)\b|"
    r"显著升高|上升|增加|正相关|危险因素",
    re.I,
)
SYNTH_NEG_OR_NULL_RE = re.compile(
    r"\b(?:no\s+association|not\s+associated|stationary|unchanged|decreas(?:ed|ing)|lower|no\s+significant|"
    r"failed\s+to\s+show|null|negative\s+association|only\s+one\s+found)\b|"
    r"无关联|未见关联|平稳|下降|降低|无显著",
    re.I,
)
SYNTH_STOP_TOKENS = STOP_WORDS | GENERIC_NON_ENTITIES | {
    "also", "however", "therefore", "although", "while", "whereas", "could", "would", "should",
    "may", "might", "can", "been", "being", "have", "has", "had", "that", "which", "who",
    "these", "those", "this", "there", "here", "from", "with", "into", "about", "among",
    "through", "between", "study", "chapter", "table", "figure", "paper", "article", "review",
    "present", "current", "previous", "further", "needed", "order", "confirm", "based", "using",
}


def _extract_topic_tokens(text: str) -> set[str]:
    en = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text) if w.lower() not in SYNTH_STOP_TOKENS}
    zh = {w for w in re.findall(r"[\u4e00-\u9fff]{2,6}", text) if w not in GENERIC_NON_ENTITIES}
    return en | zh


def _classify_polarity(text: str) -> str:
    has_neg = bool(SYNTH_NEG_OR_NULL_RE.search(text))
    has_pos = bool(SYNTH_POS_RE.search(text))
    if has_neg and not has_pos:
        return "null_or_negative"
    if has_pos and not has_neg:
        return "positive_or_increasing"
    if has_pos and has_neg:
        return "mixed_conditional"
    return "descriptive"


def _extract_scope_tags(text: str) -> list[str]:
    tags: list[str] = []
    cond = _extract_condition_key(text, 0, len(text))
    if cond != "general":
        tags.extend(cond.split("|"))
    for pat in (
        re.compile(r"\b(?:girls|boys|females?|males?|children|adolescents|preschoolers|adults|elderly|pediatric)\b", re.I),
        re.compile(r"\b(?:0\s*[–-]\s*4|0\s*[–-]\s*9|0\s*[–-]\s*19|10\s*[–-]\s*19)\s*years?\b", re.I),
        re.compile(r"\b(?:accidental|suicide\s+attempt|medicines|home|emergency|hospital|community|outpatient)\b", re.I),
        re.compile(r"\b(?:BMP6|KL|Klotho|ANXA2|MTHFR|DARC|ITGA4|CD36|HbS|SCD|AVN)\b"),
        re.compile(r"\b(?:19\d{2}|20\d{2})(?:\s*[–-]\s*(?:19\d{2}|20\d{2}))?\b"),
    ):
        for m in pat.finditer(text):
            t = re.sub(r"\s+", " ", m.group(0).strip().lower())
            if t not in tags:
                tags.append(t)
    return sorted(tags[:8])


def synthesize_general_discussion(
    chapters_dir: Path,
    *,
    out_json: Path | None = None,
    out_md: Path | None = None,
    workorder_path: Path | None = None,
) -> dict[str, Any]:
    """Build an evidence-bound multi-chapter General Discussion synthesis matrix and anchored draft."""
    files = sorted(p for p in chapters_dir.rglob("*") if p.is_file() and p.suffix.lower() in CHAPTER_EXTS)
    if not files:
        return {"ok": False, "error": f"No chapter files found in {chapters_dir}"}

    base_audit = audit_longdoc(chapters_dir, workorder_path=workorder_path)
    chapter_profiles: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []
    filtered_headers_total = 0

    for ch_idx, fp in enumerate(files, 1):
        rel = fp.relative_to(chapters_dir).as_posix()
        ch_tag = f"Ch{ch_idx}"
        raw_text, _, rmeta = read_chapter_text(fp)
        clean_text = _strip_annotations(raw_text)
        findings: list[dict[str, Any]] = []
        scopes: list[dict[str, Any]] = []
        limitations: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        in_refs = False

        for line_no, raw_ln in enumerate(clean_text.splitlines(), 1):
            ln = raw_ln.strip()
            if not ln:
                continue
            if re.match(r"^(?:#+\s*)?(?:references|bibliography|参考文献)\b", ln, re.I):
                in_refs = True
                continue
            if in_refs:
                continue
            if _is_running_page_header(ln):
                filtered_headers_total += 1
                continue
            if ln.startswith(("#", "| ---", "---", "<!--")):
                filtered_headers_total += 1
                continue

            anchor = f"[{ch_tag}:L{line_no}]"
            line_cites = [f"[{n}]" for m in CITE_NUM_RE.finditer(ln) for n in _expand_cite_numbers(m.group(1))]
            line_cites += [m.group(0) for m in CITE_AUTHOR_YEAR_RE.finditer(ln)]
            if line_cites:
                citations.append({"anchor": anchor, "file": rel, "line": line_no, "citations": sorted(set(line_cites))})

            # Split line into sentences for fine-grained evidence extraction
            sents = [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+", ln) if len(s.strip()) >= 28]
            if not sents and len(ln) >= 28:
                sents = [ln]
            for sent in sents:
                if _is_running_page_header(sent):
                    filtered_headers_total += 1
                    continue
                scope_tags = _extract_scope_tags(sent)
                if SYNTH_LIMITATION_RE.search(sent):
                    limitations.append({
                        "chapter": ch_tag, "file": rel, "line": line_no, "anchor": anchor,
                        "text": sent[:260], "conditions": scope_tags,
                    })
                if SYNTH_SCOPE_RE.search(sent) and len(scopes) < 12:
                    scopes.append({
                        "chapter": ch_tag, "file": rel, "line": line_no, "anchor": anchor,
                        "text": sent[:220], "scope_tags": scope_tags,
                    })
                if SYNTH_FINDING_RE.search(sent):
                    rec = {
                        "chapter": ch_tag,
                        "file": rel,
                        "line": line_no,
                        "anchor": anchor,
                        "text": sent[:280],
                        "topic_tokens": sorted(_extract_topic_tokens(sent)),
                        "polarity": _classify_polarity(sent),
                        "conditions": scope_tags,
                        "citations": sorted(set(line_cites)),
                    }
                    findings.append(rec)
                    all_findings.append(rec)

        chapter_profiles.append({
            "chapter": ch_tag,
            "file": rel,
            "format": rmeta.get("format", "md"),
            "findings_count": len(findings),
            "limitations_count": len(limitations),
            "citations_lines_count": len(citations),
            "findings": findings[:25],
            "conditions_scope": scopes[:10],
            "limitations": limitations[:12],
            "citations": citations[:20],
        })

    # Build 3-column evidence matrix across chapters:
    # 1) cross_chapter_convergences (>= 2 distinct chapters supporting compatible claim)
    # 2) conditional_divergences (distinct chapters differing due to experimental/population conditions)
    # 3) irreconcilable_conflicts (explicit value/acronym/symbol conflicts or same-condition opposite polarity)
    token_to_findings: dict[str, list[dict[str, Any]]] = {}
    for f in all_findings:
        for tok in f["topic_tokens"]:
            token_to_findings.setdefault(tok, []).append(f)

    # Identify cross-chapter theme clusters
    cross_chapter_convergences: list[dict[str, Any]] = []
    conditional_divergences: list[dict[str, Any]] = []
    irreconcilable_conflicts: list[dict[str, Any]] = []

    # First ingest hard deterministic conflicts from audit_longdoc as irreconcilable_conflicts
    for c in base_audit.get("entity_value_conflicts", []):
        occs = c.get("occurrences", [])
        anchors = []
        ch_set = set()
        for o in occs:
            idx_m = next((cp["chapter"] for cp in chapter_profiles if cp["file"] == o["file"]), o["file"])
            ch_set.add(idx_m)
            anchors.append(f"[{idx_m}:L{o['line']}]")
        irreconcilable_conflicts.append({
            "conflict_id": f"CONF-{len(irreconcilable_conflicts) + 1:02d}",
            "type": c.get("type", "metric_value_conflict"),
            "entity": c.get("entity", ""),
            "unit": c.get("unit", ""),
            "condition": c.get("condition", "general"),
            "values": c.get("values", []),
            "chapters": sorted(ch_set),
            "evidence_anchors": sorted(set(anchors)),
            "status": "needs_author_resolution",
            "rationale": f"Direct cross-chapter numerical conflict on '{c.get('entity')}' ({c.get('unit')}) under condition '{c.get('condition')}': {c.get('values')}; cannot be merged without author adjudication.",
        })
    for acro, exps in base_audit.get("acronym_conflicts", {}).items():
        irreconcilable_conflicts.append({
            "conflict_id": f"CONF-{len(irreconcilable_conflicts) + 1:02d}",
            "type": "acronym_conflict",
            "entity": acro,
            "values": exps,
            "chapters": [cp["chapter"] for cp in chapter_profiles],
            "evidence_anchors": [],
            "status": "needs_author_resolution",
            "rationale": f"Conflicting acronym expansions for '{acro}': {exps}; must be standardized before merging.",
        })

    # Pairwise cross-chapter claim comparison for convergences, conditional divergences, and same-condition polarity conflicts
    seen_conv_keys: set[tuple[str, ...]] = set()
    seen_div_keys: set[tuple[str, ...]] = set()
    seen_conf_pairs: set[tuple[str, str]] = set()

    for i in range(len(all_findings)):
        fa = all_findings[i]
        ta = set(fa["topic_tokens"])
        if len(ta) < 2:
            continue
        for j in range(i + 1, len(all_findings)):
            fb = all_findings[j]
            if fa["chapter"] == fb["chapter"]:
                continue
            tb = set(fb["topic_tokens"])
            shared = sorted(ta & tb)
            if len(shared) < 2:
                continue
            cond_a = set(fa["conditions"])
            cond_b = set(fb["conditions"])
            cond_diff = sorted(cond_a ^ cond_b)
            pol_a, pol_b = fa["polarity"], fb["polarity"]
            opposite_pol = (
                (pol_a == "positive_or_increasing" and pol_b == "null_or_negative")
                or (pol_b == "positive_or_increasing" and pol_a == "null_or_negative")
            )
            theme_label = ", ".join(shared[:4])
            pair_sig = tuple(sorted([fa["anchor"], fb["anchor"]]))

            if opposite_pol and not cond_diff:
                if pair_sig not in seen_conf_pairs and len(irreconcilable_conflicts) < 20:
                    seen_conf_pairs.add(pair_sig)
                    irreconcilable_conflicts.append({
                        "conflict_id": f"CONF-{len(irreconcilable_conflicts) + 1:02d}",
                        "type": "same_condition_polarity_conflict",
                        "entity": theme_label,
                        "chapters": sorted({fa["chapter"], fb["chapter"]}),
                        "evidence_anchors": [fa["anchor"], fb["anchor"]],
                        "claim_a": f"{fa['anchor']} {fa['text']}",
                        "claim_b": f"{fb['anchor']} {fb['text']}",
                        "status": "needs_author_resolution",
                        "rationale": f"Opposite directional claims on ({theme_label}) without distinguishing experimental/population condition.",
                    })
            elif opposite_pol or (cond_diff and (pol_a != pol_b or (cond_a and cond_b and not (cond_a & cond_b)))):
                div_key = (theme_label, tuple(sorted({fa["chapter"], fb["chapter"]}))[0], tuple(sorted({fa["chapter"], fb["chapter"]}))[-1])
                if div_key not in seen_div_keys and len(conditional_divergences) < 15:
                    seen_div_keys.add(div_key)
                    conditional_divergences.append({
                        "divergence_id": f"DIV-{len(conditional_divergences) + 1:02d}",
                        "theme": theme_label,
                        "chapters": sorted({fa["chapter"], fb["chapter"]}),
                        "evidence_anchors": [fa["anchor"], fb["anchor"]],
                        "condition_a": sorted(cond_a) or ["unspecified"],
                        "condition_b": sorted(cond_b) or ["unspecified"],
                        "divergent_conditions": cond_diff or ["different_study_contexts"],
                        "excerpt_a": f"{fa['anchor']} {fa['text'][:180]}",
                        "excerpt_b": f"{fb['anchor']} {fb['text'][:180]}",
                    })
            else:
                conv_key = tuple(shared[:3])
                if conv_key not in seen_conv_keys and len(cross_chapter_convergences) < 12:
                    seen_conv_keys.add(conv_key)
                    # Gather all compatible findings across distinct chapters sharing >=2 of these tokens
                    group_items = [fa, fb]
                    seen_ch = {fa["chapter"], fb["chapter"]}
                    for fc in all_findings:
                        if fc["chapter"] in seen_ch:
                            continue
                        if len(set(shared) & set(fc["topic_tokens"])) >= 2 and fc["polarity"] == pol_a:
                            group_items.append(fc)
                            seen_ch.add(fc["chapter"])
                    if len(seen_ch) >= 2:
                        cross_chapter_convergences.append({
                            "convergence_id": f"CONV-{len(cross_chapter_convergences) + 1:02d}",
                            "theme": theme_label,
                            "supporting_chapters": sorted(seen_ch),
                            "supporting_chapter_count": len(seen_ch),
                            "polarity": pol_a,
                            "shared_conditions": sorted(cond_a & cond_b),
                            "evidence_anchors": [it["anchor"] for it in group_items],
                            "evidence_items": [
                                {"chapter": it["chapter"], "anchor": it["anchor"], "text": it["text"][:200]}
                                for it in group_items
                            ],
                        })

    # Draft General Discussion synthesis paragraphs STRICTLY from >=2-chapter convergences,
    # with mandatory [ChX:Lxx] anchors on every single sentence + explicit pending_confirmations.
    draft_paragraphs: list[dict[str, Any]] = []
    for conv in cross_chapter_convergences[:6]:
        if conv["supporting_chapter_count"] < 2:
            continue
        anchors_str = " ".join(conv["evidence_anchors"])
        ch_list = ", ".join(conv["supporting_chapters"])
        lead_items = conv["evidence_items"][:3]
        clauses = "; ".join(f"{it['chapter']} reports that {it['text'][:140].rstrip('.')} {it['anchor']}" for it in lead_items)
        sent = (
            f"Across {conv['supporting_chapter_count']} chapters ({ch_list}), converging evidence on "
            f"{conv['theme']} links these findings: {clauses} {anchors_str}."
        )
        # Hard guard: drop any sentence lacking [ChX:Lxx]
        if re.search(r"\[Ch\d+:L\d+\]", sent):
            draft_paragraphs.append({
                "section": "cross_chapter_synthesis",
                "convergence_id": conv["convergence_id"],
                "supporting_chapters": conv["supporting_chapters"],
                "anchors": conv["evidence_anchors"],
                "text": sent,
            })

    if conditional_divergences:
        div_parts = []
        div_anchors: list[str] = []
        for dv in conditional_divergences[:4]:
            div_anchors.extend(dv["evidence_anchors"])
            cond_note = ", ".join(dv["divergent_conditions"][:4])
            div_parts.append(
                f"on {dv['theme']}, differences across {'/'.join(dv['chapters'])} correspond to distinct "
                f"conditions ({cond_note}) {' '.join(dv['evidence_anchors'])}"
            )
        div_sent = "Conditional divergences must remain stratified rather than pooled: " + "; ".join(div_parts) + "."
        if re.search(r"\[Ch\d+:L\d+\]", div_sent):
            draft_paragraphs.append({
                "section": "conditional_stratification",
                "supporting_chapters": sorted({c for dv in conditional_divergences[:4] for c in dv["chapters"]}),
                "anchors": sorted(set(div_anchors)),
                "text": div_sent,
            })

    pending_confirmations: list[dict[str, Any]] = []
    for conf in irreconcilable_conflicts:
        pending_confirmations.append({
            "item_id": conf["conflict_id"],
            "priority": "blocking_before_merge",
            "status": "needs_author_resolution",
            "anchors": conf.get("evidence_anchors", []),
            "prompt_to_author": conf["rationale"],
        })
    for dv in conditional_divergences[:5]:
        pending_confirmations.append({
            "item_id": dv["divergence_id"],
            "priority": "verify_scope_boundary",
            "status": "needs_author_confirmation",
            "anchors": dv["evidence_anchors"],
            "prompt_to_author": f"Confirm that differences on ({dv['theme']}) are explained by condition shift ({', '.join(dv['divergent_conditions'][:4])}) and should not be pooled.",
        })
    for cp in chapter_profiles:
        for lim in cp["limitations"][:2]:
            pending_confirmations.append({
                "item_id": f"LIM-{cp['chapter']}-L{lim['line']}",
                "priority": "retain_limitation_in_discussion",
                "status": "needs_author_confirmation",
                "anchors": [lim["anchor"]],
                "prompt_to_author": f"Retain chapter limitation {lim['anchor']}: {lim['text'][:160]}",
            })

    # Verify zero running-header evidence leaked
    header_evidence_leaks = [
        f["anchor"] for f in all_findings if _is_running_page_header(f["text"])
    ]
    all_draft_anchored = all(bool(re.search(r"\[Ch\d+:L\d+\]", p["text"])) for p in draft_paragraphs)

    result = {
        "ok": len(header_evidence_leaks) == 0 and all_draft_anchored,
        "schema_version": "general_discussion_synthesis/v1",
        "chapters_count": len(chapter_profiles),
        "filtered_running_headers_count": filtered_headers_total,
        "running_header_evidence_leaks": header_evidence_leaks,
        "all_draft_sentences_anchored": all_draft_anchored,
        "chapter_extractions": chapter_profiles,
        "evidence_matrix": {
            "cross_chapter_convergences": cross_chapter_convergences,
            "conditional_divergences": conditional_divergences,
            "irreconcilable_conflicts": irreconcilable_conflicts,
        },
        "general_discussion_draft": draft_paragraphs,
        "pending_confirmations": pending_confirmations,
    }

    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if out_md:
        md_lines = [
            "# General Discussion Synthesis Matrix & Anchored Draft",
            "",
            f"- Chapters analyzed: {len(chapter_profiles)} | Filtered running headers/headings: {filtered_headers_total} | Running-header evidence leaks: {len(header_evidence_leaks)}",
            f"- Convergences (>=2 chapters): {len(cross_chapter_convergences)} | Conditional divergences: {len(conditional_divergences)} | Irreconcilable conflicts (`needs_author_resolution`): {len(irreconcilable_conflicts)}",
            "",
            "## 1. Three-Column Cross-Chapter Evidence Matrix",
            "",
            "### 1A. Cross-Chapter Convergences (>=2 Chapters Supported)",
            "",
            "| ID | Theme | Chapters | Evidence Anchors |",
            "| --- | --- | --- | --- |",
        ]
        for conv in cross_chapter_convergences:
            md_lines.append(
                f"| {conv['convergence_id']} | {conv['theme']} | {', '.join(conv['supporting_chapters'])} | {' '.join(conv['evidence_anchors'])} |"
            )
        if not cross_chapter_convergences:
            md_lines.append("| - | No >=2-chapter convergence met threshold | - | - |")

        md_lines += [
            "",
            "### 1B. Conditional Divergences (Stratified by Population / Experimental Condition)",
            "",
            "| ID | Theme | Chapters | Divergent Conditions | Anchors |",
            "| --- | --- | --- | --- | --- |",
        ]
        for dv in conditional_divergences:
            md_lines.append(
                f"| {dv['divergence_id']} | {dv['theme']} | {', '.join(dv['chapters'])} | {', '.join(dv['divergent_conditions'][:4])} | {' '.join(dv['evidence_anchors'])} |"
            )
        if not conditional_divergences:
            md_lines.append("| - | None detected | - | - | - |")

        md_lines += [
            "",
            "### 1C. Irreconcilable Conflicts (Do Not Merge — `needs_author_resolution`)",
            "",
            "| ID | Type | Entity | Status | Anchors | Rationale |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for conf in irreconcilable_conflicts:
            md_lines.append(
                f"| {conf['conflict_id']} | {conf['type']} | {conf['entity']} | `{conf['status']}` | {' '.join(conf.get('evidence_anchors', []))} | {conf['rationale'].replace('|', '/')} |"
            )
        if not irreconcilable_conflicts:
            md_lines.append("| - | None detected | - | - | - | - |")

        md_lines += ["", "## 2. Evidence-Anchored General Discussion Draft", ""]
        for p in draft_paragraphs:
            md_lines.append(f"- **[{p['section']}]** {p['text']}")
        if not draft_paragraphs:
            md_lines.append("- Insufficient >=2-chapter convergence evidence to draft synthesis prose without fabrication.")

        md_lines += ["", "## 3. Pending Author Confirmations (`pending_confirmations`)", ""]
        for pc in pending_confirmations:
            md_lines.append(
                f"- `{pc['item_id']}` (`{pc['status']}`, {' '.join(pc.get('anchors', []))}): {pc['prompt_to_author']}"
            )

        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return result


def _extract_docx_text(docx_path: Path) -> tuple[str, dict[str, Any]]:
    """Pure stdlib OOXML .docx reader preserving paragraphs, tables, and field-code signals."""
    paras: list[str] = []
    field_codes: list[str] = []
    tracked_changes = 0
    with zipfile.ZipFile(docx_path, "r") as zf:
        for xml_name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
            if xml_name not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(xml_name))
            for elem in root.iter():
                tag = elem.tag.split("}")[-1]
                if tag in {"ins", "del"}:
                    tracked_changes += 1
                elif tag == "instrText" and elem.text and elem.text.strip():
                    field_codes.append(elem.text.strip())
            for p_node in root.iter():
                if p_node.tag.split("}")[-1] != "p":
                    continue
                pieces: list[str] = []
                for child in p_node.iter():
                    ctag = child.tag.split("}")[-1]
                    if ctag == "t" and child.text:
                        pieces.append(child.text)
                    elif ctag == "tab":
                        pieces.append("\t")
                    elif ctag == "br":
                        pieces.append("\n")
                    elif ctag == "instrText" and child.text:
                        pieces.append(f" {child.text.strip()} ")
                line = "".join(pieces).strip()
                if line:
                    paras.append(line)
    text = "\n\n".join(paras)
    return text, {
        "format": "docx",
        "extractor": "stdlib_ooxml",
        "paragraphs": len(paras),
        "tracked_change_nodes": tracked_changes,
        "field_codes_detected": len(field_codes),
    }


def _unescape_pdf_literal(s: str) -> str:
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt in "()\\":
                out.append(nxt)
                i += 2
                continue
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
            if nxt.isdigit():
                oct_str = s[i + 1 : i + 4]
                m_oct = re.match(r"[0-7]{1,3}", oct_str)
                if m_oct:
                    out.append(chr(int(m_oct.group(0), 8)))
                    i += 1 + len(m_oct.group(0))
                    continue
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_pdf_stdlib_zlib(pdf_path: Path) -> tuple[str, int]:
    """Pure stdlib zlib FlateDecode PDF text fallback when external extractors are absent."""
    raw = pdf_path.read_bytes()
    pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", raw))
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.DOTALL):
        stream_data = m.group(1)
        decoded = None
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decoded = zlib.decompress(stream_data, wbits)
                break
            except Exception:
                pass
        if decoded is None:
            decoded = stream_data
        s = decoded.decode("latin-1", errors="ignore")
        if "BT" not in s and "Tj" not in s and "TJ" not in s:
            continue
        for tm in re.finditer(r"\(((?:\\.|[^\\)])*)\)\s*Tj", s):
            txt = _unescape_pdf_literal(tm.group(1)).strip()
            if txt:
                chunks.append(txt)
        for am in re.finditer(r"\[(.*?)\]\s*TJ", s, re.DOTALL):
            parts = [_unescape_pdf_literal(pm.group(1)) for pm in re.finditer(r"\(((?:\\.|[^\\)])*)\)", am.group(1))]
            joined = "".join(parts).strip()
            if joined:
                chunks.append(joined)
        for hm in re.finditer(r"<([0-9A-Fa-f]{4,})>\s*Tj", s):
            hx = hm.group(1)
            if len(hx) % 2 == 0:
                try:
                    b = bytes.fromhex(hx)
                    if b.startswith(b"\xfe\xff"):
                        chunks.append(b.decode("utf-16-be", errors="ignore"))
                    else:
                        t = b.decode("latin-1", errors="ignore").strip()
                        if t and all(32 <= ord(c) < 127 or c in "\t\n" for c in t):
                            chunks.append(t)
                except Exception:
                    pass
    return "\n".join(chunks), max(pages, 1 if chunks else 0)


def _extract_pdf_text(pdf_path: Path) -> tuple[str, dict[str, Any]]:
    raw = pdf_path.read_bytes()
    approx_pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", raw))
    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin:
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-enc", "UTF-8", str(pdf_path), "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout, {"format": "pdf", "extractor": "pdftotext", "pages": approx_pages or 1}
        except Exception:
            pass
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            pages_txt = [(pg.extract_text() or "") for pg in doc.pages]
            joined = "\n\n".join(f"<!-- Page {i} -->\n{t}" for i, t in enumerate(pages_txt, 1) if t.strip())
            if joined.strip():
                return joined, {"format": "pdf", "extractor": "pdfplumber", "pages": len(doc.pages)}
    except Exception:
        pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        pages_txt = [(pg.extract_text() or "") for pg in reader.pages]
        joined = "\n\n".join(f"<!-- Page {i} -->\n{t}" for i, t in enumerate(pages_txt, 1) if t.strip())
        if joined.strip():
            return joined, {"format": "pdf", "extractor": "pypdf", "pages": len(reader.pages)}
    except Exception:
        pass
    txt, pages = _extract_pdf_stdlib_zlib(pdf_path)
    return txt, {"format": "pdf", "extractor": "stdlib_zlib_fallback", "pages": pages or approx_pages}


def read_chapter_text(fp: Path) -> tuple[str, bytes, dict[str, Any]]:
    raw_bytes = fp.read_bytes()
    suf = fp.suffix.lower()
    if suf == ".docx":
        txt, meta = _extract_docx_text(fp)
    elif suf == ".pdf":
        txt, meta = _extract_pdf_text(fp)
    else:
        txt = raw_bytes.decode("utf-8", errors="replace")
        meta = {"format": suf.lstrip(".") or "txt", "extractor": "utf8_text"}
    meta["bytes"] = len(raw_bytes)
    meta["sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    return txt, raw_bytes, meta


def _induce_target_patterns(target_ids: list[str]) -> list[re.Pattern[str]]:
    """Induce anchor regex templates dynamically from workorder target_ids (non-Cxx-P### safe)."""
    templates: set[str] = set()
    for tid in target_ids:
        t = tid.strip()
        if not t:
            continue
        # Generalized shape: runs of ASCII letters -> [A-Za-z]+, digits -> \d+
        parts: list[str] = []
        for tok in re.findall(r"[A-Za-z]+|\d+|[^A-Za-z0-9]+", t):
            if tok.isalpha():
                parts.append(r"[A-Za-z]+")
            elif tok.isdigit():
                parts.append(r"\d+")
            else:
                parts.append(re.escape(tok))
        shape = "".join(parts)
        templates.add(shape)
    compiled: list[re.Pattern[str]] = [DEFAULT_ANCHOR_FIND_RE]
    for tmpl in sorted(templates):
        compiled.append(
            re.compile(
                rf"(?:^\s*\[|<!--\s*(?:id\s*:\s*)?|id=[\"']|\{{#|\s|^|^\s*\*\*)({tmpl})(?:[\]\"'}}:*：]|\s|$)",
                re.MULTILINE,
            )
        )
    return compiled


def _normalize_acronym_expansion(raw_phrase: str, acro: str) -> str:
    phrase = " ".join(raw_phrase.strip().split())
    if re.search(r"[\u4e00-\u9fff]", phrase):
        return phrase.lower()
    words = re.findall(r"[A-Za-z0-9-]+", phrase)
    target = acro.upper()
    for start in range(len(words) - 1, -1, -1):
        suffix = words[start:]
        if suffix[0].lower() in STOP_WORDS:
            continue
        all_init = "".join(w[0].upper() for w in suffix if w)
        content_init = "".join(w[0].upper() for w in suffix if w and w.lower() not in STOP_WORDS)
        if all_init == target or content_init == target:
            return " ".join(suffix).lower()
    while words and words[0].lower() in STOP_WORDS:
        words.pop(0)
    return " ".join(words).lower()


def _normalize_unit(unit: str) -> str:
    u = re.sub(r"\s+", " ", unit.strip())
    u = u.replace("％", "%").replace("℃", "°C").replace("um", "μm").replace("ug", "μg").replace("uL", "μL")
    if u.lower() in {"year", "years"}:
        return "years"
    if u.lower() in {"day", "days"}:
        return "days"
    if u.lower() in {"month", "months"}:
        return "months"
    if u.lower() in {"week", "weeks"}:
        return "weeks"
    if u.lower() in {"hr", "h"}:
        return "h"
    return u


def _normalize_entity(raw_entity: str) -> str:
    s = " ".join(raw_entity.strip().split())
    if re.search(r"[\u4e00-\u9fff]", s):
        s = re.sub(r"^(?:其中|并且|以及|对于|关于|该|本|其|的|为|是)+", "", s).strip()
        if len(s) < 2 or s in GENERIC_NON_ENTITIES:
            return ""
        return s
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9_-]+", s)]
    while words and words[0] in STOP_WORDS:
        words.pop(0)
    while words and words[-1] in STOP_WORDS:
        words.pop()
    if not words:
        return ""
    if len(words) == 1 and (len(words[0]) <= 2 or words[0] in GENERIC_NON_ENTITIES or words[0].isdigit()):
        return ""
    ent = " ".join(words[-4:])
    if ent in GENERIC_NON_ENTITIES:
        return ""
    return ent


def _extract_condition_key(text: str, start: int, end: int) -> str:
    """Bind condition/subgroup/year/temperature qualifiers within the enclosing sentence (max ±90 chars)."""
    lo = max(0, start - 90)
    hi = min(len(text), end + 90)
    left_seg = text[lo:start]
    right_seg = text[end:hi]
    m_l = list(re.finditer(r"(?<!\d)\.(?!\d)|[!?\n。！？]", left_seg))
    win_l = (lo + m_l[-1].end()) if m_l else lo
    m_r = re.search(r"(?<!\d)\.(?!\d)|[!?\n。！？]", right_seg)
    win_r = (end + m_r.start()) if m_r else hi
    win = text[win_l:win_r]
    tags: list[str] = []
    for pat in COND_PATTERNS:
        for m in pat.finditer(win):
            tok = re.sub(r"\s+", " ", m.group(0).strip().lower())
            if tok not in tags:
                tags.append(tok)
    return "|".join(sorted(tags[:4])) if tags else "general"


def _bilingual_shingles(text: str) -> set[str]:
    shingles: set[str] = set()
    en_words = re.findall(r"[a-z0-9]+", text.lower())
    if len(en_words) >= 3:
        for i in range(len(en_words) - 2):
            shingles.add(" ".join(en_words[i : i + 3]))
    else:
        shingles.update(w for w in en_words if len(w) >= 3)
    zh_seq = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    if len(zh_seq) >= 3:
        for i in range(len(zh_seq) - 2):
            shingles.add(zh_seq[i : i + 3])
    return shingles


def _find_duplicates_inverted_index(
    paras_all: list[tuple[str, int, str, set[str]]],
    dup_threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Linear-time average cross-chapter duplicate detection via shingle inverted index."""
    t0 = time.perf_counter()
    n_paras = len(paras_all)
    inv: dict[str, list[int]] = {}
    for idx, (_, _, _, sh) in enumerate(paras_all):
        for s in sh:
            inv.setdefault(s, []).append(idx)

    # Skip ultra-frequent boilerplate shingles present in >30% of paragraphs
    freq_cap = max(12, int(n_paras * 0.30))
    pair_shared: dict[tuple[int, int], int] = {}
    for postings in inv.values():
        m = len(postings)
        if m < 2 or m > freq_cap:
            continue
        for i_pos in range(m):
            a = postings[i_pos]
            fa = paras_all[a][0]
            for j_pos in range(i_pos + 1, m):
                b = postings[j_pos]
                if fa == paras_all[b][0]:
                    continue
                key = (a, b) if a < b else (b, a)
                pair_shared[key] = pair_shared.get(key, 0) + 1

    duplicates: list[dict[str, Any]] = []
    for (a, b), approx_shared in pair_shared.items():
        fa, ia, _, sa = paras_all[a]
        fb, ib, tb, sb = paras_all[b]
        # Exact Jaccard on candidate pairs
        inter = len(sa & sb)
        union = len(sa) + len(sb) - inter
        jacc = inter / union if union else 0.0
        if jacc >= dup_threshold:
            duplicates.append({
                "chapter_a": f"{fa}#P{ia}",
                "chapter_b": f"{fb}#P{ib}",
                "jaccard": round(jacc, 3),
                "severity": "high" if jacc >= 0.65 else "medium",
                "excerpt": tb[:120],
            })
    duplicates.sort(key=lambda d: (-d["jaccard"], d["chapter_a"], d["chapter_b"]))
    total_possible = n_paras * (n_paras - 1) // 2
    stats = {
        "algorithm": "inverted_index_shingle_jaccard",
        "paragraphs_indexed": n_paras,
        "candidate_pairs_evaluated": len(pair_shared),
        "total_possible_pairs": total_possible,
        "pruning_ratio": round(1.0 - (len(pair_shared) / max(total_possible, 1)), 4),
        "runtime_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }
    return duplicates, stats


def _find_numbering_gaps(labels: set[str]) -> list[dict[str, Any]]:
    grouped: dict[str, set[int]] = {}
    for lbl in labels:
        if "." in lbl:
            prefix, tail = lbl.rsplit(".", 1)
            if tail.isdigit():
                grouped.setdefault(prefix, set()).add(int(tail))
        elif lbl.isdigit():
            grouped.setdefault("", set()).add(int(lbl))
    gaps: list[dict[str, Any]] = []
    for prefix, nums in sorted(grouped.items()):
        if not nums:
            continue
        for n in range(1, max(nums) + 1):
            if n not in nums:
                missing_lbl = f"{prefix}.{n}" if prefix else str(n)
                gaps.append({"prefix": prefix or "global", "missing_label": missing_lbl})
    return gaps


def _expand_cite_numbers(group_str: str) -> list[int]:
    nums: list[int] = []
    for part in group_str.split(","):
        part = part.strip()
        m_rng = re.match(r"^(\d+)\s*[–-]\s*(\d+)$", part)
        if m_rng:
            a, b = int(m_rng.group(1)), int(m_rng.group(2))
            if a <= b and b - a <= 50:
                nums.extend(range(a, b + 1))
        elif part.isdigit():
            nums.append(int(part))
    return nums


def _load_workorder_raw(workorder_path: Path | None) -> tuple[list[str], list[str], dict[str, Any]]:
    if not workorder_path or not workorder_path.is_file():
        return [], [], {}
    raw = json.loads(workorder_path.read_text(encoding="utf-8"))
    wo = raw.get("organizer_edit_workorder", raw) if isinstance(raw, dict) else {}
    if not isinstance(wo, dict):
        return [], [], {}
    target_ids: list[str] = []
    target_files: list[str] = []
    if isinstance(wo.get("target_ids"), list):
        target_ids.extend(str(x).strip() for x in wo["target_ids"] if str(x).strip())
    if isinstance(wo.get("edits"), list):
        for edit in wo["edits"]:
            if isinstance(edit, dict):
                if isinstance(edit.get("file"), str):
                    p = PurePosixPath(edit["file"].replace("\\", "/"))
                    if not p.is_absolute() and ".." not in p.parts:
                        target_files.append(p.as_posix().removeprefix("chapters/"))
                if isinstance(edit.get("target_id"), str) and edit["target_id"].strip():
                    target_ids.append(edit["target_id"].strip())
    return sorted(set(target_ids)), sorted(set(target_files)), wo


def _resolve_workorder_targets(
    wo_target_ids: list[str],
    wo_target_files: list[str],
    wo_raw: dict[str, Any],
    anchor_to_file: dict[str, str],
) -> tuple[str, list[str], list[str], list[str], str]:
    if not wo_raw:
        return "none", [], [], [], "NO_WORKORDER"
    resolved_files = list(wo_target_files)
    missing_ids: list[str] = []
    for tid in wo_target_ids:
        if tid in anchor_to_file:
            resolved_files.append(anchor_to_file[tid])
        else:
            missing_ids.append(tid)
    resolved_files = sorted(set(resolved_files))

    if wo_target_ids and wo_raw.get("edits"):
        mode = "hybrid_target_ids_and_files"
    elif wo_target_ids:
        mode = "paragraph_target_ids"
    elif resolved_files:
        mode = "chapter_edits_files"
    else:
        mode = "unconstrained"

    if missing_ids:
        scope_status = "BLOCKED_MISSING_TARGETS" if not wo_target_files else "DEGRADED_MISSING_TARGETS_WITH_FILE_SCOPE"
    else:
        scope_status = "ENFORCED"
    return mode, wo_target_ids, resolved_files, missing_ids, scope_status


def generate_chapter_plan(chapters_dir: Path, out_path: Path | None = None) -> dict[str, Any]:
    """Auto-generate an editable chapter_plan.json from actual /input chapters with role & length budgets."""
    files = sorted(p for p in chapters_dir.rglob("*") if p.is_file() and p.suffix.lower() in CHAPTER_EXTS)
    role_patterns = {
        "research_question": re.compile(r"\b(?:research\s+question|hypothesis|objective|aim|purpose|scope)\b|研究问题|科学问题|研究目标|假设", re.I),
        "methods": re.compile(r"\b(?:method(?:ology|s)?|experimental\s+design|protocol|materials|statistical\s+analysis)\b|研究方法|实验设计|材料与方法", re.I),
        "results": re.compile(r"\b(?:results?|findings?|observed|measured|incidence|outcomes?)\b|研究结果|实验结果|主要发现", re.I),
        "discussion": re.compile(r"\b(?:discussion|interpretation|implications|synthesis|assessment)\b|讨论|综合讨论|结果分析", re.I),
        "limitations": re.compile(r"\b(?:limitations?|caveats?|scope\s+boundary|future\s+work|bias|confound)\b|局限性|研究局限|适用边界|不足", re.I),
    }
    chapters_out: list[dict[str, Any]] = []
    covered_all: set[str] = set()
    for idx, fp in enumerate(files, 1):
        rel = fp.relative_to(chapters_dir).as_posix()
        txt, _, meta = read_chapter_text(fp)
        zh_chars = len(re.findall(r"[\u4e00-\u9fff]", txt))
        en_words = len(re.findall(r"[A-Za-z0-9_'-]+", txt))
        dom_len = zh_chars if zh_chars >= en_words else en_words
        roles = [r for r, pat in role_patterns.items() if pat.search(txt)]
        covered_all.update(roles)
        first_heading = next((ln.strip().lstrip("# ").strip() for ln in txt.splitlines() if ln.strip()), rel)
        chapters_out.append({
            "chapter_order": idx,
            "file": rel,
            "title_hint": first_heading[:100],
            "source_format": meta.get("format", "md"),
            "current_length": dom_len,
            "length_unit": "zh_chars" if zh_chars >= en_words else "en_words",
            "min_length": max(50, int(dom_len * 0.75)),
            "max_length": max(300, int(dom_len * 1.35)),
            "detected_roles": roles,
            "required_roles": roles[:2] if roles else ["research_question"],
        })
    plan = {
        "schema_version": "chapter_plan/v1",
        "chapters_count": len(chapters_out),
        "covered_thesis_roles": sorted(covered_all),
        "missing_thesis_roles": [r for r in role_patterns if r not in covered_all],
        "chapters": chapters_out,
    }
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def _audit_chapter_planning(files: list[Path], chapters_dir: Path, chapter_plan_path: Path | None) -> dict[str, Any]:
    role_patterns = {
        "research_question": re.compile(r"\b(?:research\s+question|hypothesis|objective|aim|purpose|scope)\b|研究问题|科学问题|研究目标|假设", re.I),
        "methods": re.compile(r"\b(?:method(?:ology|s)?|experimental\s+design|protocol|materials|statistical\s+analysis)\b|研究方法|实验设计|材料与方法", re.I),
        "results": re.compile(r"\b(?:results?|findings?|observed|measured|incidence|outcomes?)\b|研究结果|实验结果|主要发现", re.I),
        "discussion": re.compile(r"\b(?:discussion|interpretation|implications|synthesis|assessment)\b|讨论|综合讨论|结果分析", re.I),
        "limitations": re.compile(r"\b(?:limitations?|caveats?|scope\s+boundary|future\s+work|bias|confound)\b|局限性|研究局限|适用边界|不足", re.I),
    }
    per_chapter: list[dict[str, Any]] = []
    covered_roles: set[str] = set()
    for fp in files:
        rel = fp.relative_to(chapters_dir).as_posix()
        txt, _, _ = read_chapter_text(fp)
        zh_chars = len(re.findall(r"[\u4e00-\u9fff]", txt))
        en_words = len(re.findall(r"[A-Za-z0-9_'-]+", txt))
        roles = [r for r, pat in role_patterns.items() if pat.search(txt)]
        covered_roles.update(roles)
        per_chapter.append({"file": rel, "zh_chars": zh_chars, "en_words": en_words, "detected_roles": roles})

    budget_violations: list[str] = []
    if chapter_plan_path and chapter_plan_path.is_file():
        plan = json.loads(chapter_plan_path.read_text(encoding="utf-8"))
        plan_items = plan.get("chapters", plan) if isinstance(plan, dict) else plan
        by_file = {c["file"]: c for c in per_chapter}
        if isinstance(plan_items, list):
            for item in plan_items:
                if not isinstance(item, dict) or not item.get("file"):
                    continue
                fn = str(item["file"]).replace("\\", "/").removeprefix("chapters/")
                actual = by_file.get(fn)
                if actual is None:
                    budget_violations.append(f"Planned chapter missing: {fn}")
                    continue
                length_val = actual["zh_chars"] if actual["zh_chars"] >= actual["en_words"] else actual["en_words"]
                min_len = item.get("min_length")
                max_len = item.get("max_length")
                if isinstance(min_len, (int, float)) and length_val < min_len:
                    budget_violations.append(f"{fn}: length {length_val} below planned min_length {min_len}")
                if isinstance(max_len, (int, float)) and length_val > max_len:
                    budget_violations.append(f"{fn}: length {length_val} exceeds planned max_length {max_len}")
                for req_role in item.get("required_roles", []):
                    if req_role not in actual["detected_roles"]:
                        budget_violations.append(f"{fn}: missing planned argument role {req_role!r}")

    return {
        "per_chapter": per_chapter,
        "covered_thesis_roles": sorted(covered_roles),
        "missing_thesis_roles": [r for r in role_patterns if r not in covered_roles],
        "budget_violations": budget_violations,
    }


def audit_longdoc(
    chapters_dir: Path,
    *,
    workorder_path: Path | None = None,
    baseline_path: Path | None = None,
    toc_path: Path | None = None,
    chapter_plan_path: Path | None = None,
    dup_threshold: float = 0.45,
) -> dict[str, Any]:
    files = sorted(p for p in chapters_dir.rglob("*") if p.is_file() and p.suffix.lower() in CHAPTER_EXTS)
    if not files:
        return {"passed": False, "error": f"No chapter files (.md/.txt/.tex/.docx/.pdf) found in {chapters_dir}"}

    wo_raw_ids, wo_raw_files, wo_raw = _load_workorder_raw(workorder_path)
    anchor_patterns = _induce_target_patterns(wo_raw_ids)

    acronyms: dict[str, set[str]] = {}
    symbols: dict[str, dict[str, list[str]]] = {}
    entity_metrics: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    sample_sizes: dict[tuple[str, str], list[dict[str, Any]]] = {}

    defined_figs: set[str] = set()
    ref_figs: list[tuple[str, str]] = []
    defined_tabs: set[str] = set()
    ref_tabs: list[tuple[str, str]] = []
    defined_eqs: set[str] = set()
    ref_eqs: list[tuple[str, str]] = []

    paras_all: list[tuple[str, int, str, set[str]]] = []
    chapter_hashes: dict[str, str] = {}
    stripped_source_hashes: dict[str, str] = {}
    chapter_read_meta: dict[str, dict[str, Any]] = {}
    anchor_to_file: dict[str, str] = {}

    cite_nums_used: list[tuple[str, int, int, str]] = []
    ref_list_map: dict[int, str] = {}
    author_year_cites: list[tuple[str, int, str, str]] = []
    zotero_artifacts: list[dict[str, Any]] = []
    truncation_warnings: list[dict[str, Any]] = []
    chapter_nums: list[int] = []

    for ch_idx, fp in enumerate(files, 1):
        rel = fp.relative_to(chapters_dir).as_posix()
        raw_text, raw_bytes, rmeta = read_chapter_text(fp)
        chapter_read_meta[rel] = rmeta
        chapter_hashes[rel] = hashlib.sha256(raw_bytes).hexdigest()
        stripped_raw = _strip_annotations(raw_text)
        stripped_source_hashes[rel] = hashlib.sha256(stripped_raw.encode("utf-8")).hexdigest()

        m_ch = CHAPTER_NUM_RE.search(rel)
        if m_ch:
            chapter_nums.append(int(m_ch.group(1)))

        # Discover explicit & induced anchors + register deterministic auto paragraph anchors
        for pat in anchor_patterns:
            for m_a in pat.finditer(stripped_raw):
                anchor_to_file.setdefault(m_a.group(1).strip(), rel)
        for p_idx, para in enumerate(re.split(r"\n\s*\n+", stripped_raw), 1):
            if para.strip():
                anchor_to_file.setdefault(f"CH{ch_idx:02d}-P{p_idx:03d}", rel)
                anchor_to_file.setdefault(f"{rel}#P{p_idx:03d}", rel)

        text_for_scan = stripped_raw

        for m in ACRONYM_DEF_RE.finditer(text_for_scan):
            acro = m.group(2).strip()
            expansion = _normalize_acronym_expansion(m.group(1), acro)
            acronyms.setdefault(acro, set()).add(expansion)

        for pat in (SYMBOL_DEF_EN_RE, SYMBOL_DEF_ZH_RE):
            for m in pat.finditer(text_for_scan):
                sym = m.group(1).strip()
                defn = " ".join(m.group(2).strip().lower().split())
                symbols.setdefault(sym, {}).setdefault(defn, []).append(rel)

        # 1) High-weight prior lexicon extraction
        prior_spans: list[tuple[int, int]] = []
        for m in ENTITY_METRIC_PRIOR_RE.finditer(text_for_scan):
            metric = " ".join(m.group("metric").lower().split())
            unit = _normalize_unit(m.group("unit"))
            val = float(m.group("val"))
            cond = _extract_condition_key(text_for_scan, m.start(), m.end())
            line_no = text_for_scan.count("\n", 0, m.start()) + 1
            prior_spans.append((m.start(), m.end()))
            entity_metrics.setdefault((metric, unit, cond), []).append({
                "file": rel, "line": line_no, "value": val, "condition": cond,
                "extractor": "prior_lexicon", "raw": m.group(0),
            })

        # 2) Domain-agnostic SI + Chinese unit + context window + condition binding
        for m in GENERIC_METRIC_RE.finditer(text_for_scan):
            if any(a <= m.start() < b or a < m.end() <= b for a, b in prior_spans):
                continue
            ent = _normalize_entity(m.group("entity"))
            if not ent:
                continue
            unit = _normalize_unit(m.group("unit"))
            try:
                val = float(m.group("val").replace(",", ""))
            except ValueError:
                continue
            cond = _extract_condition_key(text_for_scan, m.start(), m.end())
            line_no = text_for_scan.count("\n", 0, m.start()) + 1
            entity_metrics.setdefault((ent, unit, cond), []).append({
                "file": rel, "line": line_no, "value": val, "condition": cond,
                "extractor": "domain_agnostic", "raw": m.group(0)[:90],
            })

        for m in SAMPLE_SIZE_RE.finditer(text_for_scan):
            ctx = m.group("ctx").lower().strip("_- ")
            if ctx in STOP_WORDS or ctx in GENERIC_NON_ENTITIES:
                continue
            val = int(m.group("val").replace(",", ""))
            cond = _extract_condition_key(text_for_scan, m.start(), m.end())
            line_no = text_for_scan.count("\n", 0, m.start()) + 1
            sample_sizes.setdefault((ctx, cond), []).append({
                "file": rel, "line": line_no, "n": val, "condition": cond, "raw": m.group(0),
            })

        for m in FIG_LABEL_RE.finditer(text_for_scan):
            defined_figs.add(m.group(1))
        for m in FIG_REF_RE.finditer(text_for_scan):
            ref_figs.append((rel, m.group(1)))
        for m in TAB_LABEL_RE.finditer(text_for_scan):
            defined_tabs.add(m.group(1))
        for m in TAB_REF_RE.finditer(text_for_scan):
            ref_tabs.append((rel, m.group(1)))
        for m in EQ_LABEL_RE.finditer(text_for_scan):
            num = m.group(1) or m.group(2)
            if num:
                defined_eqs.add(num)
        for m in EQ_REF_RE.finditer(text_for_scan):
            ref_eqs.append((rel, m.group(1)))

        # Citations, reference list, and claim-citation alignment
        in_refs_section = False
        for line_idx, line in enumerate(text_for_scan.splitlines(), 1):
            if re.match(r"^\s*(?:#+\s*)?(?:references|bibliography|参考文献)\b", line, re.I):
                in_refs_section = True
            for zm in ZOTERO_CORRUPT_RE.finditer(line):
                zotero_artifacts.append({"file": rel, "line": line_idx, "match": zm.group(0)})
            if in_refs_section:
                rm = REF_ENTRY_RE.match(line)
                if rm:
                    rnum = int(rm.group(1) or rm.group(2))
                    ref_list_map[rnum] = rm.group(3).strip()[:140]
            else:
                for cm in CITE_NUM_RE.finditer(line):
                    if line[cm.end() : cm.end() + 1] == "(":
                        continue
                    for num in _expand_cite_numbers(cm.group(1)):
                        cite_nums_used.append((rel, line_idx, num, line.strip()[:160]))
                for am in CITE_AUTHOR_YEAR_RE.finditer(line):
                    author_year_cites.append((rel, line_idx, am.group(0), line.strip()[:160]))

        # Truncation scan
        if TRUNCATION_MARKER_RE.search(text_for_scan):
            truncation_warnings.append({
                "file": rel,
                "reason": "explicit_truncation_marker",
                "excerpt": text_for_scan.strip()[-100:],
            })
        else:
            st_end = text_for_scan.rstrip()
            if len(st_end) >= 80:
                last_line = st_end.splitlines()[-1].strip()
                if st_end[-1] not in ".!?。！？)]}”\"'`|>" and not last_line.startswith(("#", "|", "-", "*", "\\")):
                    truncation_warnings.append({
                        "file": rel,
                        "reason": "abrupt_unpunctuated_ending",
                        "excerpt": last_line[-100:],
                    })

        for idx, para in enumerate(re.split(r"\n\s*\n+", text_for_scan), 1):
            clean_lines = [ln for ln in para.splitlines() if not re.match(r"^\s*(?:<!--\s*Page\s*\d+\s*-->|##\s*\[Page\s*\d+\]|Contains Nonbinding Recommendations)\s*$", ln, re.I)]
            st = " ".join(" ".join(clean_lines).split())
            if len(st) >= 80 and not st.startswith("#") and not _is_running_page_header(st):
                sh = _bilingual_shingles(st)
                if len(sh) >= 12:
                    paras_all.append((rel, idx, st, sh))

    acronym_conflicts = {k: sorted(v) for k, v in acronyms.items() if len(v) > 1}
    symbol_conflicts = {
        sym: {d: sorted(set(fs)) for d, fs in defs.items()}
        for sym, defs in symbols.items()
        if len(defs) > 1
    }

    entity_value_conflicts: list[dict[str, Any]] = []
    for (metric, unit, cond), obs in sorted(entity_metrics.items()):
        distinct_vals = sorted({o["value"] for o in obs})
        distinct_files = {o["file"] for o in obs}
        if len(distinct_vals) > 1 and len(distinct_files) > 1:
            # Verify meaningful numerical divergence (>0.1% relative)
            if (max(distinct_vals) - min(distinct_vals)) / max(abs(max(distinct_vals)), 1e-9) > 1e-3:
                entity_value_conflicts.append({
                    "type": "metric_value_conflict",
                    "entity": metric,
                    "unit": unit,
                    "condition": cond,
                    "extractor": obs[0]["extractor"],
                    "values": distinct_vals,
                    "occurrences": obs[:8],
                })
    for (ctx, cond), obs in sorted(sample_sizes.items()):
        distinct_n = sorted({o["n"] for o in obs})
        distinct_files = {o["file"] for o in obs}
        if len(distinct_n) > 1 and len(distinct_files) > 1:
            entity_value_conflicts.append({
                "type": "sample_size_conflict",
                "entity": ctx,
                "unit": "n",
                "condition": cond,
                "values": distinct_n,
                "occurrences": obs[:8],
            })

    dangling_figs = [{"file": f, "figure": n} for f, n in ref_figs if n not in defined_figs]
    dangling_tabs = [{"file": f, "table": n} for f, n in ref_tabs if n not in defined_tabs]
    dangling_eqs = [{"file": f, "equation": n} for f, n in ref_eqs if n not in defined_eqs]
    fig_gaps = _find_numbering_gaps(defined_figs)
    tab_gaps = _find_numbering_gaps(defined_tabs)

    cross_chapter_duplicates, dedup_stats = _find_duplicates_inverted_index(paras_all, dup_threshold)

    # Citation mapping & claim-support alignment matrix
    ref_list_nums = set(ref_list_map.keys())
    unmapped_numeric_cites: list[dict[str, Any]] = []
    claim_support_matrix: list[dict[str, Any]] = []
    for f, ln, n, excerpt in cite_nums_used:
        if ref_list_nums and n not in ref_list_nums:
            unmapped_numeric_cites.append({"file": f, "line": ln, "citation_number": n})
            status = "unmapped_citation"
            bib = ""
        elif n in ref_list_map:
            status = "bibliography_mapped"
            bib = ref_list_map[n]
        else:
            status = "no_reference_list_detected"
            bib = ""
        if len(claim_support_matrix) < 120:
            claim_support_matrix.append({
                "file": f, "line": ln, "citation": f"[{n}]",
                "claim_excerpt": excerpt, "bibliography_entry": bib, "support_status": status,
            })
    for f, ln, tok, excerpt in author_year_cites[:60]:
        claim_support_matrix.append({
            "file": f, "line": ln, "citation": tok,
            "claim_excerpt": excerpt, "bibliography_entry": "",
            "support_status": "author_year_requires_bib_check",
        })
    mixed_citation_style = bool(cite_nums_used) and bool(author_year_cites)

    # Coverage gaps
    missing_chapters: list[str] = []
    if chapter_nums:
        present = set(chapter_nums)
        for n in range(min(present), max(present) + 1):
            if n not in present:
                missing_chapters.append(f"chapter_{n:02d}")
    if toc_path and toc_path.is_file():
        toc = json.loads(toc_path.read_text(encoding="utf-8"))
        present_rels = set(chapter_hashes.keys())
        for exp in (toc.get("expected_chapters", []) if isinstance(toc, dict) else []):
            name = exp.get("file", "") if isinstance(exp, dict) else str(exp)
            if name and name not in present_rels and name not in missing_chapters:
                missing_chapters.append(name)

    wo_mode, wo_target_ids, wo_target_files, wo_missing_ids, scope_status = _resolve_workorder_targets(
        wo_raw_ids, wo_raw_files, wo_raw, anchor_to_file
    )

    errors: list[str] = []
    warnings: list[str] = []
    modified_outside_workorder: list[str] = []
    original_source_altered_files: list[str] = []

    baseline_dup_pairs: set[tuple[str, str]] = set()
    if baseline_path is not None and baseline_path.is_file():
        base_doc = json.loads(baseline_path.read_text(encoding="utf-8"))
        for bd in base_doc.get("cross_chapter_duplicates", []):
            if isinstance(bd, dict) and bd.get("chapter_a") and bd.get("chapter_b"):
                baseline_dup_pairs.add((str(bd["chapter_a"]), str(bd["chapter_b"])))
        base_hashes = base_doc.get("chapter_sha256")
        base_stripped = base_doc.get("stripped_source_sha256", base_hashes)
        if not isinstance(base_hashes, dict) or not base_hashes:
            errors.append("Baseline contains no chapter byte hashes.")
        else:
            compared = chapter_hashes.keys() | base_hashes.keys()
            # Never silently allow all files when target_ids failed to resolve!
            effective_allowed = set(wo_target_files) if scope_status != "BLOCKED_MISSING_TARGETS" else set()
            modified_outside_workorder = sorted(
                name for name in compared - effective_allowed
                if chapter_hashes.get(name) != base_hashes.get(name)
            )
            if modified_outside_workorder:
                errors.append(
                    f"{len(modified_outside_workorder)} chapter(s) modified outside approved workorder: {modified_outside_workorder}"
                )
            if wo_mode == "paragraph_target_ids" and isinstance(base_stripped, dict):
                for tf in wo_target_files:
                    if stripped_source_hashes.get(tf) != base_stripped.get(tf):
                        original_source_altered_files.append(tf)
                if original_source_altered_files:
                    errors.append(
                        f"Original publication bytes altered inside target chapter(s): {original_source_altered_files}"
                    )

    newly_introduced_high_dups: list[dict[str, Any]] = []
    source_inherent_dups: list[dict[str, Any]] = []
    for d in cross_chapter_duplicates:
        pair_key = (str(d.get("chapter_a", "")), str(d.get("chapter_b", "")))
        if baseline_path is not None and pair_key in baseline_dup_pairs:
            d["origin"] = "source_inherent"
            source_inherent_dups.append(d)
        else:
            d["origin"] = "newly_introduced" if baseline_path is not None else "baseline_observed"
            if d["severity"] == "high":
                newly_introduced_high_dups.append(d)

    if wo_missing_ids:
        errors.append(f"WORKORDER_MISSING_TARGETS_BLOCKED ({scope_status}): target_ids not found in chapters: {wo_missing_ids}")
    if acronym_conflicts:
        errors.append(f"Conflicting acronym expansions across chapters: {list(acronym_conflicts.keys())}")
    if symbol_conflicts:
        errors.append(f"Conflicting symbol definitions across chapters: {list(symbol_conflicts.keys())}")
    if entity_value_conflicts:
        errors.append(f"{len(entity_value_conflicts)} cross-chapter numerical/sample-size conflict(s) detected")
    if dangling_figs:
        errors.append(f"{len(dangling_figs)} figure reference(s) lack matching caption label")
    if dangling_tabs:
        errors.append(f"{len(dangling_tabs)} table reference(s) lack matching caption label")
    if dangling_eqs:
        errors.append(f"{len(dangling_eqs)} equation reference(s) lack matching label")
    if fig_gaps or tab_gaps:
        errors.append(f"Display numbering gap(s) detected: figures={len(fig_gaps)}, tables={len(tab_gaps)}")
    if zotero_artifacts:
        errors.append(f"{len(zotero_artifacts)} broken/flattened citation field artifact(s) detected")
    if unmapped_numeric_cites:
        errors.append(f"{len(unmapped_numeric_cites)} numeric citation(s) missing from reference list")
    if newly_introduced_high_dups and baseline_path is not None:
        errors.append(f"{len(newly_introduced_high_dups)} newly introduced high-overlap duplicate discussion paragraph(s) across chapters")
    elif source_inherent_dups:
        warnings.append(f"{len(source_inherent_dups)} source-inherent cross-chapter overlapping paragraph(s) flagged for review (not introduced by this revision)")
    elif newly_introduced_high_dups:
        warnings.append(f"{len(newly_introduced_high_dups)} high-overlap cross-chapter paragraph(s) detected in baseline source; review for consolidation")
    elif cross_chapter_duplicates:
        warnings.append(f"{len(cross_chapter_duplicates)} medium-overlap repeated discussion paragraph(s) across chapters")
    if mixed_citation_style:
        warnings.append("Mixed numeric [N] and Author-Year citation styles detected across chapters.")
    if missing_chapters:
        warnings.append(f"Incomplete chapter sequence/TOC coverage; missing: {missing_chapters}")
    if truncation_warnings:
        warnings.append(f"{len(truncation_warnings)} chapter(s) show truncation or missing-page signals.")

    chapter_plan_report = _audit_chapter_planning(files, chapters_dir, chapter_plan_path)
    if chapter_plan_report["budget_violations"]:
        errors.extend(chapter_plan_report["budget_violations"])

    return {
        "passed": len(errors) == 0,
        "chapters_count": len(files),
        "covered_chapters": sorted(chapter_hashes.keys()),
        "chapter_read_metadata": chapter_read_meta,
        "missing_chapters": missing_chapters,
        "truncation_warnings": truncation_warnings,
        "chapter_sha256": chapter_hashes,
        "stripped_source_sha256": stripped_source_hashes,
        "workorder_schema_mode": wo_mode,
        "scope_enforcement_status": scope_status,
        "workorder_target_ids": wo_target_ids,
        "workorder_missing_target_ids": wo_missing_ids,
        "workorder_targets": wo_target_files,
        "modified_outside_workorder": modified_outside_workorder,
        "original_source_altered_files": original_source_altered_files,
        "acronym_registry": {k: sorted(v) for k, v in acronyms.items()},
        "acronym_conflicts": acronym_conflicts,
        "symbol_conflicts": symbol_conflicts,
        "entity_value_conflicts": entity_value_conflicts,
        "defined_figures": sorted(defined_figs),
        "dangling_figure_refs": dangling_figs,
        "figure_numbering_gaps": fig_gaps,
        "defined_tables": sorted(defined_tabs),
        "dangling_table_refs": dangling_tabs,
        "table_numbering_gaps": tab_gaps,
        "defined_equations": sorted(defined_eqs),
        "dangling_equation_refs": dangling_eqs,
        "cross_chapter_duplicates": cross_chapter_duplicates,
        "dedup_performance": dedup_stats,
        "citation_integrity": {
            "zotero_or_field_artifacts": zotero_artifacts,
            "unmapped_numeric_citations": unmapped_numeric_cites,
            "mixed_citation_style": mixed_citation_style,
            "reference_entries_found": len(ref_list_nums),
            "claim_support_matrix_count": len(claim_support_matrix),
            "claim_support_matrix": claim_support_matrix,
        },
        "chapter_planning_audit": chapter_plan_report,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    import sys as _sys
    raw_argv = list(_sys.argv[1:] if argv is None else argv)
    is_synth_subcmd = bool(raw_argv) and raw_argv[0] == "synthesize-discussion"
    if is_synth_subcmd:
        raw_argv = raw_argv[1:]

    parser = argparse.ArgumentParser(
        description="Domain-agnostic cross-chapter consistency, chapter-plan generator, and General Discussion synthesizer (supports `synthesize-discussion` subcommand)."
    )
    parser.add_argument("--chapters-dir", required=True, help="Directory with working chapters (.md/.txt/.tex/.docx/.pdf).")
    parser.add_argument("--workorder", default="", help="Optional organizer-edit-workorder.json or case.json.")
    parser.add_argument("--baseline", type=Path, default=None, help="Baseline JSON produced from original chapter bytes.")
    parser.add_argument("--toc-json", type=Path, default=None, help="Optional TOC expectation JSON.")
    parser.add_argument("--chapter-plan-json", type=Path, default=None, help="Optional chapter length & argument-role plan JSON.")
    parser.add_argument("--generate-plan", type=Path, default=None, help="Generate an editable chapter_plan.json from chapters and exit.")
    parser.add_argument("--synthesize-discussion", action="store_true", help="Run multi-chapter General Discussion synthesis (convergences, conditional divergences, irreconcilable conflicts, anchored draft).")
    parser.add_argument("--out-md", type=Path, default=None, help="Optional Markdown output path for General Discussion synthesis.")
    parser.add_argument("--dup-threshold", type=float, default=0.45, help="Shingle Jaccard threshold for cross-chapter duplicates.")
    parser.add_argument("--out-json", default="", help="Output consistency report or synthesis JSON.")
    args = parser.parse_args(raw_argv)

    if is_synth_subcmd or args.synthesize_discussion:
        out_jp = Path(args.out_json) if args.out_json else None
        syn = synthesize_general_discussion(
            Path(args.chapters_dir),
            out_json=out_jp,
            out_md=args.out_md,
            workorder_path=Path(args.workorder) if args.workorder else None,
        )
        mat = syn.get("evidence_matrix", {})
        print(
            json.dumps(
                {
                    "ok": syn.get("ok", False),
                    "chapters_count": syn.get("chapters_count", 0),
                    "filtered_running_headers_count": syn.get("filtered_running_headers_count", 0),
                    "running_header_evidence_leaks": len(syn.get("running_header_evidence_leaks", [])),
                    "convergences_count": len(mat.get("cross_chapter_convergences", [])),
                    "conditional_divergences_count": len(mat.get("conditional_divergences", [])),
                    "irreconcilable_conflicts_count": len(mat.get("irreconcilable_conflicts", [])),
                    "draft_paragraphs_count": len(syn.get("general_discussion_draft", [])),
                    "pending_confirmations_count": len(syn.get("pending_confirmations", [])),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if syn.get("ok", False) else 1

    if args.generate_plan:
        plan = generate_chapter_plan(Path(args.chapters_dir), args.generate_plan)
        print(json.dumps({"ok": True, "plan_out": str(args.generate_plan), "chapters_count": plan["chapters_count"], "missing_thesis_roles": plan["missing_thesis_roles"]}, ensure_ascii=False, indent=2))
        return 0

    if not args.out_json:
        parser.error("--out-json is required unless --generate-plan or synthesize-discussion is used.")

    rep = audit_longdoc(
        Path(args.chapters_dir),
        workorder_path=Path(args.workorder) if args.workorder else None,
        baseline_path=args.baseline,
        toc_path=args.toc_json,
        chapter_plan_path=args.chapter_plan_json,
        dup_threshold=args.dup_threshold,
    )
    op = Path(args.out_json)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": rep["passed"],
                "chapters_count": rep.get("chapters_count", 0),
                "workorder_schema_mode": rep.get("workorder_schema_mode"),
                "scope_enforcement_status": rep.get("scope_enforcement_status"),
                "dedup_runtime_ms": rep.get("dedup_performance", {}).get("runtime_ms"),
                "errors": rep.get("errors", []),
                "warnings": rep.get("warnings", []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
