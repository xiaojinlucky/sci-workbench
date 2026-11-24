#!/usr/bin/env python3
"""Deterministic raw /input -> chart_spec.json drafter & source-to-mark provenance mapper (stdlib-only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from source_data_chart_guard import audit_source_data, load_tabular, to_float


def _detect_columns(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"numeric": [], "categorical": []}
    cols = list(rows[0].keys())
    num_cols: list[str] = []
    cat_cols: list[str] = []
    for c in cols:
        vals = [str(r.get(c, "")).strip() for r in rows[:200] if str(r.get(c, "")).strip() != ""]
        if not vals:
            continue
        parsed = [to_float(v) for v in vals]
        num_ratio = sum(1 for x in parsed if x is not None) / len(vals)
        uniq = len(set(vals))
        if num_ratio >= 0.8 and uniq > 6:
            num_cols.append(c)
        else:
            cat_cols.append(c)
    return {"numeric": num_cols, "categorical": cat_cols, "all": cols}


def draft_chart_spec_from_input(input_dir: Path, *, table_name: str = "") -> dict[str, Any]:
    """Inspect raw /input/ tables + case.json, run 5-Gate audit, and draft a publication-ready chart_spec.json."""
    case_obj: dict[str, Any] = {}
    case_p = input_dir / "case.json"
    if case_p.is_file():
        try:
            case_obj = json.loads(case_p.read_text(encoding="utf-8-sig"))
        except Exception:
            pass

    tables = [p for p in sorted(input_dir.rglob("*")) if p.is_file() and p.suffix.lower() in (".csv", ".json") and p.name != "case.json"]
    if table_name:
        tables = [p for p in tables if p.name == table_name] or tables
    if not tables:
        return {"ok": False, "error": "NO_DATA_TABLE_IN_INPUT", "not_found_in_source": ["data_table"]}

    tbl = tables[0]
    loaded = load_tabular(tbl)
    rows = loaded[1] if (isinstance(loaded, tuple) and len(loaded) >= 2 and isinstance(loaded[1], list)) else loaded
    col_info = _detect_columns(rows)
    cols_lc = {c.lower(): c for c in col_info["all"]}

    # Infer optimal publication chart family from table schema
    if any(k in cols_lc for k in ("log2fc", "logfc")) and any(k in cols_lc for k in ("padj", "fdr", "pvalue", "p_value")):
        chart_type = "scatter_volcano"
        spec: dict[str, Any] = {
            "chart_type": chart_type,
            "table": str(tbl.resolve()),
            "x_col": cols_lc.get("log2fc") or cols_lc.get("logfc"),
            "y_col": cols_lc.get("padj") or cols_lc.get("fdr") or cols_lc.get("pvalue") or cols_lc.get("p_value"),
            "label_col": cols_lc.get("gene") or cols_lc.get("name") or col_info["all"][0],
            "title": str(case_obj.get("title") or f"Differential Expression ({tbl.stem})"),
            "column_preset": "cell_1_5col",
            "panel_label": "A",
        }
        profile: dict[str, Any] = {"mode": "volcano", "rows": len(rows)}
    else:
        group_col = col_info["categorical"][0] if col_info["categorical"] else col_info["all"][0]
        series_col = col_info["categorical"][1] if len(col_info["categorical"]) >= 2 else ""
        # Exclude identifier / index / code columns from quantitative y-axis candidates
        _id_tokens = ("_id", "sample_code", "record_id", "row_id", "subject_id", "index", "code")
        meas_nums = [c for c in col_info["numeric"] if c.lower() != "id" and not any(t in c.lower() for t in _id_tokens)]
        value_col = meas_nums[0] if meas_nums else (col_info["numeric"][0] if col_info["numeric"] else col_info["all"][-1])
        unit_col = cols_lc.get("unit", "")
        prov = audit_source_data(tbl, group_col=group_col, value_col=value_col, series_col=series_col, unit_col=unit_col)
        n_groups = len({g["group"] for g in prov.get("groups", [])})
        min_n = min((g["n"] for g in prov.get("groups", [])), default=1)
        chart_type = "box_with_points" if min_n >= 15 else "bar_with_points"
        preset = "cell_double" if n_groups >= 5 else "cell_1_5col"
        spec = {
            "chart_type": chart_type,
            "table": str(tbl.resolve()),
            "group_col": group_col,
            "value_col": value_col,
            "series_col": series_col,
            "unit_col": unit_col,
            "error_bar_type": "CI95" if min_n >= 5 else "SD",
            "show_points": True,
            "show_error_bars": True,
            "title": str(case_obj.get("title") or f"{value_col} by {group_col}"),
            "xlabel": group_col.replace("_", " ").title(),
            "ylabel": value_col.replace("_", " ").title(),
            "column_preset": preset,
            "panel_label": "A",
            "source_to_mark_map": [
                {
                    "group": g["group"],
                    "series": g["series"],
                    "n": g["n"],
                    "mean": round(g["mean"], 4),
                    "sd": round(g["sd"], 4),
                    "source_ref": f"{tbl.name}:{group_col}={g['group']}/{value_col}",
                }
                for g in prov.get("groups", [])[:12]
            ],
        }
        profile = prov.get("figure_data_profile") or {}

    spec["extraction_provenance"] = {
        "extractor": "draft_chart_spec.py",
        "source_table": tbl.name,
        "rows_profiled": len(rows),
        "columns_detected": col_info,
        "figure_data_profile": profile,
        "case_id": case_obj.get("case_id") or case_obj.get("id") or None,
    }
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministically draft chart_spec.json from raw /input/ data tables.")
    parser.add_argument("--input-dir", required=True, help="Raw input directory containing CSV/JSON data tables")
    parser.add_argument("--table", default="", help="Optional specific filename inside --input-dir")
    parser.add_argument("--out", required=True, help="Output chart_spec.json path")
    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    if not in_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"Input directory not found: {in_dir}"}, indent=2))
        return 2
    spec = draft_chart_spec_from_input(in_dir, table_name=args.table)
    if spec.get("ok") is False:
        print(json.dumps(spec, indent=2, ensure_ascii=False))
        return 1
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_p), "chart_type": spec["chart_type"], "extraction_provenance": spec["extraction_provenance"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
