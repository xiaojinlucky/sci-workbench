# Thesis & Research Proposal Convergence and Feasibility Skill

## 1. What This Skill Does
Converts research guidelines, literature notes, and resource/timeline constraints into an editable, feasible 7-element thesis/research proposal (`research-proposal-planner`) and runs deterministic offline validation across DAG topology, Critical Path Method (CPM), parallel workload, budget arithmetic, unavailable-resource convergence, 4-category risks, prose↔JSON consistency, and claim-citation grounding.

## 2. When to Use
- Converging a broad or under-specified research topic into 1–3 falsifiable research questions and quantified objectives.
- Verifying schedule/dependency DAG feasibility, critical path duration, parallel monthly effort, and strict budget arithmetic.
- Handling real-world missing samples or unavailable instruments by narrowing scope and enforcing `resource_gap -> trigger_signal -> decision_checkpoint -> fallback_plan`.
- Producing editable `.docx` and standalone `.html` deliverables in zero-dependency offline Linux environments.

## 3. Package Layout
| Path | Role |
|---|---|
| `SKILL.md` | Agent workflow, hard invariants, and CLI contract |
| `scripts/proposal_feasibility_checker.py` | Pure Python 3.12 stdlib feasibility auditor (7-element substance, `已知—未知—待验证`, DAG+CPM+parallelism, budget guard, 4-category/resource-gap gate, prose↔JSON cross-check, stdlib OOXML `.docx` + HTML exporter) |
| `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` | Standard Harbor M1–M6 runtime guards |
| `references/opening_convergence_and_mainline.md` | Question convergence, 4-question mainline pruning, Aim-Figure mapping, shortest evidence chain, Pre-mortem risks, anti-AI prose rules |
| `references/plan_schema_and_contract.md` | `proposal_plan.json` schema, prose↔JSON parity rules, `result.json` 5-field `output_contract` |
| `examples/minimal_call_example.md` | Synthetic input/command/output example |

## 4. Runtime Requirements & Boundaries
- **Dependencies**: Python 3.12 standard library only. Uses `python-docx` if preinstalled; otherwise automatically falls back to a built-in pure-stdlib OOXML `.docx` generator plus HTML preview and reports `docx_exporter` honestly.
- **Boundaries**: Does not invent missing sample cohorts, unprovided budget ceilings, or external literature not present in `/input`.
- **Verification**: Run `python3 scripts/proposal_feasibility_checker.py --help` to inspect CLI flags.
