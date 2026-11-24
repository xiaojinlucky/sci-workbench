---
name: methods-experiments-repro-architect
description: Multi-source Methods and Experimental Setup drafting and reproducibility auditing Skill . Recursively extracts parameters from YAML/JSON/INI/TOML/Python/Shell/Logs, separates Planned Config vs Actual Executed values, detects config-key miscopies and ungrounded parameter fabrications, marks missing lab/run parameters explicitly as [UNRECORDED: AUTHOR_CONFIRM_REQUIRED], purifies backend script tokens from scientific prose, and outputs editable CSV/JSON/Markdown deliverables.
---

# Methods & Experimental Setup Drafting and Reproducibility Audit (`methods-experiments-writing`)

Use this Skill to transform scattered lab notes, configuration files, training/analysis scripts, and execution logs into an accurate, reproducible **Methods & Experiments** chapter paired with provenance-linked parameter tables (`experimental_parameters.csv`), a reproducibility checklist (`reproducibility_checklist.json`), and a machine-verified audit report (`methods_audit.json`).

## Six Core Required Elements (方法实验六要素)

1. **Executable Method Steps (方法步骤)**: Ordered step sequence specifying `Input -> Operation -> Output -> Decision Criterion`; never compress distinct steps into one vague sentence.
2. **Experimental Setup & Environment (实验设置)**: Separate training/validation/test or assay phases clearly; report hardware, OS, software/library or instrument versions, random seeds, and independent repeat/replicate counts.
3. **Parameters with Source Provenance (参数与来源)**: Extract every parameter with `(param_key, planned_value, actual_value, unit, source_file:line, role)` into `experimental_parameters.csv` (strictly `.csv`, never tab-delimited files) and keep prose numbers 100% synchronized with the table.
4. **Datasets, Samples & Splits (数据集与划分)**: Report dataset/cohort name, exact version, sample count, preprocessing rules, train/val/test split ratios, randomization/grouping protocol, and subset roles.
5. **Baselines & Controlled Comparisons (对照实验)**: Specify baseline methods/versions, verify matched compute/data budgets (or positive/negative/vehicle controls), and match statistical units to experimental design.
6. **Reproducibility Checklist & Concrete Limitations (可复现性与具象失效局限)**: Bind stable ELN-style source back-links (`ELN-SRC-001` -> raw file), actual run commands, and concrete operational failure modes rather than generic caveats.

## Non-Negotiable Grounding & Purification Rules

1. **Planned Config vs Actual Executed Separation**: Configuration files (`.yaml`, `.json`, `.ini`, `.toml`, `.py` defaults) record **planned** intent; execution logs (`.log`, `.txt`) and launch scripts (`.sh`) record **actual executed** state. Whenever they diverge (e.g., `planned_epochs=100` in `config.yaml` vs `early stopped at epoch 64` in `train.log`), disclose BOTH honestly—never write a planned setting as executed!
2. **Zero Parameter Fabrication (`[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`)**: If primary source files omit temperature, randomization method, sample grouping, random seed, or dataset version, NEVER invent plausible defaults for prose smoothness. Insert `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]` directly in the prose, CSV, and `reproducibility_checklist.json`.
3. **Config-Key Miscopy & Fabrication Interception**: Cross-verify every numerical parameter in Methods prose against extracted config/log keys to catch key swaps (e.g., writing `weight_decay` value as `learning_rate`) and ungrounded numbers.
4. **Frontend Scientific Semantics Purification**: Strip internal script/pipeline tokens (`QC_pass_v2`, `tmp_*`, `in-sample`, `_run3_final`, `debug_*`, `GO:0001234`, `KEGG_*`) from publication-facing Methods prose while preserving raw back-links in the provenance CSV/JSON.

## Runtime Execution Workflow

### Step 1: Preflight, budget & skeleton initialization
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "methods-experiments-writing"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```

### Step 2: Extract multi-source parameters & audit existing draft (if supplied)
```bash
python3 /skills/weekly-skill/scripts/methods_repro_extractor.py \
  --input-dir /input \
  --out-dir /app/output
```
(If auditing a newly drafted `/app/output/methods_and_experiments.md`, pass `--draft /app/output/methods_and_experiments.md --input-dir /input --out-dir /app/output`.)

### Step 3: Draft / Revise clean Methods & Experiments chapter
1. Read `references/methods_reproducibility_protocol.md`, `references/academic_writing_standards.md`, and `references/output_contract_spec.md`.
2. Inspect `/app/output/experimental_parameters.csv`, `/app/output/reproducibility_checklist.json`, and `/app/output/methods_audit.json`.
3. Write `/app/output/methods_and_experiments.md` covering all six elements, distinguishing Planned vs Actual values, preserving every verified step/value, marking unrecorded items `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`, and using clean frontend scientific terminology.
4. Re-run `methods_repro_extractor.py --input-dir /input --draft /app/output/methods_and_experiments.md --out-dir /app/output` until all fixable blocking defects (`CONFIG_KEY_MISCOPY`, `PLANNED_REPORTED_AS_ACTUAL`, `UNGROUNDED_FABRICATED_PARAMETER`, `FRONTEND_BACKEND_TOKEN_LEAK`) are resolved.

### Step 4: Grounding gate & final Harbor audit
```bash
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output \
  --require experimental_parameters.csv \
  --require reproducibility_checklist.json \
  --require methods_audit.json
```
