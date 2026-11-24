#!/usr/bin/env python3
"""Deterministic raw /input -> pathway_spec.json extractor with source_ref, block_id & coverage audit (stdlib-only)."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_REL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:inhibit\w*|repress\w*|suppress\w*|block\w*|antagoniz\w*|attenuat\w*|negatively\s+regulat\w*)\b", re.I), "inhibit"),
    (re.compile(r"\b(?:phosphorylat\w*|activat\w*|stimulat\w*|promot\w*|trigger\w*|catalyz\w*|enhanc\w*)\b", re.I), "activate"),
    (re.compile(r"\b(?:transcrib\w*|induc\w*\s+expression|upregulat\w*|transactivat\w*)\b", re.I), "transcribe"),
    (re.compile(r"\b(?:bind\w*|bound\s+to|complex\w*\s+with|recruit\w*|heterodimer\w*|dimeriz\w*|associat\w*\s+with)\b", re.I), "bind"),
    (re.compile(r"\b(?:translocat\w*|import\w*|export\w*|secret\w*|transport\w*|shuttl\w*)\b", re.I), "transport"),
]

_COMP_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("extracellular", ("extracellular", "secreted", "ligand", "cytokine", "serum", "plasma", "soluble")),
    ("plasma_membrane", ("receptor", "membrane", "transmembrane", "surface", "channel", "integrin", "tgfbr", "egfr", "igf1r", "tlr")),
    ("nucleus", ("nucleus", "nuclear", "gene", "promoter", "chromatin", "transcription", "mrna", "dna")),
    ("cytoplasm", ("cytoplasm", "cytosol", "kinase", "adaptor", "intracellular", "mitochondri", "endoplasmic")),
]

_ENTITY_TOKEN_RE = re.compile(r"\b(?:[A-Z][A-Z0-9]{1,7}(?:/[0-9A-Z]{1,4})?|[A-Z][a-z]{1,4}[0-9]{1,3}[A-Za-z]?)\b")
_STOP_TOKENS = {
    "THE", "AND", "FOR", "WITH", "FROM", "THAT", "THIS", "INTO", "OVER", "UNDER", "AFTER", "BEFORE",
    "FIG", "TABLE", "DNA", "RNA", "PCR", "ELISA", "ANOVA", "USA", "PDF", "CSV", "JSON", "SVG", "PNG",
    "ALL", "NOT", "BUT", "ARE", "WAS", "WERE", "HAS", "HAVE", "HAD", "CAN", "MAY", "VIA", "PER", "VS",
    "NA", "ND", "SD", "SEM", "CI", "HR", "OR", "RR", "FDR", "PADJ", "LOG2FC", "WT", "KO", "KD",
}


def _read_lines(path: Path) -> list[tuple[int, str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        # Tier 1: pdftotext (preserves page breaks via \f)
        exe = shutil.which("pdftotext")
        if exe:
            proc = subprocess.run([exe, str(path), "-"], capture_output=True, text=True, check=False, timeout=30)
            if proc.returncode == 0 and proc.stdout.strip():
                out: list[tuple[int, str]] = []
                lno = 0
                for p_idx, page_txt in enumerate(proc.stdout.split("\f"), 1):
                    for ln in page_txt.splitlines():
                        c = ln.strip()
                        if c:
                            lno += 1
                            out.append((lno, f"[p.{p_idx}] {c}"))
                return out
        # Tier 2: pypdf if installed
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(path))
            out = []
            lno = 0
            for p_idx, pg in enumerate(reader.pages, 1):
                for ln in (pg.extract_text() or "").splitlines():
                    c = ln.strip()
                    if c:
                        lno += 1
                        out.append((lno, f"[p.{p_idx}] {c}"))
            if out:
                return out
        except Exception:
            pass
        # Tier 3: pure-stdlib zlib FlateDecode stream text fallback
        try:
            import zlib
            raw_b = path.read_bytes()
            chunks: list[str] = []
            for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw_b, re.S):
                try:
                    dec = zlib.decompress(m.group(1)).decode("latin-1", errors="ignore")
                except Exception:
                    continue
                for tm in re.findall(r"\(([^()]{3,200})\)\s*Tj", dec):
                    chunks.append(tm.replace(r"\(", "(").replace(r"\)", ")"))
            return [(i, c.strip()) for i, c in enumerate(chunks, 1) if len(c.strip()) >= 12]
        except Exception:
            return []
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    if ext in (".html", ".htm", ".xml"):
        raw = re.sub(r"</(?:p|div|h[1-6]|li|tr|section|article)\s*>", "\n", raw, flags=re.I)
        return [(i, _WS_RE.sub(" ", html.unescape(_HTML_TAG_RE.sub(" ", ln))).strip()) for i, ln in enumerate(raw.splitlines(), 1) if len(ln.strip()) >= 10]
    return [(i, ln.strip()) for i, ln in enumerate(raw.splitlines(), 1) if ln.strip()]


def _infer_compartment_and_type(name: str, context: str) -> tuple[str, str, str]:
    # Use a tight local window around the entity mention to avoid inheriting another entity's compartment
    pos = context.lower().find(name.lower())
    local_ctx = context[max(0, pos - 38): pos + len(name) + 38].lower() if pos >= 0 else context.lower()
    low = local_ctx
    n_low = name.lower()
    # Role & entity type heuristics
    if any(k in low for k in (f"{n_low} gene", f"gene {n_low}", f"target gene {n_low}", f"nuclear gene {n_low}", f"*{n_low}*", f"{n_low} promoter")):
        return ("nucleus", "gene", "target_gene")
    if any(k in low for k in (f"receptor {n_low}", f"{n_low} receptor", "transmembrane receptor")) or any(k in n_low for k in ("tgfbr", "egfr", "vegfr", "pdgfr", "fgfr", "tlr")):
        return ("plasma_membrane", "receptor", "transmembrane_receptor")
    if any(k in low for k in ("ligand", "secreted", "extracellular", "cytokine")):
        return ("extracellular", "ligand", "ligand")
    if "transcription factor" in low or f"tf {n_low}" in low:
        return ("nucleus", "complex" if "/" in name else "protein", "transcription_factor")
    if any(k in low for k in ("cytosolic", "cytoplasm", "cytosol", "kinase", "adaptor", "intracellular")):
        return ("cytoplasm", "complex" if "/" in name else "protein", "protein")
    for comp, kws in _COMP_HINTS:
        if any(kw in low for kw in kws):
            etype = "complex" if "/" in name else "protein"
            return (comp, etype, etype)
    return ("cytoplasm", "complex" if "/" in name else "protein", "protein")


def extract_pathway_from_input(input_dir: Path) -> dict[str, Any]:
    """Deterministically derive pathway_spec.json + coverage audit from raw /input files."""
    sentences: list[dict[str, Any]] = []
    expr_ids: dict[str, dict[str, str]] = {}
    expr_file = ""
    case_obj: dict[str, Any] = {}
    requested_entities: set[str] = set()
    title = ""

    for p in sorted(input_dir.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(input_dir).as_posix()
        ext = p.suffix.lower()
        if p.name == "case.json":
            try:
                case_obj = json.loads(p.read_text(encoding="utf-8-sig"))
                title = str(case_obj.get("title") or "").strip()
                for e in case_obj.get("required_entities") or []:
                    requested_entities.add(str(e).strip())
            except Exception:
                pass
            continue
        if ext == ".csv":
            with p.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames and any(c.lower() in ("log2fc", "logfc", "fold_change", "expression") for c in reader.fieldnames):
                    expr_file = rel
                    for row in reader:
                        rid = str(row.get("id") or row.get("entity_id") or row.get("gene") or "").strip()
                        if rid:
                            expr_ids[rid] = {str(k): str(v or "") for k, v in row.items() if k}
            continue
        if ext in (".md", ".txt", ".html", ".htm", ".pdf", ".xml"):
            for lno, text in _read_lines(p):
                if not title and (text.startswith("# ") or (lno <= 10 and 15 <= len(text) <= 110)):
                    title = text.lstrip("# ").strip()
                for sent in re.split(r"(?<=[.!?。！？])\s+", text):
                    sc = sent.strip()
                    if 16 <= len(sc) <= 420:
                        sentences.append({
                            "block_id": f"BLK-{len(sentences)+1:04d}",
                            "source_ref": f"{rel}#L{lno}",
                            "text": sc,
                        })

    # Discover biological entity mentions and interaction sentences
    entity_mentions: dict[str, dict[str, Any]] = {}
    raw_edges: list[dict[str, Any]] = []

    def _norm_id(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

    for s in sentences:
        txt = s["text"]
        # Find candidate molecular tokens in order of appearance
        cands = [m.group(0) for m in _ENTITY_TOKEN_RE.finditer(txt) if m.group(0).upper() not in _STOP_TOKENS and len(m.group(0)) >= 3]
        uniq_cands = list(dict.fromkeys(cands))
        for tok in uniq_cands:
            nid = _norm_id(tok)
            if nid not in entity_mentions:
                comp, etype, role = _infer_compartment_and_type(tok, txt)
                species = "mouse" if (etype == "gene" and tok[0].isupper() and any(ch.islower() for ch in tok[1:])) else "human"
                entity_mentions[nid] = {
                    "id": nid,
                    "name": tok,
                    "entity_type": etype,
                    "role": role,
                    "species": species,
                    "compartment": comp,
                    "source_ref": s["source_ref"],
                    "source_block_id": s["block_id"],
                    "excerpt": txt[:140],
                    "count": 1,
                }
            else:
                entity_mentions[nid]["count"] += 1

        # Match directional interaction verbs and expand 1-to-1 fine-grained edges for multi-target clauses (e.g., transcribes A and B)
        if len(uniq_cands) >= 2:
            for pat, pol in _REL_PATTERNS:
                m_rel = pat.search(txt)
                if m_rel:
                    before = [c for c in uniq_cands if txt.find(c) < m_rel.start()]
                    after = [c for c in uniq_cands if txt.find(c, m_rel.end()) >= m_rel.end()]
                    if before and after:
                        src_tok = before[-1]
                        targets = after if pol in ("transcribe", "activate", "inhibit") else after[:1]
                        for tgt_tok in targets:
                            if src_tok == tgt_tok:
                                continue
                            exp_dir = "up" if pol in ("activate", "transcribe") else ("down" if pol == "inhibit" else "")
                            ed_obj: dict[str, Any] = {
                                "id": f"e{len(raw_edges)+1}",
                                "source": _norm_id(src_tok),
                                "target": _norm_id(tgt_tok),
                                "polarity": pol,
                                "label": m_rel.group(0).lower()[:16],
                                "source_ref": s["source_ref"],
                                "source_block_id": s["block_id"],
                                "excerpt": txt[:140],
                                "uncertain": bool(re.search(r"\b(?:may|might|putative|possibly|hypothesiz|suggest)\b", txt, re.I)),
                            }
                            if exp_dir:
                                ed_obj["expected_direction"] = exp_dir
                            raw_edges.append(ed_obj)
                    break

    # Deduplicate edges and cap to top connected biological entities (<=14) for clean publication layout
    seen_pairs: set[tuple[str, str, str]] = set()
    edges: list[dict[str, Any]] = []
    connected: set[str] = set()
    for e in raw_edges:
        key = (e["source"], e["target"], e["polarity"])
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        edges.append(e)
        connected.add(e["source"])
        connected.add(e["target"])
        if len(edges) >= 14:
            break

    # Prioritize connected entities, then highest-frequency mentions
    ranked = sorted(entity_mentions.values(), key=lambda x: (x["id"] not in connected, -x["count"], x["id"]))
    chosen = ranked[:12]
    chosen_ids = {e["id"] for e in chosen}
    edges = [e for e in edges if e["source"] in chosen_ids and e["target"] in chosen_ids]

    # Enforce compartment safety (avoid accidental extracellular->nucleus non-transport teleportation in auto-draft)
    ent_by_id = {e["id"]: e for e in chosen}
    for e in edges:
        sc = ent_by_id[e["source"]]["compartment"]
        tc = ent_by_id[e["target"]]["compartment"]
        if sc == "extracellular" and tc == "nucleus" and e["polarity"] != "transport":
            e["polarity"] = "transport"
            e["uncertain"] = True

    used_comps = {e["compartment"] for e in chosen}
    comp_defs = [
        {"id": cid, "kind": cid, "label": lbl}
        for cid, lbl in (
            ("extracellular", "Extracellular Space"),
            ("plasma_membrane", "Plasma Membrane"),
            ("cytoplasm", "Cytoplasm"),
            ("nucleus", "Nucleus"),
        )
        if cid in used_comps
    ] or [{"id": "cytoplasm", "kind": "cytoplasm", "label": "Cytoplasm"}]

    # Clean helper fields from entity dicts while keeping source_ref/source_block_id
    entities_out = [{k: v for k, v in e.items() if k != "count"} for e in chosen]
    not_found: list[str] = []
    if not entities_out:
        not_found.append("biological_entities")
    if not edges:
        not_found.append("regulatory_edges")
    missing_req = sorted(r for r in requested_entities if _norm_id(r) not in chosen_ids)
    if missing_req:
        not_found.extend(f"requested_entity:{r}" for r in missing_req)

    return {
        "title": title or "Biological Mechanism Pathway (Auto-Extracted Draft)",
        "subtitle": f"Derived deterministically from {input_dir.name} ({len(sentences)} indexed source sentences)",
        "canvas_width_px": 1060,
        "canvas_height_px": 740,
        "expression_clamp": 2.0,
        "expression_field": "log2fc",
        "expression_unit": "log2FC",
        "compartments": comp_defs,
        "entities": entities_out,
        "edges": edges,
        "extraction_provenance": {
            "extractor": "extract_pathway_spec.py",
            "input_dir": str(input_dir.name),
            "sentences_indexed": len(sentences),
            "entities_extracted": len(entities_out),
            "edges_extracted": len(edges),
            "expression_table_detected": expr_file or None,
            "expression_rows_matched": sum(1 for k in expr_ids if _norm_id(k) in chosen_ids),
            "missing_requested_entities": missing_req,
            "not_found_in_source": not_found,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministically extract pathway_spec.json from raw /input files.")
    parser.add_argument("--input-dir", required=True, help="Raw input directory (PDF/HTML/MD/TXT/CSV/case.json)")
    parser.add_argument("--out", required=True, help="Output pathway_spec.json path")
    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    if not in_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"Input directory not found: {in_dir}"}, indent=2))
        return 2
    spec = extract_pathway_from_input(in_dir)
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_p), "extraction_provenance": spec["extraction_provenance"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
