# Image Integrity & Third-Party Copyright Dual-Track Guide (challenge-de9f0708)

## 1. Offline Image Duplication & Flip/Rotation Fingerprinting
`compliance_ethics_auditor.py` delegates image screening to `review_io_core.detect_image_duplications()`:
- **Exact byte hash (SHA-256)**: Detects identical image files reused under different filenames (`exact_byte_duplicate`).
- **64-bit Perceptual Grid Hash across 8 Dihedral Orientations**: Decodes 8-bit PNG images via pure Python stdlib (or uses PIL when installed for broader formats) and compares across:
  - `identical_perceptual`
  - `horizontal_flip`, `vertical_flip`
  - `rotated_90`, `rotated_180`, `rotated_270`
  - `rotated_90_hflip`, `rotated_270_hflip`
- Matches passing the dual 128-bit HV-dHash (`<= 12/128`, or `<= 6/128` for sparse white-background plots) AND 16×16 Pearson intensity correlation (`>= 0.96`, or `>= 0.985` for sparse plots) gate are flagged with:
  - `status: "similarity_detected_needs_investigation"`
  - `compliance_state: "needs_human_or_legal_ethics_review"`
  - **Mandatory neutrality**: Never claim intentional fabrication or misconduct from pixel similarity alone. Request uncropped raw microscopy/instrument files and figure-assembly provenance.
- When an image format cannot be pixel-decoded offline (e.g., non-PNG without PIL), log `IMAGE_PIXEL_UNVERIFIED` in `gaps` honestly after running exact SHA-256 comparison.

## 2. Third-Party Asset Copyright Dual-Track Audit
For any caption or text mentioning external reuse (`adapted from`, `reproduced from`, `reprinted from`, `modified from`, `sourced from`, `third-party image`, `source unknown`, `改编自`, `引自原图`, `来源不明`):

| Track | Caption / +/-1 Line Context | Compliance Result | Finding Emitted |
| --- | --- | --- | --- |
| **Track A (Compliant Reuse)** | Explicit open license (`CC BY`, `CC-BY 4.0`, `CC0`, `MIT`, `Public Domain`) or written permission (`reprinted with permission`, `已获授权`) | `license_status: "pass"` | **None (0 false positives)** |
| **Track B (Missing Permission / Unknown Source)** | No license type or permission statement found | `license_status: "insufficient_evidence"` | `major`, `compliance_state: "material_not_supplied"`, `status: "needs_human_review"` |

Never label Track B as proven copyright infringement; state clearly that permission/license documentation is missing and must be supplied or the asset replaced.
