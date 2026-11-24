---
name: grounded-mindmap-builder
description: Automatically distill scientific papers, PDFs, DOCX files, plans, or meeting notes under /input into verifiable, editable mind maps (.json, layered .svg, FreeMind .mm with arrowlinks, Mermaid .mmd, and rendered .png). Enforces exact-file & page-verified citation backlinks, cross-edge semantic support validation, epistemic tags (fact/summary/inference), UTF-8 CJK integrity (zero replacement chars), formula/UNC safety guards, CJK font verification, and collision-free layout with >80-node sub-map splitting.
---

# Grounded Scientific Mind Map Distillation, Generation & Verification

Use this skill when distilling scientific literature (`.pdf`, `.docx`, `.md`, `.txt`, `.json`, `.csv`), experimental designs, project plans, or meeting notes under `/input` into editable, citation-anchored mind maps with verifiable visual previews.

## Hard Invariants

1. **Zero-manual `/input` distillation + blank-page-safe strict page/line citation backlinks**: Always run `--from-input /input` first to extract 1-based page-numbered text **preserving blank PDF pages** (via `pdftotext -layout` or `pypdf`; pure-stdlib `zlib` fallback is explicitly tagged `stdlib_stream_unindexed` and never certifies page numbers) and synthesize a grounded storyline skeleton (`nodes` + typed `cross_edges` with verbatim `quote` and fine-grained sentence-level `p.<page>:L<line> S<n>[c1-c2]` `locator`). Every node (`id`, `label`, `parent_id`, `node_type`, `epistemic_status` in `fact|summary|inference`, `source_anchor`) and cross-edge (`source`, `target`, `relation` in `supports|depends_on|causes|correlates_with|contrasts|contradicts|cites|mitigates`, `source_anchor`) must bind verbatim to a real file inside `/input` AND pass strict declared page + line verification (`MINDMAP_LOCATOR_MISMATCH` blocks pass or forces `partial_with_gaps`, never inflating `node_grounding_rate` to `1.0`). Record `unprocessed_files` and distinct `subgroup_scope_notes` (e.g. `<5 years` vs `0-14 years`) in `result.json`.
2. **Strict causal-vs-correlation & non-cooccurrence cross-edge verification**: Keep `causes` strictly for causal verbs and route association/correlation cues to `correlates_with`. Never promote endpoint token co-occurrence alone to `semantically_supported: true` — edges without an explicit relation cue are marked `unverified_cooccurrence_only` / `needs_review` (`MINDMAP_UNVERIFIED_COOCCURRENCE_EDGE`, `partial_with_gaps`). Block `relation_type_mismatch` (correlation cited as `causes`), `negated_relation` (e.g. `did not cause`), `reversed_direction`, and `unrelated_quote` with non-zero exit codes. Preserve contradictory findings side-by-side via `contradicts` or `contrasts` edges.
3. **UTF-8 file-only ingestion (zero CJK corruption)**: Never pipe UTF-8 CJK Markdown through chunked stdin; read files directly as UTF-8. Assert `replacement_char_count == 0` across inputs and all exported artifacts.
4. **Math formula & external/UNC image safety**: Preserve raw LaTeX formulas (`$...$`, `\(...\)`) in `.json`/`.mm`/`.mmd`, render readable Unicode fallback in SVG/PNG, and report complex unrendered LaTeX commands visibly in `unrendered_math_nodes`. Strip/block remote `http(s)://`, protocol-relative `//host/...`, and Windows UNC `\\server\share` image paths from offline SVG exports (`blocked_external_or_unc_refs`).
5. **Honest CJK font verification & graceful partial degradation**: Discover installed CJK fonts across `fc-list :lang=zh` and `matplotlib.font_manager` and inject the verified family into SVG/PNG before rendering. If CJK labels exist and NO CJK font is installed, refuse to emit unreadable tofu PNGs (`rendered: false`, `rendered_readable_cjk: false`); with `--allow-partial-png`, deliver all 4 valid UTF-8 editable artifacts (`.json`, `.svg`, `.mm`, `.mmd`) with explicit `status: "partial_with_gaps"` and gap `MINDMAP_CJK_FONT_MISSING` instead of discarding valid deliverables.
6. **Collision-free visual traceability & large-scale splitting**: Render `@<file> <locator>` badges and curved cross-edge `relation` labels on BOTH SVG and matplotlib PNG previews. Enforce `overlaps == []`, `clipped == []`, and automatic cross-edge label collision avoidance (`cross_edge_label_overlaps`). When `node_count > 80`, automatically export `<slug>_overview.svg` (depth `<= 2`) plus per-branch sub-map SVGs.
7. **Automated Harbor output contract**: Pass `--out-result-patch` to auto-generate all required `result.json` fields (`case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, `sources`, `uncertainty`, `gaps`) without manual JSON assembly.

## Execution Workflow

### Step 1: Probe sandbox and index source materials

```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```

Read `/input/case.json` and `/input/task.md` to confirm target reader, focus question, language (Chinese/English/bilingual), and required output formats.

### Step 2: Auto-distill raw `/input` into a grounded mind map skeleton

Run automatic corpus-to-mindmap distillation directly on `/input` (works on raw `.pdf`, `.docx`, `.md`, `.txt` without manual transcription):

```bash
python3 /skills/weekly-skill/scripts/mindmap_builder_and_qa.py \
  --from-input /input \
  --slug research-mindmap \
  --max-nodes 34 \
  --draft-spec-out /app/work/mindmap_spec.json \
  --out-dir /app/output \
  --out-json /app/output/mindmap_audit.json \
  --out-result-patch /app/work/result_patch.json \
  --case-id "<CASE_ID>" \
  --allow-partial-png
```

### Step 3: Optional domain refinement & strict QA re-render

Read `references/mindmap-grounding-and-taxonomy.md` and `references/cjk-math-security-and-layout.md`.
Inspect `/app/work/mindmap_spec.json` and `/app/output/mindmap_audit.json`. If `/input/task.md` requests specific reader framing, additional domain branches, or extra cross-links, refine `/app/work/mindmap_spec.json` (or author a UTF-8 `/app/work/outline.md` for `--from-markdown`) while keeping every node/edge bound verbatim to its source file and page locator, then re-run:

```bash
python3 /skills/weekly-skill/scripts/mindmap_builder_and_qa.py \
  --spec /app/work/mindmap_spec.json \
  --corpus-dir /input \
  --out-dir /app/output \
  --out-json /app/output/mindmap_audit.json \
  --out-result-patch /app/work/result_patch.json \
  --case-id "<CASE_ID>" \
  --allow-partial-png
```

Verify in `/app/output/mindmap_audit.json`:
- `editable_artifacts_valid: true`, `node_grounding_rate: 1.0`, `replacement_char_count: 0`, `overlaps: []`, `clipped: []`, `cross_edge_label_overlaps: []`.
- Inspect `cross_edge_semantic_audit`, `page_verified_count`, `unrendered_math_nodes`, `blocked_external_or_unc_refs`, and `unverified_quotes`.
- Visually inspect the rendered `.png` preview (confirm readable labels, `@file locator` badges, CJK glyph integrity, and non-overlapping cross-edge relation labels).

### Step 4: Seal `result.json` automatically and run final audit

Merge the auto-generated contract patch into `/app/output/result.json` and execute the final verification gates:

```bash
python3 /skills/weekly-skill/scripts/result_guard.py --patch-json "$(cat /app/work/result_patch.json)" --finalize
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output --require mindmap_audit.json
```

## Offline Degradation & Failure Handling

- **Missing CJK font in offline container**: `mindmap_builder_and_qa.py` refuses to fake a readable CJK PNG (`rendered: false`, `rendered_readable_cjk: false`), still delivers valid UTF-8 `.json`, `.svg`, FreeMind `.mm` (with `<arrowlink>` cross-edges), and `.mmd`, and records `status: "partial_with_gaps"` + `MINDMAP_CJK_FONT_MISSING` in `gaps` and `uncertainty`.
- **Scanned/image-only PDF or unreadable binary source**: Records affected nodes in `unverified_quotes` and `MINDMAP_UNVERIFIED_QUOTES` gap with explicit page/paragraph `locator` requirements rather than fabricating quotes.
- **Large-scale corpus (>80 nodes)**: Automatically emits `<slug>_overview.svg` (depth `<= 2`) and per-branch `<slug>_sub_<branch>.svg` views alongside the full editable files.
- **Contradictory inputs**: Preserves conflicting claims side-by-side via `contradicts` edges grounded verbatim in both sources.
