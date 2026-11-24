#!/usr/bin/env python3
"""Universal /input discovery and /input/case.json output_contract sealer (pure Python 3.12 stdlib).

Eliminates hardcoded /input filenames and guarantees /app/output/result.json
preserves case_id and every field declared in case.json's output_contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def _is_substantive_contract_value(val: Any) -> bool:
    """Return True only when a contract field holds real non-placeholder content."""
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    if isinstance(val, dict):
        if not val:
            return False
        if val.get("status") == "not_provided_by_upstream_payload" or val.get("placeholder") is True:
            return False
        return True
    if isinstance(val, list):
        return len(val) > 0
    return True


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
) -> dict[str, Any]:
    """Merge analysis outputs and seal result.json with case_id and all case.json output_contract fields."""
    case_obj: dict[str, Any] = {}
    if case_json_path and case_json_path.is_file():
        case_obj = json.loads(case_json_path.read_text(encoding="utf-8-sig"))
    case_id = str(case_obj.get("case_id") or case_obj.get("id") or "unknown-case")
    contract_fields = extract_contract_fields(case_obj)

    existing: dict[str, Any] = {}
    if result_json_path.is_file():
        try:
            existing = json.loads(result_json_path.read_text(encoding="utf-8-sig"))
        except Exception:
            existing = {}

    payload: dict[str, Any] = {}
    if payload_path and payload_path.is_file():
        payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))

    merged = dict(existing)
    merged["case_id"] = case_id
    merged["task_id"] = task_id
    merged["status"] = status
    merged["sealed_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    merged["artifacts"] = sorted(set(list(merged.get("artifacts") or []) + list(artifacts or [])))
    merged["sources"] = sorted(set(list(merged.get("sources") or []) + list(sources or [])))
    merged["uncertainty"] = list(dict.fromkeys(list(merged.get("uncertainty") or []) + list(uncertainty or [])))
    merged["gaps"] = list(dict.fromkeys(list(merged.get("gaps") or []) + list(gaps or [])))
    merged["change_records"] = list(dict.fromkeys(list(merged.get("change_records") or []) + list(change_records or [])))

    if payload:
        merged["analysis_summary"] = payload
        # Promote matching keys directly if requested by output_contract
        for f in contract_fields:
            if f in payload and not _is_substantive_contract_value(merged.get(f)):
                merged[f] = payload[f]

    # Ensure every output_contract field is explicitly populated; distinguish substantive values from placeholders
    contract_backfilled: list[str] = []
    alias_map = {
        "result": payload or merged.get("analysis_summary") or {},
        "results": payload or merged.get("analysis_summary") or {},
        "summary": payload or merged.get("analysis_summary") or {},
        "metrics": payload.get("metrics") or payload.get("omnibus_test") or payload.get("ols_model") or payload.get("row_accounting") or payload,
        "findings": merged.get("findings") or [f"Completed {task_id} for {case_id} with status={status}."],
        "evidence": merged["sources"],
        "provenance": {"sources": merged["sources"], "artifacts": merged["artifacts"]},
        "warnings": merged["uncertainty"],
        "limitations": merged["uncertainty"],
        "errors": merged["gaps"],
        "quality_report": payload.get("quality_report") or merged["artifacts"],
        "cleaned_data": [a for a in merged["artifacts"] if a.endswith(".csv")],
        "charts": [a for a in merged["artifacts"] if a.endswith((".svg", ".png", ".pdf"))],
        "queries": payload.get("queries") or [],
    }
    for f in contract_fields:
        if f not in merged or not _is_substantive_contract_value(merged.get(f)):
            if f in alias_map and _is_substantive_contract_value(alias_map[f]):
                merged[f] = alias_map[f]
                contract_backfilled.append(f)
            elif f not in merged or merged[f] is None:
                merged[f] = {"status": "not_provided_by_upstream_payload", "placeholder": True, "field": f}
                gap_msg = f"output_contract field {f!r} had no substantive upstream value; marked as placeholder gap"
                if gap_msg not in merged["gaps"]:
                    merged["gaps"].append(gap_msg)
                contract_backfilled.append(f)

    substantive_fields = [f for f in contract_fields if _is_substantive_contract_value(merged.get(f))]
    missing_or_placeholder = [f for f in contract_fields if f not in substantive_fields]
    all_substantive = len(missing_or_placeholder) == 0

    merged["output_contract_verification"] = {
        "required_fields": contract_fields,
        "substantive_fields": substantive_fields,
        "missing_or_placeholder_fields": missing_or_placeholder,
        "all_present": all_substantive,
        "backfilled_fields": contract_backfilled,
    }
    if not all_substantive and merged["status"] == "ok":
        merged["status"] = "partial"

    result_json_path.parent.mkdir(parents=True, exist_ok=True)
    result_json_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": all_substantive,
        "result_json": str(result_json_path),
        "case_id": case_id,
        "status": merged["status"],
        "contract_fields_verified": contract_fields,
        "substantive_fields": substantive_fields,
        "missing_or_placeholder_fields": missing_or_placeholder,
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
    p_seal.add_argument("--status", default="ok", choices=["ok", "partial", "error"], help="Honest execution status.")
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
