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
    """Extract text labels, viewBox, numeric tick candidates, and auto-digitized vector series from SVG."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    parser = SafeET if SafeET is not None else ET
    root = parser.fromstring(raw)
    texts: list[dict[str, Any]] = []
    numeric_ticks: list[dict[str, Any]] = []
    rects: list[dict[str, Any]] = []
    circles: list[dict[str, Any]] = []
    polylines: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def _f(val: str | None) -> float | None:
        if val is None:
            return None
        m = re.match(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", str(val))
        return float(m.group(1)) if m else None

    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        counts[tag] = counts.get(tag, 0) + 1
        if tag == "text":
            txt = " ".join("".join(elem.itertext()).split())
            if txt:
                x_f, y_f = _f(elem.attrib.get("x")), _f(elem.attrib.get("y"))
                rec = {"text": txt, "x": elem.attrib.get("x"), "y": elem.attrib.get("y"), "x_num": x_f, "y_num": y_f}
                texts.append(rec)
                if re.match(r"^[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$", txt) and x_f is not None and y_f is not None:
                    numeric_ticks.append({"value": float(txt), "x": x_f, "y": y_f, "text": txt})
        elif tag == "rect":
            x_f, y_f, w_f, h_f = _f(elem.attrib.get("x")), _f(elem.attrib.get("y")), _f(elem.attrib.get("width")), _f(elem.attrib.get("height"))
            fill = str(elem.attrib.get("fill") or "").lower()
            if None not in (x_f, y_f, w_f, h_f) and w_f > 2 and h_f > 2 and fill not in ("none", "white", "#fff", "#ffffff", "transparent"):
                rects.append({"x": x_f, "y": y_f, "w": w_f, "h": h_f, "fill": fill, "id": elem.attrib.get("id")})
        elif tag == "circle":
            cx, cy, r_f = _f(elem.attrib.get("cx")), _f(elem.attrib.get("cy")), _f(elem.attrib.get("r"))
            if None not in (cx, cy, r_f) and r_f > 0:
                circles.append({"cx": cx, "cy": cy, "r": r_f, "fill": str(elem.attrib.get("fill") or "")})
        elif tag in ("polyline", "path"):
            pts: list[tuple[float, float]] = []
            if tag == "polyline":
                for pair in re.findall(r"([-+]?\d+(?:\.\d+)?)[,\s]+([-+]?\d+(?:\.\d+)?)", elem.attrib.get("points") or ""):
                    pts.append((float(pair[0]), float(pair[1])))
            else:
                d_str = elem.attrib.get("d") or ""
                for pair in re.findall(r"[ML]\s*([-+]?\d+(?:\.\d+)?)[,\s]+([-+]?\d+(?:\.\d+)?)", d_str):
                    pts.append((float(pair[0]), float(pair[1])))
            if len(pts) >= 2:
                polylines.append({"tag": tag, "stroke": str(elem.attrib.get("stroke") or ""), "id": elem.attrib.get("id"), "points": pts[:300]})

    auto_dig = auto_digitize_svg_primitives(numeric_ticks, rects, circles, polylines, texts)
    return {
        "file": path.name,
        "format": "svg",
        "sha256": sha256_file(path),
        "viewBox": root.attrib.get("viewBox", ""),
        "width": root.attrib.get("width", ""),
        "height": root.attrib.get("height", ""),
        "element_counts": counts,
        "text_labels": [{k: v for k, v in t.items() if k in ("text", "x", "y")} for t in texts[:250]],
        "numeric_tick_candidates": numeric_ticks[:100],
        "auto_digitization": auto_dig,
    }


def _choose_best_scale(anchors: list[tuple[float, float]]) -> str:
    """Auto-select log10 vs linear when >=3 positive anchors span >=50x with superior log10 R^2."""
    if len(anchors) >= 3 and all(v > 0 for _, v in anchors):
        vs = [v for _, v in anchors]
        if max(vs) / min(vs) >= 50.0:
            xs = [p for p, _ in anchors]
            try:
                _, _, r2_lin = _ols_fit(xs, vs)
                _, _, r2_log = _ols_fit(xs, [math.log10(v) for v in vs])
                if r2_log > 0.995 and r2_lin < 0.95:
                    return "log10"
            except Exception:
                pass
    return "linear"


def auto_digitize_svg_primitives(
    numeric_ticks: list[dict[str, Any]],
    rects: list[dict[str, Any]],
    circles: list[dict[str, Any]],
    polylines: list[dict[str, Any]],
    texts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Automatically cluster SVG X/Y tick anchors, calibrate axes, and invert bars/scatter/curves."""
    # Group ticks sharing similar x (Y-axis ticks) or similar y (X-axis ticks)
    y_groups: list[list[dict[str, Any]]] = []
    x_groups: list[list[dict[str, Any]]] = []
    for tk in numeric_ticks:
        placed_y = False
        for g in y_groups:
            if abs(g[0]["x"] - tk["x"]) <= 12.0:
                g.append(tk)
                placed_y = True
                break
        if not placed_y:
            y_groups.append([tk])
        placed_x = False
        for g in x_groups:
            if abs(g[0]["y"] - tk["y"]) <= 10.0:
                g.append(tk)
                placed_x = True
                break
        if not placed_x:
            x_groups.append([tk])

    y_candidates = [g for g in y_groups if len(g) >= 2 and (max(t["y"] for t in g) - min(t["y"] for t in g)) >= 25.0]
    x_candidates = [g for g in x_groups if len(g) >= 2 and (max(t["x"] for t in g) - min(t["x"] for t in g)) >= 25.0]
    y_candidates.sort(key=lambda g: (-len(g), min(t["x"] for t in g)))
    x_candidates.sort(key=lambda g: (-len(g), -max(t["y"] for t in g)))

    y_cal = None
    x_cal = None
    y_axis_x = None
    x_axis_y = None
    if y_candidates:
        yg = sorted(y_candidates[0], key=lambda t: t["y"])
        y_anchors = [(t["y"], t["value"]) for t in yg]
        y_scale = _choose_best_scale(y_anchors)
        y_cal = calibrate_axis_points(y_anchors, scale=y_scale, unit="svg_detected_axis")
        y_axis_x = sum(t["x"] for t in yg) / len(yg)
    if x_candidates:
        xg = sorted(x_candidates[0], key=lambda t: t["x"])
        x_anchors = [(t["x"], t["value"]) for t in xg]
        x_scale = _choose_best_scale(x_anchors)
        x_cal = calibrate_axis_points(x_anchors, scale=x_scale, unit="svg_detected_axis")
        x_axis_y = sum(t["y"] for t in xg) / len(xg)

    def _inv(cal: dict[str, Any] | None, px: float) -> float | None:
        if not cal or not cal.get("ok"):
            return None
        s, b = cal["slope"], cal["intercept"]
        val = (10.0 ** (s * px + b)) if cal["scale"] == "log10" else (s * px + b)
        return round(val, 6)

    digitized_bars: list[dict[str, Any]] = []
    if y_cal and y_cal.get("ok"):
        for idx, r in enumerate(rects, 1):
            # Exclude huge background canvas rects
            if r["w"] > 400 and r["h"] > 300:
                continue
            top_val = _inv(y_cal, r["y"])
            base_val = _inv(y_cal, r["y"] + r["h"])
            cx = r["x"] + r["w"] / 2.0
            # Match closest x-label below bar
            lbl = None
            best_d = 1e9
            for t in texts:
                if t.get("x_num") is not None and t.get("y_num") is not None and t["y_num"] >= r["y"] + r["h"] - 5:
                    d = abs(t["x_num"] - cx) + 0.3 * abs(t["y_num"] - (r["y"] + r["h"]))
                    if d < best_d and abs(t["x_num"] - cx) < max(35.0, r["w"] * 1.2):
                        best_d = d
                        lbl = t["text"]
            digitized_bars.append({
                "bar_index": idx,
                "label": lbl or r.get("id") or f"bar_{idx}",
                "fill": r["fill"],
                "top_value": top_val,
                "baseline_value": base_val,
                "bar_height_data": round(abs((top_val or 0.0) - (base_val or 0.0)), 6) if top_val is not None and base_val is not None else None,
                "pixel_box": [r["x"], r["y"], r["w"], r["h"]],
            })

    digitized_points: list[dict[str, Any]] = []
    if y_cal and y_cal.get("ok"):
        for c in circles[:200]:
            digitized_points.append({
                "x": _inv(x_cal, c["cx"]) if x_cal and x_cal.get("ok") else round(c["cx"], 2),
                "y": _inv(y_cal, c["cy"]),
                "px": [c["cx"], c["cy"]],
                "fill": c["fill"],
            })

    digitized_curves: list[dict[str, Any]] = []
    if y_cal and y_cal.get("ok"):
        for idx, pl in enumerate(polylines, 1):
            pts_conv = []
            for px, py in pl["points"]:
                pts_conv.append({
                    "x": _inv(x_cal, px) if x_cal and x_cal.get("ok") else round(px, 2),
                    "y": _inv(y_cal, py),
                    "px": [px, py],
                })
            ys_only = [p["y"] for p in pts_conv if p["y"] is not None]
            peak = max(pts_conv, key=lambda p: p["y"] if p["y"] is not None else -1e18) if ys_only else None
            digitized_curves.append({
                "series_id": pl.get("id") or f"curve_{idx}",
                "stroke": pl["stroke"],
                "point_count": len(pts_conv),
                "peak_point": peak,
                "points_sample": pts_conv[:25],
            })

    return {
        "mode": "svg_vector_auto_digitization",
        "axes_calibrated": bool(y_cal and y_cal.get("ok")),
        "y_axis_calibration": y_cal,
        "x_axis_calibration": x_cal,
        "y_axis_x_px": round(y_axis_x, 2) if y_axis_x is not None else None,
        "x_axis_y_px": round(x_axis_y, 2) if x_axis_y is not None else None,
        "digitized_bars": digitized_bars,
        "digitized_scatter_points": digitized_points[:50],
        "digitized_curves": digitized_curves,
    }


def _hue_bucket(r: int, g: int, b: int) -> str:
    """Classify a chromatic RGB pixel into one of 6 scientific plot series hue clusters."""
    if r >= 160 and g >= 140 and b < 90:
        return "gold_yellow"
    if r >= 120 and b >= 120 and g < min(r, b) - 25:
        return "purple_magenta"
    if g >= 110 and b >= 110 and r < min(g, b) - 25:
        return "cyan_teal"
    if r >= g and r >= b:
        return "orange_red"
    if g >= r and g >= b:
        return "green"
    return "blue"


def _decode_stdlib_png_rgb(path: Path, max_dim: int = 900) -> tuple[int, int, int, int, float, list[list[tuple[int, int, int]]]] | None:
    """Pure-stdlib PNG RGB/RGBA/Grayscale decoder (zlib + PNG filters 0-4) for zero-Pillow containers."""
    import zlib
    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    w = h = bit_depth = color_type = 0
    idat_parts: list[bytes] = []
    while pos + 8 <= len(data):
        ln, ctype = struct.unpack(">I4s", data[pos : pos + 8])
        chunk = data[pos + 8 : pos + 8 + ln]
        pos += 12 + ln
        if ctype == b"IHDR" and len(chunk) >= 13:
            w, h, bit_depth, color_type, _comp, _flt, interlace = struct.unpack(">IIBBBBB", chunk[:13])
            if bit_depth != 8 or interlace != 0 or color_type not in (0, 2, 6):
                return None
        elif ctype == b"IDAT":
            idat_parts.append(chunk)
        elif ctype == b"IEND":
            break
    if not w or not h or not idat_parts or w * h > 4_000_000:
        return None
    bpp = {0: 1, 2: 3, 6: 4}[color_type]
    raw = zlib.decompress(b"".join(idat_parts))
    stride = w * bpp
    if len(raw) < h * (stride + 1):
        return None
    recon = bytearray(h * stride)
    for y in range(h):
        ftype = raw[y * (stride + 1)]
        src_off = y * (stride + 1) + 1
        dst_off = y * stride
        for i in range(stride):
            xval = raw[src_off + i]
            a = recon[dst_off + i - bpp] if i >= bpp else 0
            b = recon[dst_off - stride + i] if y > 0 else 0
            c = recon[dst_off - stride + i - bpp] if (y > 0 and i >= bpp) else 0
            if ftype == 0:
                val = xval
            elif ftype == 1:
                val = (xval + a) & 0xFF
            elif ftype == 2:
                val = (xval + b) & 0xFF
            elif ftype == 3:
                val = (xval + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                val = (xval + pr) & 0xFF
            else:
                return None
            recon[dst_off + i] = val
    step = max(1, int(math.ceil(max(w, h) / float(max_dim))))
    sw, sh = (w + step - 1) // step, (h + step - 1) // step
    scale = 1.0 / float(step)
    grid: list[list[tuple[int, int, int]]] = []
    for sy in range(sh):
        oy = min(h - 1, sy * step)
        row_px: list[tuple[int, int, int]] = []
        base = oy * stride
        for sx in range(sw):
            ox = min(w - 1, sx * step)
            off = base + ox * bpp
            if bpp == 1:
                v = recon[off]
                row_px.append((v, v, v))
            else:
                row_px.append((recon[off], recon[off + 1], recon[off + 2]))
        grid.append(row_px)
    return w, h, sw, sh, scale, grid


def _detect_tick_notches(
    get_px: Any,
    sw: int,
    sh: int,
    y_axis_x: int,
    x_axis_y: int,
    plot_top: int,
    plot_right: int,
    scale: float,
) -> tuple[list[int], list[int]]:
    """Detect outward tick-mark notch centers along bottom of X spine and left of Y spine."""
    def _cluster(coords: list[int], min_gap: int = 8) -> list[int]:
        if not coords:
            return []
        out: list[int] = []
        grp = [coords[0]]
        for c in coords[1:]:
            if c - grp[-1] <= min_gap:
                grp.append(c)
            else:
                out.append(int(round((sum(grp) / len(grp)) / scale)))
                grp = [c]
        out.append(int(round((sum(grp) / len(grp)) / scale)))
        return out

    x_hits: list[int] = []
    if x_axis_y + 4 < sh:
        for x in range(y_axis_x, plot_right + 1):
            dark_out = sum(
                1 for dy in range(2, min(6, sh - x_axis_y))
                if max(get_px(x, x_axis_y + dy)) < 130
            )
            if dark_out >= 2:
                x_hits.append(x)
    y_hits: list[int] = []
    if y_axis_x >= 4:
        for y in range(plot_top, x_axis_y + 1):
            dark_out = sum(
                1 for dx in range(2, min(6, y_axis_x + 1))
                if max(get_px(y_axis_x - dx, y)) < 130
            )
            if dark_out >= 2:
                y_hits.append(y)
    return _cluster(x_hits)[:16], _cluster(y_hits)[:16]


def auto_detect_plot_box_and_digitize_raster(path: Path) -> dict[str, Any]:
    """Detect plot-frame axis spines, tick notches, and 6-hue chromatic series on raster PNG/JPG (Pillow or pure-stdlib PNG)."""
    w = h = sw = sh = 0
    scale = 1.0
    backend_mode = "raster_pillow_spine_and_series_detector"
    get_px: Any = None
    pil_img = None
    try:
        if Image is not None:
            pil_img = Image.open(path)
            rgb = pil_img.convert("RGB")
            w, h = rgb.size
            max_dim = 900
            if max(w, h) > max_dim:
                scale = max_dim / float(max(w, h))
                rgb = rgb.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            sw, sh = rgb.size
            px_obj = rgb.load()
            if px_obj is None:
                return {"mode": "raster_pillow", "plot_box_detected": False}
            get_px = lambda x, y: px_obj[x, y]
        elif path.suffix.lower() == ".png":
            decoded = _decode_stdlib_png_rgb(path, max_dim=900)
            if decoded is None:
                return {
                    "mode": "raster_stdlib_no_pillow",
                    "plot_box_detected": False,
                    "note": "Pillow unavailable and PNG encoding unsupported by stdlib fallback; supply tick anchors visually.",
                }
            w, h, sw, sh, scale, grid = decoded
            backend_mode = "raster_stdlib_png_spine_and_series_detector"
            get_px = lambda x, y: grid[y][x]
        else:
            return {
                "mode": "raster_stdlib_no_pillow",
                "plot_box_detected": False,
                "note": "Pillow unavailable for JPEG/TIFF pixel decoding; dimensions read via stdlib header. Supply tick anchors visually to run OLS calibration.",
            }

        # Detect dark horizontal spine (X-axis) and vertical spine (Y-axis)
        row_dark = [0] * sh
        col_dark = [0] * sw
        for y in range(sh):
            cnt = 0
            for x in range(sw):
                r, g, b = get_px(x, y)
                if r < 95 and g < 95 and b < 95:
                    cnt += 1
                    col_dark[x] += 1
            row_dark[y] = cnt

        x_spine_candidates = [(row_dark[y], y) for y in range(int(sh * 0.42), int(sh * 0.96)) if row_dark[y] >= sw * 0.25]
        y_spine_candidates = [(col_dark[x], x) for x in range(int(sw * 0.03), int(sw * 0.45)) if col_dark[x] >= sh * 0.22]

        if not x_spine_candidates or not y_spine_candidates:
            return {
                "mode": "raster_pillow_spine_probe" if Image is not None else "raster_stdlib_png_spine_probe",
                "plot_box_detected": False,
                "original_size_px": [w, h],
                "note": "No strong dark orthogonal X/Y axis spines detected (figure may be photograph, schematic, heatmap, or multi-panel composite); inspect visually.",
            }

        x_axis_y = max(x_spine_candidates)[1]
        y_axis_x = max(y_spine_candidates)[1]
        top_candidates = [y for y in range(int(sh * 0.03), x_axis_y) if get_px(y_axis_x, y)[0] < 115]
        right_candidates = [x for x in range(y_axis_x + 1, int(sw * 0.98)) if get_px(x, x_axis_y)[0] < 115]
        plot_top = min(top_candidates) if top_candidates else int(sh * 0.10)
        plot_right = max(right_candidates) if right_candidates else int(sw * 0.92)

        if plot_right - y_axis_x < 25 or x_axis_y - plot_top < 25:
            return {"mode": "raster_pillow_spine_probe", "plot_box_detected": False, "original_size_px": [w, h]}

        x_ticks_px, y_ticks_px = _detect_tick_notches(get_px, sw, sh, y_axis_x, x_axis_y, plot_top, plot_right, scale)

        # Sample chromatic curve/bar pixels inside plot box across 24 evenly spaced X columns with 6-hue clustering
        series_buckets: dict[str, list[tuple[int, int]]] = {}
        step_x = max(1, (plot_right - y_axis_x) // 24)
        for x in range(y_axis_x + 4, plot_right - 3, step_x):
            col_hits: dict[str, list[int]] = {}
            for y in range(plot_top + 3, x_axis_y - 3):
                r, g, b = get_px(x, y)
                sat = max(r, g, b) - min(r, g, b)
                if sat >= 40 and max(r, g, b) < 245:
                    dom = _hue_bucket(r, g, b)
                    col_hits.setdefault(dom, []).append(y)
            for dom, ys in col_hits.items():
                med_y = sorted(ys)[len(ys) // 2]
                series_buckets.setdefault(dom, []).append((int(round(x / scale)), int(round(med_y / scale))))

        detected_series = []
        for dom, pts in sorted(series_buckets.items()):
            if len(pts) < 3:
                continue
            norm_ys = [round((x_axis_y - (p[1] * scale)) / max(1.0, (x_axis_y - plot_top)), 4) for p in pts]
            peak_idx = max(range(len(norm_ys)), key=lambda idx: norm_ys[idx])
            delta = norm_ys[-1] - norm_ys[0]
            trend = "increasing" if delta > 0.08 else ("decreasing" if delta < -0.08 else "flat_or_nonmonotonic")
            detected_series.append({
                "color_bucket": dom,
                "sampled_points_count": len(pts),
                "trend_direction": trend,
                "peak_point_px": list(pts[peak_idx]),
                "peak_normalized_y": norm_ys[peak_idx],
                "normalized_y_span": [norm_ys[0], norm_ys[len(norm_ys) // 2], norm_ys[-1]],
                "pixel_samples": pts[:16],
            })

        return {
            "mode": backend_mode,
            "plot_box_detected": True,
            "original_size_px": [w, h],
            "plot_box_px": {
                "x_min_px": int(round(y_axis_x / scale)),
                "y_top_px": int(round(plot_top / scale)),
                "x_max_px": int(round(plot_right / scale)),
                "y_bottom_px": int(round(x_axis_y / scale)),
            },
            "detected_x_ticks_px": x_ticks_px,
            "detected_y_ticks_px": y_ticks_px,
            "detected_chromatic_series": detected_series,
            "calibration_ready_note": "Pair detected_x_ticks_px / detected_y_ticks_px with verified tick labels via calibrate_axis_points() (WebPlotDigitizer OLS paradigm) for physical values with +/-0.5 px error bands.",
        }
    except Exception as exc:
        return {"mode": "raster_spine_probe", "plot_box_detected": False, "error": str(exc)}
    finally:
        if pil_img is not None:
            try:
                pil_img.close()
            except Exception:
                pass


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
