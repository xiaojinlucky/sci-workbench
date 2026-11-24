"""Five-state evidence verification, material-access hierarchy, domain-general extraction, and stdlib TF-IDF primitives.

Zero third-party dependencies and zero external network calls.
Five canonical evidence states:
  SUPPORTED | PARTIAL | REFUTED | OUTDATED | UNKNOWN
Three epistemic levels:
  FACT_OBSERVED | INFERENCE_BOUNDED | UNVERIFIED_OR_GAP
Six material-access tiers:
  fulltext_pdf | fulltext_xml | fulltext_local_text | abstract_only | discovery_only | blocked_or_unavailable
"""

from __future__ import annotations

import math
import re
from collections import Counter
from enum import Enum
from typing import Any

VALID_EVIDENCE_STATES = {"SUPPORTED", "PARTIAL", "REFUTED", "OUTDATED", "UNKNOWN"}
FULLTEXT_ACCESS_TIERS = {"fulltext_pdf", "fulltext_xml", "fulltext_local_text", "captured_text"}
NON_EVIDENCE_ACCESS_TIERS = {"discovery_only", "blocked_or_unavailable"}

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.I)
ARXIV_RE = re.compile(r"\b(?:arXiv:\s*|arxiv[_-])(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)
PMID_RE = re.compile(r"\bPMID:?\s*(\d{5,9})\b", re.I)
PMC_RE = re.compile(r"\b(PMC\d{5,9})\b", re.I)
YEAR_RE = re.compile(r"\b(1[89]\d{2}|20[0-3]\d)\b")

RETRACTION_CUE_RE = re.compile(
    r"\b(?:retract(?:ed|ion)|withdrawn\s+(?:article|paper|study)|corrigendum|erratum|expression\s+of\s+concern)\b|撤稿|勘误|更正",
    re.I,
)
# Strictly match uncompleted/prospective protocols — NEVER bare "proposed" in completed papers!
PROSPECTIVE_CUE_RE = re.compile(
    r"\b(?:study\s+protocol|trial\s+protocol|clinical\s+trial\s+protocol|prospective\s+(?:protocol|study\s+design|plan)|"
    r"will\s+be\s+(?:evaluated|tested|conducted|investigated|validated|recruited)|"
    r"not\s+yet\s+(?:conducted|completed|tested|validated|recruited)|"
    r"planned\s+(?:trial|experiment|study|cohort|validation)|"
    r"to\s+be\s+(?:tested|validated|confirmed)\s+in\s+(?:future|upcoming)|"
    r"future\s+(?:work|trials?|experiments?)\s+will)\b|"
    r"前瞻性研究方案|尚未开展|计划于后续开展|拟开展临床试验|有待后续实验验证",
    re.I,
)
OVERCLAIM_RE = re.compile(
    r"\b(?:no\s+(?:prior\s+)?(?:studies?|research|work)\s+exist[s]?\s+(?:anywhere|globally|in\s+the\s+world)|"
    r"first\s+(?:in\s+human\s+history|ever\s+globally)|never\s+been\s+studied\s+anywhere|"
    r"globally\s+exhaustive|definitively\s+(?:solved|proves?\s+universal)|universal\s+cure\s+in\s+all\s+patients|"
    r"unconditionally\s+feasible|completed\s+experiments?\s+(?:already\s+)?(?:confirm|prove|validate))\b|"
    r"全球范围内尚无任何研究|全球没有任何研究|史上首次|已完成全部实验证实|无条件可行|彻底解决|穷尽所有文献|普遍治愈",
    re.I,
)
# Domain-general positive empirical finding cues (no single-domain terms!)
POS_SIGNAL_RE = re.compile(
    r"\b(?:significant(?:ly)?\s+(?:improv(?:ed?|es|ing)|increas(?:ed?|es|ing)|reduc(?:ed?|es|ing)|enhanc(?:ed?|es|ing)|lower(?:ed?|s)|outperform(?:ed|s)?)|"
    r"statistically\s+significant\s+(?:improvement|reduction|increase|benefit|association|effect)|"
    r"outperform(?:ed|s|ing)?\s+(?:the\s+)?(?:baseline|control|existing|conventional|state-of-the-art)|"
    r"superior\s+(?:performance|efficacy|accuracy|stability|survival|activity)|"
    r"protective\s+effect|beneficial\s+(?:effect|impact|modulation)|"
    r"positively\s+(?:associated|correlated))\b|"
    r"显著提高|显著改善|显著降低|显著优于|保护效应|正向相关",
    re.I,
)
# Strictly match genuine opposing/null/contradictory empirical results — NEVER bare degradation/limited by/in contrast/however!
NEG_SIGNAL_RE = re.compile(
    r"\b(?:no\s+(?:statistically\s+)?significant\s+(?:difference|effect|association|improvement|benefit|correlation|change|reduction|increase)|"
    r"did\s+not\s+(?:significantly\s+)?(?:improve|reduce|increase|differ|affect|confirm|reproduce|replicate|alter)|"
    r"fail(?:ed|s)?\s+to\s+(?:reproduce|replicate|improve|show|confirm|demonstrate|generalize|outperform)|"
    r"null\s+(?:effect|result|finding|association)|statistically\s+insignificant|"
    r"contradict(?:s|ed|ing|ory)\s+(?:prior|previous|earlier|reported)|"
    r"opposite\s+(?:direction|effect|trend|association)|"
    r"worse\s+(?:outcomes?|performance|survival)|adverse\s+(?:events?|effects?|outcomes?)|"
    r"ineffective\s+(?:in|for|at)|no\s+evidence\s+of\s+(?:benefit|effect|efficacy|association)|"
    r"conflicting\s+(?:results?|findings?|evidence)\s+(?:were|have\s+been)\s+(?:observed|reported))\b|"
    r"无显著差异|未见显著|未能复现|无统计学差异|与既往结论相反|未观察到显著改善|无效|不良事件",
    re.I,
)
# Domain-general genuine limitation cues (no overpotential/dissolution overfitting!)
LIMITATION_CUE_RE = re.compile(
    r"\b(?:main\s+limitation|key\s+limitation|limitations?\s+of\s+(?:this|our|current|existing)\s+(?:study|work|analysis|approach)|"
    r"unresolved\s+(?:question|mechanism|challenge|issue)|remains?\s+(?:unclear|elusive|unresolved|poorly\s+understood)|"
    r"small\s+sample\s+size|limited\s+sample\s+size|lack\s+of\s+(?:external\s+validation|longitudinal|randomized|control|standardized|reproducibility)|"
    r"confounding\s+(?:bias|factors?)|selection\s+bias|publication\s+bias|high\s+heterogeneity|"
    r"poor\s+generaliz(?:ability|ation)|further\s+(?:validation|studies|trials)\s+(?:is|are)\s+(?:needed|required))\b|"
    r"主要局限|样本量较小|缺乏外部验证|混杂偏倚|异质性高|泛化能力不足|尚不清楚",
    re.I,
)
INTRO_PITCH_RE = re.compile(
    r"\b(?:to\s+(?:address|overcome|solve|mitigate|tackle)\s+(?:these|this|such)\s+(?:limitations?|challenges?|issues?)|"
    r"here,?\s+we\s+(?:introduce|present|propose|develop|report)|in\s+this\s+(?:paper|study|work),?\s+we)\b",
    re.I,
)
HEADER_NOISE_RE = re.compile(
    r"(?:@[a-z0-9.-]+\.[a-z]{2,}|orcid|correspondence\s*:|corresponding\s+author|department\s+of\b|"
    r"university\s+of\b|school\s+of\b|institute\s+(?:of|for)\b|received\s+\d{1,2}\s+\w+\s+\d{4}|"
    r"accepted\s+\d{1,2}\s+\w+\s+\d{4}|all\s+rights\s+reserved|creative\s+commons|©\s*\d{4}|"
    r"arxiv:\d{4}\.\d{4,5}|pp\.\s*\d+[–-]\d+|vol\.\s*\d+|issn\s+\d)",
    re.I,
)

# Domain-general scientific method, metric, and study-object extraction patterns
GENERAL_METHOD_RE = re.compile(
    r"\b(?:randomi[sz]ed\s+controlled\s+trial|double-blind|cohort\s+study|case-control|cross-sectional|"
    r"longitudinal\s+study|meta-analysis|systematic\s+review|regression(?:\s+model)?|logistic\s+regression|"
    r"cox\s+proportional|anova|mixed-effects|bayesian(?:\s+inference)?|monte\s+carlo|bootstrap(?:ping)?|"
    r"difference-in-differences|instrumental\s+variable|propensity\s+score|panel\s+data|time-series|"
    r"cross-validation|ablation(?:\s+study)?|benchmark(?:ing)?|machine\s+learning|deep\s+learning|"
    r"neural\s+network|transformer|graph\s+neural\s+network|random\s+forest|gradient\s+boosting|"
    r"principal\s+component|clustering|molecular\s+dynamics|density\s+functional|finite\s+element|"
    r"spectroscopy|microscopy|chromatography|mass\s+spectrometry|sequencing|rna-seq|single-cell|"
    r"metagenomics|metabolomics|proteomics|flow\s+cytometry|western\s+blot|elisa|pcr|crispr|"
    r"in\s+vivo|in\s+vitro|in\s+situ|operando|electrophysiology|imaging|mri|fmri|survey|qualitative\s+interview|"
    r"simulation|numerical\s+modeling|mathematical\s+model)\b",
    re.I,
)
SYNTAX_METHOD_RE = re.compile(
    r"\b(?:using|via|through|employing|leveraging|applying|based\s+on)\s+(?:a\s+|an\s+|the\s+)?"
    r"([a-z][a-z0-9-]*(?:\s+[a-z][a-z0-9-]*){1,3})\s+(?:model|method|algorithm|framework|assay|analysis|approach|technique|protocol|estimator|test|network|simulation)\b",
    re.I,
)
GENERAL_OBJECT_RE = re.compile(
    r"\b(?:human\s+(?:patients?|subjects?|participants?|cohorts?)|patients?|participants?|clinical\s+cohorts?|"
    r"mice|murine\s+models?|rats?|cell\s+lines?|primary\s+cells?|tissues?|organoids?|microbiota|bacteria|"
    r"proteins?|genes?|genomes?|molecules?|compounds?|polymers?|crystals?|catalysts?|electrodes?|materials?|"
    r"alloys?|semiconductors?|nanoparticles?|ecosystems?|species|households?|firms?|markets?|municipalities?|"
    r"hospitals?|students?|graphs?|networks?|benchmarks?|datasets?|images?|corpora|documents?)\b",
    re.I,
)
GENERAL_METRIC_RE = re.compile(
    r"\b(?:auc|auroc|auprc|f1(?:-score)?|rmse|mae|mse|r-squared|r2|accuracy|precision|recall|sensitivity|specificity|"
    r"hazard\s+ratio|odds\s+ratio|risk\s+ratio|relative\s+risk|effect\s+size|cohen'?s?\s+d|hedges'?\s+g|"
    r"mean\s+difference|confidence\s+interval|p-value|p\s*[<=>]\s*0\.\d+|survival\s+rate|mortality|"
    r"incidence|prevalence|elasticity|throughput|latency|efficiency|selectivity|yield|conversion|stability|"
    r"overpotential|current\s+density|tafel\s+slope|binding\s+affinity|ic50|fold\s+change|correlation|spearman|pearson)\b",
    re.I,
)

STOPWORDS = {
    "about", "above", "across", "after", "against", "also", "among", "analysis", "and", "another",
    "approach", "approaches", "are", "article", "associated", "author", "authors", "based", "because",
    "been", "before", "between", "both", "but", "can", "case", "cases", "compared", "comparison",
    "conclusion", "conclusions", "could", "current", "data", "demonstrate", "demonstrated", "did", "different",
    "discussion", "does", "during", "each", "effect", "effects", "et", "evaluation", "evidence", "existing",
    "experiment", "experimental", "experiments", "figure", "findings", "first", "following", "for",
    "from", "further", "have", "here", "high", "higher", "how", "however", "important", "improve", "improved",
    "including", "increase", "increased", "into", "introduction", "investigate", "investigated", "journal",
    "key", "known", "large", "level", "levels", "literature", "local", "low", "lower", "main", "many",
    "may", "method", "methodology", "methods", "model", "models", "more", "most", "multiple", "new",
    "not", "novel", "number", "observed", "often", "one", "only", "other", "our", "out", "over",
    "paper", "papers", "performance", "potential", "present", "previous", "prior", "problem", "process",
    "proposed", "provide", "provided", "published", "recent", "related", "report", "reported", "research",
    "result", "results", "review", "role", "same", "sample", "samples", "section", "several", "should", "show",
    "showed", "shown", "shows", "significant", "similar", "since", "some", "specific", "state", "studies",
    "study", "such", "suggest", "suggested", "summary", "system", "systems", "table", "task", "tasks",
    "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "through",
    "time", "title", "two", "under", "use", "used", "using", "various", "very", "was", "well", "were",
    "what", "when", "where", "which", "while", "who", "why", "will", "with", "within", "without", "work",
    "works", "would", "year", "years",
}


class EvidenceState(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    REFUTED = "REFUTED"
    OUTDATED = "OUTDATED"
    UNKNOWN = "UNKNOWN"


def normalize_doi(raw: str) -> str:
    txt = (raw or "").strip().lower()
    txt = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", txt)
    txt = re.sub(r"^doi:\s*", "", txt)
    return txt.rstrip(".,;:!?)>]}")


def normalize_arxiv_id(raw: str) -> str:
    txt = (raw or "").strip().lower()
    txt = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", txt)
    txt = re.sub(r"^arxiv[:_-]", "", txt)
    txt = re.sub(r"\.pdf$", "", txt)
    m = re.match(r"^(\d{4}\.\d{4,5})(?:v\d+)?$", txt)
    return m.group(1) if m else ""


def extract_identifiers(text: str) -> dict[str, list[str]]:
    """Extract normalized DOI, arXiv, PMID, and PMC identifiers without network calls."""
    dois = sorted({normalize_doi(m) for m in DOI_RE.findall(text or "") if normalize_doi(m)})
    arxivs = sorted({normalize_arxiv_id(m) for m in ARXIV_RE.findall(text or "") if normalize_arxiv_id(m)})
    pmids = sorted(set(PMID_RE.findall(text or "")))
    pmcs = sorted({m.upper() for m in PMC_RE.findall(text or "")})
    return {"doi": dois, "arxiv": arxivs, "pmid": pmids, "pmc": pmcs}


def verify_quote_grounded(quote: str, source_text: str) -> bool:
    q_norm = " ".join((quote or "").split()).casefold()
    s_norm = " ".join((source_text or "").split()).casefold()
    if len(q_norm) < 6:
        return False
    if q_norm in s_norm:
        return True
    q_alnum = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", q_norm).strip()
    s_alnum = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s_norm).strip()
    return len(q_alnum) >= 12 and q_alnum in s_alnum


def detect_overclaims(text: str) -> list[str]:
    return [m.group(0) for m in OVERCLAIM_RE.finditer(text or "")]


def is_fulltext_access(material_access: str) -> bool:
    return (material_access or "").strip().lower() in FULLTEXT_ACCESS_TIERS


def is_non_evidence_access(material_access: str) -> bool:
    return (material_access or "").strip().lower() in NON_EVIDENCE_ACCESS_TIERS


def is_header_or_affiliation_noise(sent: str) -> bool:
    """Filter out PDF title banners, author lists, affiliations, emails, and copyright lines."""
    s = (sent or "").strip()
    if len(s) < 25:
        return True
    if HEADER_NOISE_RE.search(s):
        return True
    # Heavy affiliation / author-list signature: many uppercase initials or commas without prose verbs
    words = s.split()
    if len(words) >= 6:
        cap_ratio = sum(1 for w in words if w[:1].isupper()) / len(words)
        if cap_ratio > 0.65 and not re.search(r"\b(?:is|are|was|were|showed|found|observed|demonstrated|indicated|revealed|compared|measured|tested)\b", s, re.I):
            return True
    return False


def _canon_phrase(phrase: str) -> str:
    p = " ".join(phrase.lower().split())
    if len(p) > 4 and p.endswith("s") and not p.endswith(("ss", "us", "is", "ics")):
        p = p[:-1]
    return p


def extract_general_methods_and_objects(text: str) -> dict[str, list[str]]:
    """Domain-general extraction of functional methods, study objects, and evaluation metrics with case/plural dedup."""
    blob = (text or "")[:10000]
    methods_seen: dict[str, str] = {}
    for m in GENERAL_METHOD_RE.finditer(blob):
        raw = " ".join(m.group(0).split()).lower()
        key = _canon_phrase(raw)
        methods_seen.setdefault(key, raw)
    for m in SYNTAX_METHOD_RE.finditer(blob):
        raw = " ".join(m.group(0).split()).lower()
        # Trim leading preposition
        raw = re.sub(r"^(?:using|via|through|employing|leveraging|applying|based\s+on)\s+(?:a\s+|an\s+|the\s+)?", "", raw)
        key = _canon_phrase(raw)
        if 4 <= len(raw) <= 45:
            methods_seen.setdefault(key, raw)

    objects_seen: dict[str, str] = {}
    for m in GENERAL_OBJECT_RE.finditer(blob):
        raw = " ".join(m.group(0).split()).lower()
        objects_seen.setdefault(_canon_phrase(raw), raw)

    metrics_seen: dict[str, str] = {}
    for m in GENERAL_METRIC_RE.finditer(blob):
        raw = " ".join(m.group(0).split()).lower()
        metrics_seen.setdefault(_canon_phrase(raw), raw)

    return {
        "methods": sorted(methods_seen.values())[:8],
        "objects": sorted(objects_seen.values())[:6],
        "metrics": sorted(metrics_seen.values())[:8],
    }


def classify_five_state_support(
    *,
    has_direct_support: bool,
    has_opposing_evidence: bool,
    missing_critical_context: bool,
    is_superseded_or_retracted: bool,
    source_unavailable: bool,
    material_access: str = "fulltext_local_text",
) -> str:
    """Deterministic 5-state classification guard enforcing access-tier ceiling."""
    if source_unavailable or is_non_evidence_access(material_access):
        return EvidenceState.UNKNOWN.value
    if is_superseded_or_retracted:
        return EvidenceState.OUTDATED.value
    abstract_capped = (material_access or "").strip().lower() == "abstract_only"
    if has_opposing_evidence and not has_direct_support:
        return EvidenceState.PARTIAL.value if abstract_capped else EvidenceState.REFUTED.value
    if (has_direct_support and has_opposing_evidence) or (has_direct_support and (missing_critical_context or abstract_capped)):
        return EvidenceState.PARTIAL.value
    if has_direct_support:
        return EvidenceState.SUPPORTED.value
    return EvidenceState.UNKNOWN.value


def split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\u4e00-\u9fff(\[])", cleaned)
    return [p.strip() for p in parts if len(p.strip()) >= 25]


def extract_directional_passages(
    record: dict[str, Any],
    *,
    max_per_record: int = 3,
) -> list[dict[str, Any]]:
    """Extract verbatim body/abstract sentences with locators and empirical polarity, excluding PDF header noise."""
    access = str(record.get("material_access") or "unknown")
    if is_non_evidence_access(access):
        return []

    passages = record.get("passages") or []
    candidates: list[tuple[str, str]] = []
    if passages:
        for p in passages:
            loc = str(p.get("locator") or "body")
            txt = str(p.get("text") or "")
            # Skip title/author block on page 1 if Abstract marker exists later on page 1
            if loc.endswith("#page=1"):
                am = re.search(r"\babstract\b[:.\s]*", txt, re.I)
                if am and am.start() < 2500:
                    txt = txt[am.end():]
            for sent in split_sentences(txt)[:45]:
                if not is_header_or_affiliation_noise(sent):
                    candidates.append((loc, sent))
    else:
        if record.get("text"):
            for sent in split_sentences(str(record["text"]))[:60]:
                if not is_header_or_affiliation_noise(sent):
                    candidates.append((f"{record.get('source_file') or record.get('record_id')}#body", sent))
        elif record.get("abstract"):
            for sent in split_sentences(str(record["abstract"]))[:25]:
                if not is_header_or_affiliation_noise(sent):
                    candidates.append((f"{record.get('source_file') or record.get('record_id')}#abstract", sent))

    picked: list[dict[str, Any]] = []
    seen_quotes: set[str] = set()
    for loc, sent in candidates:
        has_pos = bool(POS_SIGNAL_RE.search(sent))
        has_neg = bool(NEG_SIGNAL_RE.search(sent))
        has_lim = bool(LIMITATION_CUE_RE.search(sent)) and not bool(INTRO_PITCH_RE.search(sent))
        has_prosp = bool(PROSPECTIVE_CUE_RE.search(sent))
        if not (has_pos or has_neg or has_lim or has_prosp):
            continue
        norm_q = sent[:120].lower()
        if norm_q in seen_quotes:
            continue
        seen_quotes.add(norm_q)
        if has_prosp and not (has_pos or has_neg):
            direction = "prospective"
        elif has_pos and has_neg:
            direction = "mixed"
        elif has_neg:
            direction = "opposing_or_null"
        elif has_pos:
            direction = "supportive"
        else:
            direction = "limitation"
        picked.append({
            "record_id": record.get("record_id"),
            "locator": loc,
            "material_access": access,
            "direction": direction,
            "is_limitation": has_lim,
            "is_prospective": has_prosp,
            "quote": sent[:280],
        })
        if len(picked) >= max_per_record:
            break

    if not picked and candidates:
        loc, sent = candidates[0]
        picked.append({
            "record_id": record.get("record_id"),
            "locator": loc,
            "material_access": access,
            "direction": "descriptive",
            "is_limitation": False,
            "is_prospective": bool(PROSPECTIVE_CUE_RE.search(sent)),
            "quote": sent[:280],
        })
    return picked


def tokenize_terms(text: str, *, include_bigrams: bool = True, aliases: dict[str, str] | None = None) -> list[str]:
    """Extract normalized domain unigrams and optional bigrams with alias folding."""
    raw_tokens = re.findall(r"[a-z][a-z0-9-]{2,}|[\u4e00-\u9fff]{2,6}", (text or "").lower())
    alias_map = {k.lower(): v.lower() for k, v in (aliases or {}).items()}
    unigrams: list[str] = []
    for tok in raw_tokens:
        t = tok.strip("-")
        if len(t) < 3 or t in STOPWORDS or t.isdigit():
            continue
        t = alias_map.get(t, t)
        unigrams.append(t)
    if not include_bigrams:
        return unigrams
    bigrams: list[str] = []
    for i in range(len(unigrams) - 1):
        bg = f"{unigrams[i]} {unigrams[i + 1]}"
        bg = alias_map.get(bg, bg)
        if unigrams[i] != unigrams[i + 1]:
            bigrams.append(bg)
    return unigrams + bigrams


def build_tfidf_vectors(docs_tokens: list[list[str]]) -> list[dict[str, float]]:
    """Pure-stdlib TF-IDF vectors for clustering and prior-work similarity."""
    n_docs = len(docs_tokens)
    if n_docs == 0:
        return []
    df: Counter[str] = Counter()
    for toks in docs_tokens:
        for t in set(toks):
            df[t] += 1
    vectors: list[dict[str, float]] = []
    for toks in docs_tokens:
        tf = Counter(toks)
        total = max(1, len(toks))
        vec: dict[str, float] = {}
        for t, cnt in tf.items():
            idf = math.log((n_docs + 1) / (df[t] + 1)) + 1.0
            vec[t] = (cnt / total) * idf
        vectors.append(vec)
    return vectors


def cosine_similarity(v1: dict[str, float], v2: dict[str, float]) -> float:
    if not v1 or not v2:
        return 0.0
    common = set(v1.keys()) & set(v2.keys())
    if not common:
        return 0.0
    dot = sum(v1[k] * v2[k] for k in common)
    n1 = math.sqrt(sum(x * x for x in v1.values()))
    n2 = math.sqrt(sum(x * x for x in v2.values()))
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    return dot / (n1 * n2)
