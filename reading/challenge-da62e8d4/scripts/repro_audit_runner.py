"""Reproducibility Audit, Lockfile Drift Doctor, Deep AST Parameter Reconciliation & Isolated Multi-Run Runner (challenge-da62e8d4).

Pure Python 3.12 standard library baseline (zero mandatory third-party packages).
Produces:
- /app/output/result.json
- /app/output/reproduction_audit_report.json
- /app/output/reproduction_report.md

Key capabilities:
1. Integrates env_lock_doctor.py (Conda #15581/PR #15838 mixed-source drift, unpinned deps,
   declared-vs-installed runtime drift, research-repo-doctor executable checklist).
2. Deep AST parameter & seed hygiene inspection: top-level constants (including negative & annotated),
   function defaults, argparse.add_argument defaults, config dicts, dataclass defaults, and RNG seed calls.
   Produces 4-state reconciliation: matched / paper_code_conflict / paper_only_missing_in_code / code_only_undocumented.
3. Isolated multi-run execution with PYTHONHASHSEED injection, command/exit/timing/file SHA-256 capture,
   repeat-run determinism/jitter verification, and strict exclusion of failed runs from tolerance counts.
4. A priori tolerance verification (no post-hoc relaxation) with evidence-backed 5-axis discrepancy attribution
   and an explicit 4-tier evidence ladder (code_present -> clean_exit -> within_prior_tolerance -> scientific_claim_supported).
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import Any

import budget_guard
import env_lock_doctor
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log, sha256_file

DISCREPANCY_AXES = (
    "environment_or_seed",
    "parameter_mismatch",
    "data_or_split_difference",
    "implementation_difference",
    "paper_underspecification",
)

STOCHASTIC_CALL_NAMES = {
    "random.random", "random.uniform", "random.gauss", "random.normalvariate",
    "random.randint", "random.randrange", "random.choice", "random.choices",
    "random.shuffle", "random.sample",
    "np.random.rand", "np.random.randn", "np.random.normal", "np.random.uniform",
    "np.random.randint", "np.random.choice", "numpy.random.rand", "numpy.random.randn",
    "numpy.random.normal", "torch.rand", "torch.randn", "torch.randint",
}

SEED_SETTER_NAMES = {
    "random.seed", "np.random.seed", "numpy.random.seed", "np.random.default_rng",
    "numpy.random.default_rng", "default_rng", "torch.manual_seed",
    "torch.cuda.manual_seed", "torch.cuda.manual_seed_all", "tf.random.set_seed",
}

JSON_LINE_RE = re.compile(r"\{.*\}")
IGNORE_CODE_ONLY_KEYS = {"__name__", "__version__", "version", "help", "description", "out", "output", "input", "path", "file"}


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")


def _extract_literal(node: ast.AST) -> tuple[bool, Any]:
    """Safely extract scalar or small literal values from an AST expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool, type(None))):
        return True, node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        ok, inner = _extract_literal(node.operand)
        if ok and isinstance(inner, (int, float)) and not isinstance(inner, bool):
            return True, (-inner if isinstance(node.op, ast.USub) else +inner)
    if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) <= 12:
        items = []
        for el in node.elts:
            ok, v = _extract_literal(el)
            if not ok:
                return False, None
            items.append(v)
        return True, items
    return False, None


def _dotted_call_name(func: ast.AST) -> str:
    parts: list[str] = []
    cur = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def capture_env_snapshot() -> dict[str, Any]:
    """Capture Python runtime and installed package versions even under `python -S -B` isolated mode."""
    pkgs: dict[str, str] = {"python": platform.python_version()}
    target_pkgs = (
        "numpy", "pandas", "scipy", "scikit-learn", "torch", "matplotlib",
        "sympy", "pdfplumber", "pypdf", "pip", "setuptools", "wheel",
    )

    # 1. Inspect current sys.path via importlib.metadata
    for pkg in target_pkgs:
        try:
            pkgs[pkg] = importlib.metadata.version(pkg)
        except Exception:
            pass

    # 2. Under `python -S`, site-packages is omitted from sys.path; probe standard site-packages metadata read-only
    search_dirs: list[str] = []
    for base in {sys.prefix, sys.base_prefix}:
        if not base:
            continue
        bp = Path(base)
        for rel in (
            Path("Lib") / "site-packages",
            Path("lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
            Path("lib64") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
        ):
            cand = bp / rel
            if cand.is_dir():
                s = str(cand)
                if s not in search_dirs:
                    search_dirs.append(s)

    total_dists_seen = 0
    if search_dirs:
        try:
            target_low = {p.lower(): p for p in target_pkgs}
            for dist in importlib.metadata.distributions(path=search_dirs):
                total_dists_seen += 1
                name = (dist.metadata.get("Name") or "").strip() if dist.metadata else ""
                ver = (dist.version or "").strip()
                if not name or not ver:
                    continue
                low = name.lower().replace("_", "-")
                if low in target_low and target_low[low] not in pkgs:
                    pkgs[target_low[low]] = ver
                elif len(pkgs) < 25 and low not in {k.lower() for k in pkgs}:
                    pkgs[name] = ver
        except Exception:
            pass

    # 3. Record loaded stdlib/module versions from sys.modules as guaranteed non-empty baseline
    for mname, mod in list(sys.modules.items()):
        if not mod or "." in mname or mname.startswith("_"):
            continue
        mver = getattr(mod, "__version__", None) or getattr(mod, "ZLIB_VERSION", None)
        if isinstance(mver, str) and mver.strip() and mname not in pkgs and len(pkgs) < 30:
            pkgs[mname] = mver.strip()
    if "zlib" not in pkgs:
        pkgs["zlib"] = getattr(zlib, "ZLIB_VERSION", "stdlib")

    return {
        "python_version": platform.python_version(),
        "python_full_version": sys.version.splitlines()[0],
        "platform": sys.platform,
        "machine": platform.machine(),
        "cpu_count": os.cpu_count() or 1,
        "pythonhashseed": os.environ.get("PYTHONHASHSEED", "unset"),
        "site_packages_probed": bool(search_dirs),
        "installed_distributions_scanned": total_dists_seen,
        "packages": dict(sorted(pkgs.items())),
    }


# ---------------------------------------------------------------------------
# Paper Prose Indexing & Exact Locator/Quote Anchoring
# ---------------------------------------------------------------------------

_PAPER_SKIP_NAMES = {
    "case.json", "task.md", "requirements.txt", "environment.yml", "environment.yaml",
    "pyproject.toml", "conda-lock.yml", "uv.lock", "poetry.lock", "dockerfile",
}
_PAGE_MARKER_RE = re.compile(r"(?:\[\s*Page\s*(\d+)\s*\]|^#+\s*\[?\s*Page\s*(\d+)\s*\]?|\bPage\s*(\d+)\b)", re.I)
_SECTION_HEAD_RE = re.compile(
    r"^(?:#{1,4}\s+(.+)|((?:ABSTRACT|INTRODUCTION|METHOD|METHODS|MATERIALS AND METHODS|RESULTS|DISCUSSION|CONCLUSION|SUPPLEMENTAL INFORMATION|PROTOCOL|BENCHMARK)[A-Z0-9 :\-]*))$"
)
_TABLE_FIG_EQ_RE = re.compile(r"\b(Table\s+\d+|Figure\s+\d+|Fig\.\s*\d+|Equation\s+\d+|Eq\.\s*\(?\d+\)?|Chart\s+\d+)\b", re.I)

_PARAM_METRIC_ALIASES: dict[str, list[str]] = {
    "learning_rate": ["learning rate", "lr", "step size"],
    "batch_size": ["batch size", "minibatch"],
    "regularization": ["regularization", "weight decay", "l2", "ridge", "lambda", "alpha"],
    "ridge_alpha": ["ridge", "alpha", "l2 penalty", "regularization"],
    "significance_level": ["significance level", "5% significance", "p<0.05", "alpha"],
    "confidence_level": ["95% confidence interval", "95%ci", "confidence interval"],
    "rate_multiplier": ["10,000 inhabitants", "per 10,000", "x10,000", "10000"],
    "study_years": ["2007 to 2015", "2007-2015", "2007–2015", "study period"],
    "mean_annual_cases": ["average of", "per year", "615.44", "5,539"],
    "medicine_pct": ["medicine", "medicines", "28.5", "1,580"],
    "girls_pct": ["girls", "53.1", "2,944"],
    "accidental_pct": ["accidental", "18.2", "1,010"],
    "apc_both_sexes": ["annual percent change", "apc", "12.7", "both"],
    "train_ratio": ["train", "80%", "0.8", "split"],
    "concrete_r2": ["r2", "r²", "coefficient of determination", "compressive strength", "ols"],
    "concrete_rmse_mpa": ["rmse", "mpa", "compressive strength", "root mean squared error"],
}


def _decode_pdf_literal_string(raw_bytes: bytes) -> str:
    """Decode PDF literal string escape sequences (\\n, \\r, \\t, \\(, \\), \\\\, octal \\ddd)."""
    out = bytearray()
    i = 0
    n = len(raw_bytes)
    while i < n:
        ch = raw_bytes[i]
        if ch == 92 and i + 1 < n:  # backslash
            nxt = raw_bytes[i + 1]
            if nxt in (110, 114, 116, 98, 102, 40, 41, 92):
                esc_map = {110: 10, 114: 13, 116: 9, 98: 8, 102: 12, 40: 40, 41: 41, 92: 92}
                out.append(esc_map[nxt])
                i += 2
                continue
            if nxt in (10, 13):  # line continuation
                i += 2
                if nxt == 13 and i < n and raw_bytes[i] == 10:
                    i += 1
                continue
            if 48 <= nxt <= 55:  # octal \ddd
                oct_digits = bytes([nxt])
                j = i + 2
                while j < n and len(oct_digits) < 3 and 48 <= raw_bytes[j] <= 55:
                    oct_digits += bytes([raw_bytes[j]])
                    j += 1
                out.append(int(oct_digits, 8) & 0xFF)
                i = j
                continue
            out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    txt = out.decode("cp1252", errors="replace")
    # Common ligature control bytes in SciELO/LaTeX Type1 fonts
    return txt.replace("\x1f", "fi").replace("\x1e", "fl").replace("\x00", "")


def _extract_text_from_pdf_stream(content: bytes) -> str:
    """Extract readable lines from a decompressed PDF page content stream using stdlib only."""
    lines: list[str] = []
    cur_parts: list[str] = []
    # Tokenize BT..ET blocks or direct Tj/TJ operators
    token_re = re.compile(
        rb"(\((?:\\.|[^()\\])*\))\s*(?:Tj|'|\"|\s+Tj)"
        rb"|\[(.*?)\]\s*TJ"
        rb"|(T\*|TD|Td|Tm|ET)",
        re.S,
    )
    arr_elem_re = re.compile(rb"(\((?:\\.|[^()\\])*\))|([-+]?\d+(?:\.\d+)?)")
    for m in token_re.finditer(content):
        if m.group(1) is not None:
            s = _decode_pdf_literal_string(m.group(1)[1:-1])
            if s:
                cur_parts.append(s)
        elif m.group(2) is not None:
            seg: list[str] = []
            for am in arr_elem_re.finditer(m.group(2)):
                if am.group(1) is not None:
                    seg.append(_decode_pdf_literal_string(am.group(1)[1:-1]))
                elif am.group(2) is not None:
                    try:
                        kern = float(am.group(2))
                        if kern < -140.0:
                            seg.append(" ")
                    except ValueError:
                        pass
            if seg:
                cur_parts.append("".join(seg))
        elif m.group(3) is not None:
            joined = "".join(cur_parts).strip()
            if joined:
                lines.append(joined)
            cur_parts = []
    if cur_parts:
        joined = "".join(cur_parts).strip()
        if joined:
            lines.append(joined)
    return "\n".join(lines)


def _stdlib_zlib_pdf_extract(pdf_path: Path) -> tuple[list[tuple[int, str]], str]:
    """Pure Python stdlib zlib fallback PDF text extractor preserving page numbers."""
    data = pdf_path.read_bytes()
    if not data.startswith(b"%PDF"):
        return [], "not_a_pdf_header"

    # Parse indirect objects: N gen obj ... endobj
    obj_map: dict[int, bytes] = {}
    for om in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)\bendobj", data, re.S):
        obj_map[int(om.group(1))] = om.group(2)

    def _decompress_obj(obj_body: bytes) -> bytes:
        sm = re.search(rb"stream\r?\n(.*?)\r?\nendstream", obj_body, re.S)
        if not sm:
            return b""
        raw_s = sm.group(1)
        if b"FlateDecode" in obj_body[: obj_body.find(b"stream")]:
            try:
                return zlib.decompress(raw_s)
            except Exception:
                try:
                    return zlib.decompress(raw_s, -zlib.MAX_WBITS)
                except Exception:
                    return b""
        return raw_s

    # Collect /Type /Page objects (excluding /Pages) in object order and resolve their /Contents
    pages_out: list[tuple[int, str]] = []
    page_objs = [
        (oid, body)
        for oid, body in sorted(obj_map.items())
        if re.search(rb"/Type\s*/Page\b(?!s)", body)
    ]
    for p_idx, (_, pbody) in enumerate(page_objs, start=1):
        c_refs: list[int] = []
        m_arr = re.search(rb"/Contents\s*\[([^\]]+)\]", pbody, re.S)
        if m_arr:
            c_refs = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", m_arr.group(1))]
        else:
            m_one = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", pbody)
            if m_one:
                c_refs = [int(m_one.group(1))]
        page_chunks: list[str] = []
        for cid in c_refs:
            cbody = obj_map.get(cid, b"")
            dec = _decompress_obj(cbody)
            if dec:
                txt = _extract_text_from_pdf_stream(dec)
                if txt.strip():
                    page_chunks.append(txt.strip())
        if page_chunks:
            pages_out.append((p_idx, "\n".join(page_chunks)))

    # Fallback: if /Type /Page references were compressed in ObjStm, scan all Flate streams directly
    if not pages_out:
        s_idx = 0
        for sm in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
            raw_s = sm.group(1)
            try:
                dec = zlib.decompress(raw_s)
            except Exception:
                dec = raw_s
            if b"BT" in dec and (b"Tj" in dec or b"TJ" in dec):
                txt = _extract_text_from_pdf_stream(dec)
                if len(txt.strip()) >= 20:
                    s_idx += 1
                    pages_out.append((s_idx, txt.strip()))

    return pages_out, "stdlib_zlib" if pages_out else "stdlib_zlib_empty"


def _pdf_text_with_diagnostics(pdf_path: Path) -> dict[str, Any]:
    """Extract PDF text with per-page [PAGE N] markers via pdfplumber/pypdf/pdftotext/stdlib-zlib and full diagnostics."""
    errors: list[str] = []
    # 1. pdfplumber
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            pages = [(i, (p.extract_text() or "").strip()) for i, p in enumerate(doc.pages, 1)]
        text = "\n\n".join(f"[PAGE {i}]\n{t}" for i, t in pages if t)
        if len(re.sub(r"\s+", "", text)) >= 20:
            return {"ok": True, "backend": "pdfplumber", "page_count": len(pages), "text": text, "errors": errors}
        errors.append("pdfplumber: extracted empty text")
    except Exception as exc:
        errors.append(f"pdfplumber: {type(exc).__name__}")

    # 2. pypdf
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        pages = [(i, (p.extract_text() or "").strip()) for i, p in enumerate(reader.pages, 1)]
        text = "\n\n".join(f"[PAGE {i}]\n{t}" for i, t in pages if t)
        if len(re.sub(r"\s+", "", text)) >= 20:
            return {"ok": True, "backend": "pypdf", "page_count": len(pages), "text": text, "errors": errors}
        errors.append("pypdf: extracted empty text")
    except Exception as exc:
        errors.append(f"pypdf: {type(exc).__name__}")

    # 3. pdftotext (split on form-feed \f to preserve per-page [PAGE N] markers!)
    bin_p = shutil.which("pdftotext")
    if bin_p:
        try:
            proc = subprocess.run(
                [bin_p, "-enc", "UTF-8", str(pdf_path), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raw_pages = [p.strip() for p in proc.stdout.split("\f")]
                pages = [(i, t) for i, t in enumerate(raw_pages, 1) if t]
                text = "\n\n".join(f"[PAGE {i}]\n{t}" for i, t in pages)
                if len(re.sub(r"\s+", "", text)) >= 20:
                    return {"ok": True, "backend": "pdftotext", "page_count": len(pages), "text": text, "errors": errors}
                errors.append("pdftotext: extracted empty text")
            else:
                errors.append(f"pdftotext: rc={proc.returncode}")
        except Exception as exc:
            errors.append(f"pdftotext: {type(exc).__name__}")
    else:
        errors.append("pdftotext: binary not found")

    # 4. Pure stdlib zlib fallback
    try:
        pages, note = _stdlib_zlib_pdf_extract(pdf_path)
        text = "\n\n".join(f"[PAGE {i}]\n{t}" for i, t in pages if t)
        if len(re.sub(r"\s+", "", text)) >= 20:
            return {"ok": True, "backend": "stdlib_zlib", "page_count": len(pages), "text": text, "errors": errors}
        errors.append(f"stdlib_zlib: {note}")
    except Exception as exc:
        errors.append(f"stdlib_zlib: {type(exc).__name__}: {exc}")

    return {
        "ok": False,
        "backend": "none",
        "page_count": 0,
        "text": "",
        "errors": errors,
    }


def _pdf_text_optional(pdf_path: Path) -> str:
    return str(_pdf_text_with_diagnostics(pdf_path).get("text") or "")


def _parse_text_into_anchored_blocks(raw: str, relpath: str) -> list[dict[str, Any]]:
    """Split prose text into paragraph blocks retaining Page, Section, Table/Figure/Eq, and line span locators."""
    blocks: list[dict[str, Any]] = []
    cur_page: str | None = None
    cur_section: str | None = None
    lines = raw.splitlines()
    buf: list[str] = []
    start_ln = 1

    def _flush(end_ln: int) -> None:
        if not buf:
            return
        text = " ".join(x.strip() for x in buf if x.strip()).strip()
        if not text:
            return
        tf_m = _TABLE_FIG_EQ_RE.search(text)
        loc_parts: list[str] = []
        if cur_page:
            loc_parts.append(f"Page {cur_page}")
        if cur_section:
            loc_parts.append(f"Section: {cur_section}")
        if tf_m:
            loc_parts.append(tf_m.group(1))
        loc_parts.append(f"L{start_ln}-L{end_ln}")
        blocks.append({
            "source_file": relpath,
            "locator": " / ".join(loc_parts),
            "page": cur_page,
            "section": cur_section,
            "table_or_eq": tf_m.group(1) if tf_m else None,
            "line_start": start_ln,
            "line_end": end_ln,
            "text": text,
        })

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            _flush(idx - 1)
            buf = []
            continue
        pm = _PAGE_MARKER_RE.search(stripped)
        if pm and (stripped.startswith("#") or stripped.upper().startswith("[PAGE")):
            _flush(idx - 1)
            buf = []
            cur_page = pm.group(1) or pm.group(2) or pm.group(3)
            continue
        sm = _SECTION_HEAD_RE.match(stripped)
        if sm:
            _flush(idx - 1)
            buf = []
            heading = (sm.group(1) or sm.group(2) or "").strip(" #:")
            if heading and not heading.lower().startswith("[page"):
                cur_section = heading[:60]
            continue
        if not buf:
            start_ln = idx
        buf.append(stripped)
    _flush(len(lines))
    return blocks


def collect_paper_blocks(
    input_dir: Path,
    case_data: dict[str, Any],
    pdf_diagnostics: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collect anchored prose blocks from case.json excerpts and all supplied MD/TXT/PDF/XML paper files."""
    blocks: list[dict[str, Any]] = []
    for idx, p in enumerate(case_data.get("paper_excerpts") or case_data.get("paragraphs") or [], start=1):
        if isinstance(p, dict):
            txt = str(p.get("text") or p.get("content") or "").strip()
            loc = str(p.get("locator") or p.get("paragraph_id") or f"P{idx:03d}")
            src = str(p.get("source_file") or "case.json")
        else:
            txt = str(p).strip()
            loc = f"P{idx:03d}"
            src = "case.json"
        if txt:
            blocks.append({"source_file": src, "locator": loc, "page": None, "text": txt})

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        low = fp.name.lower()
        if low in _PAPER_SKIP_NAMES or low.startswith("requirements"):
            continue
        ext = fp.suffix.lower()
        rel = fp.relative_to(input_dir).as_posix()
        if ext in {".md", ".txt"}:
            raw = fp.read_text(encoding="utf-8", errors="replace")
            blocks.extend(_parse_text_into_anchored_blocks(raw, rel))
        elif ext == ".pdf":
            diag = _pdf_text_with_diagnostics(fp)
            rec = {
                "file": rel,
                "sha256": sha256_file(fp),
                "bytes": fp.stat().st_size,
                "ok": diag["ok"],
                "backend": diag["backend"],
                "page_count": diag["page_count"],
                "extracted_chars": len(diag["text"]),
                "errors": diag["errors"],
            }
            if pdf_diagnostics is not None:
                pdf_diagnostics.append(rec)
            if diag["ok"] and diag["text"].strip():
                blocks.extend(_parse_text_into_anchored_blocks(diag["text"], rel))
        elif ext == ".xml":
            try:
                raw = fp.read_text(encoding="utf-8", errors="replace")
                if not re.search(r"<!(?:DOCTYPE|ENTITY)\b", raw, re.I):
                    root = ET.fromstring(raw)
                    p_idx = 0
                    for elem in root.iter():
                        tag = elem.tag.split("}", 1)[-1].lower()
                        if tag in ("p", "caption"):
                            txt = " ".join("".join(elem.itertext()).split()).strip()
                            if txt:
                                p_idx += 1
                                pid = elem.attrib.get("id") or f"{tag}{p_idx:03d}"
                                blocks.append({"source_file": rel, "locator": f"XML:{pid}", "page": None, "text": txt})
            except Exception:
                pass
    return blocks


def _value_search_forms(val: Any) -> list[str]:
    forms: list[str] = []
    if val is None or isinstance(val, bool):
        return forms
    if isinstance(val, (int, float)):
        fval = float(val)
        if fval.is_integer():
            iv = int(fval)
            forms.append(str(iv))
            if abs(iv) >= 1000:
                forms.append(f"{iv:,}")
        else:
            forms.append(f"{fval:g}")
            forms.append(f"{fval:.2f}")
            forms.append(f"{fval:.1f}")
            if 0 < fval < 1:
                pct = fval * 100.0
                if abs(pct - round(pct)) < 1e-6:
                    forms.append(f"{int(round(pct))}%")
                else:
                    forms.append(f"{pct:.1f}%")
    else:
        s = str(val).strip()
        if s:
            forms.append(s)
    dedup: list[str] = []
    for x in forms:
        if x and x not in dedup:
            dedup.append(x)
    return dedup


def _infer_value_origin(
    name: str,
    explicit_origins: dict[str, str] | None,
    anchor: dict[str, Any],
    *,
    is_code_only: bool = False,
) -> str:
    """Classify value provenance strictly into paper_reported / protocol_derived / observed."""
    if is_code_only:
        return "observed"
    if explicit_origins:
        for k, v in explicit_origins.items():
            if _norm_key(k) == _norm_key(name) and v in ("paper_reported", "protocol_derived", "observed"):
                return v
    src = str(anchor.get("paper_source_file") or "").lower()
    if "protocol" in src or "benchmark" in src or "readme" in src:
        return "protocol_derived"
    if src.endswith(".pdf") or anchor.get("paper_anchor_status") in ("anchored_exact", "anchored_context"):
        return "paper_reported"
    return "protocol_derived"


def bind_paper_anchor(
    name: str,
    value: Any,
    blocks: list[dict[str, Any]],
    *,
    is_code_only: bool = False,
    pdf_extraction_failed: bool = False,
) -> dict[str, Any]:
    """Bind a paper hyperparameter or target metric to its exact paper source file, page, locator, and verbatim quote."""
    if is_code_only:
        return {
            "paper_source_file": None,
            "paper_page": None,
            "paper_locator": None,
            "paper_quote": "",
            "paper_anchor_status": "not_applicable_code_only",
        }
    if not blocks:
        return {
            "paper_source_file": None,
            "paper_page": None,
            "paper_locator": None,
            "paper_quote": "",
            "paper_anchor_status": "unverified" if pdf_extraction_failed else "not_found_in_supplied_prose",
        }

    nk = _norm_key(name)
    name_terms: list[str] = []
    for t in [name, name.replace("_", " "), *(_PARAM_METRIC_ALIASES.get(nk) or [])]:
        tl = t.strip().lower()
        if tl and len(tl) >= 2 and tl not in name_terms:
            name_terms.append(tl)
    # Also include informative sub-words (>=3 chars)
    sub_words = [w for w in nk.split("_") if len(w) >= 3 and w not in {"pct", "mpa", "val", "num", "both", "sexes"}]
    val_forms = _value_search_forms(value)

    best_score = 0
    best_block: dict[str, Any] | None = None
    best_match_pos = 0
    best_status = "not_found_in_supplied_prose"

    for blk in blocks:
        txt = blk["text"]
        low = txt.lower()
        val_pos = -1
        for vf in val_forms:
            m = re.search(rf"(?<![0-9A-Za-z.]){re.escape(vf)}(?![0-9A-Za-z])", txt)
            if m:
                val_pos = m.start()
                break

        term_pos = -1
        term_hits = 0
        for nt in name_terms:
            p = low.find(nt)
            if p >= 0:
                term_hits += 1
                if term_pos < 0:
                    term_pos = p
        sub_hits = sum(1 for sw in sub_words if sw in low)

        score = 0
        status = "not_found_in_supplied_prose"
        mpos = 0
        if val_pos >= 0 and (term_hits > 0 or sub_hits > 0):
            score = 100 + term_hits * 15 + sub_hits * 5 + (10 if blk.get("table_or_eq") else 0)
            status = "anchored_exact"
            mpos = val_pos
        elif term_hits > 0:
            score = 55 + term_hits * 10
            status = "anchored_context"
            mpos = term_pos
        elif val_pos >= 0 and len(val_forms) > 0 and any(len(vf) >= 4 for vf in val_forms):
            score = 40 + (10 if blk.get("table_or_eq") else 0)
            status = "anchored_context"
            mpos = val_pos

        if score > best_score:
            best_score = score
            best_block = blk
            best_match_pos = mpos
            best_status = status

    if best_block is None or best_score <= 0:
        return {
            "paper_source_file": None,
            "paper_page": None,
            "paper_locator": None,
            "paper_quote": "",
            "paper_anchor_status": "unverified" if pdf_extraction_failed else "not_found_in_supplied_prose",
        }

    full_txt = best_block["text"]
    start = max(0, best_match_pos - 70)
    quote = full_txt[start:start + 220].strip()
    page_val = int(best_block["page"]) if str(best_block.get("page") or "").isdigit() else None
    return {
        "paper_source_file": best_block["source_file"],
        "paper_page": page_val,
        "paper_locator": best_block["locator"],
        "paper_quote": quote,
        "paper_anchor_status": best_status,
    }


def extract_code_parameters(py_path: Path, relpath: str) -> dict[str, Any]:
    """Deep AST inspection of constants, function defaults, argparse defaults, config dicts, dataclasses, and seed hygiene."""
    src = py_path.read_text(encoding="utf-8", errors="replace")
    extracted: list[dict[str, Any]] = []
    seed_calls: list[dict[str, Any]] = []
    stochastic_calls: list[dict[str, Any]] = []
    hashseed_referenced = "PYTHONHASHSEED" in src

    try:
        tree = ast.parse(src, filename=py_path.name)
    except SyntaxError as exc:
        return {
            "file": relpath,
            "sha256": sha256_file(py_path),
            "bytes": py_path.stat().st_size,
            "syntax_error": f"line {exc.lineno}: {exc.msg}",
            "extracted_parameters": [],
            "seed_hygiene": {
                "status": "syntax_error",
                "seed_calls": [],
                "stochastic_calls": [],
                "pythonhashseed_referenced": hashseed_referenced,
            },
        }

    def _record_param(name: str, val: Any, kind: str, qualified: str, line: int) -> None:
        if name.startswith("_") or val is None:
            return
        extracted.append({
            "name": name,
            "norm_name": _norm_key(name),
            "value": val,
            "source_kind": kind,
            "qualified_name": qualified,
            "line": line,
            "file": relpath,
        })

    # 1. Top-level assignments, annotated assignments, config dicts, functions, dataclasses
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            tname = node.targets[0].id
            ok, val = _extract_literal(node.value)
            if ok:
                _record_param(tname, val, "top_level_const", tname, node.lineno)
            elif isinstance(node.value, ast.Dict):
                for k_node, v_node in zip(node.value.keys, node.value.values):
                    if isinstance(k_node, ast.Constant) and isinstance(k_node.value, str):
                        v_ok, v_val = _extract_literal(v_node)
                        if v_ok:
                            _record_param(k_node.value, v_val, "config_dict", f"{tname}[{k_node.value!r}]", k_node.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            ok, val = _extract_literal(node.value)
            if ok:
                _record_param(node.target.id, val, "top_level_annotated_const", node.target.id, node.lineno)
        elif isinstance(node, ast.FunctionDef):
            arg_names = [a.arg for a in node.args.args]
            def_vals = node.args.defaults
            if def_vals:
                offset = len(arg_names) - len(def_vals)
                for aname, dval in zip(arg_names[offset:], def_vals):
                    ok, val = _extract_literal(dval)
                    if ok:
                        _record_param(aname, val, "function_default", f"{node.name}({aname})", dval.lineno)
            for kw_arg, kw_def in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if kw_def is not None:
                    ok, val = _extract_literal(kw_def)
                    if ok:
                        _record_param(kw_arg.arg, val, "function_kw_default", f"{node.name}({kw_arg.arg})", kw_def.lineno)
        elif isinstance(node, ast.ClassDef):
            is_dc = any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                or (isinstance(d, ast.Call) and _dotted_call_name(d.func).endswith("dataclass"))
                for d in node.decorator_list
            )
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.value is not None:
                    ok, val = _extract_literal(item.value)
                    if ok:
                        _record_param(item.target.id, val, "dataclass_field" if is_dc else "class_attr", f"{node.name}.{item.target.id}", item.lineno)
                    elif isinstance(item.value, ast.Call) and _dotted_call_name(item.value.func).endswith("field"):
                        for kw in item.value.keywords:
                            if kw.arg == "default":
                                fok, fval = _extract_literal(kw.value)
                                if fok:
                                    _record_param(item.target.id, fval, "dataclass_field", f"{node.name}.{item.target.id}", item.lineno)

    # 2. Walk entire AST for argparse.add_argument, nested config dicts, seed setters, and stochastic RNG calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            cname = _dotted_call_name(node.func)
            # argparse add_argument
            if cname.endswith("add_argument"):
                opt_names = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                dest_name = None
                default_val_ok = False
                default_val = None
                for kw in node.keywords:
                    if kw.arg == "dest" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        dest_name = kw.value.value
                    elif kw.arg == "default":
                        default_val_ok, default_val = _extract_literal(kw.value)
                if default_val_ok:
                    chosen = dest_name
                    if not chosen:
                        long_opts = [o.lstrip("-") for o in opt_names if o.startswith("--")]
                        chosen = long_opts[0] if long_opts else (opt_names[0].lstrip("-") if opt_names else "")
                    if chosen:
                        _record_param(chosen.replace("-", "_"), default_val, "argparse_default", f"add_argument({chosen})", node.lineno)

            # Seed setters
            if cname in SEED_SETTER_NAMES or cname.endswith(".seed") or cname.endswith(".manual_seed") or cname.endswith("default_rng"):
                arg_preview = None
                is_none_or_dynamic = False
                if node.args:
                    ok, val = _extract_literal(node.args[0])
                    if ok:
                        arg_preview = val
                        if val is None:
                            is_none_or_dynamic = True
                    else:
                        arg_preview = ast.unparse(node.args[0]) if hasattr(ast, "unparse") else "<dynamic>"
                        if "time" in str(arg_preview).lower() or "urandom" in str(arg_preview).lower():
                            is_none_or_dynamic = True
                else:
                    is_none_or_dynamic = True
                seed_calls.append({
                    "call": cname,
                    "argument": arg_preview,
                    "line": node.lineno,
                    "effective_fixed_seed": not is_none_or_dynamic,
                })

            # Keyword seed= / random_state=
            for kw in node.keywords:
                if kw.arg in ("seed", "random_state"):
                    ok, val = _extract_literal(kw.value)
                    seed_calls.append({
                        "call": f"{cname}({kw.arg}=...)",
                        "argument": val if ok else "<expr>",
                        "line": node.lineno,
                        "effective_fixed_seed": (val is not None) if ok else True,
                    })

            # Stochastic RNG calls
            if cname in STOCHASTIC_CALL_NAMES:
                stochastic_calls.append({"call": cname, "line": node.lineno})

        # Config dicts inside functions (e.g. cfg = {"lr": 0.01, ...})
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            tname = node.targets[0].id
            if isinstance(node.value, ast.Dict) and any(tok in tname.lower() for tok in ("cfg", "config", "param", "hyper", "opt", "default", "setting")):
                if node not in tree.body:
                    for k_node, v_node in zip(node.value.keys, node.value.values):
                        if isinstance(k_node, ast.Constant) and isinstance(k_node.value, str):
                            v_ok, v_val = _extract_literal(v_node)
                            if v_ok:
                                _record_param(k_node.value, v_val, "local_config_dict", f"{tname}[{k_node.value!r}]", k_node.lineno)

    # Deduplicate extracted params by (name, qualified_name, line)
    dedup_params: list[dict[str, Any]] = []
    seen_sig: set[tuple[Any, ...]] = set()
    for ep in extracted:
        sig = (ep["norm_name"], ep["qualified_name"], ep["line"])
        if sig not in seen_sig:
            seen_sig.add(sig)
            dedup_params.append(ep)

    has_effective_seed = any(sc["effective_fixed_seed"] for sc in seed_calls)
    if stochastic_calls and not has_effective_seed:
        seed_status = "unseeded_stochastic_risk"
    elif has_effective_seed:
        seed_status = "seeded"
    else:
        seed_status = "deterministic_no_rng"

    return {
        "file": relpath,
        "sha256": sha256_file(py_path),
        "bytes": py_path.stat().st_size,
        "extracted_parameters": dedup_params,
        "seed_hygiene": {
            "status": seed_status,
            "seed_calls": seed_calls,
            "stochastic_calls": stochastic_calls,
            "pythonhashseed_referenced": hashseed_referenced,
        },
    }


def _values_equal(v1: Any, v2: Any) -> bool:
    if isinstance(v1, bool) or isinstance(v2, bool):
        return bool(v1) is bool(v2) and type(v1) is type(v2)
    if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
        return abs(float(v1) - float(v2)) <= 1e-12
    return str(v1).strip().lower() == str(v2).strip().lower()


def reconcile_parameters(
    paper_params: dict[str, Any],
    code_inspects: list[dict[str, Any]],
    paper_blocks: list[dict[str, Any]] | None = None,
    *,
    param_origins: dict[str, str] | None = None,
    pdf_extraction_failed: bool = False,
) -> list[dict[str, Any]]:
    """Reconcile paper hyperparameters against all inspected scripts with exact paper prose anchors and value_origin."""
    blocks = paper_blocks or []
    by_norm: dict[str, list[dict[str, Any]]] = {}
    for ci in code_inspects:
        for ep in ci.get("extracted_parameters") or []:
            by_norm.setdefault(ep["norm_name"], []).append(ep)

    table: list[dict[str, Any]] = []
    paper_norms: set[str] = set()

    for pkey, pval in paper_params.items():
        nk = _norm_key(pkey)
        paper_norms.add(nk)
        anchor = bind_paper_anchor(pkey, pval, blocks, is_code_only=False, pdf_extraction_failed=pdf_extraction_failed)
        origin = _infer_value_origin(pkey, param_origins, anchor, is_code_only=False)
        matches = by_norm.get(nk) or []
        if not matches:
            table.append({
                "parameter": pkey,
                "paper_value": pval,
                "value_origin": origin,
                "code_value": None,
                "code_source": None,
                "source_kind": None,
                "provenance_status": "paper_only_missing_in_code",
                **anchor,
            })
            continue
        # Record one row per candidate file where this parameter appears so conflicts in any candidate are never masked
        by_file: dict[str, list[dict[str, Any]]] = {}
        for m in matches:
            by_file.setdefault(m["file"], []).append(m)
        for fname, entries in by_file.items():
            # Prefer argparse/config_dict/dataclass over plain const if multiple
            priority = {"argparse_default": 0, "config_dict": 1, "local_config_dict": 2, "dataclass_field": 3, "function_default": 4, "function_kw_default": 5, "top_level_const": 6, "top_level_annotated_const": 7, "class_attr": 8}
            chosen = sorted(entries, key=lambda e: (priority.get(e["source_kind"], 9), e["line"]))[0]
            equal = _values_equal(pval, chosen["value"])
            table.append({
                "parameter": pkey,
                "paper_value": pval,
                "value_origin": origin,
                "code_value": chosen["value"],
                "code_value_origin": "observed",
                "code_source": fname,
                "qualified_name": chosen["qualified_name"],
                "source_kind": chosen["source_kind"],
                "line": chosen["line"],
                "provenance_status": "matched" if equal else "paper_code_conflict",
                **anchor,
            })

    # Record code_only_undocumented parameters
    seen_undoc: set[tuple[str, str]] = set()
    for ci in code_inspects:
        for ep in ci.get("extracted_parameters") or []:
            nk = ep["norm_name"]
            if nk in paper_norms or nk in IGNORE_CODE_ONLY_KEYS:
                continue
            # Filter out obvious non-hyperparameter uppercase string constants unless numeric/bool or from config/argparse/dataclass
            if ep["source_kind"] in ("top_level_const", "top_level_annotated_const") and isinstance(ep["value"], str):
                continue
            sig = (ci["file"], nk)
            if sig in seen_undoc:
                continue
            seen_undoc.add(sig)
            table.append({
                "parameter": ep["name"],
                "paper_value": None,
                "value_origin": "observed",
                "code_value": ep["value"],
                "code_value_origin": "observed",
                "code_source": ci["file"],
                "qualified_name": ep["qualified_name"],
                "source_kind": ep["source_kind"],
                "line": ep["line"],
                "provenance_status": "code_only_undocumented",
                **bind_paper_anchor(ep["name"], None, blocks, is_code_only=True),
            })
    return table


def _parse_metrics_from_stdout(stdout: str) -> dict[str, Any]:
    parsed_metrics: dict[str, Any] = {}
    try:
        obj = json.loads(stdout.strip())
        if isinstance(obj, dict):
            parsed_metrics = obj
    except Exception:
        for line in reversed(stdout.splitlines()):
            m = JSON_LINE_RE.search(line)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    if isinstance(obj, dict):
                        parsed_metrics = obj
                        break
                except Exception:
                    pass
        if not parsed_metrics:
            for km in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", stdout):
                parsed_metrics[km.group(1)] = float(km.group(2))
    return parsed_metrics


def execute_candidate_once(
    script_path: Path,
    relpath: str,
    cwd: Path,
    *,
    args: list[str] | None = None,
    hashseed: str = "0",
    timeout_sec: int = 30,
    repeat_index: int = 1,
) -> dict[str, Any]:
    """Run candidate numerical script in an isolated directory with PYTHONHASHSEED injected."""
    cwd.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(hashseed)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, "-B", str(script_path), *(args or [])]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        elapsed = round(time.monotonic() - t0, 4)
        out_files = []
        for ofp in sorted(p for p in cwd.rglob("*") if p.is_file()):
            out_files.append({
                "rel": ofp.relative_to(cwd).as_posix(),
                "bytes": ofp.stat().st_size,
                "sha256": sha256_file(ofp),
            })
        parsed_metrics = _parse_metrics_from_stdout(proc.stdout) if proc.returncode == 0 else {}
        return {
            "script": relpath,
            "repeat_index": repeat_index,
            "command": [Path(cmd[0]).name, "-B", relpath, *(args or [])],
            "pythonhashseed": str(hashseed),
            "input_sha256": sha256_file(script_path),
            "returncode": proc.returncode,
            "elapsed_sec": elapsed,
            "status": "executed_ok" if proc.returncode == 0 else "execution_failed",
            "parsed_metrics": parsed_metrics,
            "output_files": out_files,
            "stdout_tail": proc.stdout[-800:],
            "stderr_tail": proc.stderr[-800:],
        }
    except subprocess.TimeoutExpired:
        return {
            "script": relpath,
            "repeat_index": repeat_index,
            "command": [Path(cmd[0]).name, "-B", relpath, *(args or [])],
            "pythonhashseed": str(hashseed),
            "input_sha256": sha256_file(script_path),
            "returncode": -1,
            "elapsed_sec": timeout_sec,
            "status": "timeout",
            "parsed_metrics": {},
            "output_files": [],
            "stdout_tail": "",
            "stderr_tail": f"Timed out after {timeout_sec}s",
        }


def execute_candidate_with_repeats(
    script_path: Path,
    relpath: str,
    work_dir: Path,
    *,
    args: list[str] | None = None,
    hashseed: str = "0",
    repeats: int = 2,
    timeout_sec: int = 30,
) -> dict[str, Any]:
    """Execute a candidate script across isolated repeat runs to measure determinism/jitter."""
    runs: list[dict[str, Any]] = []
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", relpath)
    for r_idx in range(1, max(1, repeats) + 1):
        rcwd = work_dir / f"run_{safe_stem}_r{r_idx}"
        res = execute_candidate_once(
            script_path,
            relpath,
            rcwd,
            args=args,
            hashseed=hashseed,
            timeout_sec=timeout_sec,
            repeat_index=r_idx,
        )
        runs.append(res)
        # Do not repeat a crashing script unnecessarily
        if res["status"] != "executed_ok":
            break

    ok_runs = [r for r in runs if r["status"] == "executed_ok"]
    jitter_metrics: dict[str, dict[str, Any]] = {}
    max_jitter = 0.0
    deterministic: bool | None = None

    if len(ok_runs) >= 2:
        all_keys = sorted({k for r in ok_runs for k, v in (r.get("parsed_metrics") or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
        for k in all_keys:
            vals = [float(r["parsed_metrics"][k]) for r in ok_runs if isinstance(r.get("parsed_metrics", {}).get(k), (int, float)) and not isinstance(r.get("parsed_metrics", {}).get(k), bool)]
            if len(vals) >= 2:
                spread = max(vals) - min(vals)
                if spread > 1e-12:
                    jitter_metrics[k] = {"min": round(min(vals), 8), "max": round(max(vals), 8), "spread": round(spread, 8), "values": [round(v, 8) for v in vals]}
                    max_jitter = max(max_jitter, spread)
        deterministic = len(jitter_metrics) == 0

    primary = runs[0]
    return {
        "script": relpath,
        "status": "executed_ok" if (len(ok_runs) == len(runs) and len(runs) > 0) else primary["status"],
        "repeats_requested": repeats,
        "repeats_completed_ok": len(ok_runs),
        "returncode": primary["returncode"],
        "elapsed_sec": primary["elapsed_sec"],
        "command": primary["command"],
        "pythonhashseed": primary["pythonhashseed"],
        "input_sha256": primary["input_sha256"],
        "parsed_metrics": primary["parsed_metrics"] if primary["status"] == "executed_ok" else {},
        "deterministic": deterministic,
        "max_run_jitter": round(max_jitter, 8) if deterministic is not None else None,
        "jitter_metrics": jitter_metrics,
        "repeat_runs": runs,
        "stdout_tail": primary["stdout_tail"],
        "stderr_tail": primary["stderr_tail"],
    }


def _attribute_discrepancy(
    *,
    candidate_file: str,
    metric_name: str,
    status: str,
    run_summary: dict[str, Any],
    code_inspect_map: dict[str, dict[str, Any]],
    param_reconciliation: list[dict[str, Any]],
    env_audit: dict[str, Any],
    missing_materials: list[str],
) -> dict[str, Any]:
    """Evidence-backed 5-axis discrepancy attribution."""
    axes: list[str] = []
    evidence: list[str] = []

    # 1. Parameter mismatch in this specific script
    script_conflicts = [
        p for p in param_reconciliation
        if p.get("code_source") == candidate_file and p.get("provenance_status") == "paper_code_conflict"
    ]
    if script_conflicts:
        axes.append("parameter_mismatch")
        for sc in script_conflicts:
            evidence.append(
                f"parameter_mismatch: {sc['parameter']} paper={sc['paper_value']} vs {candidate_file}:{sc.get('source_kind')}={sc['code_value']} (line {sc.get('line')})."
            )

    # 2. Environment or seed (observed jitter, unseeded stochastic RNG, or runtime package drift)
    ci = code_inspect_map.get(candidate_file) or {}
    seed_status = (ci.get("seed_hygiene") or {}).get("status")
    if run_summary.get("deterministic") is False:
        axes.append("environment_or_seed")
        jm = (run_summary.get("jitter_metrics") or {}).get(metric_name)
        evidence.append(
            f"environment_or_seed: non-deterministic repeat execution detected on {candidate_file} "
            f"(max_run_jitter={run_summary.get('max_run_jitter')}, metric_jitter={jm})."
        )
    elif seed_status == "unseeded_stochastic_risk":
        axes.append("environment_or_seed")
        evidence.append(f"environment_or_seed: {candidate_file} uses stochastic RNG calls without a fixed seed.")
    if (env_audit.get("summary") or {}).get("version_drift_count", 0) > 0 or (env_audit.get("summary") or {}).get("conda_pip_mixed"):
        if "environment_or_seed" not in axes:
            axes.append("environment_or_seed")
        evidence.append(
            f"environment_or_seed: lockfile/env drift signals detected (conda_pip_mixed={env_audit['summary']['conda_pip_mixed']}, "
            f"unpinned={env_audit['summary']['unpinned_dependency_count']}, version_drift={env_audit['summary']['version_drift_count']})."
        )

    # 3. Data or split difference (missing original dataset/PMLB)
    if missing_materials:
        axes.append("data_or_split_difference")
        evidence.append(f"data_or_split_difference: original material(s) absent: {missing_materials[0]}")

    # 4. Paper underspecification (paper params missing in code or undocumented code hyperparams)
    paper_missing = [p["parameter"] for p in param_reconciliation if p["provenance_status"] == "paper_only_missing_in_code"]
    code_undoc = [p["parameter"] for p in param_reconciliation if p.get("code_source") == candidate_file and p["provenance_status"] == "code_only_undocumented"]
    if paper_missing or code_undoc:
        axes.append("paper_underspecification")
        if paper_missing:
            evidence.append(f"paper_underspecification: paper parameter(s) missing in code: {paper_missing[:5]}.")
        if code_undoc:
            evidence.append(f"paper_underspecification: undocumented code parameter(s) in {candidate_file}: {code_undoc[:5]}.")

    # 5. Implementation difference (always primary on failed run / omitted metric, or fallback)
    if status in ("execution_not_completed", "metric_not_emitted") or not axes:
        axes.insert(0, "implementation_difference")
        if status == "execution_not_completed":
            evidence.insert(0, f"implementation_difference: {candidate_file} exited with rc={run_summary.get('returncode')} ({run_summary.get('status')}).")
        elif status == "metric_not_emitted":
            evidence.insert(0, f"implementation_difference: {candidate_file} exited 0 but did not emit metric '{metric_name}'.")
        else:
            evidence.append(f"implementation_difference: residual deviation on '{metric_name}' not accounted for by parameter/seed/data signals alone.")

    # Choose strongest direct primary axis
    if status in ("execution_not_completed", "metric_not_emitted"):
        primary = "implementation_difference"
    elif script_conflicts:
        primary = "parameter_mismatch"
    elif run_summary.get("deterministic") is False or seed_status == "unseeded_stochastic_risk":
        primary = "environment_or_seed"
    elif missing_materials:
        primary = "data_or_split_difference"
    else:
        primary = axes[0]

    return {
        "primary_attribution_axis": primary,
        "supporting_axes": [a for a in axes if a != primary],
        "attribution_evidence": evidence,
    }


def compare_against_tolerance(
    run_summaries: list[dict[str, Any]],
    paper_targets: dict[str, Any],
    tolerances: dict[str, Any],
    *,
    code_inspect_map: dict[str, dict[str, Any]],
    param_reconciliation: list[dict[str, Any]],
    env_audit: dict[str, Any],
    missing_materials: list[str],
    paper_blocks: list[dict[str, Any]] | None = None,
    target_origins: dict[str, str] | None = None,
    pdf_extraction_failed: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare ONLY clean-exit runs against a priori tolerances with exact paper prose anchors and value_origin."""
    blocks = paper_blocks or []
    comparisons: list[dict[str, Any]] = []
    excluded_failed: list[dict[str, Any]] = []

    default_abs = float(tolerances.get("default_abs_tol", 1e-4))
    default_rel = float(tolerances.get("default_rel_tol", 0.05))

    for r in run_summaries:
        if r["status"] != "executed_ok":
            excluded_failed.append({
                "candidate": r["script"],
                "run_status": r["status"],
                "returncode": r["returncode"],
                "stderr_tail": r["stderr_tail"],
                "reason": "Excluded from tolerance statistics because candidate execution did not exit cleanly.",
            })

    ok_runs = [r for r in run_summaries if r["status"] == "executed_ok"]

    for metric_name, target_raw in paper_targets.items():
        anchor = bind_paper_anchor(
            metric_name,
            target_raw,
            blocks,
            is_code_only=False,
            pdf_extraction_failed=pdf_extraction_failed,
        )
        t_origin = _infer_value_origin(metric_name, target_origins, anchor, is_code_only=False)
        if target_raw is None:
            comparisons.append({
                "metric": metric_name,
                "candidate": None,
                "target": None,
                "value_origin": t_origin,
                "target_origin": t_origin,
                "observed_origin": None,
                "observed": None,
                "status": "missing_paper_baseline",
                "within_tolerance": None,
                "reason": "Paper target metric not reported; retained as missing without inventing a baseline.",
                **anchor,
            })
            continue

        target_val = float(target_raw)
        tol_cfg = tolerances.get(metric_name) if isinstance(tolerances.get(metric_name), dict) else {}
        abs_tol = float(tol_cfg.get("abs_tol", default_abs))
        rel_tol = float(tol_cfg.get("rel_tol", default_rel))

        # Only compare candidates that either emit this metric OR when none of the candidates emit it
        emitting_runs = [
            r for r in ok_runs
            if isinstance((r.get("parsed_metrics") or {}).get(metric_name), (int, float))
            and not isinstance((r.get("parsed_metrics") or {}).get(metric_name), bool)
        ]
        target_runs = emitting_runs if (emitting_runs and len(ok_runs) > 1 and any(
            len(set((r.get("parsed_metrics") or {}).keys()) & set(paper_targets.keys())) > 0
            and set((r.get("parsed_metrics") or {}).keys()).isdisjoint({metric_name})
            for r in ok_runs
        )) else ok_runs

        for r in target_runs:
            obs = (r.get("parsed_metrics") or {}).get(metric_name)
            if not isinstance(obs, (int, float)) or isinstance(obs, bool):
                attr = _attribute_discrepancy(
                    candidate_file=r["script"],
                    metric_name=metric_name,
                    status="metric_not_emitted",
                    run_summary=r,
                    code_inspect_map=code_inspect_map,
                    param_reconciliation=param_reconciliation,
                    env_audit=env_audit,
                    missing_materials=missing_materials,
                )
                comparisons.append({
                    "metric": metric_name,
                    "candidate": r["script"],
                    "target": target_val,
                    "value_origin": t_origin,
                    "target_origin": t_origin,
                    "observed_origin": None,
                    "observed": None,
                    "abs_tol": abs_tol,
                    "rel_tol": rel_tol,
                    "within_tolerance": None,
                    "status": "metric_not_emitted",
                    "attribution_category": attr["primary_attribution_axis"],
                    **attr,
                    **anchor,
                })
                continue

            obs_f = float(obs)
            abs_diff = abs(obs_f - target_val)
            rel_diff = abs_diff / max(1e-12, abs(target_val))
            metric_jitter = (r.get("jitter_metrics") or {}).get(metric_name)
            has_metric_jitter = bool(metric_jitter and float(metric_jitter.get("spread") or 0.0) > 1e-12)
            raw_ok = (abs_diff <= abs_tol) or (rel_diff <= rel_tol)
            ok = raw_ok and not has_metric_jitter
            comp_status = (
                "within_tolerance"
                if ok
                else ("within_tolerance_unstable_jitter" if (raw_ok and has_metric_jitter) else "out_of_tolerance")
            )
            entry: dict[str, Any] = {
                "metric": metric_name,
                "candidate": r["script"],
                "target": target_val,
                "value_origin": "observed",
                "target_origin": t_origin,
                "observed_origin": "observed",
                "observed": round(obs_f, 8),
                "abs_diff": round(abs_diff, 8),
                "rel_diff": round(rel_diff, 8),
                "abs_tol": abs_tol,
                "rel_tol": rel_tol,
                "within_tolerance": ok,
                "deterministic": not has_metric_jitter if metric_jitter is not None else r.get("deterministic"),
                "metric_jitter": metric_jitter,
                "status": comp_status,
                **anchor,
            }
            if not ok:
                attr = _attribute_discrepancy(
                    candidate_file=r["script"],
                    metric_name=metric_name,
                    status=comp_status,
                    run_summary=r,
                    code_inspect_map=code_inspect_map,
                    param_reconciliation=param_reconciliation,
                    env_audit=env_audit,
                    missing_materials=missing_materials,
                )
                if comp_status == "within_tolerance_unstable_jitter":
                    attr["primary_attribution_axis"] = "environment_or_seed"
                entry["attribution_category"] = attr["primary_attribution_axis"]
                entry.update(attr)
            else:
                entry["attribution_category"] = None
            comparisons.append(entry)

    return comparisons, excluded_failed


def render_reproduction_report_md(report: dict[str, Any], gaps: list[dict[str, Any]]) -> str:
    """Render concise, domain-facing Markdown reproduction audit report."""
    ladder = report["evidence_ladder"]
    env_doc = report["environment_lockfile_audit"]
    env_sum = env_doc["summary"]
    lines: list[str] = [
        f"# Paper Reproduction & Method Verification Report (`{report['case_id']}`)",
        "",
        f"- **Reproducibility Grade**: `{report['reproducibility_grade']}`",
        f"- **Scope Boundary**: {report['scope_disclaimer']}",
        "",
        "## 1. Four-Tier Evidence Ladder",
        "",
        "| Tier | Question | Status |",
        "| --- | --- | --- |",
        f"| Tier 1: Code Present | Candidate numerical scripts present and syntactically valid? | `{ladder['tier_1_code_present']}` |",
        f"| Tier 2: Clean Exit | All candidate runs exited with return code 0? | `{ladder['tier_2_clean_exit']}` |",
        f"| Tier 3: Within A Priori Tolerance | Clean-exit metrics within declared tolerances and deterministic across repeats? | `{ladder['tier_3_within_prior_tolerance']}` |",
        f"| Tier 4: Scientific Claim Supported | Full original dataset/repository verified beyond synthetic trial? | `{ladder['tier_4_scientific_claim_supported']}` |",
        "",
        "## 2. Environment & Lockfile Drift Audit (`env_lock_doctor`)",
        "",
        f"- Python: `{report['environment_snapshot']['python_version']}` (`{report['environment_snapshot']['platform']}`), `PYTHONHASHSEED={report['environment_snapshot']['pythonhashseed']}`",
        f"- Declared dependencies: `{env_sum['declared_dependency_count']}` | Unpinned: `{env_sum['unpinned_dependency_count']}` | Conda+Pip mixed: `{env_sum['conda_pip_mixed']}` | Strict lockfile present: `{env_sum['strict_lockfile_present']}` | Runtime version drifts: `{env_sum['version_drift_count']}`",
        f"- Research-Repo-Doctor engineering readiness ratio: `{env_doc['research_repo_doctor_checklist']['engineering_readiness_ratio']}` (not scientific reproduction)",
        "",
    ]

    actions = env_doc["research_repo_doctor_checklist"].get("manual_action_items") or []
    if actions:
        lines.append("### Manual Remediation Actions")
        lines.append("")
        for act in actions:
            lines.append(f"- **[{act['severity'].upper()}] `{act['code']}`**: {act['action']}")
        lines.append("")

    # Parameter Reconciliation (with Paper Prose Anchors & Value Origin)
    lines.append("## 3. Hyperparameter Reconciliation, Paper Anchors & Seed Hygiene")
    lines.append("")
    params = report.get("parameter_reconciliation") or []
    if not params:
        lines.append("No paper or code parameters extracted.")
        lines.append("")
    else:
        lines.append("| Parameter | Paper Value | Value Origin | Paper Source & Locator | Anchor Status | Code Value | Code Source | Source Kind | Status |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for p in params:
            loc_str = f"{p.get('paper_source_file')}:{p.get('paper_locator')}" if p.get("paper_source_file") else "none"
            lines.append(
                f"| `{p['parameter']}` | `{p.get('paper_value')}` | `{p.get('value_origin')}` | `{loc_str}` | `{p.get('paper_anchor_status')}` | "
                f"`{p.get('code_value')}` | `{p.get('code_source') or 'none'}` | `{p.get('source_kind') or 'none'}` | `{p['provenance_status']}` |"
            )
        lines.append("")
        anchored_p = [p for p in params if p.get("paper_quote")]
        if anchored_p:
            lines.append("### Verbatim Paper Parameter Quotes")
            lines.append("")
            seen_pq: set[str] = set()
            for p in anchored_p:
                if p["parameter"] in seen_pq:
                    continue
                seen_pq.add(p["parameter"])
                lines.append(f"- `{p['parameter']}` (`{p.get('paper_source_file')}` @ `{p.get('paper_locator')}`): \"{p['paper_quote']}\"")
            lines.append("")

    # Execution & Determinism
    lines.append("## 4. Isolated Execution & Repeat Determinism")
    lines.append("")
    runs = report.get("execution_runs") or []
    if runs:
        lines.append("| Candidate Script | Status | RC | Repeats OK | Deterministic | Max Jitter | Seed Hygiene |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        ci_map = {c["file"]: c for c in (report.get("candidate_scripts") or [])}
        for r in runs:
            sh = ((ci_map.get(r["script"]) or {}).get("seed_hygiene") or {}).get("status", "unknown")
            lines.append(
                f"| `{r['script']}` | `{r['status']}` | `{r['returncode']}` | "
                f"`{r['repeats_completed_ok']}/{r['repeats_requested']}` | `{r['deterministic']}` | "
                f"`{r['max_run_jitter']}` | `{sh}` |"
            )
        lines.append("")

    excluded = report.get("failed_runs_excluded") or []
    if excluded:
        lines.append("### Failed Runs Excluded from Tolerance Statistics")
        lines.append("")
        for ex in excluded:
            lines.append(f"- `{ex['candidate']}`: status=`{ex['run_status']}`, rc=`{ex['returncode']}` — {ex['reason']}")
        lines.append("")

    # Tolerance & 5-Axis Attribution (with Paper Prose Anchors & Origin Tags)
    lines.append("## 5. A Priori Tolerance Comparison, Paper Anchors & Five-Axis Attribution")
    lines.append("")
    comps = report.get("tolerance_comparisons") or []
    if comps:
        lines.append("| Candidate | Metric | Target | Target Origin | Paper Source & Locator | Anchor Status | Observed (Origin) | Abs Diff | Rel Diff | Status | Primary Attribution |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for c in comps:
            loc_str = f"{c.get('paper_source_file')}:{c.get('paper_locator')}" if c.get("paper_source_file") else "none"
            obs_lbl = f"{c.get('observed')} ({c.get('observed_origin') or 'none'})"
            lines.append(
                f"| `{c.get('candidate') or 'none'}` | `{c['metric']}` | `{c.get('target')}` | `{c.get('target_origin') or c.get('value_origin')}` | `{loc_str}` | `{c.get('paper_anchor_status')}` | "
                f"`{obs_lbl}` | `{c.get('abs_diff')}` | `{c.get('rel_diff')}` | `{c['status']}` | `{c.get('attribution_category') or 'none'}` |"
            )
        lines.append("")
        anchored_m = [c for c in comps if c.get("paper_quote")]
        if anchored_m:
            lines.append("### Verbatim Paper Metric Quotes")
            lines.append("")
            seen_mq: set[str] = set()
            for c in anchored_m:
                if c["metric"] in seen_mq:
                    continue
                seen_mq.add(c["metric"])
                lines.append(f"- `{c['metric']}` (`{c.get('paper_source_file')}` @ `{c.get('paper_locator')}`): \"{c['paper_quote']}\"")
            lines.append("")
        for c in comps:
            if c.get("attribution_evidence"):
                lines.append(f"- Attribution evidence for `{c.get('candidate')}` / `{c['metric']}` (`{c.get('primary_attribution_axis')}`):")
                for ev in c["attribution_evidence"]:
                    lines.append(f"  - {ev}")
        lines.append("")

    # Closed-Loop Remediation Verification (Before Fix vs After Fix)
    rem_runs = report.get("remediation_closed_loop") or []
    if rem_runs:
        lines.append("## 5b. Closed-Loop Parameter Conflict Remediation (Before vs After)")
        lines.append("")
        lines.append("| Remediation Label | Script | Parameter Change | Before Status -> After Status | Before Metrics -> After Metrics | Input SHA-256 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for rr in rem_runs:
            pchanges = "; ".join(f"{k}: {v['before']} -> {v['after']}" for k, v in (rr.get("parameter_changes") or {}).items())
            mchanges = "; ".join(
                f"{k}: {v['before']} -> {v['after']} (target={v['target']})"
                for k, v in (rr.get("metric_deltas") or {}).items()
            )
            lines.append(
                f"| `{rr['label']}` | `{rr['script']}` | `{pchanges}` | "
                f"`{rr['before_param_status']} -> {rr['after_param_status']}` | `{mchanges[:140]}` | `{rr['script_sha256'][:16]}` |"
            )
        lines.append("")

    # Missing original materials
    missing = report.get("missing_original_materials") or []
    if missing:
        lines.append("## 6. Missing Original Materials & Boundaries")
        lines.append("")
        for mm in missing:
            lines.append(f"- {mm}")
        lines.append("")

    if gaps:
        lines.append("## 7. Recorded Gaps")
        lines.append("")
        for g in gaps:
            lines.append(f"- `{g.get('code')}` ({g.get('severity', 'warning')}): {g.get('message')}")
        lines.append("")

    # Execution Telemetry & Output Contract
    tel = report.get("execution_telemetry") or {}
    occ = report.get("output_contract_compliance") or {}
    if tel or occ:
        lines.append("## 8. Execution Telemetry & Output Contract Coverage")
        lines.append("")
        if tel:
            lines.append(
                f"- Total audit wall time: `{tel.get('total_elapsed_sec')}s` | Steps completed: `{tel.get('steps_completed')}` | "
                f"Candidate repeat runs executed: `{tel.get('candidate_runs_executed')}` | Paper prose blocks indexed: `{tel.get('paper_blocks_indexed')}`"
            )
            if tel.get("phase_timings_sec"):
                pt = ", ".join(f"{k}={v}s" for k, v in tel["phase_timings_sec"].items())
                lines.append(f"- Phase timings: {pt}")
        if occ:
            lines.append(
                f"- Dynamic `output_contract` coverage: `{occ.get('coverage_ratio')}` "
                f"(declared={len(occ.get('declared_fields') or [])}, unpopulated={occ.get('unpopulated_fields') or []})"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path, *, repeats: int = 2) -> dict[str, Any]:
    t_total0 = time.monotonic()
    phase_timings: dict[str, float] = {}
    steps_completed = 0

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="da62e8d4-repro-verifier")

    case_path = input_dir / "case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace")) if case_path.is_file() else {}
    case_id = str(case_data.get("case_id") or "case-unknown")

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-da62e8d4")
    steps_completed += 1

    # 1. Environment snapshot + lockfile drift doctor
    t0 = time.monotonic()
    budget_guard.tick_phase(budget_file, "env_lockfile_audit")
    env_snapshot = capture_env_snapshot()
    env_audit = env_lock_doctor.audit_environment_and_locks(input_dir)
    result_guard.atomic_write_json(work_dir / "env_doctor.json", env_audit)
    phase_timings["env_lockfile_audit"] = round(time.monotonic() - t0, 4)
    steps_completed += 1

    # 2. Index supplied paper prose for exact locator & quote anchoring (with PDF diagnostics)
    t0 = time.monotonic()
    pdf_diagnostics: list[dict[str, Any]] = []
    paper_blocks = collect_paper_blocks(input_dir, case_data, pdf_diagnostics=pdf_diagnostics)
    failed_pdfs = [d for d in pdf_diagnostics if not d.get("ok")]
    pdf_extraction_failed = bool(failed_pdfs)
    phase_timings["paper_prose_indexing"] = round(time.monotonic() - t0, 4)
    steps_completed += 1

    # 3. Discover candidate Python scripts & perform deep AST inspection + paper-anchored parameter reconciliation
    t0 = time.monotonic()
    candidate_scripts = sorted(
        p for p in input_dir.rglob("*.py")
        if p.is_file() and not p.name.startswith("test_")
    )
    code_inspects = [extract_code_parameters(sp, sp.relative_to(input_dir).as_posix()) for sp in candidate_scripts]
    code_inspect_map = {ci["file"]: ci for ci in code_inspects}

    paper_params = case_data.get("paper_parameters") or case_data.get("hyperparameters") or {}
    param_origins = case_data.get("parameter_origins") or {}
    target_origins = case_data.get("target_origins") or {}
    param_reconciliation = reconcile_parameters(
        paper_params,
        code_inspects,
        paper_blocks=paper_blocks,
        param_origins=param_origins,
        pdf_extraction_failed=pdf_extraction_failed,
    )
    phase_timings["ast_param_reconciliation"] = round(time.monotonic() - t0, 4)
    steps_completed += 1

    # 4. Isolated multi-run execution
    t0 = time.monotonic()
    budget_guard.tick_phase(budget_file, "run_candidate_methods")
    effective_repeats = int(case_data.get("repeats") or repeats)
    hashseed = str(case_data.get("pythonhashseed", "0"))
    run_args = [str(a) for a in (case_data.get("runner_args") or [])]
    timeout_sec = int(case_data.get("timeout_sec") or 30)

    run_summaries: list[dict[str, Any]] = []
    for sp in candidate_scripts:
        rel = sp.relative_to(input_dir).as_posix()
        run_summaries.append(
            execute_candidate_with_repeats(
                sp,
                rel,
                work_dir,
                args=run_args,
                hashseed=hashseed,
                repeats=effective_repeats,
                timeout_sec=timeout_sec,
            )
        )
    phase_timings["candidate_execution"] = round(time.monotonic() - t0, 4)
    steps_completed += 1

    # 5. Missing original materials & paper-anchored tolerance comparisons
    t0 = time.monotonic()
    missing_materials = [str(x) for x in (case_data.get("missing_materials") or [])]
    if not candidate_scripts:
        missing_materials.append("No candidate Python method scripts supplied in /input.")
    paper_targets = case_data.get("paper_targets") or case_data.get("expected_metrics") or {}
    if not paper_targets:
        missing_materials.append("No quantitative paper_targets supplied for a priori tolerance comparison.")
    tolerances = case_data.get("tolerances") or {}

    comparisons, excluded_failed = compare_against_tolerance(
        run_summaries,
        paper_targets,
        tolerances,
        code_inspect_map=code_inspect_map,
        param_reconciliation=param_reconciliation,
        env_audit=env_audit,
        missing_materials=missing_materials,
        paper_blocks=paper_blocks,
        target_origins=target_origins,
        pdf_extraction_failed=pdf_extraction_failed,
    )

    # 5b. Closed-loop remediation re-runs for declared parameter conflict fixes (before vs after with SHA-256)
    remediation_closed_loop: list[dict[str, Any]] = []
    input_file_hashes = {
        fp.relative_to(input_dir).as_posix(): sha256_file(fp)
        for fp in sorted(p for p in input_dir.rglob("*") if p.is_file())
    }
    rem_specs = list(case_data.get("remediation_overrides") or [])
    if not rem_specs:
        # Auto-discover CLI argparse parameter conflict closed-loop verification when script accepts --<param>
        for pc in param_reconciliation:
            if pc.get("provenance_status") == "paper_code_conflict" and pc.get("source_kind") == "argparse_default":
                flag = "--" + str(pc["parameter"]).replace("_", "-")
                rem_specs.append({
                    "label": f"post_fix_{pc['parameter']}",
                    "script": pc["code_source"],
                    "args": [flag, str(pc["paper_value"])],
                    "fixed_parameters": {pc["parameter"]: pc["paper_value"]},
                })
    run_by_script = {r["script"]: r for r in run_summaries}
    for rem in rem_specs:
        rel_script = str(rem.get("script") or "")
        sp_path = input_dir / rel_script
        if not rel_script or not sp_path.is_file():
            continue
        rem_args = [str(a) for a in (rem.get("args") or [])]
        after_run = execute_candidate_with_repeats(
            sp_path,
            rel_script,
            work_dir / f"remediation_{re.sub(r'[^A-Za-z0-9_.-]+', '_', str(rem.get('label') or 'fix'))}",
            args=rem_args,
            hashseed=hashseed,
            repeats=effective_repeats,
            timeout_sec=timeout_sec,
        )
        before_run = run_by_script.get(rel_script) or {}
        fixed_map = rem.get("fixed_parameters") or {}
        param_changes: dict[str, dict[str, Any]] = {}
        all_fixed_matched = True
        for pk, pv in fixed_map.items():
            prev_row = next((p for p in param_reconciliation if p.get("code_source") == rel_script and _norm_key(p["parameter"]) == _norm_key(pk)), None)
            paper_ref = paper_params.get(pk) if pk in paper_params else (prev_row.get("paper_value") if prev_row else None)
            is_match = _values_equal(paper_ref, pv) if paper_ref is not None else False
            if not is_match:
                all_fixed_matched = False
            param_changes[pk] = {
                "paper_value": paper_ref,
                "before": prev_row.get("code_value") if prev_row else None,
                "after": pv,
                "before_status": prev_row.get("provenance_status") if prev_row else "unknown",
                "after_status": "matched" if is_match else "paper_code_conflict",
                "value_origin": prev_row.get("value_origin") if prev_row else "paper_reported",
            }
        before_m = before_run.get("parsed_metrics") or {}
        after_m = after_run.get("parsed_metrics") or {}
        metric_deltas: dict[str, dict[str, Any]] = {}
        for mk, tv in paper_targets.items():
            if mk in before_m or mk in after_m:
                bv = before_m.get(mk)
                av = after_m.get(mk)
                if bv != av or (isinstance(bv, (int, float)) and isinstance(tv, (int, float)) and abs(float(bv) - float(tv)) > 1.0):
                    metric_deltas[mk] = {
                        "target": tv,
                        "before": bv,
                        "after": av,
                        "before_abs_diff": round(abs(float(bv) - float(tv)), 6) if isinstance(bv, (int, float)) and isinstance(tv, (int, float)) else None,
                        "after_abs_diff": round(abs(float(av) - float(tv)), 6) if isinstance(av, (int, float)) and isinstance(tv, (int, float)) else None,
                        "value_origin": "observed",
                    }
        remediation_closed_loop.append({
            "label": str(rem.get("label") or f"fix_{rel_script}"),
            "script": rel_script,
            "script_sha256": sha256_file(sp_path),
            "input_file_sha256": input_file_hashes,
            "before_command": before_run.get("command"),
            "after_command": after_run.get("command"),
            "before_param_status": "paper_code_conflict",
            "after_param_status": "matched" if all_fixed_matched else "paper_code_conflict",
            "parameter_changes": param_changes,
            "before_metrics": before_m,
            "after_metrics": after_m,
            "metric_deltas": metric_deltas,
            "after_run_status": after_run["status"],
            "after_returncode": after_run["returncode"],
        })

    phase_timings["tolerance_and_attribution"] = round(time.monotonic() - t0, 4)
    steps_completed += 1

    # 5. Four-tier evidence ladder & honest reproducibility grade
    failed_runs = [r for r in run_summaries if r["status"] != "executed_ok"]
    jitter_runs = [r for r in run_summaries if r.get("deterministic") is False]
    out_of_tol = [c for c in comparisons if c.get("status") == "out_of_tolerance"]
    within_tol = [c for c in comparisons if c.get("within_tolerance") is True]
    missing_comparisons = [c for c in comparisons if c.get("status") in {"metric_not_emitted", "missing_paper_baseline"}]
    param_conflicts = [p for p in param_reconciliation if p["provenance_status"] == "paper_code_conflict"]
    code_only_undoc = [p for p in param_reconciliation if p["provenance_status"] == "code_only_undocumented"]
    paper_only_missing = [p for p in param_reconciliation if p["provenance_status"] == "paper_only_missing_in_code"]
    unseeded_scripts = [ci["file"] for ci in code_inspects if (ci.get("seed_hygiene") or {}).get("status") == "unseeded_stochastic_risk"]

    tier_1 = bool(candidate_scripts) and not any("syntax_error" in ci for ci in code_inspects)
    tier_2 = bool(run_summaries) and len(failed_runs) == 0
    tier_3 = tier_2 and bool(within_tol) and len(out_of_tol) == 0 and len(missing_comparisons) == 0 and len(jitter_runs) == 0
    tier_4 = "established_within_supplied_scope" if (tier_3 and not missing_materials and not param_conflicts) else "not_established"

    evidence_ladder = {
        "tier_1_code_present": tier_1,
        "tier_2_clean_exit": tier_2,
        "tier_3_within_prior_tolerance": tier_3,
        "tier_4_scientific_claim_supported": tier_4,
    }

    if not run_summaries or len(failed_runs) == len(run_summaries):
        repro_grade = "not_reproducible_or_missing_code"
    elif failed_runs or out_of_tol or missing_comparisons or missing_materials or jitter_runs or param_conflicts:
        repro_grade = "partially_reproducible"
    else:
        repro_grade = "reproducible_within_tolerance"

    gaps: list[dict[str, Any]] = []
    for fp_diag in failed_pdfs:
        gaps.append({
            "code": "PDF_TEXT_EXTRACTION_FAILED",
            "severity": "error",
            "blocking": True,
            "source": fp_diag["file"],
            "message": (
                f"All PDF extraction backends (pdfplumber/pypdf/pdftotext/stdlib_zlib) failed or returned empty text "
                f"for {fp_diag['file']} ({'; '.join(fp_diag.get('errors') or [])}). Dependent paper anchors marked unverified."
            ),
        })
    for mm in missing_materials:
        gaps.append({"code": "MISSING_ORIGINAL_MATERIAL", "severity": "info", "source": "repro_audit_runner", "message": mm})
    if env_audit["summary"]["conda_pip_mixed"] and not env_audit["summary"]["strict_lockfile_present"]:
        gaps.append({
            "code": "CONDA_PIP_MIXED_LOCKFILE_DRIFT",
            "severity": "warning",
            "source": "env_lock_doctor",
            "message": "Detected mixed Conda+pip environment specification without strict lockfile (Conda #15581/PR #15838 drift risk).",
        })
    if env_audit["summary"]["unpinned_dependency_count"] > 0:
        gaps.append({
            "code": "UNPINNED_DEPENDENCIES_DETECTED",
            "severity": "warning",
            "source": "env_lock_doctor",
            "message": f"{env_audit['summary']['unpinned_dependency_count']} declared dependency specifier(s) are not exact-pinned.",
        })
    if env_audit["summary"]["version_drift_count"] > 0:
        gaps.append({
            "code": "RUNTIME_VERSION_DRIFT",
            "severity": "warning",
            "source": "env_lock_doctor",
            "message": f"{env_audit['summary']['version_drift_count']} package(s) differ between manifest pin and installed runtime version.",
        })
    if param_conflicts:
        gaps.append({
            "code": "HYPERPARAMETER_CONFLICT_DETECTED",
            "severity": "warning",
            "source": "reconcile_parameters",
            "message": f"{len(param_conflicts)} hyperparameter conflict(s) between paper and candidate code.",
        })
    if unseeded_scripts:
        gaps.append({
            "code": "UNSEEDED_STOCHASTIC_CODE",
            "severity": "warning",
            "source": "extract_code_parameters",
            "message": f"Stochastic RNG usage without fixed seed detected in: {unseeded_scripts}.",
        })
    if jitter_runs:
        gaps.append({
            "code": "STOCHASTIC_RUN_JITTER",
            "severity": "warning",
            "source": "execute_candidate_with_repeats",
            "message": f"{len(jitter_runs)} candidate script(s) produced non-deterministic numeric metrics across identical repeat runs.",
        })
    if failed_runs:
        gaps.append({
            "code": "CANDIDATE_EXECUTION_FAILED",
            "severity": "warning",
            "source": "execute_candidate_with_repeats",
            "message": f"{len(failed_runs)} candidate script(s) failed execution and were excluded from tolerance statistics.",
        })
    if out_of_tol:
        gaps.append({
            "code": "METRIC_OUT_OF_TOLERANCE",
            "severity": "warning",
            "source": "compare_against_tolerance",
            "message": f"{len(out_of_tol)} clean-exit metric comparison(s) exceeded a priori tolerance.",
        })
    if missing_comparisons:
        gaps.append({
            "code": "METRIC_COMPARISON_INCOMPLETE",
            "severity": "warning",
            "source": "compare_against_tolerance",
            "message": f"{len(missing_comparisons)} comparison(s) lacked a numeric observation or paper baseline.",
        })

    paper_param_rows = [p for p in param_reconciliation if p["provenance_status"] != "code_only_undocumented"]
    anchored_param_count = sum(1 for p in paper_param_rows if p.get("paper_anchor_status") in ("anchored_exact", "anchored_context"))
    unanchored_params = sorted({p["parameter"] for p in paper_param_rows if p.get("paper_anchor_status") == "not_found_in_supplied_prose"})
    anchored_metric_count = sum(1 for c in comparisons if c.get("paper_anchor_status") in ("anchored_exact", "anchored_context"))
    unanchored_metrics = sorted({c["metric"] for c in comparisons if c.get("paper_anchor_status") == "not_found_in_supplied_prose"})
    if paper_blocks and (unanchored_params or unanchored_metrics):
        gaps.append({
            "code": "PAPER_ANCHOR_UNVERIFIED",
            "severity": "info",
            "source": "bind_paper_anchor",
            "message": f"Paper prose anchor not found in supplied text for params={unanchored_params} / metrics={unanchored_metrics}.",
        })

    # Dynamic case.json.output_contract absorption
    raw_contract = case_data.get("output_contract")
    contract_fields: list[str] = []
    if isinstance(raw_contract, list):
        contract_fields = [str(k) for k in raw_contract if k]
    elif isinstance(raw_contract, dict):
        if isinstance(raw_contract.get("required_fields"), list):
            contract_fields = [str(k) for k in raw_contract["required_fields"] if k]
        elif isinstance(raw_contract.get("fields"), list):
            contract_fields = [str(k) for k in raw_contract["fields"] if k]
        elif isinstance(raw_contract.get("properties"), dict):
            contract_fields = [str(k) for k in raw_contract["properties"].keys() if k]
        else:
            contract_fields = [str(k) for k in raw_contract.keys() if k]

    steps_completed += 1
    total_runs_exec = sum(len(r.get("repeat_runs") or []) for r in run_summaries)
    execution_telemetry: dict[str, Any] = {
        "total_elapsed_sec": round(time.monotonic() - t_total0, 4),
        "steps_completed": steps_completed,
        "candidate_runs_executed": total_runs_exec,
        "paper_blocks_indexed": len(paper_blocks),
        "phase_timings_sec": phase_timings,
    }

    report: dict[str, Any] = {
        "case_id": case_id,
        "reproducibility_grade": repro_grade,
        "evidence_ladder": evidence_ladder,
        "environment_snapshot": env_snapshot,
        "environment_lockfile_audit": env_audit,
        "pdf_extraction_diagnostics": pdf_diagnostics,
        "candidate_scripts": code_inspects,
        "parameter_reconciliation": param_reconciliation,
        "execution_runs": run_summaries,
        "failed_runs_excluded": excluded_failed,
        "tolerance_comparisons": comparisons,
        "remediation_closed_loop": remediation_closed_loop,
        "missing_original_materials": missing_materials,
        "execution_telemetry": execution_telemetry,
        "scope_disclaimer": (
            "Verification covers supplied candidate scripts, runtime manifests, and a priori tolerances only; "
            "synthetic trials do not substitute for unavailable original author code, physical experiments, or full external PMLB datasets."
        ),
    }

    metrics = {
        "candidate_script_count": len(candidate_scripts),
        "executed_ok_count": sum(1 for r in run_summaries if r["status"] == "executed_ok"),
        "failed_run_count": len(failed_runs),
        "non_deterministic_run_count": len(jitter_runs),
        "unseeded_stochastic_script_count": len(unseeded_scripts),
        "unpinned_dependency_count": env_audit["summary"]["unpinned_dependency_count"],
        "conda_pip_mixed_flag": 1 if env_audit["summary"]["conda_pip_mixed"] else 0,
        "parameter_conflict_count": len(param_conflicts),
        "paper_only_missing_param_count": len(paper_only_missing),
        "code_only_undocumented_param_count": len(code_only_undoc),
        "paper_anchored_param_count": anchored_param_count,
        "paper_anchored_metric_count": anchored_metric_count,
        "within_tolerance_count": len(within_tol),
        "out_of_tolerance_count": len(out_of_tol),
        "total_elapsed_sec": execution_telemetry["total_elapsed_sec"],
    }
    metric_provenance = {
        "candidate_script_count": "reproduction_audit_report.json:candidate_scripts",
        "executed_ok_count": "reproduction_audit_report.json:execution_runs",
        "failed_run_count": "reproduction_audit_report.json:failed_runs_excluded",
        "non_deterministic_run_count": "reproduction_audit_report.json:execution_runs",
        "unseeded_stochastic_script_count": "reproduction_audit_report.json:candidate_scripts",
        "unpinned_dependency_count": "reproduction_audit_report.json:environment_lockfile_audit",
        "conda_pip_mixed_flag": "reproduction_audit_report.json:environment_lockfile_audit",
        "parameter_conflict_count": "reproduction_audit_report.json:parameter_reconciliation",
        "paper_only_missing_param_count": "reproduction_audit_report.json:parameter_reconciliation",
        "code_only_undocumented_param_count": "reproduction_audit_report.json:parameter_reconciliation",
        "paper_anchored_param_count": "reproduction_audit_report.json:parameter_reconciliation",
        "paper_anchored_metric_count": "reproduction_audit_report.json:tolerance_comparisons",
        "within_tolerance_count": "reproduction_audit_report.json:tolerance_comparisons",
        "out_of_tolerance_count": "reproduction_audit_report.json:tolerance_comparisons",
        "total_elapsed_sec": "reproduction_audit_report.json:execution_telemetry",
    }

    patch: dict[str, Any] = {
        "artifacts": ["result.json", "reproduction_audit_report.json", "reproduction_report.md"],
        "reproducibility_grade": repro_grade,
        "evidence_ladder": evidence_ladder,
        "environment_snapshot": env_snapshot,
        "environment_lockfile_summary": env_audit["summary"],
        "pdf_extraction_diagnostics": pdf_diagnostics,
        "parameter_reconciliation": param_reconciliation,
        "execution_runs": run_summaries,
        "failed_runs_excluded": excluded_failed,
        "tolerance_comparisons": comparisons,
        "remediation_closed_loop": remediation_closed_loop,
        "missing_original_materials": missing_materials,
        "execution_telemetry": execution_telemetry,
        "metrics": metrics,
        "metric_provenance": metric_provenance,
        "findings": [
            f"Audited {len(candidate_scripts)} candidate script(s) ({metrics['executed_ok_count']} clean exit, {metrics['failed_run_count']} failed excluded); "
            f"grade={repro_grade}; within_tolerance={len(within_tol)}/{len(comparisons)}; "
            f"paper_anchored_params={anchored_param_count}/{len(paper_param_rows)}, paper_anchored_metrics={anchored_metric_count}/{len(comparisons)}; "
            f"param_conflicts={len(param_conflicts)}; jitter_runs={len(jitter_runs)}; "
            f"unpinned_deps={metrics['unpinned_dependency_count']}."
        ],
        "gaps": gaps,
    }

    output_contract_compliance: dict[str, Any] = {}
    if contract_fields:
        paper_sources = sorted({b["source_file"] for b in paper_blocks})
        alias_map = {
            "sources": paper_sources or [c["file"] for c in code_inspects],
            "uncertainty": gaps,
            "environment_lockfile_audit": env_audit,
            "candidate_scripts": code_inspects,
            "discrepancy_attribution": [
                {
                    "candidate": c.get("candidate"),
                    "metric": c.get("metric"),
                    "status": c.get("status"),
                    "primary_axis": c.get("primary_attribution_axis"),
                    "supporting_axes": c.get("supporting_axes"),
                    "evidence": c.get("attribution_evidence"),
                }
                for c in comparisons
                if c.get("primary_attribution_axis")
            ],
            "summary": patch["findings"][0],
            "research_repo_doctor_checklist": env_audit.get("research_repo_doctor_checklist"),
        }
        populated: list[str] = []
        unpopulated: list[str] = []
        for cf in contract_fields:
            if cf in patch or cf in ("case_id", "status", "task_id", "schema_version", "output_contract_compliance"):
                populated.append(cf)
            elif cf in alias_map:
                patch[cf] = alias_map[cf]
                populated.append(cf)
            else:
                patch[cf] = None
                unpopulated.append(cf)
        if unpopulated:
            gaps.append({
                "code": "OUTPUT_CONTRACT_FIELD_UNPOPULATED",
                "severity": "info",
                "source": "output_contract",
                "message": f"Declared output_contract field(s) had no computed mapping and were preserved as null: {unpopulated}.",
            })
        output_contract_compliance = {
            "declared_fields": contract_fields,
            "populated_fields": populated,
            "unpopulated_fields": unpopulated,
            "coverage_ratio": round(len(populated) / max(1, len(contract_fields)), 4),
        }
        patch["output_contract_compliance"] = output_contract_compliance
        report["output_contract_compliance"] = output_contract_compliance

    report_path = output_dir / "reproduction_audit_report.json"
    result_guard.atomic_write_json(report_path, report)

    md_text = render_reproduction_report_md(report, gaps)
    md_path = output_dir / "reproduction_report.md"
    result_guard.atomic_write_text(md_path, md_text)

    append_audit_entry(
        audit_file,
        phase="reproduce",
        action="repro_audit_completed",
        actor="repro_audit_runner.py",
        output={
            "grade": repro_grade,
            "evidence_ladder": evidence_ladder,
            "scripts_run": len(run_summaries),
            "failed_excluded": len(excluded_failed),
            "jitter_runs": len(jitter_runs),
            "comparisons": len(comparisons),
            "paper_anchored_params": anchored_param_count,
            "paper_anchored_metrics": anchored_metric_count,
            "elapsed_sec": execution_telemetry["total_elapsed_sec"],
        },
        record_id=case_id,
    )

    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["reproduction_audit_report.json", "reproduction_report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor da62e8d4 Reproducibility, Lockfile Drift & Discrepancy Audit Runner.")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    parser.add_argument("--repeats", type=int, default=2, help="Isolated repeat runs per candidate script for jitter detection (default: 2).")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], repeats=args.repeats)
    print(json.dumps({
        "ok": True,
        "case_id": res["case_id"],
        "status": res["status"],
        "reproducibility_grade": res["reproducibility_grade"],
        "evidence_ladder": res["evidence_ladder"],
        "metrics": res["metrics"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
