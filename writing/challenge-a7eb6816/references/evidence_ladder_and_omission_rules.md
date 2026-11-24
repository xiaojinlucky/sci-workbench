# Four-Tier Evidence Ladder, Numeric Conservation & Paragraph Trace Protocol

Consult this reference when classifying source evidence strength, verifying numerical conversions, and building `/app/output/paragraph_trace.json`.

## 1. Four-Tier Evidence Strength Ladder

Classify the inspected source manuscript by its **highest substantiated evidence tier** (1 = lowest translational readiness, 4 = highest clinical/causal confirmation):

| Tier | Identifier | Qualifying Source Evidence | Required Preserved Boundary | Forbidden Escalation Examples |
| --- | --- | --- | --- | --- |
| 1 | `in_vitro_or_computational` | Cell lines (HeLa, HEK293, primary cells), biochemical assays, molecular docking, in silico/ML predictions | Explicitly state "in vitro / cell model / computational prediction"; not yet tested in animals or humans | Claiming animal efficacy, patient benefit, or clinical readiness |
| 2 | `animal_or_preclinical` | Mouse/rat/zebrafish/primate models, xenografts, organoids, preclinical toxicology | Explicitly state animal/preclinical model species; human safety/efficacy unverified | Claiming "treats patients", "clinically proven", "cures human disease", "ready for hospital use" |
| 3 | `observational_correlational` | Retrospective charts, cross-sectional surveys, single-center or multi-center observational cohorts, correlation/association metrics (`r`, OR, HR without randomization) | Preserve correlational/observational framing ("associated with", "correlated with", "suggests"); retain cohort/confounder scope | Converting association into proven causation ("proves that X causes Y", "guarantees prevention", "必然导致") |
| 4 | `rct_or_clinical_causal` | Randomized controlled clinical trials (Phase II/III), double-blind placebo-controlled human trials, direct causal intervention | Preserve trial phase, eligibility criteria, follow-up window, control arm, and adverse-event/subgroup limits | Claiming universal cure across untested populations or 100% efficacy |

**Two independent hard gates**:
1. **No tier escalation (`CLAIM_LEVEL_ESCALATION`)**: Target prose must stay at or below the source evidence tier.
2. **No silent boundary drop (`MODEL_BOUNDARY_OMITTED`)**: When the source is Tier 1, Tier 2, or Tier 3, the target draft must explicitly name its model/observational boundary at least once; omitting the species or correlational qualifier to make prose "smoother" fails the gate.

## 2. Numerical, Magnitude & Statistical Conservation Rules

1. **Counts vs. percentages**: Keep counts (`n = 48`) distinct from percentages (`48%`).
2. **Bilingual magnitude equivalence**: Equivalent scale re-expressions are allowed when exact:
   - `12,000` <-> `1.2万` <-> `12 thousand`
   - `1,500,000` <-> `150万` <-> `1.5 million`
   - `230,000,000` <-> `2.3亿` <-> `0.23 billion`
3. **Derived ratios & fold-changes**: If the adapted draft introduces a derived figure (e.g., `50%` computed from `24` out of `48`, or `2x` from `40` vs `20`), include both source numbers in the same sentence or parenthetical (`24/48，即 50%`) so a reader and the auditor can verify the arithmetic directly.
4. **Statistical thresholds & comparison baselines**:
   - Never strengthen a `p`-value by rounding (e.g., source `p = 0.048` rewritten as `p < 0.01`).
   - Preserve comparison baselines (`vs vehicle control`, `compared with standard chemotherapy`, `与对照组相比`) so relative improvements are never detached from their denominator or reference arm.

## 3. `paragraph_trace.json` Schema

Write `/app/output/paragraph_trace.json` as an object with a `paragraphs` array. Every source paragraph or logical section must have one entry:

```json
{
  "source_file": "paper.md",
  "audience": "public_science",
  "paragraphs": [
    {
      "paragraph_id": "SRC-P01",
      "source_section": "Results 2.1",
      "source_excerpt": "In A549 lung cancer cells, compound K12 correlated with a 42% drop in viability (n=6 replicates, p=0.032 vs DMSO).",
      "action": "COMPRESS",
      "target_location": "adapted_article.md#Section-2",
      "retained_facts": [
        "A549 lung cancer cell line (in vitro)",
        "42% viability reduction vs DMSO control",
        "n=6 replicates, p=0.032"
      ],
      "omitted_items": [
        "plate reader optical density wavelength details"
      ],
      "keep_or_omit_reason": "Retained cell-line boundary, core effect size, and control arm; compressed assay instrumentation detail for public readability.",
      "author_check_needed": false
    }
  ]
}
```

- Allowed `action` values: `"KEEP"`, `"COMPRESS"`, `"OMIT_REDUNDANCY"`.
- Entries marked `"OMIT_REDUNDANCY"` must not list sample sizes, experimental models, primary limitations, or negative outcomes in `omitted_items` or `source_excerpt` unless those exact facts are explicitly preserved in another retained paragraph.
