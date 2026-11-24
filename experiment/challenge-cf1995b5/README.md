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
