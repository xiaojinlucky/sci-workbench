# Citation & Fact Verification Rubric (`challenge-cbe96f01`)

This rubric provides concrete domain anchors for executing atomic sub-claim decomposition, five-state support grading, three-way reconciliation, and false-positive control.

## 1. Atomic Sub-Claim Decomposition Rules

Compound scientific sentences often combine multiple distinct propositions tied to different references or different risk profiles. Always decompose before grading:

- **Example Compound Sentence**:
  `"Prior trials showed Drug X lowered mortality by 42% [1], whereas Drug Y directly causes renal recovery in all adult patients (Smith et al., 2021) and reduced hospital stay by 3.5 days [3]."`
- **Required Decomposition**:
  1. Sub-claim 1 (`[1]`): Drug X lowered mortality by 42% -> check direction (`decrease`) and value (`42%`) against Source 1.
  2. Sub-claim 2 (`smith_2021`): Drug Y directly causes renal recovery in all adult patients -> check causal verb (`causes`) and scope (`all adult patients`) against Smith 2021 (flag `partially_supported` if Smith 2021 is observational or murine-only).
  3. Sub-claim 3 (`[3]`): Drug Y reduced hospital stay by 3.5 days -> evaluate independently against Source 3 (if Source 3 is absent, mark `source_not_provided` without contaminating Sub-claims 1 and 2).

## 2. Five-State Support Decision Matrix

| State | Criteria | Severity if Defect | Forbidden Confusion |
| --- | --- | --- | --- |
| `supported_by_source` | Source text aligned; topic, direction, quantitative values, and scope match without distortion | None (Pass) | Never assign from DOI/title match alone without reading body/abstract text |
| `partially_supported` | Core direction/topic aligns, but manuscript inflates association to causation, drops species/in-vitro/subgroup qualifiers, or supports only a subset of claimed numbers | `major` (`insufficient_evidence`) | Do not mark `contradicted_by_source` when direction holds but scope/causality is overstated |
| `contradicted_by_source` | Source text explicitly states opposite direction, null result vs claimed significant effect, or materially different numeric figure beyond rounding | `critical` (`factual_error`) | Verify sentence is not a deliberate contrast ("Unlike Ref [1], which found...") before flagging |
| `source_not_provided` | Source full text/abstract not included in `/input`, or matched PDF pages have `< 30` chars (`PDF_LOW_TEXT_PAGE`) | None (Log in `uncertainty` / `gaps`) | **Never label as fabricated, hallucinated, or `contradicted_by_source`** |
| `doi_or_metadata_only_unsupported` | (a) Source file only contains bibliographic metadata (DOI/author/title) with no substantive text; OR (b) aligned source text is topically unrelated to the attributed sub-claim | `minor` (`needs_human_review`) for metadata-only; `major` (`factual_error`) for topical mismatch | Distinguish metadata-only coverage gap from confirmed mis-citation |

## 3. Three-Way Reconciliation Checkpoints

Always audit all three layers simultaneously:
1. **In-text markers**: Numeric `[n]` / `[1-3, 5]`, LaTeX `\cite{...}`, Author-Year `(Author et al., YYYY)`, inline DOI/PMID/arXiv.
2. **Bibliography / `.bib` table**: Verify 1-to-1 key resolution, catch duplicate numbering (e.g., two `[3]` entries), dangling keys, and orphan uncited references.
3. **Resolved source objects / structured history**: If multiple distinct citation keys collapse onto a single source object, or if a structured export contains many in-text markers (e.g., 15 markers) but only 1 persisted reference object, flag the reconciliation gap explicitly as a parsing/persistence or numbering defect—never equate raw marker count with verified source count.

## 4. Publication Reliability & Cross-Section Fact Checks

- **Retraction / Withdrawal / Expression of Concern**: Always `critical` (`factual_error`). Cite the exact bibliography line and all body lines relying on that reference.
- **Erratum / Corrigendum**: `major` (`needs_human_review`). Prompt author to confirm whether corrected tables/figures affect the quoted value.
- **Preprint (`arXiv`, `bioRxiv`, `medRxiv`, `SSRN`)**: Note in `uncertainty` by default; escalate to `major` if the text describes it as peer-reviewed or clinical standard without checking for a published peer-reviewed version.
- **Bare Prior-Literature Assertions**: Flag sentences attributing concrete percentages/effects to "previous studies / prior work / 既往研究" without any citation marker, while excluding sentences anchored to the authors' own study (`our data`, `Table 1`, `Fig. 2`, `本研究`).
- **Abstract vs Body Numerical Reconciliation**: Compare total cohort `N` and anchored metric percentages (`accuracy`, `sensitivity`, `survival`, `reduction`, etc.) between Abstract and Methods/Results; distinguish total `N` from explicitly labeled subgroup `n`.

## 5. Anti-False-Positive Checklist

Before confirming any finding, verify:
- [ ] Is the sentence expressing an acknowledged limitation, hypothesis, negative control, or deliberate contrast with prior literature? If yes, do not flag as self-contradiction or mis-citation.
- [ ] Does every reported finding carry an exact file, section, line/page anchor, and verbatim quote (`<= 200` chars)?
- [ ] Are missing external papers kept in `source_not_provided` + `uncertainty` rather than turned into fabrication accusations?
- [ ] Does the revision checklist prioritize `high impact × low cost` fixes first?
