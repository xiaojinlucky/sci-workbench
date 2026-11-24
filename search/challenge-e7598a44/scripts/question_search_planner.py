# -*- coding: utf-8 -*-
"""Research-Question Driven Search Planner (challenge-e7598a44).

Decomposes a compound research question into an explicit Element Card and a
dependency-ordered sub-question DAG (SQ0..SQ6), executes two-round controlled
retrieval (core -> gap diagnosis -> acronym/co-occurrence expansion) over finite
offline snapshots, evaluates 5-state sufficiency per sub-question, arbitrates
synthetic duplicate/conflicting-opinion input fixtures without treating them as
pre-baked answers, and emits an auditable stopping verdict.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "search_core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from search_core import caseio, cli, contract, dedup, pipeline, query as qm, screen as sc

SKILL = "research-question-search-planner"

PERSPECTIVE_CUES = {
    "method_developer": ("algorithm", "architecture", "framework", "method", "protocol", "catalyst design", "synthesis", "benchmark", "pipeline", "model"),
    "empirical_application": ("patient", "cohort", "randomized", "mice", "in vivo", "in vitro", "experiment", "measured", "electrolyzer", "clinical", "trial", "assay"),
    "independent_evaluator": ("systematic review", "meta-analysis", "survey", "review", "comparison", "benchmarking", "reproducibility", "limitation", "negative result"),
}


def _classify_perspective(item: Dict[str, Any]) -> str:
    txt = f"{item.get('title', '')} {item.get('abstract', '')} {item.get('pub_type', '')}".lower()
    scores = {k: sum(1 for w in words if w in txt) for k, words in PERSPECTIVE_CUES.items()}
    best_k, best_v = max(scores.items(), key=lambda x: x[1])
    return best_k if best_v > 0 else "unclassified"


def _build_requirement_ledger(plan: Dict[str, Any], constraints: List[str]) -> List[Dict[str, Any]]:
    ledger = []
    ec = plan.get("element_card", {})
    for idx, (field_name, val) in enumerate(ec.items(), 1):
        ledger.append(
            {
                "requirement_id": f"R{idx:02d}",
                "field": field_name,
                "value": val,
                "status": "unknown" if val == "UNKNOWN" else "confirmed",
                "provenance": "case_question" if val != "UNKNOWN" else "not_specified_in_question",
            }
        )
    for c_idx, c_str in enumerate(constraints[:10], len(ledger) + 1):
        ledger.append(
            {
                "requirement_id": f"R{c_idx:02d}",
                "field": "case_constraint",
                "value": c_str,
                "status": "confirmed",
                "provenance": "provided_material",
            }
        )
    return ledger


def _diagnose_subquestion_terms(S: Dict[str, Any], subs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    abbrev_idx: Dict[str, str] = {}
    for a in S.get("abbrevs", []):
        abbrev_idx[a["abbr"].lower()] = a["expansion"]
        for t in qm.stem_tokens(qm.tokenize(a["expansion"])):
            abbrev_idx.setdefault(t, a["abbr"])

    meta_tokens_list = [
        set(qm.stem_tokens(qm.tokenize(f"{it.get('title', '')} {it.get('abstract', '')} {' '.join(it.get('keywords', []) or [])}")))
        for it in S.get("items", [])
    ]
    ft_tokens_list = [set(qm.stem_tokens(qm.tokenize(d.text[:250000]))) for d in S.get("docs", [])]

    diag = []
    expansions_by_sq: Dict[str, List[str]] = defaultdict(list)

    for sq in subs:
        sq_id = sq["id"]
        for t in sq.get("terms", []):
            n_meta = sum(1 for ts in meta_tokens_list if t in ts)
            n_ft = sum(1 for ts in ft_tokens_list if t in ts)
            entry = {"sub_question": sq_id, "term": t, "metadata_postings": n_meta, "fulltext_postings": n_ft}
            if n_meta == 0 and n_ft == 0:
                cand = abbrev_idx.get(t) or abbrev_idx.get(t.lower())
                if cand:
                    entry["diagnosis"] = "zero_postings_acronym_expansion_found"
                    entry["suggested_expansion"] = cand
                    expansions_by_sq[sq_id].extend([w for w in qm.tokenize(cand) if len(w) > 2])
                else:
                    entry["diagnosis"] = "zero_postings_absent_from_finite_snapshot"
            elif n_meta == 0 and n_ft > 0:
                entry["diagnosis"] = "metadata_blind_fulltext_only"
            else:
                entry["diagnosis"] = "present_in_metadata"
            diag.append(entry)

    # Also expand explicit ALL-CAPS acronyms in the research question
    q_acros = re.findall(r"\b([A-Z][A-Z0-9\-]{1,10})\b", S.get("question", ""))
    for acr in q_acros:
        exp = abbrev_idx.get(acr.lower())
        if exp:
            expansions_by_sq["GLOBAL_ACRONYM"].append(f"{acr} = {exp}")
            for sq in subs:
                if acr.lower() in [t.lower() for t in sq.get("terms", [])] or sq["id"] == "SQ1":
                    expansions_by_sq[sq["id"]].extend([w for w in qm.tokenize(exp) if len(w) > 3])

    clean_exp = {k: list(dict.fromkeys(v)) for k, v in expansions_by_sq.items()}
    return diag, clean_exp


def _evaluate_subquestion_matrix(
    subs: List[Dict[str, Any]],
    r1: List[Dict[str, Any]],
    r2: List[Dict[str, Any]],
    items_by_rid: Dict[str, Dict[str, Any]],
    known_items: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    r1_map = {o["rid"]: o["per_sub_question"] for o in r1}
    matrix = []
    evidence_by_sq: Dict[str, List[Dict[str, Any]]] = {}

    for sq in subs:
        sq_id = sq["id"]
        sq_terms = sq.get("terms", [])
        if sq.get("type") == "known-item":
            resolved = []
            for k, v in known_items.items():
                for rid, it in items_by_rid.items():
                    hay = " ".join(str(it.get(f) or "") for f in ("doi", "arxiv", "pmid", "title")).lower()
                    if str(v).lower() in hay:
                        resolved.append({"query_key": k, "query_val": v, "rid": rid, "title": it.get("title", ""), "doi": it.get("doi", "")})
                        break
            state = "SUPPORTED" if len(resolved) >= len(known_items) and resolved else ("PARTIAL" if resolved else "UNKNOWN")
            matrix.append(
                {
                    "sub_question_id": sq_id,
                    "stage": sq.get("stage", 0),
                    "execution_mode": sq.get("execution_mode", "prerequisite"),
                    "depends_on": sq.get("depends_on", []),
                    "dimension": sq.get("dimension", "seed_anchor"),
                    "question": sq["text"],
                    "hits_round1": len(resolved),
                    "hits_round2": len(resolved),
                    "marginal_new_hits": 0,
                    "evidence_state": state,
                    "status": "covered" if state == "SUPPORTED" else ("sparse" if state == "PARTIAL" else "uncovered"),
                    "top_evidence_rids": [x["rid"] for x in resolved],
                    "gap_note": "" if state == "SUPPORTED" else "Known item(s) not fully resolved in local snapshot.",
                }
            )
            evidence_by_sq[sq_id] = resolved
            continue

        # Score threshold for meaningful sub-question evidence
        hit_r1 = [o for o in r1 if o["final_label"] != "exclude" and o["per_sub_question"].get(sq_id, 0.0) >= 2.0]
        hit_r2 = [o for o in r2 if o["final_label"] != "exclude" and o["per_sub_question"].get(sq_id, 0.0) >= 2.0]
        hit_r2.sort(key=lambda o: (-o["per_sub_question"].get(sq_id, 0.0), -o["score"], o["rid"]))

        r1_rids = {o["rid"] for o in hit_r1}
        new_from_r2 = [o["rid"] for o in hit_r2 if o["rid"] not in r1_rids]

        # Check term grounding for comparator/conditions/evidence-type so broad SQ1 terms don't falsely satisfy specific constraints
        strict_hits = []
        for o in hit_r2:
            it = items_by_rid.get(o["rid"], {})
            toks = set(qm.stem_tokens(qm.tokenize(f"{it.get('title', '')} {it.get('abstract', '')}")))
            matched = sorted(set(sq_terms) & toks)
            # For comparator require multi-term match when comparator has >=2 terms (avoid matching only generic 'diet')
            if sq.get("type") == "comparison":
                need_n = min(len(sq_terms), 2) if len(sq_terms) >= 2 else 1
                if len(matched) < need_n:
                    continue
            elif sq.get("type") in ("conditions", "evidence-type"):
                if not matched:
                    continue
            strict_hits.append((o, it, matched))

        n_strict = len(strict_hits)
        retracted_n = sum(1 for _, it, _ in strict_hits if it.get("is_retracted"))
        conflict_n = sum(1 for o, _, _ in strict_hits if "conflict" in o.get("arbitration", ""))

        if n_strict >= 2 and retracted_n < n_strict:
            state = "SUPPORTED"
            gap_note = f"{conflict_n} supporting item(s) have reviewer conflict requiring verification." if conflict_n else ""
        elif n_strict == 1:
            state = "PARTIAL"
            gap_note = "Only 1 matching record in finite snapshot; insufficient for corroborated synthesis."
        elif n_strict > 0 and retracted_n == n_strict:
            state = "OUTDATED"
            gap_note = "All matching records in snapshot are marked retracted."
        else:
            state = "UNKNOWN"
            gap_note = f"No non-excluded records in finite snapshot satisfy dimension '{sq.get('dimension')}' ({sq_terms[:6]})."

        matrix.append(
            {
                "sub_question_id": sq_id,
                "stage": sq.get("stage", 1),
                "execution_mode": sq.get("execution_mode", "parallel_after_SQ1"),
                "depends_on": sq.get("depends_on", []),
                "dimension": sq.get("dimension", sq.get("type", "")),
                "question": sq["text"],
                "query_terms": sq_terms[:12],
                "hits_round1": len(hit_r1),
                "hits_round2": n_strict,
                "marginal_new_hits": len(new_from_r2),
                "evidence_state": state,
                "status": "covered" if state == "SUPPORTED" else ("sparse" if state == "PARTIAL" else "uncovered"),
                "top_evidence_rids": [o["rid"] for o, _, _ in strict_hits[:8]],
                "gap_note": gap_note,
            }
        )
        evidence_by_sq[sq_id] = [
            {
                "rid": o["rid"],
                "title": it.get("title", ""),
                "year": it.get("year"),
                "doi": it.get("doi", ""),
                "sub_question_score": o["per_sub_question"].get(sq_id, 0.0),
                "matched_dimension_terms": matched,
                "perspective": _classify_perspective(it),
                "decision": o["final_label"],
                "arbitration": o["arbitration"],
                "source_paths": it.get("paths", []),
                "snippet": o.get("snippet", ""),
            }
            for o, it, matched in strict_hits[:12]
        ]
    return matrix, evidence_by_sq


def main(ns, input_dir: str, out: str, work: str) -> Dict[str, Any]:
    b = caseio.load_case(input_dir, ns.question)
    builder = contract.ResultBuilder(
        b,
        work,
        out,
        SKILL,
        {
            "version": "3.0",
            "workflow": "element_card -> dependency_DAG -> round1_core -> gap_diagnosis -> round2_controlled_expansion -> 5state_sufficiency -> stopping_verdict",
            "offline": True,
        },
    )
    S = pipeline.prepare(builder, question=b.question, extract_docs=not ns.no_docs)
    pipeline.attach_docs(S, builder)

    plan = S["plan"]
    subs = plan.get("sub_questions", [])
    req_ledger = _build_requirement_ledger(plan, b.constraints)
    filters = {k: v for k, v in plan.get("filters", {}).items() if k in ("year_min", "year_max", "languages", "evidence_types")}

    if not S.get("searcher") or not subs:
        stopping = qm.compute_stopping_verdict(
            {"sub_questions": []},
            marginal_new_hits=0,
            source_load_errors=S["source_load_errors"],
            has_conflicts=bool(S["opinions"]),
        )
        status = pipeline.status_block(builder, S, {"stopping_verdict": stopping})
        facts = {
            "results": [],
            "evidence": {"element_card": plan.get("element_card", {}), "requirement_ledger": req_ledger, "sub_question_plan": subs},
            "status": status,
            "summary": {"question": b.question, "text": "No searchable records or research question found in input snapshot.", "n_results": 0},
            "metrics": {"n_records": len(b.records), "n_unique_works": len(S["clusters"]), "n_sub_questions": len(subs)},
        }
        res = builder.build(facts)
        return builder.finalize(res, "# 科研问题驱动检索报告\n\n输入目录中未找到可检索文献或研究问题。\n")

    # Round 1: Core sub-question retrieval
    sq_terms_r1 = {sq["id"]: sq["terms"] for sq in subs if sq.get("terms")}
    rules = S.get("rules", [])
    r1, stats_r1 = sc.screen(S["items"], sq_terms_r1, rules, S["searcher"], filters, external_opinions=S["opinions"])
    builder.log("round1", "core_subquestion_retrieval", n=len(r1), include=stats_r1["include"], borderline=stats_r1["borderline"])

    # Gap diagnosis + controlled expansion (only expand acronyms/co-terms grounded in corpus)
    term_diag, exp_by_sq = _diagnose_subquestion_terms(S, subs)
    co_extra = [c["term"] for c in S.get("co_terms", [])[:5] if c.get("count", 0) >= 2]
    sq_terms_r2: Dict[str, List[str]] = {}
    expansion_log = []
    for sq_id, base_t in sq_terms_r1.items():
        added = list(dict.fromkeys([t for t in exp_by_sq.get(sq_id, []) if t not in base_t]))
        # Apply co-occurrence expansion only to core SQ1 to prevent semantic drift on strict comparator/evidence sub-questions
        if sq_id == "SQ1":
            added = list(dict.fromkeys(added + [t for t in co_extra if t not in base_t]))
        sq_terms_r2[sq_id] = list(dict.fromkeys(base_t + added[:8]))
        expansion_log.append({"sub_question_id": sq_id, "core_terms": base_t, "added_terms": added[:8]})

    has_any_expansion = any(e["added_terms"] for e in expansion_log)
    if has_any_expansion:
        r2, stats_r2 = sc.screen(S["items"], sq_terms_r2, rules, S["searcher"], filters, external_opinions=S["opinions"])
    else:
        r2, stats_r2 = r1, stats_r1
    builder.log("round2", "controlled_expansion_retrieval", expanded=has_any_expansion, include=stats_r2["include"], borderline=stats_r2["borderline"])

    items_by_rid = {it["rid"]: it for it in S["items"]}
    sq_matrix, evidence_by_sq = _evaluate_subquestion_matrix(subs, r1, r2, items_by_rid, plan.get("known_items", {}))
    marginal_total = sum(row["marginal_new_hits"] for row in sq_matrix)

    coverage_obj = {
        "sub_questions": sq_matrix,
        "covered": sum(1 for row in sq_matrix if row["evidence_state"] == "SUPPORTED"),
        "partial": sum(1 for row in sq_matrix if row["evidence_state"] == "PARTIAL"),
        "uncovered": sum(1 for row in sq_matrix if row["evidence_state"] == "UNKNOWN"),
        "total": len(sq_matrix),
    }

    has_conflicts = bool(stats_r2["conflicts_escalated"] or S["ambiguous_candidates"])
    stopping = qm.compute_stopping_verdict(
        coverage_obj,
        marginal_new_hits=marginal_total,
        source_load_errors=S["source_load_errors"],
        has_conflicts=has_conflicts,
    )
    builder.log("stopping", "evaluate_stopping_verdict", decision=stopping["decision"], covered=coverage_obj["covered"], total=coverage_obj["total"])

    # Assemble candidate results with sub-question mapping & three-perspective tag
    results = []
    excluded_rows = []
    evidence_chains = []
    perspective_counts: Counter = Counter()
    rank = 0
    all_terms = list(dict.fromkeys(t for ts in sq_terms_r2.values() for t in ts))

    for o in r2:
        it = items_by_rid.get(o["rid"], {})
        if o["final_label"] == "exclude":
            excluded_rows.append(
                {
                    "rid": o["rid"],
                    "title": it.get("title", ""),
                    "year": it.get("year"),
                    "doi": it.get("doi", ""),
                    "reason_codes": o["reason_codes"],
                    "arbitration": o["arbitration"],
                }
            )
            continue
        rank += 1
        persp = _classify_perspective(it)
        perspective_counts[persp] += 1
        supported_sqs = [sq_id for sq_id, sc_val in o["per_sub_question"].items() if sc_val >= 2.0]
        ft_hits = pipeline.fulltext_hits(S, o["rid"], all_terms, max_hits=3)
        v = pipeline.item_view(it, rank=rank, label=o["final_label"], reason_codes=o["reason_codes"])
        v["score"] = o["score"]
        v["relevance_tier"] = o["relevance_tier"]
        v["perspective"] = persp
        v["matched_sub_questions"] = supported_sqs
        v["sub_question_scores"] = o["per_sub_question"]
        v["confidence"] = o["confidence"]
        v["arbitration"] = o["arbitration"]
        results.append(v)

        evidence_chains.append(
            {
                "rid": o["rid"],
                "rank": rank,
                "matched_sub_questions": supported_sqs,
                "per_sub_question": o["per_sub_question"],
                "field_scores": o["explain"].get("field_scores", {}),
                "matched_terms": o["explain"].get("matched_terms", []),
                "perspective": persp,
                "records": it.get("record_refs", []),
                "fulltext_hits": ft_hits,
                "external_opinions": o["external_opinions"],
                "arbitration": o["arbitration"],
                "snippet": o["snippet"],
            }
        )

    # Gap & uncertainty compilation
    gaps = [f"SUBQUESTION_GAP [{r['sub_question_id']}:{r['dimension']}] {r['evidence_state']} — {r['gap_note']}" for r in sq_matrix if r["evidence_state"] != "SUPPORTED"]
    gaps += [f"UNKNOWN_ELEMENT_SLOT: {k} not specified in question (kept as UNKNOWN)" for k, v in plan.get("element_card", {}).items() if v == "UNKNOWN"]
    if S["ambiguous_candidates"]:
        gaps.append(f"AMBIGUOUS_IDENTITY_PAIRS: {len(S['ambiguous_candidates'])} distinct-DOI same-title pair(s) kept separate for human review")
    if stats_r2["conflicts_escalated"]:
        gaps.append(f"CONFLICTING_OPINIONS_ESCALATED: {stats_r2['conflicts_escalated']} item(s) held in borderline queue for human arbitration")

    status = pipeline.status_block(
        builder,
        S,
        {
            "completed": [
                "element_card_extraction",
                "dependency_ordered_subquestion_dag",
                "two_round_controlled_retrieval",
                "5state_subquestion_sufficiency_audit",
                "explicit_stopping_verdict",
            ],
            "not_completed": [f"live_remote_search_for_{r['sub_question_id']}" for r in sq_matrix if r["evidence_state"] == "UNKNOWN"],
            "gaps": gaps,
            "stopping_verdict": stopping,
            "sub_question_states": {r["sub_question_id"]: r["evidence_state"] for r in sq_matrix},
            "synthetic_fixtures_treated_as_input_only": bool(S["opinions"]),
        },
    )

    prisma = sc.prisma_counts(
        records_n=len(b.records),
        duplicates_n=S["dedup"]["n_merged_away"],
        screened_n=len(S["clusters"]),
        excluded_n=len(excluded_rows),
        included_n=stats_r2["include"],
        borderline_n=stats_r2["borderline"],
    )

    metrics = {
        "n_input_records": len(b.records),
        "n_unique_works": len(S["clusters"]),
        "duplicates_merged_with_trace": S["dedup"]["n_merged_away"],
        "source_count_conserved": S["dedup"]["source_count_conserved"],
        "n_sub_questions": coverage_obj["total"],
        "n_sub_questions_supported": coverage_obj["covered"],
        "n_sub_questions_partial": coverage_obj["partial"],
        "n_sub_questions_unknown": coverage_obj["uncovered"],
        "marginal_new_hits_round2": marginal_total,
        "n_results_retained": len(results),
        "n_excluded": len(excluded_rows),
        "conflicts_escalated": stats_r2["conflicts_escalated"],
        "source_load_error_count": len(S["source_load_errors"]),
        "cohen_kappa": stats_r2["cohen_kappa"],
        "perspective_distribution": dict(perspective_counts),
        "prisma_flow": prisma,
        "stopping_decision": stopping["decision"],
    }

    # Write dedicated artifacts (zero .tsv!)
    plan_artifact = {
        "case_id": b.case_id,
        "research_question": b.question,
        "element_card": plan.get("element_card", {}),
        "requirement_ledger": req_ledger,
        "sub_question_dag": subs,
        "query_rounds": S["queries"]["rounds"],
        "expansion_log": expansion_log,
        "term_diagnosis": term_diag,
        "sub_question_sufficiency": sq_matrix,
        "evidence_by_sub_question": evidence_by_sq,
        "stopping_verdict": stopping,
        "rollback_log": S["rollback_log"],
        "ambiguous_candidates": S["ambiguous_candidates"],
    }
    builder.write_json("question_search_plan.json", plan_artifact)
    builder.write_csv(
        "subquestion_evidence_matrix.csv",
        [
            {
                "sub_question_id": r["sub_question_id"],
                "stage": r["stage"],
                "depends_on": ",".join(r["depends_on"]),
                "dimension": r["dimension"],
                "hits_round1": r["hits_round1"],
                "hits_round2": r["hits_round2"],
                "marginal_new_hits": r["marginal_new_hits"],
                "evidence_state": r["evidence_state"],
                "gap_note": r["gap_note"],
            }
            for r in sq_matrix
        ],
    )
    builder.write_csv(
        "ranked_candidates.csv",
        [
            {
                "rank": r["rank"],
                "label": r["label"],
                "relevance_tier": r["relevance_tier"],
                "perspective": r["perspective"],
                "score": r["score"],
                "year": r["year"],
                "doi": r["doi"],
                "title": r["title"],
                "matched_sub_questions": ",".join(r["matched_sub_questions"]),
                "arbitration": r["arbitration"],
                "reason_codes": ";".join(r["reason_codes"]),
            }
            for r in results
        ],
    )

    # Human-readable Markdown report
    rep = [
        f"# 科研问题驱动检索报告 — {b.case_id}",
        "",
        f"**研究问题**：{b.question}",
        "",
        f"**快照边界与守恒**：输入记录 {len(b.records)} 条 → 溯源去重后 {len(S['clusters'])} 篇唯一文献（守恒校验：`{S['dedup']['source_count_conserved']}`），筛选保留 {len(results)} 篇（纳入 {stats_r2['include']} / 待定复核 {stats_r2['borderline']} / 排除 {len(excluded_rows)}）。有限离线快照范围，不宣称全网召回。",
        "",
        "## 1. 课题元素卡（未知项显式保留 UNKNOWN）",
        contract.md_table([{"维度": k, "提取值": v} for k, v in plan.get("element_card", {}).items()], ["维度", "提取值"]),
        "## 2. 依赖排序子问题与 5 态充分性矩阵",
        contract.md_table(
            [
                {
                    "ID": r["sub_question_id"],
                    "阶段": r["stage"],
                    "依赖": ",".join(r["depends_on"]) or "无(前置)",
                    "维度": r["dimension"],
                    "R1命中": r["hits_round1"],
                    "R2命中": r["hits_round2"],
                    "状态": r["evidence_state"],
                    "缺口说明": r["gap_note"] or "证据充分",
                }
                for r in sq_matrix
            ],
            ["ID", "阶段", "依赖", "维度", "R1命中", "R2命中", "状态", "缺口说明"],
        ),
        "## 3. 停止判据与升级决策",
        f"- **决策**：`{stopping['decision']}`",
        f"- **理由**：{stopping['reason']}",
        f"- **已覆盖子问题**：{stopping['covered_sub_questions']}；**部分覆盖**：{stopping['partial_sub_questions']}；**未覆盖(UNKNOWN)**：{stopping['uncovered_sub_questions']}",
        f"- **冲突仲裁**：内部/外部意见冲突升级人工复核 `{stats_r2['conflicts_escalated']}` 条；同名异 DOI 保护未误合并 `{len(S['ambiguous_candidates'])}` 对。",
        "",
        "## 4. 核心候选文献（前 20 条，含三视角与子问题映射）",
        contract.md_table(
            [
                {
                    "#": r["rank"],
                    "决策": r["label"],
                    "视角": r["perspective"],
                    "年份": r["year"],
                    "匹配子问题": ",".join(r["matched_sub_questions"]),
                    "标题": r["title"][:95],
                    "DOI": r["doi"],
                }
                for r in results[:20]
            ],
            ["#", "决策", "视角", "年份", "匹配子问题", "标题", "DOI"],
        ),
        "## 5. 剩余缺口与限制",
        contract.bullets(gaps[:12] or ["所有子问题在当前有限快照内均已达到 SUPPORTED 状态。"]),
    ]

    facts = {
        "results": results,
        "evidence": {
            "element_card": plan.get("element_card", {}),
            "requirement_ledger": req_ledger,
            "sub_question_dag": subs,
            "sub_question_sufficiency": sq_matrix,
            "evidence_by_sub_question": evidence_by_sq,
            "query_rounds": S["queries"]["rounds"],
            "expansion_log": expansion_log,
            "term_diagnosis": term_diag,
            "chains": evidence_chains,
            "rollback_log": S["rollback_log"],
            "sources": pipeline.sources_used(S),
        },
        "status": status,
        "summary": {
            "question": b.question,
            "text": (
                f"Decomposed question into {coverage_obj['total']} dependency-ordered sub-questions: "
                f"{coverage_obj['covered']} SUPPORTED, {coverage_obj['partial']} PARTIAL, {coverage_obj['uncovered']} UNKNOWN. "
                f"Retained {len(results)} candidates ({stats_r2['include']} include, {stats_r2['borderline']} borderline) "
                f"from {len(S['clusters'])} deduplicated works. Stopping verdict: {stopping['decision']}."
            ),
            "n_results": len(results),
            "stopping_decision": stopping["decision"],
        },
        "metrics": metrics,
        "findings": [
            f"Sub-question sufficiency: {coverage_obj['covered']}/{coverage_obj['total']} SUPPORTED, {coverage_obj['partial']} PARTIAL, {coverage_obj['uncovered']} UNKNOWN.",
            f"Stopping verdict: {stopping['decision']} ({stopping['reason']}).",
            f"Deduplication conserved counts: {len(b.records)} raw -> {len(S['clusters'])} unique ({S['dedup']['n_merged_away']} traceable duplicates, {len(S['ambiguous_candidates'])} ambiguous distinct-DOI pairs preserved).",
        ],
    }
    res = builder.build(facts)
    return builder.finalize(res, "\n".join(rep))


if __name__ == "__main__":
    sys.exit(cli.run(SKILL, main))
