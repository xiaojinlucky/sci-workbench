# -*- coding: utf-8 -*-
"""Direction-aware local citation graph with edge provenance, multi-hop forward/backward tracing,
co-citation, bibliographic coupling, bidirectional shortest paths with per-hop direction,
retraction/zero-citation seed flags, and snapshot diff auditing (CitationChaser #37 lesson).
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import scholarmeta as sm
except ImportError:
    import scholarmeta as sm  # type: ignore


def _quick_sim(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    return len(ta & tb) / max(1, len(ta | tb))


def build_graph(clusters, ref_sets: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build directed citation graph over deduplicated clusters with full edge provenance."""
    nodes: Dict[str, Dict[str, Any]] = {}
    doi_to_node: Dict[str, str] = {}
    pmid_to_node: Dict[str, str] = {}
    arx_to_node: Dict[str, str] = {}
    oa_to_node: Dict[str, str] = {}
    title_to_node: Dict[str, str] = {}

    for c in clusters:
        k = c.canonical.rid
        is_ret = any(m.is_retracted for m in c.members)
        is_corr = any(getattr(m, "is_correction", False) for m in c.members)
        nodes[k] = {
            "rid": k,
            "title": c.canonical.title,
            "authors": c.canonical.authors[:6],
            "year": c.canonical.year,
            "venue": c.canonical.venue,
            "version": c.canonical.version,
            "source": c.canonical.source,
            "sources": sorted({m.source for m in c.members}),
            "doi": c.canonical.doi,
            "pmid": c.canonical.pmid,
            "arxiv": c.canonical.arxiv_id,
            "openalex": getattr(c.canonical, "openalex_id", ""),
            "is_retracted": is_ret,
            "is_correction": is_corr,
            "n_merged_versions": len(c.members),
            "in": [],
            "out": [],
        }
        for d in c.ids.get("doi", []) + c.ids.get("alt_doi", []):
            doi_to_node[d] = k
        for p in c.ids.get("pmid", []):
            pmid_to_node[p] = k
        for a in c.ids.get("arxiv", []):
            arx_to_node[a] = k
        for oa in c.ids.get("openalex", []):
            oa_to_node[oa] = k
        t = sm.norm_title(c.canonical.title)
        if t:
            title_to_node.setdefault(t, k)

    edges: List[Dict[str, Any]] = []
    unresolved_refs: List[Dict[str, Any]] = []

    def resolve(ref: Dict[str, Any], ref_title: str = "") -> Tuple[Optional[str], str]:
        d = sm.norm_doi(ref.get("doi", "") or ref.get("DOI", ""))
        if d and d in doi_to_node:
            return doi_to_node[d], "doi"
        p = sm.norm_pmid(ref.get("pmid", ""))
        if p and p in pmid_to_node:
            return pmid_to_node[p], "pmid"
        a = sm.arxiv_base(ref.get("arxiv", ref.get("arxiv_id", "")))
        if a and a in arx_to_node:
            return arx_to_node[a], "arxiv"
        oa = str(ref.get("openalex_id") or ref.get("id") or "").rsplit("/", 1)[-1]
        if oa and oa in oa_to_node:
            return oa_to_node[oa], "openalex"
        t = sm.norm_title(ref_title or ref.get("title", "") or ref.get("article-title", "") or ref.get("unstructured", ""))
        if t and t in title_to_node:
            return title_to_node[t], "title_exact"
        if t and len(t) > 18:
            best, bs = None, 0.0
            for nt, nk in title_to_node.items():
                s = _quick_sim(t, nt)
                if s > bs:
                    best, bs = nk, s
            if bs >= 0.90:
                return best, f"title_fuzzy({bs:.2f})"
        return None, "unresolved"

    for c in clusters:
        k = c.canonical.rid
        for m in c.members:
            for i, ref in enumerate(m.references):
                tgt, how = resolve(ref)
                prov = {
                    "file": m.path,
                    "pointer": f"{m.pointer}/reference/{i}",
                    "source": m.source,
                    "resolution": how,
                    "raw": (ref.get("doi") or ref.get("article-title") or ref.get("unstructured") or "")[:180],
                }
                if tgt and tgt != k:
                    edges.append({"src": k, "dst": tgt, "relation": "cites", "provenance": prov})
                elif not tgt:
                    unresolved_refs.append({"from": k, "ref": ref, "provenance": prov})
            for j, rid_raw in enumerate(m.referenced_ids):
                wid = str(rid_raw).rsplit("/", 1)[-1]
                tgt = oa_to_node.get(wid) or doi_to_node.get(sm.norm_doi(wid))
                prov = {
                    "file": m.path,
                    "pointer": f"{m.pointer}/referenced_works/{j}",
                    "source": m.source,
                    "resolution": "openalex_id" if tgt else "unresolved_external_id",
                    "raw": wid,
                }
                if tgt and tgt != k:
                    edges.append({"src": k, "dst": tgt, "relation": "cites", "provenance": prov})
                elif not tgt:
                    unresolved_refs.append({"from": k, "ref": {"id": wid}, "provenance": prov})

    # Explicit reference graph files
    for _, rs in (ref_sets or {}).items():
        data = rs.get("data")
        if not isinstance(data, dict):
            continue
        ed = data.get("edges") or data.get("links") or []
        nodes_in = data.get("nodes") or []
        idmap: Dict[str, str] = {}
        for n in nodes_in if isinstance(nodes_in, list) else []:
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id") or n.get("rid") or n.get("key") or "")
            if not nid:
                continue
            d = sm.norm_doi(n.get("doi", "") or n.get("DOI", ""))
            if d and d in doi_to_node:
                idmap[nid] = doi_to_node[d]
            else:
                t = sm.norm_title(n.get("title") or n.get("label") or "")
                if t and t in title_to_node:
                    idmap[nid] = title_to_node[t]
                else:
                    idmap[nid] = nid
                    nodes.setdefault(
                        nid,
                        {
                            "rid": nid,
                            "title": n.get("title") or n.get("label") or nid,
                            "authors": n.get("authors") or [],
                            "year": n.get("year") or 0,
                            "venue": n.get("venue") or "",
                            "version": n.get("version") or "unknown",
                            "source": "graph_file",
                            "sources": ["graph_file"],
                            "doi": d,
                            "pmid": "",
                            "arxiv": "",
                            "openalex": "",
                            "is_retracted": bool(n.get("is_retracted")),
                            "is_correction": False,
                            "n_merged_versions": 1,
                            "external": True,
                            "in": [],
                            "out": [],
                        },
                    )
        for e in ed if isinstance(ed, list) else []:
            if not isinstance(e, dict):
                continue
            s_raw = str(e.get("source") or e.get("src") or e.get("from") or "")
            t_raw = str(e.get("target") or e.get("dst") or e.get("to") or "")
            s = idmap.get(s_raw, s_raw)
            t = idmap.get(t_raw, t_raw)
            rel = str(e.get("relation") or e.get("type") or "cites").lower()
            if rel in ("cited_by", "is_cited_by"):
                s, t = t, s
                rel = "cites"
            if s and t and s != t:
                edges.append(
                    {
                        "src": s,
                        "dst": t,
                        "relation": rel,
                        "provenance": {
                            "file": rs.get("file"),
                            "pointer": rs.get("pointer"),
                            "source": "graph_file",
                            "resolution": "explicit_edge",
                            "raw": sm.json_dumps(e)[:180],
                        },
                    }
                )

    # Deduplicate edges while preserving all provenance records
    uniq: List[Dict[str, Any]] = []
    idx_map: Dict[Tuple[str, str, str], int] = {}
    for e in edges:
        key = (e["src"], e["dst"], e["relation"])
        if key in idx_map:
            u = uniq[idx_map[key]]
            u.setdefault("provenance_list", [u["provenance"]]).append(e["provenance"])
            continue
        idx_map[key] = len(uniq)
        e["provenance_list"] = [e["provenance"]]
        uniq.append(e)

    for e in uniq:
        if e["src"] in nodes and e["dst"] in nodes:
            nodes[e["src"]]["out"].append(e["dst"])
            nodes[e["dst"]]["in"].append(e["src"])

    return {
        "nodes": nodes,
        "edges": uniq,
        "unresolved_references": unresolved_refs[:500],
        "stats": {
            "n_nodes": len(nodes),
            "n_edges": len(uniq),
            "n_unresolved_refs": len(unresolved_refs),
            "n_retracted_nodes": sum(1 for n in nodes.values() if n.get("is_retracted")),
        },
    }


def trace(
    graph: Dict[str, Any],
    seed_rids: List[str],
    depth: int = 2,
    max_branch_per_hop: int = 60,
) -> Dict[str, Any]:
    """Multi-hop direction-aware backward/forward citation trace with pruning, co-citation, coupling, and paths."""
    nodes, edges = graph["nodes"], graph["edges"]
    out_edges = defaultdict(list)
    in_edges = defaultdict(list)
    edge_prov = defaultdict(list)
    for e in edges:
        out_edges[e["src"]].append(e["dst"])
        in_edges[e["dst"]].append(e["src"])
        edge_prov[(e["src"], e["dst"])].extend(e.get("provenance_list") or [e["provenance"]])

    backward: Dict[int, List[str]] = {}
    forward: Dict[int, List[str]] = {}
    pruned: List[Dict[str, Any]] = []
    seen_b = set(seed_rids)
    seen_f = set(seed_rids)
    frontier_b = list(seed_rids)
    frontier_f = list(seed_rids)

    for d in range(1, depth + 1):
        nb_all = sorted({x for n in frontier_b for x in out_edges[n] if x not in seen_b})
        nf_all = sorted({x for n in frontier_f for x in in_edges[n] if x not in seen_f})
        if len(nb_all) > max_branch_per_hop:
            nb_all.sort(key=lambda r: (-len(in_edges[r]), -(nodes.get(r, {}).get("year") or 0), r))
            pruned.append({"hop": d, "direction": "backward", "kept": max_branch_per_hop, "pruned_count": len(nb_all) - max_branch_per_hop})
            nb = nb_all[:max_branch_per_hop]
        else:
            nb = nb_all
        if len(nf_all) > max_branch_per_hop:
            nf_all.sort(key=lambda r: (-len(in_edges[r]), -(nodes.get(r, {}).get("year") or 0), r))
            pruned.append({"hop": d, "direction": "forward", "kept": max_branch_per_hop, "pruned_count": len(nf_all) - max_branch_per_hop})
            nf = nf_all[:max_branch_per_hop]
        else:
            nf = nf_all
        backward[d] = nb
        forward[d] = nf
        seen_b |= set(nb)
        seen_f |= set(nf)
        frontier_b, frontier_f = nb, nf

    related = sorted((seen_b | seen_f) - set(seed_rids))

    # Flag seeds with zero in-snapshot citation edges
    zero_citation_seeds = [
        {
            "seed_rid": s,
            "title": nodes.get(s, {}).get("title", ""),
            "in_degree": len(in_edges[s]),
            "out_degree": len(out_edges[s]),
            "note": "no_citation_edges_in_local_snapshot",
        }
        for s in seed_rids
        if len(in_edges[s]) == 0 and len(out_edges[s]) == 0
    ]

    # Shortest path over bidirectional citation graph, annotating exact direction per hop
    def bfs_directed_annotated(src: str, dst: str, max_hops: int = 4) -> Optional[List[Dict[str, Any]]]:
        if src == dst:
            return []
        prev: Dict[str, Optional[Tuple[str, str]]] = {src: None}
        dist = {src: 0}
        q = deque([src])
        while q:
            u = q.popleft()
            if u == dst:
                break
            if dist[u] >= max_hops:
                continue
            for v in out_edges[u]:
                if v not in prev:
                    prev[v] = (u, "cites")
                    dist[v] = dist[u] + 1
                    q.append(v)
            for v in in_edges[u]:
                if v not in prev:
                    prev[v] = (u, "cited_by")
                    dist[v] = dist[u] + 1
                    q.append(v)
        if dst not in prev:
            return None
        hops: List[Dict[str, Any]] = []
        cur = dst
        while prev[cur] is not None:
            u, direction = prev[cur]  # type: ignore
            prov = edge_prov[(u, cur)] if direction == "cites" else edge_prov[(cur, u)]
            hops.append({"from": u, "to": cur, "direction": direction, "provenance": prov[:3]})
            cur = u
        hops.reverse()
        return hops

    # Co-citation & bibliographic coupling across full graph (finds parallel works not directly citing seed!)
    all_node_ids = list(nodes.keys())
    cocite: Counter = Counter()
    coupling: Counter = Counter()
    refsets = {n: set(out_edges[n]) for n in all_node_ids}
    seed_set = set(seed_rids)

    for s in seed_rids:
        for other in all_node_ids:
            if other == s or other in seed_set:
                continue
            shared_refs = refsets[s] & refsets[other]
            if shared_refs:
                coupling[(s, other)] = len(shared_refs)
            shared_citers = set(in_edges[s]) & set(in_edges[other])
            if shared_citers:
                cocite[(s, other)] = len(shared_citers)

    parallel_works = sorted({b for (_, b) in set(coupling.keys()) | set(cocite.keys()) if b not in related and b not in seed_set})
    universe = sorted(set(seed_rids) | set(related) | set(parallel_works))

    roles: Dict[str, Dict[str, Any]] = {}
    for n in universe:
        indeg, outdeg = len(in_edges[n]), len(out_edges[n])
        info = nodes.get(n, {})
        year = info.get("year") or 0
        if info.get("is_retracted"):
            role = "retracted_caution"
        elif n in seed_set:
            role = "seed"
        elif indeg >= max(2, len(universe) // 3) and (not year or year <= 2018):
            role = "foundational"
        elif indeg > 0 and outdeg > 0:
            role = "bridge"
        elif n in parallel_works:
            role = "parallel_coupled"
        elif year >= 2023:
            role = "recent_followup"
        else:
            role = "supporting"
        roles[n] = {"in_degree": indeg, "out_degree": outdeg, "year": year, "is_retracted": bool(info.get("is_retracted")), "role": role}

    paths = []
    for s in seed_rids:
        for t in related[:50]:
            hops = bfs_directed_annotated(s, t, max_hops=depth + 1)
            if hops:
                paths.append(
                    {
                        "seed": s,
                        "target": t,
                        "hop_count": len(hops),
                        "reachability": "direct" if len(hops) == 1 else "indirect",
                        "directions": [h["direction"] for h in hops],
                        "hops": hops,
                    }
                )

    return {
        "seed_rids": seed_rids,
        "depth_requested": depth,
        "backward": backward,
        "forward": forward,
        "related": related,
        "parallel_works_by_coupling_or_cocitation": parallel_works,
        "zero_citation_seeds": zero_citation_seeds,
        "pruning_log": pruned,
        "cocitation": [
            {"seed": a, "peer": b, "shared_citers": n, "peer_title": nodes.get(b, {}).get("title", "")}
            for (a, b), n in cocite.most_common(25)
        ],
        "coupling": [
            {"seed": a, "peer": b, "shared_references": n, "peer_title": nodes.get(b, {}).get("title", "")}
            for (a, b), n in coupling.most_common(25)
        ],
        "roles": roles,
        "role_counts": dict(Counter(r["role"] for r in roles.values())),
        "paths": paths[:60],
    }


def diff_graph_snapshots(prev_graph: Dict[str, Any], curr_graph: Dict[str, Any]) -> Dict[str, Any]:
    """Audit differences between two citation-graph snapshots (CitationChaser #37 lesson).

    Classifies node and edge deltas into `added`, `vanished`, and `unresolved` rather than
    reporting raw hit counts as coverage.
    """
    prev_nodes = set(prev_graph.get("nodes", {}).keys())
    curr_nodes = set(curr_graph.get("nodes", {}).keys())
    prev_edges = {(e["src"], e["dst"], e["relation"]) for e in prev_graph.get("edges", [])}
    curr_edges = {(e["src"], e["dst"], e["relation"]) for e in curr_graph.get("edges", [])}
    return {
        "nodes_added": sorted(curr_nodes - prev_nodes),
        "nodes_vanished": sorted(prev_nodes - curr_nodes),
        "nodes_persisted": len(prev_nodes & curr_nodes),
        "edges_added": [{"src": s, "dst": d, "relation": r} for (s, d, r) in sorted(curr_edges - prev_edges)],
        "edges_vanished": [{"src": s, "dst": d, "relation": r} for (s, d, r) in sorted(prev_edges - curr_edges)],
        "unresolved_in_current": curr_graph.get("stats", {}).get("n_unresolved_refs", 0),
        "coverage_warning": "Snapshot node/edge counts reflect finite local input only, not global citation coverage.",
    }
