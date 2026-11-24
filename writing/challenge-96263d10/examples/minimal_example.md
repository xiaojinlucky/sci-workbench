# Minimal Synthetic Invocation Example (Illustration Only)

> Note: Synthetic interface example only; not a formal test case.

## Command
```bash
python3 scripts/template_spec_and_adapter.py adapt \
  --source /input/manuscript.docx \
  --target-template /input/journal_target_spec.md \
  --alternate-template /input/journal_legacy_spec.md \
  --case-id EXAMPLE-FORMAT-01 \
  --out-dir /app/output \
  --fallback-font "Liberation Serif" \
  --render-preview
```

## Illustrative Output (`template_comparison.json` excerpt)
```json
{
  "conflicts": [
    {
      "field": "results_discussion_structure",
      "target_value": "separate",
      "alternate_value": "combined",
      "resolution": "APPLY_TARGET_ONLY"
    }
  ],
  "alternate_only_blocked_from_mixing": [
    {
      "field": "header_footer",
      "target_value": "UNSPECIFIED",
      "alternate_value": "Running head 50 chars",
      "resolution": "KEEP_UNSPECIFIED_DO_NOT_MIX"
    }
  ]
}
```
