# Experimental Apparatus & Technical Route Builder

Skill package for creating, verifying, and performing source-preserving edits on scientific experimental apparatus setups and multi-stage technical roadmaps.

The route report lists outlets reachable along a forward path. Feedback and recycle arrows may supplement a route; the primary-route check excludes them.

## Capabilities & Architecture

- **`scripts/apparatus_route_linter.py`**: Pre/post checks for inlet-to-outlet DAG reachability, cycle guard (requiring explicit `feedback`/`recycle` tags), stage monotonicity, port/spec provenance, and distinct stream-type encoding (`material`, `optical`, `electrical`, `control`, `waste`, `feedback`).
- **`scripts/apparatus_route_builder.py`**:
  - Build mode (`--spec`, `--dpi 300`, optional `--case-json`): validates schema & `source_ref` coverage (`contract_sealer.py`), generates semantic multi-layer `.svg`, native bound `.drawio` via pure-Python `svg2drawio`, runtime standalone TikZ `.tex` + `edit_contract` `.meta.json` (guided by `references/tikz_template_spec.json`; zero static `.tex` files shipped in package), `300 dpi` `.png`/vector `.pdf` via `render_preview.py`, and auto-seals `result.json` preserving `/input/case.json` `output_contract`.
  - Edit mode (`--edit-svg` + `--patch`): performs ID-preserving text, attribute, `move_node` (with stream re-routing), and numeric parameter updates on existing SVGs (plus matching `.tex` `\def` macro edits when `--edit-tex` is supplied) without altering untouched components.
- **`scripts/validate_tikz_contract.py`**: Validates `\documentclass{standalone}`, `\def` parameters, named colors, `/.style` definitions, and `edit_contract` metadata consistency.
- **`scripts/drawio_layout_check.py` / `portable_svg_audit.py` / `audit_editability.py`**: Layout collision, boundary clipping, CJK font-fallback audit, and vector editability verification.
- **`scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit}.py`**: Bundled `scripts/` provenance, budget, and output completeness guards.

## Local Smoke Verification

```bash
python3 -B scripts/apparatus_route_builder.py --spec examples/synthetic_route_spec.json --out-dir /tmp/smoke_apparatus --stem synthetic_route --dpi 300
```

All bundled samples under `examples/` are marked `SYNTHETIC EXAMPLE — NOT CASE OUTPUT`.

## Bound Draw.io Streams

The builder carries explicit unit and stream IDs into native Draw.io cell IDs. It then binds each stream edge to the source and target unit IDs declared by the spec. The mapping is checked by IDs and fails closed if conversion drops or conflicts with them; labels and object order are never used to infer connections. Edges remain connected to their unit cells when a unit is moved. For substantial manual edits, inspect the rendered diagram in Draw.io.

Unit labels and operating-spec text also retain their explicit SVG source IDs during conversion. The builder moves both verified labels into the matching native unit cell's editable `value` with centered HTML styling, then removes the duplicate text cells. The label, operating parameters, and shape therefore travel together when a unit is moved; the generated SVG remains unchanged.
