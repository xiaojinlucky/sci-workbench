#!/usr/bin/env python3
"""Universal /input discovery and /input/case.json output_contract sealer (pure Python 3.12 stdlib).

Eliminates hardcoded /input filenames and guarantees /app/output/result.json
preserves case_id and every field declared in case.json's output_contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HOST_ABS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s'\"<>|]+|/(?:mnt/[a-zA-Z]|home|Users|root|tmp|var)/[^\s'\"<>|]+)"
)


def _sanitize_host_paths(obj: Any) -> Any:
    """Recursively scrub local host absolute paths to relative basenames while preserving /input and /app paths."""
    if isinstance(obj, str):
        if obj.startswith(("/input/", "/app/", "/skills/")):
            return obj
        def _repl(m: re.Match[str]) -> str:
            token = m.group(0)
            parts = re.split(r"[\\/]", token.rstrip("\\/"))
            return parts[-1] if parts else token
        return _HOST_ABS_PATH_RE.sub(_repl, obj)
    if isinstance(obj, list):
        return [_sanitize_host_paths(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_host_paths(v) for k, v in obj.items()}
    return obj


def _collect_payload_diagnostics(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Auto-harvest honest uncertainty and gaps from runner & scientific analysis payloads."""
    auto_unc: list[str] = []
    auto_gaps: list[str] = []
    if not isinstance(payload, dict):
        return auto_unc, auto_gaps

    env_lock = payload.get("environment_lock") or {}
    for g in env_lock.get("offline_dependency_gaps") or []:
        mod = g.get("module") if isinstance(g, dict) else str(g)
        auto_gaps.append(f"Offline dependency gap: {mod}")
    avail_pkgs = env_lock.get("available_scientific_packages")
    if isinstance(avail_pkgs, list) and len(avail_pkgs) == 0:
        auto_unc.append(
            "Offline sandbox has no third-party scientific packages (numpy/pandas/scipy/matplotlib); executed verified pure-stdlib statistical & SVG pipeline."
        )

    st = payload.get("static_audit") or {}
    for w in st.get("warnings") or []:
        auto_unc.append(f"Static audit warning: {w}")
    for b in st.get("blockers") or []:
        auto_gaps.append(f"Static audit blocker: {b}")

    for v in payload.get("invariant_violations") or []:
        auto_gaps.append(f"Invariant violation: {v}")

    for g in payload.get("gaps") or []:
        if isinstance(g, dict):
            code = g.get("code", "GAP")
            detail = g.get("detail") or g.get("message") or str(g)
            auto_gaps.append(f"[{code}] {detail}")
        else:
            auto_gaps.append(str(g))

    for u in payload.get("uncertainty") or []:
        auto_unc.append(str(u))

    nc = payload.get("numeric_crosscheck") or {}
    if isinstance(nc, dict) and not nc.get("skipped", True):
        for mm in nc.get("mismatches") or []:
            auto_gaps.append(f"Numeric cross-check mismatch: {mm}")

    sci = payload.get("scientific_analysis") or payload
    if isinstance(sci, dict):
        for dw in sci.get("diagnostics_warnings") or []:
            auto_unc.append(str(dw))
        for subkey in ("regression_ols", "group_comparison"):
            sub = sci.get(subkey)
            if isinstance(sub, dict):
                for dw in sub.get("diagnostics_warnings") or []:
                    auto_unc.append(str(dw))

    return list(dict.fromkeys(auto_unc)), list(dict.fromkeys(auto_gaps))

DATA_EXTS = {".csv", ".tsv", ".json", ".jsonl"}
DB_EXTS = {".sqlite", ".sqlite3", ".db"}
SCRIPT_EXTS = {".py", ".sql"}
DOC_EXTS = {".md", ".txt", ".yaml", ".yml"}
META_HINTS = ("dict", "dictionary", "workorder", "schema", "rule", "spec", "contract", "metadata")


def _sha256_short(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_contract_fields(case_obj: dict[str, Any]) -> list[str]:
    """Extract required top-level field names from case.json output_contract."""
    oc = case_obj.get("output_contract")
    fields: list[str] = []
    if isinstance(oc, dict):
        if isinstance(oc.get("required_fields"), list):
            fields.extend(str(f) for f in oc["required_fields"])
        if isinstance(oc.get("fields"), list):
            for it in oc["fields"]:
                if isinstance(it, str):
                    fields.append(it)
                elif isinstance(it, dict) and it.get("name"):
                    fields.append(str(it["name"]))
        elif isinstance(oc.get("fields"), dict):
            fields.extend(str(k) for k in oc["fields"].keys())
        if isinstance(oc.get("properties"), dict):
            fields.extend(str(k) for k in oc["properties"].keys())
        if not fields:
            # Treat non-meta keys in output_contract dict as target schema fields
            skip = {"type", "format", "filename", "path", "description", "notes", "version"}
            fields.extend(str(k) for k in oc.keys() if k not in skip)
    elif isinstance(oc, list):
        for it in oc:
            if isinstance(it, str):
                fields.append(it)
            elif isinstance(it, dict) and it.get("name"):
                fields.append(str(it["name"]))
    return list(dict.fromkeys(fields))


def discover_input_dir(input_dir: Path) -> dict[str, Any]:
    """Recursively scan /input to locate case.json, task.md, data tables, SQLite DBs, dicts, and workorders."""
    root = input_dir.resolve()
    case_path = root / "case.json"
    task_path = root / "task.md"
    case_obj: dict[str, Any] = {}
    if case_path.is_file():
        try:
            case_obj = json.loads(case_path.read_text(encoding="utf-8-sig"))
        except Exception:
            case_obj = {}

    case_id = str(case_obj.get("case_id") or case_obj.get("id") or "unknown-case")
    contract_fields = extract_contract_fields(case_obj)

    tables: list[dict[str, Any]] = []
    sqlite_dbs: list[dict[str, Any]] = []
    dictionaries: list[dict[str, Any]] = []
    workorders: list[dict[str, Any]] = []
    scripts: list[dict[str, Any]] = []
    other_files: list[str] = []

    if root.is_dir():
        for fp in sorted(root.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(root).as_posix()
            low = rel.lower()
            ext = fp.suffix.lower()
            if rel in {"case.json", "task.md"}:
                continue
            info = {"rel": rel, "abs": str(fp), "bytes": fp.stat().st_size, "sha256": _sha256_short(fp)}
            if ext in DB_EXTS:
                sqlite_dbs.append(info)
            elif "workorder" in low or "work_order" in low or "cleaning_rule" in low:
                workorders.append(info)
            elif any(h in low for h in ("dict", "dictionary", "schema", "column_spec")) and ext in {".json", ".md", ".yaml", ".yml", ".txt"}:
                dictionaries.append(info)
            elif ext in DATA_EXTS:
                # Distinguish JSON workorder/dict vs JSON tabular data
                if ext == ".json":
                    try:
                        parsed = json.loads(fp.read_text(encoding="utf-8-sig"))
                        if isinstance(parsed, dict) and any(k in parsed for k in ("per_metric_rules", "primary_key", "sentinel_taxonomy", "qc_bounds", "forbid_imputation")):
                            workorders.append(info)
                            continue
                        if isinstance(parsed, dict) and any(k in parsed for k in ("columns", "fields", "variables", "data_dictionary")):
                            dictionaries.append(info)
                            continue
                    except Exception:
                        pass
                tables.append(info)
            elif ext in SCRIPT_EXTS:
                scripts.append(info)
            else:
                other_files.append(rel)

    return {
        "ok": True,
        "input_dir": str(root),
        "case_json_found": case_path.is_file(),
        "task_md_found": task_path.is_file(),
        "case_id": case_id,
        "output_contract_fields": contract_fields,
        "primary_table_candidate": tables[0]["abs"] if tables else (sqlite_dbs[0]["abs"] if sqlite_dbs else None),
        "primary_sqlite_candidate": sqlite_dbs[0]["abs"] if sqlite_dbs else None,
        "primary_workorder_candidate": workorders[0]["abs"] if workorders else (dictionaries[0]["abs"] if dictionaries else None),
        "primary_dictionary_candidate": dictionaries[0]["abs"] if dictionaries else None,
        "tables": tables,
        "sqlite_dbs": sqlite_dbs,
        "dictionaries": dictionaries,
        "workorders": workorders,
        "scripts": scripts,
        "other_files": other_files,
    }


def seal_result_contract(
    *,
    case_json_path: Path | None,
    result_json_path: Path,
    task_id: str,
    status: str = "ok",
    payload_path: Path | None = None,
    artifacts: list[str] | None = None,
    sources: list[str] | None = None,
    uncertainty: list[str] | None = None,
    gaps: list[str] | None = None,
    change_records: list[str] | None = None,
    fresh: bool = False,
) -> dict[str, Any]:
    """Merge analysis outputs and seal result.json with case_id and all case.json output_contract fields."""
    case_obj: dict[str, Any] = {}
    if case_json_path and case_json_path.is_file():
        case_obj = json.loads(case_json_path.read_text(encoding="utf-8-sig"))
    case_id = str(case_obj.get("case_id") or case_obj.get("id") or "unknown-case")
    contract_fields = extract_contract_fields(case_obj)

    existing: dict[str, Any] = {}
    if not fresh and result_json_path.is_file():
        try:
            existing = json.loads(result_json_path.read_text(encoding="utf-8-sig"))
        except Exception:
            existing = {}

    payload: dict[str, Any] = {}
    if payload_path and payload_path.is_file():
        payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))

    auto_unc, auto_gaps = _collect_payload_diagnostics(payload)

    merged = {} if fresh else dict(existing)
    merged["case_id"] = case_id
    merged["task_id"] = task_id
    merged["status"] = status
    merged["sealed_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    merged["artifacts"] = sorted(set(([] if fresh else list(merged.get("artifacts") or [])) + list(artifacts or [])))
    merged["sources"] = sorted(set(([] if fresh else list(merged.get("sources") or [])) + list(sources or [])))
    merged["uncertainty"] = list(dict.fromkeys(([] if fresh else list(merged.get("uncertainty") or [])) + list(uncertainty or []) + auto_unc))
    merged["gaps"] = list(dict.fromkeys(([] if fresh else list(merged.get("gaps") or [])) + list(gaps or []) + auto_gaps))
    merged["change_records"] = list(dict.fromkeys(([] if fresh else list(merged.get("change_records") or [])) + list(change_records or [])))

    if payload:
        merged["analysis_summary"] = payload
        # Promote matching keys directly if requested by output_contract (always refresh from current payload)
        for f in contract_fields:
            if f in payload and payload[f] is not None:
                merged[f] = payload[f]

    # Ensure every output_contract field is explicitly populated (honestly marked if absent)
    contract_backfilled: list[str] = []
    sci_block = payload.get("scientific_analysis") if isinstance(payload, dict) else None
    compact_results = (
        {
            "execution_status": payload.get("status"),
            "gates_passed": payload.get("ok"),
            "scientific_analysis": sci_block,
            "numeric_crosscheck": payload.get("numeric_crosscheck"),
        }
        if sci_block
        else (payload or merged.get("analysis_summary") or {})
    )
    compact_metrics = (
        payload.get("metrics")
        or (sci_block.get("key_metrics") if isinstance(sci_block, dict) else None)
        or (sci_block.get("regression_ols") if isinstance(sci_block, dict) else None)
        or (sci_block.get("group_comparison") if isinstance(sci_block, dict) else None)
        or payload.get("omnibus_test")
        or payload.get("row_accounting")
        or payload
    )
    alias_map = {
        "result": compact_results,
        "results": compact_results,
        "summary": compact_results,
        "metrics": compact_metrics,
        "findings": merged.get("findings") or (payload.get("findings") if isinstance(payload, dict) else None) or [f"Completed {task_id} for {case_id} with status={status}."],
        "evidence": merged["sources"],
        "provenance": {
            "sources": merged["sources"],
            "artifacts": merged["artifacts"],
            "script_sha256": payload.get("script_sha256") if isinstance(payload, dict) else None,
            "environment_fingerprint_sha256": (payload.get("environment_lock") or {}).get("fingerprint_sha256") if isinstance(payload, dict) else None,
            "seed": payload.get("seed") if isinstance(payload, dict) else None,
        },
        "warnings": merged["uncertainty"],
        "limitations": merged["uncertainty"],
        "errors": merged["gaps"],
        "quality_report": payload.get("quality_report") or merged["artifacts"],
        "cleaned_data": [a for a in merged["artifacts"] if a.endswith(".csv")],
        "charts": [a for a in merged["artifacts"] if a.endswith((".svg", ".png", ".pdf"))],
        "queries": payload.get("queries") or [],
    }
    for f in contract_fields:
        if f not in merged or merged[f] is None:
            if f in alias_map:
                merged[f] = alias_map[f]
            else:
                merged[f] = {"status": "not_provided_by_upstream_payload", "field": f}
                merged["gaps"].append(f"output_contract field {f!r} had no direct metric mapping; marked explicitly")
            contract_backfilled.append(f)

    merged["output_contract_verification"] = {
        "required_fields": contract_fields,
        "all_present": all(f in merged for f in contract_fields),
        "backfilled_fields": contract_backfilled,
    }

    # Scrub any host absolute paths before sealing to disk
    merged = _sanitize_host_paths(merged)

    result_json_path.parent.mkdir(parents=True, exist_ok=True)
    result_json_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": bool(merged["output_contract_verification"]["all_present"]),
        "result_json": result_json_path.name,
        "case_id": case_id,
        "status": status,
        "contract_fields_verified": contract_fields,
        "backfilled_fields": contract_backfilled,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auto-discover /input artifacts and seal case.json output_contract into result.json.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_disc = sub.add_parser("discover", help="Scan an input directory for case.json, tables, SQLite DBs, dictionaries, and workorders.")
    p_disc.add_argument("--input-dir", required=True, help="Case input directory (e.g. /input).")
    p_disc.add_argument("--out", default=None, help="Optional output JSON path.")

    p_seal = sub.add_parser("seal", help="Seal analysis summary and case.json output_contract fields into result.json.")
    p_seal.add_argument("--case-json", default=None, help="Path to /input/case.json.")
    p_seal.add_argument("--result-json", required=True, help="Path to /app/output/result.json.")
    p_seal.add_argument("--task-id", required=True, help="Challenge/task identifier.")
    p_seal.add_argument("--status", default="ok", choices=["ok", "partial", "partial_with_gaps", "error"], help="Honest execution status.")
    p_seal.add_argument("--payload-json", default=None, help="Optional analysis JSON artifact to link/promote.")
    p_seal.add_argument("--artifacts", default="", help="Comma-separated output artifact filenames.")
    p_seal.add_argument("--sources", default="", help="Comma-separated input source identifiers.")
    p_seal.add_argument("--uncertainty", default="", help="Semicolon-separated uncertainty notes.")
    p_seal.add_argument("--gaps", default="", help="Semicolon-separated honest gap notes.")
    p_seal.add_argument("--changes", default="", help="Semicolon-separated change records.")

    args = parser.parse_args(argv)
    if args.cmd == "discover":
        rep = discover_input_dir(Path(args.input_dir))
        txt = json.dumps(rep, indent=2, ensure_ascii=False)
        if args.out:
            p = Path(args.out)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(txt + "\n", encoding="utf-8")
        print(txt)
        return 0

    rep = seal_result_contract(
        case_json_path=Path(args.case_json) if args.case_json else None,
        result_json_path=Path(args.result_json),
        task_id=args.task_id,
        status=args.status,
        payload_path=Path(args.payload_json) if args.payload_json else None,
        artifacts=[x.strip() for x in args.artifacts.split(",") if x.strip()],
        sources=[x.strip() for x in args.sources.split(",") if x.strip()],
        uncertainty=[x.strip() for x in args.uncertainty.split(";") if x.strip()],
        gaps=[x.strip() for x in args.gaps.split(";") if x.strip()],
        change_records=[x.strip() for x in args.changes.split(";") if x.strip()],
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
