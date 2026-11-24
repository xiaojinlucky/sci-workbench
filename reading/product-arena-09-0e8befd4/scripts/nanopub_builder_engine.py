#!/usr/bin/env python3
"""4-Graph Nanopublication Assertion Extractor, Auditor & Local Exporter (product-arena-09-0e8befd4).

Core capabilities (pure Python 3.12 stdlib, offline Linux Docker ready):
1. Multi-format source indexing: case.json paragraphs, Markdown/TXT/LaTeX, JATS/XML, JSON paragraph lists,
   and PDF via pdftotext/pypdf (with explicit blank/scanned page gap reporting; never fabricates PDF pages).
2. Dual-mode assertion workflow:
   - Audit mode (--claims or case.json["claims"]): strictly audits every user/agent-drafted atomic claim.
   - Auto-extraction mode (when no external claims file is supplied): splits compound sentences into
     atomic candidate claims, identifies controlled predicates (active + passive voice), extracts
     conditions/scope, polarity, quantities/units, and evidence tier (direct_empirical vs indirect_inference
     vs cited_secondary), then runs EVERY candidate through the exact same verification gate.
3. Strict quality & provenance gates:
   - Exact unique (source_file, paragraph_id) locator + verbatim quote check
   - Anti-overbroad-assertion gate (rejects whole-paragraph dumps / unsplit multi-clause blocks)
   - Subject-Predicate-Object directional order verification (active & passive voice aware)
   - Numeric value, unit, and comparator conservation (E/M/L/S/I fingerprint audit)
   - Polarity preservation (affirmed / negated / uncertain) & explicit condition requirement under scope cues
   - Cross-condition vs same-condition conflict detector across papers/paragraphs
4. 4-Graph Nanopublication separation & cryptographic verification (nanopub-java #127 / PR #128):
   - Separates Head, Assertion, Provenance, and PublicationInfo named graphs
   - Separates assertion_agent, provenance authors, pubinfo creator, and npx:signedBy signer
   - Emits nanopublications.json, nanopublications.jsonld, nanopublications.trig, evidence-report.md, result.json
   - Runs self-verification via nanopub_signature_verifier before finalizing.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from nanopub_signature_verifier import (
    PREDICATE_URIS,
    export_jsonld,
    export_trig,
    sign_nanopublication,
    verify_nanopublication,
)

# Controlled predicate regexes (active voice) + passive equivalents
RELATIONS_ACTIVE: dict[str, str] = {
    "catalyzes": r"cataly[sz](?:es?|ed|ing)|催化",
    "increases": r"increase[sd]?|elevate[sd]?|enhance[sd]?|boost(?:s|ed)?|raise[sd]?|提高|增加|升高|增强",
    "decreases": r"decrease[sd]?|reduce[sd]?|lower(?:s|ed)?|diminish(?:es|ed)?|降低|减少|下降",
    "inhibits": r"inhibit(?:s|ed)?|suppress(?:es|ed)?|block(?:s|ed)?|repressed|represses|抑制|阻断",
    "activates": r"activate[sd]?|induce[sd]?|promote[sd]?|upregulate[sd]?|激活|诱导|促进|上调",
    "regulates": r"regulate[sd]?|modulate[sd]?|control(?:s|led)?|调控|调节",
    "correlates_with": r"(?:positively\s+|negatively\s+)?correlate[sd]?\s+with|相关",
    "associated_with": r"(?:is|was|are|were|be)\s+(?:significantly\s+|strongly\s+|positively\s+|negatively\s+)?associated\s+with|与.+相关",
    "expresses": r"express(?:es|ed)?|高表达|表达",
    "reaches": r"reach(?:es|ed)?|attain(?:s|ed)?|达到",
    "achieves": r"achieve[sd]?|yield(?:s|ed)?|获得|实现",
    "outperforms": r"outperform(?:s|ed)?|exceed(?:s|ed)?|surpass(?:es|ed)?|优于|高于",
    "compares_with": r"compare[sd]?\s+with|相比|对比",
    "uses": r"use[sd]?|employ(?:s|ed)?|utilize[sd]?|adopt(?:s|ed)?|(?:was|were|is|are)\s+(?:performed|analyzed|calculated|estimated)\s+(?:using|via|with)|使用|采用",
    "supports": r"support(?:s|ed)?|confirm(?:s|ed)?|corroborate[sd]?|支持|证实",
    "contradicts": r"contradict(?:s|ed)?|refute[sd]?|disagree[sd]?\s+with|conflicts?\s+with|反驳|相矛盾",
    "causes_direct": r"directly\s+cause[sd]?|causally\s+drive[sd]?|直接导致",
    "does_not_differ": r"(?:does|did|do)\s+not\s+(?:significantly\s+)?differ\s+from|无显著差异",
}

RELATIONS_PASSIVE: dict[str, str] = {
    "inhibits": r"(?:is|was|are|were)\s+(?:significantly\s+|strongly\s+|markedly\s+)?(?:inhibited|suppressed|blocked)\s+by",
    "activates": r"(?:is|was|are|were)\s+(?:significantly\s+|strongly\s+)?(?:activated|induced|promoted|upregulated)\s+by",
    "increases": r"(?:is|was|are|were)\s+(?:significantly\s+|markedly\s+)?(?:increased|elevated|enhanced)\s+by",
    "decreases": r"(?:is|was|are|were)\s+(?:significantly\s+|markedly\s+)?(?:decreased|reduced|lowered)\s+by",
    "catalyzes": r"(?:is|was|are|were)\s+cataly[sz]ed\s+by",
    "regulates": r"(?:is|was|are|were)\s+(?:positively\s+|negatively\s+)?(?:regulated|modulated)\s+by",
    "supports": r"(?:is|was|are|were)\s+(?:supported|confirmed)\s+by",
    "contradicts": r"(?:is|was|are|were)\s+(?:contradicted|refuted)\s+by",
}

POLARITIES = {"affirmed", "negated", "uncertain"}
EVIDENCE_TYPES = {"direct_empirical", "indirect_inference", "cited_secondary"}

NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|neither|nor|lack(?:s|ed)?|fail(?:s|ed)?\s+to|absent|did\s+not|does\s+not|cannot)\b"
    r"|未观察到|未见|没有|未能|不显著|无显著|并非",
    re.I,
)
HEDGE_RE = re.compile(
    r"\b(?:may|might|could|suggest(?:s|ed)?|indicat(?:es|ed)\s+that|possibly|probably|potentially|appear(?:s|ed)\s+to|likely|uncertain|hypothesi[sz]e[sd]?)\b"
    r"|可能|提示|推测|尚未|不确定|似乎",
    re.I,
)
CITED_SECONDARY_RE = re.compile(
    r"\b(?:previous(?:ly)?\s+stud(?:y|ies)|prior\s+work|et\s+al\.?(?:,?\s*\(?(?:19|20)\d\d)?|reported\s+by|according\s+to|"
    r"in\s+a\s+study\s+conducted\s+in|in\s+line\s+with|similar\s+to\s+findings\s+(?:in|from)|cited\s+(?:study|literature|report))\b"
    r"|\[\d+(?:\s*[-,]\s*\d+)*\]|前人研究|既往报道",
    re.I,
)
INDIRECT_INFERENCE_RE = re.compile(
    r"\b(?:suggest(?:s|ed)?|impli(?:es|ed)|inferred|probably|likely\s+due\s+to|consistent\s+with\s+a\s+model|we\s+speculate|may\s+explain)\b"
    r"|推测|间接提示|可能归因于",
    re.I,
)
QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(?P<comparator>[<>≤≥=])?\s*"
    r"(?P<value>[−-]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:±\s*\d+(?:\.\d+)?))?)"
    r"(?:\s*(?P<unit>%|°C|×|(?:mA/cm\^?2|kJ/mol|mol/L|mg/L|µg/mL|ug/mL|mg/dL|g/dL|µM|uM|mM|nM|mV|kPa|MPa|Pa|eV|ppm|ppb|fold|times|cm|mm|nm|µm|um|kg|mg|µg|ug|ms|min|hr|K|V|mA|A|M|m|g|h|s|x)\b))?"
)
UNRESOLVED_SUBJECT_RE = re.compile(
    r"^(?:it|its|they|them|their|he|she|we|us|our|this|that|these|those|"
    r"(?:this|the|our)\s+(?:present\s+)?(?:study|paper|work|article|report|analysis|author|authors|model|agent|method|system|tool|framework|approach|algorithm|result|results|finding|findings|data|dataset)|"
    r"each\s+(?:tool|agent|model|method|study|paper)|本研究|该研究|本文|该模型|该代理|它|它们|其)$",
    re.I,
)
PREDICATE_CORE_VERBS_RE = re.compile(
    r"\b(?:increase[sd]?|enhance[sd]?|elevate[sd]?|boost(?:s|ed)?|decrease[sd]?|reduce[sd]?|lower(?:s|ed)?|diminish(?:es|ed)?|"
    r"inhibit(?:s|ed)?|suppress(?:es|ed)?|block(?:s|ed)?|activate[sd]?|induce[sd]?|promote[sd]?|upregulate[sd]?|"
    r"regulate[sd]?|modulate[sd]?|outperform(?:s|ed)?|exceed(?:s|ed)?|surpass(?:es|ed)?|achieve[sd]?|"
    r"contradict(?:s|ed)?|refute[sd]?|support(?:s|ed)?|confirm(?:s|ed)?|cataly[sz]e[sd]?)\b",
    re.I,
)
SCOPE_RE = re.compile(
    r"\b(?:only\s+(?:when|under|at|in)|except\s+(?:when|under|for|in)|under\s+[A-Za-z0-9°.\-\s]{2,35}|"
    r"at\s+\d+(?:\.\d+)?\s*(?:°C|K|V|mV|pH|bar|atm|MPa|h|min)|in\s+(?:vitro|vivo|mice|rats|patients|acidic|alkaline|neutral)\b|"
    r"pH\s*[=:]?\s*\d+(?:\.\d+)?)\b|仅在[^，。；]{2,24}|在[^，。；]{2,20}条件下",
    re.I,
)
COMPOUND_CLAUSE_SPLIT_RE = re.compile(
    r"\s*(?:;|\bwhereas\b|\bwhile\b|\bhowever,\s*|\balthough\b|\band\s+(?:also|simultaneously|in\s+contrast)\b|；|而|但是|然而)\s*",
    re.I,
)
# Split ordinary "and" multi-predicate clauses (e.g., "X increases A and decreases B")
AND_MULTI_PRED_SPLIT_RE = re.compile(
    r",?\s+\band\b\s+(?=(?:[A-Za-z0-9\-]+\s+){0,3}(?:increase[sd]?|enhance[sd]?|elevate[sd]?|decrease[sd]?|reduce[sd]?|lower(?:s|ed)?|inhibit(?:s|ed)?|suppress(?:es|ed)?|activate[sd]?|induce[sd]?|promote[sd]?|outperform(?:s|ed)?|achieve[sd]?|support(?:s|ed)?|contradict(?:s|ed)?|helps?|ensures?)\b)",
    re.I,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff])|(?<=[。！？])")

OPPOSITE_PREDICATES = {
    ("increases", "decreases"),
    ("decreases", "increases"),
    ("activates", "inhibits"),
    ("inhibits", "activates"),
    ("supports", "contradicts"),
    ("contradicts", "supports"),
}

ADV_BRIDGE = r"(?:\s+(?:significantly|markedly|strongly|selectively|directly|partially|slightly|consistently|substantially|dramatically|rapidly|effectively|robustly|reliably|does\s+not|did\s+not|may|might|could|can|was\s+found\s+to|were\s+found\s+to|was\s+shown\s+to|is\s+shown\s+to)){0,4}"


def _norm_ws(text: str) -> str:
    return " ".join((text or "").split())


def verify_exact_quote(quote: str, source_text: str) -> bool:
    """Case-sensitive verbatim substring check (only normalizes whitespace/linebreaks; blocks mM/mm or Co/CO tampering)."""
    q = _norm_ws(quote)
    s = _norm_ws(source_text)
    return len(q) >= 6 and q in s


def _extract_quantity_records(text: str) -> list[dict[str, Any]]:
    """Extract structured quantity records bound to local entity context, unit, comparator, and char_span."""
    records: list[dict[str, Any]] = []
    if not text:
        return records
    for m in QUANTITY_RE.finditer(text):
        raw_val = _norm_ws(m.group("value") or "").replace("−", "-")
        comp = _norm_ws(m.group("comparator") or "")
        unit = _norm_ws(m.group("unit") or "")
        # Ignore bare years like 2024 or 1999 in citations when unitless
        if not unit and not comp and re.fullmatch(r"(?:19|20)\d{2}", raw_val):
            continue
        start = m.start()
        end = m.end()
        if start > 0 and end < len(text) and text[start - 1] == "[" and text[end] == "]":
            continue
        prefix_ctx = text[max(0, start - 20) : start]
        if re.search(r"\b(?:Figure|Fig\.?|Table|Supplementary\s+Figure|Section|Eq\.?|Ref\.?)\s*$", prefix_ctx, re.I):
            continue
        # Extract local entity context tokens around the quantity (for cross-entity swap detection)
        win_left = text[max(0, start - 36) : start]
        win_right = text[end : min(len(text), end + 36)]
        # Pull nearest content word anchor (metric or entity name)
        ent_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-+]{2,}", win_left + " " + win_right)
        stop = {"and", "for", "with", "the", "that", "this", "from", "were", "was", "are", "than", "more", "less", "over", "under", "about", "around", "across", "compared", "relative"}
        entity_ctx = [t.lower() for t in ent_tokens if t.lower() not in stop][:4]
        canonical = f"{comp}{raw_val}{(' ' + unit) if unit else ''}".strip()
        records.append({
            "entity_context": entity_ctx,
            "comparator": comp or "=",
            "value": raw_val,
            "unit": unit or None,
            "canonical": canonical,
            "char_span": [start, end],
        })
    return records


def _extract_quantities(text: str) -> set[str]:
    return {r["canonical"] for r in _extract_quantity_records(text)}


def _has_cross_entity_quantity_swap(quote_recs: list[dict[str, Any]], stmt_recs: list[dict[str, Any]]) -> bool:
    """Detect if two quantities exist in both texts but had their local entity bindings swapped."""
    if len(quote_recs) < 2 or len(stmt_recs) < 2:
        return False
    # Map canonical value -> set of entity context tokens in quote vs statement
    q_by_val: dict[str, set[str]] = {}
    for r in quote_recs:
        q_by_val.setdefault(r["canonical"], set()).update(r["entity_context"])
    s_by_val: dict[str, set[str]] = {}
    for r in stmt_recs:
        s_by_val.setdefault(r["canonical"], set()).update(r["entity_context"])
    # Check if two distinct quantities exist whose contexts overlap better when swapped than aligned
    common_vals = [v for v in q_by_val if v in s_by_val and q_by_val[v] and s_by_val[v]]
    for i in range(len(common_vals)):
        for j in range(i + 1, len(common_vals)):
            v1, v2 = common_vals[i], common_vals[j]
            aligned = len(q_by_val[v1] & s_by_val[v1]) + len(q_by_val[v2] & s_by_val[v2])
            swapped = len(q_by_val[v1] & s_by_val[v2]) + len(q_by_val[v2] & s_by_val[v1])
            if swapped > aligned and aligned == 0:
                return True
    return False


def _has_unsplit_and_multi_predicate(statement: str) -> bool:
    """Detect ordinary 'and' compound statements containing >=2 distinct independent predicate clauses."""
    parts = [p.strip() for p in AND_MULTI_PRED_SPLIT_RE.split(statement or "") if p.strip()]
    if len(parts) < 2:
        return False
    pred_hits = sum(1 for p in parts if PREDICATE_CORE_VERBS_RE.search(p))
    return pred_hits >= 2


def _strip_xml_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _load_pdf_paragraphs(pdf_path: Path, rel: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paras: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    pages: list[tuple[int, str]] = []
    pdftotext = shutil.which("pdftotext")
    try:
        if pdftotext:
            # Use default reading-order extraction (NEVER -layout, which splices two-column PDFs horizontally)
            proc = subprocess.run([pdftotext, str(pdf_path), "-"], capture_output=True, timeout=30, check=True)
            raw = proc.stdout.decode("utf-8", errors="replace").removesuffix("\f")
            pages = [(idx, chunk) for idx, chunk in enumerate(raw.split("\f"), start=1)]
        else:
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(str(pdf_path))
            pages = [(idx, p.extract_text() or "") for idx, p in enumerate(reader.pages, start=1)]
    except Exception as exc:
        gaps.append({
            "code": "PDF_UNREADABLE_OR_NO_TEXT_LAYER",
            "source_file": rel,
            "message": f"Failed to extract text from {rel} ({type(exc).__name__}); manual OCR or inspection required.",
        })
        return paras, gaps

    blank_pages = [pno for pno, txt in pages if not txt.strip()]
    if blank_pages:
        gaps.append({
            "code": "PDF_SCANNED_OR_EMPTY_PAGES",
            "source_file": rel,
            "pages": blank_pages,
            "message": f"{rel} has pages without extractable text: {blank_pages}.",
        })
    for pno, txt in pages:
        for bno, block in enumerate(re.split(r"\n\s*\n", txt), start=1):
            rejoined = re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", block)
            cleaned = _norm_ws(rejoined)
            if cleaned and len(cleaned) >= 12:
                paras.append({
                    "source_file": rel,
                    "paragraph_id": f"{pdf_path.stem}:p{pno}-b{bno}",
                    "page": pno,
                    "text": cleaned,
                })
    return paras, gaps


def collect_source_paragraphs(input_dir: Path, case_data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Collect paragraphs and trustworthy per-source metadata without inventing missing fields."""
    paras: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    source_meta: dict[str, dict[str, Any]] = {}

    if isinstance(case_data.get("source_metadata"), dict):
        for k, v in case_data["source_metadata"].items():
            if isinstance(v, dict):
                source_meta[str(k)] = dict(v)

    # Top-level case.json paragraphs
    for idx, item in enumerate(case_data.get("paragraphs") or [], start=1):
        if isinstance(item, dict):
            pid = str(item.get("paragraph_id") or item.get("id") or f"P{idx:03d}")
            sfile = str(item.get("source_file") or "case.json")
            txt = _norm_ws(str(item.get("text") or item.get("content") or ""))
            if txt:
                rec: dict[str, Any] = {"source_file": sfile, "paragraph_id": pid, "text": txt}
                if "page" in item and isinstance(item["page"], int):
                    rec["page"] = item["page"]
                paras.append(rec)
        elif isinstance(item, str) and item.strip():
            paras.append({"source_file": "case.json", "paragraph_id": f"P{idx:03d}", "text": _norm_ws(item)})

    ignore_names = {"case.json", "task.md", "readme.md", "instructions.md", "claims.json", "draft_claims.json"}
    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        rel = fp.relative_to(input_dir).as_posix()
        if fp.name.lower() in ignore_names or fp.is_symlink():
            continue
        ext = fp.suffix.lower()
        if ext == ".pdf":
            p_pdf, g_pdf = _load_pdf_paragraphs(fp, rel)
            paras.extend(p_pdf)
            gaps.extend(g_pdf)
        elif ext in {".md", ".txt", ".tex"}:
            raw = fp.read_text(encoding="utf-8", errors="replace")
            for bno, block in enumerate(re.split(r"\n\s*\n", raw), start=1):
                cleaned = _norm_ws(block)
                if cleaned and not cleaned.startswith("#"):
                    paras.append({"source_file": rel, "paragraph_id": f"{fp.stem}:b{bno}", "text": cleaned})
        elif ext == ".xml":
            try:
                root = ET.fromstring(fp.read_text(encoding="utf-8", errors="replace"))
                p_idx = 0
                for elem in root.iter():
                    if _strip_xml_ns(elem.tag).lower() == "p":
                        txt = _norm_ws("".join(elem.itertext()))
                        if len(txt) >= 15:
                            p_idx += 1
                            pid = elem.attrib.get("id") or f"{fp.stem}:p{p_idx}"
                            paras.append({"source_file": rel, "paragraph_id": pid, "text": txt})
            except ET.ParseError as exc:
                gaps.append({"code": "XML_PARSE_ERROR", "source_file": rel, "message": str(exc)})
        elif ext == ".json":
            try:
                obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                gaps.append({"code": "JSON_PARSE_ERROR", "source_file": rel, "message": str(exc)})
                continue
            if isinstance(obj, dict):
                # Capture per-document metadata if present
                doc_meta = {k: obj[k] for k in ("doi", "authors", "year", "version", "license", "title") if obj.get(k) is not None}
                if doc_meta:
                    source_meta.setdefault(rel, {}).update(doc_meta)
                if isinstance(obj.get("paragraphs"), list):
                    for idx, p in enumerate(obj["paragraphs"], start=1):
                        if isinstance(p, dict):
                            pid = str(p.get("paragraph_id") or p.get("id") or f"{fp.stem}:P{idx:03d}")
                            txt = _norm_ws(str(p.get("text") or p.get("content") or ""))
                        else:
                            pid = f"{fp.stem}:P{idx:03d}"
                            txt = _norm_ws(str(p))
                        if txt:
                            paras.append({"source_file": rel, "paragraph_id": pid, "text": txt})
    return paras, gaps, source_meta


def _extract_condition_phrase(text: str) -> str | None:
    m = SCOPE_RE.search(text)
    if not m:
        return None
    return _norm_ws(m.group(0)).rstrip(",.;:")


def _classify_evidence_type(text: str) -> str:
    if CITED_SECONDARY_RE.search(text):
        return "cited_secondary"
    if INDIRECT_INFERENCE_RE.search(text):
        return "indirect_inference"
    return "direct_empirical"


def _classify_polarity(text: str) -> str:
    # Strip trailing non-restrictive relative clauses (", which do not ...") when checking main predicate negation
    main_clause = re.split(r",\s+which\b", text, maxsplit=1, flags=re.I)[0]
    if NEGATION_RE.search(main_clause):
        return "negated"
    if HEDGE_RE.search(main_clause):
        return "uncertain"
    return "affirmed"


def _check_relation_direction(text: str, subject: str, predicate: str, obj: str) -> tuple[bool, str]:
    """Verify explicit directional ordering in active or passive voice (prevents subject/object inversion)."""
    s_esc = re.escape(subject)
    o_esc = re.escape(obj)
    active_rule = RELATIONS_ACTIVE.get(predicate, "")
    if active_rule:
        # Subject ... (optional short modifier <= 45 chars) ... predicate ... (optional <= 45 chars) ... Object
        active_pat = rf"{s_esc}[^.;!?]{{0,45}}?{ADV_BRIDGE}\s*(?:{active_rule})[^.;!?]{{0,55}}?{o_esc}"
        if re.search(active_pat, text, re.I):
            return True, "active"
    passive_rule = RELATIONS_PASSIVE.get(predicate, "")
    if passive_rule:
        # Passive voice: Object ... is <verbed> by ... Subject
        passive_pat = rf"{o_esc}[^.;!?]{{0,45}}?(?:{passive_rule})[^.;!?]{{0,45}}?{s_esc}"
        if re.search(passive_pat, text, re.I):
            return True, "passive"
    return False, "none"


def extract_candidate_claims_from_paragraphs(
    paragraphs: list[dict[str, Any]],
    source_meta: dict[str, dict[str, Any]],
    max_claims: int = 30,
) -> list[dict[str, Any]]:
    """Split compound scientific prose into atomic candidate assertions for verification."""
    candidates: list[dict[str, Any]] = []
    seen_quotes: set[tuple[str, str, str]] = set()

    for para in paragraphs:
        sfile = para["source_file"]
        pid = para["paragraph_id"]
        meta = source_meta.get(sfile) or {}
        authors = meta.get("authors")
        default_agent = (
            f"paper_authors:{authors[0]}" if isinstance(authors, list) and authors
            else (f"paper_authors:{authors}" if isinstance(authors, str) and authors.strip()
                  else f"source_document:{sfile}")
        )

        for sent in SENTENCE_SPLIT_RE.split(para["text"]):
            sent = _norm_ws(sent)
            if len(sent) < 20:
                continue
            # Skip bibliography / reference-list entries (e.g., "12. Author AB. 2017 Annual Report... doi:...")
            if re.search(r"\b(?:Annual\s+Report|REFERENCES|Available\s+from:|https?://|doi:\s*10\.)\b", sent, re.I) and len(sent) < 260:
                continue
            if re.match(r"^\d{1,3}\.\s+[A-Z][a-z]+(?:\s+[A-Z]{1,3})?,", sent):
                continue
            raw_clauses = [_norm_ws(c).strip(",; ") for c in COMPOUND_CLAUSE_SPLIT_RE.split(sent) if len(_norm_ws(c)) >= 24]
            clauses: list[str] = []
            for rc in raw_clauses:
                sub_parts = [_norm_ws(sp).strip(",; ") for sp in AND_MULTI_PRED_SPLIT_RE.split(rc) if len(_norm_ws(sp)) >= 18]
                clauses.extend(sub_parts if len(sub_parts) > 1 else [rc])
            for clause in clauses:
                # Skip fragmented PDF snippets with unbalanced parentheses or starting mid-clause
                if clause.count("(") != clause.count(")") or re.match(r"^(?:and|or|but|mainly|with|of|to|in|for|which|that)\b", clause, re.I):
                    continue
                matched_pred = None
                voice = "active"
                match_obj = None
                for pred_name, pat in RELATIONS_PASSIVE.items():
                    m = re.search(pat, clause, re.I)
                    if m:
                        matched_pred = pred_name
                        voice = "passive"
                        match_obj = m
                        break
                if matched_pred is None:
                    for pred_name, pat in RELATIONS_ACTIVE.items():
                        m = re.search(rf"(?:^|\s)({pat})(?:\s|$|[,.;:])", clause, re.I)
                        if m:
                            raw_tok = m.group(1).strip()
                            # Skip capitalized proper nouns (e.g., "Poison Control Centers", "Support Vector")
                            if raw_tok and raw_tok[0].isupper():
                                continue
                            # Skip nominalized, adjectival, or passive-participle false positives
                            prev_ctx = clause[: m.start()].strip().lower()
                            matched_tok = raw_tok.lower()
                            if re.search(
                                r"\b(?:a|an|the|this|that|these|those|their|its|our|his|her|of|with|by|for|in|on|to|"
                                r"most|more|widely|commonly|frequently|rarely|newly|previously|well|"
                                r"accidental|indiscriminate|chronic|acute|rapid|gradual|significant|marked|sharp|slight|annual|overall|further|"
                                r"was|were|is|are|be|been|being)$",
                                prev_ctx,
                            ) and matched_tok in {
                                "use", "used", "uses", "decrease", "decreases", "decreased", "increase", "increases", "increased",
                                "reduce", "reduced", "reduction", "elevated", "lowered", "support", "supports", "supported",
                                "control", "controls", "controlled", "express", "expressed", "reach", "reached", "achieve", "achieved", "promote", "promoted",
                            }:
                                continue
                            matched_pred = pred_name
                            voice = "active"
                            match_obj = m
                            break
                if matched_pred is None or match_obj is None:
                    continue

                left = _norm_ws(clause[: match_obj.start()]).strip(",;:- ")
                right = _norm_ws(clause[match_obj.end() :]).strip(",;:- .")
                # Trim trailing condition from right span so object stays crisp
                cond = _extract_condition_phrase(clause)
                right_clean = right
                if cond and cond in right_clean:
                    right_clean = _norm_ws(right_clean.replace(cond, "")).strip(",;:- .")
                # Strip leading filler words from left/right
                left = re.sub(r"^(?:in\s+this\s+study,\s*|we\s+found\s+that\s+|results\s+show\s+that\s+|specifically,\s*|furthermore,\s*|additionally,\s*|moreover,\s*|notably,\s*|the\s+)", "", left, flags=re.I).strip()
                right_clean = re.sub(r"^(?:the\s+|a\s+|an\s+|to\s+|by\s+)", "", right_clean, flags=re.I).strip()
                # Reject dangling function-word boundaries that indicate broken NP spans
                if re.search(r"\b(?:a|an|the|with|of|to|in|for|and|or|by|from|their|its|which|that|was|were|is|are|have|has|had)$", left, re.I):
                    continue
                if re.match(r"^(?:and|or|but|in|on|at|of|for|by|with|as|from|which|that|were|was|is|are)\b", right_clean, re.I):
                    continue
                # Bound entity span lengths
                if len(left.split()) > 10:
                    left = " ".join(left.split()[-6:])
                if len(right_clean.split()) > 12:
                    right_clean = " ".join(right_clean.split()[:8])

                if len(left) < 3 or len(right_clean) < 3:
                    continue
                if voice == "passive":
                    subj, obj = right_clean, left
                else:
                    subj, obj = left, right_clean

                key = (sfile, pid, clause.casefold())
                if key in seen_quotes:
                    continue
                seen_quotes.add(key)

                pol = _classify_polarity(clause)
                ev_type = _classify_evidence_type(clause)
                candidates.append({
                    "statement": clause,
                    "subject": subj,
                    "predicate": matched_pred,
                    "object": obj,
                    "quote": clause,
                    "source_file": sfile,
                    "paragraph_id": pid,
                    "polarity": pol,
                    "condition": cond or "",
                    "evidence_type": ev_type,
                    "assertion_agent": default_agent,
                    "extraction_origin": "auto_atomic_splitter",
                })
                if len(candidates) >= max_claims:
                    return candidates
    return candidates


def verify_and_build_nanopub(
    proposal: dict[str, Any],
    anchor_lookup: dict[tuple[str, str], list[dict[str, Any]]],
    source_meta: dict[str, dict[str, Any]],
    *,
    curator_id: str,
    signer_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Audit a single atomic claim proposal and build a signed 4-graph Nanopublication."""
    required_keys = ("statement", "subject", "predicate", "object", "quote", "source_file", "paragraph_id", "polarity")
    if not isinstance(proposal, dict) or not all(
        isinstance(proposal.get(k), str) and proposal[k].strip() for k in required_keys
    ):
        return None, {"code": "MISSING_ASSERTION_OR_ANCHOR", "detail": "Required keys: " + ", ".join(required_keys)}

    fields = {k: _norm_ws(proposal[k]) for k in required_keys}
    condition = _norm_ws(str(proposal.get("condition") or ""))
    if fields["predicate"] not in RELATIONS_ACTIVE or fields["polarity"] not in POLARITIES:
        return None, {"code": "UNKNOWN_RELATION_OR_POLARITY", "predicate": fields["predicate"], "polarity": fields["polarity"]}

    # Anti-overbroad assertion check (prevents dumping entire multi-sentence paragraphs or multi-predicate 'and' compounds)
    if len(fields["statement"].split()) > 65 or len(re.findall(r"[.!?。！？]\s+\S", fields["statement"])) >= 2:
        return None, {"code": "OVERBROAD_NON_ATOMIC_ASSERTION", "detail": "Assertion spans multiple sentences or >65 words; split into atomic claims."}
    if _has_unsplit_and_multi_predicate(fields["statement"]):
        return None, {"code": "COMPOUND_MULTI_PREDICATE_NOT_SPLIT", "detail": "Compound 'and' sentence contains >=2 independent predicates; split into atomic assertions."}

    # Hard-block unresolved pronouns or generic deictic subjects (e.g., "it", "the agent", "The present study")
    if UNRESOLVED_SUBJECT_RE.match(fields["subject"]):
        return None, {
            "code": "UNRESOLVED_PRONOUN_OR_DEICTIC_SUBJECT",
            "subject": fields["subject"],
            "detail": "Unresolved pronoun or generic deictic subject is forbidden; bind to an explicit named entity verified in the anchored paragraph.",
        }

    locations = anchor_lookup.get((fields["source_file"], fields["paragraph_id"]), [])
    if len(locations) != 1:
        return None, {
            "code": "ANCHOR_NOT_UNIQUE",
            "source_file": fields["source_file"],
            "paragraph_id": fields["paragraph_id"],
            "matches_found": len(locations),
        }
    para = locations[0]

    if not verify_exact_quote(fields["quote"], para["text"]):
        return None, {"code": "QUOTE_NOT_VERBATIM_IN_ANCHOR", "source_file": fields["source_file"], "paragraph_id": fields["paragraph_id"]}

    quote = fields["quote"]
    pronoun_in_quote = _norm_ws(str(proposal.get("pronoun_in_quote") or ""))
    # Subject must appear verbatim in quote, OR be explicitly resolved from the SAME anchored paragraph with pronoun_in_quote
    subj_in_quote = fields["subject"] in quote or fields["subject"].casefold() in quote.casefold()
    subj_coref_valid = (
        not subj_in_quote
        and bool(pronoun_in_quote)
        and (pronoun_in_quote.casefold() in quote.casefold())
        and (fields["subject"].casefold() in para["text"].casefold())
    )
    obj_in_quote = fields["object"].casefold() in quote.casefold()
    if (not subj_in_quote and not subj_coref_valid) or not obj_in_quote:
        return None, {"code": "SUBJECT_OR_OBJECT_NOT_IN_QUOTE", "subject": fields["subject"], "object": fields["object"]}
    if condition and condition.casefold() not in quote.casefold():
        return None, {"code": "CONDITION_NOT_IN_QUOTE", "condition": condition}

    # Negation / hedging / polarity fidelity (evaluate main clause; ignore non-restrictive ", which ..." aside)
    main_quote = re.split(r",\s+which\b", quote, maxsplit=1, flags=re.I)[0]
    main_stmt = re.split(r",\s+which\b", fields["statement"], maxsplit=1, flags=re.I)[0]
    quote_neg = bool(NEGATION_RE.search(main_quote))
    stmt_neg = bool(NEGATION_RE.search(main_stmt))
    quote_hedge = bool(HEDGE_RE.search(main_quote))
    stmt_hedge = bool(HEDGE_RE.search(main_stmt))
    if quote_neg != stmt_neg:
        return None, {"code": "NEGATION_NOT_PRESERVED"}
    if quote_hedge != stmt_hedge:
        return None, {"code": "UNCERTAINTY_NOT_PRESERVED"}
    if fields["polarity"] == "affirmed" and (quote_neg or quote_hedge):
        return None, {"code": "POLARITY_CONFLICTS_WITH_QUALIFIER"}
    if fields["polarity"] == "negated" and not stmt_neg:
        return None, {"code": "NEGATED_STATEMENT_MISSING_NEGATION"}
    if fields["polarity"] == "uncertain" and not stmt_hedge:
        return None, {"code": "UNCERTAIN_STATEMENT_MISSING_HEDGE"}

    # Structured entity-bound numeric/unit/comparator conservation (preserves %, °C, ×, mM/mm and catches cross-entity swaps)
    obs_qty_recs = _extract_quantity_records(quote)
    stmt_qty_recs = _extract_quantity_records(fields["statement"])
    obs_qty = {r["canonical"] for r in obs_qty_recs}
    stmt_qty = {r["canonical"] for r in stmt_qty_recs}
    if stmt_qty != obs_qty:
        return None, {
            "code": "NUMERIC_VALUE_UNIT_OR_COMPARATOR_CHANGED",
            "quote_quantities": sorted(obs_qty),
            "statement_quantities": sorted(stmt_qty),
        }
    if _has_cross_entity_quantity_swap(obs_qty_recs, stmt_qty_recs):
        return None, {
            "code": "QUANTITY_ENTITY_BINDING_MISMATCH",
            "detail": "Quantities appear swapped across distinct entities between quote and statement.",
        }

    # Explicit directional relation check (active or passive)
    quote_subj_for_dir = pronoun_in_quote if subj_coref_valid else fields["subject"]
    q_dir_ok, q_voice = _check_relation_direction(quote, quote_subj_for_dir, fields["predicate"], fields["object"])
    s_dir_ok, s_voice = _check_relation_direction(fields["statement"], fields["subject"], fields["predicate"], fields["object"])
    if not (q_dir_ok and s_dir_ok):
        return None, {"code": "DIRECTION_OR_RELATION_NEEDS_REVIEW", "quote_voice": q_voice, "statement_voice": s_voice}

    # Scope qualifier requires explicit condition
    if SCOPE_RE.search(quote) and (not condition or condition.casefold() not in fields["statement"].casefold()):
        return None, {"code": "SOURCE_SCOPE_REQUIRES_EXPLICIT_CONDITION"}

    # Deterministic evidence classification: block arbitrary draft override of cited_secondary / indirect_inference quotes!
    detected_ev = _classify_evidence_type(quote)
    proposed_ev = _norm_ws(str(proposal.get("evidence_type") or ""))
    if proposed_ev and proposed_ev in EVIDENCE_TYPES:
        # Strict guard: never allow draft to promote cited_secondary -> direct_empirical or indirect_inference -> direct_empirical
        if detected_ev in {"cited_secondary", "indirect_inference"} and proposed_ev == "direct_empirical":
            return None, {
                "code": "EVIDENCE_TYPE_OVERRIDE_CONFLICT",
                "proposed_evidence_type": proposed_ev,
                "detected_evidence_type": detected_ev,
                "detail": f"Draft proposed '{proposed_ev}' but quote exhibits deterministic '{detected_ev}' markers; override blocked.",
            }
        # Never allow draft to mislabel a cited_secondary quote as indirect_inference
        if detected_ev == "cited_secondary" and proposed_ev != "cited_secondary":
            return None, {
                "code": "EVIDENCE_TYPE_OVERRIDE_CONFLICT",
                "proposed_evidence_type": proposed_ev,
                "detected_evidence_type": detected_ev,
                "detail": "Quote contains secondary citation markers and must remain 'cited_secondary'.",
            }
        evidence_type = proposed_ev
    else:
        evidence_type = detected_ev

    raw_meta = source_meta.get(fields["source_file"]) or {}
    trusted_meta = {k: raw_meta[k] for k in ("doi", "authors", "year", "version", "license", "title") if raw_meta.get(k) is not None}

    assertion_agent = _norm_ws(str(proposal.get("assertion_agent") or ""))
    if not assertion_agent:
        authors = trusted_meta.get("authors")
        assertion_agent = (
            f"paper_authors:{authors[0]}" if isinstance(authors, list) and authors
            else f"source_document:{fields['source_file']}"
        )

    prop_signer = _norm_ws(str(proposal.get("signer_id") or signer_id))
    prop_creator = _norm_ws(str(proposal.get("curator_id") or curator_id))
    distinct_flag = bool(proposal.get("asserted_by_distinct_agent", assertion_agent != prop_signer))

    assertion_graph = {
        "subject": fields["subject"],
        "predicate": fields["predicate"],
        "predicate_uri": PREDICATE_URIS.get(fields["predicate"], f"urn:nanopub:predicate:{fields['predicate']}"),
        "object": fields["object"],
        "atomic_statement": fields["statement"],
        "condition": condition or None,
        "polarity": fields["polarity"],
        "assertion_agent": assertion_agent,
        "asserted_by_distinct_agent": distinct_flag,
        "quantities": sorted(obs_qty),
        "quantity_records": obs_qty_recs,
    }
    provenance_graph: dict[str, Any] = {
        "source_file": fields["source_file"],
        "paragraph_id": fields["paragraph_id"],
        "quote": quote,
        "evidence_type": evidence_type,
        "voice_verified": q_voice,
        "source_metadata": trusted_meta,
        "evidence_check": "verbatim_anchor+direction+emlsi_quantity+polarity_verified",
    }
    if "page" in para:
        provenance_graph["page"] = para["page"]

    pubinfo_base = {
        "creator": prop_creator,
        "state": "local_draft",
        "no_external_publish": True,
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "extraction_origin": str(proposal.get("extraction_origin") or "supplied_draft_claim"),
        "confidence_basis": (
            f"Passed verbatim locator, {q_voice}-voice relation direction, quantity/unit, polarity, and scope checks; "
            f"evidence_type={evidence_type}."
        ),
    }

    npub = sign_nanopublication(
        assertion_graph,
        provenance_graph,
        pubinfo_base,
        signer_id=prop_signer,
        signing_key_hint=str(proposal.get("signing_key_hint") or "local-draft-ephemeral"),
    )
    return npub, None


def detect_assertion_conflicts(nanopubs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect opposite-polarity or opposite-predicate pairs while distinguishing condition scope."""
    conflicts: list[dict[str, Any]] = []
    for i in range(len(nanopubs)):
        for j in range(i + 1, len(nanopubs)):
            a1 = nanopubs[i]["assertion"]
            a2 = nanopubs[j]["assertion"]
            if a1["subject"].casefold() != a2["subject"].casefold() or a1["object"].casefold() != a2["object"].casefold():
                continue
            same_pred_opp_pol = (a1["predicate"] == a2["predicate"]) and (
                {a1["polarity"], a2["polarity"]} == {"affirmed", "negated"}
            )
            opp_pred_same_pol = ((a1["predicate"], a2["predicate"]) in OPPOSITE_PREDICATES) and (
                a1["polarity"] == a2["polarity"] == "affirmed"
            )
            if same_pred_opp_pol or opp_pred_same_pol:
                c1 = (a1.get("condition") or "").casefold()
                c2 = (a2.get("condition") or "").casefold()
                scope_relation = "different_conditions_preserved_separately" if c1 != c2 else "same_condition_direct_conflict"
                conflicts.append({
                    "nanopub_a": nanopubs[i]["nanopub_id"],
                    "nanopub_b": nanopubs[j]["nanopub_id"],
                    "subject": a1["subject"],
                    "object": a1["object"],
                    "predicate_pair": [a1["predicate"], a2["predicate"]],
                    "polarity_pair": [a1["polarity"], a2["polarity"]],
                    "condition_a": a1.get("condition"),
                    "condition_b": a2.get("condition"),
                    "classification": scope_relation,
                })
    return conflicts


def render_evidence_markdown(
    case_id: str,
    nanopubs: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    sig_summary: dict[str, Any],
) -> str:
    lines = [
        f"# Nanopublication 4-Graph Audit & Evidence Report ({case_id})",
        "",
        f"- **Accepted Nanopublications**: {len(nanopubs)} | **Signatures Verified**: {sig_summary['valid_count']}/{sig_summary['checked_count']} (`no_external_publish=true`)",
        f"- **Cross-Assertion Conflicts**: {len(conflicts)} | **Coverage Gaps / Rejected Drafts**: {len(gaps)}",
        "",
        "| ID | Source & Locator | Assertion (`S -> P [pol] -> O`) | Evidence & Quantities | Roles (`assertion_agent` / `creator` / `signer`) | Trusty Code | Verbatim Quote |",
        "|---|---|---|---|---|---|---|",
    ]
    for idx, npub in enumerate(nanopubs, start=1):
        a = npub["assertion"]
        p = npub["provenance"]
        pub = npub["publication_info"]
        integ = pub["integrity"]
        pg_tag = f" Page {p['page']}" if "page" in p else ""
        loc = f"`{p['source_file']}` @ `{p['paragraph_id']}`{pg_tag}"
        qty_s = ", ".join(a.get("quantities") or []) or "—"
        cond_s = f" (cond: {a['condition']})" if a.get("condition") else ""
        q_clean = p["quote"].replace("|", "\\|")
        lines.append(
            f"| `NP{idx}` (`{npub['nanopub_id'][:14]}`) | {loc} | `{a['subject']}` —**{a['predicate']}** (`{a['polarity']}`)→ `{a['object']}`{cond_s} | "
            f"`{p['evidence_type']}` ({p['voice_verified']}); qty: {qty_s} | "
            f"`{a['assertion_agent']}` / `{pub['creator']}` / `{pub['signed_by']}` | `{integ['trusty_artifact_code'][:16]}` | {q_clean} |"
        )
    lines.append("")
    if conflicts:
        lines.extend(["## Cross-Assertion Conflict & Condition Analysis", ""])
        for c in conflicts:
            lines.append(
                f"- `{c['nanopub_a']}` vs `{c['nanopub_b']}` ({c['subject']} → {c['object']}): "
                f"`{c['classification']}` | cond_a={c['condition_a']!r}, cond_b={c['condition_b']!r}"
            )
        lines.append("")
    if gaps:
        lines.extend(["## Unresolved Proposals & Coverage Gaps", ""])
        for g in gaps:
            idx_tag = f" (draft #{g['claim_index']})" if "claim_index" in g else ""
            msg = g.get("message") or g.get("detail") or ""
            lines.append(f"- **{g['code']}**{idx_tag}: {msg}")
        lines.append("")
    return "\n".join(lines)


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    case_id: str,
    claims_file: Path | None,
    curator_id: str,
    signer_id: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)

    case_path = input_dir / "case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace")) if case_path.is_file() else {}
    case_id = case_id or str(case_data.get("case_id") or "nanopub-case")

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="product-arena-09-0e8befd4")

    budget_guard.tick_phase(budget_file, "index_sources")
    paragraphs, gaps, source_meta = collect_source_paragraphs(input_dir, case_data)
    result_guard.atomic_write_json(work_dir / "source_paragraphs.json", paragraphs)

    # Load explicit draft claims if provided; otherwise run atomic candidate extraction
    proposals: list[dict[str, Any]] = []
    mode = "supplied_claims_audit"
    if claims_file and claims_file.is_file():
        loaded = json.loads(claims_file.read_text(encoding="utf-8"))
        proposals = loaded.get("claims", []) if isinstance(loaded, dict) else loaded
    elif isinstance(case_data.get("claims"), list) and case_data["claims"]:
        proposals = case_data["claims"]
    else:
        for cand_name in ("draft_claims.json", "claims.json"):
            cf = input_dir / cand_name
            if cf.is_file():
                loaded = json.loads(cf.read_text(encoding="utf-8"))
                proposals = loaded.get("claims", []) if isinstance(loaded, dict) else loaded
                break

    if not proposals and paragraphs:
        mode = "auto_atomic_extraction_and_audit"
        proposals = extract_candidate_claims_from_paragraphs(paragraphs, source_meta)
        if not proposals:
            gaps.append({
                "code": "NO_EXPLICIT_ATOMIC_CLAIMS_EXTRACTED",
                "message": "Indexed source paragraphs but found no explicit controlled-predicate atomic claims; manual drafting via --claims recommended.",
            })
    elif not paragraphs:
        gaps.append({"code": "NO_READABLE_SOURCE", "message": "No readable source paragraphs found in input directory."})

    by_anchor: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in paragraphs:
        by_anchor[(p["source_file"], p["paragraph_id"])].append(p)

    budget_guard.tick_phase(budget_file, "verify_and_sign_nanopubs")
    accepted_map: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for idx, prop in enumerate(proposals, start=1):
        npub, err = verify_and_build_nanopub(prop, by_anchor, source_meta, curator_id=curator_id, signer_id=signer_id)
        if err:
            err_entry = {"claim_index": idx, **err}
            rejected.append(err_entry)
            gaps.append(err_entry)
        else:
            assert npub is not None
            accepted_map[npub["nanopub_id"]] = npub

    accepted = list(accepted_map.values())
    covered_sources = {n["provenance"]["source_file"] for n in accepted}
    for sf in sorted({p["source_file"] for p in paragraphs}):
        if sf not in covered_sources:
            gaps.append({
                "code": "SOURCE_FILE_ZERO_NANOPUBS_EXTRACTED",
                "source_file": sf,
                "message": f"Indexed source '{sf}' contributed 0 accepted Nanopublications; verify whether explicit atomic claims require manual drafting.",
            })
    conflicts = detect_assertion_conflicts(accepted)

    # Local self-verification of every signed 4-graph Nanopublication
    sig_reports = [verify_nanopublication(npub) for npub in accepted]
    sig_summary = {
        "checked_count": len(sig_reports),
        "valid_count": sum(1 for r in sig_reports if r["valid"]),
        "failed_count": sum(1 for r in sig_reports if not r["valid"]),
        "all_valid": all(r["valid"] for r in sig_reports),
    }

    # Write machine-readable & human-review deliverables
    np_bundle = {
        "case_id": case_id,
        "mode": mode,
        "no_external_publish": True,
        "signature_verification": sig_summary,
        "conflicts": conflicts,
        "nanopublications": accepted,
        "rejected_proposals": rejected,
    }
    result_guard.atomic_write_json(output_dir / "nanopublications.json", np_bundle)
    result_guard.atomic_write_json(output_dir / "nanopublications.jsonld", export_jsonld(case_id, accepted))
    (output_dir / "nanopublications.trig").write_text(export_trig(accepted), encoding="utf-8")
    (output_dir / "evidence-report.md").write_text(
        render_evidence_markdown(case_id, accepted, conflicts, gaps, sig_summary),
        encoding="utf-8",
    )

    artifacts = [
        "result.json",
        "nanopublications.json",
        "nanopublications.jsonld",
        "nanopublications.trig",
        "evidence-report.md",
    ]
    status = "partial_with_gaps" if gaps else "ok"
    patch = {
        "status": status,
        "sources": sorted({p["source_file"] for p in paragraphs}),
        "uncertainty": (
            "Automated verification checks exact source anchor, verbatim quote, active/passive relation direction, "
            "quantity/unit conservation, polarity/scope preservation, and 4-graph cryptographic integrity. "
            "Complex cross-sentence inference and domain semantics remain subject to human review; no external publish occurs."
        ),
        "artifacts": artifacts,
        "mode": mode,
        "signature_verification": sig_summary,
        "conflicts": conflicts,
        "metrics": {
            "paragraphs_indexed": len(paragraphs),
            "claims_received": len(proposals),
            "nanopublications_count": len(accepted),
            "rejected_proposals_count": len(rejected),
            "conflicts_detected": len(conflicts),
            "signatures_valid_count": sig_summary["valid_count"],
            "direct_empirical_count": sum(1 for n in accepted if n["provenance"]["evidence_type"] == "direct_empirical"),
            "indirect_inference_count": sum(1 for n in accepted if n["provenance"]["evidence_type"] == "indirect_inference"),
            "cited_secondary_count": sum(1 for n in accepted if n["provenance"]["evidence_type"] == "cited_secondary"),
        },
        "metric_provenance": {
            "paragraphs_indexed": "work/source_paragraphs.json",
            "claims_received": mode,
            "nanopublications_count": "nanopublications.json:nanopublications",
            "rejected_proposals_count": "nanopublications.json:rejected_proposals",
            "conflicts_detected": "nanopublications.json:conflicts",
            "signatures_valid_count": "nanopub_signature_verifier.verify_nanopublication",
            "direct_empirical_count": "nanopublications.json:nanopublications[*].provenance.evidence_type",
            "indirect_inference_count": "nanopublications.json:nanopublications[*].provenance.evidence_type",
            "cited_secondary_count": "nanopublications.json:nanopublications[*].provenance.evidence_type",
        },
        "findings": [
            f"Indexed {len(paragraphs)} paragraph(s) across {len({p['source_file'] for p in paragraphs})} source(s).",
            f"Mode={mode}: {len(accepted)} unique 4-graph Nanopublication(s) verified & locally signed; {len(rejected)} proposal(s) rejected; {len(conflicts)} conflict(s) flagged.",
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["nanopublications.json", "nanopublications.jsonld", "nanopublications.trig", "evidence-report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="4-Graph Nanopublication Builder & Verifier (product-arena-09-0e8befd4).")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    parser.add_argument("--case-id", default="", help="Optional case ID override.")
    parser.add_argument("--claims", type=Path, default=None, help="Optional JSON file with candidate atomic claims.")
    parser.add_argument("--curator-id", default="urn:agent:nanopub-curator", help="pubinfo dct:creator identity.")
    parser.add_argument("--signer-id", default="urn:agent:local-draft-signer", help="pubinfo npx:signedBy identity.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(
        paths["input_dir"],
        paths["output_dir"],
        paths["work_dir"],
        args.case_id,
        args.claims,
        args.curator_id,
        args.signer_id,
    )
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
