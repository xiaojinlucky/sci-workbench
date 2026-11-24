---
name: research-trend-evolution
description: Reproducible temporal bibliometric slicer and frontier analyzer that deduplicates preprint-to-VoR pairs before counting, separates published/online-first/preprint date bases with dual-timeline sensitivity, detects API pagination truncation, normalizes terminology drift via acronym/synonym folding, tracks source-bias-free emerging/merging/splitting themes, selects 4-role stage-balanced representatives, and verifies evidence-backed research gaps.
---

# Research Frontiers & Trend Evolution

Determining where a scientific field stands, where it is moving, and what truly remains unsolved requires auditable temporal accounting — never sorting search results by raw citation count, confusing renamed terminology with an emerging frontier, mixing preprint and journal years, or mistaking retrieval omissions for research gaps.

## Five Core Invariants

1. **Preprint-to-VoR Deduplication & Strict Date-Basis Separation**
   - Always deduplicate multi-source records (OpenAlex, Crossref, EuropePMC JSON/XML, arXiv Atom XML, PDF, BibTeX, CSV) *before* computing yearly counts so a paper appearing as both a preprint and a published journal article is NEVER double-counted.
   - Recognize EuropePMC `PPR*` records, arXiv entries, and bioRxiv/medRxiv DOIs strictly as `preprint` without silent `online_first` promotion.
   - Disclose `date_basis_breakdown`, `date_basis_by_year`, and the side-by-side **VoR-preferred vs. Preprint-first sensitivity timeline** (`preprint_vs_vor_sensitivity_timeline`).
   - Never infer publication year from body citations or Crossref deposit timestamps; exclude missing years into `missing_year_records`.

2. **Denominator Integrity & Small-Corpus Fluctuation Guard**
   - Inspect API pagination metadata (`meta.count` in OpenAlex, `total-results` in Crossref, `hitCount`/`nextCursorMark` in EuropePMC) and disclose `pagination_warnings` whenever the local snapshot is a paginated slice.
   - Emit `small_corpus_warning` whenever `records_total < 15`; separate discrete local fluctuations from sustained multi-year inflections.

3. **Source-Bias-Free Topic Evolution (Emerging / Core / Declining / Merging / Splitting)**
   - Compute cross-period term shares strictly on universal `title + abstract` text (never mixing provider-specific tags like OpenAlex `concepts` across heterogeneous sources).
   - Apply `--aliases-json` plus automatic in-corpus acronym-expansion folding (`alias_merges_applied[]`) so rebranded terms are not mislabeled as brand-new frontiers.
   - Use stratified selection (emerging/rising by `recent_share - early_share`, sustained core, declining/matured), TF-IDF-weighted co-occurrence (`CONVERGING_MERGING`), and parent-to-subbranch divergence (`SPLITTING_SPECIALIZING`).

4. **Domain-Agnostic 4-Role Stage-Balanced Representative Selection**
   Select representatives across four disclosed roles rather than raw citation count alone:
   - `foundational_origin` — earliest substantive dated anchor.
   - `methodological_pioneer_long_tail` — low/moderate-citation work introducing methods or concepts adopted by later-year papers.
   - `phase_transition_bridge` — work bridging early and recent conceptual vocabularies.
   - `recent_frontier_anchor` — recent-stage anchor prioritizing full-text VoR while honestly disclosing preprint status if applicable.

5. **Evidence-Backed Research Gaps & Read-Only Report Audit**
   - Anchor gaps strictly to verified limitation passages (with exact locators, excluding introductory pitch sentences), unresolved opposing-evidence conflicts, and explicit pagination/scope bounds.
   - Run read-only `--verify-report --strict` to check overclaims, cited record IDs, yearly table count consistency, and date-basis/pagination disclosure without overwriting deliverables.

## Execution Workflow

Run commands from this skill's root directory (where `SKILL.md` resides), resolving `scripts/` relative to `SKILL.md`.

### Step 1: Probe Environment & Run Deterministic Trend Evolution Engine
```bash
python3 scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 scripts/trend_evolution_analyzer.py \
  --input /input \
  --output /app/output \
  --work /app/work
```
Pass `--aliases-json <path>` if a domain synonym map is provided in `/input`.
Outputs `/app/output/trend_analysis.json`, `/app/output/trend_evolution_report.md`, and `/app/output/result.json`.

### Step 2: Narrative Synthesis Refinement (Agent Reasoning Pass)
Inspect `/app/output/trend_analysis.json` and refine `/app/output/trend_evolution_report.md` following `references/trend_and_evolution_protocol.md`:
- Explain *why* phase transitions occurred (methodological enabler, dataset release, or experimental bottleneck) grounded in cited record passages.
- Keep both yearly count tables (`VoR-Preferred` and `Preprint-First Sensitivity`) accurate and note any `pagination_warnings`.
- Distinguish genuine paradigm shifts from terminology rebranding listed in `alias_merges_applied`.

### Step 3: Read-Only Report Verification, Grounding & Final Deliverable Audit
```bash
python3 scripts/trend_evolution_analyzer.py \
  --input /input \
  --verify-report /app/output/trend_evolution_report.md \
  --out-json /app/work/trend_verify.json \
  --strict
python3 scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json \
  --require trend_analysis.json \
  --require trend_evolution_report.md
```

## References & Examples
- `references/output_contract_spec.md` — Required output fields and verification commands.
- `references/trend_and_evolution_protocol.md` — Date-basis sensitivity, drift normalization, and representative selection criteria.
- `examples/minimal_example.md` — Minimal illustrative walkthrough.
