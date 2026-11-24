#!/usr/bin/env python3
"""Biological mechanism & pathway nomenclature, compartment polarity, data-encoding, and provenance linter."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as _StdET
from pathlib import Path
from typing import Any

try:
    import defusedxml.ElementTree as _SafeET
except ImportError:
    _SafeET = None

VALID_COMPARTMENTS = {
    "extracellular", "plasma_membrane", "membrane", "cytoplasm", "cytosol",
    "mitochondria", "er", "golgi", "lysosome", "endosome", "nucleus", "vessel", "stroma",
}
VALID_ENTITY_TYPES = {
    "gene", "protein", "complex", "family", "metabolite", "ligand",
    "receptor", "rna", "cell_type", "process", "compartment",
}
VALID_POLARITIES = {
    "activate", "inhibit", "bind", "modify", "transport", "transcribe", "secrete",
}

_FORBIDDEN_XML = re.compile(r"<!(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)\b", re.IGNORECASE)
_BACKEND_LEAK_RE = re.compile(r"\b(?:QC_[A-Za-z0-9_]+|post_basic|preselector|in-sample|debug_)\b|#[0-9A-Fa-f]{6}\b", re.IGNORECASE)


def _safe_parse_svg(svg_path: Path) -> _StdET.Element:
    raw = svg_path.read_bytes()
    if _SafeET is not None:
        return _SafeET.fromstring(raw)
    if _FORBIDDEN_XML.search(raw[:32768].decode("utf-8", errors="ignore")):
        raise ValueError("Refused SVG with DOCTYPE/ENTITY declarations")
    return _StdET.fromstring(raw)


def lint_pathway_spec(
    spec: dict[str, Any],
    *,
    svg_path: Path | None = None,
    data_table_rows: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Audit biological entity nomenclature, compartment validity, polarity semantics, data encoding, conflicts, and provenance."""
    errors: list[str] = []
    warnings: list[str] = []
    gaps: list[dict[str, str]] = []

    compartments = spec.get("compartments") or []
    entities = spec.get("entities") or []
    edges = spec.get("edges") or []
    legend = spec.get("legend") or []
    data_bindings = spec.get("data_bindings") or []

    comp_ids = set()
    for c in compartments:
        cid = str(c.get("id", "")).strip()
        kind = str(c.get("kind", cid)).strip().lower()
        if not cid:
            errors.append("Compartment missing id")
            continue
        comp_ids.add(cid)
        if kind and kind not in VALID_COMPARTMENTS:
            warnings.append(f"Non-standard compartment kind {kind!r} on {cid!r}")

    ent_map: dict[str, dict[str, Any]] = {}
    name_types: dict[str, set[str]] = {}
    alias_owner: dict[str, str] = {}
    missing_prov = 0
    for e in entities:
        eid = str(e.get("id", "")).strip()
        name = str(e.get("name", "")).strip()
        etype = str(e.get("entity_type", "")).strip().lower()
        comp = str(e.get("compartment", "")).strip()
        role = str(e.get("role", "")).strip().lower()
        species = str(e.get("species", "")).strip().lower()
        src_ref = str(e.get("source_ref", "")).strip()

        if not eid or not name:
            errors.append(f"Entity missing id or name: {e!r}")
            continue
        if eid in ent_map:
            errors.append(f"Duplicate entity id: {eid!r}")
        ent_map[eid] = e

        if _BACKEND_LEAK_RE.search(name):
            errors.append(f"BACKEND_TERM_LEAK on entity {eid!r} name={name!r}")
        if etype not in VALID_ENTITY_TYPES:
            errors.append(f"Invalid entity_type {etype!r} on {eid!r}")
        if comp_ids and comp not in comp_ids:
            errors.append(f"Entity {eid!r} references undeclared compartment {comp!r} (declared={sorted(comp_ids)})")

        # Species gene/protein casing convention check (HGNC / MGI)
        if species in ("human", "homo_sapiens", "hsa") and etype == "gene":
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]+", name) and name != name.upper():
                warnings.append(f"Human gene {eid!r} ({name}) should follow HGNC uppercase italic convention ({name.upper()})")
        elif species in ("mouse", "mus_musculus", "mmu") and etype == "gene":
            if re.fullmatch(r"[A-Z]{2,}[A-Z0-9-]*", name):
                warnings.append(f"Mouse gene {eid!r} ({name}) should follow MGI title-case italic convention ({name[0].upper() + name[1:].lower()})")

        # Alias / synonym collision check
        for al in (e.get("aliases") or []):
            alk = str(al).strip().upper()
            if alk:
                if alk in alias_owner and alias_owner[alk] != eid:
                    warnings.append(f"Alias collision {alk!r} shared by {alias_owner[alk]!r} and {eid!r}; disambiguate explicitly")
                alias_owner[alk] = eid

        # Biological topology sanity
        if role == "transmembrane_receptor" and ("nucleus" in comp.lower() or "extra" in comp.lower()):
            errors.append(f"Topological violation: transmembrane receptor {eid!r} placed in {comp!r} instead of plasma_membrane")
        if etype == "gene" and ("extra" in comp.lower() or "membrane" in comp.lower()):
            errors.append(f"Topological violation: target gene {eid!r} placed in {comp!r} instead of nucleus")

        norm_name = name.upper()
        name_types.setdefault(norm_name, set()).add(etype)
        if not src_ref and not e.get("inferred"):
            missing_prov += 1
            gaps.append({
                "code": "ENTITY_MISSING_SOURCE_REF",
                "severity": "warning",
                "message": f"Entity {eid} ({name}) lacks source_ref provenance",
            })

    for nm, types in name_types.items():
        if "gene" in types and "protein" in types:
            warnings.append(
                f"Both gene and protein named {nm!r}; enforce italic gene vs upright protein typography"
            )

    edge_ids: set[str] = set()
    for idx, edge in enumerate(edges):
        ed_id = str(edge.get("id", f"edge-{idx+1}")).strip()
        src = str(edge.get("source", "")).strip()
        tgt = str(edge.get("target", "")).strip()
        pol = str(edge.get("polarity", "")).strip().lower()
        src_ref = str(edge.get("source_ref", "")).strip()

        if ed_id in edge_ids:
            errors.append(f"Duplicate edge id: {ed_id!r}")
        edge_ids.add(ed_id)

        if src not in ent_map:
            errors.append(f"Edge {ed_id!r} source {src!r} not in entities")
        if tgt not in ent_map:
            errors.append(f"Edge {ed_id!r} target {tgt!r} not in entities")
        if pol not in VALID_POLARITIES:
            errors.append(f"Edge {ed_id!r} has invalid polarity {pol!r}; expected {sorted(VALID_POLARITIES)}")
        if edge.get("label") and _BACKEND_LEAK_RE.search(str(edge.get("label"))):
            errors.append(f"BACKEND_TERM_LEAK on edge {ed_id!r} label={edge.get('label')!r}")

        if src in ent_map and tgt in ent_map:
            s_comp = str(ent_map[src].get("compartment", "")).lower()
            t_comp = str(ent_map[tgt].get("compartment", "")).lower()
            s_role = str(ent_map[src].get("role", "")).lower()
            if "extracellular" in s_comp and "nucleus" in t_comp and pol != "transport":
                errors.append(
                    f"Compartment teleportation on {ed_id!r}: extracellular {src!r} directly targets nuclear {tgt!r} without membrane receptor/transport"
                )
            if s_role == "transcription_factor" and pol == "transcribe":
                if "nucleus" not in t_comp and str(ent_map[tgt].get("entity_type", "")).lower() == "gene":
                    warnings.append(f"Transcription target gene {tgt!r} is in {t_comp!r} rather than nucleus")

        if not src_ref:
            missing_prov += 1
            uncertain = bool(edge.get("uncertain", False))
            if not uncertain:
                errors.append(
                    f"Edge {ed_id!r} lacks source_ref and is not marked uncertain; refusing to present an unsupported relation as definite"
                )
            else:
                warnings.append(f"Unreferenced edge {ed_id!r} is explicitly uncertain and must render dashed")
            gaps.append({
                "code": "EDGE_MISSING_SOURCE_REF",
                "severity": "warning" if uncertain else "error",
                "message": f"Edge {ed_id} ({src}->{tgt}) lacks source_ref provenance; uncertain={uncertain}",
            })

    legend_keys = {str(item.get("key", item.get("label", ""))).strip().lower() for item in legend}
    for db in data_bindings:
        enc = str(db.get("channel", "")).strip().lower()
        val = db.get("value")
        target_id = str(db.get("target_id", "")).strip()
        if target_id and target_id not in ent_map and target_id not in edge_ids:
            errors.append(f"Data binding references unknown target_id {target_id!r}")
        if enc and legend and not any(enc in lk or str(db.get("legend_key", "")).lower() in lk for lk in legend_keys):
            warnings.append(f"Data binding channel {enc!r} on {target_id!r} has no matching legend entry")
        if val is None and not db.get("missing_disclosed"):
            errors.append(f"Data binding on {target_id!r} has null value without missing_disclosed=true")

    # Cross-check optional source expression table against pathway diagram (DATA_DIAGRAM_CONFLICT)
    data_conflicts: list[dict[str, str]] = []
    if data_table_rows:
        expr_col = str(spec.get("expression_field") or (spec.get("expression_scale") or {}).get("column") or "log2fc").strip().lower()
        for tid in sorted(data_table_rows):
            if tid not in ent_map:
                msg = f"DATA_DIAGRAM_CONFLICT: data table entity_id={tid!r} is not present in pathway spec entities"
                warnings.append(msg)
                conflict = {"code": "DATA_DIAGRAM_CONFLICT", "severity": "warning", "entity_id": tid, "message": msg}
                data_conflicts.append(conflict)
                gaps.append({"code": "DATA_DIAGRAM_CONFLICT", "severity": "warning", "message": msg})
        for idx, edge in enumerate(edges):
            ed_id = str(edge.get("id", f"edge-{idx+1}")).strip()
            tgt = str(edge.get("target", "")).strip()
            exp_dir = str(edge.get("expected_direction", "")).strip().lower()
            if not exp_dir or tgt not in data_table_rows:
                continue
            row_lc = {k.strip().lower(): v for k, v in data_table_rows[tgt].items()}
            raw_v = str(row_lc.get(expr_col, "")).strip()
            try:
                num_v = float(raw_v)
            except (TypeError, ValueError):
                continue
            if (exp_dir == "up" and num_v < -0.25) or (exp_dir == "down" and num_v > 0.25):
                msg = (
                    f"DATA_DIAGRAM_CONFLICT: edge {ed_id!r} expects target {tgt!r} direction={exp_dir!r}, "
                    f"but source data reports {expr_col}={num_v:+.2f}"
                )
                warnings.append(msg)
                conflict = {"code": "DATA_DIAGRAM_CONFLICT", "severity": "warning", "edge_id": ed_id, "entity_id": tgt, "message": msg}
                data_conflicts.append(conflict)
                gaps.append({"code": "DATA_DIAGRAM_CONFLICT", "severity": "warning", "message": msg})

    svg_checks: dict[str, Any] = {}
    if svg_path is not None:
        root = _safe_parse_svg(svg_path)
        svg_edges: dict[str, dict[str, str]] = {}
        svg_nodes: set[str] = set()
        svg_labels: dict[str, _StdET.Element] = {}
        for el in root.iter():
            eid = el.get("id") or ""
            if eid.startswith("node-"):
                svg_nodes.add(eid[len("node-"):])
            elif eid.startswith("nodelabel-"):
                svg_labels[eid[len("nodelabel-"):]] = el
            elif eid.startswith("edge-") or el.get("data-polarity"):
                pol_attr = (el.get("data-polarity") or "").lower()
                mend = (el.get("marker-end") or "").lower()
                svg_edges[eid] = {"polarity": pol_attr, "marker_end": mend, "dasharray": el.get("stroke-dasharray") or ""}
                if pol_attr == "inhibit" and "inhibit" not in mend and "tbar" not in mend:
                    errors.append(f"SVG edge {eid!r} has data-polarity='inhibit' but marker-end={mend!r} (must use T-bar)")
                if pol_attr == "activate" and "inhibit" in mend:
                    errors.append(f"SVG edge {eid!r} polarity mismatch: activate using inhibition marker {mend!r}")

        # Verify gene typography is italic in rendered SVG
        italic_genes_ok = 0
        gene_total = 0
        for ent_id, ent in ent_map.items():
            if str(ent.get("entity_type", "")).lower() == "gene":
                gene_total += 1
                lbl_el = svg_labels.get(ent_id)
                if lbl_el is None or (lbl_el.get("font-style") or "").lower() != "italic":
                    errors.append(f"Gene entity {ent_id!r} ({ent.get('name')}) is missing font-style='italic' in SVG")
                else:
                    italic_genes_ok += 1

        for idx, edge in enumerate(edges):
            ed_id = str(edge.get("id", f"edge-{idx+1}")).strip()
            svg_id = ed_id if ed_id.startswith("edge-") else f"edge-{ed_id}"
            if edge.get("uncertain") or not str(edge.get("source_ref", "")).strip():
                svg_edge = svg_edges.get(svg_id)
                if svg_edge is None:
                    errors.append(f"Uncertain or unreferenced edge {ed_id!r} is missing from rendered SVG")
                elif not svg_edge["dasharray"] or svg_edge["dasharray"].lower() == "none":
                    errors.append(f"Uncertain or unreferenced edge {ed_id!r} must be rendered dashed")
                elif "arrow-transport" not in svg_edge["marker_end"]:
                    errors.append(f"Uncertain or unreferenced edge {ed_id!r} must use open uncertainty marker")

        missing_in_svg = sorted(set(ent_map) - svg_nodes)
        if missing_in_svg:
            errors.append(f"Entities missing from SVG layer-nodes: {missing_in_svg}")
        svg_checks = {
            "svg_nodes_matched": len(svg_nodes),
            "svg_edges_checked": len(svg_edges),
            "italic_genes_verified": f"{italic_genes_ok}/{gene_total}",
        }

    return {
        "passed": len(errors) == 0,
        "entity_count": len(ent_map),
        "edge_count": len(edges),
        "compartment_count": len(compartments),
        "missing_provenance_count": missing_prov,
        "blocking_errors": errors,
        "warnings": warnings,
        "gaps": gaps,
        "data_conflicts": data_conflicts,
        "svg_checks": svg_checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint biological mechanism/pathway spec and optional SVG.")
    parser.add_argument("--spec", required=True, help="Path to pathway_spec.json")
    parser.add_argument("--svg", default="", help="Optional rendered SVG path to cross-verify")
    parser.add_argument("--out", default="", help="Optional JSON report path")
    args = parser.parse_args(argv)

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    svg_p = Path(args.svg) if args.svg else None
    report = lint_pathway_spec(spec, svg_path=svg_p)
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
