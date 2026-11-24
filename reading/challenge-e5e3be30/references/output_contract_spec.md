# Output Contract Specification (`challenge-e5e3be30`)

## 1. Inputs
- Primary audit questions: `/input/claims-to-verify.json` (list or object with `claims[]`; each claim may contain `claim_id`, `claim`/`text`, optional `cited_ref`, optional `cited_page`, optional `cited_paragraph`, optional `structured_citations[]`).
- Case metadata: `/input/case.json` (`case_id`, optional fallback `claims[]` or `documents[]`).
- Corpus files under `/input/`: `.pdf`, `.json`, `.md`, `.txt`, `.csv`.

## 2. Required Deliverables under `/app/output/`
1. `result.json` — primary structured Harbor result (`case_id`, `status`, `artifacts`, `citation_legend`, `verifications`, `diagnostic_summary`, `citation_integrity_audit`, `corpus_citations_extracted`, `schema_validation`, `metrics`, `metric_provenance`, `findings`, `gaps`).
2. `verification_cards.json` — full atomic verification cards including per-subclaim `E/M/L/S/I` fingerprints, `diagnostic_flags`, `anchor_audit`, `secondary_citation_warning`, and `abstract_body_conflict`.
3. `verification_report.md` — human-auditable Markdown report containing:
   - Section 1: Citation Legend (`[1]..[N]` -> `source_document_id`, Title, DOI, Source File, SHA-256 prefix, Coverage Status)
   - Section 2: Verification & Four-State Diagnostic Summary Table
   - Section 3: Inline-vs-Structured Citation Integrity Audit
   - Section 4: Per-Claim Atomic Sub-Claim Evidence Cards (verdict, claim types, anchor, verbatim quote, diagnostic flags, rationale)

## 3. Core JSON Schema Structures

### `verifications[]`
```json
{
  "claim_id": "C01",
  "original_claim": "Compound X reduced tumor volume by 65% (p < 0.01) while extending survival to 120 days.",
  "cited_ref": "10.5555/target.2024, p. 4",
  "verdict": "partially_supported",
  "preserves_comparator_negation_conditions": true,
  "diagnostic_flags": {
    "doi_exists_but_unsupported": false,
    "wrong_page_or_anchor_mismatch": true,
    "fulltext_unavailable_or_figure_caption_missing": false,
    "inline_vs_structured_citation_drift": false,
    "untested_extrapolation_detected": false,
    "secondary_citation_warning": false,
    "abstract_body_conflict_detected": false
  },
  "citation_integrity_audit": {
    "inline_marker_count": 0,
    "inline_unique_ids": [],
    "structured_citation_count": 0,
    "drift_detected": false,
    "missing_in_structured": [],
    "unreferenced_in_text": []
  },
  "subclaims": [
    {
      "subclaim_id": "C01.1",
      "text": "Compound X reduced tumor volume by 65% (p < 0.01)",
      "claim_types": ["quantitative", "fact"],
      "verdict": "supported",
      "candidate_verdict": "supported",
      "verdict_rationale": "...",
      "diagnostic_flags": {},
      "anchor_audit": {
        "cited_pages": [4],
        "cited_paragraphs": [],
        "actual_pages": [6],
        "actual_paragraph_ids": ["target:p6-b2"],
        "wrong_page_or_anchor_mismatch": true
      },
      "evidence": [
        {
          "citation_number": 1,
          "citation_tag": "[1]",
          "source_document_id": "target",
          "source_file": "target.pdf",
          "paragraph_id": "target:p6-b2",
          "page": 6,
          "section_hint": "results",
          "attribution_type": "primary_study",
          "secondary_citation_warning": false,
          "quote": "Compound X reduced xenograft tumor volume by 65% (n=8, p=0.004)."
        }
      ]
    }
  ]
}
```

### `schema_validation`
Returned by `--validate-verifications` and embedded in `result.json`:
- `ok`: boolean
- `errors[]`: blocking rule violations (`UNKNOWN_PARAGRAPH_ID`, `CROSS_DOCUMENT_ANCHOR_MISMATCH`, `QUOTE_MISMATCH`, `INLINE_STRUCTURED_CITATION_DRIFT`, `SUPPORTED_WITHOUT_EVIDENCE`, `DOI_MISMATCH_SUPPORTED`, `CONTRADICTED_WITHOUT_OPPOSING_EVIDENCE`, `CONTRADICTED_ON_LOCALIZATION_GAP`, `CONFLICT_MISSING_DUAL_ANCHORS`)
- `warnings[]`: non-blocking advisories
- `citation_integrity_drift_count`: integer
