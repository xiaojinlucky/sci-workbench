# Multi-Panel Figure Suite & PaperStory Cross-Modal Audit Protocol

Adapted from multi-panel scientific figure auditing (`audit-figure-suite`) and PaperStory backbone-and-branch reading:

## 1. PaperStory Backbone + Branch Architecture

- **Backbone**: Summarize the core scientific question and experimental finding supported by the supplied figure and literal tables.
- **Branches**: Attach each figure panel (`A`, `B`, `C`) and each literal table (`C01-P003`, `Table 1`, etc.) as an explicit auditable branch pointing back to exact `source_id`, `paragraph_id`, caption text, and pixel coordinates.
- **Unprovided Stubs**: Keep caption-only figures whose pixel files are absent as explicit `not_provided` stubs—never merge guessed content into the backbone narrative.

## 2. Five-Point Cross-Modal Integrity Audit

1. **Quantitative Authority Hierarchy**: Treat literal source tables and analytical outputs as quantitative authority; treat raster images/screenshots as visual evidence with `+/-0.5 px` digitization uncertainty.
2. **Panel-to-Caption Alignment**: Verify uppercase panel labels (`A`, `B`, `C`) match their caption descriptions and are not swapped across sub-panels.
3. **Unit & Magnitude Consistency**: Flag coexisting magnitude prefixes (e.g., `mV` in text vs `V` in table, `nm` vs `µm`, `MPa` vs `GPa`). Never treat an unlabeled numeric column or axis as dimensionless.
4. **Error-Bar & Sample-Size Audit**: Verify whether `SD`/`SEM`/`95% CI`/`IQR` and `n` are stated in the caption, figure, or table. If omitted everywhere, explicitly record `error_bar_semantics: unreported_in_source`.
5. **Conflict Preservation**: If a figure's visible value conflicts with a literal source table or prose statement, report both verbatim with their exact anchors rather than silently overwriting either source.
