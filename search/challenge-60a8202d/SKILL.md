---
name: citation-graph-explorer
description: Direction-aware multi-hop backward/forward citation tracing, co-citation and bibliographic coupling parallel discovery, structural role classification, cross-form identity merging with distinct-DOI false-merge guard, zero-citation seed and retraction alerting, and CitationChaser #37 cross-snapshot diff auditing over finite scientific snapshots.
---

# Citation Tracing & Related Literature Discovery (`challenge-60a8202d`)

## 1. Mission & Scientific Scope

Keyword queries systematically miss three classes of essential literature: foundational methods/datasets cited upstream by a seed paper, downstream improvements/replications/critiques that adopt different terminology, and parallel works that share the same methodological lineage without directly citing the seed. This Skill implements a reproducible, offline, direction-aware citation snowballing pipeline over finite organizer-authorized snapshots (Crossref, OpenAlex, EuropePMC, PubMed, arXiv, BibTeX/RIS/CSV, and explicit seed/graph files).

**In Scope**
- Multi-hop direction-aware **backward reference tracing** (`cites`, tracing method and dataset origins) and **forward citation tracing** (`cited_by`, tracing downstream follow-ups, benchmarks, and critiques) from explicit or inferred seed papers.
- Explicit **branch pruning and stopping criteria** to prevent exponential hub explosion and separate genuine methodological relevance from generic high-citation noise.
- **Parallel literature discovery** via **co-citation** (shared citing works) and **bibliographic coupling** (shared backward references) to surface related works that do not directly cite the seed.
- **Structural role classification** of every network node (`seed`, `foundational`, `bridge`, `recent_followup`, `parallel_coupled`, `retracted_caution`, `supporting`) so reviewers receive an interpretable domain map rather than an unlabeled edge list.
- **Shortest directed path explanation** from each seed to reachable targets, with **per-hop direction labels** (`cites` vs. `cited_by`), direct vs. indirect reachability tags, and file/JSON-pointer edge provenance.
- **Cross-form identity resolution** merging preprint, conference, journal Version of Record (VoR), and supplementary variants while enforcing a **hard guard against merging distinct formal DOIs** (`ambiguous_candidates`) and retaining a full **ASReview #328 traceable rollback log** (`duplicate_ignored_with_trace`).
- **CitationChaser #37 cross-snapshot difference auditing**: comparing a prior graph snapshot against the current snapshot to report item-level `nodes_added`, `nodes_vanished`, `edges_added`, `edges_vanished`, and `unresolved_in_current`, strictly refusing to conflate raw node/edge counts with global citation coverage.
- Explicit **zero-citation seed diagnostics** (`zero_citation_seeds`), **retraction/correction alerts** (`retracted_alerts`), and **unresolved external reference ledgers** (`unresolved_references`).

**Out of Scope & Hard Boundaries**
- Does **not** perform live internet API queries, unauthorized web scraping, or global citation-count completeness claims.
- Does **not** fabricate unobserved citation edges for preprints or newly published seeds that have zero citation records in the supplied snapshot.
- Does **not** perform full-text claim verification or ungrounded sentiment inference beyond structured metadata and supplied local documents.

---

## 2. Preflight & Three-Perspective Intake Protocol

Before running graph construction, inspect `/input` (never assume domain or schema from filenames alone):

1. **Environment & Budget Initialization (`M1` + `M3`)**:
   - Locate scripts relative to this `SKILL.md` directory (`scripts/`).
   - Run `preflight_probe.py` to record Python runtime, available packages, and `/input`, `/app/work`, `/app/output` paths.
   - Initialize `budget_guard.py` so multi-step analysis stays safely within Harbor's 64-request / 900-second ceiling.
2. **Evidence-Bound Screening Stance (Screening, Not Cherry-Picking)**:
   - Treat every record across Crossref, OpenAlex, EuropePMC, arXiv, and explicit graph files uniformly; preserve exact record conservation (`raw_input_records == canonical_unique + duplicates_merged_away`).
   - When interpreting expanded nodes in `report.md`, apply three-perspective verification:
     * **Method Developer View** (backward `cites` chain: original algorithm, dataset, or assay provenance).
     * **Field Practitioner View** (forward `cited_by` chain: replications, extensions, parameter boundaries, and failure reports).
     * **Independent Evaluator View** (parallel `co-citation` / `bibliographic coupling` works, benchmarks, and `retracted_caution` flags).

---

## 3. Core Execution Pipeline

Execute the pure-Python stdlib pipeline directly inside the offline sandbox:

```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/citation_graph_tracer.py --input /input --output /app/output --work /app/work --depth 2 --max-branch 60
```

Optional flags for `citation_graph_tracer.py`:
- `--depth <int>`: Maximum hop depth in each direction (default `2`; raise to `3` only on sparse graphs with `< 500` nodes).
- `--max-branch <int>`: Per-hop branch pruning cap per direction (default `60`).
- `--question "<text>"`: Override research question when `case.json` omits it.
- `--no-docs`: Skip local PDF/DOCX extraction when running metadata-only graph audits.

### What `citation_graph_tracer.py` Executes Step-by-Step

1. **Structure-Agnostic Case & Seed Discovery (`search_core.caseio`)**:
   - Scans `/input` for `case.json`, `task.md`, API snapshots (Crossref, OpenAlex including `citing_*.json`, EuropePMC, PubMed, arXiv Atom XML, BibTeX/RIS/CSV), explicit seed/graph files (`seed_and_graph.json`), and prior snapshots (`previous_graph_snapshot.json`).
   - Filters out false-positive key collisions inside OpenAlex `abstract_inverted_index` dictionaries so only genuine graph/seed/snapshot payloads enter graph assembly.
   - Identifies seeds in strict priority order:
     1. Explicit `seeds` / `seed_papers` / `seed_ids` in graph files (`seed_and_graph.json`) or `case.json`.
     2. Explicit DOIs / arXiv IDs / OpenAlex IDs declared in `case.json`.
     3. **Fallback (`seed_selection_mode: "auto_inferred"`)**: If no explicit seed is supplied, selects the top in-degree (or highest query-relevance) canonical node and explicitly flags `seed_selection_mode = "auto_inferred"` in both `result.json` and `report.md`.
2. **Cross-Form Identity Resolution & False-Merge Guard (`search_core.dedup`)**:
   - Calls `cluster_records_detailed()` to unify preprint (`10.48550/arxiv.*`, `10.1101/*`, etc.), conference, and journal VoR records via four audited tiers (`T1:doi/pmid/arxiv`, `T2:alt_doi/preprint_to_vor`, `T3:title+year/author`, `T4:title+author+year`).
   - **Hard False-Merge Guard**: When two records share high title similarity (`>= 0.80`) yet carry **two distinct formal (non-preprint) DOIs** (e.g., main paper vs. supplementary file `.s001` or homonymous papers), merging is **blocked** and routed to `ambiguous_candidates` (`action: "kept_separate_needs_human_arbitration"`).
   - **No Silent Deletion (ASReview #328)**: Every merged duplicate is preserved in `rollback_log` with `duplicate_status: "duplicate_ignored_with_trace"`, canonical target, match tier, score, and rollback instruction.
   - Conflict resolution follows `version_rank > source_authority > field_richness` while retaining all raw conflicting values in `conflicts`.
3. **Direction-Aware Graph Assembly & Canonical Endpoint Reconciliation (`search_core.graph`)**:
   - Builds the directed citation graph (`src --[cites]--> dst`) from Crossref `reference[]`, OpenAlex `referenced_works[]`, and explicit graph `edges[]`.
   - Reconciles explicit edge endpoints (e.g., raw OpenAlex IDs like `W3110901318`, DOIs, arXiv IDs, or PMIDs) onto their canonical deduplicated node `rid` so cross-file edges (including retracted citing works in `seed_and_graph.json`) attach accurately to the canonical seed node rather than dangling.
   - Ensures every declared seed is represented in the node table even if it has zero edges (`SEED_ZERO_CIT`).
   - Preserves multi-source `provenance_list` on every deduplicated edge (`file`, `pointer`, `source`, `resolution`, `raw`).
   - Captures all unresolvable external references in `unresolved_references` with exact source locators rather than dropping them silently.
4. **Multi-Hop Tracing, Pruning, Parallel Discovery & Path Explanation**:
   - Runs `graph.trace()` up to `--depth` hops backward (`out_edges`, `cites`) and forward (`in_edges`, `cited_by`).
   - **Stopping & Pruning Criteria**:
     * Stops automatically when a hop produces an empty frontier (`0` new nodes) or reaches `depth`.
     * When a hop exceeds `max_branch_per_hop`, ranks candidates deterministically by `(in_degree DESC, year DESC, rid ASC)`, keeps the top `max_branch_per_hop`, and records exact `pruned_count` in `pruning_log`.
   - **Parallel Work Discovery**: Computes **bibliographic coupling** (shared backward references with each seed) and **co-citation** (shared forward citers with each seed) across the entire canonical graph to discover `parallel_works_by_coupling_or_cocitation` that do not directly link to the seed.
   - **Structural Role Classification**: Assigns every node in the traced universe to one explicit structural role:
     * `retracted_caution`: Any node flagged `is_retracted` (highest precedence so retracted works are never mistaken for trusted evidence).
     * `seed`: Declared or inferred starting paper(s).
     * `foundational`: High in-degree (`>= max(2, |U| // 3)`) and older/classic publication year (`<= 2018` or undated classic).
     * `bridge`: Connects both upstream and downstream literature (`in_degree > 0` and `out_degree > 0`).
     * `parallel_coupled`: Discovered via co-citation or bibliographic coupling without direct seed citation.
     * `recent_followup`: Recent downstream work (`year >= 2023`).
     * `supporting`: Other connected contextual literature.
   - **Per-Hop Direction-Annotated Shortest Paths**: Computes bidirectional BFS shortest paths from each seed to reachable nodes, labeling every single hop as `cites` (backward) or `cited_by` (forward), tagging `reachability: "direct"` (`1` hop) vs. `"indirect"` (`>= 2` hops), and attaching edge provenance per hop.
5. **CitationChaser #37 Cross-Snapshot Diff Audit**:
   - Detects any prior graph snapshot (e.g., `previous_graph_snapshot.json`), aligns its node IDs onto the same canonical ID space as the current graph (preventing false vanishings caused solely by raw-ID vs. canonical-RID formatting), and runs `diff_graph_snapshots()`.
   - Outputs `snapshot_diff.json` with explicit `nodes_added`, `nodes_vanished`, `nodes_persisted`, `edges_added`, `edges_vanished`, `unresolved_in_current`, enriched node/edge detail records, and a mandatory coverage warning: snapshot counts reflect finite local inputs, **not** global citation coverage.

---

## 4. Verification & Post-Run Audit (`M5` + `M6`)

After running `citation_graph_tracer.py`, verify grounding and deliverable integrity:

```bash
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require citation_network.json \
  --require edge_provenance_table.csv \
  --require snapshot_diff.json \
  --require report.md
```

**Host Agent Review Checklist (Before Final Sign-Off)**
1. **Direction Fidelity**: Confirm every hop in `paths` distinguishes `cites` (backward reference) from `cited_by` (forward citation), and never swaps direction.
2. **Noise vs. Relevance Check**: Inspect top `foundational` / `bridge` nodes and `coupling` / `cocitation` pairs; note when high in-degree reflects a generic software/toolkit paper versus method-specific lineage.
3. **Zero-Citation Seed Honesty**: Confirm every seed with `in_degree == 0` and `out_degree == 0` is explicitly listed in `zero_citation_seeds` with `no_citation_edges_in_local_snapshot`—never fabricate links or equate missing snapshot edges with zero real-world citations.
4. **Retraction Propagation Warning**: Verify every retracted node in `retracted_alerts` (especially those directly citing or cited by a seed) is prominently highlighted in `report.md` and `result.json` so downstream synthesis excludes compromised claims.
5. **Ambiguous Identity Review**: Inspect `ambiguous_candidates` (e.g., distinct formal DOIs sharing near-identical titles or supplementary-material DOIs `.s001/.s002`) and confirm none were silently merged.
6. **CitationChaser #37 Audit**: Confirm `snapshot_diff.json` separates `added`, `vanished`, and `unresolved_in_current` explicitly.

---

## 5. Deliverable Contract & Output Files

All runtime deliverables are written atomically under `/app/output/`:

| File | Format | Purpose |
| --- | --- | --- |
| `result.json` | JSON | Primary Harbor contract output (`case_id`, `status`, `results`, `evidence`, `status_details`, `zero_citation_seeds`, `retracted_alerts`, `unresolved_references`, `snapshot_diff`, `metrics`, `metric_provenance`, `findings`, `gaps`, `artifacts`) |
| `citation_network.json` | JSON | Complete canonical graph, role assignments, multi-hop trace, directed shortest paths, co-citation/coupling tables, dedup & rollback logs |
| `edge_provenance_table.csv` | CSV | Flat reviewable edge table (`src_rid`, `src_title`, `src_doi`, `src_retracted`, `dst_rid`, `dst_title`, `dst_doi`, `relation`, `source`, `resolution`, `file`, `pointer`, `raw_evidence`) |
| `snapshot_diff.json` | JSON | CitationChaser #37 cross-snapshot delta (`nodes_added`, `nodes_vanished`, `nodes_persisted`, `edges_added`, `edges_vanished`, `unresolved_in_current`) |
| `report.md` | Markdown | Structured human-readable citation tracing report ready for literature review inspection |
| `audit_log.json` | JSON | SHA-256 hash-chained step-by-step execution audit log |
| `run_manifest.json` | JSON | Input file inventory, SHA-256 checksums, per-file record counts, and load diagnostics |

---

## 6. Exception & Edge-Case Handling Matrix

| Condition | Detection Signal | Required Action |
| --- | --- | --- |
| Seed has zero local edges (e.g., brand-new preprint) | `in_degree == 0 and out_degree == 0` | Record in `zero_citation_seeds`; warn that snapshot absence is not zero global impact |
| Retracted or corrected citing/cited work | `is_retracted == True` or `is_correction == True` | Assign role `retracted_caution`; emit high-priority entry in `retracted_alerts` |
| Homonymous titles with distinct formal DOIs | `same_work()` -> `ambiguous:distinct_formal_dois` | Block merge; retain both nodes; log in `ambiguous_candidates` for human arbitration |
| Missing explicit seed in case input | No `seeds` in `seed_and_graph.json` or `case.json` | Select highest in-degree/relevance node; mark `seed_selection_mode: "auto_inferred"` |
| Missing prior graph snapshot | No `previous_*snapshot*.json` in `/input` | Write `snapshot_diff.json` with `previous_snapshot_available: false`; do not fabricate baseline |
| Unresolvable external reference IDs | Target DOI/OpenAlex ID absent from local snapshot | Log in `unresolved_references` with file pointer; include count in `snapshot_diff.json` |
| Source pagination/API error snapshot | `error` or `status in (error, rate_limited, timeout)` | Record in `status_details.pagination_issues` and `gaps`; never extrapolate missed pages |

---

## 7. Quick-Start Decision Rules for Host Agents

When the Host Agent invokes this Skill inside a Harbor evaluation harness, follow this 4-step verification loop:

1. **Always inspect `/input/case.json` and `/input/task.md` first**:
   - Confirm whether explicit `seeds`, `seed_and_graph.json`, or `previous_graph_snapshot.json` files exist, and read exact `output_contract` requirements.
   - Never assume field names or biological/computational domain from directory labels; read the actual JSON/XML files.
2. **Execute `citation_graph_tracer.py` early to establish a complete baseline**:
   - Even if downstream qualitative synthesis takes multiple reasoning turns, running the script in turn 1–2 immediately produces atomic, grounded `result.json`, `citation_network.json`, `edge_provenance_table.csv`, `snapshot_diff.json`, and `report.md`.
   - If the graph is very sparse at `--depth 2`, re-run with `--depth 3` and inspect whether new indirect paths or parallel works emerge.
3. **Separate Methodological Lineage from Generic High-Citation Noise**:
   - Review `trace.roles`, `trace.coupling`, and `trace.cocitation` in `citation_network.json`.
   - When presenting key expanded works to the user or in `report.md`, always attach three anchors: (a) the exact directed shortest path (`chain_summary`), (b) the source file/pointer from `edge_provenance_table.csv`, and (c) the structural role (`foundational`, `bridge`, `recent_followup`, `parallel_coupled`, or `retracted_caution`).
4. **Never Overclaim Coverage or Silently Suppress Anomalies**:
   - Quote `snapshot_diff.json` deltas (`nodes_persisted`, `nodes_added`, `nodes_vanished`, `edges_vanished`, `unresolved_in_current`) verbatim.
   - Explicitly surface `zero_citation_seeds`, `retracted_alerts`, and `ambiguous_candidates` in the final narrative so downstream literature reviews can arbitrate them cleanly.

---

## 8. Illustrative End-to-End Trace Walkthrough (Example Only)

To verify interface behavior before inspecting case outputs, consider a minimal synthetic graph with one connected seed (`SEED_01`), one brand-new preprint seed (`SEED_ZERO_02`), one upstream method (`FOUND_01`), one downstream benchmark (`FOLLOW_01`), one retracted citer (`RET_01`), and one parallel coupled study (`PARALLEL_01` citing `FOUND_01` without citing `SEED_01`):

- **Backward Hop 1 (`cites`)**: `SEED_01 --[cites]--> FOUND_01` (`role: foundational`).
- **Forward Hop 1 (`cited_by`)**: `SEED_01 <--[cited_by]-- FOLLOW_01` (`role: recent_followup`) and `SEED_01 <--[cited_by]-- RET_01` (`role: retracted_caution`, triggering `retracted_alerts`).
- **Bibliographic Coupling (Parallel Discovery)**: `PARALLEL_01` shares backward reference `FOUND_01` with `SEED_01` -> surfaced under `parallel_works` with `role: parallel_coupled` and `shared_references: 1`.
- **Zero-Citation Seed Diagnostic**: `SEED_ZERO_02` has `in_degree = 0, out_degree = 0` -> retained explicitly in `zero_citation_seeds` with `no_citation_edges_in_local_snapshot`.
- **Cross-Snapshot Audit**: If a prior snapshot contained `VANISHED_01 --[cites]--> SEED_01` absent from the current snapshot, `snapshot_diff.json` records `nodes_vanished: ["VANISHED_01"]` and `edges_vanished` verbatim without conflating hit count with global coverage.

See `references/citation_tracing_protocol.md` for full algorithmic rules, `references/output_contract_spec.md` for exact field schemas, and `examples/` for runnable sample inputs.
