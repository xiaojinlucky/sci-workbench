# Minimal Invocation Example (Illustrative Only — Not a Formal Case Answer)

## 1. Synthetic Input Directory
- `manuscript.md`:
```markdown
# Abstract
We conducted a clinical trial enrolling 120 patients (trial ID NCT04567890).

# Methods
Ethics approval No.: 2024-05-01. Written informed consent was obtained from all participants.
Sample record MRN: PT-88412 was used during quality review.
Figure 1 is adapted from Smith et al. (2022) under CC BY 4.0 license.
Figure 2 is adapted from an external clinical atlas without listed license terms.

# Declarations
Competing interests: The authors declare no competing interests.
Funding: Supported by Grant ABC-2024-001.
Data availability: Anonymized summary data are deposited on Zenodo.
```
- `guidelines.json`:
```json
{
  "journal": "Example Clinical Medicine",
  "guideline_version": "2026-01",
  "double_blind": false,
  "word_limit": 4000,
  "required_reporting_standards": ["CONSORT"]
}
```
- `fig_panel_a.png` and `fig_panel_b_flipped.png` (horizontally flipped copy of `fig_panel_a.png`).

## 2. Command
```bash
python scripts/compliance_ethics_auditor.py \
  --input /path/to/sample_dir \
  --out-json /app/output/compliance_audit.json \
  --out-md /app/output/compliance_report.md
```

## 3. Illustrative Output Excerpt (Example Only)
- **Ethics Anti-Spoofing**: Rejects `2024-05-01` as `calendar_date_spoof`; marks `ethics_approval_id_unverified` (`material_not_supplied`), while confirming `NCT04567890` (`pass`).
- **PII Redaction**: Flags `MRN` with redacted quote `Matched patient_or_id_token: MRN***12` (`confirmed_violation`, `critical`).
- **Image Integrity**: Flags `fig_panel_a.png <-> fig_panel_b_flipped.png` (`horizontal_flip`, Hamming=`0/64`) strictly as `similarity_detected_needs_investigation` (`needs_human_or_legal_ethics_review`).
- **Copyright Dual-Track**: `Figure 1` (`CC BY 4.0`) -> `pass` (zero false alarm); `Figure 2` -> `third_party_asset_license_missing` (`material_not_supplied`).
- **Guideline Enforcement**: Flags missing `CONSORT` checklist citing `Example Clinical Medicine (version=2026-01)`.
- **Scope Disclaimer**: Explicitly notes results do not constitute formal legal or IRB determination.
