# Illustrative Call & Expected Output (`challenge-ec02b694`)

> **Notice**: Illustrative example only — NOT a formal competition case or measured run.

- `interaction_and_surrogate_analyzer.py` detects **exact sign reversal**: at `knob_flow_sccm=20`, optimum is `knob_temp_c=190` (`81.7%`), whereas at `knob_flow_sccm=60`, optimum flips to `knob_temp_c=230` (`92.15%`). `r2_lift_from_interactions` jumps dramatically over the main-effects-only fit.
- `feasible_domain_validator.py` prunes `v1_unsafe_high` (`250 C > safety_max 240 C` and breaches `heater_duty_cap <= 265`).
- `candidate_plans_ranker.py` identifies both `v2_low_cost_safe` and `v3_interaction_joint_pareto` on the **Pareto frontier** (cost vs response), recommending `v3_interaction_joint_pareto` subject to independent confirmatory execution and EHS sign-off.
