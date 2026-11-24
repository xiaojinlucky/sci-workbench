"""Multi-Document Common-Field Comparison Matrix, 6-Axis Comparability Gate & Bleed Validator (challenge-95061011).

Pure Python stdlib (zero third-party Python dependencies; uses system `pdftotext` for PDFs).
Implements Organizer Revision v2:
- Reads /input/comparison-workorder.json and ALL supplied corpus documents (JSON/MD/TXT/CSV/PDF).
- Detects duplicate/preprint-vs-published versions and isolates out-of-scope distractors with trace logs.
- Builds a deterministic Citation Legend ([1]..[N] -> source_document_id, title, DOI, file SHA-256, version).
- Extracts common fields anchored to exact (source_document_id, paragraph_id, quote) with 3-state fact tags.
- Compares actual functional operations (inputs, grouping, transformation, filtering_rule, output_semantics).
- Runs strict 6-axis comparability ({task, metric, units, material, pollutant, budget}) with clique clustering
  and non-mutating unit normalization advisories; blocks pooling on any mismatch or unreported axis.
- Produces Consensus / Partial Agreement / Conflict Attribution 3-column matrix without majority voting.
- Validates agent-refined drafts via `--validate-matrix` (cross-paper bleed, verbatim quotes, illegal pooling,
  and Citation Legend integrity).
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
DEFAULT_COMMON_FIELDS = [
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
VALID_ATTRIBUTION_DIMS = {"sample", "method", "condition", "metric", "unattributable"}

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.I)
NOT_REPORTED_RE = re.compile(
    r"^(?:not[\s_-]*reported|unreported|unspecified|unknown|none|n/?a|nd|n\.d\.|nr|未报告|未说明|未知|无记录|—|-)$",
    re.I,
)

FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "task": re.compile(r"(?:^|[\s;*•-])(?:task|objective|study\s+task|assay\s+task|任务|研究任务)\s*[:=]\s*([^\n.;|]+)", re.I),
    "material": re.compile(r"(?:^|[\s;*•-])(?:material|catalyst|adsorbent|model\s+architecture|polymer|composite|platform|材料|催化剂)\s*[:=]\s*([^\n.;|]+)", re.I),
    "pollutant": re.compile(r"(?:^|[\s;*•-])(?:pollutant|dye|contaminant|target\s+analyte|analyte|substrate|target\s+pollutant|污染物|目标染料)\s*[:=]\s*([^\n.;|]+)", re.I),
    "dataset": re.compile(r"(?:^|[\s;*•-])(?:dataset|benchmark|cohort|database|数据集|队列)\s*[:=]\s*([^\n.;|]+)", re.I),
    "metric": re.compile(r"(?:^|[\s;*•-])(?:metric|primary\s+metric|endpoint|evaluation\s+metric|指标|评价指标)\s*[:=]\s*([^\n.;|]+)", re.I),
    "units": re.compile(r"(?:^|[\s;*•-])(?:units?|measurement\s+unit|单位)\s*[:=]\s*([^\n.;|]+)", re.I),
    "budget": re.compile(r"(?:^|[\s;*•-])(?:budget|experimental\s+budget|dosage|catalyst\s+loading|compute\s+budget|reaction\s+budget|预算|剂量与时间)\s*[:=]\s*([^\n.;|]+)", re.I),
    "baseline": re.compile(r"(?:^|[\s;*•-])(?:baseline|comparator|control\s+group|reference\s+method|基线|对照)\s*[:=]\s*([^\n.;|]+)", re.I),
    "sample_size": re.compile(r"(?:(?:sample\s+size|cohort\s+size|样本量)\s*[:=]\s*([^\n.;|]+)|\b[nN]\s*=\s*(\d+(?:,\d{3})*)\b)", re.I),
    "main_conclusion": re.compile(r"(?:^|[\s;*•-])(?:main\s+conclusion|conclusion|key\s+finding|finding|主要结论|结论)\s*[:=]\s*([^\n]+)", re.I),
    "limitations": re.compile(r"(?:^|[\s;*•-])(?:limitations?|drawback|caveat|局限性?)\s*[:=]\s*([^\n]+)", re.I),
    "method_name": re.compile(r"(?:^|[\s;*•-])(?:method\s+name|method|algorithm|pipeline|approach|方法)\s*[:=]\s*([^\n.;|]+)", re.I),
}

UNIT_CANONICAL_ALIASES: dict[str, str] = {
    "%": "%",
    "percent": "%",
    "percentage": "%",
    "pct": "%",
    "mg/l": "mg/l",
    "mg l-1": "mg/l",
    "mg·l-1": "mg/l",
    "mg per l": "mg/l",
    "ug/l": "ug/l",
    "µg/l": "ug/l",
    "μg/l": "ug/l",
    "ug l-1": "ug/l",
    "g/l": "g/l",
    "g l-1": "g/l",
    "mg/g": "mg/g",
    "mg g-1": "mg/g",
    "mg·g-1": "mg/g",
    "mmol/l": "mmol/l",
    "mm": "mmol/l",
    "umol/l": "umol/l",
    "µmol/l": "umol/l",
    "μmol/l": "umol/l",
    "um": "umol/l",
    "µm": "umol/l",
    "min": "min",
    "mins": "min",
    "minute": "min",
    "minutes": "min",
    "h": "h",
    "hr": "h",
    "hrs": "h",
    "hour": "h",
    "hours": "h",
    "s": "s",
    "sec": "s",
    "secs": "s",
    "second": "s",
    "seconds": "s",
    "min-1": "min^-1",
    "min^-1": "min^-1",
    "1/min": "min^-1",
    "ppm": "ppm",
    "ppb": "ppb",
    "cases": "cases",
    "reports": "reports",
    "score": "score",
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
}

POSITIVE_DIRECTION_RE = re.compile(
    r"\b(?:improv(?:ed|es|ing)|increas(?:ed|es)|outperform(?:ed|s)|superior|effective|enhanc(?:ed|es)|accelerates?|higher\s+removal|significant(?:ly)?\s+reduc(?:ed|es)\s+(?:pollutant|dye|error|false\s+discover)|recommended|strong\s+support|outstrips?)\b|显著提高|优于|有效提升|显著降低误差",
    re.I,
)
NEGATIVE_DIRECTION_RE = re.compile(
    r"\b(?:no\s+significant\s+(?:improvement|difference|effect|benefit)|fail(?:ed|s)\s+to|ineffective|inferior|worse(?:ned)?|degraded|inhibited|suppress(?:ed|es)\s+activity|contradict(?:ed|s)|did\s+not\s+(?:improve|outperform|increase)|prone\s+to\s+false\s+discoveries|biased|poor\s+removal|low\s+efficiency)\b|无显著差异|无效|劣于|未改善|明显下降|抑制活性",
    re.I,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_header_doi(text: str) -> str:
    """Extract DOI only from top header lines to avoid capturing bibliography DOIs."""
    for ln in (text or "").splitlines()[:25]:
        if re.search(r"(?:^|\b)(?:document\s+)?doi\s*[:=]|doi\.org/", ln, re.I):
            m = DOI_RE.search(ln)
            if m:
                return normalize_doi(m.group(1))
    return ""


def _extract_title_from_text(text: str, fallback: str) -> str:
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
        if len(s) >= 8 and not re.match(r"^(?:doi|author|date|page|http|task|material|pollutant)\s*[:=]", s, re.I):
            return s[:200]
    return fallback


def _pdf_pages_with_pdftotext(pdf_path: Path) -> list[tuple[int, str]]:
    """Extract per-page text from PDF using pdftotext (or optional pdfplumber/pypdf if present)."""
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
                raw_pages = proc.stdout.split("\f")
                pages = [(idx, chunk) for idx, chunk in enumerate(raw_pages, start=1) if chunk.strip()]
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
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        pages = [(idx, p.extract_text() or "") for idx, p in enumerate(reader.pages, start=1)]
        return [(i, t) for i, t in pages if t.strip()]
    except Exception:
        pass
    return []


def _split_paragraphs_from_text(doc_id: str, text: str, page: int | None = None) -> list[dict[str, Any]]:
    paras: list[dict[str, Any]] = []
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    # Fallback for single-newline-delimited PDF pages with dense paragraphs
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
        pid = f"{doc_id}:p{page}-b{bno}" if page is not None else f"{doc_id}:P{bno:03d}"
        entry: dict[str, Any] = {"paragraph_id": pid, "text": clean}
        if page is not None:
            entry["page"] = page
        paras.append(entry)
    return paras


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
        doc_id: str,
        title: str,
        paras: list[dict[str, Any]],
        source_file: str,
        *,
        doi: str = "",
        file_sha256: str = "",
        page_count: int = 0,
        extraction_status: str = "ok",
        meta: dict[str, Any] | None = None,
    ) -> None:
        base_id = doc_id
        if doc_id in seen_doc_ids:
            doc_id = f"{base_id}_{len(seen_doc_ids) + 1}"
        seen_doc_ids.add(doc_id)
        if not file_sha256:
            joined = "\n".join(p.get("text", "") for p in paras).encode("utf-8")
            file_sha256 = _sha256_bytes(joined)
        docs.append({
            "source_document_id": doc_id,
            "title": title,
            "doi": normalize_doi(doi),
            "source_file": source_file,
            "file_sha256": file_sha256,
            "page_count": page_count,
            "extraction_status": extraction_status,
            "metadata": meta or {},
            "paragraphs": paras,
        })

    # 1. Structured documents embedded inside comparison-workorder.json or case.json
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
            did = str(
                d.get("source_document_id")
                or d.get("document_id")
                or d.get("doc_id")
                or d.get("paper_id")
                or d.get("id")
                or f"DOC{idx:02d}"
            )
            title = str(d.get("title") or did)
            doi = str(d.get("doi") or d.get("DOI") or "")
            raw_paras = d.get("paragraphs")
            paras: list[dict[str, Any]] = []
            pages_seen: set[int] = set()
            if isinstance(raw_paras, list):
                for p_i, p in enumerate(raw_paras, start=1):
                    if isinstance(p, dict):
                        pid = str(p.get("paragraph_id") or p.get("id") or f"{did}:P{p_i:03d}")
                        txt = " ".join(str(p.get("text") or p.get("content") or "").split())
                        pg = p.get("page")
                    else:
                        pid = f"{did}:P{p_i:03d}"
                        txt = " ".join(str(p).split())
                        pg = None
                    if txt:
                        entry: dict[str, Any] = {"paragraph_id": pid, "text": txt}
                        if isinstance(pg, int):
                            entry["page"] = pg
                            pages_seen.add(pg)
                        paras.append(entry)
            elif isinstance(d.get("text"), str):
                paras = _split_paragraphs_from_text(did, d["text"])
            if not doi and paras:
                doi = _extract_header_doi("\n".join(p["text"] for p in paras[:3]))
            meta = {k: v for k, v in d.items() if k not in ("paragraphs", "text")}
            _add_doc(
                did,
                title,
                paras,
                str(d.get("source_file") or container_name),
                doi=doi,
                page_count=len(pages_seen),
                extraction_status="ok" if paras else "empty_document",
                meta=meta,
            )

    # 2. Additional corpus files in input_dir (PDF / MD / TXT / CSV / JSON)
    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        if fp.name in ("case.json", "comparison-workorder.json", "task.md"):
            continue
        rel = fp.relative_to(input_dir).as_posix()
        ext = fp.suffix.lower()
        raw_bytes = fp.read_bytes()
        fsha = _sha256_bytes(raw_bytes)
        if ext == ".pdf":
            pages = _pdf_pages_with_pdftotext(fp)
            paras: list[dict[str, Any]] = []
            for pno, ptxt in pages:
                paras.extend(_split_paragraphs_from_text(fp.stem, ptxt, page=pno))
            first_text = pages[0][1] if pages else ""
            title = _extract_title_from_text(first_text, fp.name)
            doi = _extract_header_doi(first_text)
            _add_doc(
                fp.stem,
                title,
                paras,
                rel,
                doi=doi,
                file_sha256=fsha,
                page_count=len(pages),
                extraction_status="ok" if paras else "no_text_layer",
            )
        elif ext in {".md", ".txt", ".tex"}:
            txt = raw_bytes.decode("utf-8", errors="replace")
            paras = _split_paragraphs_from_text(fp.stem, txt)
            title = _extract_title_from_text(txt, fp.name)
            doi = _extract_header_doi(txt)
            _add_doc(fp.stem, title, paras, rel, doi=doi, file_sha256=fsha, extraction_status="ok" if paras else "empty_document")
        elif ext == ".csv":
            txt = raw_bytes.decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(txt))
            paras = []
            for r_i, row in enumerate(reader, start=1):
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
                    doi = _extract_header_doi("\n".join(p["text"] for p in paras[:3]))
                meta = {k: v for k, v in obj.items() if k not in ("paragraphs", "text")}
                _add_doc(did, str(obj.get("title") or fp.name), paras, rel, doi=doi, file_sha256=fsha, meta=meta)

    return workorder, docs, found_wo


def _is_not_reported_value(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    if not s:
        return True
    return bool(NOT_REPORTED_RE.match(s))


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
    if (c1, c2) in UNIT_CONVERSION_ADVISORIES:
        return UNIT_CONVERSION_ADVISORIES[(c1, c2)]
    if (c2, c1) in UNIT_CONVERSION_ADVISORIES:
        return UNIT_CONVERSION_ADVISORIES[(c2, c1)]
    return None


def extract_cell_for_field(doc: dict[str, Any], field_name: str, citation_number: int) -> dict[str, Any]:
    """Extract cell anchored strictly to (source_document_id, paragraph_id, quote) with 3-state fact_tag."""
    doc_id = doc["source_document_id"]
    meta = doc.get("metadata") or {}
    para_map = {p["paragraph_id"]: p["text"] for p in doc["paragraphs"]}
    default_pid = doc["paragraphs"][0]["paragraph_id"] if doc["paragraphs"] else None
    default_txt = doc["paragraphs"][0]["text"] if doc["paragraphs"] else ""

    explicit_fields = meta.get("extracted_fields") or meta
    if isinstance(explicit_fields, dict) and field_name in explicit_fields:
        raw_val = explicit_fields[field_name]
        if isinstance(raw_val, dict):
            val = raw_val.get("value")
            status = str(raw_val.get("status") or ("not_reported" if _is_not_reported_value(val) else "reported"))
            if _is_not_reported_value(val) or status == "not_reported":
                return {
                    "field": field_name,
                    "citation_number": citation_number,
                    "source_document_id": doc_id,
                    "paragraph_id": None,
                    "value": None,
                    "raw_value": None,
                    "units": "",
                    "conditions": "",
                    "quote": "",
                    "status": "not_reported",
                    "fact_tag": "explicitly_stated",
                }
            pid = str(raw_val.get("paragraph_id") or default_pid or "")
            ptxt = para_map.get(pid, default_txt)
            raw_quote = str(raw_val.get("quote") or "").strip()
            if not raw_quote or " ".join(raw_quote.split()).casefold() not in " ".join(ptxt.split()).casefold():
                raw_quote = ptxt[:200]
            ftag = str(raw_val.get("fact_tag") or "explicitly_stated")
            if ftag not in VALID_FACT_TAGS:
                ftag = "explicitly_stated"
            return {
                "field": field_name,
                "citation_number": citation_number,
                "source_document_id": doc_id,
                "paragraph_id": pid or None,
                "value": str(val).strip(),
                "raw_value": str(raw_val.get("raw_value") or val).strip(),
                "units": str(raw_val.get("units") or ""),
                "conditions": str(raw_val.get("conditions") or ""),
                "quote": raw_quote,
                "status": status if status in VALID_CELL_STATUSES else "reported",
                "fact_tag": ftag,
            }
        elif field_name not in ("title", "doi", "source_file") and not isinstance(raw_val, (list, dict)):
            if _is_not_reported_value(raw_val):
                return {
                    "field": field_name,
                    "citation_number": citation_number,
                    "source_document_id": doc_id,
                    "paragraph_id": None,
                    "value": None,
                    "raw_value": None,
                    "units": "",
                    "conditions": "",
                    "quote": "",
                    "status": "not_reported",
                    "fact_tag": "explicitly_stated",
                }
            val_s = str(raw_val).strip()
            # Find paragraph containing val_s for verbatim grounding
            found_pid = default_pid
            found_quote = default_txt[:200]
            for p in doc["paragraphs"]:
                if val_s.casefold() in p["text"].casefold():
                    found_pid = p["paragraph_id"]
                    found_quote = p["text"][:200]
                    break
            return {
                "field": field_name,
                "citation_number": citation_number,
                "source_document_id": doc_id,
                "paragraph_id": found_pid,
                "value": val_s,
                "raw_value": val_s,
                "units": "",
                "conditions": "",
                "quote": found_quote,
                "status": "reported",
                "fact_tag": "explicitly_stated",
            }

    pat = FIELD_PATTERNS.get(field_name)
    for p in doc["paragraphs"]:
        txt = p["text"]
        if pat:
            m = pat.search(txt)
            if m:
                val = next((g for g in m.groups() if g), m.group(0)).strip(" .;,")
                if _is_not_reported_value(val):
                    return {
                        "field": field_name,
                        "citation_number": citation_number,
                        "source_document_id": doc_id,
                        "paragraph_id": None,
                        "value": None,
                        "raw_value": None,
                        "units": "",
                        "conditions": "",
                        "quote": "",
                        "status": "not_reported",
                        "fact_tag": "explicitly_stated",
                    }
                # Take verbatim slice centered around the match so quote is guaranteed substring
                s_idx = max(0, m.start() - 40)
                e_idx = min(len(txt), m.end() + 120)
                quote = txt[s_idx:e_idx].strip()
                return {
                    "field": field_name,
                    "citation_number": citation_number,
                    "source_document_id": doc_id,
                    "paragraph_id": p["paragraph_id"],
                    "value": val,
                    "raw_value": val,
                    "units": "",
                    "conditions": "",
                    "quote": quote,
                    "status": "reported",
                    "fact_tag": "explicitly_stated",
                }
        else:
            label = field_name.replace("_", " ")
            lm = re.search(rf"(?:^|[\s;*•-]){re.escape(label)}\s*[:=]\s*([^\n.;|]+)", txt, re.I)
            if lm:
                val = lm.group(1).strip(" .;,")
                if _is_not_reported_value(val):
                    continue
                s_idx = max(0, lm.start() - 30)
                e_idx = min(len(txt), lm.end() + 100)
                return {
                    "field": field_name,
                    "citation_number": citation_number,
                    "source_document_id": doc_id,
                    "paragraph_id": p["paragraph_id"],
                    "value": val,
                    "raw_value": val,
                    "units": "",
                    "conditions": "",
                    "quote": txt[s_idx:e_idx].strip(),
                    "status": "reported",
                    "fact_tag": "explicitly_stated",
                }

    # Secondary fallback for units: explicit measurement unit token only if field_name == "units"
    if field_name == "units":
        unit_tok_re = re.compile(r"\b\d+(?:\.\d+)?\s*(%|mg/L|ug/L|µg/L|mg/g|mmol/L|umol/L|min\^-1|min|h|hr|ppm|ppb)\b", re.I)
        for p in doc["paragraphs"]:
            um = unit_tok_re.search(p["text"])
            if um:
                s_idx = max(0, um.start() - 30)
                e_idx = min(len(p["text"]), um.end() + 90)
                return {
                    "field": "units",
                    "citation_number": citation_number,
                    "source_document_id": doc_id,
                    "paragraph_id": p["paragraph_id"],
                    "value": um.group(1),
                    "raw_value": um.group(1),
                    "units": um.group(1),
                    "conditions": "",
                    "quote": p["text"][s_idx:e_idx].strip(),
                    "status": "reported",
                    "fact_tag": "inferred_unverified",
                }

    return {
        "field": field_name,
        "citation_number": citation_number,
        "source_document_id": doc_id,
        "paragraph_id": None,
        "value": None,
        "raw_value": None,
        "units": "",
        "conditions": "",
        "quote": "",
        "status": "not_reported",
        "fact_tag": "inferred_unverified",
    }


def extract_functional_method_row(doc: dict[str, Any], citation_number: int) -> dict[str, Any]:
    """Compare actual functional operations (inputs, grouping, transformation, filtering, output) not just names."""
    doc_id = doc["source_document_id"]
    meta = doc.get("metadata") or {}
    fm = meta.get("functional_method") if isinstance(meta.get("functional_method"), dict) else {}
    para_map = {p["paragraph_id"]: p["text"] for p in doc["paragraphs"]}

    # Locate best method paragraph
    method_para = None
    for p in doc["paragraphs"]:
        if re.search(r"\b(?:method|methods|protocol|procedure|pipeline|algorithm|grouping|normalization|filtering|data\s+mining|surveillance)\b|方法|流程|预处理", p["text"], re.I):
            method_para = p
            break
    if method_para is None and doc["paragraphs"]:
        method_para = doc["paragraphs"][0]

    pid = str(fm.get("paragraph_id") or (method_para["paragraph_id"] if method_para else "")) or None
    ptxt = para_map.get(pid or "", method_para["text"] if method_para else "")

    def _pick(key: str, regex: str) -> str:
        if fm.get(key):
            return str(fm[key]).strip()
        m = re.search(regex, ptxt, re.I)
        if m:
            return m.group(1).strip(" .;,")
        for p in doc["paragraphs"]:
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

    reported_ops = sum(
        1 for v in (inputs, grouping, transformation, filtering_rule, output_semantics) if v != "not_reported"
    )
    ftag = str(fm.get("fact_tag") or "")
    if ftag not in VALID_FACT_TAGS:
        ftag = "explicitly_stated" if reported_ops >= 3 else "inferred_unverified"

    raw_quote = str(fm.get("quote") or "").strip()
    if not raw_quote or " ".join(raw_quote.split()).casefold() not in " ".join(ptxt.split()).casefold():
        raw_quote = ptxt[:200] if ptxt else ""

    return {
        "citation_number": citation_number,
        "citation_tag": f"[{citation_number}]",
        "source_document_id": doc_id,
        "paragraph_id": pid,
        "method_name": method_name,
        "inputs": inputs,
        "grouping": grouping,
        "transformation": transformation,
        "filtering_rule": filtering_rule,
        "output_semantics": output_semantics,
        "fact_tag": ftag,
        "quote": raw_quote,
    }


def annotate_functional_equivalence(func_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark pairs with different method_name but identical reported operational primitives as functionally_equivalent."""
    op_keys = ("inputs", "grouping", "transformation", "filtering_rule", "output_semantics")
    sig_groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for idx, row in enumerate(func_rows):
        reported = [row[k].casefold() for k in op_keys if row.get(k) and row[k] != "not_reported"]
        if len(reported) >= 3:
            sig = tuple(row[k].casefold() for k in op_keys)
            sig_groups[sig].append(idx)
    for idxs in sig_groups.values():
        if len(idxs) >= 2:
            names = {func_rows[i]["method_name"].casefold() for i in idxs}
            if len(names) >= 2:
                for i in idxs:
                    func_rows[i]["fact_tag"] = "functionally_equivalent"
                    func_rows[i]["functionally_equivalent_with"] = [
                        func_rows[j]["source_document_id"] for j in idxs if j != i
                    ]
    return func_rows


def _find_maximal_cliques(nodes: list[str], adj: dict[str, set[str]]) -> list[list[str]]:
    """Bron-Kerbosch maximal clique finder on small study graphs (guarantees every subgroup is a strict clique)."""
    cliques: list[list[str]] = []

    def _bk(r: set[str], p: set[str], x: set[str]) -> None:
        if not p and not x:
            if len(r) >= 2:
                cliques.append(sorted(r))
            return
        pivot = max(p | x, key=lambda u: len(adj[u]), default=None)
        candidates = p - adj[pivot] if pivot is not None else set(p)
        for v in sorted(candidates):
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
            "multi_document_satisfied": False,
            "pooling_allowed": False,
            "axes_tested": list(COMPARABILITY_AXES),
            "reason": "Fewer than 2 effective independent in-scope documents; single-paper summary cannot satisfy multi-document comparison.",
            "comparable_subgroups": [],
            "comparable_pairs": [],
            "noncomparable_pairs": [],
            "indeterminate_pairs": [],
            "unreported_axes_by_doc": {
                r["source_document_id"]: [
                    ax for ax in COMPARABILITY_AXES if r["cells"].get(ax, {}).get("status") != "reported"
                ]
                for r in matrix_rows
            },
            "unit_normalization_advisories": [],
            "rule": "Quantitative pooling requires >=2 independent in-scope documents with all 6 axes reported and aligned.",
        }

    noncomparable_pairs: list[dict[str, Any]] = []
    indeterminate_pairs: list[dict[str, Any]] = []
    comparable_pairs: list[list[str]] = []
    unit_advisories: list[dict[str, Any]] = []
    unreported_axes_by_doc: dict[str, list[str]] = {}

    for row in matrix_rows:
        did = row["source_document_id"]
        unrep = [ax for ax in COMPARABILITY_AXES if row["cells"].get(ax, {}).get("status") != "reported"]
        if unrep:
            unreported_axes_by_doc[did] = unrep

    row_by_id = {r["source_document_id"]: r for r in matrix_rows}

    for i in range(len(matrix_rows)):
        for j in range(i + 1, len(matrix_rows)):
            r1, r2 = matrix_rows[i], matrix_rows[j]
            d1, d2 = r1["source_document_id"], r2["source_document_id"]
            mismatches: list[dict[str, Any]] = []
            missing_axes: list[str] = []

            for ax in COMPARABILITY_AXES:
                c1 = r1["cells"].get(ax, {})
                c2 = r2["cells"].get(ax, {})
                if c1.get("status") != "reported" or c2.get("status") != "reported":
                    missing_axes.append(ax)
                    continue
                v1 = _canonicalize_axis_value(ax, str(c1.get("value", "")))
                v2 = _canonicalize_axis_value(ax, str(c2.get("value", "")))
                if v1 != v2:
                    mismatch_entry: dict[str, Any] = {
                        "axis": ax,
                        d1: c1.get("raw_value", c1.get("value")),
                        d2: c2.get("raw_value", c2.get("value")),
                        f"{d1}_paragraph_id": c1.get("paragraph_id"),
                        f"{d2}_paragraph_id": c2.get("paragraph_id"),
                    }
                    if ax == "units":
                        adv = _unit_advisory_between(str(c1.get("value", "")), str(c2.get("value", "")))
                        if adv:
                            mismatch_entry["unit_scale_convertible"] = True
                            mismatch_entry["conversion_hint"] = adv[0]
                            unit_advisories.append({
                                "pair": [d1, d2],
                                d1: c1.get("raw_value", c1.get("value")),
                                d2: c2.get("raw_value", c2.get("value")),
                                "conversion_factor": adv[0],
                                "guidance": adv[1],
                                "raw_values_preserved": True,
                            })
                    mismatches.append(mismatch_entry)

            if mismatches:
                noncomparable_pairs.append({
                    "pair": [d1, d2],
                    "mismatched_axes": mismatches,
                    "unreported_axes": missing_axes,
                    "pooling_blocked": True,
                    "comparability_status": "noncomparable",
                    "attribution": "Confirmed mismatch on axis/axes: " + ", ".join(m["axis"] for m in mismatches),
                })
            elif missing_axes:
                indeterminate_pairs.append({
                    "pair": [d1, d2],
                    "unreported_axes": missing_axes,
                    "pooling_blocked": True,
                    "comparability_status": "indeterminate",
                    "reason": "At least one required comparability axis is not_reported or ambiguous; equality cannot be assumed and pooling is blocked.",
                })
            else:
                comparable_pairs.append([d1, d2])

    # Union-Find connected components + Bron-Kerbosch clique verification
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
        is_clique = all(
            b in adj[a]
            for idx_a, a in enumerate(members)
            for b in members[idx_a + 1:]
        )
        clique_list = [sorted(members)] if is_clique else _find_maximal_cliques(members, adj)
        for clq in clique_list:
            sg_idx += 1
            rep = row_by_id[clq[0]]
            shared = {ax: rep["cells"].get(ax, {}).get("value") for ax in COMPARABILITY_AXES}
            subgroups.append({
                "subgroup_id": f"SG{sg_idx}",
                "documents": clq,
                "clique_verified": True,
                "shared_axes": shared,
            })

    return {
        "multi_document_satisfied": True,
        "pooling_allowed": len(noncomparable_pairs) == 0 and len(indeterminate_pairs) == 0 and len(comparable_pairs) > 0,
        "axes_tested": list(COMPARABILITY_AXES),
        "comparable_subgroups": subgroups,
        "comparable_pairs": comparable_pairs,
        "noncomparable_pairs": noncomparable_pairs,
        "indeterminate_pairs": indeterminate_pairs,
        "unreported_axes_by_doc": unreported_axes_by_doc,
        "unit_normalization_advisories": unit_advisories,
        "rule": "Quantitative pooling requires all six comparability axes (task, metric, units, material, pollutant, budget) to be reported and equal within a verified clique; unreported or mismatched axes block pooling.",
    }


def _classify_conclusion_direction(row: dict[str, Any], doc_meta: dict[str, Any]) -> str:
    explicit = str(doc_meta.get("conclusion_direction") or "").strip().lower()
    if explicit in {"positive", "negative", "neutral", "mixed"}:
        return explicit
    cell = row["cells"].get("main_conclusion") or {}
    txt = str(cell.get("value") or cell.get("quote") or "")
    if not txt:
        return "unclassified"
    has_pos = bool(POSITIVE_DIRECTION_RE.search(txt))
    has_neg = bool(NEGATIVE_DIRECTION_RE.search(txt))
    if has_pos and has_neg:
        return "mixed"
    if has_neg:
        return "negative"
    if has_pos:
        return "positive"
    return "neutral"


def build_consensus_conflict_matrix(
    matrix_rows: list[dict[str, Any]],
    func_rows: list[dict[str, Any]],
    comparability: dict[str, Any],
    doc_meta_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Partition cross-study conclusions into consensus, partial_agreement, and conflict attribution without voting."""
    func_by_id = {f["source_document_id"]: f for f in func_rows}
    positions: list[dict[str, Any]] = []
    for row in matrix_rows:
        did = row["source_document_id"]
        c_cell = row["cells"].get("main_conclusion") or {}
        if c_cell.get("status") != "reported":
            continue
        direction = _classify_conclusion_direction(row, doc_meta_by_id.get(did, {}))
        positions.append({
            "citation_number": row["citation_number"],
            "citation_tag": row["citation_tag"],
            "source_document_id": did,
            "paragraph_id": c_cell.get("paragraph_id"),
            "direction": direction,
            "value": c_cell.get("value"),
            "quote": c_cell.get("quote"),
        })

    consensus: list[dict[str, Any]] = []
    partial_agreement: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    if len(positions) < 2:
        return {
            "policy": "Conflicts are preserved side by side with explicit dimension attribution; majority voting and naive averaging are forbidden.",
            "consensus": consensus,
            "partial_agreement": partial_agreement,
            "conflicts": conflicts,
        }

    row_by_id = {r["source_document_id"]: r for r in matrix_rows}
    noncomp_map = {tuple(sorted(p["pair"])): p for p in comparability.get("noncomparable_pairs", [])}
    indet_map = {tuple(sorted(p["pair"])): p for p in comparability.get("indeterminate_pairs", [])}

    def _attribute_diff(d1: str, d2: str) -> tuple[list[str], str]:
        dims: set[str] = set()
        notes: list[str] = []
        r1, r2 = row_by_id[d1], row_by_id[d2]
        # sample
        s1 = (r1["cells"].get("sample_size") or {}).get("value")
        s2 = (r2["cells"].get("sample_size") or {}).get("value")
        ds1 = (r1["cells"].get("dataset") or {}).get("value")
        ds2 = (r2["cells"].get("dataset") or {}).get("value")
        if (s1 and s2 and str(s1).casefold() != str(s2).casefold()) or (ds1 and ds2 and str(ds1).casefold() != str(ds2).casefold()):
            dims.add("sample")
            notes.append(f"sample/dataset differs ({s1 or ds1} vs {s2 or ds2})")
        # method
        f1, f2 = func_by_id.get(d1, {}), func_by_id.get(d2, {})
        b1 = (r1["cells"].get("baseline") or {}).get("value")
        b2 = (r2["cells"].get("baseline") or {}).get("value")
        if (f1.get("method_name") != f2.get("method_name") and f1.get("fact_tag") != "functionally_equivalent") or (b1 and b2 and str(b1).casefold() != str(b2).casefold()):
            dims.add("method")
            notes.append(f"method/baseline differs ({f1.get('method_name')} vs {f2.get('method_name')})")
        # condition & metric from 6-axis mismatches
        key = tuple(sorted((d1, d2)))
        if key in noncomp_map:
            for m in noncomp_map[key].get("mismatched_axes", []):
                ax = m["axis"]
                if ax in ("material", "pollutant", "budget"):
                    dims.add("condition")
                elif ax in ("metric", "units", "task"):
                    dims.add("metric")
                notes.append(f"{ax}: {m.get(d1)} vs {m.get(d2)}")
        if key in indet_map:
            unrep = indet_map[key].get("unreported_axes", [])
            notes.append(f"unreported axes block full attribution: {', '.join(unrep)}")
        if not dims:
            dims.add("unattributable")
            notes.append("No reported sample/method/condition/metric mismatch explains the opposing outcomes; latent confounders require verification.")
        return sorted(dims), "; ".join(notes)

    pos_dirs = {p["direction"] for p in positions if p["direction"] in {"positive", "negative", "neutral"}}
    if ("positive" in pos_dirs and ("negative" in pos_dirs or "neutral" in pos_dirs)):
        pos_list = [p for p in positions if p["direction"] == "positive"]
        opp_list = [p for p in positions if p["direction"] in {"negative", "neutral"}]
        for p1 in pos_list:
            for p2 in opp_list:
                dims, rationale = _attribute_diff(p1["source_document_id"], p2["source_document_id"])
                conflicts.append({
                    "topic": "main_conclusion",
                    "attribution_dimensions": dims,
                    "attribution_rationale": rationale,
                    "positions": [p1, p2],
                })
    else:
        # All aligned in direction; check whether conditions are fully comparable or partially divergent
        all_comp = comparability.get("pooling_allowed", False)
        bucket = consensus if all_comp else partial_agreement
        bucket.append({
            "topic": "main_conclusion",
            "agreement_level": "consensus" if all_comp else "partial_agreement",
            "rationale": (
                "All effective documents agree in direction across fully aligned 6-axis conditions."
                if all_comp
                else "Documents share directional conclusion, but noncomparable or unreported axes limit direct quantitative pooling."
            ),
            "positions": positions,
        })

    return {
        "policy": "Conflicts are preserved side by side with explicit dimension attribution; majority voting and naive averaging are forbidden.",
        "consensus": consensus,
        "partial_agreement": partial_agreement,
        "conflicts": conflicts,
    }


def validate_matrix_draft(
    draft: dict[str, Any],
    all_docs: list[dict[str, Any]],
    effective_docs: list[dict[str, Any]],
    superseded_docs: list[dict[str, Any]],
    out_of_scope_docs: list[dict[str, Any]],
    comparability: dict[str, Any],
) -> dict[str, Any]:
    """Strictly validate a comparison matrix draft for cross-paper bleed, verbatim quotes, illegal pooling, and Citation Legend integrity."""
    para_owner: dict[str, str] = {}
    para_text: dict[tuple[str, str], str] = {}
    for d in all_docs:
        did = d["source_document_id"]
        for p in d.get("paragraphs", []):
            pid = p["paragraph_id"]
            para_owner[pid] = did
            para_text[(did, pid)] = p["text"]

    superseded_ids = {d["source_document_id"] for d in superseded_docs}
    out_of_scope_ids = {d["source_document_id"] for d in out_of_scope_docs}
    effective_ids = {d["source_document_id"] for d in effective_docs}

    noncomp_pairs = {tuple(sorted(p["pair"])) for p in comparability.get("noncomparable_pairs", [])}
    indet_pairs = {tuple(sorted(p["pair"])) for p in comparability.get("indeterminate_pairs", [])}
    comp_pairs = {tuple(sorted(p)) for p in comparability.get("comparable_pairs", [])}

    bleed_errors: list[dict[str, Any]] = []
    quote_errors: list[dict[str, Any]] = []
    pooling_errors: list[dict[str, Any]] = []
    legend_errors: list[dict[str, Any]] = []

    # 1. Validate Citation Legend structure
    legend = draft.get("citation_legend") or []
    legend_by_num: dict[int, str] = {}
    legend_by_doc: dict[str, int] = {}
    if not isinstance(legend, list) or not legend:
        legend_errors.append({"code": "MISSING_CITATION_LEGEND", "message": "citation_legend is missing or empty."})
    else:
        for entry in legend:
            cnum = entry.get("citation_number")
            cdoc = str(entry.get("source_document_id") or "")
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
                legend_errors.append({
                    "code": "CITATION_LEGEND_DRIFT",
                    "citation_number": cnum,
                    "claimed_doc": doc_id,
                    "legend_doc": legend_by_num[cnum],
                    "context": context,
                })
        if not pid:
            return
        if pid not in para_owner:
            bleed_errors.append({
                "code": "UNKNOWN_PARAGRAPH_ID",
                "source_document_id": doc_id,
                "paragraph_id": pid,
                "context": context,
            })
            return
        owner = para_owner[pid]
        if owner != doc_id:
            bleed_errors.append({
                "code": "CROSS_PAPER_BLEED",
                "claimed_document_id": doc_id,
                "paragraph_id": pid,
                "actual_owner_document_id": owner,
                "context": context,
            })
            return
        if quote:
            q_norm = " ".join(str(quote).split()).casefold()
            p_norm = " ".join(para_text.get((doc_id, pid), "").split()).casefold()
            if len(q_norm) < 4 or q_norm not in p_norm:
                quote_errors.append({
                    "code": "QUOTE_NOT_FOUND_IN_PARAGRAPH",
                    "source_document_id": doc_id,
                    "paragraph_id": pid,
                    "context": context,
                    "quote_preview": str(quote)[:120],
                })

    # 2. Check every comparison_matrix cell
    checked_cells = 0
    for row in draft.get("comparison_matrix") or []:
        row_doc = str(row.get("source_document_id") or "")
        for fname, cell in (row.get("cells") or {}).items():
            checked_cells += 1
            cell_doc = str(cell.get("source_document_id") or row_doc)
            if cell_doc != row_doc:
                bleed_errors.append({
                    "code": "ROW_CELL_DOC_MISMATCH",
                    "row_document_id": row_doc,
                    "cell_document_id": cell_doc,
                    "field": fname,
                })
            if cell.get("status") == "reported":
                pid = cell.get("paragraph_id")
                if not pid:
                    bleed_errors.append({
                        "code": "REPORTED_CELL_MISSING_PARAGRAPH_ID",
                        "source_document_id": row_doc,
                        "field": fname,
                    })
                else:
                    _check_anchor_and_quote(row_doc, str(pid), str(cell.get("quote") or ""), f"matrix:{row_doc}:{fname}", cell.get("citation_number"))

    # 3. Check functional_method_comparison & consensus_conflict_matrix anchors
    for fm in draft.get("functional_method_comparison") or []:
        if fm.get("paragraph_id"):
            _check_anchor_and_quote(
                str(fm.get("source_document_id") or ""),
                str(fm["paragraph_id"]),
                str(fm.get("quote") or ""),
                f"functional_method:{fm.get('source_document_id')}",
                fm.get("citation_number"),
            )

    ccm = draft.get("consensus_conflict_matrix") or {}
    for bucket_name in ("consensus", "partial_agreement", "conflicts"):
        for item in ccm.get(bucket_name) or []:
            for pos in item.get("positions") or []:
                if pos.get("paragraph_id"):
                    _check_anchor_and_quote(
                        str(pos.get("source_document_id") or ""),
                        str(pos["paragraph_id"]),
                        str(pos.get("quote") or ""),
                        f"{bucket_name}:{pos.get('source_document_id')}",
                        pos.get("citation_number"),
                    )

    # 4. Check quantitative_synthesis / pooled_estimates for illegal pooling
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
            pooling_errors.append({
                "code": "ILLEGAL_POOLING_DETECTED",
                "group_index": g_idx,
                "documents": docs_in_grp,
                "reason": "Single-document or empty group cannot form quantitative synthesis.",
            })
            continue
        for did in docs_in_grp:
            if did in superseded_ids:
                pooling_errors.append({
                    "code": "ILLEGAL_POOLING_DETECTED",
                    "group_index": g_idx,
                    "document_id": did,
                    "reason": "Duplicate/preprint superseded document cannot be pooled alongside Version-of-Record.",
                })
            elif did in out_of_scope_ids:
                pooling_errors.append({
                    "code": "ILLEGAL_POOLING_DETECTED",
                    "group_index": g_idx,
                    "document_id": did,
                    "reason": "Out-of-scope distractor document cannot be pooled.",
                })
            elif did not in effective_ids:
                pooling_errors.append({
                    "code": "ILLEGAL_POOLING_DETECTED",
                    "group_index": g_idx,
                    "document_id": did,
                    "reason": "Unknown document ID in quantitative synthesis group.",
                })
        for i in range(len(docs_in_grp)):
            for j in range(i + 1, len(docs_in_grp)):
                pair = tuple(sorted((docs_in_grp[i], docs_in_grp[j])))
                if pair in noncomp_pairs:
                    pooling_errors.append({
                        "code": "ILLEGAL_POOLING_DETECTED",
                        "group_index": g_idx,
                        "pair": list(pair),
                        "reason": "Pooled pair failed 6-axis comparability (noncomparable_pairs).",
                    })
                elif pair in indet_pairs:
                    pooling_errors.append({
                        "code": "ILLEGAL_POOLING_DETECTED",
                        "group_index": g_idx,
                        "pair": list(pair),
                        "reason": "Pooled pair has unreported comparability axis (indeterminate_pairs).",
                    })
                elif pair not in comp_pairs:
                    pooling_errors.append({
                        "code": "ILLEGAL_POOLING_DETECTED",
                        "group_index": g_idx,
                        "pair": list(pair),
                        "reason": "Pooled pair is not in verified comparable_pairs.",
                    })

    total_errors = len(bleed_errors) + len(quote_errors) + len(pooling_errors) + len(legend_errors)
    return {
        "ok": total_errors == 0,
        "checked_cells": checked_cells,
        "checked_pooled_groups": len(pooled_groups),
        "error_count": total_errors,
        "cross_paper_bleed_errors": bleed_errors,
        "quote_grounding_errors": quote_errors,
        "illegal_pooling_errors": pooling_errors,
        "citation_legend_errors": legend_errors,
    }


def render_markdown_matrix(
    citation_legend: list[dict[str, Any]],
    fields: list[str],
    matrix_rows: list[dict[str, Any]],
    func_rows: list[dict[str, Any]],
    comp: dict[str, Any],
    ccm: dict[str, Any],
    dedup_scope: dict[str, Any],
) -> str:
    lines: list[str] = ["# Multi-Document Comparison & Synthesis Report", ""]

    # 1. Citation Legend
    lines.extend([
        "## 1. Citation Legend (Immutable Source Mapping)",
        "",
        "| Citation | source_document_id | Title | DOI | Source File | SHA-256 (12) | Version Status |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for e in citation_legend:
        doi_disp = e.get("doi") or "`not_reported`"
        title_disp = str(e.get("title") or e.get("source_document_id") or "").replace("|", "/")
        ctag = e.get("citation_tag") or f"[{e.get('citation_number', '?')}]"
        sfile = e.get("source_file") or "unknown"
        sha_s = e.get("file_sha256_short") or str(e.get("file_sha256") or "")[:12] or "n/a"
        vstat = e.get("version_status") or "unclassified"
        lines.append(
            f"| {ctag} | `{e.get('source_document_id', '')}` | {title_disp} | {doi_disp} | `{sfile}` | `{sha_s}` | `{vstat}` |"
        )

    # 2. Deduplication & Scope Exclusions
    lines.extend(["", "## 2. Document Identity, Deduplication & Scope Audit", ""])
    lines.append(f"- Total inventoried inputs: `{dedup_scope['total_input_documents']}`")
    lines.append(f"- Effective independent in-scope documents: `{dedup_scope['effective_document_count']}`")
    lines.append(f"- Superseded duplicates / preprints mapped: `{len(dedup_scope['version_mapping'])}`")
    lines.append(f"- Out-of-scope distractors excluded: `{len(dedup_scope['out_of_scope_excluded'])}`")
    if dedup_scope["version_mapping"]:
        lines.append("")
        lines.append("| Superseded ID | Canonical VoR ID | Role | Match Method | Score |")
        lines.append("| --- | --- | --- | --- | --- |")
        for vm in dedup_scope["version_mapping"]:
            lines.append(
                f"| `{vm['superseded_document_id']}` | `{vm['canonical_document_id']}` | `{vm['superseded_role']}` | `{vm['match_method']}` | `{vm['score']}` |"
            )
    if dedup_scope["out_of_scope_excluded"]:
        lines.append("")
        lines.append("| Excluded ID | Reason |")
        lines.append("| --- | --- |")
        for ex in dedup_scope["out_of_scope_excluded"]:
            lines.append(f"| `{ex['source_document_id']}` | {ex['exclusion_reason'].replace('|', '/')} |")

    # 3. Common-Field Matrix
    lines.extend(["", "## 3. Common-Field Comparison Matrix", ""])
    header = "| Citation & Doc | " + " | ".join(fields) + " |"
    sep = "| --- | " + " | ".join("---" for _ in fields) + " |"
    lines.extend([header, sep])
    for row in matrix_rows:
        vals = []
        for f in fields:
            cell = row["cells"].get(f, {})
            if cell.get("status") == "reported":
                val_txt = str(cell.get("value", "")).replace("|", "/")
                vals.append(f"{val_txt} (`{cell.get('paragraph_id')}`, `{cell.get('fact_tag', 'explicitly_stated')}`)")
            elif cell.get("status") == "ambiguous":
                vals.append(f"`ambiguous` (`{cell.get('paragraph_id')}`)")
            else:
                vals.append("`not_reported`")
        lines.append(f"| {row['citation_tag']} `{row['source_document_id']}` | " + " | ".join(vals) + " |")

    # 4. Functional Method Operations Matrix
    lines.extend([
        "",
        "## 4. Functional Method Operations Matrix (Operations Over Names)",
        "",
        "| Citation & Doc | Method Name | Inputs | Grouping | Transformation | Filtering Rule | Output Semantics | Fact Tag | Anchor |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for fm in func_rows:
        lines.append(
            f"| {fm['citation_tag']} `{fm['source_document_id']}` | {fm['method_name'].replace('|', '/')} | "
            f"{fm['inputs'].replace('|', '/')} | {fm['grouping'].replace('|', '/')} | "
            f"{fm['transformation'].replace('|', '/')} | {fm['filtering_rule'].replace('|', '/')} | "
            f"{fm['output_semantics'].replace('|', '/')} | `{fm['fact_tag']}` | `{fm['paragraph_id'] or 'N/A'}` |"
        )

    # 5. Six-Axis Comparability Gate
    lines.extend([
        "",
        "## 5. Six-Axis Comparability Gate (`{task, metric, units, material, pollutant, budget}`)",
        "",
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

    # 6. Consensus - Partial Agreement - Conflict Attribution Matrix
    lines.extend([
        "",
        "## 6. Consensus — Partial Agreement — Conflict Attribution Matrix",
        "",
        "| Category | Topic | Attribution Dimensions | Grounded Positions & Rationale |",
        "| --- | --- | --- | --- |",
    ])
    for item in ccm.get("consensus", []):
        pos_str = "; ".join(f"{p['citation_tag']} `{p['source_document_id']}` (`{p['paragraph_id']}`)" for p in item.get("positions", []))
        lines.append(f"| `consensus` | {item.get('topic', 'main_conclusion')} | `none` | {item.get('rationale', '')} [{pos_str}] |")
    for item in ccm.get("partial_agreement", []):
        pos_str = "; ".join(f"{p['citation_tag']} `{p['source_document_id']}` (`{p['paragraph_id']}`)" for p in item.get("positions", []))
        lines.append(f"| `partial_agreement` | {item.get('topic', 'main_conclusion')} | `condition/unreported` | {item.get('rationale', '')} [{pos_str}] |")
    for item in ccm.get("conflicts", []):
        dims = ", ".join(item.get("attribution_dimensions", ["unattributable"]))
        pos_str = " vs ".join(
            f"{p['citation_tag']} `{p['source_document_id']}` (`{p['paragraph_id']}`, {p.get('direction')}): \"{str(p.get('quote', ''))[:90].replace('|', '/')}\""
            for p in item.get("positions", [])
        )
        lines.append(f"| `conflict` | {item.get('topic', 'main_conclusion')} | `{dims}` | {item.get('attribution_rationale', '').replace('|', '/')} — {pos_str} |")
    if not (ccm.get("consensus") or ccm.get("partial_agreement") or ccm.get("conflicts")):
        lines.append("| `none` | `main_conclusion` | `unattributable` | Insufficient reported conclusion cells for cross-document synthesis. |")

    return "\n".join(lines) + "\n"


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    *,
    validate_draft_path: Path | None = None,
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
    all_fields_needed = list(dict.fromkeys(list(fields) + list(COMPARABILITY_AXES)))

    budget_guard.tick_phase(budget_file, "dedup_scope_and_index")

    # Document-level deduplication (preprint/VoR/exact-hash) & scope filtering
    canonical_docs, superseded_docs, version_mapping, ctx_conflicts = deduplicate_documents_with_versions(raw_docs)
    effective_docs, out_of_scope_docs = detect_out_of_scope_documents(canonical_docs, workorder)

    # Immutable Citation Legend across ALL inventoried inputs (sorted deterministically)
    all_classified = sorted(
        effective_docs + superseded_docs + out_of_scope_docs,
        key=lambda d: d["source_document_id"],
    )
    citation_legend: list[dict[str, Any]] = []
    cit_num_by_doc: dict[str, int] = {}
    for c_idx, doc in enumerate(all_classified, start=1):
        did = doc["source_document_id"]
        cit_num_by_doc[did] = c_idx
        citation_legend.append({
            "citation_number": c_idx,
            "citation_tag": f"[{c_idx}]",
            "source_document_id": did,
            "title": doc["title"],
            "doi": doc.get("doi") or "",
            "source_file": doc["source_file"],
            "file_sha256": doc["file_sha256"],
            "file_sha256_short": doc["file_sha256"][:12],
            "version_status": doc.get("version_status", "unclassified"),
            "extraction_status": doc.get("extraction_status", "ok"),
            "page_count": doc.get("page_count", 0),
            "paragraph_count": len(doc.get("paragraphs", [])),
        })

    # Save paragraph index to /app/work/corpus_paragraphs.json for host agent deep reading
    result_guard.atomic_write_json(
        work_dir / "corpus_paragraphs.json",
        {
            "case_id": case_id,
            "citation_legend": citation_legend,
            "documents": [
                {
                    "source_document_id": d["source_document_id"],
                    "citation_number": cit_num_by_doc[d["source_document_id"]],
                    "version_status": d.get("version_status"),
                    "title": d["title"],
                    "doi": d.get("doi") or "",
                    "paragraphs": d["paragraphs"],
                }
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
        cells = {fn: extract_cell_for_field(doc, fn, cnum) for fn in all_fields_needed}
        matrix_rows.append({
            "citation_number": cnum,
            "citation_tag": f"[{cnum}]",
            "source_document_id": doc["source_document_id"],
            "title": doc["title"],
            "doi": doc.get("doi") or "",
            "paragraph_count": len(doc["paragraphs"]),
            "cells": cells,
        })
        func_rows.append(extract_functional_method_row(doc, cnum))

    func_rows = annotate_functional_equivalence(func_rows)
    comparability = evaluate_comparability(matrix_rows)
    ccm = build_consensus_conflict_matrix(matrix_rows, func_rows, comparability, doc_meta_by_id)

    dedup_scope = {
        "total_input_documents": len(raw_docs),
        "effective_document_count": len(effective_docs),
        "superseded_duplicate_count": len(superseded_docs),
        "out_of_scope_excluded_count": len(out_of_scope_docs),
        "version_mapping": version_mapping,
        "context_conflicts": ctx_conflicts,
        "out_of_scope_excluded": [
            {
                "source_document_id": d["source_document_id"],
                "title": d["title"],
                "source_file": d["source_file"],
                "exclusion_reason": d.get("exclusion_reason", "Out of scope"),
            }
            for d in out_of_scope_docs
        ],
    }

    # Optional --validate-matrix promotion / strict checking of an agent-refined draft
    corpus_comparability = comparability
    draft_obj: dict[str, Any]
    if validate_draft_path and validate_draft_path.is_file():
        draft_obj = json.loads(validate_draft_path.read_text(encoding="utf-8", errors="replace"))
        if "comparison_matrix" in draft_obj and isinstance(draft_obj["comparison_matrix"], list):
            matrix_rows = draft_obj["comparison_matrix"]
            # Re-evaluate comparability only when draft covers all effective docs; always preserve corpus blocks
            if len(matrix_rows) >= len(effective_docs):
                comparability = evaluate_comparability(matrix_rows)
        if "citation_legend" in draft_obj and isinstance(draft_obj["citation_legend"], list):
            citation_legend = draft_obj["citation_legend"]
        if "functional_method_comparison" in draft_obj and isinstance(draft_obj["functional_method_comparison"], list):
            func_rows = draft_obj["functional_method_comparison"]
        if "consensus_conflict_matrix" in draft_obj and isinstance(draft_obj["consensus_conflict_matrix"], dict):
            ccm = draft_obj["consensus_conflict_matrix"]
    else:
        draft_obj = {
            "citation_legend": citation_legend,
            "comparison_matrix": matrix_rows,
            "functional_method_comparison": func_rows,
            "consensus_conflict_matrix": ccm,
        }

    matrix_validation = validate_matrix_draft(
        draft_obj,
        all_classified,
        effective_docs,
        superseded_docs,
        out_of_scope_docs,
        corpus_comparability if (validate_draft_path and len(matrix_rows) < len(effective_docs)) else comparability,
    )

    # Render Markdown and standalone comparability_audit.json
    md_text = render_markdown_matrix(citation_legend, all_fields_needed, matrix_rows, func_rows, comparability, ccm, dedup_scope)
    result_guard.atomic_write_text(output_dir / "comparison_matrix.md", md_text)

    comp_audit_payload = {
        "case_id": case_id,
        "axes_tested": list(COMPARABILITY_AXES),
        "dedup_and_scope_audit": dedup_scope,
        "comparability_assessment": comparability,
        "matrix_validation": matrix_validation,
    }
    result_guard.atomic_write_json(output_dir / "comparability_audit.json", comp_audit_payload)

    append_audit_entry(
        audit_file,
        phase="compare",
        action="comparison_matrix_built_and_validated",
        actor="comparison_matrix_builder.py",
        output={
            "total_input_docs": len(raw_docs),
            "effective_docs": len(effective_docs),
            "pooling_allowed": comparability["pooling_allowed"],
            "noncomparable_pairs": len(comparability["noncomparable_pairs"]),
            "indeterminate_pairs": len(comparability["indeterminate_pairs"]),
            "validation_ok": matrix_validation["ok"],
        },
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    if not found_wo:
        gaps.append({
            "code": "MISSING_COMPARISON_WORKORDER",
            "severity": "warning",
            "source": "load_workorder_and_corpus",
            "message": "/input/comparison-workorder.json not found; used case.json or default comparison schema.",
        })
    empty_pdfs = [d["source_document_id"] for d in all_classified if d.get("extraction_status") == "no_text_layer"]
    if empty_pdfs:
        gaps.append({
            "code": "PDF_TEXT_EXTRACTION_EMPTY",
            "severity": "warning",
            "source": "load_workorder_and_corpus",
            "message": f"No text layer extracted for PDF(s): {', '.join(empty_pdfs)}.",
        })
    if len(effective_docs) < 2:
        gaps.append({
            "code": "INSUFFICIENT_DOCUMENTS_FOR_COMPARISON",
            "severity": "error",
            "source": "evaluate_comparability",
            "message": f"Only {len(effective_docs)} effective independent in-scope document(s) found after deduplication/scope filtering; single-paper summary cannot satisfy multi-document comparison.",
        })
    if comparability["noncomparable_pairs"]:
        gaps.append({
            "code": "NONCOMPARABLE_CONDITIONS_RETAINED",
            "severity": "info",
            "source": "evaluate_comparability",
            "message": f"{len(comparability['noncomparable_pairs'])} document pair(s) failed 6-axis comparability; pooling blocked.",
        })
    if comparability["indeterminate_pairs"]:
        gaps.append({
            "code": "COMPARABILITY_UNDETERMINED",
            "severity": "warning",
            "source": "evaluate_comparability",
            "message": f"{len(comparability['indeterminate_pairs'])} document pair(s) have unreported comparability axes; pooling blocked until verified.",
        })
    if not matrix_validation["ok"]:
        gaps.append({
            "code": "MATRIX_VALIDATION_FAILED",
            "severity": "error",
            "source": "validate_matrix_draft",
            "message": f"Matrix validation detected {matrix_validation['error_count']} blocking error(s) (bleed={len(matrix_validation['cross_paper_bleed_errors'])}, quote={len(matrix_validation['quote_grounding_errors'])}, illegal_pooling={len(matrix_validation['illegal_pooling_errors'])}, legend={len(matrix_validation['citation_legend_errors'])}).",
        })

    patch: dict[str, Any] = {
        "artifacts": ["result.json", "comparison_matrix.md", "comparability_audit.json"],
        "citation_legend": citation_legend,
        "common_fields": all_fields_needed,
        "document_inventory": [
            {
                "citation_number": cit_num_by_doc[d["source_document_id"]],
                "source_document_id": d["source_document_id"],
                "title": d["title"],
                "doi": d.get("doi") or "",
                "source_file": d["source_file"],
                "file_sha256": d["file_sha256"],
                "version_status": d.get("version_status", "unclassified"),
                "extraction_status": d.get("extraction_status", "ok"),
                "page_count": d.get("page_count", 0),
                "paragraph_count": len(d["paragraphs"]),
            }
            for d in all_classified
        ],
        "excluded_documents": [
            {
                "source_document_id": d["source_document_id"],
                "version_status": d.get("version_status"),
                "reason": d.get("exclusion_reason") or f"Superseded by {d.get('superseded_by')}",
            }
            for d in (superseded_docs + out_of_scope_docs)
        ],
        "comparison_matrix": matrix_rows,
        "functional_method_comparison": func_rows,
        "comparability_assessment": comparability,
        "consensus_conflict_matrix": ccm,
        "dedup_and_scope_audit": dedup_scope,
        "matrix_validation": matrix_validation,
        "metrics": {
            "total_input_document_count": len(raw_docs),
            "effective_document_count": len(effective_docs),
            "superseded_duplicate_count": len(superseded_docs),
            "out_of_scope_excluded_count": len(out_of_scope_docs),
            "field_count": len(all_fields_needed),
            "comparable_subgroup_count": len(comparability["comparable_subgroups"]),
            "noncomparable_pair_count": len(comparability["noncomparable_pairs"]),
            "indeterminate_pair_count": len(comparability["indeterminate_pairs"]),
            "conflict_count": len(ccm.get("conflicts", [])),
            "validation_error_count": matrix_validation["error_count"],
        },
        "metric_provenance": {
            "total_input_document_count": "document_inventory",
            "effective_document_count": "comparison_matrix",
            "superseded_duplicate_count": "dedup_and_scope_audit.version_mapping",
            "out_of_scope_excluded_count": "dedup_and_scope_audit.out_of_scope_excluded",
            "field_count": "common_fields",
            "comparable_subgroup_count": "comparability_assessment.comparable_subgroups",
            "noncomparable_pair_count": "comparability_assessment.noncomparable_pairs",
            "indeterminate_pair_count": "comparability_assessment.indeterminate_pairs",
            "conflict_count": "consensus_conflict_matrix.conflicts",
            "validation_error_count": "matrix_validation.error_count",
        },
        "findings": [
            f"Inventoried {len(raw_docs)} input document(s): {len(effective_docs)} effective independent in-scope, {len(superseded_docs)} duplicate/preprint superseded, {len(out_of_scope_docs)} out-of-scope excluded.",
            f"Built Citation Legend [1]..[{len(citation_legend)}] and {len(effective_docs)}x{len(all_fields_needed)} anchored comparison matrix with functional method operations.",
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
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["comparison_matrix.md", "comparability_audit.json"],
    )
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

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    draft_p = Path(args.validate_matrix) if args.validate_matrix else None
    res, val_ok = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], validate_draft_path=draft_p)
    out = {
        "ok": val_ok,
        "case_id": res["case_id"],
        "status": res["status"],
        "metrics": res["metrics"],
        "matrix_validation": res["matrix_validation"],
    }
    print(json.dumps(out, ensure_ascii=False))
    if draft_p is not None and not val_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
