# Long-Document & Thesis Revision Skill

Offline, zero-dependency Python 3.12 workflow for multi-chapter thesis writing, surgical editorial revision, cross-chapter consistency auditing, and advisor comment tracking.

## What It Solves

1. **Controlled local revision state machine (`scripts/longdoc_revision_engine.py`)**:
   - Imports sectioned working chapters in `.md`, `.txt`, `.tex`, `.docx` (pure-stdlib OOXML), or `.pdf` (`pdftotext` -> `pdfplumber` -> `pypdf` -> pure-stdlib `zlib` FlateDecode fallback) with byte-exact SHA-256 receipts, adaptive workorder `target_id` pattern induction, deterministic auto-anchoring for anchorless documents, and strict `BLOCKED_MISSING_TARGETS` scope protection.
   - Proposes source-grounded editorial annotations (`--mode annotate`) or authorized substantive paragraph rewrites (`--mode replace`, supporting `--block-index`, `--anchor-query`, `--annotation-file`, `--replacement-file`), with independent per-proposal `accept` or `reject`.
   - Executes real `diff`, 100% SHA-256 `rollback`, deterministic `reapply`, 5-case `fault-test` proving zero state mutation, `parse-comments` advisor-feedback decomposition, and offline zero-CDN HTML diff preview export (`export_preview.html`).
2. **Domain-agnostic cross-chapter consistency auditor (`scripts/longdoc_chapter_consistency.py`)**:
   - Enforces dual-schema edit scope protection (`target_ids` + `edits[].file`) with dynamic anchor template induction and zero silent passthrough on missing targets.
   - Detects acronym/symbol conflicts, **domain-agnostic `(entity, unit, value, condition)` numerical/sample-size conflicts** across disciplines (SI + Chinese units + condition-phrase binding preventing cross-arm/cross-year false positives), numbering gaps & dangling cross-references, **linear-time inverted-index bilingual shingle Jaccard duplicate detection**, `claim_support_matrix` citation-claim alignment, Zotero/EndNote field corruption, and chapter coverage/truncation.
   - Generates editable `chapter_plan.json` (`--generate-plan`) directly from `/input` chapters with word/character budgets and thesis argument-role checks.

## Requirements

- Python 3.12+ standard library only (zero mandatory third-party packages, zero network calls). Optional `pdftotext` / `pdfplumber` / `pypdf` accelerate complex PDF extraction when available; stdlib `zlib` fallback operates offline without them.
- Compatible with offline Linux execution sandboxes (`/input`, `/app/work`, `/app/output`) and local portable execution via `SKILL_ROOT`.

## Package Layout

```text
.
├── SKILL.md                                     # Agent entrypoint & 5-step workflow + recovery table
├── README.md                                    # Overview, verification & boundaries
├── references/
│   ├── output_contract_spec.md                  # Exact 5-field result.json contract
│   └── continuity_and_revision_protocol.md      # Multi-chapter continuity, tool-boundary table & pre-mortem
├── examples/
│   └── minimal_workflow_example.md              # Illustrative CLI walkthrough (synthetic example)
└── scripts/
    ├── longdoc_revision_engine.py               # Import/propose/accept/reject/diff/rollback/reapply/fault-test/parse-comments/export
    ├── longdoc_chapter_consistency.py           # Domain-agnostic consistency, claim-support matrix & chapter-plan generator
    ├── preflight_probe.py                       # Sandbox probe (M1)
    ├── result_guard.py                          # Atomic result.json guard with --patch-file (M2)
    ├── budget_guard.py                          # Step/time budget ledger (M3)
    ├── block_indexer.py                         # Input file & block indexer (M4)
    ├── gap_gate.py                              # Grounding provenance verifier (M5)
    └── final_audit.py                           # Pre-exit deliverable contract audit (M6)
```

## Quick Verification Commands

```bash
python3 scripts/longdoc_revision_engine.py --help
python3 scripts/longdoc_chapter_consistency.py --help
```

## Boundaries, Threshold Calibration & Maintenance

- **Multi-format scope**: Natively reads `.md`, `.txt`, `.tex`, `.docx` (OOXML text/footnotes/field-code audit), and `.pdf` (extracted to normalized `.extracted.md` working copies while hashing original binary bytes). Exporting binary `.docx` Word tracked-change XML remains outside stdlib scope; revised deliverables export as editable Markdown + unified diff + offline HTML preview.
- **Duplicate Jaccard threshold (`--dup-threshold 0.45`)**: Calibrated on 3-gram bilingual shingles where `0.45–0.64` flags medium-overlap paraphrased discussion reuse (warning) and `>= 0.65` flags high-overlap copy-paste repetition across chapters (blocking error), avoiding false positives on shared methods boilerplate while catching verbatim General Discussion duplication.
- **Cross-disciplinary extension**: Domain-agnostic `(entity, unit, value, condition)` extraction generalizes across clinical, regulatory, materials, chemistry, economics, and social science manuscripts without hard-coding a single field's vocabulary.
- **Honesty invariant**: Never fabricates missing chapters, uninspected citations, or remote platform states; source contradictions are surfaced with exact coordinates rather than silently overwritten.
