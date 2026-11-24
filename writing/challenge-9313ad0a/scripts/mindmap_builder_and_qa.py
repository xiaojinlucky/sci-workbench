#!/usr/bin/env python3
"""Grounded scientific mind map builder & QA engine.

Validates exact-file source anchors & citation backlinks, guards UTF-8 CJK integrity
(0 U+FFFD replacement chars), audits math formulas and blocks external/UNC image refs,
discovers real installed CJK fonts across fc-list/matplotlib, computes CJK-width-aware
collision-free layouts, and exports synchronized .json, layered .svg, FreeMind .mm,
Mermaid .mmd, and rendered .png previews.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path
from typing import Any

NODE_COLORS = {
    "root": ("#1B365D", "#FFFFFF"),
    "concept": ("#EAF2F8", "#1B4F72"),
    "question": ("#FEF5E7", "#935116"),
    "method": ("#E8F8F5", "#0E6251"),
    "evidence": ("#EBF5FB", "#21618C"),
    "conclusion": ("#F4ECF7", "#5B2C6F"),
    "task": ("#EAFAF1", "#196F3D"),
    "risk": ("#FDEDEC", "#943126"),
}

EDGE_STYLES = {
    "hierarchy": ("#5D6D7E", ""),
    "supports": ("#1E8449", ""),
    "depends_on": ("#2874A6", "5,3"),
    "causes": ("#B03A2E", ""),
    "contrasts": ("#7D3C98", "3,3"),
    "contradicts": ("#C0392B", "4,2"),
    "cites": ("#1F618D", "2,2"),
    "mitigates": ("#117864", "6,2"),
}

VALID_EPISTEMIC = {"fact", "summary", "inference"}
TEXT_SOURCE_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".tex", ".bib", ".xml", ".yaml", ".yml"}

# Security & encoding patterns (markmap #371 / #315 / #374)
EXTERNAL_OR_UNC_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:https?:|//|\\\\)[^)]+\)|<img\b[^>]*\bsrc\s*=\s*['\"](?:https?:|//|\\\\)[^'\"]+['\"][^>]*>|(?<![A-Za-z0-9:])//[^\s\"')>]+|\\\\[A-Za-z0-9_.-]+\\[^\s\"')>]+",
    re.I,
)
INLINE_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$([^$\n]+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)

UNICODE_MATH_MAP = (
    (r"\\alpha\b", "α"), (r"\\beta\b", "β"), (r"\\gamma\b", "γ"), (r"\\delta\b", "δ"),
    (r"\\epsilon\b|\\varepsilon\b", "ε"), (r"\\eta\b", "η"), (r"\\theta\b", "θ"),
    (r"\\Delta\b", "Δ"), (r"\\lambda\b", "λ"), (r"\\mu\b", "μ"), (r"\\rho\b", "ρ"),
    (r"\\sigma\b", "σ"), (r"\\tau\b", "τ"), (r"\\phi\b|\\varphi\b", "φ"),
    (r"\\omega\b", "ω"), (r"\\pi\b", "π"), (r"\\pm\b", "±"), (r"\\times\b", "×"),
    (r"\\cdot\b", "·"), (r"\\leq\b|\\le\b", "≤"), (r"\\geq\b|\\ge\b", "≥"),
    (r"\\neq\b|\\ne\b", "≠"), (r"\\approx\b", "≈"), (r"\\sim\b", "~"),
    (r"\\rightarrow\b|\\to\b", "→"), (r"\\leftarrow\b", "←"), (r"\\infty\b", "∞"),
    (r"\^2\b", "²"), (r"\^3\b", "³"), (r"_0\b", "₀"), (r"_1\b", "₁"), (r"_2\b", "₂"),
)
PREFERRED_CJK_FAMILIES = (
    "Noto Sans CJK SC", "Noto Serif CJK SC", "Noto Sans SC", "Source Han Sans SC",
    "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "Droid Sans Fallback",
    "AR PL UMing CN", "Microsoft YaHei", "SimHei", "DengXian", "PingFang SC",
)


def _contains_cjk(value: str) -> bool:
    return any(
        0x3400 <= ord(ch) <= 0x9FFF
        or 0xF900 <= ord(ch) <= 0xFAFF
        or 0x3040 <= ord(ch) <= 0x30FF
        or 0xAC00 <= ord(ch) <= 0xD7AF
        for ch in value
    )


def _normalise_excerpt(value: str) -> str:
    return " ".join(value.casefold().split())


def _resolve_anchor_file(raw_file: Any, corpus_dir: Path) -> Path | None:
    """Resolve an anchor strictly inside corpus_dir (no symlinks, no traversal)."""
    if not isinstance(raw_file, str) or not raw_file.strip():
        return None
    root = corpus_dir.resolve()
    raw = raw_file.strip().replace("\\", "/")
    if raw in {"/input", ".", ".."}:
        return None
    if raw.startswith("/input/"):
        raw = raw[len("/input/"):]
    p = Path(raw)
    if not p.is_absolute():
        p = root / p
    try:
        if p.is_symlink():
            return None
        resolved = p.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


_PDF_TEXT_CACHE: dict[Path, str | None] = {}


def _extract_pdf_text_offline(path: Path) -> str | None:
    """Use Poppler pdftotext when available to verify PDF anchors offline; return None if unavailable or scanned."""
    if path in _PDF_TEXT_CACHE:
        return _PDF_TEXT_CACHE[path]
    if not shutil.which("pdftotext"):
        _PDF_TEXT_CACHE[path] = None
        return None
    try:
        proc = subprocess.run(
            ["pdftotext", str(path), "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        extracted = proc.stdout if proc.returncode == 0 else ""
        # Image-only scanned PDFs yield near-empty text; treat as unverified instead of false negative
        result = extracted if len(extracted.strip()) >= 40 else None
    except OSError:
        result = None
    _PDF_TEXT_CACHE[path] = result
    return result


def _quote_match_in_file(path: Path, quote: str) -> bool | None:
    ext = path.suffix.lower()
    if ext in TEXT_SOURCE_EXTENSIONS:
        source_text = path.read_text(encoding="utf-8", errors="replace")
        return _normalise_excerpt(quote) in _normalise_excerpt(source_text)
    if ext == ".pdf":
        pdf_text = _extract_pdf_text_offline(path)
        if pdf_text is None:
            return None
        return _normalise_excerpt(quote) in _normalise_excerpt(pdf_text)
    return None


def _sanitize_text_and_audit_math(label: str) -> tuple[str, list[str], list[str], int]:
    """Sanitize remote/UNC image embeds, translate simple LaTeX math for SVG display, and count U+FFFD."""
    replacement_chars = label.count("\ufffd")
    blocked_refs = [m.group(0) for m in EXTERNAL_OR_UNC_RE.finditer(label)]
    cleaned = EXTERNAL_OR_UNC_RE.sub("[blocked-external-ref]", label)

    unrendered_math: list[str] = []

    def _replace_math(m: re.Match[str]) -> str:
        expr = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").strip()
        disp = expr
        for pat, uni in UNICODE_MATH_MAP:
            disp = re.sub(pat, uni, disp)
        # If complex TeX macros remain (e.g. \frac, \sum, \int, \begin{matrix}), flag explicitly
        if re.search(r"\\[a-zA-Z]{2,}", disp):
            unrendered_math.append(expr)
        return disp

    display_text = INLINE_MATH_RE.sub(_replace_math, cleaned)
    return display_text, blocked_refs, unrendered_math, replacement_chars


def _wrap_cjk_aware(text: str, max_units: float = 20.0) -> list[str]:
    """Wrap mixed CJK/ASCII text by visual character width without overflowing boxes."""
    lines: list[str] = []
    for raw_para in text.splitlines() or [text]:
        para = raw_para.strip()
        if not para:
            continue
        cur = ""
        cur_w = 0.0
        # Tokenize into CJK chars, ASCII words, or spaces
        tokens = re.findall(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]|[A-Za-z0-9_./:+%-]+|\s+|.", para)
        for tok in tokens:
            tw = sum(1.0 if ord(c) > 127 else 0.56 for c in tok)
            if cur and cur_w + tw > max_units:
                lines.append(cur.rstrip())
                cur = tok.lstrip()
                cur_w = sum(1.0 if ord(c) > 127 else 0.56 for c in cur)
            else:
                cur += tok
                cur_w += tw
        if cur.strip():
            lines.append(cur.rstrip())
    return lines or [text]


def _estimate_box(display_label: str, sub: str = "") -> tuple[int, int, list[str]]:
    wrapped = _wrap_cjk_aware(display_label, max_units=19.5)
    if sub:
        wrapped.append(sub)
    max_u = 0.0
    for ln in wrapped:
        u = sum(1.0 if ord(c) > 127 else 0.58 for c in ln)
        max_u = max(max_u, u)
    w = max(168, min(330, int(max_u * 13.2) + 30))
    h = max(48, len(wrapped) * 18 + 20)
    return w, h, wrapped


def discover_cjk_fonts() -> dict[str, Any]:
    """Discover real installed CJK fonts via fc-list and matplotlib.font_manager."""
    fc_families: list[str] = []
    if shutil.which("fc-list"):
        try:
            proc = subprocess.run(
                ["fc-list", ":lang=zh", "family"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if proc.returncode == 0:
                for ln in proc.stdout.splitlines():
                    for fam in ln.split(","):
                        f = fam.strip()
                        if f and f not in fc_families:
                            fc_families.append(f)
        except OSError:
            pass

    mpl_font_file: str | None = None
    mpl_font_family: str | None = None
    if importlib.util.find_spec("matplotlib") is not None:
        from matplotlib import font_manager  # type: ignore
        avail = {f.name.casefold(): (f.name, f.fname) for f in font_manager.fontManager.ttflist}
        for pref in (*PREFERRED_CJK_FAMILIES, *fc_families):
            hit = avail.get(pref.casefold())
            if hit:
                mpl_font_family, mpl_font_file = hit
                break

    chosen_svg_family: str | None = None
    for pref in PREFERRED_CJK_FAMILIES:
        if any(pref.casefold() == f.casefold() for f in fc_families):
            chosen_svg_family = pref
            break
    if not chosen_svg_family and fc_families:
        chosen_svg_family = fc_families[0]
    if not chosen_svg_family and mpl_font_family:
        chosen_svg_family = mpl_font_family

    return {
        "fc_list_available": bool(shutil.which("fc-list")),
        "fc_zh_families": fc_families[:12],
        "chosen_svg_cjk_family": chosen_svg_family,
        "matplotlib_cjk_family": mpl_font_family,
        "matplotlib_cjk_path": mpl_font_file,
        "any_cjk_font_installed": bool(chosen_svg_family or mpl_font_file),
    }


def parse_markdown_to_spec(md_path: Path, slug: str = "mindmap") -> dict[str, Any]:
    """Parse UTF-8 structured Markdown outline into canonical mindmap spec (zero stdin chunking)."""
    raw = md_path.read_text(encoding="utf-8", errors="replace")
    nodes: list[dict[str, Any]] = []
    cross_edges: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []  # (indent_level, node_id)
    counter = 0

    for line in raw.splitlines():
        if not line.strip():
            continue
        # Cross-edge line: -> N1 --supports--> N2 [@file: "quote"]
        em = re.match(r"^\s*->\s*(\S+)\s*--([a-z_]+)-->\s*(\S+)(.*)$", line)
        if em:
            src_id, rel, tgt_id, rest = em.group(1), em.group(2), em.group(3), em.group(4)
            anc = _parse_inline_anchor(rest)
            cross_edges.append({"source": src_id, "target": tgt_id, "relation": rel, "source_anchor": anc})
            continue

        hm = re.match(r"^(#{1,6})\s+(.+)$", line)
        bm = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
        if hm:
            level = len(hm.group(1))
            body = hm.group(2).strip()
        elif bm:
            level = 10 + len(bm.group(1)) // 2
            body = bm.group(2).strip()
        else:
            continue

        counter += 1
        nid = f"N{counter}"
        # Optional explicit ID tag like [id:N_custom]
        idm = re.search(r"\[id:([A-Za-z0-9_.-]+)\]", body)
        if idm:
            nid = idm.group(1)
            body = body.replace(idm.group(0), "").strip()

        epistemic = "fact"
        epm = re.search(r"\[(fact|summary|inference)\]", body, re.I)
        if epm:
            epistemic = epm.group(1).lower()
            body = body.replace(epm.group(0), "").strip()

        ntype = "root" if counter == 1 else "concept"
        ntm = re.search(r"\[type:(root|concept|question|method|evidence|conclusion|task|risk)\]", body, re.I)
        if ntm:
            ntype = ntm.group(1).lower()
            body = body.replace(ntm.group(0), "").strip()

        anc = _parse_inline_anchor(body)
        body = re.sub(r"\[@[^\]]+\]", "", body).strip()

        while stack and stack[-1][0] >= level:
            stack.pop()
        parent_id = stack[-1][1] if stack else None
        stack.append((level, nid))

        nodes.append({
            "id": nid,
            "label": body,
            "parent_id": parent_id,
            "node_type": ntype,
            "epistemic_status": epistemic,
            "source_anchor": anc,
        })

    return {"slug": slug, "nodes": nodes, "cross_edges": cross_edges}


def _parse_inline_anchor(text: str) -> dict[str, str]:
    m = re.search(r"\[@([^:\]]+):\s*\"([^\"]+)\"(?:\s*\|\s*([^\]]+))?\]", text)
    if not m:
        return {}
    out = {"file": m.group(1).strip(), "quote": m.group(2).strip()}
    if m.group(3):
        out["locator"] = m.group(3).strip()
    return out


def _validate_tree_structure(nodes: list[dict[str, Any]], cross_edges: list[dict[str, Any]]) -> tuple[list[str], list[str], dict[str, Any]]:
    """Check unique IDs, root presence, cycles, orphans, duplicate labels, and tree depth."""
    errors: list[str] = []
    warnings: list[str] = []
    ids = [n.get("id") for n in nodes]
    if any(not isinstance(i, str) or not i.strip() for i in ids):
        errors.append("Every node must have a non-empty string 'id'.")
    if len(set(ids)) != len(ids):
        errors.append("Duplicate node IDs detected in mind map spec.")

    by_id = {n["id"]: n for n in nodes if isinstance(n.get("id"), str)}
    roots = [n["id"] for n in nodes if n.get("parent_id") is None]
    if len(roots) == 0:
        errors.append("Mind map has no root node (parent_id: null).")
    elif len(roots) > 1:
        warnings.append(f"Multiple root nodes detected ({roots}); verify single unifying root.")

    # Check missing parent & cycles
    for n in nodes:
        pid = n.get("parent_id")
        if pid is not None and pid not in by_id:
            errors.append(f"Node {n['id']} references nonexistent parent_id {pid!r}.")
        ep = n.get("epistemic_status")
        if ep not in VALID_EPISTEMIC:
            errors.append(f"Node {n['id']} has invalid epistemic_status {ep!r}; expected one of {sorted(VALID_EPISTEMIC)}.")

    # Cycle detection via ancestor walk
    max_depth = 0
    for n in nodes:
        seen: set[str] = set()
        cur = n["id"]
        depth = 0
        while cur in by_id:
            if cur in seen:
                errors.append(f"Hierarchy cycle detected involving node {cur!r}.")
                break
            seen.add(cur)
            pid = by_id[cur].get("parent_id")
            if pid is None:
                break
            cur = pid
            depth += 1
        max_depth = max(max_depth, depth)

    # Duplicate labels check
    label_map: dict[str, list[str]] = {}
    for n in nodes:
        norm_l = _normalise_excerpt(str(n.get("label", "")))
        label_map.setdefault(norm_l, []).append(n["id"])
    dup_labels = {k: v for k, v in label_map.items() if k and len(v) > 1}
    if dup_labels:
        warnings.append(f"Duplicate node labels across IDs: {list(dup_labels.values())}")

    if max_depth > 5:
        warnings.append(f"Tree depth {max_depth} exceeds recommended readability bound (<=5).")
    if len(nodes) > 80:
        warnings.append(f"Node count {len(nodes)} exceeds recommended single-canvas readability bound (<=80).")

    # Cross-edge endpoint validation
    for idx, ed in enumerate(cross_edges, 1):
        s, t = ed.get("source"), ed.get("target")
        if s not in by_id or t not in by_id:
            errors.append(f"Cross-edge #{idx} references unknown node(s): {s!r} -> {t!r}.")
        if s == t:
            errors.append(f"Cross-edge #{idx} is a self-loop on node {s!r}.")

    return errors, warnings, {"root_ids": roots, "max_depth": max_depth, "duplicate_labels": dup_labels}


def _compute_dynamic_tree_layout(nodes: list[dict[str, Any]], display_labels: dict[str, str]) -> tuple[dict[str, dict[str, Any]], int, int]:
    """Dynamic per-depth column X layout avoiding collisions for CJK & long labels."""
    by_id = {n["id"]: dict(n) for n in nodes}
    children: dict[str | None, list[str]] = {}
    for n in nodes:
        children.setdefault(n.get("parent_id"), []).append(n["id"])

    roots = children.get(None, []) or [nodes[0]["id"]]
    for nid, nd in by_id.items():
        anc = nd.get("source_anchor") or {}
        src_tag = Path(str(anc.get("file", ""))).name if anc.get("file") else ""
        sub = f"[{nd.get('epistemic_status', 'fact')}]" + (f" @{src_tag}" if src_tag else "")
        w, h, lines = _estimate_box(display_labels.get(nid, nd["label"]), sub)
        nd["w"], nd["h"], nd["lines"] = w, h, lines

    # Compute depths safely (visited guard against malformed graphs)
    depth_max_w: dict[int, int] = {}
    visited_d: set[str] = set()

    def set_depth(nid: str, d: int) -> None:
        if nid in visited_d:
            return
        visited_d.add(nid)
        by_id[nid]["depth"] = d
        depth_max_w[d] = max(depth_max_w.get(d, 160), by_id[nid]["w"])
        for cid in children.get(nid, []):
            set_depth(cid, d + 1)

    for rid in roots:
        set_depth(rid, 0)
    for nid in by_id:
        if "depth" not in by_id[nid]:
            by_id[nid]["depth"] = 0
            depth_max_w[0] = max(depth_max_w.get(0, 160), by_id[nid]["w"])

    # Compute column X positions with generous 76px horizontal gutter
    col_x: dict[int, int] = {}
    cur_x = 40
    for d in sorted(depth_max_w.keys()):
        col_x[d] = cur_x
        cur_x += depth_max_w[d] + 76

    memo_h: dict[str, int] = {}

    def subtree_h(nid: str, seen: frozenset[str] = frozenset()) -> int:
        if nid in memo_h:
            return memo_h[nid]
        if nid in seen:
            return by_id[nid]["h"] + 26
        ch = children.get(nid, [])
        if not ch:
            val = by_id[nid]["h"] + 26
        else:
            val = max(by_id[nid]["h"] + 26, sum(subtree_h(c, seen | {nid}) for c in ch))
        memo_h[nid] = val
        return val

    placed: set[str] = set()

    def assign(nid: str, y_top: int) -> None:
        if nid in placed:
            return
        placed.add(nid)
        nd = by_id[nid]
        sh = subtree_h(nid)
        nd["x"] = col_x[nd["depth"]]
        nd["y"] = y_top + (sh - nd["h"]) // 2
        cy = y_top
        for cid in children.get(nid, []):
            cs = subtree_h(cid)
            assign(cid, cy)
            cy += cs

    cur_y = 40
    for rid in roots:
        rs = subtree_h(rid)
        assign(rid, cur_y)
        cur_y += rs

    max_x = max(nd["x"] + nd["w"] for nd in by_id.values()) + 70
    max_y = max(nd["y"] + nd["h"] for nd in by_id.values()) + 80
    return by_id, max_x, max_y


def _check_overlap_and_clipping(layout: dict[str, dict[str, Any]], width: int, height: int) -> tuple[list[str], list[str]]:
    items = list(layout.values())
    overlaps: list[str] = []
    clipped: list[str] = []
    for a in items:
        if a["x"] < 0 or a["y"] < 0 or a["x"] + a["w"] > width or a["y"] + a["h"] > height:
            clipped.append(a["id"])
    for i in range(len(items)):
        a = items[i]
        for j in range(i + 1, len(items)):
            b = items[j]
            if not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"]):
                overlaps.append(f"{a['id']}<->{b['id']}")
    return overlaps, clipped


def export_freemind_mm(nodes: list[dict[str, Any]], mm_path: Path) -> None:
    children: dict[str | None, list[dict[str, Any]]] = {}
    for n in nodes:
        children.setdefault(n.get("parent_id"), []).append(n)
    roots = children.get(None, []) or [nodes[0]]
    root_el = ET.Element("map", {"version": "1.0.1"})

    def add_branch(parent_xml: ET.Element, node: dict[str, Any], seen: set[str]) -> None:
        if node["id"] in seen:
            return
        seen.add(node["id"])
        el = ET.SubElement(parent_xml, "node", {"ID": str(node["id"]), "TEXT": str(node["label"])})
        anc = node.get("source_anchor") or {}
        if anc:
            rich = ET.SubElement(el, "richcontent", {"TYPE": "NOTE"})
            html = ET.SubElement(rich, "html")
            body = ET.SubElement(html, "body")
            p = ET.SubElement(body, "p")
            loc = f" ({anc.get('locator')})" if anc.get("locator") else ""
            p.text = f"[{node.get('epistemic_status', 'fact')}] {anc.get('file', '')}{loc}: {anc.get('quote', '')}"
        for ch in children.get(node["id"], []):
            add_branch(el, ch, seen)

    seen_ids: set[str] = set()
    for r in roots:
        add_branch(root_el, r, seen_ids)
    mm_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root_el).write(str(mm_path), encoding="utf-8", xml_declaration=True)


def export_mermaid_mmd(nodes: list[dict[str, Any]], cross_edges: list[dict[str, Any]], mmd_path: Path) -> None:
    lines = ["flowchart LR"]
    for n in nodes:
        nid = re.sub(r"[^A-Za-z0-9_]", "_", str(n["id"]))
        lbl = str(n["label"]).replace('"', "'").replace("\n", " ")
        ep = n.get("epistemic_status", "fact")
        lines.append(f'  {nid}["{lbl} [{ep}]"]')
    for n in nodes:
        pid = n.get("parent_id")
        if pid:
            p_clean = re.sub(r"[^A-Za-z0-9_]", "_", str(pid))
            c_clean = re.sub(r"[^A-Za-z0-9_]", "_", str(n["id"]))
            lines.append(f"  {p_clean} --> {c_clean}")
    for ed in cross_edges:
        s_clean = re.sub(r"[^A-Za-z0-9_]", "_", str(ed.get("source", "")))
        t_clean = re.sub(r"[^A-Za-z0-9_]", "_", str(ed.get("target", "")))
        rel = str(ed.get("relation", "supports")).replace('"', "'")
        lines.append(f'  {s_clean} -. "{rel}" .-> {t_clean}')
    mmd_path.parent.mkdir(parents=True, exist_ok=True)
    mmd_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_layered_svg(
    layout: dict[str, dict[str, Any]],
    cross_edges: list[dict[str, Any]],
    width: int,
    height: int,
    svg_path: Path,
    cjk_family: str | None = None,
) -> None:
    font_chain = (
        f"{cjk_family}, Noto Sans CJK SC, Noto Sans SC, WenQuanYi Zen Hei, DejaVu Sans, sans-serif"
        if cjk_family else
        "Noto Sans CJK SC, Noto Sans SC, WenQuanYi Zen Hei, DejaVu Sans, sans-serif"
    )
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '  <defs>',
        '    <marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">',
        '      <polygon points="0 0, 8 3, 0 6" fill="#34495E"/>',
        '    </marker>',
        '  </defs>',
        f'  <rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>',
        '  <g id="layer-edges">',
    ]
    for nid, nd in layout.items():
        pid = nd.get("parent_id")
        if pid and pid in layout:
            p = layout[pid]
            x1, y1 = p["x"] + p["w"], p["y"] + p["h"] // 2
            x2, y2 = nd["x"], nd["y"] + nd["h"] // 2
            cx = (x1 + x2) // 2
            lines.append(f'    <path id="tree-{escape(pid)}-{escape(nid)}" d="M {x1} {y1} C {cx} {y1}, {cx} {y2}, {x2} {y2}" fill="none" stroke="#7F8C8D" stroke-width="1.6"/>')
    lines.append('  </g>')

    lines.append('  <g id="layer-cross-edges">')
    for idx, ed in enumerate(cross_edges):
        s, t = layout.get(ed.get("source", "")), layout.get(ed.get("target", ""))
        if s and t:
            rel = str(ed.get("relation", "supports"))
            col, dash = EDGE_STYLES.get(rel, ("#2C3E50", ""))
            dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
            x1, y1 = s["x"] + s["w"] // 2, s["y"] + s["h"]
            x2, y2 = t["x"] + t["w"] // 2, t["y"]
            # Gentle quadratic curve offset so label sits cleanly off the straight chord
            mx, my = (x1 + x2) // 2 + 24, (y1 + y2) // 2
            lines.append(f'    <path id="cross-{idx}" d="M {x1} {y1} Q {mx} {my}, {x2} {y2}" fill="none" stroke="{col}" stroke-width="1.6"{dash_attr} marker-end="url(#arrow)"/>')
            lines.append(f'    <text x="{mx}" y="{my - 4}" font-family="{escape(font_chain)}" font-size="10" fill="{col}">{escape(rel)}</text>')
    lines.append('  </g>')

    lines.append('  <g id="layer-nodes">')
    for nid, nd in layout.items():
        ntype = "root" if nd.get("depth", 0) == 0 else str(nd.get("node_type", "concept"))
        fill, stroke = NODE_COLORS.get(ntype, ("#F8F9F9", "#34495E"))
        tcol = "#FFFFFF" if ntype == "root" else "#17202A"
        lines.append(f'    <g id="node-{escape(nid)}">')
        lines.append(f'      <rect x="{nd["x"]}" y="{nd["y"]}" width="{nd["w"]}" height="{nd["h"]}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>')
        ty = nd["y"] + 20
        for ln in nd["lines"]:
            lines.append(f'      <text x="{nd["x"] + 12}" y="{ty}" font-family="{escape(font_chain)}" font-size="12" fill="{tcol}">{escape(ln)}</text>')
            ty += 17
        lines.append('    </g>')
    lines.append('  </g>')

    lines.append('  <g id="layer-legend">')
    lines.append(f'    <text x="40" y="{height - 20}" font-family="{escape(font_chain)}" font-size="11" fill="#5D6D7E">Epistemic tags: [fact] verbatim grounded | [summary] synthesized | [inference] derived | @file citation backlink</text>')
    lines.append('  </g>')
    lines.append('</svg>')
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_png_preview(
    layout: dict[str, dict[str, Any]],
    cross_edges: list[dict[str, Any]],
    width: int,
    height: int,
    svg_path: Path,
    png_path: Path,
    font_info: dict[str, Any],
) -> dict[str, Any]:
    """Render PNG via rsvg-convert or matplotlib, strictly verifying CJK font availability before claiming readable CJK."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    contains_cjk = any(_contains_cjk(" ".join(nd["lines"])) for nd in layout.values())

    # If CJK is present and NO CJK font is installed anywhere in container, fail visibly rather than shipping tofu boxes!
    if contains_cjk and not font_info["any_cjk_font_installed"]:
        return {
            "rendered": False,
            "rendered_readable_cjk": False,
            "engine": "none",
            "png_path": None,
            "contains_cjk": True,
            "font_info": font_info,
            "error": "CJK content detected, but no CJK-capable font was found via fc-list or matplotlib; refusing to emit unreadable tofu PNG.",
        }

    rsvg = shutil.which("rsvg-convert")
    if rsvg and (not contains_cjk or font_info["chosen_svg_cjk_family"]):
        proc = subprocess.run([rsvg, "-o", str(png_path), str(svg_path)], capture_output=True, text=True, check=False)
        if proc.returncode == 0 and png_path.is_file() and png_path.stat().st_size > 0:
            return {
                "rendered": True,
                "rendered_readable_cjk": True if contains_cjk else None,
                "engine": "rsvg-convert",
                "png_path": png_path.name,
                "png_bytes": png_path.stat().st_size,
                "contains_cjk": contains_cjk,
                "cjk_font_used": font_info["chosen_svg_cjk_family"] if contains_cjk else None,
            }

    if importlib.util.find_spec("matplotlib") is not None:
        import matplotlib  # type: ignore
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
        import matplotlib.patches as patches  # type: ignore
        from matplotlib import font_manager  # type: ignore

        cjk_fp = font_manager.FontProperties(fname=font_info["matplotlib_cjk_path"]) if font_info["matplotlib_cjk_path"] else None
        if contains_cjk and cjk_fp is None:
            return {
                "rendered": False,
                "rendered_readable_cjk": False,
                "engine": "matplotlib_fallback",
                "png_path": None,
                "contains_cjk": True,
                "error": "Matplotlib fallback cannot locate a CJK font file.",
            }
        fig, ax = plt.subplots(figsize=(max(8, width / 100.0), max(5, height / 100.0)), dpi=140)
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.axis("off")
        for nd in layout.values():
            pid = nd.get("parent_id")
            if pid and pid in layout:
                p = layout[pid]
                ax.plot([p["x"] + p["w"], nd["x"]], [p["y"] + p["h"] / 2, nd["y"] + nd["h"] / 2], color="#7F8C8D", lw=1.4, zorder=1)
        for ed in cross_edges:
            s, t = layout.get(ed.get("source", "")), layout.get(ed.get("target", ""))
            if s and t:
                col, _ = EDGE_STYLES.get(ed.get("relation", "supports"), ("#2C3E50", ""))
                ax.annotate("", xy=(t["x"] + t["w"] / 2, t["y"]), xytext=(s["x"] + s["w"] / 2, s["y"] + s["h"]),
                            arrowprops=dict(arrowstyle="->", color=col, lw=1.4), zorder=2)
        for nd in layout.values():
            ntype = "root" if nd.get("depth", 0) == 0 else str(nd.get("node_type", "concept"))
            fill, stroke = NODE_COLORS.get(ntype, ("#F8F9F9", "#34495E"))
            tcol = "#FFFFFF" if ntype == "root" else "#17202A"
            box = patches.FancyBboxPatch((nd["x"], nd["y"]), nd["w"], nd["h"], boxstyle="round,pad=0.02,rounding_size=8", facecolor=fill, edgecolor=stroke, lw=1.5, zorder=3)
            ax.add_patch(box)
            ax.text(nd["x"] + 10, nd["y"] + nd["h"] / 2, "\n".join(nd["lines"]), va="center", ha="left", fontsize=8, color=tcol, zorder=4, fontproperties=cjk_fp)
        fig.savefig(str(png_path), bbox_inches="tight", pad_inches=0.2, dpi=140)
        plt.close(fig)
        return {
            "rendered": True,
            "rendered_readable_cjk": True if contains_cjk else None,
            "engine": "matplotlib_fallback",
            "png_path": png_path.name,
            "png_bytes": png_path.stat().st_size,
            "contains_cjk": contains_cjk,
            "cjk_font_used": font_info["matplotlib_cjk_family"] if contains_cjk else None,
        }

    return {
        "rendered": False,
        "rendered_readable_cjk": False if contains_cjk else None,
        "engine": "none",
        "png_path": None,
        "contains_cjk": contains_cjk,
        "error": "Neither rsvg-convert nor matplotlib is available in this runtime.",
    }


def build_and_validate_mindmap(spec: dict[str, Any], corpus_dir: Path, out_dir: Path) -> dict[str, Any]:
    nodes = spec.get("nodes", [])
    cross_edges = spec.get("cross_edges", [])
    if not nodes:
        return {"passed": False, "errors": ["Mind map spec has empty 'nodes' list."]}

    struct_errors, struct_warnings, struct_stats = _validate_tree_structure(nodes, cross_edges)

    ungrounded_nodes: list[str] = []
    ungrounded_edges: list[str] = []
    unverified_quotes: list[str] = []
    blocked_external_or_unc_refs: list[dict[str, Any]] = []
    unrendered_math_nodes: list[dict[str, Any]] = []
    replacement_char_count = 0
    display_labels: dict[str, str] = {}
    evidence_map: list[dict[str, Any]] = []

    for n in nodes:
        nid = str(n.get("id", ""))
        raw_label = str(n.get("label", ""))
        disp, blocked, math_left, rep_c = _sanitize_text_and_audit_math(raw_label)
        display_labels[nid] = disp
        replacement_char_count += rep_c
        if blocked:
            blocked_external_or_unc_refs.append({"node_id": nid, "refs": blocked})
        if math_left:
            unrendered_math_nodes.append({"node_id": nid, "expressions": math_left})

        anc = n.get("source_anchor") or {}
        path = _resolve_anchor_file(anc.get("file"), corpus_dir)
        if path is None:
            ungrounded_nodes.append(f"{nid}: invalid source_anchor.file (missing or outside corpus): {anc.get('file')}")
        elif not isinstance(anc.get("quote"), str) or not anc["quote"].strip():
            ungrounded_nodes.append(f"{nid}: missing source_anchor.quote")
        else:
            q_rep = anc["quote"].count("\ufffd")
            replacement_char_count += q_rep
            qm = _quote_match_in_file(path, anc["quote"])
            if qm is False:
                ungrounded_nodes.append(f"{nid}: source quote not found verbatim in anchored file {anc['file']}")
            elif qm is None:
                unverified_quotes.append(f"node {nid}: non-text source {anc['file']} requires page/paragraph locator check ({anc.get('locator', 'no locator')})")
                evidence_map.append({"node_id": nid, "file": anc["file"], "locator": anc.get("locator"), "verified_text": False})
            else:
                evidence_map.append({"node_id": nid, "file": anc["file"], "locator": anc.get("locator"), "verified_text": True})

    for idx, ed in enumerate(cross_edges, 1):
        anc = ed.get("source_anchor") or {}
        path = _resolve_anchor_file(anc.get("file"), corpus_dir)
        if path is None:
            ungrounded_edges.append(f"edge#{idx} ({ed.get('source')}->{ed.get('target')}): invalid source_anchor.file: {anc.get('file')}")
        elif not isinstance(anc.get("quote"), str) or not anc["quote"].strip():
            ungrounded_edges.append(f"edge#{idx} ({ed.get('source')}->{ed.get('target')}): missing source_anchor.quote")
        else:
            qm = _quote_match_in_file(path, anc["quote"])
            if qm is False:
                ungrounded_edges.append(f"edge#{idx} ({ed.get('source')}->{ed.get('target')}): source quote not found verbatim in {anc['file']}")
            elif qm is None:
                unverified_quotes.append(f"edge#{idx}: non-text source {anc['file']} requires human locator check")

    layout, w, h = _compute_dynamic_tree_layout(nodes, display_labels)
    overlaps, clipped = _check_overlap_and_clipping(layout, w, h)
    font_info = discover_cjk_fonts()

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", str(spec.get("slug", "mindmap"))) or "mindmap"
    json_out = out_dir / f"{stem}.json"
    svg_out = out_dir / f"{stem}.svg"
    mm_out = out_dir / f"{stem}.mm"
    mmd_out = out_dir / f"{stem}.mmd"
    png_out = out_dir / f"{stem}.png"

    json_out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    export_layered_svg(layout, cross_edges, w, h, svg_out, cjk_family=font_info["chosen_svg_cjk_family"])
    export_freemind_mm(nodes, mm_out)
    export_mermaid_mmd(nodes, cross_edges, mmd_out)
    png_res = render_png_preview(layout, cross_edges, w, h, svg_out, png_out, font_info)

    # Verify exported text artifacts have zero U+FFFD replacement chars
    for txt_art in (json_out, svg_out, mm_out, mmd_out):
        replacement_char_count += txt_art.read_text(encoding="utf-8", errors="replace").count("\ufffd")

    errors: list[str] = []
    errors.extend(struct_errors)
    errors.extend(ungrounded_nodes)
    errors.extend(ungrounded_edges)
    if replacement_char_count > 0:
        errors.append(f"Detected {replacement_char_count} U+FFFD replacement character(s) (broken UTF-8/CJK encoding).")
    if overlaps:
        errors.append(f"Node bbox overlap(s): {overlaps}")
    if clipped:
        errors.append(f"Clipped node(s): {clipped}")
    if not png_res.get("rendered"):
        errors.append(f"PNG preview unavailable: {png_res.get('error', 'renderer failed')}")

    warnings = list(struct_warnings)
    if blocked_external_or_unc_refs:
        warnings.append(f"Sanitized {len(blocked_external_or_unc_refs)} node(s) containing remote URLs or Windows UNC image paths.")
    if unrendered_math_nodes:
        warnings.append(f"{len(unrendered_math_nodes)} node(s) contain complex LaTeX math preserved raw in .json/.mm/.mmd (inspect SVG/PNG rendering).")

    artifacts = [json_out.name, svg_out.name, mm_out.name, mmd_out.name] + ([png_out.name] if png_res.get("rendered") else [])
    return {
        "passed": len(errors) == 0,
        "node_count": len(nodes),
        "cross_edge_count": len(cross_edges),
        "structure": struct_stats,
        "canvas": {"width": w, "height": h},
        "replacement_char_count": replacement_char_count,
        "blocked_external_or_unc_refs": blocked_external_or_unc_refs,
        "unrendered_math_nodes": unrendered_math_nodes,
        "overlaps": overlaps,
        "clipped": clipped,
        "artifacts": artifacts,
        "font_discovery": font_info,
        "png_renderer": png_res,
        "evidence_map": evidence_map,
        "unverified_quotes": unverified_quotes,
        "warnings": warnings,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src_group = parser.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--spec", help="Canonical mind map specification JSON.")
    src_group.add_argument("--from-markdown", help="UTF-8 structured Markdown outline file (avoids stdin CJK chunking).")
    parser.add_argument("--slug", default="mindmap", help="Output stem when using --from-markdown.")
    parser.add_argument("--corpus-dir", required=True, help="Input corpus directory for exact-file anchor grounding.")
    parser.add_argument("--out-dir", required=True, help="Output directory for .json/.svg/.mm/.mmd/.png.")
    parser.add_argument("--out-json", required=True, help="Audit summary JSON path.")
    args = parser.parse_args(argv)

    if args.spec:
        spec_obj = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    else:
        spec_obj = parse_markdown_to_spec(Path(args.from_markdown), slug=args.slug)

    rep = build_and_validate_mindmap(spec_obj, Path(args.corpus_dir), Path(args.out_dir))
    op = Path(args.out_json)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": rep["passed"],
        "node_count": rep.get("node_count", 0),
        "replacement_char_count": rep.get("replacement_char_count", 0),
        "artifacts": rep.get("artifacts", []),
        "warnings": rep.get("warnings", []),
        "errors": rep.get("errors", []),
    }, ensure_ascii=False, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
