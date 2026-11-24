# Minimal Usage Examples (`challenge-9562cc8d`)

These files illustrate the `viz_spec.json` contract on a tiny cross-zero synthetic dataset (`minimal_data.csv`, containing negative values, 1 exact duplicate `V09`, and 1 missing entry `V08`). They are **illustrative examples only** and do not represent formal evaluation cases or prepared answers.

## Run Locally (Zero External Dependencies Required)

```bash
# 1. Run 5-Gate selection, anti-pattern, and WCAG/CVD palette audit
python3 ../scripts/chart_selector_and_auditor.py \
  --spec viz_spec_example.json

# 2. Build Cell-sized editable Vector SVG (+ 300 dpi PNG/PDF when Matplotlib is available)
python3 ../scripts/publication_viz_builder.py \
  --spec viz_spec_example.json \
  --out-dir /tmp/9562cc8d_example_out \
  --stem example_fig
```
