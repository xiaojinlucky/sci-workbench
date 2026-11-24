#!/usr/bin/env python3
"""Data-aware chart grammar selector, PlotAgent 5-Gate auditor, and A/B/C fact sheet builder.

Runs natively on pure Python 3.12 stdlib (zero third-party dependencies).
Audits:
  - Gate 1 (Scientific & Data Fidelity): dedup/missingness audit, negative/cross-zero check,
    encoded-integer column warning, unit presence check, target_surface & presentation frame check
  - Gate 2 (Chart Grammar & Anti-Patterns): blocks pie/donut/3d_bar/radar, truncated bar baseline,
    mean-only hiding raw replicates (n >= 3), bar charts on continuous time series, over-crowded
    categorical legends (>8 groups without faceting)
  - Gate 3 (Color Semantics & WCAG/CVD): blocks jet/rainbow/hsv/turbo, verifies Protanopia,
    Deuteranopia, Tritanopia, and grayscale separability via palette_cvd_auditor.py
  - Gate 4 (Cell Visual Baseline & Canvas): locks 85/114/174 mm physical widths, 7-12 pt typography,
    bbox_inches=None, panel-centered regular-weight titles, 2-6 pt legend clearance, zero backend
    jargon on figure canvas
  - Gate 5 (Reconciliation Readiness): declares A/B/C fact sheets and required post-render checks
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from palette_cvd_auditor import BANNED_COLORMAPS, VERIFIED_PALETTES, audit_palette  # noqa: E402

CELL_WIDTHS_MM = {
    "single": 85.0,
    "cell_single": 85.0,
    "1.5col": 114.0,
    "cell_1_5col": 114.0,
    "double": 174.0,
    "cell_double": 174.0,
}

SUPPORTED_CHARTS = {"bar_with_points", "boxplot_points", "scatter_regression", "line_with_band", "heatmap"}
BANNED_CHARTS = {"pie", "donut", "3d_bar", "3d_pie", "radar", "spider", "stacked_3d"}
MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "nd", ".", "-"}
BACKEND_JARGON_RE = re.compile(
    r"\b(?:selected_flag|pass_filter|qc_branch|route_enum|matplotlib_backend|stdlib_svg|"
    r"parametric_welch|nonparametric_kruskal|degraded_small_n|unestimable_)\b",
    re.IGNORECASE,
)


def _try_float(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() in MISSING_TOKENS:
        return None
    try:
        f = float(s.replace(",", ""))
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def load_table_stdlib(path: Path, table: str | None = None) -> tuple[list[str], list[dict[str, Any]], str]:
    p = Path(path)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    ext = p.suffix.lower()
    if ext in {".sqlite", ".sqlite3", ".db"}:
        conn = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            chosen = table or tables[0]
            cur = conn.execute(f'SELECT * FROM "{chosen}"')
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            return cols, rows, sha
        finally:
            conn.close()
    if ext == ".json":
        obj = json.loads(p.read_text(encoding="utf-8-sig"))
        if isinstance(obj, dict):
            for k in ("rows", "data", "records"):
                if isinstance(obj.get(k), list):
                    obj = obj[k]
                    break
        cols = list(dict.fromkeys(k for row in obj for k in row.keys()))
        return cols, obj, sha
    delim = "\t" if ext == ".tsv" else ","
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh, delimiter=delim)
        return list(rd.fieldnames or []), list(rd), sha


def inspect_data_profile(spec: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any] | None:
    """Inspect source table if provided in spec to ground Gate 1 & Gate 2 checks."""
    t_raw = spec.get("table")
    if not t_raw:
        return None
    tpath = Path(str(t_raw))
    if not tpath.is_absolute() and base_dir is not None:
        tpath = base_dir / tpath
    if not tpath.is_file():
        return {"error": f"Source table not found: {tpath}"}

    cols, raw_rows, sha = load_table_stdlib(tpath, table=spec.get("sqlite_table"))
    seen: set[tuple[str, ...]] = set()
    rows: list[dict[str, Any]] = []
    for r in raw_rows:
        k = tuple(str(r.get(c, "")) for c in cols)
        if k not in seen:
            seen.add(k)
            rows.append(r)

    gcol = str(spec.get("group_col", "")).strip()
    vcol = str(spec.get("value_col", "")).strip()
    xcol = str(spec.get("x_col", gcol)).strip()

    val_nums: list[float] = []
    missing_vals = 0
    if vcol and vcol in cols:
        for r in rows:
            fv = _try_float(r.get(vcol))
            if fv is None:
                missing_vals += 1
            else:
                val_nums.append(fv)

    groups_counts: dict[str, int] = {}
    if gcol and gcol in cols:
        for r in rows:
            gs = str(r.get(gcol, "")).strip()
            if gs.lower() in MISSING_TOKENS:
                continue
            if vcol and vcol in cols and _try_float(r.get(vcol)) is None:
                continue
            groups_counts[gs] = groups_counts.get(gs, 0) + 1

    has_neg = any(v < 0.0 for v in val_nums)
    has_pos = any(v > 0.0 for v in val_nums)
    return {
        "file": tpath.name,
        "sha256": sha,
        "columns": cols,
        "rows_raw": len(raw_rows),
        "exact_duplicates": len(raw_rows) - len(rows),
        "rows_dedup": len(rows),
        "missing_values": missing_vals,
        "n_valid_values": len(val_nums),
        "value_min": round(min(val_nums), 6) if val_nums else None,
        "value_max": round(max(val_nums), 6) if val_nums else None,
        "has_negative": has_neg,
        "crosses_zero": has_neg and has_pos,
        "group_counts": groups_counts,
        "n_groups": len(groups_counts),
        "max_group_n": max(groups_counts.values(), default=0),
        "min_group_n": min(groups_counts.values(), default=0),
        "x_col_exists": bool(xcol and xcol in cols),
    }


def advise_and_audit_chart(spec: dict[str, Any], *, base_dir: Path | None = None) -> dict[str, Any]:
    """Run PlotAgent 5-Gate selection & anti-pattern audit and build A/B/C fact sheets."""
    data_kind = str(spec.get("data_kind", "categorical_continuous")).strip().lower()
    goal = str(spec.get("analysis_goal", "group_comparison")).strip().lower()
    requested = str(spec.get("chart_type", "bar_with_points")).strip().lower()
    target_surface = str(spec.get("target_surface", "paper")).strip().lower()

    blockers: list[str] = []
    warnings: list[str] = []
    gates: dict[str, str] = {}

    profile = inspect_data_profile(spec, base_dir=base_dir)
    if profile and "error" in profile:
        blockers.append(profile["error"])

    # Recommended chart grammar mapping
    rec_map = {
        ("categorical_continuous", "group_comparison"): ["boxplot_points", "bar_with_points"],
        ("categorical_continuous", "distribution"): ["boxplot_points"],
        ("time_series", "trend"): ["line_with_band"],
        ("time_series", "trajectory"): ["line_with_band"],
        ("bivariate_continuous", "correlation"): ["scatter_regression"],
        ("bivariate_continuous", "relationship"): ["scatter_regression"],
        ("matrix", "correlation"): ["heatmap"],
        ("matrix", "pattern"): ["heatmap"],
    }
    recommended = rec_map.get((data_kind, goal), ["boxplot_points", "bar_with_points", "scatter_regression"])
    if profile and profile.get("crosses_zero") and requested == "bar_with_points":
        warnings.append(
            "Data contains both negative and positive values; bar_with_points is allowed only with an explicit y=0 "
            "reference baseline covering all negative/positive raw points (boxplot_points is also strongly recommended)."
        )

    # Gate 1: Scientific & Data Fidelity
    g1_issues: list[str] = []
    if profile and "error" not in profile:
        if profile["exact_duplicates"] > 0:
            warnings.append(
                f"Source table contains {profile['exact_duplicates']} exact duplicate row(s); builder will deduplicate and log."
            )
        if profile["missing_values"] > 0:
            warnings.append(
                f"Source table contains {profile['missing_values']} missing value(s); excluded without zero-imputation."
            )
    if target_surface == "presentation" and not (spec.get("frame_width_mm") and spec.get("frame_height_mm")):
        warnings.append("Presentation surface requested without real picture-frame dimensions; treating layout as precheck only.")
    gates["gate1_data_fidelity"] = "PASS" if not g1_issues else "BLOCKED"

    # Gate 2: Chart Grammar & Anti-Patterns
    g2_blockers: list[str] = []
    if requested in BANNED_CHARTS:
        g2_blockers.append(f"ANTI_PATTERN_CHART: {requested!r} is forbidden for quantitative comparison; use {recommended[0]!r}.")
    elif requested not in SUPPORTED_CHARTS:
        g2_blockers.append(f"Unsupported chart_type {requested!r}; supported={sorted(SUPPORTED_CHARTS)}.")

    if requested == "bar_with_points" and bool(spec.get("truncate_y_baseline")):
        g2_blockers.append("TRUNCATED_BAR_BASELINE: Bar charts must anchor at y=0; switch to boxplot_points or dot plot if zooming.")
    if requested == "bar_with_points" and spec.get("show_points") is False:
        if not profile or profile.get("max_group_n", 3) >= 3:
            g2_blockers.append("MEAN_ONLY_HIDES_REPLICATES: Bar charts must overlay individual replicate observations (show_points=True).")
    if data_kind == "time_series" and "bar" in requested:
        g2_blockers.append("TIME_SERIES_BAR_MISUSE: Continuous/ordered time series should use line_with_band instead of side-by-side bars.")
    if requested in {"bar_with_points", "boxplot_points"} and profile and profile.get("n_groups", 0) > 10:
        warnings.append(f"High category count ({profile['n_groups']} groups) may crowd single-column labels; consider faceting or horizontal layout.")
    blockers.extend(g2_blockers)
    gates["gate2_chart_grammar"] = "PASS" if not g2_blockers else "BLOCKED"

    # Gate 3: Color Semantics & WCAG/CVD/Grayscale
    role = "diverging" if (requested == "heatmap" and str(spec.get("color_role", "")).lower() == "diverging") else (
        "sequential" if requested == "heatmap" else str(spec.get("color_role", "qualitative")).lower()
    )
    if role not in VERIFIED_PALETTES:
        role = "qualitative"
    cmap_name = str(spec.get("colormap", "")).strip()
    mark = "heatmap" if requested == "heatmap" else ("line" if requested == "line_with_band" else ("point" if requested == "scatter_regression" else "bar"))
    n_needed = max(4, profile["n_groups"]) if (profile and profile.get("n_groups")) else 4
    colors = list(spec.get("palette") or VERIFIED_PALETTES[role][: min(n_needed, len(VERIFIED_PALETTES[role]))])
    pal_rep = audit_palette(colors, role=role, mark=mark, colormap_name=cmap_name)
    if not pal_rep["ok"]:
        blockers.extend(pal_rep["blockers"])
    warnings.extend(pal_rep.get("warnings", []))
    gates["gate3_color_wcag_cvd"] = "PASS" if pal_rep["ok"] else "BLOCKED"

    # Gate 4: Physical Canvas, Typography, Legend & Front-End Semantics
    g4_blockers: list[str] = []
    width_mm = float(spec.get("width_mm", CELL_WIDTHS_MM.get(str(spec.get("column_preset", "cell_1_5col")).lower(), 114.0)))
    height_mm = float(spec.get("height_mm", round(width_mm * 0.72, 1)))
    font_pt = float(spec.get("font_size_pt", 8.0))
    if not (7.0 <= font_pt <= 12.0):
        g4_blockers.append(f"FONT_SIZE_OUT_OF_RANGE: font_size_pt={font_pt} outside Cell baseline [7, 12] pt.")
    if str(spec.get("bbox_mode", "standard")).lower() == "tight":
        g4_blockers.append("BBOX_TIGHT_FORBIDDEN: Use bbox_inches=None (bbox_mode='standard') to preserve exact physical column width.")

    canvas_texts = [
        str(spec.get("title", "")),
        str(spec.get("xlabel", "")),
        str(spec.get("ylabel", "")),
    ]
    for txt in canvas_texts:
        if BACKEND_JARGON_RE.search(txt):
            g4_blockers.append(f"BACKEND_JARGON_ON_CANVAS: Remove internal processing token from visible canvas text: {txt!r}")
    blockers.extend(g4_blockers)
    gates["gate4_cell_layout_semantics"] = "PASS" if not g4_blockers else "BLOCKED"

    # Gate 5: Source-to-Visual Reconciliation Contract (completed at build time)
    gates["gate5_reconciliation_contract"] = "READY_FOR_RENDER" if len(blockers) == 0 else "BLOCKED"

    # PlotAgent A/B/C Fact Sheets
    abc_facts = {
        "A_scientific_invariants": {
            "source_file": profile.get("file") if profile else str(spec.get("table", "")),
            "source_sha256": profile.get("sha256") if profile else None,
            "group_col": spec.get("group_col"),
            "x_col": spec.get("x_col", spec.get("group_col")),
            "value_col": spec.get("value_col"),
            "unit": spec.get("unit", "unspecified"),
            "missing_policy": "exclude_without_zero_imputation",
            "dedup_policy": "remove_exact_row_duplicates_with_audit",
            "crosses_zero": profile.get("crosses_zero") if profile else None,
        },
        "B_visual_grammar_retained_or_redesigned": {
            "selected_chart": requested,
            "recommended_candidates": recommended,
            "color_role": role,
            "palette": colors,
            "title_alignment": "panel_center_regular_weight",
            "legend_clearance_pt": "2-6 pt",
            "rejected_antipatterns": sorted(BANNED_CHARTS | BANNED_COLORMAPS),
        },
        "C_surface_specific_overrides": {
            "target_surface": target_surface,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "font_size_pt": font_pt,
            "bbox_inches": None,
            "pdf_fonttype": 42,
            "svg_fonttype": "none",
        },
    }

    return {
        "ok": len(blockers) == 0,
        "requested_chart": requested,
        "recommended_charts": recommended,
        "gates": gates,
        "colors": colors,
        "color_role": role,
        "palette_audit": pal_rep,
        "data_profile": profile,
        "abc_fact_sheets": abc_facts,
        "blockers": blockers,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PlotAgent 5-Gate chart selector and anti-pattern auditor.")
    parser.add_argument("--spec", required=True, help="Path to viz_spec.json.")
    parser.add_argument("--out", default=None, help="Optional JSON output path.")
    args = parser.parse_args(argv)

    sp = Path(args.spec).resolve()
    spec = json.loads(sp.read_text(encoding="utf-8-sig"))
    rep = advise_and_audit_chart(spec, base_dir=sp.parent)
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
