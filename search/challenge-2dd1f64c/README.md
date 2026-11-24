# Literature Screening & Intelligent Ranking (`challenge-2dd1f64c`)

Offline, evidence-bound Skill for auditable all-record screening and deterministic priority ranking over finite candidate sets.

## Requirements & Installation

- **Runtime**: Python 3.12+ standard library only (`--network none` compliant; zero external pip packages required).
- **No prohibited dependencies**: Zero dependency on PyMuPDF/fitz, CairoSVG, or OS-specific COM bindings.

## Quick Usage

```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/smart_screening_ranker.py --input /input --output /app/output --work /app/work
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json --require screening_audit.json --require human_review_queue.json
```

## Key Capabilities

- **4-Tier Relevance + 3-Way Screening**: `direct_relevant`, `indirect_relevant`, `insufficient_abstract_needs_review`, `irrelevant` paired with `include / borderline / exclude`.
- **Anti-Title-Stuffing & Missing-Metadata Guards**: Flags title-only keyword traps and protects records lacking abstracts or years from silent exclusion.
- **Traceable Deduplication & Arbitration**: Logs merged copies as `duplicate_ignored_with_trace` with rollback entries; keeps distinct-DOI same-title pairs separate; routes external reviewer conflicts to `human_review_queue.json`.
- **Reproducible Ranking**: 7-level deterministic tie-breaker chain, PRISMA count conservation, Cohen's kappa, and leave-one-term-out top-k rank stability.

## Scope & Limitations

- Operates strictly on finite local snapshots under `/input/`.
- Synthetic duplicate exports and conflicting opinion fixtures are processed as screening inputs, never as pre-baked answers.
