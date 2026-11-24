# Output Contract Specification (`challenge-e7598a44`)

## Primary Deliverable: `/app/output/result.json`

`result.json` dynamically honors all fields declared in `/input/case.json:output_contract` and guarantees the following top-level structure:

- `case_id` (`string`): Exact identifier read from `/input/case.json` or `/input/task.md`.
- `results` (`list[object]`): Retained deduplicated candidates (`include` and `borderline`), each carrying `rank`, `rid`, `title`, `authors`, `year`, `venue`, `doi`, `sources`, `version`, `label`, `relevance_tier`, `perspective`, `score`, `matched_sub_questions`, `sub_question_scores`, `confidence`, `arbitration`, `reason_codes`, `paths`, and `record_refs`.
- `evidence` (`object`):
  - `element_card`: 7-slot decomposition with explicit `UNKNOWN` values.
  - `requirement_ledger`: Provenance-tracked list of question facets and case constraints.
  - `sub_question_dag`: Dependency-ordered sub-questions (`stage`, `execution_mode`, `depends_on`, `terms`).
  - `sub_question_sufficiency`: Per-sub-question R1/R2 hit counts, marginal gain, 5-state `evidence_state`, and `gap_note`.
  - `evidence_by_sub_question`: Top supporting works mapped to each sub-question ID with snippets and source paths.
  - `query_rounds`, `expansion_log`, `term_diagnosis`, `chains`, `rollback_log`, `sources`.
- `status` (`object`):
  - `finite_snapshot_only`: `true`, `live_remote_coverage_claimed`: `false`.
  - `stopping_verdict`: `decision`, `reason`, `covered_sub_questions`, `partial_sub_questions`, `uncovered_sub_questions`, `marginal_new_hits_last_round`.
  - `sub_question_states`, `completed`, `not_completed`, `source_load_errors`, `pagination_issues`, `conflicts`, `ambiguous_identity_candidates`, `gaps`, `duplicates_merged`.
- `metrics` (`object`): Input/unique counts, `source_count_conserved`, sub-question state counts, `cohen_kappa`, `perspective_distribution`, `prisma_flow`, `stopping_decision`.
- `findings` (`list[string]`): Concise grounded summary claims for automated grounding checks (`gap_gate.py`).
- `artifacts` (`list[string]`): Relative paths of generated files (`question_search_plan.json`, `subquestion_evidence_matrix.csv`, `ranked_candidates.csv`, `report.md`, `audit_log.json`, `run_manifest.json`).

## Companion Artifacts (All UTF-8, Zero `.tsv`)

| Artifact | Purpose |
| --- | --- |
| `question_search_plan.json` | Complete machine-readable DAG, query rounds, term diagnosis, sufficiency table, and stopping verdict |
| `subquestion_evidence_matrix.csv` | Compact spreadsheet view of sub-question coverage and gaps |
| `ranked_candidates.csv` | Ranked non-excluded literature table with sub-question bindings and reason codes |
| `report.md` | Human-readable synthesis report with Element Card, DAG table, stopping rationale, and limitations |
| `audit_log.json` | SHA-256 hash-chained execution trail |
| `run_manifest.json` | Input file SHA-256 inventory and load diagnostics |
