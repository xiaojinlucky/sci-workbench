"""Citation Graph Builder, WIPO ST.16 Patent Number Normalizer & CPC/IPC Slash-Safe Query Helper.

Supports patent number parsing (A1/A2/A3/A4 published applications, B/B1/B2/C grants,
U/U1/Y utility models, E/P/S/H reissues/designs/defensive docs), CPC/IPC normalization and
slash-escaped query syntax (mitigating pyUSPTO #101 slash parser failures), and citation graphs.
Zero network calls, zero banned libraries.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any

from evidence_state_guard import normalize_doi

PATENT_NUM_RE = re.compile(
    r"^([A-Z]{2})[-/\s]?([A-Z]?\d{4,12})(?:[-/\s]?([A-Z]\d?))?$"
)
APP_PCT_RE = re.compile(r"^(PCT)/([A-Z]{2})(\d{4})/(\d{4,7})$", re.I)
CPC_IPC_RE = re.compile(r"\b([A-HY]\d{2}[A-Z])\s*(\d{1,4})/(\d{2,6})\b")

# WIPO ST.16 Kind Code classification map
WIPO_KIND_DESC: dict[str, tuple[str, str]] = {
    "A": ("published_application", "Patent application publication (first level)"),
    "A0": ("published_application", "Provisional / early application publication"),
    "A1": ("published_application", "Published patent application with search report"),
    "A2": ("published_application", "Published patent application without search report"),
    "A3": ("published_application", "Separate publication of search report"),
    "A4": ("amended_application_publication", "PCT Article 19 amended claims republication / supplementary application publication"),
    "A8": ("reissue_or_correction", "Corrected front page of published application"),
    "A9": ("reissue_or_correction", "Complete reprint / corrected published application"),
    "B": ("grant", "Granted patent specification"),
    "B1": ("grant", "Granted patent (first publication or without prior A1 in US)"),
    "B2": ("grant", "Granted patent (after prior A1 publication or amended grant)"),
    "B8": ("reissue_or_correction", "Corrected front page of granted patent"),
    "B9": ("reissue_or_correction", "Complete corrected granted patent"),
    "C": ("grant", "Granted patent / reexamined patent"),
    "C1": ("grant", "Granted patent (first level) / reexamination certificate"),
    "C2": ("grant", "Granted patent (second level) / amended grant"),
    "E": ("reissue_or_correction", "Reissue patent (e.g., US RE / E)"),
    "E1": ("reissue_or_correction", "Reissue patent (first publication)"),
    "U": ("utility_model", "Utility model publication/grant"),
    "U1": ("utility_model", "Utility model (first publication)"),
    "Y": ("utility_model", "Granted utility model (CN/JP/KR)"),
    "P": ("grant", "Plant patent"),
    "P2": ("grant", "Granted plant patent"),
    "S": ("grant", "Design patent"),
    "S1": ("grant", "Granted design patent"),
    "T3": ("translation_grant", "National translation of granted European patent"),
}

GRANT_KINDS = {"B", "B1", "B2", "B8", "B9", "C", "C1", "C2", "P", "P2", "S", "S1"}
APP_KINDS = {"A", "A0", "A1", "A2", "A3"}
UTILITY_KINDS = {"U", "U1", "Y"}
CORRECTION_REISSUE_KINDS = {"E", "E1", "A4", "A8", "A9", "B8", "B9"}


def normalize_patent_number(raw: str) -> dict[str, Any]:
    """Normalize patent identifier separating jurisdiction, core digits, WIPO ST.16 kind code, and stage."""
    clean = re.sub(r"[\s,:]+", "", (raw or "").strip().upper())
    if not clean:
        return {
            "raw": raw,
            "normalized": "",
            "base_number": "",
            "jurisdiction": None,
            "country": None,
            "number": None,
            "kind_code": None,
            "stage": "unknown",
            "wipo_st16_meaning": "Empty identifier",
            "is_grant": False,
            "is_published_application": False,
            "is_utility_model": False,
            "is_correction_or_reissue": False,
        }

    m_pct = APP_PCT_RE.match(clean)
    if m_pct:
        norm_pct = f"PCT/{m_pct.group(2)}{m_pct.group(3)}/{m_pct.group(4)}"
        return {
            "raw": raw,
            "normalized": norm_pct,
            "base_number": norm_pct,
            "jurisdiction": "WO",
            "country": "WO",
            "number": f"{m_pct.group(2)}{m_pct.group(3)}/{m_pct.group(4)}",
            "kind_code": None,
            "stage": "pct_application",
            "wipo_st16_meaning": "PCT international application number",
            "is_grant": False,
            "is_published_application": False,
            "is_utility_model": False,
            "is_correction_or_reissue": False,
        }

    # Handle US serial application numbers like US12/685,063 or CN202311666138.0A
    if "/" in clean or "." in clean:
        cc = clean[:2] if len(clean) >= 2 and clean[:2].isalpha() else None
        return {
            "raw": raw,
            "normalized": clean,
            "base_number": re.sub(r"[A-Z]\d?$", "", clean),
            "jurisdiction": cc,
            "country": cc,
            "number": clean[2:] if cc else clean,
            "kind_code": None,
            "stage": "application_number",
            "wipo_st16_meaning": "National application/priority serial number",
            "is_grant": False,
            "is_published_application": False,
            "is_utility_model": False,
            "is_correction_or_reissue": False,
        }

    m = PATENT_NUM_RE.match(clean)
    if not m:
        return {
            "raw": raw,
            "normalized": clean,
            "base_number": clean,
            "jurisdiction": None,
            "country": None,
            "number": None,
            "kind_code": None,
            "stage": "unknown",
            "wipo_st16_meaning": "Unrecognized patent identifier format",
            "is_grant": False,
            "is_published_application": False,
            "is_utility_model": False,
            "is_correction_or_reissue": False,
        }

    cc, num, kind = m.group(1), m.group(2), m.group(3)
    norm = f"{cc}{num}{kind or ''}"
    if kind and kind in WIPO_KIND_DESC:
        stage, meaning = WIPO_KIND_DESC[kind]
    elif kind:
        if kind[0] in ("B", "C", "P"):
            stage, meaning = "grant", f"Granted patent (kind {kind})"
        elif kind[0] == "A":
            stage, meaning = "published_application", f"Published application (kind {kind})"
        elif kind[0] in ("U", "Y"):
            stage, meaning = "utility_model", f"Utility model (kind {kind})"
        elif kind[0] == "E":
            stage, meaning = "reissue_or_correction", f"Reissue/correction (kind {kind})"
        else:
            stage, meaning = f"kind_{kind}", f"Specialized WIPO kind code {kind}"
    else:
        stage, meaning = "unclassified_no_kind", "Missing WIPO ST.16 kind code; stage cannot be assumed grant vs application"

    return {
        "raw": raw,
        "normalized": norm,
        "base_number": f"{cc}{num}",
        "jurisdiction": cc,
        "country": cc,
        "number": num,
        "kind_code": kind,
        "stage": stage,
        "wipo_st16_meaning": meaning,
        "is_grant": bool(kind and (kind in GRANT_KINDS or kind[0] in ("B", "C"))),
        "is_published_application": bool(kind and kind in APP_KINDS),
        "is_utility_model": bool(kind and (kind in UTILITY_KINDS or kind[0] in ("U", "Y"))),
        "is_correction_or_reissue": bool(kind and kind in CORRECTION_REISSUE_KINDS),
    }


def normalize_priority_key(raw: str) -> str:
    """Normalize a priority application number for deterministic DOCDB/INPADOC set comparisons."""
    s = re.sub(r"[\s,.:-]+", "", (raw or "").strip().upper())
    # Strip trailing filing-kind suffix (e.g. FR0201488A -> FR0201488, AU2008906458A -> AU2008906458)
    s = re.sub(r"([0-9])[A-Z]\d?$", r"\1", s)
    return s


def normalize_cpc_ipc_code(raw: str) -> dict[str, str] | None:
    """Normalize CPC/IPC code and generate slash-safe search syntax (pyUSPTO #101 compliant)."""
    m = CPC_IPC_RE.search((raw or "").upper())
    if not m:
        return None
    subclass, main_grp, sub_grp = m.group(1), m.group(2), m.group(3)
    canonical = f"{subclass} {main_grp}/{sub_grp}"
    compact = f"{subclass}{main_grp}/{sub_grp}"
    escaped_spaced = f'{subclass} {main_grp}\\/{sub_grp}'
    escaped_compact = f"{subclass}{main_grp}\\/{sub_grp}"
    return {
        "raw": raw.strip(),
        "canonical": canonical,
        "compact": compact,
        "quoted_query": f'"{canonical}"',
        "lucene_escaped_query": f'cpc:("{escaped_spaced}" OR {escaped_compact})',
        "espacenet_query": f'cpc="{canonical}"',
        "uspto_safe_query": f'cpc:"{canonical}"',
    }


def extract_and_normalize_cpc_ipc(codes_or_text: list[str] | str) -> list[dict[str, str]]:
    """Extract unique normalized CPC/IPC structures with slash-safe query variants."""
    raw_items: list[str] = []
    if isinstance(codes_or_text, str):
        raw_items = [f"{m.group(1)} {m.group(2)}/{m.group(3)}" for m in CPC_IPC_RE.finditer(codes_or_text.upper())]
    else:
        for item in codes_or_text:
            for m in CPC_IPC_RE.finditer(str(item).upper()):
                raw_items.append(f"{m.group(1)} {m.group(2)}/{m.group(3)}")
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in sorted(raw_items):
        parsed = normalize_cpc_ipc_code(item)
        if parsed and parsed["canonical"] not in seen:
            seen.add(parsed["canonical"])
            out.append(parsed)
    return out


def build_citation_graph(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build directed backward/forward citation graph and compute co-citation / coupling signals."""
    nodes: dict[str, dict[str, Any]] = {}
    forward_adj: dict[str, set[str]] = defaultdict(set)
    backward_adj: dict[str, set[str]] = defaultdict(set)
    edges: list[dict[str, Any]] = []

    def _id_of(r: dict[str, Any]) -> str:
        nd = normalize_doi(str(r.get("doi") or ""))
        return nd if nd else str(r.get("record_id") or r.get("publication_number") or r.get("paper_id") or r.get("id") or r.get("title") or "")[:64]

    for r in records:
        nid = _id_of(r)
        if not nid:
            continue
        nodes[nid] = {
            "node_id": nid,
            "title": r.get("title", ""),
            "year": r.get("year"),
            "source": r.get("source", "local_snapshot"),
        }
        for ref in (r.get("references") or r.get("backward_refs") or []):
            ref_id = normalize_doi(str(ref.get("doi") if isinstance(ref, dict) else ref)) or str(ref)
            if ref_id:
                backward_adj[nid].add(ref_id)
                forward_adj[ref_id].add(nid)
                edges.append({"from": nid, "to": ref_id, "relation": "cites", "source": r.get("source", "local_snapshot")})
        for cit in (r.get("citations") or r.get("forward_citations") or []):
            cit_id = normalize_doi(str(cit.get("doi") if isinstance(cit, dict) else cit)) or str(cit)
            if cit_id:
                backward_adj[cit_id].add(nid)
                forward_adj[nid].add(cit_id)
                edges.append({"from": cit_id, "to": nid, "relation": "cites", "source": r.get("source", "local_snapshot")})

    in_degrees = {nid: len(forward_adj[nid]) for nid in nodes}
    out_degrees = {nid: len(backward_adj[nid]) for nid in nodes}

    coupling_pairs: list[dict[str, Any]] = []
    node_ids = sorted(nodes)
    for i in range(len(node_ids)):
        for j in range(i + 1, len(node_ids)):
            a, b = node_ids[i], node_ids[j]
            shared_refs = sorted(backward_adj[a] & backward_adj[b])
            shared_citers = sorted(forward_adj[a] & forward_adj[b])
            if shared_refs or shared_citers:
                coupling_pairs.append({
                    "pair": [a, b],
                    "shared_references_count": len(shared_refs),
                    "co_cited_by_count": len(shared_citers),
                    "shared_references": shared_refs[:10],
                    "co_cited_by": shared_citers[:10],
                })

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": list(nodes.values()),
        "edges": edges,
        "in_degrees": in_degrees,
        "out_degrees": out_degrees,
        "coupling_and_cocitation": coupling_pairs,
        "forward_adj": {k: sorted(v) for k, v in forward_adj.items()},
        "backward_adj": {k: sorted(v) for k, v in backward_adj.items()},
    }


def shortest_citation_path(graph: dict[str, Any], seed_id: str, target_id: str) -> list[dict[str, str]] | None:
    if seed_id == target_id:
        return []
    fwd = graph.get("forward_adj", {})
    bwd = graph.get("backward_adj", {})
    visited = {seed_id}
    queue: deque[tuple[str, list[dict[str, str]]]] = deque([(seed_id, [])])
    while queue:
        cur, path = queue.popleft()
        neighbors = [(nxt, "cited_by_forward") for nxt in fwd.get(cur, [])] + [
            (nxt, "cites_backward") for nxt in bwd.get(cur, [])
        ]
        for nxt, rel in neighbors:
            if nxt == target_id:
                return path + [{"from": cur, "to": nxt, "direction": rel}]
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, path + [{"from": cur, "to": nxt, "direction": rel}]))
    return None
