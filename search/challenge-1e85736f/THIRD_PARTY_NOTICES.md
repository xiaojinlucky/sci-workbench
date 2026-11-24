# Third-Party Notices & Methodology Attributions

## Runtime Dependencies

- **Python 3.12 Standard Library** (PSF License): All scripts in `scripts/` and `scripts/search_core/` execute with zero mandatory third-party Python packages.
- No prohibited dependencies (`fitz`/`PyMuPDF`, `cairosvg`, `pywin32`/`win32com`) are imported or referenced.

## Public Metadata Schema Compatibility

Parsers in `scripts/search_core/` read offline JSON/XML/BibTeX/RIS/NBIB/CSV snapshots conforming to public scholarly metadata schemas from Crossref, OpenAlex, EuropePMC, PubMed/Entrez, and arXiv Atom XML. No network requests are issued.

## Methodology References

- **PyAlex Issue #103 Pagination/Cursor Resilience Principle**: Explicit detection and non-destructive logging of API pagination, cursor, rate-limit, and timeout failure snapshots so retries remain idempotent without losing watermark state.
- **ASReview Issue #328 Traceable Deduplication Principle**: Cross-source duplicate suppression never deletes records silently; every suppressed duplicate retains its canonical target, match tier, and normalized dedup key for replay and rollback.
