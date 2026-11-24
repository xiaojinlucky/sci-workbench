#!/usr/bin/env python3
"""Offline environment lock auditor & script-import dependency gap detector.

Pure Python 3.12 standard library. Probes real installed versions of Python,
SQLite, OS/architecture, determinism env vars, and scientific Python packages
without fabricating missing libraries. Optionally audits a target Python
script's AST imports to detect offline missing dependencies before execution.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Map import name -> distribution metadata name
SCIENTIFIC_PACKAGES: tuple[tuple[str, str], ...] = (
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("scipy", "scipy"),
    ("matplotlib", "matplotlib"),
    ("statsmodels", "statsmodels"),
    ("sklearn", "scikit-learn"),
    ("seaborn", "seaborn"),
)

DETERMINISM_ENV_KEYS: tuple[str, ...] = (
    "PYTHONHASHSEED",
    "PYTHONDONTWRITEBYTECODE",
    "MPLBACKEND",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "LC_ALL",
    "LANG",
)

STDLIB_FALLBACK_HINTS: dict[str, str] = {
    "pandas": "Use stdlib csv.DictReader + collections.defaultdict + statistics for tabular cleaning/aggregation.",
    "numpy": "Use stdlib math + statistics + array for deterministic numerical calculations.",
    "matplotlib": "Render crisp editable vector SVG using pure stdlib XML serialization (no third-party packages).",
    "scipy": "Use stdlib math/statistics for exact closed-form metrics; report UNAVAILABLE for specialized tests.",
    "seaborn": "Use pure stdlib SVG or native Matplotlib when installed.",
    "sklearn": "Report UNAVAILABLE if model fitting cannot be expressed cleanly via verified installed libraries.",
}


def _probe_package(import_name: str, dist_name: str | None = None) -> dict[str, Any]:
    spec = importlib.util.find_spec(import_name)
    if spec is None:
        return {"import_name": import_name, "status": "NOT_INSTALLED", "version": None}
    version = None
    for candidate in (dist_name or import_name, import_name):
        try:
            version = importlib.metadata.version(candidate)
            break
        except importlib.metadata.PackageNotFoundError:
            continue
    return {
        "import_name": import_name,
        "status": "AVAILABLE",
        "version": version or "installed_version_unknown",
    }


def _extract_script_imports(script_path: Path) -> list[str]:
    src = script_path.read_text(encoding="utf-8-sig")
    tree = ast.parse(src, filename=str(script_path))
    top_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                top_modules.add(node.module.split(".")[0])
    return sorted(top_modules)


def audit_environment(
    *,
    script_path: Path | None = None,
    required_packages: list[str] | None = None,
) -> dict[str, Any]:
    """Probe current offline runtime environment and verify script/required dependencies."""
    stdlib_names = set(getattr(sys, "stdlib_module_names", ()))
    pkg_matrix: dict[str, dict[str, Any]] = {}
    for imp_name, dist_name in SCIENTIFIC_PACKAGES:
        pkg_matrix[imp_name] = _probe_package(imp_name, dist_name)

    runtime_info = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "sqlite_version": sqlite3.sqlite_version,
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    }

    env_vars = {k: os.environ.get(k) for k in DETERMINISM_ENV_KEYS}

    # Compute deterministic fingerprint over runtime + package versions (excluding volatile timestamps)
    fp_payload = {
        "runtime": runtime_info,
        "packages": {k: v["version"] or "NOT_INSTALLED" for k, v in sorted(pkg_matrix.items())},
    }
    fingerprint = hashlib.sha256(json.dumps(fp_payload, sort_keys=True).encode("utf-8")).hexdigest()

    script_audit: dict[str, Any] | None = None
    offline_gaps: list[dict[str, str]] = []

    if script_path is not None:
        local_dir = script_path.resolve().parent
        bundled_dir = Path(__file__).resolve().parent
        imports = _extract_script_imports(script_path)
        stdlib_used: list[str] = []
        installed_third_party: list[dict[str, Any]] = []
        local_modules: list[str] = []

        for mod in imports:
            if mod in stdlib_names or mod == "__future__":
                stdlib_used.append(mod)
                continue
            if (
                (local_dir / f"{mod}.py").is_file()
                or (local_dir / mod / "__init__.py").is_file()
                or (bundled_dir / f"{mod}.py").is_file()
                or (bundled_dir / mod / "__init__.py").is_file()
            ):
                local_modules.append(mod)
                continue
            probe = pkg_matrix.get(mod) or _probe_package(mod)
            if probe["status"] == "AVAILABLE":
                installed_third_party.append(probe)
            else:
                offline_gaps.append(
                    {
                        "module": mod,
                        "source": script_path.name,
                        "status": "UNAVAILABLE",
                        "degradation_hint": STDLIB_FALLBACK_HINTS.get(
                            mod,
                            "Replace with verified stdlib/installed module or report UNAVAILABLE gap honestly.",
                        ),
                    }
                )

        script_audit = {
            "script": script_path.name,
            "stdlib_imports": stdlib_used,
            "installed_third_party": installed_third_party,
            "local_modules": local_modules,
            "missing_imports": [g["module"] for g in offline_gaps],
        }

    for req in required_packages or []:
        req_clean = req.strip()
        if not req_clean:
            continue
        probe = pkg_matrix.get(req_clean) or _probe_package(req_clean)
        if probe["status"] != "AVAILABLE":
            if not any(g["module"] == req_clean for g in offline_gaps):
                offline_gaps.append(
                    {
                        "module": req_clean,
                        "source": "--require",
                        "status": "UNAVAILABLE",
                        "degradation_hint": STDLIB_FALLBACK_HINTS.get(
                            req_clean,
                            "Package is absent in offline sandbox; use stdlib fallback or document gap.",
                        ),
                    }
                )

    available_pkgs = [k for k, v in pkg_matrix.items() if v["status"] == "AVAILABLE"]
    missing_pkgs = [k for k, v in pkg_matrix.items() if v["status"] != "AVAILABLE"]

    return {
        "ok": len(offline_gaps) == 0,
        "probed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment_fingerprint_sha256": fingerprint,
        "runtime": runtime_info,
        "determinism_env": env_vars,
        "scientific_packages": pkg_matrix,
        "capability_summary": {
            "stdlib_ready": True,
            "sqlite_ready": True,
            "available_scientific_packages": available_pkgs,
            "unavailable_scientific_packages": missing_pkgs,
            "recommended_visual_backend": "matplotlib+svg" if "matplotlib" in available_pkgs else "pure_stdlib_svg",
        },
        "script_import_audit": script_audit,
        "offline_dependency_gaps": offline_gaps,
    }


def _render_markdown(manifest: dict[str, Any]) -> str:
    rt = manifest["runtime"]
    lines = [
        "# Offline Environment Lock Manifest",
        "",
        f"- **Status**: `{'OK' if manifest['ok'] else 'DEPENDENCY_GAP'}`",
        f"- **Fingerprint SHA-256**: `{manifest['environment_fingerprint_sha256'][:16]}...`",
        f"- **Python**: `{rt['python_version']}` ({rt['python_implementation']}) | **SQLite**: `{rt['sqlite_version']}`",
        f"- **Platform**: `{rt['os']} {rt['os_release']} ({rt['machine']})` | **CPUs**: `{rt['cpu_count']}`",
        f"- **Visual Backend**: `{manifest['capability_summary']['recommended_visual_backend']}`",
        "",
        "## Scientific Package Matrix",
        "",
        "| Package | Status | Version |",
        "|---|---|---|",
    ]
    for name, info in manifest["scientific_packages"].items():
        lines.append(f"| `{name}` | `{info['status']}` | `{info['version'] or 'NOT_INSTALLED'}` |")
    if manifest["offline_dependency_gaps"]:
        lines.extend(["", "## Offline Dependency Gaps", ""])
        for gap in manifest["offline_dependency_gaps"]:
            lines.append(f"- **`{gap['module']}`** (from `{gap['source']}`): {gap['degradation_hint']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe offline Python/SQLite/scientific package versions and script dependency gaps.")
    parser.add_argument("--script", default=None, help="Optional Python script to audit AST imports against installed packages.")
    parser.add_argument("--require", default="", help="Optional comma-separated package names required by task.")
    parser.add_argument("--out", default=None, help="Optional output path for env_lock_manifest.json.")
    parser.add_argument("--md-out", default=None, help="Optional output path for Markdown summary.")
    args = parser.parse_args(argv)

    req_list = [x.strip() for x in args.require.split(",") if x.strip()]
    manifest = audit_environment(
        script_path=Path(args.script) if args.script else None,
        required_packages=req_list,
    )
    txt = json.dumps(manifest, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(txt + "\n", encoding="utf-8")
    if args.md_out:
        md_p = Path(args.md_out)
        md_p.parent.mkdir(parents=True, exist_ok=True)
        md_p.write_text(_render_markdown(manifest), encoding="utf-8")
    print(txt)
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
