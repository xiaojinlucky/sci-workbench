#!/usr/bin/env python3
"""Benchmark Item-Bank Quality, Corpus-Anchor & Anti-Leakage Auditor (benchmark-a1d81ca4).

Pure Python 3.12 standard library.
Pre-run checks before scoring candidate agent runs:
- Required card fields (item_id, task_type, prompt, rubric, version) and data_license/authorization audit
- Duplicate item_id detection
- Empty reference_answer detection on answerable items
- Verification that all required_paragraph_ids exist in supplied paragraph/table corpus
- Direct prompt-answer leakage detection (reference_answer or expected_values verbatim in prompt)
- Dual SHA-256 computation: semantic_sha256 (scoring-affecting fields) vs full_sha256 (entire card)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import preflight_probe
    from audit_chain import sha256_obj
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import preflight_probe
    from audit_chain import sha256_obj


SEMANTIC_ITEM_KEYS = (
    "item_id",
    "task_type",
    "prompt",
    "expected_values",
    "expected_keywords",
    "required_paragraph_ids",
    "unanswerable",
    "reference_answer",
    "rubric",
)


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def load_workorder_and_corpus(input_dir: Path, case_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load source-conditioned benchmark workorder and paragraph/table text corpus cleanly."""
    if case_data is None:
        cp = input_dir / "case.json"
        case_data = json.loads(cp.read_text(encoding="utf-8", errors="replace")) if cp.is_file() else {}

    items_raw: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    workorder_files = ("benchmark-workorder.json", "workorder.json", "benchmark.json", "items.json")

    for fname in workorder_files:
        fp = input_dir / fname
        if fp.is_file():
            obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            if isinstance(obj, dict):
                items_raw.extend(x for x in (obj.get("items") or obj.get("tasks") or obj.get("questions") or []) if isinstance(x, dict))
                if isinstance(obj.get("sources"), dict):
                    sources.update({str(k): str(v) for k, v in obj["sources"].items()})
                for idx, p in enumerate(obj.get("paragraphs") or [], start=1):
                    if isinstance(p, dict):
                        pid = str(p.get("paragraph_id") or p.get("id") or f"P{idx:03d}")
                        sources[pid] = str(p.get("text") or p.get("content") or "")
            elif isinstance(obj, list):
                items_raw.extend(x for x in obj if isinstance(x, dict))

    if not items_raw:
        items_raw = [x for x in (case_data.get("items") or case_data.get("benchmark_items") or case_data.get("tasks") or []) if isinstance(x, dict)]

    if isinstance(case_data.get("sources"), dict):
        sources.update({str(k): str(v) for k, v in case_data["sources"].items()})

    for idx, p in enumerate(case_data.get("paragraphs") or [], start=1):
        if isinstance(p, dict):
            pid = str(p.get("paragraph_id") or p.get("id") or f"P{idx:03d}")
            sources[pid] = str(p.get("text") or p.get("content") or "")
        else:
            sources[f"P{idx:03d}"] = str(p)

    for idx, t in enumerate(case_data.get("tables") or [], start=1):
        if isinstance(t, dict):
            tid = str(t.get("table_id") or t.get("id") or f"T{idx:02d}")
            sources[tid] = str(t.get("text") or t.get("content") or json.dumps(t.get("rows", []), ensure_ascii=False))

    skip_names = {"case.json", "task.md", *workorder_files}
    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file() and not p.is_symlink()):
        if fp.name in skip_names:
            continue
        if fp.suffix.lower() in {".md", ".txt", ".csv"}:
            sources[fp.relative_to(input_dir).as_posix()] = fp.read_text(encoding="utf-8", errors="replace")

    normalized_items: list[dict[str, Any]] = []
    for idx, raw in enumerate(items_raw, start=1):
        iid = str(raw.get("item_id") or raw.get("id") or f"ITEM-{idx:02d}").strip()
        norm = {
            "item_id": iid,
            "task_type": str(raw.get("task_type") or raw.get("category") or "").strip(),
            "prompt": str(raw.get("prompt") or raw.get("question") or raw.get("task") or "").strip(),
            "expected_keywords": [str(k).strip() for k in (raw.get("expected_keywords") or raw.get("required_terms") or []) if str(k).strip()],
            "expected_values": [str(v).strip() for v in (raw.get("expected_values") or []) if str(v).strip()],
            "required_paragraph_ids": [str(p).strip() for p in (raw.get("required_paragraph_ids") or []) if str(p).strip()],
            "reference_answer": raw.get("reference_answer"),
            "unanswerable": bool(raw.get("unanswerable", False)),
            "rubric": raw.get("rubric"),
            "version": str(raw.get("version") or "").strip(),
            "data_license": str(raw.get("data_license") or raw.get("authorization") or raw.get("license") or "").strip(),
            "difficulty": str(raw.get("difficulty") or "").strip(),
            "metadata_notes": str(raw.get("notes") or raw.get("metadata_notes") or "").strip(),
            "numeric_rel_tol": float(raw.get("numeric_rel_tol", 1e-3)),
        }
        semantic_core = {k: norm[k] for k in SEMANTIC_ITEM_KEYS}
        norm["semantic_sha256"] = sha256_obj(semantic_core)[:16]
        norm["full_sha256"] = sha256_obj(raw)[:16]
        norm["item_sha256"] = norm["semantic_sha256"]
        normalized_items.append(norm)

    candidate_runs = case_data.get("candidate_runs") or case_data.get("predictions") or []
    return {
        "case_id": str(case_data.get("case_id") or "case-unknown"),
        "items": normalized_items,
        "sources": sources,
        "candidate_runs": candidate_runs,
        "previous_snapshot": case_data.get("previous_snapshot"),
        "source_modality": "paragraph_and_table_text_only_not_pdf_pixel_gold",
    }


def _detect_prompt_leakage(item: dict[str, Any]) -> list[str]:
    """Detect direct leakage of reference_answer or expected_values inside the prompt."""
    if item["unanswerable"]:
        return []
    prompt_norm = _normalize_ws(item["prompt"])
    if not prompt_norm:
        return []
    leaks: list[str] = []

    ref = item.get("reference_answer")
    if isinstance(ref, str):
        ref_norm = _normalize_ws(ref)
        if len(ref_norm) >= 8 and ref_norm in prompt_norm:
            leaks.append(f"reference_answer verbatim in prompt: {ref[:60]!r}")

    for ev in item["expected_values"]:
        ev_norm = _normalize_ws(ev)
        # Flag if an expected value with unit or >=4 chars appears directly in the prompt
        if len(ev_norm) >= 3 and re.search(rf"(?<!\w){re.escape(ev_norm)}(?!\w)", prompt_norm):
            leaks.append(f"expected_value {ev!r} appears directly in prompt")
    return leaks


def audit_item_bank(workorder: dict[str, Any]) -> dict[str, Any]:
    """Audit item-card quality, corpus references, duplicate IDs, licenses, and answer leakage."""
    items = workorder["items"]
    sources = workorder["sources"]
    id_counts = Counter(it["item_id"] for it in items)
    duplicate_ids = sorted(iid for iid, cnt in id_counts.items() if cnt > 1)

    item_reports: list[dict[str, Any]] = []
    blocking_issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for it in items:
        iid = it["item_id"]
        item_errors: list[str] = []
        item_warns: list[str] = []

        if id_counts[iid] > 1:
            item_errors.append("DUPLICATE_ITEM_ID")
            blocking_issues.append({"item_id": iid, "code": "DUPLICATE_ITEM_ID", "message": f"Duplicate item_id {iid!r} detected ({id_counts[iid]} occurrences)."})

        missing_req = [f for f in ("task_type", "prompt", "rubric", "version") if not it.get(f)]
        if missing_req:
            item_errors.append("MISSING_REQUIRED_FIELD")
            blocking_issues.append({"item_id": iid, "code": "MISSING_REQUIRED_FIELD", "message": f"Item {iid} missing required fields: {missing_req}."})

        if not it["data_license"]:
            item_warns.append("MISSING_DATA_LICENSE")
            warnings.append({"item_id": iid, "code": "MISSING_DATA_LICENSE", "message": f"Item {iid} missing data_license / authorization metadata."})

        if not it["unanswerable"]:
            ref_str = "" if it.get("reference_answer") is None else str(it.get("reference_answer")).strip()
            if not ref_str:
                if not it["expected_values"] and not it["expected_keywords"]:
                    item_errors.append("EMPTY_REFERENCE_ANSWER")
                    blocking_issues.append({"item_id": iid, "code": "EMPTY_REFERENCE_ANSWER", "message": f"Answerable item {iid} has empty reference_answer and no expected values/keywords."})
                else:
                    item_warns.append("MISSING_REFERENCE_ANSWER_TEXT")
                    warnings.append({"item_id": iid, "code": "MISSING_REFERENCE_ANSWER_TEXT", "message": f"Answerable item {iid} has expected_values/keywords but no prose reference_answer."})

        missing_pids = [pid for pid in it["required_paragraph_ids"] if pid not in sources]
        if missing_pids:
            item_errors.append("MISSING_CORPUS_PARAGRAPH")
            blocking_issues.append({"item_id": iid, "code": "MISSING_CORPUS_PARAGRAPH", "message": f"Item {iid} references missing corpus paragraph_id(s): {missing_pids}."})

        leaks = _detect_prompt_leakage(it)
        if leaks:
            item_errors.append("PROMPT_ANSWER_LEAKAGE")
            blocking_issues.append({"item_id": iid, "code": "PROMPT_ANSWER_LEAKAGE", "message": f"Item {iid} leaks answer in prompt: {'; '.join(leaks)}."})

        item_reports.append({
            "item_id": iid,
            "task_type": it["task_type"] or "unspecified",
            "version": it["version"] or "unspecified",
            "data_license": it["data_license"] or "missing",
            "semantic_sha256": it["semantic_sha256"],
            "full_sha256": it["full_sha256"],
            "card_ready": len(item_errors) == 0,
            "errors": item_errors,
            "warnings": item_warns,
            "leakage_details": leaks,
            "missing_paragraph_ids": missing_pids,
        })

    semantic_bank_sha = sha256_obj([{"id": i["item_id"], "s": i["semantic_sha256"]} for i in items])
    full_bank_sha = sha256_obj([{"id": i["item_id"], "f": i["full_sha256"]} for i in items])

    return {
        "ok": len(blocking_issues) == 0 and len(items) > 0,
        "item_count": len(items),
        "ready_item_count": sum(1 for r in item_reports if r["card_ready"]),
        "duplicate_item_ids": duplicate_ids,
        "leakage_item_ids": [r["item_id"] for r in item_reports if "PROMPT_ANSWER_LEAKAGE" in r["errors"]],
        "missing_corpus_item_ids": [r["item_id"] for r in item_reports if "MISSING_CORPUS_PARAGRAPH" in r["errors"]],
        "empty_ref_item_ids": [r["item_id"] for r in item_reports if "EMPTY_REFERENCE_ANSWER" in r["errors"]],
        "semantic_bank_sha256": semantic_bank_sha,
        "item_bank_sha256": full_bank_sha,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "item_reports": item_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-run Benchmark Item-Bank & Leakage Auditor.")
    parser.add_argument("--input", default="", help="Input directory containing case.json / workorder.")
    parser.add_argument("--out", default="", help="Optional path to write audit JSON.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(input_dir=args.input or None)
    wo = load_workorder_and_corpus(paths["input_dir"])
    report = audit_item_bank(wo)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "item_count": report["item_count"], "ready_item_count": report["ready_item_count"], "blocking_count": len(report["blocking_issues"])}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
