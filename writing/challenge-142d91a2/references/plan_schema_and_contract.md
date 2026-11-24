# `proposal_plan.json` Schema, Prose Reconciliation & Output Contract

## 1. Structured `proposal_plan.json` Schema
Write `/app/output/proposal_plan.json` with exact fields verified by `proposal_feasibility_checker.py`:
```json
{
  "case_id": "CASE-01",
  "total_months": 12,
  "budget_limit": 80000,
  "budget_limit_source": "supplied_in_case_json",
  "budget_total_declared": 76000,
  "max_parallel_workload": 3.0,
  "research_questions": [
    {"rq_id": "RQ1", "question": "...", "falsifiable_criterion": "..."}
  ],
  "objectives": [
    {
      "objective_id": "O1",
      "rq_id": "RQ1",
      "title": "...",
      "metric": "Flexural strength gain (%)",
      "target_value": ">= 20% over unfilled baseline (evaluation criterion)",
      "sample_or_unit": "n=6 independent specimens per build orientation",
      "control_or_baseline": "Solid unfilled ABS baseline printed on same plate",
      "checkpoint_month": 4,
      "planned_figure": "Fig. 1"
    }
  ],
  "route_steps": [
    {
      "step_id": "S1",
      "objective_id": "O1",
      "title": "Baseline printing and void-geometry design",
      "inputs": ["case CAD geometry", "material spec"],
      "outputs": ["printed baseline batch", "baseline mechanical table"],
      "start_month": 1,
      "end_month": 4,
      "depends_on": [],
      "effort_units": 1.0
    }
  ],
  "milestones": [
    {"milestone_id": "M1", "objective_id": "O1", "month": 4, "deliverable": "Baseline strength dataset & Fig. 1"}
  ],
  "budget_items": [
    {"item": "Consumables and resin", "amount": 32000, "basis": "Planned material estimate"}
  ],
  "resource_gaps": [
    {
      "gap_id": "RG1",
      "resource": "High-temperature fatigue chamber (unavailable in /input)",
      "scope_adjustment": "Converge O3 to ambient-temperature cyclic testing",
      "trigger_signal": "Shared facility access not confirmed by M4",
      "decision_checkpoint": "M4",
      "fallback_plan": "Use room-temperature three-point bending endurance protocol and mark thermal fatigue as [EVIDENCE_GAP]"
    }
  ],
  "risks": [
    {"risk_id": "R1", "category": "data", "description": "...", "trigger_signal": "...", "decision_checkpoint": "M3", "fallback_plan": "..."},
    {"risk_id": "R2", "category": "method", "description": "...", "trigger_signal": "...", "decision_checkpoint": "M5", "fallback_plan": "..."},
    {"risk_id": "R3", "category": "schedule", "description": "...", "trigger_signal": "...", "decision_checkpoint": "M6", "fallback_plan": "..."},
    {"risk_id": "R4", "category": "ethics_compliance", "description": "...", "trigger_signal": "...", "decision_checkpoint": "M2", "fallback_plan": "..."}
  ],
  "claim_citations": [
    {"claim_id": "C1", "claim": "...", "section": "研究背景与现状缺口", "source_ref": "C01-PAPER (DOI: 10.1371/journal.pone.0122915)"}
  ],
  "human_confirmations": [
    {"confirm_id": "HC1", "item": "Confirm actual lab equipment access and budget ceiling with advisor"}
  ]
}
```

## 2. Prose ↔ JSON Reconciliation Rules
`proposal_feasibility_checker.py` cross-checks `proposal.md` against `proposal_plan.json` to block "JSON passes while prose contradicts":
1. **Duration consistency**: Any explicit total duration statement in `proposal.md` (e.g., `总周期 12 个月` / `研究周期：18个月` / `12-month timeline`) must match `proposal_plan.json["total_months"]`.
2. **Budget consistency**: Any explicit budget total or ceiling stated in `proposal.md` (e.g., `总预算 76000` / `预算上限 80000`) must match `budget_sum` / `budget_limit` within rounding tolerance.
3. **Objective ID parity**: Objective IDs referenced in `proposal.md` (`O1/O2/O3` or `目标1..N`) must match `proposal_plan.json["objectives"]`.
4. **Milestone month bounds**: Every milestone month marker (`M4`, `第 12 个月`) in `proposal.md` must stay inside `[1, total_months]` and match the corresponding JSON milestone when linked by ID.

## 3. Budget Anti-Fabrication Rule
- If `/input/case.json` supplies a numeric `budget_limit`, both `proposal_plan.json` and `proposal.md` must respect it.
- If `/input/case.json` does **not** supply a `budget_limit` (or explicitly sets `budget_limit: null` / `budget_limit_supplied: false`), set `"budget_limit": null` and `"budget_limit_source": "not_supplied_in_input"`, and mark `[EVIDENCE_GAP: 预算上限未在输入中提供，待导师/项目指南确认]`. Never invent a fake agency cap.

## 4. `result.json` Five-Field `output_contract`
Before `result_guard.py --finalize`, populate all 5 contract keys alongside Harbor standard keys:
- `case_id`: copied verbatim from `/input/case.json`.
- `deliverable`: paths and summary of `proposal.md`, `proposal.docx`, `proposal_preview.html`, `proposal_plan.json`, and `proposal_audit.json`.
- `preserved_facts`: verified figures, units, constraints, and source IDs retained verbatim from `/input`.
- `evidence_map`: claim-to-source mapping linking every quantitative claim to `/input` files or `[EVIDENCE_GAP]`.
- `change_log`: scope convergence decisions, resource-gap fallbacks, human confirmation items, and unresolved gaps.
