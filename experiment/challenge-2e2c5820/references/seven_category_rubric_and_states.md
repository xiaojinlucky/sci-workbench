# Seven-Category Feasibility Rubric & Four-State Resource Rules

## 1. Seven Mandatory Feasibility Categories

| # | Category | PASS Condition | CONDITIONAL | MISSING_INFO | FAIL |
| --- | --- | --- | --- | --- | --- |
| 1 | Hypotheses & Variables | Testable hypothesis + IV/DV + unit + inference unit | Missing unit or inference_unit clarification | Missing hypothesis or IV/DV | Contradictory/untestable goal |
| 2 | Controls | >=2 complementary control arms covering confounders | Single control arm only | Unspecified control mapping | Zero controls |
| 3 | Equipment & Materials | All `available`, calibrated, precision verified | `needs_procurement` or `outsourceable` within slack; expired quote refresh | Missing resource record, status, precision, or quote date | `unavailable`, lead > deadline, precision fail, or overdue/failed maintenance |
| 4 | Time, Budget & Samples | Verified cost <= cap, DAG makespan <= deadline, samples <= pool, stability ok | Internal prose vs computed arithmetic mismatch to reconcile | Any `null`/missing setup fee, unit cost, rate, lead time, or cap | Confirmed cost/deadline/sample/stability overrun or schedule cycle |
| 5 | Parameter Domains | All values inside `[min, min(max, safety_max)]` and on `step` grid | - | Missing parameter value | Out-of-bounds, violates `safety_max`, or off-step |
| 6 | Risks & Fallbacks | Quantitative failure triggers + actionable fallback routes | Risks listed without complete triggers/fallbacks | Zero risks/fallbacks declared | - |
| 7 | Ethics & Safety | Explicit `true` on required biosafety/ethics/hazardous permits in prospective contract | - | Any required status `null` or omitted | Explicit `false` (denied/unapproved) |

## 2. Four-State Resource Classification

- `available` (`已具备`): Present in-house, operational, calibration/maintenance current.
- `outsourceable` (`可外协`): External core/CRO with stated turnaround, price date, and cost.
- `needs_procurement` (`需采购`): Requires vendor purchase; must confirm `lead_days <= slack_days` and valid quote date.
- `unavailable` (`不具备`): Cannot be accessed in window -> hard `FAIL` unless a verified substitute with matching precision is bound.

## 3. Null-vs-Zero & Maintenance/Handover Firewall

- Explicit `0` (`one_time_setup_cost: 0`, `booking_lead_days: 0`) is preserved as `verified_zero`.
- Omitted or `null` rates, setup fees, lead times, or approvals are NEVER defaulted to `0` or `True`; they immediately raise `MISSING_INFO` so incomplete plans cannot pass silently.
- Having an instrument listed does NOT prove readiness if `maintenance_status` is `overdue`/`unverified`, precision is worse than `required_precision`, or `sample_stability_days < approval_lead_days + procurement_lead_days`.

## 4. Pre-mortem 5-Column Risk Check & 4-Question Handover Deviation Log

When auditing Category 6 (`6_risks_fallbacks`) and SOP/ELN handover readiness, check whether critical risks provide:
- Failure mode, earliest observable warning signal, preventive control, quantitative stop-loss trigger, and fallback/escalation path.
- A 4-question deviation record template (planned setting, actual deviation/observation, immediate containment & validity scope, required sign-off) linked to pinned `sop_id@version` and raw data IDs.

## 5. Criterion Source-Attribution Matrix (SOP vs Equipment Manual vs Policy vs Quote)

Every `PASS`, `CONDITIONAL`, `MISSING_INFO`, and `FAIL` entry must bind its `source_ref` to the authoritative document tier below:

| Audit Check | Primary Authoritative Source | Secondary / Escalation Source | Mandatory Status When Absent |
| --- | --- | --- | --- |
| Hypothesis, IV/DV, units, inference unit | Study protocol & source Methods | Statistical design plan | `MISSING_INFO` |
| Control arms & confounder coverage | Source SOP (`sop_id@version`) & Methods | Prior validation study | `FAIL` (0 controls) / `CONDITIONAL` (1 control) |
| Equipment range, precision, calibration | Instrument OEM Manual & Core Calibration Log | Core facility engineer memo | `MISSING_INFO` (unverified) / `FAIL` (overdue or precision fail) |
| Reagent pricing, lead time, quote expiry | Dated Vendor Quote (`quote_valid_until`) | Organizer prospective contract | `MISSING_INFO` (missing price/date) / `CONDITIONAL` (expired quote) |
| Sample pool & stability window | Biobank/Sample Registry & Storage SOP | Clinical/animal coordinator | `FAIL` (wait > stability) / `MISSING_INFO` (unspecified) |
| Biosafety, IRB/IACUC, hazardous permits | Institutional EHS / IRB / IACUC Policy | Written approval docket | `MISSING_INFO` (`null` or `"in_review"`) / `FAIL` (`false` / `"denied"`) |

## 6. Handling Pending-Review (`in_review`) vs Denied (`false`) Approvals

- `"in_review"`, `"pending"`, `"pending_amendment"`, or `"submitted"` NEVER pass feasibility and NEVER count as confirmed denial: they map to `MISSING_INFO` (`REQUIRES_INFO_OR_CONFIRMATION`) with an explicit owner-tagged condition (`PI / IRB-IACUC-EHS Office: obtain final written clearance`).
- Confirmed `"denied"` / `false` maps strictly to `FAIL` (`INFEASIBLE`).
