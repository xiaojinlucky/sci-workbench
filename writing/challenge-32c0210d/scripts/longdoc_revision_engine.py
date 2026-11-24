#!/usr/bin/env python3
"""Offline local revision state machine for long documents and thesis chapters.

Zero-dependency Python 3.12 stdlib implementation (with optional pdftotext/pdfplumber/pypdf
acceleration for PDFs and stdlib OOXML/zlib fallbacks):
  - import: byte-exact SHA-256 snapshot (.md/.txt/.tex/.docx/.pdf), adaptive target_id
    pattern induction + deterministic auto paragraph anchoring, coverage & truncation receipt
  - propose: target-scoped annotate or substantive replace_block proposal (supports
    --block-index, --anchor-query, --annotation-file, --replacement-file)
  - accept / reject: individual per-proposal decision preserving all unrequested bytes
  - diff / rollback / reapply: unified diff, 100% SHA-256 rollback, deterministic reapply
  - fault-test: 5 deliberate invalid operations proving zero state/file mutation
  - parse-comments: decompose advisor/reviewer comments into trackable edit action items
  - export: editable chapters, revision records, evidence map, offline HTML diff preview
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

CHAPTER_EXTS = {".md", ".txt", ".tex", ".docx", ".pdf"}
DEFAULT_ANCHOR_PATTERNS = [
    re.compile(r"^\s*\[([A-Za-z0-9]+-P\d+[A-Za-z0-9_-]*)\]"),
    re.compile(r"<!--\s*(?:id\s*:\s*)?([A-Za-z0-9]+-P\d+[A-Za-z0-9_-]*)\s*-->"),
    re.compile(r'id=["\']([A-Za-z0-9]+-P\d+[A-Za-z0-9_-]*)["\']'),
    re.compile(r"\{#([A-Za-z0-9]+-P\d+[A-Za-z0-9_-]*)\}"),
    re.compile(r"^\s*(?:\*\*)?([A-Za-z0-9]+-P\d+[A-Za-z0-9_-]*)(?:\*\*)?\s*[:：]"),
]
ANNOTATION_BLOCK_RE = re.compile(
    r"\n\n> \[Editorial Annotation \| ID: (?P<pid>[A-Za-z0-9_-]+) \|[^\n]*\n"
    r"(?:> [^\n]*\n)*"
    r"> \[/Editorial Annotation \| ID: (?P=pid)\]",
    re.MULTILINE,
)
TRUNCATION_MARKER_RE = re.compile(
    r"\[(?:truncated|cut\s+off|page\s+missing|pages?\s+omitted|incomplete)\]|"
    r"（(?:此处截断|缺页|内容缺失|未完)）|(?:\.\.\.|……)\s*$",
    re.I,
)
CHAPTER_NUM_RE = re.compile(r"(?:^|[/_-])(?:ch(?:apter)?|sec(?:tion)?|part|第)\s*0*(\d+)", re.I)


class RevisionError(Exception):
    error_code = "REVISION_ERROR"


class TargetNotFoundError(RevisionError):
    error_code = "TARGET_NOT_FOUND"


class UnauthorizedTargetError(RevisionError):
    error_code = "UNAUTHORIZED_SCOPE"


class ProposalNotFoundError(RevisionError):
    error_code = "PROPOSAL_NOT_FOUND"


class VersionNotFoundError(RevisionError):
    error_code = "VERSION_NOT_FOUND"


class InvalidPatchError(RevisionError):
    error_code = "INVALID_PATCH"


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def strip_editorial_annotations(text: str) -> str:
    return ANNOTATION_BLOCK_RE.sub("", text.replace("\r\n", "\n"))


def _is_running_header_line(text: str) -> bool:
    """Filter PDF/DOCX running headers, bare page numbers, and URL footers from evidence quotes."""
    st = text.strip()
    if not st:
        return True
    low = st.lower()
    if re.fullmatch(r"\d{1,4}", st) or re.fullmatch(r"page\s*\d+(?:\s*of\s*\d+)?", low):
        return True
    if low.startswith(("<!-- page", "## [page", "contains nonbinding recommendations", "https://", "http://", "doi:", "rev paul pediatr")):
        return True
    if "contains nonbinding recommendations" in low and len(st) < 420:
        return True
    if re.search(r"rev\s+paul\s+pediatr\.?\s*\d{4}", low) and len(st) < 200:
        return True
    if re.search(r"https?://\S+", st) and re.search(r"\b\d{2}/\d{2}/\d{2,4}\b", st) and len(st) < 500:
        return True
    return False


LIVE_CITATION_FIELD_RE = re.compile(
    r"ADDIN\s+(?:ZOTERO_ITEM|ZOTERO_BIBL|CSL_CITATION|EN\.CITE|EN\.REFLIST|Mendeley)|"
    r"\b(?:ZOTERO|CSL_CITATION|ENDNOTE|MENDELEY|CITATION)\b",
    re.I,
)


def _extract_docx_text(docx_path: Path) -> tuple[str, dict[str, Any]]:
    paras: list[str] = []
    live_fields: list[str] = []
    field_systems: set[str] = set()
    tracked_changes = 0
    filtered_headers = 0
    try:
        with zipfile.ZipFile(docx_path, "r") as zf:
            for xml_name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
                if xml_name not in zf.namelist():
                    continue
                root = ET.fromstring(zf.read(xml_name))
                for elem in root.iter():
                    tag = elem.tag.split("}")[-1]
                    if tag in {"ins", "del"}:
                        tracked_changes += 1
                    elif tag == "instrText" and elem.text and elem.text.strip():
                        code = elem.text.strip()
                        if LIVE_CITATION_FIELD_RE.search(code):
                            live_fields.append(code[:140])
                            low_c = code.lower()
                            if "zotero" in low_c or "csl" in low_c:
                                field_systems.add("Zotero/CSL")
                            if "en.cite" in low_c or "endnote" in low_c:
                                field_systems.add("EndNote")
                            if "mendeley" in low_c:
                                field_systems.add("Mendeley")
                            if not field_systems:
                                field_systems.add("Word_Citation_Field")
                for p_node in root.iter():
                    if p_node.tag.split("}")[-1] != "p":
                        continue
                    pieces: list[str] = []
                    for child in p_node.iter():
                        ctag = child.tag.split("}")[-1]
                        if ctag == "t" and child.text:
                            pieces.append(child.text)
                        elif ctag == "tab":
                            pieces.append("\t")
                        elif ctag == "br":
                            pieces.append("\n")
                    line = "".join(pieces).strip()
                    if not line:
                        continue
                    if _is_running_header_line(line):
                        filtered_headers += 1
                        continue
                    paras.append(line)
    except (zipfile.BadZipFile, ET.ParseError, OSError) as exc:
        raise RevisionError(f"Corrupt or unreadable DOCX archive {docx_path.name}: {exc}") from exc

    return "\n\n".join(paras) + "\n", {
        "format": "docx",
        "extractor": "stdlib_ooxml",
        "paragraphs": len(paras),
        "filtered_running_headers": filtered_headers,
        "tracked_change_nodes": tracked_changes,
        "live_citation_fields_detected": len(live_fields),
        "citation_field_systems": sorted(field_systems),
        "live_citation_field_samples": live_fields[:6],
        "format_mode": "derived_markdown_readonly_source",
        "citation_fields_preserved": False,
    }


def patch_docx_plain_paragraph(src_docx: Path, dst_docx: Path, *, match_substring: str, replacement_substring: str) -> dict[str, Any]:
    """Targeted OOXML plain-text paragraph patch leaving untouched ZIP members and w:instrText citation fields intact."""
    if not match_substring or not src_docx.is_file():
        raise InvalidPatchError("patch_docx_plain_paragraph requires existing src_docx and non-empty match_substring.")
    patched = False
    before_fields = 0
    after_fields = 0
    with zipfile.ZipFile(src_docx, "r") as zin:
        doc_xml = zin.read("word/document.xml")
        root = ET.fromstring(doc_xml)
        before_fields = sum(1 for e in root.iter() if e.tag.split("}")[-1] == "instrText" and e.text and LIVE_CITATION_FIELD_RE.search(e.text))
        for p_node in root.iter():
            if p_node.tag.split("}")[-1] != "p":
                continue
            # Never mutate paragraphs containing live citation field instructions
            has_field = any(c.tag.split("}")[-1] in {"instrText", "fldChar"} for c in p_node.iter())
            if has_field:
                continue
            for t_node in p_node.iter():
                if t_node.tag.split("}")[-1] == "t" and t_node.text and match_substring in t_node.text:
                    t_node.text = t_node.text.replace(match_substring, replacement_substring, 1)
                    patched = True
                    break
            if patched:
                break
        if not patched:
            raise TargetNotFoundError("Target plain-text paragraph not found (or protected because it holds live citation fields).")
        new_doc_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        dst_docx.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dst_docx, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                zout.writestr(item, new_doc_xml if item.filename == "word/document.xml" else zin.read(item.filename))
    with zipfile.ZipFile(dst_docx, "r") as zchk:
        rchk = ET.fromstring(zchk.read("word/document.xml"))
        after_fields = sum(1 for e in rchk.iter() if e.tag.split("}")[-1] == "instrText" and e.text and LIVE_CITATION_FIELD_RE.search(e.text))
    return {
        "ok": patched and (before_fields == after_fields),
        "format_mode": "targeted_ooxml_plain_text_patch",
        "live_citation_fields_before": before_fields,
        "live_citation_fields_after": after_fields,
        "citation_fields_preserved": before_fields == after_fields,
        "output_docx": str(dst_docx),
    }


def _extract_pdf_stdlib_zlib(pdf_path: Path) -> tuple[str, int]:
    raw = pdf_path.read_bytes()
    pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", raw))
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.DOTALL):
        data = m.group(1)
        dec = None
        for wb in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                dec = zlib.decompress(data, wb)
                break
            except Exception:
                pass
        s = (dec or data).decode("latin-1", errors="ignore")
        if "Tj" not in s and "TJ" not in s:
            continue
        for tm in re.finditer(r"\(((?:\\.|[^\\)])*)\)\s*Tj", s):
            t = tm.group(1).replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\").strip()
            if t:
                chunks.append(t)
        for am in re.finditer(r"\[(.*?)\]\s*TJ", s, re.DOTALL):
            parts = [pm.group(1).replace(r"\(", "(").replace(r"\)", ")") for pm in re.finditer(r"\(((?:\\.|[^\\)])*)\)", am.group(1))]
            joined = "".join(parts).strip()
            if joined:
                chunks.append(joined)
    return "\n\n".join(chunks) + "\n", max(pages, 1 if chunks else 0)


def _read_source_chapter(fp: Path) -> tuple[str, bytes, dict[str, Any]]:
    raw = fp.read_bytes()
    suf = fp.suffix.lower()
    if suf == ".docx":
        txt, meta = _extract_docx_text(fp)
    elif suf == ".pdf":
        approx_pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", raw))
        txt, meta = "", {"format": "pdf", "extractor": "stdlib_zlib_fallback", "pages": approx_pages}
        bin_p = shutil.which("pdftotext")
        if bin_p:
            try:
                pr = subprocess.run([bin_p, "-enc", "UTF-8", str(fp), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                if pr.returncode == 0 and pr.stdout.strip():
                    txt, meta = pr.stdout, {"format": "pdf", "extractor": "pdftotext", "pages": approx_pages or 1}
            except Exception:
                pass
        if not txt.strip():
            try:
                import pdfplumber  # type: ignore
                with pdfplumber.open(str(fp)) as doc:
                    joined = "\n\n".join((pg.extract_text() or "") for pg in doc.pages if (pg.extract_text() or "").strip())
                    if joined.strip():
                        txt = joined + "\n"
                        meta = {"format": "pdf", "extractor": "pdfplumber", "pages": len(doc.pages)}
            except Exception:
                pass
        if not txt.strip():
            try:
                from pypdf import PdfReader  # type: ignore
                rd = PdfReader(str(fp))
                joined = "\n\n".join((pg.extract_text() or "") for pg in rd.pages if (pg.extract_text() or "").strip())
                if joined.strip():
                    txt = joined + "\n"
                    meta = {"format": "pdf", "extractor": "pypdf", "pages": len(rd.pages)}
            except Exception:
                pass
        if not txt.strip():
            txt, pages = _extract_pdf_stdlib_zlib(fp)
            meta = {"format": "pdf", "extractor": "stdlib_zlib_fallback", "pages": pages}
        meta["format_mode"] = "derived_markdown_readonly_source"
        meta["citation_fields_preserved"] = False
    else:
        txt = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
        meta = {
            "format": suf.lstrip(".") or "txt",
            "extractor": "utf8_text",
            "format_mode": "native_text_in_place",
            "citation_fields_preserved": True,
            "live_citation_fields_detected": 0,
        }
    meta["source_bytes"] = len(raw)
    meta["source_sha256"] = _sha256_bytes(raw)
    return txt, raw, meta


def _induce_anchor_patterns(target_ids: list[str]) -> list[re.Pattern[str]]:
    patterns = list(DEFAULT_ANCHOR_PATTERNS)
    seen: set[str] = set()
    for tid in target_ids:
        t = tid.strip()
        if not t:
            continue
        shape = "".join(
            r"[A-Za-z]+" if tok.isalpha() else (r"\d+" if tok.isdigit() else re.escape(tok))
            for tok in re.findall(r"[A-Za-z]+|\d+|[^A-Za-z0-9]+", t)
        )
        if shape not in seen:
            seen.add(shape)
            patterns.append(
                re.compile(rf"(?:^\s*\[|<!--\s*(?:id\s*:\s*)?|id=[\"']|\{{#|\s|^|^\s*\*\*)({shape})(?:[\]\"'}}:*：]|\s|$)")
            )
    return patterns


def parse_workorder(workorder_path: Path | None) -> dict[str, Any]:
    if not workorder_path or not workorder_path.is_file():
        return {
            "present": False, "workorder_id": None, "schema_mode": "none",
            "target_ids": [], "target_files": [], "operation_requirements": [], "raw_source_path": None,
        }
    raw = json.loads(workorder_path.read_text(encoding="utf-8"))
    wo = raw.get("organizer_edit_workorder", raw) if isinstance(raw, dict) else {}
    if not isinstance(wo, dict):
        wo = {}

    target_ids: list[str] = []
    if isinstance(wo.get("target_ids"), list):
        target_ids.extend(str(x).strip() for x in wo["target_ids"] if str(x).strip())

    target_files: list[str] = []
    if isinstance(wo.get("edits"), list):
        for item in wo["edits"]:
            if isinstance(item, dict):
                if isinstance(item.get("file"), str) and item["file"].strip():
                    p = PurePosixPath(item["file"].replace("\\", "/"))
                    if not p.is_absolute() and ".." not in p.parts:
                        target_files.append(p.as_posix().removeprefix("chapters/"))
                if isinstance(item.get("target_id"), str) and item["target_id"].strip():
                    target_ids.append(item["target_id"].strip())

    target_ids = sorted(set(target_ids))
    target_files = sorted(set(target_files))
    if target_ids and target_files:
        schema_mode = "hybrid_target_ids_and_files"
    elif target_ids:
        schema_mode = "paragraph_target_ids"
    elif target_files:
        schema_mode = "chapter_edits_files"
    else:
        schema_mode = "unconstrained"

    return {
        "present": True,
        "workorder_id": wo.get("id") or raw.get("case_id"),
        "authority": wo.get("authority", ""),
        "schema_mode": schema_mode,
        "target_ids": target_ids,
        "target_files": target_files,
        "operation_requirements": wo.get("operation_requirements", []),
        "raw_source_path": workorder_path.name,
    }


def _extract_anchor_id(block_text: str, patterns: list[re.Pattern[str]] | None = None) -> str | None:
    first_lines = "\n".join(block_text.splitlines()[:3])
    for pat in (patterns or DEFAULT_ANCHOR_PATTERNS):
        m = pat.search(first_lines)
        if m:
            return m.group(1).strip()
    return None


def _split_blocks_with_offsets(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    pos = 0
    length = len(text)
    idx = 0
    for m in re.finditer(r"\r?\n\s*\r?\n+", text):
        end = m.start()
        if end > pos:
            chunk = text[pos:end]
            if chunk.strip():
                idx += 1
                blocks.append({
                    "block_index": idx, "start": pos, "end": end,
                    "start_line": text.count("\n", 0, pos) + 1,
                    "end_line": text.count("\n", 0, end) + 1, "text": chunk,
                })
        pos = m.end()
    if pos < length:
        chunk = text[pos:length]
        if chunk.strip():
            idx += 1
            blocks.append({
                "block_index": idx, "start": pos, "end": length,
                "start_line": text.count("\n", 0, pos) + 1,
                "end_line": text.count("\n", 0, length) + 1, "text": chunk,
            })
    return blocks


def _scan_chapter_files(chapters_dir: Path) -> list[Path]:
    return sorted(p for p in chapters_dir.rglob("*") if p.is_file() and p.suffix.lower() in CHAPTER_EXTS)


def _collect_dir_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for fp in sorted(p for p in root.rglob("*") if p.is_file()):
        out[fp.relative_to(root).as_posix()] = _sha256_file(fp)
    return out


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for fp in sorted(p for p in src.rglob("*") if p.is_file()):
        target = dst / fp.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fp, target)


def _load_state(state_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest_p = state_dir / "manifest.json"
    if not manifest_p.is_file():
        raise RevisionError(f"State directory not initialized: {state_dir}")
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    proposals = json.loads((state_dir / "proposals.json").read_text(encoding="utf-8")) if (state_dir / "proposals.json").is_file() else []
    history = json.loads((state_dir / "history.json").read_text(encoding="utf-8")) if (state_dir / "history.json").is_file() else []
    return manifest, proposals, history


def _save_state(state_dir: Path, manifest: dict[str, Any], proposals: list[dict[str, Any]], history: list[dict[str, Any]]) -> None:
    _atomic_write_json(state_dir / "manifest.json", manifest)
    _atomic_write_json(state_dir / "proposals.json", proposals)
    _atomic_write_json(state_dir / "history.json", history)


def cmd_import(
    chapters_dir: Path,
    state_dir: Path,
    *,
    workorder_path: Path | None = None,
    toc_path: Path | None = None,
    receipt_out: Path | None = None,
    allow_file_fallback: bool = False,
) -> dict[str, Any]:
    files = _scan_chapter_files(chapters_dir)
    if not files:
        raise RevisionError(f"No chapter files (.md/.txt/.tex/.docx/.pdf) found in {chapters_dir}")

    wo = parse_workorder(workorder_path)
    anchor_patterns = _induce_anchor_patterns(wo["target_ids"])

    if state_dir.exists():
        shutil.rmtree(state_dir)
    snap_v0 = state_dir / "snapshots" / "v0"
    working = state_dir / "working"
    snap_v0.mkdir(parents=True, exist_ok=True)
    working.mkdir(parents=True, exist_ok=True)

    anchor_index: dict[str, dict[str, Any]] = {}
    initial_sha256: dict[str, str] = {}
    initial_source_meta: dict[str, dict[str, Any]] = {}
    chapter_inventory: list[dict[str, Any]] = []
    chapter_nums: list[int] = []
    truncation_risks: list[dict[str, Any]] = []

    for ch_idx, fp in enumerate(files, 1):
        orig_rel = fp.relative_to(chapters_dir).as_posix()
        text, raw_bytes, rmeta = _read_source_chapter(fp)
        is_binary_src = fp.suffix.lower() in {".docx", ".pdf"}
        # Normalize line endings to \n on text import so annotate strip roundtrip SHA-256 is 100% exact
        work_rel = f"{orig_rel}.extracted.md" if is_binary_src else orig_rel
        work_bytes = text.replace("\r\n", "\n").encode("utf-8") if is_binary_src else raw_bytes.replace(b"\r\n", b"\n")
        initial_sha256[work_rel] = _sha256_bytes(work_bytes)
        initial_source_meta[work_rel] = {"original_file": orig_rel, **rmeta}

        for dest_root in (snap_v0, working):
            dest = dest_root / work_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(work_bytes)

        chapter_inventory.append({
            "file": work_rel, "original_file": orig_rel, "format": rmeta["format"],
            "extractor": rmeta["extractor"], "bytes": len(work_bytes),
            "lines": len(text.splitlines()), "chars": len(text), "sha256": initial_sha256[work_rel],
        })
        m_num = CHAPTER_NUM_RE.search(orig_rel)
        if m_num:
            chapter_nums.append(int(m_num.group(1)))

        if TRUNCATION_MARKER_RE.search(text):
            truncation_risks.append({"file": work_rel, "reason": "explicit_truncation_marker", "excerpt": text.strip()[-120:]})
        else:
            st = text.rstrip()
            if len(st) >= 80:
                last_line = st.splitlines()[-1].strip()
                if st[-1] not in ".!?。！？)]}”\"'`|>" and not last_line.startswith(("#", "|", "-", "*", "\\")):
                    truncation_risks.append({"file": work_rel, "reason": "abrupt_unpunctuated_ending", "excerpt": last_line[-120:]})

        for blk in _split_blocks_with_offsets(text):
            explicit_id = _extract_anchor_id(blk["text"], anchor_patterns)
            aid = explicit_id or f"CH{ch_idx:02d}-P{blk['block_index']:03d}"
            if aid in anchor_index:
                aid = f"{work_rel}::P{blk['block_index']:03d}"
            anchor_index[aid] = {
                "file": work_rel,
                "block_index": blk["block_index"],
                "start_line": blk["start_line"],
                "end_line": blk["end_line"],
                "explicit": explicit_id is not None,
                "original_sha256": _sha256_bytes(blk["text"].encode("utf-8")),
                "preview": " ".join(blk["text"].split())[:140],
            }

    missing_chapters: list[str] = []
    if chapter_nums:
        present = set(chapter_nums)
        for n in range(min(present), max(present) + 1):
            if n not in present:
                missing_chapters.append(f"chapter_{n:02d}")
    toc_gaps: list[str] = []
    if toc_path and toc_path.is_file():
        toc = json.loads(toc_path.read_text(encoding="utf-8"))
        present_rels = {item["original_file"] for item in chapter_inventory} | set(initial_sha256.keys())
        for exp in (toc.get("expected_chapters", []) if isinstance(toc, dict) else []):
            exp_name = exp.get("file", "") if isinstance(exp, dict) else str(exp)
            if exp_name and exp_name not in present_rels:
                toc_gaps.append(exp_name)
                if exp_name not in missing_chapters:
                    missing_chapters.append(exp_name)

    coverage = {
        "chapters_imported": len(files),
        "chapter_inventory": chapter_inventory,
        "missing_chapters": missing_chapters,
        "toc_gaps": toc_gaps,
        "truncation_risks": truncation_risks,
        "complete_coverage_claimed": len(missing_chapters) == 0 and len(truncation_risks) == 0,
    }

    # Resolve workorder target_ids strictly (never silently fall back to full-file passthrough!)
    resolved_targets: dict[str, str] = {}
    missing_targets: list[str] = []
    for tid in wo["target_ids"]:
        if tid in anchor_index:
            resolved_targets[tid] = anchor_index[tid]["file"]
        else:
            missing_targets.append(tid)

    # Map original file names in workorder to working relative names (handles .docx/.pdf -> .extracted.md)
    orig_to_work = {v["original_file"]: k for k, v in initial_source_meta.items()}
    norm_target_files = [orig_to_work.get(tf, tf) for tf in wo["target_files"]]
    authorized_files = sorted(set(norm_target_files) | set(resolved_targets.values()))

    if missing_targets:
        scope_status = "DEGRADED_FILE_LEVEL_WARNING" if (allow_file_fallback and norm_target_files) else "BLOCKED_MISSING_TARGETS"
    else:
        scope_status = "ENFORCED" if wo["present"] else "UNCONSTRAINED"

    manifest = {
        "schema_version": "2.1",
        "imported_at_utc": _utc_now(),
        "current_version": "v0",
        "version_counter": 0,
        "workorder": wo,
        "scope_enforcement_status": scope_status,
        "authorized_target_ids": wo["target_ids"],
        "authorized_files": authorized_files,
        "resolved_target_map": resolved_targets,
        "missing_workorder_targets": missing_targets,
        "initial_sha256": initial_sha256,
        "initial_source_meta": initial_source_meta,
        "anchor_index": anchor_index,
        "coverage": coverage,
    }
    history = [{
        "event": "import", "version": "v0", "parent_version": None,
        "timestamp_utc": manifest["imported_at_utc"], "file_sha256": initial_sha256,
        "notes": f"Imported {len(files)} chapter file(s) with {len(anchor_index)} indexed block(s); scope_status={scope_status}.",
    }]
    _save_state(state_dir, manifest, [], history)

    has_derived_binary = any(m.get("format_mode") == "derived_markdown_readonly_source" for m in initial_source_meta.values())
    total_live_fields = sum(int(m.get("live_citation_fields_detected", 0)) for m in initial_source_meta.values())
    format_declaration = {
        "format_mode": "derived_markdown_readonly_source" if has_derived_binary else "native_text_in_place",
        "citation_fields_preserved": not has_derived_binary,
        "live_citation_fields_detected": total_live_fields,
        "per_file_format_meta": {
            k: {
                "original_file": v.get("original_file"),
                "format": v.get("format"),
                "format_mode": v.get("format_mode", "native_text_in_place"),
                "citation_fields_preserved": v.get("citation_fields_preserved", True),
                "live_citation_fields_detected": v.get("live_citation_fields_detected", 0),
                "citation_field_systems": v.get("citation_field_systems", []),
            }
            for k, v in initial_source_meta.items()
        },
    }
    manifest["format_declaration"] = format_declaration
    _save_state(state_dir, manifest, [], history)

    receipt = {
        "ok": scope_status != "BLOCKED_MISSING_TARGETS",
        "receipt_type": "import_receipt",
        "imported_at_utc": manifest["imported_at_utc"],
        "current_version": "v0",
        "chapters_count": len(files),
        "indexed_anchors_count": len(anchor_index),
        "explicit_anchors_count": sum(1 for v in anchor_index.values() if v["explicit"]),
        "auto_anchors_count": sum(1 for v in anchor_index.values() if not v["explicit"]),
        "workorder_id": wo["workorder_id"],
        "workorder_schema_mode": wo["schema_mode"],
        "scope_enforcement_status": scope_status,
        "authorized_target_ids": wo["target_ids"],
        "authorized_files": authorized_files,
        "resolved_target_map": resolved_targets,
        "missing_workorder_targets": missing_targets,
        "format_mode": format_declaration["format_mode"],
        "citation_fields_preserved": format_declaration["citation_fields_preserved"],
        "live_citation_fields_detected": format_declaration["live_citation_fields_detected"],
        "format_declaration": format_declaration,
        "initial_sha256": initial_sha256,
        "coverage_manifest": coverage,
    }
    if receipt_out:
        _atomic_write_json(receipt_out, receipt)
    return receipt


def _check_authorization(
    manifest: dict[str, Any],
    *,
    target_id: str | None,
    file_rel: str | None,
    block_index: int | None = None,
    anchor_query: str = "",
) -> tuple[str, str, str]:
    """Resolve and enforce workorder authorization. Never silently bypasses missing_targets."""
    anchors = manifest["anchor_index"]
    wo = manifest["workorder"]
    scope_status = manifest.get("scope_enforcement_status", "ENFORCED")
    anchoring_warning = ""

    if wo["present"] and scope_status == "BLOCKED_MISSING_TARGETS":
        raise UnauthorizedTargetError(
            f"Workorder scope blocked: target_ids {manifest.get('missing_workorder_targets')} were not found in imported chapters."
        )

    resolved_file = file_rel
    resolved_tid = target_id

    if target_id:
        if target_id not in anchors:
            raise TargetNotFoundError(f"Target anchor {target_id!r} not found in imported chapters.")
        resolved_file = anchors[target_id]["file"]
    elif file_rel:
        orig_to_work = {v.get("original_file"): k for k, v in manifest.get("initial_source_meta", {}).items()}
        resolved_file = orig_to_work.get(file_rel, file_rel)
        if resolved_file not in manifest["initial_sha256"]:
            raise TargetNotFoundError(f"Chapter file {file_rel!r} not found in imported chapters.")
        file_anchors = sorted(
            [(aid, meta) for aid, meta in anchors.items() if meta["file"] == resolved_file],
            key=lambda x: x[1]["block_index"],
        )
        if block_index is not None:
            hit = next((aid for aid, m in file_anchors if m["block_index"] == block_index), None)
            if not hit:
                raise TargetNotFoundError(f"Block index {block_index} not found in {resolved_file!r}.")
            resolved_tid = hit
        elif anchor_query.strip():
            q = anchor_query.strip().lower()
            hit = next((aid for aid, m in file_anchors if q in m["preview"].lower()), None)
            if not hit:
                raise TargetNotFoundError(f"No block matching query {anchor_query!r} in {resolved_file!r}.")
            resolved_tid = hit
        else:
            resolved_tid = file_anchors[-1][0]
            anchoring_warning = (
                f"No --target-id, --block-index, or --anchor-query provided for {resolved_file}; "
                f"defaulted to trailing block {resolved_tid}."
            )
    else:
        raise InvalidPatchError("Either --target-id or --file must be provided.")

    assert resolved_file is not None and resolved_tid is not None
    if wo["present"]:
        auth_ids = set(manifest.get("authorized_target_ids", []))
        auth_files = set(manifest.get("authorized_files", []))
        if auth_ids and scope_status == "ENFORCED" and resolved_tid not in auth_ids:
            raise UnauthorizedTargetError(
                f"Target {resolved_tid!r} is outside workorder authorized target_ids {sorted(auth_ids)}."
            )
        if (not auth_ids or scope_status == "DEGRADED_FILE_LEVEL_WARNING") and auth_files and resolved_file not in auth_files:
            raise UnauthorizedTargetError(
                f"File {resolved_file!r} is outside workorder authorized files {sorted(auth_files)}."
            )
    return resolved_tid, resolved_file, anchoring_warning


def _format_annotation_block(prop: dict[str, Any]) -> str:
    lines = [
        f"> [Editorial Annotation | ID: {prop['proposal_id']} | Target: {prop['target_id']} | Title: {prop['title']}]",
        f"> Note: {prop['annotation']}",
        f"> Source Quote: \"{prop['source_quote']}\" (Ref: {prop['source_ref']})",
    ]
    if prop.get("uncertainty"):
        lines.append(f"> Uncertainty / Open Conflict: {prop['uncertainty']}")
    lines.append(f"> [/Editorial Annotation | ID: {prop['proposal_id']}]")
    return "\n\n" + "\n".join(lines)


def _apply_proposal_to_text(
    text: str,
    prop: dict[str, Any],
    anchor_meta: dict[str, Any],
    anchor_patterns: list[re.Pattern[str]],
) -> str:
    blocks = _split_blocks_with_offsets(text)
    target_blk = None
    tid = prop["target_id"]
    for blk in blocks:
        if _extract_anchor_id(blk["text"], anchor_patterns) == tid:
            target_blk = blk
            break
    if target_blk is None:
        orig_hash = anchor_meta["original_sha256"]
        for blk in blocks:
            if _sha256_bytes(blk["text"].encode("utf-8")) == orig_hash:
                target_blk = blk
                break
    if target_blk is None:
        bidx = anchor_meta.get("block_index")
        if isinstance(bidx, int) and 1 <= bidx <= len(blocks):
            cand = blocks[bidx - 1]
            if not cand["text"].lstrip().startswith("> [Editorial Annotation"):
                target_blk = cand
    if target_blk is None:
        raise TargetNotFoundError(f"Could not locate intact original block for {tid!r} in {prop['file']}.")

    if prop["mode"] == "annotate":
        marker = f"[Editorial Annotation | ID: {prop['proposal_id']} |"
        if marker in text:
            raise InvalidPatchError(f"Proposal {prop['proposal_id']} is already applied.")
        return text[: target_blk["end"]] + _format_annotation_block(prop) + text[target_blk["end"] :]

    if prop["mode"] == "replace_block":
        replacement = prop.get("replacement_text", "").strip()
        if not replacement:
            raise InvalidPatchError("replace_block mode requires non-empty replacement_text.")
        return text[: target_blk["start"]] + replacement + text[target_blk["end"] :]

    raise InvalidPatchError(f"Unsupported proposal mode: {prop['mode']!r}")


def _load_review_items(state_dir: Path) -> list[dict[str, Any]]:
    rp = state_dir / "review_items.json"
    if not rp.is_file():
        return []
    data = json.loads(rp.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("action_items", [])


def _save_review_items(state_dir: Path, items: list[dict[str, Any]]) -> None:
    _atomic_write_json(state_dir / "review_items.json", items)


def cmd_propose(
    state_dir: Path,
    *,
    target_id: str | None = None,
    file_rel: str | None = None,
    block_index: int | None = None,
    anchor_query: str = "",
    mode: str = "annotate",
    title: str = "",
    annotation: str = "",
    source_quote: str = "",
    source_ref: str = "",
    uncertainty: str = "",
    replacement_text: str = "",
    review_item_id: str = "",
) -> dict[str, Any]:
    manifest, proposals, history = _load_state(state_dir)
    if mode == "replace":
        mode = "replace_block"
    if mode not in {"annotate", "replace_block"}:
        raise InvalidPatchError(f"Unsupported mode {mode!r}; expected 'annotate', 'replace', or 'replace_block'.")
    if not title.strip():
        raise InvalidPatchError("Proposal title must be non-empty.")
    if mode == "annotate" and not annotation.strip():
        raise InvalidPatchError("Annotation body must be non-empty.")
    if mode == "replace_block" and not replacement_text.strip():
        raise InvalidPatchError("Substantive replace mode requires non-empty --replacement-text or --replacement-file.")
    if not source_quote.strip() or not source_ref.strip():
        raise InvalidPatchError("Both --source-quote and --source-ref are required for grounded revisions.")
    if _is_running_header_line(source_quote):
        raise InvalidPatchError(f"Running header or page-banner text cannot serve as evidence source_quote: {source_quote!r}")

    resolved_tid, resolved_file, anchoring_warning = _check_authorization(
        manifest, target_id=target_id, file_rel=file_rel, block_index=block_index, anchor_query=anchor_query
    )
    review_items = _load_review_items(state_dir)
    linked_rid = review_item_id.strip() or None
    if linked_rid:
        ritem = next((it for it in review_items if it.get("action_id") == linked_rid or it.get("review_item_id") == linked_rid), None)
        if ritem is None:
            raise InvalidPatchError(f"Review item {linked_rid!r} not found in state_dir/review_items.json.")
        if ritem.get("resolution_status") == "out_of_scope":
            raise UnauthorizedTargetError(f"Review item {linked_rid!r} is marked out_of_scope and cannot be auto-resolved.")
        if ritem.get("suggested_target_id") and ritem["suggested_target_id"] != resolved_tid:
            raise InvalidPatchError(
                f"4-key anchor mismatch: review item {linked_rid} targets {ritem['suggested_target_id']}, "
                f"but proposal targets {resolved_tid}."
            )
    else:
        # Auto-link if an existing review item binds directly to this target_id
        auto_r = next((it for it in review_items if it.get("suggested_target_id") == resolved_tid and it.get("resolution_status") not in {"out_of_scope", "resolved"}), None)
        if auto_r:
            linked_rid = auto_r.get("review_item_id") or auto_r.get("action_id")

    prop_id = f"P{len(proposals) + 1:04d}"
    prop = {
        "proposal_id": prop_id, "review_item_id": linked_rid,
        "target_id": resolved_tid, "file": resolved_file, "mode": mode,
        "title": title.strip(), "annotation": annotation.strip(), "source_quote": source_quote.strip(),
        "source_ref": source_ref.strip(), "uncertainty": uncertainty.strip(),
        "replacement_text": replacement_text.strip(), "status": "proposed", "decision_reason": "",
        "applied_version": None, "created_at_utc": _utc_now(), "decided_at_utc": None,
    }
    anchor_patterns = _induce_anchor_patterns(manifest.get("authorized_target_ids", []))
    cur_text = (state_dir / "working" / resolved_file).read_text(encoding="utf-8")
    _ = _apply_proposal_to_text(cur_text, prop, manifest["anchor_index"][resolved_tid], anchor_patterns)

    if linked_rid:
        for it in review_items:
            if it.get("action_id") == linked_rid or it.get("review_item_id") == linked_rid:
                it["target_id"] = resolved_tid
                it["suggested_target_id"] = resolved_tid
                it["linked_proposal_id"] = prop_id
                it["proposal_id"] = prop_id
                it["resolution_status"] = "proposed"
        _save_review_items(state_dir, review_items)

    proposals.append(prop)
    _save_state(state_dir, manifest, proposals, history)
    out: dict[str, Any] = {"ok": True, "proposal": prop}
    if anchoring_warning:
        out["warning"] = anchoring_warning
    return out


def cmd_decide(state_dir: Path, *, proposal_id: str, action: str, reason: str = "") -> dict[str, Any]:
    if action not in {"accept", "reject"}:
        raise InvalidPatchError(f"Invalid decision action: {action!r}")
    manifest, proposals, history = _load_state(state_dir)
    prop = next((p for p in proposals if p["proposal_id"] == proposal_id), None)
    if prop is None:
        raise ProposalNotFoundError(f"Proposal {proposal_id!r} does not exist.")
    if prop["status"] != "proposed":
        raise InvalidPatchError(f"Proposal {proposal_id!r} is already {prop['status']!r}.")

    working_dir = state_dir / "working"
    pre_hashes = _collect_dir_hashes(working_dir)

    if action == "reject":
        prop["status"] = "rejected"
        prop["decision_reason"] = reason.strip() or "Rejected during editorial review."
        prop["decided_at_utc"] = _utc_now()
        post_hashes = _collect_dir_hashes(working_dir)
        unchanged = pre_hashes == post_hashes
        if prop.get("review_item_id"):
            ritems = _load_review_items(state_dir)
            for it in ritems:
                if it.get("review_item_id") == prop["review_item_id"] or it.get("action_id") == prop["review_item_id"]:
                    it["resolution_status"] = "unresolved"
                    it["resolution_reason"] = f"Linked proposal {proposal_id} rejected: {prop['decision_reason']}"
            _save_review_items(state_dir, ritems)
        history.append({
            "event": "reject", "proposal_id": proposal_id, "version": manifest["current_version"],
            "parent_version": manifest["current_version"], "timestamp_utc": prop["decided_at_utc"],
            "reason": prop["decision_reason"], "working_bytes_unchanged": unchanged, "file_sha256": post_hashes,
        })
        _save_state(state_dir, manifest, proposals, history)
        return {"ok": True, "action": "reject", "proposal_id": proposal_id, "current_version": manifest["current_version"], "working_bytes_unchanged": unchanged}

    anchor_patterns = _induce_anchor_patterns(manifest.get("authorized_target_ids", []))
    target_file = working_dir / prop["file"]
    cur_text = target_file.read_text(encoding="utf-8")
    new_text = _apply_proposal_to_text(cur_text, prop, manifest["anchor_index"][prop["target_id"]], anchor_patterns)
    has_prior_replace = any(p["status"] == "accepted" and p["file"] == prop["file"] and p["mode"] == "replace_block" for p in proposals)
    if prop["mode"] == "annotate" and not has_prior_replace:
        v0_text = (state_dir / "snapshots" / "v0" / prop["file"]).read_text(encoding="utf-8")
        if strip_editorial_annotations(new_text) != v0_text:
            raise InvalidPatchError("Annotation unexpectedly altered original publication text.")

    _atomic_write_text(target_file, new_text)
    post_hashes = _collect_dir_hashes(working_dir)
    untouched_changed = [rel for rel, h in pre_hashes.items() if rel != prop["file"] and post_hashes.get(rel) != h]
    if untouched_changed:
        raise RevisionError(f"Unrequested chapters changed unexpectedly: {untouched_changed}")

    parent_ver = manifest["current_version"]
    manifest["version_counter"] = int(manifest.get("version_counter", 0)) + 1
    new_ver = f"v{manifest['version_counter']}"
    manifest["current_version"] = new_ver
    _copy_tree(working_dir, state_dir / "snapshots" / new_ver)

    prop["status"] = "accepted"
    prop["decision_reason"] = reason.strip() or "Accepted source-grounded editorial change."
    prop["applied_version"] = new_ver
    prop["decided_at_utc"] = _utc_now()
    if prop.get("review_item_id"):
        ritems = _load_review_items(state_dir)
        for it in ritems:
            if it.get("review_item_id") == prop["review_item_id"] or it.get("action_id") == prop["review_item_id"]:
                it["resolution_status"] = "resolved"
                it["linked_proposal_id"] = proposal_id
                it["proposal_id"] = proposal_id
                it["target_id"] = prop["target_id"]
                it["resolution_reason"] = prop["decision_reason"]
        _save_review_items(state_dir, ritems)
    history.append({
        "event": "accept", "proposal_id": proposal_id, "target_id": prop["target_id"], "file": prop["file"],
        "mode": prop["mode"], "version": new_ver, "parent_version": parent_ver,
        "timestamp_utc": prop["decided_at_utc"], "reason": prop["decision_reason"],
        "original_text_preserved": prop["mode"] == "annotate" and not has_prior_replace, "file_sha256": post_hashes,
    })
    _save_state(state_dir, manifest, proposals, history)
    return {
        "ok": True, "action": "accept", "proposal_id": proposal_id, "mode": prop["mode"],
        "version": new_ver, "file": prop["file"], "file_sha256": post_hashes[prop["file"]],
        "untouched_files_preserved": True,
    }


def _resolve_version(manifest: dict[str, Any], ver: str) -> str:
    return manifest["current_version"] if ver == "latest" else ver


def cmd_diff(
    state_dir: Path,
    *,
    from_version: str = "v0",
    to_version: str = "latest",
    out_diff: Path | None = None,
    out_json: Path | None = None,
) -> dict[str, Any]:
    manifest, proposals, _ = _load_state(state_dir)
    v_from = _resolve_version(manifest, from_version)
    v_to = _resolve_version(manifest, to_version)
    dir_from, dir_to = state_dir / "snapshots" / v_from, state_dir / "snapshots" / v_to
    if not dir_from.is_dir():
        raise VersionNotFoundError(f"Snapshot version {v_from!r} not found.")
    if not dir_to.is_dir():
        raise VersionNotFoundError(f"Snapshot version {v_to!r} not found.")

    hashes_from, hashes_to = _collect_dir_hashes(dir_from), _collect_dir_hashes(dir_to)
    replace_files = {p["file"] for p in proposals if p["status"] == "accepted" and p["mode"] == "replace_block"}
    diff_chunks: list[str] = []
    changed_files: list[str] = []
    unchanged_files: list[str] = []
    original_preserved_all = True
    annotate_only_preserved = True

    for rel in sorted(hashes_from.keys() | hashes_to.keys()):
        if hashes_from.get(rel) == hashes_to.get(rel):
            unchanged_files.append(rel)
            continue
        changed_files.append(rel)
        txt_a = (dir_from / rel).read_text(encoding="utf-8") if (dir_from / rel).is_file() else ""
        txt_b = (dir_to / rel).read_text(encoding="utf-8") if (dir_to / rel).is_file() else ""
        if v_from == "v0" and strip_editorial_annotations(txt_b) != txt_a:
            original_preserved_all = False
            if rel not in replace_files:
                annotate_only_preserved = False
        ud = difflib.unified_diff(
            txt_a.splitlines(keepends=True), txt_b.splitlines(keepends=True),
            fromfile=f"{v_from}/{rel}", tofile=f"{v_to}/{rel}", n=3,
        )
        diff_chunks.append("".join(ud))

    full_diff = "\n".join(c for c in diff_chunks if c)
    if out_diff:
        _atomic_write_text(out_diff, full_diff)
    summary = {
        "ok": True, "from_version": v_from, "to_version": v_to,
        "changed_files": changed_files, "unchanged_files": unchanged_files,
        "original_blocks_preserved": original_preserved_all,
        "annotate_only_files_preserved": annotate_only_preserved,
        "authorized_replace_files": sorted(replace_files),
        "diff_lines": len(full_diff.splitlines()), "diff_text": full_diff,
    }
    if out_json:
        _atomic_write_json(out_json, {k: v for k, v in summary.items() if k != "diff_text"})
    return summary


def cmd_rollback(state_dir: Path, *, to_version: str = "v0", out_json: Path | None = None) -> dict[str, Any]:
    manifest, proposals, history = _load_state(state_dir)
    target_snap = state_dir / "snapshots" / to_version
    if not target_snap.is_dir():
        raise VersionNotFoundError(f"Cannot rollback: version {to_version!r} does not exist.")
    parent_ver = manifest["current_version"]
    expected_hashes = _collect_dir_hashes(target_snap)
    working_dir = state_dir / "working"
    _copy_tree(target_snap, working_dir)
    actual_hashes = _collect_dir_hashes(working_dir)
    restored_exact = actual_hashes == expected_hashes

    manifest["version_counter"] = int(manifest.get("version_counter", 0)) + 1
    new_ver = f"v{manifest['version_counter']}"
    manifest["current_version"] = new_ver
    _copy_tree(working_dir, state_dir / "snapshots" / new_ver)

    receipt = {
        "ok": restored_exact, "event": "rollback", "rolled_back_from": parent_ver,
        "restored_target_version": to_version, "new_version": new_ver,
        "timestamp_utc": _utc_now(), "sha256_restored_100pct": restored_exact, "file_sha256": actual_hashes,
    }
    history.append(receipt)
    _save_state(state_dir, manifest, proposals, history)
    if out_json:
        _atomic_write_json(out_json, receipt)
    return receipt


def cmd_reapply(
    state_dir: Path,
    *,
    proposal_ids: list[str] | None = None,
    from_accepted: bool = False,
    out_json: Path | None = None,
) -> dict[str, Any]:
    manifest, proposals, history = _load_state(state_dir)
    if from_accepted:
        selected = [p for p in proposals if p["status"] == "accepted"]
    else:
        wanted = set(proposal_ids or [])
        selected = [p for p in proposals if p["proposal_id"] in wanted]
        if len(selected) != len(wanted):
            missing = sorted(wanted - {p["proposal_id"] for p in selected})
            raise ProposalNotFoundError(f"Cannot reapply missing proposal(s): {missing}")
    if not selected:
        raise InvalidPatchError("No proposals selected for reapplication.")

    prev_accept_hashes = next((ev.get("file_sha256") for ev in reversed(history) if ev.get("event") == "accept"), None)
    working_dir = state_dir / "working"
    _copy_tree(state_dir / "snapshots" / "v0", working_dir)
    anchor_patterns = _induce_anchor_patterns(manifest.get("authorized_target_ids", []))

    applied_ids: list[str] = []
    for prop in selected:
        fp = working_dir / prop["file"]
        new_text = _apply_proposal_to_text(fp.read_text(encoding="utf-8"), prop, manifest["anchor_index"][prop["target_id"]], anchor_patterns)
        _atomic_write_text(fp, new_text)
        applied_ids.append(prop["proposal_id"])

    actual_hashes = _collect_dir_hashes(working_dir)
    parent_ver = manifest["current_version"]
    manifest["version_counter"] = int(manifest.get("version_counter", 0)) + 1
    new_ver = f"v{manifest['version_counter']}"
    manifest["current_version"] = new_ver
    _copy_tree(working_dir, state_dir / "snapshots" / new_ver)

    matches_prior = (actual_hashes == prev_accept_hashes) if prev_accept_hashes is not None else None
    receipt = {
        "ok": True if matches_prior is None else bool(matches_prior),
        "event": "reapply", "parent_version": parent_ver, "new_version": new_ver,
        "reapplied_proposals": applied_ids, "timestamp_utc": _utc_now(),
        "matches_pre_rollback_sha256": matches_prior, "file_sha256": actual_hashes,
    }
    history.append(receipt)
    _save_state(state_dir, manifest, proposals, history)
    if out_json:
        _atomic_write_json(out_json, receipt)
    return receipt


def _state_fingerprint(state_dir: Path) -> dict[str, Any]:
    return {
        "working_sha256": _collect_dir_hashes(state_dir / "working"),
        "manifest_sha256": _sha256_file(state_dir / "manifest.json"),
        "proposals_sha256": _sha256_file(state_dir / "proposals.json"),
    }


def cmd_fault_test(state_dir: Path, *, out_json: Path | None = None) -> dict[str, Any]:
    manifest, _, history = _load_state(state_dir)
    before_fp = _state_fingerprint(state_dir)
    auth_ids = set(manifest.get("authorized_target_ids", []))
    auth_files = set(manifest.get("authorized_files", []))
    unauth_anchor = next(
        (aid for aid, meta in manifest["anchor_index"].items()
         if (auth_ids and aid not in auth_ids) or (not auth_ids and auth_files and meta["file"] not in auth_files)),
        None,
    )
    cases: list[dict[str, Any]] = []

    def _run_case(case_id: str, desc: str, fn) -> None:
        pre = _state_fingerprint(state_dir)
        caught_type = caught_code = None
        caught_msg = ""
        try:
            fn()
        except RevisionError as exc:
            caught_type, caught_code, caught_msg = type(exc).__name__, exc.error_code, str(exc)
        unchanged = pre == _state_fingerprint(state_dir)
        cases.append({
            "fault_case_id": case_id, "category": "deliberate_fault_injection", "is_platform_failure": False,
            "description": desc, "exception_raised": caught_type is not None, "exception_type": caught_type,
            "error_code": caught_code, "message": caught_msg, "state_unchanged": unchanged,
            "passed": (caught_type is not None) and unchanged,
        })

    _run_case("F01_OUT_OF_RANGE_TARGET", "Propose against non-existent anchor ZZ99-P999.",
              lambda: cmd_propose(state_dir, target_id="ZZ99-P999", title="Invalid", annotation="No", source_quote="N/A", source_ref="N/A"))
    if unauth_anchor:
        _run_case("F02_UNAUTHORIZED_SCOPE", f"Propose against unauthorized anchor {unauth_anchor}.",
                  lambda: cmd_propose(state_dir, target_id=unauth_anchor, title="Scope test", annotation="Block", source_quote="x", source_ref=unauth_anchor))
    else:
        _run_case("F02_UNAUTHORIZED_FILE_PATH", "Propose against external file ../outside_scope.md.",
                  lambda: cmd_propose(state_dir, file_rel="../outside_scope.md", title="Path test", annotation="Block", source_quote="N/A", source_ref="N/A"))
    _run_case("F03_INVALID_PROPOSAL_ID", "Accept non-existent proposal P9999.", lambda: cmd_decide(state_dir, proposal_id="P9999", action="accept"))
    _run_case("F04_INVALID_ROLLBACK_VERSION", "Rollback to non-existent version v999.", lambda: cmd_rollback(state_dir, to_version="v999"))
    first_tid = next(iter(manifest.get("authorized_target_ids", [])), None) or next(iter(manifest["anchor_index"].keys()))
    _run_case("F05_MALFORMED_EMPTY_PATCH", "Submit empty annotation without source quote.",
              lambda: cmd_propose(state_dir, target_id=first_tid, title="Empty", annotation="", source_quote="", source_ref=""))

    after_fp = _state_fingerprint(state_dir)
    all_unchanged = before_fp == after_fp
    all_passed = all_unchanged and all(c["passed"] for c in cases)
    report = {
        "ok": all_passed, "category": "deliberate_fault_injection", "is_platform_failure": False,
        "tested_at_utc": _utc_now(), "total_cases": len(cases),
        "passed_cases": sum(1 for c in cases if c["passed"]),
        "state_unchanged_verified": all_unchanged,
        "pre_test_working_sha256": before_fp["working_sha256"],
        "post_test_working_sha256": after_fp["working_sha256"], "cases": cases,
    }
    history.append({
        "event": "fault_test", "version": manifest["current_version"], "timestamp_utc": report["tested_at_utc"],
        "passed_cases": report["passed_cases"], "total_cases": report["total_cases"], "state_unchanged_verified": all_unchanged,
    })
    _atomic_write_json(state_dir / "history.json", history)
    if out_json:
        _atomic_write_json(out_json, report)
    return report


def cmd_parse_comments(state_dir: Path, *, comments_file: Path, out_json: Path | None = None) -> dict[str, Any]:
    """Decompose advisor/reviewer comments (Markdown/text/JSON) into actionable trackable items."""
    manifest, proposals, _ = _load_state(state_dir)
    if not comments_file.is_file():
        raise RevisionError(f"Comments file not found: {comments_file}")
    raw_txt = comments_file.read_text(encoding="utf-8", errors="replace")
    raw_items: list[str] = []
    if comments_file.suffix.lower() == ".json":
        obj = json.loads(raw_txt)
        seq = obj.get("comments", obj) if isinstance(obj, dict) else obj
        if isinstance(seq, list):
            raw_items = [str(x.get("comment") or x.get("text") or x) if isinstance(x, dict) else str(x) for x in seq]
    else:
        for ln in raw_txt.splitlines():
            st = ln.strip()
            if not st or st.startswith("#"):
                continue
            st = re.sub(r"^(?:[-*•]|\d+[.)、])\s+", "", st).strip()
            if len(st) >= 8:
                raw_items.append(st)

    anchors = manifest["anchor_index"]
    cat_rules = (
        ("terminology", re.compile(r"acronym|terminolog|symbol|abbrev|术语|缩写|符号", re.I)),
        ("numeric_consistency", re.compile(r"number|sample|n\s*=|conflict|unit|value|数值|样本量|单位|矛盾", re.I)),
        ("citation", re.compile(r"cite|citation|reference|zotero|bib|引用|文献", re.I)),
        ("duplication_structure", re.compile(r"duplicat|repeat|chapter|section|plan|重复|章节|结构", re.I)),
    )
    OUT_OF_SCOPE_RE = re.compile(
        r"\b(?:new\s+experiment|additional\s+experiment|run\s+a\s+new\s+(?:trial|assay|cohort)|collect\s+new\s+data|"
        r"wet[\s-]*lab|recruit\s+more\s+patients|external\s+validation\s+cohort\s+not\s+provided|fabricat|invent\s+data)\b|"
        r"补做(?:新)?实验|补充(?:新)?实验|新招募|新增实验数据|未提供(?:的新)?实验|超出工单范围",
        re.I,
    )
    auth_ids = set(manifest.get("authorized_target_ids", []))
    auth_files = set(manifest.get("authorized_files", []))
    wo_present = bool(manifest.get("workorder", {}).get("present"))

    items_out: list[dict[str, Any]] = []
    for idx, ctext in enumerate(raw_items, 1):
        rid = f"R{idx:03d}"
        category = next((c for c, p in cat_rules if p.search(ctext)), "substantive_revision")
        severity = "major" if re.search(r"must|error|conflict|wrong|missing|contradict|必须|错误|矛盾|缺失", ctext, re.I) else "minor"
        matched_anchor = next((aid for aid in anchors if aid in ctext), None)
        if not matched_anchor:
            cwords = {w.lower() for w in re.findall(r"[A-Za-z]{4,}|[\u4e00-\u9fff]{2,}", ctext)}
            best_score, best_aid = 0, None
            for aid, meta in anchors.items():
                if _is_running_header_line(meta.get("preview", "")):
                    continue
                pwords = {w.lower() for w in re.findall(r"[A-Za-z]{4,}|[\u4e00-\u9fff]{2,}", meta["preview"])}
                sc = len(cwords & pwords)
                if sc > best_score:
                    best_score, best_aid = sc, aid
            matched_anchor = best_aid if best_score >= 2 else None
        matched_file = anchors[matched_anchor]["file"] if matched_anchor else None
        linked_prop_obj = next(
            (p for p in proposals if (p.get("review_item_id") == rid) or (matched_anchor and p["target_id"] == matched_anchor)),
            None,
        )
        linked_prop = linked_prop_obj["proposal_id"] if linked_prop_obj else None

        # Explicit triage: resolved / proposed / out_of_scope / unresolved (never leave unactionable items blindly "open")
        resolution_reason = ""
        if OUT_OF_SCOPE_RE.search(ctext):
            status = "out_of_scope"
            resolution_reason = "Requires new empirical/experimental data or out-of-scope work not supplied in /input."
        elif wo_present and matched_anchor and (
            (auth_ids and matched_anchor not in auth_ids) or (not auth_ids and auth_files and matched_file not in auth_files)
        ):
            status = "out_of_scope"
            resolution_reason = f"Target {matched_anchor} ({matched_file}) is outside authorized workorder scope."
        elif linked_prop_obj and linked_prop_obj.get("status") == "accepted":
            status = "resolved"
            resolution_reason = f"Closed by accepted proposal {linked_prop}."
        elif linked_prop_obj and linked_prop_obj.get("status") == "proposed":
            status = "proposed"
            resolution_reason = f"Pending editorial decision on proposal {linked_prop}."
        else:
            status = "unresolved"
            resolution_reason = (
                "Matched anchor awaiting grounded proposal." if matched_anchor else "No grounded anchor matched in supplied chapters; requires author triage."
            )

        items_out.append({
            "review_item_id": rid,
            "action_id": rid,
            "category": category,
            "severity": severity,
            "comment_text": ctext,
            "target_id": matched_anchor,
            "suggested_target_id": matched_anchor,
            "suggested_file": matched_file,
            "proposal_id": linked_prop,
            "linked_proposal_id": linked_prop,
            "export_locator": None,
            "resolution_status": status,
            "resolution_reason": resolution_reason,
        })
    _save_review_items(state_dir, items_out)
    status_counts = {
        k: sum(1 for it in items_out if it["resolution_status"] == k)
        for k in ("resolved", "proposed", "unresolved", "out_of_scope")
    }
    res = {
        "ok": True,
        "source_comments_file": comments_file.name,
        "total_items": len(items_out),
        "status_counts": status_counts,
        "action_items": items_out,
    }
    if out_json:
        _atomic_write_json(out_json, res)
    return res


def _render_html_preview(manifest: dict[str, Any], proposals: list[dict[str, Any]], history: list[dict[str, Any]], diff_summary: dict[str, Any]) -> str:
    esc = html.escape
    accepted = [p for p in proposals if p["status"] == "accepted"]
    rejected = [p for p in proposals if p["status"] == "rejected"]
    rollback_ok = any(h.get("event") == "rollback" and h.get("sha256_restored_100pct") for h in history)
    reapply_ok = any(h.get("event") == "reapply" and h.get("ok") for h in history)
    fault_ok = any(h.get("event") == "fault_test" and h.get("state_unchanged_verified") for h in history)

    prop_rows = []
    for p in proposals:
        badge_cls = "ok" if p["status"] == "accepted" else ("rej" if p["status"] == "rejected" else "pend")
        body_txt = p["replacement_text"] if p["mode"] == "replace_block" else p["annotation"]
        prop_rows.append(
            f"<tr><td><code>{esc(p['proposal_id'])}</code><br><small>{esc(p['mode'])}</small></td>"
            f"<td><code>{esc(p['target_id'])}</code><br><small>{esc(p['file'])}</small></td>"
            f"<td><span class='badge {badge_cls}'>{esc(p['status'])}</span></td>"
            f"<td><strong>{esc(p['title'])}</strong><br>{esc(body_txt[:360])}</td>"
            f"<td><em>\"{esc(p['source_quote'][:220])}\"</em><br><small>{esc(p['source_ref'])}</small></td>"
            f"<td>{esc(p.get('decision_reason') or '')}<br><small>{esc(p.get('uncertainty') or '')}</small></td></tr>"
        )
    diff_lines_html = []
    for ln in diff_summary.get("diff_text", "").splitlines()[:600]:
        cls = "d-hdr" if ln.startswith(("+++", "---")) else ("d-hunk" if ln.startswith("@@") else ("d-add" if ln.startswith("+") else ("d-del" if ln.startswith("-") else "d-ctx")))
        diff_lines_html.append(f"<div class='{cls}'>{esc(ln)}</div>")
    hist_rows = [
        f"<tr><td>{i}</td><td><code>{esc(str(h.get('event','')))}</code></td><td><code>{esc(str(h.get('version') or h.get('new_version') or ''))}</code></td>"
        f"<td>{esc(str(h.get('timestamp_utc','')))}</td><td>{esc(str(h.get('proposal_id') or h.get('notes') or h.get('restored_target_version') or ''))}</td></tr>"
        for i, h in enumerate(history, 1)
    ]
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Long-Document Revision Export Preview</title><style>"
        "body{margin:0;font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a;line-height:1.5}"
        ".wrap{max-width:1120px;margin:24px auto;padding:0 16px}.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}"
        ".kpi,.card{background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;overflow-x:auto}"
        ".kpi .lbl{font-size:.78rem;color:#475569;text-transform:uppercase}.kpi .val{font-size:1.2rem;font-weight:700;margin-top:4px}"
        "table{width:100%;border-collapse:collapse;font-size:.86rem}th,td{border-bottom:1px solid #e2e8f0;padding:8px;text-align:left;vertical-align:top}"
        "th{background:#f1f5f9}.badge{padding:2px 8px;border-radius:12px;font-size:.78rem;font-weight:600}.badge.ok{background:#dcfce7;color:#15803d}"
        ".badge.rej{background:#fee2e2;color:#b91c1c}.badge.pend{background:#fef9c3;color:#854d0e}"
        ".diff{font-family:monospace;font-size:.82rem;white-space:pre-wrap;word-break:break-word}"
        ".d-hdr{background:#e2e8f0;font-weight:700;padding:2px 6px}.d-hunk{background:#dbeafe;color:#1e3a8a;padding:2px 6px}"
        ".d-add{background:#dcfce7;color:#14532d;padding:1px 6px}.d-del{background:#fee2e2;color:#7f1d1d;padding:1px 6px}.d-ctx{padding:1px 6px;color:#334155}"
        "</style></head><body><div class='wrap'><h1>Long-Document Local Revision Export Preview</h1>"
        f"<p style='color:#475569'>Workorder: <code>{esc(str(manifest['workorder'].get('workorder_id') or 'none'))}</code> | Version: <code>{esc(manifest['current_version'])}</code> | Scope: <code>{esc(manifest.get('scope_enforcement_status',''))}</code> | Offline Zero-CDN</p>"
        f"<div class='kpi-grid'><div class='kpi'><div class='lbl'>Chapters</div><div class='val'>{len(manifest['initial_sha256'])}</div></div>"
        f"<div class='kpi'><div class='lbl'>Accepted / Rejected</div><div class='val'>{len(accepted)} / {len(rejected)}</div></div>"
        f"<div class='kpi'><div class='lbl'>Annotate Scope Intact</div><div class='val'>{'YES' if diff_summary['annotate_only_files_preserved'] else 'CHECK'}</div></div>"
        f"<div class='kpi'><div class='lbl'>Rollback &amp; Reapply</div><div class='val'>{'VERIFIED' if (rollback_ok and reapply_ok) else 'PARTIAL'}</div></div>"
        f"<div class='kpi'><div class='lbl'>Fault Injection</div><div class='val'>{'VERIFIED' if fault_ok else 'NOT RUN'}</div></div></div>"
        "<h2>1. Proposal Decisions &amp; Source Grounding</h2><div class='card'><table><thead><tr><th>ID / Mode</th><th>Target</th><th>Decision</th><th>Change Body</th><th>Source Quote</th><th>Rationale &amp; Uncertainty</th></tr></thead>"
        f"<tbody>{''.join(prop_rows) or '<tr><td colspan=6>No proposals.</td></tr>'}</tbody></table></div>"
        f"<h2>2. Unified Diff (v0 &rarr; {esc(manifest['current_version'])})</h2><div class='card diff'>{''.join(diff_lines_html) or '<div class=d-ctx>No differences.</div>'}</div>"
        "<h2>3. Version &amp; Audit Chain</h2><div class='card'><table><thead><tr><th>#</th><th>Event</th><th>Version</th><th>UTC</th><th>Detail</th></tr></thead>"
        f"<tbody>{''.join(hist_rows)}</tbody></table></div></div></body></html>"
    )


def _compute_export_locator(out_dir: Path, prop: dict[str, Any], anchor_meta: dict[str, Any]) -> str:
    rel = prop["file"]
    fp = out_dir / rel
    if not fp.is_file():
        return f"{rel}:L{anchor_meta.get('start_line', 1)}-L{anchor_meta.get('end_line', 1)}"
    txt = fp.read_text(encoding="utf-8", errors="replace")
    if prop["mode"] == "annotate":
        marker = f"[Editorial Annotation | ID: {prop['proposal_id']} |"
        pos = txt.find(marker)
        if pos != -1:
            end_marker = f"[/Editorial Annotation | ID: {prop['proposal_id']}]"
            end_pos = txt.find(end_marker, pos)
            s_ln = txt.count("\n", 0, pos) + 1
            e_ln = txt.count("\n", 0, end_pos if end_pos != -1 else pos) + 1
            return f"{rel}:L{s_ln}-L{e_ln}"
    elif prop["mode"] == "replace_block":
        rep = prop.get("replacement_text", "").strip()
        if rep:
            pos = txt.find(rep[:80])
            if pos != -1:
                s_ln = txt.count("\n", 0, pos) + 1
                e_ln = txt.count("\n", 0, pos + len(rep)) + 1
                return f"{rel}:L{s_ln}-L{e_ln}"
    return f"{rel}:L{anchor_meta.get('start_line', 1)}-L{anchor_meta.get('end_line', 1)}"


def cmd_export(
    state_dir: Path, *, out_dir: Path, records_out: Path | None = None,
    evidence_out: Path | None = None, html_preview: Path | None = None,
    audit_json: Path | None = None, result_json: Path | None = None,
) -> dict[str, Any]:
    manifest, proposals, history = _load_state(state_dir)
    review_items = _load_review_items(state_dir)
    _copy_tree(state_dir / "working", out_dir)
    exported_hashes = _collect_dir_hashes(out_dir)
    diff_summary = cmd_diff(state_dir, from_version="v0", to_version="latest")

    # Compute 4-key closed loop: review_item_id <-> target_id <-> proposal_id <-> export_locator
    prop_by_id = {p["proposal_id"]: p for p in proposals}
    for p in proposals:
        ameta = manifest["anchor_index"].get(p["target_id"], {})
        p["export_locator"] = _compute_export_locator(out_dir, p, ameta) if p["status"] == "accepted" else None

    four_key_traceability: list[dict[str, Any]] = []
    seen_pids: set[str] = set()
    for it in review_items:
        pid = it.get("proposal_id") or it.get("linked_proposal_id")
        prop = prop_by_id.get(pid) if pid else None
        if prop:
            seen_pids.add(pid)
            it["export_locator"] = prop.get("export_locator")
            if prop["status"] == "accepted":
                it["resolution_status"] = "resolved"
        four_key_traceability.append({
            "review_item_id": it.get("review_item_id") or it.get("action_id"),
            "target_id": it.get("target_id") or it.get("suggested_target_id"),
            "proposal_id": pid,
            "export_locator": it.get("export_locator"),
            "resolution_status": it.get("resolution_status", "unresolved"),
            "resolution_reason": it.get("resolution_reason", ""),
        })
    for p in proposals:
        if p["proposal_id"] not in seen_pids:
            four_key_traceability.append({
                "review_item_id": p.get("review_item_id"),
                "target_id": p["target_id"],
                "proposal_id": p["proposal_id"],
                "export_locator": p.get("export_locator"),
                "resolution_status": "resolved" if p["status"] == "accepted" else ("rejected" if p["status"] == "rejected" else "proposed"),
                "resolution_reason": p.get("decision_reason", ""),
            })
    _save_review_items(state_dir, review_items)
    _save_state(state_dir, manifest, proposals, history)

    evidence_map = [
        {k: p.get(k) for k in ("proposal_id", "review_item_id", "mode", "status", "target_id", "file", "export_locator", "title", "source_ref", "source_quote", "uncertainty", "decision_reason")}
        for p in proposals
    ]

    # Collect ALL audit gaps, coverage/truncation risks, format caveats, and unresolved/out_of_scope review items
    merged_gaps: list[dict[str, Any]] = []
    merged_warnings: list[str] = []
    cov = manifest.get("coverage", {})
    for mc in cov.get("missing_chapters", []):
        merged_gaps.append({"type": "missing_chapter", "detail": mc, "status": "unresolved"})
    for tr in cov.get("truncation_risks", []):
        merged_gaps.append({"type": "truncation_risk", "file": tr.get("file"), "detail": tr.get("reason"), "status": "unresolved"})
    fmt_decl = manifest.get("format_declaration", {
        "format_mode": "native_text_in_place",
        "citation_fields_preserved": True,
        "live_citation_fields_detected": 0,
    })
    if fmt_decl.get("format_mode") == "derived_markdown_readonly_source":
        msg = (
            f"Derived Markdown export from read-only binary source: citation_fields_preserved=false "
            f"(live_citation_fields_detected={fmt_decl.get('live_citation_fields_detected', 0)})."
        )
        merged_warnings.append(msg)
        if fmt_decl.get("live_citation_fields_detected", 0) > 0:
            merged_gaps.append({"type": "citation_fields_not_preserved_in_derived_markdown", "detail": msg, "status": "requires_native_ooxml_or_manual_relink"})

    for it in review_items:
        st = it.get("resolution_status", "unresolved")
        if st in {"unresolved", "out_of_scope"}:
            merged_gaps.append({
                "type": f"review_item_{st}",
                "review_item_id": it.get("review_item_id") or it.get("action_id"),
                "target_id": it.get("target_id"),
                "detail": it.get("resolution_reason") or it.get("comment_text", "")[:140],
                "status": st,
            })

    if audit_json and audit_json.is_file():
        audit_doc = json.loads(audit_json.read_text(encoding="utf-8"))
        for err in audit_doc.get("errors", []):
            merged_gaps.append({"type": "consistency_audit_error", "detail": str(err), "status": "unresolved"})
        for warn in audit_doc.get("warnings", []):
            merged_warnings.append(str(warn))

    # Auto-merge gaps, warnings, four_key_traceability, and format_declaration into result.json if supplied!
    result_synced = False
    if result_json:
        existing: dict[str, Any] = {}
        if result_json.is_file():
            try:
                existing = json.loads(result_json.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        prev_gaps = existing.get("gaps") if isinstance(existing.get("gaps"), list) else []
        prev_warns = existing.get("warnings") if isinstance(existing.get("warnings"), list) else []
        seen_gap_keys = {json.dumps(g, sort_keys=True, ensure_ascii=False) for g in prev_gaps}
        for g in merged_gaps:
            k = json.dumps(g, sort_keys=True, ensure_ascii=False)
            if k not in seen_gap_keys:
                prev_gaps.append(g)
                seen_gap_keys.add(k)
        for w in merged_warnings:
            if w not in prev_warns:
                prev_warns.append(w)
        existing["gaps"] = prev_gaps
        existing["warnings"] = prev_warns
        existing["four_key_traceability"] = four_key_traceability
        existing["format_mode"] = fmt_decl.get("format_mode")
        existing["citation_fields_preserved"] = fmt_decl.get("citation_fields_preserved")
        existing["live_citation_fields_detected"] = fmt_decl.get("live_citation_fields_detected", 0)
        _atomic_write_json(result_json, existing)
        result_synced = True

    records = {
        "ok": True, "exported_at_utc": _utc_now(), "current_version": manifest["current_version"],
        "scope_enforcement_status": manifest.get("scope_enforcement_status"), "workorder": manifest["workorder"],
        "format_mode": fmt_decl.get("format_mode"),
        "citation_fields_preserved": fmt_decl.get("citation_fields_preserved"),
        "live_citation_fields_detected": fmt_decl.get("live_citation_fields_detected", 0),
        "format_declaration": fmt_decl,
        "initial_sha256": manifest["initial_sha256"], "exported_sha256": exported_hashes,
        "original_blocks_preserved": diff_summary["original_blocks_preserved"],
        "annotate_only_files_preserved": diff_summary["annotate_only_files_preserved"],
        "authorized_replace_files": diff_summary["authorized_replace_files"],
        "changed_files": diff_summary["changed_files"], "unchanged_files": diff_summary["unchanged_files"],
        "four_key_traceability": four_key_traceability,
        "review_items": review_items,
        "merged_gaps": merged_gaps,
        "merged_warnings": merged_warnings,
        "result_json_synced": result_synced,
        "proposals": proposals, "version_history": history, "coverage_manifest": cov,
    }
    if records_out:
        _atomic_write_json(records_out, records)
    if evidence_out:
        _atomic_write_json(evidence_out, {"evidence_map": evidence_map, "four_key_traceability": four_key_traceability})
    if html_preview:
        _atomic_write_text(html_preview, _render_html_preview(manifest, proposals, history, diff_summary))
    return {
        "ok": True, "exported_dir": str(out_dir), "exported_files": len(exported_hashes),
        "changed_files": diff_summary["changed_files"], "unchanged_files": diff_summary["unchanged_files"],
        "original_blocks_preserved": diff_summary["original_blocks_preserved"],
        "annotate_only_files_preserved": diff_summary["annotate_only_files_preserved"],
        "format_mode": fmt_decl.get("format_mode"),
        "citation_fields_preserved": fmt_decl.get("citation_fields_preserved"),
        "live_citation_fields_detected": fmt_decl.get("live_citation_fields_detected", 0),
        "four_key_links_count": len(four_key_traceability),
        "merged_gaps_count": len(merged_gaps),
        "result_json_synced": result_synced,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local controllable revision state machine for long documents.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_imp = sub.add_parser("import", help="Import chapters (.md/.txt/.tex/.docx/.pdf), hash v0, induce anchor patterns.")
    p_imp.add_argument("--chapters-dir", required=True, type=Path)
    p_imp.add_argument("--state-dir", required=True, type=Path)
    p_imp.add_argument("--workorder", type=Path, default=None)
    p_imp.add_argument("--toc-json", type=Path, default=None)
    p_imp.add_argument("--receipt-out", type=Path, default=None)
    p_imp.add_argument("--allow-file-fallback", action="store_true", help="Degrade to file-level warning if target_ids missing but file scope exists.")

    p_prop = sub.add_parser("propose", help="Create a source-grounded editorial annotation or substantive replace proposal.")
    p_prop.add_argument("--state-dir", required=True, type=Path)
    p_prop.add_argument("--target-id", default=None)
    p_prop.add_argument("--file", default=None)
    p_prop.add_argument("--block-index", type=int, default=None)
    p_prop.add_argument("--anchor-query", default="")
    p_prop.add_argument("--mode", default="annotate", choices=["annotate", "replace", "replace_block"])
    p_prop.add_argument("--title", required=True)
    p_prop.add_argument("--annotation", default="")
    p_prop.add_argument("--annotation-file", type=Path, default=None)
    p_prop.add_argument("--source-quote", required=True)
    p_prop.add_argument("--source-ref", required=True)
    p_prop.add_argument("--uncertainty", default="")
    p_prop.add_argument("--replacement-text", default="")
    p_prop.add_argument("--replacement-file", type=Path, default=None)
    p_prop.add_argument("--review-item-id", default="", help="Optional review item ID (e.g. R001) to bind 4-key traceability.")

    for act in ("accept", "reject"):
        p_dec = sub.add_parser(act, help=f"{act.capitalize()} an individual proposal.")
        p_dec.add_argument("--state-dir", required=True, type=Path)
        p_dec.add_argument("--proposal-id", required=True)
        p_dec.add_argument("--reason", default="")

    p_diff = sub.add_parser("diff", help="Compute unified diff and original-text preservation check.")
    p_diff.add_argument("--state-dir", required=True, type=Path)
    p_diff.add_argument("--from-version", default="v0")
    p_diff.add_argument("--to-version", default="latest")
    p_diff.add_argument("--out-diff", type=Path, default=None)
    p_diff.add_argument("--out-json", type=Path, default=None)

    p_rb = sub.add_parser("rollback", help="Rollback working directory to a prior version and verify SHA-256.")
    p_rb.add_argument("--state-dir", required=True, type=Path)
    p_rb.add_argument("--to-version", default="v0")
    p_rb.add_argument("--out-json", type=Path, default=None)

    p_re = sub.add_parser("reapply", help="Reapply accepted or specified proposals cleanly from v0.")
    p_re.add_argument("--state-dir", required=True, type=Path)
    p_re.add_argument("--from-accepted", action="store_true")
    p_re.add_argument("--proposal-ids", default="")
    p_re.add_argument("--out-json", type=Path, default=None)

    p_ft = sub.add_parser("fault-test", help="Run controlled invalid-operation tests and verify zero state mutation.")
    p_ft.add_argument("--state-dir", required=True, type=Path)
    p_ft.add_argument("--out-json", type=Path, default=None)

    p_pc = sub.add_parser("parse-comments", help="Decompose advisor/reviewer feedback into actionable edit items.")
    p_pc.add_argument("--state-dir", required=True, type=Path)
    p_pc.add_argument("--comments-file", required=True, type=Path)
    p_pc.add_argument("--out-json", type=Path, default=None)

    p_exp = sub.add_parser("export", help="Export editable chapters, 4-key records, evidence map, offline HTML preview, and result.json gap sync.")
    p_exp.add_argument("--state-dir", required=True, type=Path)
    p_exp.add_argument("--out-dir", required=True, type=Path)
    p_exp.add_argument("--records-out", type=Path, default=None)
    p_exp.add_argument("--evidence-out", type=Path, default=None)
    p_exp.add_argument("--html-preview", type=Path, default=None)
    p_exp.add_argument("--audit-json", type=Path, default=None, help="Optional longdoc_audit.json whose errors/warnings auto-merge into gaps.")
    p_exp.add_argument("--result-json", type=Path, default=None, help="Optional result.json to auto-merge audit gaps, review statuses, and 4-key links.")

    p_docx = sub.add_parser("patch-docx", help="Targeted OOXML plain-text paragraph patch preserving untouched ZIP members and w:instrText citation fields.")
    p_docx.add_argument("--src-docx", required=True, type=Path)
    p_docx.add_argument("--dst-docx", required=True, type=Path)
    p_docx.add_argument("--match-substring", required=True)
    p_docx.add_argument("--replacement-substring", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            res = cmd_import(
                args.chapters_dir, args.state_dir, workorder_path=args.workorder,
                toc_path=args.toc_json, receipt_out=args.receipt_out, allow_file_fallback=args.allow_file_fallback,
            )
        elif args.command == "propose":
            ann_txt = args.annotation_file.read_text(encoding="utf-8") if args.annotation_file else args.annotation
            rep_txt = args.replacement_file.read_text(encoding="utf-8") if args.replacement_file else args.replacement_text
            res = cmd_propose(
                args.state_dir, target_id=args.target_id, file_rel=args.file,
                block_index=args.block_index, anchor_query=args.anchor_query, mode=args.mode,
                title=args.title, annotation=ann_txt, source_quote=args.source_quote,
                source_ref=args.source_ref, uncertainty=args.uncertainty, replacement_text=rep_txt,
                review_item_id=args.review_item_id,
            )
        elif args.command in {"accept", "reject"}:
            res = cmd_decide(args.state_dir, proposal_id=args.proposal_id, action=args.command, reason=args.reason)
        elif args.command == "diff":
            res = cmd_diff(args.state_dir, from_version=args.from_version, to_version=args.to_version, out_diff=args.out_diff, out_json=args.out_json)
            res = {k: v for k, v in res.items() if k != "diff_text"}
        elif args.command == "rollback":
            res = cmd_rollback(args.state_dir, to_version=args.to_version, out_json=args.out_json)
        elif args.command == "reapply":
            pids = [x.strip() for x in args.proposal_ids.split(",") if x.strip()]
            res = cmd_reapply(args.state_dir, proposal_ids=pids, from_accepted=args.from_accepted, out_json=args.out_json)
        elif args.command == "fault-test":
            res = cmd_fault_test(args.state_dir, out_json=args.out_json)
        elif args.command == "parse-comments":
            res = cmd_parse_comments(args.state_dir, comments_file=args.comments_file, out_json=args.out_json)
        elif args.command == "export":
            res = cmd_export(
                args.state_dir, out_dir=args.out_dir, records_out=args.records_out,
                evidence_out=args.evidence_out, html_preview=args.html_preview,
                audit_json=args.audit_json, result_json=args.result_json,
            )
        elif args.command == "patch-docx":
            res = patch_docx_plain_paragraph(
                args.src_docx, args.dst_docx,
                match_substring=args.match_substring, replacement_substring=args.replacement_substring,
            )
        else:
            raise RevisionError(f"Unknown command: {args.command}")
    except RevisionError as exc:
        print(json.dumps({"ok": False, "error_code": exc.error_code, "message": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
