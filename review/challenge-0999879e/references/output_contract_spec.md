# Output Contract Specification (`challenge-0999879e`)

Every run against `/input/case.json` and `/input/task.md` must emit `/app/output/result.json` plus domain deliverables:

1. **Primary Structured Output (`/app/output/result.json`)**:
   - Required root keys: `schema_version`, `case_id`, `task_id`, `status` (`"ok"` or `"partial_with_gaps"` for `final_audit.py` compatibility), `audit_status` (`"completed" | "partial" | "indeterminate" | "failed" | "refused"`), `passed`, `verdict`, `coverage_ratio`, `sources`, `artifacts`, `metrics`, `metric_provenance`, `facet_coverage`, `severity_counts`, `findings`, `uncertainty`, `gaps`.
2. **Structured Domain Audit (`/app/output/quality_audit.json`)**:
   - Includes `documents_scanned` (with SHA-256 and extractor), `images_scanned`, `image_duplication_matches`, `figure_table_summary`, `facet_coverage` across all 6 canonical facets, `findings`, `uncertainty`, and `gaps`.
   - Every finding MUST include: `finding_id`, `priority` (`P0`-`P3`), `blocking_tier` (`blocking`/`optional`), `facet`, `finding_type` (`factual_error`/`insufficient_evidence`/`norm_violation`/`suggestion`), `defect_distinction` (`method_flaw`/`under_reported`/`needs_reconciliation`), `severity` (`critical`/`major`/`minor`/`suggestion`), `location` (`file`, `section`, `line_start`, `line_end`, optional `page`), `verbatim_quote`, `evidence_basis`, `actionable_revision`, `verification_method`, `revision_cost`, `firewall_check`, and `status` (`confirmed`/`needs_human_review`).
3. **Human-Readable Review Dossier (`/app/output/quality_review_report.md`)**:
   - Presents verdict, `coverage_ratio`, six-facet coverage matrix, priority-ordered actionable findings with line quotes, verification methods, and explicit coverage gaps.
4. **Special Boundary Rule (Observational Causal Claim with Citation)**:
   - For a causal claim in an observational manuscript with an in-text citation marker, retain the exact manuscript line span and citation marker as `status = "needs_human_review"`, `audit_status = "partial"`, and `passed = false` until the source passage and identification assumptions are verified.
5. **Mandatory Verification Commands**:
   ```bash
   python3 -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
   python3 -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
   python3 -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json --require quality_audit.json --require quality_review_report.md
   ```
