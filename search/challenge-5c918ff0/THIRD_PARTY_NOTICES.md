# Third-Party Notices & Attributions

## Runtime Dependencies
This Skill runs exclusively on the **Python 3.12 Standard Library** (PSF License).
Zero third-party Python packages or external binaries are required at runtime.

## Supported Public Bibliographic Schemas
Parsers in `scripts/search_core/scholarmeta.py` and `scripts/search_core/caseio.py` implement standard schema readers for publicly documented bibliographic formats:
- Crossref REST API (`work` / `work-list` JSON)
- OpenAlex API (`results` JSON with `abstract_inverted_index`)
- Europe PMC REST API (`resultList` JSON)
- PubMed / MEDLINE (`MedlineCitation` JSON and XML)
- arXiv Atom XML (`http://www.w3.org/2005/Atom`)
- BibTeX (`.bib`), RIS (`.ris`), NBIB (`.nbib`), and CSV exports

No credentials, live API keys, or third-party codebases are bundled in this package.

## Public Community Reliability References
- Reversible duplicate logging inspired by documented systematic review tooling issues (ASReview issue #328: traceable duplicate retention rather than destructive deletion).
- Explicit separation of unavailable/rate-limited database responses (HTTP 429) from healthy zero-result responses.
