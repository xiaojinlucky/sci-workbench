#!/usr/bin/env python3
"""Read-only scientific table fault scanner.

Detects encoding/BOM/delimiter anomalies, leading-zero ID collision risks,
five-way missing/sentinel vs true-zero taxonomy, inline mixed units,
Unicode/thousands-separator storage faults, exact vs conflicting duplicates,
and per-metric QC bound violations without mutating the source file.
Pure Python 3.12 stdlib (zero external dependencies).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

DEFAULT_SENTINEL_TAXONOMY: dict[str, list[str]] = {
    "below_lod": ["<lod", "blod", "<loq", "bloq", "below lod", "below loq"],
    "not_detected": ["nd", "n.d.", "not detected", "undetected", "b.d.l.", "bdl"],
    "instrument_sentinel": ["-999", "-999.0", "-9999", "9999", "99999", "-99.9"],
    "entry_missing": ["", "na", "n/a", "nan", "null", "none", "missing", "-", "--", "."],
}

_UNICODE_MINUS_CHARS = ("\u2212", "\u2013", "\u2014", "\uff0d")
_INVISIBLE_WS_RE = re.compile(r"[\u00a0\u200b\u200c\u200d\ufeff\u202f]")
_LEADING_ZERO_INT_RE = re.compile(r"^0\d+$")
_PURE_DIGITS_RE = re.compile(r"^\d+$")
_NUM_UNIT_RE = re.compile(
    r"^\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+)(?:[eE]([+-]?\d+))?\s*([A-Za-z%μµ°/·⋅²³\-\^0-9]+)?\s*$"
)


def normalize_storage_text(raw: str) -> tuple[str, list[str]]:
    """Normalize storage/encoding artifacts while reporting exact transformations."""
    flags: list[str] = []
    s = raw
    if _INVISIBLE_WS_RE.search(s):
        flags.append("invisible_or_nbsp_whitespace")
        s = _INVISIBLE_WS_RE.sub(" ", s)
    for ch in _UNICODE_MINUS_CHARS:
        if ch in s:
            flags.append("unicode_minus_or_dash")
            s = s.replace(ch, "-")
    nfkc = unicodedata.normalize("NFKC", s)
    if nfkc != s:
        # Preserve Greek mu distinction separately from fullwidth digits/symbols
        if any(ord(c) > 0xFF00 for c in s):
            flags.append("fullwidth_characters")
            s = nfkc
    s = s.strip()
    return s, sorted(set(flags))


def parse_numeric_with_unit(raw: str) -> dict[str, Any]:
    """Parse numeric string with optional thousands commas, scientific notation, and inline unit."""
    norm, storage_flags = normalize_storage_text(raw)
    if not norm:
        return {"parsed": False, "normalized_text": "", "storage_flags": storage_flags}
    m = _NUM_UNIT_RE.match(norm)
    if not m:
        return {"parsed": False, "normalized_text": norm, "storage_flags": storage_flags}
    mantissa, exp_part, unit_part = m.group(1), m.group(2), m.group(3)
    if "," in mantissa:
        storage_flags = sorted(set(storage_flags + ["thousands_separator_comma"]))
        mantissa = mantissa.replace(",", "")
    num_str = f"{mantissa}e{exp_part}" if exp_part is not None else mantissa
    try:
        val = float(num_str)
        dec_val = Decimal(num_str)
    except (ValueError, InvalidOperation):
        return {"parsed": False, "normalized_text": norm, "storage_flags": storage_flags}
    unit_clean = (unit_part or "").strip().replace("µ", "u").replace("μ", "u")
    if unit_part and unit_part != unit_clean:
        storage_flags = sorted(set(storage_flags + ["unicode_micro_sign"]))
    return {
        "parsed": True,
        "value": val,
        "decimal_str": num_str,
        "is_true_zero": dec_val == Decimal("0"),
        "inline_unit": unit_clean or None,
        "inline_unit_raw": unit_part or None,
        "normalized_text": norm,
        "storage_flags": storage_flags,
    }


def detect_encoding_and_read(path: Path) -> dict[str, Any]:
    """Detect byte encoding, BOM, line endings, and delimiter; read strictly as strings."""
    raw_bytes = path.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    encoding = "utf-8-sig" if has_bom else "utf-8"
    text = ""
    for cand in ([encoding] + [e for e in ("utf-8", "gb18030", "latin-1") if e != encoding]):
        try:
            text = raw_bytes.decode(cand)
            encoding = cand
            break
        except UnicodeDecodeError:
            continue

    line_ending = "CRLF" if b"\r\n" in raw_bytes else ("CR" if b"\r" in raw_bytes else "LF")
    if not text.strip():
        return {
            "sha256": sha256,
            "bytes": len(raw_bytes),
            "encoding": encoding,
            "has_utf8_bom": has_bom,
            "line_ending": line_ending,
            "delimiter": ",",
            "columns": [],
            "rows": [],
            "embedded_newline_cells": 0,
        }

    # Streaming delimiter detection preserving quoted embedded newlines (never text.splitlines())
    first_nonempty = next((ln for ln in text.splitlines() if ln.strip() != ""), "")
    if path.suffix.lower() == ".tsv":
        delim = "\t"
    else:
        delim = ","
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            delim = dialect.delimiter
        except Exception:
            delim_counts = {d: first_nonempty.count(d) for d in (",", ";", "\t", "|")}
            delim = max(delim_counts, key=lambda k: delim_counts[k]) if max(delim_counts.values()) > 0 else ","

    stream = io.StringIO(text, newline="")
    reader = csv.DictReader(stream, delimiter=delim)
    raw_cols = list(reader.fieldnames or [])
    clean_cols = [c.strip() for c in raw_cols]
    rows: list[dict[str, str]] = []
    embedded_nl_cells = 0
    for rec in reader:
        # Preserve raw string values verbatim (never implicit int/float coercion)
        if all((v is None or str(v).strip() == "") for v in rec.values()):
            continue
        row_obj = {str(k).strip(): ("" if v is None else str(v)) for k, v in rec.items() if k is not None}
        for v in row_obj.values():
            if "\n" in v or "\r" in v:
                embedded_nl_cells += 1
        if None in rec and rec[None]:
            row_obj["__extra_unmapped_fields__"] = "|".join(str(x) for x in rec[None])
        rows.append(row_obj)

    return {
        "sha256": sha256,
        "bytes": len(raw_bytes),
        "encoding": encoding,
        "has_utf8_bom": has_bom,
        "line_ending": line_ending,
        "delimiter": delim,
        "columns_raw": raw_cols,
        "columns": clean_cols,
        "header_whitespace_trimmed": raw_cols != clean_cols,
        "embedded_newline_cells": embedded_nl_cells,
        "rows": rows,
    }


def _build_sentinel_lookup(workorder: dict[str, Any]) -> dict[str, str]:
    tax = workorder.get("sentinel_taxonomy")
    lookup: dict[str, str] = {}
    if isinstance(tax, dict) and tax:
        for cat, tokens in tax.items():
            for tok in tokens:
                lookup[str(tok).strip().lower()] = str(cat)
    else:
        for cat, tokens in DEFAULT_SENTINEL_TAXONOMY.items():
            for tok in tokens:
                lookup[tok] = cat
        for tok in workorder.get("sentinel_tokens") or []:
            key = str(tok).strip().lower()
            if key not in lookup:
                lookup[key] = "instrument_sentinel" if key.startswith("-") else "entry_missing"
    return lookup


def _resolve_pk_cols_with_audit(cols: list[str], workorder: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Resolve primary key columns without silently dropping missing declared keys."""
    pk = workorder.get("primary_key")
    if isinstance(pk, str) and pk.strip():
        declared = [pk.strip()]
    elif isinstance(pk, list) and pk:
        declared = [str(c).strip() for c in pk if str(c).strip()]
    else:
        declared = []

    if declared:
        present = [c for c in declared if c in cols]
        missing = [c for c in declared if c not in cols]
        if missing:
            return present, {
                "status": "MISSING_DECLARED_PK",
                "declared_primary_key": declared,
                "present_primary_key": present,
                "missing_primary_key_columns": missing,
                "warning": f"Declared primary_key column(s) missing from table header: {missing}; refusing silent PK shrinkage.",
            }
        return present, {
            "status": "DECLARED_EXACT",
            "declared_primary_key": declared,
            "present_primary_key": present,
            "missing_primary_key_columns": [],
        }

    fallback = [cols[0]] if cols else []
    return fallback, {
        "status": "FALLBACK_FIRST_COLUMN_UNDECLARED",
        "declared_primary_key": [],
        "present_primary_key": fallback,
        "missing_primary_key_columns": [],
        "warning": "No explicit primary_key declared in workorder; fell back to first column — verify composite key uniqueness.",
    }


def _resolve_pk_cols(cols: list[str], workorder: dict[str, Any]) -> list[str]:
    pk_cols, _ = _resolve_pk_cols_with_audit(cols, workorder)
    return pk_cols


def _resolve_numeric_targets(cols: list[str], pk_cols: list[str], workorder: dict[str, Any]) -> tuple[list[str], str | None]:
    metric_col = str(workorder.get("metric_column") or "").strip() or None
    val_col = str(workorder.get("value_column") or "").strip() or None
    if metric_col and val_col and metric_col in cols and val_col in cols:
        return [val_col], metric_col
    num_cols = [str(c).strip() for c in (workorder.get("numeric_cols") or []) if str(c).strip() in cols]
    if num_cols:
        return num_cols, None
    inferred = [
        c for c in cols
        if c not in pk_cols and any(k in c.lower() for k in ("val", "conc", "mean", "median", "score", "rate", "pct", "ratio", "level", "count"))
    ]
    return inferred, None


def _unit_key(u: str) -> str:
    return u.strip().replace("µ", "u").replace("μ", "u")


def _lookup_metric_rule(workorder: dict[str, Any], metric_name: str | None, col_name: str) -> dict[str, Any]:
    per_metric = workorder.get("per_metric_rules") or {}
    if metric_name and metric_name in per_metric:
        return per_metric[metric_name]
    if col_name in per_metric:
        return per_metric[col_name]
    # Legacy single-metric fallback
    qc_bounds = (workorder.get("qc_bounds") or {}).get(col_name) or (workorder.get("qc_bounds") or {}).get(metric_name or "")
    return {
        "target_unit": workorder.get("target_unit"),
        "unit_factors_to_target": workorder.get("unit_factors_to_target") or {},
        "qc_bounds": qc_bounds or {},
    }


def scan_dirty_table(path: Path, workorder: dict[str, Any] | None = None) -> dict[str, Any]:
    """Perform comprehensive read-only fault profiling on a scientific table."""
    wo = workorder or {}
    raw_info = detect_encoding_and_read(path)
    cols = raw_info["columns"]
    rows = raw_info["rows"]
    sentinel_lookup = _build_sentinel_lookup(wo)
    pk_cols, pk_audit = _resolve_pk_cols_with_audit(cols, wo)
    id_cols = [str(c).strip() for c in (wo.get("id_columns_preserve_string") or pk_cols) if str(c).strip() in cols]
    num_cols, metric_col = _resolve_numeric_targets(cols, pk_cols, wo)
    unit_col = str(wo.get("unit_column") or wo.get("unit_col") or "").strip() or None

    # 1. Leading-zero ID & implicit int-cast collision analysis
    leading_zero_report: dict[str, Any] = {}
    for idc in id_cols:
        raw_vals = [r.get(idc, "").strip() for r in rows if r.get(idc, "").strip() != ""]
        lz_examples = sorted({v for v in raw_vals if _LEADING_ZERO_INT_RE.match(v)})
        int_buckets: dict[int, set[str]] = defaultdict(set)
        for v in raw_vals:
            if _PURE_DIGITS_RE.match(v):
                int_buckets[int(v)].add(v)
        collisions = [
            {"int_value": k, "distinct_string_ids": sorted(vs)}
            for k, vs in sorted(int_buckets.items())
            if len(vs) > 1
        ]
        leading_zero_report[idc] = {
            "leading_zero_count": len(lz_examples),
            "leading_zero_examples": lz_examples[:10],
            "implicit_int_cast_collisions": collisions,
            "must_read_as_string": bool(lz_examples or collisions),
        }

    # 2. Exact duplicate & normalized-key duplicate (consistent vs conflicting)
    seen_exact: dict[tuple[str, ...], int] = {}
    exact_duplicates: list[dict[str, Any]] = []
    pk_groups: dict[tuple[str, ...], list[tuple[int, dict[str, str]]]] = defaultdict(list)

    for ridx, row in enumerate(rows, start=2):
        exact_sig = tuple(row.get(c, "") for c in cols)
        if exact_sig in seen_exact:
            exact_duplicates.append({"line": ridx, "first_line": seen_exact[exact_sig]})
        else:
            seen_exact[exact_sig] = ridx
        # Preserve leading zeros in PK normalization: trim edge whitespace only, do NOT strip '0'
        pk_sig = tuple(row.get(c, "").strip() for c in pk_cols)
        pk_groups[pk_sig].append((ridx, row))

    key_duplicates_consistent: list[dict[str, Any]] = []
    key_duplicates_conflicting: list[dict[str, Any]] = []
    for pk_sig, items in pk_groups.items():
        if len(items) <= 1:
            continue
        first_line, first_row = items[0]
        first_payload = {c: first_row.get(c, "").strip() for c in cols if c not in pk_cols}
        for ridx, row in items[1:]:
            cur_payload = {c: row.get(c, "").strip() for c in cols if c not in pk_cols}
            diff_cols = {
                c: {"first": first_payload[c], "current": cur_payload[c]}
                for c in first_payload
                if first_payload[c] != cur_payload[c]
            }
            rec_info = {
                "primary_key": dict(zip(pk_cols, pk_sig)),
                "first_line": first_line,
                "duplicate_line": ridx,
                "exact_match": (tuple(first_row.get(c, "") for c in cols) == tuple(row.get(c, "") for c in cols)),
                "conflicting_fields": diff_cols,
            }
            if diff_cols:
                key_duplicates_conflicting.append(rec_info)
            else:
                key_duplicates_consistent.append(rec_info)

    # 3. Cell-level taxonomy: missing/sentinel vs true-zero vs storage/unit/QC faults
    cell_faults: list[dict[str, Any]] = []
    taxonomy_counts: dict[str, int] = defaultdict(int)
    true_zero_count = 0
    extra_field_rows = 0

    for ridx, row in enumerate(rows, start=2):
        if "__extra_unmapped_fields__" in row:
            extra_field_rows += 1
            cell_faults.append({
                "line": ridx,
                "column": "__row__",
                "fault_category": "delimiter_column_overflow",
                "raw_value": row["__extra_unmapped_fields__"],
            })
        metric_name = row.get(metric_col, "").strip() if metric_col else None
        row_unit_raw = row.get(unit_col, "").strip() if unit_col else ""

        for nc in num_cols:
            raw_v = row.get(nc, "")
            norm_v, pre_flags = normalize_storage_text(raw_v)
            low = norm_v.lower()
            if low in sentinel_lookup:
                cat = sentinel_lookup[low]
                taxonomy_counts[cat] += 1
                cell_faults.append({
                    "line": ridx,
                    "column": nc,
                    "metric": metric_name or nc,
                    "fault_category": cat,
                    "raw_value": raw_v,
                    "storage_flags": pre_flags,
                })
                continue

            parsed = parse_numeric_with_unit(raw_v)
            if not parsed["parsed"]:
                taxonomy_counts["unparseable_text_fault"] += 1
                cell_faults.append({
                    "line": ridx,
                    "column": nc,
                    "metric": metric_name or nc,
                    "fault_category": "unparseable_text_fault",
                    "raw_value": raw_v,
                    "storage_flags": parsed["storage_flags"],
                })
                continue

            val = float(parsed["value"])
            if parsed["is_true_zero"]:
                true_zero_count += 1
            if parsed["storage_flags"]:
                taxonomy_counts["storage_encoding_artifact"] += 1
                cell_faults.append({
                    "line": ridx,
                    "column": nc,
                    "metric": metric_name or nc,
                    "fault_category": "storage_encoding_artifact",
                    "raw_value": raw_v,
                    "storage_flags": parsed["storage_flags"],
                })

            inline_u = parsed["inline_unit"]
            col_u = _unit_key(row_unit_raw) if row_unit_raw else ""
            rule = _lookup_metric_rule(wo, metric_name, nc)
            target_u = _unit_key(str(rule.get("target_unit") or ""))
            factors = {_unit_key(k): float(v) for k, v in (rule.get("unit_factors_to_target") or {}).items()}

            if inline_u:
                taxonomy_counts["inline_unit_mixed"] += 1
                cell_faults.append({
                    "line": ridx,
                    "column": nc,
                    "metric": metric_name or nc,
                    "fault_category": "inline_unit_mixed",
                    "raw_value": raw_v,
                    "inline_unit": inline_u,
                    "column_unit": col_u or None,
                })

            eff_u = inline_u or col_u or target_u
            if inline_u and col_u and inline_u != col_u:
                # Check if equivalent factor (e.g., ug/mL == mg/L == factor 1.0) vs conflicting scale
                f_in = factors.get(inline_u)
                f_col = factors.get(col_u)
                if f_in is None or f_col is None or abs(f_in - f_col) > 1e-12:
                    taxonomy_counts["inline_vs_column_unit_mismatch"] += 1
                    cell_faults.append({
                        "line": ridx,
                        "column": nc,
                        "metric": metric_name or nc,
                        "fault_category": "inline_vs_column_unit_mismatch",
                        "raw_value": raw_v,
                        "inline_unit": inline_u,
                        "column_unit": col_u,
                    })

            converted_val = val
            if eff_u and target_u:
                if eff_u in factors:
                    converted_val = val * factors[eff_u]
                elif eff_u != target_u:
                    taxonomy_counts["unknown_unit_no_factor"] += 1
                    cell_faults.append({
                        "line": ridx,
                        "column": nc,
                        "metric": metric_name or nc,
                        "fault_category": "unknown_unit_no_factor",
                        "raw_value": raw_v,
                        "effective_unit": eff_u,
                        "target_unit": target_u,
                    })

            qc = rule.get("qc_bounds") or {}
            qmin, qmax = qc.get("min"), qc.get("max")
            if (qmin is not None and converted_val < float(qmin)) or (qmax is not None and converted_val > float(qmax)):
                taxonomy_counts["qc_out_of_bounds"] += 1
                cell_faults.append({
                    "line": ridx,
                    "column": nc,
                    "metric": metric_name or nc,
                    "fault_category": "qc_out_of_bounds",
                    "raw_value": raw_v,
                    "converted_value": round(converted_val, 8),
                    "target_unit": target_u or eff_u,
                    "qc_bounds": qc,
                })

    return {
        "ok": True,
        "file": path.name,
        "sha256": raw_info["sha256"],
        "bytes": raw_info["bytes"],
        "detected_encoding": raw_info["encoding"],
        "has_utf8_bom": raw_info["has_utf8_bom"],
        "line_ending": raw_info["line_ending"],
        "detected_delimiter": raw_info["delimiter"],
        "header_whitespace_trimmed": raw_info["header_whitespace_trimmed"],
        "embedded_newline_cells": raw_info.get("embedded_newline_cells", 0),
        "rows_total": len(rows),
        "columns": cols,
        "primary_key_columns": pk_cols,
        "primary_key_audit": pk_audit,
        "numeric_target_columns": num_cols,
        "metric_column": metric_col,
        "unit_column": unit_col,
        "leading_zero_protection": leading_zero_report,
        "true_zero_cells_detected": true_zero_count,
        "exact_duplicate_count": len(exact_duplicates),
        "exact_duplicates": exact_duplicates,
        "key_duplicate_consistent_count": len(key_duplicates_consistent),
        "key_duplicates_consistent": key_duplicates_consistent,
        "key_duplicate_conflicting_count": len(key_duplicates_conflicting),
        "key_duplicates_conflicting": key_duplicates_conflicting,
        "extra_field_rows": extra_field_rows,
        "fault_taxonomy_counts": dict(sorted(taxonomy_counts.items())),
        "cell_faults": cell_faults,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only scientific table fault and leading-zero scanner.")
    parser.add_argument("--input", default=None, help="Path to raw CSV/delimited table")
    parser.add_argument("--input-dir", default=None, help="Optional /input directory for automatic table and workorder discovery")
    parser.add_argument("--workorder", default=None, help="Path to explicit cleaning workorder JSON")
    parser.add_argument("--dict", default=None, help="Alias for data dictionary / workorder JSON")
    parser.add_argument("--out", default=None, help="Optional JSON output path")
    args = parser.parse_args(argv)

    in_path = Path(args.input) if args.input else None
    wo_path = Path(args.workorder or args.dict) if (args.workorder or args.dict) else None
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
    report = scan_dirty_table(in_path, workorder=wo)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
