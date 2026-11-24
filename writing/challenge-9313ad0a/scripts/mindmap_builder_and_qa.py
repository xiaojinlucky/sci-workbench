#!/usr/bin/env python3
"""Grounded scientific mind map distiller, builder, and QA engine.

Supports three input modes:
1. --from-input <dir_or_file>: Automatic zero-manual corpus distillation from raw /input
   materials (.pdf, .docx, .md, .txt, .json, .csv) into a citation-anchored storyline
   hierarchy with verbatim quotes, page/line locators, epistemic tags, and typed cross-edges.
2. --from-markdown <outline.md>: UTF-8 structured Markdown outline ingestion (zero stdin chunking).
3. --spec <mindmap_spec.json>: Canonical JSON spec validation & rendering.

Enforces exact-file + page-aware citation backlinks, cross-edge semantic co-occurrence support,
UTF-8 CJK integrity (0 U+FFFD), math/UNC/remote-URL guards, cross-engine CJK font verification,
collision-free dynamic layout (including cross-edge label avoidance and >80-node overview/sub-map
splitting), synchronized .json / layered .svg / FreeMind .mm (with arrowlinks) / Mermaid .mmd /
rendered .png exports, and automatic result.json contract-patch generation.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import zlib
from html import escape
from pathlib import Path
from typing import Any

NODE_COLORS = {
    "root": ("#1B365D", "#FFFFFF"),
    "concept": ("#EAF2F8", "#1B4F72"),
    "question": ("#FEF5E7", "#935116"),
    "method": ("#E8F8F5", "#0E6251"),
    "evidence": ("#EBF5FB", "#21618C"),
    "conclusion": ("#F4ECF7", "#5B2C6F"),
    "task": ("#EAFAF1", "#196F3D"),
    "risk": ("#FDEDEC", "#943126"),
}

EDGE_STYLES = {
    "hierarchy": ("#5D6D7E", ""),
    "supports": ("#1E8449", ""),
    "depends_on": ("#2874A6", "5,3"),
    "causes": ("#B03A2E", ""),
    "correlates_with": ("#D68910", "4,3"),
    "contrasts": ("#7D3C98", "3,3"),
    "contradicts": ("#C0392B", "4,2"),
    "cites": ("#1F618D", "2,2"),
    "mitigates": ("#117864", "6,2"),
}

VALID_CROSS_RELATIONS = {k for k in EDGE_STYLES if k != "hierarchy"}
VALID_EPISTEMIC = {"fact", "summary", "inference"}
VALID_NODE_TYPES = set(NODE_COLORS.keys())
TEXT_SOURCE_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".tex", ".bib", ".xml", ".yaml", ".yml"}
DOC_SOURCE_EXTENSIONS = TEXT_SOURCE_EXTENSIONS | {".pdf", ".docx"}

# Security & encoding patterns
EXTERNAL_OR_UNC_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:https?:|//|\\\\)[^)]+\)|<img\b[^>]*\bsrc\s*=\s*['\"](?:https?:|//|\\\\)[^'\"]+['\"][^>]*>|(?<![A-Za-z0-9:])//[^\s\"')>]+|\\\\[A-Za-z0-9_.-]+\\[^\s\"')>]+",
    re.I,
)
INLINE_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$([^$\n]+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)

UNICODE_MATH_MAP = (
    (r"\\alpha\b", "α"), (r"\\beta\b", "β"), (r"\\gamma\b", "γ"), (r"\\delta\b", "δ"),
    (r"\\epsilon\b|\\varepsilon\b", "ε"), (r"\\eta\b", "η"), (r"\\theta\b", "θ"),
    (r"\\Delta\b", "Δ"), (r"\\lambda\b", "λ"), (r"\\mu\b", "μ"), (r"\\rho\b", "ρ"),
    (r"\\sigma\b", "σ"), (r"\\tau\b", "τ"), (r"\\phi\b|\\varphi\b", "φ"),
    (r"\\omega\b", "ω"), (r"\\pi\b", "π"), (r"\\pm\b", "±"), (r"\\times\b", "×"),
    (r"\\cdot\b", "·"), (r"\\leq\b|\\le\b", "≤"), (r"\\geq\b|\\ge\b", "≥"),
    (r"\\neq\b|\\ne\b", "≠"), (r"\\approx\b", "≈"), (r"\\sim\b", "~"),
    (r"\\rightarrow\b|\\to\b", "→"), (r"\\leftarrow\b", "←"), (r"\\infty\b", "∞"),
    (r"\^2\b", "²"), (r"\^3\b", "³"), (r"_0\b", "₀"), (r"_1\b", "₁"), (r"_2\b", "₂"),
)
PREFERRED_CJK_FAMILIES = (
    "Noto Sans CJK SC", "Noto Serif CJK SC", "Noto Sans SC", "Source Han Sans SC",
    "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "Droid Sans Fallback",
    "AR PL UMing CN", "Microsoft YaHei", "SimHei", "DengXian", "PingFang SC",
)

STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "were", "was", "are", "been",
    "have", "has", "had", "into", "their", "they", "them", "than", "then", "also",
    "more", "most", "such", "these", "those", "which", "where", "when", "while",
    "about", "after", "before", "between", "under", "over", "through", "during",
    "using", "used", "study", "studies", "paper", "results", "data", "analysis",
    "figure", "table", "section", "page", "however", "therefore", "although", "because",
    "的方法", "的研究", "的结果", "以及", "通过", "进行", "可以", "一种", "本发明",
}

QUANT_SIGNAL_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:%|‰|mg|g|kg|ml|mL|L|mm|cm|m|nm|µm|um|°C|K|MPa|GPa|kPa|Pa|Hz|kHz|MHz|mV|V|mA|A|h|hr|min|s|days?|months?|years?)\b"
    r"|\bp\s*[<=>]\s*0?\.\d+|\b(?:OR|HR|RR|CI|IQR|SD|SEM|AUC|R2|R\^2|r)\s*[=:]\s*-?\d+(?:\.\d+)?"
    r"|\bn\s*=\s*\d+(?:,\d+)*|\d+(?:\.\d+)?%|\d+\s*(?:例|名|项|组|天|月|年|倍))",
    re.I,
)

SECTION_CLASS_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("question", "summary", re.compile(r"(?:objective|aim|purpose|background|introduction|motivation|research\s+question|目的|背景|引言|技术领域|背景技术|问题)", re.I)),
    ("method", "fact", re.compile(r"(?:method|methodology|materials?|protocol|design|data\s+source|data\s+collection|statistical|procedure|实验|方法|材料|设计|技术方案|具体实施方式|制备|权利要求)", re.I)),
    ("evidence", "fact", re.compile(r"(?:result|finding|data\s+synthesis|outcome|performance|table\s+\d|fig(?:ure)?\.?\s*\d|结果|发现|数据|实施例|性能|测试)", re.I)),
    ("conclusion", "summary", re.compile(r"(?:discussion|conclusion|implication|summary|contribution|讨论|结论|总结|有益效果|创新)", re.I)),
    ("risk", "inference", re.compile(r"(?:limitation|risk|bias|challenge|caveat|uncertainty|conflict|future\s+work|ethic|局限|不足|风险|偏差|挑战|展望|注意)", re.I)),
]

RELATION_CUES: list[tuple[str, re.Pattern[str]]] = [
    ("contradicts", re.compile(r"(?:contradict(?:s|ed|ing)?|conflict(?:s|ed|ing)?|inconsistent\s+with|contrary\s+to|oppos(?:e|es|ed|ing)|disagree(?:s|d)?|相矛盾|不一致|相反|冲突)", re.I)),
    ("contrasts", re.compile(r"(?:whereas|in\s+contrast|compared\s+(?:with|to)|higher\s+than|lower\s+than|unlike|相比|对照|高于|低于|差异)", re.I)),
    ("mitigates", re.compile(r"(?:mitigat(?:e|es|ed|ing)|reduc(?:e|es|ed|ing)\s+(?:risk|bias|error|poisoning|loss|mortality|incidence)|prevent(?:s|ed|ing)?|overcom(?:e|es|ing)|alleviat(?:e|es|ed|ing)|降低|缓解|抑制|避免|改善|解决)", re.I)),
    ("causes", re.compile(r"(?:caus(?:e|es|ed|ing)|lead(?:s|ing)?\s+to|led\s+to|result(?:s|ed|ing)?\s+in|induc(?:e|es|ed|ing)|trigger(?:s|ed|ing)?|driv(?:e|es|en)\s+by|导致|引起|诱发|造成|促使)", re.I)),
    ("correlates_with", re.compile(r"(?:associat(?:ed|ion)\s+with|correlat(?:e|es|ed|ing|ion)\s+with|link(?:ed|s)?\s+to|related\s+to|co-occur(?:s|red|rence)?|相关|关联|伴随)", re.I)),
    ("depends_on", re.compile(r"(?:depend(?:s|ed|ing)?\s+on|requir(?:e|es|ed|ing)|based\s+on|rel(?:y|ies|ied)\s+on|前提|依赖|基于|需要)", re.I)),
    ("supports", re.compile(r"(?:support(?:s|ed|ing)?|confirm(?:s|ed|ing)?|demonstrat(?:e|es|ed|ing)|show(?:s|ed|n)?\s+that|indicat(?:e|es|ed|ing)|证实|表明|支持|验证|显示)", re.I)),
]

NEGATION_NEAR_CUE_RE = re.compile(
    r"(?:\b(?:no|not|never|neither|nor|without|lack(?:s|ed)?|fail(?:s|ed)?\s+to|does\s+not|did\s+not|do\s+not|cannot|unrelated\s+to|independent\s+of|no\s+(?:significant\s+)?(?:causal|association|correlation|effect|link|evidence))\b"
    r"|并未|没有|未导致|未引起|未造成|无显著相关|无关|不导致|不能证明|并不引起)",
    re.I,
)
PASSIVE_CAUSE_RE = re.compile(
    r"(?:\b(?:is|are|was|were|be|been)\s+(?:caused|induced|triggered|driven|mitigated|supported)\s+by\b|\b(?:due\s+to|owing\s+to|attributed\s+to|resulting\s+from)\b|由.{1,30}(?:导致|引起|诱发|造成))",
    re.I,
)
CONDITION_SCOPE_RE = re.compile(
    r"(?:\b(?:in|among|for|under|at|during|within)\s+(?:children|infants|adults|patients|adolescents|samples|temperatures?|pH|pressures?|rural|urban|[<>≤≥]?\s*\d+\s*(?:years?|months?|days?|°C|K|%|mg|mL|MPa))[^,.;]{0,60}|在.{2,24}(?:条件下|人群中|阶段|温度下))",
    re.I,
)
SUBGROUP_SCOPE_RE = re.compile(
    r"(?:\b(?:aged?\s*(?:[<>≤≥]\s*\d+|\d+\s*[-–to]+\s*\d+)\s*(?:years?|months?)|(?:under|below|less\s+than)\s+\d+\s*(?:years?|months?)|[<>≤≥]\s*\d+\s*years?|0\s*[-–]\s*\d+\s*years?)|\d+\s*[-–~至]\s*\d+\s*岁|[<>≤≥]?\s*\d+\s*岁以下)",
    re.I,
)


def _contains_cjk(value: str) -> bool:
    return any(
        0x3400 <= ord(ch) <= 0x9FFF
        or 0xF900 <= ord(ch) <= 0xFAFF
        or 0x3040 <= ord(ch) <= 0x30FF
        or 0xAC00 <= ord(ch) <= 0xD7AF
        for ch in value
    )


def _normalise_excerpt(value: str) -> str:
    """Normalize whitespace, hyphenated line-wraps, and typographic punctuation for resilient matching."""
    s = value.replace("\u00ad", "")
    # Merge PDF hyphenated line breaks whether newline-separated or space-joined
    s = re.sub(r"(?<=[A-Za-z])-\s*[\r\n]+\s*(?=[A-Za-z])", "", s)
    s = re.sub(r"(?<=[A-Za-z])-\s+(?=[A-Za-z])", "", s)
    s = s.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    return " ".join(s.casefold().split())


def _extract_content_tokens(text: str) -> set[str]:
    """Extract domain-relevant ASCII words and CJK bigrams for semantic co-occurrence checks."""
    low = _normalise_excerpt(text)
    toks: set[str] = set()
    for w in re.findall(r"[a-z0-9][a-z0-9._-]{2,}", low):
        if w not in STOPWORDS and not w.isdigit():
            toks.add(w)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for run in cjk_runs:
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if bg not in STOPWORDS:
                toks.add(bg)
    return toks


def _resolve_anchor_file(raw_file: Any, corpus_dir: Path) -> Path | None:
    """Resolve an anchor strictly inside corpus_dir (no symlinks, no traversal)."""
    if not isinstance(raw_file, str) or not raw_file.strip():
        return None
    root = corpus_dir.resolve()
    raw = raw_file.strip().replace("\\", "/")
    if raw in {"/input", ".", ".."}:
        return None
    if raw.startswith("/input/"):
        raw = raw[len("/input/"):]
    p = Path(raw)
    if not p.is_absolute():
        p = root / p
    try:
        if p.is_symlink():
            return None
        resolved = p.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


_DOC_PAGES_CACHE: dict[Path, list[str] | None] = {}
_DOC_EXTRACTOR_CACHE: dict[Path, str] = {}


def _stdlib_pdf_text_fallback(path: Path) -> list[str] | None:
    """Best-effort pure-stdlib zlib PDF content-stream text extractor for air-gapped environments."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        stream_data = m.group(1)
        dec: bytes | None = None
        try:
            dec = zlib.decompress(stream_data)
        except zlib.error:
            dec = stream_data if b"Tj" in stream_data or b"TJ" in stream_data else None
        if not dec:
            continue
        text_parts: list[str] = []
        for lit in re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", dec):
            try:
                text_parts.append(lit.decode("latin-1", errors="ignore"))
            except Exception:
                pass
        for arr in re.findall(rb"\[(.*?)\]\s*TJ", dec, re.S):
            for lit in re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)", arr):
                try:
                    text_parts.append(lit.decode("latin-1", errors="ignore"))
                except Exception:
                    pass
        if text_parts:
            joined = " ".join(text_parts)
            joined = re.sub(r"\\([()\\])", r"\1", joined)
            joined = re.sub(r"\s+", " ", joined).strip()
            if len(joined) >= 20:
                chunks.append(joined)
    if sum(len(c) for c in chunks) < 40:
        return None
    return ["\n".join(chunks)]


def _extract_docx_pages_offline(path: Path) -> list[str] | None:
    """Pure-stdlib OOXML .docx paragraph and table text extractor."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return None
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        lines: list[str] = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "p":
                texts = [t.text for t in elem.iter() if t.tag.split("}")[-1] == "t" and t.text]
                line = "".join(texts).strip()
                if line:
                    lines.append(line)
        return ["\n".join(lines)] if lines else None
    except Exception:
        return None


def _extract_document_pages_offline(path: Path) -> list[str] | None:
    """Extract per-page text preserving original 1-based page indices (including blank pages)."""
    if path in _DOC_PAGES_CACHE:
        return _DOC_PAGES_CACHE[path]
    ext = path.suffix.lower()
    pages: list[str] | None = None
    extractor_mode = "unavailable"

    if ext in TEXT_SOURCE_EXTENSIONS:
        raw = path.read_text(encoding="utf-8", errors="replace")
        # Split by explicit page markers (<!-- Page N --> or ## [Page N] or form-feed), preserving blank pages
        if "\x0c" in raw:
            parts = raw.split("\x0c")
            if parts and parts[-1] == "":
                parts.pop()
            pages = parts
            extractor_mode = "text_formfeed_paged"
        elif re.search(r"(?m)^(?:<!--\s*Page\s+\d+\s*-->|##\s*\[Page\s+\d+\])", raw):
            page_map: dict[int, str] = {}
            matches = list(re.finditer(r"(?m)^(?:<!--\s*Page\s+(\d+)\s*-->|##\s*\[Page\s+(\d+)\])", raw))
            for idx, mm in enumerate(matches):
                pno = int(mm.group(1) or mm.group(2))
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
                page_map[pno] = raw[mm.start():end]
            if page_map:
                max_p = max(page_map.keys())
                pages = [page_map.get(i, "") for i in range(1, max_p + 1)]
                extractor_mode = "text_marker_paged"
            else:
                pages = [raw]
                extractor_mode = "text_lines"
        else:
            pages = [raw]
            extractor_mode = "text_lines"
    elif ext == ".docx":
        pages = _extract_docx_pages_offline(path)
        if pages is not None:
            extractor_mode = "docx_ooxml"
    elif ext == ".pdf":
        if shutil.which("pdftotext"):
            try:
                proc = subprocess.run(
                    ["pdftotext", "-layout", str(path), "-"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
                )
                if proc.returncode == 0 and len(proc.stdout.strip()) >= 40:
                    raw_parts = proc.stdout.split("\x0c")
                    # pdftotext appends a single trailing form-feed; preserve ALL internal blank pages!
                    if raw_parts and raw_parts[-1] == "":
                        raw_parts.pop()
                    pages = raw_parts
                    extractor_mode = "pdftotext_paged"
            except OSError:
                pages = None
        if pages is None and importlib.util.find_spec("pypdf") is not None:
            try:
                import pypdf  # type: ignore
                reader = pypdf.PdfReader(str(path))
                # Preserve exact 1-based page indices including blank pages
                ext_pages = [(pg.extract_text() or "") for pg in reader.pages]
                if sum(len(x.strip()) for x in ext_pages) >= 40:
                    pages = ext_pages
                    extractor_mode = "pypdf_paged"
            except Exception:
                pages = None
        if pages is None:
            pages = _stdlib_pdf_text_fallback(path)
            if pages is not None:
                extractor_mode = "stdlib_stream_unindexed"

    _DOC_PAGES_CACHE[path] = pages
    _DOC_EXTRACTOR_CACHE[path] = extractor_mode
    return pages


def _extract_pdf_text_offline(path: Path) -> str | None:
    pages = _extract_document_pages_offline(path)
    if not pages:
        return None
    return "\n\n".join(pages)


def _parse_page_number_from_locator(locator: Any) -> int | None:
    if not isinstance(locator, str):
        return None
    m = re.search(r"(?:\bp\.?\s*|\bpage\s*|第\s*)(\d+)", locator, re.I)
    return int(m.group(1)) if m else None


def _parse_line_number_from_locator(locator: Any) -> int | None:
    if not isinstance(locator, str):
        return None
    m = re.search(r"(?:\bL|\bline\s*|第\s*\d+\s*页\s*第?\s*|\b行\s*)(\d+)", locator, re.I)
    if m and not re.search(r"(?:\bp\.?\s*|\bpage\s*)" + re.escape(m.group(1)) + r"\b", locator, re.I):
        return int(m.group(1))
    m2 = re.search(r"(?:^|[:\s,])L(\d+)\b|\bline\s+(\d+)\b", locator, re.I)
    if m2:
        return int(m2.group(1) or m2.group(2))
    return None


def _quote_match_in_file(path: Path, quote: str, locator: Any = None) -> tuple[bool | None, bool | None, bool | None, str]:
    """Return (quote_found, page_locator_verified, line_locator_verified, extractor_mode).

    Strictly checks both declared page index (preserving blank PDF pages) and line window when present.
    Never allows unindexed zlib stream fallback to masquerade as page-verified.
    """
    pages = _extract_document_pages_offline(path)
    mode = _DOC_EXTRACTOR_CACHE.get(path, "unavailable")
    if pages is None:
        return None, None, None, mode
    norm_q = _normalise_excerpt(quote)
    if not norm_q:
        return False, False if _parse_page_number_from_locator(locator) else None, False if _parse_line_number_from_locator(locator) else None, mode

    full_norm = _normalise_excerpt("\n".join(pages))
    found = norm_q in full_norm
    pg_num = _parse_page_number_from_locator(locator)
    ln_num = _parse_line_number_from_locator(locator)

    if not found:
        return False, (False if pg_num is not None else None), (False if ln_num is not None else None), mode

    # Unindexed stream fallback cannot verify page numbers or line numbers
    if mode == "stdlib_stream_unindexed":
        return True, None, None, mode

    pg_ok: bool | None = None
    target_text = "\n".join(pages)
    if pg_num is not None:
        if 1 <= pg_num <= len(pages):
            target_text = pages[pg_num - 1]
            pg_ok = norm_q in _normalise_excerpt(target_text)
        else:
            return True, False, (False if ln_num is not None else None), mode
    elif len(pages) == 1:
        target_text = pages[0]

    ln_ok: bool | None = None
    if ln_num is not None:
        lines = target_text.splitlines()
        if 1 <= ln_num <= len(lines):
            # Allow multi-line wrapped paragraph starting at ln_num (window [ln_num-2 .. ln_num+8])
            lo = max(0, ln_num - 2)
            hi = min(len(lines), ln_num + 8)
            window_norm = _normalise_excerpt(" ".join(lines[lo:hi]))
            ln_ok = norm_q in window_norm
        else:
            ln_ok = False

    return True, pg_ok, ln_ok, mode


def _sanitize_text_and_audit_math(label: str) -> tuple[str, list[str], list[str], int]:
    """Sanitize remote/UNC image embeds, translate simple LaTeX math for SVG display, and count U+FFFD."""
    replacement_chars = label.count("\ufffd")
    blocked_refs = [m.group(0) for m in EXTERNAL_OR_UNC_RE.finditer(label)]
    cleaned = EXTERNAL_OR_UNC_RE.sub("[blocked-external-ref]", label)

    unrendered_math: list[str] = []

    def _replace_math(m: re.Match[str]) -> str:
        expr = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").strip()
        disp = expr
        for pat, uni in UNICODE_MATH_MAP:
            disp = re.sub(pat, uni, disp)
        if re.search(r"\\[a-zA-Z]{2,}", disp):
            unrendered_math.append(expr)
        return disp

    display_text = INLINE_MATH_RE.sub(_replace_math, cleaned)
    return display_text, blocked_refs, unrendered_math, replacement_chars


def _wrap_cjk_aware(text: str, max_units: float = 20.0) -> list[str]:
    """Wrap mixed CJK/ASCII text by visual character width without overflowing boxes."""
    lines: list[str] = []
    for raw_para in text.splitlines() or [text]:
        para = raw_para.strip()
        if not para:
            continue
        cur = ""
        cur_w = 0.0
        tokens = re.findall(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]|[A-Za-z0-9_./:+%-]+|\s+|.", para)
        for tok in tokens:
            tw = sum(1.0 if ord(c) > 127 else 0.56 for c in tok)
            if cur and cur_w + tw > max_units:
                lines.append(cur.rstrip())
                cur = tok.lstrip()
                cur_w = sum(1.0 if ord(c) > 127 else 0.56 for c in cur)
            else:
                cur += tok
                cur_w += tw
        if cur.strip():
            lines.append(cur.rstrip())
    return lines or [text]


def _format_source_badge(anc: dict[str, Any], epistemic: str) -> str:
    src_tag = Path(str(anc.get("file", ""))).name if anc.get("file") else ""
    loc = str(anc.get("locator", "")).strip()
    if len(loc) > 16:
        loc = loc[:15] + "…"
    badge = f"[{epistemic}]"
    if src_tag:
        short_file = src_tag if len(src_tag) <= 22 else (src_tag[:12] + "…" + src_tag[-8:])
        badge += f" @{short_file}"
        if loc:
            badge += f" {loc}"
    return badge


def _estimate_box(display_label: str, sub: str = "") -> tuple[int, int, list[str]]:
    wrapped = _wrap_cjk_aware(display_label, max_units=20.0)
    if sub:
        wrapped.append(sub)
    max_u = 0.0
    for ln in wrapped:
        u = sum(1.0 if ord(c) > 127 else 0.58 for c in ln)
        max_u = max(max_u, u)
    w = max(176, min(350, int(max_u * 13.0) + 32))
    h = max(48, len(wrapped) * 18 + 20)
    return w, h, wrapped


def discover_cjk_fonts() -> dict[str, Any]:
    """Discover real installed CJK fonts via fc-list and matplotlib.font_manager."""
    fc_families: list[str] = []
    if shutil.which("fc-list"):
        try:
            proc = subprocess.run(
                ["fc-list", ":lang=zh", "family"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if proc.returncode == 0:
                for ln in proc.stdout.splitlines():
                    for fam in ln.split(","):
                        f = fam.strip()
                        if f and f not in fc_families:
                            fc_families.append(f)
        except OSError:
            pass

    mpl_font_file: str | None = None
    mpl_font_family: str | None = None
    if importlib.util.find_spec("matplotlib") is not None:
        from matplotlib import font_manager  # type: ignore
        avail = {f.name.casefold(): (f.name, f.fname) for f in font_manager.fontManager.ttflist}
        for pref in (*PREFERRED_CJK_FAMILIES, *fc_families):
            hit = avail.get(pref.casefold())
            if hit:
                mpl_font_family, mpl_font_file = hit
                break

    chosen_svg_family: str | None = None
    for pref in PREFERRED_CJK_FAMILIES:
        if any(pref.casefold() == f.casefold() for f in fc_families):
            chosen_svg_family = pref
            break
    if not chosen_svg_family and fc_families:
        chosen_svg_family = fc_families[0]
    if not chosen_svg_family and mpl_font_family:
        chosen_svg_family = mpl_font_family

    return {
        "fc_list_available": bool(shutil.which("fc-list")),
        "fc_zh_families": fc_families[:12],
        "chosen_svg_cjk_family": chosen_svg_family,
        "matplotlib_cjk_family": mpl_font_family,
        "matplotlib_cjk_path": mpl_font_file,
        "any_cjk_font_installed": bool(chosen_svg_family or mpl_font_file),
    }


def _split_sentences_safe(text: str) -> list[str]:
    """Split English/Chinese scientific prose into sentences without breaking et al./Fig./decimals."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    parts = re.split(
        r"(?<!\bFig)(?<!\bTab)(?<!\bal)(?<!\bDr)(?<!\bvs)(?<!\be\.g)(?<!\bi\.e)(?<=[.!?。！？])\s+(?=[A-Z0-9\u4e00-\u9fff\(])|(?<=[。！？])",
        cleaned,
    )
    return [p.strip() for p in parts if len(p.strip()) >= 18]


def _classify_block_role(heading: str, sentence: str) -> tuple[str, str]:
    """Classify (node_type, epistemic_status) from section heading and sentence cues (limitation/risk takes priority even inside Discussion)."""
    if re.search(r"(?:limitation|underreport|weakness|drawback|caveat|bias|incomplete\s+record|secondary\s+data|risk\s+of|future\s+stud|局限|不足|风险|偏差|漏报|缺陷|待验证)", sentence, re.I):
        return "risk", "inference"
    combined_h = heading or ""
    for ntype, default_ep, pat in SECTION_CLASS_RULES:
        if pat.search(combined_h):
            ep = "fact" if QUANT_SIGNAL_RE.search(sentence) else default_ep
            if ntype == "risk":
                ep = "inference"
            return ntype, ep
    if re.search(r"(?:conclud|demonstrat|summary|overall|in\s+conclusion|综上|结论|有益效果)", sentence, re.I):
        return "conclusion", "fact" if QUANT_SIGNAL_RE.search(sentence) else "summary"
    if QUANT_SIGNAL_RE.search(sentence):
        return "evidence", "fact"
    if re.search(r"(?:method|protocol|sample|cohort|model|algorithm|dataset|assay|方法|步骤|工艺|流程|采集|样本)", sentence, re.I):
        return "method", "fact"
    if re.search(r"(?:objective|aim|hypothesis|problem|purpose|目的|目标|旨在|解决.*问题)", sentence, re.I):
        return "question", "summary"
    return "concept", "fact"


def _summarize_sentence_label(sentence: str, max_chars: int = 66) -> str:
    """Create a crisp, clause-complete node label (no dangling prepositions, no PDF hyphen breaks, no stray footnote nums)."""
    s = re.sub(r"(?<=[A-Za-z])-\s+(?=[A-Za-z])", "", sentence)
    s = re.sub(r"\s+", " ", s).strip()
    # Strip stray leading superscript citation numbers (e.g. "5,7 The states...") or patent tags ("[0022] ")
    s = re.sub(r"^(?:\[\d{3,4}\]\s*|\d+(?:[,–-]\d+)*\s+(?=[A-Z\u4e00-\u9fff]))", "", s).strip()
    s = re.sub(
        r"^(?:Objective|Methods?|Results?|Conclusions?|Background|Discussion|Abstract|Data\s+source|摘要|背景|方法|结果|结论|本发明的有益效果)\s*[:：]\s*",
        "", s, flags=re.I,
    )
    # Remove spaces inserted between Chinese characters by PDF extractors
    s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)
    if len(s) <= max_chars:
        return s.rstrip(" ,;:，；：")
    # Prefer a natural clause boundary within [24, max_chars]
    window = s[: max_chars + 1]
    for sep in ("。", "；", "，", ". ", "; ", ": ", ", "):
        pos = window.rfind(sep)
        if pos >= 24:
            return window[:pos].strip()
    cut = s[:max_chars]
    if not _contains_cjk(cut):
        sp = cut.rfind(" ")
        if sp > max_chars // 2:
            cut = cut[:sp]
        # Strip dangling trailing function words so labels never end mid-phrase ("... to", "... which")
        dangling = {"to", "of", "in", "for", "with", "by", "on", "at", "from", "and", "or", "the", "a", "an", "that", "which", "was", "were", "is", "are", "as", "into"}
        words = cut.split()
        while len(words) > 4 and words[-1].rstrip(",;:").lower() in dangling:
            words.pop()
        cut = " ".join(words)
    return cut.rstrip(" ,;:，；：-") + "…"


def _select_verbatim_quote(sentence: str, max_chars: int = 125) -> str:
    """Slice an exact contiguous substring from the source sentence for verbatim verification."""
    s = re.sub(r"(?<=[A-Za-z])-\s+(?=[A-Za-z])", "", sentence).strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    if not _contains_cjk(cut):
        sp = cut.rfind(" ")
        if sp > 45:
            cut = cut[:sp]
    cut = re.sub(r"\s+\S+-$", "", cut).strip()
    return cut.strip()


def distill_corpus_to_spec(
    input_path: Path,
    slug: str = "distilled-mindmap",
    max_nodes: int = 34,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Automatically distill raw /input scientific corpus into a citation-grounded mind map spec."""
    corpus_dir = input_path if input_path.is_dir() else input_path.parent
    meta_case: dict[str, Any] = {}
    task_hint = ""
    if input_path.is_dir():
        case_f = input_path / "case.json"
        if case_f.is_file():
            try:
                meta_case = json.loads(case_f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                meta_case = {}
        task_f = input_path / "task.md"
        if task_f.is_file():
            task_hint = task_f.read_text(encoding="utf-8", errors="replace")[:1500]
        raw_files = sorted(
            p for p in input_path.rglob("*")
            if p.is_file()
            and not p.is_symlink()
            and p.suffix.lower() in DOC_SOURCE_EXTENSIONS
            and p.name.lower() not in {"case.json", "task.md", "manifest.json", "public-manifest.json"}
        )
        if not raw_files:
            raw_files = sorted(
                p for p in input_path.rglob("*")
                if p.is_file() and not p.is_symlink() and p.suffix.lower() in DOC_SOURCE_EXTENSIONS
            )
    else:
        raw_files = [input_path]

    if not raw_files:
        return {"slug": slug, "nodes": [], "cross_edges": []}, {"files_scanned": 0, "candidate_sentences": 0}

    # Collect structured candidate blocks with exact file, page, line, and sentence char-offset locator
    candidates: list[dict[str, Any]] = []
    scanned_files: list[str] = []
    unprocessed_files: list[dict[str, str]] = []
    subgroup_scopes_seen: dict[str, list[str]] = {}
    doc_title = ""
    root_anchor: dict[str, str] = {}

    for fp in raw_files[:8]:
        rel = fp.relative_to(corpus_dir).as_posix()
        pages = _extract_document_pages_offline(fp)
        if not pages or sum(len(p.strip()) for p in pages) < 20:
            unprocessed_files.append({"file": rel, "reason": "no_extractable_text_layer_or_empty"})
            continue
        scanned_files.append(rel)
        current_heading = "Overview"
        for pg_idx, pg_text in enumerate(pages, 1):
            if not pg_text.strip():
                continue  # Preserve 1-based page index for blank pages while skipping empty content
            lines = pg_text.splitlines()
            para_buf: list[tuple[int, str]] = []

            def _flush_para(buf: list[tuple[int, str]], heading: str, page_no: int) -> None:
                nonlocal doc_title, root_anchor
                joined_parts: list[str] = []
                line_spans: list[tuple[int, int, int]] = []  # (char_start, char_end, line_no)
                cursor = 0
                for ln_no_i, txt_i in buf:
                    s_i = txt_i.strip()
                    if not s_i:
                        continue
                    if joined_parts:
                        cursor += 1  # single joining space
                    start_c = cursor
                    joined_parts.append(s_i)
                    cursor += len(s_i)
                    line_spans.append((start_c, cursor, ln_no_i))
                joined = " ".join(joined_parts)
                if len(joined) < 25:
                    return
                # Skip reference bibliography dumps
                if re.search(r"^(?:references|bibliography|参考文献)\b", heading, re.I):
                    return
                if re.match(r"^(?:\[\d+\]|\d+\.\s+[A-Z][a-z]+,\s+[A-Z]\.)", joined):
                    return
                search_pos = 0
                for sent_idx, sent in enumerate(_split_sentences_safe(joined), 1):
                    if len(sent) < 24:
                        continue
                    c_pos = joined.find(sent, search_pos)
                    if c_pos < 0:
                        c_pos = max(0, search_pos)
                    c_end = c_pos + len(sent)
                    search_pos = c_end
                    sent_ln = buf[0][0]
                    for sp_s, sp_e, lno in line_spans:
                        if sp_s <= c_pos < sp_e or (c_pos < sp_s and sent_ln == buf[0][0]):
                            sent_ln = lno
                            break
                    # Drop noisy URLs, cover headers, patent bibliographic boilerplate, or secondary-language duplicate abstracts
                    if re.search(
                        r"https?://\S{25,}|sharepoint\.com|©\s*20\d\d|\(19\)国家知识产权局|\(56\)对比文件|授权公告号|^\s*(?:Resumo|Resultados|Conclusões|Objetivo)\s*:",
                        sent,
                        re.I,
                    ):
                        continue
                    # Detect explicit patent title "(54)发明名称 ..."
                    pat_tm = re.search(r"\(54\)\s*发明名称\s*([^\s(（]{4,45})", joined)
                    if pat_tm and not doc_title:
                        doc_title = pat_tm.group(1).strip()
                    ntype, ep = _classify_block_role(heading, sent)
                    score = 1.0
                    if QUANT_SIGNAL_RE.search(sent):
                        score += 2.2
                    if re.search(r"(?:objective|aim|conclud|finding|significant|method|limitation|risk|bias|underreport|发明|结论|方法|结果|显著|风险|局限|有益效果)", sent, re.I):
                        score += 1.5
                    if 45 <= len(sent) <= 220:
                        score += 0.8
                    quote = _select_verbatim_quote(sent)
                    loc = f"p.{page_no}:L{sent_ln} S{sent_idx}[{c_pos}-{c_end}]" if len(pages) > 1 else f"L{sent_ln} S{sent_idx}[{c_pos}-{c_end}]"
                    for sgm in SUBGROUP_SCOPE_RE.finditer(sent):
                        key_sg = " ".join(sgm.group(0).lower().split())
                        subgroup_scopes_seen.setdefault(key_sg, []).append(f"{rel}@{loc}")
                    if not doc_title and len(quote) >= 24:
                        doc_title = _summarize_sentence_label(sent, max_chars=52)
                        root_anchor = {"file": rel, "quote": quote, "locator": loc}
                    elif doc_title and not root_anchor and len(quote) >= 24:
                        root_anchor = {"file": rel, "quote": quote, "locator": loc}
                    candidates.append({
                        "file": rel,
                        "page": page_no,
                        "line": sent_ln,
                        "locator": loc,
                        "heading": heading,
                        "sentence": sent,
                        "quote": quote,
                        "node_type": ntype,
                        "epistemic_status": ep,
                        "score": round(score, 2),
                        "tokens": _extract_content_tokens(sent),
                    })

            for ln_no, raw_ln in enumerate(lines, 1):
                st = raw_ln.strip()
                if not st:
                    if para_buf:
                        _flush_para(para_buf, current_heading, pg_idx)
                        para_buf = []
                    continue
                if st.startswith("<!--") or st.startswith("## [Page ") or st.startswith("- Source PDF SHA-256"):
                    continue
                # Heading detection (Markdown, IMRaD labels, numbered/Chinese section headers)
                hm = re.match(r"^(#{1,4})\s+(.+)$", st)
                imrad = re.match(
                    r"^(?:(?:[1-9]\d*(?:\.\d+)?\.?\s+)|(?:[一二三四五六七八九十]+[、.．]\s*))?"
                    r"(Abstract|Objective|Introduction|Background|Methods?|Materials?\s+and\s+Methods?|Data\s+source|Data\s+synthesis|Results?|Discussion|Conclusions?|Limitations?|References|摘要|技术领域|背景技术|发明内容|具体实施方式|权利要求书?|方法|结果|讨论|结论|局限性?)\s*[:：]?\s*(.*)$",
                    st,
                    re.I,
                )
                if hm:
                    if para_buf:
                        _flush_para(para_buf, current_heading, pg_idx)
                        para_buf = []
                    h_txt = hm.group(2).strip()
                    if not h_txt.startswith("Verbatim Scientific Source Extract"):
                        current_heading = h_txt[:60]
                        if len(hm.group(1)) == 1 and not doc_title:
                            doc_title = h_txt[:56]
                    continue
                if imrad and len(st) < 90 and not imrad.group(2):
                    if para_buf:
                        _flush_para(para_buf, current_heading, pg_idx)
                        para_buf = []
                    current_heading = imrad.group(1).strip()
                    continue
                if imrad and imrad.group(2):
                    if para_buf:
                        _flush_para(para_buf, current_heading, pg_idx)
                        para_buf = []
                    current_heading = imrad.group(1).strip()
                    para_buf = [(ln_no, st)]
                    continue
                para_buf.append((ln_no, st))
            if para_buf:
                _flush_para(para_buf, current_heading, pg_idx)

    if not candidates:
        return {"slug": slug, "nodes": [], "cross_edges": []}, {"files_scanned": len(scanned_files), "candidate_sentences": 0}

    # Build storyline root + 5 canonical scientific branches (only emit branches with real evidence)
    is_zh = _contains_cjk(" ".join(c["sentence"] for c in candidates[:12]))
    root_label = (
        str(meta_case.get("title") or meta_case.get("topic_title") or doc_title or raw_files[0].stem)[:58]
    )
    if not root_anchor:
        c0 = candidates[0]
        root_anchor = {"file": c0["file"], "quote": c0["quote"], "locator": c0["locator"]}

    branch_defs = [
        ("question", "Research Problem & Objectives" if not is_zh else "研究问题与核心目标", "question"),
        ("method", "Methods, Design & Protocol" if not is_zh else "方法设计与技术方案", "method"),
        ("evidence", "Key Quantitative Findings" if not is_zh else "关键证据与实测数据", "evidence"),
        ("conclusion", "Conclusions & Contributions" if not is_zh else "核心结论与有益效果", "conclusion"),
        ("risk", "Limitations, Risks & Open Gaps" if not is_zh else "局限边界与待验证风险", "risk"),
    ]

    # Bucket candidates by role, falling back 'concept' into the best-matching bucket or method/evidence
    buckets: dict[str, list[dict[str, Any]]] = {b[0]: [] for b in branch_defs}
    for cand in candidates:
        role = cand["node_type"]
        if role in buckets:
            buckets[role].append(cand)
        else:
            buckets["evidence" if QUANT_SIGNAL_RE.search(cand["sentence"]) else "method"].append(cand)

    nodes: list[dict[str, Any]] = [{
        "id": "N0",
        "label": root_label,
        "parent_id": None,
        "node_type": "root",
        "epistemic_status": "summary",
        "source_anchor": root_anchor,
    }]

    budget_leaves = max(10, max_nodes - 6)
    non_empty_roles = [r for r, _, _ in branch_defs if buckets[r]]
    per_branch_cap = max(3, min(7, budget_leaves // max(1, len(non_empty_roles))))

    leaf_records: list[dict[str, Any]] = []
    node_seq = 1
    seen_labels: set[str] = {_normalise_excerpt(root_label)}

    for role_key, branch_title, branch_ntype in branch_defs:
        items = buckets[role_key]
        if not items:
            continue
        # Sort by informativeness score while preserving page diversity
        items_sorted = sorted(items, key=lambda x: (-x["score"], x["page"], x["line"]))
        selected: list[dict[str, Any]] = []
        used_tok_sets: list[set[str]] = []
        for it in items_sorted:
            if len(selected) >= per_branch_cap:
                break
            lbl = _summarize_sentence_label(it["sentence"])
            nl = _normalise_excerpt(lbl)
            if nl in seen_labels:
                continue
            # Deduplicate near-identical sentences (Jaccard > 0.65)
            toks = it["tokens"]
            if toks and any(len(toks & prev) / max(1, len(toks | prev)) > 0.65 for prev in used_tok_sets):
                continue
            seen_labels.add(nl)
            used_tok_sets.append(toks)
            selected.append({**it, "label": lbl})
        if not selected:
            continue

        bid = f"B_{role_key}"
        first_sel = selected[0]
        nodes.append({
            "id": bid,
            "label": branch_title,
            "parent_id": "N0",
            "node_type": branch_ntype,
            "epistemic_status": "summary",
            "source_anchor": {
                "file": first_sel["file"],
                "quote": first_sel["quote"],
                "locator": first_sel["locator"],
            },
        })
        for sel in selected:
            nid = f"N{node_seq}"
            node_seq += 1
            nd = {
                "id": nid,
                "label": sel["label"],
                "parent_id": bid,
                "node_type": sel["node_type"] if sel["node_type"] in VALID_NODE_TYPES else branch_ntype,
                "epistemic_status": sel["epistemic_status"],
                "source_anchor": {
                    "file": sel["file"],
                    "quote": sel["quote"],
                    "locator": sel["locator"],
                },
            }
            nodes.append(nd)
            leaf_records.append({**sel, "id": nid, "branch": role_key})

    # Automatically mine grounded cross-edges ONLY when explicit non-negated relation cue is present.
    # Pure token co-occurrence pairs are NEVER promoted to verified edges; saved only in candidate_cooccurrence_pairs.
    cross_edges: list[dict[str, Any]] = []
    candidate_cooccurrence_pairs: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    node_deg: dict[str, int] = {}
    for i, a in enumerate(leaf_records):
        for j, b in enumerate(leaf_records):
            if i == j or a["branch"] == b["branch"]:
                continue
            if node_deg.get(a["id"], 0) >= 2 or node_deg.get(b["id"], 0) >= 2:
                continue
            if (a["id"], b["id"]) in seen_pairs or (b["id"], a["id"]) in seen_pairs:
                continue
            shared = sorted(a["tokens"] & b["tokens"])
            if len(shared) < 2:
                continue
            rel_found = None
            anchor_cand = None
            for rel_name, cue_pat in RELATION_CUES:
                if cue_pat.search(b["quote"]):
                    # Skip negated non-contradictory sentences during auto-mining
                    if rel_name != "contradicts" and NEGATION_NEAR_CUE_RE.search(b["quote"]):
                        continue
                    rel_found = rel_name
                    anchor_cand = b
                    break
            if rel_found and anchor_cand and len(cross_edges) < 6:
                seen_pairs.add((a["id"], b["id"]))
                node_deg[a["id"]] = node_deg.get(a["id"], 0) + 1
                node_deg[b["id"]] = node_deg.get(b["id"], 0) + 1
                scope_m = CONDITION_SCOPE_RE.search(anchor_cand["sentence"])
                cross_edges.append({
                    "source": a["id"],
                    "target": b["id"],
                    "relation": rel_found,
                    "direction": f"{a['id']} -> {b['id']}",
                    "condition_scope": scope_m.group(0).strip() if scope_m else None,
                    "source_anchor": {
                        "file": anchor_cand["file"],
                        "quote": anchor_cand["quote"],
                        "locator": anchor_cand["locator"],
                    },
                })
            elif len(shared) >= 3 and len(candidate_cooccurrence_pairs) < 8:
                seen_pairs.add((a["id"], b["id"]))
                candidate_cooccurrence_pairs.append({
                    "source": a["id"],
                    "target": b["id"],
                    "shared_tokens": shared[:5],
                    "status": "unverified_cooccurrence_candidate_for_human_review",
                })

    subgroup_scope_notes = [
        {"scope": k, "occurrences": len(v), "sample_locators": v[:4]}
        for k, v in sorted(subgroup_scopes_seen.items())
    ]

    spec = {
        "slug": slug,
        "case_id": meta_case.get("case_id", ""),
        "distilled_automatically": True,
        "source_files": scanned_files,
        "unprocessed_files": unprocessed_files,
        "subgroup_scope_notes": subgroup_scope_notes,
        "nodes": nodes,
        "cross_edges": cross_edges,
    }
    stats = {
        "files_scanned": len(scanned_files),
        "scanned_file_list": scanned_files,
        "unprocessed_files": unprocessed_files,
        "candidate_sentences": len(candidates),
        "selected_nodes": len(nodes),
        "auto_cross_edges": len(cross_edges),
        "candidate_cooccurrence_pairs": candidate_cooccurrence_pairs,
        "subgroup_scope_notes": subgroup_scope_notes,
        "distinct_subgroup_scopes_detected": len(subgroup_scope_notes),
        "task_hint_present": bool(task_hint),
    }
    return spec, stats


def parse_markdown_to_spec(md_path: Path, slug: str = "mindmap") -> dict[str, Any]:
    """Parse UTF-8 structured Markdown outline into canonical mindmap spec (zero stdin chunking)."""
    raw = md_path.read_text(encoding="utf-8", errors="replace")
    nodes: list[dict[str, Any]] = []
    cross_edges: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    counter = 0

    for line in raw.splitlines():
        if not line.strip():
            continue
        em = re.match(r"^\s*->\s*(\S+)\s*--([a-z_]+)-->\s*(\S+)(.*)$", line)
        if em:
            src_id, rel, tgt_id, rest = em.group(1), em.group(2), em.group(3), em.group(4)
            anc = _parse_inline_anchor(rest)
            cross_edges.append({"source": src_id, "target": tgt_id, "relation": rel, "source_anchor": anc})
            continue

        hm = re.match(r"^(#{1,6})\s+(.+)$", line)
        bm = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
        if hm:
            level = len(hm.group(1))
            body = hm.group(2).strip()
        elif bm:
            level = 10 + len(bm.group(1)) // 2
            body = bm.group(2).strip()
        else:
            continue

        counter += 1
        nid = f"N{counter}"
        idm = re.search(r"\[id:([A-Za-z0-9_.-]+)\]", body)
        if idm:
            nid = idm.group(1)
            body = body.replace(idm.group(0), "").strip()

        epistemic = "fact"
        epm = re.search(r"\[(fact|summary|inference)\]", body, re.I)
        if epm:
            epistemic = epm.group(1).lower()
            body = body.replace(epm.group(0), "").strip()

        ntype = "root" if counter == 1 else "concept"
        ntm = re.search(r"\[type:(root|concept|question|method|evidence|conclusion|task|risk)\]", body, re.I)
        if ntm:
            ntype = ntm.group(1).lower()
            body = body.replace(ntm.group(0), "").strip()

        anc = _parse_inline_anchor(body)
        body = re.sub(r"\[@[^\]]+\]", "", body).strip()

        while stack and stack[-1][0] >= level:
            stack.pop()
        parent_id = stack[-1][1] if stack else None
        stack.append((level, nid))

        nodes.append({
            "id": nid,
            "label": body,
            "parent_id": parent_id,
            "node_type": ntype,
            "epistemic_status": epistemic,
            "source_anchor": anc,
        })

    return {"slug": slug, "nodes": nodes, "cross_edges": cross_edges}


def _parse_inline_anchor(text: str) -> dict[str, str]:
    m = re.search(r"\[@([^:\]]+):\s*\"([^\"]+)\"(?:\s*\|\s*([^\]]+))?\]", text)
    if not m:
        return {}
    out = {"file": m.group(1).strip(), "quote": m.group(2).strip()}
    if m.group(3):
        out["locator"] = m.group(3).strip()
    return out


def _validate_tree_structure(nodes: list[dict[str, Any]], cross_edges: list[dict[str, Any]]) -> tuple[list[str], list[str], dict[str, Any]]:
    """Check unique IDs, root presence, cycles, orphans, duplicate labels, relation types, and tree depth."""
    errors: list[str] = []
    warnings: list[str] = []
    ids = [n.get("id") for n in nodes]
    if any(not isinstance(i, str) or not i.strip() for i in ids):
        errors.append("Every node must have a non-empty string 'id'.")
    if len(set(ids)) != len(ids):
        errors.append("Duplicate node IDs detected in mind map spec.")

    by_id = {n["id"]: n for n in nodes if isinstance(n.get("id"), str)}
    roots = [n["id"] for n in nodes if n.get("parent_id") is None]
    if len(roots) == 0:
        errors.append("Mind map has no root node (parent_id: null).")
    elif len(roots) > 1:
        warnings.append(f"Multiple root nodes detected ({roots}); verify single unifying root.")

    for n in nodes:
        pid = n.get("parent_id")
        if pid is not None and pid not in by_id:
            errors.append(f"Node {n['id']} references nonexistent parent_id {pid!r}.")
        ep = n.get("epistemic_status")
        if ep not in VALID_EPISTEMIC:
            errors.append(f"Node {n['id']} has invalid epistemic_status {ep!r}; expected one of {sorted(VALID_EPISTEMIC)}.")

    max_depth = 0
    for n in nodes:
        seen: set[str] = set()
        cur = n["id"]
        depth = 0
        while cur in by_id:
            if cur in seen:
                errors.append(f"Hierarchy cycle detected involving node {cur!r}.")
                break
            seen.add(cur)
            pid = by_id[cur].get("parent_id")
            if pid is None:
                break
            cur = pid
            depth += 1
        max_depth = max(max_depth, depth)

    label_map: dict[str, list[str]] = {}
    for n in nodes:
        norm_l = _normalise_excerpt(str(n.get("label", "")))
        label_map.setdefault(norm_l, []).append(n["id"])
    dup_labels = {k: v for k, v in label_map.items() if k and len(v) > 1}
    if dup_labels:
        warnings.append(f"Duplicate node labels across IDs: {list(dup_labels.values())}")

    if max_depth > 5:
        warnings.append(f"Tree depth {max_depth} exceeds recommended readability bound (<=5).")
    if len(nodes) > 80:
        warnings.append(f"Node count {len(nodes)} exceeds single-canvas bound (<=80); overview + sub-map views will be exported.")

    for idx, ed in enumerate(cross_edges, 1):
        s, t = ed.get("source"), ed.get("target")
        rel = ed.get("relation")
        if s not in by_id or t not in by_id:
            errors.append(f"Cross-edge #{idx} references unknown node(s): {s!r} -> {t!r}.")
        if s == t:
            errors.append(f"Cross-edge #{idx} is a self-loop on node {s!r}.")
        if rel not in VALID_CROSS_RELATIONS:
            errors.append(f"Cross-edge #{idx} has invalid relation {rel!r}; expected one of {sorted(VALID_CROSS_RELATIONS)}.")

    return errors, warnings, {"root_ids": roots, "max_depth": max_depth, "duplicate_labels": dup_labels}


def _check_directional_order(
    quote: str,
    rel: str,
    cue_span: tuple[int, int] | None,
    s_only_toks: set[str],
    t_only_toks: set[str],
) -> bool:
    """Return True if a directional relation appears REVERSED relative to source/target endpoints."""
    if rel not in {"causes", "mitigates", "depends_on"} or not cue_span or not s_only_toks or not t_only_toks:
        return False
    low = _normalise_excerpt(quote)
    # Find earliest character index in normalized quote for source-unique vs target-unique tokens
    def _first_pos(toks: set[str]) -> int | None:
        positions = [low.find(t) for t in toks if len(t) >= 3 and low.find(t) >= 0]
        return min(positions) if positions else None

    s_pos = _first_pos(s_only_toks)
    t_pos = _first_pos(t_only_toks)
    if s_pos is None or t_pos is None or s_pos == t_pos:
        return False
    cue_mid = (cue_span[0] + cue_span[1]) // 2
    is_passive = bool(PASSIVE_CAUSE_RE.search(quote))
    # For active "A causes B" / "A mitigates B": expect source before cue and target after cue.
    # For passive "B is caused by A" / "B due to A": expect target before cue and source after cue.
    # For "A depends_on B": expect source before cue and target after cue.
    if rel in {"causes", "mitigates"}:
        if not is_passive and t_pos < cue_mid < s_pos:
            return True
        if is_passive and s_pos < cue_mid < t_pos:
            return True
    elif rel == "depends_on":
        if t_pos < cue_mid < s_pos:
            return True
    return False


def _audit_cross_edge_semantic_support(
    nodes: list[dict[str, Any]],
    cross_edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Audit cross-edges strictly: co-occurrence alone is NEVER 'semantically_supported'.

    Returns (reports, hard_errors, unverified_cooccurrence_warnings).
    Blocks: unrelated quotes, correlation-as-causation mismatch, negated relations, reversed causal direction.
    Flags co-occurrence-only edges as unverified_cooccurrence_only / needs_review (semantically_supported=False).
    """
    by_id = {str(n.get("id", "")): n for n in nodes}
    reports: list[dict[str, Any]] = []
    unsupported_errors: list[str] = []
    cooccurrence_only: list[str] = []

    cue_map = {r: pat for r, pat in RELATION_CUES}

    for idx, ed in enumerate(cross_edges, 1):
        s_id = str(ed.get("source", ""))
        t_id = str(ed.get("target", ""))
        s_nd = by_id.get(s_id)
        t_nd = by_id.get(t_id)
        anc = ed.get("source_anchor") or {}
        quote = str(anc.get("quote", ""))
        rel = str(ed.get("relation", ""))
        if not s_nd or not t_nd or not quote.strip():
            continue
        s_label = str(s_nd.get("label", ""))
        t_label = str(t_nd.get("label", ""))
        s_toks = _extract_content_tokens(s_label + " " + str((s_nd.get("source_anchor") or {}).get("quote", "")))
        t_toks = _extract_content_tokens(t_label + " " + str((t_nd.get("source_anchor") or {}).get("quote", "")))
        q_toks = _extract_content_tokens(quote)
        hit_s = sorted(q_toks & s_toks)
        hit_t = sorted(q_toks & t_toks)

        rel_pat = cue_map.get(rel)
        cue_m = rel_pat.search(quote) if rel_pat else None
        cue_matched = cue_m is not None
        corr_pat = cue_map.get("correlates_with")
        corr_only_for_causes = (rel == "causes") and (not cue_matched) and bool(corr_pat and corr_pat.search(quote))

        negated = False
        if rel != "contradicts" and (cue_matched or corr_only_for_causes):
            negated = bool(NEGATION_NEAR_CUE_RE.search(quote))

        reversed_dir = False
        if cue_matched and not negated:
            s_lbl_toks = _extract_content_tokens(s_label)
            t_lbl_toks = _extract_content_tokens(t_label)
            s_only = s_lbl_toks - t_lbl_toks
            t_only = t_lbl_toks - s_lbl_toks
            reversed_dir = _check_directional_order(quote, rel, cue_m.span() if cue_m else None, s_only, t_only)

        scope_m = CONDITION_SCOPE_RE.search(quote)
        cond_scope = ed.get("condition_scope") or (scope_m.group(0).strip() if scope_m else None)

        if not hit_s and not hit_t and q_toks and (s_toks or t_toks):
            v_status = "unrelated_quote"
            sem_ok = False
            needs_rev = True
            unsupported_errors.append(
                f"edge#{idx} ({s_id} --{rel}--> {t_id}): quote shares 0 domain tokens with either endpoint (unrelated quote)."
            )
        elif corr_only_for_causes:
            v_status = "relation_type_mismatch"
            sem_ok = False
            needs_rev = True
            unsupported_errors.append(
                f"edge#{idx} ({s_id} --causes--> {t_id}): quote only expresses association/correlation, not causation (change relation to correlates_with)."
            )
        elif negated:
            v_status = "negated_relation"
            sem_ok = False
            needs_rev = True
            unsupported_errors.append(
                f"edge#{idx} ({s_id} --{rel}--> {t_id}): source quote contains negation contradicting claimed '{rel}' relation."
            )
        elif reversed_dir:
            v_status = "reversed_direction"
            sem_ok = False
            needs_rev = True
            unsupported_errors.append(
                f"edge#{idx} ({s_id} --{rel}--> {t_id}): directional evidence in quote runs opposite to claimed edge direction."
            )
        elif cue_matched and hit_s and hit_t:
            v_status = "semantically_supported"
            sem_ok = True
            needs_rev = False
        elif cue_matched and (hit_s or hit_t):
            v_status = "single_endpoint_cue_needs_review"
            sem_ok = False
            needs_rev = True
            cooccurrence_only.append(
                f"edge#{idx} ({s_id} --{rel}--> {t_id}): explicit relation cue matched but quote only overlaps one endpoint; needs human verification."
            )
        else:
            v_status = "unverified_cooccurrence_only"
            sem_ok = False
            needs_rev = True
            cooccurrence_only.append(
                f"edge#{idx} ({s_id} --{rel}--> {t_id}): endpoint token co-occurrence only (no explicit '{rel}' cue in quote); marked unverified_cooccurrence_only / needs_review."
            )

        rec = {
            "edge_index": idx,
            "source": s_id,
            "target": t_id,
            "source_claim": s_label,
            "target_claim": t_label,
            "relation": rel,
            "direction": f"{s_id} -> {t_id}",
            "evidence_file": anc.get("file"),
            "evidence_locator": anc.get("locator"),
            "evidence_quote": quote[:180],
            "condition_scope": cond_scope,
            "source_token_overlap": hit_s[:6],
            "target_token_overlap": hit_t[:6],
            "relation_cue_matched": cue_matched,
            "negated_cue_detected": negated,
            "reversed_direction_detected": reversed_dir,
            "verification_status": v_status,
            "needs_review": needs_rev,
            "semantically_supported": sem_ok,
        }
        reports.append(rec)
    return reports, unsupported_errors, cooccurrence_only


def _compute_dynamic_tree_layout(nodes: list[dict[str, Any]], display_labels: dict[str, str]) -> tuple[dict[str, dict[str, Any]], int, int]:
    """Dynamic per-depth column X layout avoiding collisions for CJK & long labels (with @file + locator badge)."""
    by_id = {n["id"]: dict(n) for n in nodes}
    children: dict[str | None, list[str]] = {}
    for n in nodes:
        children.setdefault(n.get("parent_id"), []).append(n["id"])

    roots = children.get(None, []) or [nodes[0]["id"]]
    for nid, nd in by_id.items():
        anc = nd.get("source_anchor") or {}
        sub = _format_source_badge(anc, str(nd.get("epistemic_status", "fact")))
        w, h, lines = _estimate_box(display_labels.get(nid, nd["label"]), sub)
        nd["w"], nd["h"], nd["lines"] = w, h, lines

    depth_max_w: dict[int, int] = {}
    visited_d: set[str] = set()

    def set_depth(nid: str, d: int) -> None:
        if nid in visited_d:
            return
        visited_d.add(nid)
        by_id[nid]["depth"] = d
        depth_max_w[d] = max(depth_max_w.get(d, 176), by_id[nid]["w"])
        for cid in children.get(nid, []):
            set_depth(cid, d + 1)

    for rid in roots:
        set_depth(rid, 0)
    for nid in by_id:
        if "depth" not in by_id[nid]:
            by_id[nid]["depth"] = 0
            depth_max_w[0] = max(depth_max_w.get(0, 176), by_id[nid]["w"])

    col_x: dict[int, int] = {}
    cur_x = 40
    for d in sorted(depth_max_w.keys()):
        col_x[d] = cur_x
        cur_x += depth_max_w[d] + 84

    memo_h: dict[str, int] = {}

    def subtree_h(nid: str, seen: frozenset[str] = frozenset()) -> int:
        if nid in memo_h:
            return memo_h[nid]
        if nid in seen:
            return by_id[nid]["h"] + 28
        ch = children.get(nid, [])
        if not ch:
            val = by_id[nid]["h"] + 28
        else:
            val = max(by_id[nid]["h"] + 28, sum(subtree_h(c, seen | {nid}) for c in ch))
        memo_h[nid] = val
        return val

    placed: set[str] = set()

    def assign(nid: str, y_top: int) -> None:
        if nid in placed:
            return
        placed.add(nid)
        nd = by_id[nid]
        sh = subtree_h(nid)
        nd["x"] = col_x[nd["depth"]]
        nd["y"] = y_top + (sh - nd["h"]) // 2
        cy = y_top
        for cid in children.get(nid, []):
            cs = subtree_h(cid)
            assign(cid, cy)
            cy += cs

    cur_y = 40
    for rid in roots:
        rs = subtree_h(rid)
        assign(rid, cur_y)
        cur_y += rs

    max_x = max(nd["x"] + nd["w"] for nd in by_id.values()) + 90
    max_y = max(nd["y"] + nd["h"] for nd in by_id.values()) + 90
    return by_id, max_x, max_y


def _rects_overlap(ax: int, ay: int, aw: int, ah: int, bx: int, by: int, bw: int, bh: int) -> bool:
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def _compute_cross_edge_placements(
    layout: dict[str, dict[str, Any]],
    cross_edges: list[dict[str, Any]],
    width: int,
    height: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Place cross-edge control points & relation labels avoiding collisions with node boxes."""
    node_boxes = [(nd["x"], nd["y"], nd["w"], nd["h"]) for nd in layout.values()]
    placed_label_boxes: list[tuple[int, int, int, int]] = []
    placements: list[dict[str, Any]] = []
    label_overlaps: list[str] = []

    for idx, ed in enumerate(cross_edges):
        s, t = layout.get(ed.get("source", "")), layout.get(ed.get("target", ""))
        if not s or not t:
            continue
        rel = str(ed.get("relation", "supports"))
        x1, y1 = s["x"] + s["w"] // 2, s["y"] + s["h"]
        x2, y2 = t["x"] + t["w"] // 2, t["y"]
        base_mx, base_my = (x1 + x2) // 2 + 28, (y1 + y2) // 2
        lw = max(48, len(rel) * 7 + 12)
        lh = 16

        # Search local neighborhood first, then inter-column gutters and clear right-margin lane
        candidates: list[tuple[int, int]] = [
            (base_mx + dx, base_my + dy)
            for dx, dy in (
                (0, 0), (36, 0), (-36, 0), (0, -24), (0, 24),
                (70, -20), (-70, 20), (110, 0), (-110, 0), (0, -52), (0, 52),
                (-160, 0), (160, 0), (-220, -24), (220, 24),
            )
        ]
        # Add safe right-margin lane & gutter positions
        right_lane_x = max(12, width - lw - 14)
        for step_y in range(0, 12):
            candidates.append((right_lane_x, max(24, min(height - 30, base_my + (step_y - 4) * 24))))
        candidates.append((right_lane_x, 32 + idx * 24))

        chosen_mx, chosen_my = base_mx, base_my
        found_clear = False
        for raw_cx, raw_cy in candidates:
            cx = max(12, min(width - lw - 12, raw_cx))
            cy = max(18, min(height - lh - 12, raw_cy))
            hit_node = any(_rects_overlap(cx, cy - lh, lw, lh, nx, ny, nw, nh) for nx, ny, nw, nh in node_boxes)
            hit_lbl = any(_rects_overlap(cx, cy - lh, lw, lh, lx, ly, lww, lhh) for lx, ly, lww, lhh in placed_label_boxes)
            if not hit_node and not hit_lbl:
                chosen_mx, chosen_my = cx, cy
                found_clear = True
                placed_label_boxes.append((cx, cy - lh, lw, lh))
                break
        if not found_clear:
            label_overlaps.append(f"cross-{idx}:{rel}")
        placements.append({
            "idx": idx,
            "source": ed.get("source"),
            "target": ed.get("target"),
            "relation": rel,
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "mx": chosen_mx, "my": chosen_my,
            "lw": lw, "lh": lh,
        })
    return placements, label_overlaps


def _check_overlap_and_clipping(
    layout: dict[str, dict[str, Any]],
    width: int,
    height: int,
) -> tuple[list[str], list[str]]:
    items = list(layout.values())
    overlaps: list[str] = []
    clipped: list[str] = []
    for a in items:
        if a["x"] < 0 or a["y"] < 0 or a["x"] + a["w"] > width or a["y"] + a["h"] > height:
            clipped.append(a["id"])
    for i in range(len(items)):
        a = items[i]
        for j in range(i + 1, len(items)):
            b = items[j]
            if _rects_overlap(a["x"], a["y"], a["w"], a["h"], b["x"], b["y"], b["w"], b["h"]):
                overlaps.append(f"{a['id']}<->{b['id']}")
    return overlaps, clipped


def export_freemind_mm(nodes: list[dict[str, Any]], cross_edges: list[dict[str, Any]], mm_path: Path) -> None:
    """Export FreeMind .mm XML preserving hierarchy, source notes, AND typed cross-edge <arrowlink> tags."""
    children: dict[str | None, list[dict[str, Any]]] = {}
    for n in nodes:
        children.setdefault(n.get("parent_id"), []).append(n)
    edges_by_src: dict[str, list[dict[str, Any]]] = {}
    for ed in cross_edges:
        edges_by_src.setdefault(str(ed.get("source", "")), []).append(ed)

    roots = children.get(None, []) or [nodes[0]]
    root_el = ET.Element("map", {"version": "1.0.1"})

    def add_branch(parent_xml: ET.Element, node: dict[str, Any], seen: set[str]) -> None:
        if node["id"] in seen:
            return
        seen.add(node["id"])
        el = ET.SubElement(parent_xml, "node", {"ID": str(node["id"]), "TEXT": str(node["label"])})
        anc = node.get("source_anchor") or {}
        out_edges = edges_by_src.get(str(node["id"]), [])
        for ed in out_edges:
            rel = str(ed.get("relation", "supports"))
            col, _ = EDGE_STYLES.get(rel, ("#2C3E50", ""))
            ET.SubElement(el, "arrowlink", {
                "DESTINATION": str(ed.get("target", "")),
                "MIDDLE_LABEL": rel,
                "COLOR": col,
                "ENDARROW": "Default",
                "STARTARROW": "None",
            })
        if anc or out_edges:
            rich = ET.SubElement(el, "richcontent", {"TYPE": "NOTE"})
            html = ET.SubElement(rich, "html")
            body = ET.SubElement(html, "body")
            if anc:
                p = ET.SubElement(body, "p")
                loc = f" ({anc.get('locator')})" if anc.get("locator") else ""
                p.text = f"[{node.get('epistemic_status', 'fact')}] {anc.get('file', '')}{loc}: {anc.get('quote', '')}"
            for ed in out_edges:
                eanc = ed.get("source_anchor") or {}
                p2 = ET.SubElement(body, "p")
                p2.text = f"Cross-edge --{ed.get('relation')}--> {ed.get('target')}: {eanc.get('file', '')} ({eanc.get('locator', '')}) \"{eanc.get('quote', '')}\""
        for ch in children.get(node["id"], []):
            add_branch(el, ch, seen)

    seen_ids: set[str] = set()
    for r in roots:
        add_branch(root_el, r, seen_ids)
    mm_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root_el).write(str(mm_path), encoding="utf-8", xml_declaration=True)


def export_mermaid_mmd(nodes: list[dict[str, Any]], cross_edges: list[dict[str, Any]], mmd_path: Path) -> None:
    lines = ["flowchart LR"]
    for n in nodes:
        nid = re.sub(r"[^A-Za-z0-9_]", "_", str(n["id"]))
        lbl = str(n["label"]).replace('"', "'").replace("\n", " ")
        ep = n.get("epistemic_status", "fact")
        anc = n.get("source_anchor") or {}
        loc = f" {anc.get('locator')}" if anc.get("locator") else ""
        src = Path(str(anc.get("file", ""))).name if anc.get("file") else ""
        tag = f" @{src}{loc}" if src else ""
        lines.append(f'  {nid}["{lbl} [{ep}]{tag}"]')
    for n in nodes:
        pid = n.get("parent_id")
        if pid:
            p_clean = re.sub(r"[^A-Za-z0-9_]", "_", str(pid))
            c_clean = re.sub(r"[^A-Za-z0-9_]", "_", str(n["id"]))
            lines.append(f"  {p_clean} --> {c_clean}")
    for ed in cross_edges:
        s_clean = re.sub(r"[^A-Za-z0-9_]", "_", str(ed.get("source", "")))
        t_clean = re.sub(r"[^A-Za-z0-9_]", "_", str(ed.get("target", "")))
        rel = str(ed.get("relation", "supports")).replace('"', "'")
        lines.append(f'  {s_clean} -. "{rel}" .-> {t_clean}')
    mmd_path.parent.mkdir(parents=True, exist_ok=True)
    mmd_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_layered_svg(
    layout: dict[str, dict[str, Any]],
    cross_placements: list[dict[str, Any]],
    width: int,
    height: int,
    svg_path: Path,
    cjk_family: str | None = None,
) -> None:
    font_chain = (
        f"{cjk_family}, Noto Sans CJK SC, Noto Sans SC, WenQuanYi Zen Hei, DejaVu Sans, sans-serif"
        if cjk_family else
        "Noto Sans CJK SC, Noto Sans SC, WenQuanYi Zen Hei, DejaVu Sans, sans-serif"
    )
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '  <defs>',
        '    <marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">',
        '      <polygon points="0 0, 8 3, 0 6" fill="#34495E"/>',
        '    </marker>',
        '  </defs>',
        f'  <rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>',
        '  <g id="layer-edges">',
    ]
    for nid, nd in layout.items():
        pid = nd.get("parent_id")
        if pid and pid in layout:
            p = layout[pid]
            x1, y1 = p["x"] + p["w"], p["y"] + p["h"] // 2
            x2, y2 = nd["x"], nd["y"] + nd["h"] // 2
            cx = (x1 + x2) // 2
            lines.append(f'    <path id="tree-{escape(pid)}-{escape(nid)}" d="M {x1} {y1} C {cx} {y1}, {cx} {y2}, {x2} {y2}" fill="none" stroke="#7F8C8D" stroke-width="1.6"/>')
    lines.append('  </g>')

    lines.append('  <g id="layer-cross-edges">')
    for cp in cross_placements:
        rel = cp["relation"]
        col, dash = EDGE_STYLES.get(rel, ("#2C3E50", ""))
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        lines.append(f'    <path id="cross-{cp["idx"]}" d="M {cp["x1"]} {cp["y1"]} Q {cp["mx"]} {cp["my"]}, {cp["x2"]} {cp["y2"]}" fill="none" stroke="{col}" stroke-width="1.6"{dash_attr} marker-end="url(#arrow)"/>')
        lines.append(f'    <rect x="{cp["mx"] - 3}" y="{cp["my"] - 14}" width="{cp["lw"]}" height="15" rx="3" fill="#FFFFFF" fill-opacity="0.92" stroke="{col}" stroke-width="0.6"/>')
        lines.append(f'    <text x="{cp["mx"] + 2}" y="{cp["my"] - 3}" font-family="{escape(font_chain)}" font-size="10" fill="{col}">{escape(rel)}</text>')
    lines.append('  </g>')

    lines.append('  <g id="layer-nodes">')
    for nid, nd in layout.items():
        ntype = "root" if nd.get("depth", 0) == 0 else str(nd.get("node_type", "concept"))
        fill, stroke = NODE_COLORS.get(ntype, ("#F8F9F9", "#34495E"))
        tcol = "#FFFFFF" if ntype == "root" else "#17202A"
        lines.append(f'    <g id="node-{escape(nid)}">')
        lines.append(f'      <rect x="{nd["x"]}" y="{nd["y"]}" width="{nd["w"]}" height="{nd["h"]}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>')
        ty = nd["y"] + 20
        for idx_ln, ln in enumerate(nd["lines"]):
            is_badge = idx_ln == len(nd["lines"]) - 1 and ln.startswith("[")
            fsize = "10" if is_badge else "12"
            lcol = ("#D6EAF8" if ntype == "root" else "#566573") if is_badge else tcol
            lines.append(f'      <text x="{nd["x"] + 12}" y="{ty}" font-family="{escape(font_chain)}" font-size="{fsize}" fill="{lcol}">{escape(ln)}</text>')
            ty += 17
        lines.append('    </g>')
    lines.append('  </g>')

    lines.append('  <g id="layer-legend">')
    lines.append(f'    <text x="40" y="{height - 20}" font-family="{escape(font_chain)}" font-size="11" fill="#5D6D7E">Epistemic tags: [fact] verbatim grounded | [summary] synthesized | [inference] derived | @file locator backlink</text>')
    lines.append('  </g>')
    lines.append('</svg>')
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_png_preview(
    layout: dict[str, dict[str, Any]],
    cross_placements: list[dict[str, Any]],
    width: int,
    height: int,
    svg_path: Path,
    png_path: Path,
    font_info: dict[str, Any],
) -> dict[str, Any]:
    """Render PNG via rsvg-convert or matplotlib (with cross-edge labels & @file locator badges), verifying CJK fonts."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    contains_cjk = any(_contains_cjk(" ".join(nd["lines"])) for nd in layout.values())

    if contains_cjk and not font_info["any_cjk_font_installed"]:
        return {
            "rendered": False,
            "rendered_readable_cjk": False,
            "engine": "none",
            "png_path": None,
            "contains_cjk": True,
            "font_info": font_info,
            "error": "CJK content detected, but no CJK-capable font was found via fc-list or matplotlib; refusing to emit unreadable tofu PNG.",
        }

    rsvg = shutil.which("rsvg-convert")
    if rsvg and (not contains_cjk or font_info["chosen_svg_cjk_family"]):
        proc = subprocess.run([rsvg, "-o", str(png_path), str(svg_path)], capture_output=True, text=True, check=False)
        if proc.returncode == 0 and png_path.is_file() and png_path.stat().st_size > 0:
            return {
                "rendered": True,
                "rendered_readable_cjk": True if contains_cjk else None,
                "engine": "rsvg-convert",
                "png_path": png_path.name,
                "png_bytes": png_path.stat().st_size,
                "contains_cjk": contains_cjk,
                "cjk_font_used": font_info["chosen_svg_cjk_family"] if contains_cjk else None,
            }

    if importlib.util.find_spec("matplotlib") is not None:
        import matplotlib  # type: ignore
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
        import matplotlib.patches as patches  # type: ignore
        from matplotlib import font_manager  # type: ignore

        cjk_fp = font_manager.FontProperties(fname=font_info["matplotlib_cjk_path"]) if font_info["matplotlib_cjk_path"] else None
        if contains_cjk and cjk_fp is None:
            return {
                "rendered": False,
                "rendered_readable_cjk": False,
                "engine": "matplotlib_fallback",
                "png_path": None,
                "contains_cjk": True,
                "error": "Matplotlib fallback cannot locate a CJK font file.",
            }
        fig, ax = plt.subplots(figsize=(max(9, width / 110.0), max(5.5, height / 110.0)), dpi=140)
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.axis("off")
        for nd in layout.values():
            pid = nd.get("parent_id")
            if pid and pid in layout:
                p = layout[pid]
                ax.plot([p["x"] + p["w"], nd["x"]], [p["y"] + p["h"] / 2, nd["y"] + nd["h"] / 2], color="#7F8C8D", lw=1.4, zorder=1)
        for cp in cross_placements:
            col, _ = EDGE_STYLES.get(cp["relation"], ("#2C3E50", ""))
            ax.annotate(
                "", xy=(cp["x2"], cp["y2"]), xytext=(cp["x1"], cp["y1"]),
                arrowprops=dict(arrowstyle="->", color=col, lw=1.4, connectionstyle="arc3,rad=0.14"),
                zorder=2,
            )
            ax.text(
                cp["mx"], cp["my"] - 4, cp["relation"],
                fontsize=7.5, color=col, zorder=5,
                bbox=dict(boxstyle="round,pad=0.18", facecolor="#FFFFFF", edgecolor=col, lw=0.6, alpha=0.92),
                fontproperties=cjk_fp,
            )
        for nd in layout.values():
            ntype = "root" if nd.get("depth", 0) == 0 else str(nd.get("node_type", "concept"))
            fill, stroke = NODE_COLORS.get(ntype, ("#F8F9F9", "#34495E"))
            tcol = "#FFFFFF" if ntype == "root" else "#17202A"
            box = patches.FancyBboxPatch((nd["x"], nd["y"]), nd["w"], nd["h"], boxstyle="round,pad=0.02,rounding_size=8", facecolor=fill, edgecolor=stroke, lw=1.5, zorder=3)
            ax.add_patch(box)
            ax.text(nd["x"] + 10, nd["y"] + nd["h"] / 2, "\n".join(nd["lines"]), va="center", ha="left", fontsize=7.8, color=tcol, zorder=4, fontproperties=cjk_fp)
        fig.savefig(str(png_path), bbox_inches="tight", pad_inches=0.2, dpi=140)
        plt.close(fig)
        return {
            "rendered": True,
            "rendered_readable_cjk": True if contains_cjk else None,
            "engine": "matplotlib_fallback",
            "png_path": png_path.name,
            "png_bytes": png_path.stat().st_size,
            "contains_cjk": contains_cjk,
            "cjk_font_used": font_info["matplotlib_cjk_family"] if contains_cjk else None,
        }

    return {
        "rendered": False,
        "rendered_readable_cjk": False if contains_cjk else None,
        "engine": "none",
        "png_path": None,
        "contains_cjk": contains_cjk,
        "error": "Neither rsvg-convert nor matplotlib is available in this runtime.",
    }


def _export_large_scale_submaps(
    nodes: list[dict[str, Any]],
    cross_edges: list[dict[str, Any]],
    display_labels: dict[str, str],
    out_dir: Path,
    stem: str,
    cjk_family: str | None,
) -> list[str]:
    """When node count > 80, export overview (depth <= 2) + per-branch sub-map SVGs."""
    if len(nodes) <= 80:
        return []
    by_id = {n["id"]: n for n in nodes}
    children: dict[str | None, list[dict[str, Any]]] = {}
    for n in nodes:
        children.setdefault(n.get("parent_id"), []).append(n)
    roots = children.get(None, []) or [nodes[0]]
    exported: list[str] = []

    # 1. Overview (depth <= 2)
    overview_nodes: list[dict[str, Any]] = []
    def collect_ov(nd: dict[str, Any], d: int) -> None:
        overview_nodes.append(nd)
        if d < 2:
            for ch in children.get(nd["id"], []):
                collect_ov(ch, d + 1)
    for r in roots:
        collect_ov(r, 0)
    ov_ids = {n["id"] for n in overview_nodes}
    ov_edges = [e for e in cross_edges if e.get("source") in ov_ids and e.get("target") in ov_ids]
    ov_layout, ow, oh = _compute_dynamic_tree_layout(overview_nodes, display_labels)
    ov_cp, _ = _compute_cross_edge_placements(ov_layout, ov_edges, ow, oh)
    ov_path = out_dir / f"{stem}_overview.svg"
    export_layered_svg(ov_layout, ov_cp, ow, oh, ov_path, cjk_family=cjk_family)
    exported.append(ov_path.name)

    # 2. Per depth-1 branch sub-maps
    for r in roots:
        for br in children.get(r["id"], []):
            sub_nodes: list[dict[str, Any]] = [{**br, "parent_id": None}]
            def collect_sub(nid: str) -> None:
                for ch in children.get(nid, []):
                    sub_nodes.append(ch)
                    collect_sub(ch["id"])
            collect_sub(br["id"])
            sub_ids = {n["id"] for n in sub_nodes}
            sub_edges = [e for e in cross_edges if e.get("source") in sub_ids and e.get("target") in sub_ids]
            sl, sw, sh = _compute_dynamic_tree_layout(sub_nodes, display_labels)
            scp, _ = _compute_cross_edge_placements(sl, sub_edges, sw, sh)
            safe_bid = re.sub(r"[^A-Za-z0-9_-]", "_", str(br["id"]))
            sp = out_dir / f"{stem}_sub_{safe_bid}.svg"
            export_layered_svg(sl, scp, sw, sh, sp, cjk_family=cjk_family)
            exported.append(sp.name)
    return exported


def build_and_validate_mindmap(
    spec: dict[str, Any],
    corpus_dir: Path,
    out_dir: Path,
    allow_partial_png: bool = False,
    allow_node_degrade: bool = False,
    distill_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nodes = spec.get("nodes", [])
    cross_edges = spec.get("cross_edges", [])
    if not nodes:
        return {"passed": False, "status": "failed", "errors": ["Mind map spec has empty 'nodes' list."]}

    struct_errors, struct_warnings, struct_stats = _validate_tree_structure(nodes, cross_edges)
    cross_semantic_report, cross_semantic_errors, unverified_cooccurrence_edges = _audit_cross_edge_semantic_support(nodes, cross_edges)

    ungrounded_nodes: list[str] = []
    ungrounded_edges: list[str] = []
    unverified_quotes: list[str] = []
    page_locator_mismatches: list[str] = []
    unindexed_pdf_fallbacks: list[str] = []
    blocked_external_or_unc_refs: list[dict[str, Any]] = []
    unrendered_math_nodes: list[dict[str, Any]] = []
    replacement_char_count = 0
    display_labels: dict[str, str] = {}
    evidence_map: list[dict[str, Any]] = []
    verified_node_count = 0
    page_verified_count = 0
    line_verified_count = 0

    for n in nodes:
        nid = str(n.get("id", ""))
        raw_label = str(n.get("label", ""))
        disp, blocked, math_left, rep_c = _sanitize_text_and_audit_math(raw_label)
        display_labels[nid] = disp
        replacement_char_count += rep_c
        if blocked:
            blocked_external_or_unc_refs.append({"node_id": nid, "refs": blocked})
        if math_left:
            unrendered_math_nodes.append({"node_id": nid, "expressions": math_left})

        anc = n.get("source_anchor") or {}
        path = _resolve_anchor_file(anc.get("file"), corpus_dir)
        if path is None:
            ungrounded_nodes.append(f"{nid}: invalid source_anchor.file (missing or outside corpus): {anc.get('file')}")
        elif not isinstance(anc.get("quote"), str) or not anc["quote"].strip():
            ungrounded_nodes.append(f"{nid}: missing source_anchor.quote")
        else:
            q_rep = anc["quote"].count("\ufffd")
            replacement_char_count += q_rep
            qm, pg_ok, ln_ok, ext_mode = _quote_match_in_file(path, anc["quote"], anc.get("locator"))
            if ext_mode == "stdlib_stream_unindexed" and _parse_page_number_from_locator(anc.get("locator")) is not None:
                unindexed_pdf_fallbacks.append(f"node {nid}: {anc['file']} used unindexed zlib stream fallback; page locator cannot be certified.")
            if qm is False:
                msg = f"{nid}: source quote not found verbatim in anchored file {anc['file']}"
                if allow_node_degrade:
                    unverified_quotes.append(msg)
                    evidence_map.append({
                        "node_id": nid, "file": anc["file"], "locator": anc.get("locator"),
                        "quote": anc["quote"][:140], "verified_text": False,
                        "page_locator_verified": False, "line_locator_verified": False, "extractor_mode": ext_mode,
                    })
                else:
                    ungrounded_nodes.append(msg)
            elif qm is None:
                unverified_quotes.append(f"node {nid}: non-text source {anc['file']} requires page/paragraph locator check ({anc.get('locator', 'no locator')})")
                evidence_map.append({
                    "node_id": nid, "file": anc["file"], "locator": anc.get("locator"),
                    "quote": anc["quote"][:140], "verified_text": False,
                    "page_locator_verified": None, "line_locator_verified": None, "extractor_mode": ext_mode,
                })
            else:
                loc_failed = (pg_ok is False) or (ln_ok is False)
                if loc_failed:
                    reason_bits = []
                    if pg_ok is False:
                        reason_bits.append("page mismatch")
                    if ln_ok is False:
                        reason_bits.append("line mismatch")
                    mm_msg = f"node {nid}: quote exists in {anc['file']} but failed declared locator {anc.get('locator')} ({', '.join(reason_bits)})"
                    page_locator_mismatches.append(mm_msg)
                else:
                    verified_node_count += 1
                    if pg_ok is True:
                        page_verified_count += 1
                    if ln_ok is True:
                        line_verified_count += 1
                evidence_map.append({
                    "node_id": nid, "file": anc["file"], "locator": anc.get("locator"),
                    "quote": anc["quote"][:140], "verified_text": not loc_failed,
                    "page_locator_verified": pg_ok, "line_locator_verified": ln_ok, "extractor_mode": ext_mode,
                })

    for idx, ed in enumerate(cross_edges, 1):
        anc = ed.get("source_anchor") or {}
        path = _resolve_anchor_file(anc.get("file"), corpus_dir)
        if path is None:
            ungrounded_edges.append(f"edge#{idx} ({ed.get('source')}->{ed.get('target')}): invalid source_anchor.file: {anc.get('file')}")
        elif not isinstance(anc.get("quote"), str) or not anc["quote"].strip():
            ungrounded_edges.append(f"edge#{idx} ({ed.get('source')}->{ed.get('target')}): missing source_anchor.quote")
        else:
            qm, pg_ok, ln_ok, ext_mode = _quote_match_in_file(path, anc["quote"], anc.get("locator"))
            if ext_mode == "stdlib_stream_unindexed" and _parse_page_number_from_locator(anc.get("locator")) is not None:
                unindexed_pdf_fallbacks.append(f"edge#{idx}: {anc['file']} used unindexed zlib stream fallback; page locator cannot be certified.")
            if qm is False:
                ungrounded_edges.append(f"edge#{idx} ({ed.get('source')}->{ed.get('target')}): source quote not found verbatim in {anc['file']}")
            elif qm is None:
                unverified_quotes.append(f"edge#{idx}: non-text source {anc['file']} requires human locator check")
            elif pg_ok is False or ln_ok is False:
                page_locator_mismatches.append(f"edge#{idx}: quote exists in {anc['file']} but failed declared locator {anc.get('locator')} (page_ok={pg_ok}, line_ok={ln_ok})")

    layout, w, h = _compute_dynamic_tree_layout(nodes, display_labels)
    overlaps, clipped = _check_overlap_and_clipping(layout, w, h)
    cross_placements, cross_label_overlaps = _compute_cross_edge_placements(layout, cross_edges, w, h)
    font_info = discover_cjk_fonts()

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", str(spec.get("slug", "mindmap"))) or "mindmap"
    json_out = out_dir / f"{stem}.json"
    svg_out = out_dir / f"{stem}.svg"
    mm_out = out_dir / f"{stem}.mm"
    mmd_out = out_dir / f"{stem}.mmd"
    png_out = out_dir / f"{stem}.png"

    json_out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    export_layered_svg(layout, cross_placements, w, h, svg_out, cjk_family=font_info["chosen_svg_cjk_family"])
    export_freemind_mm(nodes, cross_edges, mm_out)
    export_mermaid_mmd(nodes, cross_edges, mmd_out)
    png_res = render_png_preview(layout, cross_placements, w, h, svg_out, png_out, font_info)
    submap_svgs = _export_large_scale_submaps(nodes, cross_edges, display_labels, out_dir, stem, font_info["chosen_svg_cjk_family"])

    for txt_art in (json_out, svg_out, mm_out, mmd_out):
        replacement_char_count += txt_art.read_text(encoding="utf-8", errors="replace").count("\ufffd")

    core_errors: list[str] = []
    core_errors.extend(struct_errors)
    core_errors.extend(ungrounded_nodes)
    core_errors.extend(ungrounded_edges)
    core_errors.extend(cross_semantic_errors)
    if page_locator_mismatches and not allow_node_degrade:
        core_errors.extend(page_locator_mismatches)
    if replacement_char_count > 0:
        core_errors.append(f"Detected {replacement_char_count} U+FFFD replacement character(s) (broken UTF-8/CJK encoding).")
    if overlaps:
        core_errors.append(f"Node bbox overlap(s): {overlaps}")
    if clipped:
        core_errors.append(f"Clipped node(s): {clipped}")

    gaps: list[dict[str, str]] = []
    png_errors: list[str] = []
    if not png_res.get("rendered"):
        gap_code = "MINDMAP_CJK_FONT_MISSING" if (png_res.get("contains_cjk") and not font_info["any_cjk_font_installed"]) else "MINDMAP_PNG_RENDERER_UNAVAILABLE"
        gaps.append({"code": gap_code, "detail": str(png_res.get("error", "PNG renderer failed"))})
        if not allow_partial_png:
            png_errors.append(f"PNG preview unavailable: {png_res.get('error', 'renderer failed')}")

    errors = core_errors + png_errors
    warnings = list(struct_warnings)
    if page_locator_mismatches:
        gaps.append({
            "code": "MINDMAP_LOCATOR_MISMATCH",
            "detail": f"{len(page_locator_mismatches)} anchor(s) failed declared page/line locator check (sample: {page_locator_mismatches[0][:140]}).",
        })
        if allow_node_degrade:
            warnings.extend(page_locator_mismatches)
    if unindexed_pdf_fallbacks:
        gaps.append({
            "code": "MINDMAP_UNINDEXED_PDF_FALLBACK",
            "detail": f"{len(unindexed_pdf_fallbacks)} anchor(s) fell back to unindexed PDF stream extraction; install pypdf or pdftotext for page-certified verification.",
        })
    if unverified_cooccurrence_edges:
        gaps.append({
            "code": "MINDMAP_UNVERIFIED_COOCCURRENCE_EDGE",
            "detail": f"{len(unverified_cooccurrence_edges)} cross-edge(s) rely on token co-occurrence without explicit relation cue and require human review.",
        })
        warnings.extend(unverified_cooccurrence_edges)
    unprocessed_files = list(spec.get("unprocessed_files") or (distill_stats or {}).get("unprocessed_files") or [])
    if unprocessed_files:
        gaps.append({
            "code": "MINDMAP_UNPROCESSED_SOURCE_FILES",
            "detail": f"{len(unprocessed_files)} input file(s) had no extractable text layer and were skipped: {[u.get('file') for u in unprocessed_files]}.",
        })
    if cross_label_overlaps:
        warnings.append(f"Cross-edge label overlap fallback(s): {cross_label_overlaps}")
    if blocked_external_or_unc_refs:
        warnings.append(f"Sanitized {len(blocked_external_or_unc_refs)} node(s) containing remote URLs or Windows UNC image paths.")
    if unrendered_math_nodes:
        warnings.append(f"{len(unrendered_math_nodes)} node(s) contain complex LaTeX math preserved raw in .json/.mm/.mmd (inspect SVG/PNG rendering).")
    if unverified_quotes:
        gaps.append({"code": "MINDMAP_UNVERIFIED_QUOTES", "detail": f"{len(unverified_quotes)} anchor(s) require manual locator inspection."})

    editable_valid = len(core_errors) == 0
    if len(errors) == 0 and not gaps:
        status = "ok"
    elif editable_valid and (allow_partial_png or len(errors) == 0) and gaps:
        status = "partial_with_gaps"
    else:
        status = "failed"

    artifacts = [json_out.name, svg_out.name, mm_out.name, mmd_out.name] + ([png_out.name] if png_res.get("rendered") else []) + submap_svgs
    grounding_rate = round(verified_node_count / max(1, len(nodes)), 4)
    sem_supported_edges = sum(1 for r in cross_semantic_report if r.get("semantically_supported"))

    return {
        "passed": len(errors) == 0,
        "status": status,
        "editable_artifacts_valid": editable_valid,
        "node_count": len(nodes),
        "cross_edge_count": len(cross_edges),
        "semantically_supported_edge_count": sem_supported_edges,
        "unverified_cooccurrence_edge_count": len(unverified_cooccurrence_edges),
        "verified_node_count": verified_node_count,
        "node_grounding_rate": grounding_rate,
        "page_verified_count": page_verified_count,
        "line_verified_count": line_verified_count,
        "page_locator_mismatches": page_locator_mismatches,
        "unprocessed_files": unprocessed_files,
        "subgroup_scope_notes": spec.get("subgroup_scope_notes") or (distill_stats or {}).get("subgroup_scope_notes") or [],
        "distillation": distill_stats,
        "structure": struct_stats,
        "canvas": {"width": w, "height": h},
        "replacement_char_count": replacement_char_count,
        "blocked_external_or_unc_refs": blocked_external_or_unc_refs,
        "unrendered_math_nodes": unrendered_math_nodes,
        "overlaps": overlaps,
        "clipped": clipped,
        "cross_edge_label_overlaps": cross_label_overlaps,
        "cross_edge_semantic_audit": cross_semantic_report,
        "artifacts": artifacts,
        "font_discovery": font_info,
        "png_renderer": png_res,
        "evidence_map": evidence_map,
        "unverified_quotes": unverified_quotes,
        "gaps": gaps,
        "warnings": warnings,
        "errors": errors,
    }


def build_result_contract_patch(spec: dict[str, Any], audit: dict[str, Any], case_id: str = "") -> dict[str, Any]:
    """Generate a complete 5-field output_contract patch ready for result_guard.py."""
    cid = case_id or str(spec.get("case_id") or "UNSPECIFIED_CASE")
    sources = sorted({str(e["file"]) for e in audit.get("evidence_map", []) if e.get("file")})
    preserved = [
        f"node_count={audit.get('node_count', 0)}",
        f"cross_edge_count={audit.get('cross_edge_count', 0)}",
        f"semantically_supported_edge_count={audit.get('semantically_supported_edge_count', 0)}",
        f"unverified_cooccurrence_edge_count={audit.get('unverified_cooccurrence_edge_count', 0)}",
        f"node_grounding_rate={audit.get('node_grounding_rate', 0.0)}",
        f"page_locator_mismatches={len(audit.get('page_locator_mismatches', []))}",
        f"replacement_char_count={audit.get('replacement_char_count', 0)}",
        f"overlaps={len(audit.get('overlaps', []))}",
    ]
    uncertainty = [g["detail"] for g in audit.get("gaps", [])] + list(audit.get("unverified_quotes", []))[:8]
    return {
        "case_id": cid,
        "status": audit.get("status", "failed"),
        "deliverable": {
            "artifacts": audit.get("artifacts", []),
            "node_count": audit.get("node_count", 0),
            "cross_edge_count": audit.get("cross_edge_count", 0),
            "semantically_supported_edge_count": audit.get("semantically_supported_edge_count", 0),
            "png_rendered": bool((audit.get("png_renderer") or {}).get("rendered")),
        },
        "preserved_facts": preserved,
        "evidence_map": audit.get("evidence_map", []),
        "cross_edge_evidence": audit.get("cross_edge_semantic_audit", []),
        "unprocessed_files": audit.get("unprocessed_files", []),
        "subgroup_scope_notes": audit.get("subgroup_scope_notes", []),
        "change_log": [
            {
                "action": "distill_and_build_mindmap" if audit.get("distillation") else "build_and_verify_mindmap",
                "reason": "Synchronized editable mind map exports with strict page+line verbatim grounding, causal-vs-correlation relation verification, and layout QA.",
                "artifacts": audit.get("artifacts", []),
                "warnings_count": len(audit.get("warnings", [])),
            }
        ],
        "sources": sources,
        "uncertainty": uncertainty,
        "gaps": audit.get("gaps", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src_group = parser.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--from-input", help="Raw /input directory or document (.pdf/.docx/.md/.txt) for automatic corpus-to-mindmap distillation.")
    src_group.add_argument("--spec", help="Canonical mind map specification JSON.")
    src_group.add_argument("--from-markdown", help="UTF-8 structured Markdown outline file (avoids stdin CJK chunking).")
    parser.add_argument("--slug", default="mindmap", help="Output stem when using --from-input or --from-markdown.")
    parser.add_argument("--max-nodes", type=int, default=34, help="Maximum target nodes when using --from-input (default: 34).")
    parser.add_argument("--draft-spec-out", help="Optional path to save the auto-distilled spec JSON before rendering.")
    parser.add_argument("--corpus-dir", help="Input corpus directory for exact-file anchor grounding (defaults to --from-input dir).")
    parser.add_argument("--out-dir", required=True, help="Output directory for .json/.svg/.mm/.mmd/.png.")
    parser.add_argument("--out-json", required=True, help="Audit summary JSON path.")
    parser.add_argument("--out-result-patch", help="Optional output path for auto-generated 5-field result.json contract patch.")
    parser.add_argument("--case-id", default="", help="Optional case_id embedded in --out-result-patch.")
    parser.add_argument("--allow-partial-png", action="store_true", help="Degrade missing CJK font / PNG renderer to status=partial_with_gaps instead of hard exit 1.")
    parser.add_argument("--allow-node-degrade", action="store_true", help="Record unverified node quotes in unverified_quotes instead of hard-failing the entire map.")
    args = parser.parse_args(argv)

    distill_stats: dict[str, Any] | None = None
    if args.from_input:
        in_p = Path(args.from_input)
        corpus_dir = Path(args.corpus_dir) if args.corpus_dir else (in_p if in_p.is_dir() else in_p.parent)
        spec_obj, distill_stats = distill_corpus_to_spec(in_p, slug=args.slug, max_nodes=max(10, min(120, args.max_nodes)))
        if args.draft_spec_out:
            dp = Path(args.draft_spec_out)
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(json.dumps(spec_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        if not args.corpus_dir:
            parser.error("--corpus-dir is required with --spec or --from-markdown")
        corpus_dir = Path(args.corpus_dir)
        if args.spec:
            spec_obj = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        else:
            spec_obj = parse_markdown_to_spec(Path(args.from_markdown), slug=args.slug)

    rep = build_and_validate_mindmap(
        spec_obj,
        corpus_dir,
        Path(args.out_dir),
        allow_partial_png=args.allow_partial_png,
        allow_node_degrade=args.allow_node_degrade,
        distill_stats=distill_stats,
    )
    op = Path(args.out_json)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.out_result_patch:
        patch = build_result_contract_patch(spec_obj, rep, case_id=args.case_id)
        pp = Path(args.out_result_patch)
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text(json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "passed": rep["passed"],
        "status": rep.get("status"),
        "node_count": rep.get("node_count", 0),
        "cross_edge_count": rep.get("cross_edge_count", 0),
        "node_grounding_rate": rep.get("node_grounding_rate", 0.0),
        "page_verified_count": rep.get("page_verified_count", 0),
        "replacement_char_count": rep.get("replacement_char_count", 0),
        "artifacts": rep.get("artifacts", []),
        "gaps": rep.get("gaps", []),
        "warnings": rep.get("warnings", []),
        "errors": rep.get("errors", []),
    }, ensure_ascii=False, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
