# Graphical Abstract Narrative Composer

Offline scientific Skill for designing, composing, auditing, and source-preserving editing single-page scientific Graphical Abstracts with 4-stage narrative structure (`Question -> Method -> Core Result -> Conclusion`), adaptive narrow-canvas reflow, and editable vector outputs.

## Core Capabilities
- **Adaptive Narrow-Canvas Reflow (`scripts/ga_narrative_composer.py`)**: Supports `174×96 mm` (`1x4`) and `120×104 mm` (`2x2` / `"auto"`) canvases with dynamic point-width text wrapping, horizontal compact diagrams in `2x2` mode, 2-line wrapped mini-bar category labels, and explicit `LAYOUT_OVERFLOW` diagnostics (never silently truncating or shrinking below `6.4 pt`).
- **Rich Scientific Visual Primitives**: Typed mechanism diagrams (`activate`, `inhibit` T-bar, `flow`), quantitative `mini_bars` with error bars, exact `0` baseline markers, negative-value support and preserved units, `paired_dots` slope plots, and `metrics_strip` outcome tiles.
- **Cross-Panel Shared Color Key & External Caption Draft**: Renders a unified Cell/Okabe-Ito `entity_colors` legend strip across all 4 stages and automatically writes `*_caption_draft.md` to keep methodological caveats off the visual canvas.
- **Claim Grounding & Visual Audit (`scripts/ga_visual_audit.py`)**: Blocks ungrounded numeric panels (`UNGROUNDED_CLAIM_BLOCKER`), backend vocabulary leaks (`BACKEND_VOCAB_LEAK`), PNG outer-edge clipping, and Draw.io layout overlaps.
- **Dual Editable Vector Outputs + Librsvg PDF**: Emits layered `.svg`, native `.drawio`, vector `.pdf`, `.png`, and supports `--edit-svg` + `--patch` with `preserved_id_ratio == 1.0`.

See `SKILL.md` for the full Harbor pipeline and `references/ga_narrative_standards.md` for layout and vector QA rules.
