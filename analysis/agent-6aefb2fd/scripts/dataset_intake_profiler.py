#!/usr/bin/env python3
"""Zero-assumption read-only dataset & SQLite intake profiler (pure Python 3.12 stdlib).

Profiles CSV, JSON, and read-only SQLite tables strictly without mutating inputs.
Detects leading-zero string IDs, low-cardinality coded integers (e.g., cultivar_code),
exact duplicates, missing rates, zero-variance columns, and IQR outliers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

_LEADING_ZERO_RE = re.compile(r"^0\d+$")
_CODE_NAME_HINTS = ("code", "id", "class", "type", "status", "cultivar", "species", "group", "batch", "flag", "label")
_MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "<lod", "nd", "-999", "-999.0"}


def _quantile_sorted(vals: list[float], p: float) -> float:
    n = len(vals)
    if n == 0:
        return math.nan
    if n == 1:
        return vals[0]
    pos = (n - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def load_records_ro(input_path: Path, table: str | None = None) -> dict[str, Any]:
    """Load CSV/JSON/SQLite strictly read-only preserving string representations."""
    abs_p = input_path.resolve()
    raw_bytes = abs_p.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    ext = abs_p.suffix.lower()

    if ext in {".sqlite", ".sqlite3", ".db"}:
        conn = sqlite3.connect(f"file:{abs_p}?mode=ro", uri=True)
        try:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            if not tables:
                raise ValueError(f"SQLite database has 0 user tables: {abs_p.name}")
            chosen = table or tables[0]
            if chosen not in tables:
                raise ValueError(f"Table {chosen!r} not found in {abs_p.name}; available={tables}")
            cur = conn.execute(f'SELECT * FROM "{chosen}"')
            cols = [d[0] for d in cur.description]
            rows = [{c: ("" if v is None else str(v)) for c, v in zip(cols, rec)} for rec in cur.fetchall()]
            return {
                "input_kind": "sqlite_ro",
                "source_file": abs_p.name,
                "source_sha256": sha256,
                "table_used": chosen,
                "tables_available": tables,
                "columns": cols,
                "rows": rows,
            }
        finally:
            conn.close()

    if ext == ".json":
        obj = json.loads(raw_bytes.decode("utf-8-sig"))
        if isinstance(obj, dict) and isinstance(obj.get("records"), list):
            obj = obj["records"]
        if not isinstance(obj, list):
            raise ValueError("JSON input must be a list of record objects or {'records': [...]}")
        cols = list(dict.fromkeys(k for rec in obj if isinstance(rec, dict) for k in rec.keys()))
        rows = [{c: ("" if rec.get(c) is None else str(rec.get(c))) for c in cols} for rec in obj if isinstance(rec, dict)]
        return {
            "input_kind": "json",
            "source_file": abs_p.name,
            "source_sha256": sha256,
            "table_used": None,
            "tables_available": [],
            "columns": cols,
            "rows": rows,
        }

    # CSV or delimited text
    text = ""
    detected_enc = "utf-8"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            detected_enc = enc
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    delim = "\t" if ext == ".tsv" else ("," if not lines else max((",", ";", "\t", "|"), key=lambda d: lines[0].count(d)))
    reader = csv.DictReader(lines, delimiter=delim)
    cols = [c.strip() for c in (reader.fieldnames or [])]
    rows = []
    for rec in reader:
        if all((v is None or str(v).strip() == "") for v in rec.values()):
            continue
        rows.append({str(k).strip(): ("" if v is None else str(v).strip()) for k, v in rec.items() if k is not None})
    return {
        "input_kind": f"delimited({detected_enc},delim={delim!r})",
        "source_file": abs_p.name,
        "source_sha256": sha256,
        "table_used": None,
        "tables_available": [],
        "columns": cols,
        "rows": rows,
    }


def profile_loaded(loaded: dict[str, Any]) -> dict[str, Any]:
    cols = loaded["columns"]
    rows = loaded["rows"]
    seen_exact: set[tuple[str, ...]] = set()
    exact_dups = 0
    for r in rows:
        sig = tuple(r.get(c, "") for c in cols)
        if sig in seen_exact:
            exact_dups += 1
        else:
            seen_exact.add(sig)

    col_profiles: list[dict[str, Any]] = []
    leading_zero_cols: list[str] = []
    coded_int_cols: list[str] = []
    zero_var_cols: list[str] = []

    for c in cols:
        raw_vals = [r.get(c, "").strip() for r in rows]
        missing_cnt = sum(1 for v in raw_vals if v.lower() in _MISSING_TOKENS)
        non_miss = [v for v in raw_vals if v.lower() not in _MISSING_TOKENS]
        unique_vals = list(dict.fromkeys(non_miss))
        nunique = len(unique_vals)
        has_leading_zero = any(_LEADING_ZERO_RE.match(v) for v in non_miss)
        if has_leading_zero:
            leading_zero_cols.append(c)

        num_vals: list[float] = []
        all_int_like = True
        for v in non_miss:
            try:
                fv = float(v)
                if math.isfinite(fv):
                    num_vals.append(fv)
                    if fv != int(fv):
                        all_int_like = False
                else:
                    all_int_like = False
            except ValueError:
                all_int_like = False

        is_numeric = len(non_miss) > 0 and len(num_vals) == len(non_miss) and not has_leading_zero
        coded_flag = False
        stats: dict[str, Any] = {}
        if is_numeric and num_vals:
            svals = sorted(num_vals)
            n = len(svals)
            mean = sum(svals) / n
            sd = math.sqrt(sum((x - mean) ** 2 for x in svals) / (n - 1)) if n > 1 else 0.0
            q1, med, q3 = _quantile_sorted(svals, 0.25), _quantile_sorted(svals, 0.50), _quantile_sorted(svals, 0.75)
            iqr = q3 - q1
            iqr_out = sum(1 for x in svals if iqr > 0 and (x < q1 - 1.5 * iqr or x > q3 + 1.5 * iqr))
            if n > 1 and sd == 0.0:
                zero_var_cols.append(c)
            stats = {
                "min": round(svals[0], 6),
                "q1": round(q1, 6),
                "median": round(med, 6),
                "mean": round(mean, 6),
                "sd": round(sd, 6),
                "q3": round(q3, 6),
                "max": round(svals[-1], 6),
                "iqr_outlier_count": iqr_out,
            }
            if all_int_like and nunique <= min(12, max(3, n // 4)) and (
                any(h in c.lower() for h in _CODE_NAME_HINTS) or nunique <= 6
            ):
                coded_flag = True
                coded_int_cols.append(c)

        col_profiles.append({
            "column": c,
            "non_missing_count": len(non_miss),
            "missing_or_sentinel_count": missing_cnt,
            "missing_rate_pct": round((missing_cnt / len(rows) * 100.0), 2) if rows else 0.0,
            "unique_count": nunique,
            "is_numeric_continuous": bool(is_numeric and not coded_flag),
            "leading_zero_string_id": has_leading_zero,
            "coded_categorical_integer": coded_flag,
            "sample_values": unique_vals[:5],
            "stats": stats,
        })

    return {
        "ok": True,
        "input_kind": loaded["input_kind"],
        "source_file": loaded["source_file"],
        "source_sha256": loaded["source_sha256"],
        "table_used": loaded["table_used"],
        "tables_available": loaded["tables_available"],
        "rows_raw": len(rows),
        "columns_raw": len(cols),
        "exact_duplicate_rows": exact_dups,
        "leading_zero_id_columns": leading_zero_cols,
        "coded_integer_warnings": coded_int_cols,
        "zero_variance_columns": zero_var_cols,
        "columns": col_profiles,
    }


def profile_input(input_path: Path, *, table: str | None = None) -> dict[str, Any]:
    return profile_loaded(load_records_ro(input_path, table=table))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only zero-assumption scientific dataset profiler.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--table", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    rep = profile_input(Path(args.input), table=args.table)
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
