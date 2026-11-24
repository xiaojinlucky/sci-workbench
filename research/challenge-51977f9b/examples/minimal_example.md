# Minimal Example Walkthrough (Illustrative Only)

> Note: Illustrative interface example only; values do not represent an actual run or benchmark output.

## Example Protocol in `/input/case.json`
```json
{
  "case_id": "example-slr-01",
  "research_question": "Does butyrate supplementation reduce neuroinflammation markers in mammalian models?",
  "protocol": {
    "version": "v2_frozen",
    "year_min": 2020,
    "year_max": 2026,
    "required_terms": ["butyrate", "short-chain fatty acid"],
    "exclude_terms": ["in silico only"],
    "fulltext_exclude_terms": ["no control group"],
    "fulltext_required": true
  }
}
```

## Example Invocation
```bash
python3 scripts/systematic_review_runner.py --input /input --output /app/output --work /app/work
```
