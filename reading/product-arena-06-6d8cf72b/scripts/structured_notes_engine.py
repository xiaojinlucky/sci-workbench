#!/usr/bin/env python3
"""Genre-aware, section-aligned structured literature notes engine (product-arena-06-6d8cf72b).

Pure Python 3.12 standard library (optional pdftotext/pypdf for PDF text layer).
Capabilities:
- Multi-format authentic ingestion (.pdf, .xml/.jats/.nxml, .json, .md, .txt, .tex) with true locators:
  * PDF -> file#page=P&extracted-line=L (explicitly flags blank pages needing OCR)
  * JATS XML / structured JSON -> file#paragraph_id=ID
  * Markdown / TXT / TeX -> file#source-line=L (never fabricates PDF page numbers)
- Automatic paper genre classification (research_article / review / perspective / benchmark_framework / comment).
- Original-order section synthesis cards with argumentative role and inter-section transitions.
- PaperStory discovery chain (problem_and_gap -> method_and_operation -> key_results_and_figures -> limitations_and_prerequisites)
  plus One-Figure-One-Method cards (rationale, controls/exclusions, evidence boundary, legend interpretation flag).
- Three-layer attributed Paper Card (author_reported / model_synthesized / not_reported) in paper_card.json.
- Deduplicated terminology/abbreviation glossary + evidence-density-ranked, section-balanced, capped knowledge points.
- Zero .tsv files, zero fabricated claims, full M1-M6 Harbor contract compliance.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import block_indexer
    import budget_guard
    import final_audit
    import gap_gate
    import preflight_probe
    import result_guard
    import zotero_adapter
    from citation_anchors import extract_emlsi_fingerprint, extract_identifiers, verify_exact_quote
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import block_indexer
    import budget_guard
    import final_audit
    import gap_gate
    import preflight_probe
    import result_guard
    import zotero_adapter
    from citation_anchors import extract_emlsi_fingerprint, extract_identifiers, verify_exact_quote


SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("references", re.compile(r"\b(?:references?|bibliography|参考文献)\b", re.I)),
    ("backmatter", re.compile(r"\b(?:acknowledg(?:e)?ments?|funding|conflict\s+of\s+interests?|competing\s+interests?|authors?['’]?\s+contributions?|data\s+availability|code\s+availability|agent\s+availability|declaration|ethics|致谢|利益冲突)\b", re.I)),
    ("abstract", re.compile(r"\b(?:abstract|resumo|summary|摘要)\b", re.I)),
    ("introduction", re.compile(r"\b(?:introduction|background|overview|引言|背景)\b", re.I)),
    ("related_work", re.compile(r"\b(?:related\s+work|literature\s+review|prior\s+work|相关工作|文献综述)\b", re.I)),
    ("methods", re.compile(r"\b(?:materials?\s+and\s+methods?|methods?|methodology|experimental|protocol|benchmark\s+design|study\s+design|方法|材料与方法|实验设计)\b", re.I)),
    ("results", re.compile(r"\b(?:results?|findings?|experiments?|evaluation|empirical|benchmark\s+results?|结果|实验结果|发现)\b", re.I)),
    ("limitations", re.compile(r"\b(?:limitations?|threats?\s+to\s+validity|caveats?|局限|局限性|限制)\b", re.I)),
    ("discussion", re.compile(r"\b(?:discussion|interpretation|implications?|讨论)\b", re.I)),
    ("conclusion", re.compile(r"\b(?:conclusions?|outlook|future\s+work|roadmap|结论|总结与展望)\b", re.I)),
]

PLAIN_HEADING_RE = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*|[IVX]+)\.?\s+)?"
    r"((?:abstract(?:\s+resumo)?|resumo|introduction|background|related\s+work|literature\s+review|"
    r"(?:materials?\s+and\s+)?methods?|methodology|experimental(?:\s+section|\s+methods?)?|"
    r"results?(?:\s+and\s+discussion)?|findings?|evaluation|discussion|"
    r"conclusions?(?:\s+and\s+outlook)?|limitations?|threats?\s+to\s+validity|"
    r"future\s+work|roadmap|acknowledg(?:e)?ments?|references?|funding|"
    r"conflict\s+of\s+interests?|competing\s+interests?|authors?['’]?\s+contributions?|"
    r"data\s+and\s+code\s+availability|code\s+and\s+data\s+availability|"
    r"data\s+availability|code\s+availability|agent\s+availability|software\s+availability|"
    r"ethics(?:\s+(?:approval|statement))?|ethical\s+(?:approval|considerations?)|declaration|"
    r"摘要|背景|引言|相关工作|材料与方法|实验方法|方法|结果与讨论|结果|讨论|局限性?|结论|总结与展望|参考文献))\s*[:：]?\s*$",
    re.I,
)
NUMBERED_SUBHEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)+\.?)\s+([A-Z\u4e00-\u9fff][^\n]{3,110})$")
TEX_HEADING_RE = re.compile(r"^\s*\\(?:sub)*section\*?\{([^}]+)\}")
HTML_ANCHOR_RE = re.compile(r'^\s*<a\s+id=["\']([A-Za-z0-9_.:-]+)["\']\s*>\s*</a>\s*$', re.I)

GAP_CUE_RE = re.compile(
    r"\b(?:however|remains?|unfortunately|underexplored|scarcity|lack(?:s|ing)?|challenge|bottleneck|poorly\s+understood|yet\s+to\s+be|gap|barriers?|siloed|objective\s*:|aim(?:s|ed)?\s+to)\b|然而|仍是挑战|鲜有|缺乏|尚未|瓶颈|空白",
    re.I,
)
HINGE_CUE_RE = re.compile(
    r"\b(?:here\s+we|in\s+this\s+(?:study|work|paper|article)|we\s+(?:present|show|propose|introduce|develop|demonstrate|report|construct|address)|objective\s*:\s*to|to\s+describe|to\s+analyze|addresses?\s+this\s+challenge)\b|本文提出|本研究|在此我们",
    re.I,
)
METHOD_RATIONALE_RE = re.compile(
    r"\b(?:to\s+(?:test|avoid|isolate|overcome|measure|resolve|distinguish|evaluate|validate)|selected|chosen|designed\s+to|using|via|protocol|assay|pipeline|regression|model|spectrometry|sequencing|chronopotentiometry|descriptive\s+statistics|observational\s+study|mcp\s+server|multi-agent|workflow|framework|implemented\s+with)\b|为验证|为排除|采用|通过|方法|协议",
    re.I,
)
CONTROL_CUE_RE = re.compile(
    r"\b(?:control|baseline|placebo|sham|blank|ablation|negative\s+control|positive\s+control|compared\s+(?:to|with)|versus|vs\.?|ruled?\s+out|exclud(?:e|ed|ing)|benchmark(?:ed)?\s+against|outperform(?:s|ed|ing)|relative\s+to|contrast(?:s|ed)?\s+with)\b|对照|基线|消融|排除|相比|对比",
    re.I,
)
LIMITATION_CUE_RE = re.compile(
    r"\b(?:limitation|limitations|caveat|remains?\s+to\s+be|cannot\s+(?:exclude|rule\s+out|generalize|establish)|confined\s+to|restricted\s+to|limited\s+to|small\s+sample|not\s+tested|future\s+work|prerequisite|assumption|only\s+valid\s+under|requires?\s+further|secondary\s+data|underreporting|ignored\s+fields?|inherent\s+difficulty|challenges?\s+remain|excluded\s+from|fails?\s+validation)\b|局限|前提|仅适用于|受限于|尚需验证|无法排除|样本量有限",
    re.I,
)
DATASET_CUE_RE = re.compile(
    r"\b(?:n\s*=\s*\d+|participants?|patients?|cohort|dataset|corpus|samples?|specimens?|subjects?|trials?|cell\s+lines?|mice|rats|catalysts?|compounds?|benchmarks?|papers?\s+screened|cases\s+of|inhabitants|queries|single-cell|transcriptomics|variants?|notifiable\s+diseases|data\s+extracted)\b|样本量|队列|数据集|受试者|患者|样本",
    re.I,
)
FIG_CALL_RE = re.compile(r"\b((?:Fig(?:ure)?\.?|Extended\s+Data\s+Fig(?:ure)?\.?|Supplementary\s+Fig(?:ure)?\.?|Table)\s*\d+[a-zA-Z]?)\b|图\s*\d+[a-zA-Z]?|表\s*\d+", re.I)
LEGEND_INTERP_RE = re.compile(
    r"\b(?:indicat(?:ing|es?)\s+that|suggest(?:ing|s?)\s+that|demonstrat(?:ing|es?)\s+that|confirm(?:ing|s?)\s+that|imply(?:ing)?\s+that|consistent\s+with)\b|表明|提示|说明",
    re.I,
)
QUANT_RE = re.compile(
    r"(?:\b\d+(?:,\d{3})*(?:\.\d+)?(?:\s*±\s*\d+(?:\.\d+)?)?\s*(?:%|mg/L|mmol|µM|uM|mM|nM|mV|V|mA|A|cm-2|mA/cm\^?2|nm|µm|um|mm|cm|kg|g|mg|µg|h|hr|min|minutes|s|ms|K|°C|Pa|kPa|MPa|ppm|ppb|eV|kJ|fold|×|/10,000|/100,000)\b|[pP]\s*[<=>]\s*0?\.\d+(?:e[-+]?\d+)?|95%\s*CI|\bR\^?2\s*=|\bAUC\s*=|\bF1\s*=|\bn\s*=\s*\d+(?:,\d{3})*)",
    re.I,
)

ABBR_FULL_THEN_SHORT_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9-]+(?:\s+[A-Za-z0-9-]+){1,5})\s*\(([A-Za-z]{1,3}[A-Z][A-Za-z0-9-]{0,11}|[A-Z]{2,}[0-9-]*)\)")
ABBR_ZH_RE = re.compile(r"([\u4e00-\u9fff]{2,14})[（(]([A-Za-z]{0,2}[A-Z][A-Za-z0-9-]{1,10})[）)]")

INSTRUCTION_FILE_NAMES = {
    "task.md", "task.txt", "instructions.md", "instruction.md", "readme.md", "case.json", "reference.json",
}
SUPPORTED_TEXT_EXTS = {".md", ".txt", ".tex", ".xml", ".nxml", ".jats", ".json", ".pdf"}


def split_sentences(text: str) -> list[str]:
    """Split scientific prose into sentences while protecting abbreviations and decimals."""
    protected = text
    abbrevs = ("Fig.", "Figs.", "Eq.", "Ref.", "Refs.", "Tab.", "Dr.", "Prof.", "et al.", "vs.", "i.e.", "e.g.", "approx.", "No.")
    placeholders: dict[str, str] = {}
    for idx, ab in enumerate(abbrevs):
        token = f"__ABBR{idx}__"
        placeholders[token] = ab
        protected = re.sub(re.escape(ab), token, protected)
    raw_parts = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\u4e00-\u9fff\"'(\[])", protected)
    out: list[str] = []
    for part in raw_parts:
        restored = part
        for token, ab in placeholders.items():
            restored = restored.replace(token, ab)
        s = " ".join(restored.split()).strip()
        if s:
            out.append(s)
    return out


def classify_section_category(title: str) -> str:
    for cat, pat in SECTION_PATTERNS:
        if pat.search(title):
            return cat
    return "other"


def format_anchor(p: dict[str, Any]) -> str:
    src = p["source_file"]
    kind = p["anchor_kind"]
    if kind == "pdf-page-line" and "page" in p:
        return f"{src}#page={p['page']}&extracted-line={p['line_on_page']}"
    if kind in {"paragraph_id", "xml-path", "json-block"}:
        return f"{src}#{kind}={p['paragraph_id']}"
    return f"{src}#source-line={p['line_no']}"


FLOAT_CAPTION_START_RE = re.compile(
    r"^\s*(?:Table|Figure|Fig\.?|Supplementary\s+(?:Table|Figure|Fig\.?)|Extended\s+Data\s+(?:Table|Figure|Fig\.?))\s+\d+[A-Za-z]?(?:\s*[:.\-–]|\s+[A-Z])"
)
PDF_PARA_OPENER_RE = re.compile(
    r"^(?:Objective:|Methods:|Results:|Conclusions?:|Considering\b|Regarding\b|When\s+analyzing\b|"
    r"In\s+the\s+present\s+study\b|In\s+this\s+study\b|Another\s+(?:issue|limitation|factor|aspect)\b|"
    r"Furthermore,|However,|Likewise,|Therefore,|Thus,|This\s+study\s+has\s+limitations\b|"
    r"Other\s+limitations\b|The\s+study\s+showed\b|In\s+conclusion\b|Worldwide,\b|Although\s+this\s+study\b|"
    r"Due\s+to\s+this\b|There\s+are\s+probably\b|It\s+is\s+worth\b)"
)
REF_ENTRY_RE = re.compile(r"^\s*(?:\[\d+\]|\d{1,3}\.\s+[A-Z])")
ICD_OR_FOOTNOTE_RE = re.compile(r"^(?:[A-Z]\d{2}(?:\.\d)?\s+to\s+[A-Z]\d{2}|ICD-10:|APC:|95%CI:|\*Statistical)", re.I)


def _is_tabular_or_axis_line(s: str) -> bool:
    """Detect numeric table rows, axis tick labels, or short chart annotations in PDF text."""
    if not s:
        return True
    if ICD_OR_FOOTNOTE_RE.match(s):
        return True
    toks = s.split()
    if len(toks) <= 2 and re.fullmatch(r"[\d.,%()–\-+/<>=*]+", "".join(toks)):
        return True
    num_toks = sum(1 for t in toks if re.search(r"\d", t) or t in {"–", "-", "—", "*", "n", "%"})
    if len(toks) >= 2 and (num_toks / len(toks)) >= 0.45 and len(s) < 95:
        return True
    if len(toks) <= 6 and not s.endswith((".", "!", "?")) and not re.search(r"\b(?:the|was|were|are|is|that|with|from|which|have|has|for)\b", s, re.I):
        return True
    return False


def _detect_pdf_running_headers(pages: list[str]) -> set[str]:
    """Identify repeated running headers/footers appearing identically on >=3 pages."""
    if len(pages) < 3:
        return set()
    page_counts: Counter[str] = Counter()
    for pg in pages:
        seen: set[str] = set()
        raw_lines = [ln.strip() for ln in pg.splitlines() if ln.strip()]
        for ln in raw_lines[:4] + raw_lines[-3:]:
            if 3 <= len(ln) <= 95:
                seen.add(ln)
        for ln in seen:
            page_counts[ln] += 1
    return {ln for ln, c in page_counts.items() if c >= 3}


def parse_plain_or_md_sections(
    text: str,
    source_name: str,
    *,
    pdf: bool = False,
    extracted_floats: list[dict[str, Any]] | None = None,
    doc_meta: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Parse Markdown/TXT/TeX/PDF text preserving original headings, clean prose boundaries, and true locators."""
    pages = text.split("\f") if pdf else [text]
    running_headers = _detect_pdf_running_headers(pages) if pdf else set()
    lines = [
        (page_no, line_on_page, raw_line)
        for page_no, page_txt in enumerate(pages, 1)
        for line_on_page, raw_line in enumerate(page_txt.splitlines(), 1)
    ]

    sections: list[dict[str, Any]] = []
    current_sec: dict[str, Any] = {
        "section_id": f"{source_name}_sec_0",
        "title": "Preamble",
        "category": "unsectioned",
        "source_file": source_name,
        "line_start": 1,
        "paragraphs": [],
    }
    sec_counter = 1

    buf_lines: list[str] = []
    buf_start_global = 1
    buf_start_page = 1
    buf_start_page_line = 1
    pending_html_anchor: str | None = None
    in_pdf_float: dict[str, Any] | None = None
    post_ref_unclassified_paras: list[dict[str, Any]] = []
    parent_h2_cat: str | None = None

    def flush_float() -> None:
        nonlocal in_pdf_float
        if in_pdf_float and extracted_floats is not None:
            caption_txt = " ".join(in_pdf_float["caption_lines"]).strip()
            if caption_txt:
                in_pdf_float["text"] = caption_txt
                extracted_floats.append(in_pdf_float)
        in_pdf_float = None

    def flush_paragraph() -> None:
        nonlocal buf_lines, pending_html_anchor
        if not buf_lines:
            return
        raw_joined = "\n".join(x.strip() for x in buf_lines if x.strip())
        raw_joined = re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", raw_joined)
        joined = " ".join(raw_joined.split()).strip()
        if joined:
            if pending_html_anchor and not pdf:
                p_obj: dict[str, Any] = {
                    "line_no": buf_start_global,
                    "paragraph_id": pending_html_anchor,
                    "text": joined,
                    "source_file": source_name,
                    "anchor_kind": "paragraph_id",
                }
            else:
                p_obj = {
                    "line_no": buf_start_global,
                    "text": joined,
                    "source_file": source_name,
                    "anchor_kind": "pdf-page-line" if pdf else "source-line",
                }
                if pdf:
                    p_obj["page"] = buf_start_page
                    p_obj["line_on_page"] = buf_start_page_line
            p_obj["anchor"] = format_anchor(p_obj)

            # NEVER blindly promote non-reference prose after REFERENCES into Discussion/Conclusions
            # without an explicit standalone section heading. Quarantine as post_references_unclassified.
            if (
                pdf
                and current_sec["category"] == "references"
                and not REF_ENTRY_RE.match(joined)
                and len(joined.split()) >= 14
                and not re.search(r"\b(?:19|20)\d{2};\d+:", joined)
            ):
                post_ref_unclassified_paras.append(p_obj)
            else:
                current_sec["paragraphs"].append(p_obj)
        buf_lines = []
        pending_html_anchor = None

    for g_idx, (page_no, page_line, line) in enumerate(lines, start=1):
        stripped = line.strip()

        # Skip HTML anchor lines in Markdown while preserving native paragraph_id
        if not pdf:
            ha_m = HTML_ANCHOR_RE.match(line)
            if ha_m:
                flush_paragraph()
                pending_html_anchor = ha_m.group(1).strip()
                continue

        # Skip PDF running headers/footers and standalone page numbers
        if pdf and (stripped in running_headers or re.fullmatch(r"\d{1,3}", stripped)):
            continue

        md_m = re.match(r"^\s*(#{1,4})\s+(.+?)\s*$", line)
        tex_m = TEX_HEADING_RE.match(line)
        plain_m = PLAIN_HEADING_RE.fullmatch(line)
        num_m = NUMBERED_SUBHEADING_RE.fullmatch(line) if not stripped.endswith(".") else None

        heading_title: str | None = None
        heading_level = 2
        if md_m:
            hashes, htxt = md_m.group(1), md_m.group(2).strip().rstrip("#").strip()
            if hashes == "#" and doc_meta is not None and not doc_meta.get("title"):
                doc_meta["title"] = htxt
                continue
            heading_title = htxt
            heading_level = len(hashes)
        elif tex_m:
            heading_title = tex_m.group(1).strip()
        elif plain_m:
            heading_title = plain_m.group(1).strip()
        elif num_m and len(num_m.group(2).split()) <= 12:
            heading_title = f"{num_m.group(1)} {num_m.group(2).strip()}"
            heading_level = 3

        if heading_title:
            flush_float()
            flush_paragraph()
            if current_sec["paragraphs"]:
                sections.append(current_sec)
            own_cat = classify_section_category(heading_title)
            if heading_level <= 2:
                parent_h2_cat = own_cat if own_cat != "other" else None
                eff_cat = own_cat
            else:
                eff_cat = own_cat if own_cat != "other" else (parent_h2_cat or "other")
            current_sec = {
                "section_id": f"{source_name}_sec_{sec_counter}",
                "title": heading_title,
                "category": eff_cat,
                "source_file": source_name,
                "line_start": g_idx,
                "paragraphs": [],
            }
            sec_counter += 1
            continue

        # PDF floating Table/Figure block isolation (prevents 500+ table cells/axis ticks from bloating Discussion)
        if pdf:
            if in_pdf_float is not None:
                if page_no != in_pdf_float["page"]:
                    flush_float()
                elif not stripped:
                    in_pdf_float["blank_streak"] += 1
                    if in_pdf_float["blank_streak"] >= 2:
                        flush_float()
                    continue
                elif _is_tabular_or_axis_line(stripped) or len(in_pdf_float["caption_lines"]) < 3 and not re.search(r"[.!?]\s*$", in_pdf_float["caption_lines"][-1]):
                    in_pdf_float["blank_streak"] = 0
                    if len(in_pdf_float["caption_lines"]) < 4 and not _is_tabular_or_axis_line(stripped):
                        in_pdf_float["caption_lines"].append(stripped)
                    else:
                        in_pdf_float["data_lines"] += 1
                    continue
                else:
                    flush_float()

            if FLOAT_CAPTION_START_RE.match(stripped):
                flush_paragraph()
                in_pdf_float = {
                    "source_file": source_name,
                    "section": current_sec["title"],
                    "page": page_no,
                    "line_on_page": page_line,
                    "anchor_kind": "pdf-page-line",
                    "anchor": f"{source_name}#page={page_no}&extracted-line={page_line}",
                    "caption_lines": [stripped],
                    "data_lines": 0,
                    "blank_streak": 0,
                }
                continue

            if ICD_OR_FOOTNOTE_RE.match(stripped) and current_sec["category"] in {"discussion", "results", "methods"}:
                flush_paragraph()
                if extracted_floats is not None:
                    extracted_floats.append({
                        "source_file": source_name,
                        "section": current_sec["title"],
                        "page": page_no,
                        "line_on_page": page_line,
                        "anchor_kind": "pdf-page-line",
                        "anchor": f"{source_name}#page={page_no}&extracted-line={page_line}",
                        "text": stripped,
                    })
                continue

        if not stripped:
            flush_paragraph()
            continue

        # Clean paragraph boundaries for single-newline PDF prose & list starts
        if buf_lines:
            prev_s = buf_lines[-1]
            prev_ends_sent = bool(re.search(r"[.!?。！？\)\]]\s*(?:\d{1,2}(?:,\d{1,2})*)?$", prev_s))
            if (
                (pdf and page_no != buf_start_page)
                or re.match(r"^(?:[-*•]\s+|\d+\)\s+|Fig(?:ure)?\.?\s*\d+|Table\s*\d+)", stripped)
                or (pdf and prev_ends_sent and (PDF_PARA_OPENER_RE.match(stripped) or len(prev_s) < 48))
                or (pdf and current_sec["category"] == "references" and REF_ENTRY_RE.match(stripped))
            ):
                flush_paragraph()

        if not buf_lines:
            buf_start_global = g_idx
            buf_start_page = page_no
            buf_start_page_line = page_line
        buf_lines.append(stripped)

    flush_float()
    flush_paragraph()
    if current_sec["paragraphs"]:
        sections.append(current_sec)

    # Keep non-reference prose appearing after REFERENCES without an explicit heading
    # in an auditable post_references_unclassified section (never promote to Discussion/Conclusions).
    if post_ref_unclassified_paras:
        sections.append({
            "section_id": f"{source_name}_sec_{sec_counter}",
            "title": "Post-References Unclassified Text",
            "category": "post_references_unclassified",
            "source_file": source_name,
            "line_start": post_ref_unclassified_paras[0]["line_no"],
            "paragraphs": post_ref_unclassified_paras,
        })
        sec_counter += 1

    # Promote Preamble structured abstract (Objective:/Methods:/Results:/Conclusions:) to abstract if no abstract prose exists
    if sections and sections[0]["category"] == "unsectioned":
        pre_txt = " ".join(p["text"] for p in sections[0]["paragraphs"])
        if re.search(r"\bObjective\s*:", pre_txt, re.I) and re.search(r"\bResults\s*:", pre_txt, re.I):
            sections[0]["title"] = "Structured Abstract"
            sections[0]["category"] = "abstract"

    return sections


def _xml_text(elem: ET.Element) -> str:
    return " ".join("".join(elem.itertext()).split()).strip()


def parse_jats_xml_sections(xml_text: str, source_name: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Parse JATS/NXML keeping real <sec> structure and <p id='...'> paragraph_id anchors."""
    meta: dict[str, str] = {}
    root = ET.fromstring(xml_text)

    # Strip namespace helper
    def local_tag(tag: str) -> str:
        return tag.split("}", 1)[-1] if "}" in tag else tag

    for elem in root.iter():
        lt = local_tag(elem.tag)
        if lt == "article-title" and not meta.get("title"):
            meta["title"] = _xml_text(elem)
        elif lt == "article-id" and elem.attrib.get("pub-id-type") == "doi" and not meta.get("doi"):
            meta["doi"] = _xml_text(elem)

    sections: list[dict[str, Any]] = []
    sec_counter = 1

    # Abstract first if present
    for elem in root.iter():
        if local_tag(elem.tag) == "abstract":
            paras: list[dict[str, Any]] = []
            for p_idx, p_el in enumerate(elem.iter(), start=1):
                if local_tag(p_el.tag) == "p":
                    txt = _xml_text(p_el)
                    if not txt:
                        continue
                    pid = p_el.attrib.get("id")
                    kind = "paragraph_id" if pid else "xml-path"
                    p_obj = {
                        "line_no": p_idx,
                        "paragraph_id": pid or f"abs_p{p_idx}",
                        "text": txt,
                        "source_file": source_name,
                        "anchor_kind": kind,
                    }
                    p_obj["anchor"] = format_anchor(p_obj)
                    paras.append(p_obj)
            if paras:
                sections.append({
                    "section_id": f"{source_name}_sec_{sec_counter}",
                    "title": "Abstract",
                    "category": "abstract",
                    "source_file": source_name,
                    "line_start": 1,
                    "paragraphs": paras,
                })
                sec_counter += 1

    # Body sections
    for elem in root.iter():
        if local_tag(elem.tag) == "sec":
            title_el = next((c for c in elem if local_tag(c.tag) == "title"), None)
            title = _xml_text(title_el) if title_el is not None else f"Section {sec_counter}"
            paras = []
            p_local = 0
            for child in elem:
                if local_tag(child.tag) == "p":
                    p_local += 1
                    txt = _xml_text(child)
                    if not txt:
                        continue
                    pid = child.attrib.get("id")
                    kind = "paragraph_id" if pid else "xml-path"
                    p_obj = {
                        "line_no": p_local,
                        "paragraph_id": pid or f"sec{sec_counter}_p{p_local}",
                        "text": txt,
                        "source_file": source_name,
                        "anchor_kind": kind,
                    }
                    p_obj["anchor"] = format_anchor(p_obj)
                    paras.append(p_obj)
            if paras:
                sections.append({
                    "section_id": f"{source_name}_sec_{sec_counter}",
                    "title": title,
                    "category": classify_section_category(title),
                    "source_file": source_name,
                    "line_start": 1,
                    "paragraphs": paras,
                })
                sec_counter += 1

    return sections, meta


def parse_json_sections(json_text: str, source_name: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Parse structured paper JSON preserving native paragraph_id / section titles."""
    obj = json.loads(json_text)
    meta: dict[str, str] = {}
    sections: list[dict[str, Any]] = []

    if isinstance(obj, dict):
        if obj.get("title"):
            meta["title"] = str(obj["title"])
        if obj.get("doi") or obj.get("primary_doi"):
            meta["doi"] = str(obj.get("doi") or obj.get("primary_doi"))

        if isinstance(obj.get("sections"), list):
            for s_idx, sec in enumerate(obj["sections"], start=1):
                if not isinstance(sec, dict):
                    continue
                title = str(sec.get("title") or sec.get("heading") or f"Section {s_idx}")
                raw_ps = sec.get("paragraphs") or ([] if not sec.get("text") else [{"text": sec["text"]}])
                paras: list[dict[str, Any]] = []
                for p_idx, p in enumerate(raw_ps, start=1):
                    if isinstance(p, dict):
                        txt = str(p.get("text") or p.get("content") or "").strip()
                        pid = p.get("paragraph_id") or p.get("id")
                    else:
                        txt = str(p).strip()
                        pid = None
                    if not txt:
                        continue
                    kind = "paragraph_id" if pid else "json-block"
                    p_obj = {
                        "line_no": p_idx,
                        "paragraph_id": str(pid) if pid else f"s{s_idx}_b{p_idx}",
                        "text": txt,
                        "source_file": source_name,
                        "anchor_kind": kind,
                    }
                    p_obj["anchor"] = format_anchor(p_obj)
                    paras.append(p_obj)
                if paras:
                    sections.append({
                        "section_id": f"{source_name}_sec_{s_idx}",
                        "title": title,
                        "category": classify_section_category(title),
                        "source_file": source_name,
                        "line_start": 1,
                        "paragraphs": paras,
                    })
        elif isinstance(obj.get("paragraphs"), list):
            grouped: dict[str, list[dict[str, Any]]] = {}
            order: list[str] = []
            for p_idx, p in enumerate(obj["paragraphs"], start=1):
                if isinstance(p, dict):
                    sec_t = str(p.get("section") or p.get("heading") or "Body")
                    txt = str(p.get("text") or p.get("content") or "").strip()
                    pid = p.get("paragraph_id") or p.get("id")
                else:
                    sec_t = "Body"
                    txt = str(p).strip()
                    pid = None
                if not txt:
                    continue
                if sec_t not in grouped:
                    grouped[sec_t] = []
                    order.append(sec_t)
                kind = "paragraph_id" if pid else "json-block"
                p_obj = {
                    "line_no": p_idx,
                    "paragraph_id": str(pid) if pid else f"b{p_idx}",
                    "text": txt,
                    "source_file": source_name,
                    "anchor_kind": kind,
                }
                p_obj["anchor"] = format_anchor(p_obj)
                grouped[sec_t].append(p_obj)
            for s_idx, sec_t in enumerate(order, start=1):
                sections.append({
                    "section_id": f"{source_name}_sec_{s_idx}",
                    "title": sec_t,
                    "category": classify_section_category(sec_t),
                    "source_file": source_name,
                    "line_start": 1,
                    "paragraphs": grouped[sec_t],
                })
    return sections, meta


def classify_paper_genre(sections: list[dict[str, Any]], full_text: str) -> dict[str, Any]:
    """Classify article genre using article-anatomy cues without forcing IMRaD."""
    cats = {s["category"] for s in sections}
    low = full_text.lower()
    opening = low[:2500]
    scores: dict[str, int] = {
        "research_article": 0,
        "review": 0,
        "perspective": 0,
        "benchmark_framework": 0,
        "comment": 0,
    }
    cues: dict[str, list[str]] = {k: [] for k in scores}

    if {"methods", "results"} <= cats:
        scores["research_article"] += 5
        cues["research_article"].append("IMRaD Methods+Results sections present")
    elif "results" in cats and ("introduction" in cats or "discussion" in cats):
        scores["research_article"] += 4
        cues["research_article"].append("Empirical Results with Introduction/Discussion structure")
    if re.search(r"\b(?:here we (?:show|present|demonstrate|report)|we (?:introduce|demonstrate|measured|performed|analyzed)|observational study|retrospective|prospective|cross-sectional|case studies|our experiments)\b", low):
        scores["research_article"] += 4
        cues["research_article"].append("Empirical/observational or original-method reporting verbs")
    if re.search(r"\b(?:systematic review|scoping review|meta-analysis|prisma|literature survey|in this review|we review recent)\b", opening):
        scores["review"] += 6
        cues["review"].append("Explicit review/survey/PRISMA cues in opening")
    if "outlook" in low and "methods" not in cats and "results" not in cats:
        scores["review"] += 2
        cues["review"].append("Synthesis/outlook structure without own-data Methods/Results")
    if re.search(r"\b(?:we introduce a benchmark|new benchmark|leaderboard|evaluation suite|standardized evaluation|task suite|versioned dataset|reproducibility framework)\b", opening):
        scores["benchmark_framework"] += 6
        cues["benchmark_framework"].append("Benchmark/evaluation-suite framing in title/abstract")
    if re.search(r"\b(?:roadmap|call to action|field should|community must|perspective|policy recommendation)\b", opening):
        scores["perspective"] += 5
        cues["perspective"].append("Normative roadmap/perspective cues")
    if len(sections) <= 2 and len(full_text) < 4000 and re.search(r"\b(?:commentary|correspondence|editorial|i argue)\b", low):
        scores["comment"] += 5
        cues["comment"].append("Short non-IMRaD commentary format")

    best_genre = max(scores, key=lambda k: scores[k])
    if scores[best_genre] == 0:
        best_genre = "research_article" if ("results" in cats or "methods" in cats) else "unclassified"

    return {
        "genre": best_genre,
        "label": best_genre,
        "attribution": "model_synthesized",
        "scores": scores,
        "evidence_cues": cues.get(best_genre, ["Insufficient structural cues"]),
    }


def _sentence_evidence_score(sent: str, sec_cat: str) -> float:
    score = 0.0
    if QUANT_RE.search(sent):
        score += 3.0
    if FIG_CALL_RE.search(sent):
        score += 2.0
    if CONTROL_CUE_RE.search(sent):
        score += 2.0
    if LIMITATION_CUE_RE.search(sent):
        score += 2.2
    if HINGE_CUE_RE.search(sent):
        score += 1.8
    if GAP_CUE_RE.search(sent):
        score += 1.5
    if DATASET_CUE_RE.search(sent):
        score += 1.5
    if sec_cat in {"results", "methods", "limitations"}:
        score += 1.0
    elif sec_cat == "discussion":
        score += 0.6
    # Penalize overly short or excessively long noisy lines
    if len(sent) < 28:
        score -= 2.5
    elif len(sent) > 420:
        score -= 0.8
    return round(score, 2)


def _token_jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", a.casefold()))
    tb = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", b.casefold()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def build_section_cards(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build original-order section synthesis cards with argumentative role and transitions."""
    role_map = {
        "abstract": "funnel_abstract",
        "introduction": "hook_gap_hinge",
        "related_work": "prior_landscape",
        "methods": "method_and_controls",
        "results": "evidence_and_results",
        "discussion": "interpretation_and_limits",
        "limitations": "explicit_limitations",
        "conclusion": "synthesis_and_outlook",
    }
    transition_map = {
        ("abstract", "introduction"): "abstract_to_problem_framing",
        ("introduction", "methods"): "gap_to_experimental_design",
        ("introduction", "results"): "gap_to_direct_results",
        ("methods", "results"): "design_to_empirical_validation",
        ("results", "discussion"): "evidence_to_interpretation_and_bounds",
        ("results", "limitations"): "evidence_to_boundary_conditions",
        ("discussion", "limitations"): "interpretation_to_explicit_caveats",
        ("discussion", "conclusion"): "interpretation_to_takeaway",
        ("limitations", "conclusion"): "caveats_to_bounded_conclusion",
    }

    cards: list[dict[str, Any]] = []
    prev_cat: str | None = None
    for idx, sec in enumerate(sections, start=1):
        cat = sec["category"]
        candidates: list[tuple[float, str, str]] = []
        fig_refs: list[str] = []
        for p in sec["paragraphs"]:
            fig_refs.extend(m.group(0) for m in FIG_CALL_RE.finditer(p["text"]))
            for sent in split_sentences(p["text"]):
                if verify_exact_quote(sent, p["text"]):
                    candidates.append((_sentence_evidence_score(sent, cat), sent, p["anchor"]))
        candidates.sort(key=lambda x: -x[0])
        key_points: list[dict[str, Any]] = []
        for sc, sent, anc in candidates:
            if any(_token_jaccard(sent, kp["quote"]) >= 0.75 for kp in key_points):
                continue
            key_points.append({
                "quote": sent,
                "anchor": anc,
                "evidence_score": sc,
                "attribution": "author_reported",
            })
            if len(key_points) >= 3:
                break

        trans = "document_opening" if prev_cat is None else transition_map.get(
            (prev_cat, cat), f"{prev_cat}_to_{cat}"
        )
        cards.append({
            "order": idx,
            "section_id": sec["section_id"],
            "title": sec["title"],
            "category": cat,
            "argumentative_role": role_map.get(cat, "domain_specific_section"),
            "transition_from_previous": trans,
            "paragraph_count": len(sec["paragraphs"]),
            "locator_range": {
                "start": sec["paragraphs"][0]["anchor"] if sec["paragraphs"] else None,
                "end": sec["paragraphs"][-1]["anchor"] if sec["paragraphs"] else None,
            },
            "figure_table_mentions": sorted(set(fig_refs)),
            "key_excerpts": key_points,
        })
        prev_cat = cat
    return cards


def extract_figure_method_cards(
    sections: list[dict[str, Any]],
    extracted_floats: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """One-Figure-One-Method lens: why chosen, what controls exclude, boundary, legend interpretation flag."""
    fig_map: dict[str, dict[str, Any]] = {}
    sources_iter: list[tuple[str, str, str]] = []
    for sec in sections:
        if sec["category"] in {"references", "backmatter"}:
            continue
        for p in sec["paragraphs"]:
            sources_iter.append((sec["title"], p["text"], p["anchor"]))
    for fl in (extracted_floats or []):
        if fl.get("text"):
            sources_iter.append((fl.get("section") or "Float", fl["text"], fl["anchor"]))

    for sec_title, para_txt, anc in sources_iter:
        for sent in split_sentences(para_txt):
            matches = list(FIG_CALL_RE.finditer(sent))
            if not matches or not verify_exact_quote(sent, para_txt):
                continue
            is_caption_like = bool(re.match(r"^\s*(?:Fig(?:ure)?\.?|Extended\s+Data\s+Fig(?:ure)?\.?|Table)\s*\d+(?:\s*[:|.—-]|\s+[A-Z])", sent, re.I))
            for m in matches:
                fid = " ".join(m.group(0).split())
                entry = fig_map.setdefault(fid, {
                    "figure_or_table_id": fid,
                    "sections": [],
                    "anchors": [],
                    "quotes": [],
                    "method_rationale": [],
                    "controls_or_exclusions": [],
                    "evidence_boundaries": [],
                    "legend_contains_interpretation": False,
                    "attribution": "author_reported",
                })
                if sec_title not in entry["sections"]:
                    entry["sections"].append(sec_title)
                if anc not in entry["anchors"]:
                    entry["anchors"].append(anc)
                if sent not in entry["quotes"] and len(entry["quotes"]) < 3:
                    entry["quotes"].append(sent)
                if METHOD_RATIONALE_RE.search(sent) and sent not in entry["method_rationale"]:
                    entry["method_rationale"].append(sent)
                if CONTROL_CUE_RE.search(sent) and sent not in entry["controls_or_exclusions"]:
                    entry["controls_or_exclusions"].append(sent)
                if LIMITATION_CUE_RE.search(sent) and sent not in entry["evidence_boundaries"]:
                    entry["evidence_boundaries"].append(sent)
                if is_caption_like and LEGEND_INTERP_RE.search(sent):
                    entry["legend_contains_interpretation"] = True
    return list(fig_map.values())[:12]


def extract_terminology_glossary(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract and deduplicate Full Term (ABBR) definitions with first-definition locator."""
    glossary: dict[str, dict[str, Any]] = {}
    for sec in sections:
        if sec["category"] in {"references", "backmatter"}:
            continue
        for p in sec["paragraphs"]:
            txt = p["text"]
            for pat in (ABBR_FULL_THEN_SHORT_RE, ABBR_ZH_RE):
                for m in pat.finditer(txt):
                    full_term = " ".join(m.group(1).split()).strip()
                    abbr = m.group(2).strip()
                    # Trim leading stop/preposition words from captured English phrase
                    full_term = re.sub(r"^(?:the|a|an|of|in|for|by|and|with|to|across|using|integrated|state\s+of)\s+", "", full_term, flags=re.I)
                    if len(abbr) < 2 or abbr.isdigit() or len(full_term) < len(abbr) + 2:
                        continue
                    if abbr.lower() in {"fig", "tab", "ref", "eq", "vs", "sd", "se", "ci", "al", "uk", "us"}:
                        continue
                    # Basic letter plausibility: at least half of uppercase letters in abbr appear in full_term
                    u_letters = [ch.lower() for ch in abbr if ch.isalpha()]
                    if u_letters and sum(1 for ch in u_letters if ch in full_term.lower()) < max(2, len(u_letters) // 2):
                        continue
                    key = abbr.upper()
                    if key not in glossary:
                        glossary[key] = {
                            "abbreviation": abbr,
                            "canonical_expansion": full_term,
                            "variants": [full_term],
                            "first_anchor": p["anchor"],
                            "section": sec["title"],
                            "mention_count": 1,
                            "attribution": "author_reported",
                        }
                    else:
                        glossary[key]["mention_count"] += 1
                        if full_term not in glossary[key]["variants"]:
                            glossary[key]["variants"].append(full_term)
    # Count additional occurrences of each defined abbreviation across corpus
    full_blob = "\n".join(p["text"] for s in sections for p in s["paragraphs"])
    for item in glossary.values():
        hits = len(re.findall(rf"\b{re.escape(item['abbreviation'])}\b", full_blob))
        item["mention_count"] = max(item["mention_count"], hits)
    return sorted(glossary.values(), key=lambda x: (-x["mention_count"], x["abbreviation"]))[:25]


def _make_card_field(
    quotes_with_anchors: list[tuple[str, str]],
    *,
    is_list: bool = False,
    not_reported_msg: str = "Not reported in available source text.",
) -> dict[str, Any]:
    if not quotes_with_anchors:
        return {
            "value": [] if is_list else not_reported_msg,
            "attribution": "not_reported",
            "evidence_anchors": [],
            "evidence_quotes": [],
        }
    quotes = [q for q, _ in quotes_with_anchors]
    anchors = list(dict.fromkeys(a for _, a in quotes_with_anchors))
    return {
        "value": quotes if is_list else " ".join(quotes),
        "attribution": "author_reported",
        "evidence_anchors": anchors,
        "evidence_quotes": quotes,
    }


def build_paper_card_and_discovery_chain(
    sections: list[dict[str, Any]],
    case_data: dict[str, Any],
    doc_meta: dict[str, str],
    genre_info: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build three-layer attributed paper card and 4-stage PaperStory discovery chain."""
    title = case_data.get("title") or doc_meta.get("title")
    if not title and sections and sections[0]["title"] not in {"Preamble", "Abstract", "Introduction"}:
        title = sections[0]["title"]
    title_field = (
        {"value": str(title), "attribution": "author_reported", "evidence_anchors": ["case_or_document_header"], "evidence_quotes": [str(title)]}
        if title else {"value": "Not reported in available source text.", "attribution": "not_reported", "evidence_anchors": [], "evidence_quotes": []}
    )

    non_ref_text = "\n".join(
        p["text"] for s in sections if s["category"] != "references" for p in s["paragraphs"]
    )
    ids = extract_identifiers(non_ref_text) if non_ref_text else extract_identifiers(
        "\n".join(p["text"] for s in sections for p in s["paragraphs"])
    )
    doi_val = case_data.get("primary_doi") or case_data.get("doi") or doc_meta.get("doi")
    if not doi_val:
        doi_obj = next((i for i in ids if i.kind in {"DOI", "PMID", "PMC", "arXiv"}), None)
        if doi_obj:
            doi_val = doi_obj.value
    id_field = (
        {"value": str(doi_val), "attribution": "author_reported", "evidence_anchors": ["identifier_extraction"], "evidence_quotes": [str(doi_val)]}
        if doi_val else {"value": "Not reported in available source text.", "attribution": "not_reported", "evidence_anchors": [], "evidence_quotes": []}
    )

    q_cands: list[tuple[float, str, str]] = []
    ds_cands: list[tuple[float, str, str]] = []
    meth_cands: list[tuple[float, str, str]] = []
    ctrl_cands: list[tuple[float, str, str]] = []
    find_cands: list[tuple[float, str, str]] = []
    lim_cands: list[tuple[float, str, str]] = []
    funding_cands: list[tuple[float, str, str]] = []
    ethics_cands: list[tuple[float, str, str]] = []
    data_avail_cands: list[tuple[float, str, str]] = []
    code_avail_cands: list[tuple[float, str, str]] = []

    funding_re = re.compile(
        r"\b(?:did\s+not\s+receive\s+any\s+funding|no\s+funding|funded\s+by|funding\s+was\s+provided|"
        r"financial\s+support|supported\s+by\s+(?:grant|the\s+|NIH|NSF|NSFC|ERC|CNPq|FAPESP)|grant\s+(?:no\.?|number|#))\b|"
        r"基金资助|经费支持|无资助",
        re.I,
    )
    ethics_re = re.compile(
        r"\b(?:research\s+ethics\s+committee|ethics\s+committee|ethical\s+approval|institutional\s+review\s+board|"
        r"\bIRB\b|informed\s+consent|declaration\s+of\s+helsinki|animal\s+care\s+and\s+use|no\s+approval\s+by\s+the\s+.*ethics)\b|"
        r"伦理审批|伦理委员会|知情同意",
        re.I,
    )
    data_avail_re = re.compile(
        r"\b(?:data\s+availability|data\s+(?:are|is)\s+(?:publicly\s+)?available|database\s+that\s+originated|"
        r"available\s+(?:from|with)\s+the\s+corresponding\s+author|data\s+(?:were\s+)?deposited\s+(?:in|at)|"
        r"publicly\s+available\s+(?:data|dataset)|zenodo\.org|figshare|dryad|ncbi\s+geo|open[- ]access\s+dataset)\b|"
        r"数据可用性|数据公开",
        re.I,
    )
    code_avail_re = re.compile(
        r"\b(?:code\s+availability|source\s+code\s+is\s+available|code\s+(?:is|are)\s+(?:publicly\s+)?available|"
        r"software\s+availability|agent\s+availability|github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|"
        r"gitlab\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|open[- ]source\s+(?:code|repository|implementation)|"
        r"huggingface\.co/[A-Za-z0-9_.-]+)\b|代码可用性|开源代码",
        re.I,
    )

    for sec in sections:
        cat = sec["category"]
        sec_title_low = sec["title"].lower()
        if cat == "references":
            continue
        for p in sec["paragraphs"]:
            for sent in split_sentences(p["text"]):
                if len(sent) < 16 or not verify_exact_quote(sent, p["text"]):
                    continue
                anc = p["anchor"]

                # Governance & reproducibility fields (checked across body + backmatter + post_references_unclassified)
                if "funding" in sec_title_low or funding_re.search(sent):
                    fnd_sc = 4.0 if "funding" in sec_title_low else 2.5
                    funding_cands.append((fnd_sc, sent, anc))
                if "ethic" in sec_title_low or ethics_re.search(sent):
                    eth_sc = 4.0 if "ethic" in sec_title_low else 3.0
                    ethics_cands.append((eth_sc, sent, anc))
                if (
                    "data availability" in sec_title_low
                    or "data and code" in sec_title_low
                    or "code and data" in sec_title_low
                    or ("declaration" in sec_title_low and re.search(r"\b(?:data|database)\b", sent, re.I))
                    or data_avail_re.search(sent)
                ):
                    da_sc = 4.0 if ("data availability" in sec_title_low or "declaration" in sec_title_low) else 2.5
                    data_avail_cands.append((da_sc, sent, anc))
                if (
                    "code availability" in sec_title_low
                    or "agent availability" in sec_title_low
                    or "software availability" in sec_title_low
                    or "code and data" in sec_title_low
                    or "data and code" in sec_title_low
                    or code_avail_re.search(sent)
                ):
                    ca_sc = 4.0 if ("code availability" in sec_title_low or "agent availability" in sec_title_low) else 2.5
                    code_avail_cands.append((ca_sc, sent, anc))

                if cat == "backmatter" or len(sent) < 20:
                    continue
                # Research question / gap / hinge
                if cat in {"abstract", "introduction", "unsectioned", "other"}:
                    q_sc = (3.0 if HINGE_CUE_RE.search(sent) else 0.0) + (2.5 if GAP_CUE_RE.search(sent) else 0.0)
                    if q_sc > 0:
                        q_cands.append((q_sc + (0.5 if cat == "introduction" else 0.0), sent, anc))
                # Dataset / sample
                if DATASET_CUE_RE.search(sent) and cat in {"methods", "results", "abstract", "introduction", "other", "unsectioned"}:
                    ds_sc = 3.0 if cat == "methods" else 2.0
                    if re.search(r"\bn\s*=\s*\d+(?:,\d{3})*\b", sent, re.I):
                        ds_sc += 2.0
                    ds_cands.append((ds_sc, sent, anc))
                # Methods / conditions
                if cat == "methods" or (cat in {"abstract", "other", "results"} and METHOD_RATIONALE_RE.search(sent)):
                    m_sc = 3.0 if cat == "methods" else (2.0 if cat == "abstract" else 1.5)
                    if QUANT_RE.search(sent):
                        m_sc += 1.5
                    meth_cands.append((m_sc, sent, anc))
                # Controls / exclusions
                if CONTROL_CUE_RE.search(sent) and cat != "post_references_unclassified":
                    c_sc = 3.0 if cat in {"methods", "results"} else 1.8
                    ctrl_cands.append((c_sc, sent, anc))
                # Main findings
                if cat in {"results", "abstract", "conclusion"} or (cat == "other" and QUANT_RE.search(sent)):
                    f_sc = (3.0 if QUANT_RE.search(sent) else 0.0) + (2.0 if FIG_CALL_RE.search(sent) else 0.0)
                    if cat == "results":
                        f_sc += 2.0
                    if f_sc >= 2.0 and not LIMITATION_CUE_RE.search(sent):
                        find_cands.append((f_sc, sent, anc))
                # Limitations / prerequisites (also inside Discussion, Conclusion & post_references_unclassified)
                if cat == "limitations" or LIMITATION_CUE_RE.search(sent):
                    l_sc = 4.0 if cat == "limitations" else 2.5
                    if cat in {"discussion", "conclusion", "post_references_unclassified"}:
                        l_sc += 1.0
                    lim_cands.append((l_sc, sent, anc))

    def top_unique(cands: list[tuple[float, str, str]], k: int) -> list[tuple[str, str]]:
        cands.sort(key=lambda x: -x[0])
        picked: list[tuple[str, str]] = []
        for _, sent, anc in cands:
            if any(_token_jaccard(sent, prev) >= 0.72 for prev, _ in picked):
                continue
            picked.append((sent, anc))
            if len(picked) >= k:
                break
        return picked

    q_top = top_unique(q_cands, 2)
    ds_top = top_unique(ds_cands, 2)
    meth_top = top_unique(meth_cands, 2)
    ctrl_top = top_unique(ctrl_cands, 2)
    find_top = top_unique(find_cands, 5)
    lim_top = top_unique(lim_cands, 2)
    funding_top = top_unique(funding_cands, 2)
    ethics_top = top_unique(ethics_cands, 2)
    data_avail_top = top_unique(data_avail_cands, 2)
    code_avail_top = top_unique(code_avail_cands, 2)

    paper_card = {
        "title": title_field,
        "identifier": id_field,
        "paper_genre": genre_info,
        "research_question": _make_card_field(q_top),
        "dataset_or_sample": _make_card_field(ds_top),
        "method_conditions": _make_card_field(meth_top),
        "controls_and_exclusions": _make_card_field(ctrl_top),
        "main_findings": _make_card_field(find_top, is_list=True),
        "limitations_and_prerequisites": _make_card_field(lim_top),
        "funding": _make_card_field(funding_top, not_reported_msg="not_reported"),
        "ethics": _make_card_field(ethics_top, not_reported_msg="not_reported"),
        "data_availability": _make_card_field(data_avail_top, not_reported_msg="not_reported"),
        "code_availability": _make_card_field(code_avail_top, not_reported_msg="not_reported"),
    }

    discovery_chain = {
        "schema": "PaperStory_4stage_discovery_chain",
        "attribution_note": "Individual excerpts are author_reported with exact locators; stage grouping is model_synthesized.",
        "stages": [
            {"stage": "1_problem_and_gap", **paper_card["research_question"]},
            {"stage": "2_method_and_operation", **paper_card["method_conditions"]},
            {"stage": "3_key_results_and_figures", **paper_card["main_findings"]},
            {"stage": "4_limitations_and_prerequisites", **paper_card["limitations_and_prerequisites"]},
        ],
    }
    return paper_card, discovery_chain


def extract_knowledge_points(sections: list[dict[str, Any]], max_kps: int = 12) -> list[dict[str, Any]]:
    """Extract evidence-density-ranked, deduplicated, section-balanced verified knowledge points."""
    pool: list[dict[str, Any]] = []
    for s_idx, sec in enumerate(sections):
        cat = sec["category"]
        if cat in {"references", "backmatter"}:
            continue
        for p in sec["paragraphs"]:
            for sent in split_sentences(p["text"]):
                if len(sent) < 22 or not verify_exact_quote(sent, p["text"]):
                    continue
                ev_score = _sentence_evidence_score(sent, cat)
                if ev_score < 1.5:
                    continue
                pool.append({
                    "sec_order": s_idx,
                    "section": sec["title"],
                    "category": cat,
                    "quote": sent,
                    "anchor": p["anchor"],
                    "anchor_kind": p["anchor_kind"],
                    "evidence_density": ev_score,
                    "emlsi": extract_emlsi_fingerprint(sent),
                })

    # Sort by evidence density descending, then preserve balanced section coverage
    pool.sort(key=lambda x: (-x["evidence_density"], x["sec_order"]))
    per_sec_cap = max(2, (max_kps // max(1, min(len(sections), 5))) + 1)
    sec_counts: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []

    for cand in pool:
        if sec_counts[cand["section"]] >= per_sec_cap:
            continue
        if any(_token_jaccard(cand["quote"], prev["quote"]) >= 0.72 for prev in selected):
            continue
        selected.append(cand)
        sec_counts[cand["section"]] += 1
        if len(selected) >= max_kps:
            break

    # Restore original paper reading order for usability
    selected.sort(key=lambda x: x["sec_order"])
    kps: list[dict[str, Any]] = []
    for idx, item in enumerate(selected, start=1):
        kps.append({
            "kp_id": f"kp_{idx:03d}",
            "section": item["section"],
            "category": item["category"],
            "claim": item["quote"],
            "quote": item["quote"],
            "anchor": item["anchor"],
            "paragraph_id": item["anchor"],
            "anchor_kind": item["anchor_kind"],
            "attribution": "author_reported",
            "evidence_density": item["evidence_density"],
            "evidence_verified": True,
            "emlsi_fingerprint": item["emlsi"],
        })
    return kps


def render_markdown_notes(
    paper_card: dict[str, Any],
    section_cards: list[dict[str, Any]],
    discovery_chain: dict[str, Any],
    figure_cards: list[dict[str, Any]],
    glossary: list[dict[str, Any]],
    kps: list[dict[str, Any]],
    status: str,
    gaps: list[dict[str, Any]],
) -> str:
    """Render clean, structured, reusable Markdown literature notes."""
    title_val = paper_card["title"]["value"]
    id_val = paper_card["identifier"]["value"]
    genre = paper_card["paper_genre"]["genre"]

    lines = [
        f"# 结构化文献笔记：{title_val}",
        "",
        f"- **标识符**: `{id_val}`",
        f"- **论文体裁**: `{genre}` (`{paper_card['paper_genre']['attribution']}`)",
        f"- **运行状态**: `{status}`",
        f"- **结构统计**: 原序章节 `{len(section_cards)}`｜精炼知识点 `{len(kps)}`｜术语词条 `{len(glossary)}`｜图表方法卡 `{len(figure_cards)}`",
        "",
        "## 1. 结构化论文卡（三层归属：author_reported / model_synthesized / not_reported）",
        "",
    ]

    field_labels = [
        ("research_question", "研究问题与空白"),
        ("dataset_or_sample", "数据与样本"),
        ("method_conditions", "方法与实验条件"),
        ("controls_and_exclusions", "对照与排除边界"),
        ("limitations_and_prerequisites", "局限与成立前提"),
        ("funding", "资助声明 (funding)"),
        ("ethics", "伦理审批 (ethics)"),
        ("data_availability", "数据可用性 (data_availability)"),
        ("code_availability", "代码可用性 (code_availability)"),
    ]
    for fkey, flabel in field_labels:
        fobj = paper_card[fkey]
        anc_str = ", ".join(f"`{a}`" for a in fobj["evidence_anchors"]) if fobj["evidence_anchors"] else "无"
        lines.append(f"- **{flabel}** [`{fobj['attribution']}`]: {fobj['value']} （锚点: {anc_str}）")

    mf = paper_card["main_findings"]
    lines.append(f"- **主要发现** [`{mf['attribution']}`]:")
    if isinstance(mf["value"], list) and mf["value"]:
        for idx, (q, a) in enumerate(zip(mf["value"], mf["evidence_anchors"] + [""] * len(mf["value"])), start=1):
            anc_tail = f" (`{a}`)" if a else ""
            lines.append(f"  {idx}. {q}{anc_tail}")
    else:
        lines.append("  Not reported in available source text.")

    lines.extend(["", "## 2. 发现主线（PaperStory 四阶段论证链）", ""])
    for st in discovery_chain["stages"]:
        val = "; ".join(st["value"]) if isinstance(st["value"], list) else st["value"]
        lines.append(f"- **{st['stage']}** [`{st['attribution']}`]: {val or 'Not reported in available source text.'}")

    lines.extend(["", "## 3. 原序章节精华卡片与承接关系", ""])
    for sc in section_cards:
        lines.append(f"### {sc['order']}. {sc['title']} (`{sc['category']}` | 角色: `{sc['argumentative_role']}` | 承接: `{sc['transition_from_previous']}`)")
        lines.append(f"- 段落数: {sc['paragraph_count']}｜定位范围: `{sc['locator_range']['start']}` -> `{sc['locator_range']['end']}`")
        if sc["figure_table_mentions"]:
            lines.append(f"- 提及图表: {', '.join(sc['figure_table_mentions'])}")
        for ex in sc["key_excerpts"]:
            lines.append(f"  - [{ex['attribution']}] {ex['quote']} (`{ex['anchor']}`)")
        lines.append("")

    if figure_cards:
        lines.extend(["## 4. 一图一方法精读卡（方法理由、对照排除与图注解读标记）", ""])
        for fc in figure_cards:
            interp_tag = "⚠️ 图注含推断性解读" if fc["legend_contains_interpretation"] else "客观描述/引用"
            lines.append(f"### {fc['figure_or_table_id']}（{interp_tag}）")
            lines.append(f"- 出现章节: {', '.join(fc['sections'])}｜锚点: {', '.join(f'`{a}`' for a in fc['anchors'][:4])}")
            lines.append(f"- 方法与操作线索: {' '.join(fc['method_rationale'][:1]) or 'Not reported in figure-linked sentences.'}")
            lines.append(f"- 对照与排除线索: {' '.join(fc['controls_or_exclusions'][:1]) or 'Not reported in figure-linked sentences.'}")
            lines.append(f"- 证据边界线索: {' '.join(fc['evidence_boundaries'][:1]) or 'Not reported in figure-linked sentences.'}")
            lines.append("")

    if glossary:
        lines.extend([
            "## 5. 去重术语与缩写词表",
            "",
            "| 缩写 | 规范全称 | 出现频次 | 首次定义锚点 | 归属 |",
            "|---|---|---|---|---|",
        ])
        for g in glossary:
            lines.append(f"| `{g['abbreviation']}` | {g['canonical_expansion']} | {g['mention_count']} | `{g['first_anchor']}` | `{g['attribution']}` |")
        lines.append("")

    lines.extend([
        "## 6. 证据密度排序核验知识点（上限受控，防过碎）",
        "",
        "| ID | 章节 | 核验原文知识点 | 真实锚点 | 密度分 | 归属 |",
        "|---|---|---|---|---|---|",
    ])
    for kp in kps:
        q_clean = kp["quote"].replace("|", "\\|")
        lines.append(f"| {kp['kp_id']} | {kp['section']} | {q_clean} | `{kp['anchor']}` | {kp['evidence_density']} | `{kp['attribution']}` |")

    if gaps:
        lines.extend(["", "## 7. 诚实覆盖缺口与异常边界 (Coverage Gaps)", ""])
        for g in gaps:
            lines.append(f"- **{g['code']}**: {g['message']}（影响: {g.get('impact', 'See review details.')}）")

    lines.append("")
    return "\n".join(lines)


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path, case_id: str = "", max_kps: int = 12) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)

    case_path = input_dir / "case.json"
    case_data: dict[str, Any] = {}
    if case_path.is_file():
        try:
            case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            case_data = {}
    if not case_id:
        case_id = str(case_data.get("case_id") or "product-arena-06-default")

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="product-arena-06-6d8cf72b")
    budget_guard.tick_phase(budget_file, "ingest_sources")

    sources_scanned: list[str] = []
    source_modalities: dict[str, str] = {}
    sections: list[dict[str, Any]] = []
    extracted_floats: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    doc_meta: dict[str, str] = {}

    for src_path in sorted(input_dir.rglob("*")):
        if (
            not src_path.is_file()
            or src_path.is_symlink()
            or src_path.name.lower() in INSTRUCTION_FILE_NAMES
            or not src_path.resolve().is_relative_to(input_dir.resolve())
        ):
            continue
        ext = src_path.suffix.lower()
        if ext not in SUPPORTED_TEXT_EXTS:
            continue
        rel_name = src_path.relative_to(input_dir).as_posix()
        sources_scanned.append(rel_name)

        if ext == ".pdf":
            source_modalities[rel_name] = "pdf"
            pdftotext_bin = shutil.which("pdftotext")
            source_text = ""
            try:
                if pdftotext_bin:
                    # Use default reading-order extraction (NEVER -layout, which splices two-column PDFs horizontally)
                    proc = subprocess.run(
                        [pdftotext_bin, str(src_path), "-"],
                        capture_output=True, timeout=30, check=True,
                    )
                    source_text = proc.stdout.decode("utf-8", errors="replace").removesuffix("\f")
                else:
                    from pypdf import PdfReader
                    reader = PdfReader(str(src_path))
                    source_text = "\f".join(
                        (pg.extract_text() or "") if pg.get_contents() is not None else ""
                        for pg in reader.pages
                    )
                blank_pages = [p_no for p_no, p_txt in enumerate(source_text.split("\f"), 1) if not p_txt.strip()]
                if blank_pages:
                    gaps.append({
                        "code": "PDF_PAGES_NEED_OCR",
                        "severity": "warning",
                        "source": rel_name,
                        "message": f"{rel_name}: blank/image-only pages {blank_pages} lack extractable text.",
                        "impact": "Blank PDF pages require OCR or manual inspection; page numbers were preserved without renumbering.",
                    })
                if not source_text.strip():
                    gaps.append({
                        "code": "SOURCE_TEXT_EMPTY",
                        "severity": "error",
                        "source": rel_name,
                        "message": f"No extractable text layer found in PDF {rel_name}.",
                        "impact": "No claims can be extracted from this scanned/empty PDF.",
                    })
                    continue
                sections.extend(
                    parse_plain_or_md_sections(
                        source_text, rel_name, pdf=True, extracted_floats=extracted_floats, doc_meta=doc_meta
                    )
                )
            except Exception as exc:
                gaps.append({
                    "code": "PDF_TEXT_EXTRACTION_FAILED",
                    "severity": "error",
                    "source": rel_name,
                    "message": f"PDF text extraction unavailable for {rel_name} ({type(exc).__name__}).",
                    "impact": "Install pdftotext/pypdf or supply text/OCR layer; claims from this PDF remain unresolved.",
                })
        elif ext in {".xml", ".nxml", ".jats"}:
            source_modalities[rel_name] = "jats_xml"
            raw_xml = src_path.read_text(encoding="utf-8", errors="replace")
            try:
                xml_secs, xmeta = parse_jats_xml_sections(raw_xml, rel_name)
                doc_meta.update({k: v for k, v in xmeta.items() if v and k not in doc_meta})
                if xml_secs:
                    sections.extend(xml_secs)
                else:
                    gaps.append({
                        "code": "XML_NO_PARAGRAPHS",
                        "severity": "warning",
                        "source": rel_name,
                        "message": f"No JATS <sec>/<p> elements found in {rel_name}.",
                        "impact": "Document yielded no structured XML paragraphs.",
                    })
            except ET.ParseError as exc:
                gaps.append({
                    "code": "XML_PARSE_ERROR",
                    "severity": "error",
                    "source": rel_name,
                    "message": f"Malformed XML in {rel_name}: {exc}",
                    "impact": "Document could not be parsed as JATS XML.",
                })
        elif ext == ".json":
            source_modalities[rel_name] = "structured_json"
            raw_js = src_path.read_text(encoding="utf-8", errors="replace")
            try:
                js_secs, jmeta = parse_json_sections(raw_js, rel_name)
                doc_meta.update({k: v for k, v in jmeta.items() if v and k not in doc_meta})
                if js_secs:
                    sections.extend(js_secs)
            except Exception as exc:
                gaps.append({
                    "code": "JSON_PARSE_ERROR",
                    "severity": "warning",
                    "source": rel_name,
                    "message": f"Could not parse {rel_name} as structured paper JSON ({type(exc).__name__}).",
                    "impact": "Skipped non-paper or malformed JSON file.",
                })
        else:
            source_modalities[rel_name] = "plain_or_markdown_text"
            raw_txt = src_path.read_text(encoding="utf-8", errors="replace")
            if not raw_txt.strip():
                gaps.append({
                    "code": "SOURCE_TEXT_EMPTY",
                    "severity": "warning",
                    "source": rel_name,
                    "message": f"Empty text file: {rel_name}.",
                    "impact": "No claims anchored to empty source.",
                })
                continue
            sections.extend(
                parse_plain_or_md_sections(
                    raw_txt, rel_name, pdf=False, extracted_floats=extracted_floats, doc_meta=doc_meta
                )
            )

    # Also ingest inline paragraphs from case.json if no standalone paper files yielded sections
    if not sections and isinstance(case_data.get("paragraphs"), list):
        inline_secs, _ = parse_json_sections(json.dumps({"paragraphs": case_data["paragraphs"]}), "case.json")
        if inline_secs:
            sections.extend(inline_secs)
            sources_scanned.append("case.json")
            source_modalities["case.json"] = "structured_json"

    if not sources_scanned:
        gaps.append({
            "code": "SOURCE_MATERIAL_MISSING",
            "severity": "error",
            "source": "input_dir",
            "message": "No supported scientific source document (.pdf/.md/.txt/.tex/.xml/.json) was supplied.",
            "impact": "Paper card and structured notes cannot be populated from evidence.",
        })
    elif not sections:
        gaps.append({
            "code": "SOURCE_TEXT_UNAVAILABLE",
            "severity": "error",
            "source": "input_dir",
            "message": "Supplied sources yielded no readable paragraphs.",
            "impact": "Paper card remains marked not_reported.",
        })
    elif not any(s["category"] not in {"other", "unsectioned"} for s in sections):
        gaps.append({
            "code": "NO_RECOGNIZED_SECTION_HEADINGS",
            "severity": "warning",
            "source": "section_parser",
            "message": "No standard section headings detected; preserved flat block order with source-line anchors.",
            "impact": "Section alignment is limited to raw block order.",
        })

    budget_guard.tick_phase(budget_file, "synthesize_notes")
    full_text = "\n".join(p["text"] for s in sections for p in s["paragraphs"])
    genre_info = classify_paper_genre(sections, full_text)
    section_cards = build_section_cards(sections)
    figure_cards = extract_figure_method_cards(sections, extracted_floats=extracted_floats)
    glossary = extract_terminology_glossary(sections)
    paper_card, discovery_chain = build_paper_card_and_discovery_chain(sections, case_data, doc_meta, genre_info)
    knowledge_points = extract_knowledge_points(sections, max_kps=max_kps)

    core_card_keys = ("research_question", "dataset_or_sample", "method_conditions", "main_findings", "limitations_and_prerequisites")
    gov_card_keys = ("funding", "ethics", "data_availability", "code_availability")
    complete_count = sum(1 for k in core_card_keys if paper_card[k]["attribution"] != "not_reported")
    gov_complete_count = sum(1 for k in gov_card_keys if paper_card[k]["attribution"] != "not_reported")
    if complete_count < len(core_card_keys):
        missing_keys = [k for k in core_card_keys if paper_card[k]["attribution"] == "not_reported"]
        gaps.append({
            "code": "PAPER_CARD_FIELDS_INCOMPLETE",
            "severity": "warning",
            "source": "paper_card",
            "message": f"Only {complete_count}/{len(core_card_keys)} core paper-card fields supported by source text; missing: {missing_keys}.",
            "impact": "Unsupported fields remain attribution='not_reported' and were never fabricated.",
        })
    missing_gov = [k for k in gov_card_keys if paper_card[k]["attribution"] == "not_reported"]
    if missing_gov:
        gaps.append({
            "code": "PAPER_CARD_GOVERNANCE_FIELDS_MISSING",
            "severity": "warning",
            "source": "paper_card",
            "missing_fields": missing_gov,
            "message": f"Governance/reproducibility fields not reported in source text ({gov_complete_count}/{len(gov_card_keys)} present): {missing_gov}.",
            "impact": "Each missing governance field is recorded as 'not_reported' instead of silently returning gaps=0.",
        })
    post_ref_secs = [s for s in sections if s["category"] == "post_references_unclassified"]
    if post_ref_secs:
        n_post_paras = sum(len(s["paragraphs"]) for s in post_ref_secs)
        gaps.append({
            "code": "POST_REFERENCES_UNCLASSIFIED_TEXT",
            "severity": "warning",
            "source": "section_parser",
            "message": f"{n_post_paras} non-reference paragraph(s) appeared after REFERENCES without an explicit standalone section heading; quarantined as 'post_references_unclassified' rather than promoted to Discussion.",
            "impact": "Requires manual two-column layout verification before reassigning to Discussion/Conclusions.",
        })
    if not knowledge_points:
        gaps.append({
            "code": "NO_VERIFIED_KNOWLEDGE_POINTS",
            "severity": "warning",
            "source": "knowledge_points",
            "message": "No sentence met evidence-density and verbatim exact-quote checks.",
            "impact": "Knowledge-point table is empty; inspect source text directly.",
        })

    # Optional Zotero annotation JSON import & case-sensitive verification
    zotero_import_audit: dict[str, Any] | None = None
    zotero_in_candidates = sorted(
        p for p in input_dir.rglob("zotero_annotations*.json")
        if p.is_file() and not p.is_symlink()
    )
    if zotero_in_candidates:
        try:
            zotero_import_audit = zotero_adapter.import_and_verify_zotero_annotations(zotero_in_candidates[0], full_text)
            if zotero_import_audit["rejected_count"] > 0:
                gaps.append({
                    "code": "ZOTERO_ANNOTATIONS_REJECTED",
                    "severity": "warning",
                    "source": zotero_in_candidates[0].name,
                    "message": f"{zotero_import_audit['rejected_count']}/{zotero_import_audit['total_imported']} imported Zotero annotations failed case-sensitive verbatim verification.",
                    "impact": "Unverified or case/unit/negation-tampered highlights were excluded from trusted notes.",
                })
        except Exception as exc:
            gaps.append({
                "code": "ZOTERO_IMPORT_FAILED",
                "severity": "warning",
                "source": zotero_in_candidates[0].name,
                "message": f"Failed to parse Zotero annotation JSON ({type(exc).__name__}).",
                "impact": "Proceeded using source document text only.",
            })

    status = "partial_with_gaps" if gaps else "ok"

    # Emit standalone paper_card.json, structured_notes.json, zotero_annotations.json, structured_notes.md
    paper_card_payload = {
        "case_id": case_id,
        "schema_version": "2.2",
        "genre": genre_info["genre"],
        "attribution_legend": ["author_reported", "model_synthesized", "not_reported"],
        "source_modalities": source_modalities,
        "paper_card": paper_card,
        "discovery_chain": discovery_chain,
    }
    result_guard.atomic_write_json(output_dir / "paper_card.json", paper_card_payload)

    zotero_payload = zotero_adapter.export_zotero_annotations_payload(
        case_id=case_id,
        paper_card=paper_card,
        knowledge_points=knowledge_points,
        source_modalities=source_modalities,
    )
    if zotero_import_audit is not None:
        zotero_payload["imported_audit"] = zotero_import_audit
    result_guard.atomic_write_json(output_dir / "zotero_annotations.json", zotero_payload)

    notes_json_payload = {
        "case_id": case_id,
        "status": status,
        "source_modalities": source_modalities,
        "genre": genre_info["genre"],
        "paper_genre": genre_info,
        "paper_card": paper_card,
        "discovery_chain": discovery_chain,
        "section_cards": section_cards,
        "extracted_floats": extracted_floats[:30],
        "figure_method_cards": figure_cards,
        "terminology_glossary": glossary,
        "knowledge_points": knowledge_points,
        "zotero_export_summary": {
            "file": "zotero_annotations.json",
            "schema": zotero_payload["schema"],
            "annotation_count": zotero_payload["annotation_count"],
        },
        "gaps": gaps,
    }
    result_guard.atomic_write_json(output_dir / "structured_notes.json", notes_json_payload)

    md_text = render_markdown_notes(
        paper_card, section_cards, discovery_chain, figure_cards, glossary, knowledge_points, status, gaps
    )
    (output_dir / "structured_notes.md").write_text(md_text, encoding="utf-8")

    # Update result.json via M2 result_guard
    aligned_cats = {"abstract", "introduction", "related_work", "methods", "results", "discussion", "limitations", "conclusion"}
    patch = {
        "status": status,
        "sources": sources_scanned,
        "source_modalities": source_modalities,
        "uncertainty": (
            "All author_reported quotes passed case-sensitive verbatim substring and unit/numeric/negation fidelity checks in their cited block. "
            "Post-REFERENCES prose lacking explicit headings is quarantined as post_references_unclassified rather than promoted to Discussion. "
            "Missing funding/ethics/data_availability/code_availability fields are explicitly marked not_reported and recorded in gaps."
        ),
        "artifacts": ["result.json", "structured_notes.md", "structured_notes.json", "paper_card.json", "zotero_annotations.json"],
        "metrics": {
            "sections_aligned": sum(1 for s in sections if s["category"] in aligned_cats),
            "section_cards_count": len(section_cards),
            "extracted_floats_isolated": len(extracted_floats),
            "knowledge_points_extracted": len(knowledge_points),
            "terminology_terms_extracted": len(glossary),
            "figure_method_cards_count": len(figure_cards),
            "paper_card_fields_complete": complete_count,
            "paper_card_fields_total": len(core_card_keys),
            "governance_fields_complete": gov_complete_count,
            "governance_fields_total": len(gov_card_keys),
            "zotero_annotations_exported": zotero_payload["annotation_count"],
            "legend_interpretation_flags": sum(1 for fc in figure_cards if fc["legend_contains_interpretation"]),
        },
        "metric_provenance": {
            "sections_aligned": "structured_notes.json:section_cards",
            "section_cards_count": "structured_notes.json:section_cards",
            "extracted_floats_isolated": "structured_notes.json:extracted_floats",
            "knowledge_points_extracted": "structured_notes.json:knowledge_points",
            "terminology_terms_extracted": "structured_notes.json:terminology_glossary",
            "figure_method_cards_count": "structured_notes.json:figure_method_cards",
            "paper_card_fields_complete": "paper_card.json:paper_card",
            "paper_card_fields_total": "paper_card.json:paper_card",
            "governance_fields_complete": "paper_card.json:paper_card",
            "governance_fields_total": "paper_card.json:paper_card",
            "zotero_annotations_exported": "zotero_annotations.json:annotations",
            "legend_interpretation_flags": "structured_notes.json:figure_method_cards",
        },
        "findings": [
            f"Classified paper genre as '{genre_info['genre']}' across {len(section_cards)} original-order section card(s) ({len(extracted_floats)} floating table/figure block(s) isolated).",
            f"Verified {len(knowledge_points)} density-ranked knowledge point(s) (cap={max_kps}), {len(glossary)} deduplicated term(s), {len(figure_cards)} figure/table method card(s), and {zotero_payload['annotation_count']} zotero-mcp annotation(s).",
            f"Completed {complete_count}/{len(core_card_keys)} core + {gov_complete_count}/{len(gov_card_keys)} governance paper-card fields with three-layer attribution; gaps recorded: {len(gaps)}.",
        ],
        "structured_notes": {
            "genre": genre_info["genre"],
            "paper_genre": genre_info["genre"],
            "paper_card": paper_card,
            "discovery_chain": discovery_chain,
            "section_count": len(section_cards),
            "extracted_floats_count": len(extracted_floats),
            "knowledge_point_count": len(knowledge_points),
            "terminology_count": len(glossary),
            "figure_method_card_count": len(figure_cards),
            "zotero_annotations_exported": zotero_payload["annotation_count"],
        },
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    # Preserve partial_with_gaps if gaps present
    if status == "partial_with_gaps" and res.get("status") != "partial_with_gaps":
        res = result_guard.update_result(result_path, {"status": "partial_with_gaps"}, finalize=False)

    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        merged_gaps = gaps + grounding["gaps"]
        res = result_guard.update_result(result_path, {"gaps": merged_gaps, "status": "partial_with_gaps"}, finalize=False)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["structured_notes.md", "structured_notes.json", "paper_card.json", "zotero_annotations.json"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Structured Paper Notes Engine (product-arena-06-6d8cf72b).")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    parser.add_argument("--case-id", default="", help="Explicit case identifier override.")
    parser.add_argument("--max-kps", type=int, default=12, help="Maximum knowledge points to emit (default: 12).")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], case_id=args.case_id, max_kps=args.max_kps)
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
