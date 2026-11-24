# Academic Norms & Compliance Audit Skill (`challenge-de9f0708`)

Zero-dependency Skill for six-facet academic norms, ethics/IRB anti-spoofing, PII and double-blind redaction, 128-bit HV-dHash image duplication/flip fingerprinting, third-party copyright dual-track screening, and target-journal guideline verification.

## Key Capabilities
- **Pure-Stdlib Offline Runtime**: Executes cleanly in `python:3.12-slim --network none` on Markdown, LaTeX, plain text, DOCX (stdlib OOXML fallback), PDF (cascade extractor with low-text gap logging), and 8-bit PNG images (pure-stdlib flip/rotation fingerprinting).
- **Ethics & Clinical Trial Anti-Spoofing Engine**: Triggered only on human/animal/clinical studies; rejects placeholder words (`unknown`, `not reported`, `pending`, `waived`), dates (`2024-05-01`), page ranges, phone numbers, and grant IDs masquerading as IRB/IACUC approval IDs; validates `NCT`/`ChiCTR`/`ISRCTN` trial IDs and informed consent.
- **PII & Double-Blind Leak Protection**: Detects patient MRN/hospital IDs, national IDs, phones, participant emails, and local host paths with mandatory `_mask_match` redaction; enforces author email, institution, funding, repository, and self-citation anonymization strictly when `guidelines.json` sets `double_blind: true`.
- **Image Integrity + Third-Party Copyright Dual-Track**: Flags exact and flipped/rotated image duplicates neutrally as `similarity_detected_needs_investigation` (never alleging misconduct); cleanly separates unlicensed adaptations (`material_not_supplied`) from CC-BY/permission-attributed figures (`pass`, zero false alarms).
- **Three-State Separation & Scope Boundary**: Separates `confirmed_violation`, `material_not_supplied`, and `needs_human_or_legal_ethics_review`, reports `guideline_not_supplied` when no venue guide is provided, and states explicitly that results are not formal legal or IRB determinations.

## Quick Invocation
```bash
python scripts/compliance_ethics_auditor.py \
  --input /input \
  --out-json /app/output/compliance_audit.json \
  --out-md /app/output/compliance_report.md \
  --out-result-json /app/output/result.json
```

## Package Layout
- `SKILL.md`: Agent instructions, three-state decision tree, and M1-M6 workflow.
- `scripts/compliance_ethics_auditor.py`: Deterministic six-facet compliance & image-integrity engine with `--out-result-json`.
- `scripts/review_io_core.py`: Zero-dependency document parser + pure-stdlib 128-bit HV-dHash PNG perceptual flip/rotation detector.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: M1-M6 runtime guards.
- `references/`: Shared protocol (`evidence_and_fairness_protocol.md`), output contract (`output_contract_spec.md`), compliance anchors (`compliance_ethics_anchors.md`), and image/copyright guide (`image_and_copyright_audit_guide.md`).
- `examples/minimal_example.md`: Annotated synthetic walkthrough (not a formal case answer).
