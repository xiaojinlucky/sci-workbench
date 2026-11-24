---
name: scientific-schematic-generator-editor
description: >-
  Use when generating or performing source-preserving edits on scientific
  relationship, hierarchy, workflow, or mechanism schematics from source texts
  and data tables. Enforces source-bound entity/relation extraction, Cell-style
  visual grammar, Draw.io native node-label and edge binding, asset license
  auditing, librsvg vector PDF/PNG export, and undo/redo-verified incremental
  edits under /app/output.
---

# Scientific Schematic Generator & Source-Preserving Editor

Skill resources live at `/skills/weekly-skill`. Case inputs live at `/input/case.json`, `/input/task.md`, and attached source texts, data tables, or existing `.svg` figures. Store intermediates in `/app/work` and final deliverables in `/app/output`.

## Scope & Core Scientific Contract

- **In Scope**: Scientific workflows, conceptual hierarchies, experimental/mechanistic schematics, data-table-bound legend/node updates, layered editable SVG + native Draw.io generation, source-preserving SVG edits (including leaf `<tspan>` editing and edge-preserving node movement), asset license auditing, and publication-compliant PDF/PNG export.
- **Out of Scope**: Statistical hypothesis testing or regression modeling, decorative poster art, or bitmap-traced illustrations.
- **Three Verifiable Truth Gates**:
  1. **Source Provenance**: Every entity, hierarchy container, relation polarity (`activate`/`inhibit`/`causal`/`parallel`/`feedback`/`uncertain`), value, and unit must carry `source_ref` traceable to `/input/`. Never hallucinate genes, doses, timepoints, or legend classes; record missing facts via `gap_gate.py`.
  2. **Data-to-Legend Consistency**: Categories, colors, and displayed values in `schematic_figure.svg` must stay synchronized with the bound CSV/JSON table (`--data-table` / `--rebind-table`). Mismatched legend colors or backend debug labels (`QC_*`, `post_basic`, raw hex strings) fail the build.
  3. **True Vector Editability & Asset Compliance**: Deliver layered `<g id="layer-*">` SVG with live `<text>`, native Draw.io `.drawio` with **integrated node labels and `source`/`target`-bound edges** (dragging a node moves its label and re-routes all connected edges), librsvg searchable vector PDF + publication-grade `300 dpi` PNG previews (or `600 dpi` line-art via `--preset lineart_600dpi` from `references/export_presets.json`), font/CJK portability audit (`portable_svg_audit.py`), and an audited `*_license_manifest.json` confirming every declared icon/asset uses an open scientific license (`CC0`, `CC-BY-4.0`, `MIT`, `Apache-2.0`, `Self-Authored`).

## Visual & Typographic Baseline (Cell-Oriented Offline Standard)

Read `references/schematic_grammar.md`, `references/source_preserving_edit_protocol.md`, and `references/cell_visual_and_license_rules.md` before authoring specs:
- **Canvas & Typography**: Pure white canvas (`#FFFFFF`), Arial / Liberation Sans / DejaVu Sans, crisp hierarchy (`15 pt` title, `10.5 pt` subtitle separated by `>=26 px`, `11 pt` bold container headers, `10.5 pt` node labels, `9.2 pt` bound values, `8.8 pt` edge labels). Zero shadows, zero 3D bevels, zero gradients.
- **Color Semantics**: Hierarchical restrained Cell/Okabe-Ito tokens (`#547FA3` steel blue, `#66A9B4` cyan, `#4E9187` teal, `#83A47B` sage, `#D19A45` ochre, `#D77C70` coral highlight, `#967A9C` muted purple, `#737373` uncertain/NA). Keep colors bound to semantic category keys, never reshuffled by row order.
- **Edge Grammar**: Activation/causal = solid arrow (`#arrow-activate`); Inhibition = flat T-bar (`#arrow-inhibit`, never pointed); Parallel/binding = dashed circle-end (`#arrow-bind`); Feedback = curved top-corridor dashed arrow (`#arrow-feedback`); Uncertain/ungrounded = dashed open arrow (`#arrow-open`).

## Step-by-Step Execution Pipeline

```bash
SCRIPTS=/skills/weekly-skill/scripts

# Step 1: Probe offline Linux environment, init budget (64 req / 900s) & result.json early
python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "<TASK_ID>"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Step 2a: Deterministically extract hierarchical containers, entities, values & typed relations directly from raw /input
python3 "$SCRIPTS/extract_schematic_spec.py" --input-dir /input --out /app/work/schematic_spec.json

# Step 2b (Mode A — Generate New Schematic with 300 dpi default or --preset lineart_600dpi + auto-seal):
python3 "$SCRIPTS/schematic_spec_builder.py" \
  --spec /app/work/schematic_spec.json \
  --data-table /input/data.csv \
  --preset cell_300dpi \
  --case-json /input/case.json \
  --out-dir /app/output \
  --stem schematic_figure

# Step 2b (Mode B — Source-Preserving Incremental Edit):
# Always inspect existing IDs first; never guess IDs or blindly overwrite multi-run <text> elements!
python3 "$SCRIPTS/source_preserving_svg_editor.py" --svg /input/<existing>.svg --list-ids
python3 "$SCRIPTS/source_preserving_svg_editor.py" \
  --svg /input/<existing>.svg \
  --patch /app/work/patch.json \
  --rebind-table /input/updated.csv \
  --history-dir /app/work/edit_history \
  --out-dir /app/output \
  --stem schematic_edited

# Step 3: Independent Palette WCAG/CVD Audit, Asset License Check & Final Harbor Gate
python3 "$SCRIPTS/check_palette.py" "#547FA3,#66A9B4,#4E9187,#D77C70,#D19A45" --role categorical --mark bar
python3 "$SCRIPTS/asset_license_audit.py" --spec /app/work/schematic_spec.json --svg /app/output/schematic_figure.svg --out /app/output/schematic_figure_license_manifest.json
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --patch-json '{"artifacts":["schematic_figure.svg","schematic_figure.drawio","schematic_figure.png","schematic_figure.pdf","schematic_figure_license_manifest.json"]}' --finalize
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --require schematic_figure.svg --require schematic_figure.drawio --require schematic_figure.png --require schematic_figure.pdf
```

## Source-Preserving Edit Protocol & Failure Handling

| Failure / Edge Case | Required Action & Guard Behavior |
| --- | --- |
| Multi-style `<text>` with multiple `<tspan>` runs | `source_preserving_svg_editor.py` refuses ambiguous whole-node replacement (`INVALID_SVG_EDIT`, exit 2). Target the exact leaf `<tspan id="...">` so inline bold, italic, color, and spaces survive intact in both SVG and librsvg PDF. |
| Moving a node (`move_node`) | Translates node shape + label + value AND automatically re-routes attached SVG edges (`data-source-node-id`/`data-target-node-id`), then re-binds native Draw.io `source`/`target` cells. |
| Save / Reopen / Undo / Redo stability | Pass `--history-dir` to persist snapshots and verify byte-exact SHA-256 restore on undo and redo (`edit_history.json`). |
| Unlicensed / unattributed asset or icon | `asset_license_audit.py` blocks `All Rights Reserved`, `Unknown`, `CC-BY-NC`, or missing attribution before delivery (`ASSET_LICENSE_*`). |
| Missing `rsvg-convert` (librsvg) | `render_preview.py` falls back automatically to `resvg_py` or Matplotlib vector mirror (`matplotlib` optional) and emits an explicit `MATPLOTLIB_TSPAN_DEGRADED` warning inside `degraded_warnings` whenever multi-`<tspan>` inline styles are rendered via Matplotlib fallback; `result.json` records the exact backend and warning honestly. |
| Missing/contradictory input facts | Do not fabricate values; log explicit gaps in `/app/work/gaps.json` and set `result.json` status to `partial_with_gaps`. |
