---
name: multi-doc-comparative-synthesizer
description: Build a source_document_id- and paragraph_id-anchored multi-document comparison matrix from /input/comparison-workorder.json and corpus files (PDF, JSON, MD, TXT, CSV), enforce 6-axis comparability gating (task, metric, units, material, pollutant, budget) before pooling, compare actual functional operations with three-state fact tags, maintain a Citation Legend, and attribute cross-study conflicts without voting or averaging.
---

# Multi-Document Comparison & Synthesis (`challenge-95061011`)

## 1. Scope & Non-Negotiable Invariants
This skill implements Organizer Revision v2 for multi-paper comparison and evidence integration in offline sandboxes. Every output must satisfy five invariants:
1. **Cell-Level Grounding**: Every reported cell carries an exact `(citation_number, source_document_id, paragraph_id, quote)` where `paragraph_id` belongs strictly to `source_document_id` and `quote` is a verbatim substring of that paragraph. Zero cross-paper bleed.
2. **Pre-Pooling Six-Axis Gate**: Before any quantitative pooling or cross-study summary statistic, test all six comparability axes `{task, metric, units, material, pollutant, budget}`. Pooling is permitted **only** within a verified clique (`comparable_subgroups`) where all six axes are `reported` and aligned. Any mismatch enters `noncomparable_pairs`; any missing or ambiguous axis enters `indeterminate_pairs` and blocks pooling.
3. **Multi-Document Identity & Deduplication**: Detect exact duplicates and preprint-vs-version-of-record pairs via DOI, title similarity, and normalized content hash so two versions of one paper are mapped—never double-counted as independent studies. Isolate off-topic distractors as `out_of_scope_excluded` with trace rationale. If fewer than two independent in-scope documents remain, set `multi_document_satisfied: false` and refuse multi-document synthesis.
4. **Functional Operations Over Method Names**: Compare what each paper actually does (`inputs`, `grouping`, `transformation`, `filtering_rule`, `output_semantics`), not just method labels. Tag every method fact with one of three states: `explicitly_stated`, `functionally_equivalent`, or `inferred_unverified`.
5. **Conflict Preservation Without Majority Voting**: When studies disagree in direction or magnitude, display both positions side by side in a three-column matrix (`consensus` | `partial_agreement` | `conflict`), attributing every conflict to explicit differing dimensions (`sample`, `method`, `condition`, `metric`, or `unattributable`) with verbatim anchors from both sides.

## 2. Reference Disclosure (Read on Demand)
Locate sibling files relative to this `SKILL.md` directory:
- Read `references/output_contract_spec.md` when assembling or validating `/app/output/result.json`, `/app/output/comparison_matrix.md`, and `/app/output/comparability_audit.json`.
- Read `references/six_axis_comparability_protocol.md` when resolving unit normalization advisories, clique clustering, `indeterminate_pairs`, or pooling eligibility.
- Read `references/functional_method_comparison_and_conflict_guide.md` when decomposing implicit/DIY method steps, assigning three-state fact tags, maintaining the `[1]..[N]` Citation Legend, or attributing directional conflicts.

## 3. Execution Workflow

### Step 1: Preflight, Budget Init & Deterministic Corpus Extraction
Run the offline stdlib scripts (supports JSON, MD, TXT, CSV, and PDF via `pdftotext` with page-aware paragraph IDs `doc:p{page}-b{block}`):
```bash
python3 scripts/preflight_probe.py --out /app/work/env.json
python3 scripts/budget_guard.py --init
python3 scripts/comparison_matrix_builder.py --input /input --output /app/output --work /app/work
```
**What Step 1 produces**:
- Inventories every corpus document with SHA-256 hash, DOI (extracted strictly from metadata/header, never reference lists), page count, extraction status, and full paragraph map (`/app/work/corpus_paragraphs.json`).
- Runs `dedup_context_guard.py` to detect exact-hash duplicates and preprint/published pairs (`version_mapping`), upgrade preprints to Version-of-Record, preserve same-DOI distinct experimental arms (`context_conflicts`), and exclude off-topic distractors (`out_of_scope_excluded`).
- Builds an immutable **Citation Legend** (`[1]..[N]` -> `source_document_id`, `title`, `doi`, `source_file`, `file_sha256`, `version_status`) so multi-document indices stay stable across 5 to 50+ sources.
- Extracts initial common-field cells, functional method rows, 6-axis pairwise comparability sets (`comparable_subgroups`, `noncomparable_pairs`, `indeterminate_pairs`), unit normalization advisories, and preliminary conflict attributions.
- Writes initial `/app/output/result.json`, `/app/output/comparison_matrix.md`, and `/app/output/comparability_audit.json`.

**Step 1 completion criterion**: `env.json`, `corpus_paragraphs.json`, `result.json`, `comparison_matrix.md`, and `comparability_audit.json` all exist, and `document_inventory` accounts for every file under `/input`.

### Step 2: Deep Semantic Cell Refinement & Functional Method Audit (Host Agent Step)
Lexical extraction in Step 1 is an auditable baseline, not the final scientific reading. Inspect `/app/output/result.json`, `/app/output/comparability_audit.json`, and `/app/work/corpus_paragraphs.json`, then write a refined draft to `/app/work/matrix_draft.json`.

For every effective (canonical, in-scope) document:
1. **Verify Every Cell Against Its Own Document**:
   - Confirm `value`, `raw_value`, `units`, and `conditions` come strictly from the labeled `source_document_id` and `paragraph_id`.
   - Copy an exact verbatim `quote` substring directly from that paragraph in `corpus_paragraphs.json`.
   - Mark missing fields `status: "not_reported"` (with `value: null`, `paragraph_id: null`, `quote: ""`) and internal contradictions `status: "ambiguous"` (citing both internal paragraphs). Never infer missing axes from domain labels, neighboring papers, or general knowledge.
2. **Audit Functional Operations (`functional_method_comparison`)**:
   - Decompose each paper's actual workflow into five primitives: `inputs`, `grouping`, `transformation`, `filtering_rule`, and `output_semantics`.
   - Align packaged tools and DIY multi-step procedures by operational equivalence rather than surface labels.
   - Assign exactly one three-state `fact_tag`:

| `fact_tag` | Required Evidence Threshold | Allowed Use in Synthesis |
| --- | --- | --- |
| `explicitly_stated` | Directly stated in Methods, Results, Table, or Figure caption of that paper. | Full inclusion in method matrix and comparability decisions. |
| `functionally_equivalent` | Different method names across papers, with verbatim text proving matching `inputs`, `grouping`, `transformation`, and `output_semantics`. | Group under shared operational role; keep both original method names visible. |
| `inferred_unverified` | Inferred from figure/result pattern without explicit Methods confirmation. | Flag as tentative; never treat as confirmed methodological consensus or pooling basis. |

3. **Enforce Six-Axis Comparability & Unit Discipline**:
   Evaluate every effective document pair across `{task, metric, units, material, pollutant, budget}` using the decision matrix below:

| Pair Condition Across 6 Axes | Classification | `pooling_blocked` | Required Record |
| --- | --- | --- | --- |
| All 6 axes `reported` and canonically aligned | `comparable_pairs` -> verified clique `comparable_subgroups` | `false` | Clique member list + `shared_axes` |
| >=1 axis confirmed mismatch (different dye, dataset, material, metric, or budget) | `noncomparable_pairs` | `true` | Every `mismatched_axes` entry with both raw values + paragraph IDs |
| Unit scale differs (e.g., `mg/L` vs `ug/L`, `h` vs `min`) | `noncomparable_pairs` + `unit_normalization_advisories` | `true` | Preserve original `raw_value` and `units` unmodified; record exact conversion factor |
| Zero mismatches, >=1 axis `not_reported` or `ambiguous` | `indeterminate_pairs` | `true` | Explicit `unreported_axes[]`; unknown is never treated as equal |
| Effective independent in-scope docs `< 2` | `multi_document_satisfied: false` | `true` | Log `INSUFFICIENT_DOCUMENTS_FOR_COMPARISON` in `gaps[]` |

   - If including `quantitative_synthesis` / `pooled_estimates`, every pooled group MUST be a subset of a single verified `comparable_subgroup` (size >= 2).
4. **Construct the Consensus–Partial-Agreement–Conflict-Attribution Matrix**:
   Partition cross-document findings into three explicit buckets (`consensus`, `partial_agreement`, `conflicts`):

| Bucket | Criterion | Required Fields |
| --- | --- | --- |
| `consensus` | >=2 independent studies agree in direction under aligned comparability axes. | `topic`, `rationale`, verbatim anchored `positions[]` from all agreeing papers. |
| `partial_agreement` | Studies share core direction but differ in magnitude, boundary condition, or subgroup scope. | `topic`, `rationale` naming divergence boundary, anchored `positions[]`. |
| `conflicts` | Studies report opposite directions or incompatible effects on the same question. | `topic`, `attribution_dimensions[]` from `{sample, method, condition, metric, unattributable}`, `attribution_rationale`, and verbatim `(citation_tag, source_document_id, paragraph_id, quote)` from EVERY opposing side. |

   - Retain conflicting conclusions side by side; never resolve scientific conflicts by majority vote or naive cross-study averaging.

#### Minimal Refined Draft Layout (`/app/work/matrix_draft.json`)
```json
{
  "citation_legend": [{"citation_number": 1, "citation_tag": "[1]", "source_document_id": "DOC01", "title": "...", "doi": "...", "source_file": "...", "file_sha256": "...", "version_status": "version_of_record"}],
  "comparison_matrix": [{"citation_number": 1, "citation_tag": "[1]", "source_document_id": "DOC01", "cells": {"task": {"field": "task", "citation_number": 1, "source_document_id": "DOC01", "paragraph_id": "DOC01:P001", "value": "...", "raw_value": "...", "units": "", "quote": "...", "status": "reported", "fact_tag": "explicitly_stated"}}}],
  "functional_method_comparison": [{"citation_number": 1, "citation_tag": "[1]", "source_document_id": "DOC01", "paragraph_id": "DOC01:P002", "method_name": "...", "inputs": "...", "grouping": "...", "transformation": "...", "filtering_rule": "...", "output_semantics": "...", "fact_tag": "explicitly_stated", "quote": "..."}],
  "consensus_conflict_matrix": {"consensus": [], "partial_agreement": [], "conflicts": []},
  "quantitative_synthesis": {"pooled_estimates": []}
}
```

**Step 2 completion criterion**: `/app/work/matrix_draft.json` covers every effective document, all requested common fields plus the six comparability axes, functional method operations with three-state tags, the complete Citation Legend, and attributed conflicts.

### Step 3: Strict Cross-Paper Bleed & Pooling Validation
Run the validator on `/app/work/matrix_draft.json` before promoting outputs to `/app/output/`:
```bash
python3 scripts/comparison_matrix_builder.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --validate-matrix /app/work/matrix_draft.json
```
The validator enforces four blocking checks and exits with code `1` on any violation:

| Blocking Error Code | Trigger Condition | Required Fix |
| --- | --- | --- |
| `CROSS_PAPER_BLEED` / `ROW_CELL_DOC_MISMATCH` | Cell's `paragraph_id` belongs to a different `source_document_id`. | Re-anchor cell strictly to its own document's paragraph or mark `not_reported`. |
| `UNKNOWN_PARAGRAPH_ID` / `REPORTED_CELL_MISSING_PARAGRAPH_ID` | Reported cell lacks a valid `paragraph_id` from `corpus_paragraphs.json`. | Supply exact `paragraph_id` from Step 1 index. |
| `QUOTE_NOT_FOUND_IN_PARAGRAPH` | Cell or conflict `quote` is not a whitespace-normalized substring of the target paragraph. | Copy verbatim excerpt without paraphrasing or stitching across paragraphs. |
| `ILLEGAL_POOLING_DETECTED` | `quantitative_synthesis` pools `noncomparable_pairs`, `indeterminate_pairs`, `duplicate_superseded`, `out_of_scope_excluded`, or `< 2` docs. | Remove illegal pooled estimate; restrict pooling strictly to verified `comparable_subgroups`. |
| `CITATION_LEGEND_DRIFT` / `DANGLING_CITATION_NUMBER` | `[n]` citation number is missing, duplicated, or points to another document in `citation_legend`. | Restore exact `[1]..[N]` mapping from Step 1 `citation_legend`. |

Re-run Step 3 after fixes until `matrix_validation.ok == true` (exit code `0`).

### Step 4: Grounding Gate & Final Deliverable Audit
```bash
python3 scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py --dir /app/output --result-file result.json --require comparison_matrix.md --require comparability_audit.json
```
**Step 4 completion criterion**: Both scripts exit `0`; `/app/output/result.json`, `/app/output/comparison_matrix.md`, and `/app/output/comparability_audit.json` are non-empty, valid UTF-8/JSON, free of NaN/Inf or placeholder markers, and disclose every real gap honestly in `gaps[]`.

### Citation Legend & Multi-Source Traceability Protocol
When comparing 5 to 50+ papers, bare numeric brackets (`[1]`, `[14]`) drift easily across tables and narrative prose. Maintain one immutable `citation_legend[]` per run:
- Sort all inventoried inputs deterministically by `source_document_id` and bind `[1]..[N]` permanently to `(source_document_id, title, doi, source_file, file_sha256, version_status)`.
- Print the complete Citation Legend table at the top of `comparison_matrix.md` before any extracted values or synthesis prose.
- Keep `duplicate_superseded` and `out_of_scope_excluded` entries visible in the Citation Legend with explicit `version_status` rather than silently dropping or renumbering remaining papers.
- Pair every matrix cell, functional method row, and conflict quote with BOTH `[n]` and `(source_document_id, paragraph_id)` so any reviewer can audit cross-paper bleed in one pass.

## 4. Phased High-Fidelity Synthesis & Document Identity Rules
When handling 5 to 50+ papers or long multi-section PDFs, follow a three-phase fidelity sequence rather than one-shot summarization:
- **Phase A (Inventory & Identity Lock)**: Map every input file to its immutable Citation Legend `[n]`, SHA-256 hash, explicit header DOI, page count, and `version_status` (`version_of_record`, `preprint`, `duplicate_superseded`, `out_of_scope_excluded`).
- **Phase B (Cell Extraction & Method Operation Alignment)**: Read Methods, Tables, and Results paragraphs in `/app/work/corpus_paragraphs.json` per paper; extract common fields with verbatim quotes and align actual functional operations (`inputs`, `grouping`, `transformation`, `filtering_rule`, `output_semantics`) before touching conclusions.
- **Phase C (Comparability Gate & Conflict Synthesis)**: Run the six-axis comparability test first, restrict quantitative pooling strictly to verified cliques, and organize conclusions into `consensus`, `partial_agreement`, and `conflicts` with explicit dimension attribution.

### Standard Field Mapping (Research Paper Deconstruction Alignment)
Map unstructured paper sections to common comparison fields without losing granularity:
- **Literature Profile -> `citation_legend`**: year, venue, explicit header DOI, SHA-256 hash, version status (`version_of_record` vs. `preprint`).
- **Introduction & Study Object -> `task`, `material`, `pollutant`, `dataset`**: research question boundary, target analyte/dye/cohort, and baseline comparator (`baseline`).
- **Methods & Data Layer -> `functional_method_comparison` + `sample_size`, `budget`**: actual operations (`inputs`, `grouping`, `transformation`, `filtering_rule`, `output_semantics`), dosage/time/compute budget, and statistical unit.
- **Results & Quantitative Readout -> `metric`, `units`, `main_conclusion`**: primary quantitative readout with unmodified `raw_value` and `units` (preserving convertible scale advisories like `mg/L` vs. `ug/L` or `h` vs. `min` without silent rewriting).
- **Discussion & Code/Data Boundary -> `limitations`**: author-stated caveats, untested conditions, conflicting prior findings, and reproducibility/code availability status.

### Unit Normalization Advisory Policy (Never Silent Mutation)
- **Canonical Identity Aliases (Comparable)**: `%` == `percent`, `mg/g` == `mg g-1`, `mg/L` == `mg L-1`, `min` == `minutes`, `h` == `hours`.
- **Convertible Scale Mismatches (Blocked Until Explicit Conversion)**: `g/L` vs. `mg/L` (`1000x`), `mg/L` vs. `ug/L` (`1000x`), `mmol/L` vs. `umol/L` (`1000x`), `h` vs. `min` (`60x`), `min` vs. `s` (`60x`), `mg/L` vs. `ppm` (valid only in dilute aqueous media; requires matrix check). Always keep `raw_value` and `units` intact in cells and log the conversion factor in `unit_normalization_advisories[]`.
- **Incompatible Dimensions (Hard Noncomparable)**: e.g., `%` removal vs. `mg/g` adsorption capacity vs. `min^-1` kinetic rate constant.

| Input Document Pattern | Required Classification | Independent Study Count | Pooling Eligibility |
| --- | --- | --- | --- |
| Formal journal article (unique DOI/content) | `version_of_record` | Counted (`+1`) | Eligible if 6 axes align |
| Preprint paired with formal publication | `duplicate_superseded` (mapped in `version_mapping`) | Excluded (`0`) | Strictly blocked |
| Exact duplicate file (identical content hash) | `duplicate_superseded` | Excluded (`0`) | Strictly blocked |
| Same DOI, distinct explicit `experiment_arm` | Separate canonical rows + `CONTEXT_DISTINCT_SAME_DOI` | Preserved separately | Tested via 6-axis gate |
| Off-topic distractor (`0` scope overlap) | `out_of_scope_excluded` (logged in `excluded_documents`) | Excluded (`0`) | Strictly blocked |
| Scanned / image-only PDF (`no_text_layer`) | `version_of_record` + `PDF_TEXT_EXTRACTION_EMPTY` gap | Counted with `not_reported` axes | Blocked (`indeterminate_pairs`) |

## 5. Pre-Exit Adversarial Self-Audit (Six-Dimension Murphy's Law Check)
Before declaring completion, inspect the generated deliverables against six adversarial failure dimensions (report any unresolved blocker honestly in `gaps[]`):
1. **Workorder Completeness**: Every field listed in `/input/comparison-workorder.json` plus all six comparability axes `{task, metric, units, material, pollutant, budget}` appears in `comparison_matrix`.
2. **Anchor & Bleed Integrity**: Every reported cell's `(source_document_id, paragraph_id)` matches the owning document and its `quote` is a verified verbatim substring.
3. **Comparability Discipline**: Zero pairs from `noncomparable_pairs` or `indeterminate_pairs` appear inside any pooled estimate; unit normalization advisories preserve raw units unmodified.
4. **Functional Method Rigor**: Every effective document has a `functional_method_comparison` row describing actual operations with one valid `fact_tag` (`explicitly_stated`, `functionally_equivalent`, or `inferred_unverified`).
5. **Conflict Honesty**: Directional disagreements appear in `consensus_conflict_matrix.conflicts` with explicit attribution dimensions (`sample`, `method`, `condition`, `metric`, or `unattributable`) and verbatim quotes from every side—zero majority-vote flattening.
6. **Citation Legend & Grounding Closure**: Every `[n]` tag in Markdown and JSON resolves cleanly to `citation_legend`; `gap_gate.py` and `final_audit.py` exit `0`.

## 6. Edge-Case & Failure Handling
- **Single Valid Document**: If only 1 effective in-scope document remains after preprint deduplication and scope exclusion, set `multi_document_satisfied: false`, `pooling_allowed: false`, record `INSUFFICIENT_DOCUMENTS_FOR_COMPARISON` in `gaps[]`, and output the single-document extraction honestly without inventing a second study.
- **Image-Only or Unreadable PDF**: When `pdftotext` extracts empty text, record `extraction_status: "no_text_layer"` in `document_inventory`, log `PDF_TEXT_EXTRACTION_EMPTY` in `gaps[]`, and leave unextracted axes `not_reported` (blocking pooling).
- **Preprint + Formal Publication Pair**: Keep Version-of-Record as canonical, log the preprint in `dedup_and_scope_audit.version_mapping` with `alt_dois`, mark it `duplicate_superseded` in `citation_legend`, and exclude it from independent-study counts and pooling.
- **Same DOI, Distinct Experimental Arms**: When metadata specifies separate experimental arms (`experiment_arm` / `condition_key`), preserve both rows separately and record `CONTEXT_DISTINCT_SAME_DOI` in `context_conflicts`.
- **Unattributable Conflict**: When two studies match on all reported axes yet report opposite conclusions, record `attribution_dimensions: ["unattributable"]`, keep both verbatim anchored quotes, and list unreported methodological variables needed for resolution.
- **Missing `/input/comparison-workorder.json`**: Fall back to `case.json` fields or the default 11-field schema and log `MISSING_COMPARISON_WORKORDER` in `gaps[]`.
- **Execution Budget Discipline**: Write initial deliverables at Step 1 so partial progress is preserved early; reserve deep reading turns for ambiguous cells and conflicts, then run Step 3 + Step 4 before exiting.

## 7. Deliverable Checklist
Confirm all three artifacts under `/app/output/` before completing the task:
- `result.json`: includes `case_id`, `status`, `citation_legend`, `document_inventory`, `excluded_documents`, `common_fields`, `comparison_matrix`, `functional_method_comparison`, `comparability_assessment`, `consensus_conflict_matrix`, `dedup_and_scope_audit`, `matrix_validation`, `metrics`, `metric_provenance`, `findings`, and `gaps`.
- `comparison_matrix.md`: includes the Citation Legend table, Document Inventory & Exclusion log, Common-Field Comparison Matrix, Functional Method Operations table, Six-Axis Comparability Gate & Subgroups, and Three-Column Consensus/Partial/Conflict Attribution table.
- `comparability_audit.json`: standalone machine-auditable ledger of pairwise 6-axis checks, unit normalization advisories, clique subgroups, blocked pairs, and `--validate-matrix` status.
