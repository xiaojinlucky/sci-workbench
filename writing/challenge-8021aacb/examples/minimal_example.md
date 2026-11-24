# Minimal Usage Example (Illustrative Synthetic Sample — Not a Formal Test Case)

## Sample Input (`/input/sample_draft.md`)

```markdown
# Introduction
We investigate whether low-temperature annealing improves catalyst durability.

# Related Work
Smith et al. (2019) annealed Cu catalysts at 300 K and observed selectivity increased to 82%.
Chen et al. (2020) tested Cu catalysts at 420 K and reported selectivity decreased to 54%.
Patel et al. (2021) evaluated Cu catalysts at 500 K and measured selectivity increased to 79%.

# Methods
This is an observational retrospective cohort analysis of 40 archived samples (n = 40).

# Results
Catalyst lifetime correlated with grain size (p = 0.03). Therefore, annealing is superior to all existing methods.

# Conclusion
Our retrospective correlation proves that annealing directly causes universal stability across all industrial reactors.
```

## Command

```bash
python3 scripts/structural_logic_engine.py \
  --manuscript /input/sample_draft.md \
  --case-id synthetic-demo-01 \
  --out-dir /app/output
```

## Illustrative Output Snippet (`editing_action_plan.md`)

- `[BLOCKING] EDIT-001 — REGROUP_BY_DIMENSION (D2_section_structure_narrative_order)`:
  Paragraphs `S2.P1,S2.P2,S2.P3` (`L5-L7`) list studies chronologically `[2019, 2020, 2021]` without comparative synthesis -> regroup by annealing temperature regime.
- `[BLOCKING] EDIT-002 — DEFER_TO_AUTHOR (D3_argument_evidence_chain)`:
  Same-metric contradiction on `selectivity`: `82% (positive)` vs `54% (negative)` without reconciling temperature condition differences.
- `[BLOCKING] EDIT-003 — NARROW_CLAIM (D5_conclusion_boundary_epistemic_scale)`:
  `S5.P1` (`L15-L15`) escalates retrospective correlation into causal claim (`proves ... directly causes`) and universal extrapolation (`across all industrial reactors`) -> narrow scope and add Limitations.
