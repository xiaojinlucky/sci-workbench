#!/usr/bin/env python3
"""Offline Environment, Lockfile Drift & Research-Repo-Doctor Reproducibility Auditor.

Pure Python 3.12 standard library (uses built-in tomllib + importlib.metadata).
Capabilities:
1. Scans input repository for environment.yml/yaml, requirements*.txt, pyproject.toml,
   conda-lock.yml, uv.lock, poetry.lock, Dockerfile, README, LICENSE, CITATION.cff.
2. Detects Conda #15581 / PR #15838 mixed-source lockfile drift (conda + pip subsections
   or co-existing unpinned requirements.txt), unpinned dependencies (bare names, >=, *, VCS),
   platform-bound packages/build hashes, and declared-vs-installed runtime version drift.
3. Emits a research-repo-doctor style executable checklist separating auto_discovered facts,
   manual_action_items, and missing_items.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

NON_PYTHON_CONDA_SYSTEM_PKGS = {
    "python", "pip", "wheel", "setuptools", "conda", "mamba", "libgcc-ng",
    "libstdcxx-ng", "ca-certificates", "openssl", "zlib", "ncurses", "readline",
    "sqlite", "tk", "xz", "bzip2", "cudatoolkit", "cuda-toolkit", "cudnn",
    "vc", "vs2015_runtime", "ucrt", "mkl", "openblas", "blas", "lapack",
}

PLATFORM_BOUND_KEYWORDS = (
    "win-64", "win32", "osx-arm64", "osx-64", "vc14", "vs2015",
    "cudatoolkit", "nvidia-", "triton", "libgcc-ng", "libstdcxx-ng", "apple-",
)

CONDA_BUILD_HASH_RE = re.compile(r"=[A-Za-z0-9_]*h[0-9a-f]{6,}[A-Za-z0-9_]*$", re.I)
REQ_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?\s*(.*)$")


def _norm_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _installed_packages() -> dict[str, str]:
    installed: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        meta_name = dist.metadata.get("Name") if dist.metadata else None
        if meta_name:
            installed[_norm_pkg(meta_name)] = dist.version
    return installed


def _classify_spec(raw_spec: str, source_file: str, ecosystem: str) -> dict[str, Any] | None:
    line = raw_spec.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "--")):
        return None
    # Handle VCS/URL dependency
    if line.startswith(("git+", "http://", "https://")) or " @ " in line:
        pinned_commit = bool(re.search(r"@[0-9a-f]{7,40}\b", line))
        pkg_name = line.split(" @ ", 1)[0].strip() if " @ " in line else line.rsplit("/", 1)[-1].split("@", 1)[0]
        return {
            "name": pkg_name,
            "norm_name": _norm_pkg(pkg_name),
            "raw_spec": line,
            "source_file": source_file,
            "ecosystem": ecosystem,
            "pin_status": "pinned_vcs_commit" if pinned_commit else "unpinned_vcs_or_url",
            "pinned_version": None,
            "platform_bound": False,
        }

    m = REQ_SPEC_RE.match(line)
    if not m:
        return None
    name = m.group(1)
    rest = m.group(2).split(";", 1)[0].strip()
    norm = _norm_pkg(name)

    platform_bound = any(kw in norm or kw in rest.lower() for kw in PLATFORM_BOUND_KEYWORDS)
    if ecosystem == "conda" and CONDA_BUILD_HASH_RE.search(line):
        platform_bound = True

    pin_status = "unpinned_bare"
    pinned_version: str | None = None

    if not rest:
        pin_status = "unpinned_bare"
    elif "*" in rest:
        pin_status = "unpinned_wildcard"
    elif rest.startswith("=="):
        ver = rest[2:].split("=", 1)[0].split(",", 1)[0].strip()
        if ver and "*" not in ver:
            pin_status = "exact_pinned"
            pinned_version = ver
        else:
            pin_status = "unpinned_wildcard"
    elif ecosystem == "conda" and rest.startswith("=") and not rest.startswith("=="):
        # Conda spec like numpy=1.26.4 or numpy=1.26.4=py311h...
        ver = rest[1:].split("=", 1)[0].strip()
        if ver and "*" not in ver and ver.count(".") >= 1:
            pin_status = "exact_pinned" if ver.count(".") >= 2 else "partial_minor_pin"
            pinned_version = ver
        else:
            pin_status = "unpinned_wildcard"
    elif any(op in rest for op in (">=", "<=", "~=", ">", "<", "!=")):
        pin_status = "unpinned_range_or_lower_bound"
    else:
        pin_status = "unpinned_other"

    return {
        "name": name,
        "norm_name": norm,
        "raw_spec": line,
        "source_file": source_file,
        "ecosystem": ecosystem,
        "pin_status": pin_status,
        "pinned_version": pinned_version,
        "platform_bound": platform_bound,
    }


def _parse_environment_yml(path: Path, rel: str) -> tuple[list[dict[str, Any]], bool]:
    deps: list[dict[str, Any]] = []
    has_pip_subsection = False
    in_deps = False
    in_pip = False
    pip_indent = 0
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if re.match(r"^dependencies\s*:", stripped):
            in_deps = True
            in_pip = False
            continue
        if in_deps and indent == 0 and not stripped.startswith("-"):
            in_deps = False
            in_pip = False
            continue
        if not in_deps:
            continue
        if re.match(r"^-\s*pip\s*:\s*$", stripped):
            has_pip_subsection = True
            in_pip = True
            pip_indent = indent
            continue
        if in_pip and indent <= pip_indent:
            in_pip = False
        if stripped.startswith("-"):
            item = stripped[1:].strip().strip('"').strip("'")
            if not item or item.lower() == "pip":
                continue
            eco = "pip_in_conda_yml" if in_pip else "conda"
            parsed = _classify_spec(item, rel, eco)
            if parsed:
                deps.append(parsed)
    return deps, has_pip_subsection


def _parse_requirements_txt(path: Path, rel: str) -> list[dict[str, Any]]:
    deps: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _classify_spec(raw_line, rel, "pip")
        if parsed:
            deps.append(parsed)
    return deps


def _parse_pyproject_toml(path: Path, rel: str) -> list[dict[str, Any]]:
    deps: list[dict[str, Any]] = []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return deps
    proj = data.get("project") if isinstance(data.get("project"), dict) else {}
    for item in proj.get("dependencies") or []:
        if isinstance(item, str):
            parsed = _classify_spec(item, rel, "pyproject")
            if parsed:
                deps.append(parsed)
    opt = proj.get("optional-dependencies") if isinstance(proj.get("optional-dependencies"), dict) else {}
    for grp_items in opt.values():
        if isinstance(grp_items, list):
            for item in grp_items:
                if isinstance(item, str):
                    parsed = _classify_spec(item, rel, "pyproject_optional")
                    if parsed:
                        deps.append(parsed)
    poetry_deps = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies")
    if isinstance(poetry_deps, dict):
        for pkg, spec in poetry_deps.items():
            if pkg.lower() == "python":
                continue
            spec_str = f"{pkg}=={spec}" if isinstance(spec, str) and re.match(r"^\d+\.\d+\.\d+$", spec) else f"{pkg}{spec if isinstance(spec, str) else ''}"
            parsed = _classify_spec(spec_str, rel, "poetry")
            if parsed:
                deps.append(parsed)
    return deps


def _parse_dockerfile(path: Path, rel: str) -> list[dict[str, Any]]:
    deps: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    # Join backslash continuations
    text = re.sub(r"\\\r?\n", " ", text)
    for m in re.finditer(r"pip3?\s+install\s+([^&\n;|]+)", text):
        tokens = m.group(1).split()
        skip_next = False
        for tok in tokens:
            if skip_next:
                skip_next = False
                continue
            if tok in ("-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "--extra-index-url"):
                skip_next = True
                continue
            if tok.startswith("-"):
                continue
            parsed = _classify_spec(tok.strip('"').strip("'"), rel, "dockerfile_pip")
            if parsed:
                deps.append(parsed)
    return deps


def _compare_version_drift(deps: list[dict[str, Any]], installed: dict[str, str]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for d in deps:
        norm = d["norm_name"]
        if norm == "python":
            inst_py = platform.python_version()
            decl_m = re.search(r"python\s*[=~<>!]{1,2}\s*([0-9]+(?:\.[0-9]+){1,2})", d["raw_spec"], re.I)
            decl_py = decl_m.group(1) if decl_m else d.get("pinned_version")
            if decl_py:
                decl_parts = decl_py.split(".")
                inst_parts = inst_py.split(".")
                py_match = inst_parts[:len(decl_parts)] == decl_parts
                py_drift = "exact_match" if py_match else "version_drift"
            else:
                py_drift = "unpinned_in_manifest"
            comparisons.append({
                "name": d["name"],
                "source_file": d["source_file"],
                "ecosystem": d["ecosystem"],
                "declared_spec": d["raw_spec"],
                "pinned_version": decl_py,
                "installed_version": inst_py,
                "drift_status": py_drift,
            })
            continue
        if norm in NON_PYTHON_CONDA_SYSTEM_PKGS:
            comparisons.append({
                "name": d["name"],
                "source_file": d["source_file"],
                "ecosystem": d["ecosystem"],
                "declared_spec": d["raw_spec"],
                "installed_version": None,
                "drift_status": "system_or_runtime_dep",
            })
            continue
        inst_ver = installed.get(norm)
        if inst_ver is None:
            drift_status = "declared_but_missing_in_runtime"
        elif d["pin_status"] == "exact_pinned" and d["pinned_version"]:
            drift_status = "exact_match" if inst_ver == d["pinned_version"] else "version_drift"
        else:
            drift_status = "unpinned_in_manifest"
        comparisons.append({
            "name": d["name"],
            "source_file": d["source_file"],
            "ecosystem": d["ecosystem"],
            "declared_spec": d["raw_spec"],
            "pinned_version": d["pinned_version"],
            "installed_version": inst_ver,
            "drift_status": drift_status,
        })
    return comparisons


def audit_environment_and_locks(input_dir: Path) -> dict[str, Any]:
    """Scan repository manifests, audit Conda/Pip/UV lockfile drift, and emit research-repo-doctor checklist."""
    installed = _installed_packages()
    manifests_found: dict[str, list[str]] = {
        "conda_env_files": [],
        "requirements_files": [],
        "pyproject_files": [],
        "lockfiles": [],
        "dockerfiles": [],
        "documentation_files": [],
        "license_or_citation_files": [],
        "candidate_scripts": [],
    }

    declared_deps: list[dict[str, Any]] = []
    conda_pip_subsection_files: list[str] = []

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        rel = fp.relative_to(input_dir).as_posix()
        lname = fp.name.lower()
        if lname in ("environment.yml", "environment.yaml", "conda.yml", "conda.yaml"):
            manifests_found["conda_env_files"].append(rel)
            c_deps, has_pip = _parse_environment_yml(fp, rel)
            declared_deps.extend(c_deps)
            if has_pip:
                conda_pip_subsection_files.append(rel)
        elif re.match(r"^requirements.*\.txt$", lname):
            manifests_found["requirements_files"].append(rel)
            declared_deps.extend(_parse_requirements_txt(fp, rel))
        elif lname == "pyproject.toml":
            manifests_found["pyproject_files"].append(rel)
            declared_deps.extend(_parse_pyproject_toml(fp, rel))
        elif lname in ("conda-lock.yml", "uv.lock", "poetry.lock", "pipfile.lock"):
            manifests_found["lockfiles"].append(rel)
        elif lname == "dockerfile" or lname.startswith("dockerfile."):
            manifests_found["dockerfiles"].append(rel)
            declared_deps.extend(_parse_dockerfile(fp, rel))
        elif lname.startswith("readme"):
            manifests_found["documentation_files"].append(rel)
        elif lname.startswith("license") or lname == "citation.cff":
            manifests_found["license_or_citation_files"].append(rel)
        elif fp.suffix.lower() == ".py" and not lname.startswith("test_"):
            manifests_found["candidate_scripts"].append(rel)

    conda_pip_mixed = bool(conda_pip_subsection_files) or (
        bool(manifests_found["conda_env_files"]) and bool(manifests_found["requirements_files"])
    )
    unpinned_deps = [d for d in declared_deps if d["pin_status"] not in ("exact_pinned", "pinned_vcs_commit")]
    platform_bound_deps = [d for d in declared_deps if d["platform_bound"]]
    runtime_drift = _compare_version_drift(declared_deps, installed)
    version_drifts = [r for r in runtime_drift if r["drift_status"] == "version_drift"]
    missing_in_runtime = [r for r in runtime_drift if r["drift_status"] == "declared_but_missing_in_runtime"]

    # Build research-repo-doctor style executable checklist
    auto_discovered: list[dict[str, Any]] = [
        {"item": "runtime_python_version", "status": "verified", "detail": platform.python_version()},
        {"item": "runtime_platform", "status": "verified", "detail": f"{sys.platform} ({platform.machine()})"},
        {"item": "pythonhashseed_env", "status": "verified" if os.environ.get("PYTHONHASHSEED") else "unset", "detail": os.environ.get("PYTHONHASHSEED", "unset")},
        {"item": "candidate_python_scripts", "status": "found" if manifests_found["candidate_scripts"] else "missing", "detail": manifests_found["candidate_scripts"]},
        {"item": "dependency_manifests", "status": "found" if (manifests_found["conda_env_files"] or manifests_found["requirements_files"] or manifests_found["pyproject_files"]) else "missing",
         "detail": manifests_found["conda_env_files"] + manifests_found["requirements_files"] + manifests_found["pyproject_files"]},
        {"item": "strict_lockfiles", "status": "found" if manifests_found["lockfiles"] else "missing", "detail": manifests_found["lockfiles"]},
        {"item": "readme_documentation", "status": "found" if manifests_found["documentation_files"] else "missing", "detail": manifests_found["documentation_files"]},
        {"item": "license_or_citation", "status": "found" if manifests_found["license_or_citation_files"] else "missing", "detail": manifests_found["license_or_citation_files"]},
    ]

    manual_action_items: list[dict[str, Any]] = []
    missing_items: list[dict[str, Any]] = []

    if conda_pip_mixed and not manifests_found["lockfiles"]:
        manual_action_items.append({
            "severity": "high",
            "code": "CONDA_PIP_MIXED_DRIFT_RISK",
            "action": "Export conda and pip dependencies into a strict lockfile (conda-lock.yml or uv.lock) and pin every pip wheel version (Conda #15581 / PR #15838 drift boundary).",
            "evidence": conda_pip_subsection_files or (manifests_found["conda_env_files"] + manifests_found["requirements_files"]),
        })
    if unpinned_deps:
        manual_action_items.append({
            "severity": "high",
            "code": "UNPINNED_DEPENDENCIES",
            "action": f"Pin {len(unpinned_deps)} unpinned dependency specifier(s) to exact versions (==X.Y.Z).",
            "evidence": [f"{d['source_file']}:{d['raw_spec']} ({d['pin_status']})" for d in unpinned_deps[:15]],
        })
    if platform_bound_deps:
        manual_action_items.append({
            "severity": "medium",
            "code": "PLATFORM_BOUND_PACKAGES",
            "action": f"Strip OS/GPU-specific build strings or isolate {len(platform_bound_deps)} platform-bound package(s) into a separate hardware profile.",
            "evidence": [f"{d['source_file']}:{d['raw_spec']}" for d in platform_bound_deps[:15]],
        })
    if version_drifts:
        manual_action_items.append({
            "severity": "high",
            "code": "DECLARED_VS_INSTALLED_VERSION_DRIFT",
            "action": f"Reconcile {len(version_drifts)} package version mismatch(es) between manifest and active runtime before interpreting metric differences.",
            "evidence": [f"{r['name']}: declared={r['pinned_version']} vs installed={r['installed_version']}" for r in version_drifts],
        })
    if missing_in_runtime:
        missing_items.append({
            "code": "DECLARED_PACKAGES_MISSING_IN_RUNTIME",
            "detail": [f"{r['name']} ({r['source_file']})" for r in missing_in_runtime],
        })
        manual_action_items.append({
            "severity": "high",
            "code": "INSTALL_MISSING_DECLARED_PACKAGES",
            "action": f"Verify whether {len(missing_in_runtime)} declared package(s) are needed by candidate scripts; they are absent in current runtime.",
            "evidence": [r["name"] for r in missing_in_runtime[:15]],
        })
    if not manifests_found["lockfiles"]:
        missing_items.append({"code": "NO_STRICT_LOCKFILE", "detail": "No conda-lock.yml, uv.lock, poetry.lock, or Pipfile.lock found."})
    if not manifests_found["documentation_files"]:
        missing_items.append({"code": "NO_README_FILE", "detail": "No README documentation found in input snapshot."})
    if not manifests_found["license_or_citation_files"]:
        missing_items.append({"code": "NO_LICENSE_OR_CITATION", "detail": "No LICENSE or CITATION.cff found in input snapshot."})

    # Transparent checklist score (strictly engineering readiness, NOT scientific reproduction!)
    checks = [
        bool(manifests_found["candidate_scripts"]),
        bool(manifests_found["conda_env_files"] or manifests_found["requirements_files"] or manifests_found["pyproject_files"]),
        bool(manifests_found["lockfiles"]),
        len(unpinned_deps) == 0 and len(declared_deps) > 0,
        not conda_pip_mixed or bool(manifests_found["lockfiles"]),
        len(version_drifts) == 0,
        len(missing_in_runtime) == 0,
        bool(manifests_found["documentation_files"]),
    ]
    readiness_ratio = round(sum(1 for c in checks if c) / len(checks), 4)

    return {
        "manifests_found": manifests_found,
        "conda_pip_mixed": conda_pip_mixed,
        "conda_pip_subsection_files": conda_pip_subsection_files,
        "declared_dependencies": declared_deps,
        "unpinned_dependencies": unpinned_deps,
        "platform_bound_dependencies": platform_bound_deps,
        "runtime_version_comparisons": runtime_drift,
        "version_drifts": version_drifts,
        "missing_in_runtime": missing_in_runtime,
        "research_repo_doctor_checklist": {
            "auto_discovered": auto_discovered,
            "manual_action_items": manual_action_items,
            "missing_items": missing_items,
            "engineering_readiness_ratio": readiness_ratio,
            "readiness_disclaimer": "Engineering manifest readiness does NOT establish numerical or scientific reproducibility.",
        },
        "summary": {
            "declared_dependency_count": len(declared_deps),
            "unpinned_dependency_count": len(unpinned_deps),
            "platform_bound_count": len(platform_bound_deps),
            "version_drift_count": len(version_drifts),
            "missing_in_runtime_count": len(missing_in_runtime),
            "conda_pip_mixed": conda_pip_mixed,
            "strict_lockfile_present": bool(manifests_found["lockfiles"]),
            "manual_action_count": len(manual_action_items),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline environment, lockfile drift, and research-repo-doctor auditor.")
    parser.add_argument("--input", required=True, help="Input directory to scan.")
    parser.add_argument("--out", default="", help="Optional JSON output path.")
    args = parser.parse_args(argv)

    report = audit_environment_and_locks(Path(args.input))
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded, encoding="utf-8")
    print(json.dumps({"ok": True, "summary": report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
