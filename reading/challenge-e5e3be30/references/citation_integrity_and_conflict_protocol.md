# Citation Integrity, Secondary Citation & Conflict Audit Protocol

## 1. Immutable Citation Legend (`[1]..[N]`)
Assign every inventoried corpus document a deterministic 1-based citation tag `[1]..[N]` bound to `(source_document_id, title, doi, source_file, file_sha256, page_count, coverage_status)`.
- Print the Citation Legend at the top of `verification_report.md`.
- Every evidence entry in `verification_cards.json` must record both `citation_number` (`[n]`) and `(source_document_id, paragraph_id, page)`.
- Never renumber `[n]` across claims or sections within one verification run.

## 2. Inline-vs-Structured Citation Drift Audit
Multi-source literature tools frequently emit inline markers (`[1]`, `[2]`, `[3]`) in prose whose count or ID set diverges from the accompanying structured citation object list.
For every claim/report containing inline citation markers or a `structured_citations[]` list:
1. Parse all numeric inline citation markers (`[1]`, `[2, 4]`, `[5-7]`, `(Ref. 3)`), excluding non-citation brackets (`[p < 0.05]`, `[n=12]`, `[95% CI]`, `[Figure 1]`, `[Table 2]`).
2. Compare the unique inline marker IDs against the `structured_citations[]` array IDs/length.
3. Populate `citation_integrity_audit`:
   - `inline_marker_count`, `inline_unique_ids`
   - `structured_citation_count`, `structured_unique_ids`
   - `missing_in_structured[]` (inline markers absent from structured objects)
   - `unreferenced_in_text[]` (structured objects never cited inline)
   - `drift_detected: true/false`
4. When `drift_detected` is `true`, set `diagnostic_flags.inline_vs_structured_citation_drift = true` and record a structured gap (`INLINE_STRUCTURED_CITATION_DRIFT`) so drift is never silently ignored.

## 3. Strict Source/DOI Locking & Wrong-Page Recovery
1. **Explicit Metadata Extraction Only**: Bind a document's DOI solely from structured metadata (`doi`, `source_doi`) or explicit header lines within the top 30 lines (`DOI: ...`, `https://doi.org/...`). Never infer a paper's own DOI from its References/Bibliography section.
2. **Exact Source Binding**: If `cited_ref` specifies a DOI, `source_document_id`, or filename, restrict candidate retrieval strictly to matching documents.
   - If zero corpus documents match -> `insufficient_evidence`, `evidence: []` (zero cross-paper borrowing).
   - If the target document exists and is readable, yet yields no supporting evidence -> set `doi_exists_but_unsupported: true`, verdict `insufficient_evidence`.
3. **Wrong-Page / Anchor Mismatch Recovery (Same Document Only)**:
   - Parse explicit page/paragraph anchors from `cited_ref`, `cited_page`, or `cited_paragraph` (e.g., `p. 3`, `page 12`, `para 2`, `DOC:p4-b1`).
   - Check the cited anchor first. If the cited page/paragraph does NOT support the claim, search the remaining pages of the **same** document.
   - When evidence is located elsewhere in the same document, set `wrong_page_or_anchor_mismatch: true`, return `cited_anchor` AND `actual_anchor` (`page` + `paragraph_id`), and note the citation locator error explicitly in `verdict_rationale`.

## 4. Secondary Citation (Indirect Citation) Audit
Authors routinely summarize prior work in Introduction or Background paragraphs (e.g., *"As reported by Smith et al. (2019) [12], ..."*, *"Previous studies demonstrated ..."*, *"既往研究表明..."*).
- Tag evidence paragraphs located in Introduction/Background or matching third-party attribution patterns with `attribution_type: "secondary_citation"` and `secondary_citation_warning: true`.
- Never treat a secondary citation as primary empirical confirmation by the citing paper; state explicitly in `verdict_rationale` that the statement is indirectly cited and requires checking the primary reference.

## 5. Abstract-vs-Body / Table Conflict Audit
Abstracts occasionally generalize or omit null subgroups reported in Results or Tables.
- Tag paragraphs with `section_hint` (`abstract`, `introduction`, `methods`, `results`, `discussion`, `table_or_figure`, `body`).
- When retrieved evidence from `abstract` has opposing polarity, missing qualification, or conflicting numeric values relative to `results` / `table_or_figure` within the same document:
  1. Set `abstract_body_conflict_detected: true`.
  2. Retain BOTH verbatim anchored quotes (`abstract` paragraph + `results/table` paragraph).
  3. Issue a qualified `partially_supported` verdict explaining the exact discrepancy between the abstract claim and the full-text/table evidence.

## 6. Localization Insufficiency (`fulltext_unavailable_or_figure_caption_missing`)
When a document is marked or detected as `abstract_only`, `no_text_layer` (empty PDF extraction), or `figure_caption_missing` (claim refers to a Figure/Chart/Image whose caption or panel data is absent from extracted text):
- Set `fulltext_unavailable_or_figure_caption_missing: true` and `localization_insufficient: true`.
- Verdict MUST remain `insufficient_evidence` (never `contradicted` and never `supported`).
- State the exact missing artifact (full text, text layer, or figure caption) in `verdict_rationale` and `gaps[]`.
