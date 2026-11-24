# Output Contract Specification (`result.json`)

Every run writes `/app/output/result.json` with all five core academic-writing Harbor contract fields plus deterministic LaTeX execution and inspection telemetry:

| Field | Required Content |
| --- | --- |
| `case_id` | Exact string copied from `/input/case.json` or `--case-id`. |
| `status` | `ok` (compiled cleanly, zero blocking gaps), `partial_with_gaps` (deliverables produced with explicit gaps), or `failed` (build failed without valid PDF). |
| `deliverable` | Object naming `editable_project_dir`, `entry_tex`, `compiled_pdf`, `build_log`, and `page_previews`. |
| `preserved_facts` | Proof of immutable `/input`, source file count, label/bib counts, and per-file SHA-256 hashes. |
| `evidence_map` | Verifiable links from key deliverable claims to source files, `build_log.txt`, and artifact SHA-256 digests. |
| `change_log` | Comparison status vs `--original`, unified diffs for modified files, human confirmation items, and semantic reference warnings. |
| `sources` | Relative paths of all input files actually read. |
| `uncertainty` | Plain statement of remaining unverified items (e.g., missing `pdftoppm`, unresolved citations, or environmental limits). |
| `latex_execution` | Engine directives, per-step `step_results` with true return codes, diagnostics, and full `source_manifest`. |
| `bib_and_ref_audit` | Duplicate labels, missing `.bib` keys, ref-type mismatches, and semantic self-ref warnings. |
| `reference_validation` | Author-declared `reference_expectations.json` literal check results. |
| `layout_audit` | Two-column detection, widow/club penalties, `Overfull \hbox` counts, and severe clipping items. |
| `pdf_inspection` | `pdfinfo` page count/size, `pdftotext` `??` marker scan, widow/orphan warnings, font embedding, and rendered PNG paths. |
| `gaps` | Machine-readable list of structured gap objects (`code`, `message`, locations). |
