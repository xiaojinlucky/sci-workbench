"""Strict verbatim quote grounding helper for Harbor benchmark evaluation."""

from __future__ import annotations


def verify_exact_quote(quote: str, source_text: str, min_chars: int = 6) -> bool:
    """Verify quote appears verbatim in source_text after whitespace normalization."""
    q_norm = " ".join((quote or "").split()).casefold()
    s_norm = " ".join((source_text or "").split()).casefold()
    if len(q_norm) < min_chars or not s_norm:
        return False
    return q_norm in s_norm
