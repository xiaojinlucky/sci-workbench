# Workorder Schema, `grant_plan.json`, and Output Contract

## 1. Runtime Workorder Fields (`organizer-workorder.json` / `case.json`)
Inspect both `/input/organizer-workorder.json` (if present) and `/input/case.json["organizer_workorder"]`. Key fields parsed automatically by `grant_7element_auditor.py`:
- `authority`: e.g., `"organizer-workorder-not-funder-policy"` (never misrepresent as real agency policy).
- `actual_topic`: authoritative topic grounded in actual `/input` files (overrides stale labels).
- `source_basis`:
  - `published_paper`: source paper identifier.
  - `external_authors`: list of external authors whose prior findings must never be claimed as applicant work.
  - `applicant_owned_preliminary_results_supplied` (`bool`): when `false`, blocks first-person prior-work claims and requires `[EVIDENCE_GAP]` + applicant-evidence confirmation.
  - `actual_funder_notice_supplied` (`bool`): when `false`, blocks fabricated real funder policy citations and requires real-funder confirmation.
  - `resource_availability_evidence_supplied` (`bool`): when `false`, blocks asserting equipment/platform/ethics approvals are already secured.
- `sections`: 7 required sections (`background`, `question`, `goals`, `content`, `innovation`, `route`, `foundation`).
- `draft_size`: target Chinese character range across the 7 sections (e.g., `"正文七节合计约1600–2600汉字"`).
- `planning_months`: maximum allowed schedule horizon (e.g., `12`).
- `planning_unit_cap`: maximum sum of non-monetary planning units (e.g., `100`).
- `required_supporting_files`: the 6 required supporting deliverables.
- `scientific_limits`: explicit scope, grounding, and anti-fabrication boundaries.

## 2. Structured `grant_plan.json` Schema
Save `/app/output/grant_plan.json` with these top-level keys so `grant_7element_auditor.py` verifies exact parity and arithmetic:
```json
{
  "case_id": "C01",
  "planning_months": 12,
  "planning_unit_cap": 100,
  "planning_unit_total_declared": 100,
  "goals": [
    {"goal_id": "G1", "title": "...", "wp_id": "WP1", "milestone_id": "MS1", "metric": "...", "evaluation_status": "proposed"}
  ],
  "work_packages": [
    {"wp_id": "WP1", "goal_id": "G1", "title": "...", "start_month": 1, "end_month": 4, "planning_units": 30, "status": "proposed"}
  ],
  "milestones": [
    {"milestone_id": "MS1", "goal_id": "G1", "wp_id": "WP1", "month": 4, "deliverable": "...", "metric": "..."}
  ],
  "evidence_map": [
    {"claim_id": "CLM1", "claim": "...", "section": "立项依据", "paragraph": "P1", "source_ref": "C01-PAPER (Belter & Dollar): Results", "claim_type": "external_literature"}
  ],
  "risks": [
    {"risk_id": "R1", "category": "method", "description": "...", "trigger_signal": "...", "decision_checkpoint": "M4", "fallback_plan": "..."}
  ],
  "human_confirmations": [
    {"confirm_id": "HC1", "category": "applicant_evidence", "item": "核实申请人自有前期数据、设备与团队资质", "reason": "source_basis 未提供申请人自有前期证据"},
    {"confirm_id": "HC2", "category": "funder_policy", "item": "核对真实资助机构申报指南、预算科目与伦理要求", "reason": "organizer-workorder 非现实资助政策"}
  ],
  "rfp_coverage": [
    {"requirement_id": "RFP1", "requirement": "...", "status": "covered", "location": "grant_chapter.md#研究目标"}
  ],
  "conflict_decisions": [
    {"conflict_id": "CF1", "comment_a": "...", "comment_b": "...", "chosen_option": "...", "rationale": "...", "needs_applicant_signoff": true}
  ]
}
```

## 3. Six Required Supporting Deliverables Checklist
The auditor checks all 6 required supporting elements across `grant_chapter.md`, `element_crosswalk.md`, and `grant_plan.json`:
1. **七要素可编辑正文** (`grant_chapter.md` with all 7 headings + substantive prose inside `draft_size`).
2. **关键论断—段落—引文/计划来源映射** (`element_crosswalk.md` + `grant_plan.json["evidence_map"]`).
3. **目标—工作包—里程碑—指标矩阵** (`G1/G2/G3` 1:1 parity across `goals`, `work_packages`, `milestones`, and markdown table).
4. **规划单位分项与合计** (`work_packages[].planning_units`, finite non-negative, sum `<= planning_unit_cap`, matches declared total, explicitly non-monetary).
5. **至少 3 项风险及备选** (`risks` length `>= 3`, every entry with non-empty `trigger_signal` and `fallback_plan`).
6. **至少 2 项人工确认** (`human_confirmations` length `>= 2`, covering both real applicant evidence/resources and real funding-agency guidelines when absent).

## 4. `result.json` Five-Field `output_contract`
Before running `result_guard.py --finalize`, populate all 5 required contract keys alongside standard Harbor fields (`case_id`, `status`, `artifacts`, `metrics`, `metric_provenance`, `findings`, `gaps`):
- `case_id`: copied verbatim from `/input/case.json`.
- `deliverable`: path and summary of editable `grant_chapter.md` and supporting files (`element_crosswalk.md`, `grant_plan.json`, `grant_audit.json`).
- `preserved_facts`: list of key numbers, units, conditions, authors, and source IDs carried verbatim from `/input`.
- `evidence_map`: 3-tier list mapping key claims to section/paragraph and exact `/input` source or planned basis / `[EVIDENCE_GAP]`.
- `change_log`: drafting/revision actions, RFP coverage, collaborator conflict resolutions, human confirmation items, and unresolved evidence gaps.
