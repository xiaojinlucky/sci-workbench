"""Strict case-sensitive verbatim quote grounding helper for Harbor benchmark evaluation.

Preserves exact character case to intercept scientific unit/formula tampering
(e.g. 'mM' vs 'mm', 'Co' vs 'CO', 'pH' vs 'ph'), performing only auditable
whitespace and soft line-break normalization.
"""

from __future__ import annotations

import re
from typing import Any

_HYPHEN_LINEBREAK_RE = re.compile(r"(?<=[A-Za-z])-\s*\r?\n\s*(?=[A-Za-z])")


def _normalize_whitespace_preserve_case(text: str) -> tuple[str, bool]:
    """Normalize line-wrap hyphens and collapse whitespace while strictly preserving case."""
    raw = text or ""
    dehyphen = _HYPHEN_LINEBREAK_RE.sub("", raw)
    collapsed = " ".join(dehyphen.split())
    return collapsed, (collapsed != raw)


def verify_exact_quote_detailed(quote: str, source_text: str, min_chars: int = 6) -> dict[str, Any]:
    """Case-sensitive verbatim quote verification with auditable normalization and case-mismatch diagnosis."""
    q_norm, q_ws_changed = _normalize_whitespace_preserve_case(quote)
    s_norm, s_ws_changed = _normalize_whitespace_preserve_case(source_text)
    if len(q_norm) < min_chars or not s_norm:
        return {
            "matched": False,
            "case_sensitive_matched": False,
            "case_mismatch_detected": False,
            "whitespace_normalized": q_ws_changed or s_ws_changed,
            "char_span": None,
            "reason": "quote_too_short_or_empty_source",
        }

    idx = s_norm.find(q_norm)
    if idx >= 0:
        return {
            "matched": True,
            "case_sensitive_matched": True,
            "case_mismatch_detected": False,
            "whitespace_normalized": q_ws_changed or s_ws_changed,
            "char_span": [idx, idx + len(q_norm)],
            "reason": "exact_case_sensitive_match",
        }

    # Diagnostic check: would case-folding have falsely matched (e.g., mM vs mm, Co vs CO)?
    ci_idx = s_norm.casefold().find(q_norm.casefold())
    if ci_idx >= 0:
        return {
            "matched": False,
            "case_sensitive_matched": False,
            "case_mismatch_detected": True,
            "whitespace_normalized": q_ws_changed or s_ws_changed,
            "char_span": None,
            "source_slice_at_ci": s_norm[ci_idx : ci_idx + len(q_norm)],
            "reason": "case_mismatch_rejected",
        }

    return {
        "matched": False,
        "case_sensitive_matched": False,
        "case_mismatch_detected": False,
        "whitespace_normalized": q_ws_changed or s_ws_changed,
        "char_span": None,
        "reason": "not_found_in_source",
    }


def verify_exact_quote(quote: str, source_text: str, min_chars: int = 6) -> bool:
    """Verify quote appears verbatim (strictly case-sensitive) in source_text after whitespace/linebreak normalization."""
    return bool(verify_exact_quote_detailed(quote, source_text, min_chars=min_chars)["matched"])
