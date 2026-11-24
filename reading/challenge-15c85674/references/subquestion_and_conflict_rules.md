# Sub-Question Decomposition, Epistemic Tagging & Conflict Rules

## 1. Compound Question Decomposition

Scientific Q&A prompts routinely pack 2–4 distinct sub-questions into a single sentence, for example:
- `"该催化剂的最佳活性是多少？在什么温度和 pH 条件下测得？与商业对照相比提升了多少？作者指出了哪些局限性？"`
- `"What selectivity was achieved, under what operating conditions, how does it compare with the baseline, and what limitations are reported?"`

`literature_qa_engine.py` splits questions on punctuation (`？`, `?`, `；`, `;`), explicit connectors (`以及`, `并说明`, `并指出`, `同时`, `此外`, `分别说明`, `and what`, `and how`, `as well as`), and facet cues (value/metric, condition/parameter, comparator/delta, mechanism, limitation). The Agent must verify **every** resulting sub-question in `qa_retrieval_pack.json` and ensure none is dropped in `answer` or `claims[]`.

## 2. Four-Way Epistemic Distinction

1. **`reported` (Author Measured / Explicitly Stated)**:
   - Direct numerical results, experimental setups, sample sizes, table cells, footnotes, or explicit author statements.
   - Must attach >= 1 verbatim `quote` from the exact `paragraph_id`.
2. **`inferred` (Transparent Cross-Paragraph Synthesis)**:
   - Logical deduction combining two or more `reported` facts (for example computing a difference between Table 1 and Methods, or synthesizing a scope limitation across Results and Discussion).
   - Must explicitly cite the supporting `paragraph_id`s and state the inference step without disguising it as a direct quote.
3. **`conflict` (Internal Source Discrepancy)**:
   - Occurs when Abstract, Results, Tables, or Discussion state incompatible numbers (for example Abstract reports `88.5%` while Table 2 reports `84.2%` for the same sample/condition) or opposing significance/polarity (`significantly improved` vs `no statistically significant difference`).
   - Check `conflict_candidates[]` in `qa_retrieval_pack.json`. When confirmed, emit **two** `reported` claims citing both `paragraph_id`s and verbatim quotes, explain the discrepancy transparently in `answer`, and record the unresolved discrepancy in `unanswered_parts[]` if it prevents a single definitive value.
4. **`insufficient_evidence` (Unreported / Out-of-Scope)**:
   - Never silently bridge missing facts with `inferred`. State clearly what is unreported.

## 3. Preserving Conditions & Quantitative Fidelity

- Always pair every numerical claim with its exact unit (`mg` vs `μg`, `mV` vs `V`, `wt%` vs `at%`, `°C` vs `K`), comparator (`vs. baseline`, `vs. placebo`), sample size (`n = ...`), and scope qualifier (`in vitro`, `at pH 7.4`, `after 24 h`).
- The `--validate-answers` gate parses `(number, canonical_SI_unit)` pairs from `conditions[]` and checks them against all cited evidence paragraphs for that question. Any unit mismatch or ungrounded condition number fails validation immediately.
