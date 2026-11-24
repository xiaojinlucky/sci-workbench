#!/usr/bin/env python3
"""CLI wrapper to inspect .drawio mxGraph XML for overlaps and out-of-bounds vertices."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from layout_tools import analyze_layout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check draw.io layout for overlaps and boundary overflows.")
    parser.add_argument("--input", required=True, help="Input .drawio file path")
    parser.add_argument("--min-spacing", type=float, default=4.0)
    parser.add_argument("--out", default="", help="Optional JSON report path")
    args = parser.parse_args(argv)

    xml_text = Path(args.input).read_text(encoding="utf-8")
    report = analyze_layout(xml_text, min_spacing=args.min_spacing)
    blocking = len(report["overlaps"]) + len(report["out_of_bounds"])
    payload = {
        "ok": blocking == 0,
        "blocking_issues": blocking,
        "spacing_warnings": len(report["spacing_violations"]),
        "report": report,
    }
    encoded = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if blocking == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
