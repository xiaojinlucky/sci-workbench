# Research-Question Driven Search (`challenge-e7598a44`)

Offline, evidence-bound Skill that converts a compound scientific question into a 7-slot Element Card, a dependency-ordered sub-question DAG, two-round controlled retrieval with acronym expansion, 5-state sub-question sufficiency scoring, conflicting-fixture arbitration, and an explicit stopping verdict.

## Requirements & Installation

- **Runtime**: Python 3.12+ standard library only (`--network none` compliant; zero external pip packages required).
- **Optional PDF extractors**: Uses `pdfplumber`, `pypdf`, or `pdftotext` when available; gracefully degrades to metadata-only mode with explicit disclosure otherwise.
- **No prohibited dependencies**: Does not import or require PyMuPDF/fitz, CairoSVG, or OS-specific COM libraries.

## Quick Usage

```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/question_search_planner.py --input /input --output /app/output --work /app/work
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json --require question_search_plan.json
```

## Package Layout

- `SKILL.md`: Complete Agent workflow, verification checklist, and exception rules.
- `scripts/question_search_planner.py`: Main question-driven decomposition, two-round search, sufficiency grading, and stopping engine.
- `scripts/search_core/`: Pure stdlib scholarly metadata parser, traceable deduplicator, query planner, 4-tier screener, and hash-chained contract builder.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Harbor M1-M6 preflight, grounding, and final audit utilities.
- `references/`: Decomposition rubric and output contract specification.
- `examples/`: Minimal illustrative input/output schema examples (explicitly marked as examples).

## Scope & Limitations

- Operates strictly on the finite local snapshot provided under `/input/`.
- Never claims live remote database recall, global completeness, or fabricated human approval.
- Treats synthetic duplicate exports or conflicting reviewer opinion files as input fixtures to screen and arbitrate, never as pre-baked answers.
