# Minimal Example Walkthrough (Illustrative Only)

> Note: Illustrative interface example only; values do not represent an actual run or benchmark output.

## Example Invocation
```bash
python3 scripts/review_synthesis_builder.py --input /input --output /app/output --work /app/work
python3 scripts/review_synthesis_builder.py --input /input --output /app/output --work /app/work --verify-draft /app/output/review_synthesis_draft.md --out-json /app/work/draft_audit.json --strict
```

## Example Output Snippet (`/app/output/result.json`, Illustrative)
```json
{
  "case_id": "example-lit-review-01",
  "status": "partial_with_gaps",
  "prisma_flow": {"balanced": true, "n_identified": 12, "n_dedup": 2, "n_screened": 10, "n_screen_excl": 1, "n_included": 9},
  "metrics": {"included_source_count": 9, "fulltext_included_count": 4, "abstract_only_included_count": 5, "theme_count": 3, "conflict_count": 2},
  "draft_verification": {"passed": true, "hedging_violations": [], "overclaims": []}
}
```
