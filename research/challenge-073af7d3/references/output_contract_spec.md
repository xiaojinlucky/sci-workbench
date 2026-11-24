# Output Contract Specification

## Deliverables
- `/app/output/result.json`
- `/app/output/trend_analysis.json`
- `/app/output/trend_evolution_report.md`

## Required Fields in `result.json`
- `case_id`, `status`, `artifacts`, `sources`, `records_total`, `date_basis_breakdown`, `date_basis_by_year`, `yearly_distribution`, `preprint_vs_vor_sensitivity_timeline`, `largest_step_year`, `alias_merges_applied`, `topic_evolution`, `structural_shifts`, `stage_representatives`, `research_gaps`, `missing_year_records`, `uncertainty`, `metrics`, `metric_provenance`, `findings`, `warnings`, `gaps`.
- Every `metrics` key must map to a non-empty `metric_provenance` entry verified by `gap_gate.py`.
