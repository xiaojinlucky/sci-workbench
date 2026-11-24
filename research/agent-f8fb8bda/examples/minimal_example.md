# Minimal Example Walkthrough (Illustrative Only)

> Note: This is a minimal illustrative interface example only; values do not represent an actual run or benchmark output.

## Example Input (`/input/case.json`)
```json
{
  "case_id": "example-deep-research-01",
  "research_question": "How do short-chain fatty acids modulate neuroinflammation across preclinical vs human studies?",
  "sub_questions": [
    "What mechanistic pathways link butyrate and propionate to microglial activation?",
    "Where do animal models and human cohort studies report opposing or null effects?"
  ],
  "blocked_sources": [
    {"source_id": "paywalled-clinical-2025", "reason": "HTTP 403 / SSO paywall"}
  ]
}
```

## Example Invocation
```bash
python3 scripts/deep_research_orchestrator.py --input /input --output /app/output --work /app/work
python3 scripts/deep_research_orchestrator.py --input /input --verify-report /app/output/deep_research_brief.md --out-json /app/work/citation_audit.json --strict
```

## Example Output Snippet (`/app/output/result.json`, Illustrative)
```json
{
  "case_id": "example-deep-research-01",
  "status": "partial_with_gaps",
  "corpus_scope": "bounded_local_corpus (10 raw -> 9 canonical sources)",
  "metrics": {
    "canonical_source_count": 9,
    "fulltext_source_count": 4,
    "abstract_only_source_count": 4,
    "discovery_only_excluded_count": 1,
    "conflict_count": 1
  },
  "uncertainty": {
    "bounded_scope": true,
    "global_exhaustiveness_claimed": false,
    "blocked_sources_count": 1
  }
}
```
