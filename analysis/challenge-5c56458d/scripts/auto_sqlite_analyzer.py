#!/usr/bin/env python3
"""Zero-manual /input -> /app/output SQLite schema inspector, safe CTE synthesizer, and verifier (challenge-5c56458d).

Auto-discovers read-only SQLite snapshots and case.json under --input-dir:
  1. Runs strict mode=ro sqlite_schema_inspector (recording DB SHA-256, FKs, 1:N inflation risks,
     coded integer columns, leading-zero text IDs, and legitimate zero-child parent keys).
  2. Automatically synthesizes safe read-only SQL queries + independent Python reconciliation specs:
     - Multi-table star/1:N schemas -> pre-aggregation CTE + LEFT JOIN preserving legitimate empty groups
     - Single-table categorical schemas -> grouped aggregate + independent Python check
     - Single-table continuous schemas (e.g., Concrete 1030) -> continuous stratification CASE binning + check
  3. Executes sql_cross_table_runner.py verifying JOIN inflation == False, max_abs_diff < 1e-9,
     and db_immutability_verified == True, then seals /app/output/result.json.
Pure Python 3.12 stdlib.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from case_contract_sealer import discover_input_dir, seal_result_contract
from sql_cross_table_runner import run_sql_analysis
from sqlite_schema_inspector import inspect_sqlite_db


def _parse_task_query_clauses(task_text: str, base_cols: list[str]) -> dict[str, Any]:
    """Parse parameterized WHERE filters, ORDER BY, and LIMIT directives from task.md safely."""
    col_lower = {c.lower(): c for c in base_cols}
    filters: list[dict[str, Any]] = []
    seen_filters: set[tuple[str, str, Any]] = set()

    # Match patterns like: WHERE col >= 40, filter col > 10, col = 'value', col <= 100
    for m in re.finditer(
        r"(?:\bwhere\b|\bfilter\b|\bwith\b|\band\b)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?\s*(>=|<=|!=|<>|=|>|<)\s*(?:'([^']*)'|\"([^\"]*)\"|(-?\d+(?:\.\d+)?))",
        task_text,
        flags=re.I,
    ):
        raw_col = m.group(1).lower()
        if raw_col not in col_lower:
            continue
        col = col_lower[raw_col]
        op = m.group(2)
        if m.group(3) is not None:
            val: Any = m.group(3)
        elif m.group(4) is not None:
            val = m.group(4)
        else:
            num_s = m.group(5)
            val = float(num_s) if "." in num_s else int(num_s)
        key = (col, op, val)
        if key not in seen_filters:
            seen_filters.add(key)
            filters.append({"col": col, "op": op, "value": val})

    order_by: dict[str, Any] | None = None
    m_ord = re.search(r"\border\s+by\s+`?([A-Za-z_][A-Za-z0-9_]*)`?(?:\s+(ASC|DESC))?", task_text, flags=re.I)
    if m_ord:
        ocol_raw = m_ord.group(1).lower()
        odir = (m_ord.group(2) or "ASC").upper()
        if ocol_raw in {"mean_metric", "n_samples", "group_label", "stratum", "total_child_rows", "mean_child_metric"}:
            order_by = {"col": ocol_raw, "direction": odir}
        elif ocol_raw in col_lower:
            order_by = {"col": "mean_metric", "direction": odir, "requested_col": col_lower[ocol_raw]}

    limit_n: int | None = None
    m_lim = re.search(r"\b(?:limit|top)\s+(\d+)\b", task_text, flags=re.I)
    if m_lim:
        lv = int(m_lim.group(1))
        if lv > 0:
            limit_n = lv

    return {"filters": filters, "order_by": order_by, "limit": limit_n}


def _pick_continuous_metric(tbl_info: dict[str, Any], task_text: str = "") -> str | None:
    coded = {c["column"] for c in (tbl_info.get("coded_integer_columns") or []) if isinstance(c, dict) and c.get("column")}
    pks = set(tbl_info.get("primary_key_cols") or [])
    cands = []
    for col in tbl_info.get("columns") or []:
        name = col["name"]
        stype = str(col.get("sqlite_type", "")).upper()
        if name in coded or name in pks or name.lower().endswith("_id") or name.lower().endswith("_code"):
            continue
        if any(t in stype for t in ("REAL", "FLOAT", "DOUBLE", "NUM", "INT")):
            cands.append(name)
    if not cands:
        return None
    low_task = task_text.lower()
    if low_task:
        # Exclude columns that appear ONLY inside WHERE filter clauses when selecting primary outcome
        where_cols = {m.group(1).lower() for m in re.finditer(r"\bwhere\s+`?([A-Za-z_][A-Za-z0-9_]*)`?", low_task)}
        mentioned = [c for c in cands if c.lower() in low_task and c.lower() not in where_cols]
        if mentioned:
            return mentioned[0]
        mentioned_all = [c for c in cands if c.lower() in low_task]
        if mentioned_all:
            return mentioned_all[-1]
    return cands[-1]


def _synthesize_safe_query_spec(schema: dict[str, Any], target_table: str | None = None, task_text: str = "") -> dict[str, Any]:
    tables = {t["table"]: t for t in (schema.get("tables") or [])}
    rels = schema.get("relationship_cardinalities") or schema.get("inferred_relationships") or []
    if not tables:
        raise ValueError("SQLite database contains 0 tables")

    # Case 1: Multi-table star / 1:N schema (pick parent-fact-child chain if target_table matches or auto)
    if target_table is None:
        # Prefer 1:N edges whose parent_table itself references a dimension table (star schema: dim -> fact -> child)
        infl_rels = [r for r in rels if r.get("join_inflation_risk")]
        infl_rels.sort(
            key=lambda r: (
                1 if (tables.get(r["parent_table"], {}).get("foreign_keys")) else 0,
                int(tables.get(r["parent_table"], {}).get("row_count", 0)),
            ),
            reverse=True,
        )
        if infl_rels:
            rel_1n = infl_rels[0]
            fact_tbl = rel_1n["parent_table"]
            child_tbl = rel_1n["child_table"]
            join_pk = rel_1n["parent_col"]
            fact_info = tables[fact_tbl]
            metric_col = _pick_continuous_metric(fact_info, task_text)
            fact_col_names = [c["name"] for c in (fact_info.get("columns") or [])]
            clauses = _parse_task_query_clauses(task_text, fact_col_names)
            # Look for dimension table referenced by fact_tbl
            dim_fks = [fk for fk in (fact_info.get("foreign_keys") or []) if fk["ref_table"] in tables]
            if dim_fks and metric_col:
                fk = dim_fks[0]
                dim_tbl = fk["ref_table"]
                dim_key = fk["to"]
                fact_fk = fk["from"]
                dim_info = tables[dim_tbl]
                label_col = next(
                    (c["name"] for c in dim_info["columns"] if "TEXT" in str(c.get("sqlite_type", "")).upper() and c["name"] != dim_key),
                    dim_key,
                )
                child_info = tables[child_tbl]
                child_bin_cols = {
                    c["column"] for c in (child_info.get("coded_integer_columns") or [])
                    if c.get("coded_kind") == "binary_flag_0_1"
                }
                child_cands = [
                    c["name"] for c in child_info["columns"]
                    if c["name"] not in set(child_info["primary_key_cols"])
                    and c["name"] != join_pk
                    and any(t in str(c.get("sqlite_type", "")).upper() for t in ("INT", "REAL"))
                ]
                # Prefer binary pass_flag/qc_pass mentioned in task.md, else first numeric child column
                child_num = next((c for c in child_cands if c.lower() in task_text.lower()), None) or (child_cands[0] if child_cands else None)
                child_is_bin = bool(child_num and child_num in child_bin_cols)
                cte_select = "COUNT(*) AS n_child_rows" + (f', AVG(CAST("{child_num}" AS REAL)) AS mean_child_metric' if child_num else "")
                outer_child_metric = ",\n       AVG(ch.mean_child_metric) AS mean_child_metric" if child_num else ""

                params: list[Any] = []
                join_filters_sql = ""
                for flt in clauses["filters"]:
                    join_filters_sql += f' AND f."{flt["col"]}" {flt["op"]} ?'
                    params.append(flt["value"])

                ord_cfg = clauses["order_by"]
                if ord_cfg:
                    ocol = ord_cfg["col"]
                    odir = ord_cfg["direction"]
                    if odir == "DESC":
                        order_sql = f'ORDER BY ({ocol} IS NULL) ASC, {ocol} DESC, d."{dim_key}" ASC'
                    else:
                        order_sql = f'ORDER BY ({ocol} IS NOT NULL) ASC, {ocol} ASC, d."{dim_key}" ASC'
                else:
                    order_sql = f'ORDER BY d."{dim_key}"'

                limit_sql = ""
                if clauses["limit"] is not None:
                    limit_sql = "\nLIMIT ?"
                    params.append(clauses["limit"])

                sql = (
                    f'WITH child_per_pk AS (\n'
                    f'    SELECT "{join_pk}", {cte_select}\n'
                    f'    FROM "{child_tbl}"\n'
                    f'    GROUP BY "{join_pk}"\n'
                    f')\n'
                    f'SELECT d."{label_col}" AS group_label,\n'
                    f'       COUNT(f."{join_pk}") AS n_samples,\n'
                    f'       AVG(f."{metric_col}") AS mean_metric,\n'
                    f'       COALESCE(SUM(ch.n_child_rows), 0) AS total_child_rows{outer_child_metric}\n'
                    f'FROM "{dim_tbl}" d\n'
                    f'LEFT JOIN "{fact_tbl}" f ON d."{dim_key}" = f."{fact_fk}"{join_filters_sql}\n'
                    f'LEFT JOIN child_per_pk ch ON f."{join_pk}" = ch."{join_pk}"\n'
                    f'GROUP BY d."{dim_key}", d."{label_col}"\n'
                    f'{order_sql}{limit_sql};'
                )
                per_key_metrics = {"mean_child_metric": {"col": child_num, "agg": "mean"}} if child_num else {}
                rec_metrics = [
                    {"sql_col": "mean_metric", "source_col": metric_col, "agg": "mean"},
                    {"sql_col": "total_child_rows", "source_col": "__child_row_count__", "agg": "sum", "coalesce_zero": True},
                ]
                if child_num:
                    rec_metrics.append({
                        "sql_col": "mean_child_metric",
                        "source_col": "mean_child_metric",
                        "agg": "mean",
                        "is_binary_proportion": child_is_bin,
                    })
                rec_block: dict[str, Any] = {
                    "base_table": fact_tbl,
                    "sql_group_col": "group_label",
                    "sql_count_col": "n_samples",
                    "dimension_table": {
                        "table": dim_tbl,
                        "dim_key": dim_key,
                        "base_fk": fact_fk,
                        "label_col": label_col,
                    },
                    "child_pre_agg": {
                        "table": child_tbl,
                        "join_key": join_pk,
                        "per_key_metrics": per_key_metrics,
                    },
                    "metrics": rec_metrics,
                }
                if clauses["filters"]:
                    rec_block["filters"] = clauses["filters"]
                if clauses["order_by"]:
                    rec_block["order_by"] = clauses["order_by"]
                if clauses["limit"] is not None:
                    rec_block["limit"] = clauses["limit"]
                return {
                    "base_table": fact_tbl,
                    "base_pk": join_pk,
                    "group_col": "group_label",
                    "value_col": "mean_metric",
                    "title": f"{fact_tbl}: {metric_col} by {label_col} (1:N Pre-Aggregated CTE + Empty Group Preserved)",
                    "parsed_task_clauses": clauses,
                    "queries": [
                        {
                            "id": f"safe_cte_{fact_tbl}",
                            "sql": sql,
                            "params": params,
                            "check_inflation": True,
                            "dimension_table": dim_tbl,
                            "reconcile": rec_block,
                        }
                    ],
                }

    # Case 2: Single-table query (categorical group OR continuous stratification binning)
    chosen_tbl = target_table or max(tables.values(), key=lambda t: int(t["row_count"]))["table"]
    tinfo = tables[chosen_tbl]
    pk = (tinfo.get("primary_key_cols") or [tinfo["columns"][0]["name"]])[0]
    metric_col = _pick_continuous_metric(tinfo, task_text)
    if not metric_col:
        raise ValueError(f"No continuous numeric column found in table {chosen_tbl}")
    tbl_col_names = [c["name"] for c in (tinfo.get("columns") or [])]
    clauses = _parse_task_query_clauses(task_text, tbl_col_names)
    params_s: list[Any] = []
    where_sql = ""
    if clauses["filters"]:
        where_parts = []
        for flt in clauses["filters"]:
            where_parts.append(f'"{flt["col"]}" {flt["op"]} ?')
            params_s.append(flt["value"])
        where_sql = "\nWHERE " + " AND ".join(where_parts)

    # Find low-cardinality non-ID text grouping column
    cat_col = next(
        (
            c["name"] for c in tinfo["columns"]
            if "TEXT" in str(c.get("sqlite_type", "")).upper()
            and not c.get("leading_zero_text_flag")
            and 2 <= int(c.get("unique_non_null", 0)) <= 12
        ),
        None,
    )
    if cat_col:
        ord_cfg = clauses["order_by"]
        order_sql = f'ORDER BY {ord_cfg["col"]} {ord_cfg["direction"]}' if ord_cfg else f'ORDER BY "{cat_col}"'
        limit_sql = ""
        if clauses["limit"] is not None:
            limit_sql = "\nLIMIT ?"
            params_s.append(clauses["limit"])
        sql = (
            f'SELECT "{cat_col}" AS group_label, COUNT(*) AS n_samples, AVG("{metric_col}") AS mean_metric\n'
            f'FROM "{chosen_tbl}"{where_sql}\n'
            f'GROUP BY "{cat_col}"\n'
            f'{order_sql}{limit_sql};'
        )
        rec_block_c: dict[str, Any] = {
            "base_table": chosen_tbl,
            "base_group_col": cat_col,
            "sql_group_col": "group_label",
            "sql_count_col": "n_samples",
            "metrics": [{"sql_col": "mean_metric", "source_col": metric_col, "agg": "mean"}],
        }
        if clauses["filters"]:
            rec_block_c["filters"] = clauses["filters"]
        if clauses["order_by"]:
            rec_block_c["order_by"] = clauses["order_by"]
        if clauses["limit"] is not None:
            rec_block_c["limit"] = clauses["limit"]
        return {
            "base_table": chosen_tbl,
            "base_pk": pk,
            "group_col": "group_label",
            "value_col": "mean_metric",
            "title": f"{chosen_tbl}: {metric_col} by {cat_col}",
            "parsed_task_clauses": clauses,
            "queries": [
                {
                    "id": f"grouped_{chosen_tbl}",
                    "sql": sql,
                    "params": params_s,
                    "check_inflation": True,
                    "reconcile": rec_block_c,
                }
            ],
        }

    # Continuous stratification binning (Concrete style: pick age/time/stage integer or first continuous predictor)
    strat_col = next(
        (
            c["name"] for c in tinfo["columns"]
            if c["name"] not in {pk, metric_col}
            and any(h in c["name"].lower() for h in ("age", "day", "time", "stage", "year"))
        ),
        next((c["name"] for c in tinfo["columns"] if c["name"] not in {pk, metric_col}), None),
    )
    if not strat_col:
        raise ValueError(f"No stratification column available in {chosen_tbl}")

    ord_cfg = clauses["order_by"]
    order_sql = f'ORDER BY {ord_cfg["col"]} {ord_cfg["direction"]}' if ord_cfg else "ORDER BY stratum"
    limit_sql = ""
    if clauses["limit"] is not None:
        limit_sql = "\nLIMIT ?"
        params_s.append(clauses["limit"])

    # Default curing-age / general 3-stratum cutpoints (<=7, 8-28, >28 if age-like else <=0 / >0)
    if any(h in strat_col.lower() for h in ("age", "day")):
        sql = (
            f'WITH binned AS (\n'
            f'    SELECT "{pk}",\n'
            f'           CASE WHEN "{strat_col}" <= 7 THEN \'01_early_le_7\'\n'
            f'                WHEN "{strat_col}" <= 28 THEN \'02_standard_8_28\'\n'
            f'                ELSE \'03_mature_gt_28\' END AS stratum,\n'
            f'           "{metric_col}"\n'
            f'    FROM "{chosen_tbl}"{where_sql}\n'
            f')\n'
            f'SELECT stratum, COUNT(*) AS n_samples, AVG("{metric_col}") AS mean_metric\n'
            f'FROM binned GROUP BY stratum {order_sql}{limit_sql};'
        )
        bins = [
            {"label": "01_early_le_7", "max": 7},
            {"label": "02_standard_8_28", "min_exclusive": 7, "max": 28},
            {"label": "03_mature_gt_28", "min_exclusive": 28},
        ]
    else:
        sql = (
            f'WITH binned AS (\n'
            f'    SELECT "{pk}",\n'
            f'           CASE WHEN "{strat_col}" <= 0 THEN \'01_le_0\'\n'
            f'                ELSE \'02_gt_0\' END AS stratum,\n'
            f'           "{metric_col}"\n'
            f'    FROM "{chosen_tbl}"{where_sql}\n'
            f')\n'
            f'SELECT stratum, COUNT(*) AS n_samples, AVG("{metric_col}") AS mean_metric\n'
            f'FROM binned GROUP BY stratum {order_sql}{limit_sql};'
        )
        bins = [{"label": "01_le_0", "max": 0}, {"label": "02_gt_0", "min_exclusive": 0}]

    rec_block_b: dict[str, Any] = {
        "base_table": chosen_tbl,
        "sql_group_col": "stratum",
        "sql_count_col": "n_samples",
        "bin_spec": {"source_col": strat_col, "bins": bins},
        "metrics": [{"sql_col": "mean_metric", "source_col": metric_col, "agg": "mean"}],
    }
    if clauses["filters"]:
        rec_block_b["filters"] = clauses["filters"]
    if clauses["order_by"]:
        rec_block_b["order_by"] = clauses["order_by"]
    if clauses["limit"] is not None:
        rec_block_b["limit"] = clauses["limit"]

    return {
        "base_table": chosen_tbl,
        "base_pk": pk,
        "group_col": "stratum",
        "value_col": "mean_metric",
        "title": f"{chosen_tbl}: {metric_col} Stratified by {strat_col}",
        "parsed_task_clauses": clauses,
        "queries": [
            {
                "id": f"stratified_{chosen_tbl}",
                "sql": sql,
                "params": params_s,
                "check_inflation": True,
                "reconcile": rec_block_b,
            }
        ],
    }


def run_auto_sqlite(
    input_dir: Path,
    out_dir: Path,
    *,
    stem: str = "sql_analysis",
    db_override: Path | None = None,
    target_table: str | None = None,
    task_md_override: Path | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    disc = discover_input_dir(input_dir)
    case_json_p = (input_dir / "case.json") if (input_dir / "case.json").is_file() else None
    task_p = task_md_override if (task_md_override and task_md_override.is_file()) else ((input_dir / "task.md") if (input_dir / "task.md").is_file() else None)
    task_text = task_p.read_text(encoding="utf-8-sig") if task_p else ""
    db_path = db_override or (Path(disc["primary_sqlite_candidate"]) if disc.get("primary_sqlite_candidate") else None)
    if db_path is None or not db_path.is_file():
        return {"ok": False, "error": f"No SQLite database discovered under {input_dir}"}

    schema = inspect_sqlite_db(db_path)
    schema_p = out_dir / f"{stem}_schema.json"
    schema_p.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    qspec = _synthesize_safe_query_spec(schema, target_table=target_table, task_text=task_text)
    qspec_p = out_dir / f"{stem}_auto_queries.json"
    qspec_p.write_text(json.dumps(qspec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rep = run_sql_analysis(db_path, qspec, out_dir, stem=stem)
    elapsed = round(time.monotonic() - t0, 4)

    rep_p = out_dir / f"{stem}_report.json"
    arts = [schema_p.name, qspec_p.name, f"{stem}_report.json", f"{stem}_report.md"] + list(rep.get("chart_artifacts") or [])
    for q in rep.get("queries") or []:
        if q.get("csv_artifact"):
            arts.append(q["csv_artifact"])
        if q.get("uncertainty_csv_artifact"):
            arts.append(q["uncertainty_csv_artifact"])

    uncertainty_items: list[str] = []
    gap_items: list[str] = []
    for cw in (schema.get("constraint_loss_warnings") or []):
        uncertainty_items.append(str(cw))
        gap_items.append(f"SCHEMA_CONSTRAINT_WARNING: {cw}")
    for q in (rep.get("queries") or []):
        if q.get("empty_set_classification") == "LEGITIMATE_EMPTY_GROUP":
            uncertainty_items.append(f"Query '{q['id']}' preserved {len(q.get('legitimate_empty_groups') or [])} legitimate empty group(s) with n=0 (NULL mean, non-estimable SD/CI).")
    if not rep.get("matplotlib_rendered"):
        msg = "Matplotlib not installed in offline runtime; rendered editable vector SVG via pure-stdlib backend."
        uncertainty_items.append(msg)
        gap_items.append(f"PLOT_BACKEND_FALLBACK: {msg}")
    for err in (rep.get("errors") or []):
        uncertainty_items.append(str(err))
        gap_items.append(f"EXECUTION_ERROR: {err}")
    if not uncertainty_items:
        uncertainty_items.append("SQLite aggregates and inferential statistics reflect the read-only snapshot scope; observational group contrasts do not imply causality.")

    seal_status = "error" if not rep.get("ok") else ("partial_with_gaps" if gap_items else "ok")
    seal = seal_result_contract(
        case_json_path=case_json_p,
        result_json_path=out_dir / "result.json",
        task_id="challenge-5c56458d",
        status=seal_status,
        payload_path=rep_p if rep_p.is_file() else None,
        artifacts=arts,
        sources=[db_path.name],
        uncertainty=uncertainty_items,
        gaps=gap_items,
    )
    return {
        "ok": bool(rep.get("ok") and seal.get("ok")),
        "status": seal_status,
        "db_file": db_path.name,
        "base_table": qspec["base_table"],
        "db_immutability_verified": rep.get("db_immutability_verified"),
        "gaps_count": len(gap_items),
        "elapsed_seconds": elapsed,
        "result_json": "result.json",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-manual /input -> /app/output SQLite schema & safe CTE analyzer.")
    parser.add_argument("--input-dir", required=True, help="Case input directory (/input).")
    parser.add_argument("--out-dir", required=True, help="Output directory (/app/output).")
    parser.add_argument("--stem", default="sql_analysis", help="Output filename stem.")
    parser.add_argument("--db", default=None, help="Optional explicit SQLite file override.")
    parser.add_argument("--table", default=None, help="Optional target table override (omit for auto star-schema CTE detection).")
    parser.add_argument("--task-md", default=None, help="Optional task.md override for metric column matching.")
    args = parser.parse_args(argv)

    rep = run_auto_sqlite(
        Path(args.input_dir),
        Path(args.out_dir),
        stem=args.stem,
        db_override=Path(args.db) if args.db else None,
        target_table=args.table,
        task_md_override=Path(args.task_md) if args.task_md else None,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
