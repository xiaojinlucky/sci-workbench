# Minimal Synthetic Example (`academic-citation-management`)

> **Notice**: Synthetic interface example only; does not represent any official evaluation case.

## 1. Example Input

- `manuscript.docx`: Word draft containing citations `[3]` and `[1]` (out of order) plus a figure caption referencing `[3]`.
- `library.bib` + `export.ris`: Contains a `bioRxiv` preprint and its later peer-reviewed `Nature Communications` publication (same title + authors), plus two distinct 2024 papers by homonymous author `Wei Zhang`.

## 2. Example CLI Invocation

```bash
python3 -B scripts/citation_lifecycle_manager.py \
  --manuscript manuscript.docx \
  --bib library.bib \
  --bib export.ris \
  --out-dir /app/output \
  --case-id "SYNTH-CIT-01"
```

## 3. Illustrative Output Highlights

- `docx_live_field_audit.live_field_status`: `"intact"` (if `ADDIN ZOTERO_ITEM` / `CSL_CITATION` fields exist) or `"flattened_plain_text"` (if Google Docs round-trip stripped them, with official Zotero recovery steps attached).
- `deduplication`: Preprint + journal duplicate merged (`preprint_vs_published_title_author_match`, formal journal kept); two `Wei Zhang (2024)` papers with different titles kept separate (`kept_separate_homonymous_author_protection`).
- `sync_and_verification`: `[3]` → `[1]`, `[1]` → `[2]` synchronized across body text, figure caption, and bibliography; `metadata_existence_status` (`verified_in_library`) reported separately from `claim_support_status` (`unverified_fulltext_not_supplied` when no paper full text is provided).
- `formatted_references_gbt7714.md` & `formatted_references_apa7.md`: Properly formatted offline entries with DOI/volume/issue/pages and explicit `[MISSING_FIELDS: ...]` tags if any field is absent.
