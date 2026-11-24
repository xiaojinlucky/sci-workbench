# Output Contract Specification (`result.json` & `mindmap_audit.json`)

Every run must populate `/app/output/result.json` with all five core Harbor writing contract fields:
- `case_id`: Exact string copied from `/input/case.json`.
- `deliverable`: Object listing exported editable artifacts (`json`, `svg`, `mm`, `mmd`, `png_preview`, `audit_json`).
- `preserved_facts`: Summary of grounded node/edge counts, `replacement_char_count: 0`, and epistemic distribution (`fact` / `summary` / `inference`).
- `evidence_map`: Node/edge citation backlinks (`node_id`, `file`, `locator`, `verified_text`) copied from `mindmap_audit.json`.
- `change_log`: Build decisions, sanitized external/UNC references, unrendered complex math notes, human confirmation items, and unresolved gaps.
- `sources` and `uncertainty` / `gaps`: Honest listing of read `/input` files and any unverified PDF locators or missing offline rendering fonts.
