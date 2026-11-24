#!/usr/bin/env python3
"""Evidence-driven scientific writing guard: atomic claim decomposer, multi-source
verbatim excerpt locator, strict 5-state verdict engine, conflict-synthesis auditor,
and structured 5-field result.json emitter.

Zero third-party dependencies (Python 3.12+ stdlib). Integrates `writing_io_and_invariants.py`.
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

from writing_io_and_invariants import (  # noqa: E402
    audit_academic_prose,
    build_writing_result,
    extract_invariants,
    read_document_text,
)

# ---------------------------------------------------------------------------
# 1. Regex Patterns & Taxonomies
# ---------------------------------------------------------------------------

ABBREV_PROT = r"\b(?:Fig|Figs|Eq|Eqs|Ref|Refs|Tab|Tbl|No|Vol|Dr|Prof|vs|etc|al|e\.g|i\.e)\."
RE_CITE_BRACKET = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
RE_CITE_PAREN = re.compile(
    r"\(([A-Z][A-Za-z\-']+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-']+|&\s+[A-Z][A-Za-z\-']+))?,?\s*(?:19|20)\d{2}[a-z]?"
    r"(?:;\s*[A-Z][A-Za-z\-']+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-']+|&\s+[A-Z][A-Za-z\-']+))?,?\s*(?:19|20)\d{2}[a-z]?)*)\)"
)
RE_CITE_LATEX = re.compile(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
RE_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)

RE_CAUSAL = re.compile(
    r"\b(?:proves?|proved|proven|causes?|caused|causing|drives?|drove|determines?|determined|"
    r"directly\s+triggers?|establishes?\s+(?:a\s+)?causal|definitively\s+shows?|universally\s+causes?|"
    r"induces?\s+directly|is\s+caused\s+by)\b|证明了因果|直接导致|引发了|决定性地证明|普遍导致",
    re.I,
)
RE_CONSENSUS = re.compile(
    r"\b(?:universally|consistently\s+across\s+all|well-?established\s+consensus|all\s+studies\s+agree|"
    r"unanimously|in\s+all\s+settings|always\s+improves?|conclusively\s+established)\b|"
    r"学界公认|一致认为|所有研究均表明|普遍适用|已成定论",
    re.I,
)
RE_CORRELATIONAL = re.compile(
    r"\b(?:may\s+be\s+associated\s+with|associated\s+with|correlat(?:es?|ed|ion)\s+with|linked\s+to|"
    r"co-?occurs?\s+with|observational|suggests?|may|might|could|appears?\s+to|trended\s+toward)\b|"
    r"相关|伴随|可能|提示|倾向于|观察性",
    re.I,
)
RE_CONDITIONAL = re.compile(
    r"\b(?:under\s+[a-z0-9\-\s]{3,40}|only\s+(?:in|under|when|at)\s+[a-z0-9\-\s]{3,40}|"
    r"in\s+(?:acidic|alkaline|high-?temperature|low-?temperature|hypoxic|normoxic|in\s+vitro|in\s+vivo|"
    r"pediatric|elderly|early-?stage|late-?stage|high-?dose|low-?dose|preclinical)[a-z0-9\-\s]{0,25}|"
    r"dependent\s+on|contingent\s+upon|within\s+the\s+tested|whereas|in\s+contrast\s+to|unlike|"
    r"divergent|conflicting|inconsistent)\b|"
    r"(?:仅在[\u4e00-\u9fffA-Za-z0-9]{2,18}条件下|取决于|适用条件|相比之下|与之相反|存在分歧|并不一致)",
    re.I,
)

RE_POSITIVE_POLARITY = re.compile(
    r"\b(?:increas(?:es?|ed|ing)|improv(?:es?|ed|ing)|enhanc(?:es?|ed|ing)|elevat(?:es?|ed)|"
    r"higher|accelerat(?:es?|ed)|promot(?:es?|ed)|boost(?:s|ed)|outperform(?:s|ed)|benefit(?:s|ed)?)\b|"
    r"提高|增加|增强|上升|改善|促进|优于|正向",
    re.I,
)
RE_NEGATIVE_OR_NULL_POLARITY = re.compile(
    r"\b(?:no\s+significant\s+(?:effect|difference|improvement|benefit|change)|failed\s+to\s+(?:improve|increase|reduce|show)|"
    r"did\s+not\s+(?:improve|increase|differ|change)|decreas(?:es?|ed)|reduc(?:es?|ed)|impair(?:s|ed)|"
    r"worsen(?:s|ed)|inhibit(?:s|ed)|suppress(?:es|ed)|lower|null\s+effect|no\s+benefit|adverse)\b|"
    r"无显著(?:差异|改善|影响|效果)|未能(?:提高|改善|降低)|降低|下降|抑制|恶化|未见明显变化",
    re.I,
)

RE_DESIGN_RCT = re.compile(
    r"\b(?:randomized|randomised|RCT|double-?blind|placebo-?controlled|knockout|CRISPR|perturbation|"
    r"controlled\s+intervention|causal\s+experiment|isogenic\s+rescue)\b|随机对照|双盲|敲除|干预实验",
    re.I,
)
RE_DESIGN_OBSERVATIONAL = re.compile(
    r"\b(?:observational|cross-?sectional|retrospective|correlational|correlation\s+only|"
    r"cohort\s+association|associative|cannot\s+establish\s+causality|no\s+causal\s+inference)\b|"
    r"观察性|横断面|回顾性|相关性研究|仅支持相关|无法推断因果",
    re.I,
)
RE_DESIGN_META = re.compile(r"\b(?:meta-?analysis|systematic\s+review)\b|荟萃分析|系统综述", re.I)

RE_TYPE_PREPRINT = re.compile(r"\b(?:preprint|biorxiv|medrxiv|arxiv|chemrxiv|ssrn|not\s+peer-?reviewed)\b|预印本", re.I)
RE_TYPE_GUIDELINE = re.compile(r"\b(?:clinical\s+guideline|consensus\s+statement|practice\s+guideline|WHO\s+guideline)\b|指南|专家共识", re.I)
RE_TYPE_REVIEW = re.compile(r"\b(?:narrative\s+review|systematic\s+review|literature\s+review|review\s+article)\b|综述", re.I)
RE_ABSTRACT_ONLY = re.compile(r"\b(?:abstract\s+only|abstract-only|full\s*text\s+(?:not\s+available|unavailable|absent))\b|仅有摘要|无全文", re.I)

RE_CONFLICT_SYNTHESIS = re.compile(
    r"\b(?:divergen(?:t|ce)|conflict(?:ing|s)?|discrepan(?:cy|cies)|inconsisten(?:t|cy)|"
    r"depend(?:s|ing)?\s+on|boundary\s+condition|applicability\s+condition|whereas|in\s+contrast|"
    r"reconcil(?:e|ed|ing)|context-?dependent|heterogeneity)\b|"
    r"(?:分歧|差异|矛盾|适用条件|边界条件|取决于|相反|异质性|条件差异)",
    re.I,
)
RE_PAPER_BY_PAPER_START = re.compile(
    r"^(?:(?:Study|Paper|Reference|Author)\s*\d+|First,\s+[A-Z][a-z]+\s+et\s+al\.|Second,\s+[A-Z][a-z]+\s+et\s+al\.|"
    r"Third,\s+[A-Z][a-z]+\s+et\s+al\.|[A-Z][a-z]+\s+et\s+al\.\s*\(\d{4}\)\s+(?:reported|found|showed|studied)|"
    r"文献\s*\d+|第一篇|第二篇|第三篇)",
    re.I,
)

STOPWORDS = {
    "that", "this", "with", "from", "were", "have", "been", "also", "into", "their", "these",
    "those", "which", "where", "while", "when", "than", "then", "such", "more", "most", "some",
    "only", "both", "each", "other", "over", "under", "study", "studies", "paper", "result",
    "results", "data", "showed", "reported", "found", "using", "based", "analysis", "between",
    "within", "across", "however", "therefore", "although", "because", "through", "about",
}


def _keywords(text: str) -> set[str]:
    en = {w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower()) if w not in STOPWORDS}
    zh = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return en | zh


def _detect_polarity(text: str) -> str:
    has_neg = bool(RE_NEGATIVE_OR_NULL_POLARITY.search(text))
    has_pos = bool(RE_POSITIVE_POLARITY.search(text))
    # Explicit null/no-effect phrases override positive words inside "no significant increase"
    if re.search(r"\b(?:no\s+significant|failed\s+to|did\s+not|null\s+effect|no\s+benefit)\b|无显著|未能|未见明显", text, re.I):
        return "null_or_negative"
    if has_neg and not has_pos:
        return "null_or_negative"
    if has_pos and not has_neg:
        return "positive"
    if has_pos and has_neg:
        return "mixed_conditional"
    return "neutral"


def _extract_conditions(text: str) -> list[str]:
    return sorted({m.group(0).strip().lower() for m in RE_CONDITIONAL.finditer(text)})


RE_CITE_NARRATIVE = re.compile(
    r"\b([A-Z][A-Za-z\-']+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-']+|&\s+[A-Z][A-Za-z\-']+))?)\s*\(((?:19|20)\d{2}[a-z]?)\)"
)
RE_STEM_AY = re.compile(r"^([A-Za-z][A-Za-z\-']{1,30})[_\-\s]*((?:19|20)\d{2}[a-z]?)$")


def _extract_citations(text: str) -> list[str]:
    out: list[str] = []
    for m in RE_CITE_BRACKET.finditer(text):
        for part in m.group(1).split(","):
            part = part.strip()
            rng = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
            if rng:
                lo, hi = int(rng.group(1)), int(rng.group(2))
                if hi >= lo and hi - lo <= 20:
                    out.extend(f"[{k}]" for k in range(lo, hi + 1))
            elif part.isdigit():
                out.append(f"[{part}]")
    for m in RE_CITE_PAREN.finditer(text):
        for p in m.group(1).split(";"):
            ps = p.strip()
            if ps:
                out.append(ps)
    for m in RE_CITE_NARRATIVE.finditer(text):
        out.append(f"{m.group(1).strip()}, {m.group(2).strip()}")
    for m in RE_CITE_LATEX.finditer(text):
        for k in m.group(1).split(","):
            ks = k.strip()
            if ks:
                out.append(ks)
    for m in RE_DOI.finditer(text):
        out.append(m.group(1).rstrip(".,;)").lower())
    return list(dict.fromkeys(out))


def _parse_draft_references_bridge(draft_text: str) -> dict[str, dict[str, Any]]:
    """Parse draft's ## References / Bibliography section to bridge [n] -> DOI, author-year, and title keywords."""
    bridge: dict[str, dict[str, Any]] = {}
    in_refs = False
    cur_num: str | None = None
    cur_buf: list[str] = []

    def _flush() -> None:
        if not cur_num or not cur_buf:
            return
        entry_text = " ".join(cur_buf).strip()
        dois = [d.rstrip(".,;)").lower() for d in RE_DOI.findall(entry_text)]
        ay_keys: list[str] = []
        for m in RE_CITE_NARRATIVE.finditer(entry_text):
            first = m.group(1).split()[0].rstrip(",.").lower()
            yr = m.group(2).lower()
            ay_keys.extend([f"{first} {yr}", f"{first}{yr}", f"{m.group(1).strip().lower()}, {yr}"])
        if not ay_keys:
            m_lead = re.match(r"^([A-Z][A-Za-z\-']+)", entry_text)
            m_yr = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", entry_text)
            if m_lead and m_yr:
                first = m_lead.group(1).lower()
                yr = m_yr.group(1).lower()
                ay_keys.extend([f"{first} {yr}", f"{first}{yr}"])
        bridge[f"[{cur_num}]"] = {
            "raw": entry_text,
            "dois": list(dict.fromkeys(dois)),
            "author_years": list(dict.fromkeys(ay_keys)),
            "keywords": _keywords(entry_text),
        }

    for raw in draft_text.splitlines():
        st = raw.strip()
        hm = re.match(r"^(#{1,4})\s+(.+?)\s*$", st)
        if hm:
            heading = hm.group(2).strip()
            if in_refs:
                _flush()
                cur_num, cur_buf = None, []
            in_refs = bool(re.match(r"^(?:References|Bibliography|参考文献)\b", heading, re.I))
            continue
        if not in_refs or not st:
            continue
        m_item = re.match(r"^(?:\[(\d+)\]|(\d+)[.)])\s+(.+)$", st)
        if m_item:
            _flush()
            cur_num = m_item.group(1) or m_item.group(2)
            cur_buf = [m_item.group(3).strip()]
        elif cur_num:
            cur_buf.append(st)
    _flush()
    return bridge


# ---------------------------------------------------------------------------
# 2. Multi-Source Corpus Indexer (File/Page/Section/Line/Type/Design)
# ---------------------------------------------------------------------------

def _classify_source_type(text: str, filename: str, meta_scope: str = "") -> str:
    blob = f"{filename} {meta_scope} {text[:1200]}"
    if meta_scope == "abstract_only" or RE_ABSTRACT_ONLY.search(blob):
        return "abstract_only"
    if RE_TYPE_PREPRINT.search(blob):
        return "preprint"
    if RE_TYPE_GUIDELINE.search(blob):
        return "guideline"
    if RE_TYPE_REVIEW.search(blob):
        return "review"
    return "primary_research"


def _classify_study_design(text: str, meta_design: str = "") -> str:
    blob = f"{meta_design} {text}"
    if RE_DESIGN_OBSERVATIONAL.search(blob) and not RE_DESIGN_RCT.search(blob):
        return "observational_correlational"
    if RE_DESIGN_RCT.search(blob):
        return "rct_or_intervention"
    if RE_DESIGN_META.search(blob):
        return "meta_analysis"
    if re.search(r"\bin\s+vitro\b|\bcell\s+line\b|\bmouse\s+model\b|细胞实验|动物模型", blob, re.I):
        return "in_vitro_preclinical"
    return "unknown"


def _source_aliases(doc_id: str, filename: str, text: str, meta: dict[str, Any] | None = None) -> set[str]:
    stem_low = Path(filename).stem.lower()
    aliases: set[str] = {doc_id.lower(), stem_low}
    m_stem_ay = RE_STEM_AY.match(Path(filename).stem)
    if m_stem_ay:
        first, yr = m_stem_ay.group(1).lower(), m_stem_ay.group(2).lower()
        aliases.update({f"{first} {yr}", f"{first}, {yr}", f"{first} et al., {yr}", f"{first}{yr}"})
    if meta:
        for k in ("id", "cite_key", "key", "citation_label", "ref_num", "doi"):
            v = meta.get(k)
            if v:
                sv = str(v).strip().lower()
                aliases.add(sv)
                if sv.isdigit():
                    aliases.add(f"[{sv}]")
        if meta.get("author") and meta.get("year"):
            first = str(meta["author"]).split(",")[0].split()[0].strip().lower()
            yr = str(meta["year"]).strip()
            aliases.add(f"{first} et al., {yr}")
            aliases.add(f"{first}, {yr}")
            aliases.add(f"{first} {yr}")
    # Extract DOIs & bracket numbers from header lines
    for d in RE_DOI.findall(text[:1200]):
        aliases.add(d.rstrip(".,;)").lower())
    m_num = re.search(r"(?:ref|paper|source)[_\-\s]*(\d+)", filename, re.I)
    if m_num:
        aliases.add(f"[{m_num.group(1)}]")
        aliases.add(m_num.group(1))
    # Extract Author (Year) from header lines
    for m_ay in re.finditer(r"\b([A-Z][A-Za-z\-']+)\s+et\s+al\.,?\s*\(?((?:19|20)\d{2}[a-z]?)\)?", text[:800]):
        aliases.add(f"{m_ay.group(1).lower()} et al., {m_ay.group(2).lower()}")
        aliases.add(f"{m_ay.group(1).lower()} {m_ay.group(2).lower()}")
    for m_ay2 in re.finditer(r"\b([A-Z][A-Za-z\-']+)\s*\(((?:19|20)\d{2}[a-z]?)\)", text[:800]):
        aliases.add(f"{m_ay2.group(1).lower()}, {m_ay2.group(2).lower()}")
        aliases.add(f"{m_ay2.group(1).lower()} {m_ay2.group(2).lower()}")
    return {a for a in aliases if a}


def index_literature_corpus(corpus_dir: Path, draft_path: Path | None = None) -> list[dict[str, Any]]:
    """Scan corpus_dir for papers/excerpts/JSON references and build paragraph-indexed source objects."""
    sources: list[dict[str, Any]] = []
    if not corpus_dir.is_dir():
        return sources

    draft_resolved = draft_path.resolve() if draft_path else None
    skip_names = {"case.json", "task.md", "readme.md", "result.json", "public-manifest.json"}

    for fp in sorted(corpus_dir.rglob("*")):
        if not fp.is_file():
            continue
        if draft_resolved and fp.resolve() == draft_resolved:
            continue
        if fp.name.lower() in skip_names:
            continue
        suf = fp.suffix.lower()
        if suf not in {".md", ".txt", ".json", ".docx", ".pdf", ".tex"}:
            continue

        rel = fp.relative_to(corpus_dir).as_posix()

        # Structured JSON library of sources/excerpts
        if suf == ".json":
            try:
                data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else data.get("references", data.get("sources", data.get("papers", [])))
            if isinstance(items, list) and items and isinstance(items[0], dict):
                for idx, item in enumerate(items, start=1):
                    sid = str(item.get("id") or item.get("key") or f"{fp.stem}_{idx}")
                    body = str(item.get("full_text") or item.get("text") or item.get("excerpt") or item.get("abstract") or "")
                    scope = str(item.get("scope") or ("abstract_only" if not item.get("full_text") and item.get("abstract") else "full_text"))
                    stype = _classify_source_type(body, rel, scope)
                    sdesign = _classify_study_design(body, str(item.get("study_design") or ""))
                    chunks = [{
                        "section": str(item.get("section") or ("Abstract" if stype == "abstract_only" else "Main")),
                        "page": item.get("page"),
                        "line_start": 1,
                        "line_end": max(1, len(body.splitlines())),
                        "text": body,
                        "polarity": _detect_polarity(body),
                        "conditions": _extract_conditions(body),
                        "keywords": _keywords(body),
                    }]
                    sources.append({
                        "source_id": sid,
                        "file": rel,
                        "title": str(item.get("title") or sid),
                        "source_type": stype,
                        "study_design": sdesign,
                        "is_abstract_only": stype == "abstract_only",
                        "aliases": _source_aliases(sid, rel, body, item),
                        "chunks": chunks,
                        "overall_polarity": _detect_polarity(body),
                        "overall_conditions": _extract_conditions(body),
                        "full_text": body,
                    })
                continue

        doc = read_document_text(fp)
        if not doc["ok"] or not doc["text"].strip():
            continue
        text = doc["text"]
        stype = _classify_source_type(text, rel)
        sdesign = _classify_study_design(text)

        # Parse paragraphs with section, page, and line locators
        chunks: list[dict[str, Any]] = []
        section = "Document Header"
        page_num: int | None = None
        buf: list[tuple[int, str]] = []

        def _flush_buf() -> None:
            nonlocal buf
            if not buf:
                return
            p_text = " ".join(x[1] for x in buf).strip()
            if len(p_text) >= 15:
                chunks.append({
                    "section": section,
                    "page": page_num,
                    "line_start": buf[0][0],
                    "line_end": buf[-1][0],
                    "text": p_text,
                    "polarity": _detect_polarity(p_text),
                    "conditions": _extract_conditions(p_text),
                    "keywords": _keywords(p_text),
                })
            buf = []

        for lno, raw in enumerate(text.splitlines(), start=1):
            st = raw.strip()
            m_pg = re.match(r"^\[?Page\s+(\d+)\]?$", st, re.I)
            if m_pg:
                _flush_buf()
                page_num = int(m_pg.group(1))
                continue
            if not st:
                _flush_buf()
                continue
            hm = re.match(r"^(#{1,6})\s+(.+)$", st)
            if hm:
                _flush_buf()
                section = hm.group(2).strip()
                continue
            buf.append((lno, st))
        _flush_buf()

        sources.append({
            "source_id": fp.stem,
            "file": rel,
            "title": (text.splitlines()[0].lstrip("# ").strip()[:160] if text.splitlines() else fp.stem),
            "source_type": stype,
            "study_design": sdesign,
            "is_abstract_only": stype == "abstract_only",
            "aliases": _source_aliases(fp.stem, rel, text),
            "chunks": chunks,
            "overall_polarity": _detect_polarity(text),
            "overall_conditions": _extract_conditions(text),
            "full_text": text,
        })

    return sources


# ---------------------------------------------------------------------------
# 3. Atomic Claim Decomposer & Strict 5-State Verdict Engine
# ---------------------------------------------------------------------------

def decompose_atomic_claims(draft_text: str) -> list[dict[str, Any]]:
    """Decompose draft into atomic claim units C01, C02... with strength, polarity, citations, and conditions."""
    claims: list[dict[str, Any]] = []
    section = "Preamble"
    cid = 0
    in_refs = False

    for lno, raw in enumerate(draft_text.splitlines(), start=1):
        st = raw.strip()
        if not st or st.startswith("```") or (st.startswith("|") and st.endswith("|")):
            continue
        hm = re.match(r"^(#{1,6})\s+(.+)$", st)
        if hm:
            section = hm.group(2).strip()
            in_refs = bool(re.match(r"^(?:References|Bibliography|参考文献)\b", section, re.I))
            continue
        if in_refs:
            continue

        prot = re.sub(ABBREV_PROT, lambda m: m.group(0).replace(".", "<DOT>"), st)
        prot = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", prot)
        prot = re.sub(r"(10\.\d{4,9}/[^\s,;)]+)", lambda m: m.group(0).replace(".", "<DOT>"), prot)
        sents = re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])(?=[^\s”’\"')\]])", prot)

        for part in sents:
            sent = part.replace("<DOT>", ".").strip()
            if len(sent) < 12:
                continue
            cites = _extract_citations(sent)
            has_substantive_assertion = bool(
                cites
                or RE_CAUSAL.search(sent)
                or RE_CORRELATIONAL.search(sent)
                or RE_CONSENSUS.search(sent)
                or RE_POSITIVE_POLARITY.search(sent)
                or RE_NEGATIVE_OR_NULL_POLARITY.search(sent)
                or re.search(r"\d+(?:\.\d+)?%|\bp\s*[<=>]|n\s*=\s*\d+", sent, re.I)
            )
            if not has_substantive_assertion:
                continue

            cid += 1
            if RE_CAUSAL.search(sent):
                strength = "causal"
            elif RE_CONSENSUS.search(sent):
                strength = "consensus_broad"
            elif RE_CONDITIONAL.search(sent) and RE_CORRELATIONAL.search(sent):
                strength = "conditional_associative"
            elif RE_CONDITIONAL.search(sent):
                strength = "conditional_scoped"
            elif RE_CORRELATIONAL.search(sent):
                strength = "associative_correlational"
            else:
                strength = "descriptive"

            claims.append({
                "claim_id": f"C{cid:02d}",
                "section": section,
                "line": lno,
                "claim_text": sent,
                "claim_strength": strength,
                "polarity": _detect_polarity(sent),
                "conditions": _extract_conditions(sent),
                "citations": cites,
                "invariants": extract_invariants(sent),
            })
    return claims


def _match_citation_to_sources(
    cite: str,
    sources: list[dict[str, Any]],
    ref_bridge: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    cl = cite.strip().lower()
    cl_clean = re.sub(r"\s+", " ", cl.replace("(", "").replace(")", ""))
    matched: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(src: dict[str, Any]) -> None:
        if src["source_id"] not in seen_ids:
            seen_ids.add(src["source_id"])
            matched.append(src)

    # Also extract surname + year if author-year format
    m_ay = re.match(r"([a-z\-']+).*?((?:19|20)\d{2}[a-z]?)", cl_clean)
    ay_key = f"{m_ay.group(1)} {m_ay.group(2)}" if m_ay else ""
    ay_compact = f"{m_ay.group(1)}{m_ay.group(2)}" if m_ay else ""

    for src in sources:
        if (
            cl in src["aliases"]
            or cl_clean in src["aliases"]
            or (ay_key and ay_key in src["aliases"])
            or (ay_compact and ay_compact in src["aliases"])
        ):
            _add(src)
            continue
        # Fallback: exact author surname + year check in full text header
        if m_ay and m_ay.group(1) in src["full_text"][:1000].lower() and m_ay.group(2) in src["full_text"][:1000].lower():
            _add(src)

    # Bridge numeric citations like [1] via draft's ## References section (DOI / Author+Year / Title keywords)
    if not matched and ref_bridge and cl in ref_bridge:
        entry = ref_bridge[cl]
        for d in entry.get("dois", []):
            for src in sources:
                if d in src["aliases"] or d in src["full_text"][:2000].lower():
                    _add(src)
        if not matched:
            for ay in entry.get("author_years", []):
                for src in sources:
                    if ay in src["aliases"]:
                        _add(src)
                    else:
                        parts = ay.split()
                        if len(parts) == 2 and parts[0] in src["full_text"][:1200].lower() and parts[1] in src["full_text"][:1200].lower():
                            _add(src)
        if not matched and len(entry.get("keywords", set())) >= 3:
            ekw = entry["keywords"]
            for src in sources:
                header_kw = _keywords(src["full_text"][:1000])
                if len(ekw & header_kw) >= 3:
                    _add(src)
    return matched


def _best_chunk_for_claim(claim_kw: set[str], source: dict[str, Any]) -> tuple[dict[str, Any] | None, float, int]:
    best_chunk = None
    best_score = -1.0
    best_overlap = 0
    for ch in source["chunks"]:
        ov = len(claim_kw & ch["keywords"])
        ratio = ov / max(1, len(claim_kw))
        if ratio > best_score or (ratio == best_score and ov > best_overlap):
            best_score = ratio
            best_overlap = ov
            best_chunk = ch
    return best_chunk, round(max(0.0, best_score), 3), best_overlap


def verify_claims_against_corpus(
    claims: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    draft_text: str,
) -> dict[str, Any]:
    """Evaluate every claim under the strict 5-state rubric + check conflict synthesis quality."""
    evaluated: list[dict[str, Any]] = []
    verdict_counts = {
        "SUPPORTED": 0,
        "PARTIALLY_SUPPORTED": 0,
        "CONTRADICTED": 0,
        "UNVERIFIABLE_ABSENT": 0,
        "EXISTENCE_ONLY_NOT_SUPPORTING": 0,
    }

    ref_bridge = _parse_draft_references_bridge(draft_text)
    for cl in claims:
        cites = cl["citations"]
        ckw = _keywords(cl["claim_text"])

        if not cites:
            verdict = "UNVERIFIABLE_ABSENT"
            reason = "Substantive claim carries no inline citation anchor; cannot trace to supplied literature (not labeled fabricated)."
            evaluated.append({
                **cl,
                "verdict": verdict,
                "matched_sources": [],
                "verbatim_excerpts": [],
                "rationale": reason,
                "required_action": "Attach verifiable source citation from /input or mark [EVIDENCE_GAP].",
            })
            verdict_counts[verdict] += 1
            continue

        matched_sources: list[dict[str, Any]] = []
        missing_cites: list[str] = []
        for c in cites:
            hits = _match_citation_to_sources(c, sources, ref_bridge=ref_bridge)
            if hits:
                for h in hits:
                    if h["source_id"] not in {m["source_id"] for m in matched_sources}:
                        matched_sources.append(h)
            else:
                missing_cites.append(c)

        if not matched_sources:
            verdict = "UNVERIFIABLE_ABSENT"
            reason = f"Cited source(s) {missing_cites} are absent from /input corpus; marked unverifiable rather than fabricated."
            evaluated.append({
                **cl,
                "verdict": verdict,
                "matched_sources": [],
                "missing_citations": missing_cites,
                "verbatim_excerpts": [],
                "rationale": reason,
                "required_action": f"Supply full text for {missing_cites} or mark [EVIDENCE_GAP].",
            })
            verdict_counts[verdict] += 1
            continue

        # Locate best verbatim excerpt per matched source
        excerpts: list[dict[str, Any]] = []
        max_overlap_ratio = 0.0
        max_overlap_cnt = 0
        all_abstract_only = True
        any_rct = False
        any_obs_only = False
        any_preprint = False
        contradiction_hits: list[str] = []
        condition_gaps: list[str] = []

        for src in matched_sources:
            if not src["is_abstract_only"]:
                all_abstract_only = False
            if src["study_design"] == "rct_or_intervention":
                any_rct = True
            if src["study_design"] == "observational_correlational":
                any_obs_only = True
            if src["source_type"] == "preprint":
                any_preprint = True

            ch, ratio, ov = _best_chunk_for_claim(ckw, src)
            if ratio > max_overlap_ratio:
                max_overlap_ratio = ratio
            if ov > max_overlap_cnt:
                max_overlap_cnt = ov

            loc_str = f"{src['file']}"
            if ch:
                if ch["page"] is not None:
                    loc_str += f"#page={ch['page']}"
                loc_str += f":{ch['section']}:L{ch['line_start']}-{ch['line_end']}"
                excerpts.append({
                    "source_id": src["source_id"],
                    "source_type": src["source_type"],
                    "study_design": src["study_design"],
                    "locator": loc_str,
                    "overlap_ratio": ratio,
                    "overlap_terms": ov,
                    "excerpt_polarity": ch["polarity"],
                    "verbatim_excerpt": ch["text"][:320],
                })
                # Polarity contradiction check (only when claim and excerpt actually share topic >=2 terms)
                if ov >= 2:
                    if cl["polarity"] == "positive" and ch["polarity"] == "null_or_negative":
                        contradiction_hits.append(f"{src['source_id']} ({loc_str}) reports null/negative outcome while claim asserts positive effect")
                    elif cl["polarity"] == "null_or_negative" and ch["polarity"] == "positive":
                        contradiction_hits.append(f"{src['source_id']} ({loc_str}) reports positive outcome while claim asserts null/negative effect")
                # Applicability condition scope check
                if ch["conditions"] and not cl["conditions"] and cl["claim_strength"] in ("causal", "consensus_broad", "descriptive"):
                    condition_gaps.append(f"{src['source_id']} is bounded by condition(s) {ch['conditions'][:3]}, omitted in claim")

        # Determine strict 5-state verdict
        issues: list[str] = []
        if all_abstract_only:
            verdict = "UNVERIFIABLE_ABSENT"
            issues.append("Only abstract/stub supplied without full text; cannot verify full methodological support (not labeled fabricated).")
        elif max_overlap_cnt < 2 and max_overlap_ratio < 0.15:
            verdict = "EXISTENCE_ONLY_NOT_SUPPORTING"
            issues.append(f"Cited source(s) exist in corpus, but best lexical/topical alignment is too weak (overlap={max_overlap_cnt}, ratio={max_overlap_ratio}) to support this specific claim.")
        elif contradiction_hits and len(matched_sources) == 1:
            verdict = "CONTRADICTED"
            issues.extend(contradiction_hits)
        elif contradiction_hits and not RE_CONFLICT_SYNTHESIS.search(cl["claim_text"]):
            verdict = "CONTRADICTED"
            issues.extend(contradiction_hits)
            issues.append("Claim cites conflicting evidence without explicitly stating the divergence or boundary conditions.")
        else:
            # Check for PARTIALLY_SUPPORTED causes: causal overclaim, consensus overclaim, missing conditions, missing partial cites, preprint-only consensus
            partial_reasons: list[str] = []
            if cl["claim_strength"] == "causal" and any_obs_only and not any_rct:
                partial_reasons.append("Causal overclaim: cited source(s) are observational/correlational only; downgrade to associative/hedged phrasing.")
            if cl["claim_strength"] == "consensus_broad" and (len(matched_sources) == 1 or any_preprint):
                partial_reasons.append("Consensus overclaim: single or preprint source cannot support universal/field-consensus wording.")
            if condition_gaps:
                partial_reasons.extend(condition_gaps)
            if missing_cites:
                partial_reasons.append(f"Secondary cited source(s) absent from corpus: {missing_cites}.")

            if partial_reasons:
                verdict = "PARTIALLY_SUPPORTED"
                issues.extend(partial_reasons)
            else:
                verdict = "SUPPORTED"
                issues.append("Claim topic, epistemic strength, polarity, and scope align with supplied verbatim source excerpt(s).")

        verdict_counts[verdict] += 1
        evaluated.append({
            **cl,
            "verdict": verdict,
            "matched_sources": [
                {"source_id": s["source_id"], "file": s["file"], "source_type": s["source_type"], "study_design": s["study_design"]}
                for s in matched_sources
            ],
            "missing_citations": missing_cites,
            "verbatim_excerpts": excerpts,
            "rationale": " ".join(issues),
            "required_action": "None" if verdict == "SUPPORTED" else f"Revise claim to resolve {verdict}: {' '.join(issues)}",
        })

    # Corpus-level conflict & synthesis audit
    pos_sources = [s["source_id"] for s in sources if s["overall_polarity"] == "positive"]
    neg_sources = [s["source_id"] for s in sources if s["overall_polarity"] == "null_or_negative"]
    obs_sources = [s["source_id"] for s in sources if s["study_design"] == "observational_correlational"]
    corpus_has_conflict = bool(pos_sources and neg_sources)

    draft_acknowledges_conflict = bool(RE_CONFLICT_SYNTHESIS.search(draft_text))
    draft_explains_conditions = bool(RE_CONDITIONAL.search(draft_text))

    # Detect mechanical paper-by-paper recitation (high proportion of sentences opening with "Study 1 / Author et al. reported..." without synthesis)
    non_empty_paras = [ln.strip() for ln in draft_text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    recitation_hits = sum(1 for p in non_empty_paras if RE_PAPER_BY_PAPER_START.match(p))
    paper_by_paper_warning = recitation_hits >= 2 and not draft_acknowledges_conflict

    conflict_audit = {
        "corpus_has_divergent_evidence": corpus_has_conflict,
        "positive_polarity_sources": pos_sources,
        "null_or_negative_polarity_sources": neg_sources,
        "observational_only_sources": obs_sources,
        "draft_acknowledges_divergence": draft_acknowledges_conflict,
        "draft_explains_applicability_conditions": draft_explains_conditions,
        "paper_by_paper_recitation_warning": paper_by_paper_warning,
        "conflict_synthesis_passed": (not corpus_has_conflict) or (draft_acknowledges_conflict and draft_explains_conditions and not paper_by_paper_warning),
    }

    prose_audit = audit_academic_prose(draft_text)

    passed = (
        len(evaluated) > 0
        and verdict_counts["CONTRADICTED"] == 0
        and verdict_counts["EXISTENCE_ONLY_NOT_SUPPORTING"] == 0
        and verdict_counts["PARTIALLY_SUPPORTED"] == 0
        and conflict_audit["conflict_synthesis_passed"]
    )

    return {
        "passed": passed,
        "summary": {
            "total_claims": len(evaluated),
            "verdict_counts": verdict_counts,
            "sources_indexed": len(sources),
            "conflict_synthesis_passed": conflict_audit["conflict_synthesis_passed"],
            "prose_high_issues": prose_audit["high_count"],
            "prose_warning_issues": prose_audit["warning_count"],
        },
        "conflict_synthesis_audit": conflict_audit,
        "prose_audit": prose_audit,
        "claims": evaluated,
    }


def render_evidence_matrix_md(audit: dict[str, Any]) -> str:
    s = audit["summary"]
    vc = s["verdict_counts"]
    ca = audit["conflict_synthesis_audit"]
    lines = [
        "# Literature Evidence Matrix & Synthesis Audit",
        "",
        f"- **Gate Passed**: `{audit['passed']}` | **Total Claims**: `{s['total_claims']}` | **Sources Indexed**: `{s['sources_indexed']}`",
        f"- **5-State Verdicts**: `SUPPORTED={vc['SUPPORTED']}`, `PARTIALLY_SUPPORTED={vc['PARTIALLY_SUPPORTED']}`, `CONTRADICTED={vc['CONTRADICTED']}`, `UNVERIFIABLE_ABSENT={vc['UNVERIFIABLE_ABSENT']}`, `EXISTENCE_ONLY_NOT_SUPPORTING={vc['EXISTENCE_ONLY_NOT_SUPPORTING']}`",
        f"- **Conflict & Condition Synthesis**: divergent_corpus=`{ca['corpus_has_divergent_evidence']}`, acknowledges_divergence=`{ca['draft_acknowledges_divergence']}`, explains_conditions=`{ca['draft_explains_applicability_conditions']}`, paper_by_paper_warning=`{ca['paper_by_paper_recitation_warning']}`",
        "",
        "## Claim-by-Claim Evidence Table",
        "",
        "| Claim ID | Loc | Strength | Citations | Verdict | Source Type / Design | Locator | Verbatim Excerpt | Rationale |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in audit["claims"]:
        cites = ", ".join(c["citations"]) if c["citations"] else "NONE"
        if c["verbatim_excerpts"]:
            ex0 = c["verbatim_excerpts"][0]
            st_des = f"{ex0['source_type']} / {ex0['study_design']}"
            loc = ex0["locator"]
            quote = ex0["verbatim_excerpt"].replace("|", "/").replace("\n", " ")[:130]
        else:
            st_des = "N/A"
            loc = "ABSENT"
            quote = "—"
        rat = c["rationale"].replace("|", "/").replace("\n", " ")[:140]
        lines.append(
            f"| {c['claim_id']} | L{c['line']} ({c['section']}) | `{c['claim_strength']}` | {cites} | `{c['verdict']}` | {st_des} | `{loc}` | {quote} | {rat} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence-driven scientific writing guard (5-state verdict + conflict synthesis + structured 5-field contract).")
    parser.add_argument("--draft", required=True, help="Draft manuscript path (.md/.txt/.docx).")
    parser.add_argument("--corpus-dir", required=True, help="Directory of supplied literature full texts / abstracts / notes.")
    parser.add_argument("--out-dir", required=True, help="Output directory for claims_list.json, evidence_matrix.md, evidence_audit.json, result.json.")
    parser.add_argument("--case-id", default="CASE-UNSPECIFIED")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any claim fails or conflict synthesis fails.")
    args = parser.parse_args(argv)

    draft_path = Path(args.draft)
    doc = read_document_text(draft_path)
    if not doc["ok"]:
        print(json.dumps({"passed": False, "error": f"Failed to read draft: {doc['meta']}"}, ensure_ascii=False))
        return 2

    corpus_dir = Path(args.corpus_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = index_literature_corpus(corpus_dir, draft_path=draft_path)
    claims = decompose_atomic_claims(doc["text"])
    audit = verify_claims_against_corpus(claims, sources, doc["text"])

    # Deliverable 1: claims_list.json
    claims_list_path = out_dir / "claims_list.json"
    claims_list_path.write_text(json.dumps({"total_claims": len(claims), "claims": claims}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Deliverable 2: evidence_matrix.md
    matrix_md_path = out_dir / "evidence_matrix.md"
    matrix_md_path.write_text(render_evidence_matrix_md(audit), encoding="utf-8")

    # Deliverable 3: evidence_audit.json
    audit_json_path = out_dir / "evidence_audit.json"
    audit_json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Deliverable 4: structured 5-field result.json
    gaps: list[dict[str, Any]] = []
    for c in audit["claims"]:
        if c["verdict"] != "SUPPORTED":
            gaps.append({"claim_id": c["claim_id"], "verdict": c["verdict"], "rationale": c["rationale"]})
    if not audit["conflict_synthesis_audit"]["conflict_synthesis_passed"]:
        gaps.append({"code": "CONFLICT_SYNTHESIS_INCOMPLETE", "detail": audit["conflict_synthesis_audit"]})

    res = build_writing_result(
        case_id=args.case_id,
        task_id="evidence-driven-writing",
        deliverable={
            "draft_file": draft_path.name,
            "claims_list_json": claims_list_path.name,
            "evidence_matrix_md": matrix_md_path.name,
            "evidence_audit_json": audit_json_path.name,
        },
        preserved_facts={
            "draft_invariants": extract_invariants(doc["text"]),
            "sources_indexed_count": len(sources),
            "verdict_counts": audit["summary"]["verdict_counts"],
            "conflict_synthesis_passed": audit["conflict_synthesis_audit"]["conflict_synthesis_passed"],
        },
        evidence_map=[
            {
                "claim_id": c["claim_id"],
                "section": c["section"],
                "line": c["line"],
                "claim_strength": c["claim_strength"],
                "citations": c["citations"],
                "verdict": c["verdict"],
                "locators": [e["locator"] for e in c["verbatim_excerpts"]],
                "verbatim_excerpts": [e["verbatim_excerpt"] for e in c["verbatim_excerpts"]],
            }
            for c in audit["claims"]
        ],
        change_log=[
            {
                "claim_id": c["claim_id"],
                "verdict": c["verdict"],
                "action": c["required_action"],
            }
            for c in audit["claims"]
            if c["verdict"] != "SUPPORTED"
        ],
        artifacts=[draft_path.name, claims_list_path.name, matrix_md_path.name, audit_json_path.name, "result.json"],
        sources=[{"source_id": s["source_id"], "file": s["file"], "source_type": s["source_type"], "study_design": s["study_design"]} for s in sources],
        gaps=gaps,
        limitations=[
            "Offline verification inspects only files supplied under /input; absent or abstract-only sources are marked UNVERIFIABLE_ABSENT rather than fabricated.",
        ],
        verification={
            "evidence_guard_passed": audit["passed"],
            "summary": audit["summary"],
        },
        status="ok" if audit["passed"] and not gaps else "partial_with_gaps",
    )
    (out_dir / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"passed": audit["passed"], **audit["summary"]}, ensure_ascii=False))
    if args.strict and not audit["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
