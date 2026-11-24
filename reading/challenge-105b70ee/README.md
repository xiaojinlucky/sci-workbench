# Literature Conclusion & Data Extractor Skill (`challenge-105b70ee`)

Offline Python 3.12 standard-library skill for verifiable field-level data and conclusion extraction under Harbor and public Organizer Revision v2 contracts.

## Key Capabilities

- **1:1 Target-Field Contract**: Reads `/input/target-fields.json` and emits exactly one verified record per requested `field_id` (`scalar`, `units`, `statistic`, `experimental_condition`, `comparator`, `paragraph_id`, `quote`, `epistemic_class`, `status`).
- **2D Table Cell Anchoring**: Expands JSON (`columns`+`rows`), CSV, and Markdown tables into row/column cell anchors (`TableID:R{r}:C{c}`) with header-unit inheritance and missing-placeholder detection (`NA`/`ND`/`—` -> `null`, never `0`).
- **Condition-Value Binding & Anti-Distractor Filtering**: Filters out citation numbers, years, figure/table labels, and `p`-values from hijacking primary scalars; separates true same-condition prose-vs-table conflicts (`source_conflict`) from multi-condition measurements (`condition_stratified_candidates`).
- **Dual Verbatim + Numeric-Core Verification**: `--validate-fields` enforces exact quote match at `paragraph_id`, numeric-core verbatim presence inside the quote, zero-default-zero compliance, and strict Wenyon `output_contract` formatting.

## Quick Start

```bash
python scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python scripts/field_extraction_engine.py --input /input --output /app/output --work /app/work
# After semantic audit, validate and promote verified fields:
python scripts/field_extraction_engine.py --input /input --output /app/output --work /app/work --validate-fields /app/work/fields_draft.json
python scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python scripts/final_audit.py --dir /app/output --result-file result.json --require extracted_fields.json --require extracted_fields_report.md
```
