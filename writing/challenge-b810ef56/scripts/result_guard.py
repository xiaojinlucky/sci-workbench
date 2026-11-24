"""M2: Atomic result initialization, NaN sanitization, and non-zero anti-blank guard."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_json_value(value: Any, *, path: str = "$", stripped: list[str] | None = None) -> Any:
    """Replace non-finite floats (NaN/Inf) with None so JSON serialization never crashes or invalidates."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            if stripped is not None:
                stripped.append(path)
            return None
        return value
    if isinstance(value, dict):
        return {str(k): sanitize_json_value(v, path=f"{path}.{k}", stripped=stripped) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json_value(v, path=f"{path}[{idx}]", stripped=stripped) for idx, v in enumerate(value)]
    return value


def atomic_write_text(path: str | Path, content: str) -> Path:
    """Atomically write UTF-8 text via temp sibling and os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return target


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> tuple[Path, list[str]]:
    """Sanitize NaN/Inf values and atomically write strict RFC-8259 JSON."""
    stripped: list[str] = []
    clean = sanitize_json_value(payload, stripped=stripped)
    encoded = json.dumps(clean, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    return atomic_write_text(path, encoded), stripped


def init_result(
    path: str | Path,
    *,
    case_id: str = "unknown",
    task_id: str = "",
    schema_version: str = "1.0",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create initial skeleton result file at step 1 so timeout/cap never leaves an empty deliverable."""
    skeleton: dict[str, Any] = {
        "schema_version": schema_version,
        "status": "partial_init",
        "case_id": case_id,
        "task_id": task_id,
        "created_at_utc": _utc_now(),
        "updated_at_utc": _utc_now(),
        "artifacts": [],
        "metrics": {},
        "findings": [],
        "gaps": [],
        "warnings": [],
    }
    if extra:
        skeleton.update(extra)
    atomic_write_json(path, skeleton)
    return skeleton


def update_result(
    path: str | Path,
    patch: dict[str, Any],
    *,
    finalize: bool = False,
) -> dict[str, Any]:
    """Merge a patch into an existing result JSON and optionally seal status."""
    target = Path(path)
    if target.is_file():
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {"value": data}
    else:
        data = init_result(target)

    for key, val in patch.items():
        if key in ("gaps", "warnings", "artifacts", "findings") and isinstance(val, list) and isinstance(data.get(key), list):
            if val:
                data[key] = val
        elif key == "metrics" and isinstance(val, dict) and isinstance(data.get("metrics"), dict):
            data["metrics"].update(val)
        else:
            data[key] = val

    data["updated_at_utc"] = _utc_now()
    if finalize:
        has_gaps = bool(data.get("gaps"))
        data["status"] = "partial_with_gaps" if has_gaps else "ok"

    _, stripped = atomic_write_json(target, data)
    if stripped:
        data.setdefault("gaps", []).append({
            "code": "NON_FINITE_FLOAT_SANITIZED",
            "severity": "warning",
            "fields": stripped[:20],
        })
        if finalize:
            data["status"] = "partial_with_gaps"
        atomic_write_json(target, data)
    return data


def validate_result_file(path: str | Path, required_keys: tuple[str, ...] = ("status", "case_id")) -> dict[str, Any]:
    """Check that a JSON result file exists, parses cleanly, and has required non-empty structure."""
    target = Path(path)
    errors: list[str] = []
    if not target.is_file():
        return {"ok": False, "errors": [f"missing result file: {target}"]}
    if target.stat().st_size == 0:
        return {"ok": False, "errors": [f"empty result file: {target}"]}
    try:
        obj = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "errors": [f"invalid JSON in {target}: {exc}"]}
    if not isinstance(obj, dict):
        return {"ok": False, "errors": [f"top-level JSON must be object, got {type(obj).__name__}"]}
    for rk in required_keys:
        if rk not in obj or obj[rk] in (None, ""):
            errors.append(f"missing or empty required key: {rk}")
    return {"ok": len(errors) == 0, "errors": errors, "status": obj.get("status"), "bytes": target.stat().st_size}


def resolve_default_result_path(app_root: str | Path = "/app") -> Path:
    """Resolve /app/output/result.json when /app exists, else /output/result.json."""
    app_path = Path(app_root)
    if app_path.is_dir():
        return app_path / "output" / "result.json"
    return Path("/output/result.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atomic result JSON guard.")
    parser.add_argument("--path", default="", help="Result JSON path (defaults to /app/output/result.json or /output/result.json).")
    parser.add_argument("--init", action="store_true", help="Initialize skeleton file.")
    parser.add_argument("--case-id", default="case-01", help="Case ID for initialization.")
    parser.add_argument("--task-id", default="", help="Optional task ID.")
    parser.add_argument("--patch-json", default="", help="JSON string patch to merge.")
    parser.add_argument("--finalize", action="store_true", help="Mark status ok/partial_with_gaps.")
    parser.add_argument("--validate", action="store_true", help="Validate result JSON.")
    args = parser.parse_args(argv)
    target_path = Path(args.path) if args.path else resolve_default_result_path()

    if args.init:
        res = init_result(target_path, case_id=args.case_id, task_id=args.task_id)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if args.patch_json or args.finalize:
        patch = json.loads(args.patch_json) if args.patch_json else {}
        res = update_result(target_path, patch, finalize=args.finalize)
        print(json.dumps({"ok": True, "status": res["status"]}, ensure_ascii=False))
        return 0
    if args.validate:
        chk = validate_result_file(target_path)
        print(json.dumps(chk, ensure_ascii=False))
        return 0 if chk["ok"] else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
