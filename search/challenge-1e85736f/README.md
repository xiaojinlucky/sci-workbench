# Incremental Literature Tracker (`challenge-1e85736f`)

Offline, pure-Python 3.12 standard-library Skill for closed-loop incremental literature monitoring across fixed UTC watermark windows: 5-category triage (`genuinely_new`, `version_upgrade`, `retraction_or_correction`, `delayed_indexed_archival`, `duplicate_suppressed`), preprint-to-VoR version chains, date/timezone & PyAlex #103 pagination anomaly auditing, and SHA-256 idempotent rerun suppression.

## Requirements

- **Runtime**: Python 3.12+ (pure standard library; zero mandatory third-party packages; zero prohibited libraries).
- **Network**: Fully offline (`--network none` compatible). Operates strictly over supplied local snapshot inputs.

## Package Structure

```text
.
├── SKILL.md                               # Agent instructions, workflow, and triage/idempotency rules
├── README.md                              # Usage, CLI reference, and self-check guide
├── THIRD_PARTY_NOTICES.md                 # Dependency & methodology attributions
├── references/
│   ├── incremental_tracking_protocol.md   # UTC window, 5-way triage, dedup & idempotency spec
│   └── output_contract_spec.md            # Exact schemas for all output artifacts
├── examples/
│   ├── sample_input_case.json             # Minimal illustrative case.json (example only)
│   ├── sample_snapshots_example.json      # Minimal illustrative prior/current snapshots (example only)
│   └── sample_output_schema_example.json  # Illustrative result.json structure (example only)
└── scripts/
    ├── incremental_tracker.py             # Main incremental tracking & idempotency CLI
    ├── preflight_probe.py                 # M1 environment & path probe
    ├── result_guard.py                    # M2 atomic result JSON guard & NaN sanitizer
    ├── budget_guard.py                    # M3 Harbor request/time budget guard
    ├── block_indexer.py                   # M4 local document block indexer
    ├── gap_gate.py                        # M5 identifier grounding & gap ledger gate
    ├── final_audit.py                     # M6 pre-exit deliverable contract audit
    └── search_core/                       # Pure-stdlib scholarly parsing, dedup & contract kernel
```

## Quick Start

```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/incremental_tracker.py --input /input --output /app/output --work /app/work --state-file /app/work/alert_ledger.json
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require incremental_delta.csv --require alert_digest.json \
  --require watermark_state.json --require report.md
```

## Verifying Idempotent Reruns

Running `incremental_tracker.py` a second time against the same window and `--state-file` confirms zero secondary push notifications:
- Run 1: `run_sequence_for_window: 1`, `idempotent_rerun: false`, `new_alerts_emitted > 0`, `suppressed_already_notified: 0`.
- Run 2: `run_sequence_for_window: 2`, `idempotent_rerun: true`, `new_alerts_emitted: 0`, `suppressed_already_notified == Run 1 emitted`.

## Key Outputs

- `result.json`: Harbor contract deliverable with 5-category triage results, idempotency report, metrics, provenance, and honest gaps.
- `incremental_delta.csv`: Per-record triage table with `alert_action`, `idempotency_key`, `dedup_key`, `matched_prior_id`, and rationale.
- `alert_digest.json`: Complete alert payload, triage buckets, noise suppression counts, date anomalies, and supplementary API audit.
- `watermark_state.json`: Watermark checkpoint and SHA-256 alert ledger keys for reproducible replay.
- `report.md`: Structured markdown digest for researcher inspection.
