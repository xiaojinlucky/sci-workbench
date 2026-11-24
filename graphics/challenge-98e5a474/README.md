# Scientific Schematic Generator & Source-Preserving Editor

Offline-ready scientific schematic Skill for generating and incrementally editing layered vector SVG, native node/edge-bound `.drawio`, searchable librsvg PDF, and publication-grade `300 dpi` / `600 dpi` PNG previews from primary scientific texts and data tables.

## Key Capabilities

1. **Source-Bound Entity & Relation Schematic Builder (`scripts/schematic_spec_builder.py`)**
   - Translates source-grounded entities, hierarchical containers, typed relations (`activate`, `inhibit`, `causal`, `parallel`, `feedback`, `uncertain`), and CSV/JSON data tables into Cell-styled layered SVGs (`layer-background`, `layer-containers`, `layer-edges`, `layer-nodes`, `layer-legend`).
   - Enforces data-to-legend color consistency, zero backend-term leakage, and vertical title/subtitle separation.
2. **Native Draw.io Graph Binding (`scripts/drawio_graph_binding.py`)**
   - Preserves explicit `data-drawio-cell-id` attributes through `svg2drawio`, embeds node labels + bound values directly into native Draw.io shape `value` HTML, and binds all connectors via true `source`/`target` vertex references (verifying node-move persistence).
3. **Source-Preserving Incremental Editor (`scripts/source_preserving_svg_editor.py`)**
   - Edits text labels (targeting leaf `<tspan>` IDs while refusing ambiguous multi-run overwrites), updates attributes, translates nodes (`move_node`) with **automatic SVG edge re-routing**, rebinds updated CSV/JSON tables with **legend swatch synchronization**, and verifies **save / reopen / undo / redo** byte-exact SHA-256 round-trips (`--history-dir`).
4. **Asset & Icon License Provenance Gate (`scripts/asset_license_audit.py`)**
   - Audits declared graphic assets against approved open scientific licenses (`CC0`, `CC-BY-4.0`, `MIT`, `Apache-2.0`, `Self-Authored`) and blocks unattributed or restricted assets before delivery.
5. **Librsvg Vector PDF + PNG Preview (`scripts/render_preview.py`)**
   - Uses `rsvg-convert` when available to preserve inline `<tspan>` bold/italic/color/whitespace in searchable single-page vector PDFs and 220+ dpi PNGs, with deterministic Matplotlib vector-mirror fallback.

## Quick Example (Synthetic Scientific Workflow)

```bash
python3 scripts/schematic_spec_builder.py \
  --spec examples/synthetic_schematic_spec.json \
  --data-table examples/synthetic_table.csv \
  --out-dir /app/output \
  --stem crispr_rnp_schematic
```

See `SKILL.md` for the full Harbor `/input` -> `/app/work` -> `/app/output` workflow and `references/` for visual grammar, editing rules, and license policies.
