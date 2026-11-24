# Research Opening Convergence, Mainline Pruning, and Risk Design

Use this reference when narrowing a broad topic into a feasible research proposal before running `proposal_feasibility_checker.py`.

## 1. Converging a Broad Direction into 1–3 Falsifiable Questions
Avoid treating a literature review as a chronological summary or claiming "nobody has studied X":
- Derive the gap from **three concrete sources**: (a) contradictory findings across conditions in `/input`, (b) narrow applicability boundaries of prior protocols, or (c) an explicit mechanism/parameter link left untested.
- Express `研究背景与现状缺口` in three explicit moves:
  1. **已知 (Known)**: Quantified consensus and boundary conditions from named `/input` papers.
  2. **未知 (Unknown)**: Specific contradiction, unmeasured regime, or missing causal/mechanistic bridge.
  3. **待验证 (To Verify)**: Exact converged scope of this proposal (`1–3` answerable questions), explicitly stating what is excluded when samples/resources are unavailable.
- **Reject overbroad plans**: Never propose to "explain all influencing factors" (`解释所有影响因素`). If a key sample, clinical cohort, or instrument is unavailable in `/input`, narrow the question to the accessible proxy/benchmark system and attach a resource-gap stop/fallback condition.

## 2. Mainline Four-Question Pruning & Aim-Figure Mapping
Before including any experiment, dataset, or computation in `技术路线`, apply the four-question pruning filter:
1. Which specific research question (`RQ1–RQ3`) does this step directly answer?
2. Which planned conclusion does it establish or falsify?
3. Which planned deliverable / Figure (`Fig. 1`, `Table 1`, benchmark report) will carry its output?
4. If this step is removed, does the core logical chain still stand?

If step 4 is "yes", move it to supplementary work or cut it from the main schedule to protect the Critical Path and budget.

### Aim-to-Figure Mainline Table
| Research Question | Objective (`O1–O3`) | Unit of Inference & Control | Main Method (`route_steps`) | Quantified Criterion & Checkpoint | Planned Figure / Deliverable |
|---|---|---|---|---|---|
| RQ1 | O1: Characterize baseline regime | Independent batches / cohorts + baseline control | S1 -> S2 | Target metric threshold at M4 | Fig. 1: Baseline characterization |
| RQ2 | O2: Test mechanistic / design driver | Factorial / perturbation arm vs. sham control | S3 -> S4 | Effect size or error bound at M8 | Fig. 2: Mechanism / parametric response |
| RQ3 | O3: Validate robustness under boundary | Held-out replicate set / stress condition | S5 | Reproducibility bound at M12 | Fig. 3: External / stress validation |

## 3. Preliminary Foundation Four-Question Test
When evaluating existing pilot data or baseline papers under `/input`:
1. **Observed**: Exact trend, variance, effect direction, and sample count.
2. **Credibility**: Controls, replicates, confounders, and statistical basis actually reported.
3. **Direct implication**: Which design parameter or hypothesis branch it constrains.
4. **Critical gap**: Only the missing evidence that would flip a core decision—marked with `[EVIDENCE_GAP: ...]` and linked to a stop/fallback condition.

## 4. Shortest Evidence Chain & Stop Conditions for Missing Inputs
- Define the **unit of inference** explicitly (biological donor, print build plate, experimental run, patient cohort) so sample-size claims do not conflate technical replicates with independent units.
- Pair every dependent step with an explicit **stop/fallback condition**: if required data/samples are missing at the decision checkpoint, halt only the dependent branch, trigger the documented fallback (`fallback_plan`), and preserve unaffected deliverables.

## 5. Four-Category Pre-Mortem Risk Matrix
Assume the proposal misses its midpoint milestone and work backward to cover all four mandatory categories:
1. `data`: sample/dataset unavailability, quality attrition, batch shift.
2. `method`: model non-convergence, assay sensitivity ceiling, fabrication/protocol failure.
3. `schedule`: critical-path bottleneck, equipment queue delay, parallel workload overload.
4. `ethics_compliance`: IRB/animal ethics delay, data-sharing/privacy restriction, biosafety/chemical compliance.

Every risk row must specify: `Category | Failure Mode | Earliest Trigger Signal | Decision Checkpoint (Month) | Concrete Fallback Plan`.

## 6. Assertive Academic Prose & Anti-AI Discipline
- Open `研究背景`, `研究目标`, and `预期成果` with the scientific question, target evidence, and design logic—never with defensive apologies (`遗憾的是`, `本研究仅`).
- State each real limitation or `[EVIDENCE_GAP]` once, right where it governs scope or fallback decisions.
- Eliminate unverifiable hype (`国际领先`, `填补空白`, `尚无研究`, `首次`) and AI filler (`综上所述`, `值得注意的是`, `显著赋能`, `为未来研究奠定坚实基础`) without altering any numeric value, statistical statement, or uncertainty tag.
