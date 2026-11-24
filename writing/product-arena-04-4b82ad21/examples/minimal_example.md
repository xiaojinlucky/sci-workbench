# Minimal Usage Example (Illustrative Synthetic Sample — Not a Formal Test Case)

## Sample Input

- `/input/literature.json`: Three narrow-scope prior papers with conflicting outcomes (e.g., `Li2021` positive on synthetic low-noise data; `Park2022` null on clinical high-noise cohort; `Gomez2023` positive on single-site small cohort).
- `/input/methods_results_summary.md`: Author's actual method and Table 1 results on two public datasets.
- `/input/intro_draft.md`: Draft opening with "In recent years, graph models have attracted widespread attention", chronological per-paper summaries, an unbounded "no prior work has ever studied this", and an overclaimed contribution citing an absent `Figure 4` and unverified `99.1%` accuracy.

## Command

```bash
python3 scripts/intro_related_work_auditor.py \
  --draft /input/intro_draft.md \
  --bib /input/literature.json \
  --evidence-dir /input \
  --case-id synthetic-demo-04 \
  --out-dir /app/output
```

## Illustrative Output Highlights (`gap_contribution_alignment.md`)

- Flags `HOLLOW_GENERIC_OPENER` on line 2.
- Flags `CHRONOLOGICAL_SUMMARY_STITCHING` and `UNRECONCILED_PRIOR_WORK_DIVERGENCE` across the three conflicting narrow-scope studies.
- Flags `UNSUBSTANTIATED_GAP_CLAIM` on "no prior work has ever studied this".
- Flags `OVERCLAIMED_CONTRIBUTION` on `CONTRIB-02` referencing absent `Figure 4` and ungrounded `99.1%`.
