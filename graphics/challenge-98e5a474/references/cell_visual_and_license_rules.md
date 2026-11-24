# Cell-Oriented Schematic Visual Grammar & Asset License Rules

## 1. Visual Hierarchy & Multi-Element Alignment
- White canvas (`#FFFFFF`) with restrained pastel container fills (`#EDF4F5` cool mist, `#F6F1E8` warm sand, `#EEF5F0` soft sage) reserved strictly for hierarchical/compartment grouping.
- Title (`15 pt` bold) and subtitle (`10.5 pt`) keep `>=26 px` vertical separation; container headers sit at `11 pt` bold; node labels at `10.5 pt` bold; bound numerical readouts at `9.2 pt`; edge annotations at `8.8 pt` bold shifted clear of vertical container dividers.
- Never leak backend processing tokens (`QC_*`, `post_basic`, `preselector`, `in-sample`, hex color codes) into visible diagram text; keep methodological caveats and statistical test definitions in external captions or `result.json`.

## 2. Draw.io Native Editability Contract
- Every node shape (`rect` or `ellipse`) carries `data-drawio-cell-id="node-<id>"`.
- Every single-run node label carries `data-drawio-cell-id="nodelabel-<id>"` (and optional `nodeval-<id>`).
- Every connector path carries `data-drawio-cell-id="edge-<id>"`, `data-source-node-id`, and `data-target-node-id`.
- Post-conversion `bind_drawio_graph()` embeds labels into native node `value` HTML and replaces floating `sourcePoint`/`targetPoint` coordinates with true `source`/`target` vertex bindings so moving any node keeps its label and connectors attached.

## 3. Asset & Icon License Provenance (DrawPlotia / OpenBioFigure Standard)
- Never assume repository-level licensing covers individual biological icons or motifs.
- Declare every reused graphic motif in `spec["assets"]` with `{id, author, license, source, version}` and tag using elements with `data-asset-id="<id>"`.
- Approved open scientific licenses: `CC0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `MIT`, `Apache-2.0`, `BSD-3-Clause`, `Public Domain`, `Self-Authored`.
- Any `All Rights Reserved`, `CC-BY-NC`, `Unknown`, missing attribution, or external unverified `href` fails `asset_license_audit.py`.

## 4. 10-Point Editable Vector QA Checklist (Pre-Delivery Visual Readback)
1. **Contours & curves**: No broken segments, stray spikes, or unintended overlapping strokes.
2. **Fills & whitespace**: Clean fills with purposeful whitespace; no accidental gaps or muddy blends.
3. **Repeated elements**: Equivalent nodes/swatches keep consistent dimensions, strokes, and typography.
4. **Live text & precision**: Every label stays selectable `<text>`/`<tspan>`; verify case, Greek letters, units, and sub/superscripts.
5. **Arrows & T-bars**: Clean orthogonal/anchored attachment; activation (`->`) vs inhibition (`-|`) polarity verified against source.
6. **Dashed lines**: Uniform dash rhythm (`5,3` / `6,4`) seamlessly meeting arrowheads.
7. **Container & boundary junctions**: Arrows attach cleanly to node boundaries without piercing interiors.
8. **Crossing continuity**: Feedback curves clear intermediate boxes cleanly along the top corridor.
9. **Frame & outer bounds**: Zero clipped labels or out-of-bounds shapes across SVG, Draw.io, PNG, and PDF.
10. **Single-source multi-format export**: After any SVG edit, regenerate `.drawio`, `.png`, and `.pdf` from the same final SVG and visually inspect both full-canvas and edited regions.

