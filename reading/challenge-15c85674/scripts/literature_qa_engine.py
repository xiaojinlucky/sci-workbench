#!/usr/bin/env python3
"""Grounded Scientific Literature Q&A Engine & 10-Gate Semantic Zero-Fabrication Validator (challenge-15c85674).

Capabilities (pure Python 3.12 stdlib; reading-order pdftotext -enc UTF-8 with optional pypdf fallback):
1. Supports /input/case.json (paragraphs, sections, tables, captions, footnotes, paper_text, questions)
   AND real single/multi-file multi-page PDF documents via reading-order page extraction (no -layout bleed).
2. Enforces Four-Stage Observable Coverage Auditing (ingested_pages vs extracted_pages vs
   indexed_paragraphs vs cited_paragraphs + tail_page_coverage_check + table_footnote_coverage_check)
   to prevent silent "first-page-only" failures on long PDFs.
3. Upgrades retrieval to Okapi BM25 + thousands-aware numeric/SI-unit/entity boosting + table/footnote/
   tail-page recall + bilingual compound sub-question decomposition + cross-section conflict pre-detection.
4. Enforces 10 blocking gates (7 structural/evidence + 3 semantic gates: reported numeric-core fidelity,
   inferred reasoning-premise & anti-disguise check, and atomic sub-question coverage grounding) in
   --validate-answers before atomically writing BOTH /app/output/result.json AND /app/answers.json.
"""

from __future__ import annotations

import argparse
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
from citation_anchors import (
    detect_cross_section_conflicts,
    extract_emlsi_fingerprint,
    extract_joint_semantic_bindings,
    extract_numeric_unit_pairs,
    extract_shortest_verbatim_quote,
    verify_conditions_against_paragraphs,
    verify_exact_quote,
    verify_inferred_claim_premise,
    verify_reported_claim_numeric_fidelity,
)

VALID_ANSWER_STATUSES = {"answered", "partial", "insufficient_evidence", "input_error"}
VALID_CLAIM_KINDS = {"reported", "inferred"}

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "were", "was", "are", "have", "has",
    "into", "than", "also", "such", "their", "these", "between", "using", "based", "under",
    "what", "which", "where", "when", "does", "how", "why", "about", "there", "been",
    "什么", "哪些", "如何", "是否", "多少", "以及", "并且", "什么条件", "论文", "文章", "作者",
    "本文", "该文", "研究", "报告", "说明", "指出", "给出", "回答",
}

SUBQ_SPLIT_RE = re.compile(
    r"[；;？?]+|"
    r"(?:\s*(?:以及|并说明|并指出|并解释|此外|同时|另外|分别(?:说明|回答|指出))\s*)|"
    r"(?:\s*,\s*(?:and\s+(?:what|how|under\s+what|which|whether)|as\s+well\s+as)\s+)",
    re.I,
)

TAIL_CUE_RE = re.compile(
    r"(?:局限|不足|附录|末页|最后|后续|结论|展望|限制|例外|脚注|表注|报告时限|截止|更新|appendix|limitation|footnote|final|last\s+page|caveat|exclusion|reporting\s+timeline|postmarket|day)",
    re.I,
)
TABLE_FOOTNOTE_CUE_RE = re.compile(
    r"(?:表\s*\d*|表格|脚注|注释|排除|标准|条件|阈值|温度|样本量|单位|table|footnote|note|caption|excluded|threshold|condition|criteria|dose|unit)",
    re.I,
)

FOOTNOTE_LINE_RE = re.compile(r"^(?:\*|†|‡|§|\d+\s+[A-Z]|Note:|Footnote:|Abbreviations?:|Table\s+\d+)", re.I)
SECTION_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+|[IVX]+\.\s+)?(ABSTRACT|INTRODUCTION|BACKGROUND|METHODS?|MATERIALS?\s+AND\s+METHODS?|RESULTS?|DISCUSSION|CONCLUSIONS?|LIMITATIONS?|REFERENCES?|APPENDIX|GLOSSARY|TABLE\s+\d+)\b",
    re.I,
)

# Lightweight bilingual scientific query expansion for Chinese questions over English papers
ZH_TO_EN_SCI_EXPANSION: dict[str, tuple[str, ...]] = {
    "法拉第效率": ("faradaic", "efficiency"),
    "效率": ("efficiency", "yield", "rate"),
    "温度": ("temperature", "thermal", "°c"),
    "载量": ("loading", "dosage", "dose", "mg"),
    "剂量": ("dose", "dosage", "loading", "mg"),
    "条件": ("condition", "conditions", "temperature", "ph", "electrolyte", "loading"),
    "排除": ("excluded", "exclusion", "criteria", "leakage"),
    "标准": ("criteria", "standard", "threshold", "excluded"),
    "脚注": ("footnote", "note", "table", "excluded"),
    "表格": ("table", "tab"),
    "表注": ("footnote", "table", "caption"),
    "基线": ("baseline", "control", "reference", "compared"),
    "对照": ("control", "baseline", "placebo", "compared", "versus"),
    "变化": ("compared", "versus", "higher", "lower", "increase", "decrease", "discrepancy"),
    "提升": ("improved", "higher", "increase", "enhanced", "compared"),
    "局限": ("limitation", "limitations", "limits", "unresolved", "not", "evaluated", "tested"),
    "不足": ("limitation", "limitations", "unresolved", "shortcoming"),
    "稳定性": ("stability", "durability", "degradation", "long-term"),
    "降解": ("degradation", "stability", "decay"),
    "样本量": ("sample", "patients", "cells", "replicates"),
    "显著": ("significant", "statistically", "p-value"),
    "方法": ("method", "methods", "protocol", "measurements"),
    "机制": ("mechanism", "pathway", "process"),
    "安全": ("safety", "adverse", "pharmacovigilance", "postmarketing"),
    "附录": ("appendix", "glossary", "supplementary"),
    "补充材料": ("supplementary", "appendix"),
    "末页": ("glossary", "appendix", "final"),
    "缩写": ("glossary", "abbreviation", "acronyms"),
    "术语": ("glossary", "abbreviations", "acronyms", "terminology"),
    "中毒": ("intoxication", "intoxications", "poisoning", "poisonings", "toxic"),
    "病例": ("cases", "patients", "individuals"),
    "男童": ("boys", "male"),
    "女童": ("girls", "female"),
    "药物": ("medicine", "medicines", "drug", "drugs", "pharmacovigilance"),
    "意外": ("accidental", "unintentional"),
    "趋势": ("trend", "increasing", "stationary", "temporal"),
    "发病率": ("incidence", "rate", "rates"),
    "血药浓度": ("serum", "blood", "plasma", "concentration"),
    "费用": ("cost", "costs", "expense", "expenditure", "fee"),
    "差值": ("difference", "higher", "lower", "gap"),
    "相差": ("difference", "higher", "lower", "gap"),
}


def tokenize(text: str) -> list[str]:
    en = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9.,_/%-]{1,}", (text or "").lower())
    # Keep thousands-separated numbers both raw and comma-normalized
    en_norm: list[str] = []
    for tok in en:
        t_clean = tok.strip(".,;")
        if t_clean:
            en_norm.append(t_clean)
            if "," in t_clean and re.fullmatch(r"\d+(?:,\d{3})+(?:\.\d+)?%?", t_clean):
                en_norm.append(t_clean.replace(",", ""))
    zh = re.findall(r"[\u4e00-\u9fff]{2,4}", text or "")
    zh_chars = re.findall(r"[\u4e00-\u9fff]", text or "")
    zh_bi = [zh_chars[i] + zh_chars[i + 1] for i in range(len(zh_chars) - 1)]
    expanded: list[str] = []
    if text:
        for zh_key, en_terms in ZH_TO_EN_SCI_EXPANSION.items():
            if zh_key in text:
                expanded.extend(en_terms)
    return [t for t in en_norm + zh + zh_bi + expanded if t not in STOPWORDS]


def split_subquestions(question_text: str) -> list[str]:
    """Decompose compound Chinese/English scientific questions into atomic sub-questions."""
    raw = (question_text or "").strip()
    if not raw:
        return []
    parts = [p.strip(" ，,。.;；:：") for p in SUBQ_SPLIT_RE.split(raw) if p and p.strip(" ，,。.;；:：")]
    # Further split Chinese/English comma-separated multi-facet clauses
    facet_split = re.compile(
        r"[，,]\s*(?=(?:在什么|采用什么|与.+相比|变化多少|提升多少|降低多少|"
        r"作者(?:指出|报告|提到|讨论)|有哪些|哪些局限|存在哪些|局限|不足|样本量|是否|如何|"
        r"under what|compared with|how much|what limitations|and what))",
        re.I,
    )
    refined: list[str] = []
    for part in parts:
        sub_parts = [sp.strip(" ，,。.;；") for sp in facet_split.split(part) if sp and sp.strip(" ，,。.;；")]
        refined.extend(sub_parts if sub_parts else [part])
    dedup: list[str] = []
    for sp in refined:
        if len(sp) >= 2 and sp not in dedup:
            dedup.append(sp)
    return dedup if dedup else [raw]


def _dehyphenate_pdf_text(text: str) -> str:
    """Repair soft line-wrap hyphenations common in two-column academic PDFs."""
    return re.sub(r"([A-Za-z])-\s*\n\s*([a-z])", r"\1\2", text or "")


def _extract_pdf_corpus(
    pdf_path: Path,
    start_idx: int = 1,
    *,
    doc_prefix: str = "",
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Extract reading-order page-numbered paragraphs/tables/footnotes via pdftotext -enc UTF-8 (no -layout) or pypdf fallback."""
    has_pdfinfo = shutil.which("pdfinfo") is not None
    has_pdftotext = shutil.which("pdftotext") is not None

    total_pages = 0
    page_texts: list[tuple[int, str]] = []
    extractor_used = "unavailable"

    if has_pdftotext:
        extractor_used = "pdftotext_utf8_reading_order"
        if has_pdfinfo:
            try:
                info = subprocess.check_output(
                    ["pdfinfo", str(pdf_path)],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
                for line in info.splitlines():
                    if line.lower().startswith("pages:"):
                        val = line.split(":", 1)[1].strip()
                        if val.isdigit():
                            total_pages = int(val)
            except Exception:
                total_pages = 0

        if total_pages > 0:
            for pno in range(1, total_pages + 1):
                try:
                    txt = subprocess.check_output(
                        ["pdftotext", "-enc", "UTF-8", "-f", str(pno), "-l", str(pno), str(pdf_path), "-"],
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        stderr=subprocess.DEVNULL,
                        timeout=20,
                    )
                except Exception:
                    txt = ""
                page_texts.append((pno, txt))
        else:
            try:
                full = subprocess.check_output(
                    ["pdftotext", "-enc", "UTF-8", str(pdf_path), "-"],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
            except Exception:
                full = ""
            raw_pages = full.split("\x0c")
            if len(raw_pages) > 1 and not raw_pages[-1].strip():
                raw_pages = raw_pages[:-1]
            total_pages = max(len(raw_pages), 1)
            page_texts = [(i, t) for i, t in enumerate(raw_pages, start=1)]
    else:
        try:
            import pypdf  # type: ignore

            reader = pypdf.PdfReader(str(pdf_path))
            extractor_used = "pypdf_fallback"
            total_pages = len(reader.pages)
            for pno, pg in enumerate(reader.pages, start=1):
                try:
                    txt = pg.extract_text() or ""
                except Exception:
                    txt = ""
                page_texts.append((pno, txt))
        except Exception:
            return [], [], {
                "pdf_file": pdf_path.name,
                "extractor": "unavailable",
                "ingested_pages": 0,
                "extracted_pages": 0,
                "empty_or_scanned_pages": [],
                "error": "Neither pdftotext nor pypdf available",
            }

    paragraphs: list[dict[str, Any]] = []
    sections_seen: list[str] = []
    empty_pages: list[int] = []
    extracted_pages = 0
    current_sec = "Page_1"
    p_idx = start_idx - 1
    pid_pref = f"{doc_prefix}_" if doc_prefix else ""

    for pno, raw_ptxt in page_texts:
        ptxt = _dehyphenate_pdf_text(raw_ptxt)
        clean_all = " ".join(ptxt.split())
        if len(clean_all) < 20:
            empty_pages.append(pno)
            continue
        extracted_pages += 1
        raw_chunks = [" ".join(c.split()) for c in re.split(r"\n\s*\n+", ptxt) if " ".join(c.split())]

        # Preserve table cells on table pages without losing thousands-separated numbers (e.g., Table 1)
        merged_chunks: list[str] = []
        idx_c = 0
        while idx_c < len(raw_chunks):
            cur = raw_chunks[idx_c]
            if re.fullmatch(r"\d{1,4}", cur) and not (merged_chunks and merged_chunks[-1].lower().startswith("table ")):
                idx_c += 1
                continue
            # Collapse consecutive short table cell tokens following a Table caption into one addressable table block
            if cur.lower().startswith("table ") and len(cur) >= 12:
                table_buf = [cur]
                j = idx_c + 1
                while j < len(raw_chunks) and len(raw_chunks[j]) <= 45 and len(" ".join(table_buf)) < 2600:
                    if SECTION_HEADING_RE.match(raw_chunks[j]) and not raw_chunks[j].lower().startswith("table "):
                        break
                    table_buf.append(raw_chunks[j])
                    j += 1
                merged_chunks.append(" | ".join(table_buf) if len(table_buf) > 1 else cur)
                idx_c = j
                continue
            if (
                len(cur) <= 24
                and not cur.endswith((".", "?", "!"))
                and idx_c + 1 < len(raw_chunks)
                and not re.fullmatch(r"\d{1,4}", raw_chunks[idx_c + 1])
                and len(raw_chunks[idx_c + 1]) <= 160
            ):
                merged_chunks.append(f"{cur}: {raw_chunks[idx_c + 1]}")
                idx_c += 2
            else:
                merged_chunks.append(cur)
                idx_c += 1

        for ch_norm in merged_chunks:
            if len(ch_norm) < 4 or re.fullmatch(r"\d{1,4}", ch_norm):
                continue
            m_sec = SECTION_HEADING_RE.match(ch_norm)
            is_fn = bool(FOOTNOTE_LINE_RE.match(ch_norm))
            if m_sec and len(ch_norm) <= 100:
                current_sec = m_sec.group(1).title()
            block_type = "table" if ch_norm.lower().startswith("table ") else ("footnote" if is_fn else "paragraph")
            sec_label = "Tables" if block_type == "table" else ("Footnotes" if is_fn else (current_sec or f"Page_{pno}"))
            if sec_label not in sections_seen:
                sections_seen.append(sec_label)
            p_idx += 1
            paragraphs.append({
                "paragraph_id": f"{pid_pref}P{p_idx:03d}_p{pno}",
                "section": sec_label,
                "page": pno,
                "source_file": pdf_path.name,
                "block_type": block_type,
                "text": ch_norm,
            })

    pdf_audit = {
        "pdf_file": pdf_path.name,
        "extractor": extractor_used,
        "ingested_pages": total_pages,
        "extracted_pages": extracted_pages,
        "empty_or_scanned_pages": empty_pages,
    }
    return paragraphs, sections_seen, pdf_audit


def normalize_corpus(case_data: dict[str, Any], input_dir: Path) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Extract addressable paragraphs, tables, footnotes, captions, and PDF pages with 4-stage audit."""
    paragraphs: list[dict[str, Any]] = []
    sections_seen: list[str] = []

    def _add_block(pid: str, sec: str, text: str, *, page: int | None = None, block_type: str = "paragraph") -> None:
        clean = (text or "").strip()
        if not clean:
            return
        paragraphs.append({
            "paragraph_id": pid,
            "section": sec,
            "page": page,
            "block_type": block_type,
            "text": clean,
        })
        if sec not in sections_seen:
            sections_seen.append(sec)

    raw_paras = case_data.get("paragraphs") or case_data.get("paragraph_list") or case_data.get("segments")
    if isinstance(raw_paras, list):
        for idx, item in enumerate(raw_paras, start=1):
            if isinstance(item, dict):
                pid = str(item.get("paragraph_id") or item.get("id") or f"P{idx:03d}")
                sec = str(item.get("section") or item.get("heading") or "Body")
                page = item.get("page")
                btype = str(item.get("block_type") or ("footnote" if "footnote" in sec.lower() else "paragraph"))
                txt = str(item.get("text") or item.get("content") or "")
            else:
                pid, sec, page, btype, txt = f"P{idx:03d}", "Body", None, "paragraph", str(item)
            _add_block(pid, sec, txt, page=page, block_type=btype)

    if not paragraphs and isinstance(case_data.get("sections"), list):
        p_idx = 0
        for sec_obj in case_data["sections"]:
            if not isinstance(sec_obj, dict):
                continue
            sec = str(sec_obj.get("title") or sec_obj.get("section") or "Section")
            for item in (sec_obj.get("paragraphs") or []):
                p_idx += 1
                if isinstance(item, dict):
                    pid = str(item.get("paragraph_id") or item.get("id") or f"P{p_idx:03d}")
                    page = item.get("page")
                    txt = str(item.get("text") or item.get("content") or "")
                else:
                    pid, page, txt = f"P{p_idx:03d}", None, str(item)
                _add_block(pid, sec, txt, page=page, block_type="paragraph")

    for tidx, tbl in enumerate(case_data.get("tables") or [], start=1):
        if isinstance(tbl, dict):
            pid = str(tbl.get("paragraph_id") or tbl.get("table_id") or f"T{tidx:02d}")
            page = tbl.get("page")
            if isinstance(tbl.get("text"), str):
                txt = tbl["text"]
            else:
                txt = json.dumps(tbl, ensure_ascii=False)
            _add_block(pid, "Tables", txt, page=page, block_type="table")
            # Also index explicit table footnotes separately for recall visibility
            for fidx, fn in enumerate(tbl.get("footnotes") or [], start=1):
                _add_block(f"{pid}_FN{fidx:02d}", "Footnotes", str(fn), page=page, block_type="footnote")

    for fnidx, fn in enumerate(case_data.get("footnotes") or [], start=1):
        if isinstance(fn, dict):
            pid = str(fn.get("paragraph_id") or fn.get("id") or f"FN{fnidx:02d}")
            page = fn.get("page")
            txt = str(fn.get("text") or fn.get("content") or "")
        else:
            pid, page, txt = f"FN{fnidx:02d}", None, str(fn)
        _add_block(pid, "Footnotes", txt, page=page, block_type="footnote")

    for cidx, cap in enumerate(case_data.get("figure_captions") or case_data.get("captions") or [], start=1):
        if isinstance(cap, dict):
            pid = str(cap.get("paragraph_id") or cap.get("figure_id") or f"FigCap{cidx:02d}")
            page = cap.get("page")
            txt = str(cap.get("text") or cap.get("caption") or json.dumps(cap, ensure_ascii=False))
        else:
            pid, page, txt = f"FigCap{cidx:02d}", None, str(cap)
        _add_block(pid, "Figures", txt, page=page, block_type="caption")

    if not paragraphs and isinstance(case_data.get("paper_text"), str):
        for idx, chunk in enumerate(re.split(r"\n\s*\n+", case_data["paper_text"]), start=1):
            _add_block(f"P{idx:03d}", "Body", chunk, page=None, block_type="paragraph")

    # PDF ingestion (supports explicit pdf_files[], pdf_file/source_file, or co-located PDFs in /input when paragraphs empty)
    pdf_paths: list[Path] = []
    if isinstance(case_data.get("pdf_files"), list):
        for fn in case_data["pdf_files"]:
            cand = input_dir / str(fn)
            if cand.is_file() and cand not in pdf_paths:
                pdf_paths.append(cand)
    elif isinstance(case_data.get("pdf_file"), str):
        cand = input_dir / case_data["pdf_file"]
        if cand.is_file():
            pdf_paths.append(cand)
    elif isinstance(case_data.get("source_file"), str) and case_data["source_file"].lower().endswith(".pdf"):
        cand = input_dir / case_data["source_file"]
        if cand.is_file():
            pdf_paths.append(cand)
    elif not paragraphs and input_dir.is_dir():
        pdf_paths = sorted(input_dir.glob("*.pdf"))

    pdf_audit: dict[str, Any] = {}
    pdf_audits_all: list[dict[str, Any]] = []
    if pdf_paths:
        total_ingested_pdf = 0
        total_extracted_pdf = 0
        all_empty_pdf: list[int] = []
        multi_pdf = len(pdf_paths) > 1
        for d_idx, ppath in enumerate(pdf_paths, start=1):
            pref = f"D{d_idx}" if multi_pdf else ""
            pdf_paras, pdf_secs, pa = _extract_pdf_corpus(ppath, start_idx=len(paragraphs) + 1, doc_prefix=pref)
            paragraphs.extend(pdf_paras)
            for s in pdf_secs:
                if s not in sections_seen:
                    sections_seen.append(s)
            pdf_audits_all.append(pa)
            total_ingested_pdf += int(pa.get("ingested_pages") or 0)
            total_extracted_pdf += int(pa.get("extracted_pages") or 0)
            all_empty_pdf.extend(pa.get("empty_or_scanned_pages") or [])
        pdf_audit = {
            "pdf_files": [p.name for p in pdf_paths],
            "extractor": pdf_audits_all[0].get("extractor", "pdftotext_utf8_reading_order"),
            "ingested_pages": total_ingested_pdf,
            "extracted_pages": total_extracted_pdf,
            "empty_or_scanned_pages": sorted(set(all_empty_pdf)),
            "per_pdf_audit": pdf_audits_all,
        }

    # Record explicit scanned/empty pages declared in case.json too
    declared_empty_pages = [
        int(p) for p in (case_data.get("empty_or_scanned_pages") or [])
        if str(p).isdigit()
    ]
    empty_pages = sorted(set(pdf_audit.get("empty_or_scanned_pages", []) + declared_empty_pages))

    pages_present = sorted({int(p["page"]) for p in paragraphs if isinstance(p.get("page"), int)})
    ingested_pages = int(
        pdf_audit.get("ingested_pages")
        or case_data.get("total_pages")
        or (max(pages_present + empty_pages) if (pages_present or empty_pages) else len(sections_seen))
    )
    extracted_pages = int(
        pdf_audit.get("extracted_pages")
        or (len(pages_present) if pages_present else len(sections_seen))
    )

    # Tail-page coverage check (last 20% of pages or final 3 paragraphs)
    max_single_page = max(pages_present) if pages_present else 0
    if max_single_page >= 3 and pages_present:
        tail_start = max(1, max_single_page - max(1, max_single_page // 5) + 1)
        tail_paras = [p for p in paragraphs if isinstance(p.get("page"), int) and p["page"] >= tail_start]
        tail_check = {
            "tail_pages_range": f"{tail_start}-{max_single_page}",
            "tail_paragraphs_count": len(tail_paras),
            "final_page_extracted": max_single_page in pages_present,
            "tail_text_present": len(tail_paras) > 0,
        }
    else:
        tail_paras = paragraphs[-3:] if paragraphs else []
        tail_check = {
            "tail_pages_range": "final_blocks",
            "tail_paragraphs_count": len(tail_paras),
            "final_page_extracted": bool(tail_paras),
            "tail_text_present": bool(tail_paras),
        }

    table_footnote_check = {
        "tables_indexed": sum(1 for p in paragraphs if p.get("block_type") == "table"),
        "footnotes_indexed": sum(1 for p in paragraphs if p.get("block_type") == "footnote"),
        "captions_indexed": sum(1 for p in paragraphs if p.get("block_type") == "caption"),
    }

    page_coverage_audit = {
        "source_mode": "pdf_and_json" if (pdf_paths and raw_paras) else ("pdf" if pdf_paths else "structured_json"),
        "ingested_pages": ingested_pages,
        "extracted_pages": extracted_pages,
        "empty_or_scanned_pages": empty_pages,
        "indexed_paragraphs": len(paragraphs),
        "cited_paragraphs": 0,
        "cited_pages": [],
        "tail_page_coverage_check": tail_check,
        "table_footnote_coverage_check": table_footnote_check,
    }
    if pdf_audits_all:
        page_coverage_audit["pdf_files"] = [p.name for p in pdf_paths]
        page_coverage_audit["extractor"] = pdf_audit.get("extractor", "pdftotext_utf8_reading_order")
    return paragraphs, sections_seen, page_coverage_audit


def normalize_questions(case_data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_qs = case_data.get("questions") or case_data.get("question_list") or []
    out: list[dict[str, Any]] = []
    for idx, q in enumerate(raw_qs, start=1):
        if isinstance(q, dict):
            qid = str(q.get("question_id") or q.get("id") or f"Q{idx}")
            qtext = str(q.get("question") or q.get("text") or q.get("prompt") or "")
        else:
            qid, qtext = f"Q{idx}", str(q)
        out.append({
            "question_id": qid,
            "question": qtext.strip(),
            "sub_questions": split_subquestions(qtext),
        })
    return out


class BM25Index:
    """Pure-stdlib Okapi BM25 index with numeric/SI unit, entity, tail-page, and table/footnote boosts."""

    def __init__(self, paragraphs: list[dict[str, Any]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.paragraphs = paragraphs
        self.k1 = k1
        self.b = b
        self.n_docs = len(paragraphs)
        self.doc_tf: list[Counter[str]] = []
        self.doc_len: list[int] = []
        self.df: Counter[str] = Counter()
        self.doc_nums: list[set[str]] = []
        self.doc_units: list[set[str]] = []
        self.doc_ents: list[set[str]] = []
        self.max_page = max((int(p["page"]) for p in paragraphs if isinstance(p.get("page"), int)), default=0)

        for p in paragraphs:
            toks = tokenize(p["text"])
            tf = Counter(toks)
            self.doc_tf.append(tf)
            self.doc_len.append(max(len(toks), 1))
            for tok in tf:
                self.df[tok] += 1
            raw_nums = re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", p["text"])
            self.doc_nums.append(set(raw_nums) | {n.replace(",", "") for n in raw_nums})
            self.doc_units.append({u for _, u, _ in extract_numeric_unit_pairs(p["text"])})
            self.doc_ents.append(set(re.findall(r"\b[A-Z][A-Za-z0-9-]{1,}\b", p["text"])))

        self.avg_dl = (sum(self.doc_len) / self.n_docs) if self.n_docs > 0 else 1.0

    def query(self, query_text: str, top_k: int = 6) -> list[dict[str, Any]]:
        if not self.paragraphs:
            return []
        q_tokens = tokenize(query_text)
        raw_q_nums = re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", query_text)
        q_nums = set(raw_q_nums) | {n.replace(",", "") for n in raw_q_nums}
        q_units = {u for _, u, _ in extract_numeric_unit_pairs(query_text)}
        q_ents = set(re.findall(r"\b[A-Z][A-Za-z0-9-]{1,}\b", query_text))
        want_tail = bool(TAIL_CUE_RE.search(query_text))
        want_table_fn = bool(TABLE_FOOTNOTE_CUE_RE.search(query_text))
        tail_cutoff = max(1, self.max_page - max(1, self.max_page // 5) + 1) if self.max_page >= 3 else 0

        scored: list[tuple[float, dict[str, Any]]] = []
        for idx, p in enumerate(self.paragraphs):
            tf = self.doc_tf[idx]
            dl = self.doc_len[idx]
            score = 0.0
            for tok in set(q_tokens):
                f = tf.get(tok, 0)
                if f <= 0:
                    continue
                idf = math.log(1.0 + (self.n_docs - self.df[tok] + 0.5) / (self.df[tok] + 0.5))
                denom = f + self.k1 * (1.0 - self.b + self.b * (dl / self.avg_dl))
                score += idf * ((f * (self.k1 + 1.0)) / denom)

            if q_nums:
                score += 1.6 * len(q_nums & self.doc_nums[idx])
            if q_units:
                score += 1.2 * len(q_units & self.doc_units[idx])
            if q_ents:
                score += 1.8 * len(q_ents & self.doc_ents[idx])

            # Boost tables/footnotes and tail pages when query asks for conditions/footnotes/limitations/tail facts
            if score > 0:
                if want_table_fn and p.get("block_type") in ("table", "footnote", "caption"):
                    score *= 1.25
                if want_tail and isinstance(p.get("page"), int) and tail_cutoff > 0 and p["page"] >= tail_cutoff:
                    score *= 1.22

            scored.append((round(score, 4), p))

        scored.sort(key=lambda item: -item[0])
        return [
            {
                "paragraph_id": p["paragraph_id"],
                "section": p["section"],
                "page": p.get("page"),
                "block_type": p.get("block_type", "paragraph"),
                "score": sc,
                "text": p["text"],
                "emlsi": extract_emlsi_fingerprint(p["text"]),
            }
            for sc, p in scored[:top_k]
        ]


def _verify_subquestion_coverage(
    sub_questions: list[str],
    ans: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Gate 10: Verify every decomposed atomic sub-question has an explicit landing in answer/claims/conditions or unanswered_parts."""
    status = str(ans.get("status") or "")
    if status == "input_error" or len(sub_questions) < 2:
        return True, []

    claims = ans.get("claims") if isinstance(ans.get("claims"), list) else []
    conds = ans.get("conditions") if isinstance(ans.get("conditions"), list) else []
    unans = ans.get("unanswered_parts") if isinstance(ans.get("unanswered_parts"), list) else []

    resolved_blob = " ".join([
        str(ans.get("answer") or ""),
        " ".join(str(c.get("text") or "") for c in claims if isinstance(c, dict)),
        " ".join(str(c) for c in conds),
    ])
    gap_blob = " ".join(
        f"{u.get('part', '')} {u.get('reason', '')}" if isinstance(u, dict) else str(u)
        for u in unans
    )

    resolved_toks = set(tokenize(resolved_blob))
    resolved_ents = {e.lower() for e in re.findall(r"\b[A-Za-z][A-Za-z0-9-]{1,}\b", resolved_blob)}
    all_toks = resolved_toks | set(tokenize(gap_blob))
    all_ents = resolved_ents | {e.lower() for e in re.findall(r"\b[A-Za-z][A-Za-z0-9-]{1,}\b", gap_blob)}

    # Generic interrogative tokens and bare calendar years do not by themselves prove a sub-question was answered
    generic_q_toks = {
        "相比", "分别", "具体", "主要", "哪些", "如何", "多少", "什么", "是否", "区分",
        "说明", "代表", "全称", "有何", "各是", "人群", "儿童", "reported", "report",
    }

    def _concept_units(text: str) -> list[tuple[str, set[str]]]:
        """Extract uninflated semantic concepts (each Chinese/English concept counts once even with bilingual aliases)."""
        units: list[tuple[str, set[str]]] = []
        seen_keys: set[str] = set()
        # 1) Known bilingual scientific concepts first (so '发病率' is 1 concept, not 3 bigrams + 3 English synonyms)
        matched_zh_keys: list[str] = []
        for zh_k, en_als in sorted(ZH_TO_EN_SCI_EXPANSION.items(), key=lambda kv: -len(kv[0])):
            if zh_k in text and zh_k not in generic_q_toks and not any(zh_k in prev for prev in matched_zh_keys):
                matched_zh_keys.append(zh_k)
                seen_keys.add(zh_k)
                units.append((zh_k, {zh_k} | set(en_als)))
        # 2) English words/acronyms (excluding bare years and stopwords)
        for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}", text):
            wl = w.lower()
            if wl not in STOPWORDS and wl not in generic_q_toks and wl not in seen_keys:
                seen_keys.add(wl)
                units.append((wl, {wl}))
        # 3) Remaining Chinese 2-char content concepts not covered by matched_zh_keys
        for m in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            for i in range(0, len(m) - 1, 2):
                seg = m[i : i + 2]
                if seg in STOPWORDS or seg in generic_q_toks or any(seg in k for k in matched_zh_keys):
                    continue
                if seg not in seen_keys:
                    seen_keys.add(seg)
                    units.append((seg, {seg}))
        return units

    per_sq_concepts = [_concept_units(sq) for sq in sub_questions]

    errors: list[str] = []
    for idx, (sq, concepts) in enumerate(zip(sub_questions, per_sq_concepts)):
        if not concepts:
            continue
        other_keys: set[str] = set()
        for j, oth in enumerate(per_sq_concepts):
            if j != idx:
                other_keys |= {k for k, _ in oth}
        unique_concepts = [(k, aliases) for k, aliases in concepts if k not in other_keys]
        target_concepts = unique_concepts if unique_concepts else concepts

        pool = (resolved_toks | resolved_ents) if status == "answered" else (all_toks | all_ents)
        pool_raw = resolved_blob.lower() if status == "answered" else (resolved_blob + " " + gap_blob).lower()
        matched_cnt = 0
        for _k, aliases in target_concepts:
            if (aliases & pool) or any(a in pool_raw for a in aliases):
                matched_cnt += 1

        min_required = 2 if len(target_concepts) >= 3 else 1
        if matched_cnt < min_required:
            if status == "answered":
                errors.append(
                    f"UNADDRESSED_SUBQUESTION: sub-question {sq!r} has insufficient landing in answer/claims/conditions "
                    f"(matched {matched_cnt}/{len(target_concepts)} focus concepts, required>={min_required}) despite status='answered'"
                )
            else:
                errors.append(
                    f"UNADDRESSED_SUBQUESTION: sub-question {sq!r} is missing from both answer/claims and unanswered_parts[]"
                )
    return len(errors) == 0, errors


def validate_answers_payload(
    payload: dict[str, Any],
    *,
    case_data: dict[str, Any],
    paragraphs: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    sections_seen: list[str],
    page_coverage_audit: dict[str, Any],
) -> dict[str, Any]:
    """10-gate semantic zero-fabrication validator over drafted answers payload."""
    errors: list[str] = []
    warnings: list[str] = []
    para_lookup = {p["paragraph_id"]: p["text"] for p in paragraphs}
    para_page = {p["paragraph_id"]: p.get("page") for p in paragraphs}
    q_lookup = {q["question_id"]: q for q in questions}

    # Gate 1: Top-level keys & identity match
    for req_key in ("case_id", "paper_id", "material_version", "answers", "coverage"):
        if req_key not in payload:
            errors.append(f"GATE1_MISSING_KEY: missing top-level key {req_key!r}")

    exp_case = str(case_data.get("case_id") or "")
    exp_paper = str(case_data.get("paper_id") or case_data.get("doi") or "")
    exp_ver = str(case_data.get("material_version") or case_data.get("version") or "")
    if exp_case and str(payload.get("case_id", "")) != exp_case:
        errors.append(f"GATE1_CASE_ID_MISMATCH: expected {exp_case!r}, got {payload.get('case_id')!r}")
    if exp_paper and str(payload.get("paper_id", "")) != exp_paper:
        errors.append(f"GATE1_PAPER_ID_MISMATCH: expected {exp_paper!r}, got {payload.get('paper_id')!r}")
    if exp_ver and str(payload.get("material_version", "")) != exp_ver:
        errors.append(f"GATE1_VERSION_MISMATCH: expected {exp_ver!r}, got {payload.get('material_version')!r}")

    answers = payload.get("answers")
    if not isinstance(answers, list):
        errors.append("GATE2_INVALID_ANSWERS: answers must be a list")
        answers = []

    # Gate 2: 100% question_id coverage with zero omissions or duplicates
    expected_qids = [q["question_id"] for q in questions]
    actual_qids = [str(a.get("question_id", "")) for a in answers if isinstance(a, dict)]
    missing_qids = [qid for qid in expected_qids if qid not in actual_qids]
    dup_qids = [qid for qid, cnt in Counter(actual_qids).items() if qid and cnt > 1]
    if missing_qids:
        errors.append(f"GATE2_MISSING_QUESTIONS: missing question_id(s) {missing_qids}")
    if dup_qids:
        errors.append(f"GATE2_DUPLICATE_QUESTIONS: duplicate question_id(s) {dup_qids}")

    cited_pids: set[str] = set()
    cited_pages: set[int] = set()

    for i, ans in enumerate(answers):
        if not isinstance(ans, dict):
            errors.append(f"answers[{i}] must be an object")
            continue
        qid = str(ans.get("question_id") or f"idx_{i}")
        st = ans.get("status")
        if st not in VALID_ANSWER_STATUSES:
            errors.append(f"GATE5_INVALID_STATUS: answers[{qid}].status invalid: {st!r}")

        conds = ans.get("conditions")
        if not isinstance(conds, list):
            errors.append(f"GATE6_INVALID_CONDITIONS: answers[{qid}].conditions must be a list")
            conds = []

        unans = ans.get("unanswered_parts")
        if not isinstance(unans, list):
            errors.append(f"GATE5_INVALID_UNANSWERED: answers[{qid}].unanswered_parts must be a list")
            unans = []
        else:
            if st == "answered" and len(unans) > 0:
                errors.append(f"GATE5_STATUS_CONFLICT: answers[{qid}] status='answered' requires unanswered_parts == []")
            if st in ("partial", "insufficient_evidence") and len(unans) == 0:
                errors.append(f"GATE5_MISSING_GAP_REASON: answers[{qid}] status={st!r} requires non-empty unanswered_parts[]")

        claims = ans.get("claims")
        if not isinstance(claims, list):
            errors.append(f"GATE4_INVALID_CLAIMS: answers[{qid}].claims must be a list")
            claims = []

        q_verified_texts: list[str] = []
        for cidx, cl in enumerate(claims):
            if not isinstance(cl, dict):
                errors.append(f"answers[{qid}].claims[{cidx}] must be an object")
                continue
            kind = cl.get("kind")
            if kind not in VALID_CLAIM_KINDS:
                errors.append(f"GATE4_INVALID_KIND: answers[{qid}].claims[{cidx}].kind must be reported|inferred")
            evs = cl.get("evidence") or []
            if kind == "reported" and not evs:
                errors.append(f"GATE4_MISSING_EVIDENCE: answers[{qid}].claims[{cidx}] kind='reported' requires >=1 verbatim quote")

            verified_for_claim = 0
            claim_verified_quotes: list[str] = []
            for eidx, ev in enumerate(evs):
                if not isinstance(ev, dict):
                    errors.append(f"GATE3_INVALID_EVIDENCE: answers[{qid}].claims[{cidx}].evidence[{eidx}] must be object")
                    continue
                pid = str(ev.get("paragraph_id") or "")
                quote = str(ev.get("quote") or "")
                if pid not in para_lookup:
                    errors.append(f"GATE3_UNKNOWN_PARAGRAPH: answers[{qid}].claims[{cidx}].evidence[{eidx}] unknown paragraph_id={pid!r}")
                elif not verify_exact_quote(quote, para_lookup[pid]):
                    # Check whether quote came from a different paragraph to give precise diagnostic
                    other_hit = next((opid for opid, otxt in para_lookup.items() if opid != pid and verify_exact_quote(quote, otxt)), None)
                    if other_hit:
                        errors.append(
                            f"GATE3_CROSS_PARAGRAPH_BORROWING: answers[{qid}].claims[{cidx}].evidence[{eidx}] "
                            f"quote cited under {pid!r} actually belongs to {other_hit!r}"
                        )
                    else:
                        errors.append(
                            f"GATE3_QUOTE_NOT_FOUND: answers[{qid}].claims[{cidx}].evidence[{eidx}] "
                            f"quote does not match referenced paragraph {pid!r}"
                        )
                else:
                    verified_for_claim += 1
                    claim_verified_quotes.append(quote)
                    cited_pids.add(pid)
                    q_verified_texts.append(para_lookup[pid])
                    if isinstance(para_page.get(pid), int):
                        cited_pages.add(int(para_page[pid]))

            if kind == "reported" and evs and verified_for_claim == 0:
                errors.append(f"GATE4_NO_VERIFIED_QUOTE: answers[{qid}].claims[{cidx}] has zero verified quotes")

            # Gate 8: Semantic numeric-core fidelity for reported claims (prevents tampered numbers / off-topic quotes)
            if kind == "reported" and claim_verified_quotes:
                num_ok, num_errs = verify_reported_claim_numeric_fidelity(str(cl.get("text") or ""), claim_verified_quotes)
                if not num_ok:
                    for ne in num_errs:
                        errors.append(f"GATE8_{ne} (question={qid}, claim={cidx})")

            # Gate 9: Inferred reasoning premise & anti-disguise gate
            inf_ok, inf_errs = verify_inferred_claim_premise(cl, verified_for_claim)
            if not inf_ok:
                for ie in inf_errs:
                    errors.append(f"GATE9_{ie} (question={qid}, claim={cidx})")

        # Gate 6: Conditions numeric/SI unit fidelity against verified cited paragraphs
        if conds and q_verified_texts:
            cond_ok, cond_errs = verify_conditions_against_paragraphs([str(c) for c in conds], q_verified_texts)
            if not cond_ok:
                for ce in cond_errs:
                    errors.append(f"GATE6_{ce} (question={qid})")

        # Gate 10: Atomic sub-question coverage grounding check
        q_meta = q_lookup.get(qid)
        if q_meta:
            sq_ok, sq_errs = _verify_subquestion_coverage(q_meta.get("sub_questions") or [], ans)
            if not sq_ok:
                for se in sq_errs:
                    errors.append(f"GATE10_{se} (question={qid})")

    # Gate 7: Coverage honesty check
    cov = payload.get("coverage")
    if not isinstance(cov, dict):
        errors.append("GATE7_INVALID_COVERAGE: coverage must be an object")
    else:
        declared_sections = [str(s) for s in (case_data.get("all_sections") or sections_seen)]
        expected_unread = [s for s in declared_sections if s not in sections_seen]
        reported_unread = [str(s) for s in (cov.get("unread_sections") or [])]
        for miss_sec in expected_unread:
            if miss_sec not in reported_unread:
                errors.append(f"GATE7_UNREPORTED_UNREAD_SECTION: unread section {miss_sec!r} omitted from coverage.unread_sections")
        if page_coverage_audit.get("empty_or_scanned_pages") and not cov.get("unread_pages") and not (cov.get("page_coverage_audit") or {}).get("empty_or_scanned_pages"):
            warnings.append("Empty/scanned PDF pages detected during indexing were backfilled into coverage.page_coverage_audit.")

    updated_audit = dict(page_coverage_audit)
    updated_audit["cited_paragraphs"] = len(cited_pids)
    updated_audit["cited_pages"] = sorted(cited_pages)

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "cited_paragraphs": len(cited_pids),
        "cited_pages": sorted(cited_pages),
        "page_coverage_audit": updated_audit,
    }


def _extract_short_quote(text: str, max_len: int = 180) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= max_len:
        return clean
    cut = clean[:max_len]
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


def build_retrieval_pack_and_skeleton(
    case_data: dict[str, Any],
    paragraphs: list[dict[str, Any]],
    sections_seen: list[str],
    questions: list[dict[str, Any]],
    page_coverage_audit: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build rich BM25 retrieval pack (with sub-question, tail-page, table/footnote & conflict hits) + safe skeleton."""
    case_id = str(case_data.get("case_id") or "case-unknown")
    paper_id = str(case_data.get("paper_id") or case_data.get("doi") or "paper-unknown")
    material_version = str(case_data.get("material_version") or case_data.get("version") or "v1")

    index = BM25Index(paragraphs)
    max_page = index.max_page
    tail_cutoff = max(1, max_page - max(1, max_page // 5) + 1) if max_page >= 3 else 0

    retrieval_questions: list[dict[str, Any]] = []
    answers_out: list[dict[str, Any]] = []

    for q in questions:
        qid = q["question_id"]
        qtext = q["question"]
        subqs = q["sub_questions"]

        if not paragraphs:
            retrieval_questions.append({
                "question_id": qid,
                "question": qtext,
                "sub_questions": subqs,
                "candidates": [],
                "subquestion_candidates": [],
                "tail_page_hits": [],
                "table_footnote_hits": [],
                "conflict_candidates": [],
            })
            answers_out.append({
                "question_id": qid,
                "question": qtext,
                "answer": "输入材料缺少可读正文段落，无法回答。",
                "conditions": [],
                "status": "input_error",
                "claims": [],
                "unanswered_parts": [
                    {"part": sp, "reason": "No readable paragraphs provided in input."}
                    for sp in (subqs or [qtext])
                ],
            })
            continue

        top_hits = index.query(qtext, top_k=8)
        subq_hits: list[dict[str, Any]] = []
        subq_draft_anchors: list[dict[str, Any]] = []
        merged_pool: dict[str, dict[str, Any]] = {h["paragraph_id"]: h for h in top_hits if h["score"] > 0}

        for sq_idx, sq in enumerate(subqs, start=1):
            sh = index.query(sq, top_k=3)
            pos_sh = [h for h in sh if h["score"] > 0]
            drafted_anchor: dict[str, Any] | None = None
            if pos_sh:
                best_h = pos_sh[0]
                s_quote = extract_shortest_verbatim_quote(best_h["text"], sq, max_len=220)
                drafted_anchor = {
                    "sub_question_index": sq_idx,
                    "sub_question": sq,
                    "paragraph_id": best_h["paragraph_id"],
                    "page": best_h.get("page"),
                    "section": best_h.get("section", ""),
                    "shortest_quote": s_quote,
                    "quote_verified": verify_exact_quote(s_quote, best_h["text"]),
                    "joint_bindings": extract_joint_semantic_bindings(s_quote),
                }
                subq_draft_anchors.append(drafted_anchor)
            subq_hits.append({
                "sub_question": sq,
                "drafted_anchor": drafted_anchor,
                "candidates": pos_sh,
            })
            for h in pos_sh:
                merged_pool.setdefault(h["paragraph_id"], h)

        pool_list = list(merged_pool.values())
        tail_hits = [
            h for h in pool_list
            if isinstance(h.get("page"), int) and tail_cutoff > 0 and h["page"] >= tail_cutoff
        ]
        if not tail_hits and max_page >= 3:
            # Always inspect tail pages explicitly to prevent first-page-only blind spot
            tail_only = [p for p in paragraphs if isinstance(p.get("page"), int) and p["page"] >= tail_cutoff]
            if tail_only:
                tail_idx = BM25Index(tail_only)
                tail_hits = [h for h in tail_idx.query(qtext, top_k=3) if h["score"] > 0]
                for h in tail_hits:
                    merged_pool.setdefault(h["paragraph_id"], h)

        table_fn_hits = [
            h for h in pool_list
            if h.get("block_type") in ("table", "footnote", "caption")
        ]
        if not table_fn_hits:
            tf_only = [p for p in paragraphs if p.get("block_type") in ("table", "footnote", "caption")]
            if tf_only:
                tf_idx = BM25Index(tf_only)
                table_fn_hits = [h for h in tf_idx.query(qtext, top_k=3) if h["score"] > 0]
                if not table_fn_hits and TABLE_FOOTNOTE_CUE_RE.search(qtext):
                    table_fn_hits = tf_idx.query(qtext, top_k=3)
                for h in table_fn_hits:
                    merged_pool.setdefault(h["paragraph_id"], h)

        conflicts = detect_cross_section_conflicts(list(merged_pool.values()))

        retrieval_questions.append({
            "question_id": qid,
            "question": qtext,
            "sub_questions": subqs,
            "subquestion_draft_anchors": subq_draft_anchors,
            "candidates": top_hits,
            "subquestion_candidates": subq_hits,
            "tail_page_hits": tail_hits,
            "table_footnote_hits": table_fn_hits,
            "conflict_candidates": conflicts,
        })

        # Safe grounded skeleton answer using shortest verbatim quotes per atomic sub-question
        positive_hits = sorted(merged_pool.values(), key=lambda x: -x["score"])
        claims: list[dict[str, Any]] = []
        conds: list[str] = []
        seen_skeleton_pairs: set[tuple[str, str]] = set()
        for anc in subq_draft_anchors:
            sq_q = anc["shortest_quote"]
            sq_pid = anc["paragraph_id"]
            if not sq_q or (sq_pid, sq_q) in seen_skeleton_pairs:
                continue
            seen_skeleton_pairs.add((sq_pid, sq_q))
            claims.append({
                "claim_id": f"{qid}-C{len(claims) + 1}",
                "sub_question": anc["sub_question"],
                "text": f"Candidate grounded excerpt from {sq_pid} ({anc['section']}): {sq_q}",
                "kind": "reported",
                "evidence": [{"paragraph_id": sq_pid, "quote": sq_q}],
            })
        if not claims:
            for h_idx, h in enumerate(positive_hits[:3], start=1):
                quote = extract_shortest_verbatim_quote(h["text"], qtext, max_len=220) or _extract_short_quote(h["text"])
                claims.append({
                    "claim_id": f"{qid}-C{h_idx}",
                    "text": f"Candidate grounded excerpt from {h['paragraph_id']} ({h['section']}): {quote}",
                    "kind": "reported",
                    "evidence": [{"paragraph_id": h["paragraph_id"], "quote": quote}],
                })
        for h in positive_hits[:3]:
            conds.extend(h["emlsi"].get("S_scope_conditions", []))
        conds = sorted(set(conds))[:8]

        if not positive_hits:
            status = "insufficient_evidence"
            answer_txt = "给定材料未检索到直接支持该问题的原文依据，按诚实弃答原则标记为材料不足。"
            unans = [{"part": sp, "reason": "No supporting evidence found in indexed paragraphs, tables, or footnotes."} for sp in subqs]
        else:
            status = "partial"
            conflict_note = f" 检测到 {len(conflicts)} 处跨章节数值/极性候选冲突，需双边核对。" if conflicts else ""
            answer_txt = f"已按原子子问完成 BM25、末页与表格脚注召回（待宿主 Agent 结合原文精读定稿）。{conflict_note}"
            unans = [
                {"part": sp, "reason": "Awaiting host Agent semantic synthesis and verification against retrieved quotes."}
                for sp in subqs
            ]

        answers_out.append({
            "question_id": qid,
            "question": qtext,
            "sub_questions": subqs,
            "answer": answer_txt,
            "conditions": conds,
            "status": status,
            "claims": claims,
            "unanswered_parts": unans,
        })

    declared_sections = [str(s) for s in (case_data.get("all_sections") or sections_seen)]
    unread = [s for s in declared_sections if s not in sections_seen]
    unread_pages = list(page_coverage_audit.get("empty_or_scanned_pages") or [])

    if unread and unread_pages:
        unread_reason = "missing_declared_sections_and_unreadable_scanned_pages"
    elif unread:
        unread_reason = "not_provided_in_case_paragraphs"
    elif unread_pages:
        unread_reason = "pdf_pages_missing_text_layer_or_scanned"
    else:
        unread_reason = "all_provided_sections_and_pages_indexed"

    retrieval_pack = {
        "case_id": case_id,
        "paper_id": paper_id,
        "material_version": material_version,
        "coverage_audit": page_coverage_audit,
        "declared_sections": declared_sections,
        "read_sections": sections_seen,
        "unread_sections": unread,
        "unread_pages": unread_pages,
        "questions": retrieval_questions,
    }

    skeleton_payload = {
        "case_id": case_id,
        "paper_id": paper_id,
        "material_version": material_version,
        "answers": answers_out,
        "coverage": {
            "read_sections": sections_seen,
            "unread_sections": unread,
            "unread_pages": unread_pages,
            "unread_reason": unread_reason,
            "total_paragraphs_read": len(paragraphs),
            "page_coverage_audit": page_coverage_audit,
        },
    }
    return retrieval_pack, skeleton_payload


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    *,
    draft_answers_json: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)

    case_path = input_dir / "case.json"
    if case_path.is_file():
        case_data: dict[str, Any] = json.loads(case_path.read_text(encoding="utf-8", errors="replace"))
    else:
        case_data = {"case_id": "missing-case", "paper_id": "unknown", "material_version": "unknown"}

    case_id = str(case_data.get("case_id") or "case-unknown")
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-15c85674")

    paragraphs, sections_seen, page_coverage_audit = normalize_corpus(case_data, input_dir)
    questions = normalize_questions(case_data)

    budget_guard.tick_phase(budget_file, "index_and_retrieve")
    result_guard.atomic_write_json(
        work_dir / "indexed_corpus.json",
        {"case_id": case_id, "page_coverage_audit": page_coverage_audit, "paragraphs": paragraphs},
    )
    retrieval_pack, skeleton_payload = build_retrieval_pack_and_skeleton(
        case_data, paragraphs, sections_seen, questions, page_coverage_audit
    )
    result_guard.atomic_write_json(work_dir / "qa_retrieval_pack.json", retrieval_pack)

    if draft_answers_json and draft_answers_json.is_file():
        qa_payload = json.loads(draft_answers_json.read_text(encoding="utf-8", errors="replace"))
    else:
        qa_payload = skeleton_payload

    schema_chk = validate_answers_payload(
        qa_payload,
        case_data=case_data,
        paragraphs=paragraphs,
        questions=questions,
        sections_seen=sections_seen,
        page_coverage_audit=page_coverage_audit,
    )

    # Enrich coverage with real 4-stage page_coverage_audit (including cited_paragraphs & cited_pages)
    if isinstance(qa_payload.get("coverage"), dict):
        qa_payload["coverage"]["page_coverage_audit"] = schema_chk["page_coverage_audit"]
        qa_payload["coverage"].setdefault("unread_pages", page_coverage_audit.get("empty_or_scanned_pages", []))
        qa_payload["coverage"].setdefault("total_paragraphs_read", len(paragraphs))

    gaps: list[dict[str, Any]] = []
    if not schema_chk["ok"]:
        gaps.append({
            "code": "QA_SCHEMA_ERRORS",
            "severity": "error",
            "source": "validate_answers_payload",
            "message": "; ".join(schema_chk["errors"]),
        })
    if not paragraphs:
        gaps.append({
            "code": "EMPTY_PARAGRAPH_CORPUS",
            "severity": "error",
            "source": "normalize_corpus",
            "message": "No readable paragraphs in input case.",
        })
    if page_coverage_audit.get("empty_or_scanned_pages"):
        gaps.append({
            "code": "EMPTY_OR_SCANNED_PDF_PAGES",
            "severity": "warning",
            "source": "page_coverage_audit",
            "message": f"Pages {page_coverage_audit['empty_or_scanned_pages']} yielded no extractable text layer.",
        })
    if any(a.get("status") in ("partial", "insufficient_evidence", "input_error") for a in qa_payload.get("answers", [])):
        gaps.append({
            "code": "PARTIAL_OR_UNANSWERED_SUBQUESTIONS",
            "severity": "info",
            "source": "literature_qa_engine",
            "message": "One or more questions are honestly marked partial/insufficient_evidence/input_error.",
        })

    # Write /app/output/answers.json AND mirror /app/answers.json
    artifacts = ["result.json", "answers.json"]
    result_guard.atomic_write_json(output_dir / "answers.json", qa_payload)
    app_root = output_dir.parent
    if app_root.name == "app" and app_root.is_dir():
        try:
            result_guard.atomic_write_json(app_root / "answers.json", qa_payload)
        except OSError:
            pass

    total_conflicts = sum(len(q.get("conflict_candidates") or []) for q in retrieval_pack.get("questions", []))
    patch = {
        "schema_version": "2.0",
        "artifacts": artifacts,
        "case_id": qa_payload.get("case_id", case_id),
        "paper_id": qa_payload.get("paper_id", str(case_data.get("paper_id") or "unknown")),
        "material_version": qa_payload.get("material_version", str(case_data.get("material_version") or "v1")),
        "answers": qa_payload.get("answers", []),
        "coverage": qa_payload.get("coverage", {}),
        "schema_validation": {
            "ok": schema_chk["ok"],
            "errors": schema_chk["errors"],
            "warnings": schema_chk["warnings"],
        },
        "metrics": {
            "question_count": len(questions),
            "paragraph_count": len(paragraphs),
            "ingested_pages": schema_chk["page_coverage_audit"]["ingested_pages"],
            "extracted_pages": schema_chk["page_coverage_audit"]["extracted_pages"],
            "cited_paragraphs": schema_chk["cited_paragraphs"],
            "conflict_candidates_detected": total_conflicts,
            "answered_count": sum(1 for a in qa_payload.get("answers", []) if a.get("status") == "answered"),
            "partial_count": sum(1 for a in qa_payload.get("answers", []) if a.get("status") == "partial"),
            "insufficient_count": sum(1 for a in qa_payload.get("answers", []) if a.get("status") == "insufficient_evidence"),
        },
        "metric_provenance": {
            "question_count": "case.json:questions",
            "paragraph_count": "indexed_corpus.json",
            "ingested_pages": "page_coverage_audit",
            "extracted_pages": "page_coverage_audit",
            "cited_paragraphs": "validate_answers_payload",
            "conflict_candidates_detected": "qa_retrieval_pack.json",
            "answered_count": "answers[].status",
            "partial_count": "answers[].status",
            "insufficient_count": "answers[].status",
        },
        "findings": [
            f"Processed {len(questions)} question(s) across {len(paragraphs)} indexed block(s) "
            f"(pages extracted {schema_chk['page_coverage_audit']['extracted_pages']}/"
            f"{schema_chk['page_coverage_audit']['ingested_pages']}; cited_paragraphs={schema_chk['cited_paragraphs']}; "
            f"conflict_candidates={total_conflicts})."
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(output_dir, result_file="result.json", required_files=["answers.json"])
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor challenge-15c85674 Literature QA & 10-gate semantic validator.")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    parser.add_argument("--validate-answers", default="", help="Drafted answers JSON to validate and promote into result.json + answers.json.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    draft_path = Path(args.validate_answers) if args.validate_answers else None
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], draft_answers_json=draft_path)
    schema_ok = res.get("schema_validation", {}).get("ok") is True
    print(json.dumps(
        {
            "ok": schema_ok,
            "case_id": res["case_id"],
            "status": res["status"],
            "schema_errors": res.get("schema_validation", {}).get("errors", []),
            "metrics": res["metrics"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if schema_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
