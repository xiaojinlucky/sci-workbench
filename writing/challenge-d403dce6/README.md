# Grant Proposal 7-Element Drafting, Corpus Indexing & Workorder Audit Skill

## 1. What This Skill Does
Extracts structured facts and boundaries from raw `/input` documents (`.pdf`, `.docx`, `.md`, `.txt`, `.json`, `.csv`), drafts directly editable 7-element grant proposal chapters (`grant-proposal-7element-drafting`), and runs deterministic offline verification against runtime `case.json`, `organizer-workorder.json`, and `/input` source texts.

## 2. When to Use
- Authoring or revising grant proposal chapters from raw papers, patents, clinical/engineering reports, preliminary notes, and structured organizer/RFP workorders.
- Enforcing strict boundaries between external published findings and missing applicant preliminary evidence, resource proof, or real funding-agency guidelines (with sentence-level negation and quoted-example disambiguation).
- Verifying 3-tier `evidence_map` `source_ref` entries directly against `/input` corpus text with verbatim hit backfill.
- Verifying Goal → WP → Milestone → Metric coverage (supporting both strict 1:1 and multi-WP per goal), planning-unit/timeline arithmetic, risk fallbacks, human confirmation items, expanded workorder/RFP coverage, collaborator conflict logs, and anti-AI/anti-hype prose discipline.

## 3. Package Layout
| Path | Role |
|---|---|
| `SKILL.md` | Agent workflow, hard invariants, and CLI contract |
| `scripts/grant_7element_auditor.py` | Pure Python 3.12 stdlib offline indexer & auditor (`.pdf`/`.docx`/`.md` reader, `--extract-corpus-index`, heading + headingless semantic 7-element parser, `evidence_map` `/input` back-check, sentence-level anti-fabrication gate, expanded workorder/RFP mapper, column-anchored Markdown fallback, style scanner, contract helper) |
| `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` | Standard runtime guards (preflight, atomic result validation, budget circuit-breaker, block indexing, provenance grounding, final output audit) |
| `references/grant_craft_and_anti_ai.md` | Hypothesis structure, Aim-evidence mapping, Press-Release anti-defensive rules, Pre-mortem risk design, anti-AI rewrite guide |
| `references/workorder_and_output_contract.md` | Workorder schema, `grant_plan.json` contract, 6 supporting deliverables, `result.json` 5-field `output_contract` |
| `examples/minimal_call_example.md` | Synthetic input/command/output example |

## 4. Runtime Requirements, Known Boundaries & Maintenance
- **Dependencies**: Python 3.12 standard library only (zero third-party packages, zero network calls). Reads `.pdf` via `pdftotext` / `pypdf` / `pdfplumber` when present, falling back to pure-stdlib `zlib` PDF stream extraction; reads `.docx` via pure-stdlib OOXML XML parsing.
- **Known Boundaries**:
  - Image-only scanned PDFs without embedded text streams cannot be extracted without an external OCR layer and should be flagged via `[EVIDENCE_GAP]`.
  - Anti-hype/AI-cliché checks are lexicon-guided warnings (non-blocking unless `--strict-style`); discipline-specific terminology should be reviewed by domain researchers.
  - The auditor verifies structural, numeric, provenance, conflict-logging, and linguistic compliance against supplied `/input` files; it does not authenticate external funding policies or fabricate missing applicant credentials.
- **Version & Verification**: v3.0 (stdlib corpus indexer + `source_ref` verbatim back-check + sentence-level disambiguation). Run `python3 scripts/grant_7element_auditor.py --help` to inspect all CLI options.
