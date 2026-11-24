# -*- coding: utf-8 -*-
"""Question understanding, element-card decomposition, dependency-ordered query planning,
corpus-mined acronym/co-occurrence expansion, and explicit stopping verdicts.

Integrates practical research workflows:
  - Element-card decomposition (object/background, mechanism, method, comparison/perturbation,
    population/data structure, time window, evidence requirement) with unknowns kept as UNKNOWN.
  - Dependency-ordered sub-questions (SQ0 known-items -> SQ1 core -> SQ2..SQ6 parallel/dependent).
  - Evidence-model ledgers (requirement_ledger, query_matrix, coverage_ledger, stop_reason).
  - Dynamic reference year (derived from snapshot metadata or corpus max year, never hardcoded).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import scholarmeta as sm
    from . import lang as lg
except ImportError:
    import scholarmeta as sm  # type: ignore
    import lang as lg  # type: ignore

STOP_EN = set(
    """a an the of and or to in on for with without within by from as at is are was were be been being
    this that these those we our their its it can could may might will would should must not no than then
    so such into onto over under between among through during after before about across via per each both
    all any other more most less least also however thus hence therefore there here which who whom whose
    what when where why how using used use based study studies results result methods method approach
    approaches paper papers work works article articles research analysis analyses effect effects role
    roles impact impacts review reviews new novel different same high low higher lower increase decrease
    improve reduces significantly significant compared comparison""".split()
)
STOP_ZH = set(
    "的 了 在 是 和 与 为 对 中 及 等 我们 通过 可以 一个 以及 并 也 但 由 于 其 该 这 那 此 将 被 把 从 向 到 关于 基于 研究 方法 结果 分析 问题 进行 本文 文章 发现 显示 具有 能够 使用 采用 不同 相同 影响 作用".split()
)

TIME_RE = re.compile(
    r"(20\d{2})\s*(?:[-–~至到]|to|and|直到|到)\s*(20\d{2})"
    r"|(?:since|after|from|自|从)\s*(20\d{2})"
    r"|(?:before|until|by|至|到)\s*(20\d{2})"
    r"|(?:last|past|recent|in the last|近|最近)\s*(\d+)?\s*(years?|年|个月|months?)"
    r"|(20\d{2})\s*(?:年|至今)?"
)
DOI_Q = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
ARXIV_Q = re.compile(r"arxiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?", re.I)
PMID_Q = re.compile(r"\bPMID[:\s]*(\d{5,9})\b", re.I)
PATENT_Q = re.compile(r"\b(US|EP|WO|CN|JP|KR)\s?(\d{2,4})?[-/\s]?(\d{6,})\s?([A-Z]\d?)\b")

EVIDENCE_LEX = {
    "rct": ["randomized", "randomised", "randomized controlled", "rct", "随机对照", "临床试验", "clinical trial", "placebo"],
    "cohort": ["cohort", "longitudinal", "prospective", "队列", "前瞻"],
    "case_control": ["case-control", "case control", "病例对照"],
    "meta": ["meta-analysis", "systematic review", "meta analysis", "荟萃分析", "系统综述", "systematic literature review"],
    "review": ["review", "survey", "综述", "overview"],
    "preprint": ["preprint", "arxiv", "biorxiv", "medrxiv", "预印本"],
    "invitro": ["in vitro", "cell line", "cells", "体外"],
    "invivo": ["in vivo", "mice", "mouse", "rat", "animal", "体内", "小鼠"],
    "human": ["human", "patients", "participants", "clinical", "人体", "患者"],
    "computational": ["simulation", "dft", "md simulation", "molecular dynamics", "in silico", "model", "algorithm", "neural network", "计算"],
    "benchmark": ["benchmark", "dataset", "evaluation", "基准", "数据集"],
}

COMP_RE = re.compile(
    r"\b(versus|vs\.?|compared (?:with|to)|in comparison with|relative to|better than|worse than|outperform\w*)\b|与.{0,12}相比|相比|对比",
    re.I,
)
POP_RE = re.compile(
    r"\b(in|among|for|across|of)\s+(patients|people|participants|subjects|adults|children|mice|rats|cells|samples|individuals|women|men|catalysts|electrolytes|graphs|molecules)\b([^,.;]{0,80})",
    re.I,
)
KNOWN_ITEM_RE = re.compile(r"\b(paper|article|study|work|文献|论文)\s+(by|from|of)\s+([A-Z][\w\-']+)", re.I)
ALT_RE = re.compile(
    r"\b((?:[a-z][a-z\-]*\s+){0,3}[a-z][a-z\-]*)\s*(?:\bor\b|vs\.?|/)\s*((?:[a-z][a-z\-]*\s+){0,3}[a-z][a-z\-]*)"
)
COND_RE = re.compile(
    r"\b(acidic|alkaline|neutral|acid|base|under\s+[a-z\s]{3,40}?(?:conditions|media|environment)|in\s+(?:acidic|alkaline|neutral|aqueous)\s+(?:media|conditions|electrolyte)|PEM\s+water\s+electrolysis|proton exchange membrane|operating conditions|overpotential|low-temperature|high-pressure)\b",
    re.I,
)
EVAL_RE = re.compile(
    r"\b(?:what|which)\s+(?:degradation|stability|durability|mechanisms?|performance|metrics?|pathways?|evidence|trade-?offs?)\b[^?]{0,80}|\b(?:reported|measured|benchmark(?:ed)?|evaluated|assessed)\b[^,.;?]{0,60}",
    re.I,
)
MECH_RE = re.compile(
    r"\b(mechanism|pathway|signaling|signalling|dissolution|degradation|over-?smoothing|bottleneck|expression|modulation|inhibition|activation|catalyst\s+stability|microbiota|fermentation|receptor)\b[^,.;?]{0,50}",
    re.I,
)


def tokenize(text: str, lang_hint: str = "") -> List[str]:
    text = (text or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    if re.search(r"[\u4e00-\u9fff]", text):
        zh = re.findall(r"[\u4e00-\u9fff]+", text)
        big = []
        for seg in zh:
            if len(seg) == 1:
                big.append(seg)
            for i in range(len(seg) - 1):
                big.append(seg[i : i + 2])
        lat = re.findall(r"[a-z][a-z0-9\-]{1,}", text)
        toks = big + lat
        return [t for t in toks if t not in STOP_ZH and t not in STOP_EN]
    toks = re.findall(r"[a-z0-9][a-z0-9\-']+", text)
    return [t for t in toks if t not in STOP_EN]


def stem(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    for suf in ("ingly", "edly", "ing", "ed", "es", "s", "ly"):
        if len(t) - len(suf) >= 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def stem_tokens(tokens: List[str]) -> List[str]:
    return [stem(t) for t in tokens]


def _norm_phrase(p: str) -> str:
    return re.sub(r"\s+", " ", (p or "").strip().lower()).strip(" .,;:()[]\"'")


def decompose(question: str, reference_year: Optional[int] = None) -> Dict[str, Any]:
    """Decompose a complex research question into element card, slots, filters, and dependency DAG."""
    q = question or ""
    ql = q.lower()
    ref_year = reference_year or 2026
    out: Dict[str, Any] = {
        "question": q,
        "lang": "",
        "reference_year": ref_year,
        "slots": {},
        "element_card": {
            "research_object": "UNKNOWN",
            "mechanism_or_outcome": "UNKNOWN",
            "method_or_intervention": "UNKNOWN",
            "comparator": "UNKNOWN",
            "population_or_context": "UNKNOWN",
            "time_scope": "UNKNOWN",
            "evidence_requirement": "UNKNOWN",
        },
        "sub_questions": [],
        "known_items": {},
        "filters": {},
    }
    try:
        out["lang"], _, _ = lg.detect_language(q)
    except Exception:
        out["lang"] = "unknown"

    # Known items
    d = DOI_Q.search(q)
    a = ARXIV_Q.search(q)
    pm = PMID_Q.search(q)
    pt = PATENT_Q.search(q)
    if d:
        out["known_items"]["doi"] = sm.norm_doi(d.group(0))
    if a:
        out["known_items"]["arxiv"] = a.group(1) + (a.group(2) or "")
    if pm:
        out["known_items"]["pmid"] = pm.group(1)
    if pt:
        out["known_items"]["patent"] = pt.group(0)
    ki = KNOWN_ITEM_RE.search(q)
    if ki:
        out["known_items"]["author"] = ki.group(3)

    # Time scope
    tm = TIME_RE.search(q)
    if tm:
        g = tm.groups()
        if g[0] and g[1]:
            out["filters"]["year_min"], out["filters"]["year_max"] = int(g[0]), int(g[1])
            out["element_card"]["time_scope"] = f"{g[0]}-{g[1]}"
        elif g[2]:
            out["filters"]["year_min"] = int(g[2])
            out["element_card"]["time_scope"] = f">={g[2]}"
        elif g[3]:
            out["filters"]["year_max"] = int(g[3])
            out["element_card"]["time_scope"] = f"<={g[3]}"
        elif g[4] or g[5]:
            n = int(g[4]) if g[4] else 5
            out["filters"]["recent_years"] = n
            out["filters"]["year_min"] = ref_year - n
            out["element_card"]["time_scope"] = f"last {n} years (>={ref_year - n} relative to {ref_year})"
        elif g[6]:
            out["filters"]["year_min"] = int(g[6])
            out["element_card"]["time_scope"] = f">={g[6]}"
    if re.search(r"latest|newest|most recent|recent|最新|最新进展", ql):
        out["filters"]["recency"] = "latest"
        out["filters"].setdefault("year_min", ref_year - 2)
        if out["element_card"]["time_scope"] == "UNKNOWN":
            out["element_card"]["time_scope"] = f"recent (>={ref_year - 2} relative to {ref_year})"

    # Evidence types
    ev = []
    for k, words in EVIDENCE_LEX.items():
        if any(re.search(r"\b" + re.escape(w) + r"\b", ql) for w in words):
            ev.append(k)
    out["filters"]["evidence_types"] = ev
    if ev:
        out["element_card"]["evidence_requirement"] = ", ".join(ev)

    # Language hints
    if re.search(r"portugu[eê]s|portuguese|\bpt\b", ql):
        out["filters"]["languages"] = ["pt"]
    if re.search(r"\benglish\b|\ben\b", ql):
        out["filters"].setdefault("languages", []).append("en")
    if re.search(r"跨语言|cross-lingual|cross lingual|bidirectional|双向", ql):
        out["filters"]["cross_lingual"] = True

    # Comparison: capture right-hand comparator arm specifically so broad anchor words do not mask missing comparator evidence
    cm = COMP_RE.search(q)
    comp_rhs_terms: List[str] = []
    if cm:
        comp_span = q[max(0, cm.start() - 40) : cm.end() + 60].strip()
        rhs_raw = re.split(r"\b(?:in|during|since|from|before|between|among|across|for|under)\b|[?？,.;]", q[cm.end() : cm.end() + 80], maxsplit=1, flags=re.I)[0].strip()
        comp_rhs_terms = [t for t in stem_tokens(tokenize(rhs_raw)) if len(t) > 2]
        out["slots"]["comparison"] = rhs_raw or comp_span
        out["element_card"]["comparator"] = rhs_raw or comp_span

    sent = re.split(r"[?？。.]", q)[0]
    m = re.search(r"^(?:what|which|how|do|does|is|are|can|whether|试|请|哪些|什么|如何|是否)[^a-z]{0,3}\s*(.+)$", sent.strip(), re.I)
    core = m.group(1) if m else sent

    # Method clause
    meth = None
    for mm in re.finditer(r"\b(using|via|based on|through|employing|applying|采用|使用|利用|基于|通过)\s+([^,.;]{3,80})", core, re.I):
        meth = _norm_phrase(mm.group(2))
        out["slots"]["method"] = meth
        out["element_card"]["method_or_intervention"] = meth
        break

    # Outcome / mechanism clause
    mo = re.search(r"(?:effect|effects|impact|influence|role|作用|影响) of ([^,.;]{3,80}?) (?:on|in|of) ([^,.;?]{3,80})", core, re.I)
    if mo:
        out["slots"]["object"] = _norm_phrase(mo.group(1))
        out["slots"]["outcome"] = _norm_phrase(mo.group(2))
    else:
        mo2 = re.search(r"\b(improve[sd]?|increase[sd]?|reduce[sd]?|decrease[sd]?|enhance[sd]?|affect[sd]?|predict[sd]?|regulate[sd]?)(?: the)?\s+([^,.;?]{3,80})", core, re.I)
        if mo2:
            out["slots"]["outcome"] = _norm_phrase(mo2.group(2))
            before = core[: mo2.start()].strip(" ,;:-")
            if before and len(before) < 120:
                out["slots"]["object"] = _norm_phrase(before)

    mech_m = MECH_RE.search(q)
    if mech_m and not out["slots"].get("outcome"):
        out["slots"]["outcome"] = _norm_phrase(mech_m.group(0))
    if out["slots"].get("outcome"):
        out["element_card"]["mechanism_or_outcome"] = out["slots"]["outcome"]

    if "object" not in out["slots"]:
        cut = core
        if meth:
            idx = core.lower().find(meth[:25].lower())
            if idx > 0:
                cut = core[:idx]
        cut = re.sub(r"^(what|which|how|do|does|is|are|can|whether)\s+", "", cut.strip(), flags=re.I)
        cut = re.sub(r"^(是|请|哪些|什么|如何|是否)\s*", "", cut)
        cut = cut.split(",")[0]
        out["slots"]["object"] = _norm_phrase(cut)[:120]
    if out["slots"].get("object"):
        out["element_card"]["research_object"] = out["slots"]["object"]

    pop = POP_RE.search(core)
    if pop:
        pop_val = _norm_phrase(pop.group(2) + " " + pop.group(3))[:100]
        out["slots"]["population"] = pop_val
        out["element_card"]["population_or_context"] = pop_val

    toks = stem_tokens(tokenize(q, out["lang"]))
    out["key_terms"] = sorted(set(toks))[:60]

    # Dependency-ordered sub-questions with explicit stage & parallelizability
    obj = out["slots"].get("object", "")
    mth = out["slots"].get("method", "")
    ocm = out["slots"].get("outcome", "")
    subs: List[Dict[str, Any]] = []

    if out["known_items"]:
        subs.append(
            {
                "id": "SQ0",
                "stage": 0,
                "execution_mode": "prerequisite",
                "type": "known-item",
                "dimension": "seed_anchor",
                "text": "Resolve known item(s): " + json.dumps(out["known_items"], ensure_ascii=False),
                "terms": [],
                "depends_on": [],
            }
        )

    sq1_terms = list(dict.fromkeys(stem_tokens(tokenize(obj + " " + mth))))
    if not sq1_terms:
        sq1_terms = out["key_terms"][:10]
    subs.append(
        {
            "id": "SQ1",
            "stage": 1,
            "execution_mode": "prerequisite",
            "type": "core",
            "dimension": "object_and_scope",
            "text": f"Which works directly investigate {obj or q[:80]}{' using ' + mth if mth else ''}?",
            "terms": sq1_terms,
            "depends_on": ["SQ0"] if out["known_items"] else [],
        }
    )
    if ocm:
        subs.append(
            {
                "id": "SQ2",
                "stage": 2,
                "execution_mode": "parallel_after_SQ1",
                "type": "mechanism",
                "dimension": "mechanism_or_outcome",
                "text": f"What mechanism, pathway, or outcome is evidenced for '{ocm}' in '{obj or 'target domain'}'?",
                "terms": list(dict.fromkeys(stem_tokens(tokenize(ocm + " " + obj)))),
                "depends_on": ["SQ1"],
            }
        )
    if mth:
        subs.append(
            {
                "id": "SQ3",
                "stage": 2,
                "execution_mode": "parallel_after_SQ1",
                "type": "method-origin",
                "dimension": "method_and_design",
                "text": f"Which studies introduce, adapt, or validate '{mth}'?",
                "terms": list(dict.fromkeys(stem_tokens(tokenize(mth)))),
                "depends_on": ["SQ1"],
            }
        )
    if cm:
        subs.append(
            {
                "id": "SQ4",
                "stage": 2,
                "execution_mode": "parallel_after_SQ1",
                "type": "comparison",
                "dimension": "comparator",
                "text": f"What comparative evidence addresses comparator '{out['slots']['comparison']}'?",
                "terms": list(dict.fromkeys(comp_rhs_terms or stem_tokens(tokenize(out["slots"]["comparison"])))),
                "depends_on": ["SQ1"],
            }
        )
    else:
        alt = ALT_RE.search(core)
        if alt:
            subs.append(
                {
                    "id": "SQ4",
                    "stage": 2,
                    "execution_mode": "parallel_after_SQ1",
                    "type": "comparison",
                    "dimension": "comparator",
                    "text": "Compare alternatives: " + alt.group(0),
                    "terms": list(dict.fromkeys(stem_tokens(tokenize(alt.group(1) + " " + alt.group(2))))),
                    "alternatives": [_norm_phrase(alt.group(1)), _norm_phrase(alt.group(2))],
                    "depends_on": ["SQ1"],
                }
            )
    cond = COND_RE.findall(q)
    cond_terms = sorted({t for c in cond for t in stem_tokens(tokenize(c)) if len(t) > 3})
    if cond_terms:
        subs.append(
            {
                "id": "SQ5",
                "stage": 2,
                "execution_mode": "parallel_after_SQ1",
                "type": "conditions",
                "dimension": "operating_conditions",
                "text": "Which studies test behavior under stated conditions (" + ", ".join(sorted(set(c.strip() for c in cond))) + ")?",
                "terms": cond_terms,
                "depends_on": ["SQ1"],
            }
        )
    evm = EVAL_RE.search(q)
    if evm or ev:
        ev_phrase = evm.group(0) if evm else ", ".join(ev)
        ev_terms = list(dict.fromkeys(stem_tokens(tokenize(ev_phrase)) + [w for k in ev for w in EVIDENCE_LEX.get(k, [])[:2]]))
        subs.append(
            {
                "id": "SQ6",
                "stage": 3,
                "execution_mode": "verification_after_SQ1",
                "type": "evidence-type",
                "dimension": "evidence_requirement",
                "text": f"Which studies satisfy the evidence/evaluation requirement ({ev_phrase})?",
                "terms": [t for t in ev_terms if len(t) > 2][:12],
                "depends_on": ["SQ1"],
            }
        )
    out["sub_questions"] = subs
    return out


ACRO_DEF_RE = re.compile(r"\b([A-Z][A-Za-z0-9\-]{1,12})\s*\(([^()]{2,90})\)")
DEF_ACRO_RE = re.compile(r"\b((?:[a-z]{2,}[ -]){1,5}[a-z]{2,})\s*\(([A-Z][A-Za-z0-9\-]{1,12})\)")
ACRO_ONLY = re.compile(r"^[A-Z][A-Z0-9\-]{1,11}$")
COMMON_NOISE_ACROS = {
    "DOI", "PDF", "ISSN", "ISBN", "URL", "USA", "IEEE", "ACM", "COVID", "DNA", "RNA",
    "CPU", "GPU", "API", "GDP", "WHO", "FDA", "NIH", "EU", "UK", "UN", "OK", "NB",
    "ET", "AL", "VS", "FIG", "XML", "CSV", "JSON", "HTTP", "HTTPS", "UTC",
}


def mine_abbreviations(texts: List[str], min_count: int = 1, max_items: int = 400) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Mine 'ABBR (expansion)' and 'expansion (ABBR)' pairs from corpus text; flag unexpanded caps."""
    pairs: Dict[Tuple[str, str], int] = Counter()
    caps: Counter = Counter()
    for t in texts:
        for m in ACRO_DEF_RE.finditer(t or ""):
            a, e = m.group(1), m.group(2)
            if (
                ACRO_ONLY.match(a)
                and len(e.split()) >= 1
                and not re.search(r"\d{4}", e)
                and e.lower() not in ("fig", "figure", "table", "eq", "equation", "ref", "section", "see", "e.g", "i.e", "versus")
            ):
                pairs[(a, e.strip())] += 1
        for m in DEF_ACRO_RE.finditer(t or ""):
            e, a = m.group(1), m.group(2)
            if ACRO_ONLY.match(a) and len(e.split()) >= 2:
                pairs[(a, e.strip())] += 1
        for w in re.findall(r"\b([A-Z][A-Z0-9]{1,9})\b", t or ""):
            caps[w] += 1
    best: Dict[str, Tuple[str, int]] = {}
    for (a, e), c in pairs.items():
        if a not in best or c > best[a][1] or (c == best[a][1] and len(e) > len(best[a][0])):
            best[a] = (e, c)
    out = [{"abbr": a, "expansion": e, "count": c} for a, (e, c) in best.items() if c >= min_count]
    out.sort(key=lambda x: -x["count"])
    unexp = [
        {"abbr": a, "expansion": "", "count": c}
        for a, c in caps.most_common(60)
        if a not in best and c >= 2 and a not in COMMON_NOISE_ACROS
    ]
    return out[:max_items], unexp[:40]


def cooccurrence_expansion(key_terms: List[str], texts: List[str], top_k: int = 12) -> List[Dict[str, Any]]:
    """Terms co-occurring with query terms in titles/abstracts (controlled pseudo-relevance feedback)."""
    kt = set(key_terms)
    co: Counter = Counter()
    docs_hit = 0
    for t in texts:
        toks = set(stem_tokens(tokenize(t)))
        if toks & kt:
            docs_hit += 1
            for w in toks - kt:
                if len(w) > 3:
                    co[w] += 1
    if docs_hit == 0:
        return []
    return [{"term": w, "count": c, "pmi_proxy": round(c / docs_hit, 3)} for w, c in co.most_common(top_k)]


def build_queries(
    plan: Dict[str, Any],
    abbrevs: List[Dict[str, Any]],
    co_terms: List[Dict[str, Any]],
    sources: List[str],
) -> Dict[str, Any]:
    """Build per-source syntax-adapted queries for round 1 (core) and round 2 (controlled expansion)."""
    kt = plan.get("key_terms", [])
    obj = plan.get("slots", {}).get("object", "")
    mth = plan.get("slots", {}).get("method", "")
    base_terms = [t for t in stem_tokens(tokenize(obj)) if len(t) > 2][:8] or kt[:8]
    method_terms = [t for t in stem_tokens(tokenize(mth)) if len(t) > 2][:5]

    exp_terms: List[str] = []
    for a in abbrevs:
        e_toks = set(stem_tokens(tokenize(a["expansion"])))
        if e_toks & set(base_terms) or any(t in a["abbr"].lower() for t in base_terms if len(t) > 2):
            exp_terms.append(a["abbr"])
            exp_terms.extend(t for t in tokenize(a["expansion"]) if len(t) > 3)
    exp_terms += [c["term"] for c in co_terms[:6] if c.get("count", 0) >= 2]
    exp_terms = list(dict.fromkeys(exp_terms))

    y0 = plan.get("filters", {}).get("year_min")
    y1 = plan.get("filters", {}).get("year_max")
    ref_year = plan.get("reference_year") or 2026

    def q_crossref(terms: List[str]) -> str:
        base = " ".join(terms[:10])
        flt = f" [filter:from-pub-date:{y0}]" if y0 else ""
        return (base + flt).strip()

    def q_epmc(terms: List[str]) -> str:
        core = " OR ".join(f'"{t}"' for t in terms[:6])
        return f"({core})" + (f" AND (PUB_YEAR:[{y0 or 1900} TO {y1 or ref_year}])" if (y0 or y1) else "")

    def q_pubmed(terms: List[str]) -> str:
        core = " OR ".join(f"{t}[tiab]" for t in terms[:6])
        return f"({core})" + (f" AND {y0 or 1900}:{y1 or ref_year}[dp]" if (y0 or y1) else "")

    def q_arxiv(terms: List[str]) -> str:
        return " AND ".join(f"all:{t}" for t in terms[:5])

    def q_openalex(terms: List[str]) -> str:
        base = " ".join(terms[:10])
        flt = f" | filter=publication_year:{y0 or ''}-{y1 or ''}" if (y0 or y1) else ""
        return (base + flt).strip()

    builders = {
        "crossref": q_crossref,
        "europepmc": q_epmc,
        "pubmed": q_pubmed,
        "arxiv": q_arxiv,
        "openalex": q_openalex,
        "publisher": q_crossref,
    }
    rounds = []
    r1_terms = list(dict.fromkeys(base_terms + method_terms))
    r2_terms = list(dict.fromkeys(r1_terms + exp_terms))
    for rnd, terms, label in ((1, r1_terms, "core"), (2, r2_terms, "controlled_expansion")):
        rounds.append(
            {
                "round": rnd,
                "label": label,
                "terms": terms,
                "queries": {s: builders.get(s, q_crossref)(terms) for s in sources},
            }
        )
    return {
        "rounds": rounds,
        "expansion_evidence": {
            "abbreviations_used": exp_terms[:10],
            "cooccurring_terms": co_terms[:6],
        },
    }


def score_query_coverage(plan: Dict[str, Any], clusters) -> Dict[str, Any]:
    """Evaluate 5-state evidence sufficiency per sub-question over finite snapshot clusters."""
    cov = []
    for sq in plan.get("sub_questions", []):
        terms = set(sq.get("terms", []))
        hits = []
        retracted_hits = []
        for c in clusters:
            toks = set(stem_tokens(tokenize(c.canonical.text_for_search)))
            if terms and len(toks & terms) >= min(2, len(terms)):
                hits.append(c.canonical.rid)
                if any(m.is_retracted for m in c.members):
                    retracted_hits.append(c.canonical.rid)
        if not terms and sq.get("type") == "known-item":
            state = "UNKNOWN"
        elif len(hits) >= 2 and len(retracted_hits) < len(hits):
            state = "SUPPORTED"
        elif len(hits) == 1:
            state = "PARTIAL"
        elif len(hits) > 0 and len(retracted_hits) == len(hits):
            state = "OUTDATED"
        else:
            state = "UNKNOWN"
        cov.append(
            {
                "id": sq["id"],
                "dimension": sq.get("dimension", sq.get("type", "")),
                "type": sq["type"],
                "text": sq["text"],
                "n_terms": len(terms),
                "n_hits": len(hits),
                "evidence_state": state,
                "status": "covered" if state == "SUPPORTED" else ("sparse" if state == "PARTIAL" else "uncovered"),
                "hit_rids": hits[:50],
            }
        )
    return {
        "sub_questions": cov,
        "covered": sum(1 for x in cov if x["status"] == "covered"),
        "partial": sum(1 for x in cov if x["status"] == "sparse"),
        "uncovered": sum(1 for x in cov if x["status"] == "uncovered"),
        "total": len(cov),
    }


def compute_stopping_verdict(
    coverage: Dict[str, Any],
    marginal_new_hits: int,
    source_load_errors: List[Dict[str, Any]],
    has_conflicts: bool = False,
) -> Dict[str, Any]:
    """Explicit, auditable stopping criterion (stop_sufficient / stop_diminishing_returns / escalate_human)."""
    subs = coverage.get("sub_questions", [])
    uncovered = [s.get("id") or s.get("sub_question_id") for s in subs if s.get("evidence_state") == "UNKNOWN"]
    partial = [s.get("id") or s.get("sub_question_id") for s in subs if s.get("evidence_state") == "PARTIAL"]
    if source_load_errors or uncovered or has_conflicts:
        decision = "STOP_WITH_GAPS_ESCALATE_HUMAN"
        reason = (
            f"Finite snapshot exhausted; uncovered sub-questions={uncovered}, partial={partial}, "
            f"source_load_errors={len(source_load_errors)}, conflicts={has_conflicts}. "
            "Do not fabricate remote recall; escalate listed gaps for targeted retrieval or human review."
        )
    elif marginal_new_hits == 0 and not partial:
        decision = "STOP_SUFFICIENT_CONVERGED"
        reason = "All sub-questions reached SUPPORTED status and expansion round produced zero marginal new hits."
    elif marginal_new_hits == 0:
        decision = "STOP_DIMINISHING_RETURNS"
        reason = f"Expansion round added 0 new hits; remaining partial sub-questions={partial} bounded by finite snapshot."
    else:
        decision = "STOP_FINITE_SNAPSHOT_COMPLETE"
        reason = f"Expansion added {marginal_new_hits} hit(s) and all local snapshot evidence has been evaluated."
    return {
        "decision": decision,
        "reason": reason,
        "covered_sub_questions": [s.get("id") or s.get("sub_question_id") for s in subs if s.get("evidence_state") == "SUPPORTED"],
        "partial_sub_questions": partial,
        "uncovered_sub_questions": uncovered,
        "marginal_new_hits_last_round": marginal_new_hits,
        "last_round_no_new_signal": marginal_new_hits == 0,
        "source_load_error_count": len(source_load_errors),
        "finite_snapshot_boundary": True,
    }


def parse_criteria(text: str) -> List[Dict[str, Any]]:
    """Parse free-text inclusion/exclusion criteria into evaluable rules."""
    rules: List[Dict[str, Any]] = []
    if not text:
        return rules
    lines: List[str] = []
    for block in re.split(r"\n\s*\n", text):
        for ln in block.splitlines():
            s = ln.strip().lstrip("-*•·0123456789.() ").strip()
            if s and len(s) > 3:
                lines.append(s)
    pol = "include"
    for ln in lines:
        low = ln.lower()
        if re.match(r"^(exclusion|exclude|排除|剔除|排除标准)", low):
            pol = "exclude"
            rest = re.sub(r"^(exclusion|exclude|排除标准|排除|剔除)[:：\s-]*", "", low)
            if rest and len(rest) > 5:
                ln = rest
            else:
                continue
        elif re.match(r"^(inclusion|include|纳入|纳入标准)", low):
            pol = "include"
            rest = re.sub(r"^(inclusion|include|纳入标准|纳入)[:：\s-]*", "", low)
            if rest and len(rest) > 5:
                ln = rest
            else:
                continue
        low = ln.lower()
        m_year = re.search(r"(?:published|between|from|in)\s*(?:between\s*)?(20\d{2})\s*(?:[-–~至到]|to|and|到)\s*(20\d{2})", low)
        m_year_min = re.search(r"(?:since|after|from)\s*(20\d{2})", low)
        m_year_max = re.search(r"(?:before|until|by)\s*(20\d{2})", low)
        m_lang = re.search(r"\b(english|portugu[eê]s|portuguese|chinese|spanish|french|german)\b|英文|中文|葡萄牙语", low)
        m_type = [k for k, words in EVIDENCE_LEX.items() if any(re.search(r"\b" + re.escape(w) + r"\b", low) for w in words)]
        rule = {
            "id": f"R{len(rules) + 1}",
            "polarity": pol,
            "text": ln,
            "field": "text",
            "negated": bool(re.match(r"^(not|no|non|exclude|without|不|非|未)", low)),
            "year_min": int(m_year.group(1)) if m_year else (int(m_year_min.group(1)) if m_year_min else None),
            "year_max": int(m_year.group(2)) if m_year else (int(m_year_max.group(1)) if m_year_max else None),
            "language": m_lang.group(1) if m_lang else None,
            "evidence_types": m_type,
            "terms": stem_tokens(tokenize(re.sub(r"\b(studies|papers|articles|works|publications|only|must|should|reported|reporting)\b", " ", low)))[:10],
        }
        rules.append(rule)
    return rules
