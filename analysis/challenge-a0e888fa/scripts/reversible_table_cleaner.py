#!/usr/bin/env python3
"""Reversible, workorder-bound scientific table cleaner with zero silent imputation.

Enforces leading-zero ID preservation, separates five missing/sentinel classes
from true numeric zeros, quarantines conflicting primary-key duplicates instead
of silently overwriting, supports per-metric unit conversions and physical QC
bounds, and emits a standalone linear imperative reproduction script.
Pure Python 3.12 stdlib (zero external dependencies).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dirty_table_fault_scanner import (
    _build_sentinel_lookup,
    _lookup_metric_rule,
    _resolve_numeric_targets,
    _resolve_pk_cols,
    _resolve_pk_cols_with_audit,
    _unit_key,
    detect_encoding_and_read,
    normalize_storage_text,
    parse_numeric_with_unit,
    scan_dirty_table,
)


def _infer_si_unit_factor(from_u: str, to_u: str) -> float | None:
    """Deterministic SI prefix conversion for standard mass/volume/concentration units when not in workorder."""
    mass = {"kg": 1e3, "g": 1.0, "mg": 1e-3, "ug": 1e-6, "μg": 1e-6, "µg": 1e-6, "ng": 1e-9, "pg": 1e-12}
    vol = {"l": 1.0, "dl": 1e-1, "ml": 1e-3, "ul": 1e-6, "μl": 1e-6, "µl": 1e-6}
    length = {"m": 1.0, "cm": 1e-2, "mm": 1e-3, "um": 1e-6, "μm": 1e-6, "nm": 1e-9}
    fu, tu = from_u.strip().lower(), to_u.strip().lower()
    if fu == tu:
        return 1.0
    for tbl in (mass, vol, length):
        if fu in tbl and tu in tbl:
            return tbl[fu] / tbl[tu]
    if "/" in fu and "/" in tu:
        f_num, f_den = [x.strip() for x in fu.split("/", 1)]
        t_num, t_den = [x.strip() for x in tu.split("/", 1)]
        if f_num in mass and t_num in mass and f_den in vol and t_den in vol:
            return (mass[f_num] / mass[t_num]) / (vol[f_den] / vol[t_den])
    return None


def _format_num(val: float | Decimal, decimal_str: str | None = None, factor: float = 1.0) -> str:
    """Lossless Decimal-preserving formatter: never rounds small non-zero values (e.g. 1e-9) to '0'."""
    try:
        if decimal_str is not None:
            d = Decimal(decimal_str)
            if abs(factor - 1.0) > 1e-15:
                d = d * Decimal(str(factor))
        elif isinstance(val, Decimal):
            d = val
        else:
            d = Decimal(str(val))
    except InvalidOperation:
        d = Decimal(str(val))
    if d == 0:
        return "0"
    norm = d.normalize()
    # Keep scientific notation for extreme magnitudes (<1e-4 or >=1e12), else exact stripped decimal
    exp = norm.as_tuple().exponent
    abs_d = abs(norm)
    if abs_d < Decimal("1e-4") or abs_d >= Decimal("1e12"):
        return format(norm, "g")
    if isinstance(exp, int) and exp >= 0:
        return str(int(norm))
    s = format(norm, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def clean_table_by_workorder(
    input_path: Path,
    workorder: dict[str, Any],
    out_dir: Path,
    *,
    stem: str = "cleaned_table",
) -> dict[str, Any]:
    """Clean raw table strictly under workorder contract with full reversibility."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Hard scientific integrity guards (especially for published aggregate transcriptions)
    if workorder.get("impute_missing") is True:
        raise ValueError("Policy violation: imputing missing or invalid published source values is forbidden.")
    if workorder.get("infer_population_statistics") is True:
        raise ValueError("Policy violation: inferring unrecorded population statistics from aggregate tables is forbidden.")

    scan = scan_dirty_table(input_path, workorder=workorder)
    raw_info = detect_encoding_and_read(input_path)
    cols = raw_info["columns"]
    rows = raw_info["rows"]

    sentinel_lookup = _build_sentinel_lookup(workorder)
    pk_cols, pk_audit = _resolve_pk_cols_with_audit(cols, workorder)
    pk_missing = pk_audit.get("status") == "MISSING_DECLARED_PK"
    num_cols, metric_col = _resolve_numeric_targets(cols, pk_cols, workorder)
    unit_col = str(workorder.get("unit_column") or workorder.get("unit_col") or "").strip() or None
    dup_policy = workorder.get("duplicate_policy") or {}
    conflict_mode = str(dup_policy.get("key_conflict", "quarantine_do_not_silently_overwrite"))

    # Group rows by leading-zero-preserving primary key; refuse silent PK shrinkage when declared PK columns are missing
    pk_occurrences: dict[tuple[str, ...], list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for ridx, row in enumerate(rows, start=2):
        pk_sig = (f"__row_{ridx}__",) if pk_missing else tuple(row.get(c, "").strip() for c in pk_cols)
        pk_occurrences[pk_sig].append((ridx, row))

    conflicting_pks: set[tuple[str, ...]] = set()
    for pk_sig, items in pk_occurrences.items():
        if len(items) > 1:
            base_payload = {c: items[0][1].get(c, "").strip() for c in cols if c not in pk_cols}
            for _, r in items[1:]:
                cur_payload = {c: r.get(c, "").strip() for c in cols if c not in pk_cols}
                if cur_payload != base_payload:
                    conflicting_pks.add(pk_sig)
                    break

    cleaned_rows: list[dict[str, str]] = []
    quarantine_rows: list[dict[str, str]] = []
    duplicate_audit: list[dict[str, Any]] = []
    unit_conversion_log: list[dict[str, Any]] = []
    qc_alerts: list[dict[str, Any]] = []
    precision_preservation_log: list[dict[str, Any]] = []

    seen_nonconflict_pks: dict[tuple[str, ...], int] = {}
    status_counts: dict[str, int] = defaultdict(int)
    missing_breakdown: dict[str, int] = defaultdict(int)
    true_zero_preserved = 0
    storage_repaired_cells = 0

    for ridx, row in enumerate(rows, start=2):
        pk_sig = (f"__row_{ridx}__",) if pk_missing else tuple(row.get(c, "").strip() for c in pk_cols)

        # Handle conflicting duplicate primary keys: quarantine ALL rows sharing that conflicted key
        if pk_sig in conflicting_pks and conflict_mode == "quarantine_do_not_silently_overwrite":
            qrow = {c: row.get(c, "") for c in cols}
            qrow["source_line"] = str(ridx)
            qrow["quarantine_reason"] = "conflicting_primary_key_duplicate"
            quarantine_rows.append(qrow)
            duplicate_audit.append({
                "primary_key": dict(zip(pk_cols, pk_sig)),
                "line": ridx,
                "action": "quarantined_conflict",
                "reason": "Same primary key carried conflicting non-key values; refused silent overwrite",
            })
            continue

        # Handle exact or whitespace-only consistent duplicates: keep first with audit log
        if pk_sig in seen_nonconflict_pks:
            duplicate_audit.append({
                "primary_key": dict(zip(pk_cols, pk_sig)),
                "kept_line": seen_nonconflict_pks[pk_sig],
                "dropped_line": ridx,
                "action": "dropped_consistent_duplicate",
                "reason": "Identical normalized key and identical non-key payload; kept earliest record",
            })
            continue
        seen_nonconflict_pks[pk_sig] = ridx

        out_r: dict[str, str] = {}
        for c in cols:
            val_str = row.get(c, "")
            # Preserve exact leading zeros in PK/ID columns; only trim outer whitespace
            out_r[c] = val_str.strip() if c in pk_cols else val_str.strip()

        metric_name = out_r.get(metric_col, "") if metric_col else None
        row_unit_raw = out_r.get(unit_col, "") if unit_col else ""
        if unit_col:
            out_r[f"{unit_col}_raw"] = row_unit_raw

        row_flags: list[str] = []
        row_value_state = "valid"

        for nc in num_cols:
            raw_cell = row.get(nc, "")
            out_r[f"{nc}_raw"] = raw_cell
            norm_v, pre_flags = normalize_storage_text(raw_cell)
            low = norm_v.lower()

            if low in sentinel_lookup:
                cat = sentinel_lookup[low]
                out_r[nc] = ""
                row_value_state = cat
                row_flags.append(f"{nc}:{cat}")
                missing_breakdown[cat] += 1
                continue

            parsed = parse_numeric_with_unit(raw_cell)
            if not parsed["parsed"]:
                out_r[nc] = ""
                row_value_state = "invalid_unparseable_fault"
                row_flags.append(f"{nc}:invalid_unparseable_fault")
                missing_breakdown["invalid_unparseable_fault"] += 1
                continue

            val = float(parsed["value"])
            if parsed["storage_flags"]:
                storage_repaired_cells += 1
                row_flags.extend(f"{nc}:{fl}" for fl in parsed["storage_flags"])

            rule = _lookup_metric_rule(workorder, metric_name, nc)
            target_u = _unit_key(str(rule.get("target_unit") or ""))
            factors = {_unit_key(k): float(v) for k, v in (rule.get("unit_factors_to_target") or {}).items()}
            inline_u = parsed["inline_unit"]
            col_u = _unit_key(row_unit_raw) if row_unit_raw else ""

            # Check unit mismatch between inline cell unit and unit column
            if inline_u and col_u and inline_u != col_u:
                f_in = factors.get(inline_u)
                f_col = factors.get(col_u)
                if f_in is None or f_col is None or abs(f_in - f_col) > 1e-12:
                    # Do not silently guess when scales disagree
                    out_r[nc] = ""
                    row_value_state = "unit_conflict_unresolved"
                    row_flags.append(f"{nc}:unit_conflict({inline_u}_vs_{col_u})")
                    missing_breakdown["unit_conflict_unresolved"] += 1
                    continue

            eff_u = inline_u or col_u or target_u
            factor_used = 1.0
            if eff_u and target_u:
                si_factor = None if eff_u in factors else _infer_si_unit_factor(eff_u, target_u)
                if eff_u in factors or si_factor is not None:
                    factor_used = factors[eff_u] if eff_u in factors else float(si_factor)  # type: ignore[arg-type]
                    source_tag = "workorder" if eff_u in factors else "si_prefix_builtin"
                    converted = val * factor_used
                    if eff_u != target_u or abs(factor_used - 1.0) > 1e-12 or inline_u:
                        unit_conversion_log.append({
                            "line": ridx,
                            "primary_key": dict(zip(pk_cols, pk_sig)),
                            "metric": metric_name or nc,
                            "raw_value": raw_cell,
                            "from_unit": eff_u,
                            "to_unit": target_u,
                            "factor": factor_used,
                            "factor_source": source_tag,
                            "converted_value": round(converted, 8),
                        })
                    val = converted
                    if unit_col:
                        out_r[unit_col] = target_u
                elif eff_u != target_u:
                    out_r[nc] = ""
                    row_value_state = "unknown_unit_unconverted"
                    row_flags.append(f"{nc}:unknown_unit({eff_u})")
                    missing_breakdown["unknown_unit_unconverted"] += 1
                    continue

            if val == 0.0:
                true_zero_preserved += 1
                row_flags.append(f"{nc}:true_zero_preserved")

            qc = rule.get("qc_bounds") or {}
            qmin, qmax = qc.get("min"), qc.get("max")
            if (qmin is not None and val < float(qmin)) or (qmax is not None and val > float(qmax)):
                row_value_state = "qc_out_of_bounds"
                row_flags.append(f"{nc}:qc_out_of_bounds")
                qc_alerts.append({
                    "line": ridx,
                    "primary_key": dict(zip(pk_cols, pk_sig)),
                    "metric": metric_name or nc,
                    "value": round(val, 8),
                    "unit": target_u or eff_u,
                    "qc_bounds": qc,
                    "action": "retained_with_flag_for_expert_review",
                })

            formatted = _format_num(val, decimal_str=parsed.get("decimal_str"), factor=factor_used)
            if val != 0.0 and abs(val) < 1e-4:
                precision_preservation_log.append({
                    "line": ridx,
                    "column": nc,
                    "raw_value": raw_cell,
                    "preserved_value": formatted,
                    "reason": "sub_1e-4_nonzero_decimal_preserved_without_zeroing",
                })
                row_flags.append(f"{nc}:small_nonzero_decimal_preserved")
            out_r[nc] = formatted

        out_r["value_state"] = row_value_state
        out_r["audit_flags"] = ";".join(row_flags) if row_flags else "none"
        out_r["source_line"] = str(ridx)
        status_counts[row_value_state] += 1
        cleaned_rows.append(out_r)

    extra_cols = [f"{nc}_raw" for nc in num_cols]
    if unit_col:
        extra_cols.append(f"{unit_col}_raw")
    extra_cols.extend(["value_state", "audit_flags", "source_line"])
    out_cols = list(dict.fromkeys(cols + extra_cols))

    csv_p = out_dir / f"{stem}.csv"
    with csv_p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=out_cols)
        w.writeheader()
        w.writerows(cleaned_rows)
    csv_sha = hashlib.sha256(csv_p.read_bytes()).hexdigest()

    quarantine_p = out_dir / f"{stem}_quarantine_conflicts.csv"
    q_cols = list(dict.fromkeys(cols + ["source_line", "quarantine_reason"]))
    with quarantine_p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=q_cols)
        w.writeheader()
        w.writerows(quarantine_rows)

    manifest = {
        "ok": True,
        "schema_version": "2.0",
        "source_file": input_path.name,
        "source_sha256": scan["sha256"],
        "output_csv": csv_p.name,
        "output_csv_sha256": csv_sha,
        "quarantine_csv": quarantine_p.name,
        "detected_encoding": scan["detected_encoding"],
        "detected_delimiter": scan["detected_delimiter"],
        "embedded_newline_cells": raw_info.get("embedded_newline_cells", 0),
        "primary_key_columns": pk_cols,
        "primary_key_audit": pk_audit,
        "precision_preservation_log": precision_preservation_log,
        "leading_zero_preserved": True,
        "imputation_performed": False,
        "population_inference_performed": False,
        "row_accounting": {
            "rows_raw": len(rows),
            "rows_cleaned": len(cleaned_rows),
            "rows_dropped_consistent_duplicates": sum(1 for d in duplicate_audit if d["action"] == "dropped_consistent_duplicate"),
            "rows_quarantined_key_conflicts": len(quarantine_rows),
            "conservation_check_passed": (
                len(rows)
                == len(cleaned_rows)
                + sum(1 for d in duplicate_audit if d["action"] == "dropped_consistent_duplicate")
                + len(quarantine_rows)
            ),
        },
        "cell_accounting": {
            "value_state_counts": dict(sorted(status_counts.items())),
            "missing_and_fault_preserved_null_breakdown": dict(sorted(missing_breakdown.items())),
            "total_null_preserved_cells": sum(missing_breakdown.values()),
            "true_zero_preserved_cells": true_zero_preserved,
            "storage_artifacts_repaired_cells": storage_repaired_cells,
            "unit_normalized_cells": len(unit_conversion_log),
            "qc_out_of_bounds_cells": len(qc_alerts),
        },
        "duplicate_audit": duplicate_audit,
        "unit_conversion_log": unit_conversion_log,
        "qc_alerts": qc_alerts,
        "leading_zero_protection": scan["leading_zero_protection"],
    }

    man_p = out_dir / f"{stem}_manifest.json"
    man_p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Emit standalone linear imperative reproduction script (bio-code style)
    repro_p = out_dir / f"reproduce_{stem}.py"
    repro_code = (
        "#!/usr/bin/env python3\n"
        f'"""Linear reproduction script for cleaning {input_path.name} under explicit workorder."""\n\n'
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n\n"
        f'RAW_TABLE = Path("{input_path.name}")  # adjust relative to your case directory\n'
        'WORKORDER = Path("workorder.json")\n'
        f'OUT_DIR = Path(".")\n'
        'SCRIPTS = Path(os.environ.get("SKILL_SCRIPTS_DIR") or (Path(__file__).resolve().parent / "scripts"))\n'
        'if not (SCRIPTS / "reversible_table_cleaner.py").is_file():\n'
        '    raise SystemExit("Set SKILL_SCRIPTS_DIR to the skill scripts/ folder before running this reproduction script.")\n\n'
        "# Step 1: Read-only fault scan (encoding, leading-zero IDs, sentinels, units, duplicates)\n"
        'subprocess.run([sys.executable, "-B", str(SCRIPTS / "dirty_table_fault_scanner.py"), "--input", str(RAW_TABLE), "--workorder", str(WORKORDER), "--out", str(OUT_DIR / "fault_scan.json")], check=True)\n\n'
        "# Step 2: Reversible cleaning with zero imputation and conflict quarantine\n"
        f'subprocess.run([sys.executable, "-B", str(SCRIPTS / "reversible_table_cleaner.py"), "--input", str(RAW_TABLE), "--workorder", str(WORKORDER), "--out-dir", str(OUT_DIR), "--stem", "{stem}"], check=True)\n\n'
        "# Step 3: Independent read-back reconciliation between CSV and manifest/report\n"
        f'subprocess.run([sys.executable, "-B", str(SCRIPTS / "quality_reconciler.py"), "--csv", str(OUT_DIR / "{stem}.csv"), "--manifest", str(OUT_DIR / "{stem}_manifest.json"), "--quarantine", str(OUT_DIR / "{stem}_quarantine_conflicts.csv"), "--out-md", str(OUT_DIR / "{stem}_quality.md"), "--out-json", str(OUT_DIR / "{stem}_reconciliation.json")], check=True)\n'
    )
    repro_p.write_text(repro_code, encoding="utf-8")

    return {
        "ok": bool(manifest["row_accounting"]["conservation_check_passed"]),
        "csv": csv_p.name,
        "quarantine_csv": quarantine_p.name,
        "manifest": man_p.name,
        "reproduce_script": repro_p.name,
        "row_accounting": manifest["row_accounting"],
        "cell_accounting": manifest["cell_accounting"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean scientific table reversibly under explicit workorder.")
    parser.add_argument("--input", default=None, help="Path to raw table CSV/delimited file.")
    parser.add_argument("--workorder", default=None, help="Path to cleaning workorder or dictionary JSON.")
    parser.add_argument("--input-dir", default=None, help="Optional /input directory for automatic table and workorder discovery.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stem", default="cleaned_table")
    args = parser.parse_args(argv)

    in_path = Path(args.input) if args.input else None
    wo_path = Path(args.workorder) if args.workorder else None
    if args.input_dir and (in_path is None or wo_path is None):
        from case_contract_sealer import discover_input_dir
        disc = discover_input_dir(Path(args.input_dir))
        if in_path is None and disc.get("primary_table_candidate"):
            in_path = Path(disc["primary_table_candidate"])
        if wo_path is None and disc.get("primary_workorder_candidate"):
            wo_path = Path(disc["primary_workorder_candidate"])
    if in_path is None:
        parser.error("Provide --input or --input-dir containing a tabular data file.")
    wo = json.loads(wo_path.read_text(encoding="utf-8-sig")) if (wo_path and wo_path.is_file()) else {}
    rep = clean_table_by_workorder(in_path, wo, Path(args.out_dir), stem=args.stem)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
