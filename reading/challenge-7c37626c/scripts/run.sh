#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INPUT_DIR="${1:-/input}"
OUTPUT_DIR="${2:-/app/output}"
WORK_DIR="${3:-/app/work}"

mkdir -p "$OUTPUT_DIR" "$WORK_DIR"
python3 -B "$SKILL_ROOT/scripts/preflight_probe.py" --input "$INPUT_DIR" --output "$OUTPUT_DIR" --out "$WORK_DIR/env.json"
python3 -B "$SKILL_ROOT/scripts/budget_guard.py" --state "$WORK_DIR/.budget_state.json" --init
python3 -B "$SKILL_ROOT/scripts/formula_math_engine.py" --input "$INPUT_DIR" --output "$OUTPUT_DIR" --work "$WORK_DIR"
python3 -B "$SKILL_ROOT/scripts/math_markdown_validator.py" "$OUTPUT_DIR/formula_notes.md" --json
python3 -B "$SKILL_ROOT/scripts/gap_gate.py" --verify-json "$OUTPUT_DIR/result.json" --corpus "$INPUT_DIR" --gaps-out "$WORK_DIR/gaps.json"
python3 -B "$SKILL_ROOT/scripts/final_audit.py" --dir "$OUTPUT_DIR" --result-file result.json --require formula_verification_report.json --require formula_notes.md
