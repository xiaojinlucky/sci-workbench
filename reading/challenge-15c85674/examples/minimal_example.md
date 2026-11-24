# Minimal Invocation Example (Synthetic Illustration Only)

> **Notice**: Files in `examples/` are synthetic minimal demonstrations illustrating the CLI interface and exact answer schema. They are not official evaluation inputs or pre-computed answers.

## Step-by-Step Invocation

```bash
# 1. Index /input/case.json (and PDF if present), run 4-stage coverage audit,
#    decompose sub-questions, retrieve BM25 + tail-page + table/footnote evidence,
#    and pre-detect cross-section conflicts:
python3 scripts/literature_qa_engine.py \
  --input /input --output /app/output --work /app/work

# 2. After drafting grounded answers into /app/work/answers_draft.json, run the
#    7-gate zero-fabrication validator and atomically promote /app/output/result.json
#    and /app/answers.json:
python3 scripts/literature_qa_engine.py \
  --input /input --output /app/output --work /app/work \
  --validate-answers /app/work/answers_draft.json

# 3. Run Harbor grounding check and final deliverable audit:
python3 scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py --dir /app/output --result-file result.json --require answers.json
```

See `examples/sample_input_case.json` and `examples/sample_output_schema_example.json`.
