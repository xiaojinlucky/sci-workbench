# Academic Language Polishing Skill (`academic-language-polishing`)

Offline, zero-dependency Python 3.12+ skill for bilingual (English/Chinese) academic manuscript polishing with hard scientific fact and modality conservation.

## Capabilities

1. **Sentence-Level Minimal Editing & Redline Ledger**: Aligns source vs. revised sentences and outputs `polishing_redline_diff.md` and `polishing_audit.json` with `sentence_id`, `original`, `revised`, `edit_type`, `rationale`, and `status` (`applied` | `rejected_false_positive` | `needs_author_confirm` | `blocked_invariant_violation`).
2. **Hard Scientific Invariant Blocking Gate**: Enforces exact conservation of numbers, percentages, units, `p`/`q`/`FDR` values and inequality signs, sample sizes (`n`), citations, figure/table anchors, negation polarity, experimental condition qualifiers, and epistemic/modality strength (blocking correlation-to-causation escalations such as `may be associated with` → `proves`/`causes`).
3. **Anti-False-Positive Grammar Guard**: Protects valid academic concessive sentences (`Although ..., ...` with finite verbs and no redundant `but`) and standard scientific passive voice in `Methods`, explicitly logging dismissed alerts as `rejected_false_positive`.
4. **Glossary & Abbreviation Consistency Rescan**: Checks full-manuscript compliance against user JSON/CSV glossaries, detects cross-section hyphenation/UK-US spelling drift, and enforces abbreviation first-definition rules.
5. **Whitelist Bilingual De-AI & Press-Release Anti-Defensive Audit**: Strips verifiable AI clichés, literal translationese, and self-undermining phrasing in primary `Results`/`Abstract` without touching valid scholarly prose.

## Directory Layout

- `SKILL.md`: Agent execution protocol, trigger boundaries, and Harbor workflow.
- `scripts/polishing_invariant_engine.py`: Core polishing auditor, aligner, and invariant gate.
- `scripts/writing_io_and_invariants.py`: Shared stdlib OOXML `.docx` reader/writer, invariant extractor, and academic prose linter.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Harbor runtime guards (M1–M6).
- `references/{academic_writing_standards,polishing_playbook,output_contract_spec}.md`: Standards, register matrix, and output contract.
- `examples/minimal_example.md`: Synthetic invocation and expected audit structure.

## Requirements & Limitations

- Requires Python 3.12+ standard library only (air-gapped Linux Docker ready; no pip dependencies).
- Optional `.docx` preview rendering uses `soffice`/`pdftoppm` when installed and degrades honestly when absent.
- Deterministic invariant and lexical audits complement, rather than replace, domain-expert scientific judgment.
