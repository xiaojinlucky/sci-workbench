# Long-Document & Thesis Revision Skill

Offline, zero-dependency Python 3.12 workflow for multi-chapter thesis writing, surgical editorial revision, and cross-chapter consistency auditing.

## What It Solves

1. **Controlled local revision state machine (`scripts/longdoc_revision_engine.py`)**:
   - Imports sectioned working chapters with byte-exact SHA-256 receipts, paragraph anchor indexing (`C01-P002`, etc.), and coverage/truncation detection.
   - Proposes source-grounded editorial annotations and supports independent per-proposal `accept` or `reject` while leaving original text, tables, formulas, citations, headings, and unrequested blocks untouched.
   - Executes real `diff`, byte-verified `rollback`, deterministic `reapply`, controlled `fault-test` (proving zero state/file mutation on invalid operations), and self-contained offline HTML diff preview export (`export_preview.html`, zero CDN).
2. **Cross-chapter scientific consistency auditor (`scripts/longdoc_chapter_consistency.py`)**:
   - Enforces byte-level edit scope protection for both `target_ids` (paragraph-level) and `edits[].file` (chapter-level) workorders.
   - Detects acronym expansion conflicts, symbol definition conflicts, same-entity cross-chapter numerical/sample-size contradictions, figure/table/equation numbering gaps and dangling cross-references, bilingual cross-chapter duplicate discussion (3-gram shingle Jaccard), citation field/index anomalies, and chapter coverage/truncation risks.

## Requirements

- Python 3.12+ standard library only (no third-party packages, no network access).
- Compatible with offline Linux execution sandboxes (`/input`, `/app/work`, `/app/output`).

## Package Layout

```text
.
├── SKILL.md                                     # Agent entrypoint & 5-step execution workflow
├── README.md                                    # Usage overview & maintenance guide
├── references/
│   ├── output_contract_spec.md                  # Exact 5-field result.json contract
│   └── continuity_and_revision_protocol.md      # Multi-chapter continuity, anti-duplication & pre-mortem guide
├── examples/
│   └── minimal_workflow_example.md              # Illustrative CLI walkthrough (synthetic example)
└── scripts/
    ├── longdoc_revision_engine.py               # Import / propose / accept / reject / diff / rollback / reapply / fault-test / export
    ├── longdoc_chapter_consistency.py           # Dual-schema scope & cross-chapter scientific consistency auditor
    ├── preflight_probe.py                       # Sandbox probe (M1)
    ├── result_guard.py                          # Atomic result.json guard (M2)
    ├── budget_guard.py                          # Step/time budget ledger (M3)
    ├── block_indexer.py                         # Input file & block indexer (M4)
    ├── gap_gate.py                              # Grounding provenance verifier (M5)
    └── final_audit.py                           # Pre-exit deliverable contract audit (M6)
```

## Quick Verification Command

```bash
python3 scripts/longdoc_revision_engine.py --help
python3 scripts/longdoc_chapter_consistency.py --help
```

## Boundaries & Maintenance

- Operates on UTF-8 text chapters (`.md`, `.txt`, `.tex`). Binary Word `.docx` tracked-change XML is out of scope unless text-exported chapters are provided.
- Never fabricates missing chapters, uninspected citations, or remote platform states.
- Source contradictions are surfaced for human/author confirmation rather than silently overwritten.
