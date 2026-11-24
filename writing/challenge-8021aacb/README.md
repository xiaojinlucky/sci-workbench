# Manuscript Structural and Logical Optimization Skill (`paper-structure-optimization`)

Offline, zero-dependency Python 3.12+ Skill for six-dimension source-grounded structural and logical diagnosis of scientific manuscripts.

## What It Does

1. Parses Markdown, LaTeX, plain text, OOXML `.docx` (pure stdlib XML), and `.pdf` (when `pdftotext` is present) into indexed `(section, paragraph_id, line_range)` units.
2. Runs deterministic six-dimension diagnostics (`scripts/structural_logic_engine.py`):
   - **D1**: Cross-chapter Research Question & Contribution closure (`Abstract/Intro -> Methods/Results/Conclusion`).
   - **D2**: Section order, misplaced content, trial-and-error logs, and **chronological literature laundry lists**.
   - **D3**: `Claim -> Evidence -> Warrant` breaks, unsupported superiority claims, and **unreconciled same-metric contradictions**.
   - **D4**: Sentence/paragraph Jaccard & n-gram cross-section redundancy (distinguishing legitimate Abstract summary restatement from true duplication).
   - **D5**: Observational-to-causal epistemic escalation, universal out-of-sample extrapolation, missing limitations, and defensive self-undermining phrasing.
   - **D6**: Missing canonical sections, missing References for inline citations, and abrupt truncation.
3. Outputs concrete **Editing Action Cards** using a closed verb grammar (`MOVE`, `MERGE`, `SPLIT`, `NARROW_CLAIM`, `ADD_BRIDGE`, `REGROUP_BY_DIMENSION`, `DEFER_TO_AUTHOR`) without unsolicited full rewrites.

## Quick Usage

```bash
python3 scripts/structural_logic_engine.py \
  --manuscript /input/manuscript.md \
  --case-id case-01 \
  --out-dir /app/output
```

## Requirements & Boundaries

- **Runtime**: Python 3.12 standard library only (zero third-party Python dependencies; fully offline).
- **Inputs**: `/input/case.json`, `/input/task.md`, and manuscript file(s).
- **Outputs**: `/app/output/structural_diagnosis.json`, `/app/output/editing_action_plan.md`, `/app/output/result.json`.
- **Limitations**: Automated lexical/metric rules surface structural candidates with exact anchors; nuanced domain-specific theoretical warrants still require author review via the `Accept / Partially Accept / Reject` protocol.
