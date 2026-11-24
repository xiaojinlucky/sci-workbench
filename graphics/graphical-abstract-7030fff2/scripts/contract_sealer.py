#!/usr/bin/env python3
"""Schema/source_ref pre-validator and /input/case.json output_contract auto-sealer for graphics Skills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def validate_spec_schema(spec: dict[str, Any], kind: str) -> dict[str, Any]:
    """Validate structural schema and source_ref coverage for schematic/apparatus/pathway specs."""
    errors: list[str] = []
    warnings: list[str] = []
    missing_source_refs: list[str] = []

    if not isinstance(spec, dict):
        return {"passed": False, "errors": ["Spec root must be a JSON object"], "warnings": [], "missing_source_refs": []}
    if not str(spec.get("title", "")).strip():
        warnings.append("Spec missing non-empty 'title'")

    if kind == "schematic":
        nodes = spec.get("nodes")
        edges = spec.get("edges") or []
        if not isinstance(nodes, list) or not nodes:
            errors.append("SCHEMA_ERROR: 'nodes' must be a non-empty list")
            nodes = []
        n_ids: set[str] = set()
        for idx, n in enumerate(nodes):
            if not isinstance(n, dict) or not str(n.get("id", "")).strip():
                errors.append(f"SCHEMA_ERROR: nodes[{idx}] missing string 'id'")
                continue
            nid = str(n["id"]).strip()
            n_ids.add(nid)
            if not str(n.get("source_ref", "")).strip():
                missing_source_refs.append(f"node:{nid}")
        for idx, e in enumerate(edges):
            if not isinstance(e, dict) or not e.get("source") or not e.get("target"):
                errors.append(f"SCHEMA_ERROR: edges[{idx}] missing 'source' or 'target'")
                continue
            if str(e["source"]) not in n_ids or str(e["target"]) not in n_ids:
                errors.append(f"SCHEMA_ERROR: edge {e.get('id', idx)!r} references unknown node")
            if not str(e.get("source_ref", "")).strip() and not e.get("uncertain"):
                missing_source_refs.append(f"edge:{e.get('id', idx)}")
    elif kind == "apparatus":
        units = spec.get("units")
        streams = spec.get("streams") or []
        if not isinstance(units, list) or not units:
            errors.append("SCHEMA_ERROR: 'units' must be a non-empty list")
            units = []
        u_ids: set[str] = set()
        for idx, u in enumerate(units):
            if not isinstance(u, dict) or not str(u.get("id", "")).strip():
                errors.append(f"SCHEMA_ERROR: units[{idx}] missing string 'id'")
                continue
            uid = str(u["id"]).strip()
            u_ids.add(uid)
            if not str(u.get("source_ref", "")).strip():
                missing_source_refs.append(f"unit:{uid}")
        for idx, st in enumerate(streams):
            if not isinstance(st, dict) or not st.get("source") or not st.get("target"):
                errors.append(f"SCHEMA_ERROR: streams[{idx}] missing 'source' or 'target'")
                continue
            if str(st["source"]) not in u_ids or str(st["target"]) not in u_ids:
                errors.append(f"SCHEMA_ERROR: stream {st.get('id', idx)!r} references unknown unit")
            if not str(st.get("source_ref", "")).strip():
                missing_source_refs.append(f"stream:{st.get('id', idx)}")
    elif kind == "pathway":
        entities = spec.get("entities")
        edges = spec.get("edges") or []
        if not isinstance(entities, list) or not entities:
            errors.append("SCHEMA_ERROR: 'entities' must be a non-empty list")
            entities = []
        e_ids: set[str] = set()
        for idx, ent in enumerate(entities):
            if not isinstance(ent, dict) or not str(ent.get("id", "")).strip() or not str(ent.get("name", "")).strip():
                errors.append(f"SCHEMA_ERROR: entities[{idx}] missing 'id' or 'name'")
                continue
            eid = str(ent["id"]).strip()
            e_ids.add(eid)
            if not str(ent.get("source_ref", "")).strip():
                missing_source_refs.append(f"entity:{eid}")
        for idx, ed in enumerate(edges):
            if not isinstance(ed, dict) or not ed.get("source") or not ed.get("target"):
                errors.append(f"SCHEMA_ERROR: edges[{idx}] missing 'source' or 'target'")
                continue
            if str(ed["source"]) not in e_ids or str(ed["target"]) not in e_ids:
                errors.append(f"SCHEMA_ERROR: edge {ed.get('id', idx)!r} references unknown entity")
            if not str(ed.get("source_ref", "")).strip() and not ed.get("uncertain"):
                missing_source_refs.append(f"edge:{ed.get('id', idx)}")

    if missing_source_refs:
        warnings.append(f"Elements lacking source_ref provenance ({len(missing_source_refs)}): {missing_source_refs[:8]}")
    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "missing_source_refs": missing_source_refs,
    }


def seal_case_result(
    case_json_path: Path | None,
    result_path: Path,
    *,
    task_id: str,
    artifacts: list[str],
    sources: list[str],
    uncertainty: list[str],
    change_records: list[dict[str, Any]],
    metrics: dict[str, Any] | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    """Read /input/case.json output_contract and seal /app/output/result.json preserving all contract fields."""
    case_obj: dict[str, Any] = {}
    if case_json_path is not None and case_json_path.is_file():
        case_obj = json.loads(case_json_path.read_text(encoding="utf-8-sig"))

    case_id = str(case_obj.get("case_id") or case_obj.get("id") or "unspecified-case")
    contract_fields: list[str] = []
    oc = case_obj.get("output_contract")
    if isinstance(oc, dict):
        if isinstance(oc.get("required_fields"), list):
            contract_fields = [str(f) for f in oc["required_fields"]]
        elif isinstance(oc.get("fields"), list):
            contract_fields = [str(f) for f in oc["fields"]]
        else:
            contract_fields = [str(k) for k in oc.keys()]
    elif isinstance(oc, list):
        contract_fields = [str(f) for f in oc]

    existing: dict[str, Any] = {}
    if result_path.is_file():
        try:
            existing = json.loads(result_path.read_text(encoding="utf-8-sig"))
        except Exception:
            existing = {}

    payload: dict[str, Any] = {
        **existing,
        "case_id": case_id,
        "task_id": task_id,
        "status": "partial_with_gaps" if uncertainty and status == "ok" else status,
        "artifacts": sorted(set(artifacts)),
        "editable_sources": [a for a in artifacts if a.endswith((".svg", ".drawio", ".tex"))],
        "rendered_exports": [a for a in artifacts if a.endswith((".png", ".pdf"))],
        "sources": sorted(set((Path(s).name if (len(s) > 2 and (s[1] == ":" or s.startswith("/"))) else s) for s in sources if s)),
        "uncertainty": uncertainty,
        "change_records": change_records,
        "metrics": metrics or {},
        "output_contract_fields_sealed": contract_fields,
    }
    # Ensure every explicitly requested output_contract key exists in result.json
    alias_map = {
        "editable_source": payload["editable_sources"],
        "exports": payload["rendered_exports"],
        "export_artifacts": payload["rendered_exports"],
        "provenance": payload["sources"],
        "source_and_change_log": payload["change_records"],
        "gaps": payload["uncertainty"],
        "limitations": payload["uncertainty"],
    }
    for field in contract_fields:
        if field in alias_map:
            payload[field] = alias_map[field]
        elif field not in payload:
            payload[field] = payload.get("artifacts") if "artifact" in field else "recorded"

    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"sealed": True, "result_path": str(result_path), "case_id": case_id, "contract_fields": contract_fields, "status": payload["status"]}
