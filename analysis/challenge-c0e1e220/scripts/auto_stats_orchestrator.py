#!/usr/bin/env python3
"""Zero-manual /input -> /app/output statistical analysis orchestrator (challenge-c0e1e220).

Auto-discovers CSV/JSON/SQLite tables, case.json, and task.md under --input-dir,
infers the optimal statistical study design (independent multi-group vs continuous
multivariable OLS regression vs contingency), runs assumption diagnostics + inference
+ effect sizes + 95% CIs + SVG/PNG plot + linear reproduce_stats.py, and seals result.json.
Pure Python 3.12 stdlib (automatically enhanced by SciPy/Matplotlib when present).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from assumption_diagnostics import check_assumptions, detect_encoded_integer_columns, load_table_ro, try_parse_float
from case_contract_sealer import discover_input_dir, seal_result_contract
from inference_effect_ci_runner import run_statistical_report
from ols_regression_engine import run_ols_analysis

GROUP_HINTS = ("species", "cultivar", "group", "treatment", "cohort", "condition", "class", "category", "arm", "stratum")
STRATIFY_HINTS = ("age", "time", "day", "week", "month", "stage", "batch", "phase")


def _select_sqlite_fact_table(db_path: Path, task_text: str) -> str:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        if not tables:
            raise ValueError(f"SQLite database {db_path.name} has zero user tables")
        low_task = task_text.lower()
        # Rank all tables by row count; if task.md mentions tables, prefer the largest mentioned fact table
        ranked = []
        for t in tables:
            cnt = int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
            ranked.append((1 if t.lower() in low_task else 0, cnt, t))
        ranked.sort(reverse=True)
        return ranked[0][2]
    finally:
        conn.close()


def _infer_design_and_columns(
    cols: list[str],
    rows: list[dict[str, Any]],
    task_text: str,
) -> dict[str, Any]:
    low_task = task_text.lower()
    coded_ints = set(detect_encoded_integer_columns(cols, rows).keys())

    # Classify columns
    numeric_cols: list[str] = []
    cat_cols: list[dict[str, Any]] = []
    for c in cols:
        vals = [str(r.get(c, "")).strip() for r in rows if str(r.get(c, "")).strip() not in {"", "NA", "na", "null", "None"}]
        if not vals:
            continue
        uniq = list(dict.fromkeys(vals))
        has_lz = any(len(v) > 1 and v.startswith("0") and v.isdigit() for v in vals)
        parsed = [try_parse_float(v) for v in vals]
        is_num = (not has_lz) and all(v is not None for v in parsed)
        if is_num and c not in coded_ints and not c.lower().endswith("_id") and c.lower() != "mix_id":
            numeric_cols.append(c)
        elif (not has_lz) and (not c.lower().endswith("_id")) and 2 <= len(uniq) <= 12:
            cat_cols.append({"col": c, "nunique": len(uniq), "is_coded_int": c in coded_ints})

    # Prefer columns explicitly named in task.md with source_ref line tracking
    task_lines = task_text.splitlines()
    source_refs: dict[str, str] = {}
    for c in cols:
        for lno, ln in enumerate(task_lines, 1):
            if c.lower() in ln.lower():
                source_refs[c] = f"task.md#L{lno}"
                break

    named_cats = [d["col"] for d in cat_cols if not d["is_coded_int"]]
    hinted_cats = [d["col"] for d in cat_cols if any(h in d["col"].lower() for h in GROUP_HINTS)]

    # Pick outcome y: prefer column mentioned in task.md, else last continuous column
    mentioned_nums = [c for c in numeric_cols if c in source_refs]
    target_y = mentioned_nums[-1] if mentioned_nums else (numeric_cols[-1] if numeric_cols else None)

    if (named_cats or hinted_cats) and target_y:
        # Prefer mentioned group col, then hinted named cat, then first named/hinted cat
        mentioned_cats = [c for c in (named_cats + hinted_cats) if c in source_refs]
        chosen_g = mentioned_cats[0] if mentioned_cats else (named_cats[0] if named_cats else hinted_cats[0])
        return {
            "design": "independent",
            "group_col": chosen_g,
            "value_col": target_y,
            "x_cols": [],
            "stratify_col": None,
            "source_refs": source_refs,
        }

    if len(numeric_cols) >= 2 and target_y:
        # Respect explicit predictor subset named in task.md when >= 2 predictors are specified
        mentioned_x = [c for c in numeric_cols if c != target_y and c in source_refs]
        x_cols = mentioned_x if len(mentioned_x) >= 2 else [c for c in numeric_cols if c != target_y]
        strat = next((c for c in x_cols if any(h in c.lower() for h in STRATIFY_HINTS)), None)
        return {
            "design": "ols_regression",
            "group_col": None,
            "value_col": target_y,
            "x_cols": x_cols,
            "all_numeric_predictors": [c for c in numeric_cols if c != target_y],
            "predictor_scope": "task_md_specified" if len(mentioned_x) >= 2 else "all_numeric_fallback",
            "stratify_col": strat,
            "source_refs": source_refs,
        }

    if len(cat_cols) >= 2:
        return {
            "design": "contingency",
            "group_col": cat_cols[0]["col"],
            "value_col": cat_cols[1]["col"],
            "x_cols": [],
            "stratify_col": None,
            "source_refs": source_refs,
        }

    raise ValueError(f"Could not infer valid statistical design from columns {cols}")


def run_auto_stats(
    input_dir: Path,
    out_dir: Path,
    *,
    stem: str = "stats_report",
    input_file: Path | None = None,
    table: str | None = None,
    design_override: str | None = None,
    group_col: str | None = None,
    value_col: str | None = None,
    unit: str = "",
) -> dict[str, Any]:
    t0 = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    disc = discover_input_dir(input_dir)
    task_p = input_dir / "task.md"
    task_text = task_p.read_text(encoding="utf-8-sig") if task_p.is_file() else ""
    case_json_p = (input_dir / "case.json") if (input_dir / "case.json").is_file() else None

    target_path = input_file or (Path(disc["primary_table_candidate"]) if disc.get("primary_table_candidate") else None)
    if target_path is None or not target_path.is_file():
        return {"ok": False, "error": f"No CSV/JSON/SQLite data input found in {input_dir}"}

    chosen_table = table
    if target_path.suffix.lower() in {".sqlite", ".sqlite3", ".db"} and not chosen_table:
        chosen_table = _select_sqlite_fact_table(target_path, task_text)

    cols, rows, _meta = load_table_ro(target_path, table=chosen_table)
    inferred = _infer_design_and_columns(cols, rows, task_text)
    design = design_override or inferred["design"]
    eff_group = group_col or inferred.get("group_col")
    eff_val = value_col or inferred.get("value_col")

    # Infer measurement unit from outcome column suffix or task.md when --unit is omitted
    eff_unit = unit
    if not eff_unit and eff_val:
        low_v = str(eff_val).lower()
        for suf, ustr in (("_mpa", "MPa"), ("_cm", "cm"), ("_mm", "mm"), ("_mg_l", "mg/L"), ("_kg_m3", "kg/m^3"), ("_pct", "%"), ("_days", "days"), ("_s", "s")):
            if low_v.endswith(suf):
                eff_unit = ustr
                break

    # Unconditionally audit secondary tables under /input (e.g. QC / adversarial fixtures)
    secondary_audits: list[dict[str, Any]] = []
    for tinfo in (disc.get("tables") or []):
        tp = Path(tinfo["abs"])
        if tp.resolve() == target_path.resolve():
            continue
        try:
            s_cols, s_rows, _ = load_table_ro(tp)
            s_inf = _infer_design_and_columns(s_cols, s_rows, "")
            if s_inf["design"] == "independent" and s_inf.get("group_col") and s_inf.get("value_col"):
                s_diag = check_assumptions(tp, design="independent", group_col=s_inf["group_col"], value_col=s_inf["value_col"])
                secondary_audits.append({
                    "file": tp.name,
                    "rows_raw": len(s_rows),
                    "group_col": s_inf["group_col"],
                    "value_col": s_inf["value_col"],
                    "exact_duplicate_rows": s_diag.get("data_integrity", {}).get("exact_duplicate_rows"),
                    "missing_value_rows": s_diag.get("data_integrity", {}).get("missing_value_rows"),
                    "degenerate_groups": s_diag.get("group_diagnostics", {}).get("degenerate_groups", []),
                    "recommended_test": s_diag.get("recommended_route", {}).get("primary_test"),
                    "warnings": s_diag.get("warnings", []),
                })
        except Exception as exc:
            secondary_audits.append({"file": tp.name, "audit_error": str(exc)})

    uncertainty_all: list[str] = []
    gaps_all: list[str] = []

    if design == "ols_regression":
        res = run_ols_analysis(
            target_path,
            y_col=str(eff_val),
            x_cols=inferred.get("x_cols") or [],
            out_dir=out_dir,
            stem=stem,
            table=chosen_table,
            stratify_col=inferred.get("stratify_col"),
            unit=eff_unit,
        )
        payload_p = out_dir / f"{stem}.json"
        arts = [f"{stem}.json", f"{stem}.md", f"{stem}_ols_plot.svg"]
        uncertainty_all.extend(res.get("uncertainty") or [])
        gaps_all.extend(res.get("gaps") or [])
    else:
        diag = check_assumptions(
            target_path,
            design=design,
            group_col=eff_group,
            value_col=eff_val,
            table=chosen_table,
        )
        diag_p = out_dir / f"{stem}_assumptions.json"
        diag_p.write_text(json.dumps(diag, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        res = run_statistical_report(
            target_path,
            design=design,
            group_col=eff_group,
            value_col=eff_val,
            unit=eff_unit,
            out_dir=out_dir,
            stem=stem,
            table=chosen_table,
        )
        payload_p = out_dir / f"{stem}.json"
        arts = [f"{stem}_assumptions.json", f"{stem}.json", f"{stem}.md", res.get("reproduce_script", "reproduce_stats.py")] + list(res.get("charts") or [])
        for w in (diag.get("warnings") or []):
            uncertainty_all.append(str(w))
            gaps_all.append(f"DIAGNOSTIC_WARNING: {w}")

    if not eff_unit:
        gaps_all.append("UNIT_UNSPECIFIED: Outcome measurement unit not declared in column name or --unit.")
    if secondary_audits:
        for sa in secondary_audits:
            if sa.get("warnings"):
                uncertainty_all.append(f"Secondary table {sa['file']} flagged {len(sa['warnings'])} QC/assumption warning(s).")

    elapsed = round(time.monotonic() - t0, 4)
    if payload_p.is_file():
        pobj = json.loads(payload_p.read_text(encoding="utf-8"))
        pobj["auto_routing"] = {
            "inferred_design": design,
            "table_used": chosen_table,
            "group_col": eff_group,
            "value_col": eff_val,
            "unit_inferred": eff_unit or "unspecified",
            "source_refs": inferred.get("source_refs") or {},
            "elapsed_seconds": elapsed,
        }
        if secondary_audits:
            pobj["secondary_table_audits"] = secondary_audits
        payload_p.write_text(json.dumps(pobj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Never report status=ok with empty gaps when diagnostic warnings or backend fallbacks occurred
    seal_status = "error" if not res.get("ok") else ("partial_with_gaps" if gaps_all else "ok")
    seal = seal_result_contract(
        case_json_path=case_json_p,
        result_json_path=out_dir / "result.json",
        task_id="challenge-c0e1e220",
        status=seal_status,
        payload_path=payload_p if payload_p.is_file() else None,
        artifacts=[a for a in arts if a],
        sources=[target_path.name] + [sa["file"] for sa in secondary_audits] + (["task.md"] if task_p.is_file() else []),
        uncertainty=uncertainty_all,
        gaps=gaps_all,
    )

    return {
        "ok": bool(res.get("ok") and seal.get("ok")),
        "status": seal_status,
        "design": design,
        "source": target_path.name,
        "table_used": chosen_table,
        "group_col": eff_group,
        "value_col": eff_val,
        "unit": eff_unit or "unspecified",
        "gaps_count": len(gaps_all),
        "secondary_tables_audited": len(secondary_audits),
        "elapsed_seconds": elapsed,
        "result_json": "result.json",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-manual /input -> /app/output statistical analysis orchestrator.")
    parser.add_argument("--input-dir", required=True, help="Case input directory (/input).")
    parser.add_argument("--out-dir", required=True, help="Output directory (/app/output).")
    parser.add_argument("--stem", default="stats_report", help="Output filename stem.")
    parser.add_argument("--input", default=None, help="Optional explicit table/SQLite file override.")
    parser.add_argument("--table", default=None, help="Optional SQLite table override.")
    parser.add_argument("--design", default=None, choices=["independent", "paired", "correlation", "contingency", "ols_regression"])
    parser.add_argument("--group-col", default=None)
    parser.add_argument("--value-col", default=None)
    parser.add_argument("--unit", default="", help="Optional measurement unit (e.g. MPa, mg/L, %%).")
    args = parser.parse_args(argv)

    rep = run_auto_stats(
        Path(args.input_dir),
        Path(args.out_dir),
        stem=args.stem,
        input_file=Path(args.input) if args.input else None,
        table=args.table,
        design_override=args.design,
        group_col=args.group_col,
        value_col=args.value_col,
        unit=args.unit,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
