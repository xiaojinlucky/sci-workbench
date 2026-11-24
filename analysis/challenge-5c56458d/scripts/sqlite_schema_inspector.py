#!/usr/bin/env python3
"""Strictly read-only SQLite schema, integrity, coded-column, leading-zero ID, and 1:1/1:N cardinality inspector.

Pure Python 3.12 standard library (zero pandas/numpy/matplotlib required).
Guarantees zero DB mutation via URI mode=ro, PRAGMA query_only=ON, and pre/post SHA-256 verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Any

_LEADING_ZERO_RE = re.compile(r"^0\d+$")
_CODED_NAME_TOKENS = (
    "code",
    "id",
    "class",
    "type",
    "status",
    "cultivar",
    "species",
    "group",
    "flag",
    "pass",
    "grade",
    "stage",
    "tier",
    "arm",
    "batch",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _is_coded_integer_col(
    cname: str,
    sqlite_type: str,
    storage_types: set[str],
    pk_index: int,
    nunique: int,
    nrows: int,
    sample_vals: list[Any],
) -> tuple[bool, str]:
    low_name = cname.lower()
    low_type = sqlite_type.upper()
    non_null_types = storage_types - {"null"}
    is_int_like = ("INT" in low_type) or (non_null_types == {"integer"})
    if not is_int_like or nrows == 0:
        return False, ""
    # Binary 0/1 flag (e.g., qc_pass)
    int_vals = {v for v in sample_vals if isinstance(v, int)}
    if nunique <= 2 and int_vals.issubset({0, 1}) and not pk_index:
        return True, "binary_flag_0_1"
    # Coded category column (explicit semantic token + bounded cardinality)
    if any(tok in low_name for tok in _CODED_NAME_TOKENS):
        if nunique <= min(20, max(4, nrows // 3)):
            return True, "coded_categorical_integer"
    return False, ""


def inspect_sqlite_db(db_path: Path) -> dict[str, Any]:
    """Inspect SQLite database strictly read-only and infer integrity & inter-table cardinalities."""
    abs_p = db_path.resolve()
    sha_before = _sha256_file(abs_p)
    conn = _open_readonly(abs_p)

    try:
        table_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        tables = [str(r[0]) for r in table_rows]
        ddl_map = {str(r[0]): str(r[1] or "") for r in table_rows}

        table_reports: list[dict[str, Any]] = []
        pk_registry: dict[str, list[str]] = {}
        table_col_names: dict[str, list[str]] = {}
        declared_fk_edges: list[dict[str, Any]] = []
        constraint_loss_warnings: list[str] = []

        for t in tables:
            qt = _qident(t)
            cols_info = conn.execute(f"PRAGMA table_info({qt})").fetchall()
            fks_raw = conn.execute(f"PRAGMA foreign_key_list({qt})").fetchall()
            idxs_raw = conn.execute(f"PRAGMA index_list({qt})").fetchall()

            nrows = int(conn.execute(f"SELECT COUNT(*) FROM {qt}").fetchone()[0])
            col_names = [str(r[1]) for r in cols_info]
            table_col_names[t] = col_names

            # Exact duplicate rows via SQL GROUP BY over all columns
            if nrows > 0 and col_names:
                all_cols_sql = ", ".join(_qident(c) for c in col_names)
                distinct_rows = int(
                    conn.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {qt} GROUP BY {all_cols_sql})").fetchone()[0]
                )
                exact_dups = nrows - distinct_rows
            else:
                exact_dups = 0

            # Indexes
            indexes: list[dict[str, Any]] = []
            for idx_row in idxs_raw:
                idx_name = str(idx_row[1])
                idx_cols = [str(ic[2]) for ic in conn.execute(f"PRAGMA index_info({_qident(idx_name)})").fetchall()]
                indexes.append(
                    {
                        "name": idx_name,
                        "unique": bool(idx_row[2]),
                        "origin": str(idx_row[3]),
                        "columns": idx_cols,
                    }
                )

            pk_cols = [str(r[1]) for r in sorted(cols_info, key=lambda x: x[5]) if int(r[5]) > 0]
            pk_registry[t] = pk_cols

            if not pk_cols:
                constraint_loss_warnings.append(
                    f"Table '{t}' has no explicit PRIMARY KEY (possible sqlite-utils/CSV import constraint loss)."
                )

            pk_unique: bool | None = None
            pk_duplicate_groups = 0
            if pk_cols and nrows > 0:
                pk_sql = ", ".join(_qident(c) for c in pk_cols)
                distinct_pk = int(
                    conn.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {qt} GROUP BY {pk_sql})").fetchone()[0]
                )
                pk_unique = distinct_pk == nrows
                pk_duplicate_groups = nrows - distinct_pk

            col_meta: list[dict[str, Any]] = []
            coded_integer_cols: list[dict[str, Any]] = []
            leading_zero_cols: list[dict[str, Any]] = []

            for cid, cname, ctype, notnull, dflt, pk in cols_info:
                cname_s = str(cname)
                ctype_s = str(ctype or "")
                qc = _qident(cname_s)

                null_cnt = int(conn.execute(f"SELECT COUNT(*) FROM {qt} WHERE {qc} IS NULL").fetchone()[0])
                empty_str_cnt = int(
                    conn.execute(f"SELECT COUNT(*) FROM {qt} WHERE typeof({qc})='text' AND trim({qc})=''").fetchone()[0]
                )
                nunique = int(
                    conn.execute(f"SELECT COUNT(DISTINCT {qc}) FROM {qt} WHERE {qc} IS NOT NULL").fetchone()[0]
                )
                type_counts = {
                    str(r[0]): int(r[1])
                    for r in conn.execute(f"SELECT typeof({qc}), COUNT(*) FROM {qt} GROUP BY typeof({qc})").fetchall()
                }
                samples = [
                    r[0]
                    for r in conn.execute(
                        f"SELECT DISTINCT {qc} FROM {qt} WHERE {qc} IS NOT NULL LIMIT 6"
                    ).fetchall()
                ]

                non_null_types = set(type_counts.keys()) - {"null"}
                numeric_range: dict[str, Any] | None = None
                if non_null_types and non_null_types.issubset({"integer", "real"}):
                    mn, mx = conn.execute(f"SELECT MIN({qc}), MAX({qc}) FROM {qt} WHERE {qc} IS NOT NULL").fetchone()
                    numeric_range = {"min": mn, "max": mx}

                # Leading-zero string ID check
                lz_count = 0
                lz_examples: list[str] = []
                if "text" in non_null_types:
                    text_vals = [
                        str(r[0])
                        for r in conn.execute(
                            f"SELECT DISTINCT {qc} FROM {qt} WHERE typeof({qc})='text' AND {qc} LIKE '0%' LIMIT 500"
                        ).fetchall()
                    ]
                    matched_lz = [v for v in text_vals if _LEADING_ZERO_RE.match(v)]
                    if matched_lz:
                        lz_examples = matched_lz[:5]
                        lz_count = int(
                            conn.execute(
                                f"SELECT COUNT(*) FROM {qt} WHERE typeof({qc})='text' AND {qc} GLOB '0[0-9]*'"
                            ).fetchone()[0]
                        )
                        leading_zero_cols.append(
                            {
                                "column": cname_s,
                                "leading_zero_row_count": lz_count,
                                "examples": lz_examples,
                                "warning": "Preserve strictly as TEXT; never CAST to INTEGER or drop leading zeros.",
                            }
                        )

                is_coded, coded_kind = _is_coded_integer_col(
                    cname_s, ctype_s, set(type_counts.keys()), int(pk), nunique, nrows, samples
                )
                if is_coded:
                    coded_integer_cols.append(
                        {
                            "column": cname_s,
                            "coded_kind": coded_kind,
                            "unique_non_null": nunique,
                            "examples": samples[:6],
                            "rule": (
                                "Binary 0/1 flag: use COUNT/SUM or explicit rate AVG(CAST(col AS REAL)) labeled as rate."
                                if coded_kind == "binary_flag_0_1"
                                else "Nominal/ordinal code: use for GROUP BY/JOIN only; DO NOT apply AVG() or SUM()."
                            ),
                        }
                    )

                mixed_storage_warning = len(non_null_types) > 1
                col_meta.append(
                    {
                        "name": cname_s,
                        "sqlite_type": ctype_s,
                        "pk_index": int(pk),
                        "not_null_constraint": bool(notnull),
                        "default_value": dflt,
                        "null_count": null_cnt,
                        "empty_string_count": empty_str_cnt,
                        "unique_non_null": nunique,
                        "storage_classes": type_counts,
                        "mixed_storage_warning": mixed_storage_warning,
                        "numeric_range": numeric_range,
                        "coded_categorical_flag": is_coded,
                        "coded_kind": coded_kind or None,
                        "leading_zero_text_flag": lz_count > 0,
                        "leading_zero_examples": lz_examples,
                        "sample_values": samples[:5],
                    }
                )

            fks_formatted: list[dict[str, Any]] = []
            for r in fks_raw:
                fk_entry = {
                    "id": int(r[0]),
                    "seq": int(r[1]),
                    "ref_table": str(r[2]),
                    "from": str(r[3]),
                    "to": str(r[4]),
                }
                fks_formatted.append(fk_entry)
                declared_fk_edges.append(
                    {
                        "child_table": t,
                        "child_col": str(r[3]),
                        "parent_table": str(r[2]),
                        "parent_col": str(r[4]),
                        "source": "declared_foreign_key",
                    }
                )

            table_reports.append(
                {
                    "table": t,
                    "row_count": nrows,
                    "exact_duplicate_rows": exact_dups,
                    "primary_key_cols": pk_cols,
                    "primary_key_unique": pk_unique,
                    "pk_duplicate_excess": pk_duplicate_groups,
                    "foreign_keys": fks_formatted,
                    "indexes": indexes,
                    "coded_integer_columns": coded_integer_cols,
                    "leading_zero_text_columns": leading_zero_cols,
                    "columns": col_meta,
                    "ddl": ddl_map.get(t, ""),
                }
            )

        # Infer implicit FK edges (detecting constraint loss from sqlite-utils / CSV copies)
        declared_edge_keys = {
            (e["child_table"], e["child_col"], e["parent_table"], e["parent_col"]) for e in declared_fk_edges
        }
        all_edges = list(declared_fk_edges)
        for child_t, ccols in table_col_names.items():
            for parent_t, pks in pk_registry.items():
                if child_t == parent_t or len(pks) != 1:
                    continue
                pk_col = pks[0]
                if pk_col in ccols:
                    edge_key = (child_t, pk_col, parent_t, pk_col)
                    if edge_key not in declared_edge_keys:
                        all_edges.append(
                            {
                                "child_table": child_t,
                                "child_col": pk_col,
                                "parent_table": parent_t,
                                "parent_col": pk_col,
                                "source": "inferred_matching_pk_name",
                            }
                        )
                        constraint_loss_warnings.append(
                            f"Column '{child_t}.{pk_col}' matches PK '{parent_t}.{pk_col}' without an explicit FOREIGN KEY constraint."
                        )

        # Evaluate FK orphan counts, zero-child parent keys, and exact 1:1 vs 1:N cardinality
        relationship_cardinalities: list[dict[str, Any]] = []
        for edge in all_edges:
            ct, cc = edge["child_table"], edge["child_col"]
            pt, pc = edge["parent_table"], edge["parent_col"]
            if pt not in table_col_names or pc not in table_col_names[pt]:
                continue
            qct, qcc, qpt, qpc = _qident(ct), _qident(cc), _qident(pt), _qident(pc)

            orphan_cnt = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {qct} c LEFT JOIN {qpt} p ON c.{qcc} = p.{qpc} "
                    f"WHERE c.{qcc} IS NOT NULL AND p.{qpc} IS NULL"
                ).fetchone()[0]
            )
            zero_child_parents = [
                r[0]
                for r in conn.execute(
                    f"SELECT p.{qpc} FROM {qpt} p LEFT JOIN {qct} c ON p.{qpc} = c.{qcc} "
                    f"WHERE c.{qcc} IS NULL ORDER BY p.{qpc}"
                ).fetchall()
            ]
            mults = [
                int(r[0])
                for r in conn.execute(
                    f"SELECT COUNT(*) FROM {qct} WHERE {qcc} IS NOT NULL GROUP BY {qcc}"
                ).fetchall()
            ]
            min_m = min(mults) if mults else 0
            max_m = max(mults) if mults else 0
            med_m = float(statistics.median(mults)) if mults else 0.0
            if max_m == 0:
                rel_type = "empty_child"
            elif max_m == 1:
                rel_type = "1:1"
            else:
                rel_type = "1:N"

            relationship_cardinalities.append(
                {
                    "parent_table": pt,
                    "parent_col": pc,
                    "child_table": ct,
                    "child_col": cc,
                    "edge_source": edge["source"],
                    "inferred_cardinality": rel_type,
                    "child_rows_per_matched_parent": {
                        "min": min_m,
                        "median": med_m,
                        "max": max_m,
                        "matched_parent_keys": len(mults),
                    },
                    "orphan_child_rows": orphan_cnt,
                    "zero_child_parent_count": len(zero_child_parents),
                    "zero_child_parent_keys_sample": zero_child_parents[:10],
                    "join_inflation_risk": rel_type == "1:N",
                    "join_guidance": (
                        f"1:N relation (up to {max_m} rows in '{ct}' per '{pt}.{pc}'): pre-aggregate '{ct}' in a CTE GROUP BY {cc} before joining to '{pt}'."
                        if rel_type == "1:N"
                        else (
                            f"1:1 relation; note {len(zero_child_parents)} parent key(s) in '{pt}' have 0 child rows — use LEFT JOIN to preserve legitimate empty groups."
                            if zero_child_parents
                            else "Safe 1:1 join cardinality."
                        )
                    ),
                }
            )
    finally:
        conn.close()

    sha_after = _sha256_file(abs_p)
    immutable_ok = sha_before == sha_after

    all_coded = [
        {"table": tr["table"], **c} for tr in table_reports for c in tr["coded_integer_columns"]
    ]
    all_lz = [
        {"table": tr["table"], **lz} for tr in table_reports for lz in tr["leading_zero_text_columns"]
    ]

    return {
        "ok": immutable_ok,
        "db_file": db_path.name,
        "db_sha256_before": sha_before,
        "db_sha256_after": sha_after,
        "db_immutability_verified": immutable_ok,
        "sqlite_version": sqlite3.sqlite_version,
        "table_count": len(table_reports),
        "tables": table_reports,
        "relationship_cardinalities": relationship_cardinalities,
        "coded_integer_summary": all_coded,
        "leading_zero_text_summary": all_lz,
        "constraint_loss_warnings": constraint_loss_warnings,
    }


def _render_markdown(rep: dict[str, Any]) -> str:
    lines = [
        f"# Read-Only SQLite Schema Audit (`{rep['db_file']}`)",
        "",
        f"- **Immutability Verified**: `{rep['db_immutability_verified']}` (`sha256={rep['db_sha256_before'][:16]}...`)",
        f"- **SQLite Version**: `{rep['sqlite_version']}` | **Tables**: `{rep['table_count']}`",
        "",
        "## Tables Overview",
        "",
        "| Table | Rows | Exact Dups | Primary Key | PK Unique | Coded Int Cols | Leading-Zero ID Cols |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for t in rep["tables"]:
        coded = ", ".join(c["column"] for c in t["coded_integer_columns"]) or "-"
        lz = ", ".join(c["column"] for c in t["leading_zero_text_columns"]) or "-"
        pk = ", ".join(t["primary_key_cols"]) or "(none)"
        lines.append(
            f"| `{t['table']}` | {t['row_count']} | {t['exact_duplicate_rows']} | `{pk}` | `{t['primary_key_unique']}` | `{coded}` | `{lz}` |"
        )

    if rep["relationship_cardinalities"]:
        lines.extend(
            [
                "",
                "## Inferred Inter-Table Relationship Cardinalities",
                "",
                "| Parent -> Child | Cardinality | Max Child/Parent | Orphans | Zero-Child Parents | Inflation Risk |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        for rel in rep["relationship_cardinalities"]:
            edge = f"`{rel['parent_table']}.{rel['parent_col']}` -> `{rel['child_table']}.{rel['child_col']}`"
            lines.append(
                f"| {edge} | **{rel['inferred_cardinality']}** | {rel['child_rows_per_matched_parent']['max']} | "
                f"{rel['orphan_child_rows']} | {rel['zero_child_parent_count']} | `{rel['join_inflation_risk']}` |"
            )

    if rep["constraint_loss_warnings"]:
        lines.extend(["", "## Constraint-Loss Warnings", ""])
        for w in rep["constraint_loss_warnings"]:
            lines.append(f"- {w}")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect SQLite database strictly read-only with integrity and cardinality audit.")
    parser.add_argument("--db", required=True, help="Path to SQLite database snapshot.")
    parser.add_argument("--out", default=None, help="Optional JSON output path.")
    parser.add_argument("--md-out", default=None, help="Optional Markdown summary output path.")
    args = parser.parse_args(argv)

    rep = inspect_sqlite_db(Path(args.db))
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt + "\n", encoding="utf-8")
    if args.md_out:
        mp = Path(args.md_out)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(_render_markdown(rep), encoding="utf-8")
    print(txt)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
