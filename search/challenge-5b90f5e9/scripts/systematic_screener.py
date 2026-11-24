"""Systematic Screening, Protocol Freeze, Conflict Taxonomy & PRISMA 2020 Conservation Engine (challenge-5b90f5e9).

Upgraded offline engine addressing:
1. Protocol version freezing with SHA-256 fingerprint + criteria-drift audit against prior decisions.
2. Identity-safe multi-source deduplication (preprint->VoR retaining alt_dois; blocks silent merge of
   distinct non-preprint DOIs into near_duplicate_review_queue).
3. Clause-based screening with exact sentence-level verbatim quotes, language checks, excluded-design
   checks, PICO/required-term matching, missing-year arbitration under active date bounds, and explicit
   FULLTEXT_NOT_RETRIEVED separation (never collapsed into exclusion).
4. Dual-reviewer agreement rate, Cohen's Kappa, conflict-type taxonomy, and explicit tagging of
   synthetic reviewer/duplicate input fixtures (never presented as final human review).
5. Full 4-stage PRISMA 2020 arithmetic conservation closure + JSON & Markdown flow deliverables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log
from corpus_loader import load_input_bundle
from evidence_state_guard import extract_sentence_anchor, fold_unicode
from prisma_flow_auditor import deduplicate_bibliography, verify_prisma_conservation

REASON_CODES: dict[str, str] = {
    "INC_MATCH": "Meets all frozen inclusion clauses and eligibility bounds.",
    "EXC_WRONG_POPULATION_OR_OBJECT": "Study object, population, or intervention falls outside frozen inclusion clauses.",
    "EXC_WRONG_DESIGN": "Excluded publication or study design (e.g., editorial, commentary, erratum, letter, protocol-only).",
    "EXC_OUT_OF_DATE_RANGE": "Known publication year falls strictly outside frozen protocol date window.",
    "EXC_WRONG_LANGUAGE": "Record language falls outside frozen protocol allowed_languages.",
    "EXC_DUPLICATE_RECORD": "Merged into canonical version-of-record during identity-safe deduplication.",
    "FULLTEXT_NOT_RETRIEVED": "Passed title/abstract screening but full text was not retrieved; separated in PRISMA flow.",
    "BORDERLINE_NEEDS_ARBITRATION": "Dual-reviewer disagreement or missing abstract requires explicit human arbitration.",
    "BORDERLINE_MISSING_REQUIRED_METADATA": "Potentially eligible record is missing a protocol-bound eligibility field (e.g., year).",
    "BORDERLINE_PARTIAL_CLAUSE_MATCH": "Record matched only a subset of required inclusion clauses; requires human check.",
}

DEFAULT_EXCLUDED_DESIGN_PATTERNS = (
    r"\b(?:editorial|commentary|letter\s+to\s+the\s+editor|erratum|corrigendum|retraction\s+note|book\s+review)\b",
)


def _norm_decision_label(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    if val in {"include", "included", "yes", "accept", "keep"}:
        return "include"
    if val in {"exclude", "excluded", "no", "reject"}:
        return "exclude"
    if val in {"borderline", "maybe", "unsure", "conflict", "arbitrate", "pending"}:
        return "borderline"
    return val or "unknown"


def compute_dual_reviewer_stats(reviewer_opinions: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Compute observed agreement, Cohen's Kappa (binary/3-class), and itemized conflict taxonomy."""
    paired = 0
    agreements = 0
    labels_a: list[str] = []
    labels_b: list[str] = []
    conflicts: list[dict[str, Any]] = []

    for rid, ops in sorted(reviewer_opinions.items()):
        if len(ops) < 2:
            continue
        paired += 1
        d1 = _norm_decision_label(ops[0].get("decision"))
        d2 = _norm_decision_label(ops[1].get("decision"))
        r1 = str(ops[0].get("reason_code") or ops[0].get("reason") or "")
        r2 = str(ops[1].get("reason_code") or ops[1].get("reason") or "")
        labels_a.append(d1)
        labels_b.append(d2)

        if d1 == d2:
            agreements += 1
            if d1 == "exclude" and r1 and r2 and r1.lower() != r2.lower():
                conflicts.append({
                    "record_id": rid,
                    "conflict_type": "reason_code_disagreement",
                    "opinions": ops,
                    "requires_arbitration": False,
                    "note": "Both reviewers excluded the record but cited different exclusion reasons.",
                })
        else:
            pair_set = {d1, d2}
            if pair_set == {"include", "exclude"}:
                ctype = "include_vs_exclude"
            elif "borderline" in pair_set:
                ctype = "decision_vs_borderline"
            else:
                ctype = "other_decision_conflict"
            conflicts.append({
                "record_id": rid,
                "conflict_type": ctype,
                "opinions": ops,
                "requires_arbitration": True,
                "synthetic_input_fixture": any(bool(o.get("synthetic") or o.get("is_fixture")) for o in ops),
            })

    obs_agree = round(agreements / paired, 4) if paired > 0 else None
    kappa: float | None = None
    if paired >= 2:
        all_cats = sorted(set(labels_a) | set(labels_b))
        c_a = Counter(labels_a)
        c_b = Counter(labels_b)
        pe = sum((c_a[c] / paired) * (c_b[c] / paired) for c in all_cats)
        if abs(1.0 - pe) > 1e-9:
            kappa = round((obs_agree - pe) / (1.0 - pe), 4) if obs_agree is not None else None
        else:
            kappa = 1.0 if obs_agree == 1.0 else 0.0

    return {
        "dual_rated_record_count": paired,
        "exact_decision_agreement_count": agreements,
        "observed_agreement_rate": obs_agree,
        "cohens_kappa": kappa,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }


def screen_record_with_clauses(
    rec: dict[str, Any],
    criteria: dict[str, Any],
    reviewer_opinions: dict[str, list[dict[str, Any]]],
    protocol_sha256: str,
) -> dict[str, Any]:
    rid = rec["record_id"]
    title = str(rec.get("title") or "").strip()
    abstract = str(rec.get("abstract") or "").strip()
    body = str(rec.get("text") or "").strip()
    blob = " ".join(part for part in (title, abstract, body) if part).strip()
    low = fold_unicode(blob).lower()

    raw_req = criteria.get("required_terms") or criteria.get("include_keywords") or []
    # Support both flat strings and synonym-group lists or PICO clause dicts:
    # e.g. required_terms=[["butyrate", "SCFA"], ["colitis", "IBD"]] or pico_clauses={"P": [...], "I": [...]}
    clause_groups: list[tuple[str, list[str]]] = []
    if isinstance(criteria.get("pico_clauses"), dict) and criteria["pico_clauses"]:
        for ck, cv in criteria["pico_clauses"].items():
            terms = [fold_unicode(str(t)).lower() for t in (cv if isinstance(cv, list) else [cv]) if str(t).strip()]
            if terms:
                clause_groups.append((str(ck), terms))
    for idx_r, item in enumerate(raw_req, 1):
        if isinstance(item, list):
            terms = [fold_unicode(str(t)).lower() for t in item if str(t).strip()]
            if terms:
                clause_groups.append((f"clause_{idx_r}", terms))
        elif str(item).strip():
            clause_groups.append((f"term_{idx_r}", [fold_unicode(str(item)).lower()]))

    req_terms = [grp[1][0] for grp in clause_groups]
    excl_terms = [fold_unicode(str(t)).lower() for t in (criteria.get("exclude_terms") or criteria.get("exclude_keywords") or []) if str(t).strip()]
    excl_designs = [fold_unicode(str(t)).lower() for t in (criteria.get("exclude_designs") or []) if str(t).strip()]
    allowed_langs = [str(lg).lower() for lg in (criteria.get("allowed_languages") or []) if str(lg).strip()]
    year_min = criteria.get("year_min")
    year_max = criteria.get("year_max")
    require_fulltext = bool(criteria.get("require_fulltext") or criteria.get("stage") == "fulltext_eligibility")

    yr = rec.get("year")
    year_bound_active = year_min is not None or year_max is not None
    year_missing = year_bound_active and yr is None
    rec_lang = str(rec.get("language") or rec.get("body_language") or "").strip().lower()

    opinions = reviewer_opinions.get(rid, [])
    disagreement = False
    conflict_type: str | None = None
    if len(opinions) >= 2:
        decs = {_norm_decision_label(o.get("decision")) for o in opinions}
        if len(decs) > 1:
            disagreement = True
            conflict_type = "include_vs_exclude" if decs == {"include", "exclude"} else "decision_conflict"

    decision = "include"
    reason_code = "INC_MATCH"
    triggered_clause = "all_inclusion_clauses_satisfied"
    evidence_quote = extract_sentence_anchor(blob, req_terms[0]) if req_terms else " ".join(blob.split())[:200]
    matched_hits: list[str] = []
    missing_clauses: list[str] = []

    # Step 1: Hard exclusions that are decisive even if abstract/year is absent
    design_hit = next((d for d in excl_designs if re.search(rf"\b{re.escape(d)}\b", low)), None)
    if not design_hit:
        for pat in DEFAULT_EXCLUDED_DESIGN_PATTERNS:
            m = re.search(pat, low, re.I)
            if m and (excl_terms or excl_designs or criteria.get("exclude_non_empirical", True)):
                if any(tok in m.group(0).lower() for tok in ("editorial", "commentary", "erratum", "corrigendum", "retraction")):
                    design_hit = m.group(0).lower()
                    break

    excl_hit = next((et for et in excl_terms if et in low), None)

    if disagreement:
        decision = "borderline"
        reason_code = "BORDERLINE_NEEDS_ARBITRATION"
        triggered_clause = f"dual_reviewer_conflict:{conflict_type}"
        evidence_quote = " ".join((title or blob).split())[:200]
    elif allowed_langs and rec_lang and rec_lang not in allowed_langs:
        decision = "exclude"
        reason_code = "EXC_WRONG_LANGUAGE"
        triggered_clause = f"allowed_languages={allowed_langs}"
        evidence_quote = f"Declared record language='{rec_lang}' outside allowed {allowed_langs}; title: {title[:140]}"
    elif year_min is not None and yr is not None and int(yr) < int(year_min):
        decision = "exclude"
        reason_code = "EXC_OUT_OF_DATE_RANGE"
        triggered_clause = f"year_min={year_min}"
        evidence_quote = f"Publication year {yr} < frozen protocol year_min {year_min}; title: {title[:140]}"
    elif year_max is not None and yr is not None and int(yr) > int(year_max):
        decision = "exclude"
        reason_code = "EXC_OUT_OF_DATE_RANGE"
        triggered_clause = f"year_max={year_max}"
        evidence_quote = f"Publication year {yr} > frozen protocol year_max {year_max}; title: {title[:140]}"
    elif excl_hit is not None:
        decision = "exclude"
        is_des = excl_hit in {"editorial", "commentary", "erratum", "corrigendum", "letter", "review", "protocol"}
        reason_code = "EXC_WRONG_DESIGN" if is_des else "EXC_WRONG_POPULATION_OR_OBJECT"
        triggered_clause = f"exclude_term:{excl_hit}"
        matched_hits = [excl_hit]
        evidence_quote = extract_sentence_anchor(blob, re.escape(excl_hit))
    elif design_hit is not None and (excl_designs or any(k in design_hit for k in ("editorial", "commentary", "erratum", "retraction"))):
        decision = "exclude"
        reason_code = "EXC_WRONG_DESIGN"
        triggered_clause = f"exclude_design:{design_hit}"
        matched_hits = [design_hit]
        evidence_quote = extract_sentence_anchor(blob, re.escape(design_hit))
    elif not abstract and not body:
        decision = "borderline"
        reason_code = "BORDERLINE_NEEDS_ARBITRATION"
        triggered_clause = "missing_abstract_and_body"
        evidence_quote = title[:200]
    elif clause_groups:
        hits = []
        missing_clauses = []
        for cname, synonyms in clause_groups:
            matched_syn = next((syn for syn in synonyms if syn in low), None)
            if matched_syn is not None:
                hits.append(matched_syn)
            else:
                missing_clauses.append(f"{cname}:{'|'.join(synonyms[:3])}")
        matched_hits = hits
        if not hits:
            decision = "exclude"
            reason_code = "EXC_WRONG_POPULATION_OR_OBJECT"
            triggered_clause = f"required_clauses_none_matched:{[c[0] for c in clause_groups]}"
            evidence_quote = " ".join((abstract or title).split())[:200]
        elif len(hits) < len(clause_groups) and len(clause_groups) >= 2:
            decision = "borderline"
            reason_code = "BORDERLINE_PARTIAL_CLAUSE_MATCH"
            triggered_clause = f"missing_required_clauses:{missing_clauses}"
            evidence_quote = extract_sentence_anchor(blob, re.escape(hits[0]))
        else:
            evidence_quote = extract_sentence_anchor(blob, re.escape(hits[0]))

    missing_critical_fields: list[str] = []
    if year_missing and decision != "exclude":
        missing_critical_fields.append("year")
        if decision == "include":
            decision = "borderline"
            reason_code = "BORDERLINE_MISSING_REQUIRED_METADATA"
            triggered_clause = f"missing_protocol_bounded_year[{year_min},{year_max}]"

    # Separate FULLTEXT_NOT_RETRIEVED from exclusion cleanly (PRISMA 2020)
    ft_status = str(rec.get("retrieval_status") or "").strip().lower()
    ft_unavailable = (
        ft_status in {"not_retrieved", "unavailable", "paywalled", "missing_fulltext"}
        or (require_fulltext and rec.get("fulltext_available") is False)
    )
    if decision == "include" and ft_unavailable:
        decision = "not_retrieved"
        reason_code = "FULLTEXT_NOT_RETRIEVED"
        triggered_clause = "fulltext_unavailable_after_tiab_pass"

    return {
        "record_id": rid,
        "title": title,
        "year": yr,
        "doi": rec.get("doi") or None,
        "alt_dois": rec.get("alt_dois", []),
        "found_in_sources": rec.get("found_in_sources", [rec.get("source")]),
        "source_file": rec.get("source_file"),
        "decision": decision,
        "reason_code": reason_code,
        "reason_description": REASON_CODES.get(reason_code, ""),
        "triggered_clause": triggered_clause,
        "matched_terms": matched_hits,
        "missing_clauses": missing_clauses,
        "evidence_quote": evidence_quote,
        "missing_critical_fields": missing_critical_fields,
        "reviewer_opinions": opinions,
        "conflict_type": conflict_type,
        "synthetic_fixture_opinions": any(bool(o.get("synthetic") or o.get("is_fixture")) for o in opinions),
        "needs_human_arbitration": decision == "borderline",
        "protocol_version": criteria.get("version", "v1_frozen"),
        "protocol_sha256": protocol_sha256[:16],
    }


def render_markdown_ledger(ledger: dict[str, Any]) -> str:
    pf = ledger["prisma_flow"]
    lines = [
        f"# Systematic Screening & PRISMA 2020 Audit Ledger (`{ledger['case_id']}`)",
        "",
        f"> Scope: {ledger['scope_note']}",
        f"> Frozen Protocol Version: `{ledger['protocol_version']}` (SHA-256: `{ledger['protocol_sha256']}`)",
        "",
        "## 1. PRISMA 2020 Accounting Flow (Conservation Verified)",
        "",
        "```text",
        f"Records identified from snapshot sources  (n = {pf['n_identified']})",
        f"  ├── Duplicate records merged/removed    (n = {pf['n_dedup']})",
        f"  └── Records screened (canonical)        (n = {pf['n_screened']})",
        f"        ├── Excluded at screening         (n = {pf['n_screen_excl']})",
        f"        └── Reports sought for retrieval  (n = {pf['n_sought']})",
        f"              ├── Reports not retrieved   (n = {pf['n_not_retrieved']})",
        f"              └── Reports assessed        (n = {pf['n_assessed']})",
        f"                    ├── Excluded (elig.)  (n = {pf['n_elig_excl']})",
        f"                    ├── Awaiting human    (n = {pf['n_awaiting_elig']})",
        f"                    └── Included studies  (n = {pf['n_included']})",
        f"PRISMA Conservation Balanced: {pf['balanced']}",
        "```",
        "",
        "## 2. Screening Breakdown by Reason Code",
        "",
        "| Reason Code | Count | Description |",
        "| --- | --- | --- |",
    ]
    for code, cnt in sorted(ledger.get("reason_code_counts", {}).items()):
        lines.append(f"| `{code}` | {cnt} | {REASON_CODES.get(code, '')} |")

    rv = ledger.get("dual_reviewer_summary", {})
    if rv.get("dual_rated_record_count", 0) > 0:
        lines.extend([
            "",
            "## 3. Dual-Reviewer Agreement & Conflict Summary",
            f"- Dual-rated records: `{rv['dual_rated_record_count']}`",
            f"- Observed agreement: `{rv['observed_agreement_rate']}` | Cohen's Kappa: `{rv['cohens_kappa']}`",
            f"- Conflicts preserved for arbitration: `{rv['conflict_count']}`",
        ])

    lines.extend(["", "## 4. Arbitration Queue (Requires Explicit Human Sign-Off)", ""])
    if not ledger["arbitration_queue"]:
        lines.append("No records pending human arbitration.")
    else:
        for row in ledger["arbitration_queue"]:
            lines.append(
                f"- **{row['record_id']}** (`{row['reason_code']}` | clause=`{row['triggered_clause']}`): "
                f"{row['title']} — Quote: \"{row['evidence_quote'][:140]}\""
            )

    if ledger.get("criteria_drift_audit"):
        lines.extend([
            "",
            "## 5. Protocol Criteria-Drift Audit",
            f"Detected `{len(ledger['criteria_drift_audit'])}` record(s) whose prior decision diverges from the active frozen protocol and require retroactive re-screening.",
        ])

    lines.extend([
        "",
        "## 6. Deduplication & Near-Duplicate Guard",
        f"- Merged duplicate/preprint records logged: `{len(ledger['deduplication_log'])}`",
        f"- Blocked/ambiguous near-duplicates kept separate for review: `{len(ledger.get('near_duplicate_review_queue', []))}`",
        "",
    ])
    return "\n".join(lines)


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="5b90f5e9-systematic-screening")

    bundle = load_input_bundle(input_dir)
    case_id = bundle["case_id"]
    case_data = bundle["case_data"]

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-5b90f5e9")
    budget_guard.tick_phase(budget_file, "dedup_and_screen_all_records")

    raw_records = bundle["records"]
    n_identified = len(raw_records)
    canonical_records, dup_log, meta_conflicts, near_dups = deduplicate_bibliography(
        raw_records, return_review_candidates=True
    )
    n_dedup = len(dup_log)

    criteria = case_data.get("criteria") or case_data.get("protocol") or {"version": "v1_frozen"}
    protocol_sha256 = hashlib.sha256(
        json.dumps(criteria, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    reviewer_opinions: dict[str, list[dict[str, Any]]] = {}
    for op in (case_data.get("reviewer_opinions") or []):
        if isinstance(op, dict) and op.get("record_id"):
            reviewer_opinions.setdefault(str(op["record_id"]), []).append(op)
    # Also map reviewer opinions from merged duplicate IDs onto canonical ID
    dup_to_canon = {d["duplicate_id"]: d["canonical_id"] for d in dup_log}
    for dup_id, canon_id in dup_to_canon.items():
        if dup_id in reviewer_opinions:
            reviewer_opinions.setdefault(canon_id, []).extend(reviewer_opinions[dup_id])

    screening_rows = [
        screen_record_with_clauses(rec, criteria, reviewer_opinions, protocol_sha256)
        for rec in canonical_records
    ]

    # Audit protocol criteria drift if prior_decisions exist in input
    prior_map = {
        str(p.get("record_id")): p
        for p in (case_data.get("prior_decisions") or [])
        if isinstance(p, dict) and p.get("record_id")
    }
    drift_audit: list[dict[str, Any]] = []
    for row in screening_rows:
        prior = prior_map.get(row["record_id"])
        if prior:
            prev_dec = _norm_decision_label(prior.get("decision"))
            if prev_dec != row["decision"] or str(prior.get("protocol_version") or "") != str(row["protocol_version"]):
                drift_audit.append({
                    "record_id": row["record_id"],
                    "prior_protocol_version": prior.get("protocol_version"),
                    "prior_decision": prev_dec,
                    "current_protocol_version": row["protocol_version"],
                    "current_decision": row["decision"],
                    "action": "retroactive_rescreen_required" if prev_dec != row["decision"] else "version_reverified",
                })

    n_included = sum(1 for r in screening_rows if r["decision"] == "include")
    n_excluded = sum(1 for r in screening_rows if r["decision"] == "exclude")
    n_borderline = sum(1 for r in screening_rows if r["decision"] == "borderline")
    n_not_retrieved = sum(1 for r in screening_rows if r["decision"] == "not_retrieved")
    n_screened = len(canonical_records)

    prisma_check = verify_prisma_conservation(
        n_identified=n_identified,
        n_dedup=n_dedup,
        n_automation_excl=0,
        n_awaiting_screen=0,
        n_screened=n_screened,
        n_screen_excl=n_excluded,
        n_sought=n_included + n_borderline + n_not_retrieved,
        n_not_retrieved=n_not_retrieved,
        n_assessed=n_included + n_borderline,
        n_elig_excl=0,
        n_awaiting_elig=n_borderline,
        n_included=n_included,
    )

    dual_rev_summary = compute_dual_reviewer_stats(reviewer_opinions)
    arbitration_queue = [r for r in screening_rows if r["needs_human_arbitration"]]
    not_retrieved_queue = [r for r in screening_rows if r["decision"] == "not_retrieved"]
    reason_counts = dict(Counter(r["reason_code"] for r in screening_rows))

    ledger = {
        "case_id": case_id,
        "scope_note": "Finite snapshot screening only; no live database recall or fabricated human adjudication claimed. Any synthetic duplicate exports or reviewer opinions in inputs remain labeled as input fixtures.",
        "protocol_version": criteria.get("version", "v1_frozen"),
        "protocol_sha256": protocol_sha256,
        "frozen_criteria": criteria,
        "prisma_flow": prisma_check,
        "reason_code_counts": reason_counts,
        "dual_reviewer_summary": dual_rev_summary,
        "deduplication_log": dup_log,
        "near_duplicate_review_queue": near_dups,
        "metadata_conflicts": meta_conflicts,
        "criteria_drift_audit": drift_audit,
        "screening_decisions": screening_rows,
        "arbitration_queue": arbitration_queue,
        "fulltext_not_retrieved_queue": not_retrieved_queue,
        "ingest_diagnostics": bundle.get("ingest_diagnostics", []),
    }

    ledger_path = output_dir / "prisma_screening_ledger.json"
    result_guard.atomic_write_json(ledger_path, ledger)
    md_path = output_dir / "prisma_screening_ledger.md"
    md_path.write_text(render_markdown_ledger(ledger), encoding="utf-8")

    append_audit_entry(
        audit_file,
        phase="screen",
        action="prisma_screening_completed",
        actor="systematic_screener.py",
        output={
            "protocol_sha256": protocol_sha256[:16],
            "identified": n_identified,
            "dedup": n_dedup,
            "screened": n_screened,
            "included": n_included,
            "excluded": n_excluded,
            "not_retrieved": n_not_retrieved,
            "arbitration": n_borderline,
            "balanced": prisma_check["balanced"],
        },
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    if not prisma_check["balanced"]:
        gaps.append({
            "code": "PRISMA_CONSERVATION_VIOLATION",
            "severity": "error",
            "source": "verify_prisma_conservation",
            "message": f"PRISMA flow counts unbalanced: {prisma_check}",
        })
    if arbitration_queue:
        gaps.append({
            "code": "ARBITRATION_QUEUE_RETAINED",
            "severity": "info",
            "source": "systematic_screener",
            "message": f"{len(arbitration_queue)} borderline/disputed/missing-metadata record(s) require explicit human arbitration.",
        })
    if not_retrieved_queue:
        gaps.append({
            "code": "FULLTEXT_NOT_RETRIEVED_SEPARATED",
            "severity": "info",
            "source": "systematic_screener",
            "message": f"{len(not_retrieved_queue)} record(s) passed title/abstract screening but lacked retrievable full text; separated under PRISMA n_not_retrieved.",
        })
    if near_dups:
        gaps.append({
            "code": "NEAR_DUPLICATE_REVIEW_REQUIRED",
            "severity": "info",
            "source": "deduplicate_bibliography",
            "message": f"{len(near_dups)} ambiguous title collision(s) kept separate pending human confirmation.",
        })

    patch = {
        "artifacts": ["result.json", "prisma_screening_ledger.json", "prisma_screening_ledger.md"],
        "protocol_version": ledger["protocol_version"],
        "protocol_sha256": protocol_sha256,
        "prisma_flow": prisma_check,
        "dual_reviewer_summary": dual_rev_summary,
        "screening_decisions": screening_rows,
        "arbitration_queue": arbitration_queue,
        "fulltext_not_retrieved_queue": not_retrieved_queue,
        "deduplication_log": dup_log,
        "near_duplicate_review_queue": near_dups,
        "criteria_drift_audit": drift_audit,
        "metrics": {
            "n_identified": n_identified,
            "n_dedup": n_dedup,
            "n_screened": n_screened,
            "n_included": n_included,
            "n_excluded": n_excluded,
            "n_not_retrieved": n_not_retrieved,
            "n_arbitration": n_borderline,
            "n_near_duplicate_candidates": len(near_dups),
            "prisma_balanced": 1 if prisma_check["balanced"] else 0,
        },
        "metric_provenance": {
            "n_identified": "prisma_screening_ledger.json:prisma_flow.n_identified",
            "n_dedup": "prisma_screening_ledger.json:deduplication_log",
            "n_screened": "prisma_screening_ledger.json:screening_decisions",
            "n_included": "prisma_screening_ledger.json:screening_decisions",
            "n_excluded": "prisma_screening_ledger.json:screening_decisions",
            "n_not_retrieved": "prisma_screening_ledger.json:fulltext_not_retrieved_queue",
            "n_arbitration": "prisma_screening_ledger.json:arbitration_queue",
            "n_near_duplicate_candidates": "prisma_screening_ledger.json:near_duplicate_review_queue",
            "prisma_balanced": "prisma_screening_ledger.json:prisma_flow.balanced",
        },
        "findings": [
            f"Screened all {n_screened} canonical records under frozen protocol {ledger['protocol_version']} ({protocol_sha256[:12]}): "
            f"identified={n_identified}, dedup={n_dedup}, included={n_included}, excluded={n_excluded}, "
            f"not_retrieved={n_not_retrieved}, arbitration={n_borderline}, PRISMA balanced={prisma_check['balanced']}."
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["prisma_screening_ledger.json", "prisma_screening_ledger.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor 5b90f5e9 Systematic Screening & PRISMA 2020 Auditor.")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--work", default="")
    args = parser.parse_args(argv)
    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"])
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
