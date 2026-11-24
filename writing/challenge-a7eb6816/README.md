# Translational Science Writing Skill

Offline, zero-dependency Python 3.12 workflow for adapting scientific manuscripts (`.pdf`, `.docx`, `.md`, `.txt`) into audience-calibrated deliverables (`public_science`, `tech_report`, `grant_brief`, `policy_memo`, `industry_pitch`) with automated `/input` source-brief extraction and deterministic evidence-boundary, figure/table, statistical, and prose auditing.

## What It Solves

1. **Automated `/input` extraction & skeleton generation (`--extract-source-brief`)**: Natively reads `.pdf` (`pdftotext` -> `pdfplumber` -> `pypdf` -> stdlib `zlib` FlateDecode fallback), `.docx` (pure-stdlib OOXML), and UTF-8 text; automatically emits `source_brief.json`, an editable `paragraph_trace.json` skeleton, and a candidate `glossary_lock.json` draft so neither Agent nor researcher hand-builds complex JSON from scratch.
2. **Zero numeric, unit, p-value & benchmark drift**: Recognizes bilingual magnitude equivalences (`12,000` <-> `1.2万`), verifies sentence-scoped derived ratios (tightened tolerance), detects unit swaps (`NUMERIC_UNIT_MISMATCH`), checks **per-value `p`-value multiset conservation**, scans internal source numeric contradictions, and preserves cross-disciplinary baselines (`control group`, `vehicle`, `comparative example` / `对比例`, `prior art` / `现有技术`).
3. **Figure, table & citation anchor preservation (`figure_table_citation_preservation`)**: Extracts all source `Fig.`/`Table`/`图`/`表` anchors and blocks hallucinated display numbers, vague anonymous references, or untraced silent deletions.
4. **Cross-disciplinary 4-tier evidence ladder + proximity hedging**: Classifies both clinical/biomedical (`in_vitro` < `animal/preclinical` < `observational` < `RCT`) and engineering/materials/patent (`computational` < `bench/patent example` < `pilot` < `industrial deployment`) sources; blocks direct overclaims, unhedged translational promises within ±85 chars (`UNHEDGED_TRANSLATIONAL_PROMISE`), and omitted model boundaries.
5. **Calibrated natural prose & side-by-side author handoff**: Blocks promotional hype, corporate buzzwords, AI signposts, and em-dash overuse while protecting scientific terminology and legitimate scientific contrast sentences; exports `side_by_side_review.md`.

## Requirements

- Python 3.12+ standard library only (zero mandatory third-party packages, zero network calls). Optional `pdftotext` / `pdfplumber` / `pypdf` accelerate complex PDF extraction when present; stdlib `zlib` and OOXML fallbacks run offline without them.
- Compatible with offline Linux execution sandboxes (`/input`, `/app/work`, `/app/output`) and portable local paths via `SKILL_ROOT`.

## Package Layout

```text
.
├── SKILL.md                                         # Agent entrypoint, audience table & 4-step workflow
├── README.md                                        # Overview, verification & boundaries
├── references/
│   ├── output_contract_spec.md                      # 5-field result.json contract
│   ├── audience_matrix_and_style_rules.md           # 5-audience matrix & calibrated prose rules
│   └── evidence_ladder_and_omission_rules.md        # Cross-disciplinary 4-tier ladder & trace schema
├── examples/
│   └── minimal_workflow_example.md                  # Illustrative synthetic walkthrough
└── scripts/
    ├── translational_fidelity_auditor.py            # Native PDF/DOCX extractor + source-brief + fidelity auditor
    ├── preflight_probe.py                           # Sandbox probe (M1)
    ├── result_guard.py                              # Atomic result.json guard with --patch-file (M2)
    ├── budget_guard.py                              # Step/time budget ledger (M3)
    ├── block_indexer.py                             # Input block indexer (M4)
    ├── gap_gate.py                                  # Grounding provenance verifier (M5)
    └── final_audit.py                               # Pre-exit deliverable audit (M6)
```

## Quick Verification Command

```bash
python3 scripts/translational_fidelity_auditor.py --help
```

## Boundaries & Maintenance

- **Multi-format scope**: Natively handles `.pdf`, `.docx`, `.md`, and `.txt` sources (Chinese, English, and bilingual). Scanned image-only PDFs without an embedded text layer require external OCR and are honestly reported (`chars == 0`) rather than guessed.
- **Deterministic vs human boundary**: Automated rules enforce numeric/unit/p-value conservation, figure/table preservation, explicit tier escalation, proximity hedging, glossary lock, and AI-cliché blocking; final domain metaphors and press/regulatory approval remain with the original researcher via `side_by_side_review.md`.
