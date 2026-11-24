# Grant Proposal 7-Element Drafting & Workorder Audit Skill

## 1. What This Skill Does
Drafts editable, source-grounded 7-element grant proposal chapters and runs deterministic offline verification against runtime `case.json` and `organizer-workorder.json` constraints (`grant-proposal-7element-drafting`).

## 2. When to Use
- Authoring or revising grant proposal chapters from fixed source papers, preliminary notes, and structured organizer/RFP workorders.
- Enforcing strict boundaries between external published findings and missing applicant preliminary evidence, resource proof, or real funding-agency guidelines.
- Verifying 1:1 structural closure (`G1–G3 -> WP -> Milestones -> Metrics`), planning-unit/timeline arithmetic, risk fallbacks, human confirmation items, RFP completeness, collaborator conflict tracking, and anti-AI/anti-hype prose discipline.

## 3. Package Layout
| Path | Role |
|---|---|
| `SKILL.md` | Agent workflow, hard invariants, and CLI contract |
| `scripts/grant_7element_auditor.py` | Pure Python 3.12 stdlib offline auditor (workorder parser, dual anti-fabrication gate, 6 supporting-file verifier, arithmetic/matrix checker, style auditor, contract helper) |
| `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` | Standard Harbor M1–M6 runtime guards |
| `references/grant_craft_and_anti_ai.md` | Hypothesis structure, Aim-evidence mapping, Press-Release anti-defensive rules, Pre-mortem risk design, anti-AI rewrite guide |
| `references/workorder_and_output_contract.md` | Workorder schema, `grant_plan.json` contract, 6 supporting deliverables, `result.json` 5-field `output_contract` |
| `examples/minimal_call_example.md` | Synthetic input/command/output example |

## 4. Runtime Requirements & Boundaries
- **Dependencies**: Python 3.12 standard library only (zero third-party packages, zero network calls).
- **Boundaries**: The auditor verifies structural, numeric, provenance, conflict-logging, and linguistic compliance against supplied `/input` files; it does not authenticate external funding policies or fabricate missing applicant credentials.
- **Verification**: Run `python3 scripts/grant_7element_auditor.py --help` to inspect all CLI options.
