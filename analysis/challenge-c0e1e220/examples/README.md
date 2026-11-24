# Minimal Usage Examples (`challenge-c0e1e220`)

These files demonstrate the CLI interface and expected structure on a tiny synthetic example table (`minimal_sample.csv`, 16 raw rows including 1 exact duplicate `E11`, 1 missing measurement `E10`, and 1 encoded integer column `group_code`). They are **illustrative examples only** and do not represent formal evaluation cases or precomputed answers.

## Run Locally (Zero External Dependencies Required)

```bash
# 1. Pre-inference hygiene and assumption diagnostics
python3 ../scripts/assumption_diagnostics.py \
  --input minimal_sample.csv \
  --design independent \
  --group-col group \
  --value-col response_mg_l

# 2. Verify encoded integer column blocking (expected exit code 2 with ENCODED_INTEGER_COLUMN)
python3 ../scripts/assumption_diagnostics.py \
  --input minimal_sample.csv \
  --design independent \
  --group-col group \
  --value-col group_code

# 3. Full inference, effect size, 95% CI, Holm/BH correction, reproducible script & vector SVG
python3 ../scripts/inference_effect_ci_runner.py \
  --input minimal_sample.csv \
  --design independent \
  --group-col group \
  --value-col response_mg_l \
  --unit "mg/L" \
  --out-dir /tmp/c0e1e220_example_out \
  --stem example_report
```
