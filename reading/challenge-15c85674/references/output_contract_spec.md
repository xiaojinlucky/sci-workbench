# Output Contract Specification (`challenge-15c85674`)

## 1. Runtime & Dual-Path Compatibility

- **Skill Mount Paths**: Locate scripts relative to `SKILL.md` so both `/skills/literature-qa` and `/skills/weekly-skill` work identically.
- **Input Path**: `/input/case.json` (plus any referenced or co-located `.pdf` / `.md` / `.txt` files under `/input`).
- **Work Path**: `/app/work/` (stores `qa_retrieval_pack.json`, `indexed_corpus.json`, `answers_draft.json`, `env.json`, `gaps.json`).
- **Final Output Paths**:
  1. `/app/output/result.json` (primary Harbor deliverable; retains all original answer fields plus Harbor top-level `status` in `{"ok", "partial_with_gaps"}`, `artifacts`, `metrics`, `metric_provenance`, `findings`, `gaps`, `schema_validation`).
  2. `/app/answers.json` (legacy compatibility mirror written atomically whenever `/app` exists and is writable) and `/app/output/answers.json`.

## 2. Exact Answer Payload Fields

| Level | Field | Type & Requirement |
| --- | --- | --- |
| Top | `case_id` | String, exact match to input `case.json`. |
| Top | `paper_id` | String, exact match to input `case.json`. |
| Top | `material_version` | String, exact match to input `case.json`. |
| Top | `status` | In `/app/output/result.json`: `"ok"` (zero blocking gaps) or `"partial_with_gaps"`. |
| Top | `answers[]` | Array with one entry per input `question_id` (zero omissions, zero duplicates). |
| Answer | `question_id` | String matching input question ID. |
| Answer | `answer` | Concise, accurate answer addressing every resolved sub-question and noting conflicts/gaps. |
| Answer | `conditions[]` | Array of strings capturing exact numerical thresholds, SI units, comparators, sample sizes, and scope bounds. |
| Answer | `status` | Strictly one of `"answered"`, `"partial"`, `"insufficient_evidence"`, `"input_error"`. |
| Answer | `claims[]` | Array of `{claim_id, text, kind, evidence[]}`. |
| Claim | `kind` | Strictly `"reported"` (explicitly stated in source) or `"inferred"` (cross-paragraph reasoning). |
| Claim | `evidence[]` | Array of `{paragraph_id, quote}`. Every `reported` claim requires >= 1 verified verbatim quote. |
| Answer | `unanswered_parts[]` | Array of `{part, reason}` (or non-empty strings). Must be `[]` iff `status == "answered"`. |
| Top | `coverage` | `{read_sections[], unread_sections[], unread_reason, total_paragraphs_read, page_coverage_audit{ingested_pages, extracted_pages, empty_or_scanned_pages[], indexed_paragraphs, cited_paragraphs, cited_pages[], tail_page_coverage_check, table_footnote_coverage_check}}`. |

## 3. Seven Blocking Validation Gates (`--validate-answers`)

1. Input identifiers (`case_id`, `paper_id`, `material_version`) match `/input/case.json`.
2. Every input `question_id` is answered exactly once.
3. Every `evidence[].paragraph_id` exists in the indexed corpus and `quote` matches **that exact paragraph** (whitespace-normalized verbatim substring; cross-paragraph quote borrowing fails).
4. Every `kind == "reported"` claim has >= 1 verified verbatim `evidence` entry.
5. `status == "answered"` requires `unanswered_parts == []`; `partial` and `insufficient_evidence` require non-empty `unanswered_parts[]`.
6. Every numeric+SI unit pair declared in `conditions[]` matches the verified cited paragraphs for that question (blocks unit drift like `mg -> μg` or invented thresholds).
7. `coverage` faithfully includes any unread sections and unreadable/scanned pages detected during indexing.

On failure, the CLI exits with code `1`, writes `schema_validation.ok = false` and `QA_SCHEMA_ERRORS` to `/app/output/result.json` (`status: "partial_with_gaps"`), and refuses to mark the run clean.
