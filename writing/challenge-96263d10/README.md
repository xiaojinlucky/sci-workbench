# Academic Format & Template Adapter (`academic-template-adaptation`)

Offline, zero-dependency Skill for extracting academic template specifications, isolating conflicting template versions, performing lossless OOXML `.docx` formatting adaptation (with `Liberation Serif` font verification), and proving 9-point content fidelity.

## Key Capabilities

1. **Strict Zero-Guessing Spec Extraction (`extract-spec`)**: Parses `.docx`, `.tex`, `.json`, and `.md` templates; leaves every unstated field explicitly `UNSPECIFIED`.
2. **Conflict Template Isolation (`compare-templates`)**: Separates designated target rules from conflicting legacy/alternate templates (`APPLY_TARGET_ONLY`, `KEEP_UNSPECIFIED_DO_NOT_MIX`).
3. **Lossless OOXML Adaptation (`adapt`)**: Updates `word/styles.xml` and `word/document.xml` in place using pure Python 3.12 stdlib (no `python-docx` required), preserving equations (`m:oMath`), footnotes, comments, tracked changes (`w:ins`/`w:del`), live field codes, tables, images, and text invariants 1:1.
4. **Font & Visual Preview Verification**: Probes real container fonts via `fc-list` (honoring `Liberation Serif` fallback when target fonts are absent) and invokes LibreOffice PDF/PNG rendering with honest degradation.

See `examples/minimal_example.md` for a synthetic invocation example.
