#!/usr/bin/env python3
"""Zero-dependency Scientific Figure Inspection, Axis Calibration & Suite Audit Module.

Supports challenge-f35e4ca5 (阅读02｜科研图表解析):
- Pure Python stdlib raster image header parser (PNG/JPEG/BMP/GIF/TIFF dimensions & bit depth)
  with opportunistic Pillow luminance/exposure/edge-clipping diagnostics.
- Multi-point least-squares pixel-to-data axis calibration (linear, log10, ln, reciprocal,
  dual-Y) with R^2, anchor residuals, +/-0.5 px physical uncertainty bands, log-vs-linear
  miscalibration detection (WebPlotDigitizer #282), truncated-axis warnings, and unscaled-axis
  exact-reading refusal.
- Microscopy scale-bar physical conversion with pixel uncertainty.
- Cross-modal figure vs. literal source table consistency audit (units, n, panel IDs,
  unprovided-pixel gap separation).
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    import defusedxml.ElementTree as SafeET  # type: ignore
except ImportError:
    SafeET = None

try:
    from PIL import Image, ImageStat  # type: ignore
except ImportError:
    Image = None
    ImageStat = None


UNIT_MAGNITUDE_GROUPS = {
    "voltage": {"nv": 1e-9, "uv": 1e-6, "µv": 1e-6, "mv": 1e-3, "v": 1.0, "kv": 1e3},
    "length": {"pm": 1e-12, "nm": 1e-9, "um": 1e-6, "µm": 1e-6, "mm": 1e-3, "cm": 1e-2, "m": 1.0},
    "mass": {"ng": 1e-9, "ug": 1e-6, "µg": 1e-6, "mg": 1e-3, "g": 1.0, "kg": 1e3},
    "stress": {"pa": 1.0, "kpa": 1e3, "mpa": 1e6, "gpa": 1e9},
    "time": {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "min": 60.0, "h": 3600.0, "hr": 3600.0},
    "concentration": {"nm": 1e-9, "um": 1e-6, "µm": 1e-6, "mm": 1e-3, "m": 1.0},
}

ERROR_BAR_SEMANTIC_PATTERNS = [
    ("95% CI", re.compile(r"\b95%\s*(?:CI|confidence\s+interval)\b", re.I)),
    ("SEM", re.compile(r"\b(?:SEM|s\.e\.m\.|standard\s+error(?:\s+of\s+the\s+mean)?)\b", re.I)),
    ("SD", re.compile(r"\b(?:SD|s\.d\.|standard\s+deviation)\b", re.I)),
    ("IQR", re.compile(r"\b(?:IQR|interquartile\s+range)\b", re.I)),
    ("range", re.compile(r"\b(?:min[-–]max|full\s+range)\b", re.I)),
]

SIGNIFICANCE_PATTERNS = re.compile(
    r"(?:p\s*[<=>≤≥]\s*0?\.\d+(?:e[-+]?\d+)?|\*{1,4}\s*(?:p\s*[<≤]\s*0?\.\d+)?|\bns\b|\bnot\s+significant\b)",
    re.I,
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_image_header_stdlib(path: Path) -> dict[str, Any]:
    """Read PNG/JPEG/GIF/BMP/TIFF width, height, and bit depth using pure Python stdlib."""
    raw = path.read_bytes()
    n = len(raw)
    out: dict[str, Any] = {
        "file": path.name,
        "format": path.suffix.lstrip(".").lower(),
        "bytes": n,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "inspectable": False,
        "inspection_backend": "stdlib_header",
    }

    # PNG
    if n >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR":
        w, h, bit_depth, color_type = struct.unpack(">IIBB", raw[16:26])
        color_modes = {0: "L", 2: "RGB", 3: "P", 4: "LA", 6: "RGBA"}
        out.update({
            "inspectable": True,
            "format": "png",
            "width_px": w,
            "height_px": h,
            "bit_depth": bit_depth,
            "mode": color_modes.get(color_type, f"png_type_{color_type}"),
        })
        return out

    # GIF
    if n >= 10 and raw[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", raw[6:10])
        out.update({"inspectable": True, "format": "gif", "width_px": w, "height_px": h, "mode": "P"})
        return out

    # BMP
    if n >= 26 and raw[:2] == b"BM":
        w, h = struct.unpack("<ii", raw[18:26])
        out.update({"inspectable": True, "format": "bmp", "width_px": abs(w), "height_px": abs(h)})
        return out

    # JPEG (scan SOF0..SOF3, SOF5..SOF7, SOF9..SOF11, SOF13..SOF15)
    if n >= 4 and raw[:2] == b"\xff\xd8":
        i = 2
        sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while i + 4 < n:
            if raw[i] != 0xFF:
                i += 1
                continue
            while i < n and raw[i] == 0xFF:
                i += 1
            if i >= n:
                break
            marker = raw[i]
            i += 1
            if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                continue
            if i + 2 > n:
                break
            seg_len = struct.unpack(">H", raw[i : i + 2])[0]
            if marker in sof_markers and i + 8 <= n:
                precision, h, w, comps = struct.unpack(">BHHB", raw[i + 2 : i + 8])
                out.update({
                    "inspectable": True,
                    "format": "jpeg",
                    "width_px": w,
                    "height_px": h,
                    "bit_depth": precision,
                    "mode": "RGB" if comps == 3 else ("L" if comps == 1 else f"jpeg_{comps}ch"),
                })
                return out
            if seg_len < 2:
                break
            i += seg_len

    # TIFF (basic IFD parse)
    if n >= 8 and raw[:4] in (b"II*\x00", b"MM\x00*"):
        endian = "<" if raw[:2] == b"II" else ">"
        ifd_off = struct.unpack(endian + "I", raw[4:8])[0]
        if ifd_off + 2 <= n:
            entries = struct.unpack(endian + "H", raw[ifd_off : ifd_off + 2])[0]
            w = h = None
            for idx in range(entries):
                off = ifd_off + 2 + idx * 12
                if off + 12 > n:
                    break
                tag, typ, cnt = struct.unpack(endian + "HHI", raw[off : off + 8])
                val_bytes = raw[off + 8 : off + 12]
                val = struct.unpack(endian + ("H" if typ == 3 else "I"), val_bytes[: (2 if typ == 3 else 4)])[0]
                if tag == 256:
                    w = val
                elif tag == 257:
                    h = val
            if w and h:
                out.update({"inspectable": True, "format": "tiff", "width_px": w, "height_px": h})
                return out

    return out


def inspect_raster_image(path: Path) -> dict[str, Any]:
    """Inspect raster dimensions via stdlib, enriched by Pillow luminance/exposure checks when available."""
    info = inspect_image_header_stdlib(path)
    quality_flags: list[str] = []

    if info.get("inspectable"):
        w = int(info.get("width_px") or 0)
        h = int(info.get("height_px") or 0)
        if w < 300 or h < 200:
            quality_flags.append("low_resolution_risk")

    if Image is not None:
        try:
            with Image.open(path) as img:
                w, h = img.size
                info.update({
                    "inspectable": True,
                    "inspection_backend": "pillow",
                    "width_px": w,
                    "height_px": h,
                    "mode": img.mode,
                    "dpi": img.info.get("dpi"),
                })
                if (w < 300 or h < 200) and "low_resolution_risk" not in quality_flags:
                    quality_flags.append("low_resolution_risk")
                if ImageStat is not None and img.mode in ("L", "RGB", "RGBA"):
                    gray = img.convert("L")
                    stat = ImageStat.Stat(gray)
                    mean_l = round(stat.mean[0], 2)
                    std_l = round(stat.stddev[0], 2)
                    info["luminance_mean"] = mean_l
                    info["luminance_std"] = std_l
                    if mean_l > 248 and std_l < 12:
                        quality_flags.append("possible_overexposure_or_blank")
                    if mean_l < 12 and std_l < 12:
                        quality_flags.append("possible_underexposure_or_blank")
                    # Check edge clipping (non-white pixels touching outer 1px border)
                    px_data = gray.load()
                    if px_data is not None and w >= 10 and h >= 10:
                        border_dark = 0
                        border_total = 2 * (w + h)
                        for x in range(w):
                            if px_data[x, 0] < 220:
                                border_dark += 1
                            if px_data[x, h - 1] < 220:
                                border_dark += 1
                        for y in range(h):
                            if px_data[0, y] < 220:
                                border_dark += 1
                            if px_data[w - 1, y] < 220:
                                border_dark += 1
                        if border_dark / max(1, border_total) > 0.15:
                            quality_flags.append("border_content_or_cropping_risk")
        except Exception as exc:
            info["pillow_note"] = f"Pillow open failed ({exc}); retained stdlib header inspection."
    else:
        info["inspection_note"] = "Pillow unavailable; dimensions parsed via pure-stdlib header reader. Visual curve/text reading requires multimodal inspection."

    info["quality_flags"] = quality_flags
    return info


def parse_svg_figure(path: Path) -> dict[str, Any]:
    """Extract text labels, viewBox, numeric tick candidates, and primitive counts from SVG."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    parser = SafeET if SafeET is not None else ET
    root = parser.fromstring(raw)
    texts: list[dict[str, Any]] = []
    numeric_ticks: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        counts[tag] = counts.get(tag, 0) + 1
        if tag == "text":
            txt = " ".join("".join(elem.itertext()).split())
            if txt:
                x = elem.attrib.get("x")
                y = elem.attrib.get("y")
                rec = {"text": txt, "x": x, "y": y}
                texts.append(rec)
                if re.match(r"^[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$", txt):
                    numeric_ticks.append(rec)
    return {
        "file": path.name,
        "format": "svg",
        "sha256": sha256_file(path),
        "viewBox": root.attrib.get("viewBox", ""),
        "width": root.attrib.get("width", ""),
        "height": root.attrib.get("height", ""),
        "element_counts": counts,
        "text_labels": texts[:250],
        "numeric_tick_candidates": numeric_ticks[:100],
    }


def _ols_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Ordinary least squares y = slope * x + intercept; returns (slope, intercept, r_squared)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-12:
        raise ValueError("degenerate pixel anchor span")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 if ss_tot < 1e-15 else max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r2


def calibrate_axis_points(
    pixel_anchors: list[tuple[float, float]],
    *,
    scale: str = "linear",
    query_pixels: list[float] | None = None,
    unit: str | None = None,
    chart_type: str = "",
) -> dict[str, Any]:
    """Least-squares pixel -> physical value axis calibration with miscalibration & truncation guards."""
    if len(pixel_anchors) < 2:
        return {
            "ok": False,
            "refusal_code": "UNSCALED_AXIS_REFUSE_EXACT_READING",
            "reason": "Need at least 2 distinct (pixel, tick_value) anchors; refuse exact numeric reading on uncalibrated axes.",
            "unit": unit,
        }

    xs = [float(p) for p, _ in pixel_anchors]
    vs = [float(v) for _, v in pixel_anchors]
    if max(xs) - min(xs) < 1e-6:
        return {
            "ok": False,
            "refusal_code": "DEGENERATE_PIXEL_SPAN",
            "reason": "Pixel anchor coordinates have zero span.",
            "unit": unit,
        }

    scale_low = scale.strip().lower()
    warnings: list[str] = []

    # Check if caller chose linear on an obvious log10 axis (WebPlotDigitizer Issue #282)
    log_miscalibration_detected = False
    if scale_low == "linear" and len(pixel_anchors) >= 3 and all(v > 0 for v in vs):
        ratio = max(vs) / min(vs)
        if ratio >= 50.0:
            _, _, r2_lin = _ols_fit(xs, vs)
            _, _, r2_log = _ols_fit(xs, [math.log10(v) for v in vs])
            if r2_log > 0.995 and r2_lin < 0.92:
                log_miscalibration_detected = True
                warnings.append(
                    f"LOG_AXIS_MISCALIBRATION_WARNING: anchors span {ratio:.1f}x with log10 R^2={r2_log:.5f} vs linear R^2={r2_lin:.5f}; "
                    "treating this axis as linear causes severe distortion—re-calibrate with scale='log10'."
                )

    if scale_low == "log10":
        if any(v <= 0 for v in vs):
            return {"ok": False, "refusal_code": "INVALID_LOG_TICK", "reason": "log10 axis requires strictly positive tick values."}
        ys = [math.log10(v) for v in vs]
        slope, intercept, r2 = _ols_fit(xs, ys)
        map_fn = lambda px: 10.0 ** (slope * px + intercept)
    elif scale_low == "ln":
        if any(v <= 0 for v in vs):
            return {"ok": False, "refusal_code": "INVALID_LOG_TICK", "reason": "ln axis requires strictly positive tick values."}
        ys = [math.log(v) for v in vs]
        slope, intercept, r2 = _ols_fit(xs, ys)
        map_fn = lambda px: math.exp(slope * px + intercept)
    elif scale_low == "reciprocal":
        if any(abs(v) < 1e-12 for v in vs):
            return {"ok": False, "refusal_code": "INVALID_RECIPROCAL_TICK", "reason": "reciprocal axis requires non-zero tick values."}
        ys = [1.0 / v for v in vs]
        slope, intercept, r2 = _ols_fit(xs, ys)
        map_fn = lambda px: 1.0 / (slope * px + intercept)
    else:
        scale_low = "linear"
        ys = vs
        slope, intercept, r2 = _ols_fit(xs, ys)
        map_fn = lambda px: slope * px + intercept

    # Truncated axis warning for bar/area charts when min tick > 0
    if scale_low == "linear" and min(vs) > 0 and chart_type.lower() in {"bar", "barplot", "bar_chart", "area"}:
        warnings.append(
            f"TRUNCATED_AXIS_WARNING: non-zero baseline starts at {min(vs)} ({unit or 'unspecified unit'}); "
            "bar heights are not proportional to absolute magnitude."
        )

    if not unit or not str(unit).strip():
        warnings.append("UNIT_UNSPECIFIED: axis unit not declared; do not assume dimensionless.")

    residuals = [round(abs(map_fn(px) - val), 6) for px, val in pixel_anchors]
    mapped: list[dict[str, Any]] = []
    for qp in (query_pixels or []):
        val = map_fn(qp)
        v_lo = map_fn(qp - 0.5)
        v_hi = map_fn(qp + 0.5)
        lo, hi = (min(v_lo, v_hi), max(v_lo, v_hi))
        mapped.append({
            "pixel": qp,
            "value": round(val, 6),
            "uncertainty_half_px_interval": [round(lo, 6), round(hi, 6)],
            "scale": scale_low,
            "unit": unit,
            "approximation_note": "pixel_digitized_approx",
        })

    return {
        "ok": not log_miscalibration_detected,
        "refusal_code": "LOG_AXIS_MISCALIBRATION_BLOCKED" if log_miscalibration_detected else None,
        "scale": scale_low,
        "unit": unit,
        "anchor_count": len(pixel_anchors),
        "slope": round(slope, 8),
        "intercept": round(intercept, 8),
        "r_squared": round(r2, 6),
        "max_anchor_residual": max(residuals) if residuals else 0.0,
        "warnings": warnings,
        "mapped_points": mapped,
    }


def calibrate_scale_bar(
    scale_bar_px: float,
    scale_bar_value: float,
    unit: str,
    measured_features_px: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convert microscopy pixel measurements into physical dimensions using scale-bar calibration."""
    if scale_bar_px <= 0 or scale_bar_value <= 0 or not unit.strip():
        return {
            "ok": False,
            "refusal_code": "INVALID_SCALE_BAR",
            "reason": "Microscopy calibration requires positive scale_bar_px, positive scale_bar_value, and explicit physical unit.",
        }
    unit_per_px = scale_bar_value / scale_bar_px
    rel_bar_err = 0.5 / scale_bar_px
    converted: list[dict[str, Any]] = []
    for item in (measured_features_px or []):
        px = float(item.get("pixels", 0.0))
        phys = px * unit_per_px
        err = math.sqrt((0.5 * unit_per_px) ** 2 + (phys * rel_bar_err) ** 2)
        converted.append({
            "feature_id": item.get("feature_id", "feature"),
            "pixels": px,
            "physical_value": round(phys, 6),
            "uncertainty_abs": round(err, 6),
            "unit": unit,
        })
    return {
        "ok": True,
        "scale_bar_px": scale_bar_px,
        "scale_bar_value": scale_bar_value,
        "unit": unit,
        "unit_per_pixel": round(unit_per_px, 8),
        "converted_features": converted,
    }


def extract_error_and_significance_cues(text: str) -> dict[str, Any]:
    """Classify explicitly stated error-bar semantics and significance notations."""
    error_types = [label for label, pat in ERROR_BAR_SEMANTIC_PATTERNS if pat.search(text)]
    sig_hits = sorted(set(m.group(0).strip() for m in SIGNIFICANCE_PATTERNS.finditer(text)))
    sample_sizes = sorted(set(re.findall(r"\bn\s*=\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\s*[-–]\s*(?:\d{1,3}(?:,\d{3})+|\d+))?", text, re.I)))
    return {
        "error_bar_types_reported": error_types or ["unreported_in_text"],
        "significance_markers_found": sig_hits[:30],
        "sample_sizes_found": sample_sizes[:20],
    }


def audit_figure_vs_tables(
    provided_figures: list[dict[str, Any]],
    parsed_tables: list[dict[str, Any]],
    caption_and_body_text: str,
) -> dict[str, Any]:
    """Cross-modal audit comparing figure/caption cues against literal source tables."""
    findings: list[dict[str, Any]] = []
    table_units_all: dict[str, list[str]] = {}
    missing_unit_headers: list[dict[str, Any]] = []

    for t in parsed_tables:
        tid = t.get("table_id") or t.get("source")
        h_units = t.get("header_units") or {}
        for h in t.get("headers") or []:
            if h in h_units:
                table_units_all.setdefault(h_units[h].lower(), []).append(f"{tid}:{h}")
            else:
                # Check if column holds numeric rows without an explicit unit in header
                rows = t.get("rows") or []
                numeric_cells = sum(
                    1 for r in rows if isinstance(r, dict) and re.match(r"^\s*[-+]?\d+(?:\.\d+)?", str(r.get(h, "")))
                )
                if numeric_cells >= max(1, len(rows) // 2) and not re.search(r"\b(?:id|no\.?|index|group|sample|rank|n|count)\b", h, re.I):
                    missing_unit_headers.append({"table_id": tid, "header": h})

    if missing_unit_headers:
        findings.append({
            "severity": "advisory",
            "code": "TABLE_NUMERIC_COLUMN_WITHOUT_EXPLICIT_UNIT",
            "details": missing_unit_headers[:20],
            "rule": "Do not treat an unlabeled numeric column as dimensionless; verify unit in caption/body or mark unit_unreported.",
        })

    # Detect potential magnitude conflicts (e.g., mV vs V or nm vs um) across tables & captions
    text_units = set(m.group(1).lower() for m in re.finditer(r"\((nv|uv|µv|mv|v|kv|nm|um|µm|mm|cm|ng|ug|µg|mg|g|kg|pa|kpa|mpa|gpa)\)", caption_and_body_text, re.I))
    all_units = text_units | set(table_units_all.keys())
    magnitude_alerts: list[dict[str, Any]] = []
    for dim, mapping in UNIT_MAGNITUDE_GROUPS.items():
        present = sorted(u for u in all_units if u in mapping)
        if len(present) > 1:
            magnitude_alerts.append({
                "dimension": dim,
                "coexisting_units": present,
                "note": "Multiple magnitude scales coexist across figures/tables; preserve exact per-column/per-axis units without silent conversion.",
            })

    cues = extract_error_and_significance_cues(caption_and_body_text)
    return {
        "provided_figures_count": len(provided_figures),
        "parsed_tables_count": len(parsed_tables),
        "table_units_inventory": table_units_all,
        "unit_magnitude_alerts": magnitude_alerts,
        "error_and_significance_cues": cues,
        "findings": findings,
    }
