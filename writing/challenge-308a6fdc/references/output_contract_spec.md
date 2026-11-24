# Output Contract Specification (academic-manuscript-writing)

Every evaluation case must write `/app/output/result.json` preserving the exact `case_id` from `/input/case.json` and all required `output_contract` fields:

```json
{
  "case_id": "<verbatim from /input/case.json>",
  "task_id": "academic-manuscript-writing",
  "status": "ok | partial_with_gaps",
  "deliverable": {
    "manuscript_markdown": "manuscript.md",
    "manuscript_docx": "manuscript.docx",
    "sections_detected": ["Abstract", "Introduction", "Methods", "Results", "Discussion", "References"],
    "journal_structure_spec": "SEPARATE_RESULTS_AND_DISCUSSION | COMBINED_RESULTS_AND_DISCUSSION | UNSPECIFIED"
  },
  "preserved_facts": {
    "grounded_numbers_count": 0,
    "ungrounded_numbers_count": 0,
    "ungrounded_dois": [],
    "abstract_body_mismatch_numbers": []
  },
  "evidence_map": [
    {
      "claim_location": "manuscript.md:L24",
      "section": "Results",
      "token": "18.4",
      "status": "GROUNDED",
      "source_anchors": ["results.csv:row3:response_pct"]
    }
  ],
  "change_log": [
    {
      "action": "audit_8_gates",
      "blocking_issues": 0,
      "rd_issues": []
    }
  ],
  "artifacts": ["manuscript.md", "manuscript.docx", "evidence_map.json", "manuscript_audit.json"],
  "sources": [],
  "gaps": [],
  "limitations": [],
  "verification": {}
}
```

Editable deliverables:
- `/app/output/manuscript.md`: Clean, submission-ready editable Markdown manuscript.
- `/app/output/manuscript.docx`: Editable OOXML Word document built with pure Python stdlib (zero external library dependency) preserving headings, three-line tables, and font settings.
- `/app/output/evidence_map.json` and `/app/output/manuscript_audit.json`: Deterministic provenance and 8-gate audit records.
- `/app/output/preview/*.pdf` (and `.png` when `soffice` and `pdftoppm` are installed; honestly recorded in `gaps` if unavailable).
