"""Pure-stdlib SHA-256 hash-chained audit ledger for reproducible Harbor outputs.

Adapted from AngelChen-HC/systematic-review-skill (Apache-2.0, scripts/srlib/audit.py),
operating offline with Python 3.12 standard library only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional


def canonical_bytes(obj: Any) -> bytes:
    """Deterministic UTF-8 canonical JSON representation for hashing."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_hex(canonical_bytes(obj))


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def init_audit_log(path: str | Path, *, run_label: str = "harbor-repro") -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    log: dict[str, Any] = {
        "schema_version": "1.0",
        "run_label": run_label,
        "chain_integrity": {
            "algorithm": "SHA-256",
            "genesis_hash": sha256_hex(f"GENESIS:{run_label}".encode("utf-8")),
        },
        "entries": [],
    }
    _atomic_save(target, log)
    return log


def append_audit_entry(
    path: str | Path,
    *,
    phase: str,
    action: str,
    actor: str,
    output: dict[str, Any],
    decision: Optional[str] = None,
    record_id: Optional[str] = None,
    input_obj: Optional[Any] = None,
) -> dict[str, Any]:
    target = Path(path)
    if target.is_file():
        log = json.loads(target.read_text(encoding="utf-8"))
    else:
        log = init_audit_log(target)

    entries = log.get("entries", [])
    prev_hash = sha256_obj(entries[-1]) if entries else log["chain_integrity"]["genesis_hash"]
    entry = {
        "entry_id": str(uuid.uuid4()),
        "created_at_utc": now_utc_iso(),
        "phase": phase,
        "action": action,
        "actor": actor,
        "record_id": record_id,
        "input_hash": sha256_obj(input_obj) if input_obj is not None else None,
        "output_hash": sha256_obj(output),
        "output": output,
        "decision": decision,
        "previous_entry_hash": prev_hash,
    }
    entries.append(entry)
    log["entries"] = entries
    _atomic_save(target, log)
    return entry


def verify_audit_chain(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        return {"ok": False, "error": f"missing audit log: {target}", "entry_count": 0}
    log = json.loads(target.read_text(encoding="utf-8"))
    prev = (log.get("chain_integrity") or {}).get("genesis_hash") or ""
    entries = log.get("entries", [])
    for idx, entry in enumerate(entries):
        if entry.get("previous_entry_hash") != prev:
            return {"ok": False, "broken_index": idx, "entry_count": len(entries)}
        prev = sha256_obj(entry)
    return {"ok": True, "entry_count": len(entries), "head_hash": prev}
