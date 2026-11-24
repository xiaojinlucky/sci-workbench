# Third-Party Notices

This Harbor Skill package is self-contained and runs on Python 3.12 standard library (plus optional system Poppler CLI `pdftotext`/`pdfinfo` when present). It adapts offline design patterns from permissive open-source baselines:

1. **Citation & Identifier Extraction Pattern (`scripts/citation_anchors.py`)**
   - Offline regex identifier extraction (DOI/PMC/PMID/arXiv/URL), neutral unavailable-metadata scoring, and E/M/L/S/I (Entity/Magnitude/Logic/Scope/Identifier) context fingerprinting adapted from MIT-licensed offline verification baselines (`nolainjin/paper-verify`, MIT License) with all network fetchers removed.
2. **Epistemic Uncertainty & Coverage State Machine**
   - Distinction between `insufficient_evidence`, `partial`, and internal `conflict` alongside read/unread section tracking aligned with MIT-licensed scientific review conventions (`Light0305/Light-Science`, MIT License).
3. **Harbor Runtime Safety Guardrails (M1–M6)**
   - `scripts/preflight_probe.py`, `scripts/result_guard.py`, `scripts/budget_guard.py`, `scripts/block_indexer.py`, `scripts/gap_gate.py`, `scripts/final_audit.py` (MIT/Apache-2.0 baseline conventions).
