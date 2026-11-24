# Biological Pathway Semantics, Nomenclature & Diverging Data Encoding

## 1. Entity Nomenclature & Typography (HGNC / MGI Standards)

| Entity Class | Species Convention | SVG / Draw.io Typography | Default Shape & Fill |
| --- | --- | --- | --- |
| **Human Gene** | ALL CAPS (`TP53`, `SERPINE1`, `CDKN1A`) | **Italic** (`font-style="italic"` / `<i><b>...</b></i>`) | Sharp `rect`, white fill `#FFFFFF`, steel-blue stroke `#547FA3` |
| **Mouse Gene** | Title Case (`Trp53`, `Serpine1`, `Cdkn1a`) | **Italic** (`font-style="italic"` / `<i><b>...</b></i>`) | Sharp `rect`, white fill `#FFFFFF`, steel-blue stroke `#547FA3` |
| **Protein / Kinase / TF** | ALL CAPS (`TP53`, `SMAD2/3`, `TGFBR1/2`) | **Upright** bold (`<b>...</b>`) | Rounded `rect` (`rx=8`), cyan `#66A9B4` or steel-blue `#547FA3` |
| **Protein Complex** | Slash/colon members (`SMAD2/3/4`) | **Upright** bold | Rounded `rect`, teal `#4E9187` |
| **Ligand / Metabolite** | Standard symbol (`TGFB1`, `ATP`, `ROS`) | **Upright** bold | `ellipse`, sage `#83A47B` (ligand) / ochre `#D19A45` (metabolite) |
| **Gene & Protein Homonym** | E.g. gene *TP53* + protein TP53 in same pathway | **Strictly distinct**: italic *TP53* (nuclear gene) vs upright TP53 (protein); never merge IDs | Separate node IDs (`tp53_gene`, `tp53_protein`), explicit `entity_type` |

## 2. Interaction Polarity Visual Grammar (SBGN-Aligned)

| Polarity (`edge.polarity`) | Biological Meaning | SVG Marker & Stroke | Draw.io `endArrow` | Stroke Pattern |
| --- | --- | --- | --- | --- |
| `activate` | Activation, phosphorylation, stimulation | `#arrow-activate` filled triangle `#2E6B8E` | `classic` | Solid |
| `inhibit` | Inhibition, repression, blockade | `#arrow-inhibit` flat T-bar `#B85F56` | `dash` (`endFill=0`) | Solid |
| `bind` | Physical binding, dimerization, complex assembly | `#arrow-bind` solid circle `#4E9187` | `oval` | Solid |
| `modify` | Covalent PTM (`+P`, `+Ub`, `+Ac`) | `#arrow-activate` `#D19A45` + circular badge | `classic` | Solid |
| `transcribe` | Transcriptional regulation of target gene | `#arrow-transcribe` `#967A9C` | `classic` + `dashed=1` | Dashed `4,3` |
| `transport` / `secrete` | Translocation across compartments / export | `#arrow-transport` open arrowhead `#547FA3` | `open` + `dashed=1` | Dashed `6,4` |
| `uncertain` (`uncertain: true`) | Putative / unverified interaction without `source_ref` | `#arrow-transport` open arrowhead `#547FA3` | `open` + `dashed=1` | Dashed `6,4` |

**Hard Provenance Rule**: Any edge without `source_ref` MUST set `uncertain: true` and render dashed with an open arrowhead; otherwise `pathway_topology_linter.py` blocks the build.

## 3. Diverging Expression Overlay (`--data-table`) & Conflict Detection
- Continuous fold-change (`log2FC`, z-score) uses the Cell-standard diverging scale `#3F6FAE` (down) -> `#F7F7F5` (explicit `0.0` center) -> `#C6544D` (up) with symmetric clamp disclosure (`[-clamp, +clamp]`).
- **NA vs Explicit Zero Separation**: Missing/unmeasured entries (`NA`, `ND`, blank) render in distinct neutral dark gray `#737373` with a separate `NA (not measured)` legend swatch, never collapsed into `0.0`.
- **Source-Data vs Diagram Conflict Check**: If an attached CSV/JSON table contains entity IDs absent from the pathway spec, or if an edge asserts `expected_direction: "up"` (`"down"`) on a target whose measured `log2FC` has the opposite sign beyond `|0.25|`, `pathway_topology_linter.py` flags `DATA_DIAGRAM_CONFLICT` cleanly in `warnings`/`blocking_errors` so scientific contradictions are never hidden.

## 4. 10-Point Vector Pathway QA Checklist
1. Contours clean with zero broken or jagged lines; 2. Fills crisp without gaps or gradients; 3. Repeated shapes consistent; 4. Selectable `<text>` with verified italic genes vs upright proteins and Greek/PTM labels; 5. Arrow vs T-bar polarity verified 1:1 against source; 6. Uniform dash patterns; 7. Transmembrane receptors span plasma membrane cleanly without piercing lipid heads awkwardly; 8. Upward feedback loops route cleanly along the right outer corridor and non-adjacent nuclear fan-out edges arc cleanly below the gene row; 9. Zero clipped text or Draw.io overlaps; 10. All formats (`.svg`, `.drawio`, `.png`, `.pdf`) regenerated from the single final SVG and visually read back.
