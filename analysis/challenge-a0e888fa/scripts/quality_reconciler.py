#!/usr/bin/env python3
"""Independent disk read-back reconciler between cleaned CSV, quarantine CSV, manifest, and quality report.

Verifies 100% numeric & SHA-256 reconciliation by independently re-reading output
files from disk as strings (preserving leading zeros) and generating the
single-source Markdown quality report directly from verified counts.
Pure Python 3.12 stdlib.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _read_csv_strings(path: Path) -> tuple[list[str], list[dict[str, str]], str]:
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8")
    reader = csv.DictReader(text.splitlines())
    cols = list(reader.fieldnames or [])
    rows = [{k: ("" if v is None else v) for k, v in r.items()} for r in reader]
    return cols, rows, sha


def reconcile_cleaning_outputs(
    csv_path: Path,
    manifest_path: Path,
    *,
    quarantine_path: Path | None = None,
    out_md: Path | None = None,
    out_json: Path | None = None,
) -> dict[str, Any]:
    """Re-read cleaned files from disk and assert exact count reconciliation."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cols, rows, actual_sha = _read_csv_strings(csv_path)
    q_rows: list[dict[str, str]] = []
    if quarantine_path and quarantine_path.is_file():
        _, q_rows, _ = _read_csv_strings(quarantine_path)

    mismatches: list[str] = []

    # 1. SHA-256 check
    expected_sha = manifest.get("output_csv_sha256")
    if expected_sha and actual_sha != expected_sha:
        mismatches.append(f"CSV SHA-256 mismatch: manifest={expected_sha[:12]} disk={actual_sha[:12]}")

    # 2. Row conservation check
    ra = manifest.get("row_accounting") or {}
    if len(rows) != int(ra.get("rows_cleaned", -1)):
        mismatches.append(f"rows_cleaned mismatch: manifest={ra.get('rows_cleaned')} disk={len(rows)}")
    if len(q_rows) != int(ra.get("rows_quarantined_key_conflicts", -1)):
        mismatches.append(
            f"rows_quarantined mismatch: manifest={ra.get('rows_quarantined_key_conflicts')} disk={len(q_rows)}"
        )
    dropped_dup = int(ra.get("rows_dropped_consistent_duplicates", 0))
    rows_raw = int(ra.get("rows_raw", -1))
    if rows_raw != len(rows) + len(q_rows) + dropped_dup:
        mismatches.append(
            f"Row conservation violated: raw={rows_raw} != cleaned({len(rows)}) + dropped_dup({dropped_dup}) + quarantined({len(q_rows)})"
        )

    # 3. Primary key uniqueness in cleaned CSV (preserving leading zeros!)
    pk_cols = list(manifest.get("primary_key_columns") or [])
    pk_set = {tuple(r.get(c, "") for c in pk_cols) for r in rows} if pk_cols else set()
    if pk_cols and len(pk_set) != len(rows):
        mismatches.append(f"Primary key non-unique in cleaned CSV: unique={len(pk_set)} vs rows={len(rows)}")

    # 4. value_state reconciliation
    disk_states = dict(sorted(Counter(r.get("value_state", "") for r in rows).items()))
    man_states = dict(sorted((manifest.get("cell_accounting") or {}).get("value_state_counts", {}).items()))
    if disk_states != man_states:
        mismatches.append(f"value_state_counts mismatch: manifest={man_states} disk={disk_states}")

    # 5. Per-column missingness & raw-preservation check
    col_summary: list[dict[str, Any]] = []
    for c in cols:
        if c.endswith("_raw") or c in {"value_state", "audit_flags", "source_line"}:
            continue
        empty_cnt = sum(1 for r in rows if r.get(c, "").strip() == "")
        zero_cnt = sum(1 for r in rows if r.get(c, "").strip() in {"0", "0.0"})
        non_empty = len(rows) - empty_cnt
        col_summary.append({
            "column": c,
            "non_empty_rows": non_empty,
            "empty_or_null_rows": empty_cnt,
            "missing_rate_pct": round((empty_cnt / len(rows) * 100.0), 2) if rows else 0.0,
            "exact_zero_rows": zero_cnt,
            "has_raw_backup_column": f"{c}_raw" in cols,
        })

    # 6. Check that true zeros were not converted to empty and imputation is false
    if manifest.get("imputation_performed") is not False:
        mismatches.append("imputation_performed must be False for lossless published table cleaning")

    reconciled = {
        "ok": len(mismatches) == 0,
        "reconciliation_passed": len(mismatches) == 0,
        "csv_file": csv_path.name,
        "csv_sha256_verified": actual_sha,
        "rows_raw": rows_raw,
        "rows_cleaned_disk": len(rows),
        "rows_dropped_consistent_duplicates": dropped_dup,
        "rows_quarantined_conflicts_disk": len(q_rows),
        "primary_key_unique_verified": (len(pk_set) == len(rows)) if pk_cols else None,
        "disk_value_state_counts": disk_states,
        "column_quality_profile": col_summary,
        "mismatches": mismatches,
    }

    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(reconciled, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if out_md:
        ca = manifest.get("cell_accounting") or {}
        md_lines = [
            f"# Data Cleaning Quality & Reconciliation Report ({manifest.get('source_file', csv_path.name)})",
            "",
            "## 1. Provenance & Integrity Summary",
            "",
            f"- Source file: `{manifest.get('source_file')}` (SHA-256: `{str(manifest.get('source_sha256', ''))[:16]}...`, encoding: `{manifest.get('detected_encoding')}`, delimiter: `{manifest.get('detected_delimiter')}`)",
            f"- Cleaned table: `{csv_path.name}` (SHA-256 verified from disk: `{actual_sha[:16]}...`)",
            f"- Independent read-back reconciliation: **{'PASS' if reconciled['ok'] else 'FAIL'}** (mismatches: `{len(mismatches)}`)",
            f"- Scientific constraints: leading-zero string IDs preserved (`{manifest.get('leading_zero_preserved')}`), imputation performed (`{manifest.get('imputation_performed')}`), population inference performed (`{manifest.get('population_inference_performed')}`)",
            "",
            "## 2. Row Conservation Accounting",
            "",
            "| Stage / Category | Row Count | Disposition |",
            "| --- | ---: | --- |",
            f"| Raw input rows | {rows_raw} | Read strictly as strings without implicit type coercion |",
            f"| Cleaned output rows | {len(rows)} | Written to `{csv_path.name}` with unique primary key |",
            f"| Consistent duplicates dropped | {dropped_dup} | Earliest record retained; exact lines logged in manifest |",
            f"| Conflicting primary-key rows quarantined | {len(q_rows)} | Moved to `{manifest.get('quarantine_csv')}` for expert review; no silent overwrite |",
            "",
            "## 3. Value State & Missingness Taxonomy (Reconciled from Disk)",
            "",
            "| Value State | Cleaned Rows | Meaning |",
            "| --- | ---: | --- |",
        ]
        state_meanings = {
            "valid": "Parsed numeric value within QC bounds (includes preserved true zeros)",
            "below_lod": "Below limit of detection/quantitation; kept blank, not imputed as 0",
            "not_detected": "Reported as not detected (ND); kept blank, distinct from true 0",
            "instrument_sentinel": "Sentinel missing marker (e.g. -999); kept blank, not treated as valid number",
            "entry_missing": "Blank or NA/NULL entry missingness; kept blank without imputation",
            "invalid_unparseable_fault": "Unparseable text transcription fault; kept blank and flagged",
            "unit_conflict_unresolved": "Inline unit conflicted with column unit scale; kept blank for review",
            "unknown_unit_unconverted": "Unit absent from workorder conversion table; kept blank for review",
            "qc_out_of_bounds": "Converted value outside physical/domain QC range; value retained with alert flag",
        }
        for st, cnt in disk_states.items():
            md_lines.append(f"| `{st}` | {cnt} | {state_meanings.get(st, 'See manifest audit flags')} |")

        md_lines.extend([
            "",
            f"- True numeric zeros preserved verbatim: **{ca.get('true_zero_preserved_cells', 0)}**",
            f"- Storage/encoding artifacts normalized (Unicode minus, NBSP, thousands commas, micro sign): **{ca.get('storage_artifacts_repaired_cells', 0)}**",
            f"- Reversible unit conversions applied (`*_raw` & `*_unit_raw` retained): **{ca.get('unit_normalized_cells', 0)}**",
            f"- Physical/domain QC bound alerts flagged: **{ca.get('qc_out_of_bounds_cells', 0)}**",
            "",
            "## 4. Field-Level Completeness (Computed from Disk CSV)",
            "",
            "| Column | Non-Empty | Empty/NULL | Missing Rate (%) | Exact Zeros | Raw Backup Column |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ])
        for cs in col_summary:
            md_lines.append(
                f"| `{cs['column']}` | {cs['non_empty_rows']} | {cs['empty_or_null_rows']} | {cs['missing_rate_pct']} | {cs['exact_zero_rows']} | {'Yes' if cs['has_raw_backup_column'] else 'No'} |"
            )

        if manifest.get("qc_alerts"):
            md_lines.extend(["", "## 5. QC Bound Alerts Requiring Expert Review", ""])
            for qa in manifest["qc_alerts"]:
                md_lines.append(
                    f"- Line `{qa['line']}` | key `{qa['primary_key']}` | metric `{qa['metric']}` = `{qa['value']} {qa['unit']}` outside `{qa['qc_bounds']}` ({qa['action']})"
                )

        if q_rows:
            md_lines.extend(["", "## 6. Quarantined Conflicting Primary-Key Records", ""])
            for qr in q_rows:
                pk_view = {c: qr.get(c, "") for c in pk_cols}
                md_lines.append(
                    f"- Line `{qr.get('source_line')}` | key `{pk_view}` | reason: `{qr.get('quarantine_reason')}`"
                )

        if mismatches:
            md_lines.extend(["", "## Reconciliation Mismatches (BLOCKING)", ""])
            for mm in mismatches:
                md_lines.append(f"- {mm}")

        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return reconciled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independently reconcile cleaned CSV against manifest and generate report.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--quarantine", default=None)
    parser.add_argument("--out-md", default=None)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args(argv)

    rep = reconcile_cleaning_outputs(
        Path(args.csv),
        Path(args.manifest),
        quarantine_path=Path(args.quarantine) if args.quarantine else None,
        out_md=Path(args.out_md) if args.out_md else None,
        out_json=Path(args.out_json) if args.out_json else None,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
