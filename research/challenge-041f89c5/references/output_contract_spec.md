# Output Contract Specification (`topic-novelty-feasibility`)

## Deliverables
- `/app/output/result.json`
- `/app/output/topic_evaluation.json`
- `/app/output/topic_comparison_matrix.md`

## Required Fields in `result.json`
- `case_id`, `status`, `artifacts`, `sources`, `formulation_status`, `corpus_scope`, `question_formulation`, `prior_sources[]`, `prior_work_matrix[]`, `novelty_checks[]`, `feasibility_checks[]`, `go_no_go_decision`, `opposing_evidence[]`, `premortem_risk_matrix[]`, `uncertainty`, `metrics`, `metric_provenance`, `findings`, `errors`, `warnings`, `gaps`.
- Every `metrics` key MUST have a non-empty entry in `metric_provenance` verified by `gap_gate.py`.
- Every `[GROUNDED]` feasibility premise MUST cite a verified `source_ref` present in `/input`.
