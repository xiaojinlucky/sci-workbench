#!/usr/bin/env python3
"""Deterministic raw /input -> route_spec.json extractor with operating-param grounding & missing_params audit (stdlib-only)."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PARAM_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:°C|degC|C|K|MPa|kPa|Pa|bar|mbar|atm|mL/min|L/min|sccm|uL/min|nm|mW|W|mA|V|Hz|kHz|MHz|rpm|h|min|s|wt%|vol%|%)\b",
    re.I,
)
_STAGE_HDR_RE = re.compile(r"^(?:#{1,4}\s*|(?:stage|step|phase|zone)\s*(\d+)\s*[:.\-–])\s*(.+)$", re.I)
_STEP_ITEM_RE = re.compile(r"^(?:[-*]\s*|\d+[.)]\s*)(.+)$")
_STREAM_TYPES = (
    ("feedback", ("feedback", "recycle", "return", "pid", "loop", "closed-loop")),
    ("optical", ("laser", "lamp", "uv", "led", "beam", "optical", "photon", "xenon", "nm")),
    ("signal", ("signal", "data", "daq", "sensor", "thermocouple", "gc", "ms", "chromatograph", "readout", "voltage", "current")),
    ("control", ("control", "valve", "mfc", "trigger", "relay", "actuat")),
    ("waste", ("waste", "exhaust", "vent", "purge", "drain")),
)


def _read_lines(path: Path) -> list[tuple[int, str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        exe = shutil.which("pdftotext")
        if not exe:
            return []
        proc = subprocess.run([exe, str(path), "-"], capture_output=True, text=True, check=False, timeout=30)
        return [(i, ln.strip()) for i, ln in enumerate(proc.stdout.splitlines(), 1) if ln.strip()] if proc.returncode == 0 else []
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    if ext in (".html", ".htm", ".xml"):
        raw = re.sub(r"</(?:p|div|h[1-6]|li|tr|section|article)\s*>", "\n", raw, flags=re.I)
        return [(i, _WS_RE.sub(" ", html.unescape(_HTML_TAG_RE.sub(" ", ln))).strip()) for i, ln in enumerate(raw.splitlines(), 1) if len(ln.strip()) >= 8]
    return [(i, ln.strip()) for i, ln in enumerate(raw.splitlines(), 1) if ln.strip()]


def _guess_stream_type(text: str) -> str:
    low = text.lower()
    for stype, kws in _STREAM_TYPES:
        if any(kw in low for kw in kws):
            return stype
    return "material"


def _san_id(text: str, idx: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:18]
    return s or f"unit_{idx}"


def extract_route_from_input(input_dir: Path) -> dict[str, Any]:
    """Deterministically extract apparatus zones, units, operating specs, ports & typed streams from raw /input."""
    zones: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    streams: list[dict[str, Any]] = []
    not_found: list[str] = []
    title = ""
    case_obj: dict[str, Any] = {}
    lines_indexed = 0

    cur_zone = "stage_1"
    zone_map: dict[str, dict[str, Any]] = {}
    csv_units_loaded = False

    for p in sorted(input_dir.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(input_dir).as_posix()
        ext = p.suffix.lower()
        if p.name == "case.json":
            try:
                case_obj = json.loads(p.read_text(encoding="utf-8-sig"))
                title = str(case_obj.get("title") or "").strip()
            except Exception:
                pass
            continue
        if ext == ".csv":
            with p.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames and any(c.lower() in ("unit", "component", "equipment", "step", "stage") for c in reader.fieldnames):
                    csv_units_loaded = True
                    for idx, row in enumerate(reader, 1):
                        lbl = str(row.get("unit") or row.get("component") or row.get("equipment") or row.get("label") or "").strip()
                        if not lbl:
                            continue
                        zname = str(row.get("stage") or row.get("zone") or f"Stage {(idx-1)//2 + 1}").strip()
                        zid = _san_id(zname, len(zone_map) + 1)
                        if zid not in zone_map:
                            zone_map[zid] = {"id": zid, "label": zname, "stage_order": len(zone_map) + 1, "source_ref": f"{rel}#R{idx}"}
                        spec_txt = str(row.get("spec") or row.get("spec_text") or row.get("parameters") or "").strip()
                        uid = _san_id(lbl, idx)
                        u_obj: dict[str, Any] = {
                            "id": uid,
                            "label": lbl[:28],
                            "zone": zid,
                            "step_number": len(units) + 1,
                            "role": "inlet" if len(units) == 0 else ("sensor" if any(k in lbl.lower() for k in ("gc", "ms", "sensor", "detector", "spectrom")) else "unit"),
                            "category": "accent" if any(k in lbl.lower() for k in ("gc", "ms", "sensor", "detector")) else "primary",
                            "spec_text": spec_txt[:30],
                            "ports": ["in:main", "out:main"],
                            "source_ref": f"{rel}#R{idx}",
                        }
                        if not spec_txt:
                            u_obj["missing_params"] = ["operating_spec_not_found_in_source"]
                        units.append(u_obj)
            continue
        if ext in (".md", ".txt", ".html", ".htm", ".pdf") and not csv_units_loaded:
            for lno, text in _read_lines(p):
                lines_indexed += 1
                if not title and (text.startswith("# ") or (lno <= 10 and 14 <= len(text) <= 105)):
                    title = text.lstrip("# ").strip()
                    continue
                m_hdr = _STAGE_HDR_RE.match(text)
                if m_hdr and len(text) <= 85:
                    z_lbl = m_hdr.group(2).strip()
                    zid = f"stage_{len(zone_map)+1}"
                    zone_map[zid] = {"id": zid, "label": z_lbl[:32], "stage_order": len(zone_map) + 1, "source_ref": f"{rel}#L{lno}"}
                    cur_zone = zid
                    continue
                m_it = _STEP_ITEM_RE.match(text)
                # Also parse Markdown table rows: | Stage | Unit | Spec | ... |
                is_md_row = text.startswith("|") and text.endswith("|") and text.count("|") >= 3 and not set(text.replace("|", "").replace("-", "").replace(":", "").strip()) == set()
                if is_md_row and len(units) < 10:
                    cells = [c.strip() for c in text.strip("|").split("|")]
                    if cells and cells[0].lower() not in ("stage", "zone", "unit", "component", "step", "id", "阶段", "单元"):
                        z_lbl = cells[0] if len(cells) >= 3 else cur_zone
                        u_lbl = cells[1] if len(cells) >= 3 else cells[0]
                        sp_txt = cells[2] if len(cells) >= 3 else (cells[1] if len(cells) >= 2 else "")
                        zid = _san_id(z_lbl, len(zone_map) + 1)
                        if zid not in zone_map:
                            zone_map[zid] = {"id": zid, "label": z_lbl[:32], "stage_order": len(zone_map) + 1, "source_ref": f"{rel}#L{lno}"}
                        uid = _san_id(u_lbl, len(units) + 1)
                        if any(u["id"] == uid for u in units):
                            uid = f"{uid}_{len(units)+1}"
                        u_obj = {
                            "id": uid, "label": u_lbl[:28], "zone": zid, "step_number": len(units) + 1,
                            "role": "inlet" if len(units) == 0 else ("sensor" if any(k in u_lbl.lower() for k in ("sensor", "detector", "gc", "ms", "analyzer")) else "unit"),
                            "category": "primary", "spec_text": sp_txt[:30], "ports": ["in:main", "out:main"],
                            "source_ref": f"{rel}#L{lno}", "_raw": " ".join(cells),
                        }
                        if not sp_txt or "tbd" in sp_txt.lower():
                            u_obj["spec_text"] = ""
                            u_obj["missing_params"] = ["operating_spec_not_found_in_source"]
                        units.append(u_obj)
                    continue
                if m_it and len(units) < 8:
                    body = m_it.group(1).strip()
                    if len(body) < 8:
                        continue
                    if cur_zone not in zone_map:
                        zone_map[cur_zone] = {"id": cur_zone, "label": "Stage 1: Apparatus Flow", "stage_order": 1, "source_ref": f"{rel}#L{lno}"}
                    # Split unit name from parameters
                    name_part = re.split(r"[:(\[—–]|\s-\s", body, maxsplit=1)[0].strip()[:28]
                    params = _PARAM_RE.findall(body)
                    spec_txt = ", ".join(params[:2])
                    uid = _san_id(name_part, len(units) + 1)
                    if any(u["id"] == uid for u in units):
                        uid = f"{uid}_{len(units)+1}"
                    role = "inlet" if len(units) == 0 else ("sensor" if any(k in body.lower() for k in ("sensor", "detector", "gc", "ms", "spectrom", "analyzer")) else "unit")
                    u_obj = {
                        "id": uid,
                        "label": name_part or f"Unit {len(units)+1}",
                        "zone": cur_zone,
                        "step_number": len(units) + 1,
                        "role": role,
                        "category": "accent" if role == "sensor" else ("warning" if any(k in body.lower() for k in ("lamp", "laser", "heater", "furnace", "reactor")) else "primary"),
                        "spec_text": spec_txt[:30],
                        "ports": ["in:main", "out:main"],
                        "source_ref": f"{rel}#L{lno}",
                        "_raw": body,
                    }
                    if not spec_txt:
                        u_obj["missing_params"] = ["operating_spec_not_found_in_source"]
                    units.append(u_obj)

    if units:
        units[-1]["role"] = "outlet"
    else:
        not_found.append("apparatus_units")

    zones = sorted(zone_map.values(), key=lambda z: z["stage_order"])
    # Create forward monotonic streams connecting sequential units
    for idx in range(len(units) - 1):
        u1, u2 = units[idx], units[idx + 1]
        raw_ctx = f"{u1.get('_raw', '')} {u2.get('_raw', '')}"
        stype = _guess_stream_type(raw_ctx)
        if stype == "feedback":
            stype = "material"
        streams.append({
            "id": f"s{idx+1}",
            "source": u1["id"],
            "target": u2["id"],
            "source_port": "out:main",
            "target_port": "in:main",
            "stream_type": stype,
            "label": stype,
            "source_ref": u2["source_ref"],
        })

    # Detect explicit PID/pressure control loop & optical/energy couplings with verbatim anchors and confirmation flags
    pending_confirmations: list[dict[str, Any]] = []
    ctrl_unit = next((u for u in units[1:] if re.search(r"\b(?:pid|pressure\s+control|feedback\s+loop|closed-loop|recycle)\b", str(u.get("_raw", "")), re.I)), None)
    if ctrl_unit is not None and len(units) >= 3:
        raw_c = str(ctrl_unit.get("_raw", ""))
        is_pid = bool(re.search(r"\b(?:pid|pressure\s+control|regulator|valve)\b", raw_c, re.I))
        # Only assert confirmed direction if source explicitly states feedback target (e.g., "feeds back to", "returned to")
        explicit_dir = bool(re.search(r"\b(?:feeds?\s+back\s+to|return(?:ed|s)?\s+to|recycled?\s+to|controls?\s+(?:the\s+)?inlet)\b", raw_c, re.I))
        ctrl_unit["control_loop"] = is_pid
        pending_confirmations.append({
            "kind": "pid_control_loop" if is_pid else "recycle_loop",
            "unit_id": ctrl_unit["id"],
            "direction_confirmed": explicit_dir,
            "source_ref": ctrl_unit["source_ref"],
            "verbatim_excerpt": raw_c[:180],
            "note": "Control-loop target direction confirmed verbatim in source." if explicit_dir else "Source mentions PID/control loop without naming the upstream actuator target; confirm direction before finalizing edge.",
        })
        if explicit_dir:
            ctrl_unit["ports"] = sorted(set(list(ctrl_unit.get("ports") or []) + ["ctrl:pid_out"]))
            units[0]["ports"] = sorted(set(list(units[0].get("ports") or []) + ["ctrl:pid_in"]))
            streams.append({
                "id": f"s{len(streams)+1}",
                "source": ctrl_unit["id"],
                "target": units[0]["id"],
                "source_port": "ctrl:pid_out",
                "target_port": "ctrl:pid_in",
                "stream_type": "feedback",
                "control_loop": is_pid,
                "direction_confirmed": True,
                "signal_kind": "pid_control_loop" if is_pid else "material_recycle",
                "label": "PID control loop" if is_pid else "feedback loop",
                "source_ref": ctrl_unit["source_ref"],
            })
        else:
            not_found.append(f"control_loop_direction_unconfirmed:{ctrl_unit['id']}")

    # Capture optical/illumination energy coupling (e.g., 420 nm LED / laser) as explicit machine-readable annotation/port
    for u in units:
        raw_u = str(u.get("_raw", ""))
        m_opt = re.search(r"(\d+(?:\.\d+)?\s*nm\b[^.;]{0,60}|(?:led|laser|uv|xenon|solar)\s+(?:lamp|array|source|irradiat\w*)[^.;]{0,60})", raw_u, re.I)
        if m_opt:
            u["ports"] = sorted(set(list(u.get("ports") or []) + ["opt:illumination"]))
            u["energy_coupling"] = {
                "kind": "optical_illumination",
                "port": "opt:illumination",
                "source_ref": u["source_ref"],
                "verbatim_excerpt": m_opt.group(0).strip(),
                "direction_confirmed": True,
            }
            pending_confirmations.append({
                "kind": "optical_energy_coupling",
                "unit_id": u["id"],
                "port": "opt:illumination",
                "direction_confirmed": True,
                "source_ref": u["source_ref"],
                "verbatim_excerpt": m_opt.group(0).strip(),
            })

    clean_units = [{k: v for k, v in u.items() if k != "_raw"} for u in units]
    missing_param_units = [u["id"] for u in clean_units if u.get("missing_params")]
    if missing_param_units:
        not_found.extend(f"operating_param:{uid}" for uid in missing_param_units)

    return {
        "title": title or "Experimental Apparatus & Technical Route (Auto-Extracted Draft)",
        "subtitle": f"Derived deterministically from {input_dir.name} ({len(clean_units)} units, {len(streams)} streams)",
        "canvas_width_px": max(1040, len(zones) * 285 + 60),
        "canvas_height_px": 660,
        "zones": zones,
        "units": clean_units,
        "streams": streams,
        "pending_relation_confirmations": pending_confirmations,
        "extraction_provenance": {
            "extractor": "extract_route_spec.py",
            "input_dir": str(input_dir.name),
            "lines_indexed": lines_indexed,
            "zones_extracted": len(zones),
            "units_extracted": len(clean_units),
            "streams_extracted": len(streams),
            "pending_relation_confirmations_count": len(pending_confirmations),
            "units_missing_operating_params": missing_param_units,
            "not_found_in_source": not_found,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministically extract route_spec.json from raw /input files.")
    parser.add_argument("--input-dir", required=True, help="Raw input directory (PDF/HTML/MD/TXT/CSV/case.json)")
    parser.add_argument("--out", required=True, help="Output route_spec.json path")
    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    if not in_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"Input directory not found: {in_dir}"}, indent=2))
        return 2
    spec = extract_route_from_input(in_dir)
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_p), "extraction_provenance": spec["extraction_provenance"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
