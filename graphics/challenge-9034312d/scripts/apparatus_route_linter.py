#!/usr/bin/env python3
"""Topology, reachability, stage/step monotonicity, port/parameter gap, and stream-encoding linter for apparatus & technical route figures."""

from __future__ import annotations

import argparse
import json
import re
from collections import deque
from pathlib import Path
from typing import Any

try:
    import defusedxml.ElementTree as _SafeET  # type: ignore
except ImportError:  # pragma: no cover
    _SafeET = None
import xml.etree.ElementTree as _StdET

ALLOWED_STREAMS = {
    "material", "fluid", "reagent", "optical", "laser", "electrical",
    "signal", "data", "control", "waste", "exhaust", "feedback", "recycle",
}
BACKWARD_ALLOWED = {"feedback", "recycle"}
INLET_ROLES = {"inlet", "source", "feed", "start"}
OUTLET_ROLES = {"outlet", "target", "collector", "milestone", "output", "sink"}
_MISSING_PARAM_RE = re.compile(r"\b(?:TBD|MISSING|UNKNOWN|N/A|UNSPECIFIED)\b", re.IGNORECASE)
_BACKEND_LEAK_RE = re.compile(r"\b(?:QC_[A-Za-z0-9_]+|post_basic|preselector|in-sample|debug_)\b|#[0-9A-Fa-f]{6}\b", re.IGNORECASE)


def lint_route_spec(spec: dict[str, Any], svg_path: Path | None = None) -> dict[str, Any]:
    """Audit apparatus/roadmap JSON spec and optional generated SVG for structural, topological, and provenance soundness."""
    errors: list[str] = []
    warnings: list[str] = []
    gaps: list[dict[str, str]] = []

    zones = {str(z["id"]): z for z in (spec.get("zones") or []) if isinstance(z, dict) and z.get("id")}
    units = spec.get("units") or []
    streams = spec.get("streams") or []

    unit_map: dict[str, dict[str, Any]] = {}
    for idx, u in enumerate(units):
        uid = str(u.get("id") or f"u{idx+1}")
        if uid in unit_map:
            errors.append(f"Duplicate unit id: {uid!r}")
        unit_map[uid] = u
        if not str(u.get("source_ref", "")).strip():
            gaps.append({"unit": uid, "code": "MISSING_SOURCE_REF", "reason": "missing source_ref provenance"})
        z_id = str(u.get("zone", "")).strip()
        if zones and z_id and z_id not in zones:
            errors.append(f"Unit {uid!r} references unknown zone {z_id!r}")
        # Check declared missing operating parameters (record honestly as gaps, never hallucinate)
        spec_txt = str(u.get("spec_text", ""))
        if _MISSING_PARAM_RE.search(spec_txt):
            gaps.append({"unit": uid, "code": "MISSING_OPERATING_PARAM", "reason": f"unresolved operating parameter in spec_text: {spec_txt!r}"})
        for mp in (u.get("missing_params") or []):
            gaps.append({"unit": uid, "code": "MISSING_OPERATING_PARAM", "reason": f"unspecified parameter {mp!r} on unit {uid!r}"})
        for field in ("label", "spec_text"):
            val = str(u.get(field, ""))
            if val and _BACKEND_LEAK_RE.search(val):
                errors.append(f"BACKEND_TERM_LEAK on unit {uid!r}.{field}: {val!r}")

    if not unit_map:
        errors.append("Spec contains zero apparatus/roadmap units")

    adj_forward: dict[str, list[str]] = {uid: [] for uid in unit_map}
    degree: dict[str, int] = {uid: 0 for uid in unit_map}
    stream_types_seen: set[str] = set()
    forward_recycle_notes: list[str] = []

    for idx, st in enumerate(streams):
        sid = str(st.get("id") or f"s{idx+1}")
        src = str(st.get("source", ""))
        tgt = str(st.get("target", ""))
        stype = str(st.get("stream_type", "material")).lower().strip()
        stream_types_seen.add(stype)

        if stype not in ALLOWED_STREAMS:
            warnings.append(f"Stream {sid!r} uses non-standard stream_type {stype!r}")
        if src not in unit_map or tgt not in unit_map:
            errors.append(f"Stream {sid!r} connects missing unit {src!r} -> {tgt!r}")
            continue
        if st.get("label") and _BACKEND_LEAK_RE.search(str(st.get("label"))):
            errors.append(f"BACKEND_TERM_LEAK on stream {sid!r}.label: {st.get('label')!r}")

        degree[src] += 1
        degree[tgt] += 1

        z_src = str(unit_map[src].get("zone", ""))
        z_tgt = str(unit_map[tgt].get("zone", ""))
        ord_src = zones[z_src].get("stage_order") if z_src in zones else unit_map[src].get("stage_order")
        ord_tgt = zones[z_tgt].get("stage_order") if z_tgt in zones else unit_map[tgt].get("stage_order")

        is_backward_stage = isinstance(ord_src, (int, float)) and isinstance(ord_tgt, (int, float)) and ord_tgt < ord_src
        if is_backward_stage and stype not in BACKWARD_ALLOWED:
            errors.append(
                f"Backward stage flow on non-feedback stream {sid!r} ({src}[{ord_src}] -> {tgt}[{ord_tgt}]; type={stype})"
            )

        # Check step_number monotonicity for numbered technical roadmaps
        step_src = unit_map[src].get("step_number")
        step_tgt = unit_map[tgt].get("step_number")
        if isinstance(step_src, (int, float)) and isinstance(step_tgt, (int, float)):
            if step_tgt < step_src and stype not in BACKWARD_ALLOWED:
                errors.append(f"Step number inversion on forward stream {sid!r}: step {step_src} -> {step_tgt}")

        # Primary forward reachability classification:
        # Feedback/recycle are side paths by default; a strictly forward downstream recovery stream
        # may count as primary ONLY when explicitly flagged is_primary_forward=True and ord_tgt > ord_src.
        counts_as_primary = (stype not in BACKWARD_ALLOWED) or (
            stype == "recycle"
            and bool(st.get("is_primary_forward", False))
            and isinstance(ord_src, (int, float))
            and isinstance(ord_tgt, (int, float))
            and ord_tgt > ord_src
        )
        if counts_as_primary:
            adj_forward[src].append(tgt)
            if stype == "recycle":
                forward_recycle_notes.append(f"Stream {sid!r} counted as explicit forward downstream recovery ({src} -> {tgt})")

        # Optional port-endpoint verification
        src_port = str(st.get("source_port", "")).strip()
        tgt_port = str(st.get("target_port", "")).strip()
        if src_port and unit_map[src].get("ports") and src_port not in [str(p) for p in unit_map[src]["ports"]]:
            errors.append(f"Stream {sid!r} source_port {src_port!r} not declared on unit {src!r}")
        if tgt_port and unit_map[tgt].get("ports") and tgt_port not in [str(p) for p in unit_map[tgt]["ports"]]:
            errors.append(f"Stream {sid!r} target_port {tgt_port!r} not declared on unit {tgt!r}")

        if not str(st.get("source_ref", "")).strip():
            gaps.append({"stream": sid, "code": "MISSING_SOURCE_REF", "reason": "missing source_ref provenance"})

    # Orphan check
    if len(unit_map) > 1:
        orphans = [uid for uid, deg in degree.items() if deg == 0]
        if orphans:
            errors.append(f"Disconnected orphan apparatus units: {orphans}")

    # Primary forward cycle check
    state: dict[str, int] = {uid: 0 for uid in unit_map}

    def _dfs_cycle(node: str) -> bool:
        state[node] = 1
        for nxt in adj_forward.get(node, []):
            if state[nxt] == 1:
                return True
            if state[nxt] == 0 and _dfs_cycle(nxt):
                return True
        state[node] = 2
        return False

    for uid in unit_map:
        if state[uid] == 0 and _dfs_cycle(uid):
            errors.append("Primary forward route contains an unlabeled cycle (mark return paths as stream_type='feedback')")
            break

    # Primary forward reachability from inlets to outlets
    inlets = [uid for uid, u in unit_map.items() if str(u.get("role", "")).lower() in INLET_ROLES]
    outlets = {uid for uid, u in unit_map.items() if str(u.get("role", "")).lower() in OUTLET_ROLES}
    reachable_from_inlet: set[str] = set()
    if inlets and outlets:
        q = deque(inlets)
        while q:
            cur = q.popleft()
            if cur in reachable_from_inlet:
                continue
            reachable_from_inlet.add(cur)
            for nxt in adj_forward.get(cur, []):
                if nxt not in reachable_from_inlet:
                    q.append(nxt)
        unreached_outlets = sorted(outlets - reachable_from_inlet)
        if unreached_outlets:
            errors.append(f"No primary forward route from inlets {inlets} to outlet/target units: {unreached_outlets}")

    svg_checks: dict[str, Any] = {}
    if svg_path is not None and svg_path.exists():
        raw = svg_path.read_bytes()
        root = _SafeET.fromstring(raw) if _SafeET is not None else _StdET.fromstring(raw)
        ids = {el.get("id") for el in root.iter() if el.get("id")}
        matched_units = sum(1 for uid in unit_map if f"unit-{uid}" in ids)
        matched_streams = sum(1 for idx, st in enumerate(streams) if f"stream-{st.get('id', f's{idx+1}')}" in ids)
        svg_checks = {
            "svg_units_matched": matched_units,
            "svg_streams_matched": matched_streams,
            "all_units_present": matched_units == len(unit_map),
            "all_streams_present": matched_streams == len(streams),
        }
        if not svg_checks["all_units_present"]:
            errors.append(f"SVG missing unit elements ({matched_units}/{len(unit_map)})")
        if not svg_checks["all_streams_present"]:
            errors.append(f"SVG missing stream elements ({matched_streams}/{len(streams)})")

    return {
        "passed": len(errors) == 0,
        "zone_count": len(zones),
        "unit_count": len(unit_map),
        "stream_count": len(streams),
        "stream_types": sorted(stream_types_seen),
        "inlets": inlets,
        "outlets": sorted(outlets),
        "outlets_reachable_on_primary_route": sorted(outlets & reachable_from_inlet),
        "forward_recycle_notes": forward_recycle_notes,
        "missing_provenance_count": len(gaps),
        "blocking_errors": errors,
        "warnings": warnings,
        "gaps": gaps,
        "svg_checks": svg_checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint experimental apparatus & technical route specification.")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--svg", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    svg_p = Path(args.svg) if args.svg else None
    rep = lint_route_spec(spec, svg_path=svg_p)
    out_txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(out_txt + "\n", encoding="utf-8")
    print(out_txt)
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
