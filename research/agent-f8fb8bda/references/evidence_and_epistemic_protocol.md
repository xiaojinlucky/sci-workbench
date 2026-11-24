# Evidence, Conflict, and Epistemic Boundary Protocol

## 1. Requirement vs. Evidence Separation
- Separate what the task asks (`case.json` / `task.md`) from what the local literature actually shows. Never let a cited paper's conclusion silently rewrite the research question or presuppose an experimental outcome.

## 2. Three-Way Epistemic Tagging
- `FACT_OBSERVED`: Directly stated in inspected full text (`fulltext_pdf`, `fulltext_xml`, `fulltext_local_text`) with an exact page or section locator.
- `INFERENCE_BOUNDED`: Cross-paper comparison, functional equivalence assessment, or observation derived from `abstract_only` records where full methods/statistics were not inspected.
- `UNVERIFIED_OR_GAP`: Missing full text, paywalled/blocked source, truncated API page, prospective protocol not yet executed, or unverified claim.

## 3. Side-by-Side Conflict Presentation
When supportive and opposing/null findings coexist on a sub-question:
1. Present both groups with `source_id`, `material_access`, and exact passage locator.
2. Compare actual operational details (organism/cohort, assay/catalyst/architecture, dose/potential, endpoint definition) rather than labels alone.
3. State what minimum decisive evidence would resolve the disagreement; never average opposing findings into a smooth consensus sentence.

## 4. Access-Barrier Handling (CAPTCHA / SSO / 403 / 429 / Empty Scan)
- Log the barrier once in `access_log` with `source_file`, `record_id`, `status`, `reason`, and `action="recorded_and_stopped_without_bypass"`.
- Continue synthesizing all accessible sources cleanly and mark affected sub-questions as `partial`.
- Never retry-flood, claim exhaustive retrieval, or fabricate text for unread pages.
