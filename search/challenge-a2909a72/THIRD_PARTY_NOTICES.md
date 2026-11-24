# Third-Party Notices & Attributions

## Runtime Dependencies
This Skill runs exclusively on the **Python 3.12 Standard Library** (PSF License).
No external third-party packages are bundled or required at runtime. Optional local document libraries (`pdfplumber`, `pypdf`, `python-docx`, `PyYAML`) are imported defensively with pure-stdlib fallbacks when absent.

## Bibliographic Data Format Compatibility
Parsers in `scripts/search_core/scholarmeta.py` and `scripts/search_core/caseio.py` read publicly documented response schemas from:
- Crossref REST API (`work`, `work-list` JSON)
- OpenAlex API (`results`, `abstract_inverted_index` JSON)
- Europe PMC REST API (`resultList` JSON)
- PubMed / MEDLINE (`MedlineCitation` JSON / XML)
- arXiv Atom XML feeds (`http://www.w3.org/2005/Atom`)
- Standard BibTeX (`.bib`), RIS (`.ris`), NBIB (`.nbib`), and RFC-4180 CSV formats

No live API credentials, proprietary datasets, or third-party codebases are bundled inside this package.

## Public Community Failure-Mode References
Design guards explicitly account for documented open-source scholarly retrieval issues:
- OpenAlex / PyAlex cursor pagination error handling (PyAlex issue #103: HTTP 400 invalid cursor URL on terminal page).
- Traceable, reversible duplicate handling (ASReview issue #328: preserving duplicate records with explicit rollback links rather than silent deletion).
