---
name: math-formula-verifier
description: Parse JATS publisher-source.xml and source-formulas.xml MathML/TeX trees without double-counting, trace variable definitions with Greek Unicode/LaTeX and subscript normalization into a provenance table with SI-validated unit/range hints, build cross-equation dependency DAGs and audit multi-step derivation assumptions, approximations, boundary conditions, intermediate values, and composite SI dimensions, intercept Pix2Text-class OCR noise before derivation, self-validate formula_notes.md Markdown math, and emit honest partial/insufficient_evidence on C02-style missing-formula branches.
---

# Formula Parsing & Mathematical Reasoning (`challenge-7c37626c`)

## Purpose & Trigger Scope
Use this Skill when reading scientific papers or structured XML/PDF snapshots that require:
- faithful extraction of equations (MathML, `<tex-math>`, or display LaTeX) with exact source anchors;
- grounded symbol/variable provenance tables tied to paper paragraphs (distinguishing explicit definitions from context mentions and missing definitions);
- step-by-step derivation assumption, approximation, boundary-condition, and symbolic/numeric equivalence verification;
- general composite SI dimensional balance and order-of-magnitude scale-factor verification;
- honest missing-source handling when an input snapshot lacks formula representations or recomputable values.

All bundled scripts run offline on **pure Python 3.12 standard library** (`python:3.12-slim`, zero mandatory third-party packages), with automatic enhancement if `defusedxml` or `sympy` is available in the container.

## Runtime Paths & Case Routing
- **Input root**: `/input` (`case.json`, `task.md`, optional `artifacts/source-formulas.xml`, `artifacts/publisher-source.xml`, PDF/Markdown/TeX).
- **Work directory**: `/app/work` (environment probe, budget state, hash-chained audit ledger, intermediate gaps).
- **Output directory**: `/app/output` (`result.json`, `formula_verification_report.json`, `formula_notes.md`).

### Branch Routing (`C01`/`C03` vs `C02`)
1. **Formula-Bearing Snapshots (`C01`/`C03` class)**:
   - When `artifacts/source-formulas.xml` is present, parse it first as an **extracted formula catalog** (NOT a linearized answer or complete derivation). If `source-formulas.xml` is unreadable or corrupt, log `FORMULA_SOURCE_UNREADABLE` with its filename and fall back to `artifacts/publisher-source.xml`.
   - Always scan `artifacts/publisher-source.xml` `<p>` elements (in addition to `case.json` paragraphs and `.md`/`.txt`/`.pdf` sources) when tracing variable definitions.
   - Deduplicate `<disp-formula><alternatives><mml:math>` + `<tex-math>` pairs into a single formula record (`representation_source: "mathml+tex-math"`), never double-counting one equation across sibling representations or nested `<math>` elements.
2. **Missing-Formula Snapshots (`C02` class)**:
   - When no MathML, `<tex-math>`, or LaTeX formula exists in the input snapshot, or required variables for recomputation are absent:
     - **NEVER** back-infer missing formulas from summary numbers.
     - **NEVER** default unknown variables to `0`.
     - Audit only the explicit metrics and conditions present in the snapshot, and emit `formula_coverage_status: "insufficient_evidence"` (zero formulas) or `"partial"` (missing variables / halted OCR / unreadable source) with structured `gaps[]`.

## Deterministic Execution Workflow
Always resolve scripts via `SKILL_ROOT` (installed at `/skills/weekly-skill` in container runs) so commands succeed from `/app/work` or any working directory, or invoke `"$SKILL_ROOT/scripts/run.sh"` directly.

### Step 1: Preflight, Budget Init & Core Formula Pipeline
```bash
export SKILL_ROOT="${SKILL_ROOT:-/skills/weekly-skill}"
python3 -B "$SKILL_ROOT/scripts/preflight_probe.py" --input /input --output /app/output --out /app/work/env.json
python3 -B "$SKILL_ROOT/scripts/budget_guard.py" --state /app/work/.budget_state.json --init
python3 -B "$SKILL_ROOT/scripts/formula_math_engine.py" --input /input --output /app/output --work /app/work
# One-click equivalent (runs Steps 1 & 3 end-to-end):
# bash "$SKILL_ROOT/scripts/run.sh" /input /app/output /app/work
```
`formula_math_engine.py` performs the deterministic core pass (with an **Auto-Candidate Generation Layer** so hidden cases that omit pre-structured `derivation_chains`/`numeric_checks`/`dimensional_checks` in `case.json` still autonomously parse `task.md`, synthesize DAG-grounded derivation audits, extract prose numeric parameter values, and emit honest `insufficient_evidence` + `missing_variables` without zero-filling):
1. **XML/MathML Layered Extraction (`math_ast_guard.parse_xml_formulas`)**: lowers `<mrow>`, `<mfrac>`, `<msub>`, `<msup>`, `<msubsup>`, `<msqrt>`, `<mroot>`, `<mtable>`/`<mtr>`/`<mtd>`, `<mfenced>`, `<mover>`/`<munder>`/`<munderover>`, `<mmultiscripts>`, and `<semantics>` (without concatenating `<annotation>` noise) while recording `source_file`, `formula_id`, `xml_tag`, `label`, and `representation_source`. Preserves braced/Greek subscripts (`{E}_{a}` -> `E_a`, `\eta_0` -> `eta_0`) without splitting subscripts into orphan letters.
2. **Pix2Text #142 OCR/Transcription Noise Gate (`math_ast_guard.inspect_formula_noise`)**: distinguishes legitimate scientific Chinese inside `\text{}`/`\mathrm{}`/`\operatorname{}` from raw math-mode CJK or Pix2Text-style side-note/CMYK artifacts (`旁注`, `见第`, `.pdf`, `CMYK`), literal tabs/control characters (e.g., corrupted `\tau`/`\theta`), mojibake, unbalanced `()`/`[]`/`{}`, mismatched `\left`/`\right`, and empty `\frac{}{}`. Any halted formula is marked `verification_status: "halted_for_visual_check"` and blocked from blind derivation or numeric evaluation.
3. **Greek/Subscript-Normalized Variable Provenance Table**: traces each extracted symbol across `publisher-source.xml` `<p>` nodes, `case.json`, and text/PDF inputs using bidirectional Greek Unicode↔LaTeX (`α`↔`\alpha`, `η`↔`\eta`, `Δ`↔`\Delta`, `τ`↔`\tau`) and subscript normalization (`I_0`/`I_{0}`/`I₀`, `E_a`/`E_{a}`, `k_B`), separating `source_defined`, `context_inferred`, `mathematical_constant` (e.g., `\pi`), and `undefined_in_supplied_text`, while validating `unit_hint` via the composite SI unit parser and scoping `range_hint` to the local clause.
4. **Step-by-Step Derivation & Cross-Equation Dependency DAG Auditor (`math_ast_guard.build_equation_dependency_dag`, `extract_derivation_assumptions_and_boundaries`, `audit_derivation_chain`)**:
   - Builds an acyclic cross-equation dependency graph (`explicit_citation` edges such as `Eq. (1) -> Eq. (2)`, `derivation_chain` edges, and `symbol_flow` LHS→RHS edges) with Kahn topological ordering.
   - Automatically extracts grounded assumptions (`assuming`, `provided that`), approximations (`steady state`, `high-overpotential limit`, `for x << 1`, `neglecting`), boundary/domain conditions (`T > 0`, `cutoff`, `boundary condition`), and derivation operations from paper prose with exact paragraph anchors and verbatim quotes.
   - Audits multi-step algebraic chains and single-step derivations (or auto-synthesized DAG steps when `case.json` omits explicit chains) for symbolic/numeric equivalence, intermediate values (`numeric_before`, `numeric_after`, `rel_error`), AST-evaluated domain predicate compliance (`evaluate_domain_predicate`), and step-level dimensional self-consistency, assigning explicit step verdicts (`verified`, `verified_under_approximation`, `failed_equivalence`, `domain_violation`, `dimensional_mismatch`, `halted_ocr_noise`, `conditional_on_stated_premise`, `not_verifiable_from_supplied_materials`).
5. **General Composite SI Dimensional Algebra (`math_ast_guard.check_dimensional_balance`)**: parses arbitrary composite expressions (`m s^-2`, `kg*m^2/(s^3*A)`, `J/(mol*K)`, `kJ/mol`, `mA/cm^2`, `mV/dec`, `rpm`, `mg/L`, `eV`, `Pa*s`) with explicit semantic unit notes for dimensionless logarithmic `dec`/decade and epidemiological count denominators, decomposes SI prefixes (`f` to `T`), compares 7-dimensional SI vectors `(L, M, T, I, Theta, N, J)`, and flags both dimensional mismatches (`DIMENSIONAL_MISMATCH`) and silent order-of-magnitude scale offsets (`UNIT_ORDER_OF_MAGNITUDE_OFFSET`).
6. **Safe Numeric Verification & Dynamic `output_contract` Absorption**: evaluates arithmetic/scientific expressions via Python AST whitelist (zero `eval()`), isolates SymPy reserved names (`E, I, S, N, O, Q, C`), auto-extracts prose parameter values when `case.json.numeric_checks` is omitted, refuses zero-filling when any required variable is absent, and dynamically preserves any `output_contract` fields declared in `case.json`.
7. **Portable Markdown Notes & Self-Check**: writes `/app/output/formula_notes.md` with `E001...` anchors, the Cross-Equation Dependency DAG table, the Derivation Steps & Boundary Conditions Audit table, and Extracted Prose Premises, then validates it via `math_markdown_validator.py`.

### Step 2: Host Agent Deep Scientific Audit (Enriching Notes & Derivation Steps)
After Step 1 writes the deterministic baseline, inspect `/app/output/formula_verification_report.json` and `/app/output/formula_notes.md` against the source paper:
1. **Variable & Symbol Integrity**: confirm that `context_inferred` or `undefined_in_supplied_text` symbols are not silently promoted to `source_defined`; preserve original notation (never translate mathematical symbols or rewrite `(I_0)` outside `$...$`).
2. **Derivation Step Audit**: for each key derivation step requested by `task.md`, explicitly state:
   - starting equation ID and target equation ID (cross-equation dependencies);
   - mathematical operations applied;
   - required assumptions, approximations (e.g., steady state, small-angle, dilute limit), and domain/boundary conditions;
   - whether the step is `verified`, `conditional_on_unstated_assumption`, or `not_verifiable_from_supplied_materials`.
   Cross-check algebraic identities using `math_ast_guard.check_equivalence()` and units using `math_ast_guard.check_dimensional_balance()`.
3. **Front-End / Back-End Separation**: keep `formula_notes.md` clear and domain-oriented (equation anchors, physical meaning, assumptions, honest gaps), keeping raw parser diagnostics in JSON artifacts.

### Step 3: Markdown Validation, Grounding Gate & Final Deliverable Audit
Whenever `formula_notes.md` or `result.json` is updated, re-run all three gates:
```bash
export SKILL_ROOT="${SKILL_ROOT:-/skills/weekly-skill}"
python3 -B "$SKILL_ROOT/scripts/math_markdown_validator.py" /app/output/formula_notes.md --json
python3 -B "$SKILL_ROOT/scripts/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B "$SKILL_ROOT/scripts/final_audit.py" --dir /app/output --result-file result.json --require formula_verification_report.json --require formula_notes.md
```

## Reference Specifications
Load as needed during execution:
- `references/mathml_xml_extraction_and_dedup.md` — JATS/MathML lowering and non-double-counting rules.
- `references/composite_si_dimensional_algebra.md` — Composite SI parser grammar, prefixes, 7-dim vectors, and scale-offset diagnostics.
- `references/variable_provenance_and_derivation_audit.md` — Three-tier variable provenance, derivation assumption checklist, and Pix2Text #142 OCR noise protocol.
- `references/output_contract_and_c02_protocol.md` — Required output fields, C02 missing-formula branch contract, and degradation rules.

## Hard Boundaries & Failure Handling
- **No Double-Counting**: `<disp-formula><alternatives><mml:math>` + `<tex-math>` is ONE equation. Never emit duplicate entries or inflate `formula_count`.
- **Halt on Corrupt OCR/Transcription**: if `inspect_formula_noise` returns `halt_for_verification` (unexpected CJK characters, literal `\t`, mojibake, unbalanced braces), stop derivation/evaluation on that equation and report `OCR_NOISE_DETECTED` rather than propagating bad tokens.
- **No Fabricated Formulas or Zero-Filling**: on C02-style missing inputs or missing numeric parameters, emit `formula_coverage_status: "insufficient_evidence"` or `"partial"` with explicit `gaps[]`.
- **No Raw LaTeX Leaks in Prose**: `formula_notes.md` must pass `math_markdown_validator.py` with zero `FAIL` findings (no bare `\frac`/`\alpha`, no unbalanced `$`/`$$`, no `(I_0)` pseudo-math).
- **Save Early & Stay Within Budget**: `result_guard.py` initializes `/app/output/result.json` immediately and writes atomically so partial honest deliverables survive timeout or budget limits.
