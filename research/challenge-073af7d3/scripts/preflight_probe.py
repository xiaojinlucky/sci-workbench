"""Zero-dependency offline Linux sandbox environment and input probe."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

SAFE_CLI_BINS = (
    "python3",
    "pdftoppm",
    "pdftotext",
    "soffice",
    "libreoffice",
    "pandoc",
    "xelatex",
    "pdflatex",
    "rsvg-convert",
    "node",
    "sqlite3",
    "chromium",
    "google-chrome",
)

SAFE_PY_PKGS = (
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "seaborn",
    "PIL",
    "pptx",
    "docx",
    "openpyxl",
    "pypdf",
    "pdfplumber",
    "reportlab",
    "playwright",
    "sqlite3",
    "yaml",
)


def _read_cgroup_mem_mb() -> int | None:
    """Read container memory limit in MiB when running under Linux cgroups."""
    for p in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            raw = Path(p).read_text(encoding="utf-8").strip()
            if raw.isdigit():
                val = int(raw)
                if 0 < val < (1 << 50):
                    return round(val / (1024 * 1024))
        except OSError:
            continue
    return None


def _dir_writable(path: Path) -> bool:
    try:
        return path.exists() and path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


def _scan_inputs(input_dir: Path, max_files: int = 120) -> dict[str, Any]:
    """Inventory files in input_dir without reading large payloads into memory."""
    if not input_dir.exists() or not input_dir.is_dir():
        return {"exists": False, "file_count": 0, "files": [], "case_json": None}
    files: list[dict[str, Any]] = []
    case_json_path: str | None = None
    total_bytes = 0
    for root, _, names in os.walk(input_dir):
        for name in sorted(names):
            fpath = Path(root) / name
            try:
                sz = fpath.stat().st_size
            except OSError:
                sz = -1
            rel = fpath.relative_to(input_dir).as_posix()
            total_bytes += max(sz, 0)
            if name == "case.json" and case_json_path is None:
                case_json_path = str(fpath)
            if len(files) < max_files:
                files.append({"rel": rel, "bytes": sz, "suffix": fpath.suffix.lower()})
    return {
        "exists": True,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "case_json": case_json_path,
        "files": files,
    }


def resolve_default_paths(
    app_root: str | Path = "/app",
    input_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    work_dir: str | Path | None = None,
) -> dict[str, Path]:
    """Resolve canonical offline workspace runtime paths (/app/output and /app/work when /app exists)."""
    app_path = Path(app_root)
    has_app = app_path.is_dir()

    if input_dir:
        in_path = Path(input_dir)
    elif Path("/input").is_dir():
        in_path = Path("/input")
    elif (app_path / "input").is_dir():
        in_path = app_path / "input"
    else:
        in_path = Path("/input")

    if output_dir:
        out_path = Path(output_dir)
    elif has_app:
        out_path = app_path / "output"
    else:
        out_path = Path("/output")

    if work_dir:
        wk_path = Path(work_dir)
    elif has_app:
        wk_path = app_path / "work"
    else:
        wk_path = Path.cwd()

    return {"input_dir": in_path, "output_dir": out_path, "work_dir": wk_path}


def probe_environment(
    input_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    *,
    work_dir: str | Path | None = None,
    app_root: str | Path = "/app",
) -> dict[str, Any]:
    """Collect sandbox capabilities, toolchain tier, and input manifest."""
    resolved = resolve_default_paths(app_root, input_dir, output_dir, work_dir)
    in_path = resolved["input_dir"]
    out_path = resolved["output_dir"]
    wk_path = resolved["work_dir"]

    bins = {name: shutil.which(name) is not None for name in SAFE_CLI_BINS}
    py_pkgs = {pkg: importlib.util.find_spec(pkg) is not None for pkg in SAFE_PY_PKGS}

    has_office = bool(bins.get("soffice") or bins.get("libreoffice") or bins.get("pdftoppm"))
    has_sci_py = bool(py_pkgs.get("numpy") and py_pkgs.get("pandas") and py_pkgs.get("matplotlib"))
    if has_office and (py_pkgs.get("pptx") or py_pkgs.get("docx")):
        tier = "full_office"
    elif has_sci_py:
        tier = "scientific_python_only"
    else:
        tier = "pure_stdlib"

    return {
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "cpu_count": os.cpu_count() or 1,
        "cgroup_mem_mb": _read_cgroup_mem_mb(),
        "cwd": str(Path.cwd()),
        "resolved_paths": {
            "input_dir": str(in_path),
            "output_dir": str(out_path),
            "work_dir": str(wk_path),
        },
        "paths": {
            "app_exists": Path(app_root).is_dir(),
            "input_exists": in_path.is_dir(),
            "output_writable": _dir_writable(out_path),
            "work_writable": _dir_writable(wk_path),
            "cwd_writable": _dir_writable(Path.cwd()),
        },
        "binaries": bins,
        "python_packages": py_pkgs,
        "toolchain_tier": tier,
        "inputs": _scan_inputs(in_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="offline Linux sandbox preflight probe.")
    parser.add_argument("--input", default="", help="Input directory path (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory path (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory path (auto-detects /app/work or cwd).")
    parser.add_argument("--out", default="", help="Optional JSON output file path.")
    args = parser.parse_args(argv)

    report = probe_environment(args.input or None, args.output or None, work_dir=args.work or None)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out_file = Path(args.out)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
