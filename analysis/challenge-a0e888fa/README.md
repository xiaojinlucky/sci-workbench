# Scientific Data Cleaning & Quality Audit Skill (`challenge-a0e888fa`)

Offline, pure-stdlib Python 3.12 Skill for auditing and reversibly cleaning scientific tables and published aggregate transcriptions against explicit data dictionaries and per-metric workorders.

## Key Capabilities
- **Leading-Zero ID Protection**: Reads all tables strictly as strings so identifiers such as `00123` and `123` never collide under implicit integer inference.
- **Five-Way Missing/Sentinel vs True Zero**: Separates `below_lod` (`<LOD`), `not_detected` (`ND`), `instrument_sentinel` (`-999`), `entry_missing` (blank/`NA`), and `invalid_unparseable_fault` from true numeric `0`, with hard guards against silent imputation or population inference.
- **Conflict-Safe Deduplication**: Drops exact/consistent duplicates with full line logs while quarantining conflicting primary-key duplicates into `*_quarantine_conflicts.csv` instead of silently overwriting.
- **Per-Metric Reversible Unit & QC Normalization**: Normalizes UTF-8 BOM, Unicode minus, NBSP, thousands commas, and mixed inline units with full `*_raw` retention and physical QC alerts.
- **100% Disk Read-Back Reconciliation**: Independently re-reads output CSVs from disk to verify SHA-256, row conservation, key uniqueness, and value-state counts before generating the single-source Markdown quality report and linear reproduction script.

## Quick Start (Minimal Synthetic Example)

```bash
python3 scripts/dirty_table_fault_scanner.py --input examples/synthetic_dirty_table.csv --workorder examples/synthetic_workorder.json --out /tmp/fault_scan.json
python3 scripts/reversible_table_cleaner.py --input examples/synthetic_dirty_table.csv --workorder examples/synthetic_workorder.json --out-dir /tmp/clean_out --stem demo_clean
python3 scripts/quality_reconciler.py --csv /tmp/clean_out/demo_clean.csv --manifest /tmp/clean_out/demo_clean_manifest.json --quarantine /tmp/clean_out/demo_clean_quarantine_conflicts.csv --out-md /tmp/clean_out/demo_clean_quality.md --out-json /tmp/clean_out/demo_clean_reconciliation.json
```

See `SKILL.md` for the full Harbor `/input` -> `/app/output` contract and `examples/SYNTHETIC_EXAMPLE.md` for expected schematic outputs.
