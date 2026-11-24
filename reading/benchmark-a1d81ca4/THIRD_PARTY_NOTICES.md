# Third-Party Notices

This Skill package uses pure Python 3.12 standard library code and incorporates open-source patterns under permissive licenses:

1. **Harbor Common Core Guardrails** (`scripts/preflight_probe.py`, `scripts/budget_guard.py`, `scripts/result_guard.py`, `scripts/block_indexer.py`, `scripts/gap_gate.py`, `scripts/final_audit.py`):
   - Built for offline Harbor evaluation sandboxes.
   - License: MIT License.

2. **Audit Ledger** (`scripts/audit_chain.py`):
   - SHA-256 canonical JSON hash-chain pattern adapted from `AngelChen-HC/systematic-review-skill` (Apache License 2.0).

3. **Quote Grounding** (`scripts/citation_anchors.py`):
   - Offline exact-quote verification patterns adapted from `nolainjin/paper-verify` (MIT License).
