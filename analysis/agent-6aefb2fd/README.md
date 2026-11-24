# Multi-Step Scientific Data Analysis Agent (`agent-6aefb2fd`)

Checkpointed, dual-backend (pure Python 3.12 stdlib + Scientific Python enhancement) Skill for end-to-end scientific data analysis across CSV, JSON, and read-only SQLite inputs.

## Key Capabilities
- **5-Stage Resumable Orchestration**: `S1_intake -> S2_clean -> S3_stats -> S4_viz -> S5_report` with per-stage disk SHA-256 checkpoints and verified `--resume` recovery after interruption.
- **Zero-Assumption Read-Only Intake**: Preserves leading-zero IDs, flags coded categorical integers (blocking accidental averaging of category codes), and opens SQLite strictly via `mode=ro`.
- **Rule-Logged Non-Silent Cleaning**: Emits full row-conservation ledgers with zero silent imputation.
- **Assumption-Verified Statistics**: Routes cleanly between ANOVA/Student, Welch ANOVA/Welch t (Satterthwaite df), and Kruskal-Wallis/Mann-Whitney U + Hodges-Lehmann shift + Cliff's delta + Hedges' g + Holm-Bonferroni correction.
- **Cell-Styled Native Charts & Single-Source Backfill**: Exports editable vector SVG natively on stdlib (plus 300 dpi PNG/PDF when Matplotlib is installed) and reconciles report numbers directly against disk CSV and JSON artifacts.

## Quick Start

```bash
python3 scripts/dataset_intake_profiler.py --input examples/synthetic_cohort.csv --out /tmp/intake.json
python3 scripts/multistep_pipeline_engine.py --input examples/synthetic_cohort.csv --group-col group --value-col metric_val --unit "mg/L" --case-id demo-01 --out-dir /tmp/pipeline_out --stem demo_report
```

See `SKILL.md` for full Harbor `/input` -> `/app/output` usage and `--resume` recovery semantics.
