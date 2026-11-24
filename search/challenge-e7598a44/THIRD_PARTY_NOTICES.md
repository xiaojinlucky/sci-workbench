# Third-Party Notices

This Skill package is implemented using the Python 3.12 standard library only and bundles no third-party source code.

## Optional Runtime Backends (Not Bundled)

If preinstalled in the execution container, the loader may optionally invoke the following tools for PDF text extraction:
- `pdfplumber` (MIT License)
- `pypdf` (BSD-3-Clause License)
- `pdftotext` from Poppler (GPL-2.0 / GPL-3.0 CLI subprocess)

When absent, the pipeline runs cleanly in metadata-only mode and reports the limitation in `run_manifest.json` and `result.json:status.warnings`.

## Methodology References (Concepts Only; No Code Copied)

- Element-card & evidence-ledger concepts inspired by reproducible biomedical literature synthesis and PRISMA screening practices.
- Traceable duplicate-handling principles informed by public systematic-review screening discussions (preserving duplicate provenance rather than silent deletion).
