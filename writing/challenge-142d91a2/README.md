# Thesis & Research Proposal Extraction, Convergence & Feasibility Skill

## 1. What This Skill Does
Automatically extracts structured constraints, numeric facts, identifiers, and a grounded plan seed from raw `/input` documents (`.pdf`, `.docx`, `.md`, `.txt`, `.json`, `.csv`), drafts an editable 7-element thesis/research proposal (`research-proposal-planner`), and runs deterministic offline validation across DAG topology, declared-start Critical Path Method (CPM), parallel workload, anti-fabrication budget arithmetic, unavailable-resource convergence, 4-category risks, prose↔JSON consistency (including Chinese year synonyms), verbatim `claim_citations` `/input` grounding, and zero-crash `.docx`/`.html` export.

## 2. When to Use
- Converging a broad or under-specified research topic into 1–3 falsifiable research questions and quantified objectives (with Step 0 clarification when interactive inputs omit duration, budget, guidelines, or sample conditions).
- Verifying schedule/dependency DAG feasibility, declared-start critical path duration, parallel monthly effort, and strict budget arithmetic without fabricating missing ceilings.
- Handling real-world missing samples or unavailable instruments by narrowing scope and enforcing `resource_gap -> trigger_signal -> decision_checkpoint -> fallback_plan`.
- Back-checking citations and DOIs against raw `/input` `.pdf`/`.docx`/`.md` files and producing editable `.docx` and standalone `.html` deliverables in zero-dependency offline environments.

## 3. Package Layout
| Path | Role |
|---|---|
| `SKILL.md` | Agent workflow (Step 0 clarification + Steps 1–4), hard invariants, and CLI contract |
| `scripts/proposal_feasibility_checker.py` | Pure Python 3.12 stdlib extractor & auditor (`--extract-plan-seed`, `.pdf`/`.docx`/`.md` reader, Chinese/nested/headingless 7-element parser, `已知—未知—待验证`, declared-start DAG+CPM+parallelism, budget guard, 4-category/resource-gap gate, prose↔JSON cross-check, verbatim `claim_citations` `/input` back-check, stdlib OOXML `.docx` + HTML exporter) |
| `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` | Standard runtime guards (preflight, atomic result validation, budget circuit-breaker, block indexing, provenance grounding, final output audit) |
| `references/opening_convergence_and_mainline.md` | Question convergence, 4-question mainline pruning, Aim-Figure mapping, shortest evidence chain, Pre-mortem risks, anti-AI prose rules |
| `references/plan_schema_and_contract.md` | `proposal_plan.json` schema, prose↔JSON parity rules, `result.json` 5-field `output_contract` |
| `examples/minimal_call_example.md` | Synthetic input/command/output example |

## 4. Runtime Requirements, Known Boundaries & Maintenance
- **Dependencies**: Python 3.12 standard library only (zero third-party packages, zero network calls). Reads `.pdf` via `pdftotext` / `pypdf` / `pdfplumber` when present, falling back to pure-stdlib `zlib` PDF stream extraction; reads `.docx` via pure-stdlib OOXML XML parsing; exports `.docx` via `python-docx` when installed or built-in pure-stdlib OOXML generator otherwise, reporting `docx_exporter` honestly.
- **Known Boundaries**:
  - Image-only scanned PDFs without embedded text streams are recorded in `unparsed_pdfs` and surfaced via `[EVIDENCE_GAP]` instead of causing false-positive DOI errors.
  - Malformed `case.json` or `proposal_plan.json` files produce structured JSON errors without crashing the pipeline.
  - Does not invent missing sample cohorts, unprovided budget ceilings, or external literature not present in `/input`.
- **Version & Verification**: v3.0 (stdlib plan-seed extractor + PDF/DOCX verbatim citation back-check + declared-start CPM + Step 0 clarification). Run `python3 scripts/proposal_feasibility_checker.py --help` to inspect CLI flags.
