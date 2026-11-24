---
name: apparatus-and-technical-route-builder
description: >-
  Use when designing, auditing, or source-preserving editing experimental
  apparatus schematics, physical setup block diagrams, or multi-stage technical
  roadmaps from experimental protocols or system specs. Enforces component/port
  and operating-parameter grounding, inlet-to-outlet forward reachability,
  stream-type visual encoding, native Draw.io unit+label+spec and edge binding,
  parameterized standalone TikZ (.tex + .meta.json), and librsvg PNG/PDF
  deliverables under /app/output.
---

# Experimental Apparatus & Technical Route Builder

Skill resources are installed at `/skills/weekly-skill`. Case inputs reside under `/input/` (`case.json`, `task.md`, experimental SOPs, equipment parameter tables, or existing SVG/TeX sources). Store intermediates in `/app/work` and final deliverables in `/app/output`.

## Core Engineering & Scientific Rules

1. **Primary-Source Apparatus, Port & Operating-Parameter Grounding**:
   - Every unit, port (`inlet`/`outlet`/`vent`/`sensor`/`power`), valve, optical element, or roadmap milestone must trace to `source_ref` in `/input/`.
   - Never fabricate unspecified operating parameters (temperature, pressure, flow rate, wavelength, duration). Declare them in `missing_params` (or mark unresolved placeholders cleanly) so `apparatus_route_linter.py` logs structured `MISSING_OPERATING_PARAM` gaps into `/app/work/gaps.json` and `result.json`.
   - Zero backend/internal tokens (`QC_*`, `post_basic`, raw hex codes) may leak onto visible labels.
2. **Flow, Step & Topology Soundness (`apparatus_route_linter.py`)**:
   - **Primary Forward Reachability**: Verify directed forward path existence from declared `inlet`/`source` units to `outlet`/`target` units with zero orphan units. Return `feedback`/`recycle` loops do not substitute for a missing forward path (an explicit forward downstream recovery stream is counted only when `is_primary_forward: true` and `stage_order(target) > stage_order(source)`).
   - **Stage & Step Monotonicity**: Enforce non-decreasing `stage_order` and `step_number` along primary forward routes; backward connections must be explicitly typed `"feedback"` or `"recycle"`.
   - **Port Consistency**: If a stream declares `source_port`/`target_port`, verify both exist on their respective unit's `ports` list.
   - **Stream Visual Grammar**: `material`/`fluid` solid blue `#2E6B8E`, `optical`/`laser` solid amber `#D19A45`, `electrical`/`signal`/`data` dashed teal `#4E9187`, `control` dotted purple `#967A9C`, `waste`/`exhaust` dashed gray `#737373`, `feedback`/`recycle` curved dashed coral `#D77C70`.
3. **Multi-Format Vector Editability + Native Draw.io Binding + TikZ `edit_contract`**:
   - Emit layered native SVG (`layer-background`, `layer-zones`, `layer-streams`, `layer-units`, `layer-ports`, `layer-legend`) with `>=28 px` title/subtitle separation and zone-divider label clearance (zero overlaps).
   - **Native Draw.io Binding (`drawio_stream_binding.py`)**: Integrates BOTH `unitlabel-<id>` AND `unitspec-<id>` into each native Draw.io unit cell (`value` HTML) and binds every `stream-<id>` via explicit `source`/`target` vertex IDs (stripping floating coordinates). Dragging any unit keeps its label, operating spec, and all attached streams connected.
   - **Librsvg Vector PDF + 300 DPI PNG (`render_preview.py`)**: Uses `rsvg-convert` for crisp searchable vector PDFs and publication-grade `300 dpi` PNGs by default (`--dpi 300` / `--dpi 600` for line-art, with automatic Matplotlib fallback and explicit `MATPLOTLIB_TSPAN_DEGRADED` warning if multi-`<tspan>` styling degrades).
   - **Parameterized Standalone TikZ (`.tex` + `.meta.json`)**: Generated at runtime in `/app/output` from `references/tikz_template_spec.json` (zero static `.tex` files shipped in the skill package), using `\def` layout dimensions, named Cell/Okabe-Ito `\definecolor` tokens, `/.style` presets, and multi-column positioning verified by `validate_tikz_contract.py` (compiles via `pdflatex`/`xelatex` when TeX Live is present, cleanly degrades otherwise).
   - **Font & CJK Portability Audit (`portable_svg_audit.py`)**: Detects non-ASCII/CJK labels and emits explicit `CJK_FONT_FALLBACK_WARNING` when Latin-only font stacks lack `Noto Sans CJK` / `Source Han Sans` fallback.
4. **Source-Preserving Edits (`--edit-svg` & `--edit-tex`)**:
   - Supports `update_text`, `update_attrs`, `move_node` (with automatic SVG stream endpoint re-routing and Draw.io re-binding), and TikZ `\def` parameter patching while preserving `preserved_id_ratio == 1.0`.
5. **10-Point Vector QA & Budget Discipline (64 req / 900s)**:
   - Initialize `result.json` + `.budget_state.json` early, visually inspect rendered PNGs (checking contours, live text, arrows, zone borders, and single-source re-export consistency), and seal via `final_audit.py`.

## Standard Pipeline

```bash
SCRIPTS=/skills/weekly-skill/scripts

# Step 1: Probe sandbox, initialize budget ledger & result.json, index inputs
python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "<TASK_ID>"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Step 2a: Deterministically extract zones, apparatus units, operating parameters, ports & missing_params from raw /input
python3 "$SCRIPTS/extract_route_spec.py" --input-dir /input --out /app/work/route_spec.json

# Step 2b: Lint topology, ports, step order & operating parameter gaps before rendering
python3 "$SCRIPTS/apparatus_route_linter.py" --spec /app/work/route_spec.json --out /app/work/lint_pre.json

# Step 3: Build layered SVG, native bound .drawio, parameterized TikZ (.tex + .meta.json), 300 dpi PNG, PDF, and auto-seal result.json
python3 "$SCRIPTS/apparatus_route_builder.py" --spec /app/work/route_spec.json --out-dir /app/output --stem apparatus_route --dpi 300 --case-json /input/case.json

# Step 3b (Optional — Source-Preserving Edit on existing SVG/TikZ; always inspect IDs first)
python3 "$SCRIPTS/apparatus_route_builder.py" --edit-svg /input/diagram.svg --list-ids
python3 "$SCRIPTS/apparatus_route_builder.py" \
  --edit-svg /input/diagram.svg \
  --edit-tex /input/diagram.tex \
  --patch /app/work/edit_patch.json \
  --out-dir /app/output \
  --stem apparatus_route_edited

# Step 4: Seal result.json preserving original output_contract fields and run final_audit
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --patch-json '{"artifacts":["apparatus_route.svg","apparatus_route.drawio","apparatus_route.png","apparatus_route.pdf","apparatus_route.tex","apparatus_route.meta.json"]}' --finalize
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --require apparatus_route.svg --require apparatus_route.drawio --require apparatus_route.png --require apparatus_route.pdf --require apparatus_route.tex --require apparatus_route.meta.json
```

## Error Handling & Degraded Modes

- **Unreachable Outlet / Feedback-Only Loop**: Blocked at Step 2 (`lint_pre.json`) before rendering.
- **Port Mismatch / Step Inversion**: Blocked with exact unit/stream IDs.
- **Missing Operating Parameter**: Logged as `MISSING_OPERATING_PARAM` in `gaps.json` and preserved honestly in `result.json` (`partial_with_gaps`) without inventing numbers.
- **Missing `pdflatex` or `rsvg-convert`**: SVG + `.drawio` + `.tex` + `.meta.json` + fallback vector PDF/PNG still succeed, with actual tool state recorded in JSON output.
