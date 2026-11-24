# Literature Data Cleaning & Standardization Skill (`challenge-f4bca185`)

Offline Python 3.12 standard-library Skill for cleaning, standardizing, deduplicating, and auditing heterogeneous scientific literature extraction tables (CSV, JSON, JSONL, and read-only SQLite).

## Package Layout
- `SKILL.md` — Execution contract, R01–R06 rule stack, and workflow.
- `references/`
  - `cleaning_rules_R01_R06.md` — Numbered rule catalog and priority conflict resolution.
  - `unit_locale_and_delta_temp_dictionary.md` — Compound unit conversions, European vs English decimals, censored values, and ΔT rules.
  - `context_lock_dedup_and_replicates.md` — Context-locked dedup vs distinct-experiment preservation vs scientific conflict routing.
  - `schema_probing_and_review_queue.md` — Dynamic SQLite/CSV/JSON schema inspection and human review quarantine.
- `scripts/`
  - `data_cleaning_pipeline.py` — End-to-end R01–R06 engine and report generator.
  - `dedup_context_guard.py` — Multi-context deduplication and scientific conflict stratification guard.
  - `audit_chain.py` — SHA-256 hash-chained audit ledger.
  - `preflight_probe.py`, `budget_guard.py`, `result_guard.py`, `block_indexer.py`, `gap_gate.py`, `final_audit.py` — Harbor runtime guardrails.
- `examples/`
  - `case.json`, `sample_records.csv`, `sample_output_schema.json` — Minimal illustrative examples (zero `.tsv`).

## Quick Offline Run
```bash
python3 -B scripts/data_cleaning_pipeline.py --input examples --output /tmp/clean_out --work /tmp/clean_work
```

## Key Guardrails
- **`R05_PHYSICAL_BOUNDS`**: Intercepts `Kelvin < 0`, `pH < 0` or `pH > 14`, bounded percentage efficiencies `> 100%`, negative concentrations/sample sizes, and instrument sentinels (`-999`) into `needs_human_review`.
- **`R03_CROSS_DIMENSION_REFUSAL`**: Normalizes clinical/epidemiology/materials compound units (`mg/dL`, `g/dL`, `g/L`, `/10,000`, `/100,000`, `kcal/mol`, `kJ/mol`, `mV vs RHE`, `mA cm^-2`) and explicitly refuses `mass_conc <-> molar_conc` conversion when `molar_mass_g_mol` is missing.
- **`verification_checklist` & `quality_scorecard`**: Emits per-row `row_id -> before -> after -> rule_ids -> formula -> source_locator -> status` audit trails.
