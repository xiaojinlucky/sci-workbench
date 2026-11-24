# Third-Party Notices

This skill package is self-contained and relies exclusively on the Python 3.12 standard library (plus optional system Poppler CLI `pdftotext`/`pdfinfo` when present in the container).

1. **Harbor Runtime Safety Guardrails (M1–M6)**
   - `scripts/preflight_probe.py`, `scripts/result_guard.py`, `scripts/budget_guard.py`, `scripts/block_indexer.py`, `scripts/gap_gate.py`, `scripts/final_audit.py`
   - Standard offline runtime safety modules (MIT/Apache-2.0 baseline conventions).
2. **Bilingual Alignment & Anti-Translationese Rules**
   - `scripts/bilingual_alignment_guard.py` and `scripts/academic_deai_lint.py` implement original Python 3.12 standard-library auditing heuristics for terminology locking, bibliography identity conservation, SI unit pairing, negation/hedge checking, and Chinese academic translationese linting.
