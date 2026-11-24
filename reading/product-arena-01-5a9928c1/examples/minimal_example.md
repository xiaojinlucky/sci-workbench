# Minimal Invocation Example (Synthetic Illustration Only)

> **Notice**: All files under `examples/` are synthetic minimal demonstrations showing the CLI workflow and output contract. They are not formal benchmark cases or pre-computed official answers.

## Step-by-Step Invocation

```bash
# 1. Stage 1: Segment input, lock source bibliography identity, extract glossary candidates
python3 scripts/bilingual_alignment_guard.py --prepare \
  --input /input --work /app/work --output /app/output

# 2. Stage 2: Audit drafted bilingual JSON against locks, glossary, and invariants,
#    and atomically emit /app/output/result.json + /app/output/translation_bilingual.md
python3 scripts/bilingual_alignment_guard.py --audit \
  --draft /app/work/translation_draft.json \
  --source-segments /app/work/source_segments.json \
  --metadata-lock /app/work/source_metadata_lock.json \
  --glossary /app/work/glossary.json \
  --out-json /app/work/alignment_audit.json \
  --emit-result /app/output/result.json \
  --emit-markdown /app/output/translation_bilingual.md

# 3. Stage 3: Glossary-aware anti-translationese and de-AI lint
python3 scripts/academic_deai_lint.py \
  --input /app/work/translation_draft.json \
  --glossary /app/work/glossary.json \
  --out-json /app/work/deai_lint.json
```

See `examples/sample_case.json` for a minimal input payload and `examples/sample_bilingual_output.json` for a compliant drafted output preserving `source_title`, `15,000 == 1.5万`, `5 mg`, `25 °C`, `Fig. 1A`, `[1]`, negation polarity, and modal hedges.
