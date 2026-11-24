# Minimal Usage Example (Illustrative Synthetic Sample — Not a Formal Test Case)

## Sample Input Directory (`/input/`)

- `config.yaml`: `learning_rate: 0.0003`, `weight_decay: 0.01`, `epochs: 100`, `batch_size: 32`, `seed: 42`.
- `train.log`: `Early stopping triggered at epoch 64`, `lr=0.0003`, `batch_size=32`, `seed=42`.
- `methods_draft.md`: Claims `learning rate of 0.01` (miscopied from `weight_decay`), `trained for 100 epochs` (planned value instead of actual 64), omits temperature/randomization, and leaks `QC_pass_v2` and `in-sample` into prose.

## Command

```bash
python3 scripts/methods_repro_extractor.py \
  --input-dir /input \
  --draft /input/methods_draft.md \
  --case-id synthetic-demo-05 \
  --out-dir /app/output
```

## Illustrative Output Highlights (`methods_audit.json` & `experimental_parameters.csv`)

- `PLANNED_VS_ACTUAL_CONFLICT`: `epochs` planned=`100` (`config.yaml`) vs actual=`64` (`train.log`).
- `PLANNED_REPORTED_AS_ACTUAL`: draft reports `100` epochs without disclosing actual early stop at `64`.
- `CONFIG_KEY_MISCOPY`: draft assigns `0.01` to `learning_rate` (true value `0.0003`; `0.01` belongs to `weight_decay`).
- `UNRECORDED_TEMPERATURE_OR_AMBIENT_CONDITION` & `UNRECORDED_RANDOMIZATION_AND_GROUPING`: tagged `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`.
- `FRONTEND_BACKEND_TOKEN_LEAK`: flags `QC_pass_v2` and `in-sample` for clean scientific replacement.
