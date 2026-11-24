# Academic Compliance & Ethics Verification Anchors (challenge-de9f0708)

## 1. Ethics Approval Anti-Spoofing Rules
Ethics checks trigger **only** when human/patient/survey/clinical or animal-experiment signals appear in non-reference sections.
An ethics/IRB/IACUC keyword alone never proves an approval identifier exists. A candidate token following an explicit approval label (`IRB No.`, `Approval ID`, `IACUC Protocol`, `伦理批号`) MUST be rejected if it matches any of:
1. **Placeholders**: `unknown`, `not reported`, `pending`, `waived`, `exempt`, `none`, `missing`, `n/a`, `待补充`, `未报告`.
2. **Calendar dates**: `YYYY-MM-DD`, `YYYY/MM/DD`, `DD/MM/YYYY`, or 8-digit compact dates (`YYYYMMDD`).
3. **Page numbers or small numeric page ranges**: tokens preceded by `p.`/`pp.`/`page` or pure small ascending ranges (`12-18`).
4. **Phone numbers**: 11-digit mobile strings or formatted telephone numbers.
5. **Funding / Grant numbers**: tokens prefixed by `NSFC`, `NIH`, `NSF`, `ERC`, `R01`, `R21`, `U01`, or numeric grant IDs adjacent to grant/funding cues.
6. **Ordinary prose words**: tokens containing fewer than 2 digits.

All rejected candidates are recorded in `rejected_pseudo_ethics_ids` for transparent review.

## 2. Clinical Trial Registration Validation
When interventional/clinical trial cues appear, require a well-formed public registry ID:
- `NCT` + exactly 8 digits (`NCT\d{8}`)
- `ChiCTR` + 6-12 digits
- `ISRCTN` + 6-9 digits
- `ACTRN` + 10-14 digits, `UMIN` + 9 digits, `DRKS` + 8 digits, or `EudraCT YYYY-NNNNNN-CC`.
Malformed identifiers (e.g., 5-digit `NCT12345`) are flagged as `clinical_trial_id_malformed` (`needs_human_or_legal_ethics_review`).

## 3. PII & Double-Blind Redaction Matrix
- **Always flagged & redacted via `_mask_match`**: patient MRN/hospital IDs, national IDs/SSNs, participant emails, exposed phone numbers, and local host user directories.
- **Flagged ONLY when `guidelines.json` sets `double_blind: true`**: author contact emails, named universities/hospitals in header/methods, grant numbers in acknowledgments, non-anonymous code repositories (non-anonymous GitHub/GitLab/HuggingFace links), and first-person self-citations ("in our previous work [1]").
- **Never flagged in open / single-blind drafts**: standard author affiliations, corresponding emails, funding statements, or public code repositories.

## 4. Target Journal Guidelines Binding
Supported keys in `guidelines.json` (or `case.json["guidelines"]`):
- `journal` / `venue`, `guideline_version` / `version` / `date`
- `word_limit` (main text excluding references)
- `abstract_word_limit`
- `double_blind` (`true` / `false`)
- `max_figures`, `max_tables`
- `required_declarations` (e.g., `["conflict_of_interest", "funding_disclosure", "data_code_availability", "author_contributions"]`)
- `required_reporting_standards` (e.g., `["CONSORT", "PRISMA", "ARRIVE", "STROBE"]`)

If no guidelines are provided, set `guideline_status: "guideline_not_supplied"` and never fabricate journal limits or blinding rules.
