# Third-Party Notices

1. **AngelChen-HC / systematic-review-skill** (Apache License 2.0)
   - Adapted in `scripts/audit_chain.py`: SHA-256 canonical JSON hash-chained audit logging (`srlib/audit.py`), stripped to pure Python 3.12 stdlib for offline execution.

2. **Tom409114 / research-repo-doctor** (MIT License)
   - Conceptual checklist design reference in `scripts/env_lock_doctor.py` and `references/research_repo_doctor_checklist.md`: structuring repository readiness audits into `auto_discovered`, `manual_action_items`, and `missing_items` without conflating engineering readiness with scientific reproduction.

3. **Light0305 / Light-Science (`light-analysis` & `light-review` concepts only)** (MIT License, Copyright (c) 2026 Light0305)
   - Conceptual alignment on separating code presence, clean execution, and numerical tolerance reproduction (`reproducibility_grade` and 4-tier evidence ladder).

4. **Community Reproducibility Diagnostics (Conda #15581 / PR #15838 & Runtime Verification Contracts)**
   - Drift audit patterns in `scripts/env_lock_doctor.py` and `scripts/repro_audit_runner.py` incorporate documented Conda+pip mixed export drift risks and clean-runtime acceptance verification principles.

5. **Bundled Harbor Guardrails (`preflight_probe`, `result_guard`, `budget_guard`, `block_indexer`, `gap_gate`, `final_audit`)**
   - Adapted from permissive MIT/Apache-2.0 Harbor runtime guardrail baselines.
