# Subcellular Compartment Topology, Nomenclature & Polarity Rules

## 1. Vertical Subcellular Layer Ordering (Top-to-Bottom)

1. **`extracellular`** (`rank = 0`): Secreted ligands, cytokines, growth factors, antibodies, exosomes, ECM molecules.
2. **`plasma_membrane` / `membrane`** (`rank = 1`): Lipid bilayer band; transmembrane receptors (`role="transmembrane_receptor"`), ion channels, transporters, integrins.
3. **`cytoplasm` / `cytosol` & organelles** (`rank = 2`–`3`, e.g., `mitochondria`, `er`, `golgi`, `lysosome`, `endosome`): Kinases, adaptors, scaffolds, R-SMADs, second messengers, cytosolic complexes.
4. **`nucleus`** (`rank = 4`): Double-dashed nuclear envelope; transcription factors (`role="transcription_factor"`), chromatin regulators, and target genes (`entity_type="gene"`).

## 2. Hard Topological & Biological Blocking Invariants

- **No Transmembrane Receptors in Nucleus/Extracellular**: `role == "transmembrane_receptor"` placed outside membrane compartments is blocked pre-render.
- **No Extranuclear Target Genes**: `entity_type == "gene"` placed in `extracellular` or `plasma_membrane` is blocked pre-render.
- **No Compartment Teleportation**: An edge from `extracellular` directly to `nucleus` with non-`transport` polarity (e.g., `activate`/`inhibit`/`modify`) is blocked; signal must cross `plasma_membrane` via a receptor or explicit transmembrane transport step.
- **Zero Unsupported Solid Claims**: Every solid regulatory edge requires `source_ref` traceable to `/input/`. Speculative or inferred edges must set `uncertain: true`, render dashed with open arrowhead `#arrow-transport`, and be disclosed in `gaps`.

## 3. Homonym Gene-vs-Protein Disambiguation & HGNC/MGI Casing

- When a pathway includes BOTH a protein (e.g., `TP53` protein in cytoplasm/nucleus) and its encoding gene (*TP53* gene in nucleus):
  - Assign distinct semantic IDs (`tp53_protein` vs `tp53_gene`) and distinct `entity_type` (`"protein"` vs `"gene"`).
  - Render `gene` in **italic** (`font-style="italic"` in SVG, `<i><b>...</b></i>` in Draw.io) and `protein` **upright**.
  - Human genes: uppercase italic (`*TP53*`, `*SERPINE1*`, `*CDKN1A*`); Mouse genes: title-case italic (`*Trp53*`, `*Serpine1*`).

## 4. Five SBGN-Aligned Interaction Polarities

| Polarity | Line Style | SVG Marker | Draw.io `endArrow` | Color | Meaning |
| --- | --- | --- | --- | --- | --- |
| `activate` / `modify` | Solid 2.0 px | `#arrow-activate` | `classic` | `#2E6B8E` | Activation / phosphorylation / modification |
| `inhibit` | Solid 2.0 px | `#arrow-inhibit` (flat T-bar) | `dash` | `#B85F56` | Repression / inhibition / antagonism |
| `bind` | Solid 2.0 px | `#arrow-bind` (circle end) | `oval` | `#4E9187` | Complex formation / physical binding |
| `transcribe` | Dashed `4,3` | `#arrow-transcribe` | `classic;dashed=1` | `#967A9C` | Transcriptional regulation of target gene |
| `transport` / `secrete` / `uncertain` | Dashed `5,3` | `#arrow-transport` (open head) | `open;dashed=1` | `#547FA3` | Translocation, secretion, or uncertain edge |

## 5. Source-Data vs Diagram Conflict Detection (`DATA_DIAGRAM_CONFLICT`)

- When `--data-table` supplies measured quantitative values (e.g., `log2FC`), `pathway_topology_linter.py` cross-checks:
  1. Every entity ID in the data table against declared `entities` (unmapped IDs logged as `DATA_DIAGRAM_CONFLICT`).
  2. Any edge declaring `expected_direction` (`"up"` or `"down"`) against the target node's measured `log2FC` (contradictory sign `|log2FC| > 0.25` logged explicitly as `DATA_DIAGRAM_CONFLICT` in `warnings` and `gaps`—never silently overwritten).
- Preserve strict visual separation between explicit numeric `0.0` (`#F7F7F5`) and missing `NA` (`#737373`, labeled `NA (not measured)`).
