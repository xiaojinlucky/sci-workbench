# Four-Stage Coverage Audit & Honest Abstention Protocol

Long scientific PDFs and structured literature bundles commonly fail silently when an assistant reads only the first page or abstract and extrapolates answers to later sections, footnotes, or missing pages. This skill enforces observable coverage separation.

## 1. Four-Stage Coverage Separation

Every run computes and persists `coverage.page_coverage_audit`:

| Stage | Field | Meaning | Failure Mode Caught |
| --- | --- | --- | --- |
| Stage 1: Ingestion | `ingested_pages` / `declared_sections` | Total pages in PDF (`pdfinfo`) or declared sections in `case.json`. | Hidden truncation at upload/load time. |
| Stage 2: Extraction | `extracted_pages` & `empty_or_scanned_pages[]` | Pages yielding >= 20 non-whitespace characters via `pdftotext` (or non-empty structured sections). | Image-only/scanned PDF pages with no text layer. |
| Stage 3: Indexing | `indexed_paragraphs`, `tail_page_coverage_check`, `table_footnote_coverage_check` | Total addressable evidence blocks plus explicit audit of tail pages (last 20% / final page) and table/footnote blocks. | First-page-only reading bias; dropped table footnotes. |
| Stage 4: Citation | `cited_paragraphs`, `cited_pages[]` | Unique `paragraph_id`s and page numbers actually cited across `answers[].claims[].evidence[]`. | Claiming full-document reading while only citing Page 1 / Abstract. |

## 2. Tail-Page & Table-Footnote Traps

- **Tail-Page Trap**: Critical limitations, appendix definitions, regulatory caveats, or postmarketing reporting deadlines often sit on the final pages of a long paper or guidance document. `literature_qa_engine.py` explicitly boosts and separately lists tail-page candidate matches (`tail_page_hits`) in `/app/work/qa_retrieval_pack.json` whenever lexical/entity overlap exists.
- **Table & Footnote Trap**: Experimental exclusions, unit qualifiers (`*Measured at 25 °C; †n = 12 excluded`), and statistical thresholds frequently live in `tables[]`, `footnotes[]`, or `figure_captions[]`. These blocks are indexed as first-class addressable evidence units (`T01`, `FN01`, `FigCap01`) with dedicated retrieval recall (`table_footnote_hits`).

## 3. Honest Abstention Rules (`partial` vs `insufficient_evidence` vs `input_error`)

- **`insufficient_evidence`**: Use when the supplied paragraphs do not contain the requested value, comparison, or mechanism—or when the relevant section/page is listed in `unread_sections` or `empty_or_scanned_pages`. Explain precisely which sections/pages were checked and what is absent. Never backfill from the title, abstract, or general scientific knowledge.
- **`partial`**: Use when some atomic sub-questions are verified by verbatim quotes, while others are unreported or located in an unavailable section. Put verified claims in `claims[]` and every missing sub-question in `unanswered_parts[]`.
- **`input_error`**: Use only when `/input/case.json` is missing/malformed or contains zero readable text blocks.
