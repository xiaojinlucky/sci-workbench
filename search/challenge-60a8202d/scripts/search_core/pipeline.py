# -*- coding: utf-8 -*-
"""Shared offline retrieval pipeline stages (zero prohibited imports, pure stdlib)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

try:
    from . import caseio, contract as ct, dedup as dd, graph as gr, lang as lg, query as qm, scholarmeta as sm, screen as sc, textextract as te
except ImportError:
    import caseio  # type: ignore
    import contract as ct  # type: ignore
    import dedup as dd  # type: ignore
    import graph as gr  # type: ignore
    import lang as lg  # type: ignore
    import query as qm  # type: ignore
    import scholarmeta as sm  # type: ignore
    import screen as sc  # type: ignore
    import textextract as te  # type: ignore

DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"<>\]\)]+", re.I)


def _infer_reference_year(bundle) -> int:
    wm = getattr(bundle, "window_meta", {}) or {}
    for k in ("window_end", "as_of", "snapshot_date", "watermark"):
        v = str(wm.get(k) or "")
        m = re.search(r"(20\d{2})", v)
        if m:
            return int(m.group(1))
    years = [r.year for r in bundle.records if r.year and 1950 <= r.year <= 2035]
    return max(years) if years else 2026


def prepare(builder, question: str = "", extract_docs: bool = True) -> Dict[str, Any]:
    b = builder.b
    S: Dict[str, Any] = {}
    builder.log(
        "intake",
        "inventory",
        files=len(b.files),
        records=len(b.records),
        docs=len(b.docs),
        source_load_errors=len(getattr(b, "source_load_errors", [])),
    )
    if extract_docs and not b.docs:
        for f in b.files:
            if f.role in ("doc", "other") and f.ext in (".pdf", ".docx", ".html", ".htm", ".txt", ".md"):
                try:
                    d = te.extract_doc(f.path, f.rel)
                    if d.text.strip() or d.annots:
                        b.docs.append(d)
                        f.role = "doc"
                except Exception as e:
                    b.notes.append(f"doc extract failed {f.rel}: {e}")
    S["docs"] = b.docs
    S["records"] = b.records
    S["record_files"] = dict(b.record_files)
    S["source_load_errors"] = list(getattr(b, "source_load_errors", []))
    S["pagination_issues"] = list(getattr(b, "pagination_issues", []))
    S["opinions"] = list(getattr(b, "opinions", []))
    S["window_meta"] = dict(getattr(b, "window_meta", {}))

    texts = [d.text[:200000] for d in b.docs] + [r.text_for_search for r in b.records]
    S["texts"] = texts

    # Upgraded deduplication with ambiguous guard + rollback log
    if b.records:
        clusters, ambiguous, rollback_log = dd.cluster_records_detailed(b.records)
    else:
        clusters, ambiguous, rollback_log = [], [], []
    S["clusters"] = clusters
    S["ambiguous_candidates"] = ambiguous
    S["rollback_log"] = rollback_log
    S["dedup"] = dd.dedup_report(clusters, b.records, ambiguous=ambiguous, rollback_log=rollback_log)
    S["version_links"] = dd.link_versions(clusters)
    builder.log(
        "dedup",
        "identity_resolution",
        n_records=S["dedup"]["n_records"],
        n_unique=S["dedup"]["n_unique"],
        n_merged=S["dedup"]["n_merged_away"],
        n_ambiguous=len(ambiguous),
        conserved=S["dedup"]["source_count_conserved"],
    )

    S["items"] = [cluster_item(c) for c in clusters]

    q = question or b.question or ""
    S["question"] = q
    ref_year = _infer_reference_year(b)
    S["reference_year"] = ref_year
    S["plan"] = (
        qm.decompose(q, reference_year=ref_year)
        if q
        else {"sub_questions": [], "key_terms": [], "filters": {}, "slots": {}, "element_card": {}, "known_items": {}, "reference_year": ref_year}
    )

    S["abbrevs"], S["unexpanded"] = qm.mine_abbreviations(texts)
    S["co_terms"] = qm.cooccurrence_expansion(S["plan"].get("key_terms", []), [r.text_for_search for r in b.records][:400])
    sources = sorted({r.source for r in b.records} - {"ref_string"}) or ["crossref", "europepmc"]
    S["sources"] = sources
    q_sources = [s for s in ("crossref", "europepmc", "pubmed", "arxiv", "openalex", "publisher") if s in sources or s in ("crossref", "europepmc")]
    S["queries"] = qm.build_queries(S["plan"], S["abbrevs"], S["co_terms"], q_sources)
    builder.log(
        "plan",
        "question_decomposition",
        n_sub=len(S["plan"].get("sub_questions", [])),
        abbreviations=len(S["abbrevs"]),
        co_terms=len(S["co_terms"]),
    )

    S["searcher"] = sc.LocalSearcher(S["items"], S["abbrevs"]) if S["items"] else None

    crit_text: List[str] = []
    for _, lst in b.criteria.items():
        for it in lst:
            d = it.get("data")
            if isinstance(d, str):
                crit_text.append(d)
            elif isinstance(d, list):
                crit_text.extend(str(x) for x in d)
            elif isinstance(d, dict):
                for kk, vv in d.items():
                    if isinstance(vv, list):
                        crit_text.append(f"{kk}:\n" + "\n".join(f"- {str(x)}" for x in vv))
                    else:
                        crit_text.append(f"{kk}: {vv}")
    if not crit_text and b.task_md:
        for m in re.finditer(r"(?:纳入|排除|inclusion|exclusion|eligibility|criteria)[^\n]*\n(?:[-*\d\.].*\n?){1,12}", b.task_md, re.I):
            crit_text.append(m.group(0))
    S["criteria_text"] = "\n".join(crit_text)[:6000]
    S["rules"] = qm.parse_criteria(S["criteria_text"])
    builder.log("criteria", "rules_parsed", n_rules=len(S["rules"]))
    S["coverage"] = qm.score_query_coverage(S["plan"], clusters) if clusters else {"sub_questions": [], "covered": 0, "partial": 0, "uncovered": 0, "total": 0}
    return S


def cluster_item(c) -> Dict[str, Any]:
    k = c.canonical
    merged = c.merged()
    return {
        "rid": k.rid,
        "title": merged.get("title") or k.title,
        "abstract": merged.get("abstract") or k.abstract,
        "authors": merged.get("authors") or k.authors,
        "year": merged.get("year") or k.year,
        "date": k.date,
        "indexed_date": getattr(k, "indexed_date", ""),
        "updated_date": getattr(k, "updated_date", ""),
        "venue": merged.get("venue") or k.venue,
        "doi": merged.get("doi") or k.doi,
        "pmid": k.pmid,
        "arxiv": k.arxiv_id,
        "openalex_id": getattr(k, "openalex_id", ""),
        "source": k.source,
        "sources": sorted({m.source for m in c.members}),
        "version": k.version,
        "language": k.language,
        "keywords": merged.get("keywords") or k.keywords,
        "mesh": k.mesh,
        "pub_type": k.pub_type,
        "citations_count": merged.get("citations_count") if merged.get("citations_count") is not None else k.citations_count,
        "is_retracted": merged.get("is_retracted", False),
        "is_correction": merged.get("is_correction", False),
        "member_rids": [m.rid for m in c.members],
        "n_members": len(c.members),
        "all_ids": c.ids,
        "field_provenance": merged.get("field_provenance", {}),
        "conflicts": c.conflicts,
        "version_group": c.version_group(),
        "match_tiers": merged.get("match_tiers", []),
        "path": k.path,
        "pointer": k.pointer,
        "paths": sorted({f"{m.path}{'#' + m.pointer if m.pointer else ''}" for m in c.members}),
        "record_refs": [
            {
                "rid": m.rid,
                "role": "canonical" if m is k else "duplicate_ignored_with_trace",
                "file": m.path,
                "pointer": m.pointer,
                "source": m.source,
                "version": m.version,
                "doi": m.doi,
                "arxiv": m.arxiv_id,
            }
            for m in c.members
        ],
    }


def item_view(item: Dict[str, Any], rank: Optional[int] = None, label: str = "", reason_codes: Optional[List[str]] = None) -> Dict[str, Any]:
    v: Dict[str, Any] = {
        "rid": item["rid"],
        "title": item.get("title", ""),
        "authors": (item.get("authors") or [])[:12],
        "author_count": len(item.get("authors") or []),
        "year": item.get("year"),
        "venue": item.get("venue", ""),
        "doi": item.get("doi", ""),
        "pmid": item.get("pmid", ""),
        "arxiv": item.get("arxiv", ""),
        "source": item.get("source", ""),
        "sources": item.get("sources", []),
        "version": item.get("version", ""),
        "language": item.get("language", ""),
        "pub_type": item.get("pub_type", ""),
        "citation_count": item.get("citations_count"),
        "retracted": item.get("is_retracted", False),
        "correction": item.get("is_correction", False),
        "n_merged_records": item.get("n_members", 1),
        "paths": item.get("paths", []),
        "record_refs": item.get("record_refs", []),
        "label": label,
        "reason_codes": reason_codes or [],
        "score": item.get("score"),
    }
    if rank is not None:
        v["rank"] = rank
    return v


def status_block(builder, S: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    b = builder.b
    st: Dict[str, Any] = {
        "finite_snapshot_only": True,
        "live_remote_coverage_claimed": False,
        "completed": [],
        "not_completed": [],
        "errors": [],
        "source_load_errors": list(S.get("source_load_errors", [])),
        "pagination_issues": list(S.get("pagination_issues", [])),
        "warnings": list(b.notes),
        "conflicts": [],
        "ambiguous_identity_candidates": list(S.get("ambiguous_candidates", [])),
        "gaps": [],
        "uncertainty": [],
        "duplicates_merged": S.get("dedup", {}),
    }
    for f in b.files:
        if f.note.startswith("error"):
            st["errors"].append({"file": f.rel, "error": f.note[6:]})
    for err in S.get("source_load_errors", []):
        st["gaps"].append(f"SOURCE_SNAPSHOT_LOAD_FAILED: {err['file']} ({err['error_type']}: {err['detail'][:120]})")
    for p_err in S.get("pagination_issues", []):
        st["gaps"].append(f"SOURCE_PAGINATION_OR_ACCESS_ERROR: {p_err['file']} ({p_err['status']}: {p_err['error'][:120]})")
    for d in b.docs:
        for w in d.warnings:
            st["warnings"].append({"file": d.rel, "warning": w})
    for c in S.get("clusters", []):
        for cf in c.conflicts:
            st["conflicts"].append({"rid": c.canonical.rid, **cf})
    if not b.records and not b.docs:
        st["errors"].append({"file": None, "error": "no records or documents could be extracted from input directory"})
        st["not_completed"].append("retrieval/screening (no input material)")
    if extra:
        for k, v in extra.items():
            if isinstance(v, list):
                st.setdefault(k, []).extend(v)
            else:
                st[k] = v
    return st


def sources_used(S: Dict[str, Any]) -> Dict[str, Any]:
    src_files = Counter(r.source for r in S.get("records", []))
    return {
        "records_by_source": dict(src_files),
        "files_with_records": dict(S.get("record_files", {})),
        "source_load_error_count": len(S.get("source_load_errors", [])),
        "n_docs": len(S.get("docs", [])),
    }


def env_summary() -> Dict[str, Any]:
    mods = {}
    for m in ("pdfplumber", "pypdf", "docx", "openpyxl", "numpy", "yaml", "networkx"):
        try:
            __import__(m)
            mods[m] = True
        except Exception:
            mods[m] = False
    return {"modules": mods, "offline": True}


def attach_docs(S: Dict[str, Any], builder=None) -> Dict[str, Any]:
    """Link extracted documents to cluster items via DOI or title match."""
    items = S.get("items", [])
    docs = S.get("docs", [])
    by_doi = {}
    by_title = {}
    for it in items:
        for d in (it.get("all_ids", {}) or {}).get("doi", []) or ([it["doi"]] if it.get("doi") else []):
            by_doi.setdefault(d, it)
        t = sm.norm_title(it.get("title"))
        if t:
            by_title.setdefault(t, it)
    links = []
    for doc in docs:
        head = " ".join(p.get("text", "") for p in doc.pages[:2])
        dois = {sm.norm_doi(m.group(0)) for m in DOI_IN_TEXT.finditer(head)}
        target = next((by_doi[d] for d in dois if d in by_doi), None)
        how = "doi" if target else ""
        if not target:
            cand_titles = (
                [doc.meta.get("title", "")]
                + [b.text for b in doc.blocks if b.kind == "heading"][:3]
                + [doc.pages[0].get("text", "").split("\n")[0] if doc.pages else ""]
            )
            for ct_str in cand_titles:
                nt = sm.norm_title(ct_str)
                if len(nt) > 15:
                    target = by_title.get(nt)
                    if target:
                        how = "title"
                        break
                    best, bs = None, 0.0
                    for _, it in by_title.items():
                        s = dd.title_sim(ct_str, it.get("title"))
                        if s > bs:
                            best, bs = it, s
                    if bs >= 0.85:
                        target, how = best, f"title(fuzzy {bs:.2f})"
                        break
        entry = {
            "doc": doc.rel,
            "sha256": doc.sha256,
            "pages": len(doc.pages),
            "linked_rid": target["rid"] if target else None,
            "how": how,
            "dois_in_head": sorted(dois)[:5],
            "language": doc.language,
            "text_chars": len(doc.text),
            "warnings": doc.warnings,
        }
        if target is not None:
            target.setdefault("docs", []).append({"rel": doc.rel, "pages": len(doc.pages), "how": how, "language": doc.language})
        links.append(entry)
    S["doc_links"] = links
    if builder is not None:
        builder.log("docs", "link_documents", linked=sum(1 for l in links if l["linked_rid"]), total=len(links))
    return {"links": links, "linked": sum(1 for l in links if l["linked_rid"]), "unlinked": sum(1 for l in links if not l["linked_rid"])}


def fulltext_hits(S: Dict[str, Any], rid: str, terms: List[str], max_hits: int = 6) -> List[Dict[str, Any]]:
    """Page-anchored hits of query terms inside linked full texts of a record."""
    it = next((x for x in S.get("items", []) if x["rid"] == rid), None)
    if not it or not it.get("docs"):
        return []
    docs_by_rel = {d.rel: d for d in S.get("docs", [])}
    stems = set(qm.stem_tokens([t for t in terms if len(t) > 2]))
    hits = []
    for dl in it["docs"]:
        doc = docs_by_rel.get(dl["rel"])
        if not doc:
            continue
        for b in doc.blocks:
            if b.kind == "reference":
                continue
            toks = set(qm.stem_tokens(qm.tokenize(b.text)))
            m = toks & stems
            if m:
                hits.append(
                    {
                        "doc": doc.rel,
                        "page": b.page,
                        "section": b.section,
                        "matched": sorted(m)[:8],
                        "snippet": sc._snippet(b.text, list(m)),
                    }
                )
            if len(hits) >= max_hits:
                break
    return hits[:max_hits]
