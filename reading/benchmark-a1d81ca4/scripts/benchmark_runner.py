#!/usr/bin/env python3
"""Deterministic Literature Reading Benchmark Harness & Failure-to-Regression Engine (benchmark-a1d81ca4).

Pure Python 3.12 standard library. Implements Organizer Revision 2:
- Runs item_bank_auditor preflight (required fields, duplicate IDs, corpus paragraph verification,
  empty reference_answer detection, prompt-answer leakage check, dual SHA-256 hashing).
- Multi-dimensional 0 / 1 / 2 deterministic scorer:
  (a) Tolerance-aware numeric + SI/scientific unit evaluator separating numeric_value_mismatch from unit_mismatch.
  (b) Strict structured quote grounding (paragraph_id must exist in corpus & quote must match inside THAT paragraph)
      plus inline-citation-count vs structured-evidence-count parity check (citation_count_mismatch).
  (c) Unanswerable honest-abstention detector (false_certainty_on_missing_data).
  (d) Six-class failure taxonomy (+ schema_or_parse_error).
- Multi-run repetition stability statistics (mean/std/min/max & per-item flip rate) + Harbor budget/latency audit.
- Automatic failure-bank -> next-version targeted regression item drafts.
- Comparability-aware version diff (breaking_modified vs metadata_only_modified) + incremental_recompute_ids.
- Explicit disclaimer: synthetic/local deterministic checks are NOT official DSH scores or human semantic review.
- Outputs: result.json, benchmark_panel.json, benchmark_report.md.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import budget_guard
    import final_audit
    import gap_gate
    import item_bank_auditor
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log
    from citation_anchors import verify_exact_quote
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import budget_guard
    import final_audit
    import gap_gate
    import item_bank_auditor
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log
    from citation_anchors import verify_exact_quote


SCORE_DISCLAIMER = (
    "Deterministic local rule checks and synthetic checker logs are NOT official DeepSeek Harness (DSH) "
    "scores or independent human semantic validation. Provided sources are paragraph/table text, not PDF-pixel gold."
)

FAILURE_TAXONOMY = (
    "missing_answer_or_field",
    "ungrounded_quote_or_id",
    "citation_count_mismatch",
    "numeric_value_mismatch",
    "unit_mismatch",
    "false_certainty_on_missing_data",
    "schema_or_parse_error",
)

# Harbor execution budgets
BUDGET_MAX_REQUESTS = 64
BUDGET_MAX_SECONDS = 900.0
BUDGET_MAX_TOKENS_PER_RESP = 16384

# Unit canonicalization: (dimension, scale_to_base, offset_to_base)
# Case-sensitive exact keys checked first (e.g. 'M' molarity vs 'm' meters), then lowercase.
EXACT_UNIT_MAP: dict[str, tuple[str, float, float]] = {
    "M": ("molarity", 1.0, 0.0),
    "mM": ("molarity", 1e-3, 0.0),
    "uM": ("molarity", 1e-6, 0.0),
    "µM": ("molarity", 1e-6, 0.0),
    "nM": ("molarity", 1e-9, 0.0),
    "m": ("length", 1.0, 0.0),
    "cm": ("length", 1e-2, 0.0),
    "mm": ("length", 1e-3, 0.0),
    "um": ("length", 1e-6, 0.0),
    "µm": ("length", 1e-6, 0.0),
    "nm": ("length", 1e-9, 0.0),
}

LOWER_UNIT_MAP: dict[str, tuple[str, float, float]] = {
    "%": ("percent", 1.0, 0.0),
    "pct": ("percent", 1.0, 0.0),
    "percent": ("percent", 1.0, 0.0),
    "v": ("voltage", 1.0, 0.0),
    "mv": ("voltage", 1e-3, 0.0),
    "uv": ("voltage", 1e-6, 0.0),
    "kv": ("voltage", 1e3, 0.0),
    "a": ("current", 1.0, 0.0),
    "ma": ("current", 1e-3, 0.0),
    "ua": ("current", 1e-6, 0.0),
    "µa": ("current", 1e-6, 0.0),
    "g": ("mass", 1.0, 0.0),
    "kg": ("mass", 1e3, 0.0),
    "mg": ("mass", 1e-3, 0.0),
    "ug": ("mass", 1e-6, 0.0),
    "µg": ("mass", 1e-6, 0.0),
    "ng": ("mass", 1e-9, 0.0),
    "s": ("time", 1.0, 0.0),
    "ms": ("time", 1e-3, 0.0),
    "min": ("time", 60.0, 0.0),
    "h": ("time", 3600.0, 0.0),
    "hr": ("time", 3600.0, 0.0),
    "hours": ("time", 3600.0, 0.0),
    "mg/l": ("mass_conc", 1.0, 0.0),
    "ug/ml": ("mass_conc", 1.0, 0.0),
    "µg/ml": ("mass_conc", 1.0, 0.0),
    "g/l": ("mass_conc", 1000.0, 0.0),
    "ppm": ("ppm", 1.0, 0.0),
    "ppb": ("ppm", 1e-3, 0.0),
    "ma/cm2": ("current_density", 1.0, 0.0),
    "ma/cm^2": ("current_density", 1.0, 0.0),
    "ma cm-2": ("current_density", 1.0, 0.0),
    "a/cm2": ("current_density", 1000.0, 0.0),
    "a/cm^2": ("current_density", 1000.0, 0.0),
    "mv/dec": ("tafel_slope", 1.0, 0.0),
    "v/dec": ("tafel_slope", 1000.0, 0.0),
    "k": ("temperature", 1.0, 0.0),
    "°c": ("temperature", 1.0, 273.15),
    "degc": ("temperature", 1.0, 273.15),
    "ev": ("energy_ev", 1.0, 0.0),
    "mev": ("energy_ev", 1e-3, 0.0),
    "j": ("energy_j", 1.0, 0.0),
    "kj": ("energy_j", 1e3, 0.0),
}

QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9_])([-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*"
    r"(%|pct|percent|mV/dec|V/dec|mA/cm\^?2|mA\s*cm-2|A/cm\^?2|mg/L|µg/mL|ug/mL|g/L|ppm|ppb|"
    r"mV|uV|kV|V|mA|µA|uA|A|kg|mg|µg|ug|ng|g|cm|mm|µm|um|nm|mM|µM|uM|nM|M|m|"
    r"ms|min|hours|hr|h|s|°C|degC|K|meV|eV|kJ|J)?(?![A-Za-z0-9_])"
)

INLINE_BRACKET_CITE_RE = re.compile(r"\[((?:P|S|T|Table\s*|Fig\.?\s*|Ref\.?\s*)?\d+[A-Za-z]?(?:\s*,\s*(?:P|S|T|Table\s*|Fig\.?\s*|Ref\.?\s*)?\d+[A-Za-z]?)*)\]")
INLINE_PAREN_PID_RE = re.compile(r"\(((?:P|T|S)\d{2,4}(?:\s*,\s*(?:P|T|S)\d{2,4})*)\)")

ABSTAIN_STATUSES = {"insufficient_evidence", "not_reported", "unknown", "unanswerable", "abstain", "cannot_determine"}
ABSTAIN_CUES = (
    "not reported", "not provided", "insufficient evidence", "insufficient",
    "cannot determine", "cannot be determined", "no evidence", "missing from source",
    "未报告", "未提供", "无法确定", "证据不足", "缺少",
)


def _canonicalize_quantity(num: float, unit_raw: str) -> tuple[str, float]:
    u = " ".join((unit_raw or "").strip().split())
    if not u:
        return "dimensionless", num
    if u in EXACT_UNIT_MAP:
        dim, scale, offset = EXACT_UNIT_MAP[u]
        return dim, num * scale + offset
    ul = u.lower()
    if ul in LOWER_UNIT_MAP:
        dim, scale, offset = LOWER_UNIT_MAP[ul]
        return dim, num * scale + offset
    return f"custom:{ul}", num


def parse_expected_quantity(spec: str) -> tuple[float, str, str, float] | None:
    m = QUANTITY_RE.search(spec.strip())
    if not m:
        return None
    raw_num = float(m.group(1).replace(",", ""))
    raw_unit = (m.group(2) or "").strip()
    dim, canon_val = _canonicalize_quantity(raw_num, raw_unit)
    return raw_num, raw_unit, dim, canon_val


def extract_answer_quantities(answer_text: str) -> list[tuple[float, str, str, float]]:
    out: list[tuple[float, str, str, float]] = []
    for m in QUANTITY_RE.finditer(answer_text):
        try:
            raw_num = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        raw_unit = (m.group(2) or "").strip()
        dim, canon_val = _canonicalize_quantity(raw_num, raw_unit)
        out.append((raw_num, raw_unit, dim, canon_val))
    return out


def extract_inline_citation_markers(answer_text: str) -> list[str]:
    """Extract unique inline citation tokens like [P001], [1], [Table 1], (P002)."""
    tokens: list[str] = []
    for pat in (INLINE_BRACKET_CITE_RE, INLINE_PAREN_PID_RE):
        for m in pat.finditer(answer_text):
            for part in m.group(1).split(","):
                tok = " ".join(part.strip().split())
                # Exclude plain 4-digit years like [2024]
                if re.fullmatch(r"(?:19|20)\d{2}", tok):
                    continue
                if tok and tok not in tokens:
                    tokens.append(tok)
    return tokens


def _close_enough(a: float, b: float, rel_tol: float) -> bool:
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=1e-6)


def check_expected_value(expected_spec: str, answer_text: str, rel_tol: float = 1e-3) -> str:
    """Return 'match', 'unit_mismatch', or 'numeric_value_mismatch'."""
    parsed = parse_expected_quantity(expected_spec)
    if parsed is None:
        return "match" if expected_spec.casefold() in answer_text.casefold() else "numeric_value_mismatch"

    exp_raw, exp_unit, exp_dim, exp_canon = parsed
    cands = extract_answer_quantities(answer_text)
    if not cands:
        return "numeric_value_mismatch"

    # 1. Exact canonical match (same dimension + within tolerance, or percent vs decimal ratio if % expected)
    for c_raw, c_unit, c_dim, c_canon in cands:
        if exp_dim == c_dim and _close_enough(c_canon, exp_canon, rel_tol):
            return "match"
        # Allow unitless ratio e.g. 0.885 when 88.5% expected only if no unit conflict? No, require unit when expected has unit!

    # 2. Check if raw number matches within tolerance but unit is missing or mismatched
    if exp_unit:
        for c_raw, c_unit, c_dim, _ in cands:
            if _close_enough(c_raw, exp_raw, rel_tol) and c_dim != exp_dim:
                return "unit_mismatch"
            if _close_enough(c_raw, exp_raw, rel_tol) and not c_unit:
                return "unit_mismatch"
            # e.g., expected 88.5 mV, answered 88.5 V (same dimension, wrong scale)
            if _close_enough(c_raw, exp_raw, rel_tol) and c_dim == exp_dim and c_unit.lower() != exp_unit.lower():
                return "unit_mismatch"

    return "numeric_value_mismatch"


def score_item_012(
    item: dict[str, Any],
    response: Any,
    sources: dict[str, str],
) -> dict[str, Any]:
    """Deterministic multi-dimensional 0 / 1 / 2 scorer with six-class failure taxonomy."""
    iid = item["item_id"]
    if response is None:
        return {
            "item_id": iid,
            "task_type": item["task_type"] or "unspecified",
            "score": 0,
            "max_score": 2,
            "grounded_quotes": 0,
            "ungrounded_quotes": 0,
            "inline_citation_count": 0,
            "structured_evidence_count": 0,
            "failure_tags": ["missing_answer_or_field"],
            "rationale": "No response emitted for benchmark item.",
            "semantic_sha256": item["semantic_sha256"],
        }
    if not isinstance(response, dict):
        return {
            "item_id": iid,
            "task_type": item["task_type"] or "unspecified",
            "score": 0,
            "max_score": 2,
            "grounded_quotes": 0,
            "ungrounded_quotes": 0,
            "inline_citation_count": 0,
            "structured_evidence_count": 0,
            "failure_tags": ["schema_or_parse_error"],
            "rationale": f"Response payload has invalid type {type(response).__name__}; expected dict.",
            "semantic_sha256": item["semantic_sha256"],
        }

    answer_txt = str(response.get("answer") or response.get("text") or "").strip()
    status_val = str(response.get("status") or "").strip().lower()
    raw_quotes = response.get("evidence") if "evidence" in response else response.get("quotes", [])

    failures: list[str] = []
    details: list[str] = []

    if raw_quotes is None:
        quotes: list[Any] = []
    elif isinstance(raw_quotes, list):
        quotes = raw_quotes
    else:
        quotes = []
        failures.append("schema_or_parse_error")
        details.append("evidence field must be a list")

    if not answer_txt and status_val not in ABSTAIN_STATUSES:
        failures.append("missing_answer_or_field")
        details.append("empty answer text")

    # (b) Strict structured quote anchoring (no cross-paragraph borrowing)
    grounded_count = 0
    ungrounded_count = 0
    cited_pids: set[str] = set()
    corpus_all = "\n".join(sources.values())

    for q in quotes:
        if isinstance(q, dict):
            pid = str(q.get("paragraph_id") or "").strip()
            qtxt = str(q.get("quote") or "").strip()
            if not pid or pid not in sources:
                ungrounded_count += 1
                details.append(f"unknown_or_missing paragraph_id={pid!r}")
                continue
            if verify_exact_quote(qtxt, sources[pid]):
                grounded_count += 1
                cited_pids.add(pid)
            else:
                ungrounded_count += 1
                details.append(f"quote not found verbatim in paragraph {pid!r}")
        elif isinstance(q, str):
            if verify_exact_quote(q, corpus_all):
                grounded_count += 1
            else:
                ungrounded_count += 1
                details.append("unstructured quote not found in corpus")
        else:
            failures.append("schema_or_parse_error")
            details.append("non-string/non-dict item inside evidence list")

    if ungrounded_count > 0:
        failures.append("ungrounded_quote_or_id")

    # Inline citation count vs structured evidence count check (NotebookLM #2416 guard)
    inline_markers = extract_inline_citation_markers(answer_txt)
    inline_count = len(inline_markers)
    structured_count = len(quotes)
    if inline_count > 0 and inline_count != structured_count:
        failures.append("citation_count_mismatch")
        details.append(f"inline citations ({inline_count}: {inline_markers}) != structured evidence count ({structured_count})")

    # (c) Unanswerable honest-abstention check vs (a) tolerance-aware numeric/unit + keyword check
    if item["unanswerable"]:
        abstained = status_val in ABSTAIN_STATUSES or any(cue in answer_txt.casefold() for cue in ABSTAIN_CUES)
        if not abstained:
            failures.append("false_certainty_on_missing_data")
            details.append("failed to abstain on unanswerable item")
    else:
        rel_tol = float(item.get("numeric_rel_tol", 1e-3))
        for ev_val in item["expected_values"]:
            verdict = check_expected_value(ev_val, answer_txt, rel_tol=rel_tol)
            if verdict == "unit_mismatch":
                failures.append("unit_mismatch")
                details.append(f"unit mismatch for expected {ev_val!r}")
            elif verdict == "numeric_value_mismatch":
                failures.append("numeric_value_mismatch")
                details.append(f"numeric mismatch for expected {ev_val!r}")

        for kw in item["expected_keywords"]:
            if kw.casefold() not in answer_txt.casefold():
                failures.append("missing_answer_or_field")
                details.append(f"missing expected keyword {kw!r}")

        if item["required_paragraph_ids"] and grounded_count > 0:
            if not (cited_pids & set(item["required_paragraph_ids"])):
                failures.append("ungrounded_quote_or_id")
                details.append(f"grounded citations {sorted(cited_pids)} did not cover required_paragraph_ids {item['required_paragraph_ids']}")

        if grounded_count == 0 and not item["unanswerable"]:
            failures.append("ungrounded_quote_or_id")
            details.append("no verified source quote provided for answerable item")

    unique_failures = sorted(set(failures))
    severe_tags = {"false_certainty_on_missing_data", "schema_or_parse_error"}
    if not unique_failures:
        score = 2
    elif len(unique_failures) == 1 and not (set(unique_failures) & severe_tags) and answer_txt:
        score = 1
    else:
        score = 0

    return {
        "item_id": iid,
        "task_type": item["task_type"] or "unspecified",
        "score": score,
        "max_score": 2,
        "grounded_quotes": grounded_count,
        "ungrounded_quotes": ungrounded_count,
        "inline_citation_count": inline_count,
        "structured_evidence_count": structured_count,
        "failure_tags": unique_failures,
        "rationale": "Passed all deterministic checks." if score == 2 else "; ".join(details[:4]),
        "semantic_sha256": item["semantic_sha256"],
    }


def _infer_agent_group(run: dict[str, Any], run_id: str) -> str:
    if run.get("agent_id"):
        return str(run["agent_id"])
    if run.get("agent_name"):
        return str(run["agent_name"])
    return re.sub(r"(?:[-_](?:rep|run|r)\d+)$", "", run_id, flags=re.I)


def evaluate_runs_and_stability(
    workorder: dict[str, Any],
    item_audit: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Score supplied runs, compute multi-run stability & budget audit, and draft regression items."""
    items = workorder["items"]
    sources = workorder["sources"]
    runs = workorder["candidate_runs"]

    scoreboards: list[dict[str, Any]] = []
    failure_bank: list[dict[str, Any]] = []

    if not runs:
        scoreboards.append({
            "run_id": "item_bank_readiness_audit_only",
            "mode": "synthetic_workorder_readiness",
            "items_evaluated": len(items),
            "ready_item_count": item_audit["ready_item_count"],
            "item_results": item_audit["item_reports"],
            "note": "No candidate_runs supplied; performed item-bank quality and anti-leakage audit only without fabricating predictions.",
        })
        return scoreboards, failure_bank, {"multi_run_agents": {}, "budget_audit": []}, []

    agent_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    budget_audit: list[dict[str, Any]] = []

    for r_idx, run in enumerate(runs, start=1):
        run_id = str(run.get("run_id") or run.get("agent_name") or f"run_{r_idx:02d}")
        agent_group = _infer_agent_group(run, run_id)
        preds = run.get("responses") or run.get("answers") or {}
        if isinstance(preds, list):
            pred_map = {str(p.get("item_id") or p.get("question_id")): p for p in preds if isinstance(p, dict)}
        elif isinstance(preds, dict):
            pred_map = {str(k): v for k, v in preds.items()}
        else:
            pred_map = {}

        item_scores = [score_item_012(it, pred_map.get(it["item_id"]), sources) for it in items]
        total_pts = sum(s["score"] for s in item_scores)
        max_pts = sum(s["max_score"] for s in item_scores)
        pct = round(100.0 * total_pts / max(1, max_pts), 2)

        by_type: dict[str, dict[str, int]] = {}
        for s in item_scores:
            t = s["task_type"]
            by_type.setdefault(t, {"earned": 0, "possible": 0, "count": 0})
            by_type[t]["earned"] += s["score"]
            by_type[t]["possible"] += s["max_score"]
            by_type[t]["count"] += 1
            if s["score"] < 2:
                failure_bank.append({
                    "run_id": run_id,
                    "agent_id": agent_group,
                    "item_id": s["item_id"],
                    "task_type": t,
                    "score": s["score"],
                    "failure_tags": s["failure_tags"],
                    "rationale": s["rationale"],
                })

        # Optional latency / request / token budget audit
        lat = run.get("latency_sec") if run.get("latency_sec") is not None else run.get("duration_sec")
        reqs = run.get("model_requests")
        max_toks = run.get("max_response_tokens")
        budget_violations: list[str] = []
        if lat is not None and float(lat) > BUDGET_MAX_SECONDS:
            budget_violations.append(f"latency_sec {lat} > {BUDGET_MAX_SECONDS}")
        if reqs is not None and int(reqs) > BUDGET_MAX_REQUESTS:
            budget_violations.append(f"model_requests {reqs} > {BUDGET_MAX_REQUESTS}")
        if max_toks is not None and int(max_toks) > BUDGET_MAX_TOKENS_PER_RESP:
            budget_violations.append(f"max_response_tokens {max_toks} > {BUDGET_MAX_TOKENS_PER_RESP}")

        budget_entry = {
            "run_id": run_id,
            "agent_id": agent_group,
            "latency_sec": float(lat) if lat is not None else None,
            "model_requests": int(reqs) if reqs is not None else None,
            "max_response_tokens": int(max_toks) if max_toks is not None else None,
            "within_harbor_budget": len(budget_violations) == 0,
            "violations": budget_violations,
        }
        budget_audit.append(budget_entry)

        sb = {
            "run_id": run_id,
            "agent_id": agent_group,
            "total_score": total_pts,
            "max_score": max_pts,
            "normalized_pct": pct,
            "breakdown_by_task_type": by_type,
            "budget_check": budget_entry,
            "item_results": item_scores,
        }
        scoreboards.append(sb)
        agent_runs[agent_group].append(sb)

    # Multi-run repetition stability statistics
    multi_run_agents: dict[str, Any] = {}
    for ag, rlist in agent_runs.items():
        pcts = [r["normalized_pct"] for r in rlist]
        item_series: dict[str, list[int]] = defaultdict(list)
        for r in rlist:
            for ir in r["item_results"]:
                item_series[ir["item_id"]].append(ir["score"])
        flipped = sorted(iid for iid, vals in item_series.items() if len(set(vals)) > 1)
        per_item_var = {
            iid: round(statistics.pvariance(vals), 4) if len(vals) > 1 else 0.0
            for iid, vals in item_series.items()
        }
        multi_run_agents[ag] = {
            "repetitions": len(rlist),
            "mean_pct": round(statistics.mean(pcts), 2),
            "std_pct": round(statistics.pstdev(pcts), 2) if len(pcts) > 1 else 0.0,
            "min_pct": min(pcts),
            "max_pct": max(pcts),
            "flipped_items": flipped,
            "flip_rate": round(len(flipped) / max(1, len(item_series)), 4),
            "per_item_score_variance": per_item_var,
        }

    # Failure bank -> next-version targeted regression item drafts
    tag_guidance = {
        "unit_mismatch": "Create a unit-conversion trap item requiring explicit SI prefix conversion (e.g., mV vs V or ug/mL vs mg/L) and penalizing unitless scalars.",
        "numeric_value_mismatch": "Add a distracting secondary metric in the same table/paragraph to verify exact row-column numerical lookup.",
        "ungrounded_quote_or_id": "Add a cross-paragraph decoy with similar wording in P002 while the true parameter lives only in P001 to catch borrowed citations.",
        "citation_count_mismatch": "Require at least two distinct source paragraphs and enforce strict parity between inline citation markers and structured evidence objects.",
        "false_certainty_on_missing_data": "Add a near-miss unanswerable question asking for a subgroup or durability metric explicitly absent from the provided text.",
        "missing_answer_or_field": "Add a multi-part structured question requiring both quantitative value and experimental condition qualifier.",
        "schema_or_parse_error": "Add a strict JSON schema conformance test validating answer string and list-typed evidence objects.",
    }
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fb in failure_bank:
        for tg in fb["failure_tags"]:
            by_tag[tg].append(fb)

    regression_drafts: list[dict[str, Any]] = []
    for idx, (tag, hits) in enumerate(sorted(by_tag.items(), key=lambda kv: (-len(kv[1]), kv[0])), start=1):
        regression_drafts.append({
            "draft_item_id": f"REG-{idx:02d}-{tag.upper()[:12]}",
            "target_failure_tag": tag,
            "observed_failure_count": len(hits),
            "source_item_ids": sorted({h["item_id"] for h in hits}),
            "source_run_ids": sorted({h["run_id"] for h in hits}),
            "draft_design_guidance": tag_guidance.get(tag, "Construct a targeted regression item isolating this failure mode."),
            "proposed_rubric_scale": "0/1/2",
            "status": "draft_requires_human_review",
        })

    stability_summary = {
        "multi_run_agents": multi_run_agents,
        "budget_audit": budget_audit,
    }
    return scoreboards, failure_bank, stability_summary, regression_drafts


def compute_version_diff(prev_snapshot: dict[str, Any] | None, cur_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare semantic_sha256 vs full_sha256 to separate breaking edits from metadata-only edits."""
    cur_sem = {i["item_id"]: i["semantic_sha256"] for i in cur_items}
    cur_full = {i["item_id"]: i["full_sha256"] for i in cur_items}

    if not prev_snapshot or not isinstance(prev_snapshot, dict):
        return {
            "has_prior_snapshot": False,
            "comparable_with_prior": False,
            "full_bank_comparable": False,
            "added": sorted(cur_sem.keys()),
            "removed": [],
            "breaking_modified": [],
            "metadata_only_modified": [],
            "unchanged": [],
            "incremental_recompute_ids": sorted(cur_sem.keys()),
            "reusable_prior_item_ids": [],
        }

    prev_sem: dict[str, str] = {}
    prev_full: dict[str, str] = {}
    for p in (prev_snapshot.get("items") or []):
        if not isinstance(p, dict):
            continue
        pid = str(p.get("item_id") or p.get("id") or "")
        if not pid:
            continue
        prev_sem[pid] = str(p.get("semantic_sha256") or p.get("item_sha256") or "")
        prev_full[pid] = str(p.get("full_sha256") or p.get("item_sha256") or "")

    added = sorted(set(cur_sem) - set(prev_sem))
    removed = sorted(set(prev_sem) - set(cur_sem))
    shared = sorted(set(cur_sem) & set(prev_sem))

    breaking_modified: list[str] = []
    metadata_only_modified: list[str] = []
    unchanged: list[str] = []

    for iid in shared:
        if cur_sem[iid] != prev_sem[iid]:
            breaking_modified.append(iid)
        elif cur_full[iid] != prev_full[iid]:
            metadata_only_modified.append(iid)
        else:
            unchanged.append(iid)

    recompute_ids = sorted(added + breaking_modified)
    reusable_ids = sorted(unchanged + metadata_only_modified)
    return {
        "has_prior_snapshot": True,
        "comparable_with_prior": not (breaking_modified or removed),
        "full_bank_comparable": not (added or removed or breaking_modified),
        "added": added,
        "removed": removed,
        "breaking_modified": breaking_modified,
        "metadata_only_modified": metadata_only_modified,
        "unchanged": unchanged,
        "incremental_recompute_ids": recompute_ids,
        "reusable_prior_item_ids": reusable_ids,
    }


def render_benchmark_report_md(panel: dict[str, Any]) -> str:
    """Render concise, auditable Markdown evaluation report."""
    audit = panel["item_bank_audit"]
    vdiff = panel["version_diff"]
    lines = [
        f"# 文献阅读 Benchmark 评测与审计报告 (`{panel['case_id']}`)",
        "",
        f"> **重要边界声明**：{panel['score_disclaimer']}",
        "",
        "## 1. 题库快照与防泄漏预检",
        "",
        f"- **语料模态**: `{panel['source_modality']}`",
        f"- **语义题库哈希 (`semantic_bank_sha256`)**: `{audit['semantic_bank_sha256'][:16]}`",
        f"- **完整题库哈希 (`item_bank_sha256`)**: `{audit['item_bank_sha256'][:16]}`",
        f"- **题卡总数 / 就绪数**: `{audit['item_count']}` / `{audit['ready_item_count']}`",
        f"- **阻断性建题问题**: `{len(audit['blocking_issues'])}`（泄漏题: `{audit['leakage_item_ids']}`｜缺失语料段落题: `{audit['missing_corpus_item_ids']}`｜重复题号: `{audit['duplicate_item_ids']}`）",
        "",
    ]
    if audit["blocking_issues"]:
        lines.append("### 预检阻断明细")
        for bi in audit["blocking_issues"]:
            lines.append(f"- **[{bi['code']}]** `{bi['item_id']}`: {bi['message']}")
        lines.append("")

    lines.extend(["## 2. 确定性 0/1/2 评分面板", ""])
    for sb in panel["scoreboards"]:
        if sb.get("mode") == "synthetic_workorder_readiness":
            lines.append(f"- `{sb['run_id']}`: 仅执行题卡就绪度审计（无候选预测输入），就绪 `{sb['ready_item_count']}/{sb['items_evaluated']}`。")
            continue
        lines.append(f"### Run: `{sb['run_id']}`（Agent: `{sb['agent_id']}` | 得分: `{sb['total_score']}/{sb['max_score']}` = `{sb['normalized_pct']}%`）")
        lines.append("| Item ID | 题型 | 得分 | 核验引文/未核验 | 正文引标/结构引文 | 失败标签 | 判分依据 |")
        lines.append("|---|---|---|---|---|---|---|")
        for ir in sb["item_results"]:
            tags = ", ".join(ir["failure_tags"]) if ir["failure_tags"] else "PASS"
            rat = ir["rationale"].replace("|", "\\|")
            lines.append(
                f"| `{ir['item_id']}` | `{ir['task_type']}` | `{ir['score']}/2` | "
                f"`{ir['grounded_quotes']}/{ir['ungrounded_quotes']}` | "
                f"`{ir['inline_citation_count']}/{ir['structured_evidence_count']}` | `{tags}` | {rat} |"
            )
        lines.append("")

    mr = panel["stability_summary"]["multi_run_agents"]
    if mr:
        lines.extend(["## 3. 多轮重复运行稳定性与预算审计", ""])
        lines.append("| Agent | 轮次 | 均值 (%) | 标准差 (%) | 最小–最大 (%) | 翻转题 (`flipped_items`) |")
        lines.append("|---|---|---|---|---|---|")
        for ag, st in mr.items():
            fl = ", ".join(st["flipped_items"]) if st["flipped_items"] else "无"
            lines.append(f"| `{ag}` | {st['repetitions']} | {st['mean_pct']} | {st['std_pct']} | {st['min_pct']}–{st['max_pct']} | {fl} |")
        lines.append("")

    if panel["regression_item_drafts"]:
        lines.extend(["## 4. 失败样例库 → 下一版专项回归题草案", ""])
        for dr in panel["regression_item_drafts"]:
            lines.append(
                f"- **{dr['draft_item_id']}**（针对 `{dr['target_failure_tag']}`，命中 {dr['observed_failure_count']} 次，来源题 `{dr['source_item_ids']}`）：{dr['draft_design_guidance']}"
            )
        lines.append("")

    lines.extend([
        "## 5. 版本快照对比与增量重算清单",
        "",
        f"- **存在历史快照**: `{vdiff['has_prior_snapshot']}`｜**旧题可比 (`comparable_with_prior`)**: `{vdiff['comparable_with_prior']}`",
        f"- **破坏可比性变更 (`breaking_modified`)**: `{vdiff['breaking_modified']}`",
        f"- **仅元数据变更（历史分可复用）(`metadata_only_modified`)**: `{vdiff['metadata_only_modified']}`",
        f"- **需增量重算子集 (`incremental_recompute_ids`)**: `{vdiff['incremental_recompute_ids']}`",
        f"- **可直接复用历史分子集 (`reusable_prior_item_ids`)**: `{vdiff['reusable_prior_item_ids']}`",
        "",
    ])
    return "\n".join(lines)


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="a1d81ca4-benchmark-harness")

    workorder = item_bank_auditor.load_workorder_and_corpus(input_dir)
    case_id = workorder["case_id"]
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="benchmark-a1d81ca4")

    budget_guard.tick_phase(budget_file, "audit_item_bank")
    item_audit = item_bank_auditor.audit_item_bank(workorder)

    budget_guard.tick_phase(budget_file, "score_and_snapshot")
    scoreboards, failure_bank, stability_summary, regression_drafts = evaluate_runs_and_stability(workorder, item_audit)
    version_diff = compute_version_diff(workorder.get("previous_snapshot"), workorder["items"])

    panel = {
        "case_id": case_id,
        "item_bank_sha256": item_audit["item_bank_sha256"],
        "semantic_bank_sha256": item_audit["semantic_bank_sha256"],
        "source_modality": workorder["source_modality"],
        "score_disclaimer": SCORE_DISCLAIMER,
        "item_bank_audit": item_audit,
        "items": workorder["items"],
        "scoreboards": scoreboards,
        "stability_summary": stability_summary,
        "failure_taxonomy_bank": failure_bank,
        "regression_item_drafts": regression_drafts,
        "version_diff": version_diff,
    }
    panel_path = output_dir / "benchmark_panel.json"
    result_guard.atomic_write_json(panel_path, panel)

    report_md = render_benchmark_report_md(panel)
    (output_dir / "benchmark_report.md").write_text(report_md, encoding="utf-8")

    append_audit_entry(
        audit_file,
        phase="benchmark",
        action="panel_and_report_generated",
        actor="benchmark_runner.py",
        output={
            "item_count": len(workorder["items"]),
            "semantic_bank_sha256": item_audit["semantic_bank_sha256"],
            "blocking_audit_issues": len(item_audit["blocking_issues"]),
            "runs_scored": len(workorder["candidate_runs"]),
            "failures": len(failure_bank),
        },
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    if not workorder["items"]:
        gaps.append({"code": "EMPTY_BENCHMARK_ITEM_BANK", "severity": "error", "source": "load_workorder_and_corpus", "message": "No benchmark items found in case or workorder."})
    for bi in item_audit["blocking_issues"]:
        gaps.append({"code": bi["code"], "severity": "warning", "source": "item_bank_auditor", "message": bi["message"]})
    if not workorder["candidate_runs"]:
        gaps.append({"code": "NO_CANDIDATE_RUNS_PROVIDED", "severity": "info", "source": "evaluate_runs_and_stability", "message": "Executed item-bank quality/leakage audit only; no candidate runs provided."})

    tag_counts = Counter(t for fb in failure_bank for t in fb["failure_tags"])
    patch = {
        "artifacts": ["result.json", "benchmark_panel.json", "benchmark_report.md"],
        "sources": sorted(workorder["sources"].keys()),
        "uncertainty": SCORE_DISCLAIMER,
        "item_bank_sha256": item_audit["item_bank_sha256"],
        "semantic_bank_sha256": item_audit["semantic_bank_sha256"],
        "source_modality": workorder["source_modality"],
        "score_disclaimer": SCORE_DISCLAIMER,
        "item_bank_audit": {
            "ok": item_audit["ok"],
            "ready_item_count": item_audit["ready_item_count"],
            "blocking_issue_count": len(item_audit["blocking_issues"]),
            "leakage_item_ids": item_audit["leakage_item_ids"],
            "missing_corpus_item_ids": item_audit["missing_corpus_item_ids"],
            "duplicate_item_ids": item_audit["duplicate_item_ids"],
        },
        "scoreboards": scoreboards,
        "stability_summary": stability_summary,
        "failure_taxonomy_bank": failure_bank,
        "failure_tag_counts": dict(tag_counts),
        "regression_item_drafts": regression_drafts,
        "version_diff": version_diff,
        "metrics": {
            "item_count": len(workorder["items"]),
            "ready_item_count": item_audit["ready_item_count"],
            "audit_blocking_issue_count": len(item_audit["blocking_issues"]),
            "source_block_count": len(workorder["sources"]),
            "evaluated_run_count": len(workorder["candidate_runs"]),
            "failure_case_count": len(failure_bank),
            "regression_draft_count": len(regression_drafts),
            "incremental_recompute_count": len(version_diff["incremental_recompute_ids"]),
        },
        "metric_provenance": {
            "item_count": "benchmark_panel.json:items",
            "ready_item_count": "benchmark_panel.json:item_bank_audit",
            "audit_blocking_issue_count": "benchmark_panel.json:item_bank_audit",
            "source_block_count": "input_corpus",
            "evaluated_run_count": "benchmark_panel.json:scoreboards",
            "failure_case_count": "benchmark_panel.json:failure_taxonomy_bank",
            "regression_draft_count": "benchmark_panel.json:regression_item_drafts",
            "incremental_recompute_count": "benchmark_panel.json:version_diff",
        },
        "findings": [
            f"Audited {len(workorder['items'])} item(s) ({item_audit['ready_item_count']} ready, {len(item_audit['blocking_issues'])} blocking issue(s)); semantic_sha={item_audit['semantic_bank_sha256'][:12]}.",
            f"Scored {len(workorder['candidate_runs'])} run(s), logged {len(failure_bank)} failure case(s), and generated {len(regression_drafts)} regression item draft(s).",
            SCORE_DISCLAIMER,
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
        required_files=["benchmark_panel.json", "benchmark_report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor benchmark-a1d81ca4 Literature Reading Benchmark Harness.")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"])
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
