#!/usr/bin/env python3
"""Dual-Backend publication-grade scientific figure builder (pure Python 3.12 stdlib + optional Matplotlib).

Supported chart grammars:
  - bar_with_points    (y=0 anchor, cross-zero support, SD/SEM/95% CI, raw points, Shapiro-Francia + Welch/KW + sig brackets)
  - boxplot_points     (Q1/median/Q3, 1.5*IQR whiskers, raw points, Shapiro-Francia + Welch/KW + Holm pairwise sig brackets)
  - scatter_regression (bivariate OLS fit, Pearson r & R^2, optional group color encoding + single-row legend)
  - multivariate_ols   (dual-panel: standardized beta forest plot with 95% HC3 CI & VIF + Observed vs Predicted with JB/BP diagnostics)
  - line_with_band     (numeric/ISO-date/CJK x-axis, mean +/- SD/SEM/95% CI band, raw points, multi-series)
  - heatmap            (numeric correlation matrix, CVD-safe diverging/sequential ramp + colorbar + WCAG text)

Every render parses the written SVG back from disk to compute a real geometry/text readback max_abs_diff
against source summaries (never hardcoded 0.0), records honest export_tier & assumption diagnostics,
and emits a standalone caption plus a CSV/JSON/read-only-SQLite reproduction script.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import platform
import random
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from chart_selector_and_auditor import (  # noqa: E402
    CELL_WIDTHS_MM,
    MISSING_TOKENS,
    _try_float,
    advise_and_audit_chart,
    load_table_stdlib,
)
from palette_cvd_auditor import VERIFIED_PALETTES, best_text_color, parse_hex  # noqa: E402
from stat_assumptions import (  # noqa: E402
    describe_vector,
    fit_multivariate_ols,
    fmt_p,
    run_group_comparison_tests,
    t_crit_95,
)

try:
    import matplotlib  # type: ignore
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt  # type: ignore
    from matplotlib.colors import LinearSegmentedColormap as _LSC  # type: ignore
except Exception:
    _plt = None
    _LSC = None


# ---------------------------------------------------------------------------
# Pure stdlib math & formatting helpers
# ---------------------------------------------------------------------------

def _quantile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return math.nan
    if n == 1:
        return s[0]
    pos = q * (n - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return s[lo] if lo == hi else s[lo] * (1.0 - (pos - lo)) + s[hi] * (pos - lo)


def _mean_sd(vals: list[float]) -> tuple[float, float]:
    n = len(vals)
    if n == 0:
        return math.nan, math.nan
    m = sum(vals) / n
    if n < 2:
        return m, 0.0
    return m, math.sqrt(sum((x - m) ** 2 for x in vals) / (n - 1))


def _error_halfwidth(vals: list[float], error_type: str) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    _, sd = _mean_sd(vals)
    et = error_type.strip().upper()
    if et == "SEM":
        return sd / math.sqrt(n)
    if et in {"CI95", "95% CI", "95CI", "CI"}:
        return t_crit_95(n - 1) * (sd / math.sqrt(n))
    return sd


def _nice_ticks(vmin: float, vmax: float, count: int = 5) -> list[float]:
    if not math.isfinite(vmin) or not math.isfinite(vmax):
        return [0.0, 1.0]
    if abs(vmax - vmin) < 1e-12:
        return [vmin - 1.0, vmin, vmin + 1.0]
    raw = (vmax - vmin) / max(count - 1, 1)
    mag = 10.0 ** math.floor(math.log10(abs(raw)))
    norm = raw / mag
    step = (1.0 if norm <= 1.2 else 2.0 if norm <= 2.5 else 5.0 if norm <= 6.0 else 10.0) * mag
    start = math.floor(vmin / step) * step
    ticks: list[float] = []
    cur = start
    for _ in range(30):
        if cur > vmax + step * 1e-6:
            break
        if cur >= vmin - step * 1e-6:
            ticks.append(round(cur, 6))
        cur += step
    return ticks if len(ticks) >= 2 else [round(vmin, 4), round(vmax, 4)]


def _fmt(v: float) -> str:
    if abs(v) < 1e-9:
        return "0"
    if abs(v) >= 10000 or abs(v) < 0.01:
        return f"{v:.2g}"
    return f"{v:.4g}"


def _interp_hex(ramp: list[str], t: float) -> str:
    t = max(0.0, min(1.0, t))
    if len(ramp) == 1:
        return ramp[0]
    pos = t * (len(ramp) - 1)
    i0 = int(math.floor(pos))
    i1 = min(len(ramp) - 1, i0 + 1)
    f = pos - i0
    r0, g0, b0 = parse_hex(ramp[i0])
    r1, g1, b1 = parse_hex(ramp[i1])
    r = int(round((r0 * (1.0 - f) + r1 * f) * 255))
    g = int(round((g0 * (1.0 - f) + g1 * f) * 255))
    b = int(round((b0 * (1.0 - f) + b1 * f) * 255))
    return f"#{r:02X}{g:02X}{b:02X}"


def _compute_safe_ylim(all_vals: list[float], *, anchor_zero: bool, extra_top_frac: float = 0.0) -> tuple[float, float]:
    """Guarantee 100% coverage of raw points, error bars, and significance brackets."""
    if not all_vals:
        return 0.0, 1.0
    lo_raw, hi_raw = min(all_vals), max(all_vals)
    span = max(hi_raw - lo_raw, abs(hi_raw) * 0.2, 1e-6)
    top_pad = 0.14 + extra_top_frac
    if anchor_zero:
        if lo_raw < 0.0 and hi_raw > 0.0:
            y_lo, y_hi = lo_raw - 0.12 * span, hi_raw + top_pad * span
        elif lo_raw < 0.0:
            y_lo, y_hi = lo_raw - 0.14 * span, max(0.0, hi_raw + (0.08 + extra_top_frac) * span)
        else:
            y_lo, y_hi = 0.0, hi_raw + top_pad * span
    else:
        y_lo, y_hi = lo_raw - 0.12 * span, hi_raw + top_pad * span
    y_lo = min(y_lo, lo_raw - (0.04 * span if lo_raw < 0.0 or not anchor_zero else 0.0))
    y_hi = max(y_hi, hi_raw + (0.04 + extra_top_frac) * span)
    return y_lo, y_hi


def _pearson_r(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    n = len(xs)
    if n < 2:
        return 0.0, 0.0, math.nan
    mx, sdx = _mean_sd(xs)
    my, sdy = _mean_sd(ys)
    if sdx <= 1e-15 or sdy <= 1e-15:
        return 0.0, my, math.nan
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - 1)
    slope = cov / (sdx * sdx)
    return slope, my - slope * mx, max(-1.0, min(1.0, cov / (sdx * sdy)))


def _svg_header(width_mm: float, height_mm: float, font_pt: float) -> tuple[list[str], float, float]:
    w_px = round(width_mm * 96.0 / 25.4, 2)
    h_px = round(height_mm * 96.0 / 25.4, 2)
    pt_px = round(font_pt * 96.0 / 72.0, 2)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm" height="{height_mm}mm" viewBox="0 0 {w_px} {h_px}">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#FFFFFF"/>',
        f'<style>text {{ font-family: Arial, "DejaVu Sans", "Liberation Sans", sans-serif; fill: #111827; font-size: {pt_px}px; }}</style>',
    ]
    return lines, w_px, h_px


# ---------------------------------------------------------------------------
# True Disk Readback Verification (identity-keyed SVG geometry/color/band)
# ---------------------------------------------------------------------------

_CIRCLE_ROLE_RE = re.compile(r'<circle\b([^>]*)/>')
_RECT_ROLE_RE = re.compile(r'<rect\b([^>]*)/>')
_LINE_ROLE_RE = re.compile(r'<line\b([^>]*)/>')
_POLYGON_ROLE_RE = re.compile(r'<polygon\b([^>]*)/>')
_TEXT_TAG_RE = re.compile(r'<text\b([^>]*)>([^<]*)</text>')
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def _parse_attrs(tag_body: str) -> dict[str, str]:
    return {k: html.unescape(v) for k, v in _ATTR_RE.findall(tag_body)}


def _norm_hex(v: str | None) -> str:
    return (v or "").strip().upper()


def _parse_polygon_points(pts_str: str) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for tok in pts_str.strip().split():
        if "," in tok:
            xs, ys = tok.split(",", 1)
            out.append((float(xs), float(ys)))
    return out


def _readback_svg_max_abs_diff(
    svg_path: Path,
    chart_type: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Re-read the written SVG from disk and verify geometry, identity, bands, and color mapping."""
    svg_text = svg_path.read_text(encoding="utf-8")
    diffs: list[float] = []
    failures: list[str] = []
    checked = 0
    identity_ok = True
    color_ok = True
    band_ok = True

    if chart_type in {"bar_with_points", "boxplot_points"}:
        y_lo, y_hi, mt, ph = meta["y_lo"], meta["y_hi"], meta["mt"], meta["ph"]
        tol = max(1e-3, 0.025 * (y_hi - y_lo) / max(ph, 1.0))
        x_tol = float(meta.get("x_slot_halfwidth", 40.0))

        # 1) Identity-keyed raw-point verification (NO sorting across groups!)
        exp_pts: dict[str, dict[str, Any]] = {
            str(e["row_id"]): e for e in meta.get("expected_raw_points", [])
        }
        seen_ids: set[str] = set()
        for m in _CIRCLE_ROLE_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            if a.get("data-role") != "raw-point":
                continue
            rid = a.get("data-row-id", "")
            gid = a.get("data-group-id", "")
            if not rid or rid not in exp_pts:
                identity_ok = False
                failures.append(f"raw_point_unknown_or_missing_row_id:{rid}")
                diffs.append(math.inf)
                continue
            if rid in seen_ids:
                identity_ok = False
                failures.append(f"raw_point_duplicate_row_id:{rid}")
                diffs.append(math.inf)
                continue
            seen_ids.add(rid)
            exp = exp_pts[rid]
            if gid != str(exp["group_id"]):
                identity_ok = False
                failures.append(f"raw_point_group_mismatch:row={rid}:got={gid}:exp={exp['group_id']}")
                diffs.append(math.inf)
                continue
            if "cy" not in a or "cx" not in a:
                identity_ok = False
                failures.append(f"raw_point_missing_coords:row={rid}")
                diffs.append(math.inf)
                continue
            cy = float(a["cy"])
            cx = float(a["cx"])
            val = y_lo + (y_hi - y_lo) * (1.0 - (cy - mt) / max(ph, 1e-9))
            diffs.append(abs(val - float(exp["y_val"])))
            # Verify point cx resides inside its group's horizontal slot (catches group x-swaps)
            slot_cx = float(exp["group_cx"])
            if abs(cx - slot_cx) > x_tol:
                identity_ok = False
                failures.append(
                    f"raw_point_x_slot_violation:row={rid}:group={gid}:cx={cx:.2f}:slot_cx={slot_cx:.2f}:halfw={x_tol:.2f}"
                )
                diffs.append(math.inf)
            if exp.get("fill") and _norm_hex(a.get("fill")) != _norm_hex(exp["fill"]):
                color_ok = False
                failures.append(f"raw_point_fill_mismatch:row={rid}:got={a.get('fill')}:exp={exp['fill']}")
            checked += 1

        if len(seen_ids) != len(exp_pts):
            identity_ok = False
            failures.append(f"raw_point_count_mismatch:got={len(seen_ids)}:exp={len(exp_pts)}")
            diffs.append(math.inf)

        # 2) Identity-keyed group summary bar/box + error-bar/median geometry & fill check
        exp_groups: dict[str, dict[str, Any]] = {
            str(e["group_id"]): e for e in meta.get("expected_group_elements", [])
        }
        seen_groups: set[str] = set()
        target_rect_role = "bar-mean" if chart_type == "bar_with_points" else "box-iqr"
        for m in _RECT_ROLE_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            if a.get("data-role") != target_rect_role:
                continue
            gid = a.get("data-group-id", "")
            if gid not in exp_groups or gid in seen_groups:
                identity_ok = False
                failures.append(f"{target_rect_role}_invalid_or_dup_group:{gid}")
                diffs.append(math.inf)
                continue
            seen_groups.add(gid)
            eg = exp_groups[gid]
            rx = float(a.get("x", "nan"))
            rw = float(a.get("width", "nan"))
            ry = float(a.get("y", "nan"))
            rh = float(a.get("height", "nan"))
            rect_cx = rx + 0.5 * rw
            if abs(rect_cx - float(eg["group_cx"])) > max(2.0, 0.25 * rw):
                identity_ok = False
                failures.append(f"{target_rect_role}_x_mismatch:group={gid}:got_cx={rect_cx:.2f}:exp_cx={eg['group_cx']:.2f}")
                diffs.append(math.inf)
            if _norm_hex(a.get("fill")) != _norm_hex(eg["fill"]) or _norm_hex(a.get("data-fill")) != _norm_hex(eg["fill"]):
                color_ok = False
                failures.append(f"{target_rect_role}_fill_mismatch:group={gid}:got={a.get('fill')}:exp={eg['fill']}")
            if chart_type == "bar_with_points":
                y_zero = float(meta["y_zero_px"])
                ym_exp = float(eg["y_mean_px"])
                exp_top = min(ym_exp, y_zero)
                exp_h = max(1.0, abs(ym_exp - y_zero))
                y_err = max(abs(ry - exp_top), abs(rh - exp_h)) * (y_hi - y_lo) / max(ph, 1e-9)
                diffs.append(y_err)
            else:
                q3_val = y_lo + (y_hi - y_lo) * (1.0 - (ry - mt) / max(ph, 1e-9))
                q1_val = y_lo + (y_hi - y_lo) * (1.0 - ((ry + rh) - mt) / max(ph, 1e-9))
                diffs.append(max(abs(q3_val - float(eg["q3"])), abs(q1_val - float(eg["q1"]))))
            checked += 1

        if len(seen_groups) != len(exp_groups):
            identity_ok = False
            failures.append(f"{target_rect_role}_count_mismatch:got={len(seen_groups)}:exp={len(exp_groups)}")
            diffs.append(math.inf)

        # Check error-bar endpoints (bar_with_points) or box-median lines (boxplot_points)
        target_line_role = "error-bar" if chart_type == "bar_with_points" else "box-median"
        seen_lines: set[str] = set()
        for m in _LINE_ROLE_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            if a.get("data-role") != target_line_role:
                continue
            gid = a.get("data-group-id", "")
            if gid not in exp_groups:
                identity_ok = False
                failures.append(f"{target_line_role}_unknown_group:{gid}")
                diffs.append(math.inf)
                continue
            seen_lines.add(gid)
            eg = exp_groups[gid]
            y1_v = y_lo + (y_hi - y_lo) * (1.0 - (float(a["y1"]) - mt) / max(ph, 1e-9))
            y2_v = y_lo + (y_hi - y_lo) * (1.0 - (float(a["y2"]) - mt) / max(ph, 1e-9))
            if chart_type == "bar_with_points":
                diffs.append(max(abs(y1_v - float(eg["err_high"])), abs(y2_v - float(eg["err_low"]))))
            else:
                diffs.append(max(abs(y1_v - float(eg["median"])), abs(y2_v - float(eg["median"]))))
            checked += 1

        expected_line_groups = (
            {g for g, eg in exp_groups.items() if float(eg.get("err_halfwidth", 0.0)) > 0.0}
            if chart_type == "bar_with_points" else set(exp_groups.keys())
        )
        if seen_lines != expected_line_groups:
            identity_ok = False
            failures.append(f"{target_line_role}_groups_mismatch:got={sorted(seen_lines)}:exp={sorted(expected_line_groups)}")
            diffs.append(math.inf)

    elif chart_type == "scatter_regression":
        x_lo, x_hi, y_lo, y_hi = meta["x_lo"], meta["x_hi"], meta["y_lo"], meta["y_hi"]
        ml, mt, pw, ph = meta["ml"], meta["mt"], meta["pw"], meta["ph"]
        exp_pts = {str(e["row_id"]): e for e in meta.get("expected_points", [])}
        seen_ids = set()
        for m in _CIRCLE_ROLE_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            if a.get("data-role") != "scatter-point":
                continue
            rid = a.get("data-row-id", "")
            gid = a.get("data-group-id", "")
            if rid not in exp_pts or rid in seen_ids:
                identity_ok = False
                failures.append(f"scatter_invalid_or_dup_row_id:{rid}")
                diffs.append(math.inf)
                continue
            seen_ids.add(rid)
            ep = exp_pts[rid]
            if gid != str(ep["group_id"]):
                identity_ok = False
                failures.append(f"scatter_group_mismatch:row={rid}:got={gid}:exp={ep['group_id']}")
                diffs.append(math.inf)
                continue
            if _norm_hex(a.get("fill")) != _norm_hex(ep["fill"]):
                color_ok = False
                failures.append(f"scatter_fill_mismatch:row={rid}:got={a.get('fill')}:exp={ep['fill']}")
            xv = x_lo + (x_hi - x_lo) * ((float(a["cx"]) - ml) / max(pw, 1e-9))
            yv = y_lo + (y_hi - y_lo) * (1.0 - (float(a["cy"]) - mt) / max(ph, 1e-9))
            diffs.append(max(abs(xv - float(ep["x"])), abs(yv - float(ep["y"]))))
            checked += 1
        if len(seen_ids) != len(exp_pts):
            identity_ok = False
            failures.append(f"scatter_count_mismatch:got={len(seen_ids)}:exp={len(exp_pts)}")
            diffs.append(math.inf)
        tol = max(1e-3, 0.025 * max((x_hi - x_lo) / max(pw, 1.0), (y_hi - y_lo) / max(ph, 1.0)))

    elif chart_type == "line_with_band":
        x_lo, x_hi, y_lo, y_hi = meta["x_lo"], meta["x_hi"], meta["y_lo"], meta["y_hi"]
        ml, mt, pw, ph = meta["ml"], meta["mt"], meta["pw"], meta["ph"]
        tol_x = max(1e-3, 0.025 * (x_hi - x_lo) / max(pw, 1.0))
        tol_y = max(1e-3, 0.025 * (y_hi - y_lo) / max(ph, 1.0))
        tol = max(tol_x, tol_y)

        # 1) Identity-keyed series-mean points (verify BOTH x AND y AND fill)
        exp_means = {
            (str(e["series_id"]), int(e["x_index"])): e for e in meta.get("expected_means", [])
        }
        seen_means: set[tuple[str, int]] = set()
        for m in _CIRCLE_ROLE_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            if a.get("data-role") != "series-mean":
                continue
            sid = a.get("data-series-id", "")
            xidx_s = a.get("data-x-index", "")
            if not xidx_s.isdigit():
                identity_ok = False
                failures.append(f"series_mean_missing_x_index:{sid}")
                diffs.append(math.inf)
                continue
            key = (sid, int(xidx_s))
            if key not in exp_means or key in seen_means:
                identity_ok = False
                failures.append(f"series_mean_unknown_or_dup:{key}")
                diffs.append(math.inf)
                continue
            seen_means.add(key)
            em = exp_means[key]
            xv = x_lo + (x_hi - x_lo) * ((float(a["cx"]) - ml) / max(pw, 1e-9))
            yv = y_lo + (y_hi - y_lo) * (1.0 - (float(a["cy"]) - mt) / max(ph, 1e-9))
            diffs.append(max(abs(xv - float(em["x"])), abs(yv - float(em["mean"]))))
            if _norm_hex(a.get("fill")) != _norm_hex(em["fill"]):
                color_ok = False
                failures.append(f"series_mean_fill_mismatch:{key}:got={a.get('fill')}:exp={em['fill']}")
            checked += 1
        if len(seen_means) != len(exp_means):
            identity_ok = False
            failures.append(f"series_mean_count_mismatch:got={len(seen_means)}:exp={len(exp_means)}")
            diffs.append(math.inf)

        # 2) Verify shaded upper/lower band polygons (x, band_high, band_low, fill) per series_id
        exp_bands: dict[str, dict[str, Any]] = {
            str(b["series_id"]): b for b in meta.get("expected_bands", [])
        }
        seen_bands: set[str] = set()
        for m in _POLYGON_ROLE_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            if a.get("data-role") != "series-band":
                continue
            sid = a.get("data-series-id", "")
            if sid not in exp_bands or sid in seen_bands:
                band_ok = False
                identity_ok = False
                failures.append(f"series_band_unknown_or_dup:{sid}")
                diffs.append(math.inf)
                continue
            seen_bands.add(sid)
            eb = exp_bands[sid]
            if _norm_hex(a.get("fill")) != _norm_hex(eb["fill"]):
                color_ok = False
                failures.append(f"series_band_fill_mismatch:{sid}:got={a.get('fill')}:exp={eb['fill']}")
            poly_pts = _parse_polygon_points(a.get("points", ""))
            exp_verts = eb["vertices"]  # list of [x_val, y_val] in polygon order (upper then reversed lower)
            if len(poly_pts) != len(exp_verts):
                band_ok = False
                failures.append(f"series_band_vertex_count_mismatch:{sid}:got={len(poly_pts)}:exp={len(exp_verts)}")
                diffs.append(math.inf)
                continue
            for (px, py), (ex, ey) in zip(poly_pts, exp_verts):
                xv = x_lo + (x_hi - x_lo) * ((px - ml) / max(pw, 1e-9))
                yv = y_lo + (y_hi - y_lo) * (1.0 - (py - mt) / max(ph, 1e-9))
                d_v = max(abs(xv - float(ex)), abs(yv - float(ey)))
                diffs.append(d_v)
                if d_v > tol:
                    band_ok = False
                    failures.append(f"series_band_vertex_drift:{sid}:diff={d_v:.6f}")
                checked += 1
        if len(seen_bands) != len(exp_bands):
            band_ok = False
            failures.append(f"series_band_count_mismatch:got={len(seen_bands)}:exp={len(exp_bands)}")
            diffs.append(math.inf)

    elif chart_type == "heatmap":
        mat = meta["mat"]
        exp_fills = meta["expected_cell_fills"]
        n_c = len(mat)
        seen_text: set[tuple[int, int]] = set()
        for m in _TEXT_TAG_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            if a.get("data-role") != "cell-value":
                continue
            i = int(a.get("data-row-id", a.get("data-row", "-1")))
            j = int(a.get("data-col-id", a.get("data-col", "-1")))
            if 0 <= i < n_c and 0 <= j < n_c:
                seen_text.add((i, j))
                diffs.append(abs(float(m.group(2).strip()) - float(mat[i][j])))
                checked += 1
            else:
                identity_ok = False
                failures.append(f"heatmap_text_out_of_bounds:({i},{j})")
                diffs.append(math.inf)
        if len(seen_text) != n_c * n_c:
            identity_ok = False
            failures.append(f"heatmap_text_count_mismatch:got={len(seen_text)}:exp={n_c * n_c}")
            diffs.append(math.inf)

        # Verify EVERY cell rect fill color against expected color-ramp hex (catches color-only mutation!)
        seen_rects: set[tuple[int, int]] = set()
        for m in _RECT_ROLE_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            if a.get("data-role") != "cell-rect":
                continue
            i = int(a.get("data-row-id", a.get("data-row", "-1")))
            j = int(a.get("data-col-id", a.get("data-col", "-1")))
            if not (0 <= i < n_c and 0 <= j < n_c) or (i, j) in seen_rects:
                identity_ok = False
                failures.append(f"heatmap_rect_invalid_or_dup:({i},{j})")
                diffs.append(math.inf)
                continue
            seen_rects.add((i, j))
            actual_fill = _norm_hex(a.get("fill"))
            data_fill = _norm_hex(a.get("data-fill"))
            expected_fill = _norm_hex(exp_fills[i][j])
            if actual_fill != expected_fill or (data_fill and data_fill != expected_fill):
                color_ok = False
                failures.append(
                    f"heatmap_cell_fill_mismatch:({i},{j}):fill={actual_fill}:data_fill={data_fill}:expected={expected_fill}"
                )
            checked += 1
        if len(seen_rects) != n_c * n_c:
            identity_ok = False
            color_ok = False
            failures.append(f"heatmap_rect_count_mismatch:got={len(seen_rects)}:exp={n_c * n_c}")
            diffs.append(math.inf)
        tol = 0.006  # 2-decimal cell display rounding tolerance

    elif chart_type == "multivariate_ols":
        ax_lo, ax_hi, a_ml, a_pw = meta["a_xlo"], meta["a_xhi"], meta["a_ml"], meta["a_pw"]
        b_lo, b_hi, b_ml, b_mt, b_pw, b_ph = meta["b_lo"], meta["b_hi"], meta["b_ml"], meta["b_mt"], meta["b_pw"], meta["b_ph"]
        exp_coefs = {str(e["term"]): e for e in meta.get("expected_coefs", [])}
        exp_xy = {str(e["row_id"]): e for e in meta.get("expected_obs_pred", [])}
        seen_terms: set[str] = set()
        seen_rows: set[str] = set()
        for m in _CIRCLE_ROLE_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            role = a.get("data-role")
            if role == "coef-point":
                term = a.get("data-term") or a.get("data-group-id") or ""
                if term not in exp_coefs or term in seen_terms:
                    identity_ok = False
                    failures.append(f"coef_point_invalid_or_dup:{term}")
                    diffs.append(math.inf)
                    continue
                seen_terms.add(term)
                ec = exp_coefs[term]
                bv = ax_lo + (ax_hi - ax_lo) * ((float(a["cx"]) - a_ml) / max(a_pw, 1e-9))
                diffs.append(abs(bv - float(ec["std_beta"])))
                if _norm_hex(a.get("fill")) != _norm_hex(ec["fill"]):
                    color_ok = False
                    failures.append(f"coef_point_fill_mismatch:{term}:got={a.get('fill')}:exp={ec['fill']}")
                checked += 1
            elif role == "obs-pred-point":
                rid = a.get("data-row-id", "")
                if rid not in exp_xy or rid in seen_rows:
                    identity_ok = False
                    failures.append(f"obs_pred_invalid_or_dup:{rid}")
                    diffs.append(math.inf)
                    continue
                seen_rows.add(rid)
                exy = exp_xy[rid]
                xp = b_lo + (b_hi - b_lo) * ((float(a["cx"]) - b_ml) / max(b_pw, 1e-9))
                yo = b_lo + (b_hi - b_lo) * (1.0 - (float(a["cy"]) - b_mt) / max(b_ph, 1e-9))
                diffs.append(max(abs(xp - float(exy["y_pred"])), abs(yo - float(exy["y_obs"]))))
                checked += 1
        # Also verify Panel a CI lines by data-term
        seen_ci: set[str] = set()
        for m in _LINE_ROLE_RE.finditer(svg_text):
            a = _parse_attrs(m.group(1))
            if a.get("data-role") != "coef-ci":
                continue
            term = a.get("data-term") or a.get("data-group-id") or ""
            if term not in exp_coefs or term in seen_ci:
                identity_ok = False
                failures.append(f"coef_ci_invalid_or_dup:{term}")
                diffs.append(math.inf)
                continue
            seen_ci.add(term)
            ec = exp_coefs[term]
            lo_v = ax_lo + (ax_hi - ax_lo) * ((float(a["x1"]) - a_ml) / max(a_pw, 1e-9))
            hi_v = ax_lo + (ax_hi - ax_lo) * ((float(a["x2"]) - a_ml) / max(a_pw, 1e-9))
            diffs.append(max(abs(lo_v - float(ec["std_ci_lo"])), abs(hi_v - float(ec["std_ci_hi"]))))
            checked += 1
        if len(seen_terms) != len(exp_coefs) or len(seen_ci) != len(exp_coefs) or len(seen_rows) != len(exp_xy):
            identity_ok = False
            failures.append(
                f"multivariate_ols_count_mismatch:terms={len(seen_terms)}/{len(exp_coefs)},ci={len(seen_ci)}/{len(exp_coefs)},rows={len(seen_rows)}/{len(exp_xy)}"
            )
            diffs.append(math.inf)
        tol = max(1e-3, 0.025 * max((ax_hi - ax_lo) / max(a_pw, 1.0), (b_hi - b_lo) / max(b_ph, 1.0)))
    else:
        return {
            "max_abs_diff": None,
            "readback_elements_verified": 0,
            "readback_tolerance": None,
            "readback_ok": False,
            "readback_identity_verified": False,
            "readback_color_verified": False,
            "readback_band_verified": False,
            "readback_failures": ["unsupported_chart_type"],
            "readback_method": "unsupported",
        }

    max_d = max(diffs) if diffs else math.inf
    ok = math.isfinite(max_d) and max_d <= tol and checked > 0 and identity_ok and color_ok and band_ok
    return {
        "max_abs_diff": round(max_d, 6) if math.isfinite(max_d) else None,
        "readback_elements_verified": checked,
        "readback_tolerance": round(tol, 6),
        "readback_ok": ok,
        "readback_identity_verified": identity_ok,
        "readback_color_verified": color_ok,
        "readback_band_verified": band_ok,
        "readback_failures": failures[:20],
        "readback_method": (
            "identity_keyed_svg_cell_text_and_fill_color"
            if chart_type == "heatmap"
            else "identity_keyed_svg_geometry_and_fill"
        ),
    }


def verify_svg_readback_from_manifest(svg_path: Path, manifest_path: Path) -> dict[str, Any]:
    """Public verifier: re-check any SVG on disk against its persisted element manifest."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return _readback_svg_max_abs_diff(svg_path, str(manifest["chart_type"]), manifest["readback_meta"])


# ---------------------------------------------------------------------------
# Linear Reproduction Script Emitter (CSV + JSON + Read-Only SQLite)
# ---------------------------------------------------------------------------

def _emit_reproduce_figure_script(
    *,
    out_dir: Path,
    table_name: str,
    sha256: str,
    chart_type: str,
    spec: dict[str, Any],
    ylim: list[float] | None,
    sqlite_table: str | None = None,
    stem: str = "sci_figure",
) -> str:
    """Emit linear, researcher-readable reproduction script supporting CSV, JSON, and read-only SQLite."""
    fname = "reproduce_figure.py"
    lines = [
        "#!/usr/bin/env python3",
        f"# Reproducible figure verification script for {table_name} ({chart_type}).",
        "# Supports CSV, TSV, JSON, and strict read-only SQLite (mode=ro URI).",
        "",
        "import csv, hashlib, json, sqlite3, sys",
        "from pathlib import Path",
        "",
        f"DATA_PATH = Path({table_name!r})",
        f"SQLITE_TABLE = {sqlite_table!r}",
        f"EXPECTED_SHA256 = {sha256!r}",
        f"CHART_TYPE = {chart_type!r}",
        f"GROUP_COL = {spec.get('group_col')!r}",
        f"X_COL = {spec.get('x_col', spec.get('group_col'))!r}",
        f"X_COLS = {spec.get('x_cols') or []!r}",
        f"VALUE_COL = {spec.get('value_col')!r}",
        f"ACTUAL_YLIM = {ylim!r}",
        f"SPEC_FILE = Path({f'{stem}_auto_spec.json'!r})",
        "MISSING = {'', 'na', 'n/a', 'nan', 'null', 'none', 'nd', '.', '-'}",
        "",
        "sha = hashlib.sha256(DATA_PATH.read_bytes()).hexdigest()",
        "print(f'Source SHA-256 match: {sha == EXPECTED_SHA256} ({sha[:16]}...)')",
        "ext = DATA_PATH.suffix.lower()",
        "if ext in {'.sqlite', '.sqlite3', '.db'}:",
        "    conn = sqlite3.connect(f'file:{DATA_PATH.resolve()}?mode=ro', uri=True)",
        "    try:",
        "        tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name\")]",
        "        chosen = SQLITE_TABLE if (SQLITE_TABLE in tables) else tables[0]",
        "        cur = conn.execute(f'SELECT * FROM \"{chosen}\"')",
        "        cols = [d[0] for d in cur.description]",
        "        raw = [dict(zip(cols, r)) for r in cur.fetchall()]",
        "        print(f'SQLite read-only table={chosen}, tables_in_db={tables}')",
        "    finally:",
        "        conn.close()",
        "elif ext == '.json':",
        "    obj = json.loads(DATA_PATH.read_text(encoding='utf-8-sig'))",
        "    raw = obj if isinstance(obj, list) else next((obj[k] for k in ('rows', 'data', 'records') if isinstance(obj.get(k), list)), [])",
        "    cols = list(dict.fromkeys(k for r in raw for k in r.keys()))",
        "else:",
        "    delim = '\\t' if ext == '.tsv' else ','",
        "    with DATA_PATH.open('r', encoding='utf-8-sig', newline='') as fh:",
        "        rd = csv.DictReader(fh, delimiter=delim)",
        "        cols = list(rd.fieldnames or [])",
        "        raw = list(rd)",
        "",
        "seen, rows = set(), []",
        "for r in raw:",
        "    k = tuple(str(r.get(c, '')) for c in cols)",
        "    if k not in seen:",
        "        seen.add(k)",
        "        rows.append(r)",
        "print(f'Raw rows={len(raw)}, duplicates_removed={len(raw)-len(rows)}, clean_rows={len(rows)}')",
        "if VALUE_COL and VALUE_COL in cols:",
        "    vals = [float(r[VALUE_COL]) for r in rows if str(r.get(VALUE_COL, '')).strip().lower() not in MISSING]",
        "    print(f'Plotted {VALUE_COL} range: [{min(vals):.4f}, {max(vals):.4f}], n={len(vals)}, y_limits={ACTUAL_YLIM}')",
        "if '--rerender' in sys.argv and SPEC_FILE.is_file():",
        "    spec = json.loads(SPEC_FILE.read_text(encoding='utf-8'))",
        "    from publication_viz_builder import build_publication_figure",
        "    out = build_publication_figure(spec, Path('.'), stem='reproduced_figure', base_dir=DATA_PATH.parent)",
        "    print('Rerender ok:', out.get('ok'), 'max_abs_diff:', out.get('max_abs_diff'))",
    ]
    (out_dir / fname).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return fname


# ---------------------------------------------------------------------------
# Main Publication Figure Builder
# ---------------------------------------------------------------------------

def build_publication_figure(
    spec: dict[str, Any],
    out_dir: Path,
    *,
    stem: str = "sci_figure",
    base_dir: Path | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sel = advise_and_audit_chart(spec, base_dir=base_dir)
    if not sel["ok"]:
        aud_p = out_dir / f"{stem}_audit.json"
        blocked = {"ok": False, "selection_audit": sel, "blockers": sel["blockers"]}
        aud_p.write_text(json.dumps(blocked, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"ok": False, "blocked": True, "blockers": sel["blockers"], "audit": aud_p.name}

    tpath = Path(str(spec["table"]))
    if not tpath.is_absolute() and base_dir is not None:
        tpath = base_dir / tpath
    cols, raw_rows, sha = load_table_stdlib(tpath, table=spec.get("sqlite_table"))

    seen: set[tuple[str, ...]] = set()
    rows: list[dict[str, Any]] = []
    for r in raw_rows:
        k = tuple(str(r.get(c, "")) for c in cols)
        if k not in seen:
            seen.add(k)
            rows.append(r)
    exact_dups = len(raw_rows) - len(rows)

    chart_type = sel["requested_chart"]
    colors = sel["colors"]
    gcol = str(spec.get("group_col", "")).strip()
    vcol = str(spec.get("value_col", "")).strip()
    xcol = str(spec.get("x_col", gcol)).strip()
    xcols = [str(c).strip() for c in (spec.get("x_cols") or []) if str(c).strip()]
    series_col = str(spec.get("series_col", "")).strip()
    unit = str(spec.get("unit", "")).strip()
    unit_source = str(spec.get("unit_source", "spec" if unit else ("dimensionless_correlation" if chart_type == "heatmap" else "unresolved"))).strip()
    pred_units: dict[str, str] = dict(spec.get("predictor_units") or {})
    error_type = str(spec.get("error_type", "SD")).strip()

    xlabel = str(spec.get("xlabel", xcol or gcol)).strip()
    ylabel = str(spec.get("ylabel", vcol)).strip()
    if unit and ylabel and f"({unit})" not in ylabel:
        ylabel = f"{ylabel} ({unit})"
    raw_title = str(spec.get("title", f"{ylabel} across {xlabel}" if chart_type != "heatmap" else "Correlation Matrix")).strip()

    default_preset = "cell_double" if chart_type == "multivariate_ols" else "cell_1_5col"
    width_mm = float(spec.get("width_mm", CELL_WIDTHS_MM.get(str(spec.get("column_preset", default_preset)).lower(), 114.0)))
    default_ratio = 0.50 if chart_type == "multivariate_ols" else 0.72
    height_mm = float(spec.get("height_mm", round(width_mm * default_ratio, 1)))
    font_pt = float(spec.get("font_size_pt", 8.0))

    svg_lines, W, H = _svg_header(width_mm, height_mm, font_pt)
    ml, mr, mt, mb = 58.0, 20.0, 32.0, 48.0
    if chart_type == "heatmap":
        ml, mr, mt, mb = 74.0, 52.0, 32.0, 62.0
    pw, ph = W - ml - mr, H - mt - mb
    panel_cx = ml + pw / 2.0

    svg_lines.append(
        f'<text x="{panel_cx:.2f}" y="19" text-anchor="middle" font-size="{round((font_pt + 1.0) * 96 / 72, 2)}" font-weight="normal">{html.escape(raw_title)}</text>'
    )

    reconciliation: dict[str, Any] = {
        "source_file": tpath.name,
        "source_sha256": sha,
        "rows_raw": len(raw_rows),
        "exact_duplicates_removed": exact_dups,
        "rows_after_dedup": len(rows),
    }
    actual_ylim: list[float] | None = None
    missing_excluded = 0
    mpl_payload: dict[str, Any] = {}
    readback_meta: dict[str, Any] = {}
    auto_gaps: list[dict[str, str]] = []
    auto_uncertainty: list[str] = []

    if chart_type in {"bar_with_points", "boxplot_points"}:
        groups: list[str] = []
        grouped: dict[str, list[float]] = {}
        grouped_with_id: dict[str, list[tuple[int, float]]] = {}
        for row_idx, r in enumerate(rows, start=1):
            gv = str(r.get(gcol, "")).strip()
            fv = _try_float(r.get(vcol))
            if gv.lower() in MISSING_TOKENS or fv is None:
                missing_excluded += 1
                continue
            if gv not in grouped:
                groups.append(gv)
                grouped[gv] = []
                grouped_with_id[gv] = []
            grouped[gv].append(fv)
            grouped_with_id[gv].append((row_idx, fv))

        arrays = [grouped[g] for g in groups]
        all_pts = [x for a in arrays for x in a]
        summary_rows: list[dict[str, Any]] = []
        extrema = list(all_pts)
        for g, arr in zip(groups, arrays):
            m, sd = _mean_sd(arr)
            err = _error_halfwidth(arr, error_type)
            ci95_hw = _error_halfwidth(arr, "CI95")
            q1, med, q3 = _quantile(arr, 0.25), _quantile(arr, 0.50), _quantile(arr, 0.75)
            iqr = q3 - q1
            w_lo = min((x for x in arr if x >= q1 - 1.5 * iqr), default=min(arr))
            w_hi = max((x for x in arr if x <= q3 + 1.5 * iqr), default=max(arr))
            if chart_type == "bar_with_points":
                extrema.extend([m - err, m + err, 0.0])
            summary_rows.append({
                "group": g, "n": len(arr), "mean": round(m, 6), "sd": round(sd, 6),
                "err_halfwidth": round(err, 6), "err_low": round(m - err, 6), "err_high": round(m + err, 6),
                "ci95_low": round(m - ci95_hw, 6), "ci95_high": round(m + ci95_hw, 6),
                "median": round(med, 6), "q1": round(q1, 6), "q3": round(q3, 6), "iqr": round(iqr, 6),
                "whisker_low": round(w_lo, 6), "whisker_high": round(w_hi, 6),
                "min": round(min(arr), 6), "max": round(max(arr), 6),
            })

        stat_tests = run_group_comparison_tests(groups, arrays)
        pw_list = [p for p in stat_tests["pairwise_comparisons"] if p.get("p_holm") is not None]
        # Order brackets by span ascending so short brackets sit below wider ones
        pw_to_draw = sorted(pw_list, key=lambda p: (abs(p["index_b"] - p["index_a"]), p["index_a"]))[:3]
        extra_top = 0.08 + 0.13 * len(pw_to_draw) if pw_to_draw else 0.06
        y_lo, y_hi = _compute_safe_ylim(extrema, anchor_zero=(chart_type == "bar_with_points"), extra_top_frac=extra_top)
        actual_ylim = [round(y_lo, 4), round(y_hi, 4)]

        def _ypx(val: float) -> float:
            return mt + ph * (1.0 - (val - y_lo) / max(y_hi - y_lo, 1e-12))

        svg_lines.append(f'<line x1="{ml:.2f}" y1="{mt:.2f}" x2="{ml:.2f}" y2="{mt + ph:.2f}" stroke="#111827" stroke-width="1"/>')
        svg_lines.append(f'<line x1="{ml:.2f}" y1="{mt + ph:.2f}" x2="{ml + pw:.2f}" y2="{mt + ph:.2f}" stroke="#111827" stroke-width="1"/>')
        if y_lo < 0.0 < y_hi:
            y0 = _ypx(0.0)
            svg_lines.append(f'<line x1="{ml:.2f}" y1="{y0:.2f}" x2="{ml + pw:.2f}" y2="{y0:.2f}" stroke="#4B5563" stroke-width="0.9" stroke-dasharray="4,3"/>')

        for tv in _nice_ticks(y_lo, y_hi, 5):
            yp = _ypx(tv)
            if mt - 1 <= yp <= mt + ph + 1:
                svg_lines.append(f'<line x1="{ml - 4:.2f}" y1="{yp:.2f}" x2="{ml:.2f}" y2="{yp:.2f}" stroke="#111827" stroke-width="0.9"/>')
                svg_lines.append(f'<text x="{ml - 7:.2f}" y="{yp + 3:.2f}" text-anchor="end">{html.escape(_fmt(tv))}</text>')

        k = max(len(groups), 1)
        step = pw / k
        bw = min(38.0, step * 0.48)
        y_zero = _ypx(0.0 if y_lo <= 0.0 <= y_hi else y_lo)
        rng = random.Random(13)
        plotted_pts = 0
        clipped_pts = 0
        jitter_coords: list[list[float]] = []
        expected_raw_points: list[dict[str, Any]] = []
        expected_group_elements: list[dict[str, Any]] = []

        for gi, (srow, arr) in enumerate(zip(summary_rows, arrays)):
            cx = ml + (gi + 0.5) * step
            col = colors[gi % len(colors)]
            gid_esc = html.escape(srow["group"])
            if chart_type == "bar_with_points":
                ym = _ypx(srow["mean"])
                top_y, bh = min(ym, y_zero), max(1.0, abs(ym - y_zero))
                svg_lines.append(
                    f'<rect data-role="bar-mean" data-group-id="{gid_esc}" data-fill="{col}" '
                    f'x="{cx - bw / 2:.2f}" y="{top_y:.2f}" width="{bw:.2f}" height="{bh:.2f}" '
                    f'fill="{col}" fill-opacity="0.72" stroke="#1F2937" stroke-width="0.9"/>'
                )
                if srow["err_halfwidth"] > 0:
                    y_hi_e, y_lo_e = _ypx(srow["err_high"]), _ypx(srow["err_low"])
                    svg_lines.append(f'<line data-role="error-bar" data-group-id="{gid_esc}" x1="{cx:.2f}" y1="{y_hi_e:.2f}" x2="{cx:.2f}" y2="{y_lo_e:.2f}" stroke="#111827" stroke-width="1.1"/>')
                    svg_lines.append(f'<line x1="{cx - 5:.2f}" y1="{y_hi_e:.2f}" x2="{cx + 5:.2f}" y2="{y_hi_e:.2f}" stroke="#111827" stroke-width="1.1"/>')
                    svg_lines.append(f'<line x1="{cx - 5:.2f}" y1="{y_lo_e:.2f}" x2="{cx + 5:.2f}" y2="{y_lo_e:.2f}" stroke="#111827" stroke-width="1.1"/>')
                expected_group_elements.append({
                    "group_id": srow["group"], "group_cx": round(cx, 4), "fill": col,
                    "mean": srow["mean"], "y_mean_px": round(ym, 4),
                    "err_halfwidth": srow["err_halfwidth"], "err_low": srow["err_low"], "err_high": srow["err_high"],
                })
            else:
                y_q1, y_med, y_q3 = _ypx(srow["q1"]), _ypx(srow["median"]), _ypx(srow["q3"])
                y_wlo, y_whi = _ypx(srow["whisker_low"]), _ypx(srow["whisker_high"])
                svg_lines.append(f'<line data-role="box-whisker" data-group-id="{gid_esc}" x1="{cx:.2f}" y1="{y_whi:.2f}" x2="{cx:.2f}" y2="{y_wlo:.2f}" stroke="#1F2937" stroke-width="1.0"/>')
                svg_lines.append(f'<line x1="{cx - bw * 0.28:.2f}" y1="{y_whi:.2f}" x2="{cx + bw * 0.28:.2f}" y2="{y_whi:.2f}" stroke="#1F2937" stroke-width="1.0"/>')
                svg_lines.append(f'<line x1="{cx - bw * 0.28:.2f}" y1="{y_wlo:.2f}" x2="{cx + bw * 0.28:.2f}" y2="{y_wlo:.2f}" stroke="#1F2937" stroke-width="1.0"/>')
                svg_lines.append(
                    f'<rect data-role="box-iqr" data-group-id="{gid_esc}" data-fill="{col}" '
                    f'x="{cx - bw / 2:.2f}" y="{min(y_q1, y_q3):.2f}" width="{bw:.2f}" height="{max(1.5, abs(y_q1 - y_q3)):.2f}" '
                    f'fill="{col}" fill-opacity="0.72" stroke="#1F2937" stroke-width="0.9"/>'
                )
                svg_lines.append(f'<line data-role="box-median" data-group-id="{gid_esc}" x1="{cx - bw / 2:.2f}" y1="{y_med:.2f}" x2="{cx + bw / 2:.2f}" y2="{y_med:.2f}" stroke="#111827" stroke-width="1.8"/>')
                expected_group_elements.append({
                    "group_id": srow["group"], "group_cx": round(cx, 4), "fill": col,
                    "q1": srow["q1"], "median": srow["median"], "q3": srow["q3"],
                    "whisker_low": srow["whisker_low"], "whisker_high": srow["whisker_high"],
                })

            j_offsets = []
            for rid, v in grouped_with_id[srow["group"]]:
                off = rng.uniform(-0.15, 0.15)
                j_offsets.append(off)
                px, py = cx + off * (bw * 1.4), _ypx(v)
                svg_lines.append(
                    f'<circle data-role="raw-point" data-row-id="{rid}" data-group-id="{gid_esc}" data-fill="#1F2937" '
                    f'cx="{px:.2f}" cy="{py:.2f}" r="2.5" fill="#1F2937" stroke="#FFFFFF" stroke-width="0.55"/>'
                )
                expected_raw_points.append({
                    "row_id": rid, "group_id": srow["group"], "y_val": v,
                    "group_cx": round(cx, 4), "expected_cx": round(px, 2), "expected_cy": round(py, 2), "fill": "#1F2937",
                })
                plotted_pts += 1
                if not (y_lo - 1e-9 <= v <= y_hi + 1e-9):
                    clipped_pts += 1
            jitter_coords.append(j_offsets)

            svg_lines.append(f'<line x1="{cx:.2f}" y1="{mt + ph:.2f}" x2="{cx:.2f}" y2="{mt + ph + 4:.2f}" stroke="#111827" stroke-width="0.9"/>')
            lbl = srow["group"] if len(srow["group"]) <= 15 else srow["group"][:13] + ".."
            svg_lines.append(f'<text x="{cx:.2f}" y="{mt + ph + 15:.2f}" text-anchor="middle">{html.escape(lbl)}</text>')
            svg_lines.append(f'<text x="{cx:.2f}" y="{mt + ph + 26:.2f}" text-anchor="middle" fill="#4B5563">n={srow["n"]}</text>')

        # Draw real significance brackets with Holm-adjusted p-values
        data_max = max(extrema) if extrema else 0.0
        span_y = max(y_hi - y_lo, 1e-6)
        bracket_specs: list[dict[str, Any]] = []
        for bi, pr in enumerate(pw_to_draw):
            ia, ib = pr["index_a"], pr["index_b"]
            x1 = ml + (ia + 0.5) * step
            x2 = ml + (ib + 0.5) * step
            y_val_top = data_max + (0.055 + bi * 0.095) * (max(extrema) - min(extrema) if len(extrema) > 1 else span_y)
            y_val_top = min(y_val_top, y_hi - 0.05 * span_y)
            yt = _ypx(y_val_top)
            tick_h = 4.5
            sig_lbl = f"{pr['sig']} ({fmt_p(pr['p_holm'])})"
            svg_lines.append(f'<path data-role="sig-bracket" data-group-a="{html.escape(pr["group_a"])}" data-group-b="{html.escape(pr["group_b"])}" d="M {x1:.2f} {yt + tick_h:.2f} L {x1:.2f} {yt:.2f} L {x2:.2f} {yt:.2f} L {x2:.2f} {yt + tick_h:.2f}" fill="none" stroke="#111827" stroke-width="0.9"/>')
            svg_lines.append(f'<text data-role="sig-label" x="{0.5 * (x1 + x2):.2f}" y="{yt - 2.5:.2f}" text-anchor="middle" font-size="{round(max(7.0, font_pt - 1.0) * 96 / 72, 2)}">{html.escape(sig_lbl)}</text>')
            bracket_specs.append({"ia": ia, "ib": ib, "y_val": y_val_top, "label": sig_lbl})

        # Concise omnibus annotation inside top-left of plotting area
        omni = stat_tests["kruskal_wallis"] if stat_tests["primary_omnibus"] == "kruskal_wallis" else stat_tests["welch_anova"]
        omni_txt = ""
        if omni.get("status") == "ok":
            if stat_tests["primary_omnibus"] == "kruskal_wallis":
                omni_txt = f"Kruskal-Wallis H={omni['h_stat']:.2f}, {fmt_p(omni['p_value'])}"
            else:
                omni_txt = f"Welch ANOVA F={omni['f_stat']:.2f}, {fmt_p(omni['p_value'])}"
            svg_lines.append(f'<text x="{ml + 6:.2f}" y="{mt + 10:.2f}" text-anchor="start" font-size="{round(max(7.0, font_pt - 1.0) * 96 / 72, 2)}" fill="#374151">{html.escape(omni_txt)}</text>')

        svg_lines.append(f'<text x="{panel_cx:.2f}" y="{H - 7:.2f}" text-anchor="middle">{html.escape(xlabel)}</text>')
        ym_lbl = mt + ph / 2.0
        svg_lines.append(f'<text x="14" y="{ym_lbl:.2f}" text-anchor="middle" transform="rotate(-90 14 {ym_lbl:.2f})">{html.escape(ylabel)}</text>')

        err_covered = all(
            (y_lo - 1e-9 <= s["err_low"] <= y_hi + 1e-9 and y_lo - 1e-9 <= s["err_high"] <= y_hi + 1e-9)
            for s in summary_rows
        ) if chart_type == "bar_with_points" else True
        for vmsg in stat_tests.get("assumption_violations", []):
            auto_uncertainty.append(vmsg)
            auto_gaps.append({"code": "STATISTICAL_ASSUMPTION_ROUTING", "severity": "info", "source": "stat_assumptions", "message": vmsg, "fallback": stat_tests["primary_omnibus"]})

        reconciliation.update({
            "missing_excluded": missing_excluded,
            "clean_source_points": len(all_pts),
            "plotted_points_count": plotted_pts,
            "points_clipped_outside_ylim": clipped_pts,
            "all_raw_points_within_ylim": clipped_pts == 0,
            "all_error_bars_within_ylim": err_covered,
            "zero_baseline_drawn": bool(y_lo < 0.0 < y_hi or (chart_type == "bar_with_points" and y_lo == 0.0)),
            "actual_ylim": actual_ylim,
            "group_summaries": summary_rows,
            "descriptive_stats": {vcol: describe_vector(all_pts, unit=unit)},
            "statistical_tests": stat_tests,
            "significance_brackets_drawn": len(bracket_specs),
        })
        readback_meta = {
            "y_lo": y_lo, "y_hi": y_hi, "mt": mt, "ph": ph,
            "y_zero_px": round(y_zero, 4),
            "x_slot_halfwidth": round(min(step * 0.45, bw * 0.6 + 2.0), 4),
            "expected_raw_points": expected_raw_points,
            "expected_group_elements": expected_group_elements,
        }
        mpl_payload = {
            "groups": groups, "arrays": arrays, "summary_rows": summary_rows,
            "y_lo": y_lo, "y_hi": y_hi, "jitter_coords": jitter_coords,
            "brackets": bracket_specs, "omni_txt": omni_txt,
        }

    elif chart_type == "multivariate_ols":
        if not xcols:
            for c in cols:
                if c == vcol or c.lower().endswith(("_id", "_code")) or c.lower() in {"id", "code", "mix_id", "qc_pass"}:
                    continue
                if sum(_try_float(r.get(c)) is not None for r in rows) >= max(3, int(0.8 * len(rows))):
                    xcols.append(c)
            xcols = xcols[:6]

        y_clean: list[float] = []
        y_clean_row_ids: list[int] = []
        x_clean: list[list[float]] = [[] for _ in xcols]
        for row_idx, r in enumerate(rows, start=1):
            yv = _try_float(r.get(vcol))
            xvs = [_try_float(r.get(xc)) for xc in xcols]
            if yv is None or any(v is None for v in xvs):
                missing_excluded += 1
                continue
            y_clean.append(yv)
            y_clean_row_ids.append(row_idx)
            for j, v in enumerate(xvs):
                x_clean[j].append(float(v))

        ols = fit_multivariate_ols(y_clean, x_clean, xcols, vcol, y_unit=unit, predictor_units=pred_units)
        if not ols.get("ok"):
            return {"ok": False, "error": ols.get("error", "Multivariate OLS failed")}

        pred_coefs = [c for c in ols["coefficients"] if c["term"] != "Intercept"]
        y_obs, y_pred = ols["y_observed"], ols["y_predicted"]

        # Dual-panel geometry: Panel a (left forest plot) & Panel b (right observed vs predicted)
        a_ml, a_mr_gap, b_mr = 92.0, 48.0, 16.0
        p_mt, p_mb = 38.0, 42.0
        half_w = (W - a_ml - a_mr_gap - b_mr) / 2.0
        a_pw, b_pw = half_w, half_w
        b_ml = a_ml + a_pw + a_mr_gap
        p_ph = H - p_mt - p_mb

        # Panel a: standardized beta* with 95% HC3 CI
        ci_vals = [0.0]
        for c in pred_coefs:
            ci_vals.extend(c["std_ci95_hc3"] or [c["std_beta"] or 0.0])
        a_xlo, a_xhi = _compute_safe_ylim(ci_vals, anchor_zero=False, extra_top_frac=0.18)
        # Ensure room on right for text annotation
        a_xhi = a_xhi + 0.12 * (a_xhi - a_xlo)

        def _axpx(v: float) -> float:
            return a_ml + a_pw * ((v - a_xlo) / max(a_xhi - a_xlo, 1e-12))

        svg_lines.append(f'<text x="{a_ml - 14:.2f}" y="{p_mt - 8:.2f}" text-anchor="start" font-weight="bold" font-size="{round((font_pt + 2.0) * 96 / 72, 2)}">a</text>')
        svg_lines.append(f'<text x="{a_ml + a_pw / 2:.2f}" y="{p_mt - 8:.2f}" text-anchor="middle" fill="#374151">Standardized effects (95% HC3 CI)</text>')
        svg_lines.append(f'<line x1="{a_ml:.2f}" y1="{p_mt:.2f}" x2="{a_ml:.2f}" y2="{p_mt + p_ph:.2f}" stroke="#111827" stroke-width="1"/>')
        svg_lines.append(f'<line x1="{a_ml:.2f}" y1="{p_mt + p_ph:.2f}" x2="{a_ml + a_pw:.2f}" y2="{p_mt + p_ph:.2f}" stroke="#111827" stroke-width="1"/>')
        x0_a = _axpx(0.0)
        svg_lines.append(f'<line x1="{x0_a:.2f}" y1="{p_mt:.2f}" x2="{x0_a:.2f}" y2="{p_mt + p_ph:.2f}" stroke="#6B7280" stroke-width="0.9" stroke-dasharray="4,3"/>')

        for tv in _nice_ticks(a_xlo, a_xhi, 5):
            xp = _axpx(tv)
            if a_ml - 1 <= xp <= a_ml + a_pw + 1:
                svg_lines.append(f'<line x1="{xp:.2f}" y1="{p_mt + p_ph:.2f}" x2="{xp:.2f}" y2="{p_mt + p_ph + 4:.2f}" stroke="#111827" stroke-width="0.9"/>')
                svg_lines.append(f'<text x="{xp:.2f}" y="{p_mt + p_ph + 15:.2f}" text-anchor="middle">{html.escape(_fmt(tv))}</text>')

        row_step = p_ph / max(len(pred_coefs), 1)
        expected_coefs_meta: list[dict[str, Any]] = []
        for idx_c, c in enumerate(pred_coefs):
            cy = p_mt + (idx_c + 0.5) * row_step
            sb = float(c["std_beta"])
            lo_b, hi_b = c["std_ci95_hc3"]
            col = colors[0] if sb >= 0 else colors[1 % len(colors)]
            x_lo_p, x_hi_p, x_pt = _axpx(lo_b), _axpx(hi_b), _axpx(sb)
            term_esc = html.escape(c["term"])
            svg_lines.append(f'<line data-role="coef-ci" data-term="{term_esc}" data-group-id="{term_esc}" data-fill="{col}" x1="{x_lo_p:.2f}" y1="{cy:.2f}" x2="{x_hi_p:.2f}" y2="{cy:.2f}" stroke="{col}" stroke-width="1.6"/>')
            svg_lines.append(f'<line x1="{x_lo_p:.2f}" y1="{cy - 3.5:.2f}" x2="{x_lo_p:.2f}" y2="{cy + 3.5:.2f}" stroke="{col}" stroke-width="1.2"/>')
            svg_lines.append(f'<line x1="{x_hi_p:.2f}" y1="{cy - 3.5:.2f}" x2="{x_hi_p:.2f}" y2="{cy + 3.5:.2f}" stroke="{col}" stroke-width="1.2"/>')
            svg_lines.append(f'<circle data-role="coef-point" data-term="{term_esc}" data-group-id="{term_esc}" data-fill="{col}" cx="{x_pt:.2f}" cy="{cy:.2f}" r="3.6" fill="{col}" stroke="#FFFFFF" stroke-width="0.7"/>')
            expected_coefs_meta.append({
                "term": c["term"], "std_beta": sb, "std_ci_lo": float(lo_b), "std_ci_hi": float(hi_b), "fill": col,
            })
            tlbl = c["term"] if len(c["term"]) <= 16 else c["term"][:14] + ".."
            svg_lines.append(f'<text x="{a_ml - 6:.2f}" y="{cy + 3:.2f}" text-anchor="end">{html.escape(tlbl)}</text>')
            note = f"{sb:+.2f} {c['sig_hc3']} (VIF={c['vif']:.2f})"
            svg_lines.append(f'<text x="{min(x_hi_p + 5, a_ml + a_pw - 2):.2f}" y="{cy - 5:.2f}" text-anchor="start" font-size="{round(max(6.8, font_pt - 1.2) * 96 / 72, 2)}" fill="#374151">{html.escape(note)}</text>')

        svg_lines.append(f'<text x="{a_ml + a_pw / 2:.2f}" y="{H - 8:.2f}" text-anchor="middle">Standardized beta (95% HC3 CI)</text>')

        # Panel b: Observed vs Predicted scatter
        b_lo, b_hi = _compute_safe_ylim(y_obs + y_pred, anchor_zero=False, extra_top_frac=0.14)
        actual_ylim = [round(b_lo, 4), round(b_hi, 4)]

        def _bxpx(v: float) -> float:
            return b_ml + b_pw * ((v - b_lo) / max(b_hi - b_lo, 1e-12))

        def _bypx(v: float) -> float:
            return p_mt + p_ph * (1.0 - (v - b_lo) / max(b_hi - b_lo, 1e-12))

        svg_lines.append(f'<text x="{b_ml - 14:.2f}" y="{p_mt - 8:.2f}" text-anchor="start" font-weight="bold" font-size="{round((font_pt + 2.0) * 96 / 72, 2)}">b</text>')
        svg_lines.append(f'<text x="{b_ml + b_pw / 2:.2f}" y="{p_mt - 8:.2f}" text-anchor="middle" fill="#374151">Observed vs. predicted</text>')
        svg_lines.append(f'<line x1="{b_ml:.2f}" y1="{p_mt:.2f}" x2="{b_ml:.2f}" y2="{p_mt + p_ph:.2f}" stroke="#111827" stroke-width="1"/>')
        svg_lines.append(f'<line x1="{b_ml:.2f}" y1="{p_mt + p_ph:.2f}" x2="{b_ml + b_pw:.2f}" y2="{p_mt + p_ph:.2f}" stroke="#111827" stroke-width="1"/>')
        svg_lines.append(f'<line x1="{_bxpx(b_lo):.2f}" y1="{_bypx(b_lo):.2f}" x2="{_bxpx(b_hi):.2f}" y2="{_bypx(b_hi):.2f}" stroke="#6B7280" stroke-width="1.0" stroke-dasharray="4,3"/>')

        for tv in _nice_ticks(b_lo, b_hi, 5):
            yp, xp = _bypx(tv), _bxpx(tv)
            if p_mt - 1 <= yp <= p_mt + p_ph + 1:
                svg_lines.append(f'<line x1="{b_ml - 4:.2f}" y1="{yp:.2f}" x2="{b_ml:.2f}" y2="{yp:.2f}" stroke="#111827" stroke-width="0.9"/>')
                svg_lines.append(f'<text x="{b_ml - 7:.2f}" y="{yp + 3:.2f}" text-anchor="end">{html.escape(_fmt(tv))}</text>')
            if b_ml - 1 <= xp <= b_ml + b_pw + 1:
                svg_lines.append(f'<line x1="{xp:.2f}" y1="{p_mt + p_ph:.2f}" x2="{xp:.2f}" y2="{p_mt + p_ph + 4:.2f}" stroke="#111827" stroke-width="0.9"/>')
                svg_lines.append(f'<text x="{xp:.2f}" y="{p_mt + p_ph + 15:.2f}" text-anchor="middle">{html.escape(_fmt(tv))}</text>')

        clipped = 0
        expected_obs_pred_meta: list[dict[str, Any]] = []
        for rid, yp_v, yo_v in zip(y_clean_row_ids, y_pred, y_obs):
            svg_lines.append(
                f'<circle data-role="obs-pred-point" data-row-id="{rid}" data-fill="{colors[0]}" '
                f'cx="{_bxpx(yp_v):.2f}" cy="{_bypx(yo_v):.2f}" r="2.2" fill="{colors[0]}" fill-opacity="0.65" stroke="#FFFFFF" stroke-width="0.35"/>'
            )
            expected_obs_pred_meta.append({"row_id": rid, "y_pred": round(yp_v, 6), "y_obs": round(yo_v, 6), "fill": colors[0]})
            if not (b_lo - 1e-9 <= yo_v <= b_hi + 1e-9 and b_lo - 1e-9 <= yp_v <= b_hi + 1e-9):
                clipped += 1

        jb = ols["diagnostics"]["jarque_bera"]
        bp = ols["diagnostics"]["breusch_pagan"]
        ann1 = f"R²={ols['r_squared']:.3f}, R²_adj={ols['r_squared_adj']:.3f} (n={ols['n']})"
        ann2 = f"JB {fmt_p(jb['p_value'])} | BP {fmt_p(bp['p_value'])} | max VIF={ols['max_vif']:.2f}"
        svg_lines.append(f'<text x="{b_ml + 6:.2f}" y="{p_mt + 12:.2f}" text-anchor="start" font-size="{round(max(6.8, font_pt - 1.0) * 96 / 72, 2)}">{html.escape(ann1)}</text>')
        svg_lines.append(f'<text x="{b_ml + 6:.2f}" y="{p_mt + 23:.2f}" text-anchor="start" font-size="{round(max(6.6, font_pt - 1.3) * 96 / 72, 2)}" fill="#4B5563">{html.escape(ann2)}</text>')
        svg_lines.append(f'<text x="{b_ml + b_pw / 2:.2f}" y="{H - 8:.2f}" text-anchor="middle">Predicted {html.escape(ylabel)}</text>')
        svg_lines.append(f'<text x="{b_ml - 34:.2f}" y="{p_mt + p_ph / 2:.2f}" text-anchor="middle" transform="rotate(-90 {b_ml - 34:.2f} {p_mt + p_ph / 2:.2f})">Observed {html.escape(ylabel)}</text>')

        for vmsg in ols["diagnostics"]["assumption_violations"]:
            auto_uncertainty.append(vmsg)
            auto_gaps.append({"code": "REGRESSION_ASSUMPTION_VIOLATION", "severity": "warning", "source": "stat_assumptions", "message": vmsg, "fallback": "hc3_robust_standard_errors"})

        reconciliation.update({
            "missing_excluded": missing_excluded,
            "clean_source_points": ols["n"],
            "plotted_points_count": ols["n"],
            "points_clipped_outside_ylim": clipped,
            "all_raw_points_within_ylim": clipped == 0,
            "actual_ylim": actual_ylim,
            "x_cols": ols["x_cols"],
            "y_col": ols["y_col"],
            "r_squared": ols["r_squared"],
            "r_squared_adj": ols["r_squared_adj"],
            "rmse": ols["rmse"],
            "f_stat": ols["f_stat"],
            "f_p_value": ols["f_p_value"],
            "max_vif": ols["max_vif"],
            "vif": ols["vif"],
            "coefficients": ols["coefficients"],
            "diagnostics": ols["diagnostics"],
            "descriptive_stats": ols["descriptive_stats"],
        })
        readback_meta = {
            "a_xlo": a_xlo, "a_xhi": a_xhi, "a_ml": a_ml, "a_pw": a_pw,
            "b_lo": b_lo, "b_hi": b_hi, "b_ml": b_ml, "b_mt": p_mt, "b_pw": b_pw, "b_ph": p_ph,
            "expected_coefs": expected_coefs_meta, "expected_obs_pred": expected_obs_pred_meta,
        }
        mpl_payload = {
            "pred_coefs": pred_coefs, "y_obs": y_obs, "y_pred": y_pred,
            "a_xlo": a_xlo, "a_xhi": a_xhi, "b_lo": b_lo, "b_hi": b_hi, "ann1": ann1, "ann2": ann2,
        }

    elif chart_type == "scatter_regression":
        pts: list[tuple[int, float, float, str]] = []
        cats: list[str] = []
        for row_idx, r in enumerate(rows, start=1):
            xv, yv = _try_float(r.get(xcol)), _try_float(r.get(vcol))
            if xv is None or yv is None:
                missing_excluded += 1
                continue
            cat = str(r.get(gcol, "all")).strip() if (gcol and gcol in cols and gcol != xcol) else "all"
            if cat not in cats:
                cats.append(cat)
            pts.append((row_idx, xv, yv, cat))

        xs, ys = [p[1] for p in pts], [p[2] for p in pts]
        x_lo, x_hi = _compute_safe_ylim(xs, anchor_zero=False)
        y_lo, y_hi = _compute_safe_ylim(ys, anchor_zero=False)
        actual_ylim = [round(y_lo, 4), round(y_hi, 4)]
        slope, intercept, r_val = _pearson_r(xs, ys)

        def _xpx(v: float) -> float:
            return ml + pw * ((v - x_lo) / max(x_hi - x_lo, 1e-12))

        def _ypx(v: float) -> float:
            return mt + ph * (1.0 - (v - y_lo) / max(y_hi - y_lo, 1e-12))

        svg_lines.append(f'<line x1="{ml:.2f}" y1="{mt:.2f}" x2="{ml:.2f}" y2="{mt + ph:.2f}" stroke="#111827" stroke-width="1"/>')
        svg_lines.append(f'<line x1="{ml:.2f}" y1="{mt + ph:.2f}" x2="{ml + pw:.2f}" y2="{mt + ph:.2f}" stroke="#111827" stroke-width="1"/>')
        for tv in _nice_ticks(y_lo, y_hi, 5):
            yp = _ypx(tv)
            if mt - 1 <= yp <= mt + ph + 1:
                svg_lines.append(f'<line x1="{ml - 4:.2f}" y1="{yp:.2f}" x2="{ml:.2f}" y2="{yp:.2f}" stroke="#111827" stroke-width="0.9"/>')
                svg_lines.append(f'<text x="{ml - 7:.2f}" y="{yp + 3:.2f}" text-anchor="end">{html.escape(_fmt(tv))}</text>')
        for tv in _nice_ticks(x_lo, x_hi, 5):
            xp = _xpx(tv)
            if ml - 1 <= xp <= ml + pw + 1:
                svg_lines.append(f'<line x1="{xp:.2f}" y1="{mt + ph:.2f}" x2="{xp:.2f}" y2="{mt + ph + 4:.2f}" stroke="#111827" stroke-width="0.9"/>')
                svg_lines.append(f'<text x="{xp:.2f}" y="{mt + ph + 15:.2f}" text-anchor="middle">{html.escape(_fmt(tv))}</text>')

        cat_col = {c: colors[i % len(colors)] for i, c in enumerate(cats)}
        expected_scatter_pts: list[dict[str, Any]] = []
        for rid, xv, yv, cat in pts:
            col = cat_col[cat]
            svg_lines.append(
                f'<circle data-role="scatter-point" data-row-id="{rid}" data-group-id="{html.escape(cat)}" data-fill="{col}" '
                f'cx="{_xpx(xv):.2f}" cy="{_ypx(yv):.2f}" r="2.7" fill="{col}" fill-opacity="0.82" stroke="#FFFFFF" stroke-width="0.5"/>'
            )
            expected_scatter_pts.append({"row_id": rid, "group_id": cat, "x": xv, "y": yv, "fill": col})
        if len(xs) >= 2 and math.isfinite(r_val):
            x0_fit, x1_fit = min(xs), max(xs)
            svg_lines.append(f'<line x1="{_xpx(x0_fit):.2f}" y1="{_ypx(slope * x0_fit + intercept):.2f}" x2="{_xpx(x1_fit):.2f}" y2="{_ypx(slope * x1_fit + intercept):.2f}" stroke="#111827" stroke-width="1.3"/>')
            svg_lines.append(f'<text x="{ml + 8:.2f}" y="{mt + 14:.2f}" text-anchor="start">r = {r_val:.3f}, R² = {r_val * r_val:.3f} (n = {len(pts)})</text>')

        if len(cats) > 1:
            leg_y, lx = H - 6.0, ml + 12.0
            for c in cats[:6]:
                svg_lines.append(f'<circle cx="{lx:.2f}" cy="{leg_y - 3:.2f}" r="3" fill="{cat_col[c]}"/>')
                svg_lines.append(f'<text x="{lx + 6:.2f}" y="{leg_y:.2f}" text-anchor="start">{html.escape(c[:14])}</text>')
                lx += max(56.0, len(c[:14]) * 6.5 + 16.0)
            svg_lines.append(f'<text x="{panel_cx:.2f}" y="{mt + ph + 28:.2f}" text-anchor="middle">{html.escape(xlabel)}</text>')
        else:
            svg_lines.append(f'<text x="{panel_cx:.2f}" y="{H - 8:.2f}" text-anchor="middle">{html.escape(xlabel)}</text>')
        ym_lbl = mt + ph / 2.0
        svg_lines.append(f'<text x="14" y="{ym_lbl:.2f}" text-anchor="middle" transform="rotate(-90 14 {ym_lbl:.2f})">{html.escape(ylabel)}</text>')

        clipped = sum(1 for _, xv, yv, _ in pts if not (x_lo <= xv <= x_hi and y_lo <= yv <= y_hi))
        reconciliation.update({
            "missing_excluded": missing_excluded,
            "clean_source_points": len(pts),
            "plotted_points_count": len(pts),
            "points_clipped_outside_ylim": clipped,
            "all_raw_points_within_ylim": clipped == 0,
            "actual_xlim": [round(x_lo, 4), round(x_hi, 4)],
            "actual_ylim": actual_ylim,
            "ols_slope": round(slope, 6),
            "ols_intercept": round(intercept, 6),
            "pearson_r": round(r_val, 6) if math.isfinite(r_val) else None,
            "r_squared": round(r_val * r_val, 6) if math.isfinite(r_val) else None,
            "descriptive_stats": {xcol: describe_vector(xs, unit=pred_units.get(xcol, "")), vcol: describe_vector(ys, unit=unit)},
        })
        readback_meta = {
            "x_lo": x_lo, "x_hi": x_hi, "y_lo": y_lo, "y_hi": y_hi,
            "ml": ml, "mt": mt, "pw": pw, "ph": ph,
            "expected_points": expected_scatter_pts,
        }
        mpl_payload = {"pts": [(p[1], p[2], p[3]) for p in pts], "cats": cats, "cat_col": cat_col, "x_lo": x_lo, "x_hi": x_hi, "y_lo": y_lo, "y_hi": y_hi, "slope": slope, "intercept": intercept, "r_val": r_val}

    elif chart_type == "line_with_band":
        raw_x_vals = [str(r.get(xcol, "")).strip() for r in rows if str(r.get(xcol, "")).strip() != ""]
        numeric_x_mode = bool(raw_x_vals) and all(_try_float(v) is not None for v in raw_x_vals)
        x_label_by_num: dict[float, str] = {}
        x_num_by_label: dict[str, float] = {}
        if not numeric_x_mode:
            for idx_lbl, lbl in enumerate(list(dict.fromkeys(raw_x_vals))):
                x_num_by_label[lbl] = float(idx_lbl)
                x_label_by_num[float(idx_lbl)] = lbl

        series_map: dict[str, dict[float, list[float]]] = {}
        series_row_map: dict[str, dict[float, list[tuple[int, float]]]] = {}
        series_order: list[str] = []
        all_raw_y: list[float] = []
        for row_idx, r in enumerate(rows, start=1):
            raw_x = str(r.get(xcol, "")).strip()
            xv = _try_float(raw_x) if numeric_x_mode else x_num_by_label.get(raw_x)
            yv = _try_float(r.get(vcol))
            if xv is None or yv is None:
                missing_excluded += 1
                continue
            sname = str(r.get(series_col, "Series")).strip() if (series_col and series_col in cols) else "Series"
            if sname not in series_map:
                series_order.append(sname)
                series_map[sname] = {}
                series_row_map[sname] = {}
            series_map[sname].setdefault(xv, []).append(yv)
            series_row_map[sname].setdefault(xv, []).append((row_idx, yv))
            all_raw_y.append(yv)

        all_xs = sorted({xv for sm in series_map.values() for xv in sm.keys()})
        extrema = list(all_raw_y)
        line_summary: list[dict[str, Any]] = []
        for sname in series_order:
            for xv in sorted(series_map[sname].keys()):
                vals = series_map[sname][xv]
                m, sd = _mean_sd(vals)
                err = _error_halfwidth(vals, error_type)
                extrema.extend([m - err, m + err])
                line_summary.append({
                    "series": sname, "x": xv, "x_label": x_label_by_num.get(xv, _fmt(xv)), "n": len(vals),
                    "mean": round(m, 6), "sd": round(sd, 6), "band_low": round(m - err, 6), "band_high": round(m + err, 6),
                })

        x_lo, x_hi = _compute_safe_ylim(all_xs, anchor_zero=False)
        y_lo, y_hi = _compute_safe_ylim(extrema, anchor_zero=False)
        actual_ylim = [round(y_lo, 4), round(y_hi, 4)]

        def _xpx(v: float) -> float:
            return ml + pw * ((v - x_lo) / max(x_hi - x_lo, 1e-12))

        def _ypx(v: float) -> float:
            return mt + ph * (1.0 - (v - y_lo) / max(y_hi - y_lo, 1e-12))

        svg_lines.append(f'<line x1="{ml:.2f}" y1="{mt:.2f}" x2="{ml:.2f}" y2="{mt + ph:.2f}" stroke="#111827" stroke-width="1"/>')
        svg_lines.append(f'<line x1="{ml:.2f}" y1="{mt + ph:.2f}" x2="{ml + pw:.2f}" y2="{mt + ph:.2f}" stroke="#111827" stroke-width="1"/>')
        if y_lo < 0.0 < y_hi:
            y0 = _ypx(0.0)
            svg_lines.append(f'<line x1="{ml:.2f}" y1="{y0:.2f}" x2="{ml + pw:.2f}" y2="{y0:.2f}" stroke="#4B5563" stroke-width="0.9" stroke-dasharray="4,3"/>')

        for tv in _nice_ticks(y_lo, y_hi, 5):
            yp = _ypx(tv)
            if mt - 1 <= yp <= mt + ph + 1:
                svg_lines.append(f'<line x1="{ml - 4:.2f}" y1="{yp:.2f}" x2="{ml:.2f}" y2="{yp:.2f}" stroke="#111827" stroke-width="0.9"/>')
                svg_lines.append(f'<text x="{ml - 7:.2f}" y="{yp + 3:.2f}" text-anchor="end">{html.escape(_fmt(tv))}</text>')
        for xv in all_xs:
            xp = _xpx(xv)
            svg_lines.append(f'<line x1="{xp:.2f}" y1="{mt + ph:.2f}" x2="{xp:.2f}" y2="{mt + ph + 4:.2f}" stroke="#111827" stroke-width="0.9"/>')
            svg_lines.append(f'<text x="{xp:.2f}" y="{mt + ph + 15:.2f}" text-anchor="middle">{html.escape(x_label_by_num.get(xv, _fmt(xv)))}</text>')

        expected_means_meta: list[dict[str, Any]] = []
        expected_bands_meta: list[dict[str, Any]] = []
        for si, sname in enumerate(series_order):
            col = colors[si % len(colors)]
            sid_esc = html.escape(sname)
            s_rows = [r for r in line_summary if r["series"] == sname]
            if len(s_rows) >= 2:
                upper = [f"{_xpx(r['x']):.2f},{_ypx(r['band_high']):.2f}" for r in s_rows]
                lower = [f"{_xpx(r['x']):.2f},{_ypx(r['band_low']):.2f}" for r in reversed(s_rows)]
                svg_lines.append(
                    f'<polygon data-role="series-band" data-series-id="{sid_esc}" data-fill="{col}" '
                    f'points="{" ".join(upper + lower)}" fill="{col}" fill-opacity="0.20" stroke="none"/>'
                )
                expected_bands_meta.append({
                    "series_id": sname,
                    "fill": col,
                    "vertices": [[r["x"], r["band_high"]] for r in s_rows] + [[r["x"], r["band_low"]] for r in reversed(s_rows)],
                })
                line_pts = " ".join(f"{_xpx(r['x']):.2f},{_ypx(r['mean']):.2f}" for r in s_rows)
                svg_lines.append(f'<polyline data-role="series-line" data-series-id="{sid_esc}" points="{line_pts}" fill="none" stroke="{col}" stroke-width="1.6"/>')
            for xidx, r in enumerate(s_rows):
                svg_lines.append(
                    f'<circle data-role="series-mean" data-series-id="{sid_esc}" data-x-index="{xidx}" data-fill="{col}" '
                    f'cx="{_xpx(r["x"]):.2f}" cy="{_ypx(r["mean"]):.2f}" r="3.2" fill="{col}" stroke="#FFFFFF" stroke-width="0.6"/>'
                )
                expected_means_meta.append({
                    "series_id": sname, "x_index": xidx, "x": r["x"], "mean": r["mean"],
                    "band_low": r["band_low"], "band_high": r["band_high"], "fill": col,
                })
            for xv, rpairs in series_row_map[sname].items():
                for rid, yv in rpairs:
                    svg_lines.append(
                        f'<circle data-role="raw-point" data-row-id="{rid}" data-series-id="{sid_esc}" data-fill="#1F2937" '
                        f'cx="{_xpx(xv):.2f}" cy="{_ypx(yv):.2f}" r="2.0" fill="#1F2937" fill-opacity="0.7"/>'
                    )

        svg_lines.append(f'<text x="{panel_cx:.2f}" y="{H - 8:.2f}" text-anchor="middle">{html.escape(xlabel)}</text>')
        ym_lbl = mt + ph / 2.0
        svg_lines.append(f'<text x="14" y="{ym_lbl:.2f}" text-anchor="middle" transform="rotate(-90 14 {ym_lbl:.2f})">{html.escape(ylabel)}</text>')

        clipped = sum(1 for v in all_raw_y if not (y_lo - 1e-9 <= v <= y_hi + 1e-9))
        reconciliation.update({
            "missing_excluded": missing_excluded,
            "clean_source_points": len(all_raw_y),
            "plotted_points_count": len(all_raw_y),
            "points_clipped_outside_ylim": clipped,
            "all_raw_points_within_ylim": clipped == 0,
            "actual_ylim": actual_ylim,
            "line_summaries": line_summary,
            "descriptive_stats": {vcol: describe_vector(all_raw_y, unit=unit)},
        })
        readback_meta = {
            "x_lo": x_lo, "x_hi": x_hi, "y_lo": y_lo, "y_hi": y_hi,
            "ml": ml, "mt": mt, "pw": pw, "ph": ph,
            "expected_means": expected_means_meta,
            "expected_bands": expected_bands_meta,
        }
        mpl_payload = {"series_order": series_order, "series_map": series_map, "line_summary": line_summary, "x_lo": x_lo, "x_hi": x_hi, "y_lo": y_lo, "y_hi": y_hi}

    elif chart_type == "heatmap":
        hm_cols = list(spec.get("matrix_cols") or spec.get("columns") or [])
        if not hm_cols:
            for c in cols:
                if c.lower().endswith(("_code", "_id", "id", "code")) or c.lower() in {"qc_pass", "expected_vintage_year"}:
                    continue
                if sum(_try_float(r.get(c)) is not None for r in rows) >= max(3, int(len(rows) * 0.5)):
                    hm_cols.append(c)
            hm_cols = hm_cols[:8]

        n_c = len(hm_cols)
        mat: list[list[float]] = [[1.0] * n_c for _ in range(n_c)]
        desc_hm: dict[str, Any] = {}
        for i in range(n_c):
            col_vals = [v for v in (_try_float(r.get(hm_cols[i])) for r in rows) if v is not None]
            desc_hm[hm_cols[i]] = describe_vector(col_vals, unit=pred_units.get(hm_cols[i], ""))
            for j in range(n_c):
                if i == j:
                    mat[i][j] = 1.0
                else:
                    xi, xj = [], []
                    for r in rows:
                        v1, v2 = _try_float(r.get(hm_cols[i])), _try_float(r.get(hm_cols[j]))
                        if v1 is not None and v2 is not None:
                            xi.append(v1)
                            xj.append(v2)
                    _, _, r_ij = _pearson_r(xi, xj)
                    mat[i][j] = round(r_ij, 4) if math.isfinite(r_ij) else 0.0

        vmin, vmax = -1.0, 1.0
        ramp = VERIFIED_PALETTES["diverging"] if sel["color_role"] == "diverging" else VERIFIED_PALETTES["sequential"]
        cw, ch = pw / max(n_c, 1), ph / max(n_c, 1)
        expected_cell_fills: list[list[str]] = [[""] * n_c for _ in range(n_c)]

        for i in range(n_c):
            for j in range(n_c):
                val = mat[i][j]
                t = (val - vmin) / max(vmax - vmin, 1e-12)
                fill_hx = _interp_hex(ramp, t)
                expected_cell_fills[i][j] = fill_hx
                txt_hx = best_text_color(fill_hx)
                x0, y0 = ml + j * cw, mt + i * ch
                cell_gid = html.escape(f"{hm_cols[i]}__{hm_cols[j]}")
                svg_lines.append(
                    f'<rect data-role="cell-rect" data-row-id="{i}" data-col-id="{j}" data-row="{i}" data-col="{j}" '
                    f'data-group-id="{cell_gid}" data-fill="{fill_hx}" x="{x0:.2f}" y="{y0:.2f}" width="{cw:.2f}" height="{ch:.2f}" '
                    f'fill="{fill_hx}" stroke="#FFFFFF" stroke-width="0.6"/>'
                )
                if n_c <= 8:
                    svg_lines.append(
                        f'<text data-role="cell-value" data-row-id="{i}" data-col-id="{j}" data-row="{i}" data-col="{j}" '
                        f'data-group-id="{cell_gid}" x="{x0 + cw / 2:.2f}" y="{y0 + ch / 2 + 3:.2f}" text-anchor="middle" fill="{txt_hx}" font-size="8">{val:.2f}</text>'
                    )
            rlbl = hm_cols[i][:13]
            svg_lines.append(f'<text x="{ml - 5:.2f}" y="{mt + (i + 0.5) * ch + 3:.2f}" text-anchor="end" font-size="8">{html.escape(rlbl)}</text>')
            tx, ty = ml + (i + 0.5) * cw, mt + ph + 10.0
            svg_lines.append(f'<text x="{tx:.2f}" y="{ty:.2f}" text-anchor="end" font-size="8" transform="rotate(-35 {tx:.2f} {ty:.2f})">{html.escape(rlbl)}</text>')

        cb_x, cb_w, n_steps = ml + pw + 10.0, 10.0, 20
        for s in range(n_steps):
            seg_h = ph / n_steps
            svg_lines.append(f'<rect x="{cb_x:.2f}" y="{mt + s * seg_h:.2f}" width="{cb_w:.2f}" height="{seg_h + 0.5:.2f}" fill="{_interp_hex(ramp, 1.0 - s / (n_steps - 1))}" stroke="none"/>')
        svg_lines.append(f'<rect x="{cb_x:.2f}" y="{mt:.2f}" width="{cb_w:.2f}" height="{ph:.2f}" fill="none" stroke="#111827" stroke-width="0.7"/>')
        for lbl_v, frac in ((vmax, 0.0), (0.0, 0.5), (vmin, 1.0)):
            svg_lines.append(f'<text x="{cb_x + cb_w + 4:.2f}" y="{mt + frac * ph + 3:.2f}" text-anchor="start" font-size="8">{lbl_v:.1f}</text>')

        reconciliation.update({
            "matrix_cols": hm_cols,
            "matrix_shape": [n_c, n_c],
            "colorbar_range": [vmin, vmax],
            "all_cells_within_colorbar": all(vmin - 1e-9 <= mat[i][j] <= vmax + 1e-9 for i in range(n_c) for j in range(n_c)),
            "matrix_values": mat,
            "descriptive_stats": desc_hm,
        })
        readback_meta = {"mat": mat, "matrix_cols": hm_cols, "expected_cell_fills": expected_cell_fills, "vmin": vmin, "vmax": vmax}
        mpl_payload = {"hm_cols": hm_cols, "mat": mat, "ramp": ramp, "vmin": vmin, "vmax": vmax}

    svg_lines.append("</svg>")
    svg_p = out_dir / f"{stem}.svg"
    svg_p.write_text("\n".join(svg_lines) + "\n", encoding="utf-8")

    # Persist identity-keyed element manifest to disk for auditability & adversarial regression
    manifest_p = out_dir / f"{stem}_element_manifest.json"
    manifest_payload = {
        "schema_version": "svg-element-manifest/v1",
        "svg_file": svg_p.name,
        "chart_type": chart_type,
        "source_file": tpath.name,
        "source_sha256": sha,
        "readback_meta": readback_meta,
    }
    manifest_p.write_text(json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    reconciliation["element_manifest_file"] = manifest_p.name

    # True disk readback verification against written SVG (identity + color + band; never sorted-y!)
    rb = _readback_svg_max_abs_diff(svg_p, chart_type, readback_meta)
    reconciliation.update(rb)
    base_ok = (
        reconciliation.get("points_clipped_outside_ylim", 0) == 0
        and reconciliation.get("all_error_bars_within_ylim", True)
        and reconciliation.get("all_cells_within_colorbar", True)
        and (reconciliation.get("plotted_points_count", 1) == reconciliation.get("clean_source_points", 1))
    )
    reconciliation["reconciled"] = bool(base_ok and rb["readback_ok"])

    artifacts = [svg_p.name, manifest_p.name]
    render_backend = "python_stdlib_svg"
    export_tier = "stdlib_vector_svg_fallback"

    if _plt is not None:
        render_backend = "matplotlib_plus_stdlib_svg"
        export_tier = "matplotlib_300dpi_png_pdf_svg"
        _plt.rcParams.update({
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
            "font.size": font_pt,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.linewidth": 0.8,
        })
        if chart_type == "multivariate_ols":
            fig, (ax1, ax2) = _plt.subplots(1, 2, figsize=(width_mm / 25.4, height_mm / 25.4), dpi=300)
            pcoefs = mpl_payload["pred_coefs"]
            ys_idx = list(range(len(pcoefs)))
            for idx_c, c in enumerate(pcoefs):
                sb = float(c["std_beta"])
                lo_b, hi_b = c["std_ci95_hc3"]
                col = colors[0] if sb >= 0 else colors[1 % len(colors)]
                ax1.errorbar(sb, idx_c, xerr=[[sb - lo_b], [hi_b - sb]], fmt="o", color=col, ecolor=col, capsize=3.0, markersize=5)
                ax1.text(hi_b + 0.03 * (mpl_payload["a_xhi"] - mpl_payload["a_xlo"]), idx_c, f"{sb:+.2f} {c['sig_hc3']} (VIF={c['vif']:.2f})", va="center", fontsize=max(6.5, font_pt - 1.2))
            ax1.axvline(0.0, color="#6B7280", linestyle="--", linewidth=0.8)
            ax1.set_yticks(ys_idx)
            ax1.set_yticklabels([c["term"] for c in pcoefs], fontsize=max(7.0, font_pt - 0.5))
            ax1.invert_yaxis()
            ax1.set_xlim(mpl_payload["a_xlo"], mpl_payload["a_xhi"])
            ax1.set_xlabel("Standardized beta (95% HC3 CI)")
            ax1.set_title("a  Standardized effects", loc="left", fontsize=font_pt, fontweight="normal")
            ax1.spines["top"].set_visible(False)
            ax1.spines["right"].set_visible(False)

            ax2.scatter(mpl_payload["y_pred"], mpl_payload["y_obs"], s=12, color=colors[0], alpha=0.65, edgecolor="#FFFFFF", linewidth=0.3)
            ax2.plot([mpl_payload["b_lo"], mpl_payload["b_hi"]], [mpl_payload["b_lo"], mpl_payload["b_hi"]], color="#6B7280", linestyle="--", linewidth=0.9)
            ax2.set_xlim(mpl_payload["b_lo"], mpl_payload["b_hi"])
            ax2.set_ylim(mpl_payload["b_lo"], mpl_payload["b_hi"])
            ax2.text(0.04, 0.94, mpl_payload["ann1"] + "\n" + mpl_payload["ann2"], transform=ax2.transAxes, va="top", fontsize=max(6.5, font_pt - 1.2))
            ax2.set_xlabel(f"Predicted {ylabel}")
            ax2.set_ylabel(f"Observed {ylabel}")
            ax2.set_title("b  Observed vs. predicted", loc="left", fontsize=font_pt, fontweight="normal")
            ax2.spines["top"].set_visible(False)
            ax2.spines["right"].set_visible(False)
            fig.suptitle(raw_title, fontsize=min(12.0, font_pt + 1.0), fontweight="normal", y=0.97)
            fig.subplots_adjust(left=0.17, right=0.97, top=0.84, bottom=0.18, wspace=0.34)
        else:
            fig, ax = _plt.subplots(figsize=(width_mm / 25.4, height_mm / 25.4), dpi=300)
            if chart_type in {"bar_with_points", "boxplot_points"}:
                groups = mpl_payload["groups"]
                arrays = mpl_payload["arrays"]
                srows = mpl_payload["summary_rows"]
                xs = list(range(len(groups)))
                if chart_type == "bar_with_points":
                    ax.bar(xs, [s["mean"] for s in srows], yerr=[s["err_halfwidth"] for s in srows], width=0.52, color=[colors[i % len(colors)] for i in xs], edgecolor="#1F2937", linewidth=0.75, capsize=3.0, alpha=0.75, zorder=2)
                else:
                    bp = ax.boxplot(arrays, positions=xs, widths=0.48, patch_artist=True, showfliers=False, medianprops={"color": "#111827", "linewidth": 1.3}, boxprops={"edgecolor": "#1F2937", "linewidth": 0.8}, whiskerprops={"color": "#1F2937", "linewidth": 0.8}, capprops={"color": "#1F2937", "linewidth": 0.8})
                    for patch, gi in zip(bp["boxes"], xs):
                        patch.set_facecolor(colors[gi % len(colors)])
                        patch.set_alpha(0.72)
                for gi, (arr, joffs) in enumerate(zip(arrays, mpl_payload["jitter_coords"])):
                    ax.scatter([gi + o for o in joffs], arr, s=16, color="#1F2937", edgecolor="#FFFFFF", linewidth=0.45, zorder=5)
                if mpl_payload["y_lo"] < 0.0 < mpl_payload["y_hi"]:
                    ax.axhline(0.0, color="#4B5563", linewidth=0.8, linestyle="--", zorder=1)
                span_m = mpl_payload["y_hi"] - mpl_payload["y_lo"]
                for bspec in mpl_payload.get("brackets", []):
                    ia, ib, yv = bspec["ia"], bspec["ib"], bspec["y_val"]
                    h_t = 0.02 * span_m
                    ax.plot([ia, ia, ib, ib], [yv - h_t, yv, yv, yv - h_t], color="#111827", linewidth=0.85)
                    ax.text(0.5 * (ia + ib), yv + 0.008 * span_m, bspec["label"], ha="center", va="bottom", fontsize=max(6.5, font_pt - 1.2))
                if mpl_payload.get("omni_txt"):
                    ax.text(0.02, 0.97, mpl_payload["omni_txt"], transform=ax.transAxes, va="top", fontsize=max(6.5, font_pt - 1.2), color="#374151")
                ax.set_ylim(mpl_payload["y_lo"], mpl_payload["y_hi"])
                ax.set_xticks(xs)
                ax.set_xticklabels([f"{s['group']}\n(n={s['n']})" for s in srows], fontsize=max(7.0, font_pt - 0.5))
                ax.set_xlabel(xlabel)
                ax.set_ylabel(ylabel)
                ax.spines["top"].set_visible(False)
                ax.spines["right"].set_visible(False)
                fig.subplots_adjust(left=0.17, right=0.96, top=0.87, bottom=0.20)
            elif chart_type == "scatter_regression":
                pts, cats, cat_col = mpl_payload["pts"], mpl_payload["cats"], mpl_payload["cat_col"]
                for c in cats:
                    sub = [(x, y) for x, y, cc in pts if cc == c]
                    ax.scatter([p[0] for p in sub], [p[1] for p in sub], s=18, color=cat_col[c], edgecolor="#FFFFFF", linewidth=0.45, alpha=0.85, label=c)
                if len(pts) >= 2 and math.isfinite(mpl_payload["r_val"]):
                    x0_f, x1_f = min(p[0] for p in pts), max(p[0] for p in pts)
                    ax.plot([x0_f, x1_f], [mpl_payload["slope"] * x0_f + mpl_payload["intercept"], mpl_payload["slope"] * x1_f + mpl_payload["intercept"]], color="#111827", linewidth=1.2)
                ax.set_xlim(mpl_payload["x_lo"], mpl_payload["x_hi"])
                ax.set_ylim(mpl_payload["y_lo"], mpl_payload["y_hi"])
                ax.set_xlabel(xlabel)
                ax.set_ylabel(ylabel)
                ax.spines["top"].set_visible(False)
                ax.spines["right"].set_visible(False)
                if len(cats) > 1:
                    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=min(len(cats), 4), frameon=False, fontsize=max(7.0, font_pt - 0.5))
                    fig.subplots_adjust(left=0.17, right=0.96, top=0.87, bottom=0.26)
                else:
                    fig.subplots_adjust(left=0.17, right=0.96, top=0.87, bottom=0.18)
            elif chart_type == "line_with_band":
                for si, sname in enumerate(mpl_payload["series_order"]):
                    col = colors[si % len(colors)]
                    s_rows = [r for r in mpl_payload["line_summary"] if r["series"] == sname]
                    ax.fill_between([r["x"] for r in s_rows], [r["band_low"] for r in s_rows], [r["band_high"] for r in s_rows], color=col, alpha=0.20, zorder=2)
                    ax.plot([r["x"] for r in s_rows], [r["mean"] for r in s_rows], marker="o", markersize=4.0, color=col, linewidth=1.4, label=sname, zorder=4)
                    for xv, vals in mpl_payload["series_map"][sname].items():
                        ax.scatter([xv] * len(vals), vals, s=11, color="#1F2937", alpha=0.65, zorder=5)
                if mpl_payload["y_lo"] < 0.0 < mpl_payload["y_hi"]:
                    ax.axhline(0.0, color="#4B5563", linewidth=0.8, linestyle="--", zorder=1)
                ax.set_xlim(mpl_payload["x_lo"], mpl_payload["x_hi"])
                ax.set_ylim(mpl_payload["y_lo"], mpl_payload["y_hi"])
                ax.set_xlabel(xlabel)
                ax.set_ylabel(ylabel)
                ax.spines["top"].set_visible(False)
                ax.spines["right"].set_visible(False)
                fig.subplots_adjust(left=0.17, right=0.96, top=0.87, bottom=0.18)
            elif chart_type == "heatmap":
                hm_cols, mat = mpl_payload["hm_cols"], mpl_payload["mat"]
                cmap = _LSC.from_list("cell_cvd", mpl_payload["ramp"]) if _LSC is not None else "RdBu_r"
                im = ax.imshow(mat, cmap=cmap, vmin=mpl_payload["vmin"], vmax=mpl_payload["vmax"], aspect="auto")
                ax.set_xticks(range(len(hm_cols)))
                ax.set_yticks(range(len(hm_cols)))
                ax.set_xticklabels(hm_cols, rotation=35, ha="right", fontsize=max(7.0, font_pt - 1.0))
                ax.set_yticklabels(hm_cols, fontsize=max(7.0, font_pt - 1.0))
                if len(hm_cols) <= 8:
                    for i in range(len(hm_cols)):
                        for j in range(len(hm_cols)):
                            v = mat[i][j]
                            t = (v - mpl_payload["vmin"]) / max(mpl_payload["vmax"] - mpl_payload["vmin"], 1e-12)
                            ax.text(j, i, f"{v:.2f}", ha="center", va="center", color=best_text_color(_interp_hex(mpl_payload["ramp"], t)), fontsize=max(6.5, font_pt - 1.0))
                fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                fig.subplots_adjust(left=0.22, right=0.88, top=0.87, bottom=0.24)
            ax.set_title(raw_title, loc="center", fontsize=min(12.0, font_pt + 1.0), fontweight="normal", pad=8.0)

        png_p = out_dir / f"{stem}.png"
        pdf_p = out_dir / f"{stem}.pdf"
        fig.savefig(png_p, format="png", dpi=300, bbox_inches=None)
        fig.savefig(pdf_p, format="pdf", bbox_inches=None)
        _plt.close(fig)
        artifacts = [png_p.name, svg_p.name, pdf_p.name, manifest_p.name]
    else:
        msg = "Matplotlib unavailable in runtime; exported editable vector SVG via stdlib fallback (300 dpi PNG/PDF and raster visual readback unavailable; geometry readback verified on SVG)."
        auto_uncertainty.append(msg)
        auto_gaps.append({"code": "EXPORT_MATPLOTLIB_UNAVAILABLE_FALLBACK", "severity": "warning", "source": "publication_viz_builder", "message": msg, "fallback": "stdlib_vector_svg_fallback"})

    # Honest unit check (never claim 'verified from source schema' when unresolved)
    if not unit and chart_type != "heatmap":
        umsg = f"Measurement unit for {vcol or ylabel!r} could not be resolved from task.md or source column schema."
        auto_uncertainty.append(umsg)
        auto_gaps.append({"code": "UNIT_UNRESOLVED", "severity": "warning", "source": "publication_viz_builder", "message": umsg, "fallback": "reported_as_unresolved"})

    # Standalone self-contained figure caption
    cap_p = out_dir / f"{stem}_caption.md"
    if "group_summaries" in reconciliation:
        group_n_str = ", ".join(f"{g['group']} (n={g['n']})" for g in reconciliation["group_summaries"])
    elif "clean_source_points" in reconciliation:
        group_n_str = f"n={reconciliation['clean_source_points']} complete observations"
    elif "matrix_shape" in reconciliation:
        group_n_str = f"{reconciliation['matrix_shape'][0]}x{reconciliation['matrix_shape'][1]} correlation matrix (n={len(rows)} deduplicated samples)"
    else:
        group_n_str = f"n={len(rows)}"

    unit_clause = (
        f" (unit: `{unit}`, source: `{unit_source}`)" if unit else
        " (dimensionless Pearson r in [-1, 1])" if chart_type == "heatmap" else
        " (unit unresolved from task.md or source schema; logged in gaps)"
    )

    if chart_type == "multivariate_ols":
        jb = reconciliation["diagnostics"]["jarque_bera"]
        bp = reconciliation["diagnostics"]["breusch_pagan"]
        unc_clause = (
            f"Panel a displays standardized OLS effects (beta*) with 95% HC3 heteroscedasticity-consistent CIs and VIF (max VIF={reconciliation['max_vif']}); "
            f"Panel b plots observed vs. predicted values (R^2={reconciliation['r_squared']}, R^2_adj={reconciliation['r_squared_adj']}, RMSE={reconciliation['rmse']})."
        )
        stat_ctx = f"Multivariate OLS with classical and HC3 SEs; residual Jarque-Bera={jb['jb_stat']} ({fmt_p(jb['p_value'])}), Breusch-Pagan LM={bp['lm_stat']} ({fmt_p(bp['p_value'])})."
    elif chart_type in {"bar_with_points", "boxplot_points"}:
        st = reconciliation["statistical_tests"]
        unc_clause = (
            f"Bars show means +/- {error_type} with individual observations overlaid (y-axis {actual_ylim}, y=0 baseline retained)."
            if chart_type == "bar_with_points" else
            f"Boxes show Q1-Q3 IQR with median line, 1.5x IQR whiskers, and all individual observations overlaid (y-axis {actual_ylim})."
        )
        stat_ctx = (
            f"Shapiro-Francia normality + Brown-Forsythe homoscedasticity checked; primary omnibus={st['primary_omnibus']} "
            f"(Welch F={st['welch_anova'].get('f_stat')}, {fmt_p(st['welch_anova'].get('p_value'))}; Kruskal-Wallis H={st['kruskal_wallis'].get('h_stat')}, {fmt_p(st['kruskal_wallis'].get('p_value'))}); "
            f"pairwise {st['primary_pairwise']} with Holm-Bonferroni correction annotated via brackets."
        )
    elif chart_type == "scatter_regression":
        unc_clause = f"Points show individual paired measurements with OLS line (r={reconciliation.get('pearson_r')}, R^2={reconciliation.get('r_squared')})."
        stat_ctx = spec.get("stat_summary", "Bivariate OLS association with full-sample numeric reconciliation.")
    elif chart_type == "line_with_band":
        unc_clause = f"Lines track group means across ordered {xlabel} with shaded +/- {error_type} bands and raw observations."
        stat_ctx = spec.get("stat_summary", "Ordered trajectory summary with raw replicate overlay.")
    else:
        unc_clause = "Color tiles show pairwise Pearson correlations ([-1.0, 1.0]) with WCAG contrast-adaptive numeric labels."
        stat_ctx = spec.get("stat_summary", "Pairwise Pearson correlation matrix on non-ID continuous variables.")

    cap_lines = [
        f"# Figure Caption: {raw_title}",
        "",
        f"**Summary**: {raw_title}. Visual encoding uses `{chart_type}` sized to `{width_mm} x {height_mm} mm` (`bbox_inches=None`, Arial/DejaVu Sans `{font_pt} pt`, CVD-safe `{sel['color_role']}` palette, export tier `{export_tier}`).",
        "",
        f"- **Measurement & Units**: `{ylabel or 'Pearson r'}`{unit_clause}.",
        f"- **Sample Size (`n`)**: {group_n_str}.",
        f"- **Data Hygiene**: {exact_dups} exact duplicate row(s) removed; {missing_excluded} missing/non-numeric entry(ies) excluded without zero-imputation.",
        f"- **Summary & Uncertainty Representation**: {unc_clause}",
        f"- **Statistical Context**: {stat_ctx}",
        f"- **Source-to-Visual Readback**: Parsed {reconciliation['readback_elements_verified']} written SVG elements via `{reconciliation['readback_method']}` (identity_verified=`{reconciliation.get('readback_identity_verified')}`, color_verified=`{reconciliation.get('readback_color_verified')}`, `max_abs_diff={reconciliation['max_abs_diff']}` <= tol `{reconciliation['readback_tolerance']}`).",
        f"- **Element Manifest**: `{manifest_p.name}`.",
        f"- **Source Provenance**: `{tpath.name}` (SHA-256: `{sha}`).",
    ]
    if chart_type == "multivariate_ols" and reconciliation.get("coefficients"):
        cap_lines += [
            "",
            "## Key Statistical Table: Regression Coefficients (OLS & HC3)",
            "",
            "| Term | Beta | SE (OLS) | SE (HC3) | 95% HC3 CI | Std Beta | p (HC3) | VIF | Unit |",
            "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |",
        ]
        for c in reconciliation["coefficients"]:
            ci_lo, ci_hi = c["ci95_hc3"]
            sb_s = f"{c['std_beta']:+.4f}" if c["std_beta"] is not None else "—"
            vif_s = f"{c['vif']:.4f}" if c["vif"] is not None else "—"
            cap_lines.append(
                f"| `{c['term']}` | {c['beta']:.6f} | {c['se_ols']:.6f} | {c['se_hc3']:.6f} | [{ci_lo:.4f}, {ci_hi:.4f}] | {sb_s} | {fmt_p(c['p_hc3'])} ({c['sig_hc3']}) | {vif_s} | {c['unit']} |"
            )
    elif chart_type in {"bar_with_points", "boxplot_points"}:
        cap_lines += [
            "",
            "## Key Statistical Table: Per-Group Descriptive Statistics",
            "",
            "| Group | n | Mean | SD | 95% CI | Median | IQR | Min–Max |",
            "| --- | ---: | ---: | ---: | --- | ---: | ---: | --- |",
        ]
        for g in reconciliation.get("group_summaries", []):
            cap_lines.append(
                f"| `{g['group']}` | {g['n']} | {g['mean']:.4f} | {g['sd']:.4f} | [{g['ci95_low']:.4f}, {g['ci95_high']:.4f}] | {g['median']:.4f} | {g['iqr']:.4f} | [{g['min']:.4f}, {g['max']:.4f}] |"
            )
        pws = (reconciliation.get("statistical_tests") or {}).get("pairwise_comparisons") or []
        if pws:
            cap_lines += [
                "",
                "## Key Statistical Table: Pairwise Comparisons (Holm-Bonferroni + Hedges' g + 95% CI)",
                "",
                "| Pair | Primary Test | Mean Diff | 95% CI (Mean Diff) | Hedges' g | p_raw | p_holm | Sig |",
                "| --- | --- | ---: | --- | ---: | ---: | ---: | --- |",
            ]
            for pr in pws:
                wt = pr.get("welch_t") or {}
                ci = wt.get("ci95") or [None, None]
                ci_s = f"[{ci[0]:.4f}, {ci[1]:.4f}]" if ci[0] is not None else "n/a"
                md_s = f"{wt['mean_diff']:+.4f}" if wt.get("mean_diff") is not None else "n/a"
                hg_s = f"{wt['hedges_g']:+.4f}" if wt.get("hedges_g") is not None else "n/a"
                cap_lines.append(
                    f"| `{pr['group_a']}` vs `{pr['group_b']}` | {pr['primary_test']} | {md_s} | {ci_s} | {hg_s} | {fmt_p(pr['p_raw'])} | {fmt_p(pr['p_holm'])} | {pr['sig']} |"
                )
    elif chart_type == "heatmap" and reconciliation.get("matrix_cols") and reconciliation.get("matrix_values"):
        mcols = reconciliation["matrix_cols"]
        mvals = reconciliation["matrix_values"]
        cap_lines += [
            "",
            f"## Key Statistical Table: {len(mcols)}x{len(mcols)} Pearson Correlation Matrix",
            "",
            "| Variable | " + " | ".join(f"`{c}`" for c in mcols) + " |",
            "| --- | " + " | ".join("---:" for _ in mcols) + " |",
        ]
        for cname, row_v in zip(mcols, mvals):
            cap_lines.append(f"| `{cname}` | " + " | ".join(f"{v:.4f}" for v in row_v) + " |")
    cap_p.write_text("\n".join(cap_lines) + "\n", encoding="utf-8")

    repro_name = _emit_reproduce_figure_script(
        out_dir=out_dir,
        table_name=tpath.name,
        sha256=sha,
        chart_type=chart_type,
        spec=spec,
        ylim=actual_ylim,
        sqlite_table=spec.get("sqlite_table"),
        stem=stem,
    )

    aud_p = out_dir / f"{stem}_audit.json"
    aud_payload = {
        "ok": bool(sel["ok"] and reconciliation.get("reconciled", False)),
        "render_backend": render_backend,
        "export_tier": export_tier,
        "runtime_env": {"python": platform.python_version(), "matplotlib_available": _plt is not None},
        "physical_canvas": {
            "width_mm": width_mm, "height_mm": height_mm, "font_size_pt": font_pt,
            "bbox_inches": None, "pdf_fonttype": 42, "svg_fonttype": "none",
            "title_alignment": "center_regular_weight",
        },
        "selection_audit": sel,
        "source_to_visual_reconciliation": reconciliation,
        "auto_gaps": auto_gaps,
        "auto_uncertainty": auto_uncertainty,
        "artifacts": artifacts + [cap_p.name, repro_name, aud_p.name],
    }
    aud_p.write_text(json.dumps(aud_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": aud_payload["ok"],
        "render_backend": render_backend,
        "export_tier": export_tier,
        "chart_type": chart_type,
        "actual_ylim": actual_ylim,
        "max_abs_diff": reconciliation.get("max_abs_diff"),
        "reconciled": reconciliation.get("reconciled"),
        "auto_gaps": auto_gaps,
        "auto_uncertainty": auto_uncertainty,
        "artifacts": aud_payload["artifacts"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dual-Backend Cell-baseline publication figure builder.")
    parser.add_argument("--spec", required=True, help="Path to viz_spec.json.")
    parser.add_argument("--out-dir", required=True, help="Output directory.")
    parser.add_argument("--stem", default="sci_figure", help="Output filename stem.")
    args = parser.parse_args(argv)

    sp = Path(args.spec).resolve()
    spec = json.loads(sp.read_text(encoding="utf-8-sig"))
    rep = build_publication_figure(spec, Path(args.out_dir), stem=args.stem, base_dir=sp.parent)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
