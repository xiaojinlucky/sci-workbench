---
name: graphical-abstract-narrative-composer
description: >-
  Use when designing, composing, auditing, or source-preserving editing a
  single-page scientific Graphical Abstract (GA) from manuscripts, mechanisms,
  and quantitative results. Enforces 4-stage narrative flow (Question -> Method
  -> Core Result -> Conclusion), adaptive narrow-canvas reflow (174x96 mm,
  120x95 mm, 1x4 or 2x2), typed mechanism arrows, error/zero-aware mini-charts,
  shared Cell/Okabe-Ito legend, source_ref claim grounding, external caption
  drafting, and editable SVG/.drawio/librsvg vector PDF/PNG under /app/output.
---

# Graphical Abstract Narrative Composer & Provenance Auditor

Skill resources live at `/skills/weekly-skill`. Case inputs live under `/input/` (`case.json`, `task.md`, source papers/tables, or existing sub-panel SVGs). Work under `/app/work` and emit final deliverables under `/app/output`.

## 1. Core Narrative & Visual Hierarchy Rules

1. **Four-Beat Single-Page Storyline (`Question -> Method -> Core Result -> Conclusion`)**:
   - Organize every Graphical Abstract monotonically across 4 numbered stages connected by directional flow arrows (`1 -> 2 -> 3 -> 4`) in either `1x4` landscape (`174 × 96 mm`, Cell/Elsevier) or `2x2` grid (`120 × 95 mm` or `174 × 110 mm`, Nature/ACS; `layout: "auto"` switches to `2x2` automatically when width < 138 mm).
   - **Canvas vs Caption Separation**: Keep the visual canvas scannable (concise domain labels, units, core quantified finding, shared entity color key). Route full statistical test names, cohort `n`, and methodological caveats to the automatically generated companion `*_caption_draft.md`. Never leak backend/QC words (`QC_`, `post_basic`, `(in-sample)`, `preselector`, raw `#RRGGBB`, `.py`) onto visible canvas text.
2. **Strict Claim Grounding (`UNGROUNDED_CLAIM_BLOCKER`)**:
   - Every quantitative panel (`mini_bars`, `paired_dots`, `metrics_strip`) or numeric `highlight` MUST declare a non-empty `source_ref` traceable to `/input/`. Unprovided values must be recorded as gaps via `gap_gate.py` — never fabricated.
3. **Rich Scientific Visual Primitives (Beyond Plain Text Boxes)**:
   - `diagram`: typed mechanism/workflow mini-graph with `activate` (`->`), `inhibit`/`block` (`-|` T-bar), and `flow`/`transport` (`-->` dashed) arrows plus rounded/pill biological entity shapes.
   - `mini_bars`: quantitative comparison with explicit `unit` preservation, `err` error bars, **exact `0` baseline hollow marker** (never a positive-height fake bar!), negative-value support with zero baseline, optional `ref_line`, and 2-line auto-wrapped category labels.
   - `paired_dots`: paired before/after slope comparison.
   - `metrics_strip`: high-contrast quantified outcome tiles bound to `source_ref`.
   - **Shared Visual Key Strip**: Declaring `entity_colors` renders a compact unified color legend across all 4 stages using colorblind-safe Cell/Okabe-Ito tokens (`#547FA3`, `#D19A45`, `#4E9187`, `#D77C70`, `#967A9C`).
4. **Adaptive Narrow-Canvas Reflow & Overflow Transparency**:
   - `ga_narrative_composer.py` budgets exact width/height in points from `width_mm` × `height_mm` and auto-wraps titles, motif boxes, bar labels, bullets, and takeaway pills cleanly at readable typography (`>= 6.5 pt` body, `>= 7.5 pt` headings).
   - If text still exceeds physical bounds even after wrapping, the composer stops cleanly with `LAYOUT_OVERFLOW` (never silently clipping, overlapping, or shrinking below 6.4 pt).
5. **Dual Editable Vector Outputs + Librsvg Vector PDF + Source-Preserving Edits**:
   - Pure-vector inline SVG assembly (`figquilt_composer.py`, zero `fitz`/bitmap flattening), native `.drawio` via `svg2drawio`, and vector PDF + PNG via `render_preview.py` (librsvg `rsvg-convert` with Matplotlib fallback).
   - Revise individual headings, metric values, or colors via `--edit-svg` + `--patch` while keeping sibling panels intact (`preserved_id_ratio == 1.0`) and logging `*_edit_diff.json`.

## 2. Standard Execution Pipeline

```bash
SCRIPTS=/skills/weekly-skill/scripts

# Step 1: Probe environment, initialize budget & result.json
python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "<TASK_ID>"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Step 2: Compose 4-stage mm-locked Graphical Abstract (.svg, .drawio, 300 dpi .png, .pdf, _caption_draft.md, _manifest.json) + auto-seal
python3 "$SCRIPTS/ga_narrative_composer.py" --spec /app/work/ga_spec.json --dpi 300 --case-json /input/case.json --out-dir /app/output --stem graphical_abstract

# Step 3: Narrative, grounding, color-consistency, edge-clipping & editability audit
python3 "$SCRIPTS/ga_visual_audit.py" \
  --spec /app/work/ga_spec.json \
  --svg /app/output/graphical_abstract.svg \
  --drawio /app/output/graphical_abstract.drawio \
  --png /app/output/graphical_abstract.png

# Step 3b (Optional Source-Preserving Edit: inspect IDs first, then patch)
python3 "$SCRIPTS/ga_narrative_composer.py" --edit-svg /app/output/graphical_abstract.svg --list-ids
python3 "$SCRIPTS/ga_narrative_composer.py" \
  --edit-svg /app/output/graphical_abstract.svg \
  --patch /app/work/edit_patch.json \
  --out-dir /app/output --stem graphical_abstract_edited

# Step 4: Palette WCAG/CVD check, seal result.json & run final_audit
python3 "$SCRIPTS/check_palette.py" "#547FA3,#D19A45,#4E9187,#D77C70" --role categorical --mark bar
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json \
  --patch-json '{"artifacts":["graphical_abstract.svg","graphical_abstract.drawio","graphical_abstract.png","graphical_abstract.pdf","graphical_abstract_caption_draft.md"]}' --finalize
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output \
  --require graphical_abstract.svg --require graphical_abstract.drawio --require graphical_abstract.png --require graphical_abstract.pdf
```

## 3. Failure Handling & Vector QA Checklist

- **Narrow-canvas overflow (`LAYOUT_OVERFLOW`)**: Switch `layout` from `1x4` to `2x2` (or `"auto"`), shorten verbose bullet prose to punchy phrases, or move methodological detail into `caption_notes` so the canvas stays readable.
- **Ungrounded numeric claims (`UNGROUNDED_CLAIM_BLOCKER`)**: Trace every number back to `/input/` and add `source_ref`; if missing from inputs, remove the number and log the gap in `gaps.json`.
- **Same-source re-export**: After any SVG edit, re-export `.drawio`, `.png`, and `.pdf` from the same final SVG and visually inspect the rendered PNG for text readability, arrow connectivity, zero edge clipping, and balanced whitespace.
