# Experimental Plan Generator & Optimizer (`challenge-cf1995b5`)

Offline-native Harbor Skill for generating, auditing, and optimizing **3 versioned experimental candidate plans** from scientific sources and organizer prospective resource, pricing, and booking contracts.

## Included Components

- `SKILL.md`: End-to-end decision workflow covering research-type routing, shortest evidence chain, six mandatory design elements, confirmation gates, and exception handling.
- `scripts/hypothesis_control_mapper.py`: Audits operationalized IV/DV/falsification criteria, independent inference units vs technical replicates, normal-approx power/min-`n`, confounder-to-control coverage, augmented-factorial estimability, SOP version pinning (`SOP_VERSION_CONFLICT`), and ELN handover completeness.
- `scripts/plan_domain_optimizer.py`: Domain-agnostic multi-objective optimizer with topological DAG critical-path scheduling (incorporating booking & procurement lead times), strict null-vs-zero cost arithmetic, finite parameter/step/safety/coupled-constraint checks, and version-to-version revision diffs.
- `scripts/plan_report_verifier.py`: Audits 6-element completeness, confirmation gates, prospective disclaimer compliance, and rejects generic textbook boilerplate.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit}.py`: Deterministic runtime, budget, atomic result JSON, grounding, and output audit utilities.
- `references/design_and_control_blueprint.md` & `references/input_output_schemas.md`: Methodology guide and exact JSON schemas.
- `examples/minimal_case_example.json` & `examples/example_output_summary.md`: Illustrative end-to-end example.

## Runtime Requirements & Boundaries

- **Dependencies**: Python 3.10+ standard library only (zero external network or third-party package dependencies).
- **Scope Boundary**: Evaluates prospective planning scenarios only; does not perform literature reviews, statistical modeling of post-run datasets, or replace institutional EHS/IRB/IACUC approvals.
## Paradigm Lineage & Open-Source Boundary Mapping

- **eLabFTW Plan-vs-Actual Separation & Pinned SOP Versions (`elabftw/elabftw` & Issue #3815)**: Adopts eLabFTW's strict separation between prospective planned templates (`prospective_contract`) and executed lab logs, and operationalizes Issue #3815 (linking experiments to a pinned SOP version rather than a mutating `latest` pointer) inside `scripts/source_to_plan_extractor.py` (`SOP_VER_RE`) and `scripts/plan_domain_optimizer.py` (`SOP_VERSION_CONFLICT` / `SOP_UNPINNED_VERSION` hard blocker).
- **Resource Directory 4-State Adapter**: `scripts/source_to_plan_extractor.py` includes a zero-dependency thin adapter (`elabftw_resource_adapter` / `elabftw_json_resource_adapter`) that maps eLabFTW-style CSV/JSON resource exports (`title`, `category`, `canwrite`, `status`, `metadata`) onto our 4-state availability contract (`available`, `needs_procurement`, `outsourceable`, `unavailable`).
- **Boundary vs Full ELN Platforms**: Unlike a full server-backed ELN (eLabFTW / OpenBIS), this offline skill performs zero live instrument booking, zero vendor price lookup, and zero wet-lab approval signing; any unquoted cost or inferred unit split is explicitly marked `UNVERIFIED_COST_INPUT` / `UNVERIFIED_REQUIRED`.
