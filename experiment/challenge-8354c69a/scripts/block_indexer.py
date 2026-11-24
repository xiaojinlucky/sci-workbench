"""M4: Token-budget-aware structured document, table, and citation block indexer."""

from __future__ import annotations

import argparse
import csv
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TEX_SEC_RE = re.compile(r"^\\(?:sub)*section\*?\{(.+?)\}")
CAPTION_RE = re.compile(r"^\s*(?:Figure|Fig\.?|Table|Supplementary\s+(?:Figure|Table)|图|表)\s*\d+[:.\s-].*", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?\b", re.I)
TEXT_EXTS = {".md", ".txt", ".tex", ".rst", ".log", ".yaml", ".yml"}
TABULAR_EXTS = {".csv", ".tsv"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _est_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _index_text_lines(lines: list[str], rel: str, max_block_chars: int = 3200) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    title = "(preamble)"
    start = 1
    buf: list[str] = []

    def flush(end_line: int) -> None:
        nonlocal buf
        joined = "\n".join(buf).strip()
        if not joined:
            buf = []
            return
        chunks = [joined[i:i + max_block_chars] for i in range(0, len(joined), max_block_chars)] or [""]
        for idx, chunk in enumerate(chunks):
            captions = [ln.strip()[:160] for ln in chunk.splitlines() if CAPTION_RE.match(ln)]
            dois = sorted(set(DOI_RE.findall(chunk)))[:8]
            arxivs = sorted(set(ARXIV_RE.findall(chunk)))[:8]
            heading_tag = title if len(chunks) == 1 else f"{title} [part {idx + 1}/{len(chunks)}]"
            blocks.append({
                "file": rel,
                "kind": "text_section",
                "title": heading_tag,
                "start_line": start,
                "end_line": end_line,
                "char_len": len(chunk),
                "est_tokens": _est_tokens(chunk),
                "captions": captions[:5],
                "identifiers": dois + arxivs,
                "preview": chunk[:260].replace("\n", " "),
            })
        buf = []

    for lineno, raw in enumerate(lines, start=1):
        m_md = HEADING_RE.match(raw)
        m_tex = TEX_SEC_RE.match(raw.strip())
        if m_md or m_tex:
            flush(lineno - 1)
            title = (m_md.group(2) if m_md else m_tex.group(1)).strip()
            start = lineno
            buf.append(raw)
        else:
            buf.append(raw)
    flush(len(lines))
    return blocks


def _index_tabular(path: Path, rel: str) -> list[dict[str, Any]]:
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not raw:
        return [{"file": rel, "kind": "table", "rows": 0, "columns": []}]
    reader = csv.reader(raw, delimiter=delim)
    rows = list(reader)
    header = rows[0] if rows else []
    data_rows = rows[1:]
    null_counts = {h: 0 for h in header}
    for row in data_rows:
        for i, h in enumerate(header):
            if i >= len(row) or str(row[i]).strip() in ("", "NA", "null", "None", "nan"):
                null_counts[h] += 1
    summary = json.dumps({"cols": header, "rows": len(data_rows)}, ensure_ascii=False)
    return [{
        "file": rel,
        "kind": "table",
        "title": f"table:{path.name}",
        "start_line": 1,
        "end_line": len(raw),
        "rows": len(data_rows),
        "columns": header[:60],
        "null_counts": {k: v for k, v in null_counts.items() if v > 0},
        "sample_rows": data_rows[:3],
        "est_tokens": _est_tokens(summary),
        "identifiers": [],
        "preview": f"rows={len(data_rows)} cols={header[:10]}",
    }]


def _index_json(path: Path, rel: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        obj = json.loads(text)
    except Exception as exc:
        return [{"file": rel, "kind": "json_invalid", "error": str(exc), "est_tokens": 20}]
    if isinstance(obj, dict):
        keys = list(obj.keys())[:40]
        preview = f"object keys={keys[:12]}"
    elif isinstance(obj, list):
        keys = []
        preview = f"array len={len(obj)}"
    else:
        keys = []
        preview = f"scalar type={type(obj).__name__}"
    return [{
        "file": rel,
        "kind": "json_summary",
        "title": f"json:{path.name}",
        "start_line": 1,
        "end_line": len(text.splitlines()),
        "top_keys": keys,
        "bytes": len(text.encode("utf-8")),
        "est_tokens": _est_tokens(preview),
        "identifiers": sorted(set(DOI_RE.findall(text) + ARXIV_RE.findall(text)))[:10],
        "preview": preview,
    }]


def index_path(target: str | Path, *, max_files: int = 100) -> dict[str, Any]:
    """Build a compact structured block index for a file or directory."""
    root = Path(target)
    paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    blocks: list[dict[str, Any]] = []
    skipped: list[str] = []
    for fpath in paths[:max_files]:
        rel = fpath.name if root.is_file() else fpath.relative_to(root).as_posix()
        ext = fpath.suffix.lower()
        if ext in TEXT_EXTS:
            lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            blocks.extend(_index_text_lines(lines, rel))
        elif ext in {".html", ".htm", ".xml"}:
            parser = _TextExtractor()
            parser.feed(fpath.read_text(encoding="utf-8", errors="replace"))
            blocks.extend(_index_text_lines(parser.parts, rel))
        elif ext in TABULAR_EXTS:
            blocks.extend(_index_tabular(fpath, rel))
        elif ext == ".json":
            blocks.extend(_index_json(fpath, rel))
        else:
            skipped.append(rel)
    return {
        "root": str(root),
        "total_blocks": len(blocks),
        "total_est_tokens": sum(int(b.get("est_tokens", 0)) for b in blocks),
        "skipped_files": skipped[:40],
        "blocks": blocks,
    }


def query_blocks(index: dict[str, Any], keywords: list[str], *, max_tokens: int = 2400) -> list[dict[str, Any]]:
    """Select top keyword-matching blocks while staying inside max_tokens budget."""
    terms = [k.lower().strip() for k in keywords if k.strip()]
    scored: list[tuple[int, dict[str, Any]]] = []
    for blk in index.get("blocks", []):
        hay = " ".join([
            str(blk.get("title", "")),
            str(blk.get("preview", "")),
            " ".join(blk.get("captions", [])),
            " ".join(blk.get("columns", [])),
        ]).lower()
        hits = sum(hay.count(t) for t in terms) if terms else 1
        if hits > 0:
            scored.append((hits, blk))
    scored.sort(key=lambda pair: (-pair[0], int(pair[1].get("est_tokens", 1))))

    picked: list[dict[str, Any]] = []
    budget = 0
    for _, blk in scored:
        toks = int(blk.get("est_tokens", 1))
        if picked and budget + toks > max_tokens:
            continue
        picked.append(blk)
        budget += toks
    return picked


def slice_lines(path: str | Path, start_line: int, end_line: int, *, max_chars: int = 8000) -> str:
    """Read an exact inclusive line slice capped to max_chars."""
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    lo = max(1, int(start_line))
    hi = min(len(lines), int(end_line))
    snippet = "\n".join(lines[lo - 1:hi])
    return snippet[:max_chars]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Token-budget-aware document/table block indexer.")
    parser.add_argument("--scan", default="", help="File or directory to index.")
    parser.add_argument("--query", default="", help="Comma-separated keywords to filter blocks.")
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--out", default="", help="Optional JSON output path.")
    args = parser.parse_args(argv)

    if not args.scan:
        parser.print_help()
        return 2
    idx = index_path(args.scan)
    out_obj: Any = idx
    if args.query:
        kws = [k for k in args.query.split(",") if k.strip()]
        out_obj = {"query": kws, "matched": query_blocks(idx, kws, max_tokens=args.max_tokens)}
    encoded = json.dumps(out_obj, ensure_ascii=False, indent=2)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
