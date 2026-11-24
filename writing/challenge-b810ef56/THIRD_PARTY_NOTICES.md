# Third-Party Notices

This Skill package runs exclusively on the Python 3.12+ standard library and bundles zero third-party Python libraries or external binaries.

## Methodology Acknowledgments

- The 5-state claim verification taxonomy (`SUPPORTED`, `PARTIALLY_SUPPORTED`, `CONTRADICTED`, `UNVERIFIABLE_ABSENT`, `EXISTENCE_ONLY_NOT_SUPPORTING`) separates source existence from claim support and avoids equating unavailable full text with fabrication.
- Synthesis discipline addresses documented scholarly literature-review failure modes (paper-by-paper recitation without problem-centered synthesis or applicability-condition reconciliation).
- Optional system CLI tools (`pdftotext`, `soffice`), if installed in the runtime container, are invoked via `subprocess` under their system licenses and are not bundled.
