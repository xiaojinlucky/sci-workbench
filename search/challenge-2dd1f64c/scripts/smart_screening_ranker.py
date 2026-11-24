# -*- coding: utf-8 -*-
"""Literature Screening & Intelligent Ranking Engine (challenge-2dd1f64c).

Addresses core pain points of automated screening & ranking:
  1. 4-tier relevance classification (direct_relevant / indirect_relevant /
     insufficient_abstract_needs_review / irrelevant) paired with 3-way screening
     decisions (include / borderline / exclude).
  2. Title-only keyword trap guard: penalizes/downgrades records that match keywords
     in title alone while the abstract describes an unrelated population/domain.
  3. Missing-metadata protection: records missing abstracts or publication years are
     NEVER silently excluded; they are routed to `borderline` with explicit flags.
  4. Traceable duplicate & version handling (ASReview #328): duplicates are recorded
     as `duplicate_ignored_with_trace` with rollback entries rather than deleted;
     distinct formal DOIs with matching titles stay separate in `ambiguous_candidates`.
  5. Deterministic 7-level tie-breaker chain, external conflicting-opinion arbitration
     (human_review_queue), Cohen's kappa, PRISMA count conservation, and LOO stability.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "search_core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from search_core import caseio, cli, contract, pipeline, query as qm, screen as sc

SKILL = "literature-screening-ranker"


def _assign_evidence_role(item: Dict[str, Any], row: Dict[str, Any]) -> str:
    txt = f"{item.get('title', '')} {item.get('abstract', '')} {item.get('pub_type', '')}".lower()
    if row["final_label"] == "exclude":
        return "excluded"
    if row["relevance_tier"] == "insufficient_abstract_needs_review":
        return "needs_abstract_or_fulltext"
    if any(w in txt for w in ("systematic review", "meta-analysis", "survey", "review article", "overview")):
        return "background_review"
    if row["relevance_tier"] == "direct_relevant":
        return "core_evidence"
    if any(w in txt for w in ("benchmark", "method", "architecture", "algorithm", "protocol")):
        return "method_reference"
    return "background_context"


def main(ns, input_dir: str, out: str, work: str) -> Dict[str, Any]:
    b = caseio.load_case(input_dir, ns.question)
    builder = contract.ResultBuilder(
        b,
        work,
        out,
        SKILL,
        {
            "version": "3.0",
            "workflow": "traceable_dedup -> 4tier_relevance_screening -> title_trap_and_missing_meta_guards -> conflict_arbitration -> deterministic_tiebreak_ranking -> prisma_and_loo_audit",
            "offline": True,
        },
    )
    S = pipeline.prepare(builder, question=b.question, extract_docs=not ns.no_docs)
    pipeline.attach_docs(S, builder)

    plan = S["plan"]
    subs = plan.get("sub_questions", [])
    filters = {k: v for k, v in plan.get("filters", {}).items() if k in ("year_min", "year_max", "languages", "evidence_types")}

    if not S.get("searcher"):
        status = pipeline.status_block(builder, S, {"not_completed": ["screening_and_ranking (0 candidate records)"]})
        facts = {
            "results": [],
            "evidence": {"tie_breaker_chain": list(sc.TIE_BREAKER_CHAIN), "rules": S["rules"]},
            "status": status,
            "summary": {"question": b.question, "text": "Empty candidate set: 0 records screened.", "n_results": 0},
            "metrics": {"n_input_records": 0, "n_unique_screened": 0},
        }
        res = builder.build(facts)
        return builder.finalize(res, "# 文献筛选与智能排序报告\n\n输入候选集为空（0 条记录）。\n")

    sq_terms = {sq["id"]: sq["terms"] for sq in subs if sq.get("terms")}
    if not sq_terms:
        sq_terms = {"Q_ALL": plan.get("key_terms", [])[:15] or qm.stem_tokens(qm.tokenize(b.question or b.task_md[:400]))[:15]}

    rules = S.get("rules", [])
    screened, stats = sc.screen(
        S["items"],
        sq_terms,
        rules,
        S["searcher"],
        filters,
        external_opinions=S["opinions"],
    )
    builder.log(
        "screen",
        "dual_signal_screening",
        n=stats["n"],
        include=stats["include"],
        borderline=stats["borderline"],
        exclude=stats["exclude"],
        kappa=stats["cohen_kappa"],
        conflicts=stats["conflicts_escalated"],
    )

    all_terms = list(dict.fromkeys(t for ts in sq_terms.values() for t in ts))
    k_eval = min(10, max(1, len(S["items"])))
    stab = sc.rank_stability(S["items"], all_terms, S["searcher"], k=k_eval)

    items_by_rid = {it["rid"]: it for it in S["items"]}
    results_all = []
    included_rows = []
    borderline_queue = []
    excluded_rows = []
    evidence_rows = []
    role_counts: Counter = Counter()

    for o in screened:
        it = items_by_rid.get(o["rid"], {})
        ev_role = _assign_evidence_role(it, o)
        role_counts[ev_role] += 1

        # Build concise human-auditable rationale string
        hits_str = ", ".join(o["explain"].get("matched_terms", [])[:8]) or "none"
        inc_rules = [h["rule"] for h in o["rules"].get("inclusion_hits", [])]
        exc_rules = [h["rule"] for h in o["rules"].get("exclusion_hits", [])]
        rationale = (
            f"decision={o['final_label']} ({o['relevance_tier']}, role={ev_role}, conf={o['confidence']}); "
            f"score={o['score']} (title={o['title_score']}, abstract={o['abstract_score']}); "
            f"matched=[{hits_str}]; inc_rules={inc_rules}; exc_rules={exc_rules}; "
            f"arbitration={o['arbitration']}"
        )

        v = pipeline.item_view(it, rank=o["rank"], label=o["final_label"], reason_codes=o["reason_codes"])
        v["score"] = o["score"]
        v["title_score"] = o["title_score"]
        v["abstract_score"] = o["abstract_score"]
        v["relevance_tier"] = o["relevance_tier"]
        v["evidence_role"] = ev_role
        v["confidence"] = o["confidence"]
        v["arbitration"] = o["arbitration"]
        v["title_only_trap"] = o["title_only_trap"]
        v["missing_fields"] = o["missing_fields"]
        v["tie_detected"] = o["tie_detected"]
        if o["tie_detected"]:
            v["tie_break_key"] = o.get("tie_break_key", {})
        v["rationale"] = rationale
        v["abstract_snippet"] = o["snippet"]
        v["version_group"] = it.get("version_group", {})
        results_all.append(v)

        if o["final_label"] == "include":
            included_rows.append(v)
        elif o["final_label"] == "borderline":
            borderline_queue.append(
                {
                    "rank": o["rank"],
                    "rid": o["rid"],
                    "title": it.get("title", ""),
                    "doi": it.get("doi", ""),
                    "relevance_tier": o["relevance_tier"],
                    "score": o["score"],
                    "arbitration": o["arbitration"],
                    "missing_fields": o["missing_fields"],
                    "title_only_trap": o["title_only_trap"],
                    "external_opinions": o["external_opinions"],
                    "reason_codes": o["reason_codes"],
                    "source_paths": it.get("paths", []),
                }
            )
        else:
            excluded_rows.append(
                {
                    "rank": o["rank"],
                    "rid": o["rid"],
                    "title": it.get("title", ""),
                    "year": it.get("year"),
                    "doi": it.get("doi", ""),
                    "score": o["score"],
                    "reason_codes": o["reason_codes"],
                    "exclusion_hits": o["rules"].get("exclusion_hits", []),
                    "arbitration": o["arbitration"],
                    "source_paths": it.get("paths", []),
                }
            )

        evidence_rows.append(
            {
                "rank": o["rank"],
                "rid": o["rid"],
                "final_label": o["final_label"],
                "relevance_tier": o["relevance_tier"],
                "evidence_role": ev_role,
                "confidence": o["confidence"],
                "score": o["score"],
                "field_scores": o["explain"].get("field_scores", {}),
                "field_weights": o["explain"].get("field_weights", {}),
                "matched_by_field": o["explain"].get("matched_by_field", {}),
                "rules": o["rules"],
                "reviewer_a": o["label_a"],
                "reviewer_b": o["label_b"],
                "external_opinions": o["external_opinions"],
                "arbitration": o["arbitration"],
                "tie_detected": o["tie_detected"],
                "records": it.get("record_refs", []),
                "snippet": o["snippet"],
            }
        )

    prisma = sc.prisma_counts(
        records_n=len(b.records),
        duplicates_n=S["dedup"]["n_merged_away"],
        screened_n=len(S["clusters"]),
        excluded_n=len(excluded_rows),
        included_n=len(included_rows),
        borderline_n=len(borderline_queue),
    )

    gaps: List[str] = []
    if stats["missing_abstract_protected"]:
        gaps.append(
            f"MISSING_ABSTRACT_PROTECTED: {stats['missing_abstract_protected']} record(s) lacked abstracts and were held in borderline (insufficient_abstract_needs_review) rather than silently excluded."
        )
    if stats["title_only_traps_caught"]:
        gaps.append(
            f"TITLE_ONLY_TRAP_GUARD: {stats['title_only_traps_caught']} record(s) matched title keywords without abstract support and were downgraded/blocked."
        )
    if stats["conflicts_escalated"]:
        gaps.append(
            f"REVIEWER_CONFLICT_ESCALATED: {stats['conflicts_escalated']} record(s) with conflicting signals/opinions routed to human_review_queue.json."
        )
    if S["ambiguous_candidates"]:
        gaps.append(
            f"AMBIGUOUS_DUPLICATES_KEPT_SEPARATE: {len(S['ambiguous_candidates'])} distinct-DOI pair(s) preserved separately for human arbitration."
        )

    status = pipeline.status_block(
        builder,
        S,
        {
            "completed": [
                "traceable_cross_source_dedup",
                "all_record_4tier_screening",
                "title_trap_and_missing_metadata_protection",
                "deterministic_tiebreak_ranking",
                "prisma_conservation_and_loo_stability_audit",
            ],
            "not_completed": (
                ["human_arbitration_of_borderline_and_conflict_queue"] if (borderline_queue or S["ambiguous_candidates"]) else []
            ),
            "gaps": gaps,
            "prisma_consistency": prisma["consistency"],
            "synthetic_fixtures_treated_as_input_only": True,
        },
    )

    metrics = {
        "n_input_records": len(b.records),
        "n_unique_screened": len(S["clusters"]),
        "duplicates_ignored_with_trace": S["dedup"]["n_merged_away"],
        "source_count_conserved": S["dedup"]["source_count_conserved"],
        "n_included": len(included_rows),
        "n_borderline_needs_review": len(borderline_queue),
        "n_excluded": len(excluded_rows),
        "borderline_ratio": round(len(borderline_queue) / max(1, len(S["clusters"])), 3),
        "by_relevance_tier": stats["by_relevance_tier"],
        "by_evidence_role": dict(role_counts),
        "missing_abstract_protected": stats["missing_abstract_protected"],
        "title_only_traps_caught": stats["title_only_traps_caught"],
        "conflicts_escalated": stats["conflicts_escalated"],
        "ties_resolved_deterministically": stats["ties_resolved_deterministically"],
        "reviewer_agreement": stats["reviewer_agreement"],
        "cohen_kappa": stats["cohen_kappa"],
        "rank_stability": stab,
        "prisma_flow": prisma,
        "source_load_error_count": len(S["source_load_errors"]),
    }

    # Artifacts (zero .tsv!)
    builder.write_csv(
        "screened_ranked.csv",
        [
            {
                "rank": r["rank"],
                "decision": r["label"],
                "relevance_tier": r["relevance_tier"],
                "evidence_role": r["evidence_role"],
                "confidence": r["confidence"],
                "score": r["score"],
                "title_score": r["title_score"],
                "abstract_score": r["abstract_score"],
                "year": r["year"],
                "version": r["version"],
                "doi": r["doi"],
                "title": r["title"],
                "n_merged_records": r["n_merged_records"],
                "arbitration": r["arbitration"],
                "reason_codes": ";".join(r["reason_codes"]),
            }
            for r in results_all
        ],
    )
    builder.write_json(
        "screening_audit.json",
        {
            "case_id": b.case_id,
            "k_evaluated": k_eval,
            "scoring_weights": S["searcher"].weights,
            "tie_breaker_chain": list(sc.TIE_BREAKER_CHAIN),
            "prisma_flow": prisma,
            "stats": stats,
            "rank_stability": stab,
            "duplicate_trace_log": S["rollback_log"],
            "ambiguous_candidates": S["ambiguous_candidates"],
            "per_record_evidence": evidence_rows,
        },
    )
    builder.write_json(
        "human_review_queue.json",
        {
            "case_id": b.case_id,
            "borderline_and_conflict_items": borderline_queue,
            "ambiguous_identity_pairs": S["ambiguous_candidates"],
            "note": "Model ranking is assistive only; human arbitration resolves conflicts, missing abstracts, and ambiguous duplicate pairs.",
        },
    )

    rep = [
        f"# 文献筛选与智能排序报告 — {b.case_id}",
        "",
        f"**研究问题**：{b.question}",
        "",
        f"**筛选漏斗（PRISMA 守恒=`{prisma['consistency']}`）**：输入 {len(b.records)} 条 → 可追溯去重忽略 {S['dedup']['n_merged_away']} 条（保留于 `duplicate_trace_log` 与回滚日志）→ 逐条筛查 {len(S['clusters'])} 篇 → 保留(include) **{len(included_rows)}** / 待定复核(borderline) **{len(borderline_queue)}** / 排除(exclude) **{len(excluded_rows)}**。",
        "",
        "## 1. 排序口径、平局处理与稳定性",
        f"- **字段加权 BM25**：`title={S['searcher'].weights['title']}`, `abstract={S['searcher'].weights['abstract']}`, `keywords/venue={S['searcher'].weights['kw']}`",
        f"- **平局处理链（7 级确定性）**：{' -> '.join(sc.TIE_BREAKER_CHAIN)}",
        f"- **Top-{k_eval} LOO 排序稳定性**：`mean_overlap={stab['mean_topk_overlap_loo']}` (`{stab['stability']}`)；双信号一致性 `agreement={stats['reviewer_agreement']}`, `Cohen's kappa={stats['cohen_kappa']}`。",
        f"- **防翻车保护**：拦截标题党无摘要证据 `{stats['title_only_traps_caught']}` 条；保护缺摘要条目不静默排除 `{stats['missing_abstract_protected']}` 条；冲突升级人工复核 `{stats['conflicts_escalated']}` 条；确定性解平局 `{stats['ties_resolved_deterministically']}` 条。",
        "",
        "## 2. 筛选排序结果（前 30 条）",
        contract.md_table(
            [
                {
                    "#": r["rank"],
                    "决策": r["label"],
                    "相关层级": r["relevance_tier"],
                    "角色": r["evidence_role"],
                    "总分(摘分)": f"{r['score']}({r['abstract_score']})",
                    "年份": r["year"],
                    "标题": r["title"][:85],
                    "理由码": " ".join(r["reason_codes"][:3]),
                }
                for r in results_all[:30]
            ],
            ["#", "决策", "相关层级", "角色", "总分(摘分)", "年份", "标题", "理由码"],
        ),
        "## 3. 待人工复核队列（冲突 / 缺摘要 / 边界）",
        contract.md_table(
            [
                {
                    "#": q["rank"],
                    "ID": q["rid"],
                    "层级": q["relevance_tier"],
                    "仲裁原因": q["arbitration"],
                    "缺失字段": ",".join(q["missing_fields"]) or "无",
                    "标题": q["title"][:80],
                }
                for q in borderline_queue[:20]
            ],
            ["#", "ID", "层级", "仲裁原因", "缺失字段", "标题"],
        ),
        "## 4. 重复项留痕（ASReview #328 风格，零静默删除）",
        contract.md_table(
            [
                {
                    "重复记录": d["duplicate_rid"],
                    "归并主记录": d["canonical_rid"],
                    "状态": d["duplicate_status"],
                    "匹配层级": d["match_tier"],
                    "相似度": d["match_score"],
                }
                for d in S["rollback_log"][:20]
            ],
            ["重复记录", "归并主记录", "状态", "匹配层级", "相似度"],
        ),
        "## 5. 局限与缺口",
        contract.bullets(gaps or ["所有候选条目均具备完整摘要与年份且无未决冲突。"]),
    ]

    facts = {
        "results": results_all,
        "evidence": {
            "scoring_formula": {"algorithm": "field-weighted BM25", "weights": S["searcher"].weights, "k_evaluated": k_eval},
            "tie_breaker_chain": list(sc.TIE_BREAKER_CHAIN),
            "rules_applied": rules,
            "per_record_evidence": evidence_rows,
            "duplicate_trace_log": S["rollback_log"],
            "ambiguous_candidates": S["ambiguous_candidates"],
            "human_review_queue": borderline_queue,
            "version_links": S["version_links"],
            "sources": pipeline.sources_used(S),
        },
        "status": status,
        "summary": {
            "question": b.question,
            "text": (
                f"Screened all {len(S['clusters'])} deduplicated works from {len(b.records)} raw records "
                f"({S['dedup']['n_merged_away']} duplicates logged with trace, PRISMA consistent={prisma['consistency']}): "
                f"{len(included_rows)} include, {len(borderline_queue)} borderline (incl. {stats['missing_abstract_protected']} missing-abstract protected, "
                f"{stats['conflicts_escalated']} conflicts escalated), {len(excluded_rows)} exclude. "
                f"Title-only traps caught: {stats['title_only_traps_caught']}; Top-{k_eval} LOO stability={stab['mean_topk_overlap_loo']}."
            ),
            "n_results": len(results_all),
        },
        "metrics": metrics,
        "findings": [
            f"PRISMA flow consistent ({prisma['consistency']}): {len(b.records)} raw -> {S['dedup']['n_merged_away']} traceable duplicates -> {len(S['clusters'])} screened ({len(included_rows)} include, {len(borderline_queue)} borderline, {len(excluded_rows)} exclude).",
            f"Safety guards active: {stats['missing_abstract_protected']} missing-abstract records protected, {stats['title_only_traps_caught']} title-only traps caught, {stats['conflicts_escalated']} conflicts escalated.",
            f"Ranking transparency: deterministic 7-level tie-breaker chain applied ({stats['ties_resolved_deterministically']} tied items annotated); Top-{k_eval} LOO stability={stab['mean_topk_overlap_loo']} ({stab['stability']}).",
        ],
    }
    res = builder.build(facts)
    return builder.finalize(res, "\n".join(rep))


if __name__ == "__main__":
    sys.exit(cli.run(SKILL, main))
