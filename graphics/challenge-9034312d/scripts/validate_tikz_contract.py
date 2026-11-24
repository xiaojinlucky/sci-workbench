#!/usr/bin/env python3
"""Standalone TikZ and OpenTikZ edit_contract validator (zero external jsonschema dependency)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_META_KEYS = ("id", "name", "type", "domain", "tags", "author", "license", "requires", "preview")
REQUIRED_CONTRACT_KEYS = ("parameters", "node_naming", "styles", "operations", "invariants")
VALID_TYPES = {"icon", "template", "example"}

_RE_DEF = re.compile(r"\\def\s*(\\[A-Za-z@]+)\b")
_RE_STYLE = re.compile(r"([A-Za-z0-9_-]+)\s*/\.style\s*=")
_RE_REQ_PKG = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}")
_RE_REQ_TIKZLIB = re.compile(r"\\usetikzlibrary\{([^}]+)\}")
_RE_HEX_INLINE = re.compile(r"#[0-9A-Fa-f]{6}\b")


def validate_tikz_pair(tex_path: Path, meta_path: Path | None = None) -> dict[str, Any]:
    """Validate standalone TikZ source and optional .meta.json edit_contract consistency."""
    errors: list[str] = []
    warnings: list[str] = []

    if not tex_path.exists():
        return {"passed": False, "errors": [f"Missing .tex file: {tex_path}"], "warnings": []}

    tex = tex_path.read_text(encoding="utf-8-sig")
    if "\\documentclass" not in tex or "standalone" not in tex:
        errors.append("TikZ file must declare \\documentclass[...]{standalone}")
    if "\\begin{tikzpicture}" not in tex or "\\end{tikzpicture}" not in tex:
        errors.append("Missing \\begin{tikzpicture} ... \\end{tikzpicture}")

    body_match = re.search(r"\\begin\{tikzpicture\}(.*?)\\end\{tikzpicture\}", tex, flags=re.DOTALL)
    if body_match and _RE_HEX_INLINE.search(body_match.group(1)):
        errors.append("Inline hex color inside tikzpicture body; declare named \\definecolor in preamble instead")

    defs_found = set(_RE_DEF.findall(tex))
    styles_found = set(_RE_STYLE.findall(tex))
    pkgs_found = set()
    for grp in _RE_REQ_PKG.findall(tex):
        for p in grp.split(","):
            if p.strip():
                pkgs_found.add(p.strip())

    contract_checked = False
    if meta_path is not None:
        if not meta_path.exists():
            errors.append(f"Missing .meta.json file: {meta_path}")
        else:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                return {"passed": False, "errors": [f"Invalid meta JSON: {exc}"], "warnings": []}

            for rk in REQUIRED_META_KEYS:
                if rk not in meta:
                    errors.append(f"meta.json missing required key: {rk}")
            if meta.get("type") not in VALID_TYPES:
                errors.append(f"meta.json type must be one of {sorted(VALID_TYPES)}")
            for req in meta.get("requires", []) or []:
                if str(req) not in pkgs_found and str(req) not in tex:
                    warnings.append(f"Declared requirement {req!r} not explicitly visible in .tex preamble")

            ec = meta.get("edit_contract")
            if meta.get("type") == "template" and not isinstance(ec, dict):
                errors.append("template meta.json must contain edit_contract object")
            elif isinstance(ec, dict):
                contract_checked = True
                for ck in REQUIRED_CONTRACT_KEYS:
                    if ck not in ec:
                        errors.append(f"edit_contract missing required key: {ck}")
                for param in ec.get("parameters", []) or []:
                    pname = str(param.get("name", "")).strip()
                    if pname and pname not in defs_found:
                        errors.append(f"edit_contract parameter {pname!r} missing \\def in {tex_path.name}")
                for st in ec.get("styles", []) or []:
                    sname = str(st).strip()
                    if sname and sname not in styles_found:
                        errors.append(f"edit_contract style {sname!r} missing /.style= in {tex_path.name}")

    return {
        "passed": len(errors) == 0,
        "tex": str(tex_path),
        "meta": str(meta_path) if meta_path else None,
        "defs_count": len(defs_found),
        "styles_count": len(styles_found),
        "contract_checked": contract_checked,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate standalone TikZ .tex and .meta.json edit_contract.")
    parser.add_argument("--tex", required=True)
    parser.add_argument("--meta", default=None)
    args = parser.parse_args(argv)

    meta_p = Path(args.meta) if args.meta else None
    rep = validate_tikz_pair(Path(args.tex), meta_p)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
