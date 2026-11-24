# Third-Party Notices & Methodology Attributions

## Runtime Dependencies

- **Python 3.12 Standard Library** (PSF License): All scripts in `scripts/` and `scripts/search_core/` execute with zero mandatory third-party Python packages.
- No prohibited dependencies (`fitz`/`PyMuPDF`, `cairosvg`, `pywin32`/`win32com`) are imported or referenced.

## Public Metadata Schema Compatibility

Parsers in `scripts/search_core/` read offline JSON/XML/BibTeX/RIS/NBIB/CSV snapshots conforming to public scholarly metadata schemas from:
- Crossref REST API (`message-type: work / work-list`)
- OpenAlex Works API (`results` / `referenced_works` / `abstract_inverted_index`)
- EuropePMC REST API (`resultList`)
- PubMed / Entrez XML (`PubmedArticleSet`)
- arXiv Atom XML (`http://www.w3.org/2005/Atom`)

All parsing is performed offline on user/organizer-supplied local files; no network requests are issued.

## Methodology References

- **CitationChaser Issue #37 Audit Principle**: Cross-snapshot graph audits report item-level `added`, `vanished`, and `unresolved` deltas rather than conflating raw hit counts with global citation coverage.
- **ASReview Issue #328 Traceable Deduplication Principle**: Merged duplicate records are never silently deleted; every merged record is retained in `rollback_log` with `duplicate_ignored_with_trace`, canonical target, match tier, and rollback action.
