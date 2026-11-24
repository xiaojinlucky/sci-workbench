---
name: literature-data-normalizer
description: Clean and standardize heterogeneous literature extraction tables (CSV, JSON, JSONL, and read-only SQLite via PRAGMA table_info) using numbered rules R01-R06 with locale-aware/censored value parsing, compound SI + delta-temperature conversion, strict context-locked deduplication, and human-review quarantine for missing values, unknown units, and scientific conflicts.
---

# Literature Data Cleaning & Standardization (`challenge-f4bca185`)

## Purpose & Trigger
Use this skill to clean, standardize, deduplicate, and audit extracted scientific literature datasets in Harbor offline sandboxes (`challenge-f4bca185`). It preserves original row identities (`row_id`) and raw source values verbatim, probes real input schemas without guessing columns, executes numbered deterministic rules (`R01–R06`), protects distinct experimental conditions from accidental merging, and quarantines missing values, unknown units, and scientific value conflicts into `needs_human_review`.

## Numbered Rule Stack (`R01–R06`, Strict Execution & Priority Order)
1. **`R01` Dynamic Schema Probing & Field Mapping**: Probe actual columns across `.csv`, `.json`, `.jsonl`, and read-only `.sqlite`/`.db` (`mode=ro` + `PRAGMA table_info`); never read with assumed column names. Emit `unmapped_fields` and protect leading-zero string identifiers (e.g., `"0012"`).
2. **`R02` Locale-Aware Numeric & Complex Value Parsing**: Distinguish European decimal comma (`1,25`, `1.250,5`) vs English thousands comma (`1,250`, `1,250.5`), parse scientific notation (`1.2e-3`, `1.2×10^-3`), mean `±` uncertainty, ranges (`a–b`), and **detection-limit censored values (`<0.05`, `<=0.05`, `>100`, `>=100`)** preserving `value_kind: "censored"` and `censor_operator` (never strip `<`/`>`!). Record `precision_sigfigs`.
3. **`R03` Compound Unit & SI Standardization + ΔT Guard**: Normalize mass, concentration, potential, current, current density, temperature, time, energy, area, length, pressure, volume, and rate units while preserving `raw_value`, `raw_unit`, and `conversion_formula`. Enforce **ΔT Guard**: temperature differences (`Δ°C`, `delta_C`, `ΔC`) and `±` spreads scale by `x * 1 K` and **NEVER** add `273.15`.
4. **`R04` Context-Locked Deduplication & Conflict Stratification**:
   - `DUPLICATE_MERGED`: identical entity/DOI + metric + experimental context + equivalent value -> merge and retain `merged_row_ids` and `alt_dois`.
   - `CONTEXT_DISTINCT_PRESERVED`: same DOI/entity but different condition (`temperature`, `ph`, `material`, `catalyst`, `dataset`, `experimental_condition`, `replicate_id`, etc.) -> **keep as separate rows**, never merge.
   - `VALUE_CONFLICT_SAME_CONTEXT`: identical context + same metric + conflicting value -> **never overwrite or drop**; preserve both values with conflict marker and route to `needs_human_review`.
5. **`R05` Missingness, Unknown Unit & Unparsed Quarantine**: Keep `NA`/`ND`/`null`/`-`/`未报告` as `null`/blank (`value_kind: "missing"`, **NEVER 0**); route unknown units and unparseable strings directly to `needs_human_review`.
6. **`R06` Traceable Export & Human Review Report**: Export `cleaned_records.csv`, `cleaning_audit_report.json`, `cleaning_review_report.md`, and Harbor `result.json` with row-level `changelog` tying every modification to `row_id` and `rule_id`.

## Execution Workflow
Locate scripts relative to this `SKILL.md` directory (`scripts/`).

### Step 1: Preflight & Budget Init
```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --init --state /app/work/.budget_state.json
```

### Step 2: Run Data Cleaning Pipeline
```bash
python3 -B scripts/data_cleaning_pipeline.py --input /input --output /app/output --work /app/work
```
Emits four deliverables under `/app/output/`:
- `result.json` — Harbor contract JSON with `case_id`, `status`, `sources`, `uncertainty`, `artifacts`, `schema_probe_report`, `canonical_records`, `duplicate_log`, `context_preserved_log`, `scientific_conflicts`, `changelog`, `needs_human_review`, `metrics`, and `gaps`.
- `cleaned_records.csv` — Analysis-ready standardized table preserving `row_id`, `raw_value`, `raw_unit`, `censor_operator`, `normalized_value`, `normalized_unit`, `conversion_formula`, `precision_sigfigs`, `merged_row_ids`, and `conflict_status`.
- `cleaning_audit_report.json` — Full machine-auditable rule trace, schema probe details, and quarantine queue.
- `cleaning_review_report.md` — Human-readable cleaning summary and prioritized review table.

### Step 3: Host Agent Audit & Grounding Gate
1. Inspect `cleaning_review_report.md` for `VALUE_CONFLICT_SAME_CONTEXT`, `unknown_unit`, `ambiguous_locale_number`, and `unmapped_fields`.
2. Confirm no literal unit conversion masked a genuine scientific disagreement.
3. Run Harbor grounding and final deliverable audit:
```bash
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require cleaned_records.csv --require cleaning_audit_report.json --require cleaning_review_report.md
```

## References
- `references/cleaning_rules_R01_R06.md` — Numbered rules, preconditions, failure modes, and priority conflict resolution.
- `references/unit_locale_and_delta_temp_dictionary.md` — Compound SI conversion table, locale comma disambiguation, censored operators, and ΔT rules.
- `references/context_lock_dedup_and_replicates.md` — Context-signature keys, duplicate vs replicate boundaries, and conflict preservation.
- `references/schema_probing_and_review_queue.md` — Read-only SQLite `PRAGMA table_info`, leading-zero protection, and human-review triage.
