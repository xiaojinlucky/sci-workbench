# Grounded Scientific Mind Map Builder & QA Skill

Offline-ready scientific mind map generator and verification skill producing synchronized `.json`, layered editable `.svg`, FreeMind `.mm`, Mermaid `.mmd`, and rendered `.png` previews.

## Key Capabilities

1. **Exact-File Citation Backlinks & Epistemic Separation**: Verifies every node and cross-edge quote verbatim against its named file inside `/input`, enforces `fact | summary | inference` tags, and preserves non-text PDF locators transparently.
2. **CJK Encoding, Formula & Security Guards**: Reads UTF-8 files directly (zero U+FFFD replacement characters), preserves LaTeX math with readable SVG Unicode fallback plus explicit `unrendered_math_nodes` audit, and strips/blocks remote URLs and Windows UNC image paths.
3. **True Cross-Engine CJK Font Verification**: Probes `fc-list :lang=zh` and `matplotlib.font_manager`, injects the installed CJK font family into SVG, and fails visibly instead of emitting unreadable tofu PNGs if CJK fonts are absent.
4. **CJK-Aware Dynamic Layout & Zero-Overlap QA**: Wraps mixed Chinese/English node text by visual character width, computes dynamic per-depth column spacing, and verifies zero bounding-box overlaps and zero canvas clipping.

## Minimal Invocation

```bash
python3 scripts/mindmap_builder_and_qa.py \
  --spec examples/sample_spec.json \
  --corpus-dir examples \
  --out-dir /tmp/mm_out \
  --out-json /tmp/mm_out/mindmap_audit.json
```

## Runtime Dependencies & Limits

- Python 3.12 standard library only for spec parsing, grounding, layout QA, `.json`, `.svg`, `.mm`, and `.mmd` export.
- PNG preview uses system `rsvg-convert` or optional `matplotlib` (zero `cairosvg` or AGPL dependencies); degrades explicitly when absent or when CJK fonts are unavailable.
