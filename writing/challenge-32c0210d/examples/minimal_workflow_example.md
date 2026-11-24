# Minimal Workflow Example (Illustrative Synthetic Walkthrough)

> **Note**: This is a synthetic interface illustration showing CLI invocation and expected artifact structure. It is not a formal evaluation case or prepared answer.

## Synthetic Input Layout

```text
/input/
├── case.json
├── organizer-edit-workorder.json   # {"target_ids": ["SYN-P002", "SYN-P005"]}
└── chapters/
    ├── ch01_intro.md               # Contains [SYN-P001], [SYN-P002]
    └── ch02_results.md             # Contains [SYN-P004], [SYN-P005]
```

## Minimal End-to-End Commands

```bash
# 1. Import chapters & create byte-exact receipt
python scripts/longdoc_revision_engine.py import \
  --chapters-dir /input/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --state-dir /app/work/rev_state \
  --receipt-out /app/output/import_receipt.json

# 2. Baseline cross-chapter consistency audit
python scripts/longdoc_chapter_consistency.py \
  --chapters-dir /input/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --out-json /app/work/baseline_consistency.json

# 3. Propose two annotations (one to accept, one candidate to reject)
python scripts/longdoc_revision_engine.py propose \
  --state-dir /app/work/rev_state \
  --target-id SYN-P002 \
  --title "Clarify drying temperature condition" \
  --annotation "Source specifies 60 C vacuum drying for 12 h; thermal stability above 80 C is not reported in supplied chapters." \
  --source-quote "Specimens were dried under vacuum at 60 C for 12 h." \
  --source-ref "ch01_intro.md#SYN-P002" \
  --uncertainty "Ambient humidity during storage is not reported."

python scripts/longdoc_revision_engine.py propose \
  --state-dir /app/work/rev_state \
  --target-id SYN-P005 \
  --title "Speculative industrial scale-up note" \
  --annotation "Results imply immediate factory-scale deployment." \
  --source-quote "Tensile strength increased by 14% at lab scale." \
  --source-ref "ch02_results.md#SYN-P005" \
  --uncertainty "No industrial trial data supplied."

# 4. Accept P0001 and reject P0002 individually
python scripts/longdoc_revision_engine.py accept --state-dir /app/work/rev_state --proposal-id P0001 --reason "Grounded verbatim in source conditions."
python scripts/longdoc_revision_engine.py reject --state-dir /app/work/rev_state --proposal-id P0002 --reason "Extrapolates beyond lab-scale evidence."

# 5. Diff, rollback to v0, reapply accepted proposals, and run controlled fault injection
python scripts/longdoc_revision_engine.py diff --state-dir /app/work/rev_state --from-version v0 --to-version latest --out-diff /app/output/revision.diff
python scripts/longdoc_revision_engine.py rollback --state-dir /app/work/rev_state --to-version v0 --out-json /app/work/rollback.json
python scripts/longdoc_revision_engine.py reapply --state-dir /app/work/rev_state --from-accepted --out-json /app/work/reapply.json
python scripts/longdoc_revision_engine.py fault-test --state-dir /app/work/rev_state --out-json /app/output/fault_test_report.json

# 6. Export editable chapters + offline HTML diff preview
python scripts/longdoc_revision_engine.py export \
  --state-dir /app/work/rev_state \
  --out-dir /app/output/chapters \
  --records-out /app/output/revision_records.json \
  --evidence-out /app/output/evidence_map.json \
  --html-preview /app/output/export_preview.html

# 7. Post-edit scope & consistency verification
python scripts/longdoc_chapter_consistency.py \
  --chapters-dir /app/output/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --baseline /app/work/baseline_consistency.json \
  --out-json /app/output/longdoc_audit.json
```

## Illustrative Output Snippet (Synthetic)

```json
{
  "case_id": "EXAMPLE-SYNTHETIC-01",
  "status": "ok",
  "deliverable": {
    "chapters_dir": "chapters/",
    "diff": "revision.diff",
    "html_preview": "export_preview.html",
    "change_log_doc": "terminology_and_change_log.md"
  },
  "preserved_facts": {
    "untouched_chapters_sha256_verified": true,
    "original_target_paragraphs_preserved": true
  },
  "evidence_map": [
    {
      "proposal_id": "P0001",
      "target_id": "SYN-P002",
      "source_ref": "ch01_intro.md#SYN-P002",
      "source_quote": "Specimens were dried under vacuum at 60 C for 12 h."
    }
  ],
  "change_log": {
    "accepted": ["P0001"],
    "rejected": ["P0002"],
    "rollback_sha256_restored": true,
    "reapply_sha256_matched": true,
    "fault_injection_state_unchanged": true
  }
}
```
