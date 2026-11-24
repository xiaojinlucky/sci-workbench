---
name: academic-compliance-audit
description: Six-facet academic norms, ethics/IRB/clinical-trial anti-spoofing, PII/double-blind leak redaction, image duplication/flip fingerprinting, third-party copyright dual-track, and target-journal guideline compliance auditor with strict three-state epistemic separation.
---

# 学术规范与合规检查 (challenge-de9f0708)

Source-bound six-facet academic norms and compliance auditor for offline sandbox execution. Integrates figure-suite 128-bit HV-dHash image integrity screening (`review_io_core.detect_image_duplications`), outbound privacy and double-blind leak protection with strict `_mask_match` + post-hoc PII redaction, anti-spoofing ethics/IRB/trial-registration verification (including top-journal parenthetical IDs and computational-study exemption), dual-track third-party asset copyright review, and parameterized target-journal guideline checking.

## Scope & Mandatory Legal/Ethics Boundary
- **In scope**: (1) Human/animal ethics, IRB/IACUC approval IDs, and clinical trial registrations; (2) PII, restricted data, local host paths, and double-blind anonymization leaks; (3) Image byte-duplication and flip/rotation fingerprinting plus third-party figure/table license attribution; (4) Funding, acknowledgments, COI, and data/code availability declarations; (5) Reporting standards (CONSORT/PRISMA/ARRIVE/STROBE/STARD/TRIPOD); (6) Supplied target-journal guideline limits and requirements.
- **Out of scope**: Language polishing, ghostwriting, data reanalysis, experimental design, and **formal legal counsel or IRB/Ethics Committee adjudication**. Every report must preserve the explicit scope disclaimer.

## Three-State Epistemic Separation (Hard Guardrail)
Never collapse missing documentation or technical anomalies into proven misconduct. Classify every finding into exactly one compliance state:
1. **`confirmed_violation`**: Verifiable breach of an explicit rule or privacy/anonymization invariant (e.g., exposed patient MRN/phone/host path, double-blind email/institution leak when `double_blind: true`, human/animal study with zero ethics disclosure, exceeding supplied `word_limit` or `abstract_word_limit`, funding self-contradiction).
2. **`material_not_supplied`**: Required evidence or rule source was not included in `/input` (e.g., `guideline_not_supplied` when no venue guide is given; third-party adapted figure missing license/permission paperwork; ethics statement present without an explicitly labeled valid approval ID). Never equate missing paperwork with proven copyright infringement or missing approval.
3. **`needs_human_or_legal_ethics_review`**: Objective technical anomaly or domain judgment requiring human, editorial, institutional, or legal review (e.g., perceptual image duplication/flip match marked strictly `similarity_detected_needs_investigation`; malformed clinical trial ID; ethical exemption verification).

## Six-Facet Compliance Decision Tree
1. **Ethics & Clinical Trial Anti-Spoofing**:
   - Trigger **only** when human/patient/survey/clinical/animal signals appear in manuscript text (never flag pure computational/synthetic studies).
   - Reject pseudo-ethics IDs: placeholder words (`unknown`, `not reported`, `pending`, `waived`), calendar dates (`2024-05-01`), page numbers, phone numbers, and funding/grant numbers (`NSFC`, `NIH R01`).
   - Validate clinical trial registrations (`NCT\d{8}`, `ChiCTR\d+`, `ISRCTN\d+`, `ACTRN\d+`, `UMIN\d+`, `DRKS\d+`), informed consent, and ARRIVE/Helsinki references.
2. **PII / Restricted Data / Double-Blind Protection**:
   - Mask every detected sensitive token (MRN, patient/hospital ID, SSN/national ID, phone, local host path, double-blind email) via `_mask_match` before writing JSON or Markdown outputs.
   - Enforce author email, institution, funding, non-anonymous repository, and first-person self-citation checks **only** when `guidelines.json` enables `double_blind: true`.
3. **Image Integrity & Third-Party Copyright Dual-Track**:
   - Run `review_io_core.detect_image_duplications()` on all discovered image files for exact byte duplicates and 8-orientation flip/rotation perceptual matches. Mark matches `similarity_detected_needs_investigation` (`needs_human_or_legal_ethics_review`)—never accuse authors of fraud.
   - Distinguish unattributed/unlicensed adaptations (`insufficient_evidence` / `material_not_supplied`) from properly licensed reuses (`CC-BY`, `CC0`, `MIT`, explicit permission -> `pass`, zero false alarms).
4. **Declarations & Funding Consistency**:
   - Verify Conflict of Interest (COI), Funding/Acknowledgments (exempted when `double_blind: true`), Data/Code Availability, and internal funding contradictions (e.g., claiming "no funding" while listing a grant number).
5. **Reporting Standards**:
   - Check CONSORT/PRISMA/ARRIVE/STROBE/STARD/TRIPOD when required by `guidelines.json` or triggered by study type.
6. **Target Journal Guidelines (`guidelines.json`)**:
   - Bind every guideline finding to the exact supplied parameter, venue name, and version/date (`word_limit`, `abstract_word_limit`, `double_blind`, `max_figures`, `max_tables`, `required_declarations`, `required_reporting_standards`).
   - When no guideline file or object is provided, record `guideline_status: "guideline_not_supplied"` and never invent venue constraints.

## Dual-Compatible Output Contract (`/app/output/result.json`)
- Top-level `status`: `"ok"` (no input/parsing gaps) or `"partial_with_gaps"` (input/parsing gaps present).
- Top-level `audit_status`: `"completed"` | `"partial"` | `"indeterminate"` | `"failed"`.
- Top-level `verdict`: `"compliant_no_major_issues"` | `"violations_detected"` | `"needs_human_investigation"` | `"incomplete_inputs"`.
- Required metadata: `case_id`, `sources`, `uncertainty`, `scope_disclaimer`, `guideline_status`, `artifacts`, `gaps`, `findings`, `checklist_status`, `image_integrity_matches`, `third_party_assets`.
See `references/output_contract_spec.md`.

## Execution Workflow

### Step 1: Preflight Probe, Budget Init, Skeleton Result & Full Indexing
```bash
python /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python /skills/weekly-skill/scripts/budget_guard.py --state /app/work/budget.json --init
python /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-de9f0708"
python /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```

### Step 2: Run Deterministic Compliance & Image-Integrity Engine
```bash
python /skills/weekly-skill/scripts/compliance_ethics_auditor.py \
  --input /input \
  --out-json /app/output/compliance_audit.json \
  --out-md /app/output/compliance_report.md \
  --out-result-json /app/output/result.json
```
If a separate guideline JSON path is provided in `/input/case.json`, pass `--guidelines <PATH>`. Notice `/input/case.json` top-level metadata (such as `version`) is never mistaken for journal guidelines; only an explicit non-empty `guidelines` sub-object or dedicated guideline JSON with real venue keys activates `guideline_supplied`.

### Step 3: Manual Source & Boundary Verification
Consult:
- `references/evidence_and_fairness_protocol.md` (five-gate admissibility & anomaly-vs-misconduct rule)
- `references/compliance_ethics_anchors.md` (anti-spoofing regex boundaries, PII masking, guideline binding)
- `references/image_and_copyright_audit_guide.md` (128-bit HV-dHash flip/rotation triage & dual-track license rules)
- `references/output_contract_spec.md` (JSON schema and `status`/`audit_status` mapping)

Confirm every `material_not_supplied` and `needs_human_or_legal_ethics_review` item is accurately bounded, confirm zero raw PII leaked into any report, and keep `scope_disclaimer` intact.

### Step 4: Finalize `result.json`, Grounding Gate & Deliverable Audit
If manual verification refined any fields, patch `/app/output/result.json` via `result_guard.py`; otherwise run `--finalize`, M5 grounding gate, and M6 deliverable audit directly:
```bash
python /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --finalize
python /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json --require compliance_audit.json --require compliance_report.md
```

## Offline Extraction, Citation Norms & Six-Facet Companion Coverage
- **4-Tier PDF Cascade**: `review_io_core.py` cascades `pypdf` (two-column reading order) → `pdfplumber` → `pdftotext` → pure-stdlib FlateDecode `BT..ET` fallback (`PDF_STDLIB_FALLBACK_USED`) so multi-page real PDFs remain auditable even in minimal containers.
- **Citation Norms & Companion Audit**: In addition to ethics anti-spoofing, PII redaction, dual-track third-party asset permissions, and 128-bit HV-dHash image fingerprinting, `compliance_ethics_auditor.py` runs deterministic multi-line bibliography parsing (with Vancouver superscript protection, retracted/withdrawn reference checks, and dangling citation mapping), numeric sanity (`df > N`, `p = 0.000`), and exports `six_facet_coverage` + `semantic_review_checklist` (with post-hoc PII redaction across every finding) into both `compliance_audit.json` and `result.json`.
