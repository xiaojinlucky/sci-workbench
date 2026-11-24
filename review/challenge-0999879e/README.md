# Manuscript Quality & Publishability Audit (`challenge-0999879e`)

Offline, source-bound, six-facet academic manuscript quality auditor designed for isolated Linux execution sandboxes.

## Key Capabilities
- **Zero-Dependency Offline Parsing**: Pure Python 3.10+ stdlib reads Markdown, LaTeX, plain text, OOXML `.docx` (paragraphs + tables), `.bib`, and 8/16-bit + palette PNG images; automatically leverages `pdfplumber`, `pypdf`, `pdftotext`, `python-docx`, and `PIL` when installed.
- **Full-Document Coverage & Wrapped-PDF Resilience**: Recognizes Markdown, LaTeX, and plain-text/PDF academic headings (including merged `Results and Discussion`), merges PDF hard-wrapped paragraphs and cross-line hyphenations with exact `line_start..line_end` provenance, computes `coverage_ratio`, and flags scanned/low-text PDF pages honestly.
- **Cross-Modal & Numerical Integrity**: Reconciles Abstract vs Results percentages (filtering statistical constants like `95% CI` / `80% power` with `±0.5 pp` rounding tolerance), total `N` vs mutually exclusive group `n` sums, figure/table citations vs captions, contiguous panel letters (`A, B, C...`), error-bar definitions (`SD`/`SEM`/`95% CI`), and 128-bit HV-dHash + intensity-correlation image duplication/flip screening with white-canvas chart protection.
- **Multi-System Bibliography & Scoped Design Audit**: Supports numeric `[n]`, LaTeX `\cite{}`, and Author-Year references with multi-line continuation merging; scopes observational design classification to Abstract/Methods so Discussion "Future RCT" statements do not suppress causal overclaim detection.
- **Strict Epistemic & Fairness Separation**: Separates `factual_error`, `insufficient_evidence`, `norm_violation`, and `suggestion`, tags `method_flaw` vs `under_reported` vs `needs_reconciliation` plus `blocking` vs `optional`, enforces ACL H1-H17 fairness, and never treats perceptual image similarity or missing source texts as proven misconduct.

## Quickstart (Linux Bash)
```bash
python3 -B /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id case-01 --task-id challenge-0999879e
python3 -B /skills/weekly-skill/scripts/manuscript_quality_auditor.py \
  --input /input \
  --out-json /app/output/quality_audit.json \
  --out-md /app/output/quality_review_report.md \
  --out-result-json /app/output/result.json
python3 -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python3 -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json --require quality_audit.json --require quality_review_report.md
```

## Boundaries & Limitations
- Does not rewrite prose, translate manuscripts, or query external online databases when running offline.
- Automated heuristics surface line-anchored cues; deep semantic reconciliation (e.g., verifying whether a cited paper supports causal wording) requires reading the actual source text and keeping unresolved items as `needs_human_review`.
