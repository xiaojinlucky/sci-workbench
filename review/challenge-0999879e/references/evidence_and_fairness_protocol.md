# Evidence-Bound Review & Fairness Protocol

This protocol governs all academic review tasks under the offline execution runtime contract. It integrates defect-first triage, figure-suite scientific integrity checks, literature evidence-chain reconstruction, CNS peer-review unit/funnel calibration, and the ACL H1-H17 fairness firewall.

## 1. Five-Gate Finding Admissibility (Defect-First Discipline)

Every reported finding MUST pass all five gates before entering the final audit report:

1. **Material Impact**: The issue materially affects scientific correctness, inferential validity, reproducibility, citation fidelity, or publication compliance.
2. **Discrete & Actionable**: The finding names a specific defect and provides a concrete fix (exact wording downgrade, missing statistic to report, broken reference to reconcile, or missing declaration to supply)—never vague advice like "improve logic" or "check formatting."
3. **Source-Bound Provenance**: The finding is grounded in the supplied manuscript/materials (file, section, page, line range, and verbatim quote <= 200 chars), or explicitly marked `[MISSING: <required item>]` when absence itself is the defect.
4. **Demonstrable Failure Mode**: The evidence basis states *why* the quoted passage or numerical mismatch breaks scientific validity, internal consistency, or an explicit venue rule.
5. **No Speculative Padding**: Do not invent findings to meet a quota. If a manuscript passes all inspected checks without substantive defects, report `no_major_defects_detected` (or `No qualifying findings`) honestly while stating exact inspection coverage and any unverified boundaries.

## 2. Four-Way Finding Taxonomy & Severity Calibration

Strictly separate defect nature from reporting gaps and stylistic suggestions:

| `finding_type` | Meaning | Typical Examples |
| --- | --- | --- |
| `factual_error` | Verifiable contradiction or mathematical/citation error | Recomputed `p` flips significance (`decision_error`); Abstract vs Results numeric conflict; dangling/retracted citation; `df > N`; group counts do not sum to total `N`; source explicitly contradicts attributed claim. |
| `insufficient_evidence` | Claim strength or design lacks supplied support (separate `method_flaw` vs `under_reported` vs `needs_reconciliation`) | Causal verb in observational study; missing randomization/blinding/control details; missing multiplicity adjustment disclosure; citation DOI exists but full source text unavailable (`source_not_provided`); figure lacks unit/error-bar definition (`unverified`). |
| `norm_violation` | Breach of explicit reporting standards, privacy/ethics rules, or venue guidelines | Unmasked PII/patient ID/host path; human/animal study lacking ethics/IRB disclosure; double-blind email leak; exceeding explicit venue word limit; vague spin wording ("marginally significant") replacing exact `p` and CI. |
| `suggestion` | Optional improvement that is NOT a rejection blocker | Adding scope/limitation caveats when claims are already appropriately hedged; clarifying sub-cohort denominators; harmonizing minor notation drift. |

**Severity Mapping (P0-P3):**
- `critical` (`P0`): Release/publication blocker—significance decision flip, fabricated/retracted core evidence, exposed patient PII or double-blind breach, human/animal study with zero ethics disclosure, core conclusion contradicted by own results.
- `major` (`P1`): Substantive scientific or compliance defect—unjustified causal overclaim on observational data, unresolved Abstract-vs-Results numerical discrepancy, missing primary control/randomization report, unaddressed prior reviewer comment in revision, image duplication/flip anomaly flagged for investigation, unattributed third-party figure reuse.
- `minor` (`P2`): Localized inconsistency or incomplete reporting that does not overturn the main conclusion—missing effect size/CI alongside exact `p`, uncited bibliography entry, minor panel-label sequence gap, missing COI/funding boilerplate.
- `suggestion` (`P3`): Constructive enhancement; never counts against `passed`.

## 3. Epistemic Honesty & Unverified Boundaries

- **Never convert `unverified` into `pass` or `violation`**: Missing source text, unreadable scanned PDF pages (`PDF_LOW_TEXT_PAGE`), absent raw data, or missing journal guidelines MUST be logged in `gaps` / `uncertainty` and marked `needs_human_review` / `unverified`.
- **Anomaly != Misconduct**: Perceptual image duplication/flipping or borderline p-value clustering is an objective technical anomaly (`needs_investigation`). Never accuse authors of intentional fraud or p-hacking from automated heuristics alone.
- **Separate Author Claims from Non-Claims**: Before raising a contradiction or overclaim, classify each sentence as (a) author's own claim, (b) cited prior literature view, or (c) explicit hypothesis, negative control, ablation, or acknowledged limitation. Never flag (b) or (c) as author self-contradiction.

## 4. CNS Peer-Review Calibration & ACL H1-H17 Fairness Firewall

**CNS Calibration Checks:**
- **Statistical Unit & Pseudoreplication**: Verify that `df` and `N` match independent experimental units (patients, animals, cohorts) rather than technical replicates/cells/slices treated as independent observations.
- **Transparent Selection Funnel**: Check whether denominators, exclusion criteria, and post-hoc vs pre-specified thresholds are disclosed consistently across Methods, Results, and Figures.
- **Anti-Over-Defense**: Focus on defects that change the core claim, interpretation, or next decision. Do not demand endless extra experiments or punish transparent exploratory framing.

**ACL H1-H17 Fairness Firewall (Forbidden as Critical/Major Defects):**
- `H1/H3`: "Unsurprising" or "not novel" without concrete prior citation proving overlap.
- `H5`: "Does not beat latest SOTA" (SOTA is neither necessary nor sufficient).
- `H6`: "Negative / null result" (valid null results are scientific contributions).
- `H7/H8/H15`: "Method is too simple" or "did not use reviewer's preferred paradigm."
- `H9/H10`: "Narrow domain" or "single non-English language."
- `H13/H14`: "Authors could run extra experiment X / compare against closed model Y" unless directly required to support an existing claim.
- `H16`: "Has limitations = defective" (explicitly disclosed limitations are rewarded, not penalized).

Any candidate issue triggering `H1-H17` must either be dropped or explicitly downgraded to `suggestion` with `firewall_check: "DOWNGRADED_BY_ACL_H<n>"`.

## 5. Figure & Multi-Panel Integrity Criteria (Cross-Modal Audit)

When figures, captions, or visual assets are present:
1. **Scientific Integrity First**: Trace units, denominators, normalization, error-bar definitions (SD vs SEM vs 95% CI), and statistical annotations between text, tables, and figure captions.
2. **Caption & Panel Linkage**: Verify every in-text reference (`Fig. 1A`, `Table 2`) maps to a defined caption/table and every caption panel letter (`A, B, C...`) follows contiguous order without missing/orphan letters.
3. **Cross-Figure Semantic Consistency**: Abbreviations, group names, and reported `n` for the same cohort must not conflict across figures or between Abstract, Results, and Figure Legends.
4. **Image Integrity Screening**: Check supplied image files for exact byte duplicates and flipped/rotated perceptual matches; report match type and Hamming distance neutrally as `needs_investigation`.
