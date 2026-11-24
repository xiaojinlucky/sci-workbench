---
name: scientific-data-cleaning-auditor
description: >-
  Use when diagnosing storage/encoding faults and executing reversible,
  workorder-bound cleaning on scientific tables or transcribed published
  aggregate tables against explicit data dictionaries and per-metric unit/QC
  rules. Preserves leading-zero string identifiers, distinguishes five
  missing/sentinel classes (-999, ND, <LOD, blank/NA, invalid text) from true
  numeric zeros without silent imputation or population inference, quarantines
  conflicting duplicate keys, and enforces 100% disk read-back reconciliation
  between cleaned CSV, manifest JSON, and quality report under /app/output.
---

# Scientific Data Cleaning & Quality Audit Skill

Skill resources live under `/skills/weekly-skill`. Case inputs live under `/input/` (`case.json`, `task.md`, raw tables, data dictionary, and explicit cleaning workorder). Final deliverables belong under `/app/output/`, including `result.json` with `case_id` and all original `output_contract` fields.

**Runtime & Integrity Contract**:
- **Zero external dependency guarantee**: All cleaning, fault-scanning, and reconciliation scripts run on pure Python 3.12 stdlib (`csv`, `json`, `hashlib`, `re`, `unicodedata`) in offline Linux containers without requiring external converters or unverified packages.
- **Never assume prebuilt gold files**: Do not look for or assume a promised `quality-gold.md` file; ground every cleaning decision exclusively in the delivered raw table, data dictionary, and workorder.
- **Published aggregate tables — strict no-fabrication boundary**: When cleaning published aggregate transcriptions or workorder-bound tables, **never silently impute missing/invalid cells** (no mean/median/zero fill) and **never infer unrecorded population statistics** from aggregate numbers.

---

## 1. Decision Tree: From Raw Table to Reconciled Output

Execute the following 6-stage decision tree in strict order. Never modify a cell before Stage 2 completes.

### Stage 1 — Read-Only Intake & Contract Binding
1. Run `preflight_probe.py`, `budget_guard.py --init`, `result_guard.py --init`, and `block_indexer.py --scan /input`.
2. Read `case.json`, `task.md`, the data dictionary, and the cleaning workorder verbatim.
3. Lock five immutable facts before touching any data row:
   - **Table granularity**: Published aggregate transcription vs. individual observation table (if aggregate, set `forbid_imputation=true`, `forbid_population_inference=true`).
   - **Primary key & string IDs**: Explicitly list all identifier columns (`sample_id`, `record_id`, `subject_id`, `batch_code`, `zip_code`). **Always read as strings** (`dtype=str` in Pandas or `csv.DictReader` in stdlib) so leading zeros (`00123` vs `123`) are never coerced into identical integers.
   - **Per-metric unit & QC rules**: Map wide-table numeric columns or long-table `metric_column` values to their exact `target_unit`, reversible `unit_factors_to_target`, and physical/domain `qc_bounds`.
   - **Sentinel taxonomy**: Separate `below_lod` (`<LOD`, `<LOQ`), `not_detected` (`ND`), `instrument_sentinel` (`-999`, `9999`), and `entry_missing` (`""`, `NA`, `N/A`, `NULL`) from **true numeric `0`**.
   - **Duplicate disposition policy**: Separate exact/consistent duplicates (drop later duplicate, keep earliest with audit log) from **conflicting primary-key duplicates** (same key, different non-key payload -> **quarantine, never silently overwrite**).

### Stage 2 — Read-Only Byte & Fault Profiling (`dirty_table_fault_scanner.py`)
Inspect the raw file without modifying it:
- **Byte & storage layer**: Detect encoding (`utf-8-sig` BOM, `utf-8`, `gb18030`, `latin-1`), delimiter (`,` / `;` / `\t` / `|`), CRLF/LF, header trailing whitespace, extra unmapped columns, Unicode minus (`−`, `–`), non-breaking spaces (`\u00a0`), zero-width spaces (`\u200b`), fullwidth digits, and thousands commas (`"1,240.5"`).
- **Leading-zero collision audit**: Report every ID column containing `^0\d+$` tokens and explicitly flag pairs like `{"00123", "123"}` that implicit integer parsing would silently merge.
- **Duplicate audit**: Partition duplicate primary keys into `key_duplicates_consistent` vs `key_duplicates_conflicting`.
- **Cell fault taxonomy**: Count each missing/sentinel class, unparseable text fault, inline mixed unit (`"12.4 ug/mL"`), inline-vs-column unit scale conflict, unknown unit without conversion factor, and QC out-of-bounds value.

### Stage 3 — Workorder-Bound Reversible Cleaning (`reversible_table_cleaner.py`)
Apply deterministic, lossless transformations:
- **Hard policy abort**: Refuse execution if `impute_missing=true` or `infer_population_statistics=true` on a restricted table.
- **Leading-zero preservation**: Trim edge whitespace on keys if authorized, **never strip leading zeros** or cast IDs to numeric.
- **Conflict quarantine**: Route all rows sharing a conflicting primary key into `<stem>_quarantine_conflicts.csv` with `source_line` and `quarantine_reason`. Drop only consistent duplicates and record `kept_line` and `dropped_line` in `<stem>_manifest.json`.
- **Lossless raw retention**: For every cleaned numeric column `<col>` and unit column `<unit>`, retain `<col>_raw` and `<unit>_raw` verbatim alongside normalized output, plus `value_state`, `audit_flags`, and `source_line`.
- **True zero vs missingness**: Preserve numeric `0` as `"0"` with flag `true_zero_preserved`. Map all sentinel/missing/unparseable/unit-conflict cells to empty `""` with exact `value_state` (`below_lod`, `not_detected`, `instrument_sentinel`, `entry_missing`, `invalid_unparseable_fault`, `unit_conflict_unresolved`, `unknown_unit_unconverted`).
- **Per-metric unit normalization**: Normalize storage artifacts (Unicode minus, NBSP, thousands commas, Greek `μ`/`µ` -> `u`), resolve unit via inline unit (checked against unit column for scale conflict), multiply by exact workorder factor, and log `{line, primary_key, metric, raw_value, from_unit, to_unit, factor, converted_value}`.
- **Physical/domain QC flagging**: If a converted value violates `[min, max]`, retain the converted numeric value for transparency and mark `value_state = "qc_out_of_bounds"` with an entry in `qc_alerts` — **never silently delete or clip/winsorize without explicit workorder authorization**.
- **Linear reproduction script**: Emit `reproduce_<stem>.py` written in clean linear imperative research style (no hidden state, parameters adjacent to calls, runnable end-to-end).

### Stage 4 — Independent Disk Read-Back Reconciliation (`quality_reconciler.py`)
Never write quality report counts from memory or in-loop estimates alone:
1. Re-read `<stem>.csv` and `<stem>_quarantine_conflicts.csv` directly from disk as raw strings.
2. Verify SHA-256 against `<stem>_manifest.json`.
3. Assert exact **row conservation**: `rows_raw == rows_cleaned_disk + rows_dropped_consistent_duplicates + rows_quarantined_conflicts_disk`.
4. Assert primary-key uniqueness on disk while preserving leading zeros.
5. Assert `disk_value_state_counts == manifest.cell_accounting.value_state_counts` with zero mismatches.
6. Generate `<stem>_quality.md` and `<stem>_reconciliation.json` strictly from verified disk counts.

### Stage 5 — Domain-Readable Presentation & Honest Uncertainty
Separate scientific reporting from internal execution state:
- In `<stem>_quality.md` and `result.json`, state findings in clear scientific/data-stewardship terms (record counts, unit conversions, missingness categories, physical QC violations, quarantined conflicts requiring investigator confirmation).
- Never conflate `not_detected`, `below_lod`, `entry_missing`, `invalid_unparseable_fault`, and `true_zero` into a single ambiguous label.
- Explicitly list every unresolved anomaly (`qc_out_of_bounds`, `unit_conflict_unresolved`, `unknown_unit_unconverted`, `conflicting_primary_key_duplicate`) under `uncertainty` / `gaps` in `result.json`.

### Stage 6 — Contract Sealing & Final Audit
Patch `result.json` with verified metrics, provenance, artifacts, and honest gaps; run `gap_gate.py` and `final_audit.py`.

---

## 2. Anti-Failure Checklist (Must Pass Before Sealing)

| Check | Failure Mode Prevented | Verification Mechanism |
| --- | --- | --- |
| Leading-zero ID guard | `00123` and `123` silently merged as integer `123` | `leading_zero_protection` in `fault_scan.json` + string read-back |
| Five-way missing vs zero | `ND`/`<LOD`/`-999` imputed as `0` or mean; true `0` erased | `value_state` taxonomy + `true_zero_preserved_cells` count |
| Aggregate no-imputation gate | Inferring population SD or filling published table gaps | Hard `ValueError` guard + `imputation_performed: false` |
| Conflict duplicate quarantine | Silent `keep="first"` discarding contradicting measurements | `quarantine_conflicts.csv` + `duplicate_audit` |
| Per-metric unit & scale conflict | Applying one factor across heterogeneous assays or ignoring inline vs column mismatch | `per_metric_rules` + `unit_conflict_unresolved` |
| Storage artifact repair | Unicode `−`, NBSP, BOM, thousands commas turning valid numbers into `NaN` | `normalize_storage_text` + `storage_artifacts_repaired_cells` |
| 100% Report-to-CSV reconciliation | Prose quality report contradicting final table numbers | Independent disk read-back in `quality_reconciler.py` (`mismatches == []`) |
| Non-destructive source handling | Overwriting `/input/` files on error | Strictly read-only `/input/`, all writes under `/app/work/` and `/app/output/` |

---

## 3. Edge-Case & Failure Handling Matrix

| Condition | Required Action | Status in `result.json` |
| --- | --- | --- |
| Empty file or header-only CSV (0 rows) | Record `rows_raw=0`, emit empty schema-valid CSV + manifest explaining empty input | `partial` with explicit gap |
| All-null column | Retain column, report `missing_rate_pct=100.0`, do not drop silently unless workorder mandates | `ok` (with warning) |
| Garbled / non-UTF-8 encoding | Auto-fallback `utf-8-sig -> utf-8 -> gb18030 -> latin-1`, log `detected_encoding`; if undecodable, stop & report byte offset | `ok` if decoded cleanly, else `error` |
| Unknown unit missing from workorder | Do not invent conversion factor; set cleaned value `""`, `value_state="unknown_unit_unconverted"`, log in gaps | `partial` or `ok` with gap |
| Primary-key conflict (same key, different values) | Quarantine all conflicting rows to `*_quarantine_conflicts.csv`; never silently pick one | `ok` with manual-review gap |
| Missing workorder or dictionary | Run read-only `dirty_table_fault_scanner.py` to profile faults; stop before destructive cleaning and report exact missing rules | `partial` with explicit missing-input gap |
| Unreadable / permission-denied input | Preserve raw OS error message; do not fabricate cleaned rows | `error` |

---

## 4. Standard Execution Pipeline

```bash
SCRIPTS=/skills/weekly-skill/scripts

# 1. Preflight, budget, dynamic /input discovery (zero hardcoded filenames), and contract init
python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/case_contract_sealer.py" discover --input-dir /input --out /app/work/case_discovery.json
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Resolve discovered paths from /app/work/case_discovery.json (supports wide/long tables, any filename)
RAW_TABLE=$(python3 -c 'import json; d=json.load(open("/app/work/case_discovery.json")); print(d["primary_table_candidate"] or "")')
WORKORDER=$(python3 -c 'import json; d=json.load(open("/app/work/case_discovery.json")); print(d["primary_workorder_candidate"] or "")')
CASE_ID=$(python3 -c 'import json; d=json.load(open("/app/work/case_discovery.json")); print(d["case_id"])')
python3 "$SCRIPTS/result_guard.py" --init --case-id "$CASE_ID" --task-id "challenge-a0e888fa"

# 2. Read-only fault scan (encoding, leading-zero IDs, sentinels, units, duplicates)
python3 "$SCRIPTS/dirty_table_fault_scanner.py" \
  --input "$RAW_TABLE" \
  --workorder "$WORKORDER" \
  --out /app/work/fault_scan.json

# 3. Workorder-bound lossless reversible cleaning (also accepts --input-dir /input directly)
python3 "$SCRIPTS/reversible_table_cleaner.py" \
  --input "$RAW_TABLE" \
  --workorder "$WORKORDER" \
  --out-dir /app/output \
  --stem cleaned_table

# 4. Independent disk read-back reconciliation and single-source quality report
python3 "$SCRIPTS/quality_reconciler.py" \
  --csv /app/output/cleaned_table.csv \
  --manifest /app/output/cleaned_table_manifest.json \
  --quarantine /app/output/cleaned_table_quarantine_conflicts.csv \
  --out-md /app/output/cleaned_table_quality.md \
  --out-json /app/output/cleaned_table_reconciliation.json

# 5. Seal result.json preserving case_id and ALL case.json output_contract fields, then audit
python3 "$SCRIPTS/case_contract_sealer.py" seal \
  --case-json /input/case.json \
  --result-json /app/output/result.json \
  --task-id "challenge-a0e888fa" \
  --status ok \
  --payload-json /app/output/cleaned_table_reconciliation.json \
  --artifacts "cleaned_table.csv,cleaned_table_quarantine_conflicts.csv,cleaned_table_manifest.json,cleaned_table_reconciliation.json,cleaned_table_quality.md,reproduce_cleaned_table.py" \
  --sources "$(basename "$RAW_TABLE"),$(basename "$WORKORDER")"
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --validate
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" \
  --dir /app/output \
  --require cleaned_table.csv \
  --require cleaned_table_manifest.json \
  --require cleaned_table_reconciliation.json \
  --require cleaned_table_quality.md
```

## 5. Reference Modules & Scope Boundaries

- Read `references/cleaning_fault_taxonomy.md` for full fault codes, leading-zero collision handling, and five-way missingness vs true-zero rules.
- Read `references/workorder_and_unit_rules.md` for wide-table vs long-table per-metric workorder schemas and reversible unit conversion rules.
- Read `references/reconciliation_and_boundary_checklist.md` for disk read-back audit criteria and out-of-scope boundaries.
- **Explicit Out-of-Scope Boundaries**: This Skill does **not** perform inferential statistical modeling/hypothesis testing, publication chart styling, database permission administration, experimental protocol design, or manuscript drafting.
