---
name: grounded-mindmap-builder
description: Build verifiable, editable scientific mind maps (.json, layered .svg, FreeMind .mm, Mermaid .mmd, and rendered .png) from research papers, plans, or meeting notes. Enforces exact-file citation backlinks, epistemic tags (fact/summary/inference), UTF-8 CJK integrity (zero replacement chars), formula/UNC safety guards, CJK font verification, and collision-free layout.
---

# Grounded Scientific Mind Map Generation & Verification

Use this skill when distilling scientific literature, experimental designs, project plans, or meeting notes under `/input` into editable, citation-anchored mind maps with verifiable visual previews.

## Hard Invariants

1. **Exact-file citation backlinks & epistemic tagging**: Every node (`id`, `label`, `parent_id`, `node_type`, `epistemic_status` in `fact|summary|inference`, `source_anchor: {file, quote, locator}`) and every cross-link edge (`source`, `target`, `relation`, `source_anchor`) must bind to a real file inside `/input`. For text files (`.md`, `.txt`, `.json`, `.csv`, `.tex`, `.bib`) and text-layer `.pdf` files (when Poppler `pdftotext` is installed), `quote` MUST occur verbatim inside that exact file (borrowing a quote from another file fails). Image-only/scanned PDFs or non-text sources without extractable text require explicit page/paragraph `locator` and are listed in `unverified_quotes` for human review.
2. **UTF-8 file-only ingestion (zero CJK corruption)**: Never pipe UTF-8 CJK Markdown through chunked stdin; read files directly as UTF-8. Assert `replacement_char_count == 0` across inputs and all exported artifacts.
3. **Math formula & external/UNC image safety**: Preserve raw LaTeX formulas (`$...$`, `\(...\)`) in `.json`/`.mm`/`.mmd`, render readable Unicode fallback in SVG/PNG, and report complex unrendered LaTeX commands visibly in `unrendered_math_nodes`. Strip/block remote `http(s)://`, protocol-relative `//host/...`, and Windows UNC `\\server\share` image paths from offline SVG exports (`blocked_external_or_unc_refs`).
4. **Honest CJK font verification (no silent tofu PNGs)**: Discover installed CJK fonts across `fc-list :lang=zh` and `matplotlib.font_manager` and inject the verified family into SVG before rendering. If CJK labels exist and NO CJK font is installed, fail PNG rendering visibly (`rendered_readable_cjk: false`) instead of shipping unreadable `□□□` boxes.
5. **Collision-free, readable, editable multi-format delivery**: Use CJK-width-aware text wrapping and dynamic per-depth column placement so `overlaps == []` and `clipped == []`. Deliver synchronized `.json`, layered live-text `.svg` (`layer-edges`, `layer-cross-edges`, `layer-nodes`, `layer-legend`), FreeMind `.mm`, Mermaid `.mmd`, and rendered `.png` preview (zero `cairosvg` or AGPL dependencies).
6. **Complete Harbor output contract**: Populate `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, `sources`, and `uncertainty`/`gaps` in `/app/output/result.json`.

## Execution Workflow

### Step 1: Probe sandbox and index source materials

```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```

Read `/input/case.json`, `/input/task.md`, and all actual source files. Identify:
- Mind map scenario (paper/review synthesis, experimental design, project plan, meeting decisions, or concept taxonomy).
- Target reader, required output formats, language (Chinese/English/bilingual), and any material gaps or contradictions.

### Step 2: Distill storyline hierarchy and ground every node & edge

Read `references/mindmap-grounding-and-taxonomy.md` and `references/cjk-math-security-and-layout.md`.
Organize the map around the core scientific storyline (e.g., `Research Question -> Methods & Controls -> Key Evidence -> Conclusions & Limitations -> Next Actions/Risks`), keeping depth `<= 5`, balanced sibling granularity, and zero decorative filler nodes.

Author `/app/work/mindmap_spec.json` (or UTF-8 `/app/work/outline.md` with inline `[fact|summary|inference]` and `[@file: "verbatim quote" | locator]` tags):
- Tag every node with `node_type` (`root`, `concept`, `question`, `method`, `evidence`, `conclusion`, `task`, `risk`) and `epistemic_status` (`fact`, `summary`, `inference`).
- Only add typed cross-edges (`supports`, `depends_on`, `causes`, `contrasts`, `contradicts`, `cites`, `mitigates`) when explicitly supported by `/input` materials, and attach a `source_anchor` to every cross-edge.
- Preserve contradictory findings side-by-side via `contradicts` or `contrasts` edges rather than silently smoothing them away.

### Step 3: Build synchronized editable artifacts, render PNG, and run QA

From canonical JSON spec:
```bash
python3 /skills/weekly-skill/scripts/mindmap_builder_and_qa.py \
  --spec /app/work/mindmap_spec.json \
  --corpus-dir /input \
  --out-dir /app/output \
  --out-json /app/output/mindmap_audit.json
```

Or directly from a UTF-8 Markdown outline:
```bash
python3 /skills/weekly-skill/scripts/mindmap_builder_and_qa.py \
  --from-markdown /app/work/outline.md \
  --slug research-mindmap \
  --corpus-dir /input \
  --out-dir /app/output \
  --out-json /app/output/mindmap_audit.json
```

Inspect `/app/output/mindmap_audit.json`:
- Verify `passed: true`, `replacement_char_count: 0`, `overlaps: []`, `clipped: []`, and `errors: []`.
- Inspect `unrendered_math_nodes`, `blocked_external_or_unc_refs`, and `unverified_quotes`.
- Open/inspect the rendered `.png` preview (confirm readable labels, CJK glyph integrity, clean curved cross-edge labels, and zero overlap).

### Step 4: Seal `result.json` with all 5 output_contract fields and run final audit

Patch `/app/output/result.json` including `case_id`, `deliverable` (paths to `.json`, `.svg`, `.mm`, `.mmd`, `.png`), `preserved_facts`, `evidence_map` (from `mindmap_audit.json`), `change_log`, `sources`, and `uncertainty`/`gaps`:

```bash
python3 /skills/weekly-skill/scripts/result_guard.py --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output --require mindmap_audit.json
```

## Offline Degradation & Failure Handling

- **Missing CJK font in offline container**: `mindmap_builder_and_qa.py` refuses to claim a readable CJK PNG (`rendered: false`, `rendered_readable_cjk: false`), still emits valid UTF-8 `.json`, `.svg`, `.mm`, `.mmd`, and marks `status: "partial_with_gaps"` with exact `font_discovery` diagnostics.
- **Missing `rsvg-convert` and `matplotlib`**: Deliver all 4 text/XML editable formats (`.json`, `.svg`, `.mm`, `.mmd`), record `png_renderer.engine: "none"`, and state PNG preview as unverified in `uncertainty`.
- **Contradictory or incomplete inputs**: Represent conflicts explicitly with `contradicts` edges and create an explicit `[inference]` or gap note rather than fabricating missing links.
