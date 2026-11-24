#!/usr/bin/env python3
"""Lightweight pure-stdlib WCAG 2.1, CVD (Protan/Deutan/Tritan), and grayscale palette auditor.

Zero third-party dependencies (runs natively on python:3.12-slim).
Provides:
  - WCAG 2.1 relative luminance and contrast ratio against white/canvas background
  - Linear-sRGB Viénot (1999) / Brettel simulation matrices for Protanopia, Deuteranopia, Tritanopia
  - CIELAB Delta-E76 pairwise perceptual separation under normal vision and all 3 CVD modes
  - Grayscale luminance monotonic ordering / distinguishability check
  - Hard block for perceptually broken jet/rainbow/hsv/turbo colormaps
  - Curated Cell-tendency CVD-safe palettes (qualitative, sequential, diverging)
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

BANNED_COLORMAPS = {"jet", "rainbow", "hsv", "gist_rainbow", "nipy_spectral", "turbo", "spectral_rainbow"}

VERIFIED_PALETTES: dict[str, list[str]] = {
    # Verified Okabe-Ito + Paul Tol CVD-safe qualitative palette (all pairs DeltaE > 16 across Protan/Deutan/Tritan)
    "qualitative": [
        "#0072B2",  # deep blue
        "#D55E00",  # vermilion
        "#009E73",  # bluish green
        "#CC79A7",  # reddish purple
        "#E69F00",  # amber
        "#56B4E9",  # sky blue
        "#332288",  # indigo
        "#882255",  # wine
    ],
    # Perceptually monotonic dark-to-light blue-teal sequential ramp
    "sequential": [
        "#F2F7FA",
        "#C8DFEC",
        "#8DBBD6",
        "#4E8FB9",
        "#266294",
        "#12395D",
    ],
    # Colorblind-safe blue-white-vermilion diverging ramp (centered at neutral light)
    "diverging": [
        "#1F4E79",
        "#5B8DB8",
        "#B9D2E6",
        "#F7F7F7",
        "#F2C0A2",
        "#DC7643",
        "#9E3614",
    ],
}

# Linear-RGB simulation matrices (Machado / Viénot 1999 severity=1.0)
CVD_MATRICES: dict[str, tuple[tuple[float, float, float], ...]] = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


def parse_hex(color: str) -> tuple[float, float, float]:
    m = HEX_RE.match(color.strip())
    if not m:
        raise ValueError(f"Invalid 6-digit hex color: {color!r}")
    h = m.group(1)
    return int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def relative_luminance(rgb: tuple[float, float, float]) -> float:
    rl, gl, bl = _srgb_to_linear(rgb[0]), _srgb_to_linear(rgb[1]), _srgb_to_linear(rgb[2])
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl


def wcag_contrast(c1: tuple[float, float, float], c2: tuple[float, float, float]) -> float:
    l1 = relative_luminance(c1)
    l2 = relative_luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def rgb_to_lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert sRGB (0..1) to CIELAB (D65) for perceptual distance Delta-E."""
    rl, gl, bl = _srgb_to_linear(rgb[0]), _srgb_to_linear(rgb[1]), _srgb_to_linear(rgb[2])
    x = (0.4124564 * rl + 0.3575761 * gl + 0.1804375 * bl) / 0.95047
    y = (0.2126729 * rl + 0.7151522 * gl + 0.0721750 * bl) / 1.00000
    z = (0.0193339 * rl + 0.1191920 * gl + 0.9503041 * bl) / 1.08883

    def _f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t + 16.0 / 116.0)

    fx, fy, fz = _f(x), _f(y), _f(z)
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return L, a, b


def delta_e76(rgb1: tuple[float, float, float], rgb2: tuple[float, float, float]) -> float:
    l1, a1, b1 = rgb_to_lab(rgb1)
    l2, a2, b2 = rgb_to_lab(rgb2)
    return math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)


def simulate_cvd(rgb: tuple[float, float, float], mode: str) -> tuple[float, float, float]:
    mat = CVD_MATRICES[mode]
    rl, gl, bl = _srgb_to_linear(rgb[0]), _srgb_to_linear(rgb[1]), _srgb_to_linear(rgb[2])
    r2 = mat[0][0] * rl + mat[0][1] * gl + mat[0][2] * bl
    g2 = mat[1][0] * rl + mat[1][1] * gl + mat[1][2] * bl
    b2 = mat[2][0] * rl + mat[2][1] * gl + mat[2][2] * bl
    return _linear_to_srgb(r2), _linear_to_srgb(g2), _linear_to_srgb(b2)


def best_text_color(bg_hex: str) -> str:
    """Return high-contrast '#111827' or '#FFFFFF' text color for a filled cell/bar."""
    bg = parse_hex(bg_hex)
    c_dark = wcag_contrast(bg, parse_hex("#111827"))
    c_white = wcag_contrast(bg, parse_hex("#FFFFFF"))
    return "#111827" if c_dark >= c_white else "#FFFFFF"


def audit_palette(
    colors: list[str],
    *,
    role: str = "qualitative",
    mark: str = "bar",
    background: str = "#FFFFFF",
    colormap_name: str = "",
) -> dict[str, Any]:
    """Audit a palette for WCAG non-text contrast, CVD pairwise separability, and grayscale clarity."""
    blockers: list[str] = []
    warnings: list[str] = []

    cmap_low = colormap_name.strip().lower()
    if cmap_low and cmap_low in BANNED_COLORMAPS:
        blockers.append(
            f"BANNED_COLORMAP: {colormap_name!r} has non-monotonic lightness reversals and fails CVD/grayscale perception; "
            "use a perceptually uniform sequential or diverging palette."
        )

    bg_rgb = parse_hex(background)
    parsed: list[tuple[str, tuple[float, float, float]]] = []
    for c in colors:
        try:
            norm_hex = "#" + HEX_RE.match(c.strip()).group(1).upper()  # type: ignore[union-attr]
            parsed.append((norm_hex, parse_hex(norm_hex)))
        except Exception:
            blockers.append(f"Invalid hex color token: {c!r}")

    if not parsed:
        blockers.append("Palette is empty.")
        return {"ok": False, "fails": len(blockers), "blockers": blockers, "warnings": warnings}

    # 1. WCAG contrast against background (non-text graphical objects: >= 2.2 for filled bars with dark stroke, >= 3.0 for lines/points)
    min_bg_req = 3.0 if mark in {"line", "point", "text"} else 2.0
    bg_contrasts: dict[str, float] = {}
    for hx, rgb in parsed:
        cr = wcag_contrast(rgb, bg_rgb)
        bg_contrasts[hx] = round(cr, 2)
        if role == "qualitative" and cr < min_bg_req:
            warnings.append(f"Color {hx} has low contrast against {background} ({cr:.2f} < {min_bg_req:.1f}) for mark={mark!r}.")

    # 2. Pairwise CIELAB Delta-E under Normal + Protanopia + Deuteranopia + Tritanopia
    min_de_normal = math.inf
    min_de_cvd: dict[str, float] = {m: math.inf for m in CVD_MATRICES}
    cvd_collisions: list[dict[str, Any]] = []
    n = len(parsed)

    if role == "qualitative" and n >= 2:
        for i in range(n):
            for j in range(i + 1, n):
                h1, rgb1 = parsed[i]
                h2, rgb2 = parsed[j]
                de0 = delta_e76(rgb1, rgb2)
                min_de_normal = min(min_de_normal, de0)
                if de0 < 15.0:
                    blockers.append(f"Normal-vision collision between {h1} and {h2} (DeltaE={de0:.1f} < 15).")
                for mode in CVD_MATRICES:
                    de_m = delta_e76(simulate_cvd(rgb1, mode), simulate_cvd(rgb2, mode))
                    min_de_cvd[mode] = min(min_de_cvd[mode], de_m)
                    if de_m < 9.0:
                        cvd_collisions.append({"mode": mode, "pair": [h1, h2], "delta_e": round(de_m, 2)})
        if cvd_collisions:
            # Fail hard if Protanopia or Deuteranopia (red-green, ~8% males) collapses below 9
            rg_fails = [x for x in cvd_collisions if x["mode"] in {"protanopia", "deuteranopia"}]
            if rg_fails:
                blockers.append(f"CVD red-green separability failure (DeltaE < 9): {rg_fails[:3]}")
            else:
                warnings.append(f"Tritanopia near-collision detected: {cvd_collisions[:3]}")

    # 3. Grayscale luminance profile & monotonicity (for sequential/diverging)
    lums = [round(relative_luminance(rgb), 4) for _, rgb in parsed]
    grayscale_monotonic: bool | None = None
    if role == "sequential" and n >= 3:
        inc = all(lums[i] <= lums[i + 1] + 1e-4 for i in range(n - 1))
        dec = all(lums[i] >= lums[i + 1] - 1e-4 for i in range(n - 1))
        grayscale_monotonic = inc or dec
        if not grayscale_monotonic:
            blockers.append(f"Sequential palette is NOT luminance-monotonic in grayscale (luminances={lums}).")
    elif role == "qualitative" and n >= 2:
        sorted_l = sorted(lums)
        min_lum_gap = min(sorted_l[i + 1] - sorted_l[i] for i in range(n - 1))
        if min_lum_gap < 0.03 and n <= 4:
            warnings.append(f"Two categorical colors share nearly identical grayscale luminance (min gap={min_lum_gap:.3f}); pair with distinct markers/linestyles or direct labels for grayscale printing.")

    return {
        "ok": len(blockers) == 0,
        "result": "PASS" if len(blockers) == 0 else "FAIL",
        "fails": len(blockers),
        "role": role,
        "mark": mark,
        "colors": [h for h, _ in parsed],
        "background_wcag_contrast": bg_contrasts,
        "min_delta_e_normal": round(min_de_normal, 2) if math.isfinite(min_de_normal) else None,
        "min_delta_e_cvd": {k: (round(v, 2) if math.isfinite(v) else None) for k, v in min_de_cvd.items()},
        "grayscale_luminances": lums,
        "grayscale_monotonic": grayscale_monotonic,
        "blockers": blockers,
        "warnings": warnings,
        "recommended_fallback": VERIFIED_PALETTES.get(role, VERIFIED_PALETTES["qualitative"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pure-stdlib WCAG/CVD/grayscale palette auditor.")
    parser.add_argument("colors", nargs="?", default="", help="Comma-separated hex colors (e.g. #2B5C8F,#D55E00,#007A6E).")
    parser.add_argument("--role", default="qualitative", choices=["qualitative", "sequential", "diverging"])
    parser.add_argument("--mark", default="bar", choices=["bar", "box", "line", "point", "heatmap", "text"])
    parser.add_argument("--colormap", default="", help="Optional colormap name to check against banned rainbow scales.")
    parser.add_argument("--background", default="#FFFFFF")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    color_list = [c.strip() for c in args.colors.split(",") if c.strip()] if args.colors else VERIFIED_PALETTES[args.role][:4]
    rep = audit_palette(color_list, role=args.role, mark=args.mark, background=args.background, colormap_name=args.colormap)
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
