# Third-Party Notices

This package runs offline using the Python 3.12 standard library and contains adapted pure-stdlib logic from the following permissive open-source projects:

- **PRISMA deduplication and accounting conservation patterns** (`scripts/prisma_flow_auditor.py`): adapted and rewritten without third-party dependencies from `Black-Lights/prisma-review-tool` (MIT License).
- **SHA-256 hash-chained audit ledger** (`scripts/audit_chain.py`): adapted and simplified for offline execution from `AngelChen-HC/systematic-review-skill` (Apache-2.0 License).
- **Five-state evidence classification & neutral UNKNOWN handling** (`scripts/evidence_state_guard.py`): adapted from `nolainjin/paper-verify` (MIT License) and `Light0305/Light-Science` (MIT License).
