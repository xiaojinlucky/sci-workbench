# Citation Graph Explorer (`challenge-60a8202d`)

Offline, pure-Python 3.12 standard-library Skill for direction-aware multi-hop citation snowballing, co-citation & bibliographic coupling parallel discovery, cross-form identity resolution with distinct-DOI false-merge protection, and CitationChaser #37 cross-snapshot difference auditing.

## Requirements

- **Runtime**: Python 3.12+ (pure standard library; zero third-party dependencies required; zero prohibited packages).
- **Network**: Fully offline (`--network none` compatible). Operates strictly over supplied local snapshot inputs.

## Package Structure

```text
.
├── SKILL.md                               # Agent instructions, workflow, and quality gates
├── README.md                              # Usage, CLI reference, and self-check guide
├── THIRD_PARTY_NOTICES.md                 # Dependency & methodology attributions
├── references/
│   ├── citation_tracing_protocol.md       # Multi-hop tracing, pruning, roles, dedup & diff rules
│   └── output_contract_spec.md            # Exact schemas for all output artifacts
├── examples/
│   ├── sample_input_case.json             # Minimal illustrative case.json (example only)
│   ├── sample_seed_and_graph.json         # Minimal illustrative seed_and_graph.json (example only)
│   └── sample_output_schema_example.json  # Illustrative result.json structure (example only)
└── scripts/
    ├── citation_graph_tracer.py           # Main direction-aware citation tracing CLI
    ├── preflight_probe.py                 # M1 environment & path probe
    ├── result_guard.py                    # M2 atomic result JSON guard & NaN sanitizer
    ├── budget_guard.py                    # M3 Harbor request/time budget guard
    ├── block_indexer.py                   # M4 local document block indexer
    ├── gap_gate.py                        # M5 identifier grounding & gap ledger gate
    ├── final_audit.py                     # M6 pre-exit deliverable contract audit
    └── search_core/                       # Pure-stdlib scholarly parsing, dedup & graph kernel
```

## Quick Start

```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/citation_graph_tracer.py --input /input --output /app/output --work /app/work --depth 2 --max-branch 60
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require citation_network.json --require edge_provenance_table.csv \
  --require snapshot_diff.json --require report.md
```

## Key Outputs

- `result.json`: Harbor contract deliverable with `results`, `evidence`, `status`, `status_details`, `zero_citation_seeds`, `retracted_alerts`, `unresolved_references`, `snapshot_diff`, `metrics`, `metric_provenance`, `findings`, `gaps`, and `artifacts`.
- `citation_network.json`: Full canonical graph, multi-hop trace, structural roles, directed shortest paths, co-citation & bibliographic coupling pairs, dedup summary, ambiguous candidates, and rollback log.
- `edge_provenance_table.csv`: Auditable directed edge list with source file and JSON pointer locators.
- `snapshot_diff.json`: CitationChaser #37 item-level delta (`nodes_added`, `nodes_vanished`, `nodes_persisted`, `edges_added`, `edges_vanished`, `unresolved_in_current`).
- `report.md`: Structured markdown report suitable for literature review inspection.

## Limitations

- Operates strictly on finite local snapshots; missing edges indicate local snapshot absence, never zero global citations.
- Does not claim live remote coverage or fabricate missing metadata.
