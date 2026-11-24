# Functional Method Comparison, Citation Legend & Conflict Attribution Guide

## 1. Compare Actual Functional Operations, Not Just Method Names
Different papers frequently assign different names to identical computational or experimental workflows—or reuse the same method name while changing grouping, normalization, or filtering rules. Always decompose each paper's method into five concrete operational primitives:

| Primitive | Question Answered | Example |
| --- | --- | --- |
| `inputs` | What raw object or measurement enters the procedure? | Raw UMI counts; UV-Vis absorbance at 664 nm; FDA FAERS spontaneous reports |
| `grouping` | At what unit are observations aggregated or compared? | Summed per biological replicate (pseudobulk); per catalyst batch; per drug-event pair |
| `transformation` | What mathematical, chemical, or preprocessing step is applied? | TMM library-size normalization; dark adsorption equilibration 30 min; Empirical Bayes shrinkage |
| `filtering_rule` | What threshold or criterion retains/excludes candidates or samples? | `FDR < 0.05` and `|log2FC| >= 1`; `EB05 >= 2.0`; steady-state drift `< 5%` |
| `output_semantics` | What exact claim or quantity does the output represent? | Replicate-aware DE gene list; apparent first-order rate constant `k_app`; disproportionality signal requiring clinical review |

### Three-State Method Fact Tags
Assign exactly one `fact_tag` to every method comparison row and extracted cell:
- **`explicitly_stated`**: The paper directly specifies the method, parameter, or operation in Methods, Results, Table, or Figure caption.
- **`functionally_equivalent`**: Two papers use different method names (or a packaged tool vs. DIY basic steps), yet the source text directly proves that `inputs`, `grouping`, `transformation`, and `output_semantics` perform the same operational role. Cite both papers' exact paragraphs justifying equivalence.
- **`inferred_unverified`**: The operation is hypothesized from a figure pattern or downstream output without explicit Methods confirmation. Never upgrade `inferred_unverified` into a confirmed methodological consensus.

## 2. Citation Legend Discipline (Solving Multi-Source Number Drift)
When comparing 5 to 50+ sources, bare bracket numbers (`[1]`, `[2]`, `[14]`) lose traceability across sections and invite cross-paper bleed. Enforce a single immutable **Citation Legend** per run:
1. Assign `[1]..[N]` in deterministic sorted order over all inventoried input documents.
2. Bind each `[n]` permanently to `(source_document_id, title, doi, source_file, file_sha256, version_status)`.
3. Print the Citation Legend at the top of `comparison_matrix.md` before any matrix or synthesis prose.
4. Every cell, functional method row, and conflict position must cite BOTH `[n]` and `(source_document_id, paragraph_id)`.
5. Never reuse an index `[n]` for a different document across sections, and never omit excluded or superseded documents from the legend (keep their `version_status` transparent as `duplicate_superseded` or `out_of_scope_excluded`).

## 3. Consensus — Partial Agreement — Conflict Attribution Matrix
Never smooth over disagreements to produce a clean narrative, and **never resolve scientific conflicts by majority vote or naive averaging**. Partition cross-study findings into three distinct columns/buckets:

1. **Consensus (`consensus`)**:
   - Multiple independent in-scope documents report the same directional conclusion under compatible conditions and aligned metrics.
   - Cite every agreeing document's `(citation_tag, source_document_id, paragraph_id, quote)`.
2. **Partial Agreement (`partial_agreement`)**:
   - Papers agree on core direction or mechanism, but differ in magnitude, statistical significance, boundary conditions, or subgroup applicability (e.g., holds at acidic pH but weakens at neutral pH; holds for Method A on Dataset 1 only).
   - State both the shared core and the explicit divergence boundary.
3. **Conflict (`conflicts`)**:
   - Two or more studies report opposite conclusion directions (e.g., improvement vs. degradation/null effect) or incompatible effect magnitudes on the same stated question.
   - Preserve **all opposing positions** with verbatim `(citation_tag, source_document_id, paragraph_id, quote)`.
   - Attribute every conflict to one or more concrete differing dimensions:
     - `sample`: differing cohort, species, sample size (`n`), cell type, or baseline population.
     - `method`: differing algorithm, statistical unit (e.g., cell-level vs. replicate-level pseudobulk), assay protocol, or preprocessing.
     - `condition`: differing experimental parameters (pH, temperature, dosage, catalyst loading, irradiation time, budget, follow-up window).
     - `metric`: differing outcome definition, endpoint, or unit scale.
     - `unattributable`: all reported axes and conditions appear aligned, yet conclusions still conflict; explicitly list the unreported confounders required to resolve the discrepancy.

## 4. Anti-Bleed Verification Checklist
Before running `--validate-matrix`:
- Confirm every `paragraph_id` prefix or ownership matches the row's `source_document_id`.
- Confirm every `quote` is copied verbatim from that exact paragraph without paraphrasing or stitching sentences from two papers.
- Confirm no `noncomparable_pairs` or `indeterminate_pairs` appear together inside any `quantitative_synthesis` / `pooled_estimates` block.
