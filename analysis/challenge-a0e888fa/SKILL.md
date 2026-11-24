---
name: scientific-data-cleaning-auditor
description: >-
  Use when auditing storage/encoding faults and executing reversible,
  workorder-bound cleaning on scientific tables or transcribed published
  aggregate tables against explicit data dictionaries and per-metric unit/QC
  rules. Preserves leading-zero string IDs, separates five missing/sentinel
  classes (-999, ND, <LOD, blank/NA, invalid text) from true numeric zeros
  without silent imputation or population inference, quarantines conflicting
  duplicate keys, emits downstream statistics_assumptions and chart_choice_record
  hand-offs, and enforces 100% disk read-back reconciliation under /app/output.
---

# Scientific Data Cleaning & Quality Audit Skill

Skill resources live under `/skills/weekly-skill`. Case inputs live under `/input/` (`case.json`, `task.md`, raw tables, data dictionary, and workorder). Deliverables go under `/app/output/`, including `result.json` with `case_id` and all `output_contract` fields.

**Hard Runtime & Integrity Contract**:
- **Pure Python 3.12 stdlib**: Runs offline with zero third-party dependencies.
- **No presumed gold file**: Never assume `quality-gold.md` exists; ground all rules in delivered `/input` files.
- **Published aggregate red line**: Never impute missing/invalid source cells (`imputation_performed=false`) and never infer unrecorded population statistics from aggregate tables.
- **Downstream hand-off without scope creep**: Record `statistics_assumptions` and `chart_choice_record` per cleaned metric for downstream users, without performing inferential hypothesis tests or publication chart rendering.

---

## 1. Zero-Manual One-Command Entrypoint (`auto_cleaning_orchestrator.py`)

Prefer the unified orchestrator for any `/input` directory (zero hardcoded filenames; handles wide/long tables, GBK/UTF-8 BOM, empty tables, all-null columns, and seals `case.json`'s `output_contract` into `result.json`):

```bash
SCRIPTS=/skills/weekly-skill/scripts

python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# End-to-end: discover /input -> fault scan -> reversible clean -> disk reconciliation
# -> downstream statistics_assumptions & chart_choice_record -> seal result.json
python3 "$SCRIPTS/auto_cleaning_orchestrator.py" \
  --input-dir /input \
  --out-dir /app/output \
  --stem cleaned_table

python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --validate
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --require cleaned_table.csv --require cleaned_table_manifest.json --require cleaned_table_reconciliation.json --require cleaned_table_quality.md
```

Individual modular scripts (`dirty_table_fault_scanner.py`, `reversible_table_cleaner.py`, `quality_reconciler.py`, `case_contract_sealer.py`) remain callable step-by-step for custom multi-table workflows (see `references/`).

---

## 2. Six-Stage Scientific Cleaning Decision Tree

1. **Intake & Contract Binding**: Read `/input/case.json`, `task.md`, data dictionary, and workorder. Lock table granularity (aggregate vs individual), composite primary key, string-preserved ID columns, per-metric unit/QC rules, five-way sentinel taxonomy, and conflict duplicate policy.
2. **Read-Only Byte & Fault Profiling (`dirty_table_fault_scanner.py`)**:
   - Detect UTF-8 BOM, UTF-8, GB18030/GBK, Latin-1, delimiter (`,`/`;`/`\t`/`|`), CRLF/LF, Unicode minus (`−`/`–`), NBSP (`\u00a0`), fullwidth digits, thousands commas (`"1,240.5"`), and Greek micro variants (`µ`/`μ`).
   - **Leading-Zero Collision Guard**: Read every cell as string (`csv.DictReader`); explicitly flag `^0\d+$` IDs and pairs like `{"00123", "123"}` that implicit integer coercion would merge.
   - Partition duplicates into `key_duplicates_consistent` vs `key_duplicates_conflicting`.
3. **Workorder-Bound Reversible Cleaning (`reversible_table_cleaner.py`)**:
   - Abort immediately if `impute_missing=true` or `infer_population_statistics=true` on restricted tables.
   - Preserve leading zeros verbatim; drop only consistent duplicates (logging `kept_line`/`dropped_line`); route **all** rows sharing a conflicting primary key to `*_quarantine_conflicts.csv` (zero silent overwrite).
   - Keep `<col>_raw` and `<unit>_raw` verbatim alongside cleaned values, `value_state`, `audit_flags`, and `source_line`.
   - Preserve true numeric `0` (`true_zero_preserved`); map `below_lod`, `not_detected`, `instrument_sentinel`, `entry_missing`, `invalid_unparseable_fault`, `unit_conflict_unresolved`, and `unknown_unit_unconverted` to empty `""` with explicit `value_state`.
   - Apply per-metric unit conversion factors; flag `qc_out_of_bounds` while retaining numeric values for expert adjudication.
   - Emit linear imperative reproduction script `reproduce_<stem>.py`.
4. **Independent Disk Read-Back Reconciliation (`quality_reconciler.py`)**:
   - Re-read cleaned & quarantine CSVs from disk as raw strings; assert SHA-256 match, row conservation (`rows_raw == rows_cleaned + dropped_consistent + quarantined_conflicts`), string primary-key uniqueness, and `disk_value_state_counts == manifest.value_state_counts` (`mismatches == []`).
5. **Downstream Dependency Records (`statistics_assumptions` & `chart_choice_record`)**:
   - Compute per-metric valid `n`, null/sentinel counts, signed/zero flags, skewness estimate, downstream normality/variance check guidance, recommended chart grammar, and blocked anti-patterns (no pie, no mean-only bars for small `n`, no truncated baselines) strictly from cleaned disk values.
6. **Contract Sealing & Domain Presentation**:
   - Seal `case_id` and every `output_contract` field into `/app/output/result.json`; separate clear scientific data-stewardship prose from technical hashes/flags; never conflate missingness categories with true zero.

---

## 3. Anti-Failure & Edge-Case Matrix

| Condition / Failure Mode | Deterministic Handling | Status |
| --- | --- | --- |
| Leading-zero ID collision (`00123` vs `123`) | Read as strings; preserve both keys; flag collision in `fault_scan.json` | `ok` |
| Conflicting primary-key duplicate | Quarantine all conflicting rows to `*_quarantine_conflicts.csv` | `ok` + review uncertainty |
| Inline vs column unit scale mismatch | Do not guess; blank cleaned cell, set `unit_conflict_unresolved` | `ok` + gap |
| Empty header-only CSV (0 data rows) | Emit schema-valid 0-row CSV, manifest (`conservation_check_passed=true`), explicit gap | `partial` |
| All-null column | Retain column, report `missing_rate_pct=100.0`, `chart_choice=none_all_missing` | `ok` + warning |
| Non-UTF-8 (GBK/GB18030/Latin-1) | Auto-decode fallback, log `detected_encoding` | `ok` |
| Missing workorder | Run read-only fault scan; report missing rules without destructive guessing | `partial` |

## 4. Bundled References & Boundaries

- `references/cleaning_fault_taxonomy.md`: Seven fault classes, leading-zero collision mechanics, five-way missingness vs true zero.
- `references/workorder_and_unit_rules.md`: Long/wide per-metric workorder contract and reversible unit conversion rules.
- `references/reconciliation_and_boundary_checklist.md`: Disk read-back reconciliation criteria and explicit out-of-scope boundaries (no hypothesis testing, publication plotting, DB RBAC, experimental design, or manuscript drafting).
