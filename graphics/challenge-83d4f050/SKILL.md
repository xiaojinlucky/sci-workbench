---
name: bio-pathway-mechanism-drawer
description: >-
  Use when creating, auditing, or source-preserving editing biological mechanism
  and signaling pathway figures from primary biological text or expression
  tables. Enforces HGNC/MGI gene-vs-protein nomenclature, compartment and
  transmembrane topology, strict activation/inhibition polarity, diverging
  expression encoding with explicit NA vs 0.0 separation, native Draw.io
  node-label and polarity-typed edge binding, and librsvg vector PDF/PNG
  deliverables under /app/output.
---

# Biological Mechanism & Pathway Drawer

Skill resources are installed under `/skills/weekly-skill`. Original case inputs live at `/input/case.json`, `/input/task.md`, and attached biological source texts or expression tables. Store intermediates under `/app/work` and final deliverables under `/app/output`.

## Hard Biological & Vector Integrity Rules

1. **Source-Bound Provenance Only (Zero Invented Edges)**:
   - Read `/input/` files before drafting `pathway_spec.json`. Every entity, modification, compartment, expression value, and regulatory edge must trace to `source_ref`.
   - Any edge lacking `source_ref` MUST be flagged `uncertain: true` (rendered dashed with an open arrowhead and documented in the legend); otherwise `pathway_topology_linter.py` blocks the build rather than drawing an unverified solid claim.
2. **Nomenclature & Subcellular Compartment Topology (`pathway_topology_linter.py`)**:
   - **Gene vs Protein Typography**: Target genes render in **italic** (`font-style="italic"` in SVG, `<i><b>...</b></i>` in Draw.io); proteins/complexes render **upright**. Human genes follow uppercase HGNC (*SERPINE1*, *CDKN1A*); mouse genes follow title-case MGI (*Serpine1*); alias/synonym collisions are flagged explicitly.
   - **Compartment Polarity**: Extracellular ligands sit above the plasma membrane; transmembrane receptors span the plasma membrane (blocked in nucleus/extracellular); cytosolic kinases/adaptors reside in cytoplasm/organelles; transcription factors and target genes reside in the nucleus. Direct extracellular -> nuclear edges without a transmembrane receptor or `transport` polarity are blocked as compartment teleportation.
3. **Strict Interaction Polarity & Feedback Routing**:
   - Activation (`->`): solid `#2E6B8E`, `#arrow-activate` (Draw.io `endArrow=classic`)
   - Inhibition (`-|`): solid `#B85F56`, flat T-bar `#arrow-inhibit` (Draw.io `endArrow=dash`, never pointed)
   - Binding / Complex: solid `#4E9187`, `#arrow-bind` (Draw.io `endArrow=oval`)
   - Transcription: dashed `#967A9C`, `#arrow-transcribe`
   - Transport / Translocation / Uncertain: dashed `#547FA3`, open `#arrow-transport` (`endArrow=open;dashed=1`)
   - **Feedback Loops**: Upward negative/positive feedback (e.g., nuclear *SMAD7* -> membrane `TGFBR1`) routes cleanly along the right outer corridor without cutting across cytosolic proteins.
4. **Diverging Expression Encoding & Conflict Audit (`--data-table`)**:
   - Continuous `log2FC` tables map to the Cell-standard diverging blue–white–red scale (`#3F6FAE -> #F7F7F5 -> #C6544D`) with explicit `center = 0.0`, symmetric clamp disclosure, and an inline legend colorbar.
   - Strictly separate `NA` (`#737373`, labeled `NA (not measured)`) from explicit numeric `0.0` (`#F7F7F5`). Never leak backend processing tokens (`QC_*`, `post_basic`, raw hex codes) onto visible labels.
   - **Source-Data vs Diagram Conflict Detection (`DATA_DIAGRAM_CONFLICT`)**: Unmapped data-table entity IDs or measured `log2FC` signs contradicting an edge's `expected_direction` (`"up"`/`"down"`) are flagged explicitly in `data_conflicts` and `gaps`—never silently hidden or overwritten.
5. **Native Draw.io Binding + Librsvg 300 DPI Vector PDF/PNG + Source-Preserving Edits**:
   - `drawio_pathway_binding.py` integrates entity names (preserving gene italics `<i><b>...</b></i>` vs upright protein labels even for homonyms such as *TP53* gene and TP53 protein) + expression values into native Draw.io shapes and binds all edges via `source`/`target` with preserved biological `endArrow` styles.
   - `render_preview.py` uses `rsvg-convert` (librsvg) to preserve italic gene names and vector sharpness in searchable PDFs and `300 dpi` PNGs by default (with automatic Matplotlib fallback and explicit degraded warnings).
   - `--edit-svg` supports `update_text`, `update_attrs`, and `move_node` (with automatic SVG edge re-routing and Draw.io re-binding) at `preserved_id_ratio == 1.0`.

## Standard Pipeline

```bash
SCRIPTS=/skills/weekly-skill/scripts

# Step 1: Probe sandbox, initialize budget (64 req / 900s) & result.json, index inputs
python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "<TASK_ID>"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Step 2: Lint nomenclature, compartment polarity, and edge provenance before rendering
python3 "$SCRIPTS/pathway_topology_linter.py" --spec /app/work/pathway_spec.json --out /app/work/lint_pre.json

# Step 3: Build layered SVG + native Draw.io + 300 dpi librsvg PNG/PDF (+ optional expression CSV/JSON + auto-seal)
python3 "$SCRIPTS/bio_pathway_builder.py" \
  --spec /app/work/pathway_spec.json \
  --data-table /input/expression_log2fc.csv \
  --dpi 300 \
  --case-json /input/case.json \
  --out-dir /app/output \
  --stem mechanism_pathway

# Step 3b (Optional — Source-Preserving Edit: inspect IDs first, then apply patch)
python3 "$SCRIPTS/bio_pathway_builder.py" --edit-svg /input/<existing>.svg --list-ids
python3 "$SCRIPTS/bio_pathway_builder.py" \
  --edit-svg /input/<existing>.svg \
  --patch /app/work/patch.json \
  --spec /app/work/pathway_spec.json \
  --dpi 300 \
  --out-dir /app/output \
  --stem mechanism_pathway_edited

# Step 4: Seal result.json preserving original output_contract fields and run final_audit
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --patch-json '{"artifacts":["mechanism_pathway.svg","mechanism_pathway.drawio","mechanism_pathway.png","mechanism_pathway.pdf"]}' --finalize
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --require mechanism_pathway.svg --require mechanism_pathway.drawio --require mechanism_pathway.png --require mechanism_pathway.pdf
```

## Fallback & Error Handling

- Missing `source_ref` on solid edges -> blocked at Step 2 (`lint_pre.json`); mark `uncertain: true` and log gap via `gap_gate.py`.
- Transmembrane receptor in nucleus or extracellular -> nuclear teleportation -> blocked at Step 2.
- Missing `rsvg-convert` -> `render_preview.py` falls back to `resvg_py` or Matplotlib vector mirror and records the actual backend honestly.
