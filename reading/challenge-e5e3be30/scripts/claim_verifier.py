"""Compound Claim Fact Verifier, 4-State Entailment Auditor & Citation Integrity Validator (challenge-e5e3be30).

Pure Python stdlib (zero third-party Python dependencies; uses system `pdftotext` for PDFs).
Implements Organizer Revision v2:
- Reads /input/claims-to-verify.json strictly as untrusted audit questions (never priors).
- Decomposes compound claims (EN/ZH) into atomic sub-claims typed across
  {fact, comparison, causation, scope, quantitative} with 5D E/M/L/S/I fingerprints.
- Enforces strict 4-state entailment: supported | partially_supported | contradicted | insufficient_evidence.
- Independently distinguishes 4 failure diagnostics:
  1. doi_exists_but_unsupported
  2. wrong_page_or_anchor_mismatch
  3. fulltext_unavailable_or_figure_caption_missing
  4. inline_vs_structured_citation_drift
- Separates untested extrapolation (insufficient_evidence) from direct contradiction (contradicted).
- Audits secondary citations in Introduction/Background and abstract-vs-body/table conflicts.
- Validates agent-refined drafts via `--validate-verifications` and outputs result.json,
  verification_cards.json, and verification_report.md.
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

# Split on English & Chinese compound connectives while preserving comparator/causal lead words via lookahead
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
    r"\b(?:all\s+tested|every\s+cohort|across\s+all|only\s+in|human\s+patients?|late-stage|pediatric|elderly|pregnant|in\s+vivo|in\s+vitro|mice|rats|postmarketing|premarketing|clinical\s+trial|zero\s+(?:liver\s+)?toxicity|long-term)\b|所有|全部|仅限|人体|临床患者|儿童|老年|小鼠|长期",
    re.I,
)
FIGURE_QUERY_RE = re.compile(r"\b(?:figure|fig\.?|panel|chart|plot|diagram|supplementary\s+fig)\s*\d*[A-Za-z]?\b|图\s*\d+", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.I)

EXTRAPOLATION_DOMAIN_TOKENS = {
    "human", "humans", "patient", "patients", "clinical", "pediatric", "children",
    "elderly", "pregnant", "neonatal", "late-stage", "cure", "cures", "mortality",
    "survival", "long-term", "in-vivo", "liver", "toxicity", "hepatotoxicity",
    "cardiotoxicity", "renal", "phase", "randomized", "placebo",
    "rat", "rats", "mouse", "mice", "murine", "primate", "zebrafish", "porcine", "canine", "rabbit", "rabbits",
}
POS_POLARITY_RE = re.compile(
    r"\b(?:improv(?:ed|es|ing)|increas(?:ed|es)|reduc(?:ed|es)\s+(?:tumor|risk|mortality|error|volume)|outperform(?:ed|s|ing)?|superior|effective|cures?|prolong(?:ed|s)|extend(?:ed|s)|significant(?:ly)?\s+(?:higher|better|lower\s+risk|reduced|improved)|supports?|confirms?)\b|显著提高|显著降低肿瘤|优于|有效|延长生存",
    re.I,
)
NEG_POLARITY_RE = re.compile(
    r"\b(?:no\s+significant(?:\s+difference|\s+effect|\s+benefit|\s+improvement)?|did\s+not\s+(?:improve|reduce|extend|prolong|outperform|differ)|does\s+not\s+(?:support|improve|reduce|establish|cause)|fail(?:ed|s)\s+to|inferior|worse|ineffective|higher\s+mortality|severe\s+toxicity|adverse\s+events?\s+increased|contradict(?:ed|s)|cannot\s+(?:establish|confirm|replace|prove)|not\s+recommended)\b|无显著差异|未改善|未能|劣于|无效|不能替代|严重毒性",
    re.I,
)
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over", "under",
    "were", "was", "are", "have", "has", "had", "been", "being", "their", "they",
    "these", "those", "which", "where", "when", "what", "than", "then", "also",
    "such", "both", "each", "more", "most", "very", "only", "can", "may", "will",
    "should", "could", "would", "not", "but", "all", "any", "using", "used",
    "based", "study", "paper", "report", "reported", "showed", "found", "data",
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_dois(text: str) -> list[str]:
    return sorted({match.rstrip(".,;:!?)").lower() for match in DOI_RE.findall(text or "")})


def _document_dois_from_header(text: str) -> list[str]:
    """Read ONLY explicit DOI metadata near the document header, NEVER cited-reference lists."""
    for line in (text or "").splitlines()[:30]:
        if re.match(r"^\s*(?:document\s+)?doi\s*[:=]", line, re.I) or re.search(r"doi\.org/", line, re.I):
            dois = _extract_dois(line)
            if dois:
                return sorted(set(dois))
    return []


def _document_dois_from_fields(item: dict[str, Any]) -> list[str]:
    values = [item.get(key) for key in ("doi", "DOI", "source_doi") if item.get(key)]
    return sorted({doi for value in values for doi in _extract_dois(str(value))})


def _infer_section_hint(text: str, current_section: str = "body") -> str:
    s = (text or "").strip()
    head = s[:100].lower()
    if re.match(r"^(?:#+\s*)?(?:abstract|summary|摘要)\b", head):
        return "abstract"
    if re.match(r"^(?:#+\s*)?(?:\d+\.?\s*)?(?:introduction|background|引言|背景)\b", head):
        return "introduction"
    if re.match(r"^(?:#+\s*)?(?:\d+\.?\s*)?(?:methods?|materials?\s+and\s+methods?|methodology|实验方法|方法)\b", head):
        return "methods"
    if re.match(r"^(?:#+\s*)?(?:\d+\.?\s*)?(?:results?|findings?|结果)\b", head):
        return "results"
    if re.match(r"^(?:#+\s*)?(?:\d+\.?\s*)?(?:discussion|conclusions?|limitations?|讨论|结论)\b", head):
        return "discussion"
    if re.match(r"^(?:table|figure|fig\.?)\s*\d+", head):
        return "table_or_figure"
    if re.match(r"^(?:#+\s*)?(?:references|bibliography|参考文献)\b", head):
        return "references"
    return current_section


def _extract_title(text: str, fallback: str) -> str:
    for ln in (text or "").splitlines()[:15]:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            cand = s.lstrip("#").strip()
            if len(cand) >= 4:
                return cand[:200]
        m = re.match(r"^(?:title|标题)\s*[:=]\s*(.+)$", s, re.I)
        if m:
            return m.group(1).strip()[:200]
        if len(s) >= 8 and not re.match(r"^(?:doi|author|date|page|http)\s*[:=]", s, re.I):
            return s[:200]
    return fallback


def _pdf_pages_with_pdftotext(pdf_path: Path) -> list[tuple[int, str]]:
    bin_p = shutil.which("pdftotext")
    if bin_p:
        try:
            proc = subprocess.run(
                [bin_p, str(pdf_path), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                pages = [(idx, chunk) for idx, chunk in enumerate(proc.stdout.split("\f"), start=1) if chunk.strip()]
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
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        return [(idx, p.extract_text() or "") for idx, p in enumerate(reader.pages, start=1) if (p.extract_text() or "").strip()]
    except Exception:
        pass
    return []


def _split_blocks(doc_id: str, rel: str, text: str, dois: list[str], page: int | None = None, start_section: str = "body") -> tuple[list[dict[str, Any]], str]:
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    if len(chunks) == 1 and len(chunks[0]) > 1400:
        lines = [ln.strip() for ln in chunks[0].splitlines() if ln.strip()]
        grouped: list[str] = []
        buf: list[str] = []
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
        pid = f"{doc_id}:p{page}-b{bno}" if page is not None else f"{doc_id}:b{bno}"
        entry: dict[str, Any] = {
            "paragraph_id": pid,
            "source_document_id": doc_id,
            "source_file": rel,
            "document_dois": dois,
            "section_hint": sec,
            "text": clean,
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
        doc_id: str,
        title: str,
        source_file: str,
        dois: list[str],
        doc_paras: list[dict[str, Any]],
        *,
        file_sha256: str = "",
        page_count: int = 0,
        meta: dict[str, Any] | None = None,
        raw_text: str = "",
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
            "source_document_id": doc_id,
            "title": title,
            "source_file": source_file,
            "document_dois": dois,
            "doi": dois[0] if dois else "",
            "file_sha256": file_sha256,
            "file_sha256_short": file_sha256[:12],
            "page_count": page_count,
            "paragraph_count": len(doc_paras),
            "coverage_status": loc["coverage_status"],
            "localization_insufficient": loc["localization_insufficient"],
            "localization_reason": loc["reason"],
            "metadata": meta or {},
        })
        paragraphs.extend(doc_paras)

    # 1. Documents / paragraphs embedded in case.json
    if isinstance(case_data.get("documents"), list):
        for idx, item in enumerate(case_data["documents"], start=1):
            if not isinstance(item, dict):
                continue
            did = str(item.get("source_document_id") or item.get("document_id") or item.get("id") or f"DOC{idx:02d}")
            dois = _document_dois_from_fields(item)
            raw_p = item.get("paragraphs")
            doc_paras: list[dict[str, Any]] = []
            sec = "body"
            pages_seen: set[int] = set()
            if isinstance(raw_p, list):
                for p_i, p in enumerate(raw_p, start=1):
                    pid = str(p.get("paragraph_id") or p.get("id") or f"{did}:P{p_i:03d}") if isinstance(p, dict) else f"{did}:P{p_i:03d}"
                    txt = " ".join(str(p.get("text") or p.get("content") or p).split()) if isinstance(p, dict) else " ".join(str(p).split())
                    pg = p.get("page") if isinstance(p, dict) else None
                    psec = str(p.get("section") or p.get("section_hint") or "") if isinstance(p, dict) else ""
                    sec = psec.lower() if psec else _infer_section_hint(txt, sec)
                    if txt:
                        entry = {"paragraph_id": pid, "source_document_id": did, "source_file": str(item.get("source_file") or "case.json"), "document_dois": dois, "section_hint": sec, "text": txt}
                        if isinstance(pg, int):
                            entry["page"] = pg
                            pages_seen.add(pg)
                        doc_paras.append(entry)
            elif isinstance(item.get("text"), str):
                doc_paras, _ = _split_blocks(did, str(item.get("source_file") or "case.json"), item["text"], dois)
            if not dois and doc_paras:
                dois = _document_dois_from_header("\n".join(p["text"] for p in doc_paras[:3]))
                for p in doc_paras:
                    p["document_dois"] = dois
            meta = {k: v for k, v in item.items() if k not in ("paragraphs", "text")}
            _register_doc(did, str(item.get("title") or did), str(item.get("source_file") or "case.json"), dois, doc_paras, page_count=len(pages_seen), meta=meta)

    elif isinstance(case_data.get("paragraphs"), list):
        doc_paras = []
        sec = "body"
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

    # 2. Scan files in input_dir
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
            doc_paras = []
            sec = "body"
            for pno, ptxt in pages:
                blks, sec = _split_blocks(fp.stem, rel, ptxt, dois, page=pno, start_section=sec)
                doc_paras.extend(blks)
            _register_doc(fp.stem, _extract_title(first_txt, fp.name), rel, dois, doc_paras, file_sha256=fsha, page_count=len(pages), raw_text=first_txt)
        elif ext in {".md", ".txt", ".tex", ".xml", ".html"}:
            raw = raw_bytes.decode("utf-8", errors="replace")
            dois = _document_dois_from_header(raw)
            doc_paras, _ = _split_blocks(fp.stem, rel, raw, dois)
            _register_doc(fp.stem, _extract_title(raw, fp.name), rel, dois, doc_paras, file_sha256=fsha, raw_text=raw)
        elif ext == ".csv":
            raw = raw_bytes.decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(raw))
            doc_paras = []
            for r_i, row in enumerate(reader, start=1):
                line = "; ".join(f"{k}: {v}" for k, v in row.items() if k and v not in (None, ""))
                if line.strip():
                    doc_paras.append({"paragraph_id": f"{fp.stem}:row-{r_i:03d}", "source_document_id": fp.stem, "source_file": rel, "document_dois": [], "section_hint": "table_or_figure", "text": " ".join(line.split())})
            _register_doc(fp.stem, fp.name, rel, [], doc_paras, file_sha256=fsha, raw_text=raw)
        elif ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            # Image-only figure file with no companion caption text
            _register_doc(
                fp.stem,
                fp.name,
                rel,
                [],
                [],
                file_sha256=fsha,
                meta={"coverage_status": "figure_caption_missing", "image_only_figures": True},
            )
        elif ext == ".json":
            try:
                obj = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            except Exception:
                continue
            if isinstance(obj, dict) and (isinstance(obj.get("paragraphs"), list) or isinstance(obj.get("text"), str) or obj.get("coverage_status") or obj.get("abstract_only") or obj.get("figure_caption_missing")):
                did = str(obj.get("source_document_id") or obj.get("document_id") or obj.get("paper_id") or obj.get("id") or fp.stem)
                dois = _document_dois_from_fields(obj)
                doc_paras = []
                sec = "body"
                pages_seen = set()
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
                    dois = _document_dois_from_header("\n".join(p["text"] for p in doc_paras[:3]))
                    for p in doc_paras:
                        if not p["document_dois"]:
                            p["document_dois"] = dois
                meta = {k: v for k, v in obj.items() if k not in ("paragraphs", "text")}
                _register_doc(did, str(obj.get("title") or fp.name), rel, dois, doc_paras, file_sha256=fsha, page_count=len(pages_seen), meta=meta)

    return documents, paragraphs


def load_claims_to_verify(input_dir: Path, case_data: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    claims_file = input_dir / "claims-to-verify.json"
    raw_items: Any = None
    found_dedicated = False
    if claims_file.is_file():
        found_dedicated = True
        loaded = json.loads(claims_file.read_text(encoding="utf-8", errors="replace"))
        if isinstance(loaded, list):
            raw_items = loaded
        elif isinstance(loaded, dict):
            raw_items = loaded.get("claims") or loaded.get("items") or loaded.get("claims_to_verify") or []
    else:
        raw_items = case_data.get("claims") or case_data.get("claims_to_verify") or []

    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items or [], start=1):
        if isinstance(item, dict):
            cid = str(item.get("claim_id") or item.get("id") or f"C{idx:02d}")
            text = str(item.get("claim") or item.get("text") or item.get("statement") or "")
            cited_ref = str(item.get("cited_ref") or item.get("reference") or item.get("source_ref") or "")
            cited_page = item.get("cited_page") or item.get("page")
            cited_para = item.get("cited_paragraph") or item.get("paragraph_id")
            struct_cits = item.get("structured_citations") if "structured_citations" in item else item.get("citations")
        else:
            cid = f"C{idx:02d}"
            text = str(item)
            cited_ref = ""
            cited_page = None
            cited_para = None
            struct_cits = None
        if text.strip():
            out.append({
                "claim_id": cid,
                "claim_text": text.strip(),
                "cited_ref": cited_ref,
                "cited_page": cited_page,
                "cited_paragraph": cited_para,
                "structured_citations": struct_cits,
            })
    return out, found_dedicated


def split_compound_claim(claim_id: str, text: str) -> list[dict[str, Any]]:
    """Decompose compound scientific statement into atomic checkable sub-claims (fact/comparison/causation/scope/quantitative)."""
    raw_parts = [p.strip(" ,.;；。") for p in COMPOUND_SPLIT_RE.split(text) if p and p.strip(" ,.;；。")]
    # Merge tiny trailing fragments (< 3 words / < 8 chars) back into previous subclaim
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
        if fp["M_magnitudes_stats"] or re.search(r"\b\d+(?:\.\d+)?\s*%|\b[pP]\s*[<=>]\s*0?\.\d+", part):
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
            "subclaim_id": f"{claim_id}.{idx}",
            "text": part,
            "claim_types": claim_types,
            "axes": claim_types,
            "emlsi": fp,
            "comparator": fp["I_identifiers_comparators"]["comparators"],
            "negation_markers": fp["L_logic_negations"],
            "significance_markers": SIGNIFICANCE_RE.findall(part),
            "scope_conditions": fp["S_scope_conditions"],
            "asks_about_figure": bool(FIGURE_QUERY_RE.search(part)),
        })
    return subclaims


def _tokens(text: str) -> list[str]:
    en = [t for t in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9._/%-]{1,}", (text or "").lower()) if t not in STOPWORDS]
    zh = re.findall(r"[\u4e00-\u9fff]{2,3}", text or "")
    return en + zh


def _resolve_target_documents(
    cited_ref: str,
    documents: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bind cited_ref strictly to explicit DOI metadata, source_document_id, or filename (zero cross-paper fallback)."""
    cited_dois = set(_extract_dois(cited_ref))
    info: dict[str, Any] = {
        "cited_ref": cited_ref,
        "cited_dois": sorted(cited_dois),
        "source_locked": False,
        "doi_matched": False,
        "matched_document_ids": [],
    }
    if cited_dois:
        info["source_locked"] = True
        matched = [d for d in documents if cited_dois.intersection(d.get("document_dois") or [])]
        info["doi_matched"] = bool(matched)
        info["matched_document_ids"] = [d["source_document_id"] for d in matched]
        return matched, info

    ref_low = (cited_ref or "").strip().casefold()
    if ref_low:
        matched = []
        for d in documents:
            did = d["source_document_id"].casefold()
            sf = d["source_file"].casefold()
            sf_stem = Path(d["source_file"]).stem.casefold()
            if did in ref_low or sf in ref_low or (len(sf_stem) >= 4 and sf_stem in ref_low):
                matched.append(d)
        if matched:
            info["source_locked"] = True
            info["matched_document_ids"] = [d["source_document_id"] for d in matched]
            return matched, info
        # Check if cited_ref explicitly names an unknown file/doc ID like DOC99 or missing.pdf
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
        norm_score = round(score / max(1.0, math.sqrt(len(q_set))), 4)
        ranked.append((norm_score, p))
    ranked.sort(key=lambda x: -x[0])
    return ranked


def _extract_key_numbers(text: str) -> set[str]:
    return set(re.findall(r"\b\d+(?:\.\d+)?%?", text or ""))


def _extract_p_values(text: str) -> list[float]:
    vals: list[float] = []
    for m in re.finditer(r"\b[pP]\s*([<=>])\s*(0?\.\d+)\b", text or ""):
        try:
            vals.append(float(m.group(2)))
        except ValueError:
            pass
    return vals


def evaluate_subclaim_candidates(
    subclaim: dict[str, Any],
    eligible_docs: list[dict[str, Any]],
    all_paragraphs: list[dict[str, Any]],
    *,
    binding_info: dict[str, Any],
    anchor_refs: dict[str, Any],
    cit_num_by_doc: dict[str, int],
) -> dict[str, Any]:
    """Retrieve evidence with strict source locking, wrong-page recovery, localization check, and extrapolation vs contradiction separation."""
    diag = {
        "doi_exists_but_unsupported": False,
        "wrong_page_or_anchor_mismatch": False,
        "fulltext_unavailable_or_figure_caption_missing": False,
        "inline_vs_structured_citation_drift": False,
        "untested_extrapolation_detected": False,
        "secondary_citation_warning": False,
        "abstract_body_conflict_detected": False,
    }
    anchor_audit: dict[str, Any] = {
        "cited_pages": anchor_refs.get("cited_pages", []),
        "cited_paragraphs": anchor_refs.get("cited_paragraphs", []),
        "actual_pages": [],
        "actual_paragraph_ids": [],
        "wrong_page_or_anchor_mismatch": False,
    }

    # Case 1: Source-locked citation unmatched in corpus (NEVER borrow from another paper!)
    if binding_info["source_locked"] and not eligible_docs:
        return {
            "subclaim_id": subclaim["subclaim_id"],
            "text": subclaim["text"],
            "claim_types": subclaim["claim_types"],
            "axes": subclaim["axes"],
            "verdict": "insufficient_evidence",
            "candidate_verdict": "insufficient_evidence",
            "verdict_rationale": "Cited DOI/source not present in supplied corpus metadata; refused cross-document fallback.",
            "diagnostic_flags": diag,
            "anchor_audit": anchor_audit,
            "evidence": [],
            "alignment_checks": {"binding_info": binding_info, "eligible_doc_count": 0},
        }

    eligible_ids = {d["source_document_id"] for d in eligible_docs}
    doc_by_id = {d["source_document_id"]: d for d in eligible_docs}
    eligible_paras = [p for p in all_paragraphs if p["source_document_id"] in eligible_ids]

    # Case 2: Localization insufficiency (abstract_only / no_text_layer / figure_caption_missing)
    all_insufficient_loc = bool(eligible_docs) and all(d.get("localization_insufficient") for d in eligible_docs)
    any_fig_missing = any(d.get("coverage_status") == "figure_caption_missing" for d in eligible_docs)
    if (all_insufficient_loc and not eligible_paras) or (subclaim.get("asks_about_figure") and any_fig_missing):
        diag["fulltext_unavailable_or_figure_caption_missing"] = True
        reasons = "; ".join(sorted({d.get("localization_reason", d.get("coverage_status", "")) for d in eligible_docs if d.get("localization_insufficient")}))
        return {
            "subclaim_id": subclaim["subclaim_id"],
            "text": subclaim["text"],
            "claim_types": subclaim["claim_types"],
            "axes": subclaim["axes"],
            "verdict": "insufficient_evidence",
            "candidate_verdict": "insufficient_evidence",
            "verdict_rationale": f"Localization insufficient ({reasons}); marked insufficient_evidence, NOT contradicted.",
            "diagnostic_flags": diag,
            "anchor_audit": anchor_audit,
            "evidence": [],
            "alignment_checks": {"binding_info": binding_info, "localization_insufficient": True},
        }

    # Rank across eligible paragraphs (excluding bibliography section unless nothing else matches)
    body_paras = [p for p in eligible_paras if p.get("section_hint") != "references"] or eligible_paras
    ranked_all = _rank_paragraphs(subclaim["text"], body_paras)

    # Check explicit cited page / paragraph anchor first (same document only!)
    cited_pages = set(anchor_refs.get("cited_pages") or [])
    cited_para_ids = set(anchor_refs.get("cited_paragraphs") or [])
    ranked_anchor: list[tuple[float, dict[str, Any]]] = []
    if cited_pages or cited_para_ids:
        anchor_paras = [
            p for p in body_paras
            if (p.get("page") in cited_pages)
            or (p["paragraph_id"] in cited_para_ids)
            or any(p["paragraph_id"].endswith(f":{cp}") or p["paragraph_id"].endswith(cp) for cp in cited_para_ids)
        ]
        ranked_anchor = _rank_paragraphs(subclaim["text"], anchor_paras)

    top_all = ranked_all[:3]
    best_all_score = top_all[0][0] if top_all else 0.0
    best_anchor_score = ranked_anchor[0][0] if ranked_anchor else 0.0

    # Detect wrong_page_or_anchor_mismatch within the SAME document
    if (cited_pages or cited_para_ids) and top_all and best_all_score >= 0.35:
        top_para = top_all[0][1]
        in_cited_page = (not cited_pages) or (top_para.get("page") in cited_pages)
        in_cited_para = (not cited_para_ids) or (
            top_para["paragraph_id"] in cited_para_ids
            or any(top_para["paragraph_id"].endswith(cp) for cp in cited_para_ids)
        )
        if not (in_cited_page and in_cited_para) and best_all_score > best_anchor_score + 0.25:
            diag["wrong_page_or_anchor_mismatch"] = True
            anchor_audit["wrong_page_or_anchor_mismatch"] = True

    # If abstract_only document has low/partial match, flag localization insufficiency
    if all_insufficient_loc and best_all_score < 0.75:
        diag["fulltext_unavailable_or_figure_caption_missing"] = True
        reasons = "; ".join(sorted({d.get("localization_reason", "") for d in eligible_docs}))
        return {
            "subclaim_id": subclaim["subclaim_id"],
            "text": subclaim["text"],
            "claim_types": subclaim["claim_types"],
            "axes": subclaim["axes"],
            "verdict": "insufficient_evidence",
            "candidate_verdict": "insufficient_evidence",
            "verdict_rationale": f"Only abstract/partial text available ({reasons}) and full-text verification is absent; marked insufficient_evidence.",
            "diagnostic_flags": diag,
            "anchor_audit": anchor_audit,
            "evidence": [],
            "alignment_checks": {"binding_info": binding_info, "localization_insufficient": True, "best_score": best_all_score},
        }

    # Relevance floor & DOI-exists-but-unsupported check
    q_toks_set = set(_tokens(subclaim["text"]))
    if not top_all or best_all_score < 0.30:
        if binding_info["source_locked"] and eligible_docs:
            diag["doi_exists_but_unsupported"] = True
        if any(t in EXTRAPOLATION_DOMAIN_TOKENS for t in q_toks_set):
            diag["untested_extrapolation_detected"] = True
        return {
            "subclaim_id": subclaim["subclaim_id"],
            "text": subclaim["text"],
            "claim_types": subclaim["claim_types"],
            "axes": subclaim["axes"],
            "verdict": "insufficient_evidence",
            "candidate_verdict": "insufficient_evidence",
            "verdict_rationale": (
                "Cited document exists in corpus, but full text provides no supporting evidence for this sub-claim (doi_exists_but_unsupported); NOT marked contradicted."
                if diag["doi_exists_but_unsupported"]
                else "No relevant evidence retrieved above floor; unsupported extrapolation is marked insufficient_evidence, NOT contradicted."
            ),
            "diagnostic_flags": diag,
            "anchor_audit": anchor_audit,
            "evidence": [],
            "alignment_checks": {"binding_info": binding_info, "retrieved_candidates": 0, "best_score": best_all_score},
        }

    # Inspect top retrieved evidence for extrapolation, polarity, numbers, secondary citation & abstract-vs-body conflict
    c_fp = subclaim["emlsi"]
    c_neg = (len(c_fp["L_logic_negations"]) % 2 == 1) or bool(NEG_POLARITY_RE.search(subclaim["text"]))
    c_pos = bool(POS_POLARITY_RE.search(subclaim["text"])) and not c_neg
    c_nums = _extract_key_numbers(subclaim["text"])
    c_extrap_toks = q_toks_set & EXTRAPOLATION_DOMAIN_TOKENS

    # Check if top evidence actually covers claim's scope/extrapolation domain tokens
    top_blob_toks = set(_tokens(" ".join(p["text"] for _, p in top_all)))
    missing_extrap_toks = sorted(c_extrap_toks - top_blob_toks)

    # Also check lexical coverage of core non-stopword claim tokens in top paragraph
    top1_overlap = q_toks_set & set(_tokens(top_all[0][1]["text"]))
    overlap_ratio = len(top1_overlap) / max(1, len(q_toks_set))

    # Build grounded evidence objects
    ev_list: list[dict[str, Any]] = []
    polarity_conflicts = 0
    polarity_agrees = 0
    direct_refutations = 0
    missing_nums = list(c_nums)

    sec_polarities: dict[str, set[str]] = {"abstract": set(), "body": set()}

    for sc, para in top_all:
        p_txt = para["text"]
        p_fp = extract_emlsi_fingerprint(p_txt)
        p_neg = (len(p_fp["L_logic_negations"]) % 2 == 1) or bool(NEG_POLARITY_RE.search(p_txt))
        p_pos = bool(POS_POLARITY_RE.search(p_txt)) and not p_neg
        p_nums = _extract_key_numbers(p_txt)
        missing_nums = [n for n in missing_nums if n not in p_nums]

        sec_info = detect_secondary_citation(p_txt, para.get("section_hint", "body"))
        if sec_info["is_secondary_citation"]:
            diag["secondary_citation_warning"] = True

        # Determine paragraph polarity relative to claim
        p_pol = "negative" if p_neg else ("positive" if p_pos else "neutral")
        sec_bucket = "abstract" if para.get("section_hint") == "abstract" else "body"
        sec_polarities[sec_bucket].add(p_pol)

        # Statistical null refutation check: claim asserts p < 0.05 / significant, evidence reports p >= 0.05
        p_vals_claim = _extract_p_values(subclaim["text"])
        p_vals_ev = _extract_p_values(p_txt)
        stat_null_refute = bool(
            (any(v <= 0.05 for v in p_vals_claim) or re.search(r"\bsignificant(?:ly)?\b|显著", subclaim["text"], re.I))
            and (any(v >= 0.05 for v in p_vals_ev) or re.search(r"\bno\s+significant\b|无显著差异|不显著", p_txt, re.I))
        )

        # Direct polarity conflict requires shared subject tokens (not untested extrapolation!)
        if (c_neg != p_neg and (c_pos or c_neg or p_neg)) or stat_null_refute:
            polarity_conflicts += 1
            if not missing_extrap_toks and overlap_ratio >= 0.30:
                direct_refutations += 1
        else:
            polarity_agrees += 1

        did = para["source_document_id"]
        cnum = cit_num_by_doc.get(did, 0)
        quote = p_txt[:240]
        ev_entry: dict[str, Any] = {
            "citation_number": cnum,
            "citation_tag": f"[{cnum}]" if cnum else "",
            "source_document_id": did,
            "source_file": para["source_file"],
            "paragraph_id": para["paragraph_id"],
            "page": para.get("page"),
            "section_hint": para.get("section_hint", "body"),
            "attribution_type": sec_info["attribution_type"],
            "secondary_citation_warning": sec_info["is_secondary_citation"],
            "retrieval_score": sc,
            "quote": quote,
            "evidence_emlsi": p_fp,
        }
        ev_list.append(ev_entry)
        if para.get("page") is not None and para["page"] not in anchor_audit["actual_pages"]:
            anchor_audit["actual_pages"].append(para["page"])
        anchor_audit["actual_paragraph_ids"].append(para["paragraph_id"])

    # Check abstract vs body/table conflict within same document
    if (
        ("positive" in sec_polarities["abstract"] and "negative" in sec_polarities["body"])
        or ("negative" in sec_polarities["abstract"] and "positive" in sec_polarities["body"])
    ):
        diag["abstract_body_conflict_detected"] = True

    c_comps = set(c_fp["I_identifiers_comparators"]["comparators"])
    e_comps = set()
    for ev in ev_list:
        e_comps.update(ev["evidence_emlsi"]["I_identifiers_comparators"]["comparators"])
    missing_comparator = bool(c_comps and not (c_comps & e_comps))

    # Strict verdict decision tree
    if missing_extrap_toks:
        # Claim extrapolates to an untested population/clinical/toxicity scope -> MUST be insufficient_evidence, NEVER contradicted!
        diag["untested_extrapolation_detected"] = True
        if binding_info["source_locked"] and eligible_docs:
            diag["doi_exists_but_unsupported"] = True
        verdict = "insufficient_evidence"
        rationale = (
            f"Claim extrapolates to untested domain/scope token(s) {missing_extrap_toks} absent from retrieved evidence; "
            "untested extrapolation is classified insufficient_evidence, NOT contradicted."
        )
    elif (overlap_ratio < 0.45 and c_nums and len(missing_nums) == len(c_nums)) or (overlap_ratio < 0.25 and missing_nums):
        # Topic-related keyword overlap only, without core claim entities/numbers
        if binding_info["source_locked"] and eligible_docs:
            diag["doi_exists_but_unsupported"] = True
        verdict = "insufficient_evidence"
        rationale = (
            f"Only weak topic overlap ({overlap_ratio:.2f}) with zero matched claim numbers {missing_nums}; "
            "topic relevance does not establish support (doi_exists_but_unsupported)."
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
    elif missing_nums or missing_comparator:
        verdict = "partially_supported"
        rationale = f"Core entities matched, but missing claim numbers ({missing_nums}) or comparator alignment ({missing_comparator}) prevents unqualified support."
    elif ev_list and ev_list[0]["secondary_citation_warning"] and not any(
        (not e["secondary_citation_warning"]) and e["retrieval_score"] >= 0.70 * ev_list[0]["retrieval_score"]
        for e in ev_list[1:]
    ):
        verdict = "partially_supported"
        rationale = "Top matched passage is an indirect/secondary citation in Introduction/Background rather than primary empirical measurement by this paper."
    else:
        verdict = "supported"
        rationale = (
            "Entity, polarity, magnitude, and comparator align with grounded primary evidence"
            + (" (NOTE: cited page/anchor was mismatched; true evidence recovered elsewhere in same document)." if diag["wrong_page_or_anchor_mismatch"] else ".")
        )

    return {
        "subclaim_id": subclaim["subclaim_id"],
        "text": subclaim["text"],
        "claim_types": subclaim["claim_types"],
        "axes": subclaim["axes"],
        "verdict": verdict,
        "candidate_verdict": verdict,
        "verdict_rationale": rationale,
        "diagnostic_flags": diag,
        "anchor_audit": anchor_audit,
        "evidence": ev_list,
        "alignment_checks": {
            "binding_info": binding_info,
            "overlap_ratio": round(overlap_ratio, 3),
            "missing_claim_numbers_in_evidence": missing_nums,
            "missing_comparator_in_evidence": missing_comparator,
            "missing_extrapolation_tokens": missing_extrap_toks,
            "polarity_conflicts": polarity_conflicts,
            "polarity_agrees": polarity_agrees,
            "direct_refutations": direct_refutations,
        },
    }


def aggregate_claim_verdict(sub_results: list[dict[str, Any]]) -> str:
    verdicts = [s.get("verdict", s.get("candidate_verdict", "insufficient_evidence")) for s in sub_results]
    if not verdicts:
        return "insufficient_evidence"
    vset = set(verdicts)
    if vset == {"supported"}:
        return "supported"
    if vset == {"contradicted"}:
        return "contradicted"
    if vset == {"insufficient_evidence"}:
        return "insufficient_evidence"
    return "partially_supported"


def merge_diagnostic_flags(sub_results: list[dict[str, Any]], cit_audit: dict[str, Any]) -> dict[str, bool]:
    keys = (
        "doi_exists_but_unsupported",
        "wrong_page_or_anchor_mismatch",
        "fulltext_unavailable_or_figure_caption_missing",
        "inline_vs_structured_citation_drift",
        "untested_extrapolation_detected",
        "secondary_citation_warning",
        "abstract_body_conflict_detected",
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
    """Strictly validate verification cards: paragraph ownership, verbatim quotes, citation drift, and verdict invariants."""
    para_lookup = {p["paragraph_id"]: p for p in paragraphs}
    doc_dois_by_id = {d["source_document_id"]: set(d.get("document_dois") or []) for d in documents}

    errors: list[str] = []
    warnings: list[str] = []
    drift_count = 0

    for item in draft.get("verifications") or []:
        cid = str(item.get("claim_id") or "unknown")
        v = item.get("verdict")
        if v not in VALID_VERDICTS:
            errors.append(f"claim {cid}: INVALID_VERDICT {v!r}")

        cited_dois = set(_extract_dois(str(item.get("cited_ref") or "")))

        # Audit inline vs structured citations on claim text or report summary if structured_citations is present
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
            if strict_citation_drift:
                errors.append(msg)
            else:
                warnings.append(msg)

        for sub in item.get("subclaims") or []:
            sid = str(sub.get("subclaim_id") or cid)
            sv = sub.get("verdict", sub.get("candidate_verdict"))
            if sv not in VALID_VERDICTS:
                errors.append(f"subclaim {sid}: INVALID_VERDICT {sv!r}")

            ev_list = sub.get("evidence") or []
            s_diag = sub.get("diagnostic_flags") or {}

            if sv == "supported" and not ev_list:
                errors.append(f"subclaim {sid}: SUPPORTED_WITHOUT_EVIDENCE (supported requires >=1 grounded evidence quote)")
            if sv == "contradicted" and not ev_list:
                errors.append(f"subclaim {sid}: CONTRADICTED_WITHOUT_OPPOSING_EVIDENCE (contradicted requires >=1 grounded opposing quote)")
            if sv == "contradicted" and s_diag.get("fulltext_unavailable_or_figure_caption_missing"):
                errors.append(f"subclaim {sid}: CONTRADICTED_ON_LOCALIZATION_GAP (localization_insufficient must be insufficient_evidence)")
            if s_diag.get("abstract_body_conflict_detected") and len(ev_list) < 2:
                errors.append(f"subclaim {sid}: CONFLICT_MISSING_DUAL_ANCHORS (abstract-body conflict requires >=2 anchored quotes)")

            for ev in ev_list:
                pid = str(ev.get("paragraph_id") or "")
                ev_doc = str(ev.get("source_document_id") or "")
                q = str(ev.get("quote") or "")
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

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "citation_integrity_drift_count": drift_count,
    }


def render_verification_report_md(
    citation_legend: list[dict[str, Any]],
    verifications: list[dict[str, Any]],
    diag_summary: dict[str, int],
    cit_integrity_summary: dict[str, Any],
) -> str:
    lines: list[str] = ["# Scientific Claim Verification & Evidence Tracing Report", ""]

    # 1. Citation Legend
    lines.extend([
        "## 1. Citation Legend (Immutable Source Mapping)",
        "",
        "| Citation | source_document_id | Title | DOI | Source File | SHA-256 (12) | Pages | Coverage Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for e in citation_legend:
        doi_disp = e["doi"] or "`not_reported`"
        lines.append(
            f"| {e['citation_tag']} | `{e['source_document_id']}` | {e['title'].replace('|', '/')} | {doi_disp} | "
            f"`{e['source_file']}` | `{e['file_sha256_short']}` | `{e['page_count']}` | `{e['coverage_status']}` |"
        )

    # 2. Verdict & 4-State Diagnostic Summary
    lines.extend([
        "",
        "## 2. Verdict & Four-State Diagnostic Summary",
        "",
        f"- Total claims audited: `{len(verifications)}`",
        f"- `doi_exists_but_unsupported`: `{diag_summary.get('doi_exists_but_unsupported', 0)}`",
        f"- `wrong_page_or_anchor_mismatch`: `{diag_summary.get('wrong_page_or_anchor_mismatch', 0)}`",
        f"- `fulltext_unavailable_or_figure_caption_missing`: `{diag_summary.get('fulltext_unavailable_or_figure_caption_missing', 0)}`",
        f"- `inline_vs_structured_citation_drift`: `{diag_summary.get('inline_vs_structured_citation_drift', 0)}`",
        f"- `untested_extrapolation_detected`: `{diag_summary.get('untested_extrapolation_detected', 0)}`",
        f"- `secondary_citation_warning`: `{diag_summary.get('secondary_citation_warning', 0)}`",
        f"- `abstract_body_conflict_detected`: `{diag_summary.get('abstract_body_conflict_detected', 0)}`",
        "",
        "| Claim ID | Verdict | Cited Ref | Active Diagnostic Flags |",
        "| --- | --- | --- | --- |",
    ])
    for v in verifications:
        active = [k for k, val in (v.get("diagnostic_flags") or {}).items() if val]
        lines.append(
            f"| `{v['claim_id']}` | `{v['verdict']}` | `{str(v.get('cited_ref') or 'none').replace('|', '/')}` | `{', '.join(active) if active else 'none'}` |"
        )

    # 3. Citation Integrity Audit
    lines.extend([
        "",
        "## 3. Inline-vs-Structured Citation Integrity Audit",
        "",
        f"- Claims with inline/structured citation drift: `{cit_integrity_summary.get('claims_with_drift', 0)}`",
    ])
    for rec in cit_integrity_summary.get("drifted_claims", []):
        lines.append(
            f"- `{rec['claim_id']}`: inline IDs `{rec['inline_unique_ids']}` (count={rec['inline_marker_count']}) vs structured count=`{rec['structured_citation_count']}`, missing=`{rec['missing_in_structured']}`"
        )

    # 4. Atomic Evidence Cards
    lines.extend(["", "## 4. Atomic Sub-Claim Evidence Cards", ""])
    for v in verifications:
        lines.append(f"### `{v['claim_id']}` — Verdict: `{v['verdict']}`")
        lines.append(f"- **Original Claim**: {v['original_claim']}")
        if v.get("cited_ref"):
            lines.append(f"- **Cited Ref**: `{v['cited_ref']}`")
        lines.append("")
        lines.append("| Sub-Claim ID | Types | Verdict | Anchor | Attribution | Verbatim Quote & Rationale |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for sub in v.get("subclaims") or []:
            sv = sub.get("verdict", sub.get("candidate_verdict"))
            types = ",".join(sub.get("claim_types") or sub.get("axes") or ["fact"])
            evs = sub.get("evidence") or []
            if evs:
                e0 = evs[0]
                anc = f"{e0.get('citation_tag', '')} `{e0.get('paragraph_id')}`" + (f" (p.{e0['page']})" if e0.get("page") else "")
                attr = e0.get("attribution_type", "primary_study")
                q_str = f"\"{str(e0.get('quote', ''))[:130].replace('|', '/')}\" — {str(sub.get('verdict_rationale', '')).replace('|', '/')}"
            else:
                anc = "`none`"
                attr = "`none`"
                q_str = str(sub.get("verdict_rationale", "")).replace("|", "/")
            lines.append(f"| `{sub['subclaim_id']}` | `{types}` | `{sv}` | {anc} | `{attr}` | {q_str} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    *,
    draft_json: Path | None = None,
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

    # Immutable Citation Legend
    sorted_docs = sorted(documents, key=lambda d: d["source_document_id"])
    citation_legend: list[dict[str, Any]] = []
    cit_num_by_doc: dict[str, int] = {}
    for idx, d in enumerate(sorted_docs, start=1):
        cit_num_by_doc[d["source_document_id"]] = idx
        citation_legend.append({
            "citation_number": idx,
            "citation_tag": f"[{idx}]",
            "source_document_id": d["source_document_id"],
            "title": d["title"],
            "doi": d["doi"],
            "source_file": d["source_file"],
            "file_sha256": d["file_sha256"],
            "file_sha256_short": d["file_sha256_short"],
            "page_count": d["page_count"],
            "paragraph_count": d["paragraph_count"],
            "coverage_status": d["coverage_status"],
        })

    # Write full indexed paragraphs to /app/work/corpus_paragraphs.json for host agent deep reading
    result_guard.atomic_write_json(
        work_dir / "corpus_paragraphs.json",
        {"case_id": case_id, "citation_legend": citation_legend, "paragraphs": paragraphs},
    )

    full_blob = "\n\n".join(p["text"] for p in paragraphs)
    citations = [c.to_dict() for c in extract_citations(full_blob)]

    budget_guard.tick_phase(budget_file, "split_and_match_claims")

    is_draft_validation = bool(draft_json and draft_json.is_file())
    if is_draft_validation and draft_json is not None:
        cards_payload = json.loads(draft_json.read_text(encoding="utf-8", errors="replace"))
        if "citation_legend" not in cards_payload:
            cards_payload["citation_legend"] = citation_legend
        if "corpus_citations_extracted" not in cards_payload:
            cards_payload["corpus_citations_extracted"] = citations
    else:
        verifications: list[dict[str, Any]] = []
        for cl in claims:
            subclaims = split_compound_claim(cl["claim_id"], cl["claim_text"])
            eligible_docs, binding_info = _resolve_target_documents(cl["cited_ref"], sorted_docs)
            anchor_refs = extract_page_and_paragraph_refs(
                cl["cited_ref"],
                cited_page=cl.get("cited_page"),
                cited_paragraph=cl.get("cited_paragraph"),
            )
            cit_audit = audit_inline_vs_structured_citations(cl["claim_text"], cl.get("structured_citations"))
            sub_results = [
                evaluate_subclaim_candidates(
                    sc,
                    eligible_docs,
                    paragraphs,
                    binding_info=binding_info,
                    anchor_refs=anchor_refs,
                    cit_num_by_doc=cit_num_by_doc,
                )
                for sc in subclaims
            ]
            overall_verdict = aggregate_claim_verdict(sub_results)
            merged_diag = merge_diagnostic_flags(sub_results, cit_audit)
            verifications.append({
                "claim_id": cl["claim_id"],
                "original_claim": cl["claim_text"],
                "cited_ref": cl["cited_ref"],
                "verdict": overall_verdict,
                "preserves_comparator_negation_conditions": True,
                "diagnostic_flags": merged_diag,
                "citation_integrity_audit": cit_audit,
                "subclaims": sub_results,
            })
        cards_payload = {
            "case_id": case_id,
            "claims_file_detected": found_dedicated,
            "citation_legend": citation_legend,
            "verifications": verifications,
            "corpus_citations_extracted": citations,
        }

    val = validate_verification_draft(
        cards_payload,
        sorted_docs,
        paragraphs,
        strict_citation_drift=is_draft_validation,
    )

    ver_list = cards_payload.get("verifications") or []
    diag_keys = (
        "doi_exists_but_unsupported",
        "wrong_page_or_anchor_mismatch",
        "fulltext_unavailable_or_figure_caption_missing",
        "inline_vs_structured_citation_drift",
        "untested_extrapolation_detected",
        "secondary_citation_warning",
        "abstract_body_conflict_detected",
    )
    diag_summary = {
        k: sum(1 for v in ver_list if (v.get("diagnostic_flags") or {}).get(k))
        for k in diag_keys
    }
    drifted_claims = [
        {"claim_id": v.get("claim_id"), **(v.get("citation_integrity_audit") or {})}
        for v in ver_list
        if (v.get("citation_integrity_audit") or {}).get("drift_detected")
    ]
    cit_integrity_summary = {
        "total_claims_audited": len(ver_list),
        "claims_with_drift": len(drifted_claims),
        "drifted_claims": drifted_claims,
    }

    cards_payload["diagnostic_summary"] = diag_summary
    cards_payload["citation_integrity_audit"] = cit_integrity_summary
    cards_payload["schema_validation"] = val

    result_guard.atomic_write_json(output_dir / "verification_cards.json", cards_payload)
    report_md = render_verification_report_md(
        cards_payload.get("citation_legend") or citation_legend,
        ver_list,
        diag_summary,
        cit_integrity_summary,
    )
    result_guard.atomic_write_text(output_dir / "verification_report.md", report_md)

    append_audit_entry(
        audit_file,
        phase="verify_claims",
        action="verification_cards_and_report_written",
        actor="claim_verifier.py",
        output={
            "claim_count": len(ver_list),
            "document_count": len(sorted_docs),
            "paragraph_count": len(paragraphs),
            "validation_ok": val["ok"],
            "diagnostic_summary": diag_summary,
        },
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    if not found_dedicated:
        gaps.append({
            "code": "MISSING_CLAIMS_TO_VERIFY_FILE",
            "severity": "warning",
            "source": "load_claims_to_verify",
            "message": "/input/claims-to-verify.json not present; fell back to case.json claims if any.",
        })
    if not paragraphs:
        gaps.append({
            "code": "NO_READABLE_CORPUS",
            "severity": "error",
            "source": "collect_corpus_documents_and_paragraphs",
            "message": "No readable corpus paragraphs available for fact verification.",
        })
    if diag_summary["fulltext_unavailable_or_figure_caption_missing"] > 0:
        gaps.append({
            "code": "LOCALIZATION_INSUFFICIENT",
            "severity": "warning",
            "source": "evaluate_subclaim_candidates",
            "message": f"{diag_summary['fulltext_unavailable_or_figure_caption_missing']} claim(s) encountered abstract-only text, missing PDF text layer, or missing figure caption; marked insufficient_evidence.",
        })
    if diag_summary["inline_vs_structured_citation_drift"] > 0:
        gaps.append({
            "code": "INLINE_STRUCTURED_CITATION_DRIFT",
            "severity": "warning",
            "source": "audit_inline_vs_structured_citations",
            "message": f"{diag_summary['inline_vs_structured_citation_drift']} claim(s) exhibited inline marker vs. structured citation count/ID drift.",
        })
    if not val["ok"]:
        gaps.append({
            "code": "VERIFICATION_VALIDATION_FAILED",
            "severity": "error",
            "source": "validate_verification_draft",
            "message": "; ".join(val["errors"][:10]),
        })

    counts = Counter(v.get("verdict", "insufficient_evidence") for v in ver_list)
    patch = {
        "artifacts": ["result.json", "verification_cards.json", "verification_report.md"],
        "citation_legend": cards_payload.get("citation_legend") or citation_legend,
        "verifications": ver_list,
        "diagnostic_summary": diag_summary,
        "citation_integrity_audit": cit_integrity_summary,
        "corpus_citations_extracted": citations,
        "schema_validation": val,
        "metrics": {
            "document_count": len(sorted_docs),
            "paragraph_count": len(paragraphs),
            "claim_count": len(ver_list),
            "subclaim_count": sum(len(v.get("subclaims", [])) for v in ver_list),
            "supported_count": counts.get("supported", 0),
            "partially_supported_count": counts.get("partially_supported", 0),
            "contradicted_count": counts.get("contradicted", 0),
            "insufficient_evidence_count": counts.get("insufficient_evidence", 0),
            "doi_exists_but_unsupported_count": diag_summary["doi_exists_but_unsupported"],
            "wrong_page_or_anchor_mismatch_count": diag_summary["wrong_page_or_anchor_mismatch"],
            "localization_insufficient_count": diag_summary["fulltext_unavailable_or_figure_caption_missing"],
            "citation_drift_count": diag_summary["inline_vs_structured_citation_drift"],
            "extracted_citation_count": len(citations),
        },
        "metric_provenance": {
            "document_count": "citation_legend",
            "paragraph_count": "work/corpus_paragraphs.json",
            "claim_count": "verification_cards.json:verifications",
            "subclaim_count": "verification_cards.json:verifications[].subclaims",
            "supported_count": "verifications[].verdict",
            "partially_supported_count": "verifications[].verdict",
            "contradicted_count": "verifications[].verdict",
            "insufficient_evidence_count": "verifications[].verdict",
            "doi_exists_but_unsupported_count": "diagnostic_summary.doi_exists_but_unsupported",
            "wrong_page_or_anchor_mismatch_count": "diagnostic_summary.wrong_page_or_anchor_mismatch",
            "localization_insufficient_count": "diagnostic_summary.fulltext_unavailable_or_figure_caption_missing",
            "citation_drift_count": "diagnostic_summary.inline_vs_structured_citation_drift",
            "extracted_citation_count": "verification_cards.json:corpus_citations_extracted",
        },
        "findings": [
            f"Audited {len(ver_list)} claim(s) across {len(sorted_docs)} document(s) ({len(paragraphs)} paragraphs) with 4-state entailment and 4 independent failure diagnostics.",
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
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["verification_cards.json", "verification_report.md"],
    )
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

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    draft_p = Path(args.validate_verifications) if args.validate_verifications else None
    res, val_ok = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], draft_json=draft_p)
    print(json.dumps({
        "ok": val_ok,
        "case_id": res["case_id"],
        "status": res["status"],
        "metrics": res["metrics"],
        "schema_validation": res["schema_validation"],
    }, ensure_ascii=False))
    if draft_p is not None and not val_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
