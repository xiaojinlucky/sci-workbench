# Cross-Disciplinary 4-Tier Evidence Ladder, Numeric/Display Conservation & Paragraph Trace Protocol

Consult this reference when classifying source evidence strength, verifying numerical/p-value/display conversions, and finalizing `/app/output/paragraph_trace.json`.

## 1. Cross-Disciplinary Four-Tier Evidence Strength Ladder

Classify the inspected source manuscript by its **highest substantiated evidence tier** (1 = lowest translational readiness, 4 = highest clinical/industrial confirmation):

| Tier | Identifier | Biomedical / Clinical Evidence | Engineering / Materials / Patent Evidence | Required Preserved Boundary | Forbidden Escalation Examples |
| --- | --- | --- | --- | --- | --- |
| 1 | `in_vitro_or_computational` | Cell lines (HeLa, A549, HEK293), biochemical assays, docking, bioinformatics | DFT/MD simulations, finite-element modeling, purely theoretical calculation | Explicitly state "in vitro / cell model / simulation"; not yet tested in vivo or hardware | Claiming animal efficacy, patient cure, or industrial hardware performance |
| 2 | `animal_or_preclinical` / `bench_or_preclinical` | Mouse/rat/zebrafish models, xenografts, organoids, preclinical toxicology | Lab-scale prototypes, patent `实施例` & `对比例`, single-cell or coupon tests | Explicitly state animal species or lab/bench-scale patent example boundary | Claiming "clinically proven", "cures patients", "已大规模量产验证", "zero commercial degradation" |
| 3 | `observational_correlational` | Retrospective charts, ecological/surveillance registries, observational cohorts, cross-sectional associations | Pilot-scale trials, field observational logs without controlled intervention | Preserve observational/ecological/correlational framing and reporting/confounder caveats | Turning association into proven causation ("proves X causes Y", "必然导致") |
| 4 | `rct_or_clinical_causal` | Randomized controlled clinical trials (Phase II/III), double-blind placebo-controlled trials | Validated multi-year industrial deployment / certified commercial production | Preserve trial phase, eligibility, follow-up window, control arm, and adverse-event/subgroup limits | Claiming universal cure across untested populations or 100% risk elimination |

**Three independent hard gates**:
1. **No tier escalation (`CLAIM_LEVEL_ESCALATION`)**: Target prose must stay at or below the source evidence tier.
2. **Proximity hedging (`UNHEDGED_TRANSLATIONAL_PROMISE`)**: When source `Tier <= 3`, translational promise phrases (`有望用于临床/患者/量产`, `promising for patients`, `clinical benefit`) must carry an explicit limiting qualifier (`尚未/未验证/仍需/临床前/实验室/观察性/preliminary/not yet`) within ±85 characters.
3. **Model boundary preservation (`MODEL_BOUNDARY_OMITTED`)**: Preserves the primary active tier boundary (`--boundary-policy primary`, default for `public_science`/`industry_pitch` to avoid defensive stacking) or all detected boundaries (`--boundary-policy all`, default for `tech_report`/`grant_brief`/`policy_memo`).

## 2. Numerical, Unit, p-Value & Display Conservation Rules

1. **Counts vs. percentages & bilingual magnitude equivalence**: Keep counts (`n = 5,539`) distinct from percentages (`53.1%`); exact scale equivalents (`12,000` <-> `1.2万`, `1,500,000` <-> `1.5 million`) are recognized automatically.
2. **Unit context binding (`NUMERIC_UNIT_MISMATCH`)**: Physical units attached to a target number must match a unit observed with that value in the source (preventing silent swaps like `38.5 %` -> `38.5 mg/mL`).
3. **Sentence-scoped derived ratios**: Any derived percentage or fold-change must show both source numerator and denominator in the same sentence within tightened tolerance (`<= 0.5%` relative or `<= 0.15` pp for 1-decimal rounding; `<= 2%` relative for fold-change).
4. **Per-value `p`-value multiset conservation**: Every target `p`-value is checked individually against the source `p`-value multiset. Conservative loosening (`p = 0.042` -> `p < 0.05`) is permitted; strengthening (`p = 0.042` -> `p < 0.01`) or fabricating an unmatched exact `p = ...` fails even when another smaller `p`-value exists elsewhere in the paper.
5. **Figure, table & citation anchor preservation**: Never hallucinate figure/table numbers absent from the source (`HALLUCINATED_FIGURE_OR_TABLE_REF`) or write anonymous references ("as shown in the figure") without the source number (`ANONYMOUS_FIGURE_TABLE_REF`). Technical/grant/policy genres require preserving display anchors; lay/pitch genres must either retain them or log omission explicitly in `paragraph_trace.json`.

## 3. Automated `paragraph_trace.json` Bootstrapping

Do not hand-author `paragraph_trace.json` from scratch. Generate the skeleton directly from `/input` via:
```bash
python3 "$SKILL_ROOT/scripts/translational_fidelity_auditor.py" \
  --source /input/<SOURCE_FILE> --audience <AUDIENCE> --extract-source-brief \
  --emit-trace-skeleton /app/output/paragraph_trace.json \
  --emit-glossary-draft /app/output/glossary_lock.json \
  --out-json /app/work/source_brief.json
```
Then refine each entry's `action` (`KEEP` | `COMPRESS` | `OMIT_REDUNDANCY`), `retained_facts`, `omitted_items`, and `keep_or_omit_reason`. Entries marked `OMIT_REDUNDANCY` must not omit sample sizes, experimental/bench models, comparison baselines, or primary limitations unless those facts appear in another retained entry.
