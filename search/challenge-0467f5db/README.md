# Search Coverage & Omission Analysis Skill

Offline, pure-stdlib scientific retrieval auditing toolkit for measuring search coverage against an independently frozen reference set, attributing every missed paper to actionable query/field/date/source causes, and auditing supplemental searches for genuine net-new recoveries.

## Why Use This Toolkit vs Generic Search Summaries

| Dimension | Generic Search / Ad-Hoc Check | This Coverage & Omission Auditor |
| --- | --- | --- |
| Denominator Integrity | Infers recall from retrieved records alone | Freezes an independent deduplicated `reference_set` with SHA-256 fingerprint |
| Retracted / Unavailable Seeds | Silently deleted or mixed into recall | Explicitly separated into raw vs actionable coverage ratios |
| Title Collision Safety | Fuzzy title overlap inflates hit count | Requires exact DOI/PMID/arXiv OR (title `>=88` + year `+/-1` + first-author surname) |
| Source Timeout / Auth Failure | Reported as "0 hits" | Classified as `source_unreachable_or_failed` with raw error details |
| Supplemental Search Audit | Repeats synonym queries without tracking net gain | Computes per-query marginal-gain curve and halts zero-gain synonym spinning |

## Quick Start (Offline Linux / Python 3.12+)

```bash
python -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python -B scripts/coverage_omission_analyzer.py --input /input --output /app/output --work /app/work
python -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require coverage_audit_report.json coverage_audit_report.md
```

## Adapting to a New Research Topic (4-Step Migration Guide)

1. **Freeze Your Reference Set**: Place known benchmark papers (from prior reviews, clinical guidelines, or expert lists) into `case.json:reference_set` (or a `reference_set.json`/`.csv` file in `/input`). Include `title`, `authors`, `year`, `doi`/`pmid`/`arxiv_id`, and known `source`.
2. **Export Search Snapshots**: Drop raw database exports (Crossref JSON, EuropePMC JSON, OpenAlex JSON, arXiv XML, RIS, BibTeX, CSV, or PDFs) into `/input`, and declare `query_metadata` (`query_id`, `query_string`, `databases`, `field_scope`, `year_min`, `year_max`) plus any `source_failures`.
3. **Add Supplemental Batches**: Place supplemental search results under `supplemental_hits` (or files named `supplemental_*.json`) with distinct `query_id` labels to generate the marginal-gain curve.
4. **Verify & Stop**: Run the pipeline, inspect `coverage_audit_report.md` and `verification_sample` (all fuzzy matches, identity collisions, and stratified omissions), and execute targeted remediation only for recoverable omission classes.

## Supported Inputs & Self-Check

- **Input formats**: JSON (custom, Crossref, EuropePMC, OpenAlex with `abstract_inverted_index`), XML (arXiv Atom), RIS, BibTeX, CSV, HTML, Markdown/TXT, and PDF (via `pdfplumber`/`pypdf`/`pdftotext` when present).
- **Dependencies**: Pure Python 3.12 standard library; zero network calls; deterministic and idempotent across repeated runs on the same snapshot.
- **Scope Boundary**: Recall metrics apply strictly to the finite local snapshot and frozen reference denominator—never extrapolated to live remote database completeness.
