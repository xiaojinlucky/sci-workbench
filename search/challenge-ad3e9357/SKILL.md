---
name: patent-tech-retriever
description: Execute auditable patent and technical literature retrieval with WIPO ST.16 kind-code normalization, dual DOCDB simple vs INPADOC extended patent family grouping, pyUSPTO-safe CPC/IPC slash-escaped queries, source-located claim decomposition, WO merged OCR boundary retention, strict offline UNKNOWN live legal status, Green/Yellow/Red authorization tiering, and three-perspective scientific literature comparison. Use for patent and technical literature search tasks.
---

# Patent & Technical Literature Search (`challenge-ad3e9357`)

## 1. Mission, Scope & Legal/Epistemic Boundaries

Execute structured, verifiable patent and technical literature retrieval over finite local patent records, official USPTO/EP/WO facsimiles (HTML/PDF/JSON), and comparison scientific papers in `/input/case.json` and `/input/task.md`.

### Core Guarantees
1. **Strict WIPO ST.16 Field Separation**: Never conflate `publication_number`, `application_number`, `grant_number`, `kind_code`, `priority_date`, `filing_date`, `publication_date`, and `grant_date`. Clearly distinguish published applications (`A1/A2/A3`), grants (`B/B1/B2/C`), utility models (`U/U1/Y`), and reissues/corrections (`E/A4`).
2. **Dual Patent Family Reconciliation**: Compute and report side-by-side: (a) `publication_document_count` (all distinct publication documents including same-base `A1/A4/B1/B2`), (b) `docdb_simple_family_count` (exact identical normalized priority set equivalence classes), and (c) `inpadoc_extended_family_count` (Union-Find connected components sharing at least one direct or indirect priority/continuation link). Never pool unrelated keyword-matched patents into one family.
3. **Slash-Safe CPC/IPC & Dual-Track Queries**: Normalize CPC/IPC codes (e.g., `H01M 10/0525`, `C25B 11/04`, `H01M 8/16`) into slash-escaped and quoted engine-safe forms (`cpc:"H01M 10/0525"`, `cpc:(H01M10\/0525)`) and keep patent search queries strictly separate from scientific/technical literature queries.
4. **Source-Located Claim Attribution & WO OCR Honesty**: Tie every claim summary to its exact `claim_version_source`, keep `equal_claim_scope_assumed: false` across jurisdictions, and retain `wo_merged_ocr_boundary` whenever WO documents, HTML `class="claims ocr"`, or sparse/scanned PDFs appear.
5. **Offline Legal Status Honesty**: In any finite offline snapshot, `current_official_legal_status` MUST remain `"UNKNOWN"`. Static status fields from snapshots are stored strictly as `observed_snapshot_legal_status` with `legal_status_snapshot_date` and `legal_status_source`.

### Non-Negotiable Out-of-Scope & Compliance Boundaries
- **Not Legal Advice**: Output is technical/informational retrieval only—never issue Freedom-to-Operate (FTO), infringement, validity, or patentability legal opinions.
- **No Paywall Bypass or Restricted Exfiltration**: Never scrape paywalled commercial databases, bypass authentication, upload confidential disclosures, or redistribute restricted commercial full texts.
- **No Exhaustive Family Overclaim**: Always tag family outputs with `observed_links_only_not_exhaustive: true` and never treat a finite snapshot as exhaustive global patent family coverage.
- **No Filename-Based Hallucination**: Always read the actual patent HTML/PDF/JSON files and verify claims, dates, and CPC/IPC codes directly against the source text.

## 2. Three-Perspective & Authorization-Tiered Search Architecture

Following evidence-anchored search methodology (`references/search_methodology_protocol.md`):

- **Broad-Then-Screen (Screening, Not Cherry-Picking)**:
  - Phase A (Broad Discovery): Index all patent numbers, WIPO kind codes, priority chains, CPC/IPC subclasses, assignees, and scientific comparison papers across the entire supplied snapshot.
  - Phase B (Transparent Separation): Separate distinct publication documents from DOCDB simple and INPADOC extended families, separate scientific papers from patents, and explicitly log keyword-similar but unlinked patent pairs (`keyword_only_distinct_patents_separated`).
- **Three-Perspective Technical Triangulation**:
  1. *Patent Applicant / Claim-Scope Perspective*: Inspect independent Claim 1 boundaries, dependent claim structure, priority chain, and jurisdiction-specific kind codes (`A1` vs `B2` vs `U` vs `A4`).
  2. *Technical Implementer Perspective*: Extract concrete material compositions, electrolyte/catalyst formulations, process steps, CPC/IPC subclasses, and operational constraints directly from patent specifications and facsimiles.
  3. *Independent Novelty Evaluator Perspective*: Contrast patent disclosures against independent peer-reviewed scientific comparison papers (`comparison_scientific_papers`), highlighting shared mechanisms, empirical performance gaps, OCR caveats, and live legal-status unknowns.
- **Authorization Tiering (Green / Yellow / Red)**:
  - **Green (Authorized Local / Open Official Facsimiles)**: Full bibliographic, classification, and source-located claim analysis permitted with explicit source citation.
  - **Yellow (Secondary Aggregators / Dated Static Snapshots)**: Permitted for discovery, classification extraction, and observed priority links; live official legal status MUST remain `UNKNOWN` with snapshot date + lag caveat.
  - **Red (Paywalled / Restricted Commercial / Confidential Enterprise Data)**: Zero unauthorized access, zero full-text reproduction; degrade honestly to authorized metadata and explicit authorization gap reporting.
- **Front-End Clarity vs Back-End Auditability (W3, W5, W8)**:
  - Present decision-ready patent landscape tables, dual family summaries, and claim excerpts in `/app/output/patent_landscape_report.md`.
  - Retain deterministic normalization records, Union-Find linking keys, and SHA-256 hash-chained logs in `/app/output/patent_search_report.json` and `/app/work/audit_log.json`.

## 3. Five-Step Execution Workflow

Locate all scripts relative to this `SKILL.md` directory (`scripts/`). Perform working inspection under `/app/work` and emit final deliverables under `/app/output`.

### Step 1: Preflight, Authorization Tiering & Corpus Inventory
Probe the offline environment, initialize budget tracking, and inspect `/input/case.json`, `/input/task.md`, and all supplied files directly:
```bash
python scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python scripts/budget_guard.py --state /app/work/.budget_state.json --init
python scripts/block_indexer.py --scan /input --out /app/work/blocks.json
```
Document four operational facts before running normalization:
1. **Record Partition**: Count patent HTML/JSON/PDF facsimiles versus non-patent scientific comparison papers.
2. **Authorization & Confidentiality Tier**: Classify each source as Green, Yellow, or Red (`references/patent_family_and_legal_protocol.md`).
3. **PDF Facsimile Readability**: Inspect `text_layer_status` (`full_text`, `sparse_text`, `empty_or_scanned`) for any supplied USPTO/EP/WO PDF facsimiles.
4. **Query Modes**: Distinguish patent-number lookup queries, CPC/IPC classification queries, and technical-topic comparison queries.

### Step 2: WIPO ST.16 Normalization & Slash-Safe CPC/IPC Query Generation
Execute the pure-stdlib patent retrieval auditor:
```bash
python scripts/patent_retrieval_auditor.py --input /input --output /app/output --work /app/work
```
Verify in `/app/output/patent_search_report.json`:
1. **WIPO ST.16 Field Separation**: Every patent separates `publication_number`, `application_number`, `grant_number`, `kind_code`, `stage`, `jurisdiction`, `priority_date`, `filing_date`, `publication_date`, `grant_date`, `assignee`, and `inventors`.
2. **Non-Fabrication of Grants**: Published applications (`A`, `A1`, `A2`, `A3`) and corrections (`A4`) NEVER fabricate a `grant_number` or `grant_date` unless an explicit granted specification (`B/B1/B2/C`) is present.
3. **Slash-Safe CPC/IPC Query Construction (`pyUSPTO #101`)**: Every extracted CPC/IPC code (e.g., `H01M 8/16`, `C25B 3/01`, `H01M 10/0525`) emits canonical, USPTO-quoted (`cpc:"H01M 8/16"`), Lucene-escaped (`cpc:(H01M8\/16)`), and Espacenet-safe variants.
4. **Dual-Track Query Separation**: Confirm `patent_search_queries` (CPC/IPC + structural claim terms) are strictly separated from `scientific_literature_queries` (stripped of CPC/patent numbers to prevent zero-recall in academic indexes).

### Step 3: Dual Patent Family Consolidation (DOCDB Simple vs INPADOC Extended)
Audit `family_consolidation` in `/app/output/patent_search_report.json` to reconcile publication counts vs family counts cleanly:

| Count Layer | Exact Rule | Example Behavior |
| --- | --- | --- |
| `publication_document_count` | Distinct `(jurisdiction, number, kind_code)` documents | `US...A1` + `US...A4` or `CN...A` + `CN...B` = **2** docs |
| `docdb_simple_family_count` | Strict equivalence class with **exact identical** normalized priority set | Patent X `{P1}` vs CIP Patent Y `{P1, P2}` = **2** DOCDB families |
| `inpadoc_extended_family_count` | Union-Find connected components sharing `>= 1` direct/indirect priority or parent link | Patent X `{P1}` + CIP Patent Y `{P1, P2}` = **1** INPADOC family |

Verify three invariants:
- `inpadoc_extended_family_count <= docdb_simple_family_count <= publication_document_count`.
- `keyword_only_distinct_patents_separated` explicitly confirms that patents sharing topic keywords/CPC classes without shared priority or parent links were NOT merged.
- Every family sets `observed_links_only_not_exhaustive: true` and `equal_claim_scope_assumed: false`.

### Step 4: Source-Located Claim Decomposition, WO OCR Boundary & Scientific Contrast
Inspect `patents[].claims_analysis`, `patents[].wo_merged_ocr_boundary`, and `comparison_scientific_papers`:
1. **Claim Decomposition**: Verify `independent_claim_1_excerpt`, `observed_claim_count`, `independent_claim_count`, `dependent_claim_count`, and `claim_version_source`. Never extrapolate claims from an abstract or assume identical legal scope across US/EP/WO/CN family members.
2. **WO Merged OCR Audit**: Whenever `wo_merged_ocr_boundary == true` (triggered by WO jurisdiction, HTML `class="claims ocr"`, explicit `ocr_boundary` flag, or sparse/scanned PDF layer), verify `wo_ocr_reasons` and explicitly warn that original facsimile review is mandatory before relying on OCR text/chemical formulas.
3. **Three-Perspective Scientific Comparison**: Keep `comparison_scientific_papers` in their own track; contrast disclosed scientific mechanisms and experimental performance against patent claims without counting academic papers inside patent families.

### Step 5: Static Snapshot Legal Status Check, Grounding Gate & Final Audit
Confirm every patent record maintains `current_official_legal_status == "UNKNOWN"` offline while preserving `observed_snapshot_legal_status`, `legal_status_snapshot_date`, and `legal_status_source`. Review `/app/output/patent_landscape_report.md`, then execute mandatory verification:
```bash
python scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python scripts/final_audit.py --dir /app/output --result-file result.json --require patent_search_report.json --require patent_landscape_report.md
```
Block delivery if `final_audit.py` fails, any DOI is ungrounded, or any live official legal status is fabricated.

## 4. Pre-Mortem Risk Matrix & Honest Degradation

| Priority | Failure Mode | Early Observable Signal | Preventive Guardrail | Fallback / Remediation |
| --- | --- | --- | --- | --- |
| P0 | Publication count conflated with family count (Espacenet 118 vs 71) | Single undifferentiated count reported | Dual DOCDB simple + INPADOC extended + publication doc counts | Always output all 3 counts with explicit definitions |
| P0 | Grant number/date fabricated from `A1/A2` publication | `A1` record has non-null `grant_number` | WIPO ST.16 parser isolates `is_grant` vs `is_published_application` | Leave `grant_number`/`grant_date` null for applications |
| P0 | Unrelated keyword-matched patents merged as one family | Patents with no shared priority share a `family_id` | Priority/parent-only Union-Find + `keyword_only_distinct_patents_separated` | Keep distinct singletons; note topic similarity separately |
| P0 | Static aggregator status claimed as live official status | `current_official_legal_status != "UNKNOWN"` offline | Hard offline `UNKNOWN` invariant + separate snapshot fields | Report snapshot status + date + official register check needed |
| P1 | Unescaped CPC/IPC slash breaks search query (`pyUSPTO #101`) | Bare `H01M 10/0525` sent to Lucene/USPTO API | `normalize_cpc_ipc_code` emits quoted & `\/`-escaped variants | Provide both USPTO-safe quoted and Lucene-escaped syntax |
| P1 | WO merged OCR errors silently treated as authoritative claims | `class="claims ocr"` or scanned PDF unflagged | Automatic `wo_merged_ocr_boundary` + `wo_ocr_reasons` | Retain explicit caveat; require original facsimile verification |
| P1 | Paywalled/restricted source or legal FTO overreach | Request asks for infringement verdict or paywalled fetch | Red-tier boundary & explicit non-FTO disclaimer | Refuse legal opinion; deliver technical feature comparison only |

## 5. Self-Check Checklist Before Finishing

Before ending a run, verify every item below against `/app/output/result.json` and `/app/output/patent_search_report.json`:
- [ ] `case_id` matches `/input/case.json` verbatim and `status` is `"ok"` (or honest `"partial"` with documented gaps).
- [ ] All three counts (`publication_document_count`, `docdb_simple_family_count`, `inpadoc_extended_family_count`) are present and satisfy `INPADOC <= DOCDB <= publication_docs`.
- [ ] Every patent record has `current_official_legal_status == "UNKNOWN"` and `equal_claim_scope_assumed == false`.
- [ ] Every WO / OCR-flagged document carries `wo_merged_ocr_boundary == true` and explicit `wo_ocr_reasons`.
- [ ] CPC/IPC queries emit both quoted USPTO-safe and Lucene slash-escaped variants, separated from `scientific_literature_queries`.
- [ ] `comparison_scientific_papers` are cleanly separated from `patents` and `patent_families`.
- [ ] `gap_gate.py` returns 0 ungrounded identifiers and `final_audit.py` passes with 0 blocking failures.

## 6. Script CLI Reference & Worked Verification Pattern

Each bundled script under `scripts/` is pure Python 3.10+ stdlib and operates completely offline:

| Script | Purpose | Required Flags / Default Behavior |
| --- | --- | --- |
| `scripts/preflight_probe.py` | Environment & corpus probe | `--input /input --output /app/output --out /app/work/env.json` |
| `scripts/budget_guard.py` | Phase & budget ledger | `--state /app/work/.budget_state.json --init` / `--tick <phase>` |
| `scripts/block_indexer.py` | Section/claim block indexer | `--scan /input --out /app/work/blocks.json` |
| `scripts/patent_retrieval_auditor.py` | End-to-end patent & family auditor | `--input /input --output /app/output --work /app/work` |
| `scripts/gap_gate.py` | Identifier & metric grounding | `--verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json` |
| `scripts/final_audit.py` | Deliverable completeness gate | `--dir /app/output --result-file result.json --require patent_search_report.json --require patent_landscape_report.md` |

### Worked Execution Verification Pattern (Mixed Patent & Scientific Corpus)
When `/input` supplies Google Patents HTML snapshots, USPTO/WO PDF facsimiles, patent list JSON, and peer-reviewed scientific comparison papers:
1. `corpus_loader.py` extracts exact full publication numbers (preserving `B2`/`A1`/`A4`/`U` kind codes from `<dd itemprop="publicationNumber">`), application serials, priority applications, DOCDB family tables, CPC/IPC codes, claim text, and HTML `class="claims ocr"` flags.
2. `patent_retrieval_auditor.py` consolidates multi-format views of the exact same normalized publication number (e.g., HTML + JSON summary) while preserving distinct kind codes (`CN117374351A` vs `CN117374351B`, `WO2010068979A1` vs `WO2010068979A4`) as separate publication documents.
3. Dual-metric family grouping computes DOCDB simple families (exact identical priority set) separately from INPADOC extended families (Union-Find connected components sharing any priority or parent link), proving e.g. a CIP claiming `{P1, P2}` splits from `{P1}` under DOCDB while linking under INPADOC.
4. Any unrelated patents sharing topic words (`fuel cell`, `electrolyte`, `catalyst`) without shared priority/family links remain isolated and are explicitly audited in `keyword_only_distinct_patents_separated`.
5. Peer-reviewed comparison papers route exclusively to `comparison_scientific_papers`, `current_official_legal_status` stays `"UNKNOWN"`, and WO/OCR caveats propagate into `gaps` and `patent_landscape_report.md`.

### Stopping Discipline & Budget Control
- Execute the deterministic pipeline once per case, inspect Claim 1 excerpts and any `WO_MERGED_OCR_BOUNDARY_RETAINED` or `empty_or_scanned` PDF facsimiles directly, and enrich comparative narrative strictly from observed text.
- Stop iterating once dual family metrics, WIPO ST.16 fields, slash-safe CPC queries, legal-status boundaries, and `gap_gate.py` + `final_audit.py` all pass with exit code `0`.

### Required JSON Key & Boundary Verification Table
Verify every deliverable satisfies these deterministic checks before concluding:

| Artifact | Required Top-Level Keys | Hard Assertion |
| --- | --- | --- |
| `result.json` | `case_id`, `status`, `patents`, `family_consolidation`, `dual_track_queries`, `comparison_scientific_papers`, `three_perspective_summary`, `metrics`, `metric_provenance`, `findings`, `gaps`, `artifacts` | `status == "ok"` (or honest `"partial"`), all metrics finite & grounded |
| `patent_search_report.json` | `case_id`, `scope_disclaimer`, `legal_boundary`, `summary`, `stage_breakdown`, `patents`, `family_consolidation`, `dual_track_queries`, `comparison_scientific_papers` | `INPADOC <= DOCDB <= pub_docs`; every patent `current_official_legal_status == "UNKNOWN"` |
| `patent_landscape_report.md` | Dual count summary, WIPO ST.16 table, DOCDB vs INPADOC reconciliation, Claim 1 excerpts, scientific contrast, CPC slash-safe queries, Green/Yellow/Red boundaries | Non-empty UTF-8 Markdown; zero FTO/infringement legal verdicts |
| `/app/work/audit_log.json` | `chain_integrity`, `entries[]` with `previous_entry_hash` | Valid SHA-256 chain via `verify_audit_chain` |

### Jurisdiction & Date Field Integrity Rules
- **Priority Date vs Filing Date**: Never copy `priority_date` into `filing_date` or `grant_date`; preserve each date strictly from its labeled metadata/HTML/PDF field (or `null` when absent).
- **Application Number vs Publication Number vs Grant Number**: A pre-grant publication (`A`, `A1`, `A2`, `A3`, `A4`) carries `publication_number` and `application_number`, with `grant_number: null` unless a separate granted specification (`B/B1/B2/C`) is explicitly documented.
- **Utility Model & Search Report Separation**: Keep utility models (`U/U1/Y`) and search reports (`A3`) tagged with their exact WIPO ST.16 `stage` so substantive examination differences remain visible to technical reviewers.
- **Assignee & Inventor Attribution**: Preserve original assignee and inventor lists from the official facsimile or structured export without conflating inventors with assignees or truncating co-assignees.
- **Multi-Format Consolidation Without Loss**: When both a summary list entry (e.g., `patents_list.json`) and a full facsimile (`US7875394B2.html` / `.pdf`) describe the exact same normalized publication number, merge them into one publication document with complete `source_files[]` provenance while keeping distinct kind codes (`A1` vs `A4`, `A` vs `B`) strictly separate.

### Claim & Technical Feature Verification Rules
- Always quote `independent_claim_1_excerpt` verbatim from the observed source file (`claim_version_source`) and report `observed_claim_count`, `independent_claim_count`, and `dependent_claim_count`.
- Never claim that a granted US patent (`B2`) and a PCT application (`WO...A1`) or Chinese grant (`CN...B`) share identical legal protection scope (`equal_claim_scope_assumed: false`).
- Present scientific comparison papers (`comparison_scientific_papers`) alongside patents in `patent_landscape_report.md` to separate peer-reviewed scientific disclosure from claimed patent monopolies.

## 7. Deliverables, References & Minimal Example

- **Final Outputs (`/app/output/`)**:
  - `result.json` — Harbor contract deliverable with `case_id`, `status`, `patents`, `family_consolidation`, `dual_track_queries`, `comparison_scientific_papers`, `three_perspective_summary`, `metrics`, `metric_provenance`, `findings`, `gaps`, `artifacts`.
  - `patent_search_report.json` — Complete normalized patent records, dual family graphs, claim decompositions, and CPC/IPC query tables.
  - `patent_landscape_report.md` — Human-readable landscape report for engineering and IP review.
- **Audit Trail (`/app/work/`)**: `env.json`, `.budget_state.json`, `blocks.json`, `gaps.json`, `audit_log.json` (SHA-256 hash-chained).
- **Supporting Protocols (`references/`)**:
  - `references/patent_family_and_legal_protocol.md` — WIPO ST.16 table, DOCDB vs INPADOC math definitions, CPC slash escaping, Green/Yellow/Red matrix, non-FTO disclaimer.
  - `references/output_contract_spec.md` — Required JSON schema and count invariants.
  - `references/search_methodology_protocol.md` — Shared evidence-anchored search invariants.
- **Minimal Synthetic Example (`examples/`)**:
  - `examples/sample_input_case.json` and `examples/sample_output_schema_example.json` illustrate the input/output interface only and are explicitly marked synthetic.
