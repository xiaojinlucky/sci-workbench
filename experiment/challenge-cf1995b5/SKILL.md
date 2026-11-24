---
name: experimental-plan-generator-optimizer
description: >-
  Source-grounded experimental protocol generator and 3-version optimizer for
  research objectives under finite equipment, reagent, schedule, and budget
  contracts. Operationalizes testable hypotheses into IV/DV/confounder and
  statistical-unit mappings, checks control estimability and SOP version pins,
  solves DAG critical paths and strict null-vs-zero cost arithmetic, and audits
  six mandatory protocol elements plus confirmation gates into /app/output.
---

# Experimental Plan Generator & Optimizer (`challenge-cf1995b5`)

Transform fixed research objectives, scientific sources, and organizer prospective resource/price/booking contracts into **3 versioned, schedulable, source-grounded candidate plans (`v1`, `v2`, `v3`)** that cover all six mandatory experimental design elements without textbook boilerplate or fabricated lab claims.

## Runtime & Budget Contract

- **Skill root**: `/skills/weekly-skill` (scripts under `/skills/weekly-skill/scripts`, references under `/skills/weekly-skill/references`).
- **Inputs**: Read `/input/case.json`, `/input/task.md`, scientific literature, instrument range sheets, SOPs, and organizer prospective resource/price/booking contracts under `/input/`. Never rely on labels alone; inspect actual files.
- **Working directory**: `/app/work` (`env.json`, `.budget_state.json`, `input_blocks.json`, `parsed_goal.json`, `control_audit.json`, `report_audit.json`, `gaps.json`).
- **Deliverables**: `/app/output/optimized_plan.json`, `/app/output/optimized_plan.md` (plus any exact filename required by `/input/case.json`), and `/app/output/result.json` preserving `case_id` and every field in the case `output_contract`.
- **Execution budget**: 64 model requests / 900 s / 16,384 output tokens per turn. Initialize `preflight_probe.py`, `budget_guard.py`, and `result_guard.py` on Turn 1; save partial honest outputs early.

## Phase 1: Research-Type Routing & Shortest Evidence Chain

Before listing experimental steps, classify the study type so you never force a biological template onto materials, chemistry, or computational protocols (see `references/design_and_control_blueprint.md`):

| Research Type | Core Hypothesis Structure | Primary Falsification Anchor |
| --- | --- | --- |
| `empirical_causal` | `A -> (B -> C) -> D` distinguishing **solid** (source-established) vs **dashed** (to-be-tested) causal links | Perturbation of `A` with negative/vehicle + positive controls on `D` (and mediator `B/C`) |
| `engineering_optimization` | Target performance metric vs operating window & constraint envelope | Bounded response improvement over baseline under identical test bench & QC |
| `exploratory_discovery` | Candidate search space, screening filter funnel, and orthogonal confirmation gate | Pre-specified candidate denominator, explicit exploratory label, confirmation cohort |
| `computational_benchmark` | Method/estimator comparison across fixed splits, seeds, and compute budgets | Paired cross-split metrics with frozen preprocessing and leakage guards |

Build a compact **Shortest Evidence Chain** linking: `Research Objective -> Operationalized Prediction -> Independent & Dependent Variables (assay, unit, dynamic range, source_ref) -> True Inference Unit vs Technical Replicate -> Control/Blocking Structure -> Quantitative Pass/Stop Criterion`.

## Phase 2: Six Non-Negotiable Design Rules

1. **Inference Unit & Power Discipline (Anti-Pseudoreplication)**:
   - Separate the **independent experimental/biological unit** (e.g., independent donor, batch, cell passage, reactor run, physical specimen) from **technical replicates** (repeated wells/injections from one unit).
   - Report total sample arithmetic explicitly as `(treatment_arms + control_arms) * biological_replicates * technical_replicates + qc_extra_samples`.
   - When expected effect size and SD (or Cohen's `d`) are supplied in `/input/`, run `hypothesis_control_mapper.py` to compute approximate statistical power and minimum independent units per arm. Flag `n_biological < 3` unless justified by destructive/single-specimen constraints.
2. **Confounder-Matched Controls & Augmented-Factorial Estimability**:
   - Require at least two complementary control arms (e.g., `vehicle`/`blank`/`sham` + `positive` or `dose/time gradient`) explicitly mapped to every declared confounder.
   - When treatments span multiple timepoints, plates, or batches, audit the **Factor x Control incidence table**. If a control arm appears at only one timepoint or block (an augmented-factorial structure), explicitly enumerate which contrasts are **estimable** vs **confounded/non-estimable** — never pretend missing control blocks exist.
3. **SOP Version Pinning & ELN Handover Traceability**:
   - Pin every referenced protocol to an exact `sop_id@version` (e.g., `SOP-HPLC-04@v2.1`). If conflicting versions (`v1` and `v2`) coexist in the same plan or source packet, flag `SOP_VERSION_CONFLICT` and record which version governs each step.
   - Bind a stable `experiment_id` template to raw data directory conventions and a structured deviation log (`planned_setting`, `actual_setting`, `timestamp_utc`, `validity_impact`) so any handoff reviewer can trace results back to raw files.
4. **DAG Critical-Path Scheduling & Strict Null-vs-Zero Arithmetic**:
   - Compute schedule makespan from step dependencies (`depends_on`), step durations, instrument `booking_lead_days`, and reagent procurement `lead_days` via `plan_domain_optimizer.py` — never count bench assay time alone.
   - Distinguish **verified explicit `0`** (`0` cost or `0` lead days explicitly stated in contract) from **missing/`null` contract rates** (`UNVERIFIED_COST_INPUT` / `MISSING_LEAD_TIME`). Never coerce unknown costs, unknown booking slots, or unknown safety approvals to `0` or `True`.
5. **Finite-Domain Parameter Bounds & 3-Version Traceable Optimization**:
   - Anchor every parameter `[min, max]`, `step`, `safety_max`, and `allowed_values` to its `source_ref` (instrument manual, pilot log, or paper Methods).
   - Generate and verify **3 versioned candidate plans** (`v1_baseline`, `v2_constrained`, `v3_optimized` or case-specified version IDs) over the finite domain, exposing an explicit `version_diff` showing exact changes in arms, parameters, cost, schedule, and resolved violations.
   - Rank feasible candidates using domain-defined multi-objective criteria (`objectives` array from the case, never hard-coded biology metrics).
6. **Quantitative Stop-Loss, Fallbacks & Confirmation Gates**:
   - Attach quantitative trigger thresholds (`CV > 15%`, `blank drift > 5%`, `recovery < 80%`, `yield < threshold`), explicit stop-loss actions, and substitute equipment/reagent fallback paths (marking substitutable vs non-substitutable items).
   - Separate **executable protocol facts** from **Confirmation Items** (`safety`, `ethics_irb_iacuc`, `equipment_availability`, `vendor_quote`, `expert_signoff`).
   - Preserve the **Prospective Boundary Notice**: planning scenarios under organizer contracts are NOT claimed actual lab runs, confirmed reservations, vendor quotes, safety/ethics approvals, or measured future responses.

## Standard Execution Pipeline

Read `references/input_output_schemas.md` for the exact JSON schemas accepted by each script before generating `/app/work/parsed_goal.json`.

```bash
SCRIPTS=/skills/weekly-skill/scripts

# 1. Preflight, budget ledger, skeleton result.json, and source index
python3 "$SCRIPTS/preflight_probe.py" --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --state /app/work/.budget_state.json --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-cf1995b5"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# 2. Auto-extract source_ref-anchored draft from raw /input narrative files (unfound fields stay null / NOT_FOUND_IN_SOURCE), then validate schema
python3 "$SCRIPTS/source_to_plan_extractor.py" \
  --input-dir /input --blocks /app/work/input_blocks.json --mode cf1995b5 --out /app/work/parsed_goal.json
python3 "$SCRIPTS/validate_and_sync_result.py" validate-input \
  --input /app/work/parsed_goal.json --mode cf1995b5 --out /app/work/input_schema_audit.json

# 3. Audit hypothesis, inference unit, power, confounder controls, estimability, and SOP version pins
python3 "$SCRIPTS/hypothesis_control_mapper.py" \
  --case /app/work/parsed_goal.json \
  --out /app/work/control_audit.json

# 4. Run DAG schedule, strict null-vs-zero cost math, finite-domain checks, and 3-version optimization
python3 "$SCRIPTS/plan_domain_optimizer.py" \
  --case /app/work/parsed_goal.json \
  --controls /app/work/control_audit.json \
  --out-dir /app/output \
  --stem optimized_plan

# 5. Audit 6-element completeness, confirmation gates, and anti-boilerplate compliance
python3 "$SCRIPTS/plan_report_verifier.py" \
  --plan /app/output/optimized_plan.json \
  --case /app/work/parsed_goal.json \
  --out /app/work/report_audit.json

# 6. Auto-sync /input/case.json output_contract + 3-pillar summary into /app/output/result.json and run final audit
python3 "$SCRIPTS/validate_and_sync_result.py" sync-result \
  --case-json /input/case.json \
  --report /app/output/optimized_plan.json \
  --result-json /app/output/result.json \
  --task-id "challenge-cf1995b5" \
  --artifact optimized_plan.json --artifact optimized_plan.md
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --result-file result.json --require optimized_plan.json --require optimized_plan.md
```

## Exception & Conflict Handling

| Condition | Required Action |
| --- | --- |
| Missing budget cap, unit price, or lead time | Record in `unverified_cost_inputs` / `missing_schedule_inputs`, keep affected totals `null`, mark plan `requires_confirmation` rather than coercing to `0`. |
| Conflicting constraints (e.g., deadline < critical path or `min > safety_max`) | Emit `INFEASIBLE_CONSTRAINT_CONFLICT` with the exact binding constraint, slack deficit, and minimum relaxation options; do not fabricate a feasible plan. |
| SOP version clash (`v1` & `v2` both referenced) | Emit `SOP_VERSION_CONFLICT` listing conflicting steps/sources and add an explicit confirmation item for PI/QA sign-off. |
| Control arm missing in one or more timepoints/blocks | Classify design as augmented/incomplete factorial; list estimable vs confounded contrasts and recommend control expansion if budget permits. |
| Missing ethics/biosafety/equipment confirmation | Place under `confirmation_items` with status `UNVERIFIED_REQUIRED` — never state approval is granted. |

## Output Contract Mapping

Ensure `/app/output/result.json` includes `case_id`, all original `output_contract` fields from `/input/case.json`, plus structured entries covering:
1. `experimental_plan`: operationalized hypothesis, IV/DV/confounders, inference unit & power, controls & estimability, equipment/materials (with substitutability), DAG schedule, cost breakdown, bounded parameters, 3-version diff, stop-loss & fallbacks, and ELN handover spec.
2. `rationale_and_risks`: `source_ref` citations per parameter/resource, explicit assumptions, uncertainty bounds, failure modes, and `prospective_boundary_notice`.
3. `confirmation_items`: safety, ethics, equipment availability, vendor pricing validity, SOP version lock, and expert sign-off items with explicit `status` (`UNVERIFIED_REQUIRED` / `CONFIRMED_IN_CONTRACT` / `BLOCKING`).
## Multi-Format Intake & Schema-Variant Fallback Contract

1. **Multi-Format Offline Indexing (`scripts/block_indexer.py`)**: Indexes `.md`, `.txt`, `.yaml/.yml`, `.m`, `.csv`/`.tsv`, `.xlsx` (stdlib `zipfile+XML` per-sheet), `.docx` (stdlib `zipfile+XML`), `.xml`/`.html`, `.json`, and `.pdf` via a 3-tier offline pipeline (`pypdf`/`PyPDF2` -> `pdftotext` -> pure-stdlib zlib PDF stream scan) with exact `file#L<line>`, `file#page=<p>`, or `file#<sheet>!R<row>` anchors. Scanned/image-only PDFs without an extractable text layer emit explicit `status: "UNPARSED_SOURCE"` (`manual_transcription_needed: true`) instead of silent `null`.
2. **Table + Free-Form Prose Extraction (`scripts/source_to_plan_extractor.py`)**: Automatically extracts resource/pricing/booking tables, parameter domain tables, historical pilot runs, and natural-language prose constraints from raw `/input` documents with exact `source_ref` anchors, keeping unquoted costs and unverified approvals explicitly `null` under `NOT_FOUND_IN_SOURCE`.
3. **LLM Direct-Fill Bridge**: When `/input` uses highly heterogeneous prose or scanned forms (`extraction_coverage.fallback_to_llm_matrix_recommended=true`), populate the structured draft / `aligned_matrix` directly from the indexed blocks in context while ALWAYS passing it through the offline deterministic arithmetic, feasible-domain, null-vs-zero firewall, and `validate_and_sync_result.py` gates.
