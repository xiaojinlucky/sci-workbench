# Translational Science Writing Skill

Offline, zero-dependency Python 3.12 workflow for adapting scientific manuscripts into audience-calibrated deliverables (`public_science`, `tech_report`, `grant_brief`, `policy_memo`, `industry_pitch`) with deterministic evidence-boundary and prose auditing.

## What It Solves

1. **5-audience calibration & Keep/Compress/Omit strategy**: Enforces audience-specific ceilings on jargon density, first-use acronym expansion, readability, and mandatory statistical/model context.
2. **Zero numerical, unit & benchmark drift**: Distinguishes counts from percentages, recognizes bilingual magnitude equivalences (`12,000` <-> `1.2万`, `1.5 million` <-> `150万`), verifies derived ratios against traceable numerator/denominator pairs, and catches `p`-value strengthening or dropped control baselines.
3. **4-tier evidence ladder hard gate**: Automatically classifies source evidence (`in_vitro_or_computational` < `animal_or_preclinical` < `observational_correlational` < `rct_or_clinical_causal`) and blocks both overclaiming (e.g., turning cell/mouse correlation into human therapy promises) and silent omission of experimental model boundaries.
4. **Glossary lock & natural prose audit with scientific whitelist**: Locks cross-genre terminology, blocks promotional hype, corporate buzzwords, rhetorical false-pivot clichés, and AI signposts while protecting genuine scientific terminology (e.g., tumor killing, temperature rise, precipitation, vascularization).
5. **Paragraph-by-paragraph traceability & author review handoff**: Validates `paragraph_trace.json` (blocking omission of key limitations or sample info) and exports `side_by_side_review.md` for rapid researcher verification.

## Requirements

- Python 3.12+ standard library only (zero third-party packages, zero network calls).
- Compatible with offline Linux sandboxes (`/input`, `/app/work`, `/app/output`).

## Package Layout

```text
.
├── SKILL.md                                         # Agent entrypoint & 4-step workflow
├── README.md                                        # Overview & maintenance guide
├── references/
│   ├── output_contract_spec.md                      # 5-field result.json contract
│   ├── audience_matrix_and_style_rules.md           # 5-audience matrix & natural prose rules
│   └── evidence_ladder_and_omission_rules.md        # 4-tier evidence ladder & trace schema
├── examples/
│   └── minimal_workflow_example.md                  # Illustrative synthetic walkthrough
└── scripts/
    ├── translational_fidelity_auditor.py            # Core fidelity, ladder, prose & review engine
    ├── preflight_probe.py                           # Sandbox probe (M1)
    ├── result_guard.py                              # Atomic result.json guard (M2)
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

- Supports UTF-8 text/Markdown source and target files (Chinese, English, and bilingual scientific prose).
- Automated checks catch structural, numerical, evidentiary, and stylistic violations; final domain analogies and press/regulatory sign-off remain with the original researcher via `side_by_side_review.md`.
