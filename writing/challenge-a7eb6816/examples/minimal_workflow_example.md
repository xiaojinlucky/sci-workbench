# Minimal Workflow Example (Illustrative Synthetic Walkthrough)

> **Note**: Synthetic example illustrating CLI usage, glossary locking, paragraph traceability, and audit outputs. Not an evaluation case or pre-written test answer.

## Synthetic Scenario

Convert a preclinical mouse + cell-line study (`source_paper.md`, reporting that compound `M17` reduced inflammatory cytokine `IL-6` by 36% in `n = 24` C57BL/6 mice vs vehicle control, `p = 0.024`, and correlated with macrophage `NF-κB` inhibition in vitro) into a Chinese `public_science` article (`adapted_article.md`).

## Commands

```bash
python scripts/translational_fidelity_auditor.py \
  --source /input/source_paper.md \
  --target /app/output/adapted_article.md \
  --audience public_science \
  --glossary-json /app/output/glossary_lock.json \
  --trace-json /app/output/paragraph_trace.json \
  --out-json /app/output/translation_fidelity.json \
  --review-md /app/output/side_by_side_review.md
```

## Illustrative `glossary_lock.json` (Synthetic)

```json
{
  "terms": [
    {
      "source_term": "IL-6",
      "canonical_term": "白细胞介素-6（IL-6）",
      "allowed_variants": ["IL-6"],
      "disallowed_synonyms": ["万能炎症开关"]
    },
    {
      "source_term": "vehicle control",
      "canonical_term": "溶剂对照组",
      "allowed_variants": ["对照组"],
      "disallowed_synonyms": ["未治疗患者组"]
    }
  ]
}
```

## Illustrative Audit Summary (Synthetic)

```json
{
  "passed": true,
  "audience": "public_science",
  "source_evidence_tier": {
    "tier": 2,
    "label": "animal_or_preclinical",
    "model_boundaries": ["in_vitro_cell", "mouse_animal"]
  },
  "claim_escalation_hits": [],
  "model_boundary_preserved": true,
  "ungrounded_target_numbers": [],
  "bilingual_magnitude_matches": [],
  "unexpanded_acronyms": [],
  "glossary_violations": [],
  "prose_hard_blocks": [],
  "critical_omissions": [],
  "errors": []
}
```
