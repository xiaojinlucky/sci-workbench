#!/usr/bin/env python3
"""Evidence-Matrix-Backed Literature Review Synthesis Engine (v4).

Key guarantees:
1. Pre-writing screening & multi-source deduplication (OpenAlex/Crossref/EuropePMC/arXiv/PDF/Notes)
   conserving PRISMA counts while excluding discovery-only/blocked records prior to writing.
2. Empirical data-driven TF-IDF theme clustering (honoring explicit note Theme tags when supplied).
3. Domain-general horizontal functional-operation comparison matrix (`extract_general_methods_and_objects`)
   with case/plural deduplication and honest access-ceiling tags.
4. Genuine Consensus vs. Contested Conflict vs. Prospective separation using strict empirical polarity
   and PDF header/affiliation filtering (no false REFUTED on neutral words or author blocks).
5. Strict read-only `--verify-draft --strict` auditing ALL bracketed citations (`[FAKE-999]`, `[W99999]`,
   DOIs, arXiv, PMC), hedging inflation, and global overclaims WITHOUT overwriting `result.json`.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log
from corpus_loader import load_input_bundle
from evidence_state_guard import (
    NEG_SIGNAL_RE,
    PROSPECTIVE_CUE_RE,
    RETRACTION_CUE_RE,
    build_tfidf_vectors,
    classify_five_state_support,
    cosine_similarity,
    detect_overclaims,
    extract_directional_passages,
    extract_general_methods_and_objects,
    is_fulltext_access,
    is_non_evidence_access,
    normalize_doi,
    tokenize_terms,
)
from prisma_flow_auditor import (
    deduplicate_bibliography,
    summarize_identification_by_source,
    verify_prisma_conservation,
)

STRONG_HEDGING_RE = re.compile(
    r"\b(?:conclusively\s+prov(?:ed|es|en)|definitively\s+(?:prov(?:ed|es|en)|establish(?:ed|es))|"
    r"universally\s+confirmed|universal\s+cure|proven\s+beyond\s+doubt|proves?\s+that|"
    r"confirms?\s+full\s+methods|in\s+all\s+patients)\b|确凿证明|普遍证实|完全确立|普遍治愈",
    re.I,
)
CITE_RE = re.compile(
    r"\[([A-Za-z0-9_.:/-]+)\]|\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+|PMC\d{5,9}|arXiv:\d{4}\.\d{4,5})\b",
    re.I,
)
IGNORE_BRACKET_TAGS = {
    "supported", "partial", "refuted", "outdated", "unknown", "prospective/protocol",
    "fact_observed", "inference_bounded", "unverified_or_gap",
}


def screen_before_writing(
    canonical: list[dict[str, Any]],
    case_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply transparent pre-writing screening rules with word-boundary matching before clustering."""
    proto = case_data.get("protocol") or case_data.get("screening_criteria") or {}
    req_terms = [str(t).strip().lower() for t in (proto.get("required_terms") or proto.get("include_keywords") or []) if str(t).strip()]
    excl_terms = [str(t).strip().lower() for t in (proto.get("exclude_terms") or proto.get("exclude_keywords") or []) if str(t).strip()]
    y_min = proto.get("year_min")
    y_max = proto.get("year_max")

    req_pats = [(t, re.compile(rf"(?i)\b{re.escape(t)}\b")) for t in req_terms]
    excl_pats = [(t, re.compile(rf"(?i)\b{re.escape(t)}\b")) for t in excl_terms]

    included: list[dict[str, Any]] = []
    screening_log: list[dict[str, Any]] = []

    for rec in canonical:
        access = str(rec.get("material_access") or "discovery_only")
        blob = f"{rec.get('title', '')} {rec.get('abstract', '')} {str(rec.get('text') or '')[:3000]}"
        yr = rec.get("year")
        decision = "include"
        reason = "INC_ACCESSIBLE_RELEVANT"

        if is_non_evidence_access(access):
            decision = "exclude_non_evidence"
            reason = f"EXC_{access.upper()}"
        elif y_min and yr and int(yr) < int(y_min):
            decision = "exclude"
            reason = "EXC_BEFORE_YEAR_MIN"
        elif y_max and yr and int(yr) > int(y_max):
            decision = "exclude"
            reason = "EXC_AFTER_YEAR_MAX"
        elif excl_pats and any(p.search(blob) for _, p in excl_pats):
            matched = next(t for t, p in excl_pats if p.search(blob))
            decision = "exclude"
            reason = f"EXC_CLAUSE:{matched}"
        elif req_pats and not any(p.search(blob) for _, p in req_pats):
            decision = "exclude"
            reason = "EXC_MISSING_REQUIRED_TERM"

        screening_log.append({
            "record_id": rec["record_id"],
            "title": rec.get("title") or rec["record_id"],
            "year": yr,
            "date_basis": rec.get("date_basis", "unknown"),
            "material_access": access,
            "decision": decision,
            "reason": reason,
        })
        if decision == "include":
            included.append(rec)

    return included, screening_log


def cluster_empirical_themes(
    included: list[dict[str, Any]],
    *,
    max_themes: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """Derive themes empirically from corpus TF-IDF similarity rather than generic headings."""
    if not included:
        return {}

    explicit_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unlabeled: list[dict[str, Any]] = []
    for rec in included:
        tag = str(rec.get("theme") or "").strip()
        if tag:
            explicit_buckets[tag].append(rec)
        else:
            unlabeled.append(rec)

    if not unlabeled:
        return dict(explicit_buckets)

    docs_tokens = [
        tokenize_terms(f"{r.get('title', '')} {r.get('title', '')} {r.get('abstract', '')}", include_bigrams=True)
        for r in unlabeled
    ]
    vecs = build_tfidf_vectors(docs_tokens)

    clusters: list[list[int]] = []
    centroids: list[dict[str, float]] = []
    sim_threshold = 0.14 if len(unlabeled) >= 6 else 0.09

    for i, vec in enumerate(vecs):
        best_ci = -1
        best_sim = -1.0
        for ci, cent in enumerate(centroids):
            sim = cosine_similarity(vec, cent)
            if sim > best_sim:
                best_sim = sim
                best_ci = ci
        if best_ci >= 0 and best_sim >= sim_threshold:
            clusters[best_ci].append(i)
            merged: dict[str, float] = defaultdict(float)
            for idx in clusters[best_ci]:
                for k, v in vecs[idx].items():
                    merged[k] += v / len(clusters[best_ci])
            centroids[best_ci] = dict(merged)
        elif len(clusters) < max_themes:
            clusters.append([i])
            centroids.append(dict(vec))
        else:
            target = best_ci if best_ci >= 0 else 0
            clusters[target].append(i)

    if len(clusters) > 3:
        large = [c for c in clusters if len(c) >= 2]
        singles = [c[0] for c in clusters if len(c) == 1]
        if large and singles:
            for si in singles:
                best_ci = max(range(len(large)), key=lambda ci: max(cosine_similarity(vecs[si], vecs[j]) for j in large[ci]))
                large[best_ci].append(si)
            clusters = large

    # Hierarchical sub-clustering: split any monolithic mega-cluster (>=30% of corpus and >=12 studies)
    # first by residual TF-IDF excluding >30% DF terms, then by 2D (functional_method x corpus_subtopic) if still >35%
    refined_clusters: list[list[int]] = []
    for idxs in clusters:
        if len(idxs) >= max(12, int(len(unlabeled) * 0.30)):
            df_in: Counter[str] = Counter()
            for idx in idxs:
                for t in set(vecs[idx].keys()):
                    df_in[t] += 1
            ubiq_in = {t for t, df in df_in.items() if df / len(idxs) > 0.30}
            sub_vecs = [{t: w for t, w in vecs[idx].items() if t not in ubiq_in} for idx in idxs]
            sub_clusters: list[list[int]] = []
            sub_cents: list[dict[str, float]] = []
            for local_i, svec in enumerate(sub_vecs):
                orig_idx = idxs[local_i]
                best_si, best_s = -1, -1.0
                for si, scent in enumerate(sub_cents):
                    sim = cosine_similarity(svec, scent)
                    if sim > best_s:
                        best_s, best_si = sim, si
                if best_si >= 0 and best_s >= 0.14:
                    sub_clusters[best_si].append(orig_idx)
                    m_cent: dict[str, float] = defaultdict(float)
                    for oi in sub_clusters[best_si]:
                        for k, v in vecs[oi].items():
                            if k not in ubiq_in:
                                m_cent[k] += v / len(sub_clusters[best_si])
                    sub_cents[best_si] = dict(m_cent)
                elif len(sub_clusters) < 5:
                    sub_clusters.append([orig_idx])
                    sub_cents.append(dict(svec))
                else:
                    sub_clusters[best_si if best_si >= 0 else 0].append(orig_idx)
            # Second-pass 2D problem x functional-method split if any single sub-cluster still exceeds 35% of corpus!
            final_subs: list[list[int]] = []
            for sc in sub_clusters:
                if len(sc) >= max(14, int(len(unlabeled) * 0.35)):
                    by_meth: dict[str, list[int]] = defaultdict(list)
                    for oi in sc:
                        r = unlabeled[oi]
                        m_info = extract_general_methods_and_objects(f"{r.get('title', '')} {r.get('abstract', '')}")
                        m_key = (m_info["methods"][0] if m_info["methods"] else (m_info["metrics"][0] if m_info["metrics"] else "mechanism_and_screening"))
                        by_meth[m_key].append(oi)
                    # Fold tiny method buckets into top 3 method strata
                    sorted_m = sorted(by_meth.items(), key=lambda kv: -len(kv[1]))
                    kept_m = [list(v) for _, v in sorted_m[:3]]
                    for _, extra_v in sorted_m[3:]:
                        kept_m[-1].extend(extra_v)
                    if len([km for km in kept_m if len(km) >= 3]) >= 2:
                        final_subs.extend(kept_m)
                    else:
                        final_subs.append(sc)
                else:
                    final_subs.append(sc)
            non_tiny = [sc for sc in final_subs if len(sc) >= 2]
            if len(non_tiny) >= 2:
                refined_clusters.extend(final_subs)
            else:
                refined_clusters.append(idxs)
        else:
            refined_clusters.append(idxs)
    clusters = refined_clusters

    # Corpus-wide ubiquitous terms (>35% DF) deprioritized when naming sub-themes
    corpus_df: Counter[str] = Counter()
    for v in vecs:
        for t in v:
            corpus_df[t] += 1
    corpus_ubiq = {t for t, df in corpus_df.items() if len(vecs) >= 10 and df / len(vecs) > 0.35}

    buckets: dict[str, list[dict[str, Any]]] = dict(explicit_buckets)
    used_names: set[str] = set(buckets.keys())
    noisy_label_words = {
        "processes", "process", "study", "studies", "method", "methods", "results",
        "system", "systems", "analysis", "approach", "based", "using", "high", "low",
        "lig", "precision", "correctness", "precision correctness", "evaluation",
        "performance", "strategy", "strategies", "design", "mechanism", "mechanisms",
        "application", "applications", "efficiency", "development", "advances", "progress",
        "review", "overview", "novel", "proposed", "framework", "structure", "structures",
        "property", "properties", "electrocatalytic", "catalytic", "material", "materials",
        "catalyst", "catalysts", "reaction", "activity", "stability", "feature", "features",
        "challenge", "challenges", "potential", "effect", "effects", "behavior", "surface",
    }
    for ci, idxs in enumerate(clusters, 1):
        term_scores: Counter[str] = Counter()
        for idx in idxs:
            for term, weight in vecs[idx].items():
                words = term.replace("_", " ").split()
                if len(term) <= 3 or term in noisy_label_words or all(w in noisy_label_words for w in words):
                    continue
                if any(w in {"correctness", "lig", "precision"} for w in words):
                    continue
                bonus = 1.65 if (" " in term or "_" in term) else 1.0
                if term in corpus_ubiq:
                    bonus *= 0.25
                term_scores[term] += weight * bonus
        ranked_terms = [t for t, _ in term_scores.most_common(16)]
        picked_terms: list[str] = []
        for t in ranked_terms:
            if any(t in prev or prev in t for prev in picked_terms):
                continue
            # Merge overlapping adjacent bigrams like "oxygen evolution" + "evolution reaction" -> "oxygen evolution reaction"
            merged_into_prev = False
            t_words = t.split()
            for pi, prev in enumerate(picked_terms):
                p_words = prev.split()
                if len(p_words) >= 2 and len(t_words) >= 2:
                    if p_words[-1] == t_words[0]:
                        picked_terms[pi] = " ".join(p_words + t_words[1:])
                        merged_into_prev = True
                        break
                    if t_words[-1] == p_words[0]:
                        picked_terms[pi] = " ".join(t_words + p_words[1:])
                        merged_into_prev = True
                        break
            if merged_into_prev:
                continue
            picked_terms.append(t)
            if len(picked_terms) >= 2:
                break
        label = " / ".join(picked_terms) if picked_terms else f"empirical_cluster_{ci}"
        if label in used_names:
            label = f"{label} ({ci})"
        used_names.add(label)
        buckets[label] = [unlabeled[idx] for idx in idxs]

    return buckets


def _hedging_register(ev_state: str, material_access: str, is_prosp: bool, is_conflict: bool) -> str:
    if is_prosp:
        return "prospective_protocol_only"
    if ev_state == "UNKNOWN":
        return "unverified_unknown"
    if ev_state == "OUTDATED":
        return "outdated_or_retracted"
    if is_conflict or ev_state == "REFUTED":
        return "contested_or_opposing"
    if not is_fulltext_access(material_access) or ev_state == "PARTIAL":
        return "suggestive_abstract_only"
    return "established_in_fulltext"


def build_horizontal_method_matrix(included: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build domain-general horizontal comparison of actual functional operations vs named methods."""
    rows: list[dict[str, Any]] = []
    for rec in included:
        access = str(rec.get("material_access") or "abstract_only")
        blob = f"{rec.get('title', '')} {rec.get('abstract', '')} {str(rec.get('text') or '')[:6000]}"
        extracted = extract_general_methods_and_objects(blob)
        methods = extracted["methods"]
        objects = extracted["objects"]
        metrics = extracted["metrics"]
        fact_tag = (
            "explicitly_stated_in_fulltext"
            if (methods and is_fulltext_access(access))
            else ("abstract_only_unverified_methods" if methods else "methods_not_reported_in_available_text")
        )
        passages_rec = extract_directional_passages(rec, max_per_record=6)
        primary_dir = passages_rec[0]["direction"] if passages_rec else "UNKNOWN"
        cond_m = re.search(r"\b(?:acidic|alkaline|neutral|ph\s*[=<>]?\s*\d+(?:\.\d+)?|\d+\s*m\s*(?:koh|h2so4|hclo4)|in\s+vivo|in\s+vitro|randomized|placebo[- ]controlled|double[- ]blind)\b", blob, re.I)
        comp_m = re.search(r"\b(?:versus|vs\.?|compared\s+(?:with|to)|benchmark(?:ed)?\s+against|relative\s+to)\s+([a-z0-9\-/\s]{3,40})", blob, re.I)
        rows.append({
            "record_id": rec["record_id"],
            "title": rec.get("title") or rec["record_id"],
            "year": rec.get("year"),
            "date_basis": rec.get("date_basis", "unknown"),
            "material_access": access,
            "entity": objects[0] if objects else "unspecified_entity",
            "intervention": methods[0] if methods else "not_explicitly_extracted",
            "comparator": comp_m.group(1).strip(" .,;:") if comp_m else "not_explicitly_reported",
            "endpoint": metrics[0] if metrics else "not_explicitly_extracted",
            "condition": cond_m.group(0).strip() if cond_m else "condition_not_specified",
            "direction": primary_dir if is_fulltext_access(access) else (f"{primary_dir}_abstract_only" if primary_dir not in {"UNKNOWN", "descriptive"} else "UNKNOWN"),
            "study_objects": objects or ["not_explicitly_extracted"],
            "functional_methods_detected": methods or ["not_explicitly_extracted"],
            "evaluation_metrics_detected": metrics or ["not_explicitly_extracted"],
            "method_fact_tag": fact_tag,
            "source_file": rec.get("source_file"),
        })
    return rows


def extract_themes_and_evidence(
    included: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build empirical theme syntheses, consensus vs conflict sets, prospective items, and typed relation edges."""
    buckets = cluster_empirical_themes(included)
    themes_out: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    prospective_items: list[dict[str, Any]] = []
    consensus_findings: list[dict[str, Any]] = []
    relation_edges: list[dict[str, Any]] = []

    for tname, recs in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        entries: list[dict[str, Any]] = []
        for rec in recs:
            access = str(rec.get("material_access") or "abstract_only")
            claim_spans = rec.get("claim_spans") or []

            if claim_spans:
                for span in claim_spans:
                    if not isinstance(span, dict):
                        continue
                    claim_key = str(span.get("claim_key") or "").strip()
                    claim_text = str(span.get("claim") or "").strip()
                    quote = str(span.get("quote") or "").strip()
                    pdf_locator = str(span.get("locator") or span.get("pdf_locator") or "").strip()
                    note_span = span.get("note_span") if isinstance(span.get("note_span"), dict) else {}
                    note_start = note_span.get("start_line")
                    note_end = note_span.get("end_line")
                    note_span_valid = isinstance(note_start, int) and isinstance(note_end, int) and 1 <= note_start <= note_end
                    locator_status = str(span.get("pdf_locator_status") or "page_unverified")
                    quote_grounded = span.get("quote_grounded_at_locator") is True
                    pdf_status = str(rec.get("pdf_link_status") or "not_linked")
                    source_available = pdf_status == "available" and quote_grounded
                    stance = str(span.get("stance") or "unknown").strip().lower()
                    is_prosp = bool(PROSPECTIVE_CUE_RE.search(claim_text))
                    is_retracted = bool(RETRACTION_CUE_RE.search(f"{rec.get('title', '')} {claim_text}"))
                    is_conflict = stance in {"opposes", "refutes", "mixed"} or bool(NEG_SIGNAL_RE.search(claim_text))

                    if not (source_available and note_span_valid and pdf_locator and claim_text and rec.get("note_file")):
                        ev_state = "UNKNOWN"
                    elif is_retracted:
                        ev_state = "OUTDATED"
                    elif stance in {"supports", "support"}:
                        ev_state = "SUPPORTED"
                    elif stance in {"opposes", "refutes", "opposed"}:
                        ev_state = "REFUTED"
                    elif stance in {"mixed", "partial"}:
                        ev_state = "PARTIAL"
                    else:
                        ev_state = "UNKNOWN"

                    entry = {
                        "record_id": rec["record_id"],
                        "title": rec.get("title"),
                        "doi": normalize_doi(str(rec.get("doi") or "")) or None,
                        "year": rec.get("year"),
                        "material_access": "fulltext_pdf" if source_available else access,
                        "claim_key": claim_key or None,
                        "claim": claim_text or None,
                        "stance": stance,
                        "evidence_state": ev_state,
                        "hedging_register": _hedging_register(ev_state, "fulltext_pdf" if source_available else access, is_prosp, is_conflict),
                        "is_prospective_or_protocol": is_prosp,
                        "quote": quote,
                        "locator": f"{rec.get('pdf_file') or rec.get('source_file')}#{pdf_locator}",
                        "quote_grounded_in_pdf": quote_grounded,
                        "pdf_locator_status": locator_status,
                        "pdf_page_number": span.get("pdf_page_number"),
                        "note_file": rec.get("note_file"),
                        "note_span": {"start_line": note_start, "end_line": note_end} if note_span_valid else None,
                        "pdf_file": rec.get("pdf_file"),
                        "pdf_link_status": pdf_status,
                        "pdf_locator": pdf_locator or None,
                    }
                    entries.append(entry)
                    if is_conflict:
                        conflicts.append(entry)
                    if is_prosp:
                        prospective_items.append(entry)
                continue

            blob = f"{rec.get('title', '')} {rec.get('abstract', '')} {str(rec.get('text') or '')[:4000]}".strip()
            is_retracted = bool(RETRACTION_CUE_RE.search(blob))
            passages = extract_directional_passages(rec, max_per_record=2)
            if not passages:
                passages = [{
                    "locator": f"{rec.get('source_file')}#abstract",
                    "direction": "descriptive",
                    "is_prospective": bool(PROSPECTIVE_CUE_RE.search(blob)),
                    "quote": " ".join(blob.split())[:220],
                }]

            for p in passages:
                q_txt = p["quote"]
                is_prosp = bool(p.get("is_prospective")) or bool(PROSPECTIVE_CUE_RE.search(q_txt))
                is_conflict = p["direction"] in {"opposing_or_null", "mixed"}
                has_support = p["direction"] in {"supportive", "mixed", "descriptive", "limitation"}
                ev_state = classify_five_state_support(
                    has_direct_support=has_support,
                    has_opposing_evidence=is_conflict,
                    missing_critical_context=is_prosp or not is_fulltext_access(access),
                    is_superseded_or_retracted=is_retracted,
                    source_unavailable=not bool(q_txt),
                    material_access=access,
                )
                stance = (
                    "mixed" if p["direction"] == "mixed" else (
                        "opposes" if p["direction"] == "opposing_or_null" else (
                            "supports" if p["direction"] == "supportive" else "descriptive"
                        )
                    )
                )
                entry = {
                    "record_id": rec["record_id"],
                    "title": rec.get("title"),
                    "doi": normalize_doi(str(rec.get("doi") or "")) or None,
                    "year": rec.get("year"),
                    "date_basis": rec.get("date_basis", "unknown"),
                    "material_access": access,
                    "stance": stance,
                    "evidence_state": ev_state,
                    "hedging_register": _hedging_register(ev_state, access, is_prosp, is_conflict),
                    "is_prospective_or_protocol": is_prosp,
                    "locator": p["locator"],
                    "quote": q_txt,
                }
                entries.append(entry)
                if is_conflict:
                    conflicts.append(entry)
                if is_prosp:
                    prospective_items.append(entry)
                rel_type = (
                    "opposes" if stance == "opposes" else (
                        "supports" if (stance == "supports" and is_fulltext_access(access)) else (
                            "suggests" if stance == "supports" else "observes"
                        )
                    )
                )
                relation_edges.append({
                    "source_record_id": rec["record_id"],
                    "relation": rel_type,
                    "target_theme": tname,
                    "locator": p["locator"],
                    "material_access": access,
                    "causal_claim_allowed": False,
                })

        # Note-first cross-paper synthesis by shared claim_key
        claim_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            if entry.get("claim_key"):
                claim_groups[str(entry["claim_key"])].append(entry)

        synthesis: list[dict[str, Any]] = []
        for claim_key, group in sorted(claim_groups.items()):
            if len({str(item["record_id"]) for item in group}) < 2:
                continue
            valid = [item for item in group if item["evidence_state"] in {"SUPPORTED", "REFUTED", "PARTIAL"}]
            supported = sum(item["evidence_state"] == "SUPPORTED" for item in valid)
            refuted = sum(item["evidence_state"] == "REFUTED" for item in valid)
            partial = sum(item["evidence_state"] == "PARTIAL" for item in valid)
            unknown = len(group) - len(valid)
            verified_note_entries = [item for item in valid if item.get("note_file") and item.get("quote_grounded_in_pdf")]
            comparisons = [
                f"{item['record_id']} ({item.get('title') or 'title unavailable'}; stance={item.get('stance')}) reports {json.dumps(item['quote'], ensure_ascii=False)}"
                for item in verified_note_entries
            ]
            unknown_reasons = []
            for item in group:
                if item["evidence_state"] != "UNKNOWN":
                    continue
                if item.get("pdf_link_status") != "available":
                    reason = f"linked PDF {item.get('pdf_link_status') or 'unavailable'}"
                elif item.get("quote_grounded_in_pdf") is False:
                    st = item.get("pdf_locator_status")
                    reason = "cited PDF page does not exist" if st == "page_not_found" else ("cited PDF page cannot be parsed" if st == "page_unparseable" else "note quote not found on cited PDF page")
                else:
                    reason = "claim span or locator unverified"
                unknown_reasons.append(f"{item['record_id']} ({reason})")
            comp_txt = f"; PDF-matched observations: {'; '.join(comparisons)}" if comparisons else ""
            unk_txt = f"; unresolved sources: {', '.join(unknown_reasons)}" if unknown_reasons else ""
            if supported and refuted:
                summary = f"Source-linked notes diverge: {supported} support vs {refuted} oppose ({unknown} unknown){comp_txt}{unk_txt}; keep disagreement visible; qualitative comparison only."
            elif supported >= 2 and not refuted:
                summary = f"{supported} source-linked notes converge in support ({unknown} unknown){comp_txt}{unk_txt}; qualitative comparison only."
            elif refuted >= 2 and not supported:
                summary = f"{refuted} source-linked notes converge against this claim ({unknown} unknown){comp_txt}{unk_txt}; qualitative comparison only."
            else:
                summary = f"No directional synthesis justified yet: {supported} supporting, {refuted} opposing, {partial} mixed, {unknown} unknown."
            synthesis.append({"claim_key": claim_key, "source_count": len({str(item["record_id"]) for item in group}), "summary": summary})

        ft_studies = {e["record_id"] for e in entries if is_fulltext_access(str(e.get("material_access") or ""))}
        abs_studies = {e["record_id"] for e in entries if str(e.get("material_access") or "") == "abstract_only"}
        supp_ids = sorted({e["record_id"] for e in entries if e["stance"] == "supports" and e["evidence_state"] in {"SUPPORTED", "PARTIAL"}})
        opp_ids = sorted({e["record_id"] for e in entries if e["stance"] in {"opposes", "mixed"} or e["evidence_state"] == "REFUTED"})

        if supp_ids and opp_ids:
            theme_status = "contested_divergent"
        elif len(ft_studies & set(supp_ids)) >= 2 and not opp_ids:
            theme_status = "convergent_fulltext_supported"
            consensus_findings.append({
                "theme": tname,
                "convergence_level": "fulltext_verified",
                "fulltext_supporting_records": sorted(ft_studies & set(supp_ids)),
                "abstract_only_records": sorted(abs_studies & set(supp_ids)),
                "statement": f"Within bounded corpus, {len(ft_studies & set(supp_ids))} full-text study/studies converge in support of '{tname}' with zero empirical counter-findings observed.",
            })
        elif len(supp_ids) >= 2 and not opp_ids:
            theme_status = "convergent_suggestive_mixed_or_abstract"
            consensus_findings.append({
                "theme": tname,
                "convergence_level": "suggestive_pending_fulltext",
                "fulltext_supporting_records": sorted(ft_studies & set(supp_ids)),
                "abstract_only_records": sorted(abs_studies & set(supp_ids)),
                "statement": f"{len(supp_ids)} study/studies converge directionally on '{tname}' ({len(ft_studies & set(supp_ids))} full-text, {len(abs_studies & set(supp_ids))} abstract-only) with no opposing findings observed; full-text verification required before elevating abstract-only rows.",
            })
        elif abs_studies and not ft_studies:
            theme_status = "suggestive_abstract_only_no_fulltext"
        else:
            theme_status = "descriptive_or_insufficient_convergence"

        themes_out.append({
            "theme": tname,
            "study_count": len({str(e["record_id"]) for e in entries}),
            "fulltext_study_count": len(ft_studies),
            "abstract_only_study_count": len(abs_studies),
            "theme_epistemic_status": theme_status,
            "supporting_record_ids": supp_ids,
            "opposing_or_mixed_record_ids": opp_ids,
            "cross_paper_synthesis": synthesis,
            "evidence_records": entries,
        })

    return themes_out, consensus_findings, conflicts, prospective_items, relation_edges


def derive_evidence_gaps(themes: list[dict[str, Any]], conflicts: list[dict[str, Any]], screening_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for th in themes:
        if th["fulltext_study_count"] == 0 and th["abstract_only_study_count"] > 0:
            gaps.append({
                "gap_type": "fulltext_missing_for_theme",
                "theme": th["theme"],
                "detail": f"All {th['abstract_only_study_count']} study/studies in theme '{th['theme']}' are abstract_only; methods, effect sizes, and controls could not be verified from full text.",
            })
        if th["supporting_record_ids"] and th["opposing_or_mixed_record_ids"]:
            gaps.append({
                "gap_type": "unresolved_directional_conflict",
                "theme": th["theme"],
                "detail": f"Theme '{th['theme']}' contains competing directions ({th['supporting_record_ids'][:4]} vs {th['opposing_or_mixed_record_ids'][:4]}); head-to-head standardized comparison is missing.",
            })
    non_ev = [s["record_id"] for s in screening_log if s["decision"] == "exclude_non_evidence"]
    if non_ev:
        gaps.append({
            "gap_type": "discovery_or_blocked_records_excluded",
            "theme": "corpus_coverage",
            "detail": f"{len(non_ev)} record(s) lacked readable abstract/fulltext (discovery_only or blocked) and were excluded prior to synthesis.",
        })
    return gaps


def verify_review_draft(draft_path: Path, included: list[dict[str, Any]], excluded_non_ev: list[dict[str, Any]]) -> dict[str, Any]:
    """Read-only strict audit of synthesis Markdown draft: validates ALL bracketed citations, hedging, and overclaims."""
    txt = draft_path.read_text(encoding="utf-8", errors="replace")
    overclaims = detect_overclaims(txt)
    inc_by_tok: dict[str, dict[str, Any]] = {}
    for r in included:
        for tok in (r["record_id"], r.get("doi") or "", f"arxiv:{r['arxiv_id']}" if r.get("arxiv_id") else "", r.get("pmcid") or ""):
            if tok and str(tok).strip():
                inc_by_tok[str(tok).strip().lower()] = r
    exc_toks = {
        str(tok).strip().lower()
        for r in excluded_non_ev
        for tok in (r["record_id"], r.get("doi") or "")
        if tok and str(tok).strip()
    }

    ungrounded: list[str] = []
    non_ev_cited: list[str] = []
    hedging_violations: list[str] = []
    citation_content_mismatches: list[str] = []
    # Precompute source term sets for sentence-level claim->source content grounding check
    rec_content_terms: dict[str, set[str]] = {}
    for r in included:
        src_blob = f"{r.get('title', '')} {r.get('abstract', '')} {str(r.get('text') or '')[:12000]}"
        rec_content_terms[str(r["record_id"]).lower()] = set(tokenize_terms(src_blob, include_bigrams=False))

    for line in txt.splitlines():
        low_line = line.lower()
        is_exclusion_note = any(k in low_line for k in ("excluded", "discovery_only", "blocked_or_unavailable", "not cited"))
        is_table_or_legend = line.lstrip().startswith("|") or "citation legend" in low_line
        strong = bool(STRONG_HEDGING_RE.search(line))
        line_had_cite = False
        line_terms = set(tokenize_terms(line, include_bigrams=False))
        for m in CITE_RE.finditer(line):
            raw = (m.group(1) or m.group(2) or "").strip()
            tok = raw.lower()
            if not tok or tok in IGNORE_BRACKET_TAGS or tok.startswith("http") or bool(re.match(r"^\d{1,3}(?:\s*[-–,]\s*\d{1,3})*$", tok)):
                continue
            # Ignore Markdown link text like [Section 1](...)
            if m.group(1) and line[m.end():m.end() + 1] == "(":
                continue
            line_had_cite = True
            if tok in exc_toks:
                if not is_exclusion_note:
                    non_ev_cited.append(raw)
            elif tok in inc_by_tok:
                rec = inc_by_tok[tok]
                if strong and not is_fulltext_access(str(rec.get("material_access") or "")):
                    hedging_violations.append(f"{raw}: strong fulltext wording used on {rec.get('material_access')} record")
                # Sentence-level claim -> source excerpt/content check (catch valid-ID misattribution!)
                if not is_exclusion_note and not is_table_or_legend and len(line_terms) >= 5:
                    src_terms = rec_content_terms.get(str(rec["record_id"]).lower(), set())
                    if src_terms and len(line_terms & src_terms) == 0:
                        citation_content_mismatches.append(
                            f"{raw}: zero lexical overlap between cited sentence and source text ({line.strip()[:100]})"
                        )
            else:
                # Reject ANY unknown bracketed citation ID (e.g., [FAKE-999], [W999999], [Smith2025], [PMC999999]) or DOI!
                ungrounded.append(raw)
        if strong and not line_had_cite and not is_exclusion_note:
            hedging_violations.append(f"Unsupported absolute/universal assertion without fulltext grounding: {line.strip()[:100]}")

    errors: list[str] = []
    if overclaims:
        errors.append(f"Overclaim detected in review draft: {overclaims}")
    if non_ev_cited:
        errors.append(f"Excluded discovery/blocked source(s) cited in draft: {sorted(set(non_ev_cited))}")
    if ungrounded:
        errors.append(f"Ungrounded citation identifier(s) in draft: {sorted(set(ungrounded))}")
    if hedging_violations:
        errors.append(f"Hedging/certainty violation(s): {hedging_violations}")
    if citation_content_mismatches:
        errors.append(f"Citation-to-source content mismatch(es): {citation_content_mismatches[:5]}")
    stripped_tail = txt.rstrip()
    if stripped_tail.endswith(("(", "（", "[", ":", "：", "—", "-")) and not stripped_tail.endswith("---"):
        errors.append("Draft appears structurally truncated mid-sentence or with an unclosed delimiter.")
    return {
        "passed": len(errors) == 0,
        "overclaims": overclaims,
        "non_evidence_cited": sorted(set(non_ev_cited)),
        "ungrounded_citations": sorted(set(ungrounded)),
        "hedging_violations": hedging_violations,
        "citation_content_mismatches": citation_content_mismatches,
        "errors": errors,
    }


def _extract_rq_facets(research_question: str, included: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract key sub-question facets from research_question and map coverage or explicit gaps."""
    rq = (research_question or "").strip()
    if not rq:
        return []
    raw_chunks = re.split(r"[,;、]|(?:\band\b|\bvs\.?\b|\bversus\b|\bor\b)", rq, flags=re.I)
    facets: list[str] = []
    seen_f: set[str] = set()
    for ch in raw_chunks:
        cleaned = re.sub(r"^(?:how|what|why|where|do|does|did|are|is|in|on|for|the|a|an)\s+", "", ch.strip(" ?."), flags=re.I).strip()
        toks = [w for w in re.findall(r"[A-Za-z0-9/-]{3,}", cleaned) if w.lower() not in {"how", "what", "where", "balance", "compare", "across", "studies", "mechanisms", "diverge", "the", "and", "with", "from"}]
        if not toks:
            continue
        label = " ".join(toks[:4])
        key = label.lower()
        if key not in seen_f:
            seen_f.add(key)
            facets.append(label)
    rows: list[dict[str, Any]] = []
    for fac in facets[:6]:
        fac_toks = [t.lower() for t in re.findall(r"[A-Za-z0-9]{2,}", fac) if len(t) >= 2]
        matched: list[str] = []
        ft_matched = 0
        for rec in included:
            hay = f"{rec.get('title', '')} {rec.get('abstract', '')} {str(rec.get('text') or '')[:3000]}".lower()
            if fac_toks and any(re.search(rf"\b{re.escape(t)}\b", hay) for t in fac_toks):
                matched.append(str(rec["record_id"]))
                if is_fulltext_access(str(rec.get("material_access") or "")):
                    ft_matched += 1
        rows.append({
            "facet": fac,
            "matched_count": len(matched),
            "fulltext_matched_count": ft_matched,
            "status": "COVERED_WITH_FULLTEXT" if ft_matched > 0 else ("ABSTRACT_ONLY_COVERAGE" if matched else "FACET_COVERAGE_GAP"),
            "sample_record_ids": matched[:5],
        })
    return rows


def render_review_markdown(
    case_id: str,
    research_question: str,
    prisma: dict[str, Any],
    themes: list[dict[str, Any]],
    consensus: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    prospective_items: list[dict[str, Any]],
    method_matrix: list[dict[str, Any]],
    evidence_gaps: list[dict[str, Any]],
    facet_coverage: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        f"# Grounded Literature Review Synthesis ({case_id})",
        "",
        f"**Research Question**: {research_question}",
        f"**Pre-Writing Screening & Scope Boundary**: Identified {prisma['n_identified']} raw records -> {prisma['n_dedup']} duplicates merged -> {prisma['n_screened']} screened -> {prisma['n_included']} included for synthesis ({prisma['n_screen_excl']} excluded prior to writing). Global completeness/novelty is NOT claimed; `abstract_only` findings are hedged as suggestive (`PARTIAL`), and prospective protocols are separated from completed experiments.",
        "",
        "## 1. Citation Legend (Verifiable Source Mapping — 1 Row per Record)",
        "| Record ID | Year (Basis) | Material Access | Source File | Title |",
        "|---|---|---|---|---|",
        *[
            f"| `{m['record_id']}` | {m['year'] or 'NA'} ({m['date_basis']}) | `{m['material_access']}` | `{m['source_file']}` | {str(m['title']).replace('|', '/')[:95]} |"
            for m in method_matrix[:35]
        ],
        "",
    ]
    if facet_coverage:
        lines.extend([
            "## 1.5 Research-Question Facet Coverage Audit",
            "| Research Question Facet | Matched Studies | Full-Text Studies | Coverage Status | Sample Record IDs |",
            "|---|---|---|---|---|",
        ])
        for fc in facet_coverage:
            lines.append(
                f"| {fc['facet']} | {fc['matched_count']} | {fc['fulltext_matched_count']} | `{fc['status']}` | {', '.join(f'`{r}`' for r in fc['sample_record_ids']) or 'none'} |"
            )
        lines.append("")
    lines.append("## 2. Empirical Theme Synthesis (Data-Driven Hierarchical Clusters)")
    for th in themes:
        lines.append(
            f"### Theme: {th['theme']} ({th['study_count']} studies: {th['fulltext_study_count']} fulltext, {th['abstract_only_study_count']} abstract-only; status=`{th['theme_epistemic_status']}`)"
        )
        for syn in th.get("cross_paper_synthesis", []):
            lines.append(f"- **Cross-paper synthesis — {syn['claim_key']}**: {syn['summary']}")
        for ev in th["evidence_records"][:10]:
            tag = "[PROSPECTIVE/PROTOCOL]" if ev["is_prospective_or_protocol"] else f"[{ev['evidence_state']}]"
            if ev.get("note_file"):
                note_span = ev.get("note_span") or {}
                note_lines = f"lines {note_span['start_line']}-{note_span['end_line']}" if note_span else "span unavailable"
                pdf_target = f"{ev.get('pdf_file') or 'unlinked'}#{ev.get('pdf_locator') or ''}"
                lines.append(
                    f"- **[{ev['record_id']}]** {tag} (`{ev['hedging_register']}`) — {ev.get('claim') or 'claim unavailable'}; note `{ev['note_file']}` ({note_lines}) -> PDF `{pdf_target}` ({ev.get('pdf_link_status')}); quote: \"{ev['quote']}\""
                )
            else:
                lines.append(
                    f"- **[{ev['record_id']}]** {tag} (`{ev['material_access']}`, `{ev['hedging_register']}`, locator `{ev.get('locator', 'body')}`): \"{ev['quote']}\""
                )

    lines.extend(["", "## 3. Horizontal Functional-Operation Comparison Matrix"])
    lines.append("| Record ID | Study Objects | Functional Methods Detected | Metrics | Method Fact Tag |")
    lines.append("|---|---|---|---|---|")
    for m in method_matrix[:25]:
        lines.append(
            f"| `{m['record_id']}` | {', '.join(m['study_objects'][:3])} | {', '.join(m['functional_methods_detected'][:3])} | {', '.join(m['evaluation_metrics_detected'][:3])} | `{m['method_fact_tag']}` |"
        )

    lines.extend(["", "## 4. Consensus vs. Contested Conflict vs. Prospective Separation"])
    lines.append("### 4.1 Convergent Findings (Within Bounded Corpus)")
    if consensus:
        for c in consensus:
            lines.append(f"- **{c['theme']}** (`{c['convergence_level']}`): {c['statement']}")
    else:
        lines.append("- No theme reached multi-study directional convergence without opposing passages; do not claim field consensus.")

    lines.append("")
    lines.append("### 4.2 Conflicting / Opposing / Mixed Evidence (Preserved Side by Side)")
    if conflicts:
        for c in conflicts[:15]:
            lines.append(f"- **[{c['record_id']}]** (`{c['evidence_state']}`, `{c.get('material_access', 'unknown')}`, locator `{c.get('locator', '')}`): \"{c['quote']}\"")
    else:
        lines.append("- No direct empirical opposing/null passages detected in inspected corpus.")

    lines.append("")
    lines.append("### 4.3 Prospective Protocols / Uncompleted Experiments")
    if prospective_items:
        for p in prospective_items[:10]:
            lines.append(f"- **[{p['record_id']}]** (`{p.get('locator', '')}`): \"{p['quote']}\"")
    else:
        lines.append("- None flagged as uncompleted/prospective protocols in inspected passages.")

    lines.extend(["", "## 5. Concrete Evidence-Anchored Research Gaps"])
    for g in evidence_gaps:
        lines.append(f"- `{g.get('gap_type', 'evidence_gap')}` ({g.get('theme', 'corpus')}): {g.get('detail', '')}")
    return "\n".join(lines) + "\n"


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="literature-review-pipeline")

    bundle = load_input_bundle(input_dir, work_dir=work_dir)
    case_id = bundle["case_id"]
    case_data = bundle["case_data"]

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="grounded-literature-review")
    budget_guard.tick_phase(budget_file, "screen_and_synthesize")

    raw_records = bundle["records"]
    by_source = summarize_identification_by_source(raw_records)
    canonical, dup_log, meta_conflicts = deduplicate_bibliography(raw_records)
    included, screening_log = screen_before_writing(canonical, case_data)
    excluded_non_ev = [r for r in canonical if is_non_evidence_access(str(r.get("material_access") or ""))]
    n_screen_excl = len(canonical) - len(included)

    prisma = verify_prisma_conservation(
        n_identified=len(raw_records),
        n_dedup=len(dup_log),
        n_screened=len(canonical),
        n_screen_excl=n_screen_excl,
        n_included=len(included),
        identified_by_source=by_source,
    )

    rq = str(case_data.get("research_question") or (bundle["queries"][0] if bundle["queries"] else case_data.get("title") or "bounded literature synthesis"))
    themes, consensus, conflicts, prospective_items, relation_edges = extract_themes_and_evidence(included)
    method_matrix = build_horizontal_method_matrix(included)
    evidence_gaps = derive_evidence_gaps(themes, conflicts, screening_log)

    facet_coverage = _extract_rq_facets(rq, included)
    for fc in facet_coverage:
        if fc["status"] == "FACET_COVERAGE_GAP":
            evidence_gaps.append({
                "gap_id": f"GAP-{len(evidence_gaps) + 1:02d}",
                "gap_type": "research_question_facet_unaddressed",
                "facet": fc["facet"],
                "basis": f"Zero included local records matched research-question facet '{fc['facet']}'.",
                "bounded_scope_note": "Reflects local corpus coverage gap; requires targeted retrieval rather than silent omission.",
            })
    md_path = output_dir / "review_synthesis_draft.md"
    md_text = (
        render_review_markdown(
            case_id, rq, prisma, themes, consensus, conflicts, prospective_items, method_matrix, evidence_gaps, facet_coverage=facet_coverage
        )
        + "\n---\n*End of Grounded Literature Review Draft (bounded to verified local corpus).*\n"
    )
    result_guard.atomic_write_text(md_path, md_text)
    draft_check = verify_review_draft(md_path, included, excluded_non_ev)

    matrix_doc = {
        "case_id": case_id,
        "scope_disclaimer": "Exact-source bounded synthesis only; neither global completeness/novelty nor completed future experiments are claimed.",
        "block_index_summary": bundle["block_index_summary"],
        "prisma_flow": prisma,
        "screening_before_writing": screening_log,
        "deduplication_log": dup_log,
        "metadata_conflicts": meta_conflicts,
        "themes": themes,
        "method_comparison_matrix": method_matrix,
        "consensus_findings": consensus,
        "conflicting_evidence": conflicts,
        "prospective_or_uncompleted": prospective_items,
        "evidence_relation_edges": relation_edges,
        "evidence_gaps": evidence_gaps,
        "draft_verification": draft_check,
    }
    matrix_path = output_dir / "review_evidence_matrix.json"
    result_guard.atomic_write_json(matrix_path, matrix_doc)

    append_audit_entry(
        audit_file,
        phase="synthesize",
        action="review_draft_written",
        actor="review_synthesis_builder.py",
        output={"themes": len(themes), "included": len(included), "consensus": len(consensus), "conflicts": len(conflicts)},
        record_id=case_id,
    )

    gaps = list(bundle["ingest_gaps"])
    if prospective_items:
        gaps.append({"code": "PROSPECTIVE_ITEMS_SEPARATED", "severity": "info", "source": "review_synthesis_builder", "message": f"{len(prospective_items)} prospective/protocol statement(s) separated from completed experiments."})
    unknown_claim_spans = [e for th in themes for e in th["evidence_records"] if e.get("note_file") and e.get("evidence_state") == "UNKNOWN"]
    if unknown_claim_spans:
        gaps.append({"code": "NOTE_SPAN_UNVERIFIED", "severity": "info", "source": "review_synthesis_builder", "message": f"{len(unknown_claim_spans)} note claim span(s) lacked verified PDF quote/locator and remain UNKNOWN."})
    if not draft_check["passed"]:
        gaps.append({"code": "DRAFT_GROUNDING_VIOLATION", "severity": "error", "source": "review_synthesis_builder", "message": "; ".join(draft_check["errors"])})
    if not included:
        gaps.append({"code": "EMPTY_INCLUDED_CORPUS", "severity": "error", "source": "review_synthesis_builder", "message": "No accessible evidence-bearing records remained after pre-writing screening."})

    fulltext_n = sum(1 for r in included if is_fulltext_access(str(r.get("material_access") or "")))
    abstract_n = len(included) - fulltext_n
    all_ev_rows = [e for th in themes for e in th["evidence_records"]]
    anchored_loc_n = sum(1 for e in all_ev_rows if "#" in str(e.get("locator") or e.get("pdf_locator") or ""))
    numeric_ev_n = sum(1 for e in all_ev_rows if re.search(r"\b\d+(?:\.\d+)?\s*(?:%|mv|ma|h\b|hr|hours?|ev|p\s*[<=>]|ci\b|nm|um|mg|kg|mol)", str(e.get("quote") or ""), re.I))
    patch = {
        "artifacts": ["result.json", "review_synthesis_draft.md", "review_evidence_matrix.json"],
        "sources": [
            {"record_id": r["record_id"], "title": r.get("title"), "doi": r.get("doi") or None, "year": r.get("year"), "material_access": r.get("material_access")}
            for r in included
        ],
        "prisma_flow": prisma,
        "facet_coverage": facet_coverage,
        "themes": themes,
        "method_comparison_matrix": method_matrix,
        "consensus_findings": consensus,
        "conflicting_evidence": conflicts,
        "prospective_or_uncompleted": prospective_items,
        "evidence_gaps": evidence_gaps,
        "draft_verification": draft_check,
        "uncertainty": {
            "bounded_scope": True,
            "global_completeness_claimed": False,
            "fulltext_included_count": fulltext_n,
            "abstract_only_included_count": abstract_n,
            "non_evidence_excluded_count": len(excluded_non_ev),
            "unresolved_conflicts_count": len(conflicts),
            "pagination_warnings": bundle["pagination_warnings"],
        },
        "metrics": {
            "raw_record_count": len(raw_records),
            "canonical_source_count": len(canonical),
            "included_source_count": len(included),
            "fulltext_included_count": fulltext_n,
            "abstract_only_included_count": abstract_n,
            "theme_count": len(themes),
            "consensus_theme_count": len(consensus),
            "conflict_count": len(conflicts),
            "prospective_item_count": len(prospective_items),
            "anchored_locator_count": anchored_loc_n,
            "numeric_evidence_count": numeric_ev_n,
            "prisma_balanced": 1 if prisma["balanced"] else 0,
        },
        "metric_provenance": {
            "raw_record_count": "review_evidence_matrix.json:prisma_flow.n_identified",
            "canonical_source_count": "review_evidence_matrix.json:prisma_flow.n_screened",
            "included_source_count": "review_evidence_matrix.json:prisma_flow.n_included",
            "fulltext_included_count": "review_evidence_matrix.json:method_comparison_matrix",
            "abstract_only_included_count": "review_evidence_matrix.json:method_comparison_matrix",
            "theme_count": "review_evidence_matrix.json:themes",
            "consensus_theme_count": "review_evidence_matrix.json:consensus_findings",
            "conflict_count": "review_evidence_matrix.json:conflicting_evidence",
            "prospective_item_count": "review_evidence_matrix.json:prospective_or_uncompleted",
            "anchored_locator_count": "review_evidence_matrix.json:themes[].evidence_records[].locator",
            "numeric_evidence_count": "review_evidence_matrix.json:themes[].evidence_records[].quote",
            "prisma_balanced": "review_evidence_matrix.json:prisma_flow.balanced",
        },
        "findings": [
            f"Screened {len(raw_records)} raw -> {len(canonical)} deduplicated -> {len(included)} included sources ({fulltext_n} fulltext, {abstract_n} abstract-only) into {len(themes)} empirical theme(s) ({len(consensus)} convergent, {len(conflicts)} opposing/mixed passages, {len(prospective_items)} prospective protocols)."
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(output_dir, result_file="result.json", required_files=["review_synthesis_draft.md", "review_evidence_matrix.json"])
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grounded Literature Review Synthesis Builder.")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--work", default="")
    parser.add_argument("--verify-draft", default="", help="Read-only audit of a Markdown draft (never overwrites result.json or evidence matrix).")
    parser.add_argument("--out-json", default="", help="Optional JSON path for --verify-draft audit output.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if draft grounding audit fails.")
    args = parser.parse_args(argv)
    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )

    # STRICT READ-ONLY VERIFICATION MODE: never overwrite result.json or review_evidence_matrix.json!
    if args.verify_draft:
        bundle = load_input_bundle(paths["input_dir"])
        canonical, _, _ = deduplicate_bibliography(bundle["records"])
        included, _ = screen_before_writing(canonical, bundle["case_data"])
        excluded_non_ev = [r for r in canonical if is_non_evidence_access(str(r.get("material_access") or ""))]
        vrep = verify_review_draft(Path(args.verify_draft), included, excluded_non_ev)
        if args.out_json:
            result_guard.atomic_write_json(Path(args.out_json), {"case_id": bundle["case_id"], "draft_verified": args.verify_draft, **vrep})
        print(json.dumps({"case_id": bundle["case_id"], "draft_passed": vrep["passed"], **vrep}, ensure_ascii=False))
        return 0 if (vrep["passed"] or not args.strict) else 1

    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"])
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"], "draft_passed": res["draft_verification"]["passed"]}, ensure_ascii=False))
    if args.strict and not res["draft_verification"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
