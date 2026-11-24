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
    from citation_anchors import verify_exact_quote, verify_exact_quote_detailed
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import budget_guard
    import final_audit
    import gap_gate
    import item_bank_auditor
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log
    from citation_anchors import verify_exact_quote, verify_exact_quote_detailed


SCORE_DISCLAIMER = (
    "Deterministic local rule checks and synthetic checker logs are NOT official DeepSeek Harness (DSH) "
    "scores or independent human semantic validation. Provided sources are paragraph/table text, not PDF-pixel gold."
)

FAILURE_TAXONOMY = (
    "missing_answer_or_field",
    "ungrounded_quote_or_id",
    "citation_count_mismatch",
    "citation_id_mismatch",
    "citation_clause_binding_mismatch",
    "case_sensitive_quote_mismatch",
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
    "mg/dl": ("mass_conc", 10.0, 0.0),
    "g/dl": ("mass_conc", 10000.0, 0.0),
    "ppm": ("ppm", 1.0, 0.0),
    "ppb": ("ppm", 1e-3, 0.0),
    "ma/cm2": ("current_density", 1.0, 0.0),
    "ma/cm^2": ("current_density", 1.0, 0.0),
    "ma cm-2": ("current_density", 1.0, 0.0),
    "ma cm^-2": ("current_density", 1.0, 0.0),
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
    "kj/mol": ("molar_energy", 1.0, 0.0),
    "kcal/mol": ("molar_energy", 4.184, 0.0),
    "minutes": ("time", 60.0, 0.0),
    "days": ("time", 86400.0, 0.0),
    "cases/10,000 inhabitants": ("rate_per_10k", 1.0, 0.0),
    "cases/10,000": ("rate_per_10k", 1.0, 0.0),
    "/10,000 inhabitants": ("rate_per_10k", 1.0, 0.0),
    "/10,000": ("rate_per_10k", 1.0, 0.0),
    "per 10,000": ("rate_per_10k", 1.0, 0.0),
    "cases/100,000 inhabitants": ("rate_per_10k", 0.1, 0.0),
    "cases/100,000": ("rate_per_10k", 0.1, 0.0),
    "/100,000 inhabitants": ("rate_per_10k", 0.1, 0.0),
    "/100,000": ("rate_per_10k", 0.1, 0.0),
    "per 100,000": ("rate_per_10k", 0.1, 0.0),
    "×": ("fold_ratio", 1.0, 0.0),
    "x": ("fold_ratio", 1.0, 0.0),
}

QUANTITY_RE = re.compile(
    r"(?<![A-Za-z0-9_])([-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*"
    r"(%|pct|percent|cases/100,000\s+inhabitants|cases/10,000\s+inhabitants|"
    r"/100,000\s+inhabitants|/10,000\s+inhabitants|cases/100,000|cases/10,000|"
    r"/100,000|/10,000|per\s+100,000|per\s+10,000|kcal/mol|kJ/mol|"
    r"mV/dec|V/dec|mA/cm\^?2|mA\s*cm\^?-2|A/cm\^?2|mg/dL|g/dL|mg/L|µg/mL|ug/mL|g/L|ppm|ppb|"
    r"mV|uV|kV|V|mA|µA|uA|A|kg|mg|µg|ug|ng|g|cm|mm|µm|um|nm|mM|µM|uM|nM|M|m|"
    r"ms|minutes|min|hours|hr|days|h|s|°C|degC|K|meV|eV|kJ|J|×|x)?(?![A-Za-z0-9_])"
)

# Match ONLY recognizable block IDs like [P001], [T01], [S1-P02], [P0035], [TAB01], [Table 1];
# strictly exclude bare numeric bibliography references like [1], [2], [3] or [1, 2, 3].
_BLOCK_ID_TOKEN = r"(?:[A-Za-z][A-Za-z0-9_.:-]*[-:]?(?:P|T|S|FIG|TAB)\d+[A-Za-z]?|(?:P|T|S|FIG|TAB)\d+[A-Za-z]?|(?:Table|Fig\.?)\s+\d+[A-Za-z]?)"
INLINE_BRACKET_CITE_RE = re.compile(rf"\[({_BLOCK_ID_TOKEN}(?:\s*,\s*{_BLOCK_ID_TOKEN})*)\]")
INLINE_PAREN_PID_RE = re.compile(rf"\(({_BLOCK_ID_TOKEN}(?:\s*,\s*{_BLOCK_ID_TOKEN})*)\)")

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
    """Extract unique recognizable inline block IDs like [P001], [T01], [S1-P02] (excluding [1]/[2]/[3] refs)."""
    tokens: list[str] = []
    for pat in (INLINE_BRACKET_CITE_RE, INLINE_PAREN_PID_RE):
        for m in pat.finditer(answer_text):
            for part in m.group(1).split(","):
                tok = " ".join(part.strip().split())
                if re.fullmatch(r"\d+", tok) or re.fullmatch(r"(?:19|20)\d{2}", tok):
                    continue
                if tok and tok not in tokens:
                    tokens.append(tok)
    return tokens


def _locate_quote_in_source(pid: str, quote: str, source_text: str) -> dict[str, Any]:
    """Resolve structured (page, line_range, table_cell/row/col, char_span) locator for a verified quote."""
    page_m = re.search(r"(?:\[\s*Page\s*(\d+)\s*\]|\bPage\s+(\d+)\b)", source_text, re.I)
    page_num = int(page_m.group(1) or page_m.group(2)) if page_m else None
    lines = source_text.splitlines()
    q_norm = " ".join((quote or "").split())
    matched_row: int | None = None
    matched_col: int | None = None
    table_cell: str | None = None
    line_start: int | None = None
    line_end: int | None = None

    if q_norm:
        for lno, ln in enumerate(lines, start=1):
            ln_norm = " ".join(ln.split())
            if q_norm in ln_norm or (len(q_norm) >= 12 and q_norm[:20] in ln_norm):
                line_start = lno
                line_end = lno
                break
        if line_start is None:
            # Multi-line match across wrapped lines
            head_tok = q_norm.split()[0] if q_norm.split() else ""
            tail_tok = q_norm.split()[-1] if q_norm.split() else ""
            for lno, ln in enumerate(lines, start=1):
                if head_tok and head_tok in ln and line_start is None:
                    line_start = lno
                if line_start is not None and tail_tok and tail_tok in ln:
                    line_end = lno
                    break
            if line_start is not None and line_end is None:
                line_end = min(len(lines), line_start + 2)

    is_table = pid.upper().startswith("T") or "Table " in (lines[0] if lines else "")
    if is_table and line_start is not None:
        matched_row = line_start
        row_line = lines[line_start - 1]
        cells = [c.strip() for c in row_line.split("|")] if "|" in row_line else row_line.split()
        matched_col = 1
        for cidx, cell in enumerate(cells, start=1):
            if cell and cell in q_norm:
                matched_col = cidx
                break
        table_cell = f"{pid}:R{matched_row}C{matched_col}"

    return {
        "paragraph_id": pid,
        "page": page_num,
        "line_start": line_start,
        "line_end": line_end,
        "table_row": matched_row,
        "table_col": matched_col,
        "table_cell": table_cell,
        "locator_label": (
            f"{table_cell} (L{line_start}-L{line_end})"
            if table_cell
            else (
                f"{pid}:Page {page_num}/L{line_start}-L{line_end}"
                if (page_num and line_start)
                else (f"{pid}:L{line_start}-L{line_end}" if line_start else pid)
            )
        ),
    }


def _extract_clause_citation_bindings(
    answer_text: str,
    sources: dict[str, str],
    valid_evidence_by_pid: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Bind each cited clause in answer_text to its inline block ID(s) and verify clause-to-source support."""
    bindings: list[dict[str, Any]] = []
    # Split answer into sentence/semicolon clauses while keeping citation markers attached
    clauses = [c.strip() for c in re.split(r"(?<=[.;!?。；！？])\s+", answer_text) if c.strip()]
    if not clauses and answer_text.strip():
        clauses = [answer_text.strip()]
    num_pat = re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?\b")

    for idx, cl in enumerate(clauses, start=1):
        c_markers = extract_inline_citation_markers(cl)
        if not c_markers:
            continue
        cl_nums = [m.group(0) for m in num_pat.finditer(cl) if not re.fullmatch(r"(?:19|20)\d{2}", m.group(0))]
        # Strip inline citation tags from clause before checking word/number overlap
        cl_clean = INLINE_BRACKET_CITE_RE.sub("", INLINE_PAREN_PID_RE.sub("", cl))
        cl_terms = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", cl_clean) if w.lower() not in {"with", "from", "that", "this", "were", "have", "what", "reported", "supplied", "equivalent", "cases", "total"}]
        for mid in c_markers:
            src_txt = sources.get(mid, "")
            has_ev = mid in valid_evidence_by_pid
            if not src_txt:
                bindings.append({
                    "clause_index": idx,
                    "cited_id": mid,
                    "clause_excerpt": cl[:160],
                    "structured_evidence_present": has_ev,
                    "clause_supported_by_cited_block": False,
                    "reason": "cited_id_missing_from_corpus",
                })
                continue
            src_flat = " ".join(src_txt.split())
            num_hits = [n for n in cl_nums if n in src_flat or n.replace(",", "") in src_flat.replace(",", "")]
            term_hits = [t for t in cl_terms if t.casefold() in src_flat.casefold()]
            # If clause cites multiple IDs, check local segment before this marker
            supported = bool(num_hits or len(term_hits) >= 1 or any(cue in cl.casefold() for cue in ABSTAIN_CUES))
            bindings.append({
                "clause_index": idx,
                "cited_id": mid,
                "clause_excerpt": cl[:160],
                "structured_evidence_present": has_ev,
                "clause_supported_by_cited_block": supported,
                "matched_numbers": num_hits[:5],
                "matched_terms": term_hits[:5],
            })
    return bindings


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


def normalize_prediction(raw_response: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalize raw agent output (dict, JSON string, markdown fenced JSON, or free text) into
    structured `{answer, status, evidence}` without requiring manual hand-adaptation.

    Returns (normalized_dict_or_none, extra_soft_warnings).
    """
    if raw_response is None:
        return None, []
    if isinstance(raw_response, dict):
        return raw_response, []
    if not isinstance(raw_response, str):
        return None, ["non_string_non_dict_payload"]

    text = raw_response.strip()
    if not text:
        return {"answer": "", "evidence": []}, []

    # 1. Try fenced JSON or raw JSON object
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I)
    cand_json = fence.group(1).strip() if fence else text
    if "{" in cand_json and "}" in cand_json:
        a, b = cand_json.find("{"), cand_json.rfind("}")
        try:
            obj = json.loads(cand_json[a : b + 1])
            if isinstance(obj, dict) and any(k in obj for k in ("answer", "text", "evidence", "quotes", "status")):
                return obj, []
        except json.JSONDecodeError:
            pass

    # 2. Degraded free-text extraction: pull inline [PID] + quoted excerpts `"..."` or `「...」`
    evidence: list[dict[str, str]] = []
    markers = extract_inline_citation_markers(text)
    quoted_spans = re.findall(r'["“「]([^"”」]{10,260})["”」]', text)
    if markers and quoted_spans:
        for idx, q in enumerate(quoted_spans[: len(markers)]):
            evidence.append({"paragraph_id": markers[idx], "quote": q.strip()})
    elif quoted_spans:
        for q in quoted_spans[:3]:
            evidence.append({"paragraph_id": "", "quote": q.strip()})

    return {
        "answer": text,
        "evidence": evidence,
        "_normalized_from_free_text": True,
    }, ["degraded_free_text_parse"]


def score_item_012(
    item: dict[str, Any],
    response: Any,
    sources: dict[str, str],
) -> dict[str, Any]:
    """Deterministic multi-dimensional 0 / 1 / 2 scorer with transparent criterion breakdown."""
    iid = item["item_id"]
    norm_resp, parse_notes = normalize_prediction(response)
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
            "criterion_breakdown": {
                "value_unit_ok": None if item["unanswerable"] else (False if item["expected_values"] else None),
                "keyword_ok": None if item["unanswerable"] else (False if item["expected_keywords"] else None),
                "anchor_id_ok": False,
                "verbatim_quote_ok": False,
                "citation_parity_ok": True,
                "abstention_ok": False if item["unanswerable"] else None,
            },
            "failure_tags": ["missing_answer_or_field"],
            "rationale": "No response emitted for benchmark item.",
            "semantic_sha256": item["semantic_sha256"],
        }
    if norm_resp is None:
        return {
            "item_id": iid,
            "task_type": item["task_type"] or "unspecified",
            "score": 0,
            "max_score": 2,
            "grounded_quotes": 0,
            "ungrounded_quotes": 0,
            "inline_citation_count": 0,
            "structured_evidence_count": 0,
            "criterion_breakdown": {
                "value_unit_ok": None if item["unanswerable"] else (False if item["expected_values"] else None),
                "keyword_ok": None if item["unanswerable"] else (False if item["expected_keywords"] else None),
                "anchor_id_ok": False,
                "verbatim_quote_ok": False,
                "citation_parity_ok": False,
                "abstention_ok": False if item["unanswerable"] else None,
            },
            "failure_tags": ["schema_or_parse_error"],
            "rationale": f"Response payload has invalid type {type(response).__name__}; expected dict or string.",
            "semantic_sha256": item["semantic_sha256"],
        }

    response = norm_resp
    answer_txt = str(response.get("answer") or response.get("text") or "").strip()
    status_val = str(response.get("status") or "").strip().lower()
    raw_quotes = response.get("evidence") if "evidence" in response else response.get("quotes", [])

    failures: list[str] = []
    details: list[str] = []
    if parse_notes:
        details.append("normalized from raw free-text output via degraded parser")

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

    # (b) Strict case-sensitive structured quote anchoring + locator verification (no cross-paragraph borrowing)
    grounded_count = 0
    ungrounded_count = 0
    bad_anchor_id_count = 0
    bad_quote_count = 0
    case_mismatch_count = 0
    cited_pids: set[str] = set()
    struct_pids_all: set[str] = set()
    valid_evidence_by_pid: dict[str, list[str]] = defaultdict(list)
    verified_locators: list[dict[str, Any]] = []
    corpus_all = "\n".join(sources.values())

    for q in quotes:
        if isinstance(q, dict):
            pid = str(q.get("paragraph_id") or "").strip()
            qtxt = str(q.get("quote") or "").strip()
            if pid:
                struct_pids_all.add(pid)
            if not pid or pid not in sources:
                ungrounded_count += 1
                bad_anchor_id_count += 1
                details.append(f"unknown_or_missing paragraph_id={pid!r}")
                continue
            vdet = verify_exact_quote_detailed(qtxt, sources[pid])
            if vdet["matched"]:
                loc_info = _locate_quote_in_source(pid, qtxt, sources[pid])
                loc_info["char_span"] = vdet["char_span"]
                # Verify optional declared (page, table_row, table_col, table_cell) against source
                loc_conflict = False
                if q.get("page") is not None and loc_info["page"] is not None and int(q["page"]) != loc_info["page"]:
                    loc_conflict = True
                    details.append(f"declared page={q['page']} != source page={loc_info['page']} for {pid}")
                if q.get("table_row") is not None and loc_info["table_row"] is not None and int(q["table_row"]) != loc_info["table_row"]:
                    loc_conflict = True
                    details.append(f"declared table_row={q['table_row']} != source row={loc_info['table_row']} for {pid}")
                if loc_conflict:
                    ungrounded_count += 1
                    bad_anchor_id_count += 1
                else:
                    grounded_count += 1
                    cited_pids.add(pid)
                    valid_evidence_by_pid[pid].append(qtxt)
                    verified_locators.append(loc_info)
            else:
                ungrounded_count += 1
                bad_quote_count += 1
                if vdet.get("case_mismatch_detected"):
                    case_mismatch_count += 1
                    failures.append("case_sensitive_quote_mismatch")
                    details.append(f"case-sensitive mismatch in {pid!r}: quote={qtxt!r} vs source={vdet.get('source_slice_at_ci')!r}")
                else:
                    details.append(f"quote not found verbatim (case-sensitive) in paragraph {pid!r}")
        elif isinstance(q, str):
            vdet = verify_exact_quote_detailed(q, corpus_all)
            if vdet["matched"]:
                grounded_count += 1
            else:
                ungrounded_count += 1
                bad_quote_count += 1
                if vdet.get("case_mismatch_detected"):
                    case_mismatch_count += 1
                    failures.append("case_sensitive_quote_mismatch")
                    details.append("unstructured quote rejected due to case-sensitive mismatch")
                else:
                    details.append("unstructured quote not found verbatim in corpus")
        else:
            failures.append("schema_or_parse_error")
            details.append("non-string/non-dict item inside evidence list")

    if ungrounded_count > 0:
        failures.append("ungrounded_quote_or_id")

    # Exact ID-to-ID & clause-level binding check (never allow count-only parity to mask ID mismatch!)
    inline_markers = extract_inline_citation_markers(answer_txt)
    inline_set = set(inline_markers)
    inline_count = len(inline_markers)
    structured_count = len(quotes)
    count_parity_ok = not (inline_count > 0 and inline_count != structured_count)
    id_binding_ok = True
    if inline_set:
        missing_in_struct = sorted(inline_set - struct_pids_all)
        extra_in_struct = sorted(struct_pids_all - inline_set)
        if missing_in_struct or extra_in_struct:
            id_binding_ok = False
            failures.append("citation_id_mismatch")
            details.append(
                f"inline IDs {sorted(inline_set)} != structured evidence IDs {sorted(struct_pids_all)} "
                f"(missing_in_evidence={missing_in_struct}, uncited_in_answer={extra_in_struct})"
            )
    if not count_parity_ok:
        failures.append("citation_count_mismatch")
        details.append(f"inline citations ({inline_count}: {inline_markers}) != structured evidence count ({structured_count})")

    clause_bindings = _extract_clause_citation_bindings(answer_txt, sources, valid_evidence_by_pid)
    bad_clause_bindings = [cb for cb in clause_bindings if not cb["clause_supported_by_cited_block"] or not cb["structured_evidence_present"]]
    clause_binding_ok = len(bad_clause_bindings) == 0
    if not clause_binding_ok:
        failures.append("citation_clause_binding_mismatch")
        details.append(f"{len(bad_clause_bindings)} clause-to-citation binding(s) unsupported or missing structured evidence")

    citation_parity_ok = count_parity_ok and id_binding_ok and clause_binding_ok

    # (c) Unanswerable honest-abstention check vs (a) tolerance-aware numeric/unit + keyword check
    value_unit_ok: bool | None = None
    keyword_ok: bool | None = None
    abstention_ok: bool | None = None
    required_pid_covered = True

    if item["unanswerable"]:
        abstention_ok = status_val in ABSTAIN_STATUSES or any(cue in answer_txt.casefold() for cue in ABSTAIN_CUES)
        if not abstention_ok:
            failures.append("false_certainty_on_missing_data")
            details.append("failed to abstain on unanswerable item")
    else:
        rel_tol = float(item.get("numeric_rel_tol", 1e-3))
        if item["expected_values"]:
            value_unit_ok = True
            for ev_val in item["expected_values"]:
                verdict = check_expected_value(ev_val, answer_txt, rel_tol=rel_tol)
                if verdict == "unit_mismatch":
                    value_unit_ok = False
                    failures.append("unit_mismatch")
                    details.append(f"unit mismatch for expected {ev_val!r}")
                elif verdict == "numeric_value_mismatch":
                    value_unit_ok = False
                    failures.append("numeric_value_mismatch")
                    details.append(f"numeric mismatch for expected {ev_val!r}")

        if item["expected_keywords"]:
            keyword_ok = True
            for kw in item["expected_keywords"]:
                if kw.casefold() not in answer_txt.casefold():
                    keyword_ok = False
                    failures.append("missing_answer_or_field")
                    details.append(f"missing expected keyword {kw!r}")

        if item["required_paragraph_ids"] and grounded_count > 0:
            missing_req_pids = sorted(set(item["required_paragraph_ids"]) - cited_pids)
            if missing_req_pids:
                required_pid_covered = False
                failures.append("ungrounded_quote_or_id")
                details.append(f"grounded citations {sorted(cited_pids)} missed required_paragraph_ids {missing_req_pids}")

        if grounded_count == 0 and not item["unanswerable"]:
            required_pid_covered = False
            failures.append("ungrounded_quote_or_id")
            details.append("no verified source quote provided for answerable item")

    anchor_id_ok = (bad_anchor_id_count == 0) and id_binding_ok and (True if item["unanswerable"] else required_pid_covered)
    verbatim_quote_ok = (bad_quote_count == 0) and (case_mismatch_count == 0) and (True if item["unanswerable"] else grounded_count > 0)

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
        "inline_citation_ids": inline_markers,
        "structured_evidence_count": structured_count,
        "structured_evidence_ids": sorted(struct_pids_all),
        "verified_locators": verified_locators,
        "clause_bindings": clause_bindings,
        "criterion_breakdown": {
            "value_unit_ok": value_unit_ok,
            "keyword_ok": keyword_ok,
            "anchor_id_ok": anchor_id_ok,
            "verbatim_quote_ok": verbatim_quote_ok,
            "citation_parity_ok": citation_parity_ok,
            "citation_id_binding_ok": id_binding_ok,
            "clause_binding_ok": clause_binding_ok,
            "abstention_ok": abstention_ok,
        },
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


def _generate_offline_fallback_run(
    items: list[dict[str, Any]],
    sources: dict[str, str],
) -> dict[str, Any]:
    """Conservative offline retrieval-grounded self-answer fallback when candidate_runs is omitted.

    Reads ONLY item['prompt'] and supplied sources (never peaks at reference_answer/expected_values).
    """
    stop = {
        "what", "which", "when", "where", "were", "with", "from", "that", "this", "these", "those",
        "according", "cite", "exact", "source", "paragraph", "paragraphs", "table", "study", "between",
        "among", "does", "did", "have", "been", "into", "about", "both", "many", "much", "rate",
    }
    responses: dict[str, dict[str, Any]] = {}

    for it in items:
        prompt = it.get("prompt") or ""
        q_tokens = [w.lower() for w in re.findall(r"[A-Za-z0-9_-]{3,}", prompt) if w.lower() not in stop]
        q_set = set(q_tokens)

        # Rank corpus blocks by lexical overlap against prompt only
        scored_blocks: list[tuple[float, str, str]] = []
        for sid, stxt in sorted(sources.items()):
            low = stxt.lower()
            hits = sum(1 for tok in q_set if tok in low)
            if hits > 0:
                scored_blocks.append((hits / max(1, len(q_set)), sid, stxt))
        scored_blocks.sort(key=lambda x: (-x[0], x[1]))

        # Detect mortality/fatality or low-overlap queries conservatively -> abstain
        rare_target_terms = [t for t in ("mortality", "case-fatality", "fatality", "survival", "deaths") if t in prompt.lower()]
        top_blocks = scored_blocks[:2]
        if not top_blocks or top_blocks[0][0] < 0.15 or (rare_target_terms and not any(rt in top_blocks[0][2].lower() for rt in rare_target_terms)):
            ev_list: list[dict[str, Any]] = []
            cite_suffix = ""
            if top_blocks:
                _, top_id, top_txt = top_blocks[0]
                first_line = next((ln.strip() for ln in top_txt.splitlines() if len(ln.strip()) >= 12), top_txt[:120].strip())
                loc = _locate_quote_in_source(top_id, first_line[:120], top_txt)
                ev_list.append({
                    "paragraph_id": top_id,
                    "quote": first_line[:120],
                    "page": loc["page"],
                    "line_start": loc["line_start"],
                    "table_cell": loc["table_cell"],
                })
                cite_suffix = f" [{top_id}]"
            responses[it["item_id"]] = {
                "status": "not_reported",
                "answer": f"Not reported in the retrieved source blocks (conservative offline fallback abstention){cite_suffix}.",
                "evidence": ev_list,
                "fallback_self_answer": True,
            }
            continue

        chosen = [top_blocks[0]]
        if len(top_blocks) > 1 and top_blocks[1][0] >= top_blocks[0][0] * 0.65:
            chosen.append(top_blocks[1])

        ans_parts: list[str] = []
        ev_list = []
        for _, sid, stxt in chosen:
            # Pick best matching sentence/line from source block
            segs = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", stxt) if len(s.strip()) >= 12]
            best_seg = max(segs, key=lambda sg: sum(1 for tok in q_set if tok in sg.lower())) if segs else stxt[:160].strip()
            quote_slice = " ".join(best_seg.split())[:180]
            loc = _locate_quote_in_source(sid, quote_slice, stxt)
            ans_parts.append(f"{quote_slice} [{sid}]")
            ev_list.append({
                "paragraph_id": sid,
                "quote": quote_slice,
                "page": loc["page"],
                "line_start": loc["line_start"],
                "table_row": loc["table_row"],
                "table_col": loc["table_col"],
                "table_cell": loc["table_cell"],
            })

        responses[it["item_id"]] = {
            "status": "fallback_extractive_baseline",
            "answer": " ".join(ans_parts),
            "evidence": ev_list,
            "fallback_self_answer": True,
        }

    return {
        "run_id": "offline_fallback_self_answer",
        "agent_id": "offline_fallback_baseline",
        "fallback_self_answer": True,
        "latency_sec": 0.01,
        "model_requests": 0,
        "max_response_tokens": 512,
        "responses": responses,
    }


def evaluate_runs_and_stability(
    workorder: dict[str, Any],
    item_audit: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Score supplied runs (or conservative offline self-answer fallback), compute stability & budget audit, and draft regression items."""
    items = workorder["items"]
    sources = workorder["sources"]
    runs = list(workorder["candidate_runs"] or [])
    used_fallback = False

    scoreboards: list[dict[str, Any]] = []
    failure_bank: list[dict[str, Any]] = []

    if not runs:
        used_fallback = True
        runs = [_generate_offline_fallback_run(items, sources)]

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
            "fallback_self_answer": bool(run.get("fallback_self_answer", used_fallback)),
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


def compute_human_calibration(
    scoreboards: list[dict[str, Any]],
    stability_summary: dict[str, Any],
    human_gold_scores: list[dict[str, Any]],
    max_queue: int = 12,
) -> dict[str, Any]:
    """Compute Auto-vs-Human calibration (exact/within-1 agreement, Cohen's kappa, confusion matrix)
    or emit a stratified human review queue without fabricating agreement when gold labels are absent.
    """
    auto_map: dict[tuple[str, str], dict[str, Any]] = {}
    all_records: list[dict[str, Any]] = []
    flipped_set: set[str] = set()
    for ag_stats in (stability_summary.get("multi_run_agents") or {}).values():
        flipped_set.update(ag_stats.get("flipped_items") or [])

    for sb in scoreboards:
        if sb.get("mode") == "synthetic_workorder_readiness":
            continue
        rid = sb["run_id"]
        for ir in sb.get("item_results") or []:
            rec = {
                "run_id": rid,
                "agent_id": sb.get("agent_id", rid),
                "item_id": ir["item_id"],
                "task_type": ir["task_type"],
                "auto_score": int(ir["score"]),
                "failure_tags": ir.get("failure_tags") or [],
                "criterion_breakdown": ir.get("criterion_breakdown") or {},
                "rationale": ir.get("rationale") or "",
            }
            auto_map[(rid, ir["item_id"])] = rec
            all_records.append(rec)

    paired: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_gold: list[dict[str, Any]] = []
    paired_keys: set[tuple[str, str]] = set()
    for g in human_gold_scores or []:
        key = (g["run_id"], g["item_id"])
        if key in auto_map:
            paired.append((auto_map[key], g))
            paired_keys.add(key)
        else:
            unmatched_gold.append({"run_id": g["run_id"], "item_id": g["item_id"], "human_score": g["human_score"]})

    # Build stratified review queue for unannotated items (never fabricate human scores)
    unannotated = [r for r in all_records if (r["run_id"], r["item_id"]) not in paired_keys]

    def _queue_priority(rec: dict[str, Any]) -> tuple[int, str, str]:
        if rec["item_id"] in flipped_set:
            return (0, rec["task_type"], rec["item_id"])
        if rec["auto_score"] == 1:
            return (1, rec["task_type"], rec["item_id"])
        if rec["auto_score"] == 0:
            return (2, rec["task_type"], rec["item_id"])
        return (3, rec["task_type"], rec["item_id"])

    unannotated.sort(key=_queue_priority)
    queue: list[dict[str, Any]] = []
    seen_buckets: Counter[tuple[int, str]] = Counter()
    for rec in unannotated:
        if len(queue) >= max_queue:
            break
        bucket = (rec["auto_score"], rec["task_type"])
        if len(unannotated) > max_queue and seen_buckets[bucket] >= 3:
            continue
        seen_buckets[bucket] += 1
        reason = (
            "repetition_flip_dispute"
            if rec["item_id"] in flipped_set
            else (
                "borderline_partial_score_1"
                if rec["auto_score"] == 1
                else ("zero_score_failure_audit" if rec["auto_score"] == 0 else "full_score_spot_check")
            )
        )
        queue.append({
            "run_id": rec["run_id"],
            "item_id": rec["item_id"],
            "task_type": rec["task_type"],
            "auto_score": rec["auto_score"],
            "auto_failure_tags": rec["failure_tags"],
            "stratum_reason": reason,
            "human_score": None,
            "human_notes": "",
        })

    if not paired:
        return {
            "calibration_status": "awaiting_human_gold_queue_generated",
            "paired_count": 0,
            "exact_agreement_rate": None,
            "within_1_agreement_rate": None,
            "cohens_kappa": None,
            "quadratic_weighted_kappa": None,
            "confusion_matrix": None,
            "per_item_disagreements": [],
            "unmatched_gold_entries": unmatched_gold,
            "human_calibration_queue": queue,
            "note": "No paired human_gold_scores supplied; agreement metrics kept null (never fabricated) and stratified human review queue generated.",
        }

    # 3x3 confusion matrix over ordinal {0, 1, 2}: rows = human, cols = auto
    cm = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    exact_hits = 0
    within1_hits = 0
    disagreements: list[dict[str, Any]] = []

    for auto_rec, gold_rec in paired:
        a = auto_rec["auto_score"]
        h = gold_rec["human_score"]
        cm[h][a] += 1
        if a == h:
            exact_hits += 1
        if abs(a - h) <= 1:
            within1_hits += 1
        if a != h:
            delta = a - h
            action = (
                "tighten_deterministic_rule_false_positive"
                if delta > 0
                else "relax_or_add_synonym_tolerance_false_negative"
            )
            disagreements.append({
                "run_id": auto_rec["run_id"],
                "item_id": auto_rec["item_id"],
                "task_type": auto_rec["task_type"],
                "auto_score": a,
                "human_score": h,
                "delta_auto_minus_human": delta,
                "auto_failure_tags": auto_rec["failure_tags"],
                "criterion_breakdown": auto_rec["criterion_breakdown"],
                "human_notes": gold_rec.get("human_notes", ""),
                "recommended_action": action,
            })

    n = len(paired)
    po = exact_hits / n
    row_totals = [sum(cm[i]) for i in range(3)]
    col_totals = [sum(cm[i][j] for i in range(3)) for j in range(3)]
    pe = sum(row_totals[k] * col_totals[k] for k in range(3)) / (n * n)
    if abs(1.0 - pe) < 1e-12:
        kappa = 1.0 if abs(po - 1.0) < 1e-12 else 0.0
    else:
        kappa = (po - pe) / (1.0 - pe)

    # Quadratic weighted kappa for ordinal 0/1/2 scale
    num_qwk = 0.0
    den_qwk = 0.0
    for i in range(3):
        for j in range(3):
            w = ((i - j) ** 2) / 4.0
            num_qwk += w * (cm[i][j] / n)
            den_qwk += w * ((row_totals[i] / n) * (col_totals[j] / n))
    if abs(den_qwk) < 1e-12:
        qwk = 1.0 if abs(num_qwk) < 1e-12 else 0.0
    else:
        qwk = 1.0 - (num_qwk / den_qwk)

    disagreements.sort(key=lambda d: (-abs(d["delta_auto_minus_human"]), d["run_id"], d["item_id"]))
    cm_named = {
        f"human_{i}": {f"auto_{j}": cm[i][j] for j in range(3)}
        for i in range(3)
    }
    return {
        "calibration_status": "calibrated_with_human_gold",
        "paired_count": n,
        "exact_agreement_rate": round(po, 4),
        "within_1_agreement_rate": round(within1_hits / n, 4),
        "cohens_kappa": round(kappa, 4),
        "quadratic_weighted_kappa": round(qwk, 4),
        "confusion_matrix": cm_named,
        "per_item_disagreements": disagreements,
        "unmatched_gold_entries": unmatched_gold,
        "human_calibration_queue": queue,
        "note": f"Computed Auto-vs-Human agreement across {n} paired (run_id, item_id) judgments on 0/1/2 scale.",
    }


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
    if audit.get("corpus_consistency_warnings"):
        lines.append("### 语料算术/表格自洽预警 (`corpus_consistency_warnings`)")
        for cw in audit["corpus_consistency_warnings"]:
            lines.append(f"- **[{cw['code']}]** `{cw['source_id']}`: {cw['message']}")
        lines.append("")
    if audit.get("source_conditioned_leakage_matrix"):
        lines.extend([
            "### 题干无源可猜泄漏矩阵 (`source_conditioned_leakage_matrix`)",
            "| Item ID | 直接泄漏 | 关键词重合率 | 源锚点验证 | 跨块冗余 | 风险等级 | 说明 |",
            "|---|---|---|---|---|---|---|",
        ])
        for row in audit["source_conditioned_leakage_matrix"]:
            red = ", ".join(row.get("cross_block_redundancy_ids") or []) or "无"
            lines.append(
                f"| `{row['item_id']}` | `{row['direct_prompt_leakage']}` | `{row['expected_keyword_overlap_ratio']}` | "
                f"`{row['source_dependency_verified']}` | `{red}` | `{row['prompt_only_guessability_risk']}` | `{row['risk_reason']}` |"
            )
        lines.append("")

    lines.extend(["## 2. 确定性 0/1/2 评分面板", ""])
    for sb in panel["scoreboards"]:
        if sb.get("mode") == "synthetic_workorder_readiness":
            lines.append(f"- `{sb['run_id']}`: 仅执行题卡就绪度审计（无候选预测输入），就绪 `{sb['ready_item_count']}/{sb['items_evaluated']}`。")
            continue
        fb_badge = " | `fallback_self_answer=true`" if sb.get("fallback_self_answer") else ""
        lines.append(f"### Run: `{sb['run_id']}`（Agent: `{sb['agent_id']}`{fb_badge} | 得分: `{sb['total_score']}/{sb['max_score']}` = `{sb['normalized_pct']}%`）")
        lines.append("| Item ID | 题型 | 得分 | 核验引文/未核验 | 正文引标/结构引文 | 精确定位锚点 (Page/Line/Cell) | 子项明细 (val/anc/quo/par/abs) | 失败标签 | 判分依据 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for ir in sb["item_results"]:
            tags = ", ".join(ir["failure_tags"]) if ir["failure_tags"] else "PASS"
            rat = ir["rationale"].replace("|", "\\|")
            cb = ir.get("criterion_breakdown") or {}
            locs = ", ".join(vl.get("locator_label", vl.get("paragraph_id", "")) for vl in (ir.get("verified_locators") or [])) or "none"

            def _sym(v: Any) -> str:
                return "-" if v is None else ("Y" if v else "N")

            cb_str = "/".join([
                _sym(cb.get("value_unit_ok")),
                _sym(cb.get("anchor_id_ok")),
                _sym(cb.get("verbatim_quote_ok")),
                _sym(cb.get("citation_parity_ok")),
                _sym(cb.get("abstention_ok")),
            ])
            lines.append(
                f"| `{ir['item_id']}` | `{ir['task_type']}` | `{ir['score']}/2` | "
                f"`{ir['grounded_quotes']}/{ir['ungrounded_quotes']}` | "
                f"`{ir['inline_citation_count']}/{ir['structured_evidence_count']}` | `{locs}` | `{cb_str}` | `{tags}` | {rat} |"
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

    hc = panel.get("human_calibration") or {}
    if hc:
        lines.extend(["## 4. 自动判分 vs 人工复核一致性校准", ""])
        lines.append(f"- **校准状态**: `{hc.get('calibration_status')}`｜**配对样本数**: `{hc.get('paired_count', 0)}`")
        if hc.get("paired_count", 0) > 0:
            lines.append(
                f"- **完全一致率 (`exact_agreement_rate`)**: `{hc['exact_agreement_rate']}`｜"
                f"**±1分一致率 (`within_1_agreement_rate`)**: `{hc['within_1_agreement_rate']}`｜"
                f"**Cohen's kappa**: `{hc['cohens_kappa']}`｜**QWK**: `{hc['quadratic_weighted_kappa']}`"
            )
            cm = hc.get("confusion_matrix") or {}
            lines.extend(["", "| 人工\\自动 | Auto=0 | Auto=1 | Auto=2 |", "|---|---:|---:|---:|"])
            for i in range(3):
                row = cm.get(f"human_{i}", {})
                lines.append(f"| Human={i} | {row.get('auto_0', 0)} | {row.get('auto_1', 0)} | {row.get('auto_2', 0)} |")
            if hc.get("per_item_disagreements"):
                lines.extend(["", "### 逐项分歧清单 (`per_item_disagreements`)"])
                for dis in hc["per_item_disagreements"]:
                    lines.append(
                        f"- `{dis['run_id']}` / `{dis['item_id']}`: auto=`{dis['auto_score']}` vs human=`{dis['human_score']}` "
                        f"(`{dis['recommended_action']}`) — {dis.get('human_notes') or '无人工备注'}"
                    )
        else:
            lines.append(f"- **说明**: {hc.get('note')}")
            q = hc.get("human_calibration_queue") or []
            if q:
                lines.append(f"- **已生成分层人工抽样复核队列 (`human_calibration_queue`)**: `{len(q)}` 条（严禁伪造一致率）")
                for qi in q[:6]:
                    lines.append(
                        f"  - `{qi['run_id']}` / `{qi['item_id']}` (`{qi['task_type']}`, auto=`{qi['auto_score']}`, 抽样原因=`{qi['stratum_reason']}`)"
                    )
        lines.append("")

    if panel["regression_item_drafts"]:
        lines.extend(["## 5. 失败样例库 → 下一版专项回归题草案", ""])
        for dr in panel["regression_item_drafts"]:
            lines.append(
                f"- **{dr['draft_item_id']}**（针对 `{dr['target_failure_tag']}`，命中 {dr['observed_failure_count']} 次，来源题 `{dr['source_item_ids']}`）：{dr['draft_design_guidance']}"
            )
        lines.append("")

    lines.extend([
        "## 6. 版本快照对比与增量重算清单",
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
    human_calibration = compute_human_calibration(
        scoreboards,
        stability_summary,
        workorder.get("human_gold_scores") or [],
    )
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
        "human_calibration": human_calibration,
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
            "human_calibration_status": human_calibration["calibration_status"],
            "human_paired_count": human_calibration["paired_count"],
        },
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    if not workorder["items"]:
        gaps.append({"code": "EMPTY_BENCHMARK_ITEM_BANK", "severity": "error", "source": "load_workorder_and_corpus", "message": "No benchmark items found in case or workorder."})
    for bi in item_audit["blocking_issues"]:
        gaps.append({"code": bi["code"], "severity": "warning", "source": "item_bank_auditor", "message": bi["message"]})
    for cw in item_audit.get("corpus_consistency_warnings") or []:
        gaps.append({"code": cw["code"], "severity": cw.get("severity", "warning"), "source": cw["source_id"], "message": cw["message"]})
    if not workorder["candidate_runs"]:
        gaps.append({"code": "NO_CANDIDATE_RUNS_PROVIDED", "severity": "info", "source": "evaluate_runs_and_stability", "message": "Executed item-bank quality/leakage audit only; no candidate runs provided."})
    elif human_calibration["paired_count"] == 0:
        gaps.append({"code": "HUMAN_GOLD_NOT_SUPPLIED", "severity": "info", "source": "compute_human_calibration", "message": f"No human_gold_scores provided; emitted {len(human_calibration['human_calibration_queue'])} stratified review queue item(s) without fabricating agreement."})

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
            "empty_ref_item_ids": item_audit.get("empty_ref_item_ids", []),
            "missing_rubric_item_ids": item_audit.get("missing_rubric_item_ids", []),
            "duplicate_item_ids": item_audit["duplicate_item_ids"],
            "corpus_consistency_warnings": item_audit.get("corpus_consistency_warnings", []),
            "source_conditioned_leakage_matrix": item_audit.get("source_conditioned_leakage_matrix", []),
        },
        "scoreboards": scoreboards,
        "stability_summary": stability_summary,
        "human_calibration": human_calibration,
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
            "human_paired_count": human_calibration["paired_count"],
            "human_exact_agreement_rate": human_calibration["exact_agreement_rate"],
            "human_cohens_kappa": human_calibration["cohens_kappa"],
            "human_calibration_queue_size": len(human_calibration["human_calibration_queue"]),
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
            "human_paired_count": "benchmark_panel.json:human_calibration",
            "human_exact_agreement_rate": "benchmark_panel.json:human_calibration",
            "human_cohens_kappa": "benchmark_panel.json:human_calibration",
            "human_calibration_queue_size": "benchmark_panel.json:human_calibration",
        },
        "findings": [
            f"Audited {len(workorder['items'])} item(s) ({item_audit['ready_item_count']} ready, {len(item_audit['blocking_issues'])} blocking issue(s)); semantic_sha={item_audit['semantic_bank_sha256'][:12]}.",
            f"Scored {len(workorder['candidate_runs'])} run(s), logged {len(failure_bank)} failure case(s), generated {len(regression_drafts)} regression item draft(s); human_calibration={human_calibration['calibration_status']} (paired={human_calibration['paired_count']}, exact={human_calibration['exact_agreement_rate']}, kappa={human_calibration['cohens_kappa']}).",
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
