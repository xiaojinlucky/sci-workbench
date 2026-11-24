---
name: multi-db-federated-search
description: Multi-database federated literature search orchestration, schema standardization, 4-tier deduplication with hard same-title distinct-formal-DOI anti-false-merge guard, deterministic conflict resolution, per-field provenance lineage, source exclusivity/overlap matrices, and degraded-source gap disclosure. Use for federated multi-database retrieval tasks.
---

# Multi-Database Federated Search (`challenge-5c918ff0`)

## 1. Mission & Non-Negotiable Boundaries
This Skill directs an offline research Agent (DeepSeek Harness) to orchestrate, standardize, deduplicate, merge, and audit heterogeneous bibliographic snapshots across **Crossref, OpenAlex, Europe PMC, PubMed, arXiv**, and standard exports (`.bib`, `.ris`, `.nbib`, `.csv`, normalized JSON).

Success is judged on three strict criteria rather than raw query count:
1. **Reliable Identity & Merge Integrity**: Distinct sources converge into a single deduplicated record set without falsely collapsing distinct studies that share a title.
2. **End-to-End Field Lineage**: Every merged record and key field traces back along `database -> adapted_query -> raw_response (file + JSON pointer) -> field_mapping -> dedup_match_tier -> merged_canonical`.
3. **Explicit Coverage & Failure Transparency**: Single-database exclusive papers, cross-database overlaps, systematically missing fields, and unavailable/rate-limited databases (e.g., PubMed HTTP 429) are surfaced explicitly—never silently swallowed.

**Hard Boundaries (Never Violate):**
- **No Naive Concatenation**: Never concatenate heterogeneous API responses or copy one identical query string across databases with incompatible syntaxes.
- **Hard Anti-False-Merge Guard**: Never auto-merge two records carrying **distinct formal (non-preprint) DOIs**, even at `title_similarity == 1.0`. Route them to `ambiguous_same_title_distinct_doi` for human arbitration.
- **No Silent Deletion (ASReview #328)**: Every merged duplicate must remain traceable in `dedup_rollback_log.json` (`duplicate_status = "duplicate_ignored_with_trace"`) and satisfy `source_count_conserved == True`.
- **No Fabricated Coverage**: Never fabricate records for a rate-limited, timed-out, or missing database; disclose it in `degraded_sources` and set `result.json:status = "partial_with_gaps"`.
- **No Rate-Limit Bypass / No Live Network Calls**: Pure Python 3.12 stdlib offline execution only.

---

## 2. Quickstart Execution Protocol (DeepSeek Harness)
Locate all scripts relative to this `SKILL.md` directory (`scripts/`).

### Step 1 — Preflight, Budget Init & Federated Merge Pipeline
```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/federated_search_merger.py --input /input --output /app/output --work /app/work
```
This single run executes:
- Structure-agnostic multi-database parsing (Crossref `work`/`work-list`, OpenAlex `results` + `abstract_inverted_index` reconstruction, EuropePMC `resultList`, PubMed XML/JSON, arXiv Atom XML, normalized bibliographic JSON).
- Multi-source query syntax adaptation across all target databases.
- Per-source field coverage matrix (`field_coverage_by_source`) and systematic missing-field detection (`systematic_missing_fields`).
- 4-tier deduplication with hard distinct-formal-DOI guard (`ambiguous_same_title_distinct_doi`), preprint-to-VoR chaining (`preprint_to_vor_chains`), deterministic conflict resolution (`field_conflicts_resolved`), and ASReview #328 `rollback_log`.
- Single-source exclusivity (`exclusive_works_by_source`), N x N pairwise overlap (`source_overlap_matrix`), and degraded/unavailable source triage (`degraded_sources`).
- Atomic emission of `result.json`, `federated_merged.csv`, `source_coverage_matrix.json`, `dedup_rollback_log.json`, `ambiguous_arbitration_queue.json`, `report.md`, `audit_log.json`, and `run_manifest.json`.

### Step 2 — Grounding & Deliverable Audit (M5 + M6)
```bash
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require federated_merged.csv --require source_coverage_matrix.json \
  --require dedup_rollback_log.json --require ambiguous_arbitration_queue.json \
  --require report.md
```
Both commands must exit `0` with zero `UNGROUNDED_IDENTIFIER` errors and zero `MISSING_METRIC_PROVENANCE` warnings.

---

## 3. Step-by-Step Federated Methodology

### Phase A — Multi-Source Query Syntax Adaptation Matrix
Never copy one raw search string identically to all databases. Adapt both Round 1 (core terms) and Round 2 (corpus-mined abbreviations + co-occurring terms) to each database's native syntax (emitted in `source_coverage_matrix.json:per_source_queries`):

| Database | Native Query Syntax Pattern | Date Filter Syntax | Key Field Capability & Quirk |
|---|---|---|---|
| **Crossref** | Space-delimited bibliographic terms (`query.bibliographic`) | `[filter:from-pub-date:YYYY]` | High DOI/venue authority; JATS abstracts often absent by publisher choice |
| **OpenAlex** | Keyword search string | `\| filter=publication_year:YYYY-YYYY` | Inverted-index abstracts (`abstract_inverted_index`), rich `cited_by_count` & `referenced_works` |
| **Europe PMC** | Quoted Boolean `("term1" OR "term2")` | `AND (PUB_YEAR:[YYYY TO YYYY])` | Rich biomedical abstracts (`abstractText`), `pmid`/`pmcid`, MeSH descriptors |
| **PubMed** | Field-tagged Boolean `(term1[tiab] OR term2[tiab])` | `AND YYYY:YYYY[dp]` | Authoritative biomedical index; no native citation counts; strict rate limits (HTTP 429) |
| **arXiv** | Field-prefixed Boolean `all:term1 AND all:term2` | Post-filter on Atom `<published>` | Fast preprint coverage; lacks publisher formal DOI & citation counts structurally |

### Phase B — Field Semantic Standardization & Missing vs Unknown Distinction
Normalize heterogeneous source keys into a unified schema (`title`, `abstract`, `authors`, `year`, `venue`, `doi`, `alt_dois`, `arxiv_id`, `pmid`, `version`, `citations_count`, `language`, `is_retracted`) while explicitly distinguishing **three non-equivalent states**:
1. **Populated Value**: Present and normalized with exact `source:file#pointer` provenance.
2. **Structurally Absent / Systematically Missing (`fill_rate <= 15%`)**: Database schema does not expose the field (e.g., `citations_count` in arXiv Atom XML or `abstract` in Crossref deposits where publishers withhold abstracts). Record as `null`/missing with `semantic_note`—**NEVER** substitute `0` for a missing citation count!
3. **Unknown Due to Degraded Source**: When a database fails (e.g., PubMed HTTP 429), its fill rates are recorded as `null` (`ALL_UNAVAILABLE_SOURCE`), strictly distinct from `0%`.

### Phase C — 4-Tier Identity Matching & Hard Same-Title Distinct-DOI Guard
Records are resolved via Union-Find across 4 deterministic tiers (`scripts/search_core/dedup.py`):
- **Tier 1 (`T1:doi`, `T1:pmid`, `T1:arxiv`)**: Exact match on normalized DOI, PMID, or arXiv base ID (version suffix stripped).
- **Tier 2 (`T2:alt_doi_relation`, `T2:preprint_doi_to_vor`, `T2:arxiv-doi-title`, `T2:arxiv-doi-fuzzy`)**: Links preprints (`10.48550/arxiv.*`, `10.1101/*`, `10.26434/*`, etc.) to published Versions of Record (VoR) via `alt_dois` or high title+first-author agreement (`>= 0.88`). Promotes VoR as canonical while retaining the preprint in `version_group` and `preprint_to_vor_chains`.
- **Tier 3/4 (`T3:title`, `T4:title+author+year`)**: High-similarity title (`>= 0.92` or `>= 0.82` with matching first author & `|year_diff| <= 1`) **ONLY** when records do NOT carry conflicting formal DOIs.
- **Hard Guard (`ambiguous:distinct_formal_dois`)**: If two records have `title_similarity >= 0.80` AND carry **two distinct non-preprint DOIs**, auto-merging is **hard-blocked**. Both records remain separate canonical entries and are written to `ambiguous_arbitration_queue.json` with authors, venues, years, DOIs, and `recommended_action: "keep_separate_unless_erratum_or_parallel_edition_confirmed"`.

### Phase D — Deterministic Field Conflict Resolution (`version_rank > source_authority > field_richness`)
When merged members disagree on `year`, `venue`, `version`, `language`, `abstract`, `authors`, or `citations_count`, select the canonical field deterministically using the 3-tuple priority key:
1. **`version_rank`**: `published (4) > conference (3) > preprint (2) > unknown (0)`.
2. **`source_authority`** (field-specific):
   - `doi / title / year / venue`: `crossref / publisher (5) > openalex / europepmc / pubmed (4-3) > arxiv (2-1)`.
   - `abstract`: `europepmc / pubmed (5) > arxiv / openalex (4) > crossref / publisher (3)`.
   - `citations_count`: `openalex (5) > crossref / s2 (4) > europepmc (3)`.
3. **`field_richness`**: Longer/more complete string or author list breaks remaining ties.
4. **Full Raw Value Preservation**: Never overwrite conflicts silently. Every resolved conflict is logged in `field_conflicts_resolved` with all raw values, their `source:file#pointer` locators, the winning value, winning source, and resolution rule.

### Phase E — 6-Step Provenance Lineage, Coverage Matrices & Degraded-Source Disclosure
1. **Per-Record 6-Step Lineage**: Each entry in `result.json:results.canonical_records` embeds `lineage = {libraries, queries_adapted, raw_responses, field_mapping_provenance, dedup_match_tiers, conflicts_resolved}`.
2. **Exclusivity & Overlap**: Report `exclusive_works_by_source` (papers found in only one database—quantifying why multi-database search was necessary) alongside the N x N `source_overlap_matrix` and pairwise Jaccard overlap.
3. **Count Conservation & Rollback**: Assert `raw_record_count == canonical_record_count + dedup_merged_count` (`source_count_conserved == True`) and emit `dedup_rollback_log.json` with an explicit split action per merged duplicate.
4. **Degraded Source Triage**: Any file reporting `status in {unavailable, rate_limited, error, failed, timeout, truncated}` (e.g., `api/pubmed_unavailable.json` HTTP 429), any unparseable file (`source_load_errors`), or any expected database absent from `/input` is captured in `degraded_sources` with explicit coverage impact and mitigation.

---

## 4. Required Output Contract & Host Agent Verification Checklist
All deliverables live under `/app/output/`:
- `result.json` — Contains `case_id`, `status` (`"ok"` or `"partial_with_gaps"`), `results`, `evidence`, `status_report`, `source_count_conserved`, `field_coverage_by_source`, `exclusive_works_by_source`, `source_overlap_matrix`, `ambiguous_same_title_distinct_doi`, `preprint_to_vor_chains`, `field_conflicts_resolved`, `rollback_log`, `degraded_sources`, `metrics`, `metric_provenance`, `findings`, `gaps`, `uncertainty`, and `artifacts`.
- `federated_merged.csv` — Canonical deduplicated table with per-record provenance summaries, exclusivity flags, conflict counts, and source locators.
- `source_coverage_matrix.json` — Adapted per-source queries, field fill rates, systematically missing fields, exclusivity counts, overlap matrix, and degraded sources.
- `dedup_rollback_log.json` — Complete ASReview #328 reversible deduplication ledger.
- `ambiguous_arbitration_queue.json` — All same-title distinct-formal-DOI pairs kept separate for human review.
- `report.md` — Audit-ready Markdown report separating scientific coverage conclusions from backend diagnostics.
- `audit_log.json` & `run_manifest.json` — SHA-256 hash-chained log and file inventory.

**Host Agent Pre-Exit Checks:**
1. Verify `source_count_conserved == True` and confirm zero same-title distinct-formal-DOI pairs were merged.
2. Inspect `degraded_sources` and `systematic_missing_fields` in `report.md` to confirm every unavailable database (e.g., PubMed HTTP 429) and schema gap is transparently stated.
3. Confirm `gap_gate.py` and `final_audit.py` both exited `0`.

---

## 5. DeepSeek Harness Inspection & Diagnostic Recipes
After executing `federated_search_merger.py` and M5/M6 audits, inspect the merged state directly before drafting your scientific summary:

```bash
python3 -B -c "
import json
d = json.load(open('/app/output/result.json'))
print('STATUS:', d['status'], 'CONSERVED:', d['source_count_conserved'])
print('METRICS:', json.dumps(d['metrics']))
print('DEGRADED:', [(x['source'], x['status'], x['error']) for x in d['degraded_sources']])
print('AMBIGUOUS_DISTINCT_DOI:', [(x['doi_a'], x['doi_b'], x['action']) for x in d['ambiguous_same_title_distinct_doi']])
print('PREPRINT_TO_VOR:', [(x.get('canonical_doi'), x.get('canonical_year')) for x in d['preprint_to_vor_chains']])
print('CONFLICTS_RESOLVED:', len(d['field_conflicts_resolved']))
print('EXCLUSIVE:', d['exclusive_works_by_source']['counts_by_source'])
"
```

### Worked Verification Scenarios (How Each Trap Is Handled)
1. **Trap 1 — Same Title, Distinct Formal DOIs (Anti-False-Merge Guard)**:
   - *Scenario*: Crossref returns `"Stability Mechanisms of Iridium Oxide..."` (`10.1016/...`, M. Zhang, *Journal of Catalysis*) while Europe PMC returns an independent study with the identical title (`10.1021/...`, K. Patel, *ACS Energy Letters*), OR OpenAlex returns parallel language editions (`10.1002/anie...` vs `10.1002/ange...`).
   - *Required Behavior*: Both formal DOIs are non-preprint -> `same_work()` returns `(False, "ambiguous:distinct_formal_dois", 1.0)`. Both records remain separate canonical entries in `federated_merged.csv` and are queued in `ambiguous_arbitration_queue.json` with authors, venues, years, and DOIs side by side.
2. **Trap 2 — Preprint <-> Version-of-Record Upgrade Chain**:
   - *Scenario*: arXiv holds `2305.01234v1` (`10.48550/arxiv.2305.01234`, year `2023`, venue `"arXiv"`, version `"preprint"`), while Crossref holds the peer-reviewed paper (`10.1038/...`, `alt_dois: ["10.48550/arxiv.2305.01234"]`, year `2024`, venue `"Nature Catalysis"`, version `"published"`).
   - *Required Behavior*: Matched cleanly via `T2:alt_doi_relation` (or `T2:arxiv-doi-title` when arXiv XML lacks DOI). The published VoR becomes canonical; `year` (`2023` vs `2024`), `venue` (`arXiv` vs `Nature Catalysis`), and `version` (`preprint` vs `published`) are logged in `field_conflicts_resolved` with all raw values preserved, and the arXiv record is logged in `dedup_rollback_log.json` (`duplicate_ignored_with_trace`).
3. **Trap 3 — Unavailable / Rate-Limited Database (e.g., PubMed HTTP 429)**:
   - *Scenario*: `/input/api/pubmed_unavailable.json` reports `{"source": "pubmed", "status": "unavailable", "error": "HTTP 429 rate limit exceeded"}`.
   - *Required Behavior*: Captured in `degraded_sources` and `gaps` (`DEGRADED_OR_UNAVAILABLE_SOURCE`), forcing `result.json:status = "partial_with_gaps"`. In `field_coverage_by_source["pubmed"]`, fill rates are set to `null` (`ALL_UNAVAILABLE_SOURCE`), never silently dropped or misreported as 0 hits.
4. **Trap 4 — Systematic Cross-Database Field Gaps**:
   - *Scenario*: arXiv Atom XML provides `0%` `citations_count`; Crossref REST omits publisher-withheld `abstract` fields on most records (`<= 15%`).
   - *Required Behavior*: Highlighted explicitly in `source_coverage_matrix.json:systematic_missing_fields` with `classification` (`structurally_absent` vs `systematically_sparse`) and `semantic_note`, ensuring downstream users never treat missing arXiv citations as `0` citations.

### Failure-Mode Triage Table
| Condition | Pipeline Output Field | Host Agent Disclosure Rule |
|---|---|---|
| Rate limit / timeout / HTTP 429 | `degraded_sources` + `gaps` | State exact affected database, error message, and unmeasured single-source coverage gap. |
| Malformed / truncated JSON file | `degraded_sources` (`status="parse_error"`) | Report file path and parser error; never treat as zero records. |
| Valid empty JSON array `[]` | `source_coverage_matrix.json:valid_empty_sources` | Report cleanly as 0 returned records (healthy response, not a failure). |
| Same title + distinct formal DOIs | `ambiguous_same_title_distinct_doi` | Present pair for human review; affirm zero silent merging occurred. |
| Retracted paper in any source | `is_retracted: true` propagated to cluster | Highlight retraction status prominently so retracted studies are never cited uncritically. |
| Multi-source disagreement on year/venue/abstract | `field_conflicts_resolved` + `field_provenance` | Report winning value, deterministic rule, and raw source alternatives. |

---

## 6. Budget Discipline, Pre-Exit Checklist & References
- **Execution Discipline**: Run `federated_search_merger.py` in turn 1, inspect `result.json` + `source_coverage_matrix.json` + `ambiguous_arbitration_queue.json` in turn 2, run `gap_gate.py` + `final_audit.py` in turn 3, and deliver your concise, evidence-grounded synthesis without fabricating missing database coverage.
- **Front-End / Back-End Reporting Separation**: In the final user-facing synthesis (`report.md` and chat reply), lead with scientific coverage findings (unique papers found per source, cross-database overlap, unavailable databases, and arbitration items) using clear domain language while keeping JSON pointers, match tiers, and SHA-256 chain heads in the structured artifacts.

### Pre-Exit Audit Verification Checklist
- [ ] `result.json` parses cleanly with zero `NaN`/`Infinity` values and `status` in `{"ok", "partial_with_gaps"}`.
- [ ] `source_count_conserved == True` (`raw_record_count == canonical_record_count + dedup_merged_count` and member sum == raw).
- [ ] `source_coverage_matrix.json` reports `per_source_queries` adapted per database (no verbatim query copy across incompatible syntaxes).
- [ ] `field_coverage_by_source` distinguishes populated values, structurally absent fields (`systematic_missing_fields`), and unavailable databases (`null`).
- [ ] `exclusive_works_by_source` and N x N `source_overlap_matrix` are populated and consistent with `federated_merged.csv`.
- [ ] Zero same-title distinct-formal-DOI pairs were falsely merged; all are listed in `ambiguous_arbitration_queue.json`.
- [ ] Every preprint-to-VoR pair is recorded in `preprint_to_vor_chains` with canonical VoR promoted and preprint retained.
- [ ] Every cross-source field conflict is resolved deterministically via `version_rank > source_authority > field_richness` with all raw values preserved.
- [ ] Every merged duplicate has a reversible entry in `dedup_rollback_log.json` (ASReview #328).
- [ ] Every unavailable or rate-limited source (e.g., PubMed HTTP 429) appears in `degraded_sources` and `gaps`.
- [ ] `gap_gate.py` (`ok: true`, `ungrounded_identifiers == []`, `ungrounded_metrics == []`) and `final_audit.py` (`passed: true`) both exited `0`.

### Additional References
- `references/federated_schema_mapping.md` — Complete field-mapping table, authority priority rules, and missing-vs-unknown semantics.
- `references/output_contract_spec.md` — Machine-readable schema definitions for all deliverables.
- `examples/sample_input_case.json` & `examples/sample_output_schema_example.json` — Minimal illustrative interface examples.


