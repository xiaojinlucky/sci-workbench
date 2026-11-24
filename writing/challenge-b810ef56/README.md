# Literature-Evidence-Driven Writing Skill (`evidence-driven-writing`)

Offline, zero-dependency Python 3.12+ skill implementing a verifiable three-part scientific writing pipeline (**Atomic Claims List → Evidence Matrix → Calibrated Synthesis Draft**) with strict 5-state verification and full structured 5-field `result.json` compliance.

## Capabilities

1. **Atomic Claim Decomposer**: Splits drafts into indexed claims (`C01`, `C02`...) with detected epistemic strength (`causal`, `associative_correlational`, `conditional_scoped`, `consensus_broad`, `descriptive`), polarity, applicability conditions, and citation anchors.
2. **Multi-Source Verbatim Excerpt Locator**: Scans `/input` literature files (`.md`, `.txt`, `.json`, `.docx`, `.pdf`) to attach verbatim excerpts with `file#page:section:line` locators and classifies both `source_type` (`primary_research`, `review`, `guideline`, `preprint`, `abstract_only`) and `study_design` (`rct_or_intervention`, `observational_correlational`, `meta_analysis`, `in_vitro_preclinical`) per individual source.
3. **Strict 5-State Verdict Engine**: Grades every claim as `SUPPORTED`, `PARTIALLY_SUPPORTED`, `CONTRADICTED`, `UNVERIFIABLE_ABSENT` (never conflating missing/abstract-only files with fabrication), or `EXISTENCE_ONLY_NOT_SUPPORTING`.
4. **Conflict Synthesis & Applicability Condition Auditor**: Detects divergent evidence in `/input`, checks whether the draft explicitly reconciles applicability conditions rather than hiding conflicts or reciting papers mechanically, and enforces per-source causal boundaries.
5. **structured 5-Field Output Contract**: Emits `claims_list.json`, `evidence_matrix.md`, `evidence_audit.json`, and `result.json` preserving `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, and `change_log`.

## Directory Layout

- `SKILL.md`: Agent execution workflow, three-part delivery discipline, and red lines.
- `scripts/evidence_driven_writer_guard.py`: Core claim decomposer, 5-state verifier, and conflict auditor.
- `scripts/writing_io_and_invariants.py`: Shared stdlib I/O, invariant extractor, and prose linter.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Harbor M1–M6 guards.
- `references/{academic_writing_standards,evidence_grading_and_synthesis,output_contract_spec}.md`: Standards and contracts.
- `examples/minimal_example.md`: Synthetic interface and grading example.

## Requirements & Limitations

- Python 3.12+ standard library only (air-gapped Linux Docker ready; no pip dependencies).
- Offline verification covers only literature files supplied in `/input`; absent or abstract-only sources are transparently marked `UNVERIFIABLE_ABSENT`.
