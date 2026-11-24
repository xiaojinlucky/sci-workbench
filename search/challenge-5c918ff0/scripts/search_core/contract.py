# -*- coding: utf-8 -*-
"""Output-contract-driven result assembly, artifact writers, and hash-chained audit trail.

Compatible with both Harbor case output_contract discovery and common_core M2/M5/M6 gates.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

try:
    from .caseio import SYNONYM_FIELD, _norm_field_key
except ImportError:
    from caseio import SYNONYM_FIELD, _norm_field_key  # type: ignore

DEFAULT_CONTRACT: Dict[str, Dict[str, Any]] = {
    "results": {"name": "results", "description": "Ranked/screened candidate literature with metadata, labels, rationale, and source locators", "required": True},
    "evidence": {"name": "evidence", "description": "Auditable query, screening, ranking, merge, or citation trace evidence", "required": True},
    "status": {"name": "status", "description": "Honest status: empty results, conflicts, source errors, completed/uncompleted scope", "required": True},
}

KEY_TERMS = {
    "results": ["result", "candidate", "ranking", "ranked", "hit", "retrieved", "selected", "screened", "paper", "record", "document", "answer", "finding", "included", "new", "added", "entry", "entries", "item", "list", "table", "merged", "plan", "diff", "change", "update"],
    "evidence": ["evidence", "source", "provenance", "citation", "reference", "justification", "rationale", "reason", "basis", "log", "trace", "query", "queries", "search", "anchor", "link", "support"],
    "status": ["status", "state", "warning", "gap", "uncertainty", "limitation", "missing", "error", "conflict", "excluded", "exclusion", "duplicate", "dedup", "skipped", "failure", "failed", "incomplete", "limit"],
    "summary": ["summary", "overview", "abstract", "conclusion", "takeaway", "headline", "description"],
    "metrics": ["metric", "statistics", "stats", "count", "quality", "score", "agreement", "kappa", "recall", "precision", "coverage", "evaluation"],
    "report": ["report", "draft", "review", "synthesis", "analysis", "narrative", "markdown", "outline"],
    "artifacts": ["artifact", "file", "output", "table", "csv", "json", "deliverable"],
    "changes": ["change", "diff", "delta", "version", "update", "history", "rollback"],
}


def map_field(name: str) -> str:
    key = _norm_field_key(name)
    if key in SYNONYM_FIELD:
        return SYNONYM_FIELD[key]
    low = key.replace("_", " ")
    best, bs = None, 0.0
    for canon, terms in KEY_TERMS.items():
        for t in terms:
            if re.search(r"(^|_)" + re.escape(t) + r"($|_)", key) or (len(t) > 4 and t in low):
                s = len(t) / (len(key) + 1) + 0.5
                if s > bs:
                    best, bs = canon, s
    return best or "extra"


class ResultBuilder:
    def __init__(self, bundle, work_dir: str, out_dir: str, skill_name: str, pipeline: Dict[str, Any]):
        self.b = bundle
        self.work = work_dir
        self.out = out_dir
        self.skill = skill_name
        self.pipeline = pipeline
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)
        self.t0 = time.time()
        self.audit: List[Dict[str, Any]] = []
        self.prev_hash = "0" * 64
        self.artifacts: List[Dict[str, Any]] = []

    def log(self, step: str, action: str, **kw):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        entry = {"ts": ts, "step": step, "action": action, **kw}
        h = hashlib.sha256((self.prev_hash + json.dumps(entry, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()
        entry["hash"] = h
        entry["prev"] = self.prev_hash
        self.prev_hash = h
        self.audit.append(entry)

    def write_json(self, rel: str, data: Any) -> str:
        p = os.path.join(self.out, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
        self.artifacts.append({"path": rel, "kind": "json", "bytes": os.path.getsize(p)})
        return rel

    def write_csv(self, rel: str, rows: List[Dict[str, Any]], fields: Optional[List[str]] = None) -> str:
        p = os.path.join(self.out, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if not rows:
            rows = [{"note": "no rows"}]
        fields = fields or list(rows[0].keys())
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: _to_cell(v) for k, v in r.items()})
        self.artifacts.append({"path": rel, "kind": "csv", "bytes": os.path.getsize(p)})
        return rel

    def write_markdown(self, rel: str, text: str) -> str:
        p = os.path.join(self.out, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        self.artifacts.append({"path": rel, "kind": "markdown", "bytes": os.path.getsize(p)})
        return rel

    def build(self, facts: Dict[str, Any], contract: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        contract = contract or self.b.contract or DEFAULT_CONTRACT
        result: Dict[str, Any] = {
            "case_id": self.b.case_id,
            "skill": self.skill,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        coverage: Dict[str, Any] = {}
        for key, spec in contract.items():
            canon = map_field(spec.get("name", key))
            value = facts.get(key) if key in facts else facts.get(canon)
            how = f"facts.{key}" if key in facts else (f"facts.{canon}" if canon in facts else None)
            if value is None:
                value = {
                    "status": "not_produced",
                    "note": f"Pipeline did not produce a value for contract field '{key}'. Nothing was fabricated.",
                    "contract_field": key,
                }
                how = "not_produced(honest)"
            result[key] = value
            coverage[key] = {"mapped_to": how, "type": type(value).__name__, "size": _size(value)}

        for k in ("summary", "metrics", "status", "evidence", "results", "findings", "gaps", "uncertainty"):
            if k not in result and k in facts:
                result[k] = facts[k]

        result.setdefault("summary", {})
        # Keep Harbor M2/M6 top-level `status` strictly as "ok" | "partial_with_gaps" while preserving full structured status in `status_detail`
        raw_status = result.get("status")
        if isinstance(raw_status, dict):
            result["status_detail"] = raw_status
            has_gaps = bool(
                raw_status.get("gaps")
                or raw_status.get("errors")
                or raw_status.get("source_load_errors")
                or raw_status.get("pagination_issues")
                or raw_status.get("not_completed")
            )
            result["status"] = "partial_with_gaps" if has_gaps else "ok"
            result.setdefault("gaps", list(raw_status.get("gaps", [])))
            result.setdefault("warnings", list(raw_status.get("warnings", [])))
            if "stopping_verdict" in raw_status:
                result["stopping_verdict"] = raw_status["stopping_verdict"]
        elif raw_status not in ("ok", "partial_with_gaps"):
            result["status_detail"] = {"raw_status": raw_status}
            result["status"] = "partial_with_gaps"
        # Derive concise grounded findings if not explicitly supplied
        if "findings" not in result:
            s_text = result["summary"].get("text") if isinstance(result["summary"], dict) else str(result["summary"])
            result["findings"] = [s_text] if s_text else []
        result["contract_coverage"] = coverage
        result["contract_source"] = self.b.contract_source or "default (no explicit output_contract in case)"
        result["run"] = {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "duration_s": round(time.time() - self.t0, 2),
            "offline": True,
            "input_files": len(self.b.files),
            "records": len(self.b.records),
            "docs": len(self.b.docs),
            "pipeline": self.pipeline,
        }
        return result

    def finalize(self, result: Dict[str, Any], report_md: Optional[str] = None) -> Dict[str, Any]:
        self.log("finalize", "assemble_result_json", contract_fields=len(result.get("contract_coverage", {})))
        audit = {"entries": self.audit, "chain_head": self.prev_hash, "verified": _verify_chain(self.audit)}
        self.write_json("audit_log.json", audit)
        manifest = {
            "case_id": self.b.case_id,
            "files": self.b.inventory(),
            "records_by_file": self.b.record_files,
            "source_load_errors": getattr(self.b, "source_load_errors", []),
            "notes": self.b.notes,
        }
        self.write_json("run_manifest.json", manifest)
        if report_md:
            self.write_markdown("report.md", report_md)
        result["artifacts"] = [a["path"] for a in self.artifacts]
        result["artifact_details"] = [{"path": a["path"], "kind": a["kind"], "bytes": a["bytes"]} for a in self.artifacts]
        result["audit"] = {"log": "audit_log.json", "entries": len(self.audit), "chain_head": self.prev_hash, "verified": audit["verified"]}
        result["self_check"] = self_check(result)
        target = os.path.join(self.out, "result.json")
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=_json_default)
        os.replace(tmp, target)
        return result


def _empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, dict, str)):
        return len(v) == 0
    return False


def _to_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=_json_default)
    return str(v)


def _json_default(o: Any):
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)


def _size(v: Any) -> int:
    if isinstance(v, (list, dict, str)):
        return len(v)
    return 1 if v is not None else 0


def _verify_chain(entries: List[Dict[str, Any]]) -> bool:
    prev = "0" * 64
    for e in entries:
        body = {k: v for k, v in e.items() if k not in ("hash", "prev")}
        h = hashlib.sha256((e["prev"] + json.dumps(body, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()
        if h != e["hash"] or e["prev"] != prev:
            return False
        prev = h
    return True


def self_check(result: Dict[str, Any]) -> Dict[str, Any]:
    issues = []
    cov = result.get("contract_coverage", {})
    missing = [k for k, v in cov.items() if str(v.get("mapped_to", "")).startswith("not_produced")]
    if missing:
        issues.append({"level": "warn", "msg": f"contract fields not produced: {missing}"})
    if not result.get("case_id"):
        issues.append({"level": "warn", "msg": "case_id empty"})
    for k in ("results", "evidence", "status"):
        if k in result and _empty(result[k]):
            issues.append({"level": "warn", "msg": f"{k} is empty"})
    if result.get("status") not in ("ok", "partial_with_gaps"):
        issues.append({"level": "error", "msg": f"top-level status must be 'ok' or 'partial_with_gaps', got {result.get('status')!r}"})
    try:
        json.dumps(result, ensure_ascii=False, default=_json_default)
    except Exception as e:
        issues.append({"level": "error", "msg": f"result not JSON-serializable: {e}"})
    return {
        "ok": not any(i["level"] == "error" for i in issues),
        "issues": issues,
        "contract_fields": len(cov),
        "filled": len(cov) - len(missing),
    }


def md_table(rows: List[Dict[str, Any]], cols: List[str], max_rows: int = 60, widths: Optional[Dict[str, int]] = None) -> str:
    if not rows:
        return "_(no rows)_\n"
    widths = widths or {}
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    lines = [head, sep]
    for r in rows[:max_rows]:
        cells = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False, default=_json_default)
            v = str(v).replace("\n", " ").replace("|", "\\|")
            w = widths.get(c, 80)
            cells.append(v[:w] + ("..." if len(v) > w else ""))
        lines.append("| " + " | ".join(cells) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n_... {len(rows) - max_rows} more rows in CSV/JSON artifact_")
    return "\n".join(lines) + "\n"


def bullets(items: List[str]) -> str:
    return "\n".join(f"- {i}" for i in items) + "\n" if items else ""
