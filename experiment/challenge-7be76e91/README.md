# Aligned Experimental Plan Comparator & Selector (`challenge-7be76e91`)

Offline-native Harbor Skill for unified horizontal comparison and defensible selection across **3+ versioned candidate experimental plans** under organizer prospective contracts.

## Included Components

- `SKILL.md`: 5-step horizontal comparison workflow, 7-dimension schema, null-vs-zero firewall, Pareto & 5-profile sensitivity protocol, and all-infeasible handling.
- `scripts/aligned_matrix_normalizer.py`: Strict null-vs-zero cost/approval/schedule normalizer, DAG critical-path calculator, parameter/safety checker, and cross-plan comparability/estimability auditor.
- `scripts/plan_choice_evaluator.py`: 4-bucket disposition engine (`selected`, `feasible_alternatives`, `rejected_hard_constraints`, `unresolved_incomplete_evidence`), Pareto dominance pairs, 5-profile weight-perturbation rank stability, and 1-factor budget/deadline flip sensitivity.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit}.py`: Deterministic Harbor runtime & grounding gates.
- `references/` & `examples/`: Unified matrix specification, JSON schemas, and illustrative missing-vs-zero example.

## Dependencies

Python 3.10+ standard library only.
