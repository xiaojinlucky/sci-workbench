# Literature Study Quality & Risk-of-Bias Appraisal Skill

Offline, pure-stdlib methodological appraisal toolkit that routes scientific papers to version-pinned quality scales, extracts sentence- and page-level verbatim evidence anchors, abstains strictly on abstract-only or scanned inputs, and ranks appraised candidates with transparent triage rationales.

## Comparison vs Generic Summaries & Static Checklists

| Capability | Generic Abstract Summary | Static Reporting Checklist | This Scale-Routed Appraisal Skill |
| --- | --- | --- | --- |
| Scale Selection & Version Pinning | None | Often mixes legacy/updated domains (e.g., ROBINS-I 2016 vs V2) | Routes per study design (`RoB 2.0`, `ROBINS-I V2 (2024)`, `AMSTAR-2`, `Scientific Quality v1.0`) and pins version explicitly |
| Evidence Grounding | Paraphrases abstract without anchors | Manual checkmarks only | Binds every non-UNKNOWN rating to verbatim sentence quote + `paragraph_id`/`section`/`page_number` |
| Missing / Abstract-Only Input | Guesses "adequate sample / low risk" | Leaves blank or forces pass/fail | Enforces `insufficient_evidence` + `UNKNOWN`; never defaults unknown to low risk |
| Summary Grading & Triage | Orphan label ("reliable") | Unweighted count | Emits explicit `tier_derivation_rule`, `ranked_evidence_candidates`, and synchronized traffic-light Markdown matrix |

## Quick Start (Offline Linux / Python 3.12+)

```bash
python -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python -B scripts/study_quality_evaluator.py --input /input --output /app/output --work /app/work
python -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require quality_appraisal_table.json quality_appraisal_report.md
```

## Adapting to a New Domain or Dual-Reviewer Workflow

1. **Supply Papers**: Place structured records in `case.json:records` (with `paragraphs[]` or `text`/`abstract`) and/or real scientific PDFs/HTML/Markdown files in `/input`.
2. **Pin Scale Version**: Set `appraisal_scale: "auto"` for per-study design routing, or explicitly pin `"ROBINS-I"` (`scale_version: "V2"`), `"RoB 2"`, `"AMSTAR-2"`, or `"general"` at case or record level; add `custom_domains[]` for domain-specific checkpoints.
3. **Dual-Reviewer Reconciliation**: Supply optional `human_review_overrides` keyed by `record_id` and `dimension_id` to preserve machine-vs-human deltas (`human_review_delta`) for scale refinement rather than silent patching.
4. **Verify & Triage**: Inspect `quality_appraisal_report.md` (confirming traffic-light icons match raw rationales) and `ranked_evidence_candidates` for evidence prioritization.

## Dependencies, Determinism & Boundaries

- Pure Python 3.12 standard library (uses `pdfplumber`/`pypdf`/`pdftotext` when available for PDF pagination; degrades honestly on scanned PDFs).
- Deterministic and idempotent across repeated runs. Supports methodological appraisal and evidence screening; does NOT replace formal peer review or clinical/policy judgment.
