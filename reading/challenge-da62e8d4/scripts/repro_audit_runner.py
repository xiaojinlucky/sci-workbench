"""Reproducibility Audit, Lockfile Drift Doctor, Deep AST Parameter Reconciliation & Isolated Multi-Run Runner (challenge-da62e8d4).

Pure Python 3.12 standard library baseline (zero mandatory third-party packages).
Produces:
- /app/output/result.json
- /app/output/reproduction_audit_report.json
- /app/output/reproduction_report.md

Key capabilities:
1. Integrates env_lock_doctor.py (Conda #15581/PR #15838 mixed-source drift, unpinned deps,
   declared-vs-installed runtime drift, research-repo-doctor executable checklist).
2. Deep AST parameter & seed hygiene inspection: top-level constants (including negative & annotated),
   function defaults, argparse.add_argument defaults, config dicts, dataclass defaults, and RNG seed calls.
   Produces 4-state reconciliation: matched / paper_code_conflict / paper_only_missing_in_code / code_only_undocumented.
3. Isolated multi-run execution with PYTHONHASHSEED injection, command/exit/timing/file SHA-256 capture,
   repeat-run determinism/jitter verification, and strict exclusion of failed runs from tolerance counts.
4. A priori tolerance verification (no post-hoc relaxation) with evidence-backed 5-axis discrepancy attribution
   and an explicit 4-tier evidence ladder (code_present -> clean_exit -> within_prior_tolerance -> scientific_claim_supported).
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import budget_guard
import env_lock_doctor
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log, sha256_file

DISCREPANCY_AXES = (
    "environment_or_seed",
    "parameter_mismatch",
    "data_or_split_difference",
    "implementation_difference",
    "paper_underspecification",
)

STOCHASTIC_CALL_NAMES = {
    "random.random", "random.uniform", "random.gauss", "random.normalvariate",
    "random.randint", "random.randrange", "random.choice", "random.choices",
    "random.shuffle", "random.sample",
    "np.random.rand", "np.random.randn", "np.random.normal", "np.random.uniform",
    "np.random.randint", "np.random.choice", "numpy.random.rand", "numpy.random.randn",
    "numpy.random.normal", "torch.rand", "torch.randn", "torch.randint",
}

SEED_SETTER_NAMES = {
    "random.seed", "np.random.seed", "numpy.random.seed", "np.random.default_rng",
    "numpy.random.default_rng", "default_rng", "torch.manual_seed",
    "torch.cuda.manual_seed", "torch.cuda.manual_seed_all", "tf.random.set_seed",
}

JSON_LINE_RE = re.compile(r"\{.*\}")
IGNORE_CODE_ONLY_KEYS = {"__name__", "__version__", "version", "help", "description", "out", "output", "input", "path", "file"}


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def _extract_literal(node: ast.AST) -> tuple[bool, Any]:
    """Safely extract scalar or small literal values from an AST expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool, type(None))):
        return True, node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        ok, inner = _extract_literal(node.operand)
        if ok and isinstance(inner, (int, float)) and not isinstance(inner, bool):
            return True, (-inner if isinstance(node.op, ast.USub) else +inner)
    if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) <= 12:
        items = []
        for el in node.elts:
            ok, v = _extract_literal(el)
            if not ok:
                return False, None
            items.append(v)
        return True, items
    return False, None


def _dotted_call_name(func: ast.AST) -> str:
    parts: list[str] = []
    cur = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def capture_env_snapshot() -> dict[str, Any]:
    pkgs: dict[str, str] = {}
    for pkg in ("numpy", "pandas", "scipy", "scikit-learn", "torch", "matplotlib", "sympy"):
        try:
            pkgs[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            pass
    return {
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "cpu_count": os.cpu_count() or 1,
        "pythonhashseed": os.environ.get("PYTHONHASHSEED", "unset"),
        "packages": pkgs,
    }


def extract_code_parameters(py_path: Path, relpath: str) -> dict[str, Any]:
    """Deep AST inspection of constants, function defaults, argparse defaults, config dicts, dataclasses, and seed hygiene."""
    src = py_path.read_text(encoding="utf-8", errors="replace")
    extracted: list[dict[str, Any]] = []
    seed_calls: list[dict[str, Any]] = []
    stochastic_calls: list[dict[str, Any]] = []
    hashseed_referenced = "PYTHONHASHSEED" in src

    try:
        tree = ast.parse(src, filename=py_path.name)
    except SyntaxError as exc:
        return {
            "file": relpath,
            "sha256": sha256_file(py_path),
            "bytes": py_path.stat().st_size,
            "syntax_error": f"line {exc.lineno}: {exc.msg}",
            "extracted_parameters": [],
            "seed_hygiene": {
                "status": "syntax_error",
                "seed_calls": [],
                "stochastic_calls": [],
                "pythonhashseed_referenced": hashseed_referenced,
            },
        }

    def _record_param(name: str, val: Any, kind: str, qualified: str, line: int) -> None:
        if name.startswith("_") or val is None:
            return
        extracted.append({
            "name": name,
            "norm_name": _norm_key(name),
            "value": val,
            "source_kind": kind,
            "qualified_name": qualified,
            "line": line,
            "file": relpath,
        })

    # 1. Top-level assignments, annotated assignments, config dicts, functions, dataclasses
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            tname = node.targets[0].id
            ok, val = _extract_literal(node.value)
            if ok:
                _record_param(tname, val, "top_level_const", tname, node.lineno)
            elif isinstance(node.value, ast.Dict):
                for k_node, v_node in zip(node.value.keys, node.value.values):
                    if isinstance(k_node, ast.Constant) and isinstance(k_node.value, str):
                        v_ok, v_val = _extract_literal(v_node)
                        if v_ok:
                            _record_param(k_node.value, v_val, "config_dict", f"{tname}[{k_node.value!r}]", k_node.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            ok, val = _extract_literal(node.value)
            if ok:
                _record_param(node.target.id, val, "top_level_annotated_const", node.target.id, node.lineno)
        elif isinstance(node, ast.FunctionDef):
            arg_names = [a.arg for a in node.args.args]
            def_vals = node.args.defaults
            if def_vals:
                offset = len(arg_names) - len(def_vals)
                for aname, dval in zip(arg_names[offset:], def_vals):
                    ok, val = _extract_literal(dval)
                    if ok:
                        _record_param(aname, val, "function_default", f"{node.name}({aname})", dval.lineno)
            for kw_arg, kw_def in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if kw_def is not None:
                    ok, val = _extract_literal(kw_def)
                    if ok:
                        _record_param(kw_arg.arg, val, "function_kw_default", f"{node.name}({kw_arg.arg})", kw_def.lineno)
        elif isinstance(node, ast.ClassDef):
            is_dc = any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                or (isinstance(d, ast.Call) and _dotted_call_name(d.func).endswith("dataclass"))
                for d in node.decorator_list
            )
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.value is not None:
                    ok, val = _extract_literal(item.value)
                    if ok:
                        _record_param(item.target.id, val, "dataclass_field" if is_dc else "class_attr", f"{node.name}.{item.target.id}", item.lineno)
                    elif isinstance(item.value, ast.Call) and _dotted_call_name(item.value.func).endswith("field"):
                        for kw in item.value.keywords:
                            if kw.arg == "default":
                                fok, fval = _extract_literal(kw.value)
                                if fok:
                                    _record_param(item.target.id, fval, "dataclass_field", f"{node.name}.{item.target.id}", item.lineno)

    # 2. Walk entire AST for argparse.add_argument, nested config dicts, seed setters, and stochastic RNG calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            cname = _dotted_call_name(node.func)
            # argparse add_argument
            if cname.endswith("add_argument"):
                opt_names = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                dest_name = None
                default_val_ok = False
                default_val = None
                for kw in node.keywords:
                    if kw.arg == "dest" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        dest_name = kw.value.value
                    elif kw.arg == "default":
                        default_val_ok, default_val = _extract_literal(kw.value)
                if default_val_ok:
                    chosen = dest_name
                    if not chosen:
                        long_opts = [o.lstrip("-") for o in opt_names if o.startswith("--")]
                        chosen = long_opts[0] if long_opts else (opt_names[0].lstrip("-") if opt_names else "")
                    if chosen:
                        _record_param(chosen.replace("-", "_"), default_val, "argparse_default", f"add_argument({chosen})", node.lineno)

            # Seed setters
            if cname in SEED_SETTER_NAMES or cname.endswith(".seed") or cname.endswith(".manual_seed") or cname.endswith("default_rng"):
                arg_preview = None
                is_none_or_dynamic = False
                if node.args:
                    ok, val = _extract_literal(node.args[0])
                    if ok:
                        arg_preview = val
                        if val is None:
                            is_none_or_dynamic = True
                    else:
                        arg_preview = ast.unparse(node.args[0]) if hasattr(ast, "unparse") else "<dynamic>"
                        if "time" in str(arg_preview).lower() or "urandom" in str(arg_preview).lower():
                            is_none_or_dynamic = True
                else:
                    is_none_or_dynamic = True
                seed_calls.append({
                    "call": cname,
                    "argument": arg_preview,
                    "line": node.lineno,
                    "effective_fixed_seed": not is_none_or_dynamic,
                })

            # Keyword seed= / random_state=
            for kw in node.keywords:
                if kw.arg in ("seed", "random_state"):
                    ok, val = _extract_literal(kw.value)
                    seed_calls.append({
                        "call": f"{cname}({kw.arg}=...)",
                        "argument": val if ok else "<expr>",
                        "line": node.lineno,
                        "effective_fixed_seed": (val is not None) if ok else True,
                    })

            # Stochastic RNG calls
            if cname in STOCHASTIC_CALL_NAMES:
                stochastic_calls.append({"call": cname, "line": node.lineno})

        # Config dicts inside functions (e.g. cfg = {"lr": 0.01, ...})
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            tname = node.targets[0].id
            if isinstance(node.value, ast.Dict) and any(tok in tname.lower() for tok in ("cfg", "config", "param", "hyper", "opt", "default", "setting")):
                if node not in tree.body:
                    for k_node, v_node in zip(node.value.keys, node.value.values):
                        if isinstance(k_node, ast.Constant) and isinstance(k_node.value, str):
                            v_ok, v_val = _extract_literal(v_node)
                            if v_ok:
                                _record_param(k_node.value, v_val, "local_config_dict", f"{tname}[{k_node.value!r}]", k_node.lineno)

    # Deduplicate extracted params by (name, qualified_name, line)
    dedup_params: list[dict[str, Any]] = []
    seen_sig: set[tuple[Any, ...]] = set()
    for ep in extracted:
        sig = (ep["norm_name"], ep["qualified_name"], ep["line"])
        if sig not in seen_sig:
            seen_sig.add(sig)
            dedup_params.append(ep)

    has_effective_seed = any(sc["effective_fixed_seed"] for sc in seed_calls)
    if stochastic_calls and not has_effective_seed:
        seed_status = "unseeded_stochastic_risk"
    elif has_effective_seed:
        seed_status = "seeded"
    else:
        seed_status = "deterministic_no_rng"

    return {
        "file": relpath,
        "sha256": sha256_file(py_path),
        "bytes": py_path.stat().st_size,
        "extracted_parameters": dedup_params,
        "seed_hygiene": {
            "status": seed_status,
            "seed_calls": seed_calls,
            "stochastic_calls": stochastic_calls,
            "pythonhashseed_referenced": hashseed_referenced,
        },
    }


def _values_equal(v1: Any, v2: Any) -> bool:
    if isinstance(v1, bool) or isinstance(v2, bool):
        return bool(v1) is bool(v2) and type(v1) is type(v2)
    if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
        return abs(float(v1) - float(v2)) <= 1e-12
    return str(v1).strip().lower() == str(v2).strip().lower()


def reconcile_parameters(paper_params: dict[str, Any], code_inspects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconcile paper hyperparameters against all inspected scripts into 4 explicit provenance statuses."""
    by_norm: dict[str, list[dict[str, Any]]] = {}
    for ci in code_inspects:
        for ep in ci.get("extracted_parameters") or []:
            by_norm.setdefault(ep["norm_name"], []).append(ep)

    table: list[dict[str, Any]] = []
    paper_norms: set[str] = set()

    for pkey, pval in paper_params.items():
        nk = _norm_key(pkey)
        paper_norms.add(nk)
        matches = by_norm.get(nk) or []
        if not matches:
            table.append({
                "parameter": pkey,
                "paper_value": pval,
                "code_value": None,
                "code_source": None,
                "source_kind": None,
                "provenance_status": "paper_only_missing_in_code",
            })
            continue
        # Record one row per candidate file where this parameter appears so conflicts in any candidate are never masked
        by_file: dict[str, list[dict[str, Any]]] = {}
        for m in matches:
            by_file.setdefault(m["file"], []).append(m)
        for fname, entries in by_file.items():
            # Prefer argparse/config_dict/dataclass over plain const if multiple
            priority = {"argparse_default": 0, "config_dict": 1, "local_config_dict": 2, "dataclass_field": 3, "function_default": 4, "function_kw_default": 5, "top_level_const": 6, "top_level_annotated_const": 7, "class_attr": 8}
            chosen = sorted(entries, key=lambda e: (priority.get(e["source_kind"], 9), e["line"]))[0]
            equal = _values_equal(pval, chosen["value"])
            table.append({
                "parameter": pkey,
                "paper_value": pval,
                "code_value": chosen["value"],
                "code_source": fname,
                "qualified_name": chosen["qualified_name"],
                "source_kind": chosen["source_kind"],
                "line": chosen["line"],
                "provenance_status": "matched" if equal else "paper_code_conflict",
            })

    # Record code_only_undocumented parameters
    seen_undoc: set[tuple[str, str]] = set()
    for ci in code_inspects:
        for ep in ci.get("extracted_parameters") or []:
            nk = ep["norm_name"]
            if nk in paper_norms or nk in IGNORE_CODE_ONLY_KEYS:
                continue
            # Filter out obvious non-hyperparameter uppercase string constants unless numeric/bool or from config/argparse/dataclass
            if ep["source_kind"] in ("top_level_const", "top_level_annotated_const") and isinstance(ep["value"], str):
                continue
            sig = (ci["file"], nk)
            if sig in seen_undoc:
                continue
            seen_undoc.add(sig)
            table.append({
                "parameter": ep["name"],
                "paper_value": None,
                "code_value": ep["value"],
                "code_source": ci["file"],
                "qualified_name": ep["qualified_name"],
                "source_kind": ep["source_kind"],
                "line": ep["line"],
                "provenance_status": "code_only_undocumented",
            })
    return table


def _parse_metrics_from_stdout(stdout: str) -> dict[str, Any]:
    parsed_metrics: dict[str, Any] = {}
    try:
        obj = json.loads(stdout.strip())
        if isinstance(obj, dict):
            parsed_metrics = obj
    except Exception:
        for line in reversed(stdout.splitlines()):
            m = JSON_LINE_RE.search(line)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    if isinstance(obj, dict):
                        parsed_metrics = obj
                        break
                except Exception:
                    pass
        if not parsed_metrics:
            for km in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", stdout):
                parsed_metrics[km.group(1)] = float(km.group(2))
    return parsed_metrics


def execute_candidate_once(
    script_path: Path,
    relpath: str,
    cwd: Path,
    *,
    args: list[str] | None = None,
    hashseed: str = "0",
    timeout_sec: int = 30,
    repeat_index: int = 1,
) -> dict[str, Any]:
    """Run candidate numerical script in an isolated directory with PYTHONHASHSEED injected."""
    cwd.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(hashseed)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, "-B", str(script_path), *(args or [])]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        elapsed = round(time.monotonic() - t0, 4)
        out_files = []
        for ofp in sorted(p for p in cwd.rglob("*") if p.is_file()):
            out_files.append({
                "rel": ofp.relative_to(cwd).as_posix(),
                "bytes": ofp.stat().st_size,
                "sha256": sha256_file(ofp),
            })
        parsed_metrics = _parse_metrics_from_stdout(proc.stdout) if proc.returncode == 0 else {}
        return {
            "script": relpath,
            "repeat_index": repeat_index,
            "command": [Path(cmd[0]).name, "-B", relpath, *(args or [])],
            "pythonhashseed": str(hashseed),
            "input_sha256": sha256_file(script_path),
            "returncode": proc.returncode,
            "elapsed_sec": elapsed,
            "status": "executed_ok" if proc.returncode == 0 else "execution_failed",
            "parsed_metrics": parsed_metrics,
            "output_files": out_files,
            "stdout_tail": proc.stdout[-800:],
            "stderr_tail": proc.stderr[-800:],
        }
    except subprocess.TimeoutExpired:
        return {
            "script": relpath,
            "repeat_index": repeat_index,
            "command": [Path(cmd[0]).name, "-B", relpath, *(args or [])],
            "pythonhashseed": str(hashseed),
            "input_sha256": sha256_file(script_path),
            "returncode": -1,
            "elapsed_sec": timeout_sec,
            "status": "timeout",
            "parsed_metrics": {},
            "output_files": [],
            "stdout_tail": "",
            "stderr_tail": f"Timed out after {timeout_sec}s",
        }


def execute_candidate_with_repeats(
    script_path: Path,
    relpath: str,
    work_dir: Path,
    *,
    args: list[str] | None = None,
    hashseed: str = "0",
    repeats: int = 2,
    timeout_sec: int = 30,
) -> dict[str, Any]:
    """Execute a candidate script across isolated repeat runs to measure determinism/jitter."""
    runs: list[dict[str, Any]] = []
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", relpath)
    for r_idx in range(1, max(1, repeats) + 1):
        rcwd = work_dir / f"run_{safe_stem}_r{r_idx}"
        res = execute_candidate_once(
            script_path,
            relpath,
            rcwd,
            args=args,
            hashseed=hashseed,
            timeout_sec=timeout_sec,
            repeat_index=r_idx,
        )
        runs.append(res)
        # Do not repeat a crashing script unnecessarily
        if res["status"] != "executed_ok":
            break

    ok_runs = [r for r in runs if r["status"] == "executed_ok"]
    jitter_metrics: dict[str, dict[str, Any]] = {}
    max_jitter = 0.0
    deterministic: bool | None = None

    if len(ok_runs) >= 2:
        all_keys = sorted({k for r in ok_runs for k, v in (r.get("parsed_metrics") or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
        for k in all_keys:
            vals = [float(r["parsed_metrics"][k]) for r in ok_runs if isinstance(r.get("parsed_metrics", {}).get(k), (int, float)) and not isinstance(r.get("parsed_metrics", {}).get(k), bool)]
            if len(vals) >= 2:
                spread = max(vals) - min(vals)
                if spread > 1e-12:
                    jitter_metrics[k] = {"min": round(min(vals), 8), "max": round(max(vals), 8), "spread": round(spread, 8), "values": [round(v, 8) for v in vals]}
                    max_jitter = max(max_jitter, spread)
        deterministic = len(jitter_metrics) == 0

    primary = runs[0]
    return {
        "script": relpath,
        "status": "executed_ok" if (len(ok_runs) == len(runs) and len(runs) > 0) else primary["status"],
        "repeats_requested": repeats,
        "repeats_completed_ok": len(ok_runs),
        "returncode": primary["returncode"],
        "elapsed_sec": primary["elapsed_sec"],
        "command": primary["command"],
        "pythonhashseed": primary["pythonhashseed"],
        "input_sha256": primary["input_sha256"],
        "parsed_metrics": primary["parsed_metrics"] if primary["status"] == "executed_ok" else {},
        "deterministic": deterministic,
        "max_run_jitter": round(max_jitter, 8) if deterministic is not None else None,
        "jitter_metrics": jitter_metrics,
        "repeat_runs": runs,
        "stdout_tail": primary["stdout_tail"],
        "stderr_tail": primary["stderr_tail"],
    }


def _attribute_discrepancy(
    *,
    candidate_file: str,
    metric_name: str,
    status: str,
    run_summary: dict[str, Any],
    code_inspect_map: dict[str, dict[str, Any]],
    param_reconciliation: list[dict[str, Any]],
    env_audit: dict[str, Any],
    missing_materials: list[str],
) -> dict[str, Any]:
    """Evidence-backed 5-axis discrepancy attribution."""
    axes: list[str] = []
    evidence: list[str] = []

    # 1. Parameter mismatch in this specific script
    script_conflicts = [
        p for p in param_reconciliation
        if p.get("code_source") == candidate_file and p.get("provenance_status") == "paper_code_conflict"
    ]
    if script_conflicts:
        axes.append("parameter_mismatch")
        for sc in script_conflicts:
            evidence.append(
                f"parameter_mismatch: {sc['parameter']} paper={sc['paper_value']} vs {candidate_file}:{sc.get('source_kind')}={sc['code_value']} (line {sc.get('line')})."
            )

    # 2. Environment or seed (observed jitter, unseeded stochastic RNG, or runtime package drift)
    ci = code_inspect_map.get(candidate_file) or {}
    seed_status = (ci.get("seed_hygiene") or {}).get("status")
    if run_summary.get("deterministic") is False:
        axes.append("environment_or_seed")
        jm = (run_summary.get("jitter_metrics") or {}).get(metric_name)
        evidence.append(
            f"environment_or_seed: non-deterministic repeat execution detected on {candidate_file} "
            f"(max_run_jitter={run_summary.get('max_run_jitter')}, metric_jitter={jm})."
        )
    elif seed_status == "unseeded_stochastic_risk":
        axes.append("environment_or_seed")
        evidence.append(f"environment_or_seed: {candidate_file} uses stochastic RNG calls without a fixed seed.")
    if (env_audit.get("summary") or {}).get("version_drift_count", 0) > 0 or (env_audit.get("summary") or {}).get("conda_pip_mixed"):
        if "environment_or_seed" not in axes:
            axes.append("environment_or_seed")
        evidence.append(
            f"environment_or_seed: lockfile/env drift signals detected (conda_pip_mixed={env_audit['summary']['conda_pip_mixed']}, "
            f"unpinned={env_audit['summary']['unpinned_dependency_count']}, version_drift={env_audit['summary']['version_drift_count']})."
        )

    # 3. Data or split difference (missing original dataset/PMLB)
    if missing_materials:
        axes.append("data_or_split_difference")
        evidence.append(f"data_or_split_difference: original material(s) absent: {missing_materials[0]}")

    # 4. Paper underspecification (paper params missing in code or undocumented code hyperparams)
    paper_missing = [p["parameter"] for p in param_reconciliation if p["provenance_status"] == "paper_only_missing_in_code"]
    code_undoc = [p["parameter"] for p in param_reconciliation if p.get("code_source") == candidate_file and p["provenance_status"] == "code_only_undocumented"]
    if paper_missing or code_undoc:
        axes.append("paper_underspecification")
        if paper_missing:
            evidence.append(f"paper_underspecification: paper parameter(s) missing in code: {paper_missing[:5]}.")
        if code_undoc:
            evidence.append(f"paper_underspecification: undocumented code parameter(s) in {candidate_file}: {code_undoc[:5]}.")

    # 5. Implementation difference (always primary on failed run / omitted metric, or fallback)
    if status in ("execution_not_completed", "metric_not_emitted") or not axes:
        axes.insert(0, "implementation_difference")
        if status == "execution_not_completed":
            evidence.insert(0, f"implementation_difference: {candidate_file} exited with rc={run_summary.get('returncode')} ({run_summary.get('status')}).")
        elif status == "metric_not_emitted":
            evidence.insert(0, f"implementation_difference: {candidate_file} exited 0 but did not emit metric '{metric_name}'.")
        else:
            evidence.append(f"implementation_difference: residual deviation on '{metric_name}' not accounted for by parameter/seed/data signals alone.")

    # Choose strongest direct primary axis
    if status in ("execution_not_completed", "metric_not_emitted"):
        primary = "implementation_difference"
    elif script_conflicts:
        primary = "parameter_mismatch"
    elif run_summary.get("deterministic") is False or seed_status == "unseeded_stochastic_risk":
        primary = "environment_or_seed"
    elif missing_materials:
        primary = "data_or_split_difference"
    else:
        primary = axes[0]

    return {
        "primary_attribution_axis": primary,
        "supporting_axes": [a for a in axes if a != primary],
        "attribution_evidence": evidence,
    }


def compare_against_tolerance(
    run_summaries: list[dict[str, Any]],
    paper_targets: dict[str, Any],
    tolerances: dict[str, Any],
    *,
    code_inspect_map: dict[str, dict[str, Any]],
    param_reconciliation: list[dict[str, Any]],
    env_audit: dict[str, Any],
    missing_materials: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare ONLY clean-exit runs against a priori tolerances; segregate failed runs separately."""
    comparisons: list[dict[str, Any]] = []
    excluded_failed: list[dict[str, Any]] = []

    default_abs = float(tolerances.get("default_abs_tol", 1e-4))
    default_rel = float(tolerances.get("default_rel_tol", 0.05))

    for r in run_summaries:
        if r["status"] != "executed_ok":
            excluded_failed.append({
                "candidate": r["script"],
                "run_status": r["status"],
                "returncode": r["returncode"],
                "stderr_tail": r["stderr_tail"],
                "reason": "Excluded from tolerance statistics because candidate execution did not exit cleanly.",
            })

    ok_runs = [r for r in run_summaries if r["status"] == "executed_ok"]

    for metric_name, target_raw in paper_targets.items():
        if target_raw is None:
            comparisons.append({
                "metric": metric_name,
                "candidate": None,
                "target": None,
                "observed": None,
                "status": "missing_paper_baseline",
                "within_tolerance": None,
                "reason": "Paper target metric not reported; retained as missing without inventing a baseline.",
            })
            continue

        target_val = float(target_raw)
        tol_cfg = tolerances.get(metric_name) if isinstance(tolerances.get(metric_name), dict) else {}
        abs_tol = float(tol_cfg.get("abs_tol", default_abs))
        rel_tol = float(tol_cfg.get("rel_tol", default_rel))

        for r in ok_runs:
            obs = (r.get("parsed_metrics") or {}).get(metric_name)
            if not isinstance(obs, (int, float)) or isinstance(obs, bool):
                attr = _attribute_discrepancy(
                    candidate_file=r["script"],
                    metric_name=metric_name,
                    status="metric_not_emitted",
                    run_summary=r,
                    code_inspect_map=code_inspect_map,
                    param_reconciliation=param_reconciliation,
                    env_audit=env_audit,
                    missing_materials=missing_materials,
                )
                comparisons.append({
                    "metric": metric_name,
                    "candidate": r["script"],
                    "target": target_val,
                    "observed": None,
                    "abs_tol": abs_tol,
                    "rel_tol": rel_tol,
                    "within_tolerance": None,
                    "status": "metric_not_emitted",
                    "attribution_category": attr["primary_attribution_axis"],
                    **attr,
                })
                continue

            obs_f = float(obs)
            abs_diff = abs(obs_f - target_val)
            rel_diff = abs_diff / max(1e-12, abs(target_val))
            ok = (abs_diff <= abs_tol) or (rel_diff <= rel_tol)
            entry: dict[str, Any] = {
                "metric": metric_name,
                "candidate": r["script"],
                "target": target_val,
                "observed": round(obs_f, 8),
                "abs_diff": round(abs_diff, 8),
                "rel_diff": round(rel_diff, 8),
                "abs_tol": abs_tol,
                "rel_tol": rel_tol,
                "within_tolerance": ok,
                "deterministic": r.get("deterministic"),
                "status": "within_tolerance" if ok else "out_of_tolerance",
            }
            if not ok:
                attr = _attribute_discrepancy(
                    candidate_file=r["script"],
                    metric_name=metric_name,
                    status="out_of_tolerance",
                    run_summary=r,
                    code_inspect_map=code_inspect_map,
                    param_reconciliation=param_reconciliation,
                    env_audit=env_audit,
                    missing_materials=missing_materials,
                )
                entry["attribution_category"] = attr["primary_attribution_axis"]
                entry.update(attr)
            else:
                entry["attribution_category"] = None
            comparisons.append(entry)

    return comparisons, excluded_failed


def render_reproduction_report_md(report: dict[str, Any], gaps: list[dict[str, Any]]) -> str:
    """Render concise, domain-facing Markdown reproduction audit report."""
    ladder = report["evidence_ladder"]
    env_doc = report["environment_lockfile_audit"]
    env_sum = env_doc["summary"]
    lines: list[str] = [
        f"# Paper Reproduction & Method Verification Report (`{report['case_id']}`)",
        "",
        f"- **Reproducibility Grade**: `{report['reproducibility_grade']}`",
        f"- **Scope Boundary**: {report['scope_disclaimer']}",
        "",
        "## 1. Four-Tier Evidence Ladder",
        "",
        "| Tier | Question | Status |",
        "| --- | --- | --- |",
        f"| Tier 1: Code Present | Candidate numerical scripts present and syntactically valid? | `{ladder['tier_1_code_present']}` |",
        f"| Tier 2: Clean Exit | All candidate runs exited with return code 0? | `{ladder['tier_2_clean_exit']}` |",
        f"| Tier 3: Within A Priori Tolerance | Clean-exit metrics within declared tolerances and deterministic across repeats? | `{ladder['tier_3_within_prior_tolerance']}` |",
        f"| Tier 4: Scientific Claim Supported | Full original dataset/repository verified beyond synthetic trial? | `{ladder['tier_4_scientific_claim_supported']}` |",
        "",
        "## 2. Environment & Lockfile Drift Audit (`env_lock_doctor`)",
        "",
        f"- Python: `{report['environment_snapshot']['python_version']}` (`{report['environment_snapshot']['platform']}`), `PYTHONHASHSEED={report['environment_snapshot']['pythonhashseed']}`",
        f"- Declared dependencies: `{env_sum['declared_dependency_count']}` | Unpinned: `{env_sum['unpinned_dependency_count']}` | Conda+Pip mixed: `{env_sum['conda_pip_mixed']}` | Strict lockfile present: `{env_sum['strict_lockfile_present']}` | Runtime version drifts: `{env_sum['version_drift_count']}`",
        f"- Research-Repo-Doctor engineering readiness ratio: `{env_doc['research_repo_doctor_checklist']['engineering_readiness_ratio']}` (not scientific reproduction)",
        "",
    ]

    actions = env_doc["research_repo_doctor_checklist"].get("manual_action_items") or []
    if actions:
        lines.append("### Manual Remediation Actions")
        lines.append("")
        for act in actions:
            lines.append(f"- **[{act['severity'].upper()}] `{act['code']}`**: {act['action']}")
        lines.append("")

    # Parameter Reconciliation
    lines.append("## 3. Hyperparameter Reconciliation & Seed Hygiene")
    lines.append("")
    params = report.get("parameter_reconciliation") or []
    if not params:
        lines.append("No paper or code parameters extracted.")
        lines.append("")
    else:
        lines.append("| Parameter | Paper Value | Code Value | Code Source | Source Kind | Status |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for p in params:
            lines.append(
                f"| `{p['parameter']}` | `{p.get('paper_value')}` | `{p.get('code_value')}` | "
                f"`{p.get('code_source') or 'none'}` | `{p.get('source_kind') or 'none'}` | `{p['provenance_status']}` |"
            )
        lines.append("")

    # Execution & Determinism
    lines.append("## 4. Isolated Execution & Repeat Determinism")
    lines.append("")
    runs = report.get("execution_runs") or []
    if runs:
        lines.append("| Candidate Script | Status | RC | Repeats OK | Deterministic | Max Jitter | Seed Hygiene |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        ci_map = {c["file"]: c for c in (report.get("candidate_scripts") or [])}
        for r in runs:
            sh = ((ci_map.get(r["script"]) or {}).get("seed_hygiene") or {}).get("status", "unknown")
            lines.append(
                f"| `{r['script']}` | `{r['status']}` | `{r['returncode']}` | "
                f"`{r['repeats_completed_ok']}/{r['repeats_requested']}` | `{r['deterministic']}` | "
                f"`{r['max_run_jitter']}` | `{sh}` |"
            )
        lines.append("")

    excluded = report.get("failed_runs_excluded") or []
    if excluded:
        lines.append("### Failed Runs Excluded from Tolerance Statistics")
        lines.append("")
        for ex in excluded:
            lines.append(f"- `{ex['candidate']}`: status=`{ex['run_status']}`, rc=`{ex['returncode']}` — {ex['reason']}")
        lines.append("")

    # Tolerance & 5-Axis Attribution
    lines.append("## 5. A Priori Tolerance Comparison & Five-Axis Attribution")
    lines.append("")
    comps = report.get("tolerance_comparisons") or []
    if comps:
        lines.append("| Candidate | Metric | Target | Observed | Abs Diff | Rel Diff | Status | Primary Attribution |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for c in comps:
            lines.append(
                f"| `{c.get('candidate') or 'none'}` | `{c['metric']}` | `{c.get('target')}` | `{c.get('observed')}` | "
                f"`{c.get('abs_diff')}` | `{c.get('rel_diff')}` | `{c['status']}` | `{c.get('attribution_category') or 'none'}` |"
            )
        lines.append("")
        for c in comps:
            if c.get("attribution_evidence"):
                lines.append(f"- Attribution evidence for `{c.get('candidate')}` / `{c['metric']}` (`{c.get('primary_attribution_axis')}`):")
                for ev in c["attribution_evidence"]:
                    lines.append(f"  - {ev}")
        lines.append("")

    # Missing original materials
    missing = report.get("missing_original_materials") or []
    if missing:
        lines.append("## 6. Missing Original Materials & Boundaries")
        lines.append("")
        for mm in missing:
            lines.append(f"- {mm}")
        lines.append("")

    if gaps:
        lines.append("## 7. Recorded Gaps")
        lines.append("")
        for g in gaps:
            lines.append(f"- `{g.get('code')}` ({g.get('severity', 'warning')}): {g.get('message')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path, *, repeats: int = 2) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="da62e8d4-repro-verifier")

    case_path = input_dir / "case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace")) if case_path.is_file() else {}
    case_id = str(case_data.get("case_id") or "case-unknown")

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-da62e8d4")

    # 1. Environment snapshot + lockfile drift doctor
    budget_guard.tick_phase(budget_file, "env_lockfile_audit")
    env_snapshot = capture_env_snapshot()
    env_audit = env_lock_doctor.audit_environment_and_locks(input_dir)
    result_guard.atomic_write_json(work_dir / "env_doctor.json", env_audit)

    # 2. Discover candidate Python scripts & perform deep AST inspection
    candidate_scripts = sorted(
        p for p in input_dir.rglob("*.py")
        if p.is_file() and not p.name.startswith("test_")
    )
    code_inspects = [extract_code_parameters(sp, sp.relative_to(input_dir).as_posix()) for sp in candidate_scripts]
    code_inspect_map = {ci["file"]: ci for ci in code_inspects}

    paper_params = case_data.get("paper_parameters") or case_data.get("hyperparameters") or {}
    param_reconciliation = reconcile_parameters(paper_params, code_inspects)

    # 3. Isolated multi-run execution
    budget_guard.tick_phase(budget_file, "run_candidate_methods")
    effective_repeats = int(case_data.get("repeats") or repeats)
    hashseed = str(case_data.get("pythonhashseed", "0"))
    run_args = [str(a) for a in (case_data.get("runner_args") or [])]
    timeout_sec = int(case_data.get("timeout_sec") or 30)

    run_summaries: list[dict[str, Any]] = []
    for sp in candidate_scripts:
        rel = sp.relative_to(input_dir).as_posix()
        run_summaries.append(
            execute_candidate_with_repeats(
                sp,
                rel,
                work_dir,
                args=run_args,
                hashseed=hashseed,
                repeats=effective_repeats,
                timeout_sec=timeout_sec,
            )
        )

    # 4. Missing original materials & tolerance comparisons
    missing_materials = [str(x) for x in (case_data.get("missing_materials") or [])]
    if not candidate_scripts:
        missing_materials.append("No candidate Python method scripts supplied in /input.")
    paper_targets = case_data.get("paper_targets") or case_data.get("expected_metrics") or {}
    if not paper_targets:
        missing_materials.append("No quantitative paper_targets supplied for a priori tolerance comparison.")
    tolerances = case_data.get("tolerances") or {}

    comparisons, excluded_failed = compare_against_tolerance(
        run_summaries,
        paper_targets,
        tolerances,
        code_inspect_map=code_inspect_map,
        param_reconciliation=param_reconciliation,
        env_audit=env_audit,
        missing_materials=missing_materials,
    )

    # 5. Four-tier evidence ladder & honest reproducibility grade
    failed_runs = [r for r in run_summaries if r["status"] != "executed_ok"]
    jitter_runs = [r for r in run_summaries if r.get("deterministic") is False]
    out_of_tol = [c for c in comparisons if c.get("status") == "out_of_tolerance"]
    within_tol = [c for c in comparisons if c.get("within_tolerance") is True]
    missing_comparisons = [c for c in comparisons if c.get("status") in {"metric_not_emitted", "missing_paper_baseline"}]
    param_conflicts = [p for p in param_reconciliation if p["provenance_status"] == "paper_code_conflict"]
    code_only_undoc = [p for p in param_reconciliation if p["provenance_status"] == "code_only_undocumented"]
    paper_only_missing = [p for p in param_reconciliation if p["provenance_status"] == "paper_only_missing_in_code"]
    unseeded_scripts = [ci["file"] for ci in code_inspects if (ci.get("seed_hygiene") or {}).get("status") == "unseeded_stochastic_risk"]

    tier_1 = bool(candidate_scripts) and not any("syntax_error" in ci for ci in code_inspects)
    tier_2 = bool(run_summaries) and len(failed_runs) == 0
    tier_3 = tier_2 and bool(within_tol) and len(out_of_tol) == 0 and len(missing_comparisons) == 0 and len(jitter_runs) == 0
    tier_4 = "established_within_supplied_scope" if (tier_3 and not missing_materials and not param_conflicts) else "not_established"

    evidence_ladder = {
        "tier_1_code_present": tier_1,
        "tier_2_clean_exit": tier_2,
        "tier_3_within_prior_tolerance": tier_3,
        "tier_4_scientific_claim_supported": tier_4,
    }

    if not run_summaries or len(failed_runs) == len(run_summaries):
        repro_grade = "not_reproducible_or_missing_code"
    elif failed_runs or out_of_tol or missing_comparisons or missing_materials or jitter_runs or param_conflicts:
        repro_grade = "partially_reproducible"
    else:
        repro_grade = "reproducible_within_tolerance"

    gaps: list[dict[str, Any]] = []
    for mm in missing_materials:
        gaps.append({"code": "MISSING_ORIGINAL_MATERIAL", "severity": "info", "source": "repro_audit_runner", "message": mm})
    if env_audit["summary"]["conda_pip_mixed"] and not env_audit["summary"]["strict_lockfile_present"]:
        gaps.append({
            "code": "CONDA_PIP_MIXED_LOCKFILE_DRIFT",
            "severity": "warning",
            "source": "env_lock_doctor",
            "message": "Detected mixed Conda+pip environment specification without strict lockfile (Conda #15581/PR #15838 drift risk).",
        })
    if env_audit["summary"]["unpinned_dependency_count"] > 0:
        gaps.append({
            "code": "UNPINNED_DEPENDENCIES_DETECTED",
            "severity": "warning",
            "source": "env_lock_doctor",
            "message": f"{env_audit['summary']['unpinned_dependency_count']} declared dependency specifier(s) are not exact-pinned.",
        })
    if env_audit["summary"]["version_drift_count"] > 0:
        gaps.append({
            "code": "RUNTIME_VERSION_DRIFT",
            "severity": "warning",
            "source": "env_lock_doctor",
            "message": f"{env_audit['summary']['version_drift_count']} package(s) differ between manifest pin and installed runtime version.",
        })
    if param_conflicts:
        gaps.append({
            "code": "HYPERPARAMETER_CONFLICT_DETECTED",
            "severity": "warning",
            "source": "reconcile_parameters",
            "message": f"{len(param_conflicts)} hyperparameter conflict(s) between paper and candidate code.",
        })
    if unseeded_scripts:
        gaps.append({
            "code": "UNSEEDED_STOCHASTIC_CODE",
            "severity": "warning",
            "source": "extract_code_parameters",
            "message": f"Stochastic RNG usage without fixed seed detected in: {unseeded_scripts}.",
        })
    if jitter_runs:
        gaps.append({
            "code": "STOCHASTIC_RUN_JITTER",
            "severity": "warning",
            "source": "execute_candidate_with_repeats",
            "message": f"{len(jitter_runs)} candidate script(s) produced non-deterministic numeric metrics across identical repeat runs.",
        })
    if failed_runs:
        gaps.append({
            "code": "CANDIDATE_EXECUTION_FAILED",
            "severity": "warning",
            "source": "execute_candidate_with_repeats",
            "message": f"{len(failed_runs)} candidate script(s) failed execution and were excluded from tolerance statistics.",
        })
    if out_of_tol:
        gaps.append({
            "code": "METRIC_OUT_OF_TOLERANCE",
            "severity": "warning",
            "source": "compare_against_tolerance",
            "message": f"{len(out_of_tol)} clean-exit metric comparison(s) exceeded a priori tolerance.",
        })
    if missing_comparisons:
        gaps.append({
            "code": "METRIC_COMPARISON_INCOMPLETE",
            "severity": "warning",
            "source": "compare_against_tolerance",
            "message": f"{len(missing_comparisons)} comparison(s) lacked a numeric observation or paper baseline.",
        })

    report: dict[str, Any] = {
        "case_id": case_id,
        "reproducibility_grade": repro_grade,
        "evidence_ladder": evidence_ladder,
        "environment_snapshot": env_snapshot,
        "environment_lockfile_audit": env_audit,
        "candidate_scripts": code_inspects,
        "parameter_reconciliation": param_reconciliation,
        "execution_runs": run_summaries,
        "failed_runs_excluded": excluded_failed,
        "tolerance_comparisons": comparisons,
        "missing_original_materials": missing_materials,
        "scope_disclaimer": (
            "Verification covers supplied candidate scripts, runtime manifests, and a priori tolerances only; "
            "synthetic trials do not substitute for unavailable original author code, physical experiments, or full external PMLB datasets."
        ),
    }

    report_path = output_dir / "reproduction_audit_report.json"
    result_guard.atomic_write_json(report_path, report)

    md_text = render_reproduction_report_md(report, gaps)
    md_path = output_dir / "reproduction_report.md"
    result_guard.atomic_write_text(md_path, md_text)

    append_audit_entry(
        audit_file,
        phase="reproduce",
        action="repro_audit_completed",
        actor="repro_audit_runner.py",
        output={
            "grade": repro_grade,
            "evidence_ladder": evidence_ladder,
            "scripts_run": len(run_summaries),
            "failed_excluded": len(excluded_failed),
            "jitter_runs": len(jitter_runs),
            "comparisons": len(comparisons),
        },
        record_id=case_id,
    )

    metrics = {
        "candidate_script_count": len(candidate_scripts),
        "executed_ok_count": sum(1 for r in run_summaries if r["status"] == "executed_ok"),
        "failed_run_count": len(failed_runs),
        "non_deterministic_run_count": len(jitter_runs),
        "unseeded_stochastic_script_count": len(unseeded_scripts),
        "unpinned_dependency_count": env_audit["summary"]["unpinned_dependency_count"],
        "conda_pip_mixed_flag": 1 if env_audit["summary"]["conda_pip_mixed"] else 0,
        "parameter_conflict_count": len(param_conflicts),
        "paper_only_missing_param_count": len(paper_only_missing),
        "code_only_undocumented_param_count": len(code_only_undoc),
        "within_tolerance_count": len(within_tol),
        "out_of_tolerance_count": len(out_of_tol),
    }
    metric_provenance = {
        "candidate_script_count": "reproduction_audit_report.json:candidate_scripts",
        "executed_ok_count": "reproduction_audit_report.json:execution_runs",
        "failed_run_count": "reproduction_audit_report.json:failed_runs_excluded",
        "non_deterministic_run_count": "reproduction_audit_report.json:execution_runs",
        "unseeded_stochastic_script_count": "reproduction_audit_report.json:candidate_scripts",
        "unpinned_dependency_count": "reproduction_audit_report.json:environment_lockfile_audit",
        "conda_pip_mixed_flag": "reproduction_audit_report.json:environment_lockfile_audit",
        "parameter_conflict_count": "reproduction_audit_report.json:parameter_reconciliation",
        "paper_only_missing_param_count": "reproduction_audit_report.json:parameter_reconciliation",
        "code_only_undocumented_param_count": "reproduction_audit_report.json:parameter_reconciliation",
        "within_tolerance_count": "reproduction_audit_report.json:tolerance_comparisons",
        "out_of_tolerance_count": "reproduction_audit_report.json:tolerance_comparisons",
    }

    patch = {
        "artifacts": ["result.json", "reproduction_audit_report.json", "reproduction_report.md"],
        "reproducibility_grade": repro_grade,
        "evidence_ladder": evidence_ladder,
        "environment_snapshot": env_snapshot,
        "environment_lockfile_summary": env_audit["summary"],
        "parameter_reconciliation": param_reconciliation,
        "execution_runs": run_summaries,
        "failed_runs_excluded": excluded_failed,
        "tolerance_comparisons": comparisons,
        "missing_original_materials": missing_materials,
        "metrics": metrics,
        "metric_provenance": metric_provenance,
        "findings": [
            f"Audited {len(candidate_scripts)} candidate script(s) ({metrics['executed_ok_count']} clean exit, {metrics['failed_run_count']} failed excluded); "
            f"grade={repro_grade}; within_tolerance={len(within_tol)}/{len(comparisons)}; "
            f"param_conflicts={len(param_conflicts)}; jitter_runs={len(jitter_runs)}; "
            f"unpinned_deps={metrics['unpinned_dependency_count']}."
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["reproduction_audit_report.json", "reproduction_report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor da62e8d4 Reproducibility, Lockfile Drift & Discrepancy Audit Runner.")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    parser.add_argument("--repeats", type=int, default=2, help="Isolated repeat runs per candidate script for jitter detection (default: 2).")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], repeats=args.repeats)
    print(json.dumps({
        "ok": True,
        "case_id": res["case_id"],
        "status": res["status"],
        "reproducibility_grade": res["reproducibility_grade"],
        "evidence_ladder": res["evidence_ladder"],
        "metrics": res["metrics"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
