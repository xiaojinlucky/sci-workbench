#!/usr/bin/env python3
"""Source-data audit, SFL/PlotAgent Figure Data Profile, replicate guard, persistent color maps, and mark provenance."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any

SENTINEL_STRINGS = {"", "na", "n/a", "nan", "null", "none", "nd", "bdl", "."}
SUSPICIOUS_SENTINELS = {888.0, 999.0, -999.0, -9999.0, 9999.0}

# Cell-tendency low-to-medium saturation research palette (colorblind-aware starting tokens)
DEFAULT_CELL_PALETTE = [
    "#547FA3",  # steel blue
    "#D77C70",  # muted coral
    "#4E9187",  # blue-teal
    "#D19A45",  # ochre gold
    "#967A9C",  # muted purple
    "#66A9B4",  # cyan-slate
    "#83A47B",  # sage green
    "#DF986D",  # soft terracotta
]

# Two-tailed 95% Student-t critical values by df (1..30); df > 30 uses 1.95996
_T95_TABLE = {
    1: 12.7062, 2: 4.3027, 3: 3.1824, 4: 2.7764, 5: 2.5706,
    6: 2.4469, 7: 2.3646, 8: 2.3060, 9: 2.2622, 10: 2.2281,
    11: 2.2010, 12: 2.1788, 13: 2.1604, 14: 2.1448, 15: 2.1314,
    16: 2.1199, 17: 2.1098, 18: 2.1009, 19: 2.0930, 20: 2.0860,
    21: 2.0796, 22: 2.0739, 23: 2.0687, 24: 2.0639, 25: 2.0595,
    26: 2.0555, 27: 2.0518, 28: 2.0484, 29: 2.0452, 30: 2.0423,
}


def t_crit_95(n: int) -> float:
    if n < 2:
        return 0.0
    df = n - 1
    return _T95_TABLE.get(df, 1.95996)


def load_tabular(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load CSV, TSV (input only), or JSON list-of-objects table preserving raw strings."""
    ext = path.suffix.lower()
    if ext == ".json":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            cols = list(dict.fromkeys(k for r in raw for k in r.keys()))
            return cols, [{k: "" if r.get(k) is None else str(r.get(k)) for k in cols} for r in raw]
        if isinstance(raw, dict) and isinstance(raw.get("rows"), list):
            rows_raw = raw["rows"]
            cols = list(raw.get("columns") or dict.fromkeys(k for r in rows_raw for k in r.keys()))
            return cols, [{k: "" if r.get(k) is None else str(r.get(k)) for k in cols} for r in rows_raw]
        raise ValueError(f"JSON table must be a non-empty list of objects: {path}")
    delim = "\t" if ext == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delim)
        cols = list(reader.fieldnames or [])
        rows = [{str(k): ("" if v is None else str(v)) for k, v in r.items() if k is not None} for r in reader]
    return cols, rows


def to_float(val: str) -> float | None:
    s = str(val).strip()
    if s.lower() in SENTINEL_STRINGS:
        return None
    try:
        x = float(s)
        if math.isfinite(x):
            return x
    except ValueError:
        return None
    return None


def load_or_update_style_map(
    style_path: Path | None,
    domain: str,
    entities: list[str],
    *,
    palette: list[str] | None = None,
    explicit_colors: dict[str, str] | None = None,
) -> dict[str, str]:
    """Load persistent named entity colors from figure_style.json without reshuffling existing keys."""
    pal = list(palette or DEFAULT_CELL_PALETTE)
    data: dict[str, Any] = {
        "style_id": "cell_publication_v1",
        "named_colors": {},
        "category_order": {},
    }
    if style_path is not None and style_path.is_file():
        try:
            loaded = json.loads(style_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass

    named: dict[str, str] = dict(data.get("named_colors") or {})
    order_map: dict[str, list[str]] = dict(data.get("category_order") or {})
    used_colors = {v.upper() for v in named.values() if isinstance(v, str)}
    assigned: dict[str, str] = {}

    for ent in entities:
        key = f"{domain}/{ent}"
        if explicit_colors and ent in explicit_colors:
            named[key] = explicit_colors[ent]
            used_colors.add(explicit_colors[ent].upper())
            assigned[ent] = explicit_colors[ent]
            continue
        if key in named:
            assigned[ent] = named[key]
            continue
        chosen = next((c for c in pal if c.upper() not in used_colors), None)
        if chosen is None:
            chosen = pal[len(named) % len(pal)]
        named[key] = chosen
        used_colors.add(chosen.upper())
        assigned[ent] = chosen

    existing_order = list(order_map.get(domain) or [])
    for ent in entities:
        if ent not in existing_order:
            existing_order.append(ent)
    order_map[domain] = existing_order
    data["named_colors"] = named
    data["category_order"] = order_map

    if style_path is not None:
        style_path.parent.mkdir(parents=True, exist_ok=True)
        style_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return assigned


def audit_source_data(
    table_path: Path,
    *,
    group_col: str,
    value_col: str,
    series_col: str = "",
    unit_col: str = "",
    chart_type: str = "bar_with_points",
    show_points: bool = True,
    show_error_bars: bool = True,
    allow_summary_only: bool = False,
    fail_on_sentinel: bool = False,
    statistical_unit: str = "biological_replicate",
) -> dict[str, Any]:
    """Compute exact per-group statistics, Figure Data Profile, 5-Gate checks, and mark provenance."""
    cols, rows = load_tabular(table_path)
    errors: list[str] = []
    warnings: list[str] = []
    gaps: list[dict[str, str]] = []

    if group_col not in cols:
        errors.append(f"Missing group column {group_col!r}; available={cols}")
    if value_col not in cols:
        errors.append(f"Missing value column {value_col!r}; available={cols}")
    if series_col and series_col not in cols:
        errors.append(f"Missing series column {series_col!r}; available={cols}")
    if unit_col and unit_col not in cols:
        errors.append(f"Missing unit column {unit_col!r}; available={cols}")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "gaps": gaps, "groups": []}

    grouped: dict[tuple[str, str], list[tuple[int, float]]] = {}
    missing_rows: list[int] = []
    sentinel_hits: list[tuple[int, float]] = []
    explicit_zeros: list[int] = []
    all_numeric_vals: list[float] = []
    units_seen: set[str] = set()

    for idx, row in enumerate(rows, start=2):  # 1-based CSV row with header=1
        gval = row.get(group_col, "").strip()
        sval = row.get(series_col, "").strip() if series_col else "default"
        if unit_col:
            uval = row.get(unit_col, "").strip()
            if uval:
                units_seen.add(uval)
        raw_num = row.get(value_col, "")
        num = to_float(raw_num)
        if num is None:
            missing_rows.append(idx)
            continue
        if num in SUSPICIOUS_SENTINELS:
            sentinel_hits.append((idx, num))
        if num == 0.0:
            explicit_zeros.append(idx)
        all_numeric_vals.append(num)
        grouped.setdefault((gval, sval), []).append((idx, num))

    if len(units_seen) > 1:
        errors.append(
            f"MIXED_UNIT_BLOCKER: Column {unit_col!r} mixes incompatible units {sorted(units_seen)}; normalize before plotting."
        )

    if missing_rows:
        gaps.append({
            "code": "MISSING_NUMERIC_VALUES",
            "severity": "warning",
            "message": f"Excluded {len(missing_rows)} missing/NA rows in {value_col!r} (lines {missing_rows[:8]}); never imputed as zero.",
        })
    if sentinel_hits:
        msg_s = f"Potential sentinel codes detected in {value_col!r}: {sentinel_hits[:5]}"
        if fail_on_sentinel:
            errors.append(f"SENTINEL_CODE_BLOCKER: {msg_s}")
        else:
            warnings.append(msg_s)

    summary_groups: list[dict[str, Any]] = []
    for (gval, sval), pts in grouped.items():
        vals = [v for _, v in pts]
        row_nums = [r for r, _ in pts]
        n = len(vals)
        mean_v = statistics.fmean(vals)
        sd_v = statistics.stdev(vals) if n >= 2 else 0.0
        sem_v = sd_v / math.sqrt(n) if n >= 2 else 0.0
        ci95_v = t_crit_95(n) * sem_v if n >= 2 else 0.0
        med_v = statistics.median(vals)
        q1_v = float(statistics.quantiles(vals, n=4)[0]) if n >= 4 else min(vals)
        q3_v = float(statistics.quantiles(vals, n=4)[2]) if n >= 4 else max(vals)
        summary_groups.append({
            "group": gval,
            "series": sval,
            "n": n,
            "mean": round(mean_v, 6),
            "sd": round(sd_v, 6),
            "sem": round(sem_v, 6),
            "ci95": round(ci95_v, 6),
            "median": round(med_v, 6),
            "q1": round(q1_v, 6),
            "q3": round(q3_v, 6),
            "min": round(min(vals), 6),
            "max": round(max(vals), 6),
            "has_negative": any(v < 0.0 for v in vals),
            "explicit_zero_count": sum(1 for v in vals if v == 0.0),
            "raw_values": vals,
            "provenance": {
                "source_file": table_path.name,
                "group_col": group_col,
                "series_col": series_col or None,
                "value_col": value_col,
                "csv_lines": row_nums,
                "statistical_unit": statistical_unit,
                "aggregation": "mean +- error with raw points" if show_points else "summary only",
            },
        })

    if not summary_groups:
        errors.append(f"No valid numeric rows parsed for {value_col!r}")

    # Anti-lazy-mean guard: block bar/line summary hiding dispersion/n for replicate tables
    is_summary_bar = "bar" in chart_type.lower()
    max_n = max((g["n"] for g in summary_groups), default=0)
    min_n = min((g["n"] for g in summary_groups), default=0)
    has_negative = any(v < 0.0 for v in all_numeric_vals)
    if is_summary_bar and not allow_summary_only:
        if max_n >= 3 and (not show_points or not show_error_bars):
            errors.append(
                f"AI_LAZY_MEAN_ONLY_BLOCKER: Replicate table has n={max_n}; must overlay individual points and SD/SEM/CI95 error bars instead of naked mean bars."
            )

    figure_data_profile = {
        "statistical_unit": statistical_unit,
        "group_count": len(summary_groups),
        "n_range": [min_n, max_n],
        "has_negative_values": has_negative,
        "global_min": round(min(all_numeric_vals), 6) if all_numeric_vals else None,
        "global_max": round(max(all_numeric_vals), 6) if all_numeric_vals else None,
        "explicit_zero_rows": len(explicit_zeros),
        "missing_rows_excluded": len(missing_rows),
        "sentinel_hit_count": len(sentinel_hits),
        "units_detected": sorted(units_seen),
        "sfl_five_gates": {
            "statistical_unit": "ACCEPT" if statistical_unit != "pooled_cells" else "REJECT",
            "axis_semantics": "ACCEPT_SIGNED_ZERO_LINE" if has_negative else "ACCEPT_ZERO_BASELINE",
            "evidence_visibility": "ACCEPT" if (show_points or max_n < 3 or allow_summary_only) else "REJECT",
            "scale_uncertainty": "ACCEPT" if (show_error_bars or max_n < 2 or allow_summary_only) else "PASS_WITH_REPAIR",
            "visual_encoding": "ACCEPT",
        },
    }

    return {
        "ok": len(errors) == 0,
        "source_file": table_path.name,
        "row_count_total": len(rows),
        "group_count": len(summary_groups),
        "figure_data_profile": figure_data_profile,
        "errors": errors,
        "warnings": warnings,
        "gaps": gaps,
        "groups": summary_groups,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit source table, generate Figure Data Profile & mark-to-row provenance.")
    parser.add_argument("--table", required=True, help="CSV/JSON source data table")
    parser.add_argument("--group-col", required=True)
    parser.add_argument("--value-col", required=True)
    parser.add_argument("--series-col", default="")
    parser.add_argument("--unit-col", default="")
    parser.add_argument("--chart-type", default="bar_with_points")
    parser.add_argument("--statistical-unit", default="biological_replicate")
    parser.add_argument("--no-points", action="store_true")
    parser.add_argument("--no-error-bars", action="store_true")
    parser.add_argument("--allow-summary-only", action="store_true")
    parser.add_argument("--fail-on-sentinel", action="store_true")
    parser.add_argument("--out", default="", help="Optional provenance JSON output path")
    args = parser.parse_args(argv)

    rep = audit_source_data(
        Path(args.table),
        group_col=args.group_col,
        value_col=args.value_col,
        series_col=args.series_col,
        unit_col=args.unit_col,
        chart_type=args.chart_type,
        show_points=not args.no_points,
        show_error_bars=not args.no_error_bars,
        allow_summary_only=args.allow_summary_only,
        fail_on_sentinel=args.fail_on_sentinel,
        statistical_unit=args.statistical_unit,
    )
    encoded = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
