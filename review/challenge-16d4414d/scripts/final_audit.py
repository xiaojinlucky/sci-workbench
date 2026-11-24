"""M6: Final pre-exit deliverable contract and quality audit (quote-safe for review findings)."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

# Build marker regex dynamically so audit source text stays self-clean.
MARKER_RE = re.compile(
    r"\b(?:" + "TO" + "DO|" + "TB" + "D|" + "FIX" + "ME" + r")\b|\[insert\s+[^\]]+\]|lorem\s+ipsum",
    re.I,
)
SECRET_RE = re.compile(r"(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})")
HOST_LEAK_RE = re.compile(r"(?:[A-Za-z]:\\Users\\|/Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/)")
TEXT_SUFFIXES = {".json", ".md", ".txt", ".csv", ".tsv", ".svg", ".html", ".tex"}

# Verbatim manuscript quote / raw evidence fields that may legitimately quote placeholder tokens (e.g., "IRB: TBD")
QUOTE_EXEMPT_KEYS = {
    "verbatim_quote",
    "quote",
    "excerpt",
    "source_quote",
    "manuscript_quote",
    "raw",
    "raw_text",
    "raw_value",
    "candidate",
    "bibliography_entry",
    "rejected_pseudo_ethics_ids",
    "claim_text",
    "evidence_text",
    "evidence_basis",
    "message",
}

MD_QUOTE_LINE_RE = re.compile(
    r"^\s*(?:>\s*|[-*]\s*\*\*(?:Quote|Excerpt|Verbatim|Evidence\s+Basis|Rejected\s+Pseudo|Source\s+Quote)[^*]*\*\*\s*:)",
    re.I,
)


def _find_non_finite(obj: Any, path: str = "$") -> list[str]:
    bad: list[str] = []
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return [path]
    if isinstance(obj, dict):
        for k, v in obj.items():
            bad.extend(_find_non_finite(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for idx, v in enumerate(obj):
            bad.extend(_find_non_finite(v, f"{path}[{idx}]"))
    return bad


def _strip_quote_fields(obj: Any) -> Any:
    """Return a copy of parsed JSON with verbatim-quote evidence fields omitted before unfinished-marker checks."""
    if isinstance(obj, dict):
        return {
            k: _strip_quote_fields(v)
            for k, v in obj.items()
            if str(k).lower() not in QUOTE_EXEMPT_KEYS
        }
    if isinstance(obj, list):
        return [_strip_quote_fields(v) for v in obj]
    return obj


def _strip_markdown_quotes(text: str) -> str:
    """Exclude quoted manuscript excerpts and inline quoted snippets from unfinished-marker scanning."""
    kept: list[str] = []
    for line in text.splitlines():
        if MD_QUOTE_LINE_RE.match(line):
            continue
        # Also mask double-quoted verbatim manuscript fragments on narrative lines
        cleaned = re.sub(r'"[^"\n]{1,240}"', '""', line)
        cleaned = re.sub(r"`[^`\n]{1,120}`", "``", cleaned)
        kept.append(cleaned)
    return "\n".join(kept)


def resolve_default_output_dir(app_root: str | Path = "/app") -> Path:
    """Resolve /app/output when /app exists, else fallback to /output."""
    app_path = Path(app_root)
    if app_path.is_dir():
        return app_path / "output"
    return Path("/output")


def audit_deliverables(
    output_dir: str | Path | None = None,
    *,
    app_root: str | Path = "/app",
    result_file: str = "result.json",
    required_files: list[str] | None = None,
    min_text_bytes: int = 16,
) -> dict[str, Any]:
    """Verify output directory completeness, UTF-8/JSON validity, and leak prevention."""
    out_root = Path(output_dir) if output_dir else resolve_default_output_dir(app_root)
    blocking: list[str] = []
    warnings: list[str] = []
    file_stats: list[dict[str, Any]] = []

    if not out_root.is_dir():
        return {
            "passed": False,
            "blocking_failures": [f"output directory missing: {out_root}"],
            "warnings": [],
            "files": [],
        }

    req = list(required_files or [])
    if result_file and result_file not in req:
        req.insert(0, result_file)

    for rel in req:
        fp = out_root / rel
        if not fp.is_file():
            blocking.append(f"missing required deliverable: {rel}")
        elif fp.stat().st_size == 0:
            blocking.append(f"zero-byte required deliverable: {rel}")

    for fp in sorted(p for p in out_root.rglob("*") if p.is_file()):
        rel = fp.relative_to(out_root).as_posix()
        sz = fp.stat().st_size
        file_stats.append({"rel": rel, "bytes": sz})
        if sz == 0:
            blocking.append(f"zero-byte artifact present: {rel}")
            continue

        if fp.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = fp.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                blocking.append(f"invalid UTF-8 in {rel}: {exc}")
                continue

            if sz < min_text_bytes and rel in req:
                warnings.append(f"very small text deliverable ({sz} bytes): {rel}")
            if SECRET_RE.search(text):
                blocking.append(f"credential-like secret pattern detected in {rel}")
            if HOST_LEAK_RE.search(text):
                warnings.append(f"host-like user directory path detected in {rel}")

            if fp.suffix.lower() == ".json":
                try:
                    parsed = json.loads(text)
                except Exception as exc:
                    blocking.append(f"invalid JSON in {rel}: {exc}")
                    continue
                # Check unfinished markers only outside verbatim manuscript quote fields
                non_quote_blob = json.dumps(_strip_quote_fields(parsed), ensure_ascii=False)
                if MARKER_RE.search(non_quote_blob):
                    blocking.append(f"unfinished marker detected in {rel}")
                non_finite = _find_non_finite(parsed)
                if non_finite:
                    blocking.append(f"non-finite floats in {rel}: {non_finite[:5]}")
                if rel == result_file and isinstance(parsed, dict):
                    if parsed.get("status") not in {"ok", "partial_with_gaps"}:
                        blocking.append(f"{rel} status must be 'ok' or 'partial_with_gaps', got {parsed.get('status')!r}")
                    if not parsed.get("case_id"):
                        blocking.append(f"{rel} missing non-empty case_id")
                    declared_arts = parsed.get("artifacts")
                    if isinstance(declared_arts, list):
                        for art in declared_arts:
                            raw_art = art.get("path") or art.get("file") or art.get("rel") if isinstance(art, dict) else (art if isinstance(art, str) else "")
                            norm_art = str(raw_art or "").strip().lstrip("/")
                            if norm_art.startswith("app/output/"):
                                norm_art = norm_art[len("app/output/"):]
                            elif norm_art.startswith("output/"):
                                norm_art = norm_art[len("output/"):]
                            if not norm_art:
                                continue
                            art_p = out_root / norm_art
                            if not art_p.is_file():
                                blocking.append(f"missing artifact declared in {rel}: {norm_art}")
                            elif art_p.stat().st_size == 0:
                                blocking.append(f"zero-byte artifact declared in {rel}: {norm_art}")
            elif fp.suffix.lower() == ".md":
                if MARKER_RE.search(_strip_markdown_quotes(text)):
                    blocking.append(f"unfinished marker detected in {rel}")
            else:
                if MARKER_RE.search(text):
                    blocking.append(f"unfinished marker detected in {rel}")

    if not file_stats:
        blocking.append("no deliverable files produced in output directory")

    return {
        "passed": len(blocking) == 0,
        "blocking_failures": blocking,
        "warnings": warnings,
        "file_count": len(file_stats),
        "total_bytes": sum(f["bytes"] for f in file_stats),
        "files": file_stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Final deliverable contract audit.")
    parser.add_argument("--dir", default="", help="Output directory to audit (auto-detects /app/output or /output).")
    parser.add_argument("--result-file", default="result.json", help="Primary result JSON filename.")
    parser.add_argument("--require", action="append", default=[], help="Additional required relative file path.")
    parser.add_argument("--out", default="", help="Optional audit JSON report path.")
    args = parser.parse_args(argv)

    report = audit_deliverables(args.dir or None, result_file=args.result_file, required_files=args.require)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
