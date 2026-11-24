#!/usr/bin/env python3
"""Deep Research Agent Orchestrator (v4).

Key guarantees:
1. True resumable multi-stage checkpointing (`--resume`): persists per-stage artifacts
   (`stage1_ingest.json`, `stage2_dedup.json`, `stage3_dag.json`) and updates `checkpoint_state.json`
   after EVERY stage with an input fingerprint so interrupted runs resume cleanly without redoing work.
2. 6-Axis Research Question Decomposition DAG with stopword-filtered executable retrieval queries
   and domain-clause splitting for single compound questions.
3. 6-Tier material-access hierarchy blocking discovery snippets and capping abstract-only claims,
   plus non-blocking access-barrier logging (403/429/CAPTCHA/SSO).
4. True sub-question-scoped empirical conflict ledger (no false conflicts on neutral/header text)
   and compact `result.json` referencing `deep_research_ledger.json`.
5. Strict read-only `--verify-report --strict` catching ANY ungrounded bracketed citation
   (`[W99999]`, `[Smith2025]`, `[FAKE-999]`, DOIs, arXiv, PMC), discovery-only leaks (without
   false-positiving exclusion notes), abstract over-promotion, and global overclaims.
"""

from __future__ import annotations

import argparse
import hashlib
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
    PROSPECTIVE_CUE_RE,
    RETRACTION_CUE_RE,
    STOPWORDS,
    build_tfidf_vectors,
    classify_five_state_support,
    cosine_similarity,
    detect_overclaims,
    extract_directional_passages,
    is_fulltext_access,
    is_non_evidence_access,
    tokenize_terms,
)
from prisma_flow_auditor import deduplicate_bibliography, summarize_identification_by_source

CITE_TOKEN_RE = re.compile(
    r"\[([A-Za-z0-9_.:/-]+)\]|\b(SRC-\d+|REC-\d+|10\.\d{4,9}/[-._;()/:A-Za-z0-9]+|PMC\d{5,9}|arXiv:\d{4}\.\d{4,5})\b",
    re.I,
)
STRONG_PROOF_RE = re.compile(
    r"\b(?:conclusively\s+prov(?:ed|es|en)|definitively\s+(?:confirm(?:ed|s)|prov(?:ed|es|en))|"
    r"full\s+methods\s+(?:verify|confirm)|confirms?\s+full\s+methods|unequivocally\s+demonstrat(?:ed|es)|"
    r"proves?\s+(?:that|universal)|universal\s+cure)\b|确凿证明|全文方法证实|毫无疑问证明",
    re.I,
)
IGNORE_BRACKET_TAGS = {
    "fact_observed", "inference_bounded", "unverified_or_gap", "supported", "partial",
    "refuted", "outdated", "unknown", "prospective/protocol",
}

ACCESS_NOTES = {
    "fulltext_pdf": "Local PDF text layer extracted with page locators; verify critical claims against exact page quotes.",
    "fulltext_xml": "Full-text JATS XML sections extracted with section locators.",
    "fulltext_local_text": "Local text/Markdown/HTML document read; confirm whether document represents a full article or excerpt.",
    "captured_text": "Captured full-text field read; verify scope coverage before generalizing.",
    "abstract_only": "Abstract text only; full methods, statistics, and supplementary details are NOT inspected (capped at PARTIAL).",
    "discovery_only": "Discovery/search metadata or snippet only; MUST NOT be cited as evidence supporting a research claim.",
    "blocked_or_unavailable": "Source blocked or unreadable (paywall/CAPTCHA/403/429/empty scan); logged in access_log as coverage gap.",
}


def _compute_input_fingerprint(input_dir: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(f for f in input_dir.rglob("*") if f.is_file()):
        rel = p.relative_to(input_dir).as_posix()
        st = p.stat()
        h.update(f"{rel}:{st.st_size}:{int(st.st_mtime)}|".encode("utf-8"))
    return h.hexdigest()[:16]


def _extract_question_axes(q_text: str, case_data: dict[str, Any]) -> dict[str, str]:
    low = q_text.lower()
    years = re.findall(r"\b(19\d{2}|20[0-3]\d)\b", q_text)
    time_scope = f"{min(years)}-{max(years)}" if len(years) >= 2 else (years[0] if years else str(case_data.get("time_window") or "bounded_local_snapshot"))
    comp_match = re.search(r"\b(?:compar(?:e|ing|ed|ison)|versus|vs\.?|against|relative\s+to|baseline[s]?)\s+([^,.;?]{4,60})", q_text, re.I)
    mech_match = re.search(r"\b(?:mechanism[s]?|pathway[s]?|effect[s]?\s+of|impact\s+of|role\s+of|mediat(?:e|ed|ing)|regulat(?:e|ed|ing))\s+([^,.;?]{4,60})", q_text, re.I)
    meth_match = re.search(r"\b(?:method[s]?|assay[s]?|model[s]?|algorithm[s]?|benchmark[s]?|metric[s]?|protocol[s]?|trial[s]?|cohort[s]?|design[s]?)\b[^,.;?]{0,50}", q_text, re.I)

    ev_req = "conflict_and_gap_audit" if any(k in low for k in ("conflict", "disagree", "contradict", "gap", "limitation", "分歧", "矛盾", "空白")) else (
        "comparative_method_matrix" if any(k in low for k in ("compar", "versus", "vs", "benchmark", "method", "比较", "方法")) else "fulltext_empirical_evidence"
    )
    toks = [t for t in tokenize_terms(q_text, include_bigrams=False) if t not in STOPWORDS]
    return {
        "research_object": str(case_data.get("domain") or case_data.get("target_object") or " ".join(toks[:4]) or "corpus_subject"),
        "mechanism_or_variable": mech_match.group(0).strip() if mech_match else (" ".join(toks[:5]) or "primary_phenomenon"),
        "method_axis": meth_match.group(0).strip() if meth_match else "reported_experimental_or_computational_methods",
        "comparison_or_baseline": comp_match.group(0).strip() if comp_match else "cross_study_baselines_and_controls",
        "time_or_scope_window": time_scope,
        "evidence_requirement": ev_req,
    }


def decompose_questions_dag(case_data: dict[str, Any], task_md: str) -> list[dict[str, Any]]:
    """Build a non-overlapping, dependency-aware sub-question DAG with stopword-free executable retrieval queries."""
    raw_qs: list[str] = []
    for key in ("sub_questions", "questions"):
        val = case_data.get(key)
        if isinstance(val, list):
            for item in val:
                s = item.get("question") if isinstance(item, dict) else str(item)
                if s and s.strip() and s.strip() not in raw_qs:
                    raw_qs.append(s.strip())
    if not raw_qs and case_data.get("research_question"):
        raw_qs.append(str(case_data["research_question"]).strip())
    if task_md:
        for ln in task_md.splitlines():
            m = re.match(r"^\s*(?:\d+\.|[-*])\s+(.+?)\s*$", ln)
            if m:
                cand = m.group(1).strip()
                if len(cand) >= 18 and ("?" in cand or "？" in cand or any(w in cand.lower() for w in ("compare", "evaluate", "identify", "synthesize", "analyze", "assess", "比较", "评估", "分析", "识别"))):
                    if cand not in raw_qs and len(raw_qs) < 6:
                        raw_qs.append(cand)

    primary_rq = str(case_data.get("research_question") or case_data.get("title") or (raw_qs[0] if raw_qs else "bounded scientific evidence synthesis")).strip()
    if len(raw_qs) <= 1:
        # Clause-aware decomposition extracting actual domain terms from the compound question
        clauses = [c.strip(" ?;.,") for c in re.split(r"\b(?:and\s+(?:where|how|what|whether)|;\s*|\?\s+)", primary_rq, flags=re.I) if len(c.strip()) >= 15]
        key_terms = [t for t in tokenize_terms(primary_rq, include_bigrams=False) if t not in STOPWORDS][:6]
        focus_a = clauses[0] if clauses else primary_rq
        focus_b = clauses[1] if len(clauses) > 1 else f"methodological comparators and quantitative endpoints for {' / '.join(key_terms[:3])}"
        raw_qs = [
            f"Empirical Baseline: {focus_a}?",
            f"Method & Comparator Evaluation: How do study designs, baselines, and outcomes compare regarding {focus_b}?",
            f"Counter-Evidence & Divergence: Where do studies on {' / '.join(key_terms[:3]) or primary_rq[:60]} report null, opposing, or context-dependent outcomes?",
            f"Evidence Boundaries & Gaps: Which concrete methodological limitations, abstract-only ceilings, or access barriers bound conclusions?",
        ]

    dag: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for idx, q in enumerate(raw_qs, 1):
        sq_id = f"SQ-{idx}"
        axes = _extract_question_axes(q, case_data)
        toks = [t for t in tokenize_terms(q, include_bigrams=False) if t not in STOPWORDS and len(t) >= 3]
        q_terms = toks[:5] or tokenize_terms(primary_rq, include_bigrams=False)[:4]
        q_str = " AND ".join(f'"{t}"' for t in q_terms[:4]) if q_terms else f'"{primary_rq[:48]}"'
        if q_str in seen_queries:
            q_str = f'{q_str} AND "{axes["evidence_requirement"]}"'
        seen_queries.add(q_str)

        low = q.lower()
        if idx == 1:
            depends_on: list[str] = []
            wave = "wave_1_prerequisite_baseline"
        elif any(k in low for k in ("gap", "boundary", "synthesis", "conclu", "空白", "边界", "综合")):
            depends_on = [f"SQ-{j}" for j in range(1, idx)]
            wave = "wave_3_bounded_synthesis"
        elif any(k in low for k in ("conflict", "counter", "oppos", "disagree", "diverge", "分歧", "矛盾", "反证")):
            depends_on = ["SQ-1"]
            wave = "wave_2_parallel_audit"
        else:
            depends_on = ["SQ-1"]
            wave = "wave_2_parallel_comparison"

        dag.append({
            "sq_id": sq_id,
            "question": q,
            "axes": axes,
            "depends_on": depends_on,
            "execution_wave": wave,
            "query_terms": q_terms,
            "retrieval_query": q_str,
            "source_priority": ["fulltext_pdf", "fulltext_xml", "fulltext_local_text", "abstract_only"],
            "stop_condition": "Stop expanding once all accessible fulltext/abstract sources in snapshot are indexed or two consecutive query refinements yield zero new grounded claims.",
        })
    return dag


def build_evidence_and_conflicts(
    canonical: list[dict[str, Any]],
    subqs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Map deduplicated sources -> sub-question-linked passages via query terms + TF-IDF, preserving real conflicts."""
    sources_matrix: list[dict[str, Any]] = []
    evidence_matrix: list[dict[str, Any]] = []
    prospective_items: list[dict[str, Any]] = []

    sq_tokens = [tokenize_terms(sq["question"] + " " + " ".join(sq.get("query_terms") or [])) for sq in subqs]
    rec_tokens = [
        tokenize_terms(f"{r.get('title', '')} {r.get('abstract', '')} {str(r.get('text') or '')[:3000]}")
        for r in canonical
    ]
    all_vecs = build_tfidf_vectors(sq_tokens + rec_tokens)
    sq_vecs = all_vecs[:len(subqs)]
    rec_vecs = all_vecs[len(subqs):]

    sq_passages: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for idx, rec in enumerate(canonical, 1):
        src_id = f"SRC-{idx:02d}"
        access = str(rec.get("material_access") or "discovery_only")
        access_note = ACCESS_NOTES.get(access, "Local record read; check exact access tier before generalizing.")
        blob = f"{rec.get('title', '')} {rec.get('abstract', '')} {str(rec.get('text') or '')[:4000]}"
        is_retracted = bool(RETRACTION_CUE_RE.search(blob))
        passages = extract_directional_passages(rec)

        dirs = {p["direction"] for p in passages}
        if is_non_evidence_access(access):
            polarity = "not_assessed_non_evidence"
        elif "supportive" in dirs and ("opposing_or_null" in dirs or "mixed" in dirs):
            polarity = "mixed"
        elif "opposing_or_null" in dirs:
            polarity = "null_or_opposing"
        elif "supportive" in dirs:
            polarity = "supportive"
        elif "prospective" in dirs:
            polarity = "prospective_only"
        else:
            polarity = "descriptive_or_neutral"

        # Execute sub-question matching combining retrieval query term hit ratio + TF-IDF cosine with relevance gate!
        matched_sqs: list[str] = []
        max_relevance = 0.0
        scope_relevance = "in_scope"
        if not is_non_evidence_access(access) and subqs:
            rec_term_set = set(rec_tokens[idx - 1])
            scores = []
            for j, sq in enumerate(subqs):
                q_terms = set(sq.get("query_terms") or [])
                q_hit = (len(q_terms & rec_term_set) / max(1, len(q_terms))) if q_terms else 0.0
                cos = cosine_similarity(rec_vecs[idx - 1], sq_vecs[j])
                scores.append((round(0.6 * cos + 0.4 * q_hit, 4), sq["sq_id"]))
            scores.sort(reverse=True)
            max_relevance = scores[0][0] if scores else 0.0
            matched_sqs = [sid for sc, sid in scores if sc >= 0.06][:2]
            if not matched_sqs:
                if max_relevance >= 0.04:
                    matched_sqs = [scores[0][1]]
                else:
                    # Off-topic / low-relevance record (e.g. unrelated sensor or sleep review): do NOT attach to SQ-1 as supportive!
                    scope_relevance = "out_of_scope_background"
                    polarity = "out_of_scope_background"

        src_row = {
            "source_id": src_id,
            "record_id": rec["record_id"],
            "title": rec.get("title") or rec["record_id"],
            "year": rec.get("year"),
            "date_basis": rec.get("date_basis", "unknown"),
            "doi": rec.get("doi") or None,
            "arxiv_id": rec.get("arxiv_id") or None,
            "venue": rec.get("venue") or "",
            "source_files": rec.get("source_files") or ([rec["source_file"]] if rec.get("source_file") else []),
            "found_in_sources": rec.get("found_in_sources") or [rec.get("source", "local")],
            "material_access": access,
            "access_note": access_note,
            "polarity": polarity,
            "scope_relevance": scope_relevance,
            "max_sq_relevance": max_relevance,
            "is_retracted_or_erratum": is_retracted,
            "matched_sub_questions": matched_sqs,
            "version_upgrade": rec.get("version_upgrade"),
        }
        sources_matrix.append(src_row)

        if is_non_evidence_access(access) or scope_relevance == "out_of_scope_background":
            continue

        for p in passages:
            is_prosp = bool(p.get("is_prospective")) or bool(PROSPECTIVE_CUE_RE.search(p["quote"]))
            ev_state = classify_five_state_support(
                has_direct_support=p["direction"] in {"supportive", "mixed", "descriptive", "limitation"},
                has_opposing_evidence=p["direction"] in {"opposing_or_null", "mixed"},
                missing_critical_context=is_prosp or not is_fulltext_access(access),
                is_superseded_or_retracted=is_retracted,
                source_unavailable=False,
                material_access=access,
            )
            epistemic = "UNVERIFIED_OR_GAP" if ev_state == "UNKNOWN" else (
                "FACT_OBSERVED" if is_fulltext_access(access) and not is_prosp else "INFERENCE_BOUNDED"
            )
            target_sq = matched_sqs[0] if matched_sqs else "SQ-1"
            if p["direction"] in {"opposing_or_null", "mixed"}:
                conf_sq = next((sq["sq_id"] for sq in subqs if sq["axes"]["evidence_requirement"] == "conflict_and_gap_audit"), target_sq)
                target_sq = conf_sq
            elif p.get("is_limitation"):
                gap_sq = next((sq["sq_id"] for sq in subqs if "gap" in sq["question"].lower() or "limitation" in sq["question"].lower() or "空白" in sq["question"]), target_sq)
                target_sq = gap_sq

            ev_row = {
                "evidence_id": f"EV-{len(evidence_matrix) + 1:03d}",
                "sq_id": target_sq,
                "source_id": src_id,
                "record_id": rec["record_id"],
                "doi": rec.get("doi") or None,
                "year": rec.get("year"),
                "date_basis": rec.get("date_basis", "unknown"),
                "material_access": access,
                "locator": p["locator"],
                "direction": p["direction"],
                "evidence_state": ev_state,
                "epistemic_level": epistemic,
                "is_prospective_or_protocol": is_prosp,
                "quote": p["quote"],
            }
            evidence_matrix.append(ev_row)
            sq_passages[target_sq].append(ev_row)
            if is_prosp:
                prospective_items.append(ev_row)

    conflicts: list[dict[str, Any]] = []
    for sq in subqs:
        rows = sq_passages.get(sq["sq_id"], [])
        supp = [r for r in rows if r["direction"] == "supportive"]
        opp = [r for r in rows if r["direction"] in {"opposing_or_null", "mixed"}]
        if supp and opp:
            conflicts.append({
                "conflict_id": f"CONF-{len(conflicts) + 1:02d}",
                "sq_id": sq["sq_id"],
                "question": sq["question"],
                "supportive_evidence": [{"source_id": r["source_id"], "record_id": r["record_id"], "locator": r["locator"], "material_access": r["material_access"], "quote": r["quote"]} for r in supp[:5]],
                "opposing_or_null_evidence": [{"source_id": r["source_id"], "record_id": r["record_id"], "locator": r["locator"], "material_access": r["material_access"], "quote": r["quote"]} for r in opp[:5]],
                "reconciliation_policy": "Preserve supportive and null/opposing findings side by side; explain divergence by study design, population, or fulltext vs abstract access rather than smoothing disagreement.",
            })

    if not conflicts:
        all_supp = [r for r in evidence_matrix if r["direction"] == "supportive"]
        all_opp = [r for r in evidence_matrix if r["direction"] in {"opposing_or_null", "mixed"}]
        if all_supp and all_opp:
            conflicts.append({
                "conflict_id": "CONF-01",
                "sq_id": "CORPUS_WIDE",
                "question": subqs[0]["question"] if subqs else "Primary research question",
                "supportive_evidence": [{"source_id": r["source_id"], "record_id": r["record_id"], "locator": r["locator"], "material_access": r["material_access"], "quote": r["quote"]} for r in all_supp[:5]],
                "opposing_or_null_evidence": [{"source_id": r["source_id"], "record_id": r["record_id"], "locator": r["locator"], "material_access": r["material_access"], "quote": r["quote"]} for r in all_opp[:5]],
                "reconciliation_policy": "Opposing or mixed evidence spans sub-questions; present both sides with exact locators and scope conditions.",
            })

    research_gaps: list[dict[str, Any]] = []
    for c in conflicts:
        research_gaps.append({
            "gap_id": f"GAP-{len(research_gaps) + 1:02d}",
            "gap_type": "unresolved_evidence_conflict",
            "sq_id": c["sq_id"],
            "basis": f"Competing empirical passages between {[x['source_id'] for x in c['supportive_evidence'][:3]]} and {[x['source_id'] for x in c['opposing_or_null_evidence'][:3]]}.",
            "bounded_scope_note": "Observed within local corpus; resolving requires direct comparison of experimental conditions and full methods.",
        })
    for sq in subqs:
        rows = sq_passages.get(sq["sq_id"], [])
        ft_rows = [r for r in rows if is_fulltext_access(r["material_access"])]
        if not rows:
            research_gaps.append({
                "gap_id": f"GAP-{len(research_gaps) + 1:02d}",
                "gap_type": "bounded_subquestion_evidence_gap",
                "sq_id": sq["sq_id"],
                "basis": f"Zero evidence-bearing passages matched {sq['sq_id']} inside the provided finite corpus.",
                "bounded_scope_note": "Indicates retrieval/corpus boundary gap, NOT global absence of published studies.",
            })
        elif not ft_rows:
            research_gaps.append({
                "gap_id": f"GAP-{len(research_gaps) + 1:02d}",
                "gap_type": "abstract_only_coverage_ceiling",
                "sq_id": sq["sq_id"],
                "basis": f"{len(rows)} passage(s) for {sq['sq_id']} come solely from abstract_only sources; zero full-text PDF/XML passages inspected.",
                "bounded_scope_note": "Methods, statistical controls, and subgroup outcomes remain unverified until full text is retrieved.",
            })

    return sources_matrix, evidence_matrix, conflicts, prospective_items, research_gaps


def verify_synthesis_report(
    report_path: Path,
    sources_matrix: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    access_log: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read-only strict audit: catches ALL ungrounded bracketed citations (any prefix), discovery leaks,
    abstract over-promotion, and global overclaims while exempting explicit exclusion notes."""
    txt = report_path.read_text(encoding="utf-8", errors="replace")
    overclaims = detect_overclaims(txt)

    by_token: dict[str, dict[str, Any]] = {}
    for s in sources_matrix:
        for tok in (
            s["source_id"],
            s["record_id"],
            s.get("doi") or "",
            f"arxiv:{s['arxiv_id']}" if s.get("arxiv_id") else "",
            *(s.get("source_files") or []),
            *(Path(sf).stem for sf in (s.get("source_files") or [])),
        ):
            if tok and str(tok).strip():
                by_token[str(tok).strip().lower()] = s
    for al in (access_log or []):
        for tok in (al.get("record_id") or "", al.get("source_id") or "", al.get("source_file") or ""):
            if tok and str(tok).strip() and str(tok).strip().lower() not in by_token:
                by_token[str(tok).strip().lower()] = {"material_access": "blocked_or_unavailable", "record_id": str(tok).strip()}

    ungrounded: list[str] = []
    discovery_only_refs: list[str] = []
    blocked_refs: list[str] = []
    abstract_overpromotions: list[str] = []

    for line in txt.splitlines():
        low_l = line.lower()
        is_exclusion_note = any(k in low_l for k in ("excluded", "blocked_or_unavailable", "blocked source", "was not accessed", "access barrier", "not cited", "halted without bypass"))
        line_strong = bool(STRONG_PROOF_RE.search(line))
        for m in CITE_TOKEN_RE.finditer(line):
            raw_tok = (m.group(1) or m.group(2) or "").strip()
            tok = raw_tok.lower()
            if (
                not tok
                or tok in IGNORE_BRACKET_TAGS
                or tok.startswith(("sq-", "ev-", "conf-", "gap-", "http"))
                or bool(re.match(r"^\d{1,3}(?:\s*[-–,]\s*\d{1,3})*$", tok))
            ):
                continue
            if m.group(1) and line[m.end():m.end() + 1] == "(":
                continue
            src = by_token.get(tok)
            if src is None:
                # Reject ANY unrecognized bracketed citation (e.g. [W999999], [Smith2025], [PAPER-99], [FAKE-999]) or DOI!
                ungrounded.append(raw_tok)
                continue
            acc = src["material_access"]
            if acc == "discovery_only":
                if not is_exclusion_note:
                    discovery_only_refs.append(raw_tok)
            elif acc == "blocked_or_unavailable":
                if not is_exclusion_note:
                    blocked_refs.append(raw_tok)
            elif acc == "abstract_only" and line_strong:
                abstract_overpromotions.append(f"{raw_tok}: strong fulltext proof wording on abstract_only source")

    errors: list[str] = []
    if overclaims:
        errors.append(f"Unbounded global completeness or completed-experiment overclaim detected: {overclaims}")
    if ungrounded:
        errors.append(f"Ungrounded citation/source reference(s): {sorted(set(ungrounded))}")
    if discovery_only_refs:
        errors.append(f"Discovery-only metadata cited as synthesis evidence: {sorted(set(discovery_only_refs))}")
    if blocked_refs:
        errors.append(f"Blocked/unavailable source cited as evidence: {sorted(set(blocked_refs))}")
    if abstract_overpromotions:
        errors.append(f"Abstract-only source over-promoted as conclusive fulltext proof: {abstract_overpromotions}")
    if conflicts and not any(k in txt.lower() for k in ("conflict", "opposing", "contradict", "disagree", "null", "分歧", "矛盾", "反证")):
        errors.append("Corpus contains opposing evidence conflicts, but report omits conflict presentation.")

    return {
        "passed": len(errors) == 0,
        "overclaims": overclaims,
        "ungrounded_refs": sorted(set(ungrounded)),
        "discovery_only_refs": sorted(set(discovery_only_refs)),
        "blocked_refs": sorted(set(blocked_refs)),
        "abstract_overpromotions": abstract_overpromotions,
        "errors": errors,
    }


def render_brief_markdown(
    case_id: str,
    research_question: str,
    subqs: list[dict[str, Any]],
    sources_matrix: list[dict[str, Any]],
    evidence_matrix: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    prospective_items: list[dict[str, Any]],
    research_gaps: list[dict[str, Any]],
    access_log: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> str:
    acc_counts = Counter(s["material_access"] for s in sources_matrix)
    evidence_sources = [s for s in sources_matrix if not is_non_evidence_access(s["material_access"])]
    excluded_non_ev = [s for s in sources_matrix if is_non_evidence_access(s["material_access"])]
    lines = [
        f"# Bounded Deep Research Synthesis Brief ({case_id})",
        "",
        f"**Research Question**: {research_question}",
        f"**Corpus Scope & Access Distribution**: {len(sources_matrix)} deduplicated sources (`{json.dumps(dict(sorted(acc_counts.items())), ensure_ascii=False)}`).",
        f"**Epistemic Boundary**: Findings apply strictly to the {len(evidence_sources)} evidence-bearing local sources inspected. {len(excluded_non_ev)} `discovery_only` / `blocked_or_unavailable` record(s) are retained in `deep_research_ledger.json` for retrieval provenance ONLY and excluded from synthesis citations; `abstract_only` records are capped at `PARTIAL` and never represent full-text verification. Global exhaustiveness and completed prospective experiments are NOT claimed.",
        "",
        "## 1. Research Question Decomposition DAG & Retrieval Plan",
        "| Sub-Question | Wave | Depends On | Evidence Requirement | Retrieval Query |",
        "|---|---|---|---|---|",
        *[
            f"| `{sq['sq_id']}`: {sq['question']} | {sq['execution_wave']} | {', '.join(sq['depends_on']) or 'none'} | {sq['axes']['evidence_requirement']} | `{sq['retrieval_query']}` |"
            for sq in subqs
        ],
        "",
        "## 2. Evidence-Bearing Source Ledger & Access Tiers (Discovery-Only Excluded)",
        "| Source ID | Record ID | Year (Basis) | Material Access | Polarity | Matched SQs |",
        "|---|---|---|---|---|---|",
        *[
            f"| `{s['source_id']}` | {s['record_id']} | {s['year'] or 'NA'} ({s['date_basis']}) | `{s['material_access']}` | {s['polarity']} | {', '.join(s['matched_sub_questions']) or 'none'} |"
            for s in evidence_sources[:40]
        ],
    ]
    if len(evidence_sources) > 40:
        lines.append(f"\n*Showing first 40 of {len(evidence_sources)} evidence-bearing canonical sources; full ledger (including {len(excluded_non_ev)} excluded discovery-only records) in `deep_research_ledger.json`.*")

    lines.extend(["", "## 3. Sub-Question Grounded Evidence Matrix (Passage-Linked)"])
    for sq in subqs:
        sq_rows = [e for e in evidence_matrix if e["sq_id"] == sq["sq_id"]]
        lines.append(f"### {sq['sq_id']}: {sq['question']}")
        if not sq_rows:
            lines.append("- `[UNVERIFIED_OR_GAP]` No evidence-bearing passages mapped to this sub-question within the bounded local corpus.")
            continue
        for ev in sq_rows[:8]:
            lines.append(
                f"- [{ev['source_id']}] `{ev['evidence_state']}` (`{ev['epistemic_level']}`, `{ev['material_access']}`, `{ev['direction']}`, locator `{ev['locator']}`): \"{ev['quote']}\""
            )

    lines.extend(["", "## 4. Conflicting / Opposing Evidence (Preserved Side by Side)"])
    if conflicts:
        for c in conflicts:
            lines.append(f"### {c['conflict_id']} ({c['sq_id']})")
            lines.append(f"- **Policy**: {c['reconciliation_policy']}")
            lines.append("- **Supportive passages**:")
            for s in c["supportive_evidence"][:3]:
                lines.append(f"  - [{s['source_id']}] (`{s['material_access']}`, `{s['locator']}`): \"{s['quote']}\"")
            lines.append("- **Opposing / Null / Mixed passages**:")
            for o in c["opposing_or_null_evidence"][:3]:
                lines.append(f"  - [{o['source_id']}] (`{o['material_access']}`, `{o['locator']}`): \"{o['quote']}\"")
    else:
        lines.append("- No direct cross-source empirical conflict detected in inspected passages; verify whether unretrieved full-text or blocked sources hold opposing results.")

    lines.extend(["", "## 5. Prospective Protocols & Uncompleted Proposals (Separated from Empirical Proof)"])
    if prospective_items:
        for p in prospective_items[:10]:
            lines.append(f"- [{p['source_id']}] (`{p['locator']}`): \"{p['quote']}\"")
    else:
        lines.append("- None flagged as uncompleted/prospective protocols in inspected passages.")

    lines.extend(["", "## 6. Evidence-Grounded Research Gaps & Access Barriers"])
    for g in research_gaps:
        lines.append(f"- `{g['gap_id']}` (`{g['gap_type']}`, {g['sq_id']}): {g['basis']} *Boundary*: {g['bounded_scope_note']}")
    if access_log:
        lines.append("")
        lines.append("### Access Barriers Logged (Halted Without Bypass)")
        for a in access_log:
            lines.append(f"- `{a['record_id']}` (`{a['source_file']}`): status=`{a['status']}`, reason=`{a['reason']}`, action=`{a['action']}`")
    if gaps:
        lines.append("")
        lines.append("### Pipeline & Ingest Gaps")
        for g in gaps:
            lines.append(f"- `{g['code']}` ({g['severity']}): {g['source']} — {g['message']}")
    return "\n".join(lines) + "\n"


def _save_checkpoint(ckpt_path: Path, fp: str, case_id: str, completed: list[str], extra: dict[str, Any]) -> None:
    payload = {"input_fingerprint": fp, "case_id": case_id, "completed_stages": completed, "resumable": True, **extra}
    result_guard.atomic_write_json(ckpt_path, payload)


def run_pipeline(
    input_dir: Path,
    out_json: Path,
    out_md: Path | None = None,
    output_dir: Path | None = None,
    work_dir: Path | None = None,
    verify_report: Path | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    eff_out_dir = output_dir or out_json.parent
    eff_work_dir = work_dir or (eff_out_dir.parent / "work")
    eff_work_dir.mkdir(parents=True, exist_ok=True)

    input_fp = _compute_input_fingerprint(input_dir)
    ckpt_path = eff_work_dir / "checkpoint_state.json"
    st1_path = eff_work_dir / "stage1_ingest.json"
    st2_path = eff_work_dir / "stage2_dedup.json"
    st3_path = eff_work_dir / "stage3_dag.json"

    prev_ckpt: dict[str, Any] = {}
    if resume and ckpt_path.is_file():
        try:
            loaded = json.loads(ckpt_path.read_text(encoding="utf-8"))
            if loaded.get("input_fingerprint") == input_fp:
                prev_ckpt = loaded
        except Exception:
            prev_ckpt = {}
    completed_prev = set(prev_ckpt.get("completed_stages") or [])
    tool_log: list[dict[str, Any]] = []
    completed_now: list[str] = []

    # Stage 1: Multi-source ingest (resumable from stage1_ingest.json)
    if "1_ingest_multisource" in completed_prev and st1_path.is_file():
        bundle = json.loads(st1_path.read_text(encoding="utf-8"))
        tool_log.append({"step": "1_ingest_multisource", "status": "resumed_from_checkpoint", "raw_records": len(bundle["records"])})
    else:
        bundle = load_input_bundle(input_dir, work_dir=eff_work_dir)
        result_guard.atomic_write_json(st1_path, {k: v for k, v in bundle.items() if k != "raw_texts"})
        tool_log.append({
            "step": "1_ingest_multisource",
            "params": {"input_dir": str(input_dir)},
            "files_scanned": len(bundle["source_inventory"]),
            "raw_records": len(bundle["records"]),
            "blocked_entries": len(bundle["access_log"]),
            "status": "ok" if bundle["records"] or bundle["access_log"] else "empty",
        })
    completed_now.append("1_ingest_multisource")
    case_id = bundle["case_id"]
    case_data = bundle["case_data"]
    raw_records = bundle["records"]
    _save_checkpoint(ckpt_path, input_fp, case_id, completed_now, {"blocked_sources": bundle["access_log"], "pagination_warnings": bundle["pagination_warnings"]})

    # Stage 2: Deduplication & lineage upgrade (resumable from stage2_dedup.json)
    if "2_deduplicate_and_upgrade_lineage" in completed_prev and st2_path.is_file():
        st2 = json.loads(st2_path.read_text(encoding="utf-8"))
        canonical, dup_log, meta_conflicts, by_source = st2["canonical"], st2["dup_log"], st2["meta_conflicts"], st2["by_source"]
        tool_log.append({"step": "2_deduplicate_and_upgrade_lineage", "status": "resumed_from_checkpoint", "canonical_sources": len(canonical)})
    else:
        canonical, dup_log, meta_conflicts = deduplicate_bibliography(raw_records)
        by_source = summarize_identification_by_source(raw_records)
        result_guard.atomic_write_json(st2_path, {"canonical": canonical, "dup_log": dup_log, "meta_conflicts": meta_conflicts, "by_source": by_source})
        tool_log.append({
            "step": "2_deduplicate_and_upgrade_lineage",
            "raw_records": len(raw_records),
            "canonical_sources": len(canonical),
            "duplicates_merged": len(dup_log),
            "meta_conflicts": len(meta_conflicts),
            "status": "ok",
        })
    completed_now.append("2_deduplicate_and_upgrade_lineage")
    _save_checkpoint(ckpt_path, input_fp, case_id, completed_now, {"blocked_sources": bundle["access_log"], "pagination_warnings": bundle["pagination_warnings"]})

    # Stage 3: Sub-question DAG (resumable from stage3_dag.json)
    if "3_decompose_question_dag" in completed_prev and st3_path.is_file():
        subqs = json.loads(st3_path.read_text(encoding="utf-8"))["subqs"]
        tool_log.append({"step": "3_decompose_question_dag", "status": "resumed_from_checkpoint", "sub_questions": len(subqs)})
    else:
        subqs = decompose_questions_dag(case_data, bundle["task_md"])
        result_guard.atomic_write_json(st3_path, {"subqs": subqs})
        tool_log.append({"step": "3_decompose_question_dag", "sub_questions": len(subqs), "waves": sorted({sq["execution_wave"] for sq in subqs}), "status": "ok"})
    completed_now.append("3_decompose_question_dag")
    _save_checkpoint(ckpt_path, input_fp, case_id, completed_now, {"blocked_sources": bundle["access_log"], "pagination_warnings": bundle["pagination_warnings"]})

    # Stage 4: Passage synthesis & conflict preservation
    sources_matrix, evidence_matrix, conflicts, prospective_items, research_gaps = build_evidence_and_conflicts(canonical, subqs)
    tool_log.append({
        "step": "4_synthesize_passage_evidence_and_conflicts",
        "evidence_rows": len(evidence_matrix),
        "conflicts": len(conflicts),
        "prospective_items": len(prospective_items),
        "research_gaps": len(research_gaps),
        "status": "ok",
    })
    completed_now.append("4_synthesize_passage_evidence_and_conflicts")
    _save_checkpoint(ckpt_path, input_fp, case_id, completed_now, {"blocked_sources": bundle["access_log"], "pagination_warnings": bundle["pagination_warnings"]})

    # STRICT READ-ONLY VERIFY MODE: audit report without overwriting deliverables or result.json!
    if verify_report is not None:
        vrep = verify_synthesis_report(verify_report, sources_matrix, conflicts, access_log=bundle["access_log"])
        out_payload = {"case_id": case_id, "passed": vrep["passed"], "report_verified": str(verify_report), "synthesis_verification": vrep, "sources_matrix_count": len(sources_matrix)}
        out_json.parent.mkdir(parents=True, exist_ok=True)
        result_guard.atomic_write_json(out_json, out_payload)
        return out_payload

    eff_out_dir.mkdir(parents=True, exist_ok=True)
    preflight_probe.probe_environment(input_dir=input_dir, output_dir=eff_out_dir, work_dir=eff_work_dir)
    budget_file = eff_work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = eff_work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="deep-research-orchestrator")

    acc_counts = dict(sorted(Counter(s["material_access"] for s in sources_matrix).items()))
    discovery_n = acc_counts.get("discovery_only", 0)
    raw_disc_n = sum(1 for r in raw_records if str(r.get("material_access") or "") == "discovery_only")
    out_of_scope_n = sum(1 for s in sources_matrix if s.get("scope_relevance") == "out_of_scope_background")
    # Single-source-of-truth gap reconciliation: replace raw discovery_only gap with canonical post-dedup count!
    gaps = [g for g in bundle["ingest_gaps"] if "discovery_only" not in str(g.get("code", "")).lower() and "discovery_only" not in str(g.get("message", "")).lower()]
    if discovery_n > 0:
        gaps.append({
            "code": "DISCOVERY_ONLY_EXCLUDED",
            "severity": "info",
            "source": "deep_research_orchestrator",
            "canonical_count": discovery_n,
            "raw_count": raw_disc_n,
            "message": f"{discovery_n} canonical deduplicated source(s) (deduplicated from {raw_disc_n} raw records) are discovery_only and strictly excluded from synthesis evidence.",
        })
    if out_of_scope_n > 0:
        gaps.append({
            "code": "OUT_OF_SCOPE_LOW_RELEVANCE_FILTERED",
            "severity": "info",
            "source": "deep_research_orchestrator",
            "count": out_of_scope_n,
            "message": f"{out_of_scope_n} source(s) scored below sub-question relevance threshold (<0.04) and were quarantined as out_of_scope_background rather than attached to sub-questions.",
        })
    if bundle["access_log"]:
        gaps.append({
            "code": "ACCESS_BARRIER_SOURCES_LOGGED",
            "severity": "warning",
            "source": "deep_research_orchestrator",
            "message": f"{len(bundle['access_log'])} source(s) encountered access barriers (blocked/CAPTCHA/403/429/unreadable); halted cleanly without bypass.",
        })
    if not canonical:
        gaps.append({"code": "EMPTY_CORPUS", "severity": "error", "source": "deep_research_orchestrator", "message": "No parseable research records found in input directory."})

    rq = str(case_data.get("research_question") or case_data.get("title") or subqs[0]["question"])
    md_text = render_brief_markdown(case_id, rq, subqs, sources_matrix, evidence_matrix, conflicts, prospective_items, research_gaps, bundle["access_log"], gaps)
    if out_md is not None:
        result_guard.atomic_write_text(out_md, md_text)

    verification = verify_synthesis_report(out_md, sources_matrix, conflicts, access_log=bundle["access_log"]) if (out_md is not None and out_md.is_file()) else {"passed": True, "errors": []}
    passed = len(sources_matrix) > 0 and verification["passed"]

    anchored_locator_n = sum(1 for e in evidence_matrix if "#" in str(e.get("locator") or "") and not str(e.get("locator") or "").endswith("#body"))
    # Output-contract completeness gate: ensure core sub-questions (including SQ-1) are answered & record-level denominator lineages exist
    sq_evidence_counts: dict[str, int] = {sq["sq_id"]: 0 for sq in subqs}
    for e in evidence_matrix:
        for sid in (e.get("matched_sub_questions") or [e.get("sub_question_id")]):
            if sid in sq_evidence_counts:
                sq_evidence_counts[sid] += 1
    unanswered_sqs = [sid for sid, cnt in sq_evidence_counts.items() if cnt == 0]
    discovery_only_ids = [s["record_id"] for s in sources_matrix if s.get("material_access") == "discovery_only"]
    out_of_scope_ids = [s["record_id"] for s in sources_matrix if s.get("scope_relevance") == "out_of_scope_background"]
    output_contract_audit = {
        "sq_evidence_counts": sq_evidence_counts,
        "sq1_answered": sq_evidence_counts.get("SQ-1", 0) > 0,
        "unanswered_sub_questions": unanswered_sqs,
        "denominator_record_lineage": {
            "raw_total": len(raw_records),
            "canonical_total": len(sources_matrix),
            "discovery_only_ids": discovery_only_ids,
            "out_of_scope_ids": out_of_scope_ids,
            "blocked_sources_count": len(bundle["access_log"]),
        },
        "contract_complete": (len(unanswered_sqs) == 0 and verification["passed"] and len(sources_matrix) > 0),
    }
    if unanswered_sqs:
        gaps.append({
            "code": "OUTPUT_CONTRACT_UNANSWERED_SQ",
            "severity": "warning",
            "source": "deep_research_orchestrator",
            "message": f"Sub-question(s) without direct grounded evidence in local corpus: {unanswered_sqs}; status marked partial_with_gaps.",
        })
    ledger_status = "completed" if (not gaps and output_contract_audit["contract_complete"]) else "partial_with_gaps"
    ledger: dict[str, Any] = {
        "case_id": case_id,
        "passed": passed,
        "status": ledger_status,
        "output_contract_audit": output_contract_audit,
        "corpus_scope": f"bounded_local_corpus ({len(raw_records)} raw -> {len(sources_matrix)} canonical sources)",
        "block_index_summary": bundle.get("block_index_summary", {}),
        "identified_by_source": by_source,
        "access_tier_breakdown": acc_counts,
        "sub_questions_dag": subqs,
        "sources_matrix": sources_matrix,
        "evidence_matrix": evidence_matrix,
        "conflict_ledger": conflicts,
        "prospective_protocols": prospective_items,
        "research_gaps": research_gaps,
        "deduplication_log": dup_log,
        "metadata_conflicts": meta_conflicts,
        "access_log": bundle["access_log"],
        "pagination_warnings": bundle["pagination_warnings"],
        "tool_call_log": tool_log,
        "synthesis_verification": verification,
        "gaps": gaps,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    result_guard.atomic_write_json(out_json, ledger)
    append_audit_entry(audit_file, phase="deep_research", action="ledger_written", actor="deep_research_orchestrator.py", output={"canonical": len(sources_matrix), "evidence_rows": len(evidence_matrix), "conflicts": len(conflicts)}, record_id=case_id)

    if output_dir is not None:
        result_path = eff_out_dir / "result.json"
        result_guard.init_result(result_path, case_id=case_id, task_id="deep-research-orchestrator")
        fulltext_n = sum(1 for s in sources_matrix if is_fulltext_access(s["material_access"]))
        abstract_n = acc_counts.get("abstract_only", 0)
        discovery_n = acc_counts.get("discovery_only", 0)
        artifacts = ["result.json", out_json.name] + ([out_md.name] if out_md is not None else [])
        # Keep result.json compact: include full sub_questions_dag, conflicts, gaps, access_log, verification, and top 40 sources/evidence (full ledger in deep_research_ledger.json)
        patch = {
            "artifacts": artifacts,
            "ledger_ref": out_json.name,
            "sources": [
                {"source_id": s["source_id"], "record_id": s["record_id"], "title": s["title"], "doi": s["doi"], "year": s["year"], "date_basis": s["date_basis"], "material_access": s["material_access"]}
                for s in sources_matrix[:40]
            ],
            "corpus_scope": ledger["corpus_scope"],
            "access_tier_breakdown": acc_counts,
            "sub_questions_dag": subqs,
            "output_contract_audit": output_contract_audit,
            "evidence_matrix_preview": evidence_matrix[:30],
            "conflict_ledger": conflicts,
            "research_gaps": research_gaps,
            "access_log": bundle["access_log"],
            "tool_call_log": tool_log,
            "synthesis_verification": verification,
            "uncertainty": {
                "bounded_scope": True,
                "global_exhaustiveness_claimed": False,
                "completed_prospective_experiments_claimed": False,
                "abstract_only_count": abstract_n,
                "discovery_only_excluded_count": discovery_n,
                "blocked_sources_count": len(bundle["access_log"]),
                "unresolved_conflicts_count": len(conflicts),
                "pagination_warnings": bundle["pagination_warnings"],
            },
            "metrics": {
                "raw_record_count": len(raw_records),
                "canonical_source_count": len(sources_matrix),
                "duplicates_merged_count": len(dup_log),
                "fulltext_source_count": fulltext_n,
                "abstract_only_source_count": abstract_n,
                "discovery_only_excluded_count": discovery_n,
                "sub_question_count": len(subqs),
                "grounded_passage_count": len(evidence_matrix),
                "anchored_locator_count": anchored_locator_n,
                "out_of_scope_filtered_count": out_of_scope_n,
                "conflict_count": len(conflicts),
                "research_gap_count": len(research_gaps),
            },
            "metric_provenance": {
                "raw_record_count": f"{out_json.name}:identified_by_source",
                "canonical_source_count": f"{out_json.name}:sources_matrix",
                "duplicates_merged_count": f"{out_json.name}:deduplication_log",
                "fulltext_source_count": f"{out_json.name}:access_tier_breakdown",
                "abstract_only_source_count": f"{out_json.name}:access_tier_breakdown",
                "discovery_only_excluded_count": f"{out_json.name}:access_tier_breakdown",
                "sub_question_count": f"{out_json.name}:sub_questions_dag",
                "grounded_passage_count": f"{out_json.name}:evidence_matrix",
                "anchored_locator_count": f"{out_json.name}:evidence_matrix.locator",
                "out_of_scope_filtered_count": f"{out_json.name}:sources_matrix.scope_relevance",
                "conflict_count": f"{out_json.name}:conflict_ledger",
                "research_gap_count": f"{out_json.name}:research_gaps",
            },
            "findings": [
                f"Decomposed research question into {len(subqs)} dependency-ordered sub-questions and synthesized {len(sources_matrix)} canonical sources ({fulltext_n} fulltext, {abstract_n} abstract-only, {discovery_n} discovery-only excluded) into {len(evidence_matrix)} passage-linked evidence rows with {len(conflicts)} preserved conflict(s) and {len(research_gaps)} typed gap(s)."
            ],
            "gaps": gaps,
        }
        res = result_guard.update_result(result_path, patch, finalize=True)
        grounding = gap_gate.check_grounding(res, [input_dir])
        if not grounding["ok"]:
            result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)
        budget_guard.tick_phase(budget_file, "final_audit")
        req_files = [out_json.name] + ([out_md.name] if out_md is not None else [])
        audit = final_audit.audit_deliverables(eff_out_dir, result_file="result.json", required_files=req_files)
        if not audit["passed"]:
            raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")

    return ledger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded multi-source deep research orchestrator.")
    parser.add_argument("--input", default="", help="Input directory containing case.json, task.md, and source records.")
    parser.add_argument("--output", default="", help="Output directory (writes result.json, deep_research_ledger.json, deep_research_brief.md).")
    parser.add_argument("--work", default="", help="Working directory for checkpoints and logs.")
    parser.add_argument("--out-json", default="", help="Explicit ledger/audit JSON path.")
    parser.add_argument("--out-md", default="", help="Explicit Markdown brief path.")
    parser.add_argument("--verify-report", default="", help="Read-only strict audit of a candidate synthesis report.")
    parser.add_argument("--resume", action="store_true", help="Resume from matching checkpoint_state.json in --work.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if verification fails.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    in_dir = paths["input_dir"]
    out_dir = Path(args.output) if args.output else None
    out_json = Path(args.out_json) if args.out_json else ((out_dir or paths["output_dir"]) / "deep_research_ledger.json")
    out_md = Path(args.out_md) if args.out_md else (None if args.verify_report else ((out_dir or paths["output_dir"]) / "deep_research_brief.md"))
    verify_path = Path(args.verify_report) if args.verify_report else None

    res = run_pipeline(
        input_dir=in_dir,
        out_json=out_json,
        out_md=out_md,
        output_dir=out_dir if args.output else (None if (args.out_json or args.verify_report) else paths["output_dir"]),
        work_dir=paths["work_dir"],
        verify_report=verify_path,
        resume=args.resume,
    )
    if verify_path is not None:
        print(json.dumps({"passed": res["passed"], "errors": res["synthesis_verification"]["errors"]}, ensure_ascii=False))
        return 0 if (res["passed"] or not args.strict) else 1

    print(json.dumps({
        "passed": res["passed"],
        "sources": len(res["sources_matrix"]),
        "evidence_rows": len(res["evidence_matrix"]),
        "conflicts": len(res["conflict_ledger"]),
        "gaps": len(res["research_gaps"]),
        "resumed_steps": [t["step"] for t in res["tool_call_log"] if t.get("status") == "resumed_from_checkpoint"],
    }, ensure_ascii=False))
    if not res["sources_matrix"] and not res["access_log"]:
        return 1
    if args.strict and not res["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
