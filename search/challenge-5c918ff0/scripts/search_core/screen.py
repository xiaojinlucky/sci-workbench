# -*- coding: utf-8 -*-
"""Field-weighted BM25 retrieval, 4-tier relevance screening, missing-abstract/year protection,
external conflicting-opinion arbitration, deterministic tie-breaking, Cohen's kappa, and LOO stability.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import scholarmeta as sm
    from . import query as qm
    from . import lang as lg
except ImportError:
    import scholarmeta as sm  # type: ignore
    import query as qm  # type: ignore
    import lang as lg  # type: ignore

TIE_BREAKER_CHAIN = (
    "1. final_decision_order (include < borderline < exclude)",
    "2. relevance_tier_order (direct_relevant < indirect_relevant < insufficient_abstract_needs_review < irrelevant)",
    "3. composite_relevance_score DESC",
    "4. abstract_evidence_score DESC (prevents title-only keyword stuffing from beating abstract-backed studies)",
    "5. version_authority DESC (published > conference > preprint > unknown)",
    "6. publication_year DESC (missing year treated neutrally as 0 after evidence)",
    "7. deterministic_rid ASC",
)

DECISION_ORDER = {"include": 0, "borderline": 1, "exclude": 2}
RELEVANCE_TIER_ORDER = {
    "direct_relevant": 0,
    "indirect_relevant": 1,
    "insufficient_abstract_needs_review": 2,
    "irrelevant": 3,
}
VERSION_AUTHORITY = {"published": 3, "conference": 2, "preprint": 1, "unknown": 0, "": 0}


def _snippet(text: str, terms: List[str], width: int = 220) -> str:
    """Return best evidence window around the densest concentration of query terms."""
    if not text:
        return ""
    low = text.lower()
    stem_terms = [t for t in terms if len(t) > 2]
    if not stem_terms:
        return text[:width] + ("..." if len(text) > width else "")
    toks = [(m.start(), m.group(0).lower()) for m in re.finditer(r"[a-z0-9][a-z0-9\-']+|[\u4e00-\u9fff]+", low)]
    stems = [(pos, qm.stem(tok)) for pos, tok in toks]
    tset = set(stem_terms)
    best_count, best_span = 0, (0, min(len(text), width))
    n = len(stems)
    j = 0
    total = 0
    for i in range(n):
        while j < n and (stems[j][0] - stems[i][0]) < 160:
            if stems[j][1] in tset or any(stems[j][1].startswith(t) for t in tset if len(t) > 3):
                total += 1
            j += 1
        if total > best_count:
            best_count = total
            best_span = (stems[i][0], (stems[j - 1][0] + 40) if j > i else stems[i][0] + width)
        if stems[i][1] in tset or any(stems[i][1].startswith(t) for t in tset if len(t) > 3):
            total -= 1
    s, e = best_span
    s = max(0, s - 20)
    e = min(len(text), e + 40)
    seg = re.sub(r"\s+", " ", text[s:e]).strip()
    return ("..." if s > 0 else "") + seg + ("..." if e < len(text) else "")


class BM25Index:
    def __init__(self, docs_tokens: List[List[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs_tokens
        self.N = len(docs_tokens)
        self.avgdl = sum(len(d) for d in docs_tokens) / max(1, self.N)
        self.df: Counter = Counter()
        self.tf: List[Counter] = []
        for d in docs_tokens:
            c = Counter(d)
            self.tf.append(c)
            for w in c:
                self.df[w] += 1
        self.idf = {w: math.log(1 + (self.N - df + 0.5) / (df + 0.5)) for w, df in self.df.items()}

    def score(self, qi: int, terms: List[str]) -> float:
        dl = len(self.docs[qi]) or 1
        s = 0.0
        tf = self.tf[qi]
        for t in set(terms):
            f = tf.get(t, 0)
            if f:
                s += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s


class LocalSearcher:
    """Field-weighted BM25 over title / abstract / keywords+mesh with acronym expansion.

    Balances abstract weight (2.2) with title weight (2.4) so title-only keyword overlap
    cannot automatically override a study with rich abstract method/population evidence.
    """

    def __init__(self, items: List[Dict[str, Any]], abbrevs: Optional[List[Dict[str, Any]]] = None):
        self.items = items
        self.abbr_map: Dict[str, List[str]] = defaultdict(list)
        for a in abbrevs or []:
            if a.get("abbr") and a.get("expansion"):
                toks = qm.stem_tokens(qm.tokenize(a["expansion"]))
                self.abbr_map[a["abbr"].lower()] = toks
                for t in toks:
                    self.abbr_map[t].append(a["abbr"].lower())
        self.fields = {
            "title": [qm.stem_tokens(qm.tokenize(it.get("title", ""))) for it in items],
            "abstract": [qm.stem_tokens(qm.tokenize(it.get("abstract", ""))) for it in items],
            "kw": [
                qm.stem_tokens(
                    qm.tokenize(
                        " ".join(it.get("keywords", []) or [])
                        + " "
                        + " ".join(it.get("mesh", []) or [])
                        + " "
                        + (it.get("venue") or "")
                    )
                )
                for it in items
            ],
        }
        self.idx = {f: BM25Index(toks) for f, toks in self.fields.items()}
        self.weights = {"title": 2.4, "abstract": 2.2, "kw": 1.8}

    def expand_terms(self, terms: List[str]) -> List[str]:
        out = list(terms)
        for t in terms:
            for a in self.abbr_map.get(t, [])[:3]:
                out.append(a)
            for e in self.abbr_map.get(t.lower(), [])[:3]:
                out.append(e)
        return list(dict.fromkeys(out))

    def score_all(self, terms: List[str], rid_subset: Optional[List[str]] = None) -> Dict[str, float]:
        clean = [t for t in qm.stem_tokens(terms) if len(t) > 1]
        exp = self.expand_terms(clean)
        scores: Dict[str, float] = {}
        for i, it in enumerate(self.items):
            if rid_subset and it["rid"] not in rid_subset:
                continue
            s = sum(w * self.idx[f].score(i, exp) for f, w in self.weights.items())
            scores[it["rid"]] = s
        return scores

    def explain(self, terms: List[str], rid: str) -> Dict[str, Any]:
        terms_e = self.expand_terms([t for t in qm.stem_tokens(terms) if len(t) > 1])
        i = next((k for k, it in enumerate(self.items) if it["rid"] == rid), None)
        if i is None:
            return {}
        per = {f: round(self.idx[f].score(i, terms_e), 3) for f in self.fields}
        matched = sorted({t for f in self.fields for t in terms_e if self.idx[f].tf[i].get(t)})
        matched_by_field = {
            f: sorted({t for t in terms_e if self.idx[f].tf[i].get(t)})[:12] for f in self.fields
        }
        return {
            "field_scores": per,
            "field_weights": dict(self.weights),
            "weighted_total": round(sum(per[f] * self.weights[f] for f in per), 3),
            "matched_terms": matched[:20],
            "matched_by_field": matched_by_field,
        }


def evaluate_rules(rules: List[Dict[str, Any]], rec: Dict[str, Any]) -> Dict[str, Any]:
    """Apply parsed inclusion/exclusion criteria to a record, keeping missing metadata distinct."""
    text_parts = [
        rec.get("title", ""),
        rec.get("abstract", ""),
        " ".join(rec.get("keywords", []) or []),
        " ".join(rec.get("mesh", []) or []),
        rec.get("pub_type", ""),
        rec.get("version", ""),
    ]
    text = " ".join(p for p in text_parts if p)
    year = rec.get("year") or 0
    hits, misses, unknowns = [], [], []

    for r in rules:
        reasons = []
        ok = True
        unk = False
        if r.get("year_min") or r.get("year_max"):
            if not year:
                unk = True
                reasons.append("year_missing_cannot_verify")
            else:
                if r.get("year_min") and year < r["year_min"]:
                    ok = False
                    reasons.append(f"year {year} < {r['year_min']}")
                if r.get("year_max") and year > r["year_max"]:
                    ok = False
                    reasons.append(f"year {year} > {r['year_max']}")
        if r.get("language"):
            if not rec.get("language"):
                unk = True
                reasons.append("language_missing")
            elif not str(rec.get("language", "")).lower().startswith(r["language"][:2].lower()):
                ok = False
                reasons.append(f"language {rec.get('language')} != {r['language']}")
        if r.get("evidence_types"):
            low = text.lower()
            found = any(
                re.search(r"\b" + re.escape(w) + r"\b", low)
                for k in r["evidence_types"]
                for w in qm.EVIDENCE_LEX.get(k, [])
            )
            if not found:
                if not rec.get("abstract"):
                    unk = True
                    reasons.append(f"abstract_missing_for_evidence_type:{r['evidence_types']}")
                elif not r.get("terms"):
                    ok = False
                    reasons.append(f"no evidence-type cue for {r['evidence_types']}")
        if r.get("terms"):
            toks = set(qm.stem_tokens(qm.tokenize(text)))
            hit_terms = [t for t in r["terms"] if t in toks or any(tok.startswith(t) for tok in toks if len(t) > 3)]
            if r.get("polarity") == "exclude":
                # Require multi-term co-occurrence for exclusion rules so a single generic word (e.g. 'theory') never false-excludes valid papers
                min_excl_hits = min(len(r["terms"]), max(2, int(math.ceil(len(r["terms"]) * 0.35)))) if len(r["terms"]) >= 2 else 1
                if len(hit_terms) >= min_excl_hits:
                    reasons.append(f"exclude_terms({len(hit_terms)}/{len(r['terms'])}): " + ", ".join(hit_terms[:6]))
                    if r.get("negated"):
                        ok = False
                else:
                    ok = False
                    reasons.append(f"exclude_threshold_not_met({len(hit_terms)}/{min_excl_hits})")
            else:
                if hit_terms:
                    reasons.append("terms: " + ", ".join(hit_terms[:6]))
                    if r.get("negated"):
                        ok = False
                else:
                    if not rec.get("abstract") and r.get("polarity") == "include":
                        unk = True
                        reasons.append("abstract_missing_cannot_verify_terms")
                    elif not r.get("negated") and r.get("polarity") == "include" and r.get("field") == "text":
                        ok = False
                        reasons.append("no criterion terms found")

        entry = {"rule": r["id"], "polarity": r["polarity"], "text": r.get("text", "")[:160], "reasons": reasons}
        if unk and ok:
            unknowns.append(entry)
        elif ok:
            hits.append(entry)
        else:
            misses.append(entry)

    excl_hit = [h for h in hits if h["polarity"] == "exclude"]
    inc_hit = [h for h in hits if h["polarity"] == "include"]
    if excl_hit:
        verdict = "exclude"
    elif inc_hit:
        verdict = "include"
    elif unknowns:
        verdict = "insufficient_metadata"
    elif not any(r["polarity"] == "include" for r in rules):
        verdict = "include"
    else:
        verdict = "unclear"

    return {
        "exclusion_hits": excl_hit,
        "inclusion_hits": inc_hit,
        "inclusion_misses": [m for m in misses if m["polarity"] == "include"],
        "unknown_checks": unknowns,
        "verdict": verdict,
    }


def _match_external_opinions(it: Dict[str, Any], opinions: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    if not opinions:
        return []
    matched = []
    it_doi = sm.norm_doi(it.get("doi") or "")
    it_rid = str(it.get("rid") or "")
    it_title = sm.norm_title(it.get("title") or "")
    for op in opinions:
        op_doi = sm.norm_doi(op.get("doi") or "")
        op_id = str(op.get("record_id") or op.get("rid") or op.get("paper_id") or "")
        op_title = sm.norm_title(op.get("title") or "")
        if (it_doi and op_doi and it_doi == op_doi) or (op_id and op_id == it_rid) or (it_title and op_title and it_title == op_title):
            matched.append(op)
    return matched


def screen(
    items: List[Dict[str, Any]],
    terms_by_sq: Dict[str, List[str]],
    rules: List[Dict[str, Any]],
    searcher: LocalSearcher,
    filters: Optional[Dict[str, Any]] = None,
    external_opinions: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run transparent dual-signal screening + 4-tier relevance + missing-metadata guard + conflict arbitration."""
    filters = filters or {}
    all_terms = [t for ts in terms_by_sq.values() for t in ts]
    out: List[Dict[str, Any]] = []

    for it in items:
        rid = it["rid"]
        per_sq = {}
        total = 0.0
        for sq, terms in terms_by_sq.items():
            s = searcher.score_all(terms, [rid]).get(rid, 0.0)
            per_sq[sq] = round(s, 3)
            total += s
        expl = searcher.explain(all_terms, rid)
        f_scores = expl.get("field_scores", {})
        abs_score = float(f_scores.get("abstract", 0.0))
        title_score = float(f_scores.get("title", 0.0))
        has_abstract = bool((it.get("abstract") or "").strip())
        has_year = bool(it.get("year"))

        rule_res = (
            evaluate_rules(rules, it)
            if rules
            else {"verdict": "include" if total > 0 else "unclear", "exclusion_hits": [], "inclusion_hits": [], "inclusion_misses": [], "unknown_checks": []}
        )

        # Year/language filter: NEVER silently exclude when year or language is missing!
        year_missing = bool((filters.get("year_min") or filters.get("year_max")) and not has_year)
        year_ok = (not filters.get("year_min") or not has_year or it["year"] >= filters["year_min"]) and (
            not filters.get("year_max") or not has_year or it["year"] <= filters["year_max"]
        )
        lang_missing = bool(filters.get("languages") and not it.get("language"))
        lang_ok = not filters.get("languages") or not it.get("language") or any(
            str(it.get("language", "")).lower().startswith(l[:2].lower()) for l in filters["languages"]
        )

        # Reviewer A: Evidence-weighted lexical + explicit criteria
        if rule_res["verdict"] == "exclude" or not year_ok or not lang_ok:
            label_a = "exclude"
        elif not has_abstract and total > 0:
            label_a = "borderline"
        elif total >= 5.5 and (abs_score > 0 or not has_abstract) and rule_res["verdict"] in ("include", "insufficient_metadata"):
            label_a = "include"
        elif total >= 2.2:
            label_a = "borderline"
        else:
            label_a = "exclude" if (rules and rule_res["verdict"] == "unclear" and has_abstract) else ("borderline" if (total > 0 or not has_abstract) else "exclude")

        # Reviewer B: Independent facet coverage + evidence-type + non-title-only sanity check
        sq_covered_n = sum(1 for v in per_sq.values() if v >= 1.5)
        b_score = 0.0
        if abs_score >= 1.0:
            b_score += 1.2
        if title_score >= 0.8:
            b_score += 0.6
        if sq_covered_n >= min(2, max(1, len(per_sq))):
            b_score += 0.8
        if filters.get("evidence_types"):
            low = (it.get("abstract", "") + " " + it.get("pub_type", "")).lower()
            if any(re.search(r"\b" + re.escape(w) + r"\b", low) for k in filters["evidence_types"] for w in qm.EVIDENCE_LEX.get(k, [])[:3]):
                b_score += 0.6
        if it.get("citations_count"):
            b_score += min(0.4, math.log10(1 + int(it["citations_count"])) / 6.0)

        if not year_ok or not lang_ok or rule_res["verdict"] == "exclude":
            label_b = "exclude"
        elif not has_abstract:
            label_b = "borderline"
        elif b_score >= 1.8:
            label_b = "include"
        elif b_score >= 0.8:
            label_b = "borderline"
        else:
            label_b = "exclude"

        # External conflicting opinions (e.g., search09 synthetic reviewer fixture)
        ext_ops = _match_external_opinions(it, external_opinions)
        ext_decisions = sorted(
            {
                str(op.get("decision") or op.get("label") or op.get("verdict") or "").strip().lower()
                for op in ext_ops
                if op.get("decision") or op.get("label") or op.get("verdict")
            }
        )
        ext_conflict = len(ext_decisions) > 1

        # 4-tier relevance & arbitration
        if rule_res["verdict"] == "exclude" or not year_ok or not lang_ok:
            final_label = "exclude"
            relevance_tier = "irrelevant"
            arbitration = "hard_exclusion_rule_or_filter"
        elif ext_conflict:
            final_label = "borderline"
            relevance_tier = "indirect_relevant" if total >= 2.2 else "insufficient_abstract_needs_review"
            arbitration = f"external_reviewer_conflict({','.join(ext_decisions)})->escalate_human"
        elif not has_abstract:
            final_label = "borderline"
            relevance_tier = "insufficient_abstract_needs_review"
            arbitration = "missing_abstract_protected_from_silent_exclusion"
        elif label_a == label_b:
            final_label = label_a
            if final_label == "include":
                relevance_tier = "direct_relevant"
            elif final_label == "borderline":
                relevance_tier = "indirect_relevant"
            else:
                relevance_tier = "irrelevant"
            arbitration = "dual_reviewer_agree"
        elif {label_a, label_b} == {"include", "exclude"}:
            final_label = "borderline"
            relevance_tier = "indirect_relevant"
            arbitration = "internal_conflict->escalate_human"
        else:
            final_label = "borderline"
            relevance_tier = "indirect_relevant"
            arbitration = "borderline_review_queue"

        # Detect title-only trap (high title hit but zero abstract hit when abstract exists)
        title_only_trap = has_abstract and title_score >= 1.2 and abs_score == 0.0
        if title_only_trap and final_label == "include":
            final_label = "borderline"
            relevance_tier = "indirect_relevant"
            arbitration = "title_only_keyword_trap_downgraded_to_borderline"

        # Confidence
        if arbitration in ("dual_reviewer_agree", "hard_exclusion_rule_or_filter") and has_abstract and has_year:
            confidence = "high"
        elif not has_abstract or year_missing or "conflict" in arbitration or title_only_trap:
            confidence = "low"
        else:
            confidence = "medium"

        out.append(
            {
                "rid": rid,
                "score": round(total, 3),
                "abstract_score": round(abs_score, 3),
                "title_score": round(title_score, 3),
                "per_sub_question": per_sq,
                "explain": expl,
                "rules": rule_res,
                "label_a": label_a,
                "label_b": label_b,
                "features_b": round(b_score, 2),
                "final_label": final_label,
                "relevance_tier": relevance_tier,
                "arbitration": arbitration,
                "confidence": confidence,
                "title_only_trap": title_only_trap,
                "missing_fields": [f for f, miss in (("abstract", not has_abstract), ("year", not has_year), ("language", lang_missing)) if miss],
                "external_opinions": ext_ops,
                "year_ok": year_ok,
                "lang_ok": lang_ok,
                "title": it.get("title", ""),
                "year": it.get("year"),
                "source": it.get("source", ""),
                "version": it.get("version", ""),
                "doi": it.get("doi", ""),
                "snippet": _snippet(it.get("abstract", "") or it.get("title", ""), all_terms),
            }
        )

    # Agreement stats
    bin_a = [("include" if o["label_a"] == "include" else "exclude") for o in out]
    bin_b = [("include" if o["label_b"] == "include" else "exclude") for o in out]
    kappa = cohens_kappa(bin_a, bin_b)
    agree = sum(1 for a, b in zip(bin_a, bin_b) if a == b) / max(1, len(out))

    # Deterministic sort with explicit tie-breaker chain
    out.sort(
        key=lambda o: (
            DECISION_ORDER.get(o["final_label"], 3),
            RELEVANCE_TIER_ORDER.get(o["relevance_tier"], 4),
            -o["score"],
            -o["abstract_score"],
            -VERSION_AUTHORITY.get(o.get("version", ""), 0),
            -(o.get("year") or 0),
            o["rid"],
        )
    )

    # Detect exact score ties and annotate tie-break justification
    score_counts = Counter((o["final_label"], o["score"]) for o in out)
    for i, o in enumerate(out, 1):
        o["rank"] = i
        o["tie_detected"] = score_counts[(o["final_label"], o["score"])] > 1
        if o["tie_detected"]:
            o["tie_break_key"] = {
                "relevance_tier": o["relevance_tier"],
                "abstract_score": o["abstract_score"],
                "version": o.get("version", ""),
                "year": o.get("year") or 0,
                "rid": o["rid"],
            }
        codes = []
        if o["rules"].get("exclusion_hits"):
            codes += [f"EXC:{h['rule']}" for h in o["rules"]["exclusion_hits"]]
        if not o["year_ok"]:
            codes.append("EXC:year-window")
        if not o["lang_ok"]:
            codes.append("EXC:language")
        if o["missing_fields"]:
            codes.append("MISSING:" + "+".join(o["missing_fields"]))
        if o["title_only_trap"]:
            codes.append("GUARD:title-only-no-abstract-evidence")
        if "conflict" in o["arbitration"]:
            codes.append("CONFLICT:needs-human-arbitration")
        if o["final_label"] == "include":
            codes += [f"INC:{h['rule']}" for h in o["rules"].get("inclusion_hits", [])] or ["INC:direct-evidence"]
        elif o["final_label"] == "borderline":
            codes.append(f"TIER:{o['relevance_tier']}")
        o["reason_codes"] = codes or ["NO_RULES_APPLIED"]

    stats = {
        "n": len(out),
        "include": sum(1 for o in out if o["final_label"] == "include"),
        "borderline": sum(1 for o in out if o["final_label"] == "borderline"),
        "exclude": sum(1 for o in out if o["final_label"] == "exclude"),
        "by_relevance_tier": dict(Counter(o["relevance_tier"] for o in out)),
        "missing_abstract_protected": sum(1 for o in out if o["relevance_tier"] == "insufficient_abstract_needs_review"),
        "title_only_traps_caught": sum(1 for o in out if o["title_only_trap"]),
        "reviewer_agreement": round(agree, 3),
        "cohen_kappa": round(kappa, 3),
        "conflicts_escalated": sum(1 for o in out if "conflict" in o["arbitration"]),
        "ties_resolved_deterministically": sum(1 for o in out if o["tie_detected"]),
        "tie_breaker_chain": list(TIE_BREAKER_CHAIN),
    }
    return out, stats


def cohens_kappa(a: List[str], b: List[str]) -> float:
    n = len(a)
    if not n:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(a) | set(b))
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def rank_stability(items: List[Dict[str, Any]], terms: List[str], searcher: LocalSearcher, k: int = 10) -> Dict[str, Any]:
    """Leave-one-term-out top-k rank stability check."""
    if not items:
        return {"top_k": k, "effective_k": 0, "mean_topk_overlap_loo": 1.0, "min_overlap": 1.0, "stability": "empty"}
    eff_k = min(k, len(items))
    base = searcher.score_all(terms)
    top = [rid for rid, _ in sorted(base.items(), key=lambda x: (-x[1], x[0]))[:eff_k]]
    overlaps = []
    uniq_terms = list(dict.fromkeys(terms))
    for t in uniq_terms:
        sub = [x for x in uniq_terms if x != t]
        if not sub:
            continue
        s2 = searcher.score_all(sub)
        top2 = [rid for rid, _ in sorted(s2.items(), key=lambda x: (-x[1], x[0]))[:eff_k]]
        overlaps.append(len(set(top) & set(top2)) / max(1, eff_k))
    mean = sum(overlaps) / len(overlaps) if overlaps else 1.0
    return {
        "top_k": k,
        "effective_k": eff_k,
        "mean_topk_overlap_loo": round(mean, 3),
        "min_overlap": round(min(overlaps), 3) if overlaps else 1.0,
        "stability": "stable" if mean >= 0.9 else ("partially_stable" if mean >= 0.7 else "unstable"),
        "note": "leave-one-term-out perturbation; unstable means top-k depends heavily on a single term",
    }


def prisma_counts(records_n: int, duplicates_n: int, screened_n: int, excluded_n: int, included_n: int, borderline_n: int = 0) -> Dict[str, Any]:
    """Audit PRISMA-style count conservation (fixed identified_n NameError)."""
    return {
        "identified": records_n,
        "duplicates_removed": duplicates_n,
        "screened": screened_n,
        "included": included_n,
        "borderline_needs_review": borderline_n,
        "excluded": excluded_n,
        "flow": [
            f"identified={records_n}",
            f"duplicates_ignored_with_trace={duplicates_n}",
            f"screened={screened_n}",
            f"included={included_n}",
            f"borderline={borderline_n}",
            f"excluded={excluded_n}",
        ],
        "consistency": (screened_n == records_n - duplicates_n) and (included_n + borderline_n + excluded_n == screened_n),
    }
