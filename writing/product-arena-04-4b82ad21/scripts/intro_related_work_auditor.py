#!/usr/bin/env python3
"""Introduction & Related Work four-beat, taxonomy, bounded-gap, and 1:1 contribution auditor.

Target task: introduction-related-work-writing (Writing 08 | Introduction & Related Work Drafting).
Zero third-party dependencies (Python 3.12+ stdlib + sibling writing_io_and_invariants.py).
Produces:
  - related_work_taxonomy.json       (dimension-grouped literature matrix, conflict analysis,
                                      four-beat check, gap audit, contribution alignment)
  - gap_contribution_alignment.md    (human-readable comparative taxonomy + 1:1 alignment table)
  - result.json                      (Harbor/structured output contract)
"""

from __future__ import annotations

import argparse
import csv
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
# Patterns (bilingual EN + ZH)
# ---------------------------------------------------------------------------

MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SENT_SPLIT_RE = re.compile(
    r"(?<!\bal\.)(?<!\bFig\.)(?<!\bFigs\.)(?<!\bEq\.)(?<!\bTab\.)(?<!\bTbl\.)(?<!\bvs\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bNo\.)(?<!\bDr\.)(?<!\bRef\.)(?<!\bSec\.)(?<=[。！？.!?])\s+",
    re.I,
)

HOLLOW_OPENER_RE = re.compile(
    r"(?:近年来[，,]?[^。！？\n]{0,28}(?:受到(?:了)?广泛关注|成为研究热点|飞速发展|蓬勃发展|日新月异)"
    r"|随着(?:人工|科学|信息|现代)?技术(?:与社会)?的(?:不断|快速|飞速)发展"
    r"|\bin\s+recent\s+years,?\s+[^.?!]{0,60}\b(?:attracted\s+(?:widespread|increasing|growing)\s+attention|become\s+a\s+hot\s+topic|rapidly\s+developed|gained\s+momentum)\b"
    r"|\bwith\s+the\s+rapid\s+development\s+of\b)",
    re.I,
)

# Four-beat press-release opening detectors
BEAT_PROBLEM_RE = re.compile(
    r"\b(?:bottleneck|challenge|problem|failure\s+mode|trade-?off|constraint|critical\s+barrier|clinical\s+need|remains\s+difficult)\b"
    r"|核心问题|技术瓶颈|主要挑战|关键难题|约束条件|实际痛点|尚难解决",
    re.I,
)
BEAT_GAP_RE = re.compile(
    r"\b(?:however|yet|nevertheless|remain(?:s)?\s+(?:unclear|unresolved|unaddressed|limited|contradictory)|"
    r"limitation|fail(?:s|ed)?\s+to|diverg(?:e|ent|ing)|conflict(?:s|ing)?|gap)\b"
    r"|然而|但是|仍存在|局限在于|分歧|冲突|未能解决|尚不清楚|研究空白|缺口",
    re.I,
)
BEAT_APPROACH_RE = re.compile(
    r"\b(?:in\s+this\s+(?:paper|study|work),?\s+we\s+(?:propose|introduce|present|develop|design|construct|formulate)|"
    r"to\s+address\s+this\s+(?:gap|limitation|challenge),?\s+we|our\s+(?:method|framework|approach|model))\b"
    r"|为此，?本文(?:提出|构建|设计|开发)|针对上述(?:缺口|局限|问题)，?我们",
    re.I,
)
BEAT_FINDING_RE = re.compile(
    r"\b(?:experiment(?:s|al\s+results)?\s+(?:show|demonstrate|indicate|confirm)|"
    r"outperform(?:s|ed)?|achiev(?:e|es|ed)|reduc(?:e|es|ed)\s+[^.]{0,40}\bby\b|"
    r"improv(?:e|es|ed)\s+[^.]{0,40}\bby\b|\d+(?:\.\d+)?%\s+(?:higher|lower|improvement|reduction|gain|accuracy|AUC|F1))\b"
    r"|实验结果表明|在.{1,25}上(?:提升|降低|达到|优于)[^。]{0,30}\d|核心发现",
    re.I,
)

# Unsubstantiated / unbounded "first" or "no prior work" claims
ABSOLUTE_GAP_RE = re.compile(
    r"(?:尚无(?:任何)?(?:相关)?研究|从未(?:被)?研究|国内外尚未(?:见|有)报道|首次(?:系统)?(?:提出|探讨|研究|证明)|"
    r"填补(?:了)?(?:领域|国内|国际)?空白|前所未有|目前没有任何研究|"
    r"\bfirst\s+(?:ever|study\s+to|work\s+to|attempt\s+to|to\s+the\s+best\s+of\s+our\s+knowledge,?\s+no)\b|"
    r"\bno\s+(?:prior|previous|existing)\s+(?:work|stud(?:y|ies)|research|method(?:s)?)\b|"
    r"\bhas\s+never\s+been\s+(?:studied|explored|investigated|addressed)\b|"
    r"\bunprecedented\b|\bfills?\s+this\s+(?:critical\s+)?gap\s+for\s+the\s+first\s+time\b)",
    re.I,
)
SCOPE_BOUNDARY_RE = re.compile(
    r"\b(?:under\s+[^,.;]{3,50}|within\s+[^,.;]{3,50}|in\s+(?:the\s+context\s+of|high-|low-|small-|clinical|in\s+vitro|multi-center|non-stationary)[^,.;]{0,50}|"
    r"specifically\s+for|scoped\s+to|restricted\s+to|when\s+[^,.;]{5,60}|compared\s+(?:with|to)|unlike\s+[A-Z])\b"
    r"|在.{2,25}(?:条件下|场景下|范围内| regime|队列中)|针对.{2,25}(?:场景|人群|样本|条件)|受限于|局限于|相较于",
    re.I,
)
LIMITATION_OR_CONFLICT_CUE_RE = re.compile(
    r"\b(?:limit(?:ed|ation)|conflict(?:ing|s)?|diverg(?:e|ent|ence)|inconsistent|contradict(?:ory|s)?|"
    r"assume(?:s|d)?|fail(?:s|ed)?\s+when|degrade(?:s|d)?|restricted\s+to|narrow\s+(?:cohort|regime|scope)|only\s+evaluated)\b"
    r"|局限|分歧|冲突|不一致|矛盾|假设|仅在.{1,20}验证|适用范围窄|失效|未覆盖",
    re.I,
)

# Chronological author-year stitching detection
YEAR_SENT_RE = re.compile(
    r"(?:^|[.!?。！？]\s*)(?:(?:[A-Z][A-Za-z\-]+(?:\s+et\s+al\.|\s+and\s+[A-Z][A-Za-z\-]+)?\s*\(\s*((?:19|20)\d{2})[a-z]?\s*\))"
    r"|(?:In\s+((?:19|20)\d{2}),?\s+[A-Z][A-Za-z\-]+)"
    r"|(?:((?:19|20)\d{2})\s*年[^。！？\n]{0,18}(?:等|团队|学者|研究|报道|提出)))"
)
DIMENSION_AXIS_RE = re.compile(
    r"\b(?:categor(?:y|ized|ies)|taxonom(?:y|ies)|group(?:ed|s)?\s+(?:by|into)|"
    r"paradigm|route|axis|axes|dimension|trade-?off|in\s+contrast|whereas|unlike|"
    r"first\s+line\s+of\s+work|second\s+line\s+of\s+work|on\s+the\s+other\s+hand|compar(?:ed|ing)\s+across)\b"
    r"|按.{1,12}(?:维度|路线|范式|机制|设定)划分|第一类|第二类|相比之下|与之相对|共同局限|横向比较|核心取舍",
    re.I,
)

# Citation extractors
CITE_AY_RE = re.compile(
    r"\(([A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-]+|&\s+[A-Z][A-Za-z\-]+))?,?\s*(?:19|20)\d{2}[a-z]?"
    r"(?:\s*;\s*[A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-]+|&\s+[A-Z][A-Za-z\-]+))?,?\s*(?:19|20)\d{2}[a-z]?)*)\)"
)
CITE_NARRATIVE_AY_RE = re.compile(r"\b([A-Z][A-Za-z\-]+(?:\s+et\s+al\.)?)\s*\(\s*((?:19|20)\d{2}[a-z]?)\s*\)")
CITE_BRACKET_RE = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
CITE_TEX_RE = re.compile(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.I)

CONTRIB_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|\(\d+\)|[（(][一二三四五1-5][）)])\s+(.+)$")
CONTRIB_HEADER_RE = re.compile(r"\b(?:our\s+(?:main\s+|key\s+|primary\s+)?contributions?\s+(?:are|include|can\s+be\s+summarized)|in\s+summary,?\s+we\s+make)\b|本文(?:的)?主要贡献|核心贡献(?:如下|包括)", re.I)

POS_OUTCOME_RE = re.compile(r"\b(?:improv|increas|higher|positive|effective|outperform|gain|beneficial|reduces?\s+error)\w*\b|提升|提高|正相关|有效|优于|显著改善", re.I)
NEG_OUTCOME_RE = re.compile(r"\b(?:degrad|decreas|lower|negative|ineffective|no\s+significant|fail(?:s|ed)?|harmful|worse|null)\w*\b|下降|降低|负相关|无效|无显著|劣于|未能改善", re.I)


def _tokenize(text: str) -> set[str]:
    en = re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
    zh = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    zh_bi: list[str] = []
    for chunk in zh:
        if len(chunk) == 2:
            zh_bi.append(chunk)
        else:
            for k in range(len(chunk) - 1):
                zh_bi.append(chunk[k : k + 2])
    stop = {
        "the", "and", "that", "this", "with", "from", "for", "are", "was", "were",
        "have", "has", "been", "into", "which", "their", "our", "not", "but", "can",
        "also", "more", "than", "using", "used", "paper", "study", "work", "method",
        "results", "propose", "present", "introduce", "show", "demonstrate", "based",
        "我们", "本文", "研究", "方法", "结果", "提出", "通过", "进行", "以及", "可以",
    }
    return {t for t in en + zh_bi if t not in stop}


def _extract_cites(text: str) -> list[str]:
    found: list[str] = []
    for m in CITE_AY_RE.finditer(text):
        for part in m.group(1).split(";"):
            p = part.strip()
            if p:
                found.append(p)
    for m in CITE_NARRATIVE_AY_RE.finditer(text):
        found.append(f"{m.group(1).strip()}, {m.group(2).strip()}")
    for m in CITE_BRACKET_RE.finditer(text):
        found.append(f"[{m.group(1).strip()}]")
    for m in CITE_TEX_RE.finditer(text):
        for k in m.group(1).split(","):
            if k.strip():
                found.append(k.strip())
    for m in DOI_RE.finditer(text):
        found.append(m.group(1).rstrip(".,;)").lower())
    return list(dict.fromkeys(found))


def _load_literature_entries(bib_path: Path | None, corpus_dir: Path | None) -> list[dict[str, Any]]:
    """Load structured literature cards from JSON/CSV/BIB/MD files."""
    entries: list[dict[str, Any]] = []
    files_to_read: list[Path] = []
    if bib_path and bib_path.is_file():
        files_to_read.append(bib_path)
    if corpus_dir and corpus_dir.is_dir():
        for fp in sorted(corpus_dir.rglob("*")):
            if not fp.is_file():
                continue
            low = fp.name.lower()
            if low in {"case.json", "task.md"}:
                continue
            if (
                any(k in low for k in ("bib", "ref", "lit", "paper", "prior", "source"))
                or fp.suffix.lower() in {".bib", ".pdf", ".docx"}
            ):
                if fp not in files_to_read:
                    files_to_read.append(fp)

    for fp in files_to_read:
        suf = fp.suffix.lower()
        if suf in {".docx", ".pdf"}:
            doc = wio.read_document_text(fp)
            if not doc.get("ok"):
                entries.append({
                    "cite_key": fp.stem,
                    "author": fp.stem,
                    "year": "",
                    "doi": "",
                    "title": "",
                    "method_route": "unreadable_degraded",
                    "scope_condition": "degraded_extractor_unavailable",
                    "finding": f"UNREADABLE_{suf.lstrip('.').upper()}: {doc.get('meta', {}).get('error', 'extractor unavailable')}",
                    "direction": "neutral",
                    "limitation": "Extractor unavailable in offline container",
                    "source_file": fp.name,
                })
                continue
            raw = doc.get("text", "")
        else:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        if suf == ".json":
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            items = obj if isinstance(obj, list) else obj.get("references", obj.get("papers", obj.get("literature", [])))
            if isinstance(items, list):
                for idx, it in enumerate(items, 1):
                    if not isinstance(it, dict):
                        continue
                    author = str(it.get("author") or it.get("authors") or it.get("first_author") or f"Author{idx}").strip()
                    year = str(it.get("year") or "").strip()
                    key = str(it.get("key") or it.get("id") or f"{author.split()[0]}, {year}").strip()
                    finding = str(it.get("finding") or it.get("conclusion") or it.get("result") or it.get("abstract") or "").strip()
                    scope = str(it.get("scope") or it.get("condition") or it.get("dataset") or it.get("applicability") or "unspecified").strip()
                    route = str(it.get("method_route") or it.get("method") or it.get("category") or "general").strip()
                    lim = str(it.get("limitation") or it.get("limitations") or "").strip()
                    direction = "positive" if (POS_OUTCOME_RE.search(finding) and not NEG_OUTCOME_RE.search(finding)) else (
                        "negative" if NEG_OUTCOME_RE.search(finding) else "neutral"
                    )
                    entries.append({
                        "cite_key": key,
                        "author": author,
                        "year": year,
                        "doi": str(it.get("doi") or "").strip().lower(),
                        "title": str(it.get("title") or "").strip(),
                        "method_route": route,
                        "scope_condition": scope,
                        "finding": finding,
                        "direction": direction,
                        "limitation": lim,
                        "source_file": fp.name,
                    })
        elif suf == ".csv":
            rows = list(csv.DictReader(raw.splitlines()))
            for idx, r in enumerate(rows, 1):
                author = (r.get("author") or r.get("authors") or f"Author{idx}").strip()
                year = (r.get("year") or "").strip()
                key = (r.get("key") or r.get("cite_key") or r.get("id") or f"{author.split()[0]}, {year}").strip()
                finding = (r.get("finding") or r.get("conclusion") or r.get("result") or "").strip()
                scope = (r.get("scope") or r.get("condition") or r.get("dataset") or "unspecified").strip()
                route = (r.get("method_route") or r.get("method") or r.get("category") or "general").strip()
                lim = (r.get("limitation") or "").strip()
                direction = "positive" if (POS_OUTCOME_RE.search(finding) and not NEG_OUTCOME_RE.search(finding)) else (
                    "negative" if NEG_OUTCOME_RE.search(finding) else "neutral"
                )
                entries.append({
                    "cite_key": key, "author": author, "year": year, "doi": (r.get("doi") or "").strip().lower(),
                    "title": (r.get("title") or "").strip(), "method_route": route, "scope_condition": scope,
                    "finding": finding, "direction": direction, "limitation": lim, "source_file": fp.name,
                })
        elif suf == ".bib":
            for m in re.finditer(r"@\w+\{([^,\s]+),([\s\S]*?)\n\}", raw):
                key, body = m.group(1).strip(), m.group(2)
                def _f(name: str) -> str:
                    fm = re.search(rf"{name}\s*=\s*[\{{\"]([^}}\"]+)[\}}\"]", body, re.I)
                    return fm.group(1).strip() if fm else ""
                entries.append({
                    "cite_key": key, "author": _f("author"), "year": _f("year"), "doi": _f("doi").lower(),
                    "title": _f("title"), "method_route": "from_bibtex", "scope_condition": "unspecified",
                    "finding": _f("abstract") or _f("note"), "direction": "neutral", "limitation": "", "source_file": fp.name,
                })
        elif suf in {".md", ".txt", ".docx", ".pdf"}:
            # Parse structured bullet/section literature notes or extracted DOCX/PDF full text
            for idx, ln in enumerate(raw.splitlines(), 1):
                st = ln.strip()
                if not st or st.startswith("#"):
                    continue
                cites = _extract_cites(st)
                ym = re.search(r"\b((?:19|20)\d{2})\b", st)
                if cites or ym or suf in {".docx", ".pdf"} and DOI_RE.search(st):
                    key = cites[0] if cites else f"{fp.stem}:L{idx}"
                    direction = "positive" if (POS_OUTCOME_RE.search(st) and not NEG_OUTCOME_RE.search(st)) else (
                        "negative" if NEG_OUTCOME_RE.search(st) else "neutral"
                    )
                    entries.append({
                        "cite_key": key, "author": key.split(",")[0], "year": ym.group(1) if ym else "",
                        "doi": (DOI_RE.search(st).group(1).lower() if DOI_RE.search(st) else ""),
                        "title": "", "method_route": f"extracted_{suf.lstrip('.')}", "scope_condition": "see_excerpt",
                        "finding": st[:220], "direction": direction, "limitation": "", "source_file": f"{fp.name}:L{idx}",
                    })
    return entries


def _load_own_evidence(evidence_dir: Path | None, draft_path: Path) -> dict[str, Any]:
    """Load the author's own methods/results/figures/tables artifacts (including DOCX/PDF) to verify 1:1 contribution alignment."""
    evidence_texts: list[str] = []
    evidence_files: list[str] = []
    degraded_files: list[dict[str, str]] = []
    figure_table_ids: set[str] = set()
    if evidence_dir and evidence_dir.is_dir():
        for fp in sorted(evidence_dir.rglob("*")):
            if not fp.is_file() or fp.resolve() == draft_path.resolve():
                continue
            low = fp.name.lower()
            if low in {"case.json", "task.md"}:
                continue
            if any(k in low for k in ("bib", "ref", "lit", "prior")) and not any(k in low for k in ("method", "result", "table", "fig", "exp")):
                continue
            evidence_files.append(fp.name)
            # Detect figure/table filenames directly
            for fm in re.finditer(r"(fig(?:ure)?|table|tab)[_\-\s]*s?(\d+[a-z]?)", low):
                kind = "figure" if fm.group(1).startswith("fig") else "table"
                figure_table_ids.add(f"{kind}_{fm.group(2)}")
            suf = fp.suffix.lower()
            txt = ""
            if suf in {".docx", ".pdf"}:
                doc = wio.read_document_text(fp)
                if doc.get("ok"):
                    txt = doc.get("text", "")
                else:
                    degraded_files.append({"file": fp.name, "reason": str(doc.get("meta", {}).get("error", "unreadable"))})
            elif suf in {".md", ".txt", ".json", ".csv", ".tex", ".py", ".yaml", ".yml"}:
                txt = fp.read_text(encoding="utf-8", errors="replace")
            if txt:
                evidence_texts.append(txt)
                for m in wio.RE_FIG_REF.finditer(txt):
                    ref = (m.group(1) or m.group(2)).strip().lower()
                    num_m = re.search(r"s?\d+[a-z]?", ref)
                    if num_m:
                        kind = "figure" if ("fig" in ref or "图" in ref) else "table"
                        figure_table_ids.add(f"{kind}_{num_m.group(0)}")
    joined = "\n".join(evidence_texts)
    return {
        "files": evidence_files,
        "degraded_files": degraded_files,
        "text": joined,
        "tokens": _tokenize(joined),
        "figure_table_ids": sorted(figure_table_ids),
        "invariants": wio.extract_invariants(joined) if joined else {"bare_numbers": []},
    }


def _split_draft_sections(text: str) -> dict[str, list[tuple[int, str]]]:
    sections: dict[str, list[tuple[int, str]]] = {"introduction": [], "related_work": [], "other": []}
    cur = "introduction"
    for lineno, raw in enumerate(text.splitlines(), 1):
        st = raw.strip()
        hm = MD_HEADING_RE.match(st)
        if hm:
            title = hm.group(2).lower()
            if any(k in title for k in ("related work", "literature", "prior work", "相关工作", "文献综述", "研究现状")):
                cur = "related_work"
            elif any(k in title for k in ("intro", "background", "引言", "绪论", "背景")):
                cur = "introduction"
            else:
                cur = "other"
            continue
        if st:
            sections[cur].append((lineno, st))
    return sections


def _entry_is_cited(entry: dict[str, Any], draft_text: str, draft_cites: list[str]) -> bool:
    low_draft = draft_text.lower()
    if entry["doi"] and entry["doi"] in low_draft:
        return True
    key = entry["cite_key"].lower()
    if key and (key in low_draft or any(key in c.lower() for c in draft_cites)):
        return True
    surname = re.split(r"[\s,;]+", entry["author"].strip())[0].lower() if entry["author"] else ""
    year = entry["year"].strip()
    if surname and len(surname) >= 3 and year and surname in low_draft and year in low_draft:
        return True
    return False


def audit_intro_and_related_work(
    draft_path: Path,
    *,
    bib_path: Path | None = None,
    corpus_dir: Path | None = None,
    evidence_dir: Path | None = None,
    case_id: str = "case-01",
    task_id: str = "introduction-related-work-writing",
) -> dict[str, Any]:
    read_res = wio.read_document_text(draft_path)
    if not read_res["ok"]:
        raise RuntimeError(f"Failed to read draft {draft_path}: {read_res.get('meta')}")
    draft_text = read_res["text"]
    sec_lines = _split_draft_sections(draft_text)
    intro_text = "\n".join(t for _, t in sec_lines["introduction"])
    rw_text = "\n".join(t for _, t in sec_lines["related_work"])
    all_lines = [(ln, t, sec) for sec, items in sec_lines.items() for ln, t in items]

    lit_entries = _load_literature_entries(bib_path, corpus_dir)
    own_ev = _load_own_evidence(evidence_dir or corpus_dir, draft_path)
    draft_cites = _extract_cites(draft_text)

    findings: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    def add_finding(code: str, severity: str, section: str, line: int, quote: str, message: str, remediation: str) -> None:
        fid = f"IRW-{len(findings) + 1:03d}"
        findings.append({
            "finding_id": fid,
            "code": code,
            "severity": severity,
            "section": section,
            "line": line,
            "quote": quote[:180],
            "message": message,
            "remediation": remediation,
        })
        evidence_map.append({
            "finding_id": fid,
            "code": code,
            "source_file": draft_path.name,
            "section": section,
            "line": line,
            "quote": quote[:140],
        })

    # =========================================================================
    # 1. Four-Beat Press-Release Opening Check + Hollow Opener Interception
    # =========================================================================
    four_beats = {
        "problem": bool(BEAT_PROBLEM_RE.search(intro_text or draft_text)),
        "gap": bool(BEAT_GAP_RE.search(intro_text or draft_text)),
        "approach": bool(BEAT_APPROACH_RE.search(intro_text or draft_text)),
        "key_finding": bool(BEAT_FINDING_RE.search(intro_text or draft_text)),
    }
    for beat_name, present in four_beats.items():
        if not present:
            add_finding(
                code=f"MISSING_FOUR_BEAT_{beat_name.upper()}",
                severity="major",
                section="introduction",
                line=sec_lines["introduction"][0][0] if sec_lines["introduction"] else 1,
                quote=(sec_lines["introduction"][0][1] if sec_lines["introduction"] else ""),
                message=f"Introduction is missing press-release opening beat '{beat_name}' (required arc: Problem -> Gap -> Approach -> Key Finding).",
                remediation=f"Add an explicit, source-grounded '{beat_name}' statement in Introduction anchored to concrete evidence.",
            )

    for ln, txt, sec in all_lines:
        hm = HOLLOW_OPENER_RE.search(txt)
        if hm:
            add_finding(
                code="HOLLOW_GENERIC_OPENER",
                severity="major",
                section=sec,
                line=ln,
                quote=txt,
                message=f"Generic hollow opening cliché detected ('{hm.group(0)}'); fails to establish a concrete technical constraint or problem.",
                remediation="Replace the generic importance opener with the specific unresolved scientific/technical constraint and a cited baseline.",
            )

    # =========================================================================
    # 2. Related Work Grouping vs Chronological Summary Stitching & Divergence
    # =========================================================================
    rw_scan_lines = sec_lines["related_work"] if sec_lines["related_work"] else sec_lines["introduction"]
    rw_years: list[tuple[int, int, str]] = []
    for ln, txt in rw_scan_lines:
        for sent in [s.strip() for s in SENT_SPLIT_RE.split(txt) if s.strip()]:
            ym = YEAR_SENT_RE.search(sent)
            if ym:
                yr_s = ym.group(1) or ym.group(2) or ym.group(3)
                if yr_s and yr_s.isdigit():
                    rw_years.append((int(yr_s), ln, sent))
    has_dimension_grouping = bool(DIMENSION_AXIS_RE.search(rw_text or intro_text))
    chronological_stitching = False
    if len(rw_years) >= 3:
        yrs = [x[0] for x in rw_years]
        if yrs == sorted(yrs) and not has_dimension_grouping:
            chronological_stitching = True
            add_finding(
                code="CHRONOLOGICAL_SUMMARY_STITCHING",
                severity="blocking",
                section="related_work" if sec_lines["related_work"] else "introduction",
                line=rw_years[0][1],
                quote=rw_years[0][2],
                message=f"Related Work lists {len(rw_years)} studies in chronological order ({yrs}) as isolated per-paper summaries without shared comparison dimensions.",
                remediation="Reorganize Related Work by shared comparison axes (problem setting, method route, applicability regime, evaluation metric) and contrast trade-offs explicitly.",
            )

    # Analyze supplied literature for divergent findings across narrow scopes
    pos_lit = [e for e in lit_entries if e["direction"] == "positive"]
    neg_lit = [e for e in lit_entries if e["direction"] == "negative"]
    supplied_lit_conflict = bool(pos_lit and neg_lit)
    draft_acknowledges_conflict = bool(re.search(
        r"\b(?:conflict|diverg|contradict|inconsistent|differ(?:ing|ent)\s+(?:condition|cohort|regime|scope|setting)|whereas|in\s+contrast)\b|分歧|冲突|不一致|矛盾|条件差异|适用范围",
        draft_text,
        re.I,
    ))
    if supplied_lit_conflict and not draft_acknowledges_conflict:
        add_finding(
            code="UNRECONCILED_PRIOR_WORK_DIVERGENCE",
            severity="blocking",
            section="related_work",
            line=rw_scan_lines[0][0] if rw_scan_lines else 1,
            quote=(rw_scan_lines[0][1] if rw_scan_lines else ""),
            message=(
                f"Supplied literature contains conflicting conclusions ({len(pos_lit)} positive vs {len(neg_lit)} negative/null studies across narrow conditions), "
                "but the draft does not explain their differing scope conditions."
            ),
            remediation="Explicitly contrast the experimental conditions/cohorts of conflicting studies and derive a scope-bounded research gap from that divergence.",
        )

    # Build comparative taxonomy matrix grouped by method_route
    taxonomy_groups: dict[str, list[dict[str, Any]]] = {}
    for e in lit_entries:
        taxonomy_groups.setdefault(e["method_route"] or "general", []).append({
            "cite_key": e["cite_key"],
            "year": e["year"],
            "scope_condition": e["scope_condition"],
            "direction": e["direction"],
            "finding_excerpt": e["finding"][:160],
            "limitation": e["limitation"] or "not_explicitly_stated_in_card",
            "cited_in_draft": _entry_is_cited(e, draft_text, draft_cites),
        })

    # =========================================================================
    # 3. Unsubstantiated Gap / Baseless "First Ever / No Prior Work" Interceptor
    # =========================================================================
    unsubstantiated_gap_count = 0
    for ln, txt, sec in all_lines:
        for sent in [s.strip() for s in SENT_SPLIT_RE.split(txt) if s.strip()]:
            gm = ABSOLUTE_GAP_RE.search(sent)
            if not gm:
                continue
            para_cites = _extract_cites(txt)
            has_lim_cue = bool(LIMITATION_OR_CONFLICT_CUE_RE.search(txt))
            has_scope_bound = bool(SCOPE_BOUNDARY_RE.search(sent)) or bool(SCOPE_BOUNDARY_RE.search(txt))
            missing_parts = []
            if not para_cites:
                missing_parts.append("no cited prior work anchoring the gap")
            if not has_lim_cue:
                missing_parts.append("no concrete limitation/conflict of prior work")
            if not has_scope_bound:
                missing_parts.append("no explicit applicability scope boundary")
            if missing_parts:
                unsubstantiated_gap_count += 1
                add_finding(
                    code="UNSUBSTANTIATED_GAP_CLAIM",
                    severity="blocking",
                    section=sec,
                    line=ln,
                    quote=sent,
                    message=f"Unbounded/unsubstantiated gap or novelty claim ('{gm.group(0)}'): {'; '.join(missing_parts)}.",
                    remediation=(
                        "Remove absolute 'first ever / no prior work / 尚无研究' phrasing; derive the gap explicitly "
                        "from concrete cited limitations or condition conflicts and state the exact applicability scope."
                    ),
                )

    # =========================================================================
    # 4. Contribution Items <-> Methods/Figures/Tables/Results 1:1 Alignment
    # =========================================================================
    contribution_items: list[dict[str, Any]] = []
    in_contrib_block = False
    for ln, txt in sec_lines["introduction"]:
        if CONTRIB_HEADER_RE.search(txt):
            in_contrib_block = True
        bm = CONTRIB_BULLET_RE.match(txt)
        if bm and (in_contrib_block or re.search(r"\b(?:propose|develop|introduce|design|construct|validate|demonstrate|framework|dataset|benchmark|algorithm)\b|提出|构建|设计|验证|方法|实验|框架", txt, re.I)):
            contribution_items.append({"line": ln, "text": bm.group(1).strip()})
        elif in_contrib_block and not bm and len(contribution_items) > 0:
            in_contrib_block = False

    # Fallback: extract explicit contribution sentences if not bulleted
    if not contribution_items:
        for ln, txt in sec_lines["introduction"]:
            for sent in [s.strip() for s in SENT_SPLIT_RE.split(txt) if s.strip()]:
                if re.search(r"\b(?:we\s+(?:propose|introduce|develop|construct|validate|demonstrate)|our\s+(?:first|second|third|main)\s+contribution)\b|我们(?:提出|构建|验证)了", sent, re.I):
                    contribution_items.append({"line": ln, "text": sent})

    contribution_alignment: list[dict[str, Any]] = []
    overclaimed_count = 0
    for idx, item in enumerate(contribution_items, 1):
        cid = f"CONTRIB-{idx:02d}"
        ctoks = _tokenize(item["text"])
        ev_toks = own_ev["tokens"]
        overlap = sorted(ctoks & ev_toks)
        coverage = len(overlap) / max(1, len(ctoks)) if ctoks else 0.0

        # Check figure/table references cited inside the contribution
        cited_fts = []
        missing_fts = []
        for m in wio.RE_FIG_REF.finditer(item["text"]):
            ref = (m.group(1) or m.group(2)).strip()
            num_m = re.search(r"s?\d+[a-z]?", ref.lower())
            if num_m:
                kind = "figure" if ("fig" in ref.lower() or "图" in ref) else "table"
                norm_id = f"{kind}_{num_m.group(0)}"
                cited_fts.append(ref)
                if own_ev["figure_table_ids"] and norm_id not in own_ev["figure_table_ids"]:
                    missing_fts.append(ref)

        # Check numeric claims in contribution against own evidence numbers (excluding Fig/Table label digits)
        text_no_fig_labels = wio.RE_FIG_REF.sub(" ", item["text"])
        item_nums = set(wio.extract_invariants(text_no_fig_labels)["bare_numbers"])
        ev_nums = set(own_ev["invariants"]["bare_numbers"])
        ungrounded_nums = sorted(item_nums - ev_nums) if ev_nums else []

        status = "VERIFIED_1_TO_1"
        reasons: list[str] = []
        if not own_ev["files"]:
            status = "UNVERIFIABLE_NO_EVIDENCE_FILES"
            reasons.append("No author Methods/Results/Figure/Table files supplied to verify contribution.")
        else:
            if coverage < 0.20:
                status = "OVERCLAIMED_CONTRIBUTION"
                reasons.append(f"Low conceptual overlap ({coverage:.2f}) with supplied Methods/Results files.")
            if missing_fts:
                status = "OVERCLAIMED_CONTRIBUTION"
                reasons.append(f"Referenced Fig/Table not found in supplied evidence: {missing_fts}.")
            if ungrounded_nums:
                status = "OVERCLAIMED_CONTRIBUTION"
                reasons.append(f"Quantitative value(s) {ungrounded_nums} in contribution absent from supplied results.")

        contribution_alignment.append({
            "contribution_id": cid,
            "line": item["line"],
            "text": item["text"][:220],
            "token_coverage": round(coverage, 3),
            "matched_tokens_sample": overlap[:8],
            "cited_fig_tables": cited_fts,
            "missing_fig_tables": missing_fts,
            "ungrounded_numbers": ungrounded_nums,
            "alignment_status": status,
            "reasons": reasons,
        })
        if status == "OVERCLAIMED_CONTRIBUTION":
            overclaimed_count += 1
            add_finding(
                code="OVERCLAIMED_CONTRIBUTION",
                severity="blocking",
                section="introduction",
                line=item["line"],
                quote=item["text"],
                message=f"Contribution {cid} is not substantiated by supplied Methods/Results/Figure/Table evidence: {'; '.join(reasons)}",
                remediation=f"Either supply the supporting Methods/Results artifact or narrow/remove {cid} so contributions match verified evidence 1:1.",
            )

    if not contribution_items:
        add_finding(
            code="MISSING_ITEMIZED_CONTRIBUTIONS",
            severity="major",
            section="introduction",
            line=sec_lines["introduction"][-1][0] if sec_lines["introduction"] else 1,
            quote=(sec_lines["introduction"][-1][1] if sec_lines["introduction"] else ""),
            message="Introduction lacks explicit, verifiable itemized contributions.",
            remediation="Add 2-4 concise contribution bullets anchored 1:1 to supplied Methods, Figures, Tables, and Results.",
        )

    # =========================================================================
    # 5. Citation Coverage & Verification against Supplied Bibliography
    # =========================================================================
    uncovered_refs: list[str] = []
    for e in lit_entries:
        if not _entry_is_cited(e, draft_text, draft_cites):
            uncovered_refs.append(e["cite_key"])
    if uncovered_refs:
        add_finding(
            code="UNCOVERED_SUPPLIED_REFERENCES",
            severity="major",
            section="related_work",
            line=rw_scan_lines[0][0] if rw_scan_lines else 1,
            quote=f"Uncited supplied references: {', '.join(uncovered_refs[:6])}",
            message=f"{len(uncovered_refs)} supplied literature source(s) are not cited in the draft: {', '.join(uncovered_refs[:6])}.",
            remediation="Integrate missing key supplied references into the comparative taxonomy or document explicit exclusion scope.",
        )

    # Verify draft citations exist in supplied corpus when corpus is supplied
    unverified_cites: list[str] = []
    if lit_entries:
        lit_blob = " ".join(f"{e['cite_key']} {e['author']} {e['year']} {e['doi']} {e['title']}" for e in lit_entries).lower()
        for c in draft_cites:
            cl = c.lower()
            parts = [p for p in re.split(r"[\s,;.\[\]]+", cl) if len(p) >= 3]
            if cl not in lit_blob and not (parts and all(p in lit_blob for p in parts[:2])):
                unverified_cites.append(c)
        if unverified_cites:
            add_finding(
                code="UNVERIFIED_CITATION",
                severity="blocking",
                section="related_work",
                line=1,
                quote=", ".join(unverified_cites[:6]),
                message=f"Draft cites {len(unverified_cites)} source(s) absent from supplied bibliography/corpus: {', '.join(unverified_cites[:6])}.",
                remediation="Remove fabricated/unverified citations or attach exact verified bibliography records from supplied sources.",
            )

    if not lit_entries:
        gaps.append({"code": "NO_BIBLIOGRAPHY_CORPUS_SUPPLIED", "severity": "major", "detail": "No structured bibliography/literature files found; citation grounding could not be verified externally."})
    if not own_ev["files"]:
        gaps.append({"code": "NO_AUTHOR_EVIDENCE_FILES_SUPPLIED", "severity": "major", "detail": "No author Methods/Results/Figure/Table files supplied for 1:1 contribution verification."})

    prose_audit = wio.audit_academic_prose(draft_text)
    blocking_n = sum(1 for f in findings if f["severity"] == "blocking")

    taxonomy_payload = {
        "case_id": case_id,
        "task_id": task_id,
        "draft_file": draft_path.name,
        "four_beat_opening": four_beats,
        "four_beat_complete": all(four_beats.values()),
        "chronological_stitching_detected": chronological_stitching,
        "supplied_literature_count": len(lit_entries),
        "supplied_literature_conflict_detected": supplied_lit_conflict,
        "draft_acknowledges_conflict": draft_acknowledges_conflict,
        "taxonomy_by_method_route": taxonomy_groups,
        "unsubstantiated_gap_claims_count": unsubstantiated_gap_count,
        "contribution_alignment": contribution_alignment,
        "overclaimed_contributions_count": overclaimed_count,
        "citation_coverage": {
            "draft_citations_detected": draft_cites,
            "uncovered_supplied_references": uncovered_refs,
            "unverified_citations": unverified_cites,
            "coverage_ratio": round((len(lit_entries) - len(uncovered_refs)) / max(1, len(lit_entries)), 3) if lit_entries else None,
        },
        "prose_audit_summary": {
            "clean": prose_audit["clean"],
            "high_count": prose_audit["high_count"],
            "warning_count": prose_audit["warning_count"],
        },
        "blocking_count": blocking_n,
        "findings_count": len(findings),
        "findings": findings,
    }

    return {
        "taxonomy": taxonomy_payload,
        "invariants": wio.extract_invariants(draft_text),
        "evidence_map": evidence_map,
        "gaps": gaps,
        "own_evidence_files": own_ev["files"],
        "lit_sources": sorted({e["source_file"].split(":")[0] for e in lit_entries}),
    }


def render_alignment_md(tax: dict[str, Any]) -> str:
    lines = [
        f"# Introduction & Related Work Taxonomy and Contribution Alignment ({tax['case_id']})",
        "",
        f"- **Draft Audited**: `{tax['draft_file']}`",
        f"- **Press-Release Four-Beat Opening**: Problem=`{tax['four_beat_opening']['problem']}`, Gap=`{tax['four_beat_opening']['gap']}`, Approach=`{tax['four_beat_opening']['approach']}`, Key Finding=`{tax['four_beat_opening']['key_finding']}`",
        f"- **Chronological Laundry List Detected**: `{tax['chronological_stitching_detected']}`",
        f"- **Unsubstantiated Gap Claims**: `{tax['unsubstantiated_gap_claims_count']}` | **Overclaimed Contributions**: `{tax['overclaimed_contributions_count']}`",
        f"- **Total Findings**: `{tax['findings_count']}` (Blocking: `{tax['blocking_count']}`)",
        "",
        "## 1. Related Work Comparative Taxonomy Matrix",
        "",
        "| Comparison Axis (Method Route) | Cite Key | Year | Scope / Condition | Outcome Direction | Limitation | Cited in Draft? |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not tax["taxonomy_by_method_route"]:
        lines.append("| (none) | - | - | No structured literature entries supplied | - | - | - |")
    else:
        for route, items in tax["taxonomy_by_method_route"].items():
            for it in items:
                lines.append(
                    f"| {route} | {it['cite_key']} | {it['year'] or '-'} | {it['scope_condition'][:60]} | "
                    f"{it['direction']} | {it['limitation'][:60]} | {'Yes' if it['cited_in_draft'] else 'NO'} |"
                )

    lines.extend([
        "",
        "## 2. Contribution-to-Evidence 1:1 Alignment Matrix",
        "",
        "| ID | Line | Alignment Status | Token Coverage | Cited Fig/Table | Contribution Excerpt | Diagnostics |",
        "| --- | ---: | --- | ---: | --- | --- | --- |",
    ])
    if not tax["contribution_alignment"]:
        lines.append("| - | - | MISSING | 0.0 | - | No itemized contributions extracted | Add explicit verifiable contribution bullets |")
    else:
        for c in tax["contribution_alignment"]:
            fts = ", ".join(c["cited_fig_tables"]) if c["cited_fig_tables"] else "-"
            diag_s = "; ".join(c["reasons"]) if c["reasons"] else "Aligned with supplied evidence"
            lines.append(
                f"| {c['contribution_id']} | L{c['line']} | `{c['alignment_status']}` | {c['token_coverage']:.2f} | "
                f"{fts} | {c['text'].replace('|', '/')[:90]} | {diag_s.replace('|', '/')} |"
            )

    lines.extend([
        "",
        "## 3. Audit Findings & Required Revisions",
        "",
    ])
    if not tax["findings"]:
        lines.append("All automated four-beat, dimension-grouping, bounded-gap, citation-coverage, and 1:1 contribution checks passed.")
    else:
        for f in tax["findings"]:
            lines.extend([
                f"### [{f['severity'].upper()}] {f['finding_id']} — `{f['code']}` (`{f['section']}` L{f['line']})",
                f"- **Excerpt**: \"{f['quote']}\"",
                f"- **Issue**: {f['message']}",
                f"- **Remediation**: {f['remediation']}",
                "",
            ])
    return "\n".join(lines)


def _discover_inputs(input_dir: Path) -> tuple[Path, str]:
    case_id = "case-01"
    cj = input_dir / "case.json"
    if cj.is_file():
        try:
            case_id = str(json.loads(cj.read_text(encoding="utf-8")).get("case_id") or case_id)
        except Exception:
            pass
    cands = [
        p for p in sorted(input_dir.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in {".md", ".txt", ".tex", ".docx"}
        and p.name.lower() not in {"task.md", "readme.md"}
        and not any(k in p.name.lower() for k in ("bib", "ref", "lit"))
    ]
    if not cands:
        raise FileNotFoundError(f"No draft file found under {input_dir}")
    # Prefer file explicitly named intro / draft / manuscript
    cands.sort(key=lambda p: (0 if any(k in p.name.lower() for k in ("intro", "draft", "manuscript", "related")) else 1, -p.stat().st_size))
    return cands[0], case_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Introduction & Related Work four-beat, taxonomy, gap, and contribution auditor.")
    parser.add_argument("--draft", default="", help="Introduction/Related Work draft path (.md/.tex/.txt/.docx).")
    parser.add_argument("--bib", default="", help="Optional structured literature/bibliography file (.json/.csv/.bib/.md).")
    parser.add_argument("--corpus-dir", default="", help="Directory containing literature sources and/or author evidence files.")
    parser.add_argument("--evidence-dir", default="", help="Directory containing author Methods/Results/Figure/Table files.")
    parser.add_argument("--input-dir", default="", help="Auto-discover draft, literature, and evidence under /input.")
    parser.add_argument("--out-dir", required=True, help="Output directory for taxonomy JSON, alignment MD, and result.json.")
    parser.add_argument("--case-id", default="", help="Override case_id.")
    args = parser.parse_args(argv)

    if args.input_dir:
        in_dir = Path(args.input_dir)
        draft_p, det_cid = _discover_inputs(in_dir)
        case_id = args.case_id or det_cid
        corpus_p = Path(args.corpus_dir) if args.corpus_dir else in_dir
        ev_p = Path(args.evidence_dir) if args.evidence_dir else in_dir
    elif args.draft:
        draft_p = Path(args.draft)
        case_id = args.case_id or draft_p.stem
        corpus_p = Path(args.corpus_dir) if args.corpus_dir else None
        ev_p = Path(args.evidence_dir) if args.evidence_dir else corpus_p
    else:
        parser.error("Provide --draft or --input-dir.")

    bib_p = Path(args.bib) if args.bib else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    res = audit_intro_and_related_work(
        draft_p,
        bib_path=bib_p,
        corpus_dir=corpus_p,
        evidence_dir=ev_p,
        case_id=case_id,
    )
    tax = res["taxonomy"]
    align_md = render_alignment_md(tax)

    (out_dir / "related_work_taxonomy.json").write_text(json.dumps(tax, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "gap_contribution_alignment.md").write_text(align_md, encoding="utf-8")

    sources_list = [{"file": draft_p.name, "role": "draft_manuscript"}]
    for s in res["lit_sources"]:
        sources_list.append({"file": s, "role": "supplied_literature"})
    for s in res["own_evidence_files"]:
        sources_list.append({"file": s, "role": "author_methods_results_evidence"})

    result_payload = wio.build_writing_result(
        case_id=case_id,
        task_id="introduction-related-work-writing",
        deliverable={
            "type": "introduction_related_work_taxonomy_and_gap_alignment",
            "taxonomy_json": "related_work_taxonomy.json",
            "alignment_md": "gap_contribution_alignment.md",
            "four_beat_complete": tax["four_beat_complete"],
            "blocking_count": tax["blocking_count"],
        },
        preserved_facts={
            "draft_file": draft_p.name,
            "numbers_with_units_count": len(res["invariants"]["numbers_with_units"]),
            "dois": res["invariants"]["dois"],
            "figure_table_refs": res["invariants"]["figure_table_refs"],
            "draft_citations_count": len(tax["citation_coverage"]["draft_citations_detected"]),
        },
        evidence_map=res["evidence_map"],
        change_log=[
            {"finding_id": f["finding_id"], "code": f["code"], "line": f["line"], "status": "audit_flagged"}
            for f in tax["findings"]
        ],
        artifacts=["result.json", "related_work_taxonomy.json", "gap_contribution_alignment.md"],
        sources=sources_list,
        gaps=res["gaps"],
        limitations=[
            "Automated checks verify lexical/structural grounding of gaps, citations, and contributions against supplied files; deep domain novelty remains bounded strictly by the supplied literature corpus.",
        ],
        verification={
            "engine": "intro_related_work_auditor.py",
            "four_beat_complete": tax["four_beat_complete"],
            "chronological_stitching_detected": tax["chronological_stitching_detected"],
            "unsubstantiated_gap_claims_count": tax["unsubstantiated_gap_claims_count"],
            "overclaimed_contributions_count": tax["overclaimed_contributions_count"],
            "unverified_citations_count": len(tax["citation_coverage"]["unverified_citations"]),
            "blocking_count": tax["blocking_count"],
        },
    )
    (out_dir / "result.json").write_text(json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "four_beat_complete": tax["four_beat_complete"],
        "chronological_stitching_detected": tax["chronological_stitching_detected"],
        "unsubstantiated_gap_claims": tax["unsubstantiated_gap_claims_count"],
        "overclaimed_contributions": tax["overclaimed_contributions_count"],
        "blocking_count": tax["blocking_count"],
        "findings_count": tax["findings_count"],
        "artifacts": ["result.json", "related_work_taxonomy.json", "gap_contribution_alignment.md"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
