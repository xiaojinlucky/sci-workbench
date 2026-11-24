# Methods & Experimental Setup Drafting and Reproducibility Skill (`methods-experiments-writing`)

Offline, zero-dependency Python 3.12+ Skill for extracting experimental parameters from heterogeneous lab/code files, enforcing Planned-vs-Actual separation, and auditing Methods & Experiments drafts.

## Core Capabilities (`scripts/methods_repro_extractor.py`)

1. **Multi-Source Recursive Extraction**: Parses `.yaml`/`.yml` (pure stdlib indentation parser), `.json`, `.toml` (`tomllib`), `.ini`/`.cfg`, `.py` (AST `argparse` defaults + uppercase constants), `.sh` (CLI flags + exports), and `.log`/`.txt` execution records.
2. **Planned Config vs Actual Executed Separation**: Detects conflicts between planned config files and actual execution logs (`PLANNED_VS_ACTUAL_CONFLICT`), and catches drafts reporting planned settings as executed (`PLANNED_REPORTED_AS_ACTUAL`).
3. **Config-Key Miscopy & Fabrication Interception**: Catches swapped parameter values (`CONFIG_KEY_MISCOPY`, e.g., copying `weight_decay` into `learning_rate`), value mismatches, and ungrounded fabricated parameters (`UNGROUNDED_FABRICATED_PARAMETER`).
4. **Explicit Missing-Item Marking**: Flags absent temperature, randomization/grouping, random seeds, replicate counts, environment specs, dataset splits, matched-budget controls, and failure limitations as `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`.
5. **Frontend Scientific Terminology Purification**: Flags leaked script variables (`QC_pass_v2`, `tmp_*`, `in-sample`, `_run3_final`, `debug_*`) in Methods prose.

## Quick Usage

```bash
python3 scripts/methods_repro_extractor.py \
  --input-dir /input \
  --draft /input/methods_draft.md \
  --case-id case-01 \
  --out-dir /app/output
```

## Runtime Requirements & Boundaries

- **Runtime**: Python 3.12 standard library only (zero third-party dependencies; fully offline).
- **Outputs**: `experimental_parameters.csv`, `reproducibility_checklist.json`, `methods_audit.json`, `result.json` (strictly CSV/JSON/Markdown; no tab-delimited outputs).
