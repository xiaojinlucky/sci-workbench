# Output Contract Specification (`grounded-literature-review`)

## Deliverables
- `/app/output/result.json`
- `/app/output/review_synthesis_draft.md`
- `/app/output/review_evidence_matrix.json`

## Required Fields in `result.json`
- `case_id`, `status`, `artifacts`, `sources`, `prisma_flow`, `themes[]`, `method_comparison_matrix[]`, `consensus_findings[]`, `conflicting_evidence[]`, `prospective_or_uncompleted[]`, `evidence_gaps[]`, `draft_verification`, `uncertainty`, `metrics`, `metric_provenance`, `findings`, `gaps`.

## Note-First & Multi-Source Semantics
- Each note-backed evidence record preserves `claim_key`, `note_file`, `note_span`, `pdf_file`, `pdf_link_status`, `pdf_locator`, `pdf_locator_status`, `pdf_page_number`, `quote_grounded_in_pdf`, and `evidence_state`. Missing PDFs or quotes absent from the cited page remain `UNKNOWN`.
- General multi-source records preserve `material_access`, `hedging_register`, `locator`, `evidence_state`, and verbatim `quote`. `abstract_only` records are capped at `PARTIAL`; `discovery_only` records are excluded before writing.
- Every `metrics` key has a non-empty `metric_provenance` entry verified by `gap_gate.py`.
