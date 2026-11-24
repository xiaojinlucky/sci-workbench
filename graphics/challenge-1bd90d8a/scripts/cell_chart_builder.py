#!/usr/bin/env python3
"""Publication-grade Cell-style scientific data chart builder (6 chart types, negative-safe, persistent colors, mm lock, suite audit)."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from contract_sealer import seal_case_result
from source_data_chart_guard import (
    DEFAULT_CELL_PALETTE,
    audit_source_data,
    load_or_update_style_map,
    load_tabular,
    to_float,
)
from portable_svg_audit import audit_portable_svg
from audit_editability import inspect_svg_only
from figure_suite_audit import audit_single_chart
from layout_tools import analyze_layout
import svg2drawio

matplotlib: Any = None
plt: Any = None
LinearSegmentedColormap: Any = None
TwoSlopeNorm: Any = None
GridSpec: Any = None
np: Any = None
audit_figure_json: Any = None
_CELL_DIVERGING: Any = None

CELL_WIDTHS_MM = {
    "single": 85.0,
    "cell_single": 85.0,
    "1.5col": 114.0,
    "cell_1_5col": 114.0,
    "double": 174.0,
    "cell_double": 174.0,
}

_MATPLOTLIB_SVG11_DOCTYPE_RE = re.compile(
    rb'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1\.1//EN"\s+'
    rb'"http://www\.w3\.org/Graphics/SVG/1\.1/DTD/svg11\.dtd">'
)


def _ensure_mpl_stack() -> None:
    """Lazily import matplotlib/numpy/sciglyph so CLI --help works cleanly in stdlib-only containers."""
    global matplotlib, plt, LinearSegmentedColormap, TwoSlopeNorm, GridSpec, np, audit_figure_json, _CELL_DIVERGING
    if plt is not None and np is not None and _CELL_DIVERGING is not None:
        return
    try:
        import matplotlib as _mpl
        _mpl.use("Agg")
        import matplotlib.pyplot as _plt
        from matplotlib.colors import LinearSegmentedColormap as _LSC, TwoSlopeNorm as _TSN
        from matplotlib.gridspec import GridSpec as _GS
        import numpy as _np
        from sciglyph.layout import audit_figure_json as _afj
    except ImportError as exc:
        raise RuntimeError(
            f"MISSING_MATPLOTLIB_DEPENDENCY: chart rendering requires matplotlib and numpy ({exc}); "
            "run source_data_chart_guard.py for stdlib-only data auditing or install matplotlib/numpy."
        ) from exc
    matplotlib, plt, LinearSegmentedColormap, TwoSlopeNorm, GridSpec, np, audit_figure_json = (
        _mpl, _plt, _LSC, _TSN, _GS, _np, _afj
    )
    _CELL_DIVERGING = _LSC.from_list("cell_diverging", ["#3F6FAE", "#F7F7F5", "#C6544D"], N=256)


def _strip_matplotlib_svg11_doctype(svg_path: Path) -> None:
    svg_bytes = svg_path.read_bytes()
    matches = list(_MATPLOTLIB_SVG11_DOCTYPE_RE.finditer(svg_bytes))
    if len(matches) > 1:
        raise ValueError("Generated SVG has multiple Matplotlib SVG 1.1 DOCTYPE declarations")
    if matches:
        svg_path.write_bytes(_MATPLOTLIB_SVG11_DOCTYPE_RE.sub(b"", svg_bytes, count=1))


def _mm_to_in(mm: float) -> float:
    return mm / 25.4


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wrap_label_lossless(text: str, max_chars: int = 28) -> str:
    """Wrap long pathway/gene names at natural delimiters without truncating text."""
    s = str(text).strip()
    if len(s) <= max_chars or "\n" in s:
        return s
    parts = re.split(r"(\s+|/|_|(?<=-))", s)
    lines: list[str] = []
    cur = ""
    for p in parts:
        if not p:
            continue
        if cur and len(cur) + len(p) > max_chars:
            lines.append(cur.rstrip())
            cur = p.lstrip()
        else:
            cur += p
    if cur.strip():
        lines.append(cur.rstrip())
    return "\n".join(lines) if lines else s


def _configure_fonts_and_style(spec: dict[str, Any], text_sample: str) -> None:
    style_path = ASSETS / "evident.mplstyle"
    if style_path.is_file():
        plt.style.use(str(style_path))
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text_sample)
    try:
        import matplotlib.font_manager as _fm
        installed = {f.name for f in _fm.fontManager.ttflist}
    except Exception:
        installed = set()
    cjk_cands = ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Microsoft YaHei", "SimHei"]
    latin_cands = ["Arial", "Liberation Sans", "DejaVu Sans"]
    cjk_avail = [f for f in cjk_cands if f in installed]
    sans_stack = (cjk_avail + latin_cands) if (has_cjk and cjk_avail) else (latin_cands + (cjk_avail or cjk_cands))
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": sans_stack,
        "font.size": float(spec.get("base_font_pt", 7.5)),
        "axes.titlesize": float(spec.get("title_font_pt", 8.5)),
        "axes.labelsize": float(spec.get("label_font_pt", 8.0)),
        "xtick.labelsize": float(spec.get("tick_font_pt", 7.0)),
        "ytick.labelsize": float(spec.get("tick_font_pt", 7.0)),
        "axes.linewidth": 0.75,
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "savefig.bbox": "standard",
        "savefig.pad_inches": 0.0,
        "axes.unicode_minus": False,
    })


def _place_bottom_legend_with_gap(
    fig: plt.Figure,
    ax: plt.Axes,
    handles: list[Any],
    labels: list[str],
    *,
    target_gap_pt: float = 4.0,
    fontsize: float = 7.0,
) -> dict[str, Any]:
    """Place a compact single-row legend 2-6 pt below the complete x-axis decorations."""
    if not handles:
        return {"placed": False, "gap_pt": None, "rows": 0}

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    fig_w_px, fig_h_px = fig.canvas.get_width_height()
    dpi = float(fig.dpi)

    def _x_bottom() -> float:
        yb = ax.get_window_extent(renderer).y0
        for lbl in ax.get_xticklabels():
            if lbl.get_text().strip():
                yb = min(yb, lbl.get_window_extent(renderer).y0)
        if ax.xaxis.label.get_text().strip():
            yb = min(yb, ax.xaxis.label.get_window_extent(renderer).y0)
        return yb

    x_bottom_px = _x_bottom()
    est_leg_h_px = (fontsize + 9.0) * dpi / 72.0
    gap_px = target_gap_pt * dpi / 72.0
    if x_bottom_px < est_leg_h_px + gap_px + 8.0:
        shift_px = (est_leg_h_px + gap_px + 10.0) - x_bottom_px
        pos = ax.get_position()
        new_y0 = min(0.42, pos.y0 + shift_px / max(fig_h_px, 1.0))
        if pos.y1 - new_y0 > 0.28:
            ax.set_position([pos.x0, new_y0, pos.width, pos.y1 - new_y0])
            fig.canvas.draw()
            renderer = fig.canvas.get_renderer()
            x_bottom_px = _x_bottom()

    ax_bb = ax.get_window_extent(renderer)
    leg_top_px = x_bottom_px - gap_px
    y_anchor_ax = (leg_top_px - ax_bb.y0) / max(ax_bb.height, 1.0)
    leg = ax.legend(
        handles, labels,
        loc="upper center", bbox_to_anchor=(0.5, y_anchor_ax),
        ncol=len(handles), frameon=False, fontsize=fontsize,
        columnspacing=1.1, handletextpad=0.45, borderaxespad=0.0,
    )
    fig.canvas.draw()
    leg_bb = leg.get_window_extent(renderer)
    measured_gap_pt = (x_bottom_px - leg_bb.y1) * 72.0 / dpi
    clipped = leg_bb.y0 < 2 or leg_bb.x0 < 2 or leg_bb.x1 > fig_w_px - 2
    return {
        "placed": True,
        "single_row_ncol": int(len(handles)),
        "measured_gap_pt": round(float(measured_gap_pt), 2),
        "gap_in_2_to_6pt_range": bool(1.5 <= float(measured_gap_pt) <= 6.5),
        "legend_clipped": bool(clipped),
    }


def _err_value(rec: dict[str, Any], err_type: str) -> float:
    et = err_type.upper().strip()
    if et == "SEM":
        return float(rec["sem"])
    if et in ("CI95", "95% CI", "95CI"):
        return float(rec["ci95"])
    return float(rec["sd"])


def _draw_panel_letter(fig: plt.Figure, ax: plt.Axes, panel_label: str) -> None:
    if not panel_label:
        return
    fig.text(
        0.025, 0.965, panel_label.strip(),
        fontsize=8.5, fontweight="bold", color="#111111", ha="left", va="top",
    )


def _draw_vector_colorbar(cax: plt.Axes, cmap: Any, norm: Any, label: str) -> None:
    """Draw a 100% pure-vector vertical colorbar (zero embedded raster <image> nodes)."""
    y_edges = np.linspace(norm.vmin, norm.vmax, 49)
    y_mids = 0.5 * (y_edges[:-1] + y_edges[1:])
    cax.pcolormesh(np.array([0.0, 1.0]), y_edges, y_mids[:, None], cmap=cmap, norm=norm, shading="flat", edgecolors="none")
    cax.set_xlim(0.0, 1.0)
    cax.set_ylim(norm.vmin, norm.vmax)
    cax.set_xticks([])
    cax.yaxis.tick_right()
    cax.yaxis.set_label_position("right")
    cax.set_ylabel(label, fontsize=7.0, labelpad=4.0)
    cax.tick_params(axis="y", labelsize=6.5, width=0.5, length=2.5)
    cax.grid(False)
    for sp in cax.spines.values():
        sp.set_linewidth(0.6)
        sp.set_color("#444444")


def _build_volcano_or_scatter(
    spec: dict[str, Any], table_path: Path, width_mm: float, height_mm: float, style_map_path: Path | None
) -> tuple[plt.Figure, plt.Axes, dict[str, Any], dict[str, str], dict[str, Any]]:
    cols, rows = load_tabular(table_path)
    x_col = str(spec["x_col"])
    y_col = str(spec["y_col"])
    label_col = str(spec.get("label_col", ""))
    y_transform = str(spec.get("y_transform", "none")).lower()
    x_thresh = float(spec.get("x_threshold", 1.0))
    y_thresh = float(spec.get("y_threshold", 1.30103))  # -log10(0.05)

    xs, ys, labels, row_lines = [], [], [], []
    missing_rows: list[int] = []
    for idx, r in enumerate(rows, start=2):
        xv = to_float(r.get(x_col, ""))
        yv = to_float(r.get(y_col, ""))
        if xv is None or yv is None:
            missing_rows.append(idx)
            continue
        if y_transform == "neg_log10":
            yv = -math.log10(max(yv, 1e-300))
        xs.append(xv)
        ys.append(yv)
        labels.append(r.get(label_col, "").strip() if label_col else f"R{idx}")
        row_lines.append(idx)

    if not xs:
        raise ValueError(f"No valid numeric rows for scatter_volcano ({x_col}, {y_col})")

    cats = []
    for xv, yv in zip(xs, ys):
        if yv >= y_thresh and xv >= x_thresh:
            cats.append("Up")
        elif yv >= y_thresh and xv <= -x_thresh:
            cats.append("Down")
        else:
            cats.append("NS")

    explicit = {"Up": "#D77C70", "Down": "#547FA3", "NS": "#B0B0B0"}
    explicit.update(spec.get("named_colors") or {})
    cmap = load_or_update_style_map(style_map_path, "volcano", ["Down", "NS", "Up"], explicit_colors=explicit)

    _configure_fonts_and_style(spec, " ".join(labels + [str(spec.get("title", "")), str(spec.get("xlabel", "")), str(spec.get("ylabel", ""))]))
    fig = plt.figure(figsize=(_mm_to_in(width_mm), _mm_to_in(height_mm)), dpi=300)
    ax = fig.add_axes([0.18, 0.25, 0.77, 0.62])

    for cname in ("NS", "Down", "Up"):
        pts_x = [x for x, c in zip(xs, cats) if c == cname]
        pts_y = [y for y, c in zip(ys, cats) if c == cname]
        if pts_x:
            ax.scatter(
                pts_x, pts_y, s=16 if cname != "NS" else 10,
                color=cmap[cname], alpha=0.88 if cname != "NS" else 0.55,
                edgecolors="#FFFFFF" if cname != "NS" else "none", linewidths=0.35,
                label=f"{cname} ({len(pts_x)})", zorder=4 if cname != "NS" else 2,
            )

    ax.axvline(x_thresh, color="#666666", linestyle="--", linewidth=0.7, zorder=1)
    ax.axvline(-x_thresh, color="#666666", linestyle="--", linewidth=0.7, zorder=1)
    ax.axhline(y_thresh, color="#666666", linestyle="--", linewidth=0.7, zorder=1)

    # Label top significant hits with clean vertical de-overlap spacing (no overlapping arrow tips)
    top_n = int(spec.get("label_top_n", 6))
    sig_idx = [i for i, c in enumerate(cats) if c in ("Up", "Down")]
    sig_idx.sort(key=lambda i: (ys[i], abs(xs[i])), reverse=True)
    placed_xy: list[tuple[float, float]] = []
    x_span = max(max(xs) - min(xs), 1.0)
    y_span = max(max(ys) - min(ys), 1.0)
    for i in sig_idx[:top_n]:
        lx, ly = xs[i], ys[i]
        dy = 0.045 * y_span
        for px, py in placed_xy:
            if abs(lx - px) < 0.20 * x_span and abs((ly + dy) - py) < 0.09 * y_span:
                dy += 0.09 * y_span
        placed_xy.append((lx, ly + dy))
        ax.text(lx, ly + dy, labels[i], fontsize=6.5, color="#1A1A1A", ha="center", va="bottom", zorder=6)

    ax.set_xlim(min(xs) - 0.12 * x_span, max(xs) + 0.12 * x_span)
    ax.set_ylim(max(-0.05 * y_span, min(ys) - 0.06 * y_span), max(ys) + 0.18 * y_span)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlabel(str(spec.get("xlabel", x_col)), labelpad=4.0)
    ax.set_ylabel(str(spec.get("ylabel", f"-log10({y_col})" if y_transform == "neg_log10" else y_col)), labelpad=4.0)
    if spec.get("title"):
        ax.set_title(str(spec["title"]), loc="center", fontweight="bold", pad=7.0)
    _draw_panel_letter(fig, ax, str(spec.get("panel_label", "")))

    handles, leg_labels = ax.get_legend_handles_labels()
    leg_info = _place_bottom_legend_with_gap(fig, ax, handles, leg_labels, target_gap_pt=4.0, fontsize=float(spec.get("tick_font_pt", 7.0)))
    prov_extra = {
        "chart_type": "scatter_volcano",
        "point_count": len(xs),
        "counts_by_category": {k: cats.count(k) for k in ("Up", "Down", "NS")},
        "thresholds": {"x_threshold": x_thresh, "y_threshold": y_thresh, "y_transform": y_transform},
        "missing_rows_excluded": missing_rows,
        "csv_lines_plotted": row_lines,
    }
    return fig, ax, prov_extra, cmap, leg_info


def _build_heatmap(
    spec: dict[str, Any], table_path: Path, width_mm: float, height_mm: float
) -> tuple[plt.Figure, plt.Axes, dict[str, Any], dict[str, str], dict[str, Any]]:
    cols, rows = load_tabular(table_path)
    row_col = str(spec.get("row_col", cols[0]))
    val_cols = list(spec.get("value_cols") or [c for c in cols if c != row_col])

    row_labels_orig: list[str] = []
    mat = np.full((len(rows), len(val_cols)), np.nan, dtype=float)
    na_count = 0
    zero_count = 0
    for ri, r in enumerate(rows):
        row_labels_orig.append(str(r.get(row_col, f"R{ri+1}")).strip())
        for ci, c in enumerate(val_cols):
            v = to_float(r.get(c, ""))
            if v is None:
                na_count += 1
            else:
                if v == 0.0:
                    zero_count += 1
                mat[ri, ci] = v

    wrap_chars = int(spec.get("wrap_label_chars", 24))
    row_labels_disp = [_wrap_label_lossless(x, wrap_chars) for x in row_labels_orig]
    col_labels_disp = [_wrap_label_lossless(x, 16) for x in val_cols]

    _configure_fonts_and_style(spec, " ".join(row_labels_orig + val_cols + [str(spec.get("title", ""))]))
    fig = plt.figure(figsize=(_mm_to_in(width_mm), _mm_to_in(height_mm)), dpi=300)

    # GridSpec locks vertical colorbar strictly aligned to the heatmap body top/height (Cell/PlotAgent rule)
    max_row_len = max((len(line) for lbl in row_labels_disp for line in lbl.splitlines()), default=8)
    left_margin = min(0.40, max(0.18, 0.08 + max_row_len * 0.011))
    gs = GridSpec(1, 2, figure=fig, width_ratios=[18, 1.15], wspace=0.08, left=left_margin, right=0.86, top=0.86, bottom=0.22)
    ax = fig.add_subplot(gs[0, 0])
    cax = fig.add_subplot(gs[0, 1])

    finite_vals = mat[np.isfinite(mat)]
    if finite_vals.size == 0:
        raise ValueError("Heatmap matrix contains no finite numeric values")
    scale_mode = str(spec.get("scale_mode", "diverging" if (finite_vals.min() < 0 < finite_vals.max()) else "sequential")).lower()
    if scale_mode == "diverging":
        center = float(spec.get("center", 0.0))
        vmax = float(spec.get("vmax", max(abs(finite_vals.min() - center), abs(finite_vals.max() - center), 1e-6)))
        vmin = float(spec.get("vmin", center - vmax))
        vmax_eff = float(spec.get("vmax", center + vmax))
        cmap = _CELL_DIVERGING.copy()
        norm = TwoSlopeNorm(vmin=vmin, vcenter=center, vmax=vmax_eff)
        scale_meta = {"mode": "diverging", "vmin": round(vmin, 4), "center": center, "vmax": round(vmax_eff, 4)}
    else:
        vmin = float(spec.get("vmin", float(finite_vals.min())))
        vmax_eff = float(spec.get("vmax", float(finite_vals.max())))
        cmap = plt.get_cmap("viridis").copy()
        norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax_eff)
        scale_meta = {"mode": "sequential", "vmin": round(vmin, 4), "vmax": round(vmax_eff, 4)}

    cmap.set_bad(color="#737373")  # Distinct NA grey separate from numeric 0.0
    n_r, n_c = mat.shape
    ax.pcolormesh(np.arange(n_c + 1), np.arange(n_r + 1), np.ma.masked_invalid(mat), cmap=cmap, norm=norm, shading="flat", edgecolors="none")
    ax.set_xlim(0, n_c)
    ax.set_ylim(n_r, 0)
    ax.grid(False)

    ax.set_xticks(np.arange(n_c) + 0.5)
    ax.set_xticklabels(col_labels_disp, rotation=25, ha="right")
    ax.set_yticks(np.arange(n_r) + 0.5)
    ax.set_yticklabels(row_labels_disp)
    for sp in ax.spines.values():
        sp.set_linewidth(0.6)
        sp.set_color("#444444")

    if bool(spec.get("annotate_cells", mat.size <= 36)):
        for ri in range(n_r):
            for ci in range(n_c):
                v = mat[ri, ci]
                txt = "NA" if not np.isfinite(v) else f"{v:.1f}"
                tcol = "#FFFFFF" if (not np.isfinite(v) or abs(v) > 0.6 * max(abs(vmin), abs(vmax_eff), 1e-6)) else "#111111"
                ax.text(ci + 0.5, ri + 0.5, txt, ha="center", va="center", fontsize=6.2, color=tcol)

    cb_label = str(spec.get("colorbar_label", spec.get("unit", "Value")))
    _draw_vector_colorbar(cax, cmap, norm, cb_label)

    if na_count > 0:
        from matplotlib.patches import Patch
        na_handle = [Patch(facecolor="#737373", edgecolor="#333333", linewidth=0.5, label="NA (missing)")]
        leg_info = _place_bottom_legend_with_gap(fig, ax, na_handle, ["NA (missing)"], target_gap_pt=4.0, fontsize=6.8)
    else:
        leg_info = {"placed": False, "gap_pt": None, "rows": 0}

    if spec.get("title"):
        ax.set_title(str(spec["title"]), loc="center", fontweight="bold", pad=7.0)
    _draw_panel_letter(fig, ax, str(spec.get("panel_label", "")))

    prov_extra = {
        "chart_type": "heatmap",
        "shape": [int(mat.shape[0]), int(mat.shape[1])],
        "na_cell_count": na_count,
        "explicit_zero_cell_count": zero_count,
        "scale": scale_meta,
        "colorbar_aligned_to_body": True,
        "row_label_mapping": dict(zip(row_labels_orig, row_labels_disp)),
        "col_label_mapping": dict(zip(val_cols, col_labels_disp)),
    }
    return fig, ax, prov_extra, {"NA": "#737373"}, leg_info


def _build_dotplot(
    spec: dict[str, Any], table_path: Path, width_mm: float, height_mm: float
) -> tuple[plt.Figure, plt.Axes, dict[str, Any], dict[str, str], dict[str, Any]]:
    cols, rows = load_tabular(table_path)
    term_col = str(spec["term_col"])
    x_col = str(spec["x_col"])
    size_col = str(spec["size_col"])
    color_col = str(spec["color_col"])
    enforce_top10 = bool(spec.get("enforce_top10", True))

    parsed = []
    for idx, r in enumerate(rows, start=2):
        xv = to_float(r.get(x_col, ""))
        sv = to_float(r.get(size_col, ""))
        cv = to_float(r.get(color_col, ""))
        if xv is None or sv is None or cv is None:
            continue
        parsed.append({"line": idx, "term": str(r.get(term_col, "")).strip(), "x": xv, "size": sv, "color": cv})
    if not parsed:
        raise ValueError("DotPlot table has no valid numeric rows")

    parsed.sort(key=lambda d: d["x"], reverse=True)
    shown = parsed[:10] if (enforce_top10 and len(parsed) > 10) else parsed
    shown_lines = {d["line"] for d in shown}
    for d in parsed:
        d["in_plot"] = d["line"] in shown_lines
    shown_rev = list(reversed(shown))

    _configure_fonts_and_style(spec, " ".join([d["term"] for d in shown_rev] + [str(spec.get("title", ""))]))
    fig = plt.figure(figsize=(_mm_to_in(width_mm), _mm_to_in(height_mm)), dpi=300)

    max_term = max((len(d["term"]) for d in shown_rev), default=12)
    left_m = min(0.46, max(0.24, 0.09 + max_term * 0.010))
    gs = GridSpec(2, 3, figure=fig, width_ratios=[14, 0.9, 2.5], height_ratios=[1.15, 1.0], wspace=0.22, hspace=0.32, left=left_m, right=0.92, top=0.86, bottom=0.18)
    ax = fig.add_subplot(gs[:, 0])
    cax = fig.add_subplot(gs[0, 1])
    sax = fig.add_subplot(gs[1, 1:])
    sax.axis("off")

    xs = np.array([d["x"] for d in shown_rev])
    ys = np.arange(len(shown_rev))
    sizes_raw = np.array([d["size"] for d in shown_rev])
    colors_raw = np.array([d["color"] for d in shown_rev])

    # Exact area-proportional scaling (s in pt^2)
    size_scale = float(spec.get("bubble_scale", 6.0))
    areas = np.maximum(18.0, sizes_raw * size_scale)

    c_norm = matplotlib.colors.Normalize(vmin=float(colors_raw.min()), vmax=float(colors_raw.max()))
    c_cmap = plt.get_cmap("viridis")
    ax.scatter(xs, ys, s=areas, c=colors_raw, cmap=c_cmap, norm=c_norm, edgecolors="#222222", linewidths=0.5, zorder=4)
    ax.set_yticks(ys)
    ax.set_yticklabels([d["term"] for d in shown_rev])
    ax.set_xlabel(str(spec.get("xlabel", x_col)), labelpad=4.0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.xaxis.grid(True, color="#E6E6E6", linewidth=0.5)
    ax.set_axisbelow(True)
    x_span = max(float(xs.max() - xs.min()), 1.0)
    ax.set_xlim(float(xs.min()) - 0.14 * x_span, float(xs.max()) + 0.14 * x_span)
    ax.set_ylim(-0.6, len(shown_rev) - 0.4)

    # Separate Guide Block 1: Pure-vector Continuous Colorbar
    _draw_vector_colorbar(cax, c_cmap, c_norm, str(spec.get("color_label", color_col)))

    # Separate Guide Block 2: Exact Area-Proportional Size Key
    q_vals = sorted({round(float(sizes_raw.min()), 1), round(float(np.median(sizes_raw)), 1), round(float(sizes_raw.max()), 1)})
    handles = [
        ax.scatter([], [], s=max(18.0, v * size_scale), facecolor="#CCCCCC", edgecolor="#222222", linewidth=0.5, label=f"{v:g}")
        for v in q_vals
    ]
    sax.legend(
        handles=handles, title=str(spec.get("size_label", size_col)),
        loc="center left", frameon=False, fontsize=6.5, title_fontsize=6.8, labelspacing=0.9, handletextpad=0.6,
    )

    if spec.get("title"):
        ax.set_title(str(spec["title"]), loc="center", fontweight="bold", pad=7.0)
    _draw_panel_letter(fig, ax, str(spec.get("panel_label", "")))

    prov_extra = {
        "chart_type": "dotplot",
        "total_terms": len(parsed),
        "plotted_terms": len(shown),
        "top10_cap_applied": enforce_top10 and len(parsed) > 10,
        "area_scale_formula": f"max(18.0, value * {size_scale})",
        "separate_guide_blocks": ["continuous_colorbar", "area_size_legend"],
        "rows_with_in_plot": parsed,
    }
    return fig, ax, prov_extra, {}, {"placed": False, "gap_pt": None, "rows": 0}


def build_chart(
    spec: dict[str, Any],
    out_dir: Path,
    *,
    stem: str = "data_chart",
    style_map_path: Path | None = None,
    restyle_from: str | None = None,
) -> dict[str, Any]:
    """Build a Cell-styled vector data chart (.svg/.drawio/.pdf/.png/_provenance.json) with full QA."""
    _ensure_mpl_stack()
    out_dir.mkdir(parents=True, exist_ok=True)
    table_path = Path(spec["table"])
    if not table_path.is_absolute() and spec.get("_base_dir"):
        table_path = Path(spec["_base_dir"]) / table_path

    chart_type = str(spec.get("chart_type", "bar_with_points")).lower().strip()
    col_preset = str(spec.get("column_preset", "cell_1_5col")).lower()
    width_mm = float(spec.get("width_mm", CELL_WIDTHS_MM.get(col_preset, 114.0)))
    height_mm = float(spec.get("height_mm", round(width_mm * 0.74, 1)))
    error_bar_type = str(spec.get("error_bar_type", "SD")).strip()

    prov: dict[str, Any] = {"ok": True, "warnings": [], "gaps": [], "groups": []}
    named_colors: dict[str, str] = {}

    if chart_type in ("scatter_volcano", "volcano", "scatter"):
        fig, ax, prov_extra, named_colors, leg_info = _build_volcano_or_scatter(spec, table_path, width_mm, height_mm, style_map_path)
    elif chart_type == "heatmap":
        fig, ax, prov_extra, named_colors, leg_info = _build_heatmap(spec, table_path, width_mm, height_mm)
    elif chart_type == "dotplot":
        fig, ax, prov_extra, named_colors, leg_info = _build_dotplot(spec, table_path, width_mm, height_mm)
    else:
        group_col = str(spec["group_col"])
        value_col = str(spec["value_col"])
        series_col = str(spec.get("series_col", ""))
        unit_col = str(spec.get("unit_col", ""))
        title = str(spec.get("title", "")).strip()
        xlabel = str(spec.get("xlabel", group_col)).strip()
        ylabel = str(spec.get("ylabel", value_col)).strip()
        unit = str(spec.get("unit", "")).strip()
        if unit and unit not in ylabel:
            ylabel = f"{ylabel} ({unit})"

        prov = audit_source_data(
            table_path,
            group_col=group_col,
            value_col=value_col,
            series_col=series_col,
            unit_col=unit_col,
            chart_type=chart_type,
            show_points=bool(spec.get("show_points", True)),
            show_error_bars=bool(spec.get("show_error_bars", True)),
            allow_summary_only=bool(spec.get("allow_summary_only", False)),
            fail_on_sentinel=bool(spec.get("fail_on_sentinel", False)),
            statistical_unit=str(spec.get("statistical_unit", "biological_replicate")),
        )
        if not prov["ok"]:
            return {"ok": False, "stage": "source_data_audit", "provenance": prov}

        groups_data = prov["groups"]
        group_names = list(spec.get("group_order") or dict.fromkeys(g["group"] for g in groups_data))
        series_names = list(spec.get("series_order") or dict.fromkeys(g["series"] for g in groups_data))
        text_sample = " ".join([title, xlabel, ylabel] + group_names + series_names)
        _configure_fonts_and_style(spec, text_sample)

        palette = list(spec.get("palette") or DEFAULT_CELL_PALETTE)
        color_map = load_or_update_style_map(
            style_map_path,
            domain=series_col or "series",
            entities=series_names,
            palette=palette,
            explicit_colors=spec.get("named_colors"),
        )
        named_colors = {f"{series_col or 'series'}/{k}": v for k, v in color_map.items()}
        lookup = {(g["group"], g["series"]): g for g in groups_data}

        fig = plt.figure(figsize=(_mm_to_in(width_mm), _mm_to_in(height_mm)), dpi=300)
        ax = fig.add_axes([0.19, 0.25, 0.77, 0.62])

        rng = np.random.default_rng(int(spec.get("jitter_seed", 42)))
        n_ser = max(1, len(series_names))
        x_pos = np.arange(len(group_names), dtype=float)
        bar_w = min(0.68 / n_ser, 0.36 if n_ser > 1 else 0.54)

        all_y_extents: list[float] = []
        bar_centers: dict[tuple[str, str], float] = {}

        if chart_type in ("bar_with_points", "bar"):
            for si, sname in enumerate(series_names):
                offset = (si - (n_ser - 1) / 2.0) * (bar_w * 1.08)
                means, errs, xs = [], [], []
                for gi, gname in enumerate(group_names):
                    rec = lookup.get((gname, sname))
                    if rec:
                        cx = float(x_pos[gi] + offset)
                        bar_centers[(gname, sname)] = cx
                        xs.append(cx)
                        means.append(rec["mean"])
                        ev = _err_value(rec, error_bar_type)
                        errs.append(ev)
                        all_y_extents.extend([rec["mean"] - ev, rec["mean"] + ev] + rec["raw_values"])
                        if spec.get("show_points", True):
                            jitter = rng.uniform(-bar_w * 0.22, bar_w * 0.22, size=len(rec["raw_values"]))
                            ax.scatter(
                                np.full(len(rec["raw_values"]), cx) + jitter, rec["raw_values"],
                                s=13, color="#222222", alpha=0.72, linewidths=0.35, edgecolors="#FFFFFF", zorder=5,
                            )
                lbl_str = sname if (sname != "default" and len(series_names) > 1) else (spec.get("series_label") or value_col)
                ax.bar(
                    xs, means, width=bar_w, color=color_map[sname], edgecolor="#2B2B2B", linewidth=0.65,
                    alpha=0.88, label=lbl_str, zorder=3,
                )
                if spec.get("show_error_bars", True):
                    ax.errorbar(
                        xs, means, yerr=errs, fmt="none", ecolor="#222222", elinewidth=0.8, capsize=2.4, zorder=4,
                    )

            # Negative-value safe y-axis: NEVER clip negative values at bottom=0.0!
            y_min = min(all_y_extents) if all_y_extents else 0.0
            y_max = max(all_y_extents) if all_y_extents else 1.0
            y_span = max(y_max - min(0.0, y_min), 1e-6)
            if y_min < 0.0:
                ax.axhline(0.0, color="#222222", linewidth=0.75, zorder=2)
                ax.set_ylim(y_min - 0.14 * y_span, max(0.0, y_max) + 0.18 * y_span)
            else:
                ax.set_ylim(0.0, y_max + 0.18 * y_span)

            # Optional source-bound significance brackets (never invented!)
            sig_specs = spec.get("significance") or []
            if sig_specs:
                y_cur = max(all_y_extents) + 0.06 * y_span
                step = 0.09 * y_span
                for sitem in sig_specs:
                    ga, gb = str(sitem["group_a"]), str(sitem["group_b"])
                    sa = str(sitem.get("series_a", series_names[0]))
                    sb = str(sitem.get("series_b", series_names[0]))
                    if (ga, sa) in bar_centers and (gb, sb) in bar_centers:
                        x1, x2 = bar_centers[(ga, sa)], bar_centers[(gb, sb)]
                        h_tick = 0.025 * y_span
                        ax.plot([x1, x1, x2, x2], [y_cur, y_cur + h_tick, y_cur + h_tick, y_cur], color="#222222", lw=0.75, zorder=6)
                        ax.text((x1 + x2) / 2.0, y_cur + h_tick + 0.015 * y_span, str(sitem["p_label"]), ha="center", va="bottom", fontsize=6.8, color="#111111")
                        y_cur += step
                    else:
                        prov.setdefault("gaps", []).append({
                            "code": "SIGNIFICANCE_TARGET_UNMATCHED",
                            "severity": "warning",
                            "message": f"Significance comparison ({ga}/{sa} vs {gb}/{sb}) did not match plotted bars; never silently dropped.",
                        })
                lo, hi = ax.get_ylim()
                if y_cur + 0.06 * y_span > hi:
                    ax.set_ylim(lo, y_cur + 0.08 * y_span)

            ax.set_xticks(x_pos)
            ax.set_xticklabels(group_names)

        elif chart_type in ("box_with_points", "box", "violin_with_points", "violin"):
            positions, vals_list, cols_list = [], [], []
            for gi, gname in enumerate(group_names):
                for si, sname in enumerate(series_names):
                    rec = lookup.get((gname, sname))
                    if rec:
                        pos = float(x_pos[gi] + (si - (n_ser - 1) / 2.0) * (bar_w * 1.1))
                        positions.append(pos)
                        vals_list.append(rec["raw_values"])
                        cols_list.append(color_map[sname])
                        all_y_extents.extend(rec["raw_values"])
                        jitter = rng.uniform(-bar_w * 0.16, bar_w * 0.16, size=len(rec["raw_values"]))
                        ax.scatter(
                            np.full(len(rec["raw_values"]), pos) + jitter, rec["raw_values"],
                            s=13, color="#222222", alpha=0.72, zorder=5,
                        )
            if "violin" in chart_type:
                vp = ax.violinplot(vals_list, positions=positions, widths=bar_w * 0.9, showmeans=False, showmedians=True, showextrema=False)
                for body, c in zip(vp["bodies"], cols_list):
                    body.set_facecolor(c)
                    body.set_edgecolor("#333333")
                    body.set_linewidth(0.7)
                    body.set_alpha(0.75)
                if "cmedians" in vp:
                    vp["cmedians"].set_color("#111111")
                    vp["cmedians"].set_linewidth(1.1)
            else:
                bp = ax.boxplot(vals_list, positions=positions, widths=bar_w * 0.84, patch_artist=True, showfliers=False)
                for patch, c in zip(bp["boxes"], cols_list):
                    patch.set_facecolor(c)
                    patch.set_edgecolor("#333333")
                    patch.set_linewidth(0.75)
                for med in bp["medians"]:
                    med.set_color("#111111")
                    med.set_linewidth(1.1)
            from matplotlib.patches import Patch
            for sname in series_names:
                ax.plot([], [], color="none")  # keep axes clean
            if len(series_names) > 1:
                handles = [Patch(facecolor=color_map[s], edgecolor="#333333", linewidth=0.6, label=s) for s in series_names]
                leg_labels = list(series_names)
            else:
                handles, leg_labels = [], []
            if all_y_extents and min(all_y_extents) < 0.0 < max(all_y_extents):
                ax.axhline(0.0, color="#666666", linestyle="--", linewidth=0.65, zorder=1)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(group_names)

        elif chart_type in ("line_with_band", "line"):
            for si, sname in enumerate(series_names):
                xs, means, errs = [], [], []
                for gi, gname in enumerate(group_names):
                    rec = lookup.get((gname, sname))
                    if rec:
                        xs.append(float(x_pos[gi]))
                        means.append(rec["mean"])
                        ev = _err_value(rec, error_bar_type)
                        errs.append(ev)
                        all_y_extents.extend([rec["mean"] - ev, rec["mean"] + ev])
                        if spec.get("show_points", False):
                            ax.scatter(np.full(len(rec["raw_values"]), x_pos[gi]), rec["raw_values"], s=10, color=color_map[sname], alpha=0.5, zorder=3)
                xs_a, m_a, e_a = np.array(xs), np.array(means), np.array(errs)
                lbl_str = sname if (sname != "default" and len(series_names) > 1) else value_col
                ax.plot(xs_a, m_a, marker="o", markersize=4.0, linewidth=1.15, color=color_map[sname], label=lbl_str, zorder=4)
                ax.fill_between(xs_a, m_a - e_a, m_a + e_a, color=color_map[sname], alpha=0.18, zorder=2)
            if all_y_extents and min(all_y_extents) < 0.0 < max(all_y_extents):
                ax.axhline(0.0, color="#666666", linestyle="--", linewidth=0.65, zorder=1)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(group_names)
        else:
            return {"ok": False, "error": f"Unsupported chart_type {chart_type!r}"}

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        if bool(spec.get("show_grid", False)):
            ax.yaxis.grid(True, color="#E8E8E8", linewidth=0.5)
            ax.set_axisbelow(True)
        ax.set_xlabel(xlabel, labelpad=4.0)
        ax.set_ylabel(ylabel, labelpad=4.0)
        if title:
            ax.set_title(title, loc="center", fontweight="bold", pad=7.0)
        _draw_panel_letter(fig, ax, str(spec.get("panel_label", "")))

        if chart_type not in ("box_with_points", "box", "violin_with_points", "violin"):
            handles, leg_labels = ax.get_legend_handles_labels()
        leg_info = _place_bottom_legend_with_gap(fig, ax, handles, leg_labels, target_gap_pt=4.0, fontsize=float(spec.get("tick_font_pt", 7.0)))
        prov_extra = {
            "chart_type": chart_type,
            "figure_data_profile": prov.get("figure_data_profile"),
            "groups": prov["groups"],
        }

    # Pre-export sciglyph collision & glyph check
    layout_rep = audit_figure_json(fig, ax)

    svg_p = out_dir / f"{stem}.svg"
    pdf_p = out_dir / f"{stem}.pdf"
    png_p = out_dir / f"{stem}.png"
    drawio_p = out_dir / f"{stem}.drawio"
    prov_p = out_dir / f"{stem}_provenance.json"

    # Locked physical dimensions: NEVER use bbox_inches="tight"
    fig.savefig(svg_p, format="svg", bbox_inches=None)
    _strip_matplotlib_svg11_doctype(svg_p)
    try:
        fig.savefig(pdf_p, format="pdf", bbox_inches=None)
    except ValueError:
        plt.rcParams["pdf.fonttype"] = 3
        fig.savefig(pdf_p, format="pdf", bbox_inches=None)
    fig.savefig(png_p, format="png", dpi=300, bbox_inches=None)
    plt.close(fig)

    svg2drawio.svg_to_drawio_file(svg_p, drawio_p)
    portable = audit_portable_svg(svg_p)
    editability = inspect_svg_only(svg_p)
    drawio_chk = analyze_layout(drawio_p.read_text(encoding="utf-8"), min_spacing=1.5)

    declared_w_in, declared_h_in = editability["svg"]["dimensions_inches"]
    actual_w_mm = round(float(declared_w_in) * 25.4, 2)
    actual_h_mm = round(float(declared_h_in) * 25.4, 2)
    phys_match = bool(abs(actual_w_mm - float(width_mm)) <= 1.5 and abs(actual_h_mm - float(height_mm)) <= 1.5)

    # Unified bidirectional mark_id -> {figure, source_rows, column} map across all 6 chart types
    mark_to_row: dict[str, dict[str, Any]] = {}
    if prov_extra.get("groups"):
        vcol = str(spec.get("value_col", ""))
        for idx_g, rec in enumerate(prov_extra["groups"], 1):
            m_id = f"{stem}:{chart_type}:{rec.get('group')}:{rec.get('series')}:{idx_g}"
            rows_1b = list(rec.get("source_rows") or (rec.get("provenance") or {}).get("csv_lines") or [])
            mark_to_row[m_id] = {
                "figure": stem,
                "chart_type": chart_type,
                "group": rec.get("group"),
                "series": rec.get("series"),
                "column": vcol,
                "source_rows": rows_1b,
                "n": rec.get("n"),
            }
    elif prov_extra.get("csv_lines_plotted"):
        mark_to_row[f"{stem}:{chart_type}:points"] = {
            "figure": stem,
            "chart_type": chart_type,
            "column": f"{spec.get('x_col', '')},{spec.get('y_col', '')}",
            "source_rows": list(prov_extra["csv_lines_plotted"]),
            "n": len(prov_extra["csv_lines_plotted"]),
        }
    elif prov_extra.get("rows_with_in_plot"):
        for rinfo in prov_extra["rows_with_in_plot"]:
            if rinfo.get("in_plot"):
                mark_to_row[f"{stem}:dotplot:{rinfo.get('term')}"] = {
                    "figure": stem,
                    "chart_type": "dotplot",
                    "column": f"{spec.get('x_col', '')},{spec.get('size_col', '')},{spec.get('color_col', '')}",
                    "source_rows": [int(rinfo["line"])],
                }
    elif prov_extra.get("row_label_mapping"):
        for idx_r, (orig_lbl, disp_lbl) in enumerate(prov_extra["row_label_mapping"].items(), start=2):
            mark_to_row[f"{stem}:heatmap:row:{disp_lbl}"] = {
                "figure": stem,
                "chart_type": "heatmap",
                "column": ",".join(list((prov_extra.get("col_label_mapping") or {}).keys())),
                "source_rows": [idx_r],
                "original_row_label": orig_lbl,
            }

    prov_payload = {
        "source_table": str(table_path.name),
        "source_table_sha256": _sha256(table_path),
        "declared_width_mm": float(width_mm),
        "declared_height_mm": float(height_mm),
        "actual_svg_width_mm": actual_w_mm,
        "actual_svg_height_mm": actual_h_mm,
        "physical_dimension_locked": phys_match,
        "error_bar_type": error_bar_type,
        "bbox_mode": "standard",
        "named_colors": named_colors,
        "legend_audit": leg_info,
        "restyle_from": restyle_from,
        "warnings": prov.get("warnings", []),
        "gaps": prov.get("gaps", []),
        "mark_to_row_provenance": mark_to_row,
        **prov_extra,
    }
    _def = lambda o: o.item() if hasattr(o, "item") else str(o)
    prov_p.write_text(json.dumps(prov_payload, indent=2, ensure_ascii=False, default=_def) + "\n", encoding="utf-8")

    suite_rep = audit_single_chart(
        svg_p, png_path=png_p, drawio_path=drawio_p, provenance_path=prov_p,
        declared_width_mm=width_mm, declared_height_mm=height_mm,
    )

    # Dense scatter clouds & adjacent pcolormesh gradient strips naturally share edges;
    # keep strict all-object overlap check for bar/box/line and strict text/OOB check for all chart types.
    is_dense_geom = chart_type in ("scatter_volcano", "volcano", "scatter", "heatmap", "dotplot")
    drawio_blocking_overlaps = (
        [ov for ov in drawio_chk["overlaps"] if ov.get("involves_text")] if is_dense_geom else drawio_chk["overlaps"]
    )

    ok = bool(
        layout_rep["passed"]
        and portable["passed"]
        and editability["status"] == "pass"
        and phys_match
        and suite_rep["status"] != "fail"
        and len(drawio_blocking_overlaps) == 0
        and len(drawio_chk["out_of_bounds"]) == 0
    )
    return {
        "ok": ok,
        "chart_type": chart_type,
        "declared_width_mm": width_mm,
        "declared_height_mm": height_mm,
        "actual_svg_width_mm": actual_w_mm,
        "actual_svg_height_mm": actual_h_mm,
        "physical_dimension_locked": phys_match,
        "legend_audit": leg_info,
        "sciglyph_layout": layout_rep,
        "portable_svg": portable,
        "editability": editability,
        "drawio_layout": drawio_chk,
        "suite_audit": suite_rep,
        "gaps": prov.get("gaps", []),
        "mark_to_row_count": len(mark_to_row),
        "artifacts": [svg_p.name, drawio_p.name, png_p.name, pdf_p.name, prov_p.name],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or restyle Cell-style source-bound data chart + .svg/.drawio/.png/.pdf.")
    parser.add_argument("--spec", help="Chart specification JSON")
    parser.add_argument("--restyle", help="Existing spec JSON to revise in place without losing provenance")
    parser.add_argument("--override-json", default="", help="JSON overrides applied in --restyle mode")
    parser.add_argument("--new-table", default="", help="Updated source table path for --restyle mode")
    parser.add_argument("--style-map", default="", help="Persistent figure_style.json path for cross-panel named colors")
    parser.add_argument("--case-json", default="", help="Optional /input/case.json path to auto-seal result.json preserving output_contract")
    parser.add_argument("--result-out", default="", help="Optional output path for sealed result.json")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--stem", default="data_chart")
    args = parser.parse_args(argv)

    spec_src = args.restyle or args.spec
    if not spec_src:
        parser.error("Provide --spec or --restyle")
    spec_p = Path(spec_src).resolve()
    spec = json.loads(spec_p.read_text(encoding="utf-8-sig"))
    spec["_base_dir"] = str(spec_p.parent)

    restyle_from = None
    if args.restyle:
        restyle_from = f"{spec_p.name}@{_sha256(spec_p)[:12]}"
        if args.override_json:
            ov = json.loads(args.override_json)
            if isinstance(ov, dict):
                spec.update(ov)
        if args.new_table:
            spec["table"] = str(Path(args.new_table).resolve())

    style_p = Path(args.style_map) if args.style_map else None
    out_dir = Path(args.out_dir)
    try:
        res = build_chart(spec, out_dir, stem=args.stem, style_map_path=style_p, restyle_from=restyle_from)
    except RuntimeError as exc:
        msg = str(exc)
        err_code = "MISSING_MATPLOTLIB_DEPENDENCY" if "MISSING_MATPLOTLIB_DEPENDENCY" in msg else "BUILD_RUNTIME_ERROR"
        print(json.dumps({"ok": False, "error": err_code, "message": msg}, indent=2, ensure_ascii=False))
        return 2

    if args.case_json:
        case_p = Path(args.case_json)
        result_p = Path(args.result_out) if args.result_out else (out_dir / "result.json")
        uncert = [w for w in list((res.get("portable_svg") or {}).get("warnings") or []) if "CJK" in w]
        for g in (res.get("gaps") or []):
            uncert.append(f"{g.get('code', 'GAP')}: {g.get('message', '')}" if isinstance(g, dict) else str(g))
        res["result_seal"] = seal_case_result(
            case_p, result_p, task_id="source-bound-scientific-chart-builder",
            artifacts=list(res.get("artifacts") or []),
            sources=[str(spec.get("table", "")), str(spec_p.name)],
            uncertainty=uncert,
            change_records=[{"op": "restyle_chart" if restyle_from else "build_chart", "chart_type": res.get("chart_type"), "restyle_from": restyle_from}],
            metrics={"declared_width_mm": res.get("declared_width_mm"), "actual_svg_width_mm": res.get("actual_svg_width_mm"), "physical_dimension_locked": res.get("physical_dimension_locked"), "mark_to_row_count": res.get("mark_to_row_count")},
            status="ok" if res.get("ok") else "failed",
        )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
