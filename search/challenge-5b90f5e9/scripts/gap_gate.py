"""M5: Grounding provenance verifier and honest-degradation gap ledger."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?\b", re.I)


def load_gaps(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("gaps"), list):
        return raw["gaps"]
    return []


def record_gap(
    path: str | Path,
    *,
    code: str,
    message: str,
    severity: str = "warning",
    source: str = "pipeline",
    fallback: str = "",
) -> list[dict[str, Any]]:
    """Append a structured gap record atomically and avoid duplicates."""
    p = Path(path)
    gaps = load_gaps(p)
    entry = {
        "code": code,
        "severity": severity,
        "source": source,
        "message": message,
        "fallback": fallback,
    }
    if entry not in gaps:
        gaps.append(entry)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"gaps": gaps}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return gaps


def _extract_pdf_text(pdf_path: Path) -> str:
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            return "\n".join((pg.extract_text() or "") for pg in doc.pages)
    except Exception:
        pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception:
        pass
    bin_p = shutil.which("pdftotext")
    if bin_p:
        try:
            proc = subprocess.run([bin_p, str(pdf_path), "-"], capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20)
            if proc.returncode == 0:
                return proc.stdout
        except Exception:
            pass
    return ""


def _collect_corpus_text(corpus_paths: list[Path]) -> str:
    chunks: list[str] = []
    text_exts = {".md", ".txt", ".json", ".csv", ".tsv", ".tex", ".xml", ".html", ".htm", ".yaml", ".yml", ".ris", ".bib", ".nbib", ".enw"}
    for cp in corpus_paths:
        if cp.is_file():
            if cp.suffix.lower() == ".pdf":
                chunks.append(_extract_pdf_text(cp))
            else:
                chunks.append(cp.read_text(encoding="utf-8", errors="ignore"))
        elif cp.is_dir():
            for fpath in sorted(cp.rglob("*")):
                if not fpath.is_file():
                    continue
                suf = fpath.suffix.lower()
                if suf in text_exts:
                    chunks.append(fpath.read_text(encoding="utf-8", errors="ignore"))
                elif suf == ".pdf":
                    pdf_txt = _extract_pdf_text(fpath)
                    if pdf_txt:
                        chunks.append(pdf_txt)
    return "\n".join(chunks)


def check_grounding(
    result_obj: Any,
    corpus_paths: list[str | Path],
    *,
    strict_identifiers: bool = True,
) -> dict[str, Any]:
    """Verify DOIs, arXiv IDs, and explicit numeric provenance entries against source inputs."""
    text_blob = json.dumps(result_obj, ensure_ascii=False)
    corpus = _collect_corpus_text([Path(p) for p in corpus_paths]).lower()

    dois = sorted(set(DOI_RE.findall(text_blob)))
    arxivs = sorted(set(ARXIV_RE.findall(text_blob)))
    ungrounded_ids: list[str] = []
    if corpus and strict_identifiers:
        for ident in dois + arxivs:
            norm_id = ident.lower().replace(" ", "")
            if norm_id not in corpus.replace(" ", ""):
                ungrounded_ids.append(ident)

    ungrounded_metrics: list[str] = []
    if isinstance(result_obj, dict):
        metrics = result_obj.get("metrics")
        provenance = result_obj.get("metric_provenance")
        if isinstance(metrics, dict) and isinstance(provenance, dict):
            for k in metrics:
                if k not in provenance:
                    ungrounded_metrics.append(str(k))

    gaps: list[dict[str, str]] = []
    for uid in ungrounded_ids:
        gaps.append({
            "code": "UNGROUNDED_IDENTIFIER",
            "severity": "error",
            "source": "gap_gate",
            "message": f"Identifier not found in source corpus: {uid}",
            "fallback": "drop_or_mark_unverified",
        })
    for um in ungrounded_metrics:
        gaps.append({
            "code": "MISSING_METRIC_PROVENANCE",
            "severity": "warning",
            "source": "gap_gate",
            "message": f"Metric missing provenance mapping: {um}",
            "fallback": "attach_source_or_script_trace",
        })

    return {
        "ok": len(ungrounded_ids) == 0,
        "checked_dois": len(dois),
        "checked_arxivs": len(arxivs),
        "ungrounded_identifiers": ungrounded_ids,
        "ungrounded_metrics": ungrounded_metrics,
        "gaps": gaps,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provenance grounding and gap ledger gate.")
    parser.add_argument("--verify-json", default="", help="Result or report JSON file to audit.")
    parser.add_argument("--corpus", action="append", default=[], help="Source input directory or file.")
    parser.add_argument("--gaps-out", default="", help="Optional gaps.json path.")
    args = parser.parse_args(argv)

    if not args.verify_json:
        parser.print_help()
        return 2
    payload = json.loads(Path(args.verify_json).read_text(encoding="utf-8"))
    report = check_grounding(payload, args.corpus or [])
    if args.gaps_out:
        p = Path(args.gaps_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"gaps": report["gaps"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
