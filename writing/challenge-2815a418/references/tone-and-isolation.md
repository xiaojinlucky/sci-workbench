# Four-Tier Academic Tone, Reviewer Isolation, and Anti-AI Prose Discipline

## 1. Four-Tier Tone Calibration

Choose the tone tier matching each comment's disposition:

| Tone Tier | Mapped Dispositions | Required Opening & Core Structure |
| --- | --- | --- |
| `ACCEPT` | `ACCEPT_TEXT`, `SOFTEN_CLAIM` | Direct agreement + exact action taken + location anchor `[§Section Lx-Ly]` + verbatim revised excerpt (`> ...`). |
| `CLARIFY` | `CLARIFY_EXISTING` | Direct factual answer first + acknowledgment that original presentation can be clearer + location anchor + clarification excerpt. Never blame the reviewer for missing existing text. |
| `POLITE_DISAGREE` | `DISAGREE_WITH_EVIDENCE`, `PARTIAL` (declined sub-item) | Narrow acknowledgment of concern + polite evidence-based disagreement ("We respectfully disagree on [narrow point] because [manuscript/scope evidence]") + explicit boundary/limitation revision if helpful. |
| `APPEAL` | Escalated methodological dispute (only when explicitly requested with strong evidence) | Formal editorial framing + side-by-side factual record + narrow request for editorial adjudication, free of ad hominem language. |

## 2. Calibrated Claim-Strength Verbs

Match verb strength to supplied evidence:
- **Direct empirical result**: `demonstrated`, `measured`, `observed`, `showed`
- **Moderate / model-based support**: `indicated`, `suggested`, `supported`
- **Associative / bounded evidence**: `was associated with`, `was consistent with`, `raised the possibility that`

When a reviewer challenges causality and the baseline reports observational or correlational data, replace causal verbs (`caused`, `drove`, `proved`) with associative wording before quoting the revised text.

## 3. Reviewer Isolation Rules

- Keep each reviewer's responses self-contained.
- Inside `R1.*` replies, never cite `Reviewer 2`, `R2`, `the other reviewer`, or `see our response to Reviewer 2` (and symmetrically for `R2.*`, `R3.*`, Chinese `审稿人1/2`).
- When two reviewers ask related or conflicting questions, execute one consistent edit in `manuscript-revised.md` and explain it independently under each reviewer's comment ID.

## 4. Forbidden Rebuke, Fabrication, and AI-Boilerplate Patterns

- **Zero rebukes**: Do not write "the reviewer misunderstood", "the reviewer failed to read", "as we already stated in the manuscript", or "审稿人未仔细阅读 / 如前文早已清楚说明".
- **Zero fabricated compliance**: Do not write "we performed the additional wet-lab experiment", "a new animal cohort was collected", "the editor accepted the manuscript", or "已完成全部真实补实验 / 编辑部已接收".
- **Zero empty replies**: "Thank you for the comment. We have revised accordingly." without concrete edit details, location anchor, and quoted passage is invalid.
- **Anti-AI & anti-defensive prose (Press-Release Principle)**: Lead each answer with the concrete judgment or revision action rather than throat-clearing fillers. Eliminate stock boilerplate (`综上所述`, `值得注意的是`, `不难发现`, `众所周知`, `不是……而是……`, `显著赋能`, `深刻揭示`, `it is worth noting that`, `plays a pivotal role`, `delve into`, routine em-dash `——` overuse). Apply the anti-defensive rule: state scientific boundaries cleanly once where they alter interpretation or scope, and remove purely self-weakening apologies that neither advance the argument nor disclose a real limit.

## 5. Pre-Submission Adversarial & Pre-Mortem Gate (Self-Audit Before Sealing)

Before finalizing `response-letter.md` and `manuscript-revised.md`, run a quick adversarial pass assuming the revision could be rejected on a second review:
- **Blocking vs Optional separation**: Resolve every blocking flaw (unanswered sub-item, anchor-quote mismatch, unchanged baseline quoted as edited, unlogged missing evidence, causal overclaim, cross-reviewer leak) before polishing phrasing.
- **Pre-mortem top-3 failure check**:
  1. *Could an editor open the claimed line `[§Section Lx-Ly]` and fail to see the quoted edit?* -> Verify via `rebuttal_closure_checker.py`.
  2. *Could a reviewer read a `PARTIAL` / `AUTHOR_INPUT_NEEDED` reply as an empty promise or evasive dismissal?* -> Confirm all 3 missing-evidence elements (boundary rationale + existing-material alternative + concrete author confirmation).
  3. *Could any claim in `manuscript-revised.md` exceed the supplied baseline evidence?* -> Confirm verb strength matches the actual data design.
