# Citation Standards, Deduplication Rules & Zotero Live-Field Migration Protocol

## 1. Word ↔ Google Docs Live-Field Integrity & Recovery (Zotero Official Workflow)

Opening a Word `.docx` directly in Google Docs (even via Google Drive sync) strips Word OOXML field codes (`ADDIN ZOTERO_ITEM`, `CSL_CITATION`, `EN.CITE`, `CITAVI`) and converts them into flattened plain text. Visible `[1]` or `(Smith et al., 2021)` strings remain on screen, creating silent desynchronization on the next edit.

### Diagnostic States
- `intact`: Every cited paragraph with visible citation markers carries active OOXML field instructions.
- `flattened_plain_text`: Visible citation markers exist, but zero active citation field instructions exist in `word/document.xml`.
- `mixed`: Some paragraphs retain live fields while others were pasted or edited as plain text.

### Official Safe Migration & Recovery Checklist
1. **Master Backup First**: Save a read-only archival copy of the master `.docx` with intact live fields before collaborating.
2. **Safe Cross-Processor Transfer**: In Zotero Word Plugin → `Document Preferences` → **`Switch to a Different Word Processor`**, export the transfer document before uploading to Google Docs.
3. **Post-Transfer Refresh**: Open in target processor, run **Zotero → Refresh**, and verify fields remain editable and update automatically upon adding/deleting a test citation.
4. **Recovery After Flattening**: Do not overwrite the master; compare the returned flattened draft against the intact master using Word Track Changes / Compare, or re-insert live fields from the shared library.

## 2. Explainable Bibliography Deduplication Rules

| Priority | Match Criterion | Survivor Selection | Explanation Logged |
| --- | --- | --- | --- |
| 1 | Exact normalized DOI match | Peer-reviewed > preprint; richer metadata count wins | `exact_doi_match` |
| 2 | Normalized title similarity ≥ 0.90 + author overlap (preprint vs. journal) | Formal peer-reviewed publication wins; preprint DOI archived in `preprint_doi` | `preprint_vs_published_title_author_match` |
| 3 | Bilingual / alternative title match (`title_en`/`title_zh`/`TT`) + author overlap | Record with DOI and fuller pagination wins; missing fields backfilled | `bilingual_title_author_match` |
| Guard | Same author surname & same year, but title similarity < 0.90 and different DOIs | **Keep both separate**; never merge on author+year alone | `kept_separate_homonymous_author_protection` |

## 3. Target Formatting Specifications

### GB/T 7714-2015 (Sequential Numeric)
- **Format**: `[序号] 主要责任者. 题名[文献类型标识]. 刊名, 年, 卷(期): 起止页码. DOI: ...`
- **Type Codes**: `[J]` Journal article, `[C]` Conference paper, `[M]` Monograph/Book, `[D]` Thesis, `[EB/OL]` Online/Preprint.
- **Authors**: List up to 3 authors; if >3, append `, 等` (Chinese) or `, et al` (English). English surnames uppercase + initials (`ZHANG W, LI M`).
- **Missing Fields**: Never fabricate; emit explicit `[MISSING_FIELDS: ...]`.

### APA 7th Edition
- **Format**: `Surname, F. M., & Surname, F. M. (Year). Title. *Journal*, *Volume*(Issue), pages. https://doi.org/...`
- **Authors**: List up to 20 authors (`&` before final author); for >20, list first 19 + `, ... ` + final author.
- **Missing Fields**: Use `(n.d.)` when year is missing and append `[MISSING_FIELDS: ...]`.

## 4. Two-Layer Verification & Multi-Marker Legend Contract

- **Layer 1 (`metadata_existence_status`)**: Checks whether a bibliographic record exists, is complete, or carries Retraction / Expression of Concern flags.
- **Layer 2 (`claim_support_status`)**: Checks whether supplied source text/excerpts actually support the citing sentence. When full text/abstract is not provided offline, status MUST be `unverified_fulltext_not_supplied` (never `supported`).
- **Multi-Marker Legend Check (NotebookLM #2416 Guard)**: When text contains ≥3 distinct citation markers (`[1]..[20]`) but structured source metadata collapses to only 1 source object, emit `marker_to_source_collapse_detected: true` and list every unmapped marker.
