# Incremental Literature Tracking & Idempotency Protocol (`challenge-1e85736f`)

## 1. Fixed UTC Window & Watermark Normalization

1. Load `subscription_config.json` (or `subscription` / `window` in `case.json` and snapshot headers).
2. Normalize `window_start`, `window_end`, and `previous_watermark` to strict ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`).
3. Log explicit `date_anomalies` for:
   - `timezone_missing_assumed_utc`: Naive ISO timestamps lacking `Z` or `±HH:MM`.
   - `timezone_converted_to_utc`: Non-UTC offsets converted cleanly to UTC.
   - `inverted_window_bounds`: `window_start > window_end`.
   - `record_missing_pub_date`: Record missing both `date` and `year`.
   - `indexed_before_published`: `indexed_date < pub_date`.

## 2. Canonical Dedup Keys & Preprint-to-VoR Matching

- **Canonical Dedup Key Precedence**:
  1. `doi:<norm_doi>` (lowercased, stripped of `https://doi.org/` / `doi:`)
  2. `pmid:<norm_pmid>`
  3. `arxiv:<arxiv_base>` (version suffix `v1/v2/...` stripped)
  4. `title_author:<norm_title>|<first_author_family>`
  5. `rid:<record_id>`
- **Intra-Window Deduplication**: Runs `search_core.dedup.cluster_records_detailed()` on `current_snapshot`. Distinct formal (non-preprint) DOIs are never silently merged (`ambiguous:distinct_formal_dois`). Every merged intra-window duplicate is emitted to `duplicate_suppressed` with `suppression_reason: "intra_window_cross_source_duplicate"`.
- **Cross-Snapshot Matching**: Each deduplicated current cluster is matched against `previous_snapshot` via:
  1. Shared normalized DOI or `alt_dois` cross-link (`exact_doi` / `alt_doi_bridge`).
  2. Shared PMID (`exact_pmid`) or arXiv base ID (`exact_arxiv_base`).
  3. `search_core.dedup.same_work()` (respecting the distinct-formal-DOI guard).

## 3. Five-Category Triage Precedence (100% Conservation)

Every raw record in `current_snapshot` is assigned to exactly one bucket:

1. **`retraction_or_correction` (Critical Alert)**: Any record with `is_retracted`, `is_correction`, or retraction/erratum/corrigendum status/title cues. Takes highest precedence so safety updates on previously seen papers are never muted.
2. **`version_upgrade` (High Alert)**: Matched to a prior `preprint` record (version `"preprint"`, preprint DOI prefix `10.1101/`, `10.48550/arxiv.`, etc., or source `"arxiv"`) and upgraded to a formal `published`/`conference` VoR. Retains full `prev_doi -> new_doi/venue` chain and combined `alt_dois`.
3. **`duplicate_suppressed` (Silent Suppress)**: Matched to a prior record with no retraction or preprint-to-VoR upgrade (`already_seen_in_previous_snapshot`), plus all intra-window cross-source duplicates (`intra_window_cross_source_duplicate`).
4. **`delayed_indexed_archival` (Archival Log Only)**: Unseen in prior snapshot, but `year < window_start_year` (or `window_start - pub_date > 365 days`). Records exact `indexing_lag_days` and suppresses "latest breakthrough" push alerts.
5. **`genuinely_new` (Normal Alert)**: Unseen in prior snapshot, published in the current window, consolidated across sources so multi-source hits count strictly once.

**Conservation Invariant**:
`current_raw_count == len(genuinely_new) + len(version_upgrade) + len(retraction_or_correction) + len(delayed_indexed_archival) + len(duplicate_suppressed)`.

## 4. SHA-256 Idempotent Alert Ledger

For each alert-eligible item (`retraction_or_correction`, `version_upgrade`, `genuinely_new`):
```text
idempotency_key = SHA-256(f"{subscription_id}|{window_start_utc}|{window_end_utc}|{category}|{dedup_key}")
```
- Keys not in `alert_ledger.json` are emitted and persisted atomically.
- Keys already present in `alert_ledger.json` are routed to `suppressed_already_notified_items`.
- On a repeat execution of the same window with the same `--state-file`: `idempotent_rerun == true`, `new_alerts_emitted == 0`, and `suppressed_already_notified == len(alert_candidates)`.
