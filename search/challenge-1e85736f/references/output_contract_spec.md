# Output Contract Specification (`challenge-1e85736f`)

All runtime deliverables are written atomically under `/app/output/` (or `--output`).

## 1. `result.json`

- `case_id` (`string`), `task_id` (`"challenge-1e85736f"`), `skill` (`"incremental-literature-tracker"`).
- `status` (`string`): `"ok"` or `"partial_with_gaps"` (strict M6 `final_audit.py` compliance).
- `results` (`object`):
  - `genuinely_new`: List of deduplicated new publications in window.
  - `version_upgrade`: List of preprint-to-VoR upgrades preserving `prev_doi`, `new_doi`, `new_venue`, `alt_dois`, and `version_chain`.
  - `retraction_or_correction`: List of critical retraction/erratum updates with `state_transition` and `matched_prior_id`.
  - `delayed_indexed_archival`: List of backfilled older publications with `indexing_lag_days` (muted from push alerts).
  - `duplicate_suppressed`: List of already-seen and intra-window duplicates with `suppression_reason` and `dedup_key`.
  - `emitted_alerts`: Priority-ordered (`critical -> high -> normal`) alerts emitted on this run.
  - `triage_count_conserved` (`boolean`): Exact raw-count conservation check.
- `evidence` (`object`): `subscription_config`, `utc_window`, `snapshot_metadata`, `incremental_delta_csv`, `alert_digest_json`, `watermark_state_json`, `dedup_summary`, `supplementary_api_audit`, `audit_chain`.
- `idempotency` (`object`): `window_key`, `run_sequence_for_window`, `idempotent_rerun`, `alert_candidates_total`, `new_alerts_emitted`, `suppressed_already_notified`, `ledger_total_keys`, `state_file_name`.
- `status_details` (`object`): Finite-snapshot boundaries, `date_anomalies`, `pagination_issues`, `source_load_errors`, `ambiguous_identity_candidates`.
- `metrics` (`object`) & `metric_provenance` (`object`): 1-to-1 mapped numeric metrics and artifact locators.
- `findings`, `warnings`, `gaps`, `artifacts`.

## 2. `incremental_delta.csv`

UTF-8-SIG CSV columns:
`record_id,triage_category,alert_action,idempotency_key,title,doi,alt_dois,venue,pub_date,indexed_date,source,version,matched_prior_id,dedup_key,rationale`

## 3. `alert_digest.json` & `watermark_state.json`

- `alert_digest.json`: Complete triage buckets, emitted and rerun-suppressed alert lists, noise suppression summary, date anomalies, pagination issues, and supplementary API corpus audit.
- `watermark_state.json`: Subscription metadata, UTC window bounds, `previous_watermark_utc`, `new_watermark_utc`, `run_sequence_for_window`, `idempotent_rerun`, and `alert_ledger_keys`.
