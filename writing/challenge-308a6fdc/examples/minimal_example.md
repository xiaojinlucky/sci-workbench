# Minimal Synthetic Invocation Example (Illustration Only)

> Note: This is a synthetic interface demonstration, not a formal competition test case or answer.

## 1. Synthetic Input Layout (`/input/`)

- `/input/case.json`:
  ```json
  {
    "case_id": "EXAMPLE-MANUSCRIPT-01",
    "task_instruction": "Draft an IMRaD manuscript excerpt from cohort_metrics.csv and refs.bib."
  }
  ```
- `/input/cohort_metrics.csv`:
  ```csv
  group,n,response_pct,sd_pct,p_value
  Control,40,12.5,3.1,
  Treated,42,24.8,4.2,0.014
  ```
- `/input/refs.bib`:
  ```bibtex
  @article{chen2024baseline,
    author = {Chen, L. and Patel, R.},
    title = {Cohort response benchmarks in metabolic modulation},
    journal = {Journal of Experimental Medicine},
    year = {2024},
    doi = {10.1084/jem.20240001}
  }
  ```

## 2. CLI Commands

```bash
python3 scripts/manuscript_workbench_engine.py index --input-dir /input --out-json /app/work/material_index.json
python3 scripts/manuscript_workbench_engine.py audit --input-dir /input --draft /app/output/manuscript.md --out-json /app/work/manuscript_audit.json
python3 scripts/manuscript_workbench_engine.py export --input-dir /input --draft /app/output/manuscript.md --case-id EXAMPLE-MANUSCRIPT-01 --out-dir /app/output --render-preview
```

## 3. Illustrative Structured Output (`result.json` excerpt)

```json
{
  "case_id": "EXAMPLE-MANUSCRIPT-01",
  "task_id": "academic-manuscript-writing",
  "status": "partial_with_gaps",
  "deliverable": {
    "manuscript_markdown": "manuscript.md",
    "manuscript_docx": "manuscript.docx",
    "journal_structure_spec": "UNSPECIFIED"
  },
  "preserved_facts": {
    "grounded_numbers_count": 6,
    "ungrounded_numbers_count": 0,
    "ungrounded_dois": [],
    "abstract_body_mismatch_numbers": []
  }
}
```
