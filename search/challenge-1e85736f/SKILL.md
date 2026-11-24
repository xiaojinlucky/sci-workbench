---
name: incremental-literature-tracker
description: Closed-loop incremental literature monitoring across fixed UTC watermark windows: diffs prior and current snapshots into five strict categories (genuinely_new, version_upgrade, retraction_or_correction, delayed_indexed_archival, duplicate_suppressed), preserves preprint-to-VoR chains, flags date/timezone and PyAlex #103 pagination anomalies, and enforces SHA-256 idempotent rerun suppression.
---

# Incremental Literature Discovery & Tracking (`challenge-1e85736f`)

## 1. Mission & Scientific Scope

A one-off keyword query cannot sustain longitudinal literature tracking. In real bibliographic feeds, "new" records are noisy: database indexing lags behind publication dates, a manuscript appears first as a preprint (`bioRxiv`/`arXiv`) before upgrading to a journal Version of Record (VoR), prior records receive retractions or errata, and the same new article arrives from multiple indexes (Crossref, EuropePMC, OpenAlex) inside the same window. Treating every raw hit as a fresh discovery destroys trust through alert fatigue, duplicate notifications, and stale-paper false alarms.

This Skill delivers an end-to-end, offline, replayable **incremental tracking pipeline** rather than a single search dump:

`Subscription Config -> Fixed UTC Window & Watermark -> Previous vs. Current Snapshot Diff -> 5-Category Incremental Triage -> Alert Merging & Noise Suppression -> SHA-256 Idempotent Rerun Verification & Version Ledger`

**In Scope**
- **Subscription & Fixed UTC Window Management**: Parses `subscription_config.json` and `case.json` window metadata, normalizes `[window_start, window_end]` and `previous_watermark` to strict ISO-8601 UTC, and detects missing dates, missing timezones, or `indexed_date < pub_date` inversions (`date_anomalies`).
- **Five-Category Incremental Triage (100% Record Conservation)**:
  1. `genuinely_new`: Published within the current tracking window, unseen in `previous_snapshot`, deduplicated across sources (counted strictly once).
  2. `version_upgrade`: Present in `previous_snapshot` as a preprint and upgraded in the current window to a formal journal/conference Version of Record, retaining the full `prev_doi/arxiv -> new_doi/venue` chain without inflating new-discovery counts.
  3. `retraction_or_correction`: High-priority safety override when a prior or newly observed record enters a retracted, withdrawn, erratum, or corrigendum state.
  4. `delayed_indexed_archival`: Legacy publication whose publication date/year significantly precedes the current window and only entered the index during this window; logged as archival backfill and suppressed from "latest breakthrough" push alerts.
  5. `duplicate_suppressed`: Either already seen in `previous_snapshot` (without state/version upgrade) or a cross-source duplicate inside the current window; silently suppressed from alerts while preserving full dedup key and rollback evidence.
- **Idempotent Rerun Ledger (`--state-file` / `alert_ledger.json`)**: Computes a deterministic SHA-256 idempotency key per `(subscription_id, window_start_utc, window_end_utc, category, canonical_dedup_key)`. Re-executing the identical window guarantees `idempotent_rerun: true`, `new_alerts_emitted: 0`, and `suppressed_already_notified > 0`—eliminating secondary push noise after retries or network/pagination recoveries.
- **Failure & Anomaly Auditing**: Explicitly detects empty windows (`0` new discoveries without backfill), PyAlex #103 cursor/pagination failures, rate-limit/timeout snapshot errors, and distinct-formal-DOI merge ambiguities.

**Out of Scope & Hard Boundaries**
- Does **not** execute live remote web scraping, scheduled external polling, or claim global "latest publication" completeness beyond the organizer-supplied finite snapshots.
- Does **not** treat alert digests as peer-reviewed scientific conclusions or perform automated full-text synthesis.

---

## 2. Preflight & Intake Protocol (`M1` + `M3`)

Locate all scripts relative to this `SKILL.md` directory (`scripts/`). Before triaging records:

1. **Inspect `/input` without guessing**: Check `case.json`, `task.md`, `subscription_config.json`, `previous_snapshot.json`, `current_snapshot.json`, and any supplementary `api/` responses (Crossref, OpenAlex, EuropePMC, arXiv).
2. **Initialize Runtime & Budget Guards**:
   ```bash
   python -B scripts/preflight_probe.py --input /input --output /app/output --work /app/work --out /app/work/env.json
   python -B scripts/budget_guard.py --state /app/work/budget.json --init
   ```
3. **Screening, Not Cherry-Picking**: Every record in `current_snapshot` must map cleanly to exactly one of the five triage categories (`triage_count_conserved == true`). Never drop duplicates silently; retain exact dedup keys and suppression reasons for replay.

---

## 3. Core Execution Pipeline & Idempotency Verification

Run the pure-Python 3.12 stdlib incremental tracker:

```bash
python -B scripts/incremental_tracker.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --state-file /app/work/alert_ledger.json
```

### Step-by-Step Pipeline Mechanics

1. **Subscription & UTC Window Normalization**:
   - Reads `subscription_id`, `topic`, `schedule`, `timezone`, `previous_watermark`, `window_start`, and `window_end` from `subscription_config.json` and/or `case.json`.
   - Converts all timestamps to explicit UTC (`YYYY-MM-DDTHH:MM:SSZ`). If a timestamp lacks timezone info, normalizes as UTC and logs `timezone_missing_assumed_utc` in `date_anomalies`. If a record lacks publication or indexing dates, logs `record_missing_pub_date` / `record_missing_indexed_date`.
2. **Multi-Format Snapshot Resolution**:
   - Supports (a) standalone `previous_snapshot.json` and `current_snapshot.json`, (b) embedded `previous_snapshot` / `current_snapshot` arrays in `case.json`, and (c) multi-source `api/` fallback when no explicit current snapshot exists.
   - When explicit snapshots coexist with supplementary `api/` files, computes the conserved 5-way delta over the explicit snapshots while separately auditing supplementary `api/` files in `supplementary_api_audit` (recording source counts, in-window date hits, retractions, and pagination/cursor errors).
3. **Intra-Window Cross-Source Deduplication (`search_core.dedup`)**:
   - Runs `cluster_records_detailed()` over `current_snapshot` using normalized DOI, `alt_dois`, PMID, arXiv base ID (stripping `vN`), and title + first-author matching, enforcing the hard false-merge guard across distinct formal DOIs.
   - Canonical records advance to watermark comparison; every merged cross-source duplicate is logged in `duplicate_suppressed` with `suppression_reason: "intra_window_cross_source_duplicate"`, canonical target ID, match tier, and dedup key.
4. **Watermark Comparison & 5-Category Triage**:
   - Matches each deduplicated current cluster against `previous_snapshot` via normalized DOI, `alt_dois` cross-links, PMID, arXiv base ID, or `same_work()`.
   - Evaluates categories in strict precedence order:
     1. **`retraction_or_correction`** (`priority: critical`): Retraction, withdrawal, erratum, or corrigendum flag or status change (always overrides prior-seen suppression so safety updates are never muted).
     2. **`version_upgrade`** (`priority: high`): Matched prior record is a preprint (`version == "preprint"` or preprint DOI prefix `10.1101/`, `10.48550/arxiv.`, etc.) and current canonical record is a published/conference VoR; preserves full `prev_doi -> new_doi/venue` chain and `alt_dois`.
     3. **`duplicate_suppressed`** (`action: silent_suppress`): Matched prior record with no version upgrade or retraction change (`suppression_reason: "already_seen_in_previous_snapshot"`).
     4. **`delayed_indexed_archival`** (`action: archival_log_only`): Unseen in prior snapshot, but publication year precedes `window_start_year` (or publication date precedes `window_start` by `> 365` days); logs exact `indexing_lag_days` and keeps it out of "latest discovery" pushes.
     5. **`genuinely_new`** (`priority: normal`): Unseen in prior snapshot, published in the current window, deduplicated across sources.
5. **Alert Merging, Noise Suppression & SHA-256 Idempotent Ledger**:
   - Only `retraction_or_correction`, `version_upgrade`, and `genuinely_new` generate push alerts (sorted `critical -> high -> normal`).
   - Each alert receives a deterministic SHA-256 `idempotency_key` derived from `(subscription_id, window_start_utc, window_end_utc, category, canonical_dedup_key)`.
   - Checks `--state-file` (`alert_ledger.json`): unseen keys are emitted and persisted; keys already recorded in the ledger are routed to `suppressed_already_notified` so re-running the identical window emits `new_alerts_emitted: 0` with `idempotent_rerun: true`.
   - Never mutates `/input`; writes updated state to `--state-file` and exports an immutable snapshot copy to `/app/output/watermark_state.json`.

---

## 4. Verification & Post-Run Audit (`M5` + `M6`)

```bash
python -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require incremental_delta.csv \
  --require alert_digest.json \
  --require watermark_state.json \
  --require report.md
```

**Host Agent Review Checklist**
1. **Strict Conservation**: Confirm `raw_current_count == genuinely_new + version_upgrade + retraction_or_correction + delayed_indexed_archival + duplicate_suppressed` (`triage_count_conserved == true`).
2. **Preprint-to-VoR Integrity**: Verify `version_upgrade` items preserve both `prev_doi`/`arxiv` and `new_doi`/`venue` and are NOT double-counted in `genuinely_new`.
3. **Archival Isolation**: Verify `delayed_indexed_archival` items (e.g., 2021 articles newly indexed in 2026) are excluded from push alerts and carry computed `indexing_lag_days`.
4. **Retraction Precedence**: Verify retracted records previously seen as published are elevated to `critical` alerts rather than muted as seen duplicates.
5. **Idempotent Retry Safety**: On retry/recovery runs over the same window with `--state-file`, confirm `idempotent_rerun == true` and `new_alerts_emitted == 0`.

---

## 5. Deliverable Contract & Output Files

| File | Format | Purpose |
| --- | --- | --- |
| `result.json` | JSON | Primary Harbor deliverable (`case_id`, `status`, `results`, `evidence`, `status_details`, `idempotency`, `metrics`, `metric_provenance`, `findings`, `gaps`, `artifacts`) |
| `incremental_delta.csv` | CSV | Per-record 5-category triage table with alert action, idempotency key, dedup key, matched prior ID, and rationale |
| `alert_digest.json` | JSON | Structured alert payload, 5 triage buckets, noise suppression counts, date anomalies, pagination issues, and supplementary API audit |
| `watermark_state.json` | JSON | Reproducible watermark checkpoint (`subscription_id`, UTC window bounds, `previous_watermark_utc`, `new_watermark_utc`, run sequence, `idempotent_rerun`, ledger keys) |
| `report.md` | Markdown | Concise human-readable tracking digest ready for researcher review |
| `audit_log.json` | JSON | SHA-256 hash-chained execution audit log |
| `run_manifest.json` | JSON | Input file inventory, checksums, and load error ledger |

---

## 6. Exception & Failure Recovery Matrix

| Anomaly / Failure | Detection Signal | Required Action |
| --- | --- | --- |
| Empty current window | `len(current_records) == 0` | Emit `0` alerts, log `EMPTY_CURRENT_SNAPSHOT` gap, advance/hold watermark honestly without backfill |
| Missing or naive timezone | Timestamp lacks `Z` or `±HH:MM` | Normalize to UTC, record `timezone_missing_assumed_utc` in `date_anomalies` |
| Missing pub/indexed date | Empty `date` and `year == 0` | Log `record_missing_pub_date`; do not fabricate publication year; triage conservatively |
| PyAlex #103 cursor / pagination failure | API snapshot contains `error`, `rate_limited`, `timeout`, or broken `next_cursor` | Record in `pagination_issues` and `gaps`; preserve prior watermark/ledger so retry is idempotent |
| Duplicate run over same window | All candidate `idempotency_key` values already in `alert_ledger.json` | Set `idempotent_rerun: true`, `new_alerts_emitted: 0`, log `suppressed_already_notified` |
| Homonymous distinct formal DOIs | `same_work()` -> `ambiguous:distinct_formal_dois` | Keep separate; log in `ambiguous_candidates` for human review |

---

## 7. Subscription Lifecycle, Window Advancement & Replay Rules

1. **Continuous Non-Overlapping Windows**:
   - Each tracking cycle anchors its start boundary to `previous_watermark` and advances `new_watermark_utc` to `window_end_utc` only after the run finishes cleanly and persists `alert_ledger.json`.
   - When an API source reports a PyAlex #103 cursor failure, timeout, or rate-limit error (`pagination_issues` non-empty), the watermark state records the incident explicitly so operators can retry the exact same window safely without duplicating already-emitted alerts.
2. **Replayability & Full Version Trail**:
   - Every run emits `watermark_state.json`, `alert_digest.json`, `incremental_delta.csv`, `audit_log.json`, and `run_manifest.json` (with input SHA-256 checksums), allowing any historical window delta to be independently recomputed and audited.
   - Version upgrades (`version_upgrade`) retain `prev_record_id`, `prev_doi`/`prev_arxiv`, `new_doi`, `new_venue`, combined `alt_dois`, and `match_basis` so reviewers can trace every manuscript from preprint to peer-reviewed publication.
3. **Host Agent Execution & Review Discipline**:
   - Inspect `/input/case.json`, `subscription_config.json`, `previous_snapshot.json`, and `current_snapshot.json` before summarizing results; never infer window bounds or topic scope from folder names alone.
   - Execute `incremental_tracker.py` in the first turn to guarantee an atomic, M2/M5/M6-compliant deliverable under `/app/output/result.json`.
   - When presenting alerts in the final summary, separate **Actionable Updates** (`retraction_or_correction`, `version_upgrade`, `genuinely_new`) from **Suppressed Noise & Archival Backfills** (`delayed_indexed_archival`, `duplicate_suppressed`) and state the finite-snapshot boundary explicitly.
   - For empty windows (`current_raw_count == 0`), report `0` discoveries honestly—never backfill historical papers from outside the window to make the digest look busy.

---

## 8. Quick-Start Walkthrough (Illustrative Window Example)

Given a biweekly UTC window `[2026-11-01T00:00:00Z, 2026-11-15T23:59:59Z]` with 6 raw current records:
- Two cross-source copies of one 2026 article -> `1 genuinely_new` (emitted once) + `1 duplicate_suppressed` (`intra_window_cross_source_duplicate`).
- One 2025 preprint now published in a 2026 journal with `alt_dois` linking back to the preprint DOI -> `1 version_upgrade` (`high` alert preserving full version chain).
- One 2024 published article newly flagged `is_retracted: true` -> `1 retraction_or_correction` (`critical` alert overriding prior-seen status).
- One 2021 archival paper newly indexed on `2026-11-07` -> `1 delayed_indexed_archival` (logged with lag days, muted from latest-paper push).
- One 2025 article identical to `previous_snapshot` -> `1 duplicate_suppressed` (`already_seen_in_previous_snapshot`).
- **Run 1** emits `3` alerts (`1 critical + 1 high + 1 normal`) and suppresses `3` noise records; **Run 2** with the same `--state-file` emits `0` alerts (`idempotent_rerun: true`, `suppressed_already_notified: 3`).

---

## 9. Canonical Dedup Key & Replay Verification Reference

To guarantee deterministic replay across retries and independent audits, every record is keyed using strict identifier precedence:

1. `doi:<norm_doi>` (lowercased, stripped of `https://doi.org/` and `doi:` prefixes).
2. `pmid:<norm_pmid>` (5–9 digit PubMed identifier).
3. `arxiv:<arxiv_base>` (version suffix `v1`/`v2`/`v3` stripped so arXiv revisions unify cleanly).
4. `title_author:<norm_title>|<first_author_family>` (NFKC + diacritic-folded title plus normalized first-author surname).
5. `rid:<record_id>` (final fallback when metadata fields are absent).

**Replay Verification Steps for Reviewers**
- Compare `alert_digest.json:conservation` against raw input length: `current_raw_count == genuinely_new + version_upgrade + retraction_or_correction + delayed_indexed_archival + duplicate_suppressed`.
- Inspect `watermark_state.json:alert_ledger_keys` to verify every emitted alert maps 1-to-1 to a deterministic SHA-256 idempotency key.
- Re-run `incremental_tracker.py` against the identical `/input` and `--state-file` to confirm `run_sequence_for_window` increments while `new_alerts_emitted` stays `0` and `idempotent_rerun` flips to `true`.
- Inspect `incremental_delta.csv` to verify every suppressed duplicate and delayed archival paper carries an explicit `dedup_key` and human-readable `rationale`.

**Safety & Grounding Checklist Before Finalizing**
- Confirm zero identifiers (DOIs, arXiv IDs, PMIDs) appear in `result.json` unless grounded verbatim in `/input` (`gap_gate.py` returns `ok: true`).
- Confirm all declared files in `result.json:artifacts` exist and are non-empty under `/app/output` (`final_audit.py` returns `passed: true`).
- Confirm no temporary state files or `/input` files were modified during execution; all mutable ledger state stays strictly in `--state-file` / `/app/work`.
- Confirm `status_details.finite_snapshot_only == true` and `live_remote_coverage_claimed == false` in every run.

See `references/incremental_tracking_protocol.md`, `references/output_contract_spec.md`, and `examples/` for full rules, schemas, and minimal runnable examples.
