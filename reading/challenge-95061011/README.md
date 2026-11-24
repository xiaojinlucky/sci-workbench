# Multi-Document Comparative Synthesizer (`challenge-95061011`)

An offline, zero-dependency Python 3.12 skill for multi-paper comparison, functional method alignment, six-axis comparability gating, and conflict attribution under Harbor Organizer Revision v2.

## Capabilities
1. **Multi-Format Reading-Flow Offline Ingestion**: Reads `/input/comparison-workorder.json`, `/input/case.json`, and corpus files in `.pdf` (via reading-flow `pdftotext -enc UTF-8` with `pypdf`/`pdfplumber` fallback, zero two-column interleaving, preserving thousands-separated sample sizes like `n=5,539`/`n=1,010`), `.json`, `.md`, `.txt`, and `.csv`.
2. **Deep Automatic 7-Field Prose Extraction Engine (Zero Manual Handoff)**: Directly extracts `study_object`, `dataset_and_sample_size`, `method_operations`, `experimental_conditions`, condition-scoped `metrics_and_values`, `main_conclusion`, and `limitations` alongside all 6 comparability axes from unstructured real papers, grounding every cell to `(source_document_id, paragraph_id, quote)`.
3. **Document Identity, Preprint Deduplication & Scope Filtering (`scripts/dedup_context_guard.py`)**:
   - Detects exact content duplicates, DOI matches, and preprint-vs-version-of-record pairs (`version_mapping`), preventing double counting of revised manuscripts.
   - Preserves distinct experimental contexts under the same DOI via strict context-signature locking.
   - Identifies off-topic distractor documents (`out_of_scope_excluded`) with transparent audit rationale.
   - Enforces `multi_document_satisfied: false` whenever fewer than two independent in-scope documents exist.
3. **Citation Legend (`[1]..[N]`)**: Generates a deterministic index mapping every citation number to `source_document_id`, `title`, `doi`, `source_file`, SHA-256 digest, and `version_status`.
4. **Functional Method Comparison (`literature-mining` protocol)**: Compares operational primitives (`inputs`, `grouping`, `transformation`, `filtering_rule`, `output_semantics`) rather than surface labels, tagging each row as `explicitly_stated`, `functionally_equivalent`, or `inferred_unverified`.
5. **Six-Axis Comparability Gate & Clique Clustering (`scripts/comparison_matrix_builder.py`)**:
   - Evaluates `{task, metric, units, material, pollutant, budget}` pairwise before allowing quantitative synthesis.
   - Provides explicit unit normalization advisories without mutating raw values or units.
   - Separates `comparable_subgroups` (strict cliques), `noncomparable_pairs` (confirmed axis mismatch), and `indeterminate_pairs` (unreported axis blocking pooling).
7. **Three-Column Consensus / Partial Agreement / Conflict Attribution Matrix**: Preserves opposing conclusions and same-metric numeric divergences side by side with structured attribution (`sample_diff`, `method_diff`, `condition_diff`, `metric_or_unit_diff`, `unattributable`) and verbatim `(source_document_id, paragraph_id, quote)` anchors.
8. **Cross-Paper Bleed, Numeric-Stem & Pooling Validator (`--validate-matrix`)**: Rejects cross-paper paragraph bleed, ungrounded quotes, `NUMERIC_VALUE_NOT_IN_QUOTE` (any numeric stem in a non-`not_reported` cell missing from its own quote), illegal pooling of noncomparable/indeterminate pairs, and Citation Legend drift.

## Directory Layout
- `SKILL.md`: Primary execution protocol for the agent.
- `references/output_contract_spec.md`: Complete JSON and Markdown schema specification.
- `references/six_axis_comparability_protocol.md`: Six-axis rules, unit normalization advisories, and clique clustering rules.
- `references/functional_method_comparison_and_conflict_guide.md`: Functional method comparison tags, Citation Legend discipline, and conflict attribution guide.
- `scripts/comparison_matrix_builder.py`: End-to-end extraction, comparability engine, report renderer, and `--validate-matrix` gate.
- `scripts/dedup_context_guard.py`: Identity, version-of-record deduplication, context lock, and scope filter.
- `scripts/preflight_probe.py`, `result_guard.py`, `budget_guard.py`, `block_indexer.py`, `gap_gate.py`, `final_audit.py`, `audit_chain.py`: Offline Harbor runtime guardrails.
- `examples/`: Minimal synthetic workorder and illustrative output schema (not formal test cases).

## Quick Start (Offline)
```bash
# 1. Automated extraction & comparability gate
python3 scripts/comparison_matrix_builder.py --input /input --output /app/output --work /app/work

# 2. Validate agent-refined matrix draft and promote to /app/output
python3 scripts/comparison_matrix_builder.py \
  --input /input --output /app/output --work /app/work \
  --validate-matrix /app/work/matrix_draft.json

# 3. Grounding and final deliverable audit
python3 scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py --dir /app/output --result-file result.json --require comparison_matrix.md --require comparability_audit.json
```

## Runtime Dependencies & Limits
- Python >= 3.10 standard library only (zero third-party Python packages required).
- PDF text extraction uses system `pdftotext` (Poppler); scanned image-only PDFs without an embedded text layer are reported honestly as `no_text_layer` and block pooling rather than fabricating values.
- Lexical regex extraction provides an auditable baseline; complex narrative or table semantics should be refined by the host agent and verified through `--validate-matrix`.
