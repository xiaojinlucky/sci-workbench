# Methods & Reproducibility Protocol (`methods-experiments-writing`)

## 1. Planned Configuration vs Actual Execution Separation

Scientific reproducibility frequently breaks when authors copy planned hyperparameters from a YAML/JSON config file while the actual run terminated early or received CLI overrides in a shell script or log.
- **`planned_config`**: Values extracted from `.yaml`/`.yml`, `.json`, `.toml`, `.ini`/`.cfg`, or `.py` defaults.
- **`actual_executed_log`**: Values extracted from `.log`/`.txt` execution records, `.sh` invocation flags, or keys prefixed with `actual_`/`executed_`/`observed_`.
- **Reporting Rule**: When Planned != Actual (e.g., `planned_epochs=100` vs `early stopped at epoch 64`), both values must be explicitly stated in the Methods chapter and in `experimental_parameters.csv`.

## 2. Stable ELN-Style Provenance Back-Linking

Borrowing the core principle of reproducible electronic lab notebooks without bundling server software:
- Every raw input file under `/input/` receives a deterministic reference ID (`ELN-SRC-001`, `ELN-SRC-002`, ...).
- Every extracted parameter row in `experimental_parameters.csv` links directly back to `source_file:line` (`EXP-PARAM-001`, ...).
- Original raw files remain unmodified; deviations between protocol and execution are documented transparently.

## 3. Explicit Missing-Parameter Policy (`[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`)

Never invent missing details to make Methods read smoothly. Explicitly mark missing items with `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]` across:
1. Incubation/reaction temperature or ambient conditions;
2. Randomization, blinding, and sample group allocation;
3. Computational random seeds;
4. Independent replicate/repeat counts;
5. Hardware specs and software/instrument versions;
6. Dataset version, cohort size, and train/val/test splits;
7. Baseline matched-budget or control-group conditions;
8. Concrete operational failure modes and applicability limits.

## 4. Frontend Scientific Terminology Purification

Keep implementation details in scripts/logs and use clean scientific terminology in the Methods manuscript:
- Replace `QC_pass_v2` / `qc_pass_flag` -> "samples meeting predefined quality-control criteria".
- Replace `tmp_merged` / `temp_split` / `debug_out` -> descriptive scientific cohort/data stage.
- Replace `in-sample` / `in_sample_v1` -> "training partition" or "internal development cohort".
- Replace `model_run3_final` / `data_v2_final` -> canonical method name or versioned dataset identifier.
- Replace raw database prefixes (`GO:0006954`, `KEGG_...`, `HALLMARK_...`) in prose with clean biological pathway names.
