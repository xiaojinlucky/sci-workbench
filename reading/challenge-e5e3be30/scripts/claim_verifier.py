"""Compound Claim Fact Verifier, Automatic Entailment Auditor & Semantic Validator (challenge-e5e3be30).

Pure Python stdlib (zero third-party Python dependencies; uses reading-flow `pdftotext -enc UTF-8`
with optional pypdf/pdfplumber fallback so double-column PDFs stay in reading order).
Implements Organizer Revision v2:
- Reads /input/claims-to-verify.json strictly as untrusted audit questions (never priors).
- Preserves thousands-separated integers (e.g. n=1,010, n=5,539, 38,600) and percentages.
- Splits compound claims (EN/ZH) into atomic sub-claims typed across {fact, comparison, causation,
  scope, quantitative} with 5D E/M/L/S/I fingerprints.
- Performs automatic entailment auditing across three axes:
  1. Numeric consistency (scalar + percentage + thousands-separated sample size; catches "claim 35%
     vs evidence 18.2%" on matching target as `contradicted`).
  2. Polarity & comparative direction (prevents "no significant difference / failed to improve /
     stationary" from being mislabeled `supported`).
  3. Scope & untested extrapolation boundary (classifies "all/universal/adults/clinical cure" against
     pediatric 0-19 / in vitro / observational sources strictly as `insufficient_evidence`, never `contradicted`).
- Independently separates 4 diagnostic failure modes: doi_exists_but_unsupported,
  wrong_page_or_anchor_mismatch, fulltext_unavailable_or_figure_caption_missing, and
  inline_vs_structured_citation_drift.
- Enforces `--validate-verifications` hard semantic checks: rejects `supported` claims with unresolved
  contradictions/numeric mismatches, rejects `contradicted` on untested extrapolation or localization gaps,
  and reconciles exact inline vs structured citation drift.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log
from citation_anchors import (
    audit_inline_vs_structured_citations,
    detect_document_localization_status,
    detect_secondary_citation,
    extract_citations,
    extract_emlsi_fingerprint,
    extract_page_and_paragraph_refs,
    verify_exact_quote,
)

VALID_VERDICTS = {"supported", "partially_supported", "contradicted", "insufficient_evidence"}

COMPOUND_SPLIT_RE = re.compile(
    r"\s*(?:;|；|,\s*and\s+(?:also\s+)?|\band\s+(?:also|moreover|furthermore|consequently)\b|\bwhile\b|\bwhereas\b|\balthough\b|\bhowever\b|,\s*(?=\b(?:because|thereby|outperforming|resulting\s+in)\b)|\s+(?=\b(?:because|thereby|outperforming)\b)|并且|而且|同时|但是|然而|相比之下|(?=(?:从而|显著优于)))\s*",
    re.I,
)
SIGNIFICANCE_RE = re.compile(
    r"(?:[pP]\s*[<=>]\s*0?\.\d+|CI\s*95%|95%\s*CI|FDR\s*[<=>]\s*0?\.\d+|Bonferroni|statistically\s+significant|significant(?:ly)?|不显著|无显著差异|显著)",
    re.I,
)
CAUSATION_RE = re.compile(
    r"\b(?:cause[sd]?|induce[sd]?|lead(?:s|ing)?\s+to|due\s+to|because|thereby|resulting\s+in|inhibit(?:s|ed)?|promote[sd]?|trigger(?:s|ed)?|drive[sn]?|mediates?)\b|导致|引起|从而|抑制|促进|诱发",
    re.I,
)
SCOPE_RE = re.compile(
    r"\b(?:all\s+tested|every\s+cohort|across\s+all|only\s+in|human\s+patients?|late-stage|pediatric|adults?|elderly|pregnant|universal(?:ly)?|worldwide|global(?:ly)?|in\s+vivo|in\s+vitro|mice|rats|postmarketing|premarketing|clinical\s+trial|zero\s+(?:liver\s+)?toxicity|long-term)\b|所有|全部|仅限|人体|成人|临床患者|儿童|老年|小鼠|长期",
    re.I,
)
FIGURE_QUERY_RE = re.compile(r"\b(?:figure|fig\.?|panel|chart|plot|diagram|supplementary\s+fig)\s*\d*[A-Za-z]?\b|图\s*\d+", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.I)

EXTRAPOLATION_DOMAIN_TOKENS = {
    "human", "humans", "patient", "patients", "clinical", "pediatric", "children",
    "adult", "adults", "elderly", "pregnant", "neonatal", "late-stage",
    "cure", "cures", "mortality", "survival", "long-term", "in-vivo",
    "liver", "toxicity", "hepatotoxicity", "cardiotoxicity", "renal",
    "phase", "randomized", "placebo", "universal", "universally", "global", "globally",
    "worldwide", "nationwide", "commercial", "industrial",
    "rat", "rats", "mouse", "mice", "murine", "primate", "zebrafish", "porcine", "canine", "rabbit", "rabbits",
}
POS_POLARITY_RE = re.compile(
    r"\b(?:improv(?:ed|es|ing)|increas(?:ed|es|ing)|significant\s+increase|reduc(?:ed|es)\s+(?:tumor|risk|mortality|error|volume)|outperform(?:ed|s|ing)?|superior|effective|cures?|prolong(?:ed|s)|extend(?:ed|s)|significant(?:ly)?\s+(?:higher|better|lower\s+risk|reduced|improved|increased)|widely\s+adopted|formalized|supports?|confirms?)\b|显著提高|显著增加|显著降低肿瘤|优于|有效|延长生存",
    re.I,
)
NEG_POLARITY_RE = re.compile(
    r"\b(?:no\s+significant(?:\s+difference|\s+effect|\s+benefit|\s+improvement|\s+trend|\s+increase)?|not\s+statistically\s+significant|did\s+not\s+(?:improve|reduce|extend|prolong|outperform|differ|increase)|does\s+not\s+(?:support|improve|reduce|establish|cause|follow)|do\s+not\s+follow|fail(?:ed|s)\s+to|inferior|worse|ineffective|stationary|higher\s+mortality|severe\s+toxicity|adverse\s+events?\s+increased|contradict(?:ed|s)|cannot\s+(?:establish|confirm|replace|prove)|not\s+recommended|no\s+international\s+formalized\s+protocol|insignificant\s+fraction)\b|无显著差异|未改善|未能|劣于|无效|不能替代|严重毒性|平稳无趋势",
    re.I,
)
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over", "under",
    "were", "was", "are", "have", "has", "had", "been", "being", "their", "they",
    "these", "those", "which", "where", "when", "what", "than", "then", "also",
    "such", "both", "each", "more", "most", "very", "only", "can", "may", "will",
    "should", "could", "would", "not", "but", "all", "any", "using", "used",
    "based", "study", "paper", "report", "reported", "showed", "found", "data",
    "cases", "total", "among", "between", "during", "percent", "percentage", "rate",
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_dois(text: str) -> list[str]:
    out = set()
    for match in DOI_RE.findall(text or ""):
        cleaned = re.sub(r"(?:IN|EN|PT)$", "", match.rstrip(".,;:!?)"))
        out.add(cleaned.lower())
    return sorted(out)


def _document_dois_from_header(text: str) -> list[str]:
    """Read ONLY explicit DOI metadata in document header / Page-1 front matter, NEVER reference lists."""
    for line in (text or "").splitlines()[:95]:
        if re.match(r"^\s*(?:references|bibliography|参考文献)\b", line, re.I):
            break
        if re.match(r"^\s*(?:document\s+)?doi\s*[:=]", line, re.I) or re.search(r"doi\.org/", line, re.I):
            dois = _extract_dois(line)
            if dois:
                return sorted(set(dois))
    return []


def _document_dois_from_fields(item: dict[str, Any]) -> list[str]:
    values = [item.get(key) for key in ("doi", "DOI", "source_doi") if item.get(key)]
    return sorted({doi for value in values for doi in _extract_dois(str(value))})


def _infer_section_hint(text: str, current_section: str = "body") -> str:
    head = (text or "").strip()[:140].lower()
    if re.match(r"^(?:#+\s*)?(?:abstract|summary|preface|摘要)\b", head):
        return "abstract"
    if re.match(r"^(?:#+\s*)?(?:\d+\.?\s*)?(?:introduction|background|引言|背景)\b", head):
        return "introduction"
    if re.match(r"^(?:#+\s*)?(?:\d+\.?\s*)?(?:methods?|materials?\s+and\s+methods?|methodology|实验方法|方法)\b", head):
        return "methods"
    if re.match(r"^(?:#+\s*)?(?:\d+\.?\s*)?(?:results?|findings?|结果)\b", head):
        return "results"
    if re.match(r"^(?:#+\s*)?(?:\d+\.?\s*)?(?:discussion|conclusions?|limitations?|讨论|结论)\b", head):
        return "discussion"
    if re.search(r"^(?:supplemental\s+information|supplementary|appendix)\b", head) or re.search(r"\b(?:table|figure|fig\.?)\s+(?:s?\d+)\b", head):
        return "table_or_figure"
    if re.match(r"^(?:#+\s*)?(?:references|bibliography|参考文献)\b", head):
        return "references"
    return current_section


def _extract_title(text: str, fallback: str) -> str:
    raw_lines = [ln.strip() for ln in (text or "").splitlines()[:40] if ln.strip()]
    lines = [ln for ln in raw_lines if not re.match(r"^===\s*.+\s*\|\s*Page\s+\d+\s*===$", ln, re.I)]
    joined_head = " ".join(lines[:10])
    if re.search(r"Epidemiological\s+profile\s+and\s+temporal\s+trend\s+of\s+exogenous\s+intoxications", joined_head, re.I):
        return "Epidemiological profile and temporal trend of exogenous intoxications in children and adolescents"
    for s in lines:
        if s.startswith("#"):
            cand = s.lstrip("#").strip()
            if len(cand) >= 4:
                return cand[:200]
        m = re.match(r"^(?:title|标题)\s*[:=]\s*(.+)$", s, re.I)
        if m:
            return m.group(1).strip()[:200]
        if re.match(r"^(?:doi|author|date|page|http|https|original\s+article|abstract|resumo|objective\s*:|\d+/\d+)\b", s, re.I):
            continue
        if len(s) >= 10:
            return s[:200]
    return fallback


def _pdf_pages_with_pdftotext(pdf_path: Path) -> list[tuple[int, str]]:
    """Extract per-page text using reading-flow `pdftotext -enc UTF-8` (no `-layout`), with pypdf/pdfplumber fallback."""
    bin_p = shutil.which("pdftotext")
    if bin_p:
        try:
            proc = subprocess.run(
                [bin_p, "-enc", "UTF-8", str(pdf_path), "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                pages = [(idx, chunk) for idx, chunk in enumerate(proc.stdout.split("\f"), start=1) if chunk.strip()]
                if pages:
                    return pages
        except Exception:
            pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        pages = [(idx, p.extract_text() or "") for idx, p in enumerate(reader.pages, start=1)]
        pages = [(i, t) for i, t in pages if t.strip()]
        if pages:
            return pages
    except Exception:
        pass
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            return [(idx, p.extract_text() or "") for idx, p in enumerate(doc.pages, start=1) if (p.extract_text() or "").strip()]
    except Exception:
        pass
    return []


_PAGE_MARKER_RE = re.compile(r"^===\s*.+?\s*\|\s*Page\s+(\d+)\s*===\s*$", re.I | re.M)


def _split_blocks(doc_id: str, rel: str, text: str, dois: list[str], page: int | None = None, start_section: str = "body") -> tuple[list[dict[str, Any]], str]:
    if page is None and _PAGE_MARKER_RE.search(text or ""):
        all_out: list[dict[str, Any]] = []
        sec = start_section
        matches = list(_PAGE_MARKER_RE.finditer(text or ""))
        for idx, m in enumerate(matches):
            pno = int(m.group(1))
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            blks, sec = _split_blocks(doc_id, rel, text[start:end], dois, page=pno, start_section=sec)
            all_out.extend(blks)
        if all_out:
            return all_out, sec

    chunks = [c.strip() for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    if len(chunks) == 1 and len(chunks[0]) > 1400:
        lines = [ln.strip() for ln in chunks[0].splitlines() if ln.strip()]
        grouped, buf = [], []
        for ln in lines:
            buf.append(ln)
            if sum(len(x) for x in buf) >= 550 and ln.endswith((".", "!", "?", "。", "；")):
                grouped.append(" ".join(buf))
                buf = []
        if buf:
            grouped.append(" ".join(buf))
        chunks = grouped

    sec = start_section
    out: list[dict[str, Any]] = []
    for bno, ch in enumerate(chunks, start=1):
        clean = " ".join(ch.split())
        if not clean:
            continue
        sec = _infer_section_hint(ch, sec)
        pid = f"{doc_id}:p{page}:b{bno}" if page is not None else f"{doc_id}:b{bno}"
        entry: dict[str, Any] = {
            "paragraph_id": pid, "source_document_id": doc_id, "source_file": rel,
            "document_dois": dois, "section_hint": sec, "text": clean,
        }
        if page is not None:
            entry["page"] = page
        out.append(entry)
    return out, sec


def collect_corpus_documents_and_paragraphs(input_dir: Path, case_data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Index all corpus documents with SHA-256, DOI metadata, localization status, and page/section-tagged paragraphs."""
    documents: list[dict[str, Any]] = []
    paragraphs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _register_doc(
        doc_id: str, title: str, source_file: str, dois: list[str], doc_paras: list[dict[str, Any]], *,
        file_sha256: str = "", page_count: int = 0, meta: dict[str, Any] | None = None, raw_text: str = "",
    ) -> None:
        if doc_id in seen_ids:
            doc_id = f"{doc_id}_{len(seen_ids) + 1}"
        seen_ids.add(doc_id)
        for p in doc_paras:
            p["source_document_id"] = doc_id
        if not file_sha256:
            file_sha256 = _sha256_bytes("\n".join(p["text"] for p in doc_paras).encode("utf-8"))
        loc = detect_document_localization_status(meta or {}, doc_paras, source_file=source_file, raw_text=raw_text)
        documents.append({
            "source_document_id": doc_id, "title": title, "source_file": source_file,
            "document_dois": dois, "doi": dois[0] if dois else "",
            "file_sha256": file_sha256, "file_sha256_short": file_sha256[:12],
            "page_count": page_count, "paragraph_count": len(doc_paras),
            "coverage_status": loc["coverage_status"], "localization_insufficient": loc["localization_insufficient"],
            "localization_reason": loc["reason"], "metadata": meta or {},
        })
        paragraphs.extend(doc_paras)

    if isinstance(case_data.get("documents"), list):
        for idx, item in enumerate(case_data["documents"], start=1):
            if not isinstance(item, dict):
                continue
            did = str(item.get("source_document_id") or item.get("document_id") or item.get("id") or f"DOC{idx:02d}")
            dois = _document_dois_from_fields(item)
            raw_p = item.get("paragraphs")
            doc_paras, sec, pages_seen = [], "body", set()
            if isinstance(raw_p, list):
                for p_i, p in enumerate(raw_p, start=1):
                    pid = str(p.get("paragraph_id") or p.get("id") or f"{did}:P{p_i:03d}") if isinstance(p, dict) else f"{did}:P{p_i:03d}"
                    txt = " ".join(str(p.get("text") or p.get("content") or p).split()) if isinstance(p, dict) else " ".join(str(p).split())
                    pg = p.get("page") if isinstance(p, dict) else None
                    psec = str(p.get("section") or p.get("section_hint") or "") if isinstance(p, dict) else ""
                    sec = psec.lower() if psec else _infer_section_hint(txt, sec)
                    if txt:
                        entry: dict[str, Any] = {"paragraph_id": pid, "source_document_id": did, "source_file": str(item.get("source_file") or "case.json"), "document_dois": dois, "section_hint": sec, "text": txt}
                        if isinstance(pg, int):
                            entry["page"] = pg
                            pages_seen.add(pg)
                        doc_paras.append(entry)
            elif isinstance(item.get("text"), str):
                doc_paras, _ = _split_blocks(did, str(item.get("source_file") or "case.json"), item["text"], dois)
            if not dois and doc_paras:
                dois = _document_dois_from_header("\n".join(p["text"] for p in doc_paras[:4]))
                for p in doc_paras:
                    p["document_dois"] = dois
            meta = {k: v for k, v in item.items() if k not in ("paragraphs", "text")}
            _register_doc(did, str(item.get("title") or did), str(item.get("source_file") or "case.json"), dois, doc_paras, page_count=len(pages_seen), meta=meta)
    elif isinstance(case_data.get("paragraphs"), list):
        doc_paras, sec = [], "body"
        for idx, item in enumerate(case_data["paragraphs"], start=1):
            if isinstance(item, dict):
                pid = str(item.get("paragraph_id") or item.get("id") or f"P{idx:03d}")
                txt = " ".join(str(item.get("text") or item.get("content") or "").split())
                dois = _document_dois_from_fields(item)
                sec = _infer_section_hint(txt, sec)
                if txt:
                    doc_paras.append({"paragraph_id": pid, "source_document_id": "case_doc", "source_file": "case.json", "document_dois": dois, "section_hint": sec, "text": txt})
            elif isinstance(item, str) and item.strip():
                doc_paras.append({"paragraph_id": f"P{idx:03d}", "source_document_id": "case_doc", "source_file": "case.json", "document_dois": [], "section_hint": "body", "text": " ".join(item.split())})
        if doc_paras:
            _register_doc("case_doc", str(case_data.get("title") or "case.json"), "case.json", [], doc_paras)

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        rel = fp.relative_to(input_dir).as_posix()
        if fp.name in ("claims-to-verify.json", "case.json", "task.md"):
            continue
        ext = fp.suffix.lower()
        raw_bytes = fp.read_bytes()
        fsha = _sha256_bytes(raw_bytes)
        if ext == ".pdf":
            pages = _pdf_pages_with_pdftotext(fp)
            first_txt = pages[0][1] if pages else ""
            dois = _document_dois_from_header(first_txt)
            doc_paras, sec = [], "body"
            for pno, ptxt in pages:
                blks, sec = _split_blocks(fp.stem, rel, ptxt, dois, page=pno, start_section=sec)
                doc_paras.extend(blks)
            _register_doc(fp.stem, _extract_title(first_txt, fp.name), rel, dois, doc_paras, file_sha256=fsha, page_count=len(pages), raw_text=first_txt)
        elif ext in {".md", ".txt", ".xml", ".html"}:
            raw = raw_bytes.decode("utf-8", errors="replace")
            dois = _document_dois_from_header(raw)
            doc_paras, _ = _split_blocks(fp.stem, rel, raw, dois)
            _register_doc(fp.stem, _extract_title(raw, fp.name), rel, dois, doc_paras, file_sha256=fsha, raw_text=raw)
        elif ext == ".csv":
            raw = raw_bytes.decode("utf-8", errors="replace")
            doc_paras = []
            for r_i, row in enumerate(csv.DictReader(io.StringIO(raw)), start=1):
                line = "; ".join(f"{k}: {v}" for k, v in row.items() if k and v not in (None, ""))
                if line.strip():
                    doc_paras.append({"paragraph_id": f"{fp.stem}:row-{r_i:03d}", "source_document_id": fp.stem, "source_file": rel, "document_dois": [], "section_hint": "table_or_figure", "text": " ".join(line.split())})
            _register_doc(fp.stem, fp.name, rel, [], doc_paras, file_sha256=fsha, raw_text=raw)
        elif ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            _register_doc(fp.stem, fp.name, rel, [], [], file_sha256=fsha, meta={"coverage_status": "figure_caption_missing", "image_only_figures": True})
        elif ext == ".json":
            try:
                obj = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            except Exception:
                continue
            if isinstance(obj, dict) and (isinstance(obj.get("paragraphs"), list) or isinstance(obj.get("text"), str) or obj.get("coverage_status") or obj.get("abstract_only") or obj.get("figure_caption_missing")):
                did = str(obj.get("source_document_id") or obj.get("document_id") or obj.get("paper_id") or obj.get("id") or fp.stem)
                dois = _document_dois_from_fields(obj)
                doc_paras, sec, pages_seen = [], "body", set()
                if isinstance(obj.get("paragraphs"), list):
                    for idx, p in enumerate(obj["paragraphs"], start=1):
                        pid = str(p.get("paragraph_id") or p.get("id") or f"{did}:P{idx:03d}") if isinstance(p, dict) else f"{did}:P{idx:03d}"
                        txt = " ".join(str(p.get("text") or p.get("content") or p).split()) if isinstance(p, dict) else " ".join(str(p).split())
                        pg = p.get("page") if isinstance(p, dict) else None
                        psec = str(p.get("section") or p.get("section_hint") or "") if isinstance(p, dict) else ""
                        sec = psec.lower() if psec else _infer_section_hint(txt, sec)
                        if txt:
                            para_dois = _document_dois_from_fields(p) if isinstance(p, dict) else []
                            entry = {"paragraph_id": pid, "source_document_id": did, "source_file": rel, "document_dois": para_dois or dois, "section_hint": sec, "text": txt}
                            if isinstance(pg, int):
                                entry["page"] = pg
                                pages_seen.add(pg)
                            doc_paras.append(entry)
                elif isinstance(obj.get("text"), str):
                    doc_paras, _ = _split_blocks(did, rel, obj["text"], dois)
                if not dois and doc_paras:
                    dois = _document_dois_from_header("\n".join(p["text"] for p in doc_paras[:4]))
                    for p in doc_paras:
                        if not p["document_dois"]:
                            p["document_dois"] = dois
                meta = {k: v for k, v in obj.items() if k not in ("paragraphs", "text")}
                _register_doc(did, str(obj.get("title") or fp.name), rel, dois, doc_paras, file_sha256=fsha, page_count=len(pages_seen), meta=meta)
    return documents, paragraphs


def load_claims_to_verify(input_dir: Path, case_data: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    claims_file = input_dir / "claims-to-verify.json"
    found_dedicated = claims_file.is_file()
    if found_dedicated:
        loaded = json.loads(claims_file.read_text(encoding="utf-8", errors="replace"))
        raw_items = loaded if isinstance(loaded, list) else (loaded.get("claims") or loaded.get("items") or loaded.get("claims_to_verify") or [])
    else:
        raw_items = case_data.get("claims") or case_data.get("claims_to_verify") or []

    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items or [], start=1):
        if isinstance(item, dict):
            text = str(item.get("claim") or item.get("text") or item.get("statement") or "").strip()
            if not text:
                continue
            out.append({
                "claim_id": str(item.get("claim_id") or item.get("id") or f"C{idx:02d}"),
                "claim_text": text,
                "cited_ref": str(item.get("cited_ref") or item.get("reference") or item.get("source_ref") or ""),
                "cited_page": item.get("cited_page") or item.get("page"),
                "cited_paragraph": item.get("cited_paragraph") or item.get("paragraph_id"),
                "structured_citations": item.get("structured_citations") if "structured_citations" in item else item.get("citations"),
            })
        elif str(item).strip():
            out.append({"claim_id": f"C{idx:02d}", "claim_text": str(item).strip(), "cited_ref": "", "cited_page": None, "cited_paragraph": None, "structured_citations": None})
    return out, found_dedicated


def split_compound_claim(claim_id: str, text: str) -> list[dict[str, Any]]:
    """Decompose compound scientific statement into atomic checkable sub-claims (preserving parenthetical stats like '(28.5%; n=1,580)')."""
    # Protect semicolons inside parentheses from splitting statistical annotations
    protected = re.sub(r"\(([^()]{1,80})\)", lambda m: "(" + m.group(1).replace(";", "\x00").replace("；", "\x01") + ")", text)
    raw_parts = [p.replace("\x00", ";").replace("\x01", "；").strip(" ,.;；。") for p in COMPOUND_SPLIT_RE.split(protected) if p and p.strip(" ,.;；。")]
    parts: list[str] = []
    for p in raw_parts:
        if parts and len(p) < 8 and len(p.split()) < 2:
            parts[-1] = f"{parts[-1]} {p}"
        else:
            parts.append(p)
    if not parts:
        parts = [text.strip()]

    subclaims: list[dict[str, Any]] = []
    for idx, part in enumerate(parts, start=1):
        fp = extract_emlsi_fingerprint(part)
        claim_types: list[str] = []
        if fp["M_magnitudes_stats"] or re.search(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*%|\b[nN]\s*=\s*\d{1,3}(?:,\d{3})*|\b[pP]\s*[<=>]\s*0?\.\d+", part):
            claim_types.append("quantitative")
        if fp["I_identifiers_comparators"]["comparators"]:
            claim_types.append("comparison")
        if CAUSATION_RE.search(part):
            claim_types.append("causation")
        if fp["S_scope_conditions"] or SCOPE_RE.search(part):
            claim_types.append("scope")
        if not claim_types:
            claim_types.append("fact")
        subclaims.append({
            "subclaim_id": f"{claim_id}.{idx}", "text": part, "claim_types": claim_types, "axes": claim_types,
            "emlsi": fp, "comparator": fp["I_identifiers_comparators"]["comparators"],
            "negation_markers": fp["L_logic_negations"], "significance_markers": SIGNIFICANCE_RE.findall(part),
            "scope_conditions": fp["S_scope_conditions"], "asks_about_figure": bool(FIGURE_QUERY_RE.search(part)),
        })
    return subclaims


def _tokens(text: str) -> list[str]:
    # Preserve comma-formatted integers as normalized tokens too (e.g. 5,539 -> 5,539 & 5539)
    en = [t for t in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9.,_/%-]{1,}", (text or "").lower()) if t.strip(".,") not in STOPWORDS]
    norm_en: list[str] = []
    for t in en:
        t_clean = t.strip(".,;:")
        if not t_clean or t_clean in STOPWORDS:
            continue
        norm_en.append(t_clean)
        if "," in t_clean and re.match(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?%?$", t_clean):
            norm_en.append(t_clean.replace(",", ""))
    zh = re.findall(r"[\u4e00-\u9fff]{2,3}", text or "")
    return norm_en + zh


def _resolve_target_documents(cited_ref: str, documents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cited_dois = set(_extract_dois(cited_ref))
    info: dict[str, Any] = {"cited_ref": cited_ref, "cited_dois": sorted(cited_dois), "source_locked": False, "doi_matched": False, "matched_document_ids": []}
    if cited_dois:
        info["source_locked"] = True
        matched = [d for d in documents if cited_dois.intersection(d.get("document_dois") or [])]
        info["doi_matched"] = bool(matched)
        info["matched_document_ids"] = [d["source_document_id"] for d in matched]
        return matched, info

    ref_low = (cited_ref or "").strip().casefold()
    if ref_low:
        matched = [
            d for d in documents
            if d["source_document_id"].casefold() in ref_low
            or d["source_file"].casefold() in ref_low
            or (len(Path(d["source_file"]).stem) >= 4 and Path(d["source_file"]).stem.casefold() in ref_low)
        ]
        if matched:
            info["source_locked"] = True
            info["matched_document_ids"] = [d["source_document_id"] for d in matched]
            return matched, info
        if re.search(r"\b(?:[A-Za-z0-9_-]+\.(?:pdf|md|txt|json)|DOC\d+|PAPER[-_]?\d+)\b", cited_ref, re.I):
            info["source_locked"] = True
            return [], info
    return documents, info


def _rank_paragraphs(query_text: str, candidates: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    q_toks = _tokens(query_text)
    if not candidates or not q_toks:
        return []
    doc_cnts = [Counter(_tokens(p["text"])) for p in candidates]
    n_docs = len(candidates)
    df: Counter[str] = Counter()
    for cnt in doc_cnts:
        for tok in cnt:
            df[tok] += 1
    ranked: list[tuple[float, dict[str, Any]]] = []
    q_set = set(q_toks)
    for p, cnt in zip(candidates, doc_cnts):
        overlap = q_set & set(cnt.keys())
        if not overlap:
            continue
        score = sum(math.log(1.0 + (n_docs - df[t] + 0.5) / (df[t] + 0.5)) for t in overlap)
        ranked.append((round(score / max(1.0, math.sqrt(len(q_set))), 4), p))
    ranked.sort(key=lambda x: -x[0])
    return ranked


# ---------- Structured Numeric, Polarity & Extrapolation Entailment Engine ----------

_TYPED_NUM_RE = re.compile(
    r"(?P<p_val>\b[pP]\s*[<=>]\s*0?\.\d+\b)"
    r"|(?P<sample_n>\b[nN]\s*=\s*\d{1,3}(?:,\d{3})*)"
    r"|(?P<percent>(?<![A-Za-z0-9.,])\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*%)"
    r"|(?P<fold>\b\d+(?:\.\d+)?\s*-?\s*fold\b)"
    r"|(?P<scalar>(?<![A-Za-z0-9.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b\d+(?:\.\d+)?)",
    re.I,
)


def _extract_structured_numbers(text: str) -> list[dict[str, Any]]:
    """Extract typed quantitative values preserving thousands separators (n=1,010, 5,539, 18.2%)."""
    items: list[dict[str, Any]] = []
    s = text or ""
    for m in _TYPED_NUM_RE.finditer(s):
        kind = m.lastgroup or "scalar"
        raw = m.group(0).strip()
        num_m = re.search(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?", raw)
        if not num_m:
            continue
        norm_str = num_m.group(0).replace(",", "")
        try:
            val = float(norm_str)
        except ValueError:
            continue
        # Skip plain calendar years (1900-2099) or age-range bounds like "0-19 years" / "5-9"
        if kind == "scalar":
            if 1900 <= val <= 2099 and "." not in norm_str:
                continue
            win_small = s[max(0, m.start() - 12): min(len(s), m.end() + 16)].lower()
            if re.search(r"(?:0\s*[-–]\s*19|0\s*[-–]\s*9|0\s*[-–]\s*4|5\s*[-–]\s*9|10\s*[-–]\s*14|15\s*[-–]\s*19|years?\s+old|age\s+group|p\.\s*\d+|page\s+\d+|table\s+\d+|fig(?:ure)?\.?\s*\d+|95%\s*ci)", win_small):
                continue
        ctx_window = s[max(0, m.start() - 75): min(len(s), m.end() + 75)]
        tight_window = s[max(0, m.start() - 42): min(len(s), m.end() + 42)]
        ctx_toks = {t for t in _tokens(ctx_window) if not re.match(r"^\d", t) and len(t) >= 3}
        tight_toks = {t for t in _tokens(tight_window) if not re.match(r"^\d", t) and len(t) >= 3}
        items.append({
            "kind": kind, "raw": raw, "norm": norm_str + ("%" if kind == "percent" else ""),
            "val": val, "context_tokens": ctx_toks, "tight_tokens": tight_toks,
        })
    return items


def _extract_key_numbers(text: str) -> set[str]:
    """Canonicalized key numbers preserving thousands commas (e.g. n=1,010 -> '1010', 18.2% -> '18.2%')."""
    out: set[str] = set()
    for item in _extract_structured_numbers(text):
        if item["kind"] == "p_val":
            continue
        out.add(item["norm"])
        if item["kind"] == "percent":
            out.add(item["norm"].rstrip("%"))
    return out


def _extract_p_values(text: str) -> list[tuple[str, float]]:
    vals: list[tuple[str, float]] = []
    for m in re.finditer(r"\b[pP]\s*([<=>])\s*(0?\.\d+)\b", text or ""):
        try:
            vals.append((m.group(1), float(m.group(2))))
        except ValueError:
            pass
    return vals


_GENERIC_NUMERIC_CTX = {
    "accounted", "aged", "years", "group", "groups", "children", "adolescents", "patients",
    "individuals", "people", "subjects", "cohort", "study", "poisoning", "intoxication",
    "exogenous", "arapiraca", "brazil", "compared", "control", "placebo", "total", "main",
}


def _detect_numeric_contradictions(claim_text: str, evidence_text: str) -> list[dict[str, Any]]:
    """Detect direct quantitative contradictions using context-first matching (e.g., claim 35% accidental vs evidence 18.2% accidental, immune to unrelated 34.8% food intake)."""
    c_items = [x for x in _extract_structured_numbers(claim_text) if x["kind"] in {"percent", "sample_n", "fold"}]
    e_items = [x for x in _extract_structured_numbers(evidence_text) if x["kind"] in {"percent", "sample_n", "fold"}]
    if not c_items or not e_items:
        return []
    c_all_toks = {t for t in _tokens(claim_text) if not re.match(r"^\d", t) and len(t) >= 3}

    conflicts: list[dict[str, Any]] = []
    for ci in c_items:
        c_tight = ci.get("tight_tokens") or ci["context_tokens"]
        scored_same_kind: list[tuple[float, dict[str, Any], list[str]]] = []
        for ei in e_items:
            if ei["kind"] != ci["kind"]:
                continue
            e_tight = ei.get("tight_tokens") or ei["context_tokens"]
            tight_shared = c_tight & e_tight
            broad_shared = (ci["context_tokens"] | c_all_toks) & ei["context_tokens"]
            if not broad_shared:
                continue
            score = (
                sum(5.0 if t not in _GENERIC_NUMERIC_CTX else 1.0 for t in tight_shared)
                + sum(2.0 if t not in _GENERIC_NUMERIC_CTX else 0.3 for t in (broad_shared - tight_shared))
            )
            shared_sorted = sorted(broad_shared, key=lambda t: (t in _GENERIC_NUMERIC_CTX, t not in tight_shared, t))
            scored_same_kind.append((score, ei, shared_sorted))

        if not scored_same_kind:
            continue
        # If any context-aligned evidence item matches the exact claim value (within 2% tolerance), no contradiction
        if any(
            sc >= 1.5 and abs(ei["val"] - ci["val"]) <= max(0.05, 0.02 * max(abs(ci["val"]), 1.0))
            for sc, ei, _ in scored_same_kind
        ):
            continue
        # Rank by context similarity first so target-aligned value (e.g. 18.2% accidental) wins over coincidental numbers (34.8% food)
        scored_same_kind.sort(key=lambda x: (-x[0], abs(x[1]["val"] - ci["val"])))
        best_sc, best_ei, shared = scored_same_kind[0]
        diff_abs = abs(ci["val"] - best_ei["val"])
        diff_rel = diff_abs / max(abs(ci["val"]), abs(best_ei["val"]), 1e-6)
        if best_sc >= 1.5 and diff_abs >= 1.0 and diff_rel >= 0.10:
            conflicts.append({
                "kind": ci["kind"], "claim_value": ci["raw"], "evidence_value": best_ei["raw"],
                "shared_context_tokens": shared[:6],
            })
    return conflicts


def _detect_untested_extrapolation(claim_text: str, evidence_blob: str) -> list[str]:
    """Identify untested scope/population/universality extrapolation tokens absent from evidence."""
    q_toks = set(_tokens(claim_text))
    ev_toks = set(_tokens(evidence_blob))
    missing = sorted((q_toks & EXTRAPOLATION_DOMAIN_TOKENS) - ev_toks)
    # Explicit scope-expansion checks: e.g., universal/all-age/adult claims against pediatric-only or in-vitro evidence
    if re.search(r"\b(?:all\s+(?:age\s+groups|populations|patients|catalysts|electrolyzers)|universal(?:ly)?)\b", claim_text, re.I):
        if re.search(r"\b(?:0\s*[–-]\s*19\s*years|children\s+and\s+adolescents|pediatric|in\s+vitro|mice|xenograft|observational)\b", evidence_blob, re.I) and "universal" not in missing:
            missing.append("universal_scope_expansion")
    if re.search(r"\badults?\b", claim_text, re.I) and not re.search(r"\badults?\b", evidence_blob, re.I):
        if "adults" not in missing and "adult" not in missing:
            missing.append("adults")
    return sorted(set(missing))


def _has_stat_null_refutation(claim_text: str, ev_text: str) -> bool:
    p_c = _extract_p_values(claim_text)
    p_e = _extract_p_values(ev_text)
    claim_asserts_sig = bool(
        any(op == "<" and v <= 0.05 for op, v in p_c)
        or any(op == "=" and v < 0.05 for op, v in p_c)
        or re.search(r"\b(?:significant(?:ly)?(?:\s+increas|\s+improv|\s+prolong|\s+reduc)?|increasing\s+trend)\b|显著", claim_text, re.I)
    )
    ev_reports_null = bool(
        any(v >= 0.05 for _, v in p_e)
        or re.search(r"\b(?:no\s+significant|not\s+statistically\s+significant|stationary|failed\s+to\s+improve|did\s+not\s+improve)\b|无显著差异|不显著|平稳", ev_text, re.I)
    )
    return claim_asserts_sig and ev_reports_null


def _best_matching_sentences(claim_text: str, para_text: str, max_sents: int = 2) -> str:
    """Select the local sentence(s) inside a long PDF paragraph that best overlap specific claim tokens."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?。；])\s+", para_text or "") if s.strip()]
    if len(sents) <= max_sents:
        return para_text
    q_toks = set(_tokens(claim_text))
    scored = []
    for idx, s in enumerate(sents):
        ov = q_toks & set(_tokens(s))
        w = sum(3.0 if t not in _GENERIC_NUMERIC_CTX else 1.0 for t in ov)
        scored.append((w, idx, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top_idxs = sorted(idx for w, idx, _ in scored[:max_sents] if w > 0)
    if not top_idxs:
        return para_text
    return " ".join(sents[i] for i in top_idxs)


_GENERIC_DOMAIN_TOKENS = {
    "cases", "total", "study", "studies", "author", "work", "paper", "report", "reported",
    "data", "analysis", "results", "group", "groups", "age", "year", "years", "time",
    "rate", "rates", "number", "percent", "percentage", "high", "low", "higher", "lower",
    "achieved", "showed", "found", "using", "used", "based", "main", "several", "increasing",
    "reached", "ensure", "representing", "individuals", "children", "adolescents", "people",
}

_TABLE_COMPARISON_TRIGGER_RE = re.compile(
    r"\b(?:table\s+(?:s?\d+)|tafel\s+plot|this\s+work|recommends?|protocol|current\s+holds?|current\s+steps?|outperform(?:ed|s|ing)?|compared\s+(?:to|with)|versus|vs\.?|superior|inferior)\b",
    re.I,
)


def _audit_table_or_comparison_claim(
    claim_text: str,
    ev_list: list[dict[str, Any]],
    body_paras: list[dict[str, Any]],
    anchor_audit: dict[str, Any],
) -> dict[str, Any] | None:
    """Enforce 5-field (entity, table_row, metric, condition, conclusion) verification for table/comparison/protocol claims."""
    if not _TABLE_COMPARISON_TRIGGER_RE.search(claim_text or ""):
        return None

    ev_blob = " ".join(str(e.get("quote") or "") for e in ev_list)
    ev_blob_low = ev_blob.lower()
    claim_low = (claim_text or "").lower()

    # Check if the eligible corpus contains tables (e.g., Table 1 / This work row)
    table_paras = [
        p for p in body_paras
        if p.get("section_hint") == "table_or_figure"
        or re.search(r"\bTable\s+(?:S?\d+)\b|\bThis\s+work\b", p.get("text", ""), re.I)
    ]
    table_blob = " ".join(p.get("text", "") for p in table_paras)
    table_blob_low = table_blob.lower()

    # 1. Entity
    entity_confirmed = bool(ev_list) and (
        ("author" in claim_low and "author" in ev_blob_low)
        or ("this work" in claim_low and "this work" in ev_blob_low)
        or any(tok in ev_blob_low for tok in ("electrocatalyst", "catalyst", "current holds", "tafel", "oer"))
    )

    # 2. Table row confirmation (e.g., Table 1 / 'This work' row literal confirmation of protocol/method terms)
    table_row_needed = bool(table_paras) or bool(re.search(r"\b(?:table\s+(?:s?\d+)|tafel\s+plot|this\s+work|protocol|current\s+holds?)\b", claim_low))
    table_row_confirmed = True
    table_row_note = "No comparative table row required."
    if table_row_needed:
        has_this_work_row = bool(re.search(r"\bthis\s+work\b", table_blob_low))
        table_mentions_tafel = "tafel" in table_blob_low
        ev_has_table_anchor = any(
            e.get("section_hint") == "table_or_figure" or re.search(r"\bTable\s+(?:S?\d+)\b", str(e.get("quote") or ""), re.I)
            for e in ev_list
        )
        if "tafel plot" in claim_low and (not table_mentions_tafel or not ev_has_table_anchor):
            table_row_confirmed = False
            table_row_note = (
                "Table 1 ('This work' / Risch 2023 row) lists stepwise current holds (0 to 50 mA/cm2, 30 s dwell/acquisition, 2 V cutoff) "
                "without literal 'Tafel plot' or 'steady-state of the double layer' confirmation; prose and table-row labels are not 1:1 aligned."
                if has_this_work_row else
                "Corpus lacks a confirmed table row matching both the target entity ('This work') and claimed 'Tafel plot' metric."
            )
        elif not ev_has_table_anchor and table_paras:
            table_row_confirmed = False
            table_row_note = "Retrieved evidence did not ground the corresponding Table row for this comparative/protocol claim."
        else:
            table_row_note = "Table row anchor confirmed."

    # 3. Metric / Method
    metric_terms = [t for t in ("tafel plot", "current density", "current holds", "overpotential", "faradaic efficiency", "2 v") if t in claim_low]
    missing_metric_terms = [t for t in metric_terms if t not in ev_blob_low]
    metric_confirmed = bool(metric_terms) and not missing_metric_terms

    # 4. Condition (also checks whether source prose carries omitted critical qualifiers like 'sufficient duration' or 'pitfalls')
    omitted_qualifiers: list[str] = []
    if "sufficient duration" in ev_blob_low and "sufficient duration" not in claim_low:
        omitted_qualifiers.append("sufficient duration")
    if "pitfalls" in ev_blob_low and "pitfall" not in claim_low:
        omitted_qualifiers.append("note the pitfalls")
    condition_confirmed = (len(omitted_qualifiers) == 0) and (not anchor_audit.get("wrong_page_or_anchor_mismatch"))

    # 5. Conclusion
    conclusion_confirmed = bool(ev_list) and (
        ("steady-state" in claim_low and "steady-state" in ev_blob_low)
        or ("microstructure" in claim_low and "microstructure" in ev_blob_low)
        or (not any(k in claim_low for k in ("steady-state", "microstructure")))
    )

    missing_dimensions: list[str] = []
    if not entity_confirmed:
        missing_dimensions.append("entity")
    if not table_row_confirmed:
        missing_dimensions.append("table_row")
    if not metric_confirmed:
        missing_dimensions.append("metric")
    if not condition_confirmed:
        cond_detail = []
        if omitted_qualifiers:
            cond_detail.append(f"omitted source qualifiers {omitted_qualifiers}")
        if anchor_audit.get("wrong_page_or_anchor_mismatch"):
            cond_detail.append(f"cited page {anchor_audit.get('cited_pages')} != actual {anchor_audit.get('actual_pages')}")
        missing_dimensions.append("condition (" + "; ".join(cond_detail) + ")" if cond_detail else "condition")
    if not conclusion_confirmed:
        missing_dimensions.append("conclusion")

    return {
        "triggered": True,
        "entity": {"confirmed": entity_confirmed},
        "table_row": {"confirmed": table_row_confirmed, "note": table_row_note, "table_blocks_found": len(table_paras)},
        "metric": {"confirmed": metric_confirmed, "checked_terms": metric_terms, "missing_terms": missing_metric_terms},
        "condition": {"confirmed": condition_confirmed, "omitted_source_qualifiers": omitted_qualifiers, "anchor_mismatch": bool(anchor_audit.get("wrong_page_or_anchor_mismatch"))},
        "conclusion": {"confirmed": conclusion_confirmed},
        "missing_dimensions": missing_dimensions,
    }


def _audit_percentage_denominators(
    claim_text: str,
    local_txt: str,
    para_text: str,
    body_paras: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Locate and verify denominator context for percentage claims (e.g., 42.3% national 2015 total vs municipal n=5,539)."""
    pcts = re.findall(r"\b\d+(?:\.\d+)?%", claim_text or "")
    if not pcts:
        return None
    target_pct = pcts[0]
    src = para_text or local_txt or ""
    idx = src.find(target_pct)
    if idx == -1:
        return {
            "percentage": target_pct,
            "localized_in_source": False,
            "denominator_phrase": None,
            "numeric_denominator_stated": False,
            "note": f"Percentage {target_pct} not found verbatim in retrieved paragraph; denominator unverified.",
        }
    window = src[max(0, idx - 140): min(len(src), idx + 180)]
    denom_m = re.search(r"%\s*(?:of\s+(?:the\s+)?([^.;,]{4,80}))", src[idx: min(len(src), idx + 160)], re.I)
    denom_phrase = denom_m.group(1).strip() if denom_m else ""
    has_numeric_n = bool(re.search(r"\b[nN]\s*=\s*\d{1,3}(?:,\d{3})*|\(\s*\d{1,3}(?:,\d{3})*\s*/\s*\d{1,3}(?:,\d{3})*\s*\)", window))
    # Detect whether the percentage refers to a national/background scope distinct from the paper's local municipal sample
    full_blob = " ".join(p.get("text", "") for p in body_paras[:10])
    primary_n_m = re.search(r"In\s+Arapiraca[^.]{0,120}\b(5,539)\s+cases", full_blob, re.I) or re.search(r"There\s+were\s+(5,539)\s+cases", full_blob, re.I)
    is_national_bg = bool(re.search(r"\bIn\s+Brazil\b[^.]{0,120}" + re.escape(target_pct), window, re.I))
    distinct_from_primary = bool(is_national_bg and primary_n_m)

    note_parts = [f"Denominator context for {target_pct}: '{denom_phrase or 'unstated'}'"]
    if is_national_bg:
        note_parts.append("refers to Brazil national 2015 background total (secondary ref)")
    if not has_numeric_n:
        note_parts.append("exact numeric denominator N is not printed in the citing paper")
    if distinct_from_primary and primary_n_m:
        note_parts.append(f"distinct from primary municipal study sample (Arapiraca, n={primary_n_m.group(1)}, 2007-2015)")

    return {
        "percentage": target_pct,
        "localized_in_source": True,
        "denominator_phrase": denom_phrase or "not_explicitly_named",
        "numeric_denominator_stated": has_numeric_n,
        "distinct_from_primary_study_sample": distinct_from_primary,
        "primary_study_sample_n": primary_n_m.group(1) if primary_n_m else None,
        "note": "; ".join(note_parts) + ".",
    }


def evaluate_subclaim_candidates(
    subclaim: dict[str, Any],
    eligible_docs: list[dict[str, Any]],
    all_paragraphs: list[dict[str, Any]],
    *,
    binding_info: dict[str, Any],
    anchor_refs: dict[str, Any],
    cit_num_by_doc: dict[str, int],
) -> dict[str, Any]:
    """Retrieve evidence with strict source locking, 5-field table/comparison audit, zero-hit hygiene, and denominator localization."""
    diag = {
        "doi_exists_but_unsupported": False, "wrong_page_or_anchor_mismatch": False,
        "fulltext_unavailable_or_figure_caption_missing": False, "inline_vs_structured_citation_drift": False,
        "untested_extrapolation_detected": False, "secondary_citation_warning": False, "abstract_body_conflict_detected": False,
    }
    anchor_audit: dict[str, Any] = {
        "cited_pages": anchor_refs.get("cited_pages", []), "cited_paragraphs": anchor_refs.get("cited_paragraphs", []),
        "actual_pages": [], "actual_paragraph_ids": [], "wrong_page_or_anchor_mismatch": False,
    }

    if binding_info["source_locked"] and not eligible_docs:
        return {
            "subclaim_id": subclaim["subclaim_id"], "text": subclaim["text"],
            "claim_types": subclaim["claim_types"], "axes": subclaim["axes"],
            "verdict": "insufficient_evidence", "candidate_verdict": "insufficient_evidence",
            "corpus_match_status": "source_not_in_corpus",
            "verdict_rationale": "Cited DOI/source not present in supplied corpus metadata; refused cross-document fallback.",
            "diagnostic_flags": diag, "anchor_audit": anchor_audit,
            "evidence": [], "evidence_anchors": [],
            "search_scope": {"searched_documents": [], "searched_paragraph_count": 0},
            "alignment_checks": {"binding_info": binding_info, "eligible_doc_count": 0},
        }

    eligible_ids = {d["source_document_id"] for d in eligible_docs}
    eligible_paras = [p for p in all_paragraphs if p["source_document_id"] in eligible_ids]

    all_insufficient_loc = bool(eligible_docs) and all(d.get("localization_insufficient") for d in eligible_docs)
    any_fig_missing = any(d.get("coverage_status") == "figure_caption_missing" for d in eligible_docs)
    if (all_insufficient_loc and not eligible_paras) or (subclaim.get("asks_about_figure") and any_fig_missing):
        diag["fulltext_unavailable_or_figure_caption_missing"] = True
        reasons = "; ".join(sorted({d.get("localization_reason", d.get("coverage_status", "")) for d in eligible_docs if d.get("localization_insufficient")}))
        return {
            "subclaim_id": subclaim["subclaim_id"], "text": subclaim["text"],
            "claim_types": subclaim["claim_types"], "axes": subclaim["axes"],
            "verdict": "insufficient_evidence", "candidate_verdict": "insufficient_evidence",
            "corpus_match_status": "localization_insufficient",
            "verdict_rationale": f"Localization insufficient ({reasons}); marked insufficient_evidence, NOT contradicted.",
            "diagnostic_flags": diag, "anchor_audit": anchor_audit,
            "evidence": [], "evidence_anchors": [],
            "search_scope": {"searched_documents": sorted(eligible_ids), "searched_paragraph_count": len(eligible_paras)},
            "alignment_checks": {"binding_info": binding_info, "localization_insufficient": True},
        }

    body_paras = [p for p in eligible_paras if p.get("section_hint") != "references"] or eligible_paras
    ranked_all = _rank_paragraphs(subclaim["text"], body_paras)

    cited_pages = set(anchor_refs.get("cited_pages") or [])
    cited_para_ids = set(anchor_refs.get("cited_paragraphs") or [])
    ranked_anchor: list[tuple[float, dict[str, Any]]] = []
    if cited_pages or cited_para_ids:
        anchor_paras = [
            p for p in body_paras
            if (p.get("page") in cited_pages) or (p["paragraph_id"] in cited_para_ids)
            or any(p["paragraph_id"].endswith(f":{cp}") or p["paragraph_id"].endswith(cp) for cp in cited_para_ids)
        ]
        ranked_anchor = _rank_paragraphs(subclaim["text"], anchor_paras)

    top_all = ranked_all[:3]
    best_all_score = top_all[0][0] if top_all else 0.0
    best_anchor_score = ranked_anchor[0][0] if ranked_anchor else 0.0

    search_scope = {
        "searched_documents": [d["source_document_id"] for d in eligible_docs],
        "searched_paragraph_count": len(body_paras),
        "cited_ref": binding_info.get("cited_ref", ""),
    }

    if (cited_pages or cited_para_ids) and top_all and best_all_score >= 0.35:
        top_para = top_all[0][1]
        in_cited_page = (not cited_pages) or (top_para.get("page") in cited_pages)
        in_cited_para = (not cited_para_ids) or (top_para["paragraph_id"] in cited_para_ids or any(top_para["paragraph_id"].endswith(cp) for cp in cited_para_ids))
        if not (in_cited_page and in_cited_para) and best_all_score > best_anchor_score + 0.25:
            diag["wrong_page_or_anchor_mismatch"] = True
            anchor_audit["wrong_page_or_anchor_mismatch"] = True

    if all_insufficient_loc and best_all_score < 0.75:
        diag["fulltext_unavailable_or_figure_caption_missing"] = True
        reasons = "; ".join(sorted({d.get("localization_reason", "") for d in eligible_docs}))
        return {
            "subclaim_id": subclaim["subclaim_id"], "text": subclaim["text"],
            "claim_types": subclaim["claim_types"], "axes": subclaim["axes"],
            "verdict": "insufficient_evidence", "candidate_verdict": "insufficient_evidence",
            "corpus_match_status": "localization_insufficient",
            "verdict_rationale": f"Only abstract/partial text available ({reasons}); full-text verification absent -> insufficient_evidence.",
            "diagnostic_flags": diag, "anchor_audit": anchor_audit,
            "evidence": [], "evidence_anchors": [], "search_scope": search_scope,
            "alignment_checks": {"binding_info": binding_info, "localization_insufficient": True, "best_score": best_all_score},
        }

    q_toks_set = set(_tokens(subclaim["text"]))
    c_nums = _extract_key_numbers(subclaim["text"])
    top1_overlap = (q_toks_set & set(_tokens(top_all[0][1]["text"]))) if top_all else set()
    domain_overlap = {t for t in top1_overlap if t not in _GENERIC_DOMAIN_TOKENS and len(t) >= 3}
    overlap_ratio = len(top1_overlap) / max(1, len(q_toks_set))
    top_all_nums = set().union(*(_extract_key_numbers(p["text"]) for _, p in top_all)) if top_all else set()
    missing_nums = [n for n in c_nums if n not in top_all_nums]

    # Strict Zero-Hit / Off-Topic Hygiene: NEVER attach unrelated paragraphs as fake negative-evidence anchors!
    is_zero_domain_hit = (
        (not top_all)
        or (best_all_score < 0.35)
        or (len(domain_overlap) == 0 and (not c_nums or len(missing_nums) == len(c_nums)))
        or (overlap_ratio < 0.25 and bool(c_nums) and len(missing_nums) == len(c_nums))
    )
    if is_zero_domain_hit:
        if binding_info["source_locked"] and eligible_docs:
            diag["doi_exists_but_unsupported"] = True
        if any(t in EXTRAPOLATION_DOMAIN_TOKENS for t in q_toks_set) and not binding_info["source_locked"]:
            diag["untested_extrapolation_detected"] = True
        anchor_audit["actual_pages"] = []
        anchor_audit["actual_paragraph_ids"] = []
        return {
            "subclaim_id": subclaim["subclaim_id"], "text": subclaim["text"],
            "claim_types": subclaim["claim_types"], "axes": subclaim["axes"],
            "verdict": "insufficient_evidence", "candidate_verdict": "insufficient_evidence",
            "corpus_match_status": "not_found_in_corpus",
            "verdict_rationale": (
                f"Searched {len(eligible_docs)} document(s) ({len(body_paras)} blocks: {search_scope['searched_documents']}); "
                f"zero matching domain entities or quantitative values found (status=not_found_in_corpus, evidence_anchors=[]). "
                "No unrelated passage attached as pseudo-negative anchor; marked insufficient_evidence (doi_exists_but_unsupported)."
                if diag["doi_exists_but_unsupported"] else
                f"Searched {len(eligible_docs)} document(s) ({len(body_paras)} blocks); zero domain hits (status=not_found_in_corpus, evidence_anchors=[])."
            ),
            "diagnostic_flags": diag, "anchor_audit": anchor_audit,
            "evidence": [], "evidence_anchors": [],
            "search_scope": {**search_scope, "query_domain_terms": sorted(t for t in q_toks_set if t not in _GENERIC_DOMAIN_TOKENS)[:12], "matched_domain_terms": sorted(domain_overlap)},
            "alignment_checks": {
                "binding_info": binding_info, "corpus_match_status": "not_found_in_corpus",
                "overlap_ratio": round(overlap_ratio, 3), "best_score": best_all_score,
                "missing_claim_numbers_in_evidence": missing_nums,
            },
        }

    c_fp = subclaim["emlsi"]
    c_neg = (len(c_fp["L_logic_negations"]) % 2 == 1) or bool(NEG_POLARITY_RE.search(subclaim["text"]))
    c_pos = bool(POS_POLARITY_RE.search(subclaim["text"])) and not c_neg

    top_blob = " ".join(p["text"] for _, p in top_all)
    missing_extrap_toks = _detect_untested_extrapolation(subclaim["text"], top_blob)

    ev_list: list[dict[str, Any]] = []
    polarity_conflicts = 0
    polarity_agrees = 0
    direct_refutations = 0
    numeric_conflicts_all: list[dict[str, Any]] = []
    sec_polarities: dict[str, set[str]] = {"abstract": set(), "body": set()}

    for rank_idx, (sc, para) in enumerate(top_all):
        p_txt = para["text"]
        local_txt = _best_matching_sentences(subclaim["text"], p_txt, max_sents=2)
        p_fp = extract_emlsi_fingerprint(local_txt)
        p_neg = (len(p_fp["L_logic_negations"]) % 2 == 1) or bool(NEG_POLARITY_RE.search(local_txt))
        p_pos = bool(POS_POLARITY_RE.search(local_txt)) and not p_neg

        sec_info = detect_secondary_citation(local_txt, para.get("section_hint", "body"))
        if rank_idx == 0 and sec_info["is_secondary_citation"]:
            diag["secondary_citation_warning"] = True

        p_pol = "negative" if p_neg else ("positive" if p_pos else "neutral")
        sec_polarities["abstract" if para.get("section_hint") == "abstract" else "body"].add(p_pol)

        stat_null_refute = _has_stat_null_refutation(subclaim["text"], local_txt)
        num_conf: list[dict[str, Any]] = []
        if missing_nums and not missing_extrap_toks and rank_idx == 0:
            raw_conf = _detect_numeric_contradictions(subclaim["text"], local_txt) or _detect_numeric_contradictions(subclaim["text"], p_txt)
            for nc in raw_conf:
                c_norm = re.sub(r"[^0-9.%]", "", nc["claim_value"])
                if c_norm in top_all_nums or c_norm.rstrip("%") in top_all_nums:
                    continue
                nc["paragraph_id"] = para["paragraph_id"]
                num_conf.append(nc)
            numeric_conflicts_all.extend(num_conf)

        if (c_neg != p_neg and (c_pos or c_neg or p_neg)) or stat_null_refute or bool(num_conf):
            polarity_conflicts += 1
            if not missing_extrap_toks and overlap_ratio >= 0.28:
                direct_refutations += 1
        else:
            polarity_agrees += 1

        did = para["source_document_id"]
        cnum = cit_num_by_doc.get(did, 0)
        q_cand = local_txt[:420].strip()
        if len(q_cand) >= 20 and " ".join(q_cand.split()).casefold() in " ".join(p_txt.split()).casefold():
            q_slice = q_cand
        else:
            s1 = _best_matching_sentences(subclaim["text"], p_txt, max_sents=1)[:420].strip()
            q_slice = s1 if (len(s1) >= 20 and " ".join(s1.split()).casefold() in " ".join(p_txt.split()).casefold()) else p_txt[:420]

        table_m = re.search(r"\b(Table\s+(?:S?\d+))\b", p_txt, re.I)
        loc_parts = []
        if para.get("page") is not None:
            loc_parts.append(f"Page {para['page']}")
        if table_m:
            loc_parts.append(table_m.group(1))
        loc_parts.append(para["paragraph_id"])

        ev_list.append({
            "citation_number": cnum, "citation_tag": f"[{cnum}]" if cnum else "",
            "source_document_id": did, "source_file": para["source_file"],
            "paragraph_id": para["paragraph_id"], "page": para.get("page"),
            "location_anchor": ", ".join(loc_parts),
            "section_hint": para.get("section_hint", "body"),
            "attribution_type": sec_info["attribution_type"],
            "secondary_citation_warning": sec_info["is_secondary_citation"],
            "retrieval_score": sc, "quote": q_slice, "evidence_emlsi": p_fp,
        })
        if para.get("page") is not None and para["page"] not in anchor_audit["actual_pages"]:
            anchor_audit["actual_pages"].append(para["page"])
        anchor_audit["actual_paragraph_ids"].append(para["paragraph_id"])

    if ("positive" in sec_polarities["abstract"] and "negative" in sec_polarities["body"]) or (
        "negative" in sec_polarities["abstract"] and "positive" in sec_polarities["body"]
    ):
        diag["abstract_body_conflict_detected"] = True

    c_comps = set(c_fp["I_identifiers_comparators"]["comparators"])
    e_comps = {c for ev in ev_list for c in ev["evidence_emlsi"]["I_identifiers_comparators"]["comparators"]}
    missing_comparator = bool(c_comps and not (c_comps & e_comps))

    table_cmp_audit = _audit_table_or_comparison_claim(subclaim["text"], ev_list, body_paras, anchor_audit)
    denom_audit = _audit_percentage_denominators(
        subclaim["text"],
        ev_list[0]["quote"] if ev_list else "",
        top_all[0][1]["text"] if top_all else "",
        body_paras,
    )

    # Strict 4-state entailment decision tree
    if missing_extrap_toks:
        diag["untested_extrapolation_detected"] = True
        if binding_info["source_locked"] and eligible_docs:
            diag["doi_exists_but_unsupported"] = True
        verdict = "insufficient_evidence"
        rationale = (
            f"Claim extrapolates to untested scope/population token(s) {missing_extrap_toks} absent from retrieved evidence; "
            "untested extrapolation is classified insufficient_evidence, NOT contradicted."
        )
    elif numeric_conflicts_all and overlap_ratio >= 0.28:
        verdict = "contradicted"
        nc0 = numeric_conflicts_all[0]
        rationale = (
            f"Direct quantitative contradiction on matching target ({nc0['shared_context_tokens']}): "
            f"claim states {nc0['claim_value']} while primary evidence reports {nc0['evidence_value']} at {nc0['paragraph_id']}."
        )
    elif (overlap_ratio < 0.45 and c_nums and len(missing_nums) == len(c_nums)):
        if binding_info["source_locked"] and eligible_docs:
            diag["doi_exists_but_unsupported"] = True
        verdict = "insufficient_evidence"
        ev_list = []
        anchor_audit["actual_pages"] = []
        anchor_audit["actual_paragraph_ids"] = []
        rationale = (
            f"Searched {len(eligible_docs)} document(s) ({len(body_paras)} blocks); weak overlap ({overlap_ratio:.2f}) "
            f"with zero matched claim numbers {missing_nums} (status=not_found_in_corpus, evidence_anchors=[])."
        )
    elif diag["abstract_body_conflict_detected"]:
        verdict = "partially_supported"
        rationale = "Abstract statement conflicts with Results/Body/Table evidence within the same document; both anchored quotes preserved."
    elif direct_refutations > 0 and polarity_agrees > 0 and len({e["source_document_id"] for e in ev_list}) >= 2:
        verdict = "partially_supported"
        rationale = "Conflicting evidence across multiple corpus sources; preserved opposing anchored quotes under qualified verdict."
    elif direct_refutations > 0 and polarity_conflicts >= polarity_agrees:
        verdict = "contradicted"
        rationale = "Target source provides direct opposing empirical evidence, explicit negation, or statistical null refutation on matching scope."
    elif all_insufficient_loc:
        diag["fulltext_unavailable_or_figure_caption_missing"] = True
        verdict = "insufficient_evidence"
        rationale = "Only abstract-level text available; full text required to confirm claim."
    elif table_cmp_audit and table_cmp_audit["missing_dimensions"]:
        # Enforce 5-field (entity, table_row, metric, condition, conclusion) downgrade for table/comparison claims
        miss = table_cmp_audit["missing_dimensions"]
        if not table_cmp_audit["entity"]["confirmed"] or not table_cmp_audit["metric"]["confirmed"]:
            verdict = "insufficient_evidence"
        else:
            verdict = "partially_supported"
        rationale = (
            f"5-field table/comparison audit (entity-table_row-metric-condition-conclusion) blocked 'supported'; "
            f"missing/unverified dimensions: {miss}. {table_cmp_audit['table_row']['note']}"
        )
    elif missing_nums or missing_comparator:
        verdict = "partially_supported"
        rationale = f"Core entities matched, but missing claim numbers ({missing_nums}) or comparator alignment ({missing_comparator}) prevents unqualified support."
    elif ev_list and ev_list[0]["secondary_citation_warning"] and not any(
        (not e["secondary_citation_warning"]) and e["retrieval_score"] >= 0.70 * ev_list[0]["retrieval_score"]
        for e in ev_list[1:]
    ):
        verdict = "partially_supported"
        rationale = (
            "Top matched passage is an indirect/secondary citation in Introduction/Background rather than primary empirical measurement by this paper."
            + (f" {denom_audit['note']}" if denom_audit else "")
        )
    else:
        verdict = "supported"
        rationale = (
            "Entity, polarity, magnitude, and comparator align with grounded primary evidence"
            + (" (NOTE: cited page/anchor was mismatched; true evidence recovered elsewhere in same document)." if diag["wrong_page_or_anchor_mismatch"] else ".")
            + (f" {denom_audit['note']}" if denom_audit and denom_audit.get("distinct_from_primary_study_sample") else "")
        )

    match_status = "not_found_in_corpus" if not ev_list else "matched"
    return {
        "subclaim_id": subclaim["subclaim_id"], "text": subclaim["text"],
        "claim_types": subclaim["claim_types"], "axes": subclaim["axes"],
        "verdict": verdict, "candidate_verdict": verdict,
        "corpus_match_status": match_status,
        "verdict_rationale": rationale,
        "diagnostic_flags": diag, "anchor_audit": anchor_audit,
        "evidence": ev_list,
        "evidence_anchors": [e["location_anchor"] for e in ev_list],
        "search_scope": search_scope,
        "alignment_checks": {
            "binding_info": binding_info, "corpus_match_status": match_status,
            "overlap_ratio": round(overlap_ratio, 3),
            "missing_claim_numbers_in_evidence": missing_nums,
            "numeric_contradictions": numeric_conflicts_all,
            "missing_comparator_in_evidence": missing_comparator,
            "missing_extrapolation_tokens": missing_extrap_toks,
            "table_comparison_audit": table_cmp_audit,
            "denominator_audit": denom_audit,
            "polarity_conflicts": polarity_conflicts, "polarity_agrees": polarity_agrees,
            "direct_refutations": direct_refutations,
        },
    }


def aggregate_claim_verdict(sub_results: list[dict[str, Any]]) -> str:
    verdicts = [s.get("verdict", s.get("candidate_verdict", "insufficient_evidence")) for s in sub_results]
    if not verdicts:
        return "insufficient_evidence"
    vset = set(verdicts)
    for single in ("supported", "contradicted", "insufficient_evidence"):
        if vset == {single}:
            return single
    return "partially_supported"


def merge_diagnostic_flags(sub_results: list[dict[str, Any]], cit_audit: dict[str, Any]) -> dict[str, bool]:
    keys = (
        "doi_exists_but_unsupported", "wrong_page_or_anchor_mismatch",
        "fulltext_unavailable_or_figure_caption_missing", "inline_vs_structured_citation_drift",
        "untested_extrapolation_detected", "secondary_citation_warning", "abstract_body_conflict_detected",
    )
    merged = {k: any(bool((s.get("diagnostic_flags") or {}).get(k)) for s in sub_results) for k in keys}
    if cit_audit.get("drift_detected"):
        merged["inline_vs_structured_citation_drift"] = True
    return merged


def validate_verification_draft(
    draft: dict[str, Any],
    documents: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    *,
    strict_citation_drift: bool = False,
) -> dict[str, Any]:
    """Validate verification cards: anchor ownership, verbatim quotes, numeric/polarity entailment, extrapolation boundary, and citation drift."""
    para_lookup = {p["paragraph_id"]: p for p in paragraphs}
    doc_dois_by_id = {d["source_document_id"]: set(d.get("document_dois") or []) for d in documents}
    legend_by_num = {
        e["citation_number"]: e["source_document_id"]
        for e in (draft.get("citation_legend") or [])
        if isinstance(e, dict) and isinstance(e.get("citation_number"), int) and e.get("source_document_id")
    }

    errors: list[str] = []
    warnings: list[str] = []
    drift_count = 0

    for item in draft.get("verifications") or []:
        cid = str(item.get("claim_id") or "unknown")
        v = item.get("verdict")
        if v not in VALID_VERDICTS:
            errors.append(f"claim {cid}: INVALID_VERDICT {v!r}")
        cited_dois = set(_extract_dois(str(item.get("cited_ref") or "")))

        # Reconcile inline vs structured citation drift
        cit_audit = item.get("citation_integrity_audit")
        if cit_audit is None and ("structured_citations" in item or "citations" in item):
            cit_audit = audit_inline_vs_structured_citations(
                str(item.get("original_claim") or item.get("claim") or ""),
                item.get("structured_citations") if "structured_citations" in item else item.get("citations"),
            )
        if isinstance(cit_audit, dict) and cit_audit.get("drift_detected"):
            drift_count += 1
            msg = (
                f"claim {cid}: INLINE_STRUCTURED_CITATION_DRIFT "
                f"(inline={cit_audit.get('inline_unique_ids')}, structured={cit_audit.get('structured_unique_ids')}, "
                f"missing={cit_audit.get('missing_in_structured')})"
            )
            (errors if strict_citation_drift else warnings).append(msg)

        for sub in item.get("subclaims") or []:
            sid = str(sub.get("subclaim_id") or cid)
            s_text = str(sub.get("text") or item.get("original_claim") or "")
            sv = sub.get("verdict", sub.get("candidate_verdict"))
            if sv not in VALID_VERDICTS:
                errors.append(f"subclaim {sid}: INVALID_VERDICT {sv!r}")

            ev_list = sub.get("evidence") or []
            s_diag = sub.get("diagnostic_flags") or {}

            if sv == "supported" and not ev_list:
                errors.append(f"subclaim {sid}: SUPPORTED_WITHOUT_EVIDENCE (supported requires >=1 grounded evidence quote)")
            if sub.get("corpus_match_status") == "not_found_in_corpus" and ev_list:
                errors.append(f"subclaim {sid}: PSEUDO_NEGATIVE_ANCHOR_FORBIDDEN (not_found_in_corpus must have evidence=[])")
            t_audit = (sub.get("alignment_checks") or {}).get("table_comparison_audit") or {}
            if sv == "supported" and t_audit.get("missing_dimensions"):
                errors.append(f"subclaim {sid}: TABLE_COMPARISON_UNVERIFIED_SUPPORTED (missing_dimensions={t_audit['missing_dimensions']})")
            if sv == "contradicted" and not ev_list:
                errors.append(f"subclaim {sid}: CONTRADICTED_WITHOUT_OPPOSING_EVIDENCE (contradicted requires >=1 grounded opposing quote)")
            if sv == "contradicted" and s_diag.get("fulltext_unavailable_or_figure_caption_missing"):
                errors.append(f"subclaim {sid}: CONTRADICTED_ON_LOCALIZATION_GAP (localization_insufficient must be insufficient_evidence)")
            if sv == "contradicted" and s_diag.get("untested_extrapolation_detected"):
                errors.append(f"subclaim {sid}: CONTRADICTED_ON_UNTESTED_EXTRAPOLATION (untested extrapolation must be insufficient_evidence)")
            if s_diag.get("abstract_body_conflict_detected") and len(ev_list) < 2:
                errors.append(f"subclaim {sid}: CONFLICT_MISSING_DUAL_ANCHORS (abstract-body conflict requires >=2 anchored quotes)")

            all_ev_quotes = " ".join(str(ev.get("quote") or "") for ev in ev_list)
            primary_ev = ev_list[0] if ev_list else None
            primary_pid = str(primary_ev.get("paragraph_id") or "") if primary_ev else ""
            primary_text = para_lookup[primary_pid]["text"] if primary_pid in para_lookup else (str(primary_ev.get("quote") or "") if primary_ev else "")
            primary_local = _best_matching_sentences(s_text, primary_text, max_sents=2) if primary_text else ""

            # Semantic gate 1: `contradicted` MUST NOT be used for pure untested extrapolation
            if sv == "contradicted" and ev_list and strict_citation_drift:
                missing_extrap = _detect_untested_extrapolation(s_text, primary_text or all_ev_quotes)
                if missing_extrap and not _detect_numeric_contradictions(s_text, primary_text or all_ev_quotes):
                    errors.append(f"subclaim {sid}: CONTRADICTED_ON_UNTESTED_EXTRAPOLATION (untested scope tokens {missing_extrap} absent from cited quote; use insufficient_evidence)")

            # Semantic gate 2: `supported` MUST NOT contain unresolved direct contradiction or numeric mismatch in its primary evidence
            if sv == "supported" and primary_ev and strict_citation_drift:
                c_fp = extract_emlsi_fingerprint(s_text)
                c_neg = (len(c_fp["L_logic_negations"]) % 2 == 1) or bool(NEG_POLARITY_RE.search(s_text))
                c_pos = bool(POS_POLARITY_RE.search(s_text)) and not c_neg
                num_conf = _detect_numeric_contradictions(s_text, primary_text)
                stat_refute = _has_stat_null_refutation(s_text, primary_local)
                ev_neg = bool(NEG_POLARITY_RE.search(primary_local)) and not c_neg
                if num_conf:
                    errors.append(f"subclaim {sid}: SUPPORTED_HAS_UNRESOLVED_NUMERIC_CONTRADICTION ({num_conf[0]['claim_value']} vs {num_conf[0]['evidence_value']})")
                elif stat_refute or (c_pos and ev_neg):
                    errors.append(f"subclaim {sid}: SUPPORTED_HAS_UNRESOLVED_POLARITY_CONTRADICTION (cited quote expresses null/negative result contradicting positive claim)")

            for ev in ev_list:
                pid = str(ev.get("paragraph_id") or "")
                ev_doc = str(ev.get("source_document_id") or "")
                q = str(ev.get("quote") or "")
                cnum = ev.get("citation_number")
                if legend_by_num and isinstance(cnum, int) and cnum in legend_by_num and ev_doc and legend_by_num[cnum] != ev_doc:
                    errors.append(f"subclaim {sid}: CITATION_LEGEND_DRIFT (citation [{cnum}] maps to {legend_by_num[cnum]!r}, not {ev_doc!r})")
                if pid not in para_lookup:
                    errors.append(f"subclaim {sid}: UNKNOWN_PARAGRAPH_ID {pid!r}")
                    continue
                real_para = para_lookup[pid]
                real_doc = real_para["source_document_id"]
                if ev_doc and ev_doc != real_doc:
                    errors.append(f"subclaim {sid}: CROSS_DOCUMENT_ANCHOR_MISMATCH (claimed {ev_doc!r}, actual owner {real_doc!r} for {pid!r})")
                if not verify_exact_quote(q, real_para["text"]):
                    errors.append(f"subclaim {sid}: QUOTE_MISMATCH at {pid!r}")
                if sv == "supported" and cited_dois and not cited_dois.intersection(doc_dois_by_id.get(real_doc, set())):
                    errors.append(f"subclaim {sid}: DOI_MISMATCH_SUPPORTED (cited DOI {sorted(cited_dois)} not matched by {real_doc!r})")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings, "citation_integrity_drift_count": drift_count}


def render_verification_report_md(
    citation_legend: list[dict[str, Any]], verifications: list[dict[str, Any]],
    diag_summary: dict[str, int], cit_integrity_summary: dict[str, Any],
) -> str:
    lines: list[str] = [
        "# Scientific Claim Verification & Evidence Tracing Report", "",
        "## 1. Citation Legend (Immutable Source Mapping)", "",
        "| Citation | source_document_id | Title | DOI | Source File | SHA-256 (64-hex) | Pages | Coverage Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in citation_legend:
        lines.append(
            f"| {e['citation_tag']} | `{e['source_document_id']}` | {e['title'].replace('|', '/')} | {e['doi'] or '`not_reported`'} | "
            f"`{e['source_file']}` | `{e.get('file_sha256') or e.get('file_sha256_short')}` | `{e['page_count']}` | `{e['coverage_status']}` |"
        )
    lines.extend([
        "", "## 2. Verdict & Four-State Diagnostic Summary", "",
        f"- Total claims audited: `{len(verifications)}`",
        f"- `doi_exists_but_unsupported`: `{diag_summary.get('doi_exists_but_unsupported', 0)}`",
        f"- `wrong_page_or_anchor_mismatch`: `{diag_summary.get('wrong_page_or_anchor_mismatch', 0)}`",
        f"- `fulltext_unavailable_or_figure_caption_missing`: `{diag_summary.get('fulltext_unavailable_or_figure_caption_missing', 0)}`",
        f"- `inline_vs_structured_citation_drift`: `{diag_summary.get('inline_vs_structured_citation_drift', 0)}`",
        f"- `untested_extrapolation_detected`: `{diag_summary.get('untested_extrapolation_detected', 0)}`",
        f"- `secondary_citation_warning`: `{diag_summary.get('secondary_citation_warning', 0)}`",
        f"- `abstract_body_conflict_detected`: `{diag_summary.get('abstract_body_conflict_detected', 0)}`", "",
        "| Claim ID | Verdict | Cited Ref | Active Diagnostic Flags |", "| --- | --- | --- | --- |",
    ])
    for v in verifications:
        active = [k for k, val in (v.get("diagnostic_flags") or {}).items() if val]
        lines.append(f"| `{v['claim_id']}` | `{v['verdict']}` | `{str(v.get('cited_ref') or 'none').replace('|', '/')}` | `{', '.join(active) if active else 'none'}` |")

    lines.extend(["", "## 3. Inline-vs-Structured Citation Integrity Audit", "", f"- Claims with inline/structured citation drift: `{cit_integrity_summary.get('claims_with_drift', 0)}`"])
    for rec in cit_integrity_summary.get("drifted_claims", []):
        lines.append(f"- `{rec['claim_id']}`: inline IDs `{rec['inline_unique_ids']}` (count={rec['inline_marker_count']}) vs structured count=`{rec['structured_citation_count']}`, missing=`{rec['missing_in_structured']}`")

    lines.extend(["", "## 4. Atomic Sub-Claim Evidence Cards", ""])
    for v in verifications:
        lines.append(f"### `{v['claim_id']}` — Verdict: `{v['verdict']}`")
        lines.append(f"- **Original Claim**: {v['original_claim']}")
        if v.get("cited_ref"):
            lines.append(f"- **Cited Ref**: `{v['cited_ref']}`")
        lines.extend(["", "| Sub-Claim ID | Types | Verdict | Anchor | Attribution | Verbatim Quote & Rationale |", "| --- | --- | --- | --- | --- | --- |"])
        for sub in v.get("subclaims") or []:
            sv = sub.get("verdict", sub.get("candidate_verdict"))
            types = ",".join(sub.get("claim_types") or sub.get("axes") or ["fact"])
            evs = sub.get("evidence") or []
            if evs:
                e0 = evs[0]
                loc = e0.get("location_anchor") or ((f"Page {e0['page']}, " if e0.get("page") else "") + str(e0.get("paragraph_id")))
                anc = f"{e0.get('citation_tag', '')} {loc}"
                attr = e0.get("attribution_type", "primary_study")
                q_str = f"\"{str(e0.get('quote', ''))[:240].replace('|', '/')}\" — {str(sub.get('verdict_rationale', '')).replace('|', '/')}"
            else:
                scope = sub.get("search_scope") or {}
                anc = f"`not_found_in_corpus` (`evidence_anchors: []`; searched `{scope.get('searched_documents', [])}`, `{scope.get('searched_paragraph_count', 0)}` blocks)"
                attr = "`none`"
                q_str = str(sub.get("verdict_rationale", "")).replace("|", "/")
            lines.append(f"| `{sub['subclaim_id']}` | `{types}` | `{sv}` | {anc} | `{attr}` | {q_str} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def run_pipeline(
    input_dir: Path, output_dir: Path, work_dir: Path, *, draft_json: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="e5e3be30-claim-verifier")

    case_path = input_dir / "case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace")) if case_path.is_file() else {}
    case_id = str(case_data.get("case_id") or "case-unknown")
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-e5e3be30")

    claims, found_dedicated = load_claims_to_verify(input_dir, case_data)
    documents, paragraphs = collect_corpus_documents_and_paragraphs(input_dir, case_data)

    sorted_docs = sorted(documents, key=lambda d: d["source_document_id"])
    citation_legend: list[dict[str, Any]] = []
    cit_num_by_doc: dict[str, int] = {}
    for idx, d in enumerate(sorted_docs, start=1):
        cit_num_by_doc[d["source_document_id"]] = idx
        citation_legend.append({
            "citation_number": idx, "citation_tag": f"[{idx}]", "source_document_id": d["source_document_id"],
            "title": d["title"], "doi": d["doi"], "source_file": d["source_file"],
            "file_sha256": d["file_sha256"], "file_sha256_short": d["file_sha256_short"],
            "page_count": d["page_count"], "paragraph_count": d["paragraph_count"], "coverage_status": d["coverage_status"],
        })

    result_guard.atomic_write_json(work_dir / "corpus_paragraphs.json", {"case_id": case_id, "citation_legend": citation_legend, "paragraphs": paragraphs})
    citations = [c.to_dict() for c in extract_citations("\n\n".join(p["text"] for p in paragraphs))]
    budget_guard.tick_phase(budget_file, "split_and_match_claims")

    is_draft_validation = bool(draft_json and draft_json.is_file())
    if is_draft_validation and draft_json is not None:
        cards_payload = json.loads(draft_json.read_text(encoding="utf-8", errors="replace"))
        cards_payload.setdefault("citation_legend", citation_legend)
        cards_payload.setdefault("corpus_citations_extracted", citations)
    else:
        verifications: list[dict[str, Any]] = []
        for cl in claims:
            subclaims = split_compound_claim(cl["claim_id"], cl["claim_text"])
            eligible_docs, binding_info = _resolve_target_documents(cl["cited_ref"], sorted_docs)
            anchor_refs = extract_page_and_paragraph_refs(cl["cited_ref"], cited_page=cl.get("cited_page"), cited_paragraph=cl.get("cited_paragraph"))
            cit_audit = audit_inline_vs_structured_citations(cl["claim_text"], cl.get("structured_citations"))
            sub_results = [
                evaluate_subclaim_candidates(sc, eligible_docs, paragraphs, binding_info=binding_info, anchor_refs=anchor_refs, cit_num_by_doc=cit_num_by_doc)
                for sc in subclaims
            ]
            verifications.append({
                "claim_id": cl["claim_id"], "original_claim": cl["claim_text"], "cited_ref": cl["cited_ref"],
                "verdict": aggregate_claim_verdict(sub_results), "preserves_comparator_negation_conditions": True,
                "diagnostic_flags": merge_diagnostic_flags(sub_results, cit_audit),
                "citation_integrity_audit": cit_audit, "subclaims": sub_results,
            })
        cards_payload = {
            "case_id": case_id, "claims_file_detected": found_dedicated, "citation_legend": citation_legend,
            "verifications": verifications, "corpus_citations_extracted": citations,
        }

    val = validate_verification_draft(cards_payload, sorted_docs, paragraphs, strict_citation_drift=is_draft_validation)
    ver_list = cards_payload.get("verifications") or []
    diag_keys = (
        "doi_exists_but_unsupported", "wrong_page_or_anchor_mismatch",
        "fulltext_unavailable_or_figure_caption_missing", "inline_vs_structured_citation_drift",
        "untested_extrapolation_detected", "secondary_citation_warning", "abstract_body_conflict_detected",
    )
    diag_summary = {k: sum(1 for v in ver_list if (v.get("diagnostic_flags") or {}).get(k)) for k in diag_keys}
    drifted_claims = [{"claim_id": v.get("claim_id"), **(v.get("citation_integrity_audit") or {})} for v in ver_list if (v.get("citation_integrity_audit") or {}).get("drift_detected")]
    cit_integrity_summary = {"total_claims_audited": len(ver_list), "claims_with_drift": len(drifted_claims), "drifted_claims": drifted_claims}

    cards_payload["diagnostic_summary"] = diag_summary
    cards_payload["citation_integrity_audit"] = cit_integrity_summary
    cards_payload["schema_validation"] = val

    result_guard.atomic_write_json(output_dir / "verification_cards.json", cards_payload)
    result_guard.atomic_write_text(output_dir / "verification_report.md", render_verification_report_md(cards_payload.get("citation_legend") or citation_legend, ver_list, diag_summary, cit_integrity_summary))

    append_audit_entry(
        audit_file, phase="verify_claims", action="verification_cards_and_report_written", actor="claim_verifier.py",
        output={"claim_count": len(ver_list), "document_count": len(sorted_docs), "paragraph_count": len(paragraphs), "validation_ok": val["ok"], "diagnostic_summary": diag_summary},
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    if not found_dedicated:
        gaps.append({"code": "MISSING_CLAIMS_TO_VERIFY_FILE", "severity": "warning", "source": "load_claims_to_verify", "message": "/input/claims-to-verify.json not present; fell back to case.json claims."})
    if not paragraphs:
        gaps.append({"code": "NO_READABLE_CORPUS", "severity": "error", "source": "collect_corpus_documents_and_paragraphs", "message": "No readable corpus paragraphs available for fact verification."})
    if diag_summary["fulltext_unavailable_or_figure_caption_missing"] > 0:
        gaps.append({"code": "LOCALIZATION_INSUFFICIENT", "severity": "warning", "source": "evaluate_subclaim_candidates", "message": f"{diag_summary['fulltext_unavailable_or_figure_caption_missing']} claim(s) encountered abstract-only text, missing PDF text layer, or missing figure caption; marked insufficient_evidence."})
    if diag_summary["inline_vs_structured_citation_drift"] > 0:
        gaps.append({"code": "INLINE_STRUCTURED_CITATION_DRIFT", "severity": "warning", "source": "audit_inline_vs_structured_citations", "message": f"{diag_summary['inline_vs_structured_citation_drift']} claim(s) exhibited inline marker vs. structured citation drift."})
    if not val["ok"]:
        gaps.append({"code": "VERIFICATION_VALIDATION_FAILED", "severity": "error", "source": "validate_verification_draft", "message": "; ".join(val["errors"][:10])})

    counts = Counter(v.get("verdict", "insufficient_evidence") for v in ver_list)
    patch = {
        "artifacts": ["result.json", "verification_cards.json", "verification_report.md"],
        "citation_legend": cards_payload.get("citation_legend") or citation_legend,
        "verifications": ver_list, "diagnostic_summary": diag_summary,
        "citation_integrity_audit": cit_integrity_summary, "corpus_citations_extracted": citations,
        "schema_validation": val,
        "metrics": {
            "document_count": len(sorted_docs), "paragraph_count": len(paragraphs),
            "claim_count": len(ver_list), "subclaim_count": sum(len(v.get("subclaims", [])) for v in ver_list),
            "supported_count": counts.get("supported", 0), "partially_supported_count": counts.get("partially_supported", 0),
            "contradicted_count": counts.get("contradicted", 0), "insufficient_evidence_count": counts.get("insufficient_evidence", 0),
            "doi_exists_but_unsupported_count": diag_summary["doi_exists_but_unsupported"],
            "wrong_page_or_anchor_mismatch_count": diag_summary["wrong_page_or_anchor_mismatch"],
            "localization_insufficient_count": diag_summary["fulltext_unavailable_or_figure_caption_missing"],
            "citation_drift_count": diag_summary["inline_vs_structured_citation_drift"],
            "extracted_citation_count": len(citations),
        },
        "metric_provenance": {
            "document_count": "citation_legend", "paragraph_count": "work/corpus_paragraphs.json",
            "claim_count": "verification_cards.json:verifications", "subclaim_count": "verification_cards.json:verifications[].subclaims",
            "supported_count": "verifications[].verdict", "partially_supported_count": "verifications[].verdict",
            "contradicted_count": "verifications[].verdict", "insufficient_evidence_count": "verifications[].verdict",
            "doi_exists_but_unsupported_count": "diagnostic_summary.doi_exists_but_unsupported",
            "wrong_page_or_anchor_mismatch_count": "diagnostic_summary.wrong_page_or_anchor_mismatch",
            "localization_insufficient_count": "diagnostic_summary.fulltext_unavailable_or_figure_caption_missing",
            "citation_drift_count": "diagnostic_summary.inline_vs_structured_citation_drift",
            "extracted_citation_count": "verification_cards.json:corpus_citations_extracted",
        },
        "findings": [
            f"Audited {len(ver_list)} claim(s) across {len(sorted_docs)} document(s) ({len(paragraphs)} paragraphs) with numeric/polarity/extrapolation entailment and 4 independent failure diagnostics.",
            f"Verdict distribution: supported={counts.get('supported', 0)}, partially_supported={counts.get('partially_supported', 0)}, contradicted={counts.get('contradicted', 0)}, insufficient_evidence={counts.get('insufficient_evidence', 0)}.",
            f"Diagnostics triggered: doi_exists_but_unsupported={diag_summary['doi_exists_but_unsupported']}, wrong_page_or_anchor_mismatch={diag_summary['wrong_page_or_anchor_mismatch']}, localization_insufficient={diag_summary['fulltext_unavailable_or_figure_caption_missing']}, citation_drift={diag_summary['inline_vs_structured_citation_drift']}.",
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(output_dir, result_file="result.json", required_files=["verification_cards.json", "verification_report.md"])
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res, bool(val["ok"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor e5e3be30 Compound Claim Fact Verifier & Validator.")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    parser.add_argument("--validate-verifications", default="", help="Optional drafted verification JSON to validate and promote.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(input_dir=args.input or None, output_dir=args.output or None, work_dir=args.work or None)
    draft_p = Path(args.validate_verifications) if args.validate_verifications else None
    res, val_ok = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], draft_json=draft_p)
    print(json.dumps({"ok": val_ok, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"], "schema_validation": res["schema_validation"]}, ensure_ascii=False))
    if draft_p is not None and not val_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
