---
name: literature-data-normalizer
description: Clean and standardize heterogeneous literature extraction tables (CSV, JSON, JSONL, and read-only SQLite via PRAGMA table_info) using numbered rules R01-R06 with locale-aware/censored value parsing, compound SI + delta-temperature conversion, strict context-locked deduplication, and human-review quarantine for missing values, unknown units, and scientific conflicts.
---

# Literature Data Cleaning & Standardization (`challenge-f4bca185`)

## Purpose & Trigger
Use this skill to clean, standardize, deduplicate, and audit extracted scientific literature datasets in Harbor offline sandboxes (`challenge-f4bca185`). It preserves original row identities (`row_id`) and raw source values verbatim, probes real input schemas without guessing columns, executes numbered deterministic rules (`R01–R06`), protects distinct experimental conditions from accidental merging, and quarantines missing values, unknown units, and scientific value conflicts into `needs_human_review`.

## Numbered Rule Stack (`R01–R06`, Strict Execution & Priority Order)
1. **`R01` Dynamic Schema Probing, Field Mapping & Zotero/CSL Guard**: Probe actual columns across `.csv`, `.json`, `.jsonl`, and read-only `.sqlite`/`.db` (`mode=ro` + `PRAGMA table_info`); never read with assumed column names. Emit `unmapped_fields`, protect leading-zero string identifiers (e.g., `"0012"`), backfill empty `entity` from real row context (`R01_ENTITY_CONTEXT_BACKFILL`), and validate optional Zotero/CSL-JSON `author`/`creators` (requiring `family`/`lastName`/`literal`) and `issued`/`date` fields—quarantining malformed schema records into `needs_human_review` (`ZOTERO_CSL_SCHEMA_INVALID_AUTHOR` / `ZOTERO_CSL_SCHEMA_INVALID_DATE`; see negative example `examples/zotero_csl_malformed_example.json`).
2. **`R02` Locale-Aware Numeric, Complex Value & Auditable Notes LOD Parsing**: Distinguish European decimal comma (`1,25`, `1.250,5`) vs English thousands comma (`1,250`, `1,250.5`), parse scientific notation (`1.2e-3`, `1.2×10^-3`), mean `±` uncertainty, ranges (`a–b`), and **detection-limit censored values (`<0.05`, `<=0.05`, `>100`, `>=100`, `<LOD`)** preserving `value_kind: "censored"` and `censor_operator` (never strip `<`/`>`!). When `<LOD`/`<LOQ` has a numeric detection limit in `notes` (e.g., `"below limit of detection 0.05 mg/L"`), extract structured `lod_value`/`lod_unit` auditably into `changelog` (`R02_NOTES_LOD_EXTRACTED`). Record `precision_sigfigs`.
3. **`R03` Compound Unit & SI Standardization + ΔT Guard + Cross-Dimension Refusal**: Normalize mass, clinical/mass/molar concentration (`mg/dL`, `g/dL`, `g/L`, `mg/L`, `pg/mL`, `mmol/L`), potential (`mV vs RHE` -> `V vs RHE`), current density (`mA cm^-2`), epidemiology rates (`/100,000` -> `/10,000`), molar energy (`kcal/mol` -> `kJ/mol`), temperature, time, area, pressure, volume, and rate units while preserving `raw_value`, `raw_unit`, and `conversion_formula`. Enforce **ΔT Guard** (`Δ°C` / `±` spreads scale by `x * 1 K`, NEVER `+273.15`) and **Cross-Dimension Refusal Guard** (`R03_CROSS_DIMENSION_REFUSAL`: explicitly refuse mass-to-molar conversion such as `mg/L -> mmol/L` when `molar_mass_g_mol` is missing, routing to `needs_human_review` without fabricating molecular weight).
4. **`R04` Order-Independent Context-Locked Deduplication & Conflict Stratification**:
   - **Explicit 5-tuple grouping key**: `(source_doi, experiment_context, sample_id, metric, extraction_id)`. **Cross-DOI auto-merging is strictly forbidden** (different DOIs emit `CROSS_DOI_CANDIDATE_NOT_MERGED` only); rows missing critical identity (`metric` or `sample_id`/`entity`) are never auto-merged (`DEDUP_CANDIDATE_MISSING_IDENTITY`).
   - **Decimal + relative tolerance (`rel_tol=1e-6`, NO fixed `abs_tol=1e-9`)** + **full-group transitive clustering (NO first-match `break`)**: prevents low-concentration differences (e.g., `1e-12` vs `9e-10`) from being swallowed and eliminates input-order dependence (e.g., `[1, 2, 2]` vs `[2, 1, 2]` produce identical merge/conflict counts).
   - `DUPLICATE_MERGED`: equivalent values in the same group merge into canonical row preserving `merged_row_ids`; `verification_checklist` marks merged children strictly by `raw_row_index` so `duplicate_merged_count == status_breakdown["DUPLICATE_MERGED"]`.
   - `CONTEXT_DISTINCT_PRESERVED`: same `source_doi`/sample/metric under different experimental context -> **keep as separate rows**, never merge.
   - `VALUE_CONFLICT_SAME_CONTEXT`: identical group with conflicting value clusters -> **never overwrite or drop**; preserve all conflicting clusters with non-empty `entity` and route to `needs_human_review`.
5. **`R05` Missingness, Unknown Unit & Physical Bounds Quarantine (`R05_PHYSICAL_BOUNDS`)**: Keep `NA`/`ND`/`null`/`not_recorded`/`-`/`未报告` as `null`/blank (`value_kind: "missing"`, **NEVER 0**); intercept physically impossible values (`Kelvin < 0`, `pH < 0` or `pH > 14`, bounded efficiency/selectivity/purity/accuracy `% > 100%` or `< 0%`, negative concentration or negative `sample_size`, instrument sentinels like `-999`) under `PHYSICAL_BOUNDS_VIOLATION` into `needs_human_review` without silent coercion; route unknown units and unparseable strings directly to `needs_human_review`.
6. **`R06` Traceable Export, Per-Row Verification Checklist & Human Review Report**: Export `cleaned_records.csv`, `cleaning_audit_report.json`, `cleaning_review_report.md`, and contract `result.json` with row-level `changelog`, `verification_checklist` (`row_id -> before -> after -> rule_ids -> formula -> source_locator -> status`), and `quality_scorecard`.

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
