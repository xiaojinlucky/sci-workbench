# Bilingual Academic Translation and Parallel Writing Skill (`bilingual-academic-translation`)

Offline, deterministic EN-ZH academic translation, bilingual alignment, live-citation/reference consistency, and anti-translationese quality guard for Harbor DeepSeek Harness execution.

## Purpose and Scope

Designed for scholarly translation and bilingual parallel writing tasks requiring strict scientific fidelity and editable outputs. The skill enforces:

1. **Bidirectional magnitude, scientific notation, statistics, SI unit, and order verification**: Equivalences Chinese/English magnitudes (`千/万/亿` ↔ `thousand/million/billion`), scientific notation (`1.5e-4` ↔ `1.5×10^-4`), percentages, `p`-value operators, confidence intervals, and SI units per paragraph, catching silent group/condition value swaps while supporting explicit `reordered_clause_note` attestations for legitimate EN-ZH syntax reordering.
2. **Live citation, display, equation, and bilingual bibliography 1:1 integrity**: Audits numeric brackets (`[1]`, `[1-3]`), LaTeX `\cite{}`, author-year citations, figure/table/equation numbering (`Fig. 1` ↔ `图 1`, `Table 2` ↔ `表 2`), math blocks (`$...$`, `$$...$$`), and reference-list DOI/year/author/title alignment—catching orphaned Chinese in-text citations against English bibliographies and cross-entry translated title swaps.
3. **Negation scope and epistemic hedge conservation**: Blocks negation polarity flips and correlation-to-causation overclaim escalation (`may suggest` / `associated with` escalated to `证明了` / `直接导致`).
4. **Glossary lock and anti-translationese audit**: Enforces canonical term locking across sections and scans target prose for translationese and AI clichés with exact line/paragraph locations without false-positiving on technical terms.
5. **Automated exports**: Generates editable `bilingual_aligned.md`, `translation_audit.json`, and a 5-field `result.json` contract patch.

## Directory Layout

- `SKILL.md`: Agent execution protocol, invariants, and completion criteria.
- `scripts/bilingual_alignment_guard.py`: Zero-dependency Python 3.12 alignment, magnitude, citation, bibliography, negation, glossary, and style auditor.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Common Harbor M1-M6 runtime guards.
- `references/bilingual-fidelity-rules.md`: Magnitude tables, `reordered_clause_note` syntax, bibliography sync, and hedge rules.
- `references/academic-chinese-style.md`: Anti-translationese and natural scholarly prose guide.
- `references/output-contract-and-qa.md`: Exact 5-field `result.json` schema and pre-seal QA checklist.
- `examples/synthetic_example.md`: Minimal illustrative interface example.

## Dependencies and Runtime Requirements

- Python 3.12 standard library only (`argparse`, `json`, `math`, `re`, `pathlib`, `collections`). Zero third-party packages and zero network access required.

## CLI Usage

```bash
python3 scripts/bilingual_alignment_guard.py \
  --source /input/source.md \
  --target /app/output/translated.md \
  --glossary /app/work/glossary.json \
  --references /app/work/reference_map.json \
  --case-id "<CASE_ID>" \
  --out-json /app/output/translation_audit.json \
  --out-aligned /app/output/bilingual_aligned.md \
  --out-contract-patch /app/work/contract_patch.json
```

Exit code `0` indicates `"passed": true` (zero hard errors). Non-zero exit indicates at least one blocking numeric, order, citation, bibliography, display, equation, negation, causal-escalation, or glossary error.

## Limitations and Boundaries

- Performs deterministic structural, numerical, citation, bibliography, polarity, and lexical-style verification offline; subtle domain nuances still benefit from researcher review via the exported `bilingual_aligned.md`.
- Does not query live external DOI registries; bibliography metadata consistency is audited against the supplied source document and optional `reference_map.json`.
