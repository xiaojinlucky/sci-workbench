# CJK Integrity, Math Handling, Security, and Layout Rules

## 1. Known Failure Modes Addressed

| Failure Mode | Mitigation Enforced by `mindmap_builder_and_qa.py` |
| --- | --- |
| Chunked stdin corrupting multibyte CJK into `U+FFFD` (``) | UTF-8 file-only ingestion (`--spec` / `--from-markdown`) + hard `replacement_char_count == 0` gate across inputs and all exports |
| Unrendered LaTeX math silently breaking or disappearing | Preserves raw LaTeX in `.json`/`.mm`/`.mmd`, applies readable Unicode math rendering in SVG/PNG, and reports complex unrendered macros in `unrendered_math_nodes` |
| Protocol-relative (`//host/...`), remote `http(s)://`, or Windows UNC (`\\server\share`) images causing hangs/leaks | Strips and neutralizes external/UNC image references in SVG exports and logs `blocked_external_or_unc_refs` |
| Missing CJK fonts producing silent tofu boxes (`□□□`) in PNG | Queries `fc-list :lang=zh` and `matplotlib.font_manager`, injects actual installed CJK font into SVG, and fails visibly (`rendered_readable_cjk: false`) if no CJK font is installed |
| Space-delimited `textwrap.wrap` overflowing long Chinese node boxes | Visual character-width CJK wrapper (`1.0` full-width vs `0.56` ASCII) + dynamic per-depth column X spacing + exact bbox overlap/clipping check |

## 2. Layered Editable Vector SVG Contract

Exported `.svg` files maintain clean live `<text>` elements (never rasterized paths) inside four semantic groups:
- `layer-edges`: Hierarchy Bezier connectors
- `layer-cross-edges`: Curved dashed/solid cross-links with relation labels
- `layer-nodes`: Color-coded rounded cards with live editable text and `@source` citation badges
- `layer-legend`: Epistemic tag legend
