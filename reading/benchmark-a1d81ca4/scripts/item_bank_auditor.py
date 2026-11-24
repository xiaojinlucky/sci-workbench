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


def extract_id_tagged_blocks(text: str, doc_prefix: str = "") -> dict[str, str]:
    """Extract ID-tagged paragraphs and tables from real .md/.txt papers.

    Supports explicit anchors (`<a id="p0001"></a>`, `[P001]`, `[T01]`), markdown
    pipe tables, and paragraph chunks so real papers can be directly benchmarked.
    """
    blocks: dict[str, str] = {}
    if not text.strip():
        return blocks

    # 1. Explicit HTML anchor tags like <a id="p0035"></a> followed by content
    anchor_pat = re.compile(r'<a\s+id=["\']([A-Za-z0-9_.:-]+)["\']\s*>\s*</a>', re.I)
    matches = list(anchor_pat.finditer(text))
    if matches:
        for idx, m in enumerate(matches):
            aid = m.group(1).strip()
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if body:
                canon_id = aid.upper() if re.fullmatch(r"p\d+", aid, re.I) else aid
                blocks[canon_id] = body
        return blocks

    # 2. Explicit bracketed block headers like [P001] or [T01] at line start
    bracket_pat = re.compile(r"(?m)^\[((?:P|T|S|FIG|TAB)[A-Za-z0-9_.-]{1,16})\]\s*")
    bmatches = list(bracket_pat.finditer(text))
    if bmatches:
        for idx, m in enumerate(bmatches):
            bid = m.group(1).strip()
            start = m.end()
            end = bmatches[idx + 1].start() if idx + 1 < len(bmatches) else len(text)
            body = text[start:end].strip()
            if body:
                blocks[bid] = body
        return blocks

    # 3. Auto-segment real markdown/plain text into paragraph (P###) and table (T##) blocks
    p_idx = 0
    t_idx = 0
    chunks = [c.strip() for c in re.split(r"\n\s*\n+", text) if c.strip()]
    prefix = f"{doc_prefix}:" if doc_prefix else ""
    for ch in chunks:
        lines = [ln for ln in ch.splitlines() if ln.strip()]
        is_md_table = len(lines) >= 2 and sum(1 for ln in lines if "|" in ln) >= 2 and re.search(r"\|?\s*:?-{2,}", lines[1])
        is_caption_table = bool(re.match(r"^Table\s+\d+\b", lines[0], re.I))
        if is_md_table or is_caption_table:
            t_idx += 1
            blocks[f"{prefix}T{t_idx:02d}"] = ch
        else:
            p_idx += 1
            blocks[f"{prefix}P{p_idx:03d}"] = ch
    return blocks


def _normalize_human_gold_scores(raw: Any) -> list[dict[str, Any]]:
    """Normalize human_gold_scores from dict or list form into canonical records."""
    records: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            rid = str(entry.get("run_id") or entry.get("agent_id") or "").strip()
            iid = str(entry.get("item_id") or entry.get("question_id") or "").strip()
            val = entry.get("human_score") if "human_score" in entry else entry.get("score")
            if rid and iid and val is not None:
                try:
                    sc = int(val)
                except (TypeError, ValueError):
                    continue
                if sc in (0, 1, 2):
                    records.append({
                        "run_id": rid,
                        "item_id": iid,
                        "human_score": sc,
                        "annotator_id": str(entry.get("annotator_id") or entry.get("reviewer") or "human_gold").strip(),
                        "human_notes": str(entry.get("human_notes") or entry.get("notes") or entry.get("rationale") or "").strip(),
                    })
    elif isinstance(raw, dict):
        for rid, item_map in raw.items():
            if not isinstance(item_map, dict):
                continue
            for iid, val in item_map.items():
                if isinstance(val, dict):
                    sc_raw = val.get("human_score") if "human_score" in val else val.get("score")
                    notes = str(val.get("human_notes") or val.get("notes") or "").strip()
                    ann = str(val.get("annotator_id") or "human_gold").strip()
                else:
                    sc_raw = val
                    notes = ""
                    ann = "human_gold"
                if sc_raw is None:
                    continue
                try:
                    sc = int(sc_raw)
                except (TypeError, ValueError):
                    continue
                if sc in (0, 1, 2):
                    records.append({
                        "run_id": str(rid).strip(),
                        "item_id": str(iid).strip(),
                        "human_score": sc,
                        "annotator_id": ann,
                        "human_notes": notes,
                    })
    return records


def load_workorder_and_corpus(input_dir: Path, case_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load source-conditioned benchmark workorder and paragraph/table text corpus cleanly."""
    if case_data is None:
        cp = input_dir / "case.json"
        case_data = json.loads(cp.read_text(encoding="utf-8", errors="replace")) if cp.is_file() else {}

    items_raw: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    human_gold_raw: list[dict[str, Any]] = []
    workorder_files = ("benchmark-workorder.json", "workorder.json", "benchmark.json", "items.json")
    gold_files = ("human_gold_scores.json", "human_review.json", "human_calibration.json")

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
                if obj.get("human_gold_scores"):
                    human_gold_raw.extend(_normalize_human_gold_scores(obj["human_gold_scores"]))
            elif isinstance(obj, list):
                items_raw.extend(x for x in obj if isinstance(x, dict))

    for gname in gold_files:
        gp = input_dir / gname
        if gp.is_file():
            gobj = json.loads(gp.read_text(encoding="utf-8", errors="replace"))
            if isinstance(gobj, dict) and "human_gold_scores" in gobj:
                human_gold_raw.extend(_normalize_human_gold_scores(gobj["human_gold_scores"]))
            else:
                human_gold_raw.extend(_normalize_human_gold_scores(gobj))

    if case_data.get("human_gold_scores"):
        human_gold_raw.extend(_normalize_human_gold_scores(case_data["human_gold_scores"]))

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

    skip_names = {"case.json", "task.md", *workorder_files, *gold_files}
    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file() and not p.is_symlink()):
        if fp.name in skip_names:
            continue
        ext = fp.suffix.lower()
        rel = fp.relative_to(input_dir).as_posix()
        if ext in {".md", ".txt", ".csv"}:
            txt = fp.read_text(encoding="utf-8", errors="replace")
            if ext in {".md", ".txt"}:
                extracted = extract_id_tagged_blocks(txt, doc_prefix=fp.stem)
                if extracted:
                    for bid, btxt in extracted.items():
                        if bid not in sources:
                            sources[bid] = btxt
                else:
                    sources[rel] = txt
            else:
                sources[rel] = txt
        elif ext == ".json":
            try:
                jobj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if isinstance(jobj, dict):
                if isinstance(jobj.get("sources"), dict):
                    for k, v in jobj["sources"].items():
                        if str(k) not in sources:
                            sources[str(k)] = str(v)
                for idx, p in enumerate(jobj.get("paragraphs") or [], start=1):
                    if isinstance(p, dict):
                        pid = str(p.get("paragraph_id") or p.get("id") or f"{fp.stem}:P{idx:03d}")
                        if pid not in sources:
                            sources[pid] = str(p.get("text") or p.get("content") or "")
                for idx, t in enumerate(jobj.get("tables") or [], start=1):
                    if isinstance(t, dict):
                        tid = str(t.get("table_id") or t.get("id") or f"{fp.stem}:T{idx:02d}")
                        if tid not in sources:
                            sources[tid] = str(t.get("text") or t.get("content") or json.dumps(t.get("rows", []), ensure_ascii=False))

    # Deduplicate human_gold_scores keeping last occurrence per (run_id, item_id)
    dedup_gold: dict[tuple[str, str], dict[str, Any]] = {}
    for g in human_gold_raw:
        dedup_gold[(g["run_id"], g["item_id"])] = g

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
        "human_gold_scores": list(dedup_gold.values()),
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


def audit_corpus_arithmetic_consistency(sources: dict[str, str]) -> list[dict[str, Any]]:
    """Audit paragraph/table corpus for implied-denominator drift and mixed percentage decimal precision."""
    findings: list[dict[str, Any]] = []
    pct_n_re = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*[;,]\s*n\s*=\s*(\d+(?:,\d{3})*)|\b(\d+(?:\.\d+)?)\s*%\s*\(\s*n\s*=\s*(\d+(?:,\d{3})*)\s*\)")

    for sid, txt in sorted(sources.items()):
        # 1. Check implied denominator consistency across (pct%, n=count) pairs in the same block
        implied_denoms: list[tuple[float, float, int, str]] = []
        for m in pct_n_re.finditer(txt):
            pct_s = m.group(1) or m.group(3)
            cnt_s = m.group(2) or m.group(4)
            pct_v = float(pct_s)
            cnt_v = int(cnt_s.replace(",", ""))
            if pct_v >= 1.0 and cnt_v >= 10:
                implied_n = cnt_v / (pct_v / 100.0)
                implied_denoms.append((implied_n, pct_v, cnt_v, m.group(0)))

        if len(implied_denoms) >= 2:
            # Check pairwise relative spread in implied denominator within the same paragraph/sentence group
            denoms = [d[0] for d in implied_denoms]
            d_min, d_max = min(denoms), max(denoms)
            if d_min > 0 and (d_max - d_min) / d_min > 0.15:
                samples = [f"{s} -> implied N≈{round(n_est)}" for n_est, _, _, s in implied_denoms[:4]]
                findings.append({
                    "source_id": sid,
                    "code": "CORPUS_ARITHMETIC_DENOMINATOR_DRIFT",
                    "severity": "warning",
                    "message": (
                        f"Block {sid} reports (%, n) pairs with >15% implied-denominator spread "
                        f"({round(d_min)} vs {round(d_max)}): {'; '.join(samples)}. "
                        "Verify whether subgroup denominators or missing-data exclusions differ before authoring numeric benchmark items."
                    ),
                })

        # 2. Check table-like blocks for mixed percentage decimal precision across rows
        pct_precisions: set[int] = set()
        if sid.upper().startswith("T") or "Table " in txt:
            for line in txt.splitlines():
                nums = re.findall(r"\b\d+\.\d+\b", line)
                if len(nums) >= 3:
                    for num in nums:
                        val = float(num)
                        if 0.0 < val <= 100.0:
                            pct_precisions.add(len(num.split(".")[1]))
            if len(pct_precisions) > 1:
                findings.append({
                    "source_id": sid,
                    "code": "TABLE_DECIMAL_FORMAT_INCONSISTENCY",
                    "severity": "info",
                    "message": (
                        f"Table block {sid} mixes numeric decimal precisions ({sorted(pct_precisions)} decimal places); "
                        "set numeric_rel_tol explicitly so rounding differences do not cause false negatives."
                    ),
                })
    return findings


def _rubric_has_012_criteria(rubric: Any) -> bool:
    """Require explicit 0/1/2 scoring scale and criteria in the item rubric."""
    if rubric is None:
        return False
    if isinstance(rubric, dict):
        scale = str(rubric.get("scale") or rubric.get("scoring_scale") or "").replace(" ", "")
        has_scale = ("0/1/2" in scale) or all(str(k) in rubric or f"score_{k}" in rubric for k in (0, 1, 2))
        has_criteria = bool(
            rubric.get("criteria")
            or rubric.get("dimensions")
            or all(str(k) in rubric or f"score_{k}" in rubric for k in (0, 1, 2))
        )
        return bool(has_scale and has_criteria)
    if isinstance(rubric, str):
        s = rubric.replace(" ", "")
        return ("0/1/2" in s) or all(tok in rubric for tok in ("0", "1", "2"))
    return False


def build_source_conditioned_leakage_matrix(
    items: list[dict[str, Any]],
    sources: dict[str, str],
) -> list[dict[str, Any]]:
    """Build per-item source-conditioned leakage & prompt-only guessability audit matrix."""
    matrix: list[dict[str, Any]] = []
    num_re = re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?%?\b")

    for it in items:
        iid = it["item_id"]
        prompt = it.get("prompt") or ""
        prompt_low = _normalize_ws(prompt)
        req_pids = list(it.get("required_paragraph_ids") or [])
        leaks = _detect_prompt_leakage(it)

        # Keyword overlap already revealed in prompt
        exp_kws = list(it.get("expected_keywords") or [])
        kw_in_prompt = [kw for kw in exp_kws if kw.casefold() in prompt_low]
        kw_overlap = round(len(kw_in_prompt) / max(1, len(exp_kws)), 4) if exp_kws else 0.0

        # Verify expected values/keywords actually live in required source blocks (source-conditioned)
        req_text = "\n".join(sources.get(pid, "") for pid in req_pids)
        ev_tokens = [num_re.search(ev).group(0) for ev in (it.get("expected_values") or []) if num_re.search(ev)]
        ev_in_req_source = [tok for tok in ev_tokens if tok in req_text]
        source_dep_verified = (
            bool(req_pids)
            and all(pid in sources for pid in req_pids)
            and (it["unanswerable"] or (len(ev_in_req_source) == len(ev_tokens) if ev_tokens else bool(req_text.strip())))
        )

        # Cross-block redundancy: check whether non-required corpus blocks also contain the key expected values
        redundant_blocks: list[str] = []
        if ev_tokens and not it["unanswerable"]:
            for sid, stxt in sorted(sources.items()):
                if sid in req_pids:
                    continue
                if any(tok in stxt for tok in ev_tokens if len(tok) >= 3):
                    redundant_blocks.append(sid)

        # Prompt-only guessability risk classification
        is_binary_prompt = bool(re.search(r"\b(?:yes\s+or\s+no|true\s+or\s+false|whether\s+or\s+not)\b", prompt, re.I))
        if leaks:
            risk = "high"
            risk_reason = "direct_answer_or_value_leakage_in_prompt"
        elif is_binary_prompt or (exp_kws and kw_overlap >= 0.8 and not ev_tokens and not it["unanswerable"]):
            risk = "medium"
            risk_reason = "high_keyword_overlap_or_binary_prompt_guessable_without_source"
        elif not source_dep_verified:
            risk = "high"
            risk_reason = "missing_or_unverified_required_source_anchor"
        else:
            risk = "low"
            risk_reason = "requires_source_specific_evidence_not_present_in_prompt"

        matrix.append({
            "item_id": iid,
            "unanswerable": bool(it["unanswerable"]),
            "direct_prompt_leakage": bool(leaks),
            "direct_leakage_details": leaks,
            "expected_keyword_overlap_ratio": kw_overlap,
            "keywords_already_in_prompt": kw_in_prompt,
            "source_dependency_verified": source_dep_verified,
            "required_paragraph_ids": req_pids,
            "cross_block_redundancy_ids": redundant_blocks,
            "prompt_only_guessability_risk": risk,
            "risk_reason": risk_reason,
        })
    return matrix


def audit_item_bank(workorder: dict[str, Any]) -> dict[str, Any]:
    """Audit item-card readiness (non-empty reference_answer + verifiable anchor + 0/1/2 rubric), corpus references, and anti-leakage matrix."""
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

        if not _rubric_has_012_criteria(it.get("rubric")):
            item_errors.append("MISSING_012_RUBRIC_CRITERIA")
            blocking_issues.append({"item_id": iid, "code": "MISSING_012_RUBRIC_CRITERIA", "message": f"Item {iid} lacks explicit 0/1/2 rubric scale and grading criteria."})

        if not it["data_license"]:
            item_warns.append("MISSING_DATA_LICENSE")
            warnings.append({"item_id": iid, "code": "MISSING_DATA_LICENSE", "message": f"Item {iid} missing data_license / authorization metadata."})

        # Hard readiness gate: non-empty reference_answer is mandatory for every item card
        ref_str = "" if it.get("reference_answer") is None else str(it.get("reference_answer")).strip()
        if not ref_str:
            item_errors.append("EMPTY_REFERENCE_ANSWER")
            blocking_issues.append({"item_id": iid, "code": "EMPTY_REFERENCE_ANSWER", "message": f"Item {iid} has empty reference_answer (hard readiness gate requires non-empty reference_answer)."})

        # Hard readiness gate: verifiable source anchor(s) must be declared and exist in corpus
        if not it["required_paragraph_ids"]:
            item_errors.append("MISSING_VERIFIABLE_SOURCE_ANCHOR")
            blocking_issues.append({"item_id": iid, "code": "MISSING_VERIFIABLE_SOURCE_ANCHOR", "message": f"Item {iid} declares no required_paragraph_ids source anchor."})
        missing_pids = [pid for pid in it["required_paragraph_ids"] if pid not in sources]
        if missing_pids:
            item_errors.append("MISSING_CORPUS_PARAGRAPH")
            blocking_issues.append({"item_id": iid, "code": "MISSING_CORPUS_PARAGRAPH", "message": f"Item {iid} references missing corpus paragraph_id(s): {missing_pids}."})

        leaks = _detect_prompt_leakage(it)
        if leaks:
            item_errors.append("PROMPT_ANSWER_LEAKAGE")
            blocking_issues.append({"item_id": iid, "code": "PROMPT_ANSWER_LEAKAGE", "message": f"Item {iid} leaks answer in prompt: {'; '.join(leaks)}."})

        is_ready = len(item_errors) == 0
        item_reports.append({
            "item_id": iid,
            "task_type": it["task_type"] or "unspecified",
            "version": it["version"] or "unspecified",
            "data_license": it["data_license"] or "missing",
            "semantic_sha256": it["semantic_sha256"],
            "full_sha256": it["full_sha256"],
            "ready": is_ready,
            "card_ready": is_ready,
            "errors": item_errors,
            "warnings": item_warns,
            "leakage_details": leaks,
            "missing_paragraph_ids": missing_pids,
        })

    corpus_consistency_warnings = audit_corpus_arithmetic_consistency(sources)
    leakage_matrix = build_source_conditioned_leakage_matrix(items, sources)
    semantic_bank_sha = sha256_obj([{"id": i["item_id"], "s": i["semantic_sha256"]} for i in items])
    full_bank_sha = sha256_obj([{"id": i["item_id"], "f": i["full_sha256"]} for i in items])

    return {
        "ok": len(blocking_issues) == 0 and len(items) > 0,
        "item_count": len(items),
        "ready_item_count": sum(1 for r in item_reports if r["ready"]),
        "duplicate_item_ids": duplicate_ids,
        "leakage_item_ids": [r["item_id"] for r in item_reports if "PROMPT_ANSWER_LEAKAGE" in r["errors"]],
        "missing_corpus_item_ids": [r["item_id"] for r in item_reports if "MISSING_CORPUS_PARAGRAPH" in r["errors"] or "MISSING_VERIFIABLE_SOURCE_ANCHOR" in r["errors"]],
        "empty_ref_item_ids": [r["item_id"] for r in item_reports if "EMPTY_REFERENCE_ANSWER" in r["errors"]],
        "missing_rubric_item_ids": [r["item_id"] for r in item_reports if "MISSING_012_RUBRIC_CRITERIA" in r["errors"]],
        "semantic_bank_sha256": semantic_bank_sha,
        "item_bank_sha256": full_bank_sha,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "corpus_consistency_warnings": corpus_consistency_warnings,
        "source_conditioned_leakage_matrix": leakage_matrix,
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
