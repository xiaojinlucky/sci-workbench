# Systematic Literature Search & PRISMA 2020 Screening Skill

Offline, pure-stdlib scientific screening engine that enforces protocol version freezing, identity-safe multi-source deduplication, clause-linked reason codes with verbatim sentence quotes, dual-reviewer conflict arbitration, and strict PRISMA 2020 arithmetic conservation.

## Comparison vs Ad-Hoc Screening & Legacy Workflows

| Capability | Ad-Hoc Screening | This PRISMA 2020 Screening Engine |
| --- | --- | --- |
| Protocol Drift Control | Oral/mid-stream changes leave early labels stale | Computes `protocol_sha256` and audits `prior_decisions` in `criteria_drift_audit` |
| Deduplication Safety | Title-only merge risks fusing distinct studies | Exact DOI/PMID/arXiv + verified preprint->VoR (`alt_dois`); blocks distinct non-preprint DOIs into `near_duplicate_review_queue` |
| Exclusion Provenance | Generic "not relevant" labels | Structured `reason_code`, `triggered_clause`, and exact verbatim sentence quote |
| Missing Metadata & Paywalls | Missing years auto-included; unretrieved fulltexts miscounted as exclusions | Missing protocol-bound years route to `arbitration_queue`; unretrieved fulltexts separate into PRISMA `n_not_retrieved` |
| Dual-Reviewer Conflicts | Averaged away or silently resolved | Classifies conflict types, computes observed agreement + Cohen's Kappa, retains synthetic fixture labels |

## Quick Start (Offline Linux / Python 3.12+)

```bash
python -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python -B scripts/systematic_screener.py --input /input --output /app/output --work /app/work
python -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require prisma_screening_ledger.json prisma_screening_ledger.md
```

## Adapting to Your Systematic Review

1. **Define Frozen Protocol (`case.json:criteria`)**: Set `version`, `year_min`/`year_max`, `allowed_languages`, `pico_clauses` (or `required_terms` with synonym groups like `[["butyrate", "SCFA"], ["colitis", "IBD"]]`), `exclude_terms`, `exclude_designs`, and `require_fulltext`.
2. **Import Heterogeneous Exports**: Place RIS (`.ris`), BibTeX (`.bib`), Crossref/EuropePMC/OpenAlex JSON, arXiv XML, CSV, or PDFs in `/input`. UTF-8 replacement decoding prevents crashes on malformed characters from mixed exports.
3. **Supply Dual-Reviewer Records (Optional)**: Add `reviewer_opinions[]` (`record_id`, `reviewer`, `decision`, `reason_code`, `synthetic` flag if fixture) and `prior_decisions[]` to enable Cohen's Kappa and criteria-drift detection.
4. **Audit & Arbitrate**: Inspect `prisma_screening_ledger.md` (verifying `PRISMA Conservation Balanced: True`) and resolve `arbitration_queue` and `near_duplicate_review_queue` before final synthesis.

## Dependencies, Determinism & Limits

- Pure Python 3.12 standard library; zero network calls; deterministic and idempotent across repeated runs.
- Finite snapshot screening only; does not claim live remote database recall or replace researcher scientific adjudication.
