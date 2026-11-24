# References & Citation Management Skill (`academic-citation-management`)

Offline, zero-dependency Python 3.12+ skill for end-to-end academic reference auditing, Word/Google Docs live-field diagnosis, bidirectional citation renumbering, multi-format bibliography deduplication, and GB/T 7714-2015 / APA 7th formatting.

## Capabilities

1. **OOXML Live-Field vs. Flattened Plain-Text Audit**: Parses `.docx` `word/document.xml` directly (no `python-docx` required) to distinguish genuine `ADDIN ZOTERO_ITEM` / `CSL_CITATION` / `EN.CITE` / `CITAVI` fields from flattened plain text after Word↔Google Docs round-trips (`intact` | `flattened_plain_text` | `mixed`), attaching official Zotero migration/recovery steps.
2. **Bidirectional Synchronization & Renumbering**: Re-indexes `[1]`, `[1-3, 5]` citations in first-appearance order across body text, figure/table captions, and end-of-manuscript bibliographies; detects duplicate numbers, dangling citations, orphan entries, and multi-marker → single-source collapse (NotebookLM #2416 failure mode).
3. **Explainable Multi-Format Deduplication**: Parses `.bib`, `.ris`, and CSL-JSON; merges duplicates via normalized DOI, preprint-vs-published recognition (preferring peer-reviewed records), and bilingual title matching while explicitly protecting distinct papers by homonymous authors.
4. **Offline GB/T 7714-2015 & APA 7th Formatting**: Generates clean formatted reference lists preserving DOI/volume/issue/pages and explicitly annotating missing fields without fabrication.
5. **Two-Layer Verification**: Strictly separates `metadata_existence_status` from `claim_support_status` so bibliography existence is never conflated with claim verification.

## Directory Layout

- `SKILL.md`: Agent instructions, boundaries, and Harbor workflow.
- `scripts/citation_lifecycle_manager.py`: Core OOXML, dedup, sync, formatting, and verification engine.
- `scripts/writing_io_and_invariants.py`: Shared stdlib OOXML & invariant engine.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Harbor M1–M6 guards.
- `references/{academic_writing_standards,citation_standards_and_migration,output_contract_spec}.md`: Standards and contracts.
- `examples/minimal_example.md`: Synthetic invocation example.

## Requirements & Limitations

- Python 3.12+ standard library only; fully functional inside air-gapped Linux Docker containers.
- Offline mode cannot call external CrossRef/PubMed APIs; metadata checks are bounded by supplied libraries and full-text claim checks require supplied excerpts.
