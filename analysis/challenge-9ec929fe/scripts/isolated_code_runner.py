#!/usr/bin/env python3
"""Isolated scientific script execution runner with SHA-256 snapshot diffing,
anti-stale-artifact protection, numeric invariant verification, and pure-stdlib SVG visual.

Pure Python 3.12 standard library core (runs natively in python:3.12-slim).
When Matplotlib is genuinely installed, optionally renders an additional 300-dpi PNG preview.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from code_contract_checker import audit_script_contract
from env_lock_auditor import audit_environment

_NONFINITE_TOKENS = {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_dir(root: Path) -> dict[str, dict[str, Any]]:
    snap: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            snap[rel] = {"rel": rel, "bytes": p.stat().st_size, "sha256": _sha256_file(p)}
    return snap


def _scan_json_finite(obj: Any, path: str = "root") -> list[str]:
    bad: list[str] = []
    if isinstance(obj, float) and not math.isfinite(obj):
        bad.append(f"{path}=nonfinite({obj})")
    elif isinstance(obj, str) and obj.strip().lower() in _NONFINITE_TOKENS:
        bad.append(f"{path}=nonfinite_string({obj!r})")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            bad.extend(_scan_json_finite(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad.extend(_scan_json_finite(v, f"{path}[{i}]"))
    return bad


def _verify_csv_invariants(csv_path: Path, expected_rows: int | None = None) -> list[str]:
    violations: list[str] = []
    rel = csv_path.name
    if csv_path.stat().st_size == 0:
        return [f"{rel}: empty 0-byte CSV file"]
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
    except Exception as exc:
        return [f"{rel}: CSV read error ({exc})"]
    if len(rows) <= 1:
        violations.append(f"{rel}: header-only CSV with 0 data rows")
        return violations
    header = rows[0]
    width = len(header)
    data_rows = [r for r in rows[1:] if any(cell.strip() for cell in r)]
    if not data_rows:
        violations.append(f"{rel}: all data rows are blank")
        return violations
    if expected_rows is not None and len(data_rows) != expected_rows:
        violations.append(f"{rel}: row_count={len(data_rows)} mismatches expected_rows={expected_rows}")
    for idx, r in enumerate(data_rows, start=2):
        if len(r) != width:
            violations.append(f"{rel}:L{idx} column width mismatch ({len(r)} != {width})")
            break
        for c_idx, cell in enumerate(r):
            if cell.strip().lower() in _NONFINITE_TOKENS:
                cname = header[c_idx] if c_idx < width else f"col_{c_idx}"
                violations.append(f"{rel}:L{idx} ({cname}) contains non-finite token {cell!r}")
                break
    return violations


def _classify_failure(
    static_audit: dict[str, Any],
    env_audit: dict[str, Any],
    timed_out: bool,
    exit_code: int | None,
    stderr_text: str,
    invariant_violations: list[str],
    fresh_count: int,
    min_artifacts: int,
) -> str:
    if not static_audit["ok"]:
        return "STATIC_CONTRACT_BLOCKER"
    if not env_audit["ok"]:
        return "MISSING_OFFLINE_DEPENDENCY"
    if timed_out:
        return "EXECUTION_TIMEOUT"
    if exit_code != 0:
        if "ModuleNotFoundError" in stderr_text or "ImportError" in stderr_text:
            return "MISSING_OFFLINE_DEPENDENCY"
        if "KeyError" in stderr_text or "ValueError" in stderr_text or "FileNotFoundError" in stderr_text:
            return "INPUT_SCHEMA_OR_ENCODING_ERROR"
        if "readonly database" in stderr_text.lower():
            return "SQLITE_PERMISSION_ERROR"
        return "RUNTIME_EXECUTION_ERROR"
    if fresh_count < min_artifacts:
        return "NO_FRESH_ARTIFACTS_PRODUCED"
    if invariant_violations:
        return "NUMERIC_OR_STRUCTURAL_INVARIANT_FAILURE"
    return "NONE"


def _render_stdlib_svg(
    svg_path: Path,
    *,
    script_name: str,
    status: str,
    elapsed_s: float,
    seed: int,
    gates: list[tuple[str, bool, str]],
) -> None:
    """Render a crisp, editable pure-stdlib SVG execution verification summary."""
    w, h = 680, 290
    bar_x = 185
    bar_max_w = 340
    row_h = 34
    start_y = 66
    status_color = "#2E7D6B" if status == "ok" else "#C65A4F"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        '<rect x="12" y="12" width="656" height="266" rx="6" fill="#FAFBFD" stroke="#D8E0E8" stroke-width="1"/>',
        f'<text x="28" y="38" font-family="DejaVu Sans, Arial, sans-serif" font-size="14" font-weight="bold" fill="#1F2937">'
        f'Isolated Execution Gate: {html.escape(script_name)}</text>',
        f'<text x="28" y="54" font-family="DejaVu Sans, Arial, sans-serif" font-size="10.5" fill="#4B5563">'
        f'Status: {html.escape(status.upper())} | Elapsed: {elapsed_s:.3f}s | PYTHONHASHSEED={seed}</text>',
        f'<rect x="546" y="22" width="108" height="24" rx="4" fill="{status_color}"/>',
        f'<text x="600" y="38" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="11" font-weight="bold" fill="#FFFFFF">'
        f'{html.escape("PASS" if status == "ok" else "STALE/FAIL")}</text>',
    ]

    for idx, (label, passed, detail) in enumerate(gates):
        y = start_y + idx * row_h
        fill = "#4E9187" if passed else "#D77C70"
        bar_w = bar_max_w if passed else int(bar_max_w * 0.28)
        badge = "PASS" if passed else "FAIL"
        parts.extend(
            [
                f'<text x="28" y="{y + 18}" font-family="DejaVu Sans, Arial, sans-serif" font-size="11.5" fill="#1F2937">{html.escape(label)}</text>',
                f'<rect x="{bar_x}" y="{y + 4}" width="{bar_max_w}" height="18" rx="3" fill="#EEF2F6"/>',
                f'<rect x="{bar_x}" y="{y + 4}" width="{bar_w}" height="18" rx="3" fill="{fill}"/>',
                f'<text x="{bar_x + bar_max_w + 10}" y="{y + 17}" font-family="DejaVu Sans, Arial, sans-serif" font-size="10.5" font-weight="bold" fill="{fill}">{badge}</text>',
                f'<text x="{bar_x + 8}" y="{y + 17}" font-family="DejaVu Sans, Arial, sans-serif" font-size="9.5" fill="#FFFFFF">{html.escape(detail[:48])}</text>',
            ]
        )

    parts.append("</svg>")
    svg_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _try_render_matplotlib_png(
    png_path: Path,
    *,
    script_name: str,
    elapsed_s: float,
    gates: list[tuple[str, bool, str]],
) -> bool:
    """Optionally render a 300-dpi PNG preview ONLY when Matplotlib is genuinely installed."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Liberation Sans"],
            "font.size": 8.5,
        }
    )
    labels = [g[0] for g in gates]
    vals = [1.0 if g[1] else 0.25 for g in gates]
    cols = ["#4E9187" if g[1] else "#D77C70" for g in gates]
    y_pos = list(range(len(labels)))

    fig, ax = plt.subplots(figsize=(5.6, 3.0), dpi=300)
    ax.barh(y_pos, vals, color=cols, edgecolor="#222222", linewidth=0.7, height=0.58)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.25)
    ax.set_xticks([0.25, 1.0])
    ax.set_xticklabels(["FAIL", "PASS"])
    ax.set_title(f"Isolated Execution Gate: {script_name} ({elapsed_s:.2f}s)", loc="center", fontsize=9.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for y, (_, ok_flag, detail) in enumerate(gates):
        ax.text(1.03 if ok_flag else 0.28, y, "PASS" if ok_flag else "FAIL", va="center", fontsize=7.5, fontweight="bold")
    fig.tight_layout()
    fig.savefig(png_path, format="png", dpi=300)
    plt.close(fig)
    return True


def run_isolated(
    script_path: Path,
    work_dir: Path,
    out_dir: Path,
    *,
    stem: str = "exec_verification",
    seed: int = 42,
    timeout_s: int = 120,
    input_csv: Path | None = None,
    expected_rows: int | None = None,
    min_artifacts: int = 1,
    script_args: list[str] | None = None,
    allow_static_warnings: bool = True,
) -> dict[str, Any]:
    """Execute script in isolated work_dir with full snapshot hashing, invariant checks, and anti-stale guard."""
    script_abs = script_path.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    script_sha = _sha256_file(script_abs)
    static_audit = audit_script_contract(script_abs, input_csv=input_csv, strict=not allow_static_warnings)
    env_audit = audit_environment(script_path=script_abs)
    pre_snapshot = _snapshot_dir(work_dir)

    cmd = [sys.executable, "-B", str(script_abs)] + (script_args or [])
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(seed)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MPLBACKEND"] = "Agg"
    env["OMP_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"

    timed_out = False
    exit_code: int | None = None
    stdout_text = ""
    stderr_text = ""
    executed = False
    t0 = time.monotonic()

    # Block execution immediately if static contract blockers or offline missing imports exist
    if static_audit["ok"] and env_audit["ok"]:
        executed = True
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(work_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
            exit_code = proc.returncode
            stdout_text = proc.stdout
            stderr_text = proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout_text = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr_text = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            stderr_text += f"\n[runner] Execution exceeded timeout ({timeout_s}s) and was terminated."
    else:
        exit_code = 2
        reasons = []
        if not static_audit["ok"]:
            reasons.append(f"static contract blockers={static_audit['blocker_count']}")
        if not env_audit["ok"]:
            reasons.append(f"offline dependency gaps={[g['module'] for g in env_audit['offline_dependency_gaps']]}")
        stderr_text = "[runner] Execution blocked prior to launch: " + "; ".join(reasons)

    elapsed_s = round(time.monotonic() - t0, 3)
    post_snapshot = _snapshot_dir(work_dir)

    created_files: list[dict[str, Any]] = []
    modified_files: list[dict[str, Any]] = []
    unchanged_preexisting_files: list[dict[str, Any]] = []

    for rel, meta in post_snapshot.items():
        if rel not in pre_snapshot:
            created_files.append(meta)
        elif pre_snapshot[rel]["sha256"] != meta["sha256"]:
            modified_files.append({**meta, "previous_sha256": pre_snapshot[rel]["sha256"]})
        else:
            unchanged_preexisting_files.append(meta)

    fresh_artifacts = created_files + modified_files

    # Numeric & structural invariants on FRESH artifacts only (never count stale files as passing outputs!)
    invariant_violations: list[str] = []
    for item in fresh_artifacts:
        fpath = work_dir / item["rel"]
        if item["bytes"] == 0:
            invariant_violations.append(f"{item['rel']}: empty 0-byte artifact")
            continue
        suf = fpath.suffix.lower()
        if suf == ".json":
            try:
                raw_txt = fpath.read_text(encoding="utf-8-sig")
                parsed = json.loads(raw_txt)
                invariant_violations.extend(_scan_json_finite(parsed, item["rel"]))
            except Exception as exc:
                invariant_violations.append(f"{item['rel']}: invalid JSON ({exc})")
        elif suf == ".csv":
            invariant_violations.extend(_verify_csv_invariants(fpath, expected_rows=expected_rows))

    failure_class = _classify_failure(
        static_audit=static_audit,
        env_audit=env_audit,
        timed_out=timed_out,
        exit_code=exit_code,
        stderr_text=stderr_text,
        invariant_violations=invariant_violations,
        fresh_count=len(fresh_artifacts),
        min_artifacts=min_artifacts,
    )

    all_ok = (
        static_audit["ok"]
        and env_audit["ok"]
        and executed
        and not timed_out
        and exit_code == 0
        and len(fresh_artifacts) >= min_artifacts
        and len(invariant_violations) == 0
    )

    status = "ok" if all_ok else ("timeout" if timed_out else "stale_or_failed")

    # Anti-stale marker enforcement
    verified_marker = out_dir / f"{stem}_VERIFIED.ok"
    failed_marker = out_dir / f"{stem}_FAILED_MARKER.json"
    if all_ok:
        if failed_marker.exists():
            failed_marker.unlink()
        verified_marker.write_text(
            json.dumps({"status": "ok", "script": script_abs.name, "script_sha256": script_sha, "fresh_artifacts": len(fresh_artifacts)}) + "\n",
            encoding="utf-8",
        )
    else:
        if verified_marker.exists():
            verified_marker.unlink()
        failed_marker.write_text(
            json.dumps(
                {
                    "status": status,
                    "failure_classification": failure_class,
                    "script": script_abs.name,
                    "exit_code": exit_code,
                    "stale_preexisting_files": [f["rel"] for f in unchanged_preexisting_files],
                    "warning": "DO NOT treat stale_preexisting_files as valid outputs of this failed run.",
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    gates = [
        ("1. Static Contract", static_audit["ok"], f"blockers={static_audit['blocker_count']} warn={static_audit['warning_count']}"),
        ("2. Offline Env Lock", env_audit["ok"], f"gaps={len(env_audit['offline_dependency_gaps'])}"),
        ("3. Isolated Exit Code", executed and not timed_out and exit_code == 0, f"exit={exit_code} executed={executed}"),
        ("4. Fresh Artifacts", len(fresh_artifacts) >= min_artifacts, f"fresh={len(fresh_artifacts)} stale={len(unchanged_preexisting_files)}"),
        ("5. Numeric Invariants", len(invariant_violations) == 0, f"violations={len(invariant_violations)}"),
        ("6. Anti-Stale Guard", all_ok, "verified" if all_ok else "stale_or_failed_locked"),
    ]

    svg_p = out_dir / f"{stem}_gate_summary.svg"
    _render_stdlib_svg(svg_p, script_name=script_abs.name, status=status, elapsed_s=elapsed_s, seed=seed, gates=gates)

    png_p = out_dir / f"{stem}_gate_summary.png"
    png_ok = _try_render_matplotlib_png(png_p, script_name=script_abs.name, elapsed_s=elapsed_s, gates=gates)

    rep = {
        "ok": all_ok,
        "status": status,
        "failure_classification": failure_class,
        "script": script_abs.name,
        "script_sha256": script_sha,
        "command": [Path(cmd[0]).name] + cmd[1:],
        "seed": seed,
        "timeout_s": timeout_s,
        "environment_lock": {
            "python": env_audit["runtime"]["python_version"],
            "sqlite": env_audit["runtime"]["sqlite_version"],
            "fingerprint_sha256": env_audit["environment_fingerprint_sha256"],
            "available_scientific_packages": env_audit["capability_summary"]["available_scientific_packages"],
            "offline_dependency_gaps": env_audit["offline_dependency_gaps"],
        },
        "execution": {
            "executed": executed,
            "timed_out": timed_out,
            "exit_code": exit_code,
            "elapsed_s": elapsed_s,
            "stdout_tail": stdout_text[-2000:],
            "stderr_tail": stderr_text[-2000:],
        },
        "static_audit": static_audit,
        "snapshot_audit": {
            "pre_run_file_count": len(pre_snapshot),
            "post_run_file_count": len(post_snapshot),
            "created_files": created_files,
            "modified_files": modified_files,
            "fresh_artifacts": fresh_artifacts,
            "unchanged_preexisting_files": unchanged_preexisting_files,
            "stale_preexisting_files_flagged": [] if all_ok else [f["rel"] for f in unchanged_preexisting_files],
        },
        "invariant_violations": invariant_violations,
        "visual_artifacts": {
            "svg": svg_p.name,
            "matplotlib_png_generated": png_ok,
            "png": png_p.name if png_ok else None,
        },
        "markers": {
            "verified_marker": verified_marker.name if all_ok else None,
            "failed_marker": None if all_ok else failed_marker.name,
        },
    }

    rep_p = out_dir / f"{stem}_report.json"
    md_p = out_dir / f"{stem}_report.md"
    rep_p.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md_lines = [
        f"# Isolated Code Verification Report (`{script_abs.name}`)",
        "",
        f"- **Status**: `{status}` | **OK**: `{all_ok}` | **Failure Class**: `{failure_class}`",
        f"- **Exit Code**: `{exit_code}` | **Elapsed**: `{elapsed_s}s` | **Seed**: `{seed}`",
        f"- **Script SHA-256**: `{script_sha[:16]}...` | **Env Fingerprint**: `{env_audit['environment_fingerprint_sha256'][:16]}...`",
        f"- **Fresh Artifacts**: `{len(fresh_artifacts)}` | **Stale Pre-existing Files**: `{len(unchanged_preexisting_files)}` | **Invariant Violations**: `{len(invariant_violations)}`",
        f"- **Visuals**: `{svg_p.name}` (Matplotlib PNG: `{png_p.name if png_ok else 'not_installed'}`)",
        "",
        "## Gate Summary",
        "",
        "| Gate | Result | Detail |",
        "|---|---|---|",
    ]
    for label, ok_flag, detail in gates:
        md_lines.append(f"| {label} | `{'PASS' if ok_flag else 'FAIL'}` | {detail} |")
    if invariant_violations:
        md_lines.extend(["", "## Invariant Violations", ""])
        for v in invariant_violations:
            md_lines.append(f"- {v}")
    if not all_ok and unchanged_preexisting_files:
        md_lines.extend(["", "## Stale Pre-existing Files Warning", ""])
        md_lines.append("The following pre-existing files in `work_dir` were NOT updated by this failed run and MUST NOT be treated as valid outputs:")
        for item in unchanged_preexisting_files:
            md_lines.append(f"- `{item['rel']}` (`sha256={item['sha256'][:12]}...`)")
    md_p.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return rep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute and verify scientific Python script in isolated workspace.")
    parser.add_argument("--script", required=True, help="Target Python script to verify and execute.")
    parser.add_argument("--work-dir", required=True, help="Isolated working directory for script execution.")
    parser.add_argument("--out-dir", required=True, help="Output directory for verification reports and visuals.")
    parser.add_argument("--stem", default="exec_verification", help="Filename prefix for generated verification reports.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic PYTHONHASHSEED value.")
    parser.add_argument("--timeout", type=int, default=120, help="Subprocess timeout in seconds.")
    parser.add_argument("--input-csv", default=None, help="Optional input CSV for leading-zero contract check.")
    parser.add_argument("--expected-rows", type=int, default=None, help="Optional expected row count for generated CSV outputs.")
    parser.add_argument("--min-artifacts", type=int, default=1, help="Minimum number of fresh artifacts expected.")
    parser.add_argument("--strict-static", action="store_true", help="Treat static warnings as blockers.")
    parser.add_argument("script_args", nargs="*", help="Optional extra arguments passed to the target script after '--'.")
    args = parser.parse_args(argv)

    rep = run_isolated(
        Path(args.script),
        Path(args.work_dir),
        Path(args.out_dir),
        stem=args.stem,
        seed=args.seed,
        timeout_s=args.timeout,
        input_csv=Path(args.input_csv) if args.input_csv else None,
        expected_rows=args.expected_rows,
        min_artifacts=args.min_artifacts,
        script_args=args.script_args,
        allow_static_warnings=not args.strict_static,
    )
    print(
        json.dumps(
            {
                "ok": rep["ok"],
                "status": rep["status"],
                "failure_classification": rep["failure_classification"],
                "exit_code": rep["execution"]["exit_code"],
                "fresh_artifacts": len(rep["snapshot_audit"]["fresh_artifacts"]),
                "stale_preexisting_files": len(rep["snapshot_audit"]["stale_preexisting_files_flagged"]),
                "invariant_violations": len(rep["invariant_violations"]),
                "svg": rep["visual_artifacts"]["svg"],
                "png": rep["visual_artifacts"]["png"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
