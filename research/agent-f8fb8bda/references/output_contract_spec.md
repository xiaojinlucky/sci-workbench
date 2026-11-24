# Output Contract Specification (`deep-research-orchestrator`)

Every execution against `/input/case.json` produces `/app/output/result.json`, `/app/output/deep_research_ledger.json`, `/app/output/deep_research_brief.md`, and `/app/work/checkpoint_state.json`.

## Required Root Keys in `result.json`
- `case_id`, `status`, `artifacts`, `sources`, `corpus_scope`, `sub_questions_dag`, `sources_matrix`, `evidence_matrix`, `conflict_ledger`, `research_gaps`, `access_log`, `tool_call_log`, `synthesis_verification`, `uncertainty`, `metrics`, `metric_provenance`, `findings`, `gaps`.

## Access-Tier & Grounding Guarantees
- Every row in `sources_matrix` declares `material_access` (`fulltext_pdf` | `fulltext_xml` | `fulltext_local_text` | `abstract_only` | `discovery_only` | `blocked_or_unavailable`) and `access_note`.
- `discovery_only` and `blocked_or_unavailable` records are strictly excluded from `evidence_matrix` and rejected by `--verify-report --strict`.
- `abstract_only` records are capped at `PARTIAL` and flagged if promoted with conclusive full-text proof language.
- Every key inside `metrics` has a non-empty entry in `metric_provenance` verified by `gap_gate.py`.
- Final deliverables are verified by `final_audit.py --dir /app/output --result-file result.json --require deep_research_ledger.json --require deep_research_brief.md`.
