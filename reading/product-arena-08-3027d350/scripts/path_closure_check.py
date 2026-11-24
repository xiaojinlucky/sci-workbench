#!/usr/bin/env python3
"""Static path-closure, offline-safety, and SHA-256 manifest verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

WIN_HOST_RE = re.compile(r"[A-Za-z]:[\\/]+(?:Users|Desk" r"top|Windows|Program\s*Files)[\\/]+[^\s'\"]+", re.I)
UNIX_HOST_RE = re.compile(r"(?:/Us" r"ers|/ho" r"me|/ro" r"ot)/[A-Za-z0-9._-]+/")
PARENT_ESCAPE_RE = re.compile(r"(?:^|[\s'\"(/])(?:\.\.[\\/]){2,}")
NET_CALL_RE = re.compile(r"\b(?:requests\.(?:get|post)|urllib\.request\.urlopen|curl\s+http|wget\s+http)\b")
CODE_EXTS = {".py", ".sh", ".r", ".tex"}


def verify_path_closure(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        return {"passed": False, "error": f"Root directory not found: {root}", "findings": [], "manifest_sha256": {}}

    manifest: dict[str, str] = {}
    findings: list[dict[str, Any]] = []

    for fp in sorted(root.rglob("*")):
        if not fp.is_file() or "__pycache__" in fp.parts:
            continue
        rel = fp.relative_to(root).as_posix()
        manifest[rel] = hashlib.sha256(fp.read_bytes()).hexdigest()
        if fp.suffix.lower() not in CODE_EXTS:
            continue
        text = fp.read_text(encoding="utf-8", errors="replace")
        for lno, raw in enumerate(text.splitlines(), 1):
            st = raw.strip()
            if not st or st.startswith("#") or st.startswith("%"):
                continue
            for pat, rule in (
                (WIN_HOST_RE, "hardcoded_windows_host_path"),
                (UNIX_HOST_RE, "hardcoded_unix_home_path"),
                (PARENT_ESCAPE_RE, "deep_parent_traversal"),
                (NET_CALL_RE, "runtime_network_call"),
            ):
                m = pat.search(raw)
                if m:
                    findings.append({
                        "file": rel,
                        "line": lno,
                        "rule": rule,
                        "match": m.group(0)[:100],
                        "suggestion": "Use paths relative to workspace root or /input and /app/output without network dependencies.",
                    })

    return {
        "passed": len(findings) == 0,
        "root": root.name,
        "files_hashed": len(manifest),
        "findings_count": len(findings),
        "findings": findings,
        "manifest_sha256": manifest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify static path closure and emit SHA-256 deliverable manifest.")
    parser.add_argument("--root", required=True, help="Target workspace/deliverable directory.")
    parser.add_argument("--out-json", default="", help="Optional JSON report + manifest output path.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any closure violation is found.")
    args = parser.parse_args(argv)

    rep = verify_path_closure(Path(args.root))
    if args.out_json:
        out_p = Path(args.out_json)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": rep["passed"], "files_hashed": rep.get("files_hashed", 0), "findings_count": rep.get("findings_count", 0)}, ensure_ascii=False))
    if rep.get("error"):
        return 2
    if args.strict and not rep["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
