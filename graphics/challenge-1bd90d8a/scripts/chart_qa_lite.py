#!/usr/bin/env python3
"""Lightweight zero-dependency static QA linter for publication chart scripts."""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

RAINBOW_CMAPS = {"jet", "rainbow", "hsv", "nipy_spectral", "gist_rainbow"}
FRONTEND_CLUTTER_RE = re.compile(r"\b(?:QC_|in-sample|temp_|debug_)\b|suptitle\s*\(|subtitle\s*=", re.I)


def audit_chart_script(script_path: Path) -> dict[str, Any]:
    src = script_path.read_text(encoding="utf-8")
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {
            "passed": False,
            "script": str(script_path),
            "issues": [{"rule": "syntax_error", "line": exc.lineno or 1, "message": str(exc)}],
            "warnings": [],
        }

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else (node.func.id if isinstance(node.func, ast.Name) else "")
            if fn == "savefig":
                for kw in node.keywords:
                    if kw.arg == "bbox_inches" and isinstance(kw.value, ast.Constant) and kw.value.value == "tight":
                        issues.append({
                            "rule": "locked_canvas_tight_override",
                            "line": node.lineno,
                            "message": "Avoid bbox_inches='tight'; preserve declared Cell physical dimensions with savefig.bbox='standard'.",
                        })
                    if kw.arg == "dpi" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, (int, float)) and kw.value.value < 300:
                        warnings.append({
                            "rule": "low_dpi",
                            "line": node.lineno,
                            "message": f"Export dpi={kw.value.value} < 300; use >= 300 dpi for publication raster previews.",
                        })
            if fn in {"twinx", "twiny"}:
                warnings.append({
                    "rule": "dual_axis_detected",
                    "line": node.lineno,
                    "message": "Dual axis (twinx/twiny) detected; prefer split aligned panels unless dual scales are explicitly justified.",
                })
            for kw in node.keywords:
                if kw.arg in {"cmap", "colormap"} and isinstance(kw.value, ast.Constant) and str(kw.value.value).lower() in RAINBOW_CMAPS:
                    issues.append({
                        "rule": "rainbow_colormap",
                        "line": node.lineno,
                        "message": f"Non-perceptual colormap '{kw.value.value}' detected; use curated CVD-safe sequential/diverging palette.",
                    })
                if kw.arg == "fontsize" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, (int, float)) and kw.value.value < 7:
                    warnings.append({
                        "rule": "tiny_font_size",
                        "line": node.lineno,
                        "message": f"fontsize={kw.value.value} < 7 pt may become illegible at single-column width.",
                    })

    if "svg.fonttype" not in src and "rcParams" in src:
        warnings.append({
            "rule": "missing_svg_fonttype_none",
            "line": 1,
            "message": "Set plt.rcParams['svg.fonttype'] = 'none' to preserve live editable SVG text nodes.",
        })
    if "pdf.fonttype" not in src and "rcParams" in src:
        warnings.append({
            "rule": "missing_pdf_fonttype_42",
            "line": 1,
            "message": "Set plt.rcParams['pdf.fonttype'] = 42 for TrueType vector PDF text.",
        })

    for idx, line in enumerate(src.splitlines(), 1):
        m = FRONTEND_CLUTTER_RE.search(line)
        if m:
            warnings.append({
                "rule": "frontend_clutter_or_debug_token",
                "line": idx,
                "message": f"Potential backend debug token or subtitle clutter '{m.group(0)}' in figure script.",
            })

    return {
        "passed": len(issues) == 0,
        "script": str(script_path),
        "issues_count": len(issues),
        "warnings_count": len(warnings),
        "issues": issues,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lightweight static QA check for scientific figure Python scripts.")
    parser.add_argument("--script", required=True, help="Target figure generation Python script.")
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    parser.add_argument("--strict", action="store_true", help="Fail if warnings are present too.")
    args = parser.parse_args(argv)

    p = Path(args.script)
    if not p.is_file():
        print(json.dumps({"passed": False, "error": f"script not found: {p}"}))
        return 2
    rep = audit_chart_script(p)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"passed": rep["passed"], "issues_count": rep["issues_count"], "warnings_count": rep["warnings_count"]}))
    if not rep["passed"]:
        return 1
    if args.strict and rep["warnings_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
