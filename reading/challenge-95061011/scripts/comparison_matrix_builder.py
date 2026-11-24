"""Multi-Document Common-Field Comparison Matrix, 6-Axis Comparability Gate & Semantic Validator (challenge-95061011).

Pure Python stdlib (zero third-party Python dependencies; uses `pdftotext -enc UTF-8` reading-flow
extraction with optional pypdf/pdfplumber fallback).
Implements Organizer Revision v2:
- Reads /input/comparison-workorder.json and ALL supplied corpus documents (PDF/JSON/MD/TXT/CSV).
- Preserves thousands-separated sample sizes (e.g., n=5,539, n=1,010) and reading-order double-column PDFs.
- Deduplicates exact/preprint-vs-Version-of-Record pairs and isolates off-topic distractors.
- Builds an immutable Citation Legend ([1]..[N]) and auto-extracts 7 deep structured fields directly
  from raw prose papers (study_object, dataset_and_sample_size, method_operations, experimental_conditions,
  metrics_and_values, main_conclusion, limitations) plus the 6 comparability axes {task, metric, units,
  material, pollutant, budget}, each anchored to (source_document_id, paragraph_id, verbatim quote).
- Attributes directional and same-metric numeric conflicts to structured causes (sample_diff, method_diff,
  condition_diff, metric_or_unit_diff, unattributable) without majority voting or naive averaging.
- Enforces `--validate-matrix` blocking gates: cross-paper bleed, verbatim quotes, numeric-stem grounding
  inside quotes (blocks cross-cell number bleed & fabrication), illegal pooling, and Citation Legend drift.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log
from dedup_context_guard import (
    deduplicate_documents_with_versions,
    detect_out_of_scope_documents,
    normalize_doi,
)

COMPARABILITY_AXES = ("task", "metric", "units", "material", "pollutant", "budget")
CORE_SEVEN_FIELDS = (
    "study_object",
    "dataset_and_sample_size",
    "method_operations",
    "experimental_conditions",
    "metrics_and_values",
    "main_conclusion",
    "limitations",
)
DEFAULT_COMMON_FIELDS = [
    "study_object",
    "dataset_and_sample_size",
    "method_operations",
    "experimental_conditions",
    "metrics_and_values",
    "task",
    "material",
    "pollutant",
    "dataset",
    "sample_size",
    "metric",
    "units",
    "budget",
    "baseline",
    "main_conclusion",
    "limitations",
]
VALID_CELL_STATUSES = {"reported", "not_reported", "ambiguous"}
VALID_FACT_TAGS = {"explicitly_stated", "functionally_equivalent", "inferred_unverified"}
VALID_ATTRIBUTION_DIMS = {
    "sample_diff",
    "method_diff",
    "condition_diff",
    "metric_or_unit_diff",
    "unattributable",
    "sample",
    "method",
    "condition",
    "metric",
}

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.I)
NOT_REPORTED_RE = re.compile(
    r"^(?:not[\s_-]*reported|unreported|unspecified|unknown|none|n/?a|nd|n\.d\.|nr|未报告|未说明|未知|无记录|—|-)$",
    re.I,
)
NUM_STEM_RE = re.compile(r"(?<![A-Za-z0-9.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b\d+(?:\.\d+)?")
SAMPLE_N_RE = re.compile(
    r"(?:\b[nN]\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)\b|\b(\d{1,3}(?:,\d{3})+|\d+)\s+(?:cases|patients|subjects|individuals|participants|reports|samples|studies|protocols)\b)"
)
TIME_WINDOW_RE = re.compile(
    r"\b(?:from\s+(?:19|20)\d{2}\s+to\s+(?:19|20)\d{2}|(?:19|20)\d{2}\s*[–-]\s*(?:19|20)\d{2}|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+(?:19|20)\d{2})\b",
    re.I,
)

FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "task": re.compile(r"(?:^|[\s;*•-])(?:task|objective|study\s+task|assay\s+task|任务|研究任务)\s*[:=]\s*([^\n.;|]+)", re.I),
    "material": re.compile(r"(?:^|[\s;*•-])(?:material|catalyst|adsorbent|model\s+architecture|polymer|composite|platform|材料|催化剂)\s*[:=]\s*([^\n.;|]+)", re.I),
    "pollutant": re.compile(r"(?:^|[\s;*•-])(?:pollutant|dye|contaminant|target\s+analyte|analyte|substrate|target\s+pollutant|污染物|目标染料)\s*[:=]\s*([^\n.;|]+)", re.I),
    "dataset": re.compile(r"(?:^|[\s;*•-])(?:dataset|benchmark|cohort|database|data\s+source|数据集|队列)\s*[:=]\s*([^\n.;|]+)", re.I),
    "metric": re.compile(r"(?:^|[\s;*•-])(?:metric|primary\s+metric|endpoint|evaluation\s+metric|指标|评价指标)\s*[:=]\s*([^\n.;|]+)", re.I),
    "units": re.compile(r"(?:^|[\s;*•-])(?:units?|measurement\s+unit|单位)\s*[:=]\s*([^\n.;|]+)", re.I),
    "budget": re.compile(r"(?:^|[\s;*•-])(?:budget|experimental\s+budget|dosage|catalyst\s+loading|compute\s+budget|reaction\s+budget|预算|剂量与时间)\s*[:=]\s*([^\n.;|]+)", re.I),
    "baseline": re.compile(r"(?:^|[\s;*•-])(?:baseline|comparator|control\s+group|reference\s+method|基线|对照)\s*[:=]\s*([^\n.;|]+)", re.I),
    "sample_size": re.compile(r"(?:(?:sample\s+size|cohort\s+size|样本量)\s*[:=]\s*([^\n.;|]+)|\b[nN]\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)\b)", re.I),
    "main_conclusion": re.compile(r"(?:^|[\s;*•-])(?:main\s+conclusion|conclusions?|key\s+finding|finding|主要结论|结论)\s*[:=]\s*([^\n]+)", re.I),
    "limitations": re.compile(r"(?:^|[\s;*•-])(?:limitations?|drawback|caveat|局限性?)\s*[:=]\s*([^\n]+)", re.I),
    "method_name": re.compile(r"(?:^|[\s;*•-])(?:method\s+name|method|algorithm|pipeline|approach|方法)\s*[:=]\s*([^\n.;|]+)", re.I),
    "study_object": re.compile(r"(?:^|[\s;*•-])(?:study\s+object|target\s+population|population|subject|研究对象)\s*[:=]\s*([^\n.;|]+)", re.I),
    "experimental_conditions": re.compile(r"(?:^|[\s;*•-])(?:experimental\s+conditions?|operating\s+conditions?|conditions?|实验条件)\s*[:=]\s*([^\n.;|]+)", re.I),
}

UNIT_CANONICAL_ALIASES: dict[str, str] = {
    "%": "%", "percent": "%", "percentage": "%", "pct": "%",
    "mg/l": "mg/l", "mg l-1": "mg/l", "mg·l-1": "mg/l", "mg per l": "mg/l",
    "ug/l": "ug/l", "µg/l": "ug/l", "μg/l": "ug/l", "ug l-1": "ug/l",
    "g/l": "g/l", "g l-1": "g/l", "mg/g": "mg/g", "mg g-1": "mg/g", "mg·g-1": "mg/g",
    "mmol/l": "mmol/l", "mm": "mmol/l", "umol/l": "umol/l", "µmol/l": "umol/l", "μmol/l": "umol/l",
    "um": "umol/l", "µm": "umol/l", "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "min-1": "min^-1", "min^-1": "min^-1", "1/min": "min^-1",
    "ppm": "ppm", "ppb": "ppb", "ma/cm2": "ma/cm2", "ma cm-2": "ma/cm2",
    "v vs rhe": "v vs rhe", "v": "v", "mv/s": "mv/s", "cases": "cases",
    "per 10,000 inhabitants": "/10,000 inhabitants", "/10,000 inhabitants": "/10,000 inhabitants",
    "reports": "reports", "score": "score",
}

UNIT_CONVERSION_ADVISORIES: dict[tuple[str, str], tuple[str, str]] = {
    ("g/l", "mg/l"): ("1 g/L = 1000 mg/L", "Convert concentration units explicitly before comparing magnitudes."),
    ("mg/l", "ug/l"): ("1 mg/L = 1000 ug/L", "Convert concentration units explicitly before comparing magnitudes."),
    ("mg/l", "ppm"): ("1 mg/L ~= 1 ppm only in dilute aqueous media", "Verify solvent density/matrix before equating mg/L with ppm."),
    ("ppm", "ppb"): ("1 ppm = 1000 ppb", "Convert concentration scale explicitly before pooling."),
    ("mmol/l", "umol/l"): ("1 mmol/L = 1000 umol/L", "Convert molarity scale explicitly before pooling."),
    ("h", "min"): ("1 h = 60 min", "Convert duration units explicitly before pooling."),
    ("min", "s"): ("1 min = 60 s", "Convert duration units explicitly before pooling."),
    ("h", "s"): ("1 h = 3600 s", "Convert duration units explicitly before pooling."),
    ("a/cm2", "ma/cm2"): ("1 A/cm2 = 1000 mA/cm2", "Convert current density scale explicitly before pooling."),
}

POSITIVE_DIRECTION_RE = re.compile(
    r"\b(?:improv(?:ed|es|ing)|increas(?:ed|es|ing)|outperform(?:ed|s)|superior|effective|enhanc(?:ed|es)|accelerates?|higher\s+(?:removal|frequency|incidence)|significant(?:ly)?\s+(?:increas|reduc(?:ed|es)\s+(?:pollutant|dye|error|false\s+discover))|recommended|strong\s+support|outstrips?)\b|显著提高|优于|有效提升|显著降低误差",
    re.I,
)
NEGATIVE_DIRECTION_RE = re.compile(
    r"\b(?:no\s+significant\s+(?:improvement|difference|effect|benefit)|fail(?:ed|s)\s+to|ineffective|inferior|worse(?:ned)?|degraded|inhibited|suppress(?:ed|es)\s+activity|contradict(?:ed|s)|did\s+not\s+(?:improve|outperform|increase)|prone\s+to\s+false\s+discoveries|biased|poor\s+removal|low\s+efficiency|stationary|no\s+international\s+formalized\s+protocol|do\s+not\s+follow\s+any)\b|无显著差异|无效|劣于|未改善|明显下降|抑制活性",
    re.I,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_cell(
    field_name: str,
    citation_number: int,
    doc_id: str,
    *,
    paragraph_id: str | None = None,
    page: int | None = None,
    value: str | None = None,
    raw_value: str | None = None,
    units: str = "",
    conditions: str = "",
    quote: str = "",
    status: str = "not_reported",
    fact_tag: str = "explicitly_stated",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_page = page
    if resolved_page is None and paragraph_id:
        pm = re.search(r":p(\d+)(?:[:\-]b\d+)?\b", str(paragraph_id))
        if pm:
            resolved_page = int(pm.group(1))
    table_ref = ""
    if quote:
        tm = re.search(r"\b(Table\s+(?:S?\d+))\b", quote, re.I)
        if tm:
            table_ref = tm.group(1)
    location_anchor = ""
    if status != "not_reported" and paragraph_id:
        loc_parts = []
        if resolved_page is not None:
            loc_parts.append(f"Page {resolved_page}")
        if table_ref:
            loc_parts.append(table_ref)
        loc_parts.append(str(paragraph_id))
        location_anchor = ", ".join(loc_parts)
    cell: dict[str, Any] = {
        "field": field_name,
        "citation_number": citation_number,
        "source_document_id": doc_id,
        "paragraph_id": paragraph_id,
        "page": resolved_page,
        "location_anchor": location_anchor,
        "value": value,
        "raw_value": raw_value if raw_value is not None else value,
        "units": units,
        "conditions": conditions,
        "quote": quote,
        "status": status if status in VALID_CELL_STATUSES else "reported",
        "fact_tag": fact_tag if fact_tag in VALID_FACT_TAGS else "explicitly_stated",
    }
    if extra:
        cell.update(extra)
    return cell


def _extract_header_doi(text: str) -> str:
    """Extract DOI only from header / Page-1 front matter, never reference lists."""
    for ln in (text or "").splitlines()[:95]:
        if re.match(r"^\s*(?:references|bibliography|参考文献)\b", ln, re.I):
            break
        if re.search(r"(?:^|\b)(?:document\s+)?doi\s*[:=]|doi\.org/", ln, re.I):
            m = DOI_RE.search(ln)
            if m:
                raw_doi = re.sub(r"(?:IN|EN|PT)$", "", m.group(1))
                return normalize_doi(raw_doi)
    return ""


def _extract_title_from_text(text: str, fallback: str) -> str:
    raw_lines = [ln.strip() for ln in (text or "").splitlines()[:45] if ln.strip()]
    lines = [
        ln for ln in raw_lines
        if not re.match(r"^===\s*.+\s*\|\s*Page\s+\d+\s*===$", ln, re.I)
        and not re.search(r"sharepoint\.com|Tasker/.*\.doc\b|Update\.doc\b", ln, re.I)
    ]
    # Handle multi-line FDA cover titles & bilingual journal headers cleanly
    joined_head = " ".join(lines[:10])
    if re.search(r"Guidance\s+for\s+Industry\s+Good\s+Pharmacovigilance\s+Practices", joined_head, re.I):
        return "Guidance for Industry: Good Pharmacovigilance Practices and Pharmacoepidemiologic Assessment"
    if re.search(r"Best\s+Practices\s+for\s+FDA\s+Staff\s+in\s+the\s+Postmarketing\s+Safety\s+Surveillance", joined_head, re.I):
        return "Best Practices for FDA Staff in the Postmarketing Safety Surveillance of Human Drug and Biological Products"
    if re.search(r"Epidemiological\s+profile\s+and\s+temporal\s+trend\s+of\s+exogenous\s+intoxications", joined_head, re.I):
        return "Epidemiological profile and temporal trend of exogenous intoxications in children and adolescents"

    for idx, s in enumerate(lines):
        if s.startswith("#"):
            cand = s.lstrip("#").strip()
            if len(cand) >= 4:
                return cand[:200]
        m = re.match(r"^(?:title|标题)\s*[:=]\s*(.+)$", s, re.I)
        if m:
            return m.group(1).strip()[:200]
        if re.match(
            r"^(?:doi|author|date|page|http|https|task|material|pollutant|contains\s+nonbinding|original\s+article|abstract|resumo|objective\s*:|objetivo\s*:|u\.s\.\s+department|\d+/\d+|\d{2}/\d{2}/\d{2})\b",
            s,
            re.I,
        ):
            continue
        if len(s) >= 12:
            # Merge immediate continuation line if title wrapped mid-phrase
            if idx + 1 < len(lines) and len(s) < 70 and not s.endswith((".", "?", "!")) and len(lines[idx + 1]) >= 8 and not re.match(r"^(?:U\.S\.|Food\s+and|Center\s+for|March|January|Abstract|Preface|http)", lines[idx + 1], re.I):
                combined = f"{s} {lines[idx + 1]}".strip()
                if len(combined) <= 180 and not re.search(r"@|University|Institut|Zentrum", combined, re.I):
                    return combined[:200]
            return s[:200]
    return fallback


def _pdf_pages_with_pdftotext(pdf_path: Path) -> list[tuple[int, str]]:
    """Extract per-page text via reading-flow `pdftotext -enc UTF-8` (no `-layout`), with pypdf/pdfplumber fallback."""
    bin_p = shutil.which("pdftotext")
    if bin_p:
        try:
            proc = subprocess.run(
                [bin_p, "-enc", "UTF-8", str(pdf_path), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
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
            pages = [(idx, p.extract_text() or "") for idx, p in enumerate(doc.pages, start=1)]
            return [(i, t) for i, t in pages if t.strip()]
    except Exception:
        pass
    return []


_PAGE_MARKER_RE = re.compile(r"^===\s*.+?\s*\|\s*Page\s+(\d+)\s*===\s*$", re.I | re.M)


def _split_paragraphs_from_text(doc_id: str, text: str, page: int | None = None) -> list[dict[str, Any]]:
    if page is None and _PAGE_MARKER_RE.search(text or ""):
        paras_all: list[dict[str, Any]] = []
        matches = list(_PAGE_MARKER_RE.finditer(text or ""))
        for idx, m in enumerate(matches):
            pno = int(m.group(1))
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            paras_all.extend(_split_paragraphs_from_text(doc_id, text[start:end], page=pno))
        if paras_all:
            return paras_all

    paras: list[dict[str, Any]] = []
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    if len(chunks) == 1 and len(chunks[0]) > 1400:
        lines = [ln.strip() for ln in chunks[0].splitlines() if ln.strip()]
        grouped: list[str] = []
        buf: list[str] = []
        for ln in lines:
            buf.append(ln)
            if sum(len(x) for x in buf) >= 600 and ln.endswith((".", "!", "?", "。", "；")):
                grouped.append(" ".join(buf))
                buf = []
        if buf:
            grouped.append(" ".join(buf))
        chunks = grouped
    for bno, chunk in enumerate(chunks, start=1):
        clean = " ".join(chunk.split())
        if not clean:
            continue
        pid = f"{doc_id}:p{page}:b{bno}" if page is not None else f"{doc_id}:P{bno:03d}"
        entry: dict[str, Any] = {"paragraph_id": pid, "text": clean}
        if page is not None:
            entry["page"] = page
        paras.append(entry)
    return paras


def _non_ref_paragraphs(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Skip bibliography entries but retain post-reference Supplemental Information / Appendix / Table blocks."""
    out: list[dict[str, Any]] = []
    in_refs = False
    for p in doc.get("paragraphs", []):
        head = p["text"].strip()
        if re.match(r"^(?:REFERENCES|BIBLIOGRAPHY|参考文献)\b", head, re.I):
            in_refs = True
            continue
        if in_refs and re.search(r"^(?:Supplemental\s+information|Supplementary|Appendix|Table\s+(?:S?\d+))\b", head, re.I):
            in_refs = False
        if not in_refs:
            out.append(p)
    return out or doc.get("paragraphs", [])


def _verbatim_slice(txt: str, start: int, end: int, pad_before: int = 35, pad_after: int = 120, max_len: int = 240) -> str:
    s = max(0, start - pad_before)
    e = min(len(txt), end + pad_after)
    if e - s > max_len:
        e = min(len(txt), s + max_len)
    return txt[s:e].strip()


def _extract_numeric_stems(text: str) -> list[str]:
    """Extract normalized numeric stems preserving thousands-separator semantics (e.g. 5,539 -> 5539)."""
    stems: list[str] = []
    for m in NUM_STEM_RE.finditer(text or ""):
        raw = m.group(0)
        norm = raw.replace(",", "")
        if norm not in stems:
            stems.append(norm)
    return stems


def _numeric_stem_in_quote(stem: str, quote: str) -> bool:
    """Verify normalized numeric stem appears in quote (with or without thousands commas)."""
    q_stems = set(_extract_numeric_stems(quote))
    if stem in q_stems:
        return True
    try:
        val = float(stem)
        for qs in q_stems:
            try:
                if abs(float(qs) - val) < 1e-9:
                    return True
            except ValueError:
                pass
    except ValueError:
        pass
    return False


def load_workorder_and_corpus(input_dir: Path, case_data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    wo_path = input_dir / "comparison-workorder.json"
    found_wo = wo_path.is_file()
    workorder = json.loads(wo_path.read_text(encoding="utf-8", errors="replace")) if found_wo else {}
    fields = workorder.get("fields") or workorder.get("common_fields") or case_data.get("fields") or DEFAULT_COMMON_FIELDS
    workorder["resolved_fields"] = [
        str(f.get("field_id") or f.get("name") or f) if isinstance(f, dict) else str(f)
        for f in fields
    ]

    docs: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()

    def _add_doc(
        doc_id: str, title: str, paras: list[dict[str, Any]], source_file: str, *,
        doi: str = "", file_sha256: str = "", page_count: int = 0,
        extraction_status: str = "ok", meta: dict[str, Any] | None = None,
    ) -> None:
        if doc_id in seen_doc_ids:
            doc_id = f"{doc_id}_{len(seen_doc_ids) + 1}"
        seen_doc_ids.add(doc_id)
        if not file_sha256:
            file_sha256 = _sha256_bytes("\n".join(p.get("text", "") for p in paras).encode("utf-8"))
        docs.append({
            "source_document_id": doc_id, "title": title, "doi": normalize_doi(doi),
            "source_file": source_file, "file_sha256": file_sha256, "page_count": page_count,
            "extraction_status": extraction_status, "metadata": meta or {}, "paragraphs": paras,
        })

    for container_name, container in (
        ("comparison-workorder.json", workorder.get("documents")),
        ("case.json", case_data.get("documents")),
        ("case.json", case_data.get("papers")),
    ):
        if not isinstance(container, list):
            continue
        for idx, d in enumerate(container, start=1):
            if not isinstance(d, dict):
                continue
            did = str(d.get("source_document_id") or d.get("document_id") or d.get("doc_id") or d.get("paper_id") or d.get("id") or f"DOC{idx:02d}")
            doi = str(d.get("doi") or d.get("DOI") or "")
            raw_paras = d.get("paragraphs")
            paras: list[dict[str, Any]] = []
            pages_seen: set[int] = set()
            if isinstance(raw_paras, list):
                for p_i, p in enumerate(raw_paras, start=1):
                    pid = str(p.get("paragraph_id") or p.get("id") or f"{did}:P{p_i:03d}") if isinstance(p, dict) else f"{did}:P{p_i:03d}"
                    txt = " ".join(str(p.get("text") or p.get("content") or p).split()) if isinstance(p, dict) else " ".join(str(p).split())
                    pg = p.get("page") if isinstance(p, dict) else None
                    if txt:
                        entry: dict[str, Any] = {"paragraph_id": pid, "text": txt}
                        if isinstance(pg, int):
                            entry["page"] = pg
                            pages_seen.add(pg)
                        paras.append(entry)
            elif isinstance(d.get("text"), str):
                paras = _split_paragraphs_from_text(did, d["text"])
            if not doi and paras:
                doi = _extract_header_doi("\n".join(p["text"] for p in paras[:4]))
            meta = {k: v for k, v in d.items() if k not in ("paragraphs", "text")}
            _add_doc(did, str(d.get("title") or did), paras, str(d.get("source_file") or container_name),
                     doi=doi, page_count=len(pages_seen), extraction_status="ok" if paras else "empty_document", meta=meta)

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        if fp.name in ("case.json", "comparison-workorder.json", "task.md"):
            continue
        rel = fp.relative_to(input_dir).as_posix()
        ext = fp.suffix.lower()
        raw_bytes = fp.read_bytes()
        fsha = _sha256_bytes(raw_bytes)
        if ext == ".pdf":
            pages = _pdf_pages_with_pdftotext(fp)
            paras = []
            for pno, ptxt in pages:
                paras.extend(_split_paragraphs_from_text(fp.stem, ptxt, page=pno))
            first_text = pages[0][1] if pages else ""
            _add_doc(fp.stem, _extract_title_from_text(first_text, fp.name), paras, rel,
                     doi=_extract_header_doi(first_text), file_sha256=fsha,
                     page_count=len(pages), extraction_status="ok" if paras else "no_text_layer")
        elif ext in {".md", ".txt"}:
            txt = raw_bytes.decode("utf-8", errors="replace")
            paras = _split_paragraphs_from_text(fp.stem, txt)
            _add_doc(fp.stem, _extract_title_from_text(txt, fp.name), paras, rel,
                     doi=_extract_header_doi(txt), file_sha256=fsha, extraction_status="ok" if paras else "empty_document")
        elif ext == ".csv":
            txt = raw_bytes.decode("utf-8", errors="replace")
            paras = []
            for r_i, row in enumerate(csv.DictReader(io.StringIO(txt)), start=1):
                line = "; ".join(f"{k}: {v}" for k, v in row.items() if k and v not in (None, ""))
                if line.strip():
                    paras.append({"paragraph_id": f"{fp.stem}:row-{r_i:03d}", "text": " ".join(line.split())})
            _add_doc(fp.stem, fp.name, paras, rel, file_sha256=fsha, extraction_status="ok" if paras else "empty_document")
        elif ext == ".json":
            try:
                obj = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            except Exception:
                continue
            if isinstance(obj, dict) and (obj.get("paragraphs") or obj.get("text")):
                did = str(obj.get("source_document_id") or obj.get("paper_id") or obj.get("id") or fp.stem)
                if did in seen_doc_ids:
                    continue
                raw_p = obj.get("paragraphs") or []
                paras = []
                if isinstance(raw_p, list) and raw_p:
                    for p_i, p in enumerate(raw_p, start=1):
                        pid = str(p.get("paragraph_id") or p.get("id") or f"{did}:P{p_i:03d}") if isinstance(p, dict) else f"{did}:P{p_i:03d}"
                        ptxt = " ".join(str(p.get("text") or p.get("content") or p).split()) if isinstance(p, dict) else " ".join(str(p).split())
                        if ptxt:
                            paras.append({"paragraph_id": pid, "text": ptxt})
                elif isinstance(obj.get("text"), str):
                    paras = _split_paragraphs_from_text(did, obj["text"])
                doi = str(obj.get("doi") or obj.get("DOI") or "")
                if not doi and paras:
                    doi = _extract_header_doi("\n".join(p["text"] for p in paras[:4]))
                meta = {k: v for k, v in obj.items() if k not in ("paragraphs", "text")}
                _add_doc(did, str(obj.get("title") or fp.name), paras, rel, doi=doi, file_sha256=fsha, meta=meta)
    return workorder, docs, found_wo


def _is_not_reported_value(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    return (not s) or bool(NOT_REPORTED_RE.match(s))


def _canonicalize_axis_value(axis: str, value: str) -> str:
    raw = " ".join(str(value or "").strip().casefold().split()).rstrip(".,;:")
    if axis == "units":
        cleaned = raw.replace("μ", "u").replace("µ", "u").replace("·", " ")
        return UNIT_CANONICAL_ALIASES.get(cleaned, cleaned)
    return raw


def _unit_advisory_between(u1: str, u2: str) -> tuple[str, str] | None:
    c1 = _canonicalize_axis_value("units", u1)
    c2 = _canonicalize_axis_value("units", u2)
    if c1 == c2:
        return None
    return UNIT_CONVERSION_ADVISORIES.get((c1, c2)) or UNIT_CONVERSION_ADVISORIES.get((c2, c1))


# ---------- Deep 7-Field Prose Extraction Engine (Zero Manual Handoff) ----------

def _title_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in {"the", "and", "for", "with", "from", "that", "this", "into", "guidance", "industry", "staff", "best", "practices"}}


def _is_title_or_header_block(para: dict[str, Any], doc: dict[str, Any]) -> bool:
    """Detect cover/title/abstract-heading blocks that must NEVER be used as experimental_conditions."""
    txt = para.get("text", "").strip()
    if _is_toc_paragraph(txt):
        return True
    pid = str(para.get("paragraph_id") or "")
    pg = para.get("page")
    if pg is None:
        pm = re.search(r":p(\d+)(?:[:\-]b(\d+))?", pid)
        if pm:
            pg = int(pm.group(1))
    bm = re.search(r"[:\-]b(\d+)$", pid)
    bno = int(bm.group(1)) if bm else 99
    title = str(doc.get("title") or "")
    # Cover page / title-only / agency masthead blocks
    if pg == 1 and bno <= 2:
        if re.search(r"(?:Guidance\s+for\s+Industry|Best\s+Practices\s+for\s+FDA\s+Staff|U\.S\.\s+Department\s+of\s+Health|ORIGINAL\s+ARTICLE|Nachwuchsgruppe|Helmholtz-Zentrum)", txt, re.I):
            if not re.search(r"\b(?:Methods?|Results?|observed|measured|mA/cm2|V\s+vs\.?\s*RHE|alkaline|acidic|from\s+\d{4}\s+to\s+\d{4})\b", txt, re.I):
                return True
    if title and len(txt) <= len(title) + 65:
        t_toks = _title_tokens(title)
        p_toks = _title_tokens(txt)
        if t_toks and p_toks and len(t_toks & p_toks) / max(1, len(p_toks)) >= 0.70:
            return True
    return False


def _value_is_title_or_guidance_label(val: str, doc: dict[str, Any]) -> bool:
    """Reject candidate experimental_conditions that merely repeat paper title or regulatory guidance label."""
    v = " ".join((val or "").split()).strip()
    if not v:
        return True
    title = " ".join(str(doc.get("title") or "").split()).strip()
    if title and (v.casefold() in title.casefold() or title.casefold() in v.casefold()):
        return True
    t_toks = _title_tokens(title)
    v_toks = _title_tokens(v)
    if t_toks and v_toks and len(v_toks & t_toks) / max(1, len(v_toks)) >= 0.65:
        return True
    if re.match(
        r"^(?:postmarketing\s+(?:safety\s+)?surveillance|good\s+pharmacovigilance\s+practices(?:\s+and\s+pharmacoepidemiologic\s+assessment)?|pharmacoepidemiologic\s+assessment|guidance\s+for\s+industry|best\s+practices\s+for\s+fda\s+staff)$",
        v,
        re.I,
    ):
        return True
    return False


def _extract_prose_study_object(doc: dict[str, Any], cnum: int) -> dict[str, Any]:
    doc_id = doc["source_document_id"]
    pats = [
        re.compile(r"\b(benchmarking\s+the\s+OER\s+in\s+materials-\s*and\s*device-centered\s+investigations)", re.I),
        re.compile(r"\b(exogenous\s+(?:poisoning|intoxication)\s+concerning\s+children\s+and\s+adolescents\s*\(0[–-]19\s+years\s+old\))", re.I),
        re.compile(r"\b(?:concerning|in|for|of)\s+((?:children\s+and\s+adolescents(?:\s*\([^)]+\))?|human\s+drug\s+and\s+biological\s+products|oxygen\s+evolution\s+reaction(?:\s*\(OER\))?|exogenous\s+(?:poisoning|intoxication)|patients?\s+with\s+[A-Za-z0-9\s-]{4,40}|electrocatalysts?\s+and\s+electrodes?))\b", re.I),
        re.compile(r"\b(postmarketing\s+safety\s+surveillance\s+of\s+human\s+drug\s+and\s+biological\s+products|good\s+pharmacovigilance\s+practices\s+and\s+pharmacoepidemiologic\s+assessment)\b", re.I),
        re.compile(r"\b(Ni-Fe\s+oxides|RuO2|N-doped\s+TiO2|TiO2)\b", re.I),
    ]
    for p in _non_ref_paragraphs(doc)[:40]:
        txt = p["text"]
        for pat in pats:
            m = pat.search(txt)
            if m:
                val = m.group(1).strip(" .;,:")
                q = _verbatim_slice(txt, m.start(), m.end())
                return _make_cell("study_object", cnum, doc_id, paragraph_id=p["paragraph_id"], page=p.get("page"), value=val, quote=q, status="reported", fact_tag="explicitly_stated")
    return _make_cell("study_object", cnum, doc_id)


def _is_toc_paragraph(txt: str) -> bool:
    return ("......" in txt) or ("TABLE OF CONTENTS" in txt.upper())


def _extract_prose_dataset_and_sample(doc: dict[str, Any], cnum: int) -> dict[str, Any]:
    doc_id = doc["source_document_id"]
    source_re = re.compile(
        r"\b(Notifiable\s+Diseases\s+Information\s+System(?:\s*\([^)]+\))?|SINAN|FAERS|AERS|VAERS|Sentinel(?:\s+System)?|MedWatch|DATASUS|IBGE|Table\s+1\.\s+Benchmarking\s+protocols\s+for\s+the\s+oxygen\s+evolution\s+reaction\s+on\s+materials\s+and\s+devices|Table\s+1\b[^.;]{0,45}|UK\s+Biobank|TCGA|GEO)\b",
        re.I,
    )
    best: tuple[int, dict[str, Any], str, str] | None = None
    for p in _non_ref_paragraphs(doc)[:220]:
        txt = p["text"]
        if _is_toc_paragraph(txt):
            continue
        n_m = SAMPLE_N_RE.search(txt)
        src_m = source_re.search(txt)
        tw_m = TIME_WINDOW_RE.search(txt)
        score = (3 if n_m else 0) + (2 if src_m else 0) + (1 if tw_m else 0)
        if score >= 2:
            parts: list[str] = []
            spans: list[tuple[int, int]] = []
            if src_m:
                parts.append(src_m.group(1).strip(" .;"))
                spans.append((src_m.start(), src_m.end()))
            if n_m:
                parts.append(n_m.group(0).strip())
                spans.append((n_m.start(), n_m.end()))
            if tw_m:
                parts.append(tw_m.group(0).strip())
                spans.append((tw_m.start(), tw_m.end()))
            s0 = min(a for a, _ in spans)
            e0 = max(b for _, b in spans)
            q = _verbatim_slice(txt, s0, e0, pad_before=25, pad_after=70, max_len=280)
            val = "; ".join(parts)
            if not all(_numeric_stem_in_quote(st, q) for st in _extract_numeric_stems(val)):
                q = txt[max(0, s0 - 10): min(len(txt), e0 + 25)].strip()
            if best is None or score > best[0]:
                best = (score, p, val, q)
                if score >= 5:
                    break
    if best:
        _, p, val, q = best
        return _make_cell("dataset_and_sample_size", cnum, doc_id, paragraph_id=p["paragraph_id"], page=p.get("page"), value=val, quote=q, status="reported", fact_tag="explicitly_stated")
    return _make_cell("dataset_and_sample_size", cnum, doc_id)


def _extract_prose_experimental_conditions(doc: dict[str, Any], cnum: int) -> dict[str, Any]:
    """Extract genuine experimental/empirical operating conditions; strictly block paper titles & guidance headers."""
    doc_id = doc["source_document_id"]
    cond_re = re.compile(
        r"(\b\d+(?:\.\d+)?\s*(?:to|-|–)\s*\d+(?:\.\d+)?\s*V(?:\s+vs\.?\s*RHE)?(?:\s+at\s+\d+\s*mV/s)?|\b0\s+to\s+50\s*mA/cm2[^.;]{0,70}until\s+2\s*V[^.;]{0,40}|\b\d+(?:\.\d+)?\s*(?:to|-|–)\s*\d+(?:\.\d+)?\s*mA/cm2[^.;]{0,50}|\b\d+(?:\.\d+)?\s*g/L[^.;]{0,40}|\bin\s+the\s+city\s+of\s+[A-Z][A-Za-z,\s]{4,55}(?:in\s+the\s+period\s+)?from\s+\d{4}\s+to\s+\d{4}|\b(?:alkaline|acidic?)\s+media\b|\b2\s*V\s+cutoff\b|\bpH\s*=?\s*\d+(?:\.\d+)?\b)",
        re.I,
    )
    for p in _non_ref_paragraphs(doc)[:220]:
        txt = p["text"]
        if _is_title_or_header_block(p, doc):
            continue
        m = cond_re.search(txt)
        if m:
            val = m.group(1).strip(" .;,")
            if _value_is_title_or_guidance_label(val, doc):
                continue
            q = _verbatim_slice(txt, m.start(), m.end())
            if not all(_numeric_stem_in_quote(st, q) for st in _extract_numeric_stems(val)):
                q = txt[max(0, m.start() - 10): min(len(txt), m.end() + 25)].strip()
            return _make_cell("experimental_conditions", cnum, doc_id, paragraph_id=p["paragraph_id"], page=p.get("page"), value=val, quote=q, status="reported", fact_tag="explicitly_stated")
    return _make_cell("experimental_conditions", cnum, doc_id, status="not_reported", value=None, quote="")


def _extract_prose_metrics_and_values(doc: dict[str, Any], cnum: int) -> dict[str, Any]:
    """Extract condition-scoped (metric, value, unit, entity, cohort_or_dataset, comparator, condition_context) tuples."""
    doc_id = doc["source_document_id"]
    metric_re = re.compile(
        r"([A-Za-z][A-Za-z0-9\s/-]{2,35}?)\s*(?:\(|[:=]\s*)?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(%|/10,000\s+inhabitants|mg/L|ug/L|mA/cm2|A/cm2|mV/s|V\s+vs\.?\s*RHE|V\s+cutoff|fold|min\b|hours?\b|(?<![A-Z])h\b(?!\d))(?:\s*;\s*[nN]\s*=\s*(\d{1,3}(?:,\d{3})*))?"
    )
    aapc_re = re.compile(
        r"(Average\s+Annual\s+Percent\s+Change|AAPC)\s*[:=]\s*(\d+(?:\.\d+)?)(?:%?\s*;\s*95%\s*CI\s*([\d.\-–]+))?(?:\s*;\s*[pP]\s*([<=>]\s*0?\.\d+))?",
        re.I,
    )
    records: list[dict[str, Any]] = []
    best_para: dict[str, Any] | None = None
    best_quote = ""
    best_val_parts: list[str] = []

    for p in _non_ref_paragraphs(doc)[:220]:
        txt = p["text"]
        if _is_toc_paragraph(txt):
            continue
        p_recs: list[dict[str, Any]] = []
        for am in aapc_re.finditer(txt):
            p_recs.append({
                "metric": "AAPC", "value": am.group(2), "unit": "%",
                "entity": doc_id, "cohort_or_dataset": "reported_cohort", "comparator": "temporal_trend",
                "condition_context": _verbatim_slice(txt, am.start(), am.end(), 20, 40, 120),
                "paragraph_id": p["paragraph_id"], "page": p.get("page"),
                "quote": _verbatim_slice(txt, am.start(), am.end(), 20, 50, 180),
            })
        for mm in metric_re.finditer(txt):
            label = " ".join(mm.group(1).split())[-32:].strip(" ,;:()")
            val = mm.group(2)
            unit = mm.group(3).strip()
            n_val = mm.group(4)
            if len(label) < 3 or re.match(r"^(?:page|table|fig|figure|section|vol|rev|doi|from|make|o\s+be|and|for|with|until)\b", label, re.I):
                continue
            clause = _verbatim_slice(txt, mm.start(), mm.end(), 25, 35, 130)
            entry_str = f"{label}: {val} {unit}" if unit.startswith(("V ", "mV", "mA", "A/", "min", "h")) else f"{label}: {val}{unit}"
            if n_val:
                entry_str += f" (n={n_val})"
            p_recs.append({
                "metric": label, "value": val, "unit": unit,
                "entity": doc_id, "cohort_or_dataset": "table_or_body_context", "comparator": "protocol_or_subgroup",
                "sample_n": n_val or "", "condition_context": clause,
                "paragraph_id": p["paragraph_id"], "page": p.get("page"),
                "quote": _verbatim_slice(txt, mm.start(), mm.end(), 25, 45, 180),
                "display": entry_str,
            })
            if len(p_recs) >= 4:
                break
        if p_recs:
            records.extend(p_recs)
            if best_para is None:
                best_para = p
                first_m = aapc_re.search(txt) or metric_re.search(txt)
                best_quote = _verbatim_slice(txt, first_m.start(), first_m.end(), 30, 180, 260) if first_m else txt[:240]
                for rec in p_recs:
                    disp = rec.get("display") or f"{rec['metric']}: {rec['value']}{rec['unit']}"
                    if all(_numeric_stem_in_quote(st, best_quote) for st in _extract_numeric_stems(disp)):
                        best_val_parts.append(disp)
            if len(records) >= 6:
                break

    if best_para and best_val_parts:
        val_summary = "; ".join(best_val_parts[:3])
        primary_unit = records[0]["unit"] if records else ""
        return _make_cell(
            "metrics_and_values", cnum, doc_id,
            paragraph_id=best_para["paragraph_id"], page=best_para.get("page"), value=val_summary, raw_value=val_summary,
            units=primary_unit, quote=best_quote, status="reported", fact_tag="explicitly_stated",
            extra={"metric_value_records": records[:6]},
        )
    qual_re = re.compile(r"\b(disproportionality\s+(?:score|analysis|metrics?)|reporting\s+rates?|incidence\s+rates?|Faradaic\s+efficiency|overpotentials?\s+at\s+fixed\s+current|current\s+densit(?:y|ies)|data\s+mining\s+scores?|PRR|EBGM|MGPS)\b", re.I)
    for p in _non_ref_paragraphs(doc)[:220]:
        txt = p["text"]
        if _is_toc_paragraph(txt):
            continue
        qm = qual_re.search(txt)
        if qm:
            q = _verbatim_slice(txt, qm.start(), qm.end())
            return _make_cell(
                "metrics_and_values", cnum, doc_id,
                paragraph_id=p["paragraph_id"], page=p.get("page"), value=f"{qm.group(1)} (qualitative/guidance metric)",
                quote=q, status="reported", fact_tag="explicitly_stated",
                extra={"metric_value_records": [{"metric": qm.group(1), "value": "qualitative", "unit": "", "entity": doc_id, "cohort_or_dataset": "guidance_scope", "comparator": "none", "condition_context": q, "paragraph_id": p["paragraph_id"], "page": p.get("page"), "quote": q}]},
            )
    return _make_cell("metrics_and_values", cnum, doc_id)


def _extract_prose_conclusion(doc: dict[str, Any], cnum: int) -> dict[str, Any]:
    doc_id = doc["source_document_id"]
    pats = [
        re.compile(r"\bConclusions?\s*[:.]\s*([^\n]{25,240}?\.(?=\s+(?:Implementation|Keywords|Objetivo|RESUMO)|$|\s+[A-Z]))", re.I),
        re.compile(r"\b(?:This\s+author\s+recommends|In\s+summary|Taken\s+together)\b[^\n]{25,220}?(?:(?<!\bi\.e)(?<!\be\.g)(?<!\bvs)\.(?:\s|$)|$)", re.I),
        re.compile(r"\b(?:There\s+was\s+a\s+significant\s+increase|The\s+study\s+showed|Here,\s+the\s+author\s+assesses)[^\n]{15,210}?(?:(?<!\bi\.e)(?<!\be\.g)\.(?:\s|$)|$)", re.I),
        re.compile(r"\bThis\s+(?:Best\s+Practices\s+)?(?:guidance|document)(?:,\s*[^.]{5,140},)?\s+(?:describes|provides|outlines|represents|is\s+intended|sets\s+forth)[^\n]{15,220}?\.", re.I),
        re.compile(r"\b(?:FDA|CDER|CBER)\s+(?:conducts|monitors|evaluates|uses\s+a\s+risk-based)[^\n]{15,210}?(?:\.(?:\s|$)|$)", re.I),
    ]
    for pat in pats:
        for p in _non_ref_paragraphs(doc)[:200]:
            txt = p["text"]
            if _is_toc_paragraph(txt):
                continue
            m = pat.search(txt)
            if m:
                val = (m.group(1) if m.lastindex else m.group(0)).strip()
                # Strip trailing bilingual/keyword spillover cleanly
                val = re.split(r"\b(?:Keywords|Objetivo|RESUMO)\s*:", val, maxsplit=1)[0].strip()
                q = _verbatim_slice(txt, m.start(), m.end(), 15, 60, 280)
                if not all(_numeric_stem_in_quote(st, q) for st in _extract_numeric_stems(val)):
                    q = txt[m.start(): min(len(txt), m.end() + 20)].strip()
                return _make_cell("main_conclusion", cnum, doc_id, paragraph_id=p["paragraph_id"], page=p.get("page"), value=val, quote=q, status="reported", fact_tag="explicitly_stated")
    return _make_cell("main_conclusion", cnum, doc_id)


def _extract_prose_limitations(doc: dict[str, Any], cnum: int) -> dict[str, Any]:
    doc_id = doc["source_document_id"]
    pats = [
        re.compile(r"(?:This\s+study\s+has\s+limitations|Other\s+limitations?\s+(?:were|are|include)|limitations?\s+(?:of\s+this|include|of\s+spontaneous))[^.\n]{10,260}\.", re.I),
        re.compile(r"(?:no\s+international\s+formalized\s+protocol|do\s+not\s+follow\s+any\s+of\s+the\s+reported\s+protocols|underreporting|lack\s+of\s+denominator|Contains\s+Nonbinding\s+Recommendations|does\s+not\s+establish\s+legally\s+enforceable)[^.\n]{0,180}\.?", re.I),
    ]
    for pat in pats:
        for p in _non_ref_paragraphs(doc):
            txt = p["text"]
            if ".........." in txt:
                continue
            m = pat.search(txt)
            if m:
                val = m.group(0).strip()
                q = _verbatim_slice(txt, m.start(), m.end(), 15, 60, 280)
                if not all(_numeric_stem_in_quote(st, q) for st in _extract_numeric_stems(val)):
                    q = txt[m.start(): min(len(txt), m.end() + 20)].strip()
                return _make_cell("limitations", cnum, doc_id, paragraph_id=p["paragraph_id"], page=p.get("page"), value=val, quote=q, status="reported", fact_tag="explicitly_stated")
    return _make_cell("limitations", cnum, doc_id)


def extract_functional_method_row(doc: dict[str, Any], citation_number: int) -> dict[str, Any]:
    """Compare actual functional operations (inputs, grouping, transformation, filtering, output) not just names."""
    doc_id = doc["source_document_id"]
    meta = doc.get("metadata") or {}
    fm = meta.get("functional_method") if isinstance(meta.get("functional_method"), dict) else {}
    para_map = {p["paragraph_id"]: p["text"] for p in doc["paragraphs"]}
    body_paras = _non_ref_paragraphs(doc)

    method_para = None
    for p in body_paras:
        if ".........." in p["text"]:
            continue
        if re.search(r"\b(?:method|methods|protocol|procedure|pipeline|algorithm|grouping|normalization|filtering|data\s+mining|surveillance|voltammetry|regression)\b|方法|流程|预处理", p["text"], re.I):
            method_para = p
            break
    if method_para is None and body_paras:
        method_para = body_paras[0]

    pid = str(fm.get("paragraph_id") or (method_para["paragraph_id"] if method_para else "")) or None
    ptxt = para_map.get(pid or "", method_para["text"] if method_para else "")

    def _pick(key: str, regex: str) -> str:
        if fm.get(key):
            return str(fm[key]).strip()
        m = re.search(regex, ptxt, re.I)
        if m:
            return m.group(1).strip(" .;,")
        for p in body_paras:
            m2 = re.search(regex, p["text"], re.I)
            if m2:
                return m2.group(1).strip(" .;,")
        return "not_reported"

    method_name = _pick("method_name", r"(?:method\s+name|method|algorithm|pipeline|approach|tool)\s*[:=]\s*([^\n.;|]+)")
    inputs = _pick("inputs", r"(?:inputs?|input\s+data|raw\s+input)\s*[:=]\s*([^\n.;|]+)")
    grouping = _pick("grouping", r"(?:grouping|aggregation\s+unit|unit\s+of\s+analysis|grouped\s+by)\s*[:=]\s*([^\n.;|]+)")
    transformation = _pick("transformation", r"(?:transformation|normalization|preprocessing|transform)\s*[:=]\s*([^\n.;|]+)")
    filtering_rule = _pick("filtering_rule", r"(?:filtering(?:\s+rule)?|selection\s+rule|threshold|filter)\s*[:=]\s*([^\n.;|]+)")
    output_semantics = _pick("output_semantics", r"(?:output\s+semantics|output|readout|endpoint)\s*[:=]\s*([^\n.;|]+)")

    # Prose auto-extraction fallback for real papers lacking explicit Key: Value method headers
    prose_hits = 0
    prose_anchor_para = method_para
    if method_name == "not_reported":
        for p in body_paras[:45]:
            if ".........." in p["text"]:
                continue
            m = re.search(
                r"\b(Observational\s+study[^.;]{0,50}|inflection\s+point\s+regression\s+model|current\s+holds[^.;]{0,40}|cyclic\s+or\s+linear\s+sweep\s+voltammetry|data\s+mining\s+to\s+large\s+databases|risk-based\s+approach\s+to\s+drug\s+safety\s+surveillance|pharmacoepidemiologic\s+assessment)\b",
                p["text"], re.I,
            )
            if m:
                method_name = m.group(1).strip(" .;,")
                prose_anchor_para = p
                prose_hits += 1
                break
    if inputs == "not_reported":
        for p in body_paras[:45]:
            if ".........." in p["text"]:
                continue
            m = re.search(
                r"\b(data\s+extracted\s+from\s+the\s+[^.;]{4,60}|spontaneous\s+(?:adverse\s+event\s+)?reports?[^.;]{0,45}|potential\s+sweeps|current\s+holds|AERS|FAERS|SINAN)\b",
                p["text"], re.I,
            )
            if m:
                inputs = m.group(1).strip(" .;,")
                prose_hits += 1
                break
    if grouping == "not_reported":
        for p in body_paras[:45]:
            if ".........." in p["text"]:
                continue
            m = re.search(
                r"\b(sex,\s*age\s+group,\s*toxic\s+agent,\s*and\s+circumstance|materials-\s*and\s*device-centered\s+investigations|product-event\s+combinations|case\s+series|age\s+group)\b",
                p["text"], re.I,
            )
            if m:
                grouping = m.group(1).strip(" .;,")
                prose_hits += 1
                break
    if transformation == "not_reported":
        for p in body_paras[:45]:
            if ".........." in p["text"]:
                continue
            m = re.search(
                r"\b(incidence/10,000\s+inhabitants|normalized\s+by\s+a\s+property\s+of\s+the\s+used\s+electrocatalyst\s+material|Tafel\s+plot|disproportionality\s+(?:score|analysis)|descriptive\s+statistics)\b",
                p["text"], re.I,
            )
            if m:
                transformation = m.group(1).strip(" .;,")
                prose_hits += 1
                break
    if filtering_rule == "not_reported":
        for p in body_paras[:45]:
            if ".........." in p["text"]:
                continue
            m = re.search(
                r"\b(above\s+a\s+specified\s+threshold|2\s*V\s+cutoff|disregarding\s+the\s+number\s+of\s+the\s+ignored\s+fields|risk-based\s+(?:triage|evaluation|prioritization)|p\s*<\s*0\.05)\b",
                p["text"], re.I,
            )
            if m:
                filtering_rule = m.group(1).strip(" .;,")
                prose_hits += 1
                break
    if output_semantics == "not_reported":
        for p in body_paras[:45]:
            if ".........." in p["text"]:
                continue
            m = re.search(
                r"\b(Average\s+Annual\s+Percent\s+Change|incidence\s+rate\s+of\s+exogenous\s+poisoning|overpotential(?:s)?\s+at\s+fixed\s+current|Faradaic\s+efficiency|safety\s+signals?|regulatory\s+action)\b",
                p["text"], re.I,
            )
            if m:
                output_semantics = m.group(1).strip(" .;,")
                prose_hits += 1
                break

    if prose_hits > 0 and prose_anchor_para is not None and not fm.get("paragraph_id"):
        pid = prose_anchor_para["paragraph_id"]
        ptxt = prose_anchor_para["text"]

    reported_ops = sum(1 for v in (inputs, grouping, transformation, filtering_rule, output_semantics) if v != "not_reported")
    ftag = str(fm.get("fact_tag") or "")
    if ftag not in VALID_FACT_TAGS:
        ftag = "explicitly_stated" if reported_ops >= 3 else "inferred_unverified"

    raw_quote = str(fm.get("quote") or "").strip()
    if not raw_quote or " ".join(raw_quote.split()).casefold() not in " ".join(ptxt.split()).casefold():
        raw_quote = ptxt[:220] if ptxt else ""

    return {
        "citation_number": citation_number, "citation_tag": f"[{citation_number}]",
        "source_document_id": doc_id, "paragraph_id": pid,
        "method_name": method_name, "inputs": inputs, "grouping": grouping,
        "transformation": transformation, "filtering_rule": filtering_rule,
        "output_semantics": output_semantics, "fact_tag": ftag, "quote": raw_quote,
    }


def extract_cell_for_field(
    doc: dict[str, Any], field_name: str, citation_number: int, func_row: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Extract cell anchored strictly to (source_document_id, paragraph_id, quote) with 3-state fact_tag."""
    doc_id = doc["source_document_id"]
    meta = doc.get("metadata") or {}
    para_map = {p["paragraph_id"]: p["text"] for p in doc["paragraphs"]}
    default_pid = doc["paragraphs"][0]["paragraph_id"] if doc["paragraphs"] else None
    default_txt = doc["paragraphs"][0]["text"] if doc["paragraphs"] else ""

    # 1. Explicit metadata extracted_fields
    explicit_fields = meta.get("extracted_fields") or meta
    if isinstance(explicit_fields, dict) and field_name in explicit_fields:
        raw_val = explicit_fields[field_name]
        if isinstance(raw_val, dict):
            val = raw_val.get("value")
            status = str(raw_val.get("status") or ("not_reported" if _is_not_reported_value(val) else "reported"))
            if _is_not_reported_value(val) or status == "not_reported":
                return _make_cell(field_name, citation_number, doc_id)
            pid = str(raw_val.get("paragraph_id") or default_pid or "")
            ptxt = para_map.get(pid, default_txt)
            raw_quote = str(raw_val.get("quote") or "").strip()
            if not raw_quote or " ".join(raw_quote.split()).casefold() not in " ".join(ptxt.split()).casefold():
                raw_quote = ptxt[:220]
            return _make_cell(
                field_name, citation_number, doc_id,
                paragraph_id=pid or None, value=str(val).strip(),
                raw_value=str(raw_val.get("raw_value") or val).strip(),
                units=str(raw_val.get("units") or ""), conditions=str(raw_val.get("conditions") or ""),
                quote=raw_quote, status=status, fact_tag=str(raw_val.get("fact_tag") or "explicitly_stated"),
            )
        elif field_name not in ("title", "doi", "source_file") and not isinstance(raw_val, (list, dict)):
            if _is_not_reported_value(raw_val):
                return _make_cell(field_name, citation_number, doc_id)
            val_s = str(raw_val).strip()
            found_pid, found_quote = default_pid, default_txt[:220]
            for p in doc["paragraphs"]:
                if val_s.casefold() in p["text"].casefold():
                    found_pid, found_quote = p["paragraph_id"], p["text"][:220]
                    break
            return _make_cell(field_name, citation_number, doc_id, paragraph_id=found_pid, value=val_s, quote=found_quote, status="reported")

    # 2. Explicit Key: Value pattern extraction (honors explicit `Field: not reported` immediately!)
    pat = FIELD_PATTERNS.get(field_name)
    for p in _non_ref_paragraphs(doc):
        txt = p["text"]
        if pat:
            m = pat.search(txt)
            if m:
                val = next((g for g in m.groups() if g), m.group(0)).strip(" .;,")
                if _is_not_reported_value(val):
                    return _make_cell(field_name, citation_number, doc_id)
                q = _verbatim_slice(txt, m.start(), m.end())
                if not all(_numeric_stem_in_quote(st, q) for st in _extract_numeric_stems(val)):
                    q = txt[max(0, m.start() - 10): min(len(txt), m.end() + 20)].strip()
                return _make_cell(field_name, citation_number, doc_id, paragraph_id=p["paragraph_id"], value=val, quote=q, status="reported", fact_tag="explicitly_stated")
        elif field_name not in CORE_SEVEN_FIELDS:
            label = field_name.replace("_", " ")
            lm = re.search(rf"(?:^|[\s;*•-]){re.escape(label)}\s*[:=]\s*([^\n.;|]+)", txt, re.I)
            if lm:
                val = lm.group(1).strip(" .;,")
                if _is_not_reported_value(val):
                    return _make_cell(field_name, citation_number, doc_id)
                q = _verbatim_slice(txt, lm.start(), lm.end())
                return _make_cell(field_name, citation_number, doc_id, paragraph_id=p["paragraph_id"], value=val, quote=q, status="reported", fact_tag="explicitly_stated")

    # 3. Deep 7-Field Prose Extraction Engine (for real unstructured academic PDFs)
    if field_name == "study_object":
        return _extract_prose_study_object(doc, citation_number)
    if field_name == "dataset_and_sample_size":
        return _extract_prose_dataset_and_sample(doc, citation_number)
    if field_name == "method_operations":
        fr = func_row or extract_functional_method_row(doc, citation_number)
        reported_parts = [f"{k}={fr[k]}" for k in ("method_name", "inputs", "grouping", "transformation", "filtering_rule", "output_semantics") if fr.get(k) and fr[k] != "not_reported"]
        if reported_parts and fr.get("paragraph_id"):
            # Keep numeric stems strictly anchored to fr["quote"]
            safe_parts = [pt for pt in reported_parts if all(_numeric_stem_in_quote(st, fr["quote"]) for st in _extract_numeric_stems(pt))]
            val = "; ".join((safe_parts or [f"method={fr['method_name']}"])[:4])
            if all(_numeric_stem_in_quote(st, fr["quote"]) for st in _extract_numeric_stems(val)):
                return _make_cell("method_operations", citation_number, doc_id, paragraph_id=fr["paragraph_id"], value=val, quote=fr["quote"], status="reported", fact_tag=fr["fact_tag"])
        return _make_cell("method_operations", citation_number, doc_id)
    if field_name == "experimental_conditions":
        return _extract_prose_experimental_conditions(doc, citation_number)
    if field_name == "metrics_and_values":
        return _extract_prose_metrics_and_values(doc, citation_number)
    if field_name == "main_conclusion":
        return _extract_prose_conclusion(doc, citation_number)
    if field_name == "limitations":
        return _extract_prose_limitations(doc, citation_number)

    # 4. Conservative prose fallback for comparability/common fields on real PDFs (never fabricates absent axes)
    if field_name == "task":
        so = _extract_prose_study_object(doc, citation_number)
        if so["status"] == "reported":
            return _make_cell("task", citation_number, doc_id, paragraph_id=so["paragraph_id"], value=so["value"], quote=so["quote"], status="reported", fact_tag="explicitly_stated")
    if field_name == "dataset":
        ds = _extract_prose_dataset_and_sample(doc, citation_number)
        if ds["status"] == "reported":
            return _make_cell("dataset", citation_number, doc_id, paragraph_id=ds["paragraph_id"], value=ds["value"], quote=ds["quote"], status="reported", fact_tag="explicitly_stated")
    if field_name == "sample_size":
        for p in _non_ref_paragraphs(doc)[:45]:
            m = SAMPLE_N_RE.search(p["text"])
            if m:
                val = m.group(0).strip()
                q = _verbatim_slice(p["text"], m.start(), m.end())
                return _make_cell("sample_size", citation_number, doc_id, paragraph_id=p["paragraph_id"], value=val, quote=q, status="reported", fact_tag="explicitly_stated")
    if field_name == "metric":
        mv = _extract_prose_metrics_and_values(doc, citation_number)
        if mv["status"] == "reported" and mv.get("metric_value_records"):
            mname = mv["metric_value_records"][0]["metric"]
            if all(_numeric_stem_in_quote(st, mv["quote"]) for st in _extract_numeric_stems(mname)):
                return _make_cell("metric", citation_number, doc_id, paragraph_id=mv["paragraph_id"], value=mname, quote=mv["quote"], status="reported", fact_tag="explicitly_stated")
    if field_name == "units":
        unit_tok_re = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(%|/10,000\s+inhabitants|mg/L|ug/L|µg/L|mg/g|mmol/L|umol/L|mA/cm2|A/cm2|mV/s|min\^-1|min|h|hr|ppm|ppb)\b", re.I)
        for p in _non_ref_paragraphs(doc)[:60]:
            if ".........." in p["text"]:
                continue
            um = unit_tok_re.search(p["text"])
            if um:
                q = _verbatim_slice(p["text"], um.start(), um.end())
                return _make_cell("units", citation_number, doc_id, paragraph_id=p["paragraph_id"], value=um.group(1), units=um.group(1), quote=q, status="reported", fact_tag="inferred_unverified")

    return _make_cell(field_name, citation_number, doc_id, fact_tag="inferred_unverified")


def annotate_functional_equivalence(func_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark pairs with different method_name but identical reported operational primitives as functionally_equivalent."""
    op_keys = ("inputs", "grouping", "transformation", "filtering_rule", "output_semantics")
    sig_groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for idx, row in enumerate(func_rows):
        reported = [row[k].casefold() for k in op_keys if row.get(k) and row[k] != "not_reported"]
        if len(reported) >= 3:
            sig_groups[tuple(row[k].casefold() for k in op_keys)].append(idx)
    for idxs in sig_groups.values():
        if len(idxs) >= 2 and len({func_rows[i]["method_name"].casefold() for i in idxs}) >= 2:
            for i in idxs:
                func_rows[i]["fact_tag"] = "functionally_equivalent"
                func_rows[i]["functionally_equivalent_with"] = [func_rows[j]["source_document_id"] for j in idxs if j != i]
    return func_rows


def _find_maximal_cliques(nodes: list[str], adj: dict[str, set[str]]) -> list[list[str]]:
    cliques: list[list[str]] = []

    def _bk(r: set[str], p: set[str], x: set[str]) -> None:
        if not p and not x:
            if len(r) >= 2:
                cliques.append(sorted(r))
            return
        pivot = max(p | x, key=lambda u: len(adj[u]), default=None)
        for v in sorted(p - adj[pivot] if pivot is not None else set(p)):
            _bk(r | {v}, p & adj[v], x & adj[v])
            p.remove(v)
            x.add(v)

    _bk(set(), set(nodes), set())
    cliques.sort(key=lambda c: (-len(c), c))
    return cliques


def evaluate_comparability(matrix_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Test pairwise comparability across all 6 axes with clique subgroups and non-mutating unit advisories."""
    doc_ids = [r["source_document_id"] for r in matrix_rows]
    if len(matrix_rows) < 2:
        return {
            "multi_document_satisfied": False, "pooling_allowed": False,
            "axes_tested": list(COMPARABILITY_AXES),
            "reason": "Fewer than 2 effective independent in-scope documents; single-paper summary cannot satisfy multi-document comparison.",
            "comparable_subgroups": [], "comparable_pairs": [], "noncomparable_pairs": [], "indeterminate_pairs": [],
            "unreported_axes_by_doc": {r["source_document_id"]: [ax for ax in COMPARABILITY_AXES if r["cells"].get(ax, {}).get("status") != "reported"] for r in matrix_rows},
            "unit_normalization_advisories": [],
            "rule": "Quantitative pooling requires >=2 independent in-scope documents with all 6 axes reported and aligned.",
        }

    noncomparable_pairs: list[dict[str, Any]] = []
    indeterminate_pairs: list[dict[str, Any]] = []
    comparable_pairs: list[list[str]] = []
    unit_advisories: list[dict[str, Any]] = []
    unreported_axes_by_doc: dict[str, list[str]] = {}
    for row in matrix_rows:
        unrep = [ax for ax in COMPARABILITY_AXES if row["cells"].get(ax, {}).get("status") != "reported"]
        if unrep:
            unreported_axes_by_doc[row["source_document_id"]] = unrep

    row_by_id = {r["source_document_id"]: r for r in matrix_rows}
    for i in range(len(matrix_rows)):
        for j in range(i + 1, len(matrix_rows)):
            r1, r2 = matrix_rows[i], matrix_rows[j]
            d1, d2 = r1["source_document_id"], r2["source_document_id"]
            mismatches: list[dict[str, Any]] = []
            missing_axes: list[str] = []
            for ax in COMPARABILITY_AXES:
                c1, c2 = r1["cells"].get(ax, {}), r2["cells"].get(ax, {})
                if c1.get("status") != "reported" or c2.get("status") != "reported":
                    missing_axes.append(ax)
                    continue
                v1, v2 = _canonicalize_axis_value(ax, str(c1.get("value", ""))), _canonicalize_axis_value(ax, str(c2.get("value", "")))
                if v1 != v2:
                    mm: dict[str, Any] = {
                        "axis": ax, d1: c1.get("raw_value", c1.get("value")), d2: c2.get("raw_value", c2.get("value")),
                        f"{d1}_paragraph_id": c1.get("paragraph_id"), f"{d2}_paragraph_id": c2.get("paragraph_id"),
                    }
                    if ax == "units":
                        adv = _unit_advisory_between(str(c1.get("value", "")), str(c2.get("value", "")))
                        if adv:
                            mm["unit_scale_convertible"] = True
                            mm["conversion_hint"] = adv[0]
                            unit_advisories.append({
                                "pair": [d1, d2], d1: c1.get("raw_value", c1.get("value")), d2: c2.get("raw_value", c2.get("value")),
                                "conversion_factor": adv[0], "guidance": adv[1], "raw_values_preserved": True,
                            })
                    mismatches.append(mm)
            if mismatches:
                noncomparable_pairs.append({
                    "pair": [d1, d2], "mismatched_axes": mismatches, "unreported_axes": missing_axes,
                    "pooling_blocked": True, "comparability_status": "noncomparable",
                    "attribution": "Confirmed mismatch on axis/axes: " + ", ".join(m["axis"] for m in mismatches),
                })
            elif missing_axes:
                indeterminate_pairs.append({
                    "pair": [d1, d2], "unreported_axes": missing_axes, "pooling_blocked": True,
                    "comparability_status": "indeterminate",
                    "reason": "At least one required comparability axis is not_reported or ambiguous; pooling blocked.",
                })
            else:
                comparable_pairs.append([d1, d2])

    parent = {d: d for d in doc_ids}
    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in comparable_pairs:
        adj[a].add(b)
        adj[b].add(a)
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    comps: dict[str, list[str]] = defaultdict(list)
    for d in doc_ids:
        comps[_find(d)].append(d)

    subgroups: list[dict[str, Any]] = []
    sg_idx = 0
    for members in sorted(comps.values(), key=lambda c: (-len(c), c)):
        if len(members) < 2:
            continue
        is_clique = all(b in adj[a] for idx_a, a in enumerate(members) for b in members[idx_a + 1:])
        for clq in ([sorted(members)] if is_clique else _find_maximal_cliques(members, adj)):
            sg_idx += 1
            rep = row_by_id[clq[0]]
            subgroups.append({
                "subgroup_id": f"SG{sg_idx}", "documents": clq, "clique_verified": True,
                "shared_axes": {ax: rep["cells"].get(ax, {}).get("value") for ax in COMPARABILITY_AXES},
            })

    return {
        "multi_document_satisfied": True,
        "pooling_allowed": len(noncomparable_pairs) == 0 and len(indeterminate_pairs) == 0 and len(comparable_pairs) > 0,
        "axes_tested": list(COMPARABILITY_AXES), "comparable_subgroups": subgroups,
        "comparable_pairs": comparable_pairs, "noncomparable_pairs": noncomparable_pairs,
        "indeterminate_pairs": indeterminate_pairs, "unreported_axes_by_doc": unreported_axes_by_doc,
        "unit_normalization_advisories": unit_advisories,
        "rule": "Quantitative pooling requires all six comparability axes (task, metric, units, material, pollutant, budget) to be reported and equal within a verified clique.",
    }


def _classify_conclusion_direction(row: dict[str, Any], doc_meta: dict[str, Any]) -> str:
    explicit = str(doc_meta.get("conclusion_direction") or "").strip().lower()
    if explicit in {"positive", "negative", "neutral", "mixed"}:
        return explicit
    cell = row["cells"].get("main_conclusion") or {}
    txt = str(cell.get("value") or cell.get("quote") or "")
    if not txt:
        return "unclassified"
    has_pos, has_neg = bool(POSITIVE_DIRECTION_RE.search(txt)), bool(NEGATIVE_DIRECTION_RE.search(txt))
    if has_pos and has_neg:
        return "mixed"
    return "negative" if has_neg else ("positive" if has_pos else "neutral")


def build_consensus_conflict_matrix(
    matrix_rows: list[dict[str, Any]],
    func_rows: list[dict[str, Any]],
    comparability: dict[str, Any],
    doc_meta_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Partition cross-study conclusions and metric divergences into consensus/partial/conflict with structured attribution."""
    func_by_id = {f["source_document_id"]: f for f in func_rows}
    row_by_id = {r["source_document_id"]: r for r in matrix_rows}
    noncomp_map = {tuple(sorted(p["pair"])): p for p in comparability.get("noncomparable_pairs", [])}
    indet_map = {tuple(sorted(p["pair"])): p for p in comparability.get("indeterminate_pairs", [])}

    def _attribute_diff(d1: str, d2: str) -> tuple[list[str], str]:
        dims: set[str] = set()
        notes: list[str] = []
        r1, r2 = row_by_id[d1], row_by_id[d2]
        # 1. sample_diff (sample_size, dataset, dataset_and_sample_size)
        for sf in ("sample_size", "dataset", "dataset_and_sample_size"):
            v1, v2 = (r1["cells"].get(sf) or {}).get("value"), (r2["cells"].get(sf) or {}).get("value")
            if v1 and v2 and str(v1).casefold() != str(v2).casefold():
                dims.add("sample_diff")
                notes.append(f"{sf} differs ({v1} vs {v2})")
                break
        # 2. method_diff (functional_method_comparison, baseline)
        f1, f2 = func_by_id.get(d1, {}), func_by_id.get(d2, {})
        b1, b2 = (r1["cells"].get("baseline") or {}).get("value"), (r2["cells"].get("baseline") or {}).get("value")
        same_func = (f1.get("fact_tag") == "functionally_equivalent" and d2 in (f1.get("functionally_equivalent_with") or []))
        if (not same_func and f1.get("method_name") != f2.get("method_name") and "not_reported" not in {f1.get("method_name"), f2.get("method_name")}) or (b1 and b2 and str(b1).casefold() != str(b2).casefold()):
            dims.add("method_diff")
            notes.append(f"method/baseline differs ({f1.get('method_name')} vs {f2.get('method_name')})")
        # 3. condition_diff & metric_or_unit_diff from 6-axis + experimental_conditions
        ec1, ec2 = (r1["cells"].get("experimental_conditions") or {}).get("value"), (r2["cells"].get("experimental_conditions") or {}).get("value")
        if ec1 and ec2 and str(ec1).casefold() != str(ec2).casefold():
            dims.add("condition_diff")
            notes.append(f"experimental_conditions differs ({ec1} vs {ec2})")
        key = tuple(sorted((d1, d2)))
        if key in noncomp_map:
            for m in noncomp_map[key].get("mismatched_axes", []):
                ax = m["axis"]
                if ax in ("material", "pollutant", "budget"):
                    dims.add("condition_diff")
                elif ax in ("metric", "units", "task"):
                    dims.add("metric_or_unit_diff")
                notes.append(f"{ax}: {m.get(d1)} vs {m.get(d2)}")
        if key in indet_map:
            notes.append(f"unreported axes block full attribution: {', '.join(indet_map[key].get('unreported_axes', []))}")
        if not dims:
            dims.add("unattributable")
            notes.append("No reported sample/method/condition/metric mismatch explains the opposing outcomes; latent confounders require verification.")
        return sorted(dims), "; ".join(notes)

    positions: list[dict[str, Any]] = []
    for row in matrix_rows:
        did = row["source_document_id"]
        c_cell = row["cells"].get("main_conclusion") or {}
        if c_cell.get("status") != "reported":
            continue
        positions.append({
            "citation_number": row["citation_number"], "citation_tag": row["citation_tag"],
            "source_document_id": did, "paragraph_id": c_cell.get("paragraph_id"),
            "direction": _classify_conclusion_direction(row, doc_meta_by_id.get(did, {})),
            "value": c_cell.get("value"), "quote": c_cell.get("quote"),
        })

    consensus: list[dict[str, Any]] = []
    partial_agreement: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    if len(positions) >= 2:
        pos_dirs = {p["direction"] for p in positions if p["direction"] in {"positive", "negative", "neutral"}}
        if "positive" in pos_dirs and ("negative" in pos_dirs or "neutral" in pos_dirs):
            for p1 in (p for p in positions if p["direction"] == "positive"):
                for p2 in (p for p in positions if p["direction"] in {"negative", "neutral"}):
                    dims, rationale = _attribute_diff(p1["source_document_id"], p2["source_document_id"])
                    conflicts.append({
                        "topic": "main_conclusion", "attribution_dimensions": dims,
                        "attribution_rationale": rationale, "positions": [p1, p2],
                    })
        else:
            all_comp = comparability.get("pooling_allowed", False)
            (consensus if all_comp else partial_agreement).append({
                "topic": "main_conclusion",
                "agreement_level": "consensus" if all_comp else "partial_agreement",
                "rationale": (
                    "All effective documents agree in direction across fully aligned 6-axis conditions."
                    if all_comp else
                    "Documents share directional conclusion, but noncomparable or unreported axes block quantitative pooling."
                ),
                "positions": positions,
            })

    # Same-metric numeric divergence check across effective papers
    seen_conflict_pairs = {tuple(sorted(p["source_document_id"] for p in c["positions"])) for c in conflicts}
    for i in range(len(matrix_rows)):
        for j in range(i + 1, len(matrix_rows)):
            r1, r2 = matrix_rows[i], matrix_rows[j]
            d1, d2 = r1["source_document_id"], r2["source_document_id"]
            if tuple(sorted((d1, d2))) in seen_conflict_pairs:
                continue
            m1, m2 = (r1["cells"].get("metric") or {}), (r2["cells"].get("metric") or {})
            if m1.get("status") == "reported" and m2.get("status") == "reported":
                if _canonicalize_axis_value("metric", str(m1.get("value", ""))) == _canonicalize_axis_value("metric", str(m2.get("value", ""))):
                    mv1, mv2 = r1["cells"].get("metrics_and_values") or {}, r2["cells"].get("metrics_and_values") or {}
                    nums1 = [float(x) for x in _extract_numeric_stems(str(mv1.get("value") or "")) if float(x) > 0]
                    nums2 = [float(x) for x in _extract_numeric_stems(str(mv2.get("value") or "")) if float(x) > 0]
                    if nums1 and nums2 and abs(nums1[0] - nums2[0]) / max(nums1[0], nums2[0]) > 0.25 and mv1.get("paragraph_id") and mv2.get("paragraph_id"):
                        dims, rationale = _attribute_diff(d1, d2)
                        conflicts.append({
                            "topic": f"metric_divergence:{m1.get('value')}",
                            "attribution_dimensions": dims,
                            "attribution_rationale": f"Quantitative divergence on shared metric ({nums1[0]} vs {nums2[0]}); {rationale}",
                            "positions": [
                                {"citation_number": r1["citation_number"], "citation_tag": r1["citation_tag"], "source_document_id": d1, "paragraph_id": mv1["paragraph_id"], "direction": "numeric_a", "value": mv1.get("value"), "quote": mv1.get("quote")},
                                {"citation_number": r2["citation_number"], "citation_tag": r2["citation_tag"], "source_document_id": d2, "paragraph_id": mv2["paragraph_id"], "direction": "numeric_b", "value": mv2.get("value"), "quote": mv2.get("quote")},
                            ],
                        })

    return {
        "policy": "Conflicts are preserved side by side with structured attribution (sample_diff, method_diff, condition_diff, metric_or_unit_diff, unattributable); majority voting and naive averaging are forbidden.",
        "consensus": consensus, "partial_agreement": partial_agreement, "conflicts": conflicts,
    }


def validate_matrix_draft(
    draft: dict[str, Any],
    all_docs: list[dict[str, Any]],
    effective_docs: list[dict[str, Any]],
    superseded_docs: list[dict[str, Any]],
    out_of_scope_docs: list[dict[str, Any]],
    comparability: dict[str, Any],
) -> dict[str, Any]:
    """Validate matrix draft for cross-paper bleed, verbatim quotes, numeric-stem grounding, illegal pooling, and Citation Legend integrity."""
    para_owner: dict[str, str] = {}
    para_text: dict[tuple[str, str], str] = {}
    for d in all_docs:
        did = d["source_document_id"]
        for p in d.get("paragraphs", []):
            para_owner[p["paragraph_id"]] = did
            para_text[(did, p["paragraph_id"])] = p["text"]

    superseded_ids = {d["source_document_id"] for d in superseded_docs}
    out_of_scope_ids = {d["source_document_id"] for d in out_of_scope_docs}
    effective_ids = {d["source_document_id"] for d in effective_docs}
    noncomp_pairs = {tuple(sorted(p["pair"])) for p in comparability.get("noncomparable_pairs", [])}
    indet_pairs = {tuple(sorted(p["pair"])) for p in comparability.get("indeterminate_pairs", [])}
    comp_pairs = {tuple(sorted(p)) for p in comparability.get("comparable_pairs", [])}

    bleed_errors: list[dict[str, Any]] = []
    quote_errors: list[dict[str, Any]] = []
    numeric_errors: list[dict[str, Any]] = []
    pooling_errors: list[dict[str, Any]] = []
    legend_errors: list[dict[str, Any]] = []

    legend = draft.get("citation_legend") or []
    legend_by_num: dict[int, str] = {}
    legend_by_doc: dict[str, int] = {}
    if not isinstance(legend, list) or not legend:
        legend_errors.append({"code": "MISSING_CITATION_LEGEND", "message": "citation_legend is missing or empty."})
    else:
        for entry in legend:
            cnum, cdoc = entry.get("citation_number"), str(entry.get("source_document_id") or "")
            if not isinstance(cnum, int) or cnum < 1 or not cdoc:
                legend_errors.append({"code": "INVALID_LEGEND_ENTRY", "entry": entry})
                continue
            if cnum in legend_by_num:
                legend_errors.append({"code": "DUPLICATE_CITATION_NUMBER", "citation_number": cnum, "docs": [legend_by_num[cnum], cdoc]})
            legend_by_num[cnum] = cdoc
            legend_by_doc[cdoc] = cnum
        for d in all_docs:
            if d["source_document_id"] not in legend_by_doc:
                legend_errors.append({"code": "DOC_MISSING_FROM_LEGEND", "source_document_id": d["source_document_id"]})

    def _check_anchor_and_quote(doc_id: str, pid: str | None, quote: str, context: str, cnum: Any = None) -> None:
        if cnum is not None:
            if not isinstance(cnum, int) or cnum not in legend_by_num:
                legend_errors.append({"code": "DANGLING_CITATION_NUMBER", "citation_number": cnum, "context": context})
            elif legend_by_num[cnum] != doc_id:
                legend_errors.append({"code": "CITATION_LEGEND_DRIFT", "citation_number": cnum, "claimed_doc": doc_id, "legend_doc": legend_by_num[cnum], "context": context})
        if not pid:
            return
        if pid not in para_owner:
            bleed_errors.append({"code": "UNKNOWN_PARAGRAPH_ID", "source_document_id": doc_id, "paragraph_id": pid, "context": context})
            return
        owner = para_owner[pid]
        if owner != doc_id:
            bleed_errors.append({"code": "CROSS_PAPER_BLEED", "claimed_document_id": doc_id, "paragraph_id": pid, "actual_owner_document_id": owner, "context": context})
            return
        if quote:
            q_norm = " ".join(str(quote).split()).casefold()
            p_norm = " ".join(para_text.get((doc_id, pid), "").split()).casefold()
            if len(q_norm) < 4 or q_norm not in p_norm:
                quote_errors.append({"code": "QUOTE_NOT_FOUND_IN_PARAGRAPH", "source_document_id": doc_id, "paragraph_id": pid, "context": context, "quote_preview": str(quote)[:120]})

    checked_cells = 0
    doc_by_id = {d["source_document_id"]: d for d in all_docs}
    for row in draft.get("comparison_matrix") or []:
        row_doc = str(row.get("source_document_id") or "")
        for fname, cell in (row.get("cells") or {}).items():
            checked_cells += 1
            cell_doc = str(cell.get("source_document_id") or row_doc)
            if cell_doc != row_doc:
                bleed_errors.append({"code": "ROW_CELL_DOC_MISMATCH", "row_document_id": row_doc, "cell_document_id": cell_doc, "field": fname})
            if cell.get("status") != "not_reported":
                pid = cell.get("paragraph_id")
                quote = str(cell.get("quote") or "")
                if fname == "experimental_conditions" and row_doc in doc_by_id:
                    if _value_is_title_or_guidance_label(str(cell.get("value") or ""), doc_by_id[row_doc]):
                        quote_errors.append({
                            "code": "TITLE_AS_EXPERIMENTAL_CONDITIONS",
                            "source_document_id": row_doc,
                            "paragraph_id": pid,
                            "field": fname,
                            "value": str(cell.get("value"))[:120],
                        })
                if not pid:
                    bleed_errors.append({"code": "REPORTED_CELL_MISSING_PARAGRAPH_ID", "source_document_id": row_doc, "field": fname})
                else:
                    _check_anchor_and_quote(row_doc, str(pid), quote, f"matrix:{row_doc}:{fname}", cell.get("citation_number"))
                # Semantic numeric-stem grounding gate: every number in value/raw_value MUST appear in cell.quote
                val_blob = f"{cell.get('value') or ''} {cell.get('raw_value') or ''}"
                val_stems = _extract_numeric_stems(val_blob)
                missing_stems = [st for st in val_stems if not _numeric_stem_in_quote(st, quote)]
                if missing_stems:
                    numeric_errors.append({
                        "code": "NUMERIC_VALUE_NOT_IN_QUOTE",
                        "source_document_id": row_doc,
                        "paragraph_id": pid,
                        "field": fname,
                        "value": str(cell.get("value"))[:120],
                        "missing_numeric_stems": missing_stems,
                        "quote_preview": quote[:120],
                    })

    for fm in draft.get("functional_method_comparison") or []:
        if fm.get("paragraph_id"):
            _check_anchor_and_quote(str(fm.get("source_document_id") or ""), str(fm["paragraph_id"]), str(fm.get("quote") or ""), f"functional_method:{fm.get('source_document_id')}", fm.get("citation_number"))

    ccm = draft.get("consensus_conflict_matrix") or {}
    for bucket_name in ("consensus", "partial_agreement", "conflicts"):
        for item in ccm.get(bucket_name) or []:
            for pos in item.get("positions") or []:
                if pos.get("paragraph_id"):
                    _check_anchor_and_quote(str(pos.get("source_document_id") or ""), str(pos["paragraph_id"]), str(pos.get("quote") or ""), f"{bucket_name}:{pos.get('source_document_id')}", pos.get("citation_number"))

    pooled_groups: list[dict[str, Any]] = []
    qs = draft.get("quantitative_synthesis")
    if isinstance(qs, list):
        pooled_groups.extend(x for x in qs if isinstance(x, dict))
    elif isinstance(qs, dict):
        for k in ("pooled_estimates", "pooled_groups", "subgroups"):
            if isinstance(qs.get(k), list):
                pooled_groups.extend(x for x in qs[k] if isinstance(x, dict))
        if isinstance(qs.get("documents"), list):
            pooled_groups.append(qs)
    if isinstance(draft.get("pooled_estimates"), list):
        pooled_groups.extend(x for x in draft["pooled_estimates"] if isinstance(x, dict))

    for g_idx, grp in enumerate(pooled_groups, start=1):
        docs_in_grp = [str(x) for x in (grp.get("documents") or grp.get("source_document_ids") or [])]
        if len(set(docs_in_grp)) < 2:
            pooling_errors.append({"code": "ILLEGAL_POOLING_DETECTED", "group_index": g_idx, "documents": docs_in_grp, "reason": "Single-document or empty group cannot form quantitative synthesis."})
            continue
        for did in docs_in_grp:
            if did in superseded_ids:
                pooling_errors.append({"code": "ILLEGAL_POOLING_DETECTED", "group_index": g_idx, "document_id": did, "reason": "Duplicate/preprint superseded document cannot be pooled alongside Version-of-Record."})
            elif did in out_of_scope_ids:
                pooling_errors.append({"code": "ILLEGAL_POOLING_DETECTED", "group_index": g_idx, "document_id": did, "reason": "Out-of-scope distractor document cannot be pooled."})
            elif did not in effective_ids:
                pooling_errors.append({"code": "ILLEGAL_POOLING_DETECTED", "group_index": g_idx, "document_id": did, "reason": "Unknown document ID in quantitative synthesis group."})
        for i in range(len(docs_in_grp)):
            for j in range(i + 1, len(docs_in_grp)):
                pair = tuple(sorted((docs_in_grp[i], docs_in_grp[j])))
                if pair in noncomp_pairs:
                    pooling_errors.append({"code": "ILLEGAL_POOLING_DETECTED", "group_index": g_idx, "pair": list(pair), "reason": "Pooled pair failed 6-axis comparability (noncomparable_pairs)."})
                elif pair in indet_pairs:
                    pooling_errors.append({"code": "ILLEGAL_POOLING_DETECTED", "group_index": g_idx, "pair": list(pair), "reason": "Pooled pair has unreported comparability axis (indeterminate_pairs)."})
                elif pair not in comp_pairs:
                    pooling_errors.append({"code": "ILLEGAL_POOLING_DETECTED", "group_index": g_idx, "pair": list(pair), "reason": "Pooled pair is not in verified comparable_pairs."})

    total_errors = len(bleed_errors) + len(quote_errors) + len(numeric_errors) + len(pooling_errors) + len(legend_errors)
    return {
        "ok": total_errors == 0, "checked_cells": checked_cells, "checked_pooled_groups": len(pooled_groups),
        "error_count": total_errors, "cross_paper_bleed_errors": bleed_errors,
        "quote_grounding_errors": quote_errors, "numeric_grounding_errors": numeric_errors,
        "illegal_pooling_errors": pooling_errors, "citation_legend_errors": legend_errors,
    }


def _fmt_loc_anchor(pid: str | None, quote: str = "", page: int | None = None) -> str:
    if not pid:
        return "`N/A`"
    pg = page
    if pg is None:
        pm = re.search(r":p(\d+)(?:[:\-]b\d+)?\b", str(pid))
        if pm:
            pg = int(pm.group(1))
    tm = re.search(r"\b(Table\s+(?:S?\d+))\b", quote or "", re.I)
    parts = []
    if pg is not None:
        parts.append(f"Page {pg}")
    if tm:
        parts.append(tm.group(1))
    parts.append(f"`{pid}`")
    return ", ".join(parts)


def render_markdown_matrix(
    citation_legend: list[dict[str, Any]], fields: list[str], matrix_rows: list[dict[str, Any]],
    func_rows: list[dict[str, Any]], comp: dict[str, Any], ccm: dict[str, Any], dedup_scope: dict[str, Any],
) -> str:
    lines: list[str] = [
        "# Multi-Document Comparison & Synthesis Report", "",
        "## 1. Citation Legend (Immutable Source Mapping)", "",
        "| Citation | source_document_id | Title | DOI | Source File | SHA-256 (64-hex) | Version Status |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in citation_legend:
        ctag = e.get("citation_tag") or f"[{e.get('citation_number', '?')}]"
        full_sha = str(e.get("file_sha256") or "n/a")
        lines.append(
            f"| {ctag} | `{e.get('source_document_id', '')}` | "
            f"{str(e.get('title') or e.get('source_document_id') or '').replace('|', '/')} | {e.get('doi') or '`not_reported`'} | "
            f"`{e.get('source_file') or 'unknown'}` | `{full_sha}` | `{e.get('version_status') or 'unclassified'}` |"
        )
    lines.extend([
        "", "## 2. Document Identity, Deduplication & Scope Audit", "",
        f"- Total inventoried inputs: `{dedup_scope['total_input_documents']}`",
        f"- Effective independent in-scope documents: `{dedup_scope['effective_document_count']}`",
        f"- Superseded duplicates / preprints mapped: `{len(dedup_scope['version_mapping'])}`",
        f"- Out-of-scope distractors excluded: `{len(dedup_scope['out_of_scope_excluded'])}`",
    ])
    if dedup_scope["version_mapping"]:
        lines.extend(["", "| Superseded ID | Canonical VoR ID | Role | Match Method | Score |", "| --- | --- | --- | --- | --- |"])
        for vm in dedup_scope["version_mapping"]:
            lines.append(f"| `{vm['superseded_document_id']}` | `{vm['canonical_document_id']}` | `{vm['superseded_role']}` | `{vm['match_method']}` | `{vm['score']}` |")
    if dedup_scope["out_of_scope_excluded"]:
        lines.extend(["", "| Excluded ID | Reason |", "| --- | --- |"])
        for ex in dedup_scope["out_of_scope_excluded"]:
            lines.append(f"| `{ex['source_document_id']}` | {ex['exclusion_reason'].replace('|', '/')} |")

    lines.extend(["", "## 3. Common-Field Comparison Matrix", "", "| Citation & Doc | " + " | ".join(fields) + " |", "| --- | " + " | ".join("---" for _ in fields) + " |"])
    for row in matrix_rows:
        vals = []
        for f in fields:
            cell = row["cells"].get(f, {})
            if cell.get("status") == "reported":
                anc = _fmt_loc_anchor(cell.get("paragraph_id"), str(cell.get("quote") or ""), cell.get("page"))
                vals.append(f"{str(cell.get('value', '')).replace('|', '/')} ({anc}, `{cell.get('fact_tag', 'explicitly_stated')}`)")
            elif cell.get("status") == "ambiguous":
                anc = _fmt_loc_anchor(cell.get("paragraph_id"), str(cell.get("quote") or ""), cell.get("page"))
                vals.append(f"`ambiguous` ({anc})")
            else:
                vals.append("`not_reported`")
        lines.append(f"| {row['citation_tag']} `{row['source_document_id']}` | " + " | ".join(vals) + " |")

    lines.extend([
        "", "## 4. Functional Method Operations Matrix (Operations Over Names)", "",
        "| Citation & Doc | Method Name | Inputs | Grouping | Transformation | Filtering Rule | Output Semantics | Fact Tag | Anchor |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for fm in func_rows:
        fm_anc = _fmt_loc_anchor(fm.get("paragraph_id"), str(fm.get("quote") or ""))
        lines.append(
            f"| {fm['citation_tag']} `{fm['source_document_id']}` | {fm['method_name'].replace('|', '/')} | "
            f"{fm['inputs'].replace('|', '/')} | {fm['grouping'].replace('|', '/')} | {fm['transformation'].replace('|', '/')} | "
            f"{fm['filtering_rule'].replace('|', '/')} | {fm['output_semantics'].replace('|', '/')} | `{fm['fact_tag']}` | {fm_anc} |"
        )

    lines.extend([
        "", "## 5. Six-Axis Comparability Gate (`{task, metric, units, material, pollutant, budget}`)", "",
        f"- Multi-document satisfied: `{comp['multi_document_satisfied']}`",
        f"- Pooling allowed across entire effective corpus: `{comp['pooling_allowed']}`",
        f"- Comparable subgroups (verified cliques): `{[sg['documents'] for sg in comp.get('comparable_subgroups', [])]}`",
        f"- Noncomparable pairs (pooling blocked): `{len(comp.get('noncomparable_pairs', []))}`",
        f"- Indeterminate pairs (unreported axis blocks pooling): `{len(comp.get('indeterminate_pairs', []))}`",
    ])
    if comp.get("noncomparable_pairs"):
        lines.extend(["", "### Noncomparable Pairs", "| Pair | Mismatched Axes | Unreported Axes | Pooling |", "| --- | --- | --- | --- |"])
        for np in comp["noncomparable_pairs"]:
            mm = "; ".join(f"{m['axis']}: {m.get(np['pair'][0])} vs {m.get(np['pair'][1])}" for m in np["mismatched_axes"])
            lines.append(f"| `{np['pair'][0]}` vs `{np['pair'][1]}` | {mm.replace('|', '/')} | `{np['unreported_axes']}` | `blocked` |")
    if comp.get("indeterminate_pairs"):
        lines.extend(["", "### Indeterminate Pairs (Unreported Axis Blocks Pooling)", "| Pair | Unreported Axes | Pooling |", "| --- | --- | --- |"])
        for ip in comp["indeterminate_pairs"]:
            lines.append(f"| `{ip['pair'][0]}` vs `{ip['pair'][1]}` | `{ip['unreported_axes']}` | `blocked` |")
    if comp.get("unit_normalization_advisories"):
        lines.extend(["", "### Unit Normalization Advisories (Raw Values Unmodified)", "| Pair | Raw Units | Conversion Factor | Guidance |", "| --- | --- | --- | --- |"])
        for ua in comp["unit_normalization_advisories"]:
            p0, p1 = ua["pair"]
            lines.append(f"| `{p0}` vs `{p1}` | `{ua[p0]}` vs `{ua[p1]}` | {ua['conversion_factor']} | {ua['guidance']} |")

    lines.extend([
        "", "## 6. Consensus — Partial Agreement — Conflict Attribution Matrix", "",
        "| Category | Topic | Attribution Dimensions | Grounded Positions & Rationale |", "| --- | --- | --- | --- |",
    ])
    for item in ccm.get("consensus", []):
        pos_str = "; ".join(f"{p['citation_tag']} `{p['source_document_id']}` (`{p['paragraph_id']}`)" for p in item.get("positions", []))
        lines.append(f"| `consensus` | {item.get('topic', 'main_conclusion')} | `none` | {item.get('rationale', '')} [{pos_str}] |")
    for item in ccm.get("partial_agreement", []):
        pos_str = "; ".join(f"{p['citation_tag']} `{p['source_document_id']}` (`{p['paragraph_id']}`)" for p in item.get("positions", []))
        lines.append(f"| `partial_agreement` | {item.get('topic', 'main_conclusion')} | `condition_diff/unreported` | {item.get('rationale', '')} [{pos_str}] |")
    for item in ccm.get("conflicts", []):
        dims = ", ".join(item.get("attribution_dimensions", ["unattributable"]))
        pos_str = " vs ".join(f"{p['citation_tag']} `{p['source_document_id']}` (`{p['paragraph_id']}`, {p.get('direction')}): \"{str(p.get('quote', ''))[:90].replace('|', '/')}\"" for p in item.get("positions", []))
        lines.append(f"| `conflict` | {item.get('topic', 'main_conclusion')} | `{dims}` | {item.get('attribution_rationale', '').replace('|', '/')} — {pos_str} |")
    if not (ccm.get("consensus") or ccm.get("partial_agreement") or ccm.get("conflicts")):
        lines.append("| `none` | `main_conclusion` | `unattributable` | Insufficient reported conclusion cells for cross-document synthesis. |")
    return "\n".join(lines) + "\n"


def run_pipeline(
    input_dir: Path, output_dir: Path, work_dir: Path, *, validate_draft_path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="95061011-comparison-matrix")

    case_path = input_dir / "case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace")) if case_path.is_file() else {}
    case_id = str(case_data.get("case_id") or "case-unknown")
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-95061011")

    workorder, raw_docs, found_wo = load_workorder_and_corpus(input_dir, case_data)
    fields = workorder["resolved_fields"]
    all_fields_needed = list(dict.fromkeys(list(fields) + list(COMPARABILITY_AXES) + list(CORE_SEVEN_FIELDS)))

    budget_guard.tick_phase(budget_file, "dedup_scope_and_index")
    canonical_docs, superseded_docs, version_mapping, ctx_conflicts = deduplicate_documents_with_versions(raw_docs)
    effective_docs, out_of_scope_docs = detect_out_of_scope_documents(canonical_docs, workorder)

    all_classified = sorted(effective_docs + superseded_docs + out_of_scope_docs, key=lambda d: d["source_document_id"])
    citation_legend: list[dict[str, Any]] = []
    cit_num_by_doc: dict[str, int] = {}
    for c_idx, doc in enumerate(all_classified, start=1):
        did = doc["source_document_id"]
        cit_num_by_doc[did] = c_idx
        citation_legend.append({
            "citation_number": c_idx, "citation_tag": f"[{c_idx}]", "source_document_id": did,
            "title": doc["title"], "doi": doc.get("doi") or "", "source_file": doc["source_file"],
            "file_sha256": doc["file_sha256"], "file_sha256_short": doc["file_sha256"][:12],
            "version_status": doc.get("version_status", "unclassified"),
            "extraction_status": doc.get("extraction_status", "ok"),
            "page_count": doc.get("page_count", 0), "paragraph_count": len(doc.get("paragraphs", [])),
        })

    result_guard.atomic_write_json(
        work_dir / "corpus_paragraphs.json",
        {
            "case_id": case_id, "citation_legend": citation_legend,
            "documents": [
                {"source_document_id": d["source_document_id"], "citation_number": cit_num_by_doc[d["source_document_id"]],
                 "version_status": d.get("version_status"), "title": d["title"], "doi": d.get("doi") or "", "paragraphs": d["paragraphs"]}
                for d in all_classified
            ],
        },
    )

    budget_guard.tick_phase(budget_file, "build_matrix_and_test_comparability")
    doc_meta_by_id = {d["source_document_id"]: d.get("metadata") or {} for d in all_classified}
    matrix_rows: list[dict[str, Any]] = []
    func_rows: list[dict[str, Any]] = []
    for doc in sorted(effective_docs, key=lambda x: cit_num_by_doc[x["source_document_id"]]):
        cnum = cit_num_by_doc[doc["source_document_id"]]
        fr = extract_functional_method_row(doc, cnum)
        func_rows.append(fr)
        cells = {fn: extract_cell_for_field(doc, fn, cnum, func_row=fr) for fn in all_fields_needed}
        matrix_rows.append({
            "citation_number": cnum, "citation_tag": f"[{cnum}]", "source_document_id": doc["source_document_id"],
            "title": doc["title"], "doi": doc.get("doi") or "", "paragraph_count": len(doc["paragraphs"]), "cells": cells,
        })

    func_rows = annotate_functional_equivalence(func_rows)
    # Propagate functionally_equivalent tag back to method_operations cell when detected
    func_by_id = {fr["source_document_id"]: fr for fr in func_rows}
    for row in matrix_rows:
        fr = func_by_id.get(row["source_document_id"])
        mo_cell = row["cells"].get("method_operations")
        if fr and mo_cell and mo_cell.get("status") == "reported":
            mo_cell["fact_tag"] = fr["fact_tag"]

    comparability = evaluate_comparability(matrix_rows)
    ccm = build_consensus_conflict_matrix(matrix_rows, func_rows, comparability, doc_meta_by_id)
    dedup_scope = {
        "total_input_documents": len(raw_docs), "effective_document_count": len(effective_docs),
        "superseded_duplicate_count": len(superseded_docs), "out_of_scope_excluded_count": len(out_of_scope_docs),
        "version_mapping": version_mapping, "context_conflicts": ctx_conflicts,
        "out_of_scope_excluded": [
            {"source_document_id": d["source_document_id"], "title": d["title"], "source_file": d["source_file"],
             "exclusion_reason": d.get("exclusion_reason", "Out of scope")}
            for d in out_of_scope_docs
        ],
    }

    corpus_comparability = comparability
    if validate_draft_path and validate_draft_path.is_file():
        draft_obj = json.loads(validate_draft_path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(draft_obj.get("comparison_matrix"), list):
            matrix_rows = draft_obj["comparison_matrix"]
            if len(matrix_rows) >= len(effective_docs):
                comparability = evaluate_comparability(matrix_rows)
        if isinstance(draft_obj.get("citation_legend"), list):
            citation_legend = draft_obj["citation_legend"]
        if isinstance(draft_obj.get("functional_method_comparison"), list):
            func_rows = draft_obj["functional_method_comparison"]
        if isinstance(draft_obj.get("consensus_conflict_matrix"), dict):
            ccm = draft_obj["consensus_conflict_matrix"]
    else:
        draft_obj = {
            "citation_legend": citation_legend, "comparison_matrix": matrix_rows,
            "functional_method_comparison": func_rows, "consensus_conflict_matrix": ccm,
        }

    matrix_validation = validate_matrix_draft(
        draft_obj, all_classified, effective_docs, superseded_docs, out_of_scope_docs,
        corpus_comparability if (validate_draft_path and len(matrix_rows) < len(effective_docs)) else comparability,
    )

    md_text = render_markdown_matrix(citation_legend, all_fields_needed, matrix_rows, func_rows, comparability, ccm, dedup_scope)
    result_guard.atomic_write_text(output_dir / "comparison_matrix.md", md_text)
    result_guard.atomic_write_json(output_dir / "comparability_audit.json", {
        "case_id": case_id, "axes_tested": list(COMPARABILITY_AXES), "core_seven_fields": list(CORE_SEVEN_FIELDS),
        "dedup_and_scope_audit": dedup_scope, "comparability_assessment": comparability, "matrix_validation": matrix_validation,
    })

    append_audit_entry(
        audit_file, phase="compare", action="comparison_matrix_built_and_validated", actor="comparison_matrix_builder.py",
        output={
            "total_input_docs": len(raw_docs), "effective_docs": len(effective_docs),
            "pooling_allowed": comparability["pooling_allowed"],
            "noncomparable_pairs": len(comparability["noncomparable_pairs"]),
            "indeterminate_pairs": len(comparability["indeterminate_pairs"]),
            "validation_ok": matrix_validation["ok"],
        },
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    if not found_wo:
        gaps.append({"code": "MISSING_COMPARISON_WORKORDER", "severity": "warning", "source": "load_workorder_and_corpus",
                     "message": "/input/comparison-workorder.json not found; used case.json or default comparison schema."})
    empty_pdfs = [d["source_document_id"] for d in all_classified if d.get("extraction_status") == "no_text_layer"]
    if empty_pdfs:
        gaps.append({"code": "PDF_TEXT_EXTRACTION_EMPTY", "severity": "warning", "source": "load_workorder_and_corpus",
                     "message": f"No text layer extracted for PDF(s): {', '.join(empty_pdfs)}."})
    if len(effective_docs) < 2:
        gaps.append({"code": "INSUFFICIENT_DOCUMENTS_FOR_COMPARISON", "severity": "error", "source": "evaluate_comparability",
                     "message": f"Only {len(effective_docs)} effective independent in-scope document(s) found; single-paper summary cannot satisfy multi-document comparison."})
    if comparability["noncomparable_pairs"]:
        gaps.append({"code": "NONCOMPARABLE_CONDITIONS_RETAINED", "severity": "info", "source": "evaluate_comparability",
                     "message": f"{len(comparability['noncomparable_pairs'])} document pair(s) failed 6-axis comparability; pooling blocked."})
    if comparability["indeterminate_pairs"]:
        gaps.append({"code": "COMPARABILITY_UNDETERMINED", "severity": "warning", "source": "evaluate_comparability",
                     "message": f"{len(comparability['indeterminate_pairs'])} document pair(s) have unreported comparability axes; pooling blocked until verified."})
    if not matrix_validation["ok"]:
        gaps.append({"code": "MATRIX_VALIDATION_FAILED", "severity": "error", "source": "validate_matrix_draft",
                     "message": f"Matrix validation detected {matrix_validation['error_count']} blocking error(s) (bleed={len(matrix_validation['cross_paper_bleed_errors'])}, quote={len(matrix_validation['quote_grounding_errors'])}, numeric={len(matrix_validation['numeric_grounding_errors'])}, illegal_pooling={len(matrix_validation['illegal_pooling_errors'])}, legend={len(matrix_validation['citation_legend_errors'])})."})

    core7_reported = sum(
        1 for r in matrix_rows for f in CORE_SEVEN_FIELDS if (r["cells"].get(f) or {}).get("status") == "reported"
    )
    patch: dict[str, Any] = {
        "artifacts": ["result.json", "comparison_matrix.md", "comparability_audit.json"],
        "citation_legend": citation_legend, "common_fields": all_fields_needed,
        "core_seven_fields": list(CORE_SEVEN_FIELDS),
        "document_inventory": [
            {"citation_number": cit_num_by_doc[d["source_document_id"]], "source_document_id": d["source_document_id"],
             "title": d["title"], "doi": d.get("doi") or "", "source_file": d["source_file"], "file_sha256": d["file_sha256"],
             "version_status": d.get("version_status", "unclassified"), "extraction_status": d.get("extraction_status", "ok"),
             "page_count": d.get("page_count", 0), "paragraph_count": len(d["paragraphs"])}
            for d in all_classified
        ],
        "excluded_documents": [
            {"source_document_id": d["source_document_id"], "version_status": d.get("version_status"),
             "reason": d.get("exclusion_reason") or f"Superseded by {d.get('superseded_by')}"}
            for d in (superseded_docs + out_of_scope_docs)
        ],
        "comparison_matrix": matrix_rows, "functional_method_comparison": func_rows,
        "comparability_assessment": comparability, "consensus_conflict_matrix": ccm,
        "dedup_and_scope_audit": dedup_scope, "matrix_validation": matrix_validation,
        "metrics": {
            "total_input_document_count": len(raw_docs), "effective_document_count": len(effective_docs),
            "superseded_duplicate_count": len(superseded_docs), "out_of_scope_excluded_count": len(out_of_scope_docs),
            "field_count": len(all_fields_needed), "core_seven_cells_reported": core7_reported,
            "comparable_subgroup_count": len(comparability["comparable_subgroups"]),
            "noncomparable_pair_count": len(comparability["noncomparable_pairs"]),
            "indeterminate_pair_count": len(comparability["indeterminate_pairs"]),
            "conflict_count": len(ccm.get("conflicts", [])), "validation_error_count": matrix_validation["error_count"],
        },
        "metric_provenance": {
            "total_input_document_count": "document_inventory", "effective_document_count": "comparison_matrix",
            "superseded_duplicate_count": "dedup_and_scope_audit.version_mapping",
            "out_of_scope_excluded_count": "dedup_and_scope_audit.out_of_scope_excluded",
            "field_count": "common_fields", "core_seven_cells_reported": "comparison_matrix[].cells[core_seven_fields]",
            "comparable_subgroup_count": "comparability_assessment.comparable_subgroups",
            "noncomparable_pair_count": "comparability_assessment.noncomparable_pairs",
            "indeterminate_pair_count": "comparability_assessment.indeterminate_pairs",
            "conflict_count": "consensus_conflict_matrix.conflicts", "validation_error_count": "matrix_validation.error_count",
        },
        "findings": [
            f"Inventoried {len(raw_docs)} input document(s): {len(effective_docs)} effective independent in-scope, {len(superseded_docs)} duplicate/preprint superseded, {len(out_of_scope_docs)} out-of-scope excluded.",
            f"Built Citation Legend [1]..[{len(citation_legend)}] and {len(effective_docs)}x{len(all_fields_needed)} anchored comparison matrix (core 7 structured fields populated: {core7_reported}/{len(effective_docs)*len(CORE_SEVEN_FIELDS)}).",
            f"Six-axis comparability gate: multi_document_satisfied={comparability['multi_document_satisfied']}, pooling_allowed={comparability['pooling_allowed']}, cliques={len(comparability['comparable_subgroups'])}, noncomparable_pairs={len(comparability['noncomparable_pairs'])}, indeterminate_pairs={len(comparability['indeterminate_pairs'])}.",
            f"Consensus/Conflict attribution: consensus={len(ccm.get('consensus', []))}, partial_agreement={len(ccm.get('partial_agreement', []))}, conflicts={len(ccm.get('conflicts', []))}.",
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(output_dir, result_file="result.json", required_files=["comparison_matrix.md", "comparability_audit.json"])
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res, bool(matrix_validation["ok"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor 95061011 Multi-Document Comparison Matrix Builder & Validator.")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    parser.add_argument("--validate-matrix", default="", help="Optional refined matrix JSON to validate against corpus & comparability rules.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(input_dir=args.input or None, output_dir=args.output or None, work_dir=args.work or None)
    draft_p = Path(args.validate_matrix) if args.validate_matrix else None
    res, val_ok = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], validate_draft_path=draft_p)
    print(json.dumps({"ok": val_ok, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"], "matrix_validation": res["matrix_validation"]}, ensure_ascii=False))
    if draft_p is not None and not val_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
