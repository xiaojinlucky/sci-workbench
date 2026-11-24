---
name: scientific-claim-fact-verifier
description: Audit scientific claims in /input/claims-to-verify.json as untrusted questions by splitting compound claims into atomic sub-claims (fact, comparison, causation, scope, quantitative) with 5D E/M/L/S/I fingerprints, binding strictly to cited DOIs/sources, separating 4 diagnostic failure modes (doi_exists_but_unsupported, wrong_page_or_anchor_mismatch, fulltext_unavailable_or_figure_caption_missing, inline_vs_structured_citation_drift), and issuing grounded 4-state verdicts.
---

# Scientific Claim Evidence Tracing & Fact Verification (`challenge-e5e3be30`)

## 1. Scope & Non-Negotiable Invariants
This skill implements Organizer Revision v2 for literature evidence tracing and fact verification in offline sandboxes. Every audit must satisfy six invariants:
1. **Audit Questions, Never Priors**: Treat every entry in `/input/claims-to-verify.json` strictly as an unverified question to audit against actual corpus text—never assume a claim is true because it appears plausible or matches a topic label.
2. **Atomic Decomposition + 5D E/M/L/S/I Fingerprint**: Split compound statements into minimal checkable atomic sub-claims (`Cxx.y`), classify each into `{fact, comparison, causation, scope, quantitative}`, and preserve comparator (`A vs B`), negation polarity, statistical significance (`p`, `CI`, `FDR`), and scope conditions (`species`, `dosage`, `cohort`, `window`).
3. **Strict 4-State Entailment**: Every sub-claim and claim verdict belongs strictly to `{supported, partially_supported, contradicted, insufficient_evidence}`.
4. **Extrapolation != Contradiction**: If the cited source never tested an extrapolated population, species, dose, or mechanism, classify it as `insufficient_evidence`—never `contradicted`. Reserve `contradicted` strictly for opposing experimental evidence, explicit negation, or statistical null refutation on the matching target.
5. **Four Independent Failure Diagnostics**: Separate and explicitly label four distinct failure states rather than collapsing them:
   - `doi_exists_but_unsupported`
   - `wrong_page_or_anchor_mismatch`
   - `fulltext_unavailable_or_figure_caption_missing`
   - `inline_vs_structured_citation_drift`
6. **Zero Cross-Paper Borrowing & Secondary Citation Discipline**: When `cited_ref` specifies a DOI, paper ID, or filename, bind exclusively to that document; never borrow evidence from a neighboring paper to rescue an unsupported or missing citation. Flag secondary/indirect citations in Introductions and preserve both sides of abstract-vs-body conflicts.

## 2. Reference Disclosure (Read on Demand)
Locate sibling files relative to this `SKILL.md` directory:
- Read `references/output_contract_spec.md` when assembling or checking `/app/output/result.json`, `/app/output/verification_cards.json`, and `/app/output/verification_report.md`.
- Read `references/atomic_claim_and_four_state_rubric.md` when classifying compound claim types, assigning 4-state verdicts, distinguishing extrapolation from contradiction, or reviewing misjudgment counterexamples.
- Read `references/citation_integrity_and_conflict_protocol.md` when auditing inline `[1],[2]` vs. structured citation drift, wrong-page anchors, secondary citations, abstract-vs-body conflicts, or image/caption-missing localization limits.

## 3. Execution Workflow

### Step 1: Preflight, Budget Init & Deterministic Evidence Indexing
Run the offline stdlib scripts (supports JSON, MD, TXT, CSV, and PDF via `pdftotext` with exact page-anchored IDs `doc:p{page}-b{block}`):
```bash
python3 scripts/preflight_probe.py --out /app/work/env.json
python3 scripts/budget_guard.py --init
python3 scripts/claim_verifier.py --input /input --output /app/output --work /app/work
```
**What Step 1 produces**:
- Builds an immutable **Citation Legend** (`[1]..[N]` -> `source_document_id`, `title`, `doi`, `source_file`, `file_sha256`, `page_count`, `coverage_status`) and writes `/app/work/corpus_paragraphs.json` for full-context reading.
- Decomposes compound claims in `/input/claims-to-verify.json` across English and Chinese connectives (`;`, `and`, `while`, `whereas`, `because`, `thereby`, `outperforming`, `同时`, `然而`, `从而`, `并且`, `显著优于`) into atomic sub-claims annotated with `claim_types`, `comparator`, `negation_markers`, `significance_markers`, `scope_conditions`, and 5D `E/M/L/S/I` fingerprints.
- Binds `cited_ref` strictly to explicit document metadata (DOI from header/metadata only—never bibliography lines—or exact `source_document_id`/filename), flags all four diagnostic failure modes, detects secondary citations and abstract-vs-body conflicts, and audits inline citation markers vs. structured citation arrays.
- Writes initial `/app/output/verification_cards.json`, `/app/output/verification_report.md`, and `/app/output/result.json`.

**Step 1 completion criterion**: `env.json`, `corpus_paragraphs.json`, `verification_cards.json`, `verification_report.md`, and `result.json` exist and account for every claim in `/input/claims-to-verify.json`.

### Atomic Claim Taxonomy & Decomposition Protocol
When auditing compound statements, verify that Step 1 split every independent clause without losing its comparator, negation, significance threshold, or scope qualifier:

| Atomic Type | Trigger Cues | What Must Be Grounded |
| --- | --- | --- |
| `fact` | Existence, mechanism, property, or procedural assertion | Exact entity identity, section context, and primary vs. secondary attribution |
| `comparison` | `vs.`, `compared to`, `outperforming`, `higher/lower than`, `显著优于`, `相比之下` | Both arms (`A` and `B`), direction of difference, and matching evaluation metric |
| `causation` | `because`, `thereby`, `causes`, `induces`, `inhibits`, `导致`, `从而`, `抑制` | Direct intervention/causal evidence; observational correlation alone is never full causal support |
| `scope` | `all tested`, `human patients`, `pediatric`, `at pH 7.0`, `postmarketing`, `仅限`, `所有` | Exact species, population, dose, condition, and universality qualifier (`all` vs. `subset`) |
| `quantitative` | Percentages, fold changes, sample sizes (`n=`), `p < 0.05`, `95% CI`, `HR`, `FDR` | Exact number, physical unit, statistical significance, and denominator |

Preserve the 5-dimensional **E/M/L/S/I** fingerprint on every sub-claim: **E**ntity, **M**agnitude/unit/stat, **L**ogic/negation, **S**cope/condition, and **I**dentifier/comparator. If a compound claim mixes a supported factual clause with an unsupported quantitative clause or contradicted scope clause, aggregate the parent claim verdict as `partially_supported`—never `supported`.

### Step 2: Deep Semantic Entailment & Murphy's-Law Adversarial Audit (Host Agent Step)
Lexical matching in Step 1 provides grounded candidate anchors, not final semantic entailment. Read `/app/output/verification_cards.json` alongside the complete surrounding context in `/app/work/corpus_paragraphs.json`, then write the refined audit to `/app/work/verifications_draft.json`.

Audit every atomic sub-claim against the 4-state rubric:

| Verdict | Strict Entry Condition | Hard Prohibition |
| --- | --- | --- |
| `supported` | Exact cited source directly establishes entity, direction, magnitude/unit, comparator, significance, and scope with verbatim quote. | Never assign on topic/keyword overlap alone, on missing DOI, or via cross-paper borrowing. |
| `partially_supported` | Source supports part of the sub-claim/compound claim, or matches direction under narrower scope/significance, or exhibits verified internal/cross-source conflict (citing >=2 opposing anchors). | Never hide an unsupported or contradicted component inside an unqualified `supported` label. |
| `contradicted` | Target source provides direct opposing empirical data, explicit negation, or statistical null refutation (`p >= 0.05` / no significant difference) on the same target and condition. | Never assign for untested extrapolations, absent full text, or missing figure captions. |
| `insufficient_evidence` | Source is absent, DOI is unmatched, text is `abstract_only` / `figure_caption_missing`, claim extrapolates to an untested cohort/dose/mechanism, or DOI exists without supporting the claim. | Never guess missing data or backfill from an uncited neighboring paper. |

Audit and preserve all **Four Diagnostic Failure Modes** independently in `diagnostic_flags`:

| Diagnostic Flag | Exact Meaning | Mandatory Verdict Impact |
| --- | --- | --- |
| `doi_exists_but_unsupported` | Cited DOI/document exists and is readable in corpus, yet its full text does not support the asserted claim. | Blocks `supported`; forces `insufficient_evidence` (or `contradicted` only if direct opposing evidence exists). |
| `wrong_page_or_anchor_mismatch` | Cited page/paragraph anchor does not contain the claim's evidence, while matching evidence exists at a different page/paragraph in the **same** document. | Record `cited_anchor` and `actual_anchor` (`page` + `paragraph_id`); never borrow from another paper. |
| `fulltext_unavailable_or_figure_caption_missing` | Document only supplies an abstract, lacks a PDF text layer, or references a figure/image without readable caption text (`localization_insufficient: true`). | Forces `insufficient_evidence`; strictly forbids `contradicted`. |
| `inline_vs_structured_citation_drift` | Count or ID set of inline citation markers (`[1],[2]`) in claim/report text differs from the structured `structured_citations[]` array. | Logged in `citation_integrity_audit`; blocks silent pass during validation. |

Additionally verify two high-frequency scientific pitfalls:
1. **Secondary Citation Audit**: If a retrieved sentence appears in Introduction/Background citing third-party work (`Smith et al., 2019 reported...`, `previous studies showed [12]`), set `secondary_citation_warning: true` and `attribution_type: "secondary_citation"`; distinguish third-party citations from the paper's own primary measurements.
2. **Abstract vs. Body / Table Conflict**: When a paper's abstract overstates a finding relative to its Results text or Table values, set `abstract_body_conflict_detected: true`, preserve BOTH anchored verbatim quotes (`abstract` + `results/table`), and issue a qualified `partially_supported` verdict.

#### Minimal Refined Draft Layout (`/app/work/verifications_draft.json`)
```json
{
  "case_id": "case-01",
  "citation_legend": [{"citation_number": 1, "citation_tag": "[1]", "source_document_id": "DOC01", "title": "...", "doi": "...", "source_file": "..."}],
  "verifications": [
    {
      "claim_id": "C01",
      "original_claim": "...",
      "cited_ref": "10.5555/example.01, p. 4",
      "verdict": "partially_supported",
      "diagnostic_flags": {
        "doi_exists_but_unsupported": false,
        "wrong_page_or_anchor_mismatch": true,
        "fulltext_unavailable_or_figure_caption_missing": false,
        "inline_vs_structured_citation_drift": false
      },
      "subclaims": [
        {
          "subclaim_id": "C01.1",
          "text": "...",
          "claim_types": ["quantitative", "comparison"],
          "verdict": "supported",
          "verdict_rationale": "...",
          "evidence": [{"citation_number": 1, "source_document_id": "DOC01", "paragraph_id": "DOC01:p6-b2", "page": 6, "quote": "Exact verbatim substring from paragraph."}]
        }
      ]
    }
  ]
}
```

**Step 2 completion criterion**: `/app/work/verifications_draft.json` covers every claim and atomic sub-claim with exact verbatim quotes, valid `(source_document_id, paragraph_id)` anchors, accurate 4-state verdicts, explicit diagnostic flags, and synchronized inline/structured citations.

### Step 3: Strict Verification Draft Validation
Run the validator to verify `/app/work/verifications_draft.json` before promotion to `/app/output/`:
```bash
python3 scripts/claim_verifier.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --validate-verifications /app/work/verifications_draft.json
```
The validator enforces five blocking rules and exits with code `1` on any violation:

| Blocking Error Code | Trigger Condition | Required Fix |
| --- | --- | --- |
| `UNKNOWN_PARAGRAPH_ID` / `CROSS_DOCUMENT_ANCHOR_MISMATCH` | `paragraph_id` does not exist or belongs to another `source_document_id`. | Anchor strictly to the owning document's `paragraph_id`. |
| `QUOTE_MISMATCH` | `quote` is not a whitespace-normalized verbatim substring of the cited paragraph. | Copy exact substring without paraphrasing. |
| `INLINE_STRUCTURED_CITATION_DRIFT` | Inline `[n]` markers in claim/report text do not match `structured_citations[]` count or IDs. | Reconcile structured citations or record explicit drift gap before finalizing. |
| `SUPPORTED_WITHOUT_EVIDENCE` / `DOI_MISMATCH_SUPPORTED` | Verdict is `supported` with empty evidence or evidence borrowed from a non-matching DOI. | Downgrade to `insufficient_evidence` or cite exact matching DOI evidence. |
| `CONTRADICTED_WITHOUT_OPPOSING_EVIDENCE` / `CONTRADICTED_ON_LOCALIZATION_GAP` | Verdict is `contradicted` without grounded opposing evidence or when `localization_insufficient` is true. | Downgrade to `insufficient_evidence` unless direct opposing evidence is anchored. |

Re-run Step 3 after fixes until `schema_validation.ok == true` (exit code `0`).

### Step 4: Grounding Gate & Final Deliverable Audit
```bash
python3 scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py --dir /app/output --result-file result.json --require verification_cards.json --require verification_report.md
```
**Step 4 completion criterion**: Both scripts exit `0`; `/app/output/result.json`, `/app/output/verification_cards.json`, and `/app/output/verification_report.md` are valid, non-empty, and disclose all gaps honestly.

### Compound-Claim Aggregation & Dual-Anchor Conflict Discipline
Aggregate atomic sub-claim verdicts into the parent claim verdict using deterministic rules:
- All sub-claims `supported` -> parent `supported`.
- All sub-claims `contradicted` -> parent `contradicted`.
- All sub-claims `insufficient_evidence` -> parent `insufficient_evidence`.
- Any mixture (e.g., 1 `supported` + 1 `insufficient_evidence` + 1 `contradicted`) -> parent `partially_supported`, preserving every sub-claim's individual verdict and verbatim anchor rather than collapsing details.
- Whenever two sources or two sections (e.g., Abstract vs. Results/Table) disagree, emit `partially_supported` with `>= 2` opposing anchored quotes and explain the exact methodological or scope discrepancy.

## 4. Extrapolation vs. Contradiction & Evidence Chain Discipline
Every claim card must preserve an auditable evidence chain (`claim -> atomic sub-claim -> target source & anchor -> verbatim quote -> diagnostic flags -> 4-state verdict -> boundary rationale`). Apply this decision table to prevent the highest-frequency fact-verification errors:

| Scenario Observed in Corpus | Required Verdict | Mandatory Diagnostic / Action |
| --- | --- | --- |
| Exact DOI & paragraph match entity, metric, direction, comparator, significance, and scope | `supported` | Cite `(citation_tag, source_document_id, paragraph_id, page, verbatim quote)`. |
| Cited DOI exists, paper discusses related topic, but never proves the claimed effect/metric | `insufficient_evidence` | Set `doi_exists_but_unsupported: true`; never confuse topic overlap with support. |
| Cited page `p. 3` lacks the fact, while `p. 11` of the **same** document confirms it | `supported` (or `partially_supported` if qualified) | Set `wrong_page_or_anchor_mismatch: true`; report both `cited_pages` and `actual_pages`/`actual_paragraph_ids`. |
| Cited DOI is absent from corpus, even if an unrelated paper states a similar number | `insufficient_evidence` | Return `evidence: []`; zero cross-paper borrowing. |
| Mouse/in-vitro study cited to claim human clinical cure or zero human organ toxicity | `insufficient_evidence` | Set `untested_extrapolation_detected: true`; **never** label untested extrapolation `contradicted`. |
| Same cohort/comparator shows opposite direction or statistical null (`p = 0.72`, `no significant difference`) | `contradicted` | Anchor exact opposing quote + statistical value (`p`, `HR`, `CI`). |
| Source supplies abstract only, unreadable PDF (`no_text_layer`), or image without caption text | `insufficient_evidence` | Set `fulltext_unavailable_or_figure_caption_missing: true` & `localization_insufficient: true`; **never** `contradicted`. |
| Text cites `[1]`, `[2]`, `[3]` inline while `structured_citations[]` contains only 2 entries | Audit verdict + flag drift | Set `inline_vs_structured_citation_drift: true`; populate `missing_in_structured` in `citation_integrity_audit`. |
| Introduction sentence cites third-party author (`Smith et al., 2019 reported...`) | `partially_supported` (unless primary data also confirms) | Set `secondary_citation_warning: true`, `attribution_type: "secondary_citation"`. |
| Abstract asserts broad benefit while Results/Table shows null subgroup or narrower scope | `partially_supported` | Set `abstract_body_conflict_detected: true`; preserve BOTH anchored verbatim quotes. |

## 5. Pre-Exit Adversarial Self-Audit (Six-Dimension Murphy's Law Check)
Before finishing, inspect `/app/output/` across six adversarial dimensions (record every unresolved limitation honestly in `gaps[]`):
1. **Claim Decomposition Completeness**: Every compound claim in `/input/claims-to-verify.json` is decomposed into atomic sub-claims covering all clauses (`fact`, `comparison`, `causation`, `scope`, `quantitative`) without dropping negations, comparators, or significance thresholds.
2. **Source-Binding Strictness**: Zero cross-paper borrowing when `cited_ref` specifies a DOI, document ID, or filename; DOIs come solely from explicit document metadata/headers, never bibliography lines.
3. **Four-State Diagnostic Separation**: `doi_exists_but_unsupported`, `wrong_page_or_anchor_mismatch`, `fulltext_unavailable_or_figure_caption_missing`, and `inline_vs_structured_citation_drift` are evaluated and reported independently.
4. **Extrapolation Boundary**: Zero untested extrapolations or localization gaps are mislabeled `contradicted`.
5. **Quote & Anchor Grounding**: Every evidence quote is a verbatim substring of its declared `(source_document_id, paragraph_id)` and passes `--validate-verifications` with exit code `0`.
6. **Report & Guardrail Closure**: `verification_report.md` includes the immutable Citation Legend and per-claim atomic cards; `gap_gate.py` and `final_audit.py` exit `0`.

## 6. Edge-Case & Failure Handling
- **Unmatched Cited DOI**: When `cited_ref` contains a DOI absent from all supplied documents' explicit header/metadata, emit `insufficient_evidence` with `evidence: []` and zero cross-paper fallback.
- **Image-Only Figure / Missing Caption / Abstract-Only Source**: Set `fulltext_unavailable_or_figure_caption_missing: true`, `localization_insufficient: true`, and verdict `insufficient_evidence`; explicitly state what missing text or caption prevents verification.
- **Conflicting Sources**: Retain `>= 2` opposing anchored quotes (`paragraph_id`, `source_document_id`, `quote`) and emit a qualified `partially_supported` (with conflict attribution) rather than dropping one side.
- **Missing `/input/claims-to-verify.json`**: Fall back to `case.json` claims array if present and record `MISSING_CLAIMS_TO_VERIFY_FILE` in `gaps[]`.
- **Execution Budget Discipline**: Emit initial deliverables in Step 1 so partial results are persisted early; prioritize Step 2 deep context checks on compound claims, conflicts, and wrong-page candidates before running Step 3 + Step 4.

## 7. Deliverable Checklist
Confirm all three artifacts under `/app/output/` before completing the task:
- `result.json`: includes `case_id`, `status`, `citation_legend`, `verifications`, `diagnostic_summary`, `citation_integrity_audit`, `corpus_citations_extracted`, `schema_validation`, `metrics`, `metric_provenance`, `findings`, `gaps`.
- `verification_cards.json`: complete structured atomic sub-claim evidence cards with E/M/L/S/I fingerprints, diagnostic flags, secondary citation warnings, and anchor offsets.
- `verification_report.md`: human-auditable report containing Citation Legend, Verdict & Diagnostic Summary Table, Citation Integrity Audit, and per-claim atomic evidence cards.
