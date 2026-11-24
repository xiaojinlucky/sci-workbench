#!/usr/bin/env python3
"""Offline local revision state machine for long documents and thesis chapters.

Implements the complete organizer-edit-workorder lifecycle using Python 3.12 stdlib only:
  - import: byte-exact SHA-256 snapshot, paragraph anchor index, coverage & truncation receipt
  - propose: target-scoped editorial annotation or block proposal with source quote & uncertainty
  - accept / reject: individual per-proposal decision preserving all unrequested bytes
  - diff: unified diff & original-block preservation check between any two versions
  - rollback: byte-verified restoration to a prior snapshot (e.g., v0)
  - reapply: deterministic reapplication of accepted proposals with SHA-256 parity check
  - fault-test: controlled invalid-operation injection proving 100% zero state/file mutation
  - export: editable chapters, structured revision records, evidence map, offline HTML preview
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
import sys
from pathlib import Path, PurePosixPath
from typing import Any

CHAPTER_EXTS = {".md", ".txt", ".tex"}
ANCHOR_PATTERNS = [
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
    """Base error for controlled revision state machine exceptions."""

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
    """Remove tagged editorial annotation blocks to recover original manuscript text."""
    return ANNOTATION_BLOCK_RE.sub("", text)


def parse_workorder(workorder_path: Path | None) -> dict[str, Any]:
    """Parse organizer-edit-workorder.json or case.json containing organizer_edit_workorder."""
    if not workorder_path or not workorder_path.is_file():
        return {
            "present": False,
            "workorder_id": None,
            "schema_mode": "none",
            "target_ids": [],
            "target_files": [],
            "operation_requirements": [],
            "raw_source_path": None,
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


def _extract_anchor_id(block_text: str) -> str | None:
    first_lines = "\n".join(block_text.splitlines()[:3])
    for pat in ANCHOR_PATTERNS:
        m = pat.search(first_lines)
        if m:
            return m.group(1).strip()
    return None


def _split_blocks_with_offsets(text: str) -> list[dict[str, Any]]:
    """Split text into paragraph blocks while preserving exact character offsets."""
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
                    "block_index": idx,
                    "start": pos,
                    "end": end,
                    "start_line": text.count("\n", 0, pos) + 1,
                    "end_line": text.count("\n", 0, end) + 1,
                    "text": chunk,
                })
        pos = m.end()
    if pos < length:
        chunk = text[pos:length]
        if chunk.strip():
            idx += 1
            blocks.append({
                "block_index": idx,
                "start": pos,
                "end": length,
                "start_line": text.count("\n", 0, pos) + 1,
                "end_line": text.count("\n", 0, length) + 1,
                "text": chunk,
            })
    return blocks


def _scan_chapter_files(chapters_dir: Path) -> list[Path]:
    return sorted(
        p for p in chapters_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in CHAPTER_EXTS
    )


def _detect_coverage_and_truncation(
    chapters_dir: Path,
    files: list[Path],
    toc_path: Path | None = None,
) -> dict[str, Any]:
    chapter_nums: list[int] = []
    truncation_risks: list[dict[str, Any]] = []
    chapter_inventory: list[dict[str, Any]] = []

    for fp in files:
        rel = fp.relative_to(chapters_dir).as_posix()
        raw = fp.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        chapter_inventory.append({
            "file": rel,
            "bytes": len(raw),
            "lines": len(lines),
            "chars": len(text),
            "sha256": _sha256_bytes(raw),
        })
        m_num = CHAPTER_NUM_RE.search(rel)
        if m_num:
            chapter_nums.append(int(m_num.group(1)))

        if TRUNCATION_MARKER_RE.search(text):
            truncation_risks.append({
                "file": rel,
                "reason": "explicit_truncation_or_missing_page_marker",
                "excerpt": text.strip()[-120:],
            })
        else:
            stripped = text.rstrip()
            if stripped and len(stripped) >= 80:
                last_char = stripped[-1]
                last_line = stripped.splitlines()[-1].strip()
                if (
                    last_char not in ".!?。！？)]}”\"'`|>"
                    and not last_line.startswith(("#", "|", "-", "*", "\\"))
                ):
                    truncation_risks.append({
                        "file": rel,
                        "reason": "abrupt_unpunctuated_ending",
                        "excerpt": last_line[-120:],
                    })

    missing_chapters: list[str] = []
    if chapter_nums:
        lo, hi = min(chapter_nums), max(chapter_nums)
        present_set = set(chapter_nums)
        for n in range(lo, hi + 1):
            if n not in present_set:
                missing_chapters.append(f"chapter_{n:02d}")

    toc_gaps: list[str] = []
    if toc_path and toc_path.is_file():
        toc = json.loads(toc_path.read_text(encoding="utf-8"))
        expected = toc.get("expected_chapters", []) if isinstance(toc, dict) else []
        present_rels = {item["file"] for item in chapter_inventory}
        for exp in expected:
            exp_name = exp.get("file", "") if isinstance(exp, dict) else str(exp)
            if exp_name and exp_name not in present_rels:
                toc_gaps.append(exp_name)
                if exp_name not in missing_chapters:
                    missing_chapters.append(exp_name)

    return {
        "chapters_imported": len(files),
        "chapter_inventory": chapter_inventory,
        "missing_chapters": missing_chapters,
        "toc_gaps": toc_gaps,
        "truncation_risks": truncation_risks,
        "complete_coverage_claimed": len(missing_chapters) == 0 and len(truncation_risks) == 0,
    }


def _collect_dir_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for fp in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = fp.relative_to(root).as_posix()
        out[rel] = _sha256_file(fp)
    return out


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for fp in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = fp.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fp, target)


def _load_state(state_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest_p = state_dir / "manifest.json"
    if not manifest_p.is_file():
        raise RevisionError(f"State directory not initialized: {state_dir}")
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    proposals_p = state_dir / "proposals.json"
    proposals = json.loads(proposals_p.read_text(encoding="utf-8")) if proposals_p.is_file() else []
    history_p = state_dir / "history.json"
    history = json.loads(history_p.read_text(encoding="utf-8")) if history_p.is_file() else []
    return manifest, proposals, history


def _save_state(
    state_dir: Path,
    manifest: dict[str, Any],
    proposals: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> None:
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
) -> dict[str, Any]:
    files = _scan_chapter_files(chapters_dir)
    if not files:
        raise RevisionError(f"No chapter files (.md/.txt/.tex) found in {chapters_dir}")

    wo = parse_workorder(workorder_path)
    coverage = _detect_coverage_and_truncation(chapters_dir, files, toc_path=toc_path)

    if state_dir.exists():
        shutil.rmtree(state_dir)
    snap_v0 = state_dir / "snapshots" / "v0"
    working = state_dir / "working"
    snap_v0.mkdir(parents=True, exist_ok=True)
    working.mkdir(parents=True, exist_ok=True)

    anchor_index: dict[str, dict[str, Any]] = {}
    initial_sha256: dict[str, str] = {}

    for ch_idx, fp in enumerate(files, 1):
        rel = fp.relative_to(chapters_dir).as_posix()
        raw = fp.read_bytes()
        initial_sha256[rel] = _sha256_bytes(raw)
        for dest_root in (snap_v0, working):
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)

        text = raw.decode("utf-8", errors="replace")
        for blk in _split_blocks_with_offsets(text):
            explicit_id = _extract_anchor_id(blk["text"])
            aid = explicit_id or f"CH{ch_idx:02d}-P{blk['block_index']:03d}"
            if aid in anchor_index:
                aid = f"{rel}::P{blk['block_index']:03d}"
            anchor_index[aid] = {
                "file": rel,
                "block_index": blk["block_index"],
                "start_line": blk["start_line"],
                "end_line": blk["end_line"],
                "explicit": explicit_id is not None,
                "original_sha256": _sha256_bytes(blk["text"].encode("utf-8")),
                "preview": " ".join(blk["text"].split())[:140],
            }

    # Resolve workorder target_ids to files
    resolved_targets: dict[str, str] = {}
    missing_targets: list[str] = []
    for tid in wo["target_ids"]:
        if tid in anchor_index:
            resolved_targets[tid] = anchor_index[tid]["file"]
        else:
            missing_targets.append(tid)

    authorized_files = sorted(set(wo["target_files"]) | set(resolved_targets.values()))
    manifest = {
        "schema_version": "2.0",
        "imported_at_utc": _utc_now(),
        "current_version": "v0",
        "version_counter": 0,
        "workorder": wo,
        "authorized_target_ids": wo["target_ids"],
        "authorized_files": authorized_files,
        "resolved_target_map": resolved_targets,
        "missing_workorder_targets": missing_targets,
        "initial_sha256": initial_sha256,
        "anchor_index": anchor_index,
        "coverage": coverage,
    }
    history = [
        {
            "event": "import",
            "version": "v0",
            "parent_version": None,
            "timestamp_utc": manifest["imported_at_utc"],
            "file_sha256": initial_sha256,
            "notes": f"Imported {len(files)} chapter file(s) with {len(anchor_index)} indexed block(s).",
        }
    ]
    _save_state(state_dir, manifest, [], history)

    receipt = {
        "ok": True,
        "receipt_type": "import_receipt",
        "imported_at_utc": manifest["imported_at_utc"],
        "current_version": "v0",
        "chapters_count": len(files),
        "indexed_anchors_count": len(anchor_index),
        "workorder_id": wo["workorder_id"],
        "workorder_schema_mode": wo["schema_mode"],
        "authorized_target_ids": wo["target_ids"],
        "authorized_files": authorized_files,
        "resolved_target_map": resolved_targets,
        "missing_workorder_targets": missing_targets,
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
) -> tuple[str, str]:
    anchors = manifest["anchor_index"]
    wo = manifest["workorder"]
    resolved_file = file_rel
    resolved_tid = target_id

    if target_id:
        if target_id not in anchors:
            raise TargetNotFoundError(f"Target anchor {target_id!r} not found in imported chapters.")
        resolved_file = anchors[target_id]["file"]
    elif file_rel:
        if file_rel not in manifest["initial_sha256"]:
            raise TargetNotFoundError(f"Chapter file {file_rel!r} not found in imported chapters.")
        # Pick the last block of the file if only chapter file is specified
        file_anchors = [
            (aid, meta) for aid, meta in anchors.items() if meta["file"] == file_rel
        ]
        file_anchors.sort(key=lambda x: x[1]["block_index"])
        resolved_tid = file_anchors[-1][0]
    else:
        raise InvalidPatchError("Either --target-id or --file must be provided.")

    assert resolved_file is not None and resolved_tid is not None
    if wo["present"]:
        auth_ids = set(manifest.get("authorized_target_ids", []))
        auth_files = set(manifest.get("authorized_files", []))
        if auth_ids and resolved_tid not in auth_ids:
            raise UnauthorizedTargetError(
                f"Target {resolved_tid!r} is outside organizer-edit-workorder authorized target_ids {sorted(auth_ids)}."
            )
        if not auth_ids and auth_files and resolved_file not in auth_files:
            raise UnauthorizedTargetError(
                f"File {resolved_file!r} is outside organizer-edit-workorder authorized files {sorted(auth_files)}."
            )
    return resolved_tid, resolved_file


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


def _apply_proposal_to_text(text: str, prop: dict[str, Any], anchor_meta: dict[str, Any]) -> str:
    blocks = _split_blocks_with_offsets(text)
    target_blk = None
    tid = prop["target_id"]
    for blk in blocks:
        if _extract_anchor_id(blk["text"]) == tid:
            target_blk = blk
            break
    if target_blk is None:
        # Match by original SHA-256 of the block (ignoring previously inserted annotations)
        orig_hash = anchor_meta["original_sha256"]
        for blk in blocks:
            if _sha256_bytes(blk["text"].encode("utf-8")) == orig_hash:
                target_blk = blk
                break
    if target_blk is None:
        raise TargetNotFoundError(f"Could not locate intact original block for {tid!r} in {prop['file']}.")

    if prop["mode"] == "annotate":
        marker = f"[Editorial Annotation | ID: {prop['proposal_id']} |"
        if marker in text:
            raise InvalidPatchError(f"Proposal {prop['proposal_id']} is already applied.")
        insertion = _format_annotation_block(prop)
        return text[: target_blk["end"]] + insertion + text[target_blk["end"] :]

    if prop["mode"] == "replace_block":
        replacement = prop.get("replacement_text", "").strip()
        if not replacement:
            raise InvalidPatchError("replace_block mode requires non-empty replacement_text.")
        return text[: target_blk["start"]] + replacement + text[target_blk["end"] :]

    raise InvalidPatchError(f"Unsupported proposal mode: {prop['mode']!r}")


def cmd_propose(
    state_dir: Path,
    *,
    target_id: str | None = None,
    file_rel: str | None = None,
    mode: str = "annotate",
    title: str = "",
    annotation: str = "",
    source_quote: str = "",
    source_ref: str = "",
    uncertainty: str = "",
    replacement_text: str = "",
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
        raise InvalidPatchError("Substantive replace mode requires non-empty --replacement-text.")
    if not source_quote.strip() or not source_ref.strip():
        raise InvalidPatchError("Both --source-quote and --source-ref are required for grounded revisions.")

    resolved_tid, resolved_file = _check_authorization(manifest, target_id=target_id, file_rel=file_rel)
    prop_id = f"P{len(proposals) + 1:04d}"

    prop = {
        "proposal_id": prop_id,
        "target_id": resolved_tid,
        "file": resolved_file,
        "mode": mode,
        "title": title.strip(),
        "annotation": annotation.strip(),
        "source_quote": source_quote.strip(),
        "source_ref": source_ref.strip(),
        "uncertainty": uncertainty.strip(),
        "replacement_text": replacement_text.strip(),
        "status": "proposed",
        "decision_reason": "",
        "applied_version": None,
        "created_at_utc": _utc_now(),
        "decided_at_utc": None,
    }
    # Dry-run apply to guarantee patch validity before mutating proposals.json
    work_file = state_dir / "working" / resolved_file
    cur_text = work_file.read_text(encoding="utf-8")
    _ = _apply_proposal_to_text(cur_text, prop, manifest["anchor_index"][resolved_tid])

    proposals.append(prop)
    _save_state(state_dir, manifest, proposals, history)
    return {"ok": True, "proposal": prop}


def cmd_decide(
    state_dir: Path,
    *,
    proposal_id: str,
    action: str,
    reason: str = "",
) -> dict[str, Any]:
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
        history.append({
            "event": "reject",
            "proposal_id": proposal_id,
            "version": manifest["current_version"],
            "parent_version": manifest["current_version"],
            "timestamp_utc": prop["decided_at_utc"],
            "reason": prop["decision_reason"],
            "working_bytes_unchanged": unchanged,
            "file_sha256": post_hashes,
        })
        _save_state(state_dir, manifest, proposals, history)
        return {
            "ok": True,
            "action": "reject",
            "proposal_id": proposal_id,
            "current_version": manifest["current_version"],
            "working_bytes_unchanged": unchanged,
        }

    # Accept: apply strictly to authorized file and verify non-target files + original text
    target_file = working_dir / prop["file"]
    cur_text = target_file.read_text(encoding="utf-8")
    new_text = _apply_proposal_to_text(cur_text, prop, manifest["anchor_index"][prop["target_id"]])
    if prop["mode"] == "annotate":
        v0_text = (state_dir / "snapshots" / "v0" / prop["file"]).read_text(encoding="utf-8")
        if strip_editorial_annotations(new_text) != v0_text:
            raise InvalidPatchError("Annotation unexpectedly altered original publication text.")

    _atomic_write_text(target_file, new_text)
    post_hashes = _collect_dir_hashes(working_dir)

    untouched_changed = [
        rel for rel, h in pre_hashes.items() if rel != prop["file"] and post_hashes.get(rel) != h
    ]
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
    history.append({
        "event": "accept",
        "proposal_id": proposal_id,
        "target_id": prop["target_id"],
        "file": prop["file"],
        "version": new_ver,
        "parent_version": parent_ver,
        "timestamp_utc": prop["decided_at_utc"],
        "reason": prop["decision_reason"],
        "original_text_preserved": prop["mode"] == "annotate",
        "file_sha256": post_hashes,
    })
    _save_state(state_dir, manifest, proposals, history)
    return {
        "ok": True,
        "action": "accept",
        "proposal_id": proposal_id,
        "version": new_ver,
        "file": prop["file"],
        "file_sha256": post_hashes[prop["file"]],
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
    manifest, _, _ = _load_state(state_dir)
    v_from = _resolve_version(manifest, from_version)
    v_to = _resolve_version(manifest, to_version)
    dir_from = state_dir / "snapshots" / v_from
    dir_to = state_dir / "snapshots" / v_to
    if not dir_from.is_dir():
        raise VersionNotFoundError(f"Snapshot version {v_from!r} not found.")
    if not dir_to.is_dir():
        raise VersionNotFoundError(f"Snapshot version {v_to!r} not found.")

    hashes_from = _collect_dir_hashes(dir_from)
    hashes_to = _collect_dir_hashes(dir_to)
    all_files = sorted(hashes_from.keys() | hashes_to.keys())

    diff_chunks: list[str] = []
    changed_files: list[str] = []
    unchanged_files: list[str] = []
    original_preserved_all = True

    for rel in all_files:
        if hashes_from.get(rel) == hashes_to.get(rel):
            unchanged_files.append(rel)
            continue
        changed_files.append(rel)
        txt_a = (dir_from / rel).read_text(encoding="utf-8") if (dir_from / rel).is_file() else ""
        txt_b = (dir_to / rel).read_text(encoding="utf-8") if (dir_to / rel).is_file() else ""
        if v_from == "v0" and strip_editorial_annotations(txt_b) != txt_a:
            original_preserved_all = False
        ud = difflib.unified_diff(
            txt_a.splitlines(keepends=True),
            txt_b.splitlines(keepends=True),
            fromfile=f"{v_from}/{rel}",
            tofile=f"{v_to}/{rel}",
            n=3,
        )
        diff_chunks.append("".join(ud))

    full_diff = "\n".join(c for c in diff_chunks if c)
    if out_diff:
        _atomic_write_text(out_diff, full_diff)

    summary = {
        "ok": True,
        "from_version": v_from,
        "to_version": v_to,
        "changed_files": changed_files,
        "unchanged_files": unchanged_files,
        "original_blocks_preserved": original_preserved_all,
        "diff_lines": len(full_diff.splitlines()),
        "diff_text": full_diff,
    }
    if out_json:
        _atomic_write_json(out_json, {k: v for k, v in summary.items() if k != "diff_text"})
    return summary


def cmd_rollback(
    state_dir: Path,
    *,
    to_version: str = "v0",
    out_json: Path | None = None,
) -> dict[str, Any]:
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
        "ok": restored_exact,
        "event": "rollback",
        "rolled_back_from": parent_ver,
        "restored_target_version": to_version,
        "new_version": new_ver,
        "timestamp_utc": _utc_now(),
        "sha256_restored_100pct": restored_exact,
        "file_sha256": actual_hashes,
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
            found = {p["proposal_id"] for p in selected}
            missing = sorted(wanted - found)
            raise ProposalNotFoundError(f"Cannot reapply missing proposal(s): {missing}")

    if not selected:
        raise InvalidPatchError("No proposals selected for reapplication.")

    # Find most recent pre-rollback accept snapshot for byte parity comparison
    prev_accept_hashes: dict[str, str] | None = None
    for ev in reversed(history):
        if ev.get("event") == "accept":
            prev_accept_hashes = ev.get("file_sha256")
            break

    # Reset working to v0 before reapplying cleanly
    working_dir = state_dir / "working"
    _copy_tree(state_dir / "snapshots" / "v0", working_dir)

    applied_ids: list[str] = []
    for prop in selected:
        fp = working_dir / prop["file"]
        cur_text = fp.read_text(encoding="utf-8")
        new_text = _apply_proposal_to_text(cur_text, prop, manifest["anchor_index"][prop["target_id"]])
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
        "event": "reapply",
        "parent_version": parent_ver,
        "new_version": new_ver,
        "reapplied_proposals": applied_ids,
        "timestamp_utc": _utc_now(),
        "matches_pre_rollback_sha256": matches_prior,
        "file_sha256": actual_hashes,
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
    """Run 5 deliberate invalid operations and verify 100% zero state/file mutation."""
    manifest, proposals, history = _load_state(state_dir)
    before_fp = _state_fingerprint(state_dir)

    # Pick an unauthorized anchor if workorder restricts scope
    auth_ids = set(manifest.get("authorized_target_ids", []))
    auth_files = set(manifest.get("authorized_files", []))
    unauth_anchor = None
    for aid, meta in manifest["anchor_index"].items():
        if auth_ids and aid not in auth_ids:
            unauth_anchor = aid
            break
        if not auth_ids and auth_files and meta["file"] not in auth_files:
            unauth_anchor = aid
            break

    cases: list[dict[str, Any]] = []

    def _run_case(case_id: str, desc: str, fn) -> None:
        pre = _state_fingerprint(state_dir)
        caught_type = None
        caught_code = None
        caught_msg = ""
        try:
            fn()
        except RevisionError as exc:
            caught_type = type(exc).__name__
            caught_code = exc.error_code
            caught_msg = str(exc)
        post = _state_fingerprint(state_dir)
        unchanged = pre == post
        cases.append({
            "fault_case_id": case_id,
            "category": "deliberate_fault_injection",
            "is_platform_failure": False,
            "description": desc,
            "exception_raised": caught_type is not None,
            "exception_type": caught_type,
            "error_code": caught_code,
            "message": caught_msg,
            "state_unchanged": unchanged,
            "passed": (caught_type is not None) and unchanged,
        })

    _run_case(
        "F01_OUT_OF_RANGE_TARGET",
        "Propose annotation against non-existent anchor ZZ99-P999.",
        lambda: cmd_propose(
            state_dir,
            target_id="ZZ99-P999",
            title="Invalid target test",
            annotation="Must be rejected.",
            source_quote="N/A",
            source_ref="N/A",
        ),
    )

    if unauth_anchor:
        _run_case(
            "F02_UNAUTHORIZED_SCOPE",
            f"Propose annotation against existing but unauthorized anchor {unauth_anchor}.",
            lambda: cmd_propose(
                state_dir,
                target_id=unauth_anchor,
                title="Scope violation test",
                annotation="Must be blocked by workorder scope.",
                source_quote="Existing text",
                source_ref=unauth_anchor,
            ),
        )
    else:
        _run_case(
            "F02_UNAUTHORIZED_FILE_PATH",
            "Propose annotation against non-existent external file outside chapter tree.",
            lambda: cmd_propose(
                state_dir,
                file_rel="../outside_scope.md",
                title="Path boundary test",
                annotation="Must be blocked.",
                source_quote="N/A",
                source_ref="N/A",
            ),
        )

    _run_case(
        "F03_INVALID_PROPOSAL_ID",
        "Attempt to accept non-existent proposal P9999.",
        lambda: cmd_decide(state_dir, proposal_id="P9999", action="accept"),
    )

    _run_case(
        "F04_INVALID_ROLLBACK_VERSION",
        "Attempt to rollback to non-existent version v999.",
        lambda: cmd_rollback(state_dir, to_version="v999"),
    )

    first_valid_tid = next(iter(manifest.get("authorized_target_ids", [])), None) or next(
        iter(manifest["anchor_index"].keys())
    )
    _run_case(
        "F05_MALFORMED_EMPTY_PATCH",
        "Attempt to submit an empty annotation without source quote.",
        lambda: cmd_propose(
            state_dir,
            target_id=first_valid_tid,
            title="Malformed patch",
            annotation="",
            source_quote="",
            source_ref="",
        ),
    )

    after_fp = _state_fingerprint(state_dir)
    all_unchanged = before_fp == after_fp
    all_passed = all_unchanged and all(c["passed"] for c in cases)

    report = {
        "ok": all_passed,
        "category": "deliberate_fault_injection",
        "is_platform_failure": False,
        "tested_at_utc": _utc_now(),
        "total_cases": len(cases),
        "passed_cases": sum(1 for c in cases if c["passed"]),
        "state_unchanged_verified": all_unchanged,
        "pre_test_working_sha256": before_fp["working_sha256"],
        "post_test_working_sha256": after_fp["working_sha256"],
        "cases": cases,
    }
    # Record fault test receipt in history without touching working/manifest/proposals
    history.append({
        "event": "fault_test",
        "version": manifest["current_version"],
        "timestamp_utc": report["tested_at_utc"],
        "passed_cases": report["passed_cases"],
        "total_cases": report["total_cases"],
        "state_unchanged_verified": all_unchanged,
    })
    _atomic_write_json(state_dir / "history.json", history)
    if out_json:
        _atomic_write_json(out_json, report)
    return report


def _render_html_preview(
    manifest: dict[str, Any],
    proposals: list[dict[str, Any]],
    history: list[dict[str, Any]],
    diff_summary: dict[str, Any],
) -> str:
    esc = html.escape
    accepted = [p for p in proposals if p["status"] == "accepted"]
    rejected = [p for p in proposals if p["status"] == "rejected"]
    rollback_ok = any(h.get("event") == "rollback" and h.get("sha256_restored_100pct") for h in history)
    reapply_ok = any(h.get("event") == "reapply" and h.get("ok") for h in history)
    fault_ok = any(h.get("event") == "fault_test" and h.get("state_unchanged_verified") for h in history)

    prop_rows = []
    for p in proposals:
        badge_cls = "ok" if p["status"] == "accepted" else ("rej" if p["status"] == "rejected" else "pend")
        prop_rows.append(
            f"<tr>"
            f"<td><code>{esc(p['proposal_id'])}</code></td>"
            f"<td><code>{esc(p['target_id'])}</code><br><small>{esc(p['file'])}</small></td>"
            f"<td><span class='badge {badge_cls}'>{esc(p['status'])}</span></td>"
            f"<td><strong>{esc(p['title'])}</strong><br>{esc(p['annotation'])}</td>"
            f"<td><em>\"{esc(p['source_quote'])}\"</em><br><small>{esc(p['source_ref'])}</small></td>"
            f"<td>{esc(p.get('decision_reason') or '')}<br><small>{esc(p.get('uncertainty') or '')}</small></td>"
            f"</tr>"
        )

    diff_lines_html = []
    for ln in diff_summary.get("diff_text", "").splitlines():
        if ln.startswith("+++") or ln.startswith("---"):
            cls = "d-hdr"
        elif ln.startswith("@@"):
            cls = "d-hunk"
        elif ln.startswith("+"):
            cls = "d-add"
        elif ln.startswith("-"):
            cls = "d-del"
        else:
            cls = "d-ctx"
        diff_lines_html.append(f"<div class='{cls}'>{esc(ln)}</div>")

    hist_rows = []
    for idx, h in enumerate(history, 1):
        hist_rows.append(
            f"<tr><td>{idx}</td><td><code>{esc(str(h.get('event','')))}</code></td>"
            f"<td><code>{esc(str(h.get('version') or h.get('new_version') or ''))}</code></td>"
            f"<td>{esc(str(h.get('timestamp_utc','')))}</td>"
            f"<td>{esc(str(h.get('proposal_id') or h.get('notes') or h.get('restored_target_version') or ''))}</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Long-Document Revision Export Preview</title>
<style>
:root {{ --bg:#f8fafc; --card:#ffffff; --ink:#0f172a; --muted:#475569; --line:#cbd5e1; --accent:#1d4ed8; --ok:#15803d; --rej:#b91c1c; }}
body {{ margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--ink); line-height:1.5; }}
.wrap {{ max-width:1120px; margin:24px auto; padding:0 16px; }}
h1 {{ font-size:1.5rem; margin:0 0 8px; }}
h2 {{ font-size:1.15rem; margin:24px 0 10px; border-bottom:1px solid var(--line); padding-bottom:4px; }}
.kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:16px 0; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; }}
.kpi .lbl {{ font-size:0.78rem; color:var(--muted); text-transform:uppercase; }}
.kpi .val {{ font-size:1.25rem; font-weight:700; margin-top:4px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px; overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:0.88rem; }}
th, td {{ border-bottom:1px solid #e2e8f0; padding:8px 10px; text-align:left; vertical-align:top; }}
th {{ background:#f1f5f9; font-weight:600; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:12px; font-size:0.78rem; font-weight:600; }}
.badge.ok {{ background:#dcfce7; color:var(--ok); }}
.badge.rej {{ background:#fee2e2; color:var(--rej); }}
.badge.pend {{ background:#fef9c3; color:#854d0e; }}
.diff {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:0.82rem; white-space:pre-wrap; word-break:break-word; }}
.d-hdr {{ background:#e2e8f0; font-weight:700; padding:2px 6px; }}
.d-hunk {{ background:#dbeafe; color:#1e3a8a; padding:2px 6px; }}
.d-add {{ background:#dcfce7; color:#14532d; padding:1px 6px; }}
.d-del {{ background:#fee2e2; color:#7f1d1d; padding:1px 6px; }}
.d-ctx {{ padding:1px 6px; color:#334155; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Long-Document Local Revision Export Preview</h1>
  <p style="color:var(--muted);margin:0;">Workorder: <code>{esc(str(manifest['workorder'].get('workorder_id') or 'none'))}</code> | Current Version: <code>{esc(manifest['current_version'])}</code> | Offline Self-Contained Preview (Zero External Requests)</p>
  <div class="kpi-grid">
    <div class="kpi"><div class="lbl">Chapters Imported</div><div class="val">{len(manifest['initial_sha256'])}</div></div>
    <div class="kpi"><div class="lbl">Accepted / Rejected</div><div class="val">{len(accepted)} / {len(rejected)}</div></div>
    <div class="kpi"><div class="lbl">Original Blocks Preserved</div><div class="val">{'YES' if diff_summary['original_blocks_preserved'] else 'CHECK'}</div></div>
    <div class="kpi"><div class="lbl">Rollback &amp; Reapply</div><div class="val">{'VERIFIED' if (rollback_ok and reapply_ok) else 'PARTIAL'}</div></div>
    <div class="kpi"><div class="lbl">Fault Injection Unchanged</div><div class="val">{'VERIFIED' if fault_ok else 'NOT RUN'}</div></div>
  </div>
  <h2>1. Proposal Decisions &amp; Source Grounding</h2>
  <div class="card">
    <table>
      <thead><tr><th>ID</th><th>Target</th><th>Decision</th><th>Editorial Annotation</th><th>Exact Source Quote</th><th>Rationale &amp; Uncertainty</th></tr></thead>
      <tbody>{''.join(prop_rows) or '<tr><td colspan="6">No proposals recorded.</td></tr>'}</tbody>
    </table>
  </div>
  <h2>2. Unified Diff (v0 &rarr; {esc(manifest['current_version'])})</h2>
  <div class="card diff">{''.join(diff_lines_html) or '<div class="d-ctx">No byte differences between v0 and current version.</div>'}</div>
  <h2>3. Version &amp; Audit Event Chain</h2>
  <div class="card">
    <table>
      <thead><tr><th>#</th><th>Event</th><th>Version</th><th>Timestamp (UTC)</th><th>Detail</th></tr></thead>
      <tbody>{''.join(hist_rows)}</tbody>
    </table>
  </div>
</div>
</body>
</html>
"""


def cmd_export(
    state_dir: Path,
    *,
    out_dir: Path,
    records_out: Path | None = None,
    evidence_out: Path | None = None,
    html_preview: Path | None = None,
) -> dict[str, Any]:
    manifest, proposals, history = _load_state(state_dir)
    working_dir = state_dir / "working"
    _copy_tree(working_dir, out_dir)
    exported_hashes = _collect_dir_hashes(out_dir)
    diff_summary = cmd_diff(state_dir, from_version="v0", to_version="latest")

    evidence_map = [
        {
            "proposal_id": p["proposal_id"],
            "status": p["status"],
            "target_id": p["target_id"],
            "file": p["file"],
            "title": p["title"],
            "source_ref": p["source_ref"],
            "source_quote": p["source_quote"],
            "uncertainty": p["uncertainty"],
            "decision_reason": p["decision_reason"],
        }
        for p in proposals
    ]
    records = {
        "ok": True,
        "exported_at_utc": _utc_now(),
        "current_version": manifest["current_version"],
        "workorder": manifest["workorder"],
        "initial_sha256": manifest["initial_sha256"],
        "exported_sha256": exported_hashes,
        "original_blocks_preserved": diff_summary["original_blocks_preserved"],
        "changed_files": diff_summary["changed_files"],
        "unchanged_files": diff_summary["unchanged_files"],
        "proposals": proposals,
        "version_history": history,
        "coverage_manifest": manifest["coverage"],
    }
    if records_out:
        _atomic_write_json(records_out, records)
    if evidence_out:
        _atomic_write_json(evidence_out, {"evidence_map": evidence_map})
    if html_preview:
        _atomic_write_text(html_preview, _render_html_preview(manifest, proposals, history, diff_summary))
    return {
        "ok": True,
        "exported_dir": str(out_dir),
        "exported_files": len(exported_hashes),
        "changed_files": diff_summary["changed_files"],
        "unchanged_files": diff_summary["unchanged_files"],
        "original_blocks_preserved": diff_summary["original_blocks_preserved"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local controllable revision state machine for long documents.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_imp = sub.add_parser("import", help="Import chapters, hash v0 snapshot, index anchors, check coverage.")
    p_imp.add_argument("--chapters-dir", required=True, type=Path)
    p_imp.add_argument("--state-dir", required=True, type=Path)
    p_imp.add_argument("--workorder", type=Path, default=None)
    p_imp.add_argument("--toc-json", type=Path, default=None)
    p_imp.add_argument("--receipt-out", type=Path, default=None)

    p_prop = sub.add_parser("propose", help="Create a source-grounded editorial proposal.")
    p_prop.add_argument("--state-dir", required=True, type=Path)
    p_prop.add_argument("--target-id", default=None)
    p_prop.add_argument("--file", default=None)
    p_prop.add_argument("--mode", default="annotate", choices=["annotate", "replace", "replace_block"])
    p_prop.add_argument("--title", required=True)
    p_prop.add_argument("--annotation", default="")
    p_prop.add_argument("--source-quote", required=True)
    p_prop.add_argument("--source-ref", required=True)
    p_prop.add_argument("--uncertainty", default="")
    p_prop.add_argument("--replacement-text", default="")

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

    p_exp = sub.add_parser("export", help="Export editable chapters, records, evidence map, and offline HTML preview.")
    p_exp.add_argument("--state-dir", required=True, type=Path)
    p_exp.add_argument("--out-dir", required=True, type=Path)
    p_exp.add_argument("--records-out", type=Path, default=None)
    p_exp.add_argument("--evidence-out", type=Path, default=None)
    p_exp.add_argument("--html-preview", type=Path, default=None)

    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            res = cmd_import(
                args.chapters_dir,
                args.state_dir,
                workorder_path=args.workorder,
                toc_path=args.toc_json,
                receipt_out=args.receipt_out,
            )
        elif args.command == "propose":
            res = cmd_propose(
                args.state_dir,
                target_id=args.target_id,
                file_rel=args.file,
                mode=args.mode,
                title=args.title,
                annotation=args.annotation,
                source_quote=args.source_quote,
                source_ref=args.source_ref,
                uncertainty=args.uncertainty,
                replacement_text=args.replacement_text,
            )
        elif args.command in {"accept", "reject"}:
            res = cmd_decide(args.state_dir, proposal_id=args.proposal_id, action=args.command, reason=args.reason)
        elif args.command == "diff":
            res = cmd_diff(
                args.state_dir,
                from_version=args.from_version,
                to_version=args.to_version,
                out_diff=args.out_diff,
                out_json=args.out_json,
            )
            res = {k: v for k, v in res.items() if k != "diff_text"}
        elif args.command == "rollback":
            res = cmd_rollback(args.state_dir, to_version=args.to_version, out_json=args.out_json)
        elif args.command == "reapply":
            pids = [x.strip() for x in args.proposal_ids.split(",") if x.strip()]
            res = cmd_reapply(
                args.state_dir,
                proposal_ids=pids,
                from_accepted=args.from_accepted,
                out_json=args.out_json,
            )
        elif args.command == "fault-test":
            res = cmd_fault_test(args.state_dir, out_json=args.out_json)
        elif args.command == "export":
            res = cmd_export(
                args.state_dir,
                out_dir=args.out_dir,
                records_out=args.records_out,
                evidence_out=args.evidence_out,
                html_preview=args.html_preview,
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
