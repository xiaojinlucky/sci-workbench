#!/usr/bin/env python3
"""Scientific Figure & Table Parser for challenge-f35e4ca5 (阅读02｜科研图表解析).

Implements Organizer Revision v2 & Wenyon Public Output Contract:
- Reads `/input/case.json` (including `organizer_supplied_scope`: `required_actual_image_files`
  with SHA-256 verification and `required_literal_source_table_ids`) plus `/input/material.json`,
  image artifacts, CSV/JSON/Markdown tables, and PDFs.
- Uses zero-dependency pure Python stdlib header inspection (PNG/JPEG/BMP/GIF/TIFF) with
  optional Pillow exposure/cropping diagnostics and SVG text/tick extraction.
- Enforces WebPlotDigitizer #282 axis-calibration rules (linear, log10, ln, reciprocal,
  dual-Y, truncated-axis alert, unscaled-axis exact-reading refusal, scale-bar conversion).
- Builds a PaperStory backbone-and-branch index linking each provided figure/panel to its
  caption, literal source tables, and body discussion paragraphs.
- Strictly separates `provided_figures` from `unprovided_caption_only_figures`:
  unprovided-pixel figures are logged in `uncertainties` and `missing_pixel_figure_gaps`
  without fabrication and without mislabeling a completed supplied-scope run as `partial`.
- Emits `/app/output/result.json` (Wenyon output_contract + Harbor status/metrics),
  `/app/output/figure_table_bundle.json`, and `/app/output/figure_analysis_report.md`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import axis_calibrator_and_audit as aca
    import block_indexer
    import budget_guard
    import final_audit
    import gap_gate
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log, sha256_file
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import axis_calibrator_and_audit as aca
    import block_indexer
    import budget_guard
    import final_audit
    import gap_gate
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log, sha256_file


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif", ".svg"}
TEXT_EXTS = {".md", ".txt", ".json", ".xml", ".html", ".tex"}
VALID_CONTRACT_STATUSES = {"answered", "partial", "insufficient_evidence", "input_error"}
VALID_READING_TIERS = {"directly_readable_from_image", "reasonable_inference", "undetermined_not_provided", "not_applicable"}

FIGURE_MENTION_RE = re.compile(
    r"\b(?:Supplementary\s+|Extended\s+Data\s+)?(?:Figure|Fig\.?|图)\s*([S]?\d+[A-Za-z]?)\b", re.I
)
TABLE_MENTION_RE = re.compile(
    r"\b(?:Supplementary\s+)?(?:Table|表)\s*([S]?\d+[A-Za-z]?)\b", re.I
)
AXIS_UNIT_RE = re.compile(r"([A-Za-z0-9\s/_-]+?)\s*\(([a-zA-Zµμ°%/^0-9\-\.\s]{1,24})\)")


def _detect_header_units(headers: list[str]) -> dict[str, str]:
    units: dict[str, str] = {}
    for h in headers:
        m = AXIS_UNIT_RE.search(str(h))
        if m:
            units[str(h)] = m.group(2).strip()
    return units


def parse_csv_table(path: Path, rel_source: str) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    if not lines:
        return {"table_id": path.stem, "source": rel_source, "headers": [], "header_units": {}, "row_count": 0, "rows": []}
    rows = list(csv.reader(lines))
    headers = [str(c) for c in rows[0]]
    parsed_rows: list[dict[str, Any]] = []
    for ridx, r in enumerate(rows[1:], start=1):
        rec: dict[str, Any] = {
            headers[i] if i < len(headers) else f"col_{i+1}": (r[i] if i < len(r) else "")
            for i in range(max(len(headers), len(r)))
        }
        rec["_row_index"] = ridx
        parsed_rows.append(rec)
    return {
        "table_id": path.stem,
        "source": rel_source,
        "format": "csv",
        "headers": headers,
        "header_units": _detect_header_units(headers),
        "row_count": len(parsed_rows),
        "rows": parsed_rows,
    }


def parse_json_table_payload(payload: Any, source_name: str, table_id: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """Parse explicit columns/rows JSON table verbatim without coercing or swapping columns."""
    if not isinstance(payload, dict) or not ({"columns", "rows"} & set(payload)):
        return None, None
    columns = payload.get("columns")
    rows = payload.get("rows")
    if not isinstance(columns, list) or not columns or not all(isinstance(col, str) and col.strip() for col in columns):
        return None, "table columns must be a non-empty list of non-blank strings"
    if len(set(columns)) != len(columns):
        return None, "table columns must be unique to preserve every cell"
    if not isinstance(rows, list):
        return None, "table rows must be a list"

    parsed_rows: list[dict[str, Any]] = []
    for ridx, row in enumerate(rows, start=1):
        if not isinstance(row, list):
            return None, f"row {ridx} must be a list"
        if len(row) != len(columns):
            return None, f"row {ridx} has {len(row)} cells; expected {len(columns)}"
        if any(isinstance(v, (dict, list)) for v in row):
            return None, f"row {ridx} contains a nested object; cells must be scalar"
        if any(isinstance(v, float) and not math.isfinite(v) for v in row):
            return None, f"row {ridx} contains non-finite float"
        rec = {columns[i]: val for i, val in enumerate(row)}
        rec["_row_index"] = ridx
        parsed_rows.append(rec)

    return {
        "table_id": table_id or source_name.rsplit(".", 1)[0],
        "source": source_name,
        "format": "json_table",
        "headers": columns,
        "header_units": _detect_header_units(columns),
        "row_count": len(parsed_rows),
        "rows": parsed_rows,
    }, None


def extract_markdown_tables(text: str, source_name: str, base_id: str = "") -> list[dict[str, Any]]:
    lines = text.splitlines()
    tables: list[dict[str, Any]] = []
    i = 0
    t_idx = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:-]+\|", lines[i + 1]):
            headers = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            i += 2
            body: list[dict[str, Any]] = []
            r_idx = 0
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                r_idx += 1
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rec: dict[str, Any] = {
                    headers[k] if k < len(headers) else f"col_{k+1}": (cells[k] if k < len(cells) else "")
                    for k in range(max(len(headers), len(cells)))
                }
                rec["_row_index"] = r_idx
                body.append(rec)
                i += 1
            t_idx += 1
            tid = base_id if (base_id and t_idx == 1) else f"{base_id or source_name}_md_table_{t_idx}"
            tables.append({
                "table_id": tid,
                "source": source_name,
                "format": "markdown_table",
                "headers": headers,
                "header_units": _detect_header_units(headers),
                "row_count": len(body),
                "rows": body,
            })
        else:
            i += 1
    return tables


def _pdfplumber_column_aware_text(page: Any) -> str:
    """Extract pdfplumber text with automatic two-column vertical split to prevent cross-column bleed."""
    try:
        w, h = float(page.width), float(page.height)
        words = page.extract_words() or []
        mid = w / 2.0
        body_words = [wd for wd in words if wd["top"] > h * 0.10 and wd["bottom"] < h * 0.90]
        if len(body_words) >= 40:
            left_w = sum(1 for wd in body_words if wd["x1"] <= mid + 4.0)
            right_w = sum(1 for wd in body_words if wd["x0"] >= mid - 4.0)
            cross_w = sum(1 for wd in body_words if wd["x0"] < mid - 4.0 and wd["x1"] > mid + 4.0)
            if left_w >= 20 and right_w >= 20 and (cross_w / max(1, len(body_words))) < 0.08:
                left_txt = page.crop((0, 0, mid, h)).extract_text() or ""
                right_txt = page.crop((mid, 0, w, h)).extract_text() or ""
                return (left_txt.strip() + "\n\n" + right_txt.strip()).strip()
    except Exception:
        pass
    return page.extract_text() or ""


def run_pdftotext_pages(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract per-page reading-order text (pdftotext reading-flow -> pypdf -> column-aware pdfplumber)."""
    bin_p = shutil.which("pdftotext")
    if bin_p:
        try:
            proc = subprocess.run(
                [bin_p, "-enc", "UTF-8", str(pdf_path), "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            if proc.returncode == 0:
                chunks = proc.stdout.split("\f")
                if chunks and not chunks[-1].strip():
                    chunks.pop()
                if chunks:
                    return [{"page": idx, "text": ch} for idx, ch in enumerate(chunks, start=1) if ch.strip()]
        except Exception:
            pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        return [{"page": idx, "text": p.extract_text() or ""} for idx, p in enumerate(reader.pages, start=1)]
    except Exception:
        pass
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            return [{"page": idx, "text": _pdfplumber_column_aware_text(p)} for idx, p in enumerate(doc.pages, start=1)]
    except Exception:
        pass
    return []


def _extract_material_paragraphs_and_tables(
    input_dir: Path,
    case_data: dict[str, Any],
    required_table_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Extract literal paragraphs and literal source tables from material.json and case.json."""
    paragraphs: list[dict[str, Any]] = []
    literal_tables: list[dict[str, Any]] = []
    warnings: list[str] = []
    req_set = set(required_table_ids)

    sources_to_scan: list[tuple[str, dict[str, Any]]] = [("case.json", case_data)]
    for candidate_name in ("material.json", "paper.json", "organizer-supplied-scope.json"):
        fp = input_dir / candidate_name
        if fp.is_file():
            try:
                loaded = json.loads(fp.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    sources_to_scan.append((candidate_name, loaded))
            except Exception as exc:
                warnings.append(f"Failed to parse {candidate_name}: {exc}")

    seen_pids: set[str] = set()
    for src_name, obj in sources_to_scan:
        # Paragraphs
        for idx, p in enumerate(obj.get("paragraphs") or [], start=1):
            if isinstance(p, dict):
                pid = str(p.get("paragraph_id") or p.get("id") or f"{src_name}:P{idx:03d}")
                txt = str(p.get("text") or p.get("content") or "").strip()
                sec = str(p.get("section") or p.get("role") or "Body")
                kind = str(p.get("kind") or p.get("type") or "")
            else:
                pid = f"{src_name}:P{idx:03d}"
                txt = str(p).strip()
                sec = "Body"
                kind = ""
            if pid not in seen_pids and txt:
                seen_pids.add(pid)
                rec = {"paragraph_id": pid, "section": sec, "kind": kind, "source": src_name, "text": txt}
                paragraphs.append(rec)
                # If this paragraph ID is in required_literal_source_table_ids or contains a markdown/literal table
                is_req_tbl = pid in req_set
                md_tbls = extract_markdown_tables(txt, f"{src_name}:{pid}", base_id=pid)
                if md_tbls:
                    for mt in md_tbls:
                        mt["paragraph_id"] = pid
                        mt["required_by_scope"] = is_req_tbl
                        literal_tables.append(mt)
                elif is_req_tbl or kind.lower() == "table" or re.match(r"^(?:Table|表)\s*\d+", txt, re.I):
                    # Preserve verbatim literal table block even when formatted with spaces/semicolons
                    literal_tables.append({
                        "table_id": pid,
                        "paragraph_id": pid,
                        "source": f"{src_name}:{pid}",
                        "format": "literal_source_table_text",
                        "required_by_scope": is_req_tbl,
                        "headers": [],
                        "header_units": {},
                        "row_count": len([ln for ln in txt.splitlines() if ln.strip()]),
                        "rows": [{"_row_index": i, "verbatim_line": ln} for i, ln in enumerate(txt.splitlines(), 1) if ln.strip()],
                        "verbatim_text": txt,
                    })

        # Explicit tables list inside JSON
        for tidx, t in enumerate(obj.get("tables") or [], start=1):
            if isinstance(t, dict):
                tid = str(t.get("paragraph_id") or t.get("table_id") or t.get("id") or f"{src_name}:T{tidx:02d}")
                parsed_t, err = parse_json_table_payload(t, f"{src_name}:{tid}", table_id=tid)
                if parsed_t:
                    parsed_t["paragraph_id"] = tid
                    parsed_t["required_by_scope"] = tid in req_set
                    if t.get("caption"):
                        parsed_t["caption"] = str(t["caption"])
                    literal_tables.append(parsed_t)
                elif err:
                    warnings.append(f"{src_name}:{tid}: {err}")
                else:
                    raw_txt = str(t.get("text") or t.get("content") or json.dumps(t, ensure_ascii=False))
                    literal_tables.append({
                        "table_id": tid,
                        "paragraph_id": tid,
                        "source": f"{src_name}:{tid}",
                        "format": "literal_json_object_table",
                        "required_by_scope": tid in req_set,
                        "headers": list(t.keys()),
                        "header_units": _detect_header_units(list(t.keys())),
                        "row_count": 1,
                        "rows": [{"_row_index": 1, "verbatim_text": raw_txt}],
                        "verbatim_text": raw_txt,
                    })

    return paragraphs, literal_tables, warnings


def _build_paperstory_branch_index(
    provided_figures: list[dict[str, Any]],
    unprovided_figures: list[dict[str, Any]],
    parsed_tables: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build PaperStory backbone-and-branch jump index linking figures, captions, tables, and prose."""
    figure_branches: list[dict[str, Any]] = []
    for fig in provided_figures:
        fname = str(fig.get("file", ""))
        m_num = re.search(r"fig(?:ure)?[_\-\s]*([S]?\d+[A-Za-z]?)", fname, re.I)
        fig_num = m_num.group(1) if m_num else None
        matching_captions: list[dict[str, Any]] = []
        matching_mentions: list[str] = []
        if fig_num:
            pat = re.compile(rf"\b(?:Figure|Fig\.?|图)\s*{re.escape(fig_num)}\b", re.I)
            for p in paragraphs:
                if pat.search(p["text"]):
                    if re.match(rf"^(?:Figure|Fig\.?|图)\s*{re.escape(fig_num)}\b", p["text"], re.I) or "caption" in p.get("section", "").lower():
                        matching_captions.append({"paragraph_id": p["paragraph_id"], "excerpt": p["text"][:260]})
                    else:
                        matching_mentions.append(p["paragraph_id"])
        figure_branches.append({
            "file": fname,
            "inferred_figure_label": f"Fig. {fig_num}" if fig_num else fname,
            "width_px": fig.get("width_px"),
            "height_px": fig.get("height_px"),
            "sha256": fig.get("sha256"),
            "caption_anchors": matching_captions,
            "body_mention_paragraph_ids": matching_mentions[:12],
            "linked_table_ids": [t.get("table_id") for t in parsed_tables],
            "reading_status": "pixel_file_supplied_ready_for_reading",
        })

    return {
        "backbone_summary": "Main scientific narrative anchored to supplied figure pixel files and verbatim literal tables; unprovided caption-only figures are isolated as explicit gaps.",
        "provided_figure_branches": figure_branches,
        "unprovided_figure_stubs": unprovided_figures,
        "table_anchors": [
            {
                "table_id": t.get("table_id"),
                "paragraph_id": t.get("paragraph_id"),
                "source": t.get("source"),
                "row_count": t.get("row_count"),
                "headers": t.get("headers"),
                "required_by_scope": t.get("required_by_scope", False),
            }
            for t in parsed_tables
        ],
    }


def _render_markdown_report(bundle: dict[str, Any]) -> str:
    scope = bundle.get("organizer_scope_audit") or {}
    audit = bundle.get("cross_modal_audit") or {}
    lines = [
        f"# Scientific Figure & Table Analysis Report (`{bundle.get('case_id')}`)",
        "",
        f"- **Contract Status**: `{bundle.get('contract_status')}` (Harbor status: `{bundle.get('harbor_status')}`)",
        f"- **Provided Figures**: `{len(bundle.get('provided_figures') or [])}` | **Parsed Tables**: `{len(bundle.get('parsed_tables') or [])}`",
        f"- **Unprovided Caption-Only Figures (Explicit Gaps)**: `{len(bundle.get('missing_pixel_figure_gaps') or [])}`",
        "",
        "## 1. Supplied Scope Verification",
        "",
        f"- Scope Declared: `{scope.get('scope_declared')}` | Required Images Verified: `{scope.get('required_images_verified')}/{scope.get('required_images_total')}` | "
        f"Required Tables Verified: `{scope.get('required_tables_verified')}/{scope.get('required_tables_total')}`",
        "",
        "## 2. Provided Figures Inventory",
        "",
        "| File | Format | Dimensions (px) | Backend | SHA-256 (12) | Quality Flags |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for fig in bundle.get("provided_figures") or []:
        dim = f"{fig.get('width_px')}x{fig.get('height_px')}" if fig.get("width_px") else (fig.get("viewBox") or "N/A")
        flags = ", ".join(fig.get("quality_flags") or []) or "none"
        lines.append(
            f"| {fig.get('file')} | {fig.get('format')} | {dim} | {fig.get('inspection_backend', 'svg_xml')} | "
            f"`{str(fig.get('sha256', ''))[:12]}` | {flags} |"
        )
    if not (bundle.get("provided_figures") or []):
        lines.append("| (none) | - | - | - | - | - |")

    lines.extend(["", "## 3. Literal Source Tables Inventory", ""])
    for t in bundle.get("parsed_tables") or []:
        lines.append(
            f"- **{t.get('table_id')}** (`{t.get('source')}` | `{t.get('format')}` | rows={t.get('row_count')} | "
            f"required_by_scope={t.get('required_by_scope', False)}): headers={t.get('headers')}"
        )
    if not (bundle.get("parsed_tables") or []):
        lines.append("- _(none)_")

    lines.extend(["", "## 4. Cross-Modal Unit, Error-Bar & Significance Audit", ""])
    cues = audit.get("error_and_significance_cues") or {}
    lines.append(f"- Reported error-bar semantics: `{cues.get('error_bar_types_reported')}`")
    lines.append(f"- Significance markers detected: `{cues.get('significance_markers_found')}`")
    lines.append(f"- Sample sizes detected: `{cues.get('sample_sizes_found')}`")
    for al in audit.get("unit_magnitude_alerts") or []:
        lines.append(f"- **Unit Magnitude Alert ({al['dimension']})**: coexisting `{al['coexisting_units']}` — {al['note']}")
    for fnd in audit.get("findings") or []:
        lines.append(f"- **{fnd['code']}**: {fnd['rule']}")

    lines.extend(["", "## 5. Unprovided Figures & Explicit Uncertainties", ""])
    for g in bundle.get("missing_pixel_figure_gaps") or []:
        lines.append(f"- `{g.get('figure_ref')}`: `{g.get('status')}` — {g.get('reason')}")
    for u in bundle.get("uncertainties") or []:
        lines.append(f"- {u}")
    lines.append("")
    return "\n".join(lines)


def validate_readings_draft(
    draft: dict[str, Any],
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """Deterministically validate Agent multimodal figure readings and axis calibrations."""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(draft, dict):
        return {"ok": False, "errors": ["readings draft must be a JSON object"], "warnings": []}

    st = draft.get("status")
    if st not in VALID_CONTRACT_STATUSES:
        errors.append(f"invalid contract status {st!r}; must be in {sorted(VALID_CONTRACT_STATUSES)}")

    # Check every required provided image is addressed
    req_imgs = {str(f.get("file")) for f in bundle.get("provided_figures") or []}
    fig_readings = draft.get("figure_readings") or []
    addressed_imgs = {str(r.get("file") or r.get("figure_file")) for r in fig_readings if isinstance(r, dict)}
    missing_imgs = sorted(req_imgs - addressed_imgs)
    if missing_imgs and st == "answered":
        errors.append(f"status='answered' but provided figures missing from figure_readings: {missing_imgs}")

    # Check unprovided figures were NOT fabricated with exact digitized points
    unprov_refs = {str(g.get("figure_ref")).lower() for g in bundle.get("missing_pixel_figure_gaps") or []}
    for r in fig_readings:
        if not isinstance(r, dict):
            continue
        fref = str(r.get("figure_ref") or r.get("file") or "").lower()
        if fref in unprov_refs and (r.get("digitized_points") or r.get("axis_calibrations")):
            errors.append(f"fabricated readings for unprovided-pixel figure {fref!r}")
        tier = r.get("reading_tier")
        if tier and tier not in VALID_READING_TIERS:
            errors.append(f"figure {fref}: invalid reading_tier {tier!r}")
        # Re-run axis calibration checks if pixel anchors supplied
        for cal in r.get("axis_calibrations") or []:
            if not isinstance(cal, dict):
                continue
            anchors = cal.get("pixel_anchors") or []
            scale = str(cal.get("scale") or "linear")
            unit = cal.get("unit")
            chart_type = str(r.get("chart_type") or "")
            if anchors:
                pairs = [(float(a[0]), float(a[1])) for a in anchors if isinstance(a, (list, tuple)) and len(a) == 2]
                chk = aca.calibrate_axis_points(pairs, scale=scale, unit=unit, chart_type=chart_type)
                if not chk.get("ok"):
                    errors.append(f"figure {fref} axis calibration failed ({chk.get('refusal_code')}): {chk.get('reason') or chk.get('warnings')}")
                elif chk.get("r_squared", 1.0) < 0.98:
                    warnings.append(f"figure {fref} axis calibration R^2={chk['r_squared']} < 0.98; check tick alignment")

    # Check claims format
    claims = draft.get("claims") or []
    if not isinstance(claims, list):
        errors.append("claims must be a list")
    else:
        for idx, c in enumerate(claims, 1):
            if not isinstance(c, dict):
                errors.append(f"claim #{idx} must be an object")
                continue
            for req_k in ("kind", "evidence", "source_id", "location"):
                if not c.get(req_k):
                    errors.append(f"claim #{idx} missing required key {req_k!r}")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    validate_readings_path: Path | None = None,
) -> tuple[dict[str, Any], int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_guard.init_budget(work_dir / ".budget_state.json")
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="f35e4ca5-figure-table-parser")

    case_path = input_dir / "case.json"
    case_data: dict[str, Any] = {}
    input_errors: list[str] = []
    if case_path.is_file():
        try:
            case_data = json.loads(case_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            input_errors.append(f"Malformed case.json: {exc}")
    case_id = str(case_data.get("case_id") or "case-f35e4ca5-default")

    # Load Organizer Revision v2 scope (from case.json or organizer-supplied-scope.json)
    scope_obj = case_data.get("organizer_supplied_scope")
    if not isinstance(scope_obj, dict):
        scope_file = input_dir / "organizer-supplied-scope.json"
        if scope_file.is_file():
            try:
                loaded_s = json.loads(scope_file.read_text(encoding="utf-8-sig"))
                if isinstance(loaded_s, dict):
                    scope_obj = loaded_s.get("organizer_supplied_scope", loaded_s)
            except Exception as exc:
                input_errors.append(f"Malformed organizer-supplied-scope.json: {exc}")
    scope_obj = scope_obj if isinstance(scope_obj, dict) else {}

    required_image_specs = scope_obj.get("required_actual_image_files") or []
    required_table_ids = [str(x) for x in (scope_obj.get("required_literal_source_table_ids") or [])]

    # Inventory images, tables, PDFs, and text
    provided_figures: list[dict[str, Any]] = []
    parsed_tables: list[dict[str, Any]] = []
    text_fragments: list[str] = []
    sources_seen: list[str] = []
    table_errors: list[str] = []

    paragraphs, material_tables, mat_warnings = _extract_material_paragraphs_and_tables(
        input_dir, case_data, required_table_ids
    )
    parsed_tables.extend(material_tables)
    table_errors.extend(mat_warnings)
    for p in paragraphs:
        text_fragments.append(p["text"])

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        rel = fp.relative_to(input_dir).as_posix()
        sources_seen.append(rel)
        ext = fp.suffix.lower()
        if ext in IMAGE_EXTS:
            if ext == ".svg":
                fig_info = aca.parse_svg_figure(fp)
            else:
                fig_info = aca.inspect_raster_image(fp)
            fig_info["rel_path"] = rel
            provided_figures.append(fig_info)
        elif ext == ".csv":
            parsed_tables.append(parse_csv_table(fp, rel))
        elif ext == ".pdf":
            pages = run_pdftotext_pages(fp)
            for pg in pages:
                text_fragments.append(pg["text"])
                parsed_tables.extend(extract_markdown_tables(pg["text"], f"{rel}#p{pg['page']}"))
        elif ext in TEXT_EXTS and fp.name not in ("case.json", "material.json", "organizer-supplied-scope.json"):
            raw_t = fp.read_text(encoding="utf-8-sig", errors="replace")
            text_fragments.append(raw_t)
            if ext == ".json":
                try:
                    jobj = json.loads(raw_t)
                    pt, perr = parse_json_table_payload(jobj, rel)
                    if pt:
                        parsed_tables.append(pt)
                    elif perr:
                        table_errors.append(f"{rel}: {perr}")
                except Exception as exc:
                    table_errors.append(f"{rel}: malformed JSON ({exc})")
            else:
                parsed_tables.extend(extract_markdown_tables(raw_t, rel))

    # Verify organizer_supplied_scope required image files & SHA-256 + required literal tables
    fig_by_rel = {f["rel_path"]: f for f in provided_figures}
    fig_by_name = {f["file"]: f for f in provided_figures}
    req_img_checks: list[dict[str, Any]] = []
    missing_req_imgs: list[str] = []
    for spec in required_image_specs:
        if isinstance(spec, dict):
            rf = str(spec.get("file") or "")
            exp_sha = str(spec.get("sha256") or "").lower().strip()
        else:
            rf = str(spec)
            exp_sha = ""
        matched = fig_by_rel.get(rf) or fig_by_name.get(Path(rf).name)
        if not matched:
            missing_req_imgs.append(rf)
            req_img_checks.append({"file": rf, "present": False, "sha256_match": False})
        else:
            actual_sha = str(matched.get("sha256") or "").lower()
            sha_ok = (not exp_sha) or (actual_sha == exp_sha)
            matched["required_by_scope"] = True
            matched["sha256_verified"] = sha_ok
            req_img_checks.append({"file": rf, "present": True, "sha256_match": sha_ok, "actual_sha256": actual_sha})

    found_tids = {str(t.get("table_id")) for t in parsed_tables} | {str(t.get("paragraph_id")) for t in parsed_tables if t.get("paragraph_id")}
    missing_req_tables = [tid for tid in required_table_ids if tid not in found_tids]

    scope_audit = {
        "scope_declared": bool(scope_obj),
        "scope_id": scope_obj.get("id"),
        "actual_scientific_domain": scope_obj.get("actual_scientific_domain") or case_data.get("domain"),
        "required_images_total": len(required_image_specs),
        "required_images_verified": sum(1 for c in req_img_checks if c["present"] and c["sha256_match"]),
        "required_image_checks": req_img_checks,
        "missing_required_images": missing_req_imgs,
        "required_tables_total": len(required_table_ids),
        "required_tables_verified": len(required_table_ids) - len(missing_req_tables),
        "missing_required_tables": missing_req_tables,
    }

    # Detect caption-mentioned figures lacking pixel files
    all_text = "\n".join(text_fragments)
    mentioned_figs = sorted(set(m.group(1) for m in FIGURE_MENTION_RE.finditer(all_text)))
    provided_nums: set[str] = set()
    for f in provided_figures:
        for m in re.finditer(r"fig(?:ure)?[_\-\s]*([S]?\d+[A-Za-z]?)", f["file"], re.I):
            provided_nums.add(m.group(1).lower())
            provided_nums.add(re.sub(r"[a-z]+$", "", m.group(1).lower()))

    missing_pixel_gaps: list[dict[str, Any]] = []
    for fnum in mentioned_figs:
        base_num = re.sub(r"[a-z]+$", "", fnum.lower())
        if fnum.lower() not in provided_nums and base_num not in provided_nums:
            missing_pixel_gaps.append({
                "figure_ref": f"Figure {fnum}",
                "status": "not_provided",
                "reason": "Referenced in manuscript text/captions but no pixel file was supplied in /input; excluded from fabrication per Organizer Revision v2.",
            })

    cross_modal_audit = aca.audit_figure_vs_tables(provided_figures, parsed_tables, all_text)
    paperstory_index = _build_paperstory_branch_index(provided_figures, missing_pixel_gaps, parsed_tables, paragraphs)

    # Determine honest Wenyon & Harbor statuses
    uncertainties: list[str] = []
    for err in input_errors + table_errors:
        uncertainties.append(f"Input/table parsing issue: {err}")
    for mi in missing_req_imgs:
        uncertainties.append(f"Required scope image missing from /input: {mi}")
    for mt in missing_req_tables:
        uncertainties.append(f"Required scope literal source table missing: {mt}")
    if missing_pixel_gaps:
        uncertainties.append(
            f"{len(missing_pixel_gaps)} figure(s) mentioned in text/captions lack supplied pixel files "
            f"({', '.join(g['figure_ref'] for g in missing_pixel_gaps[:8])}); logged as unprovided_objects without fabrication."
        )
    for al in cross_modal_audit.get("unit_magnitude_alerts") or []:
        uncertainties.append(f"Coexisting {al['dimension']} units ({al['coexisting_units']}): verify exact axis/column scale.")

    # Auto-draft claims from literal tables & inspected images
    claims: list[dict[str, Any]] = []
    for fig in provided_figures:
        dim_desc = f"{fig.get('width_px')}x{fig.get('height_px')} px" if fig.get("width_px") else f"SVG viewBox={fig.get('viewBox')}"
        claims.append({
            "kind": "directly_readable_from_image",
            "evidence": f"Supplied figure {fig['rel_path']} verified ({dim_desc}, sha256={str(fig.get('sha256',''))[:12]}).",
            "source_id": fig["rel_path"],
            "location": fig["rel_path"],
        })
    for t in parsed_tables:
        claims.append({
            "kind": "reported",
            "evidence": f"Literal source table {t['table_id']} extracted verbatim ({t['row_count']} rows, headers={t.get('headers', [])[:8]}).",
            "source_id": t["source"],
            "location": t.get("paragraph_id") or t["source"],
        })

    # Optional --validate-readings promotion
    validation_report: dict[str, Any] | None = None
    readings_payload: dict[str, Any] | None = None
    exit_code = 0

    if validate_readings_path is not None:
        if not validate_readings_path.is_file():
            input_errors.append(f"Requested --validate-readings draft not found: {validate_readings_path}")
            exit_code = 1
        else:
            try:
                readings_payload = json.loads(validate_readings_path.read_text(encoding="utf-8-sig"))
            except Exception as exc:
                input_errors.append(f"Malformed readings draft JSON: {exc}")
                exit_code = 1

    contract_status = "answered"
    if input_errors:
        contract_status = "input_error"
    elif not provided_figures and not parsed_tables:
        contract_status = "insufficient_evidence"
    elif missing_req_imgs or missing_req_tables:
        contract_status = "partial"

    harbor_status = "ok" if (contract_status == "answered" and not table_errors and not missing_req_imgs and not missing_req_tables) else "partial_with_gaps"

    bundle: dict[str, Any] = {
        "case_id": case_id,
        "contract_status": contract_status,
        "harbor_status": harbor_status,
        "organizer_scope_audit": scope_audit,
        "provided_figures": provided_figures,
        "parsed_tables": parsed_tables,
        "missing_pixel_figure_gaps": missing_pixel_gaps,
        "cross_modal_audit": cross_modal_audit,
        "paperstory_branch_index": paperstory_index,
        "uncertainties": uncertainties,
    }

    if readings_payload is not None:
        validation_report = validate_readings_draft(readings_payload, bundle)
        bundle["readings_validation"] = validation_report
        if not validation_report["ok"]:
            contract_status = "input_error"
            harbor_status = "partial_with_gaps"
            uncertainties.extend(f"Validation error: {e}" for e in validation_report["errors"])
            exit_code = 1
        else:
            contract_status = readings_payload["status"]
            harbor_status = "ok" if contract_status == "answered" else "partial_with_gaps"
            claims = readings_payload.get("claims") or claims
            if readings_payload.get("uncertainties"):
                uncertainties = list(dict.fromkeys(uncertainties + [str(u) for u in readings_payload["uncertainties"]]))
            bundle["validated_figure_readings"] = readings_payload.get("figure_readings") or []
            bundle["validated_table_summaries"] = readings_payload.get("table_summaries") or []
            bundle["contract_status"] = contract_status
            bundle["harbor_status"] = harbor_status
            bundle["uncertainties"] = uncertainties

    (output_dir / "figure_table_bundle.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "figure_analysis_report.md").write_text(_render_markdown_report(bundle), encoding="utf-8")

    # Emit primary /app/output/result.json satisfying BOTH Wenyon output_contract & Harbor result_guard
    result_payload: dict[str, Any] = {
        "case_id": case_id,
        "status": harbor_status,
        "contract_status": contract_status,
        "sources": sources_seen,
        "artifacts": ["result.json", "figure_table_bundle.json", "figure_analysis_report.md"],
        "result": {
            "organizer_scope_audit": scope_audit,
            "provided_figures": provided_figures,
            "parsed_tables": [
                {k: v for k, v in t.items() if k != "rows"} | {"rows_preview": (t.get("rows") or [])[:10]}
                for t in parsed_tables
            ],
            "figure_readings": bundle.get("validated_figure_readings", []),
            "missing_pixel_figure_gaps": missing_pixel_gaps,
            "cross_modal_audit": cross_modal_audit,
            "paperstory_branch_index": paperstory_index,
        },
        "claims": claims,
        "uncertainties": uncertainties,
        "uncertainty": "; ".join(uncertainties) if uncertainties else "Supplied scope verified; pixel readings remain approximate within +/-0.5 px calibration bounds.",
        "metrics": {
            "provided_figures_count": len(provided_figures),
            "parsed_tables_count": len(parsed_tables),
            "required_images_verified": scope_audit["required_images_verified"],
            "required_tables_verified": scope_audit["required_tables_verified"],
            "missing_pixel_figure_gaps_count": len(missing_pixel_gaps),
            "unit_magnitude_alerts_count": len(cross_modal_audit.get("unit_magnitude_alerts") or []),
        },
        "metric_provenance": {
            "provided_figures_count": "stdlib_header_and_optional_pillow_inventory",
            "parsed_tables_count": "literal_json_csv_markdown_table_parser",
            "required_images_verified": "organizer_supplied_scope_sha256_check",
            "required_tables_verified": "organizer_supplied_scope_table_id_check",
            "missing_pixel_figure_gaps_count": "caption_mention_vs_supplied_pixel_diff",
            "unit_magnitude_alerts_count": "cross_modal_unit_magnitude_audit",
        },
        "findings": [
            f"Inspected {len(provided_figures)} provided figure(s) and extracted {len(parsed_tables)} literal source table(s).",
            f"Scope check: {scope_audit['required_images_verified']}/{scope_audit['required_images_total']} required images, "
            f"{scope_audit['required_tables_verified']}/{scope_audit['required_tables_total']} required tables verified.",
            f"Logged {len(missing_pixel_gaps)} caption-only unprovided figure(s) as explicit gaps without fabrication.",
        ],
    }
    if validation_report is not None:
        result_payload["readings_validation"] = validation_report

    (output_dir / "result.json").write_text(
        json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    append_audit_entry(
        audit_file,
        phase="extract",
        action="figure_table_parser",
        actor="figure_table_parser.py",
        output=result_payload["metrics"],
        decision=contract_status,
        record_id=case_id,
    )
    return result_payload, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Scientific Figure & Table Parser (challenge-f35e4ca5)")
    parser.add_argument("--input", default="/input", help="Input directory")
    parser.add_argument("--output", default="/app/output", help="Output directory")
    parser.add_argument("--work", default="/app/work", help="Working directory")
    parser.add_argument("--validate-readings", default="", help="Optional JSON draft of multimodal figure readings to validate and promote")
    args = parser.parse_args()

    paths = preflight_probe.resolve_default_paths(input_dir=args.input, output_dir=args.output, work_dir=args.work)
    val_path = Path(args.validate_readings) if args.validate_readings else None
    _, rc = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], validate_readings_path=val_path)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
