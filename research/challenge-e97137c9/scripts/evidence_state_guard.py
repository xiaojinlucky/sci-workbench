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
SECONDARY_ACCESS_TIERS = {"secondary_note"}
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
# Domain-general genuine opposing/null/contradictory/trade-off empirical cues — NEVER bare degradation/limited by/in contrast/however!
NEG_SIGNAL_RE = re.compile(
    r"\b(?:no\s+(?:statistically\s+)?significant\s+(?:difference|effect|association|improvement|benefit|correlation|change|reduction|increase)|"
    r"did\s+not\s+(?:significantly\s+)?(?:improve|reduce|increase|differ|affect|confirm|reproduce|replicate|alter)|"
    r"fail(?:ed|s)?\s+to\s+(?:reproduce|replicate|improve|show|confirm|demonstrate|generalize|outperform|sustain|maintain)|"
    r"null\s+(?:effect|result|finding|association)|statistically\s+insignificant|"
    r"contradict(?:s|ed|ing|ory)\s+(?:prior|previous|earlier|reported|existing|conventional)|"
    r"inconsistent\s+with\s+(?:prior|previous|earlier|reported|existing|conventional)|"
    r"opposite\s+(?:direction|effect|trend|association)|"
    r"worse\s+(?:outcomes?|performance|stability|survival)|adverse\s+(?:events?|effects?|outcomes?)|"
    r"ineffective\s+(?:in|for|at)|no\s+evidence\s+of\s+(?:benefit|effect|efficacy|association)|"
    r"conflicting\s+(?:results?|findings?|evidence|reports?|conclusions?)|"
    r"remains?\s+(?:controversial|debated|disputed)|"
    r"(?:activity[- ]stability|accuracy[- ]latency|efficacy[- ]toxicity|performance[- ]stability|cost[- ]performance|fundamental|inherent|intrinsic|severe)\s+trade[- ]?off|"
    r"trade[- ]?off\s+between\b|"
    r"poor\s+(?:stability|durability|reproducibility|selectivity|generalization|long-term\s+stability)|"
    r"rapid(?:ly)?\s+(?:decay(?:s|ed|ing)?|declin(?:es?|ed|ing)|degrad(?:es?|ed|ing)|deteriorat(?:es?|ed|ing))|"
    r"suffer(?:s|ed|ing)?\s+from\s+(?:severe|rapid|poor|high|low|instability|toxicity|overfitting|bias|degradation|dissolution|leaching|decay|loss)|"
    r"inferior\s+(?:to|performance|stability|efficacy|accuracy)|underperform(?:s|ed|ing)?|"
    r"severe\s+(?:instability|leaching|degradation|decay|loss|bias|toxicity)|"
    r"at\s+the\s+expense\s+of|compromis(?:es?|ed|ing)\s+(?:stability|durability|specificity|safety|generalization|accuracy))\b|"
    r"无显著差异|未见显著|未能复现|无统计学差异|与既往结论相反|未观察到显著改善|无效|不良事件|存在争议|权衡矛盾|稳定性差|快速衰减",
    re.I,
)
HARD_REFUTE_RE = re.compile(
    r"\b(?:no\s+(?:statistically\s+)?significant|did\s+not\s+(?:significantly\s+)?|fail(?:ed|s)?\s+to|"
    r"contradict|inconsistent\s+with|opposite\s+(?:direction|effect|trend)|worse\s+|adverse\s+|"
    r"ineffective|conflicting\s+|controversial|debated|disputed|inferior\s+|underperform)\b",
    re.I,
)
POSITIVE_MITIGATION_GUARD_RE = re.compile(
    r"\b(?:negligible|minimal|no|without|zero|suppress(?:es|ed|ing)?|prevent(?:s|ed|ing)?|avoid(?:s|ed|ing)?|"
    r"mitigat(?:es|ed|ing)?|overcom(?:es?|ing)|eliminat(?:es?|ed|ing)|circumvent(?:s|ed|ing)?|break(?:s|ing)?\s+the)\s+"
    r"(?:\w+\s+){0,3}(?:degradation|decay|dissolution|leaching|instability|loss|toxicity|trade[- ]?off)\b",
    re.I,
)
# Explicit negation-scope guard for safety/adverse-event absence (must NOT become opposing_or_null!)
NO_ADVERSE_SAFETY_RE = re.compile(
    r"\b(?:no|zero|without|absence\s+of|free\s+of|neither)\s+"
    r"(?:statistically\s+significant\s+|significant\s+|serious\s+|severe\s+|major\s+|treatment[- ]related\s+|observable\s+|detectable\s+){0,2}"
    r"(?:adverse\s+(?:events?|effects?|reactions?|outcomes?)|side\s+effects?|toxicity|toxicities|safety\s+(?:signals?|concerns?|issues?)|"
    r"complications?|harmful\s+effects?|mortality|deaths?|degradation|dissolution|leaching)\b|"
    r"(?:未观察到|未见|无)(?:明显|严重|显著)?(?:不良事件|不良反应|副作用|毒性|并发症|溶出|衰减)",
    re.I,
)
# Adverse / undesirable endpoints whose significant INCREASE is opposing/harmful (NOT supportive!)
ADVERSE_ENDPOINT_RE = re.compile(
    r"\b(?:mortality|all[- ]cause\s+mortality|death\s+rate|fatality|toxicity|hepatotoxicity|nephrotoxicity|neurotoxicity|"
    r"adverse\s+(?:events?|effects?|outcomes?)|serious\s+adverse|error\s+rate|false[- ]positive\s+rate|failure\s+rate|"
    r"relapse\s+rate|hospitali[sz]ation|complications?|bleeding|infection\s+rate|"
    r"overpotential|dissolution\s+rate|corrosion\s+rate|degradation\s+rate|leaching\s+rate|capacity\s+fading|"
    r"hallucination\s+rate|latency|loss\s+rate)\b|"
    r"死亡率|病死率|毒性|不良事件|错误率|失败率|复发率|过电位|溶出速率|腐蚀速率|衰减率",
    re.I,
)
INCREASE_DIRECTION_RE = re.compile(
    r"\b(?:significant(?:ly)?\s+(?:increas(?:ed?|es|ing)|elevat(?:ed?|es|ing)|ris(?:es?|ing)|rose|higher|worsen(?:ed|s|ing)?|accelerat(?:ed?|es|ing)|exacerbat(?:ed?|es|ing))|"
    r"(?:increas(?:ed?|es|ing)|elevat(?:ed?|es|ing)|higher|worsen(?:ed|s|ing)?|accelerat(?:ed?|es|ing))\s+(?:the\s+|in\s+)?(?:\w+\s+){0,3}(?:mortality|toxicity|adverse|error\s+rate|dissolution|degradation|overpotential|corrosion|leaching|failure|relapse|complications?)|"
    r"(?:mortality|toxicity|adverse\s+events?|error\s+rate|dissolution|degradation|overpotential|corrosion|leaching|failure\s+rate)\s+(?:was|were|is|are)?\s*(?:significant(?:ly)?\s+)?(?:increas(?:ed?|es)|elevated|higher|rose|worsened|accelerated))\b|"
    r"(?:显著|明显)(?:升高|上升|增加|恶化|加剧)",
    re.I,
)
DECREASE_DIRECTION_RE = re.compile(
    r"\b(?:significant(?:ly)?\s+(?:reduc(?:ed?|es|ing)|decreas(?:ed?|es|ing)|lower(?:ed?|s|ing)|suppress(?:ed?|es|ing)|inhibit(?:ed?|s|ing)|prevent(?:ed?|s|ing)|mitigat(?:ed?|es|ing)|attenuat(?:ed?|es|ing))|"
    r"lower(?:ed|s)?\s+(?:the\s+)?(?:\w+\s+){0,2}(?:mortality|toxicity|overpotential|error\s+rate|dissolution|degradation|leaching|relapse|adverse))\b|"
    r"(?:显著|明显)(?:降低|减少|下降|抑制|缓解)",
    re.I,
)
# Prior/background attribution guard: quotes reporting earlier null/negative work in Introduction are NOT primary findings
ATTRIBUTED_BACKGROUND_RE = re.compile(
    r"\b(?:(?:previous|prior|earlier|historical|past|initial)\s+(?:studies|work|reports?|trials?|investigations?|analyses)\s+"
    r"(?:reported|found|showed|observed|suggested|failed|indicated|demonstrated)\s+(?:that\s+)?(?:no\s+|little\s+|limited\s+|conflicting|inconsistent|null|negative|failed)|"
    r"previously\s+(?:thought|believed|reported|assumed)\s+to\s+(?:have\s+no|be\s+ineffective|fail)|"
    r"in\s+contrast\s+to\s+(?:earlier|previous|prior)\s+(?:reports?|studies)\s+(?:that|which)\s+(?:found|reported|failed)|"
    r"historically,?\s+(?:studies|trials|work)\s+(?:failed|found\s+no))\b|"
    r"(?:既往|早期|先前|此前)(?:研究|报道|试验)(?:曾)?(?:未见|未能|认为无|报告无)",
    re.I,
)
# Domain-general genuine limitation & bottleneck cues (no single-domain overfitting!)
LIMITATION_CUE_RE = re.compile(
    r"\b(?:main\s+limitation|key\s+limitation|limitations?\s+of\s+(?:this|our|current|existing)\s+(?:study|work|analysis|approach)|"
    r"unresolved\s+(?:question|mechanism|challenge|issue)|remains?\s+(?:unclear|elusive|unresolved|poorly\s+understood|a\s+(?:major\s+)?(?:challenge|bottleneck|obstacle|barrier))|"
    r"major\s+bottleneck|hinder(?:s|ed|ing)\s+(?:practical|clinical|large-scale|real-world|widespread)|"
    r"small\s+sample\s+size|limited\s+sample\s+size|lack\s+of\s+(?:external\s+validation|longitudinal|randomized|control|standardized|reproducibility)|"
    r"confounding\s+(?:bias|factors?)|selection\s+bias|publication\s+bias|high\s+heterogeneity|"
    r"poor\s+generaliz(?:ability|ation)|further\s+(?:validation|studies|trials)\s+(?:is|are)\s+(?:needed|required))\b|"
    r"主要局限|样本量较小|缺乏外部验证|混杂偏倚|异质性高|泛化能力不足|尚不清楚|主要瓶颈",
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


def _extract_sentence_entity_endpoint(sent: str) -> tuple[str, str]:
    """Extract lightweight normalized (entity, endpoint) pair for same-entity/same-endpoint conflict gating."""
    low = (sent or "").lower()
    obj_m = GENERAL_OBJECT_RE.search(low)
    met_m = GENERAL_METRIC_RE.search(low)
    adv_m = ADVERSE_ENDPOINT_RE.search(low)
    entity = _canon_phrase(obj_m.group(0)) if obj_m else ""
    endpoint = _canon_phrase(adv_m.group(0)) if adv_m else (_canon_phrase(met_m.group(0)) if met_m else "")
    return entity, endpoint


def _clip_full_sentence(sent: str, max_chars: int = 650) -> str:
    """Keep full sentence without blind 280-char truncation; only bound extreme run-on blocks at word boundary."""
    s = " ".join((sent or "").split()).strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "..."


def _classify_passage_polarity(sent: str) -> tuple[str, dict[str, Any]]:
    """Classify sentence polarity with explicit negation scope, adverse-outcome inversion, and background guard."""
    meta: dict[str, Any] = {
        "negation_scope_resolved": False,
        "adverse_outcome_inverted": False,
        "background_attribution": False,
        "is_limitation": False,
        "is_prospective": False,
    }
    has_prosp = bool(PROSPECTIVE_CUE_RE.search(sent))
    has_lim = bool(LIMITATION_CUE_RE.search(sent)) and not bool(INTRO_PITCH_RE.search(sent))
    meta["is_prospective"] = has_prosp
    meta["is_limitation"] = has_lim

    # 1. Background / Introduction attributed prior null claims -> NEVER tag as primary study opposing finding!
    if ATTRIBUTED_BACKGROUND_RE.search(sent):
        meta["background_attribution"] = True
        return "UNKNOWN", meta

    # 2. Explicit safety / absence-of-adverse-events negation scope (e.g., "No adverse events were observed")
    has_no_adverse = bool(NO_ADVERSE_SAFETY_RE.search(sent))
    if has_no_adverse:
        meta["negation_scope_resolved"] = True

    # 3. Adverse endpoint polarity check (e.g., "mortality significantly increased" -> opposing_or_null, NOT supportive!)
    has_adverse_ep = bool(ADVERSE_ENDPOINT_RE.search(sent))
    adverse_increased = has_adverse_ep and bool(INCREASE_DIRECTION_RE.search(sent)) and not has_no_adverse
    adverse_decreased = has_adverse_ep and bool(DECREASE_DIRECTION_RE.search(sent))

    has_pos = bool(POS_SIGNAL_RE.search(sent))
    has_neg = bool(NEG_SIGNAL_RE.search(sent)) and not bool(INTRO_PITCH_RE.search(sent))

    if has_no_adverse:
        # Remove false opposing match triggered by "adverse events" inside "No adverse events..."
        stripped = NO_ADVERSE_SAFETY_RE.sub(" ", sent)
        has_neg = bool(NEG_SIGNAL_RE.search(stripped)) and not bool(INTRO_PITCH_RE.search(stripped))
        if not has_neg and not has_pos:
            # Absence of adverse events is a favorable safety finding, NOT opposing_or_null
            return "supportive", meta

    if adverse_increased and not adverse_decreased:
        meta["adverse_outcome_inverted"] = True
        has_neg = True
        # Only retain has_pos if a separate non-adverse improvement clause co-exists
        non_adv = ADVERSE_ENDPOINT_RE.sub(" ", sent)
        has_pos = bool(re.search(r"\b(?:improv(?:ed?|es)|enhanc(?:ed?|es)|outperform|superior|protective|beneficial)\b", non_adv, re.I))
    elif adverse_decreased and not adverse_increased:
        has_pos = True
        if not HARD_REFUTE_RE.search(sent):
            has_neg = False

    if has_neg and POSITIVE_MITIGATION_GUARD_RE.search(sent) and not HARD_REFUTE_RE.search(sent) and not adverse_increased:
        has_neg = False

    if not (has_pos or has_neg or has_lim or has_prosp):
        return "", meta
    if has_prosp and not (has_pos or has_neg):
        return "prospective", meta
    if has_pos and has_neg:
        return "mixed", meta
    if has_neg:
        return "opposing_or_null", meta
    if has_pos:
        return "supportive", meta
    return "limitation", meta


def extract_directional_passages(
    record: dict[str, Any],
    *,
    max_per_record: int | None = None,
) -> list[dict[str, Any]]:
    """Extract full verbatim body/abstract sentences with real page/section/paragraph locators and balanced polarity."""
    access = str(record.get("material_access") or "unknown")
    if is_non_evidence_access(access):
        return []

    src_key = str(record.get("source_file") or record.get("record_id") or "record")
    passages = record.get("passages") or []
    candidates: list[tuple[str, str]] = []
    if passages:
        for p_idx, p in enumerate(passages, start=1):
            raw_loc = str(p.get("locator") or "").strip()
            if not raw_loc or raw_loc.endswith("#body") or raw_loc == "body":
                sec = str(p.get("section") or f"paragraph={p_idx}")
                raw_loc = f"{src_key}#{sec}"
            txt = str(p.get("text") or "")
            if raw_loc.endswith("#page=1"):
                am = re.search(r"\babstract\b[:.\s]*", txt, re.I)
                if am and am.start() < 2500:
                    txt = txt[am.end():]
            for s_idx, sent in enumerate(split_sentences(txt)[:60], start=1):
                if not is_header_or_affiliation_noise(sent):
                    candidates.append((f"{raw_loc}:S{s_idx}", sent))
    else:
        if record.get("text"):
            paras = [pp.strip() for pp in re.split(r"\n\s*\n+", str(record["text"])) if pp.strip()]
            if not paras:
                paras = [str(record["text"])]
            for p_idx, para in enumerate(paras[:40], start=1):
                for s_idx, sent in enumerate(split_sentences(para)[:15], start=1):
                    if not is_header_or_affiliation_noise(sent):
                        candidates.append((f"{src_key}#para={p_idx}:S{s_idx}", sent))
        elif record.get("abstract"):
            for s_idx, sent in enumerate(split_sentences(str(record["abstract"]))[:30], start=1):
                if not is_header_or_affiliation_noise(sent):
                    candidates.append((f"{src_key}#abstract:S{s_idx}", sent))

    # Balanced per-polarity quotas prevent sequential truncation from dropping later opposing/limitation findings!
    polarity_caps = {
        "supportive": 4,
        "opposing_or_null": 4,
        "mixed": 3,
        "limitation": 3,
        "prospective": 2,
        "UNKNOWN": 2,
    }
    polarity_counts: Counter[str] = Counter()
    picked: list[dict[str, Any]] = []
    seen_quotes: set[str] = set()
    hard_cap = max_per_record if (isinstance(max_per_record, int) and max_per_record > 0) else 14

    for loc, sent in candidates:
        direction, pmeta = _classify_passage_polarity(sent)
        if not direction:
            continue
        if polarity_counts[direction] >= polarity_caps.get(direction, 2):
            continue
        norm_q = sent[:140].lower()
        if norm_q in seen_quotes:
            continue
        seen_quotes.add(norm_q)
        entity, endpoint = _extract_sentence_entity_endpoint(sent)
        polarity_counts[direction] += 1
        picked.append({
            "record_id": record.get("record_id"),
            "locator": loc,
            "material_access": access,
            "direction": direction,
            "entity": entity or "unspecified_entity",
            "endpoint": endpoint or "unspecified_endpoint",
            "comparability_status": "comparable_entity_endpoint" if (entity and endpoint) else "unaligned_endpoint_or_ambiguous",
            "negation_scope_resolved": pmeta["negation_scope_resolved"],
            "adverse_outcome_inverted": pmeta["adverse_outcome_inverted"],
            "background_attribution": pmeta["background_attribution"],
            "is_limitation": pmeta["is_limitation"],
            "is_prospective": pmeta["is_prospective"],
            "quote": _clip_full_sentence(sent, max_chars=650),
        })
        if len(picked) >= hard_cap:
            break

    if not picked and candidates:
        loc, sent = candidates[0]
        entity, endpoint = _extract_sentence_entity_endpoint(sent)
        picked.append({
            "record_id": record.get("record_id"),
            "locator": loc,
            "material_access": access,
            "direction": "descriptive",
            "entity": entity or "unspecified_entity",
            "endpoint": endpoint or "unspecified_endpoint",
            "comparability_status": "descriptive_only",
            "negation_scope_resolved": False,
            "adverse_outcome_inverted": False,
            "background_attribution": False,
            "is_limitation": False,
            "is_prospective": bool(PROSPECTIVE_CUE_RE.search(sent)),
            "quote": _clip_full_sentence(sent, max_chars=650),
        })
    return picked


def build_comparable_conflict_edges(passages: list[dict[str, Any]]) -> dict[str, Any]:
    """Build conflict edges ONLY between opposing passages sharing compatible entity and endpoint; else mark UNKNOWN."""
    pos_list = [p for p in passages if p.get("direction") == "supportive"]
    neg_list = [p for p in passages if p.get("direction") == "opposing_or_null"]
    edges: list[dict[str, Any]] = []
    unaligned_pairs: list[dict[str, Any]] = []
    for pos in pos_list:
        for neg in neg_list:
            if pos.get("record_id") == neg.get("record_id"):
                continue
            pe, pep = str(pos.get("entity") or ""), str(pos.get("endpoint") or "")
            ne, nep = str(neg.get("entity") or ""), str(neg.get("endpoint") or "")
            same_endpoint = bool(pep and nep and pep != "unspecified_endpoint" and pep == nep)
            same_entity = bool(pe and ne and pe != "unspecified_entity" and pe == ne)
            if same_endpoint and (same_entity or pe == "unspecified_entity" or ne == "unspecified_entity"):
                edges.append({
                    "status": "COMPARABLE_CONFLICT",
                    "entity": pe if pe != "unspecified_entity" else ne,
                    "endpoint": pep,
                    "supportive_record_id": pos.get("record_id"),
                    "supportive_locator": pos.get("locator"),
                    "opposing_record_id": neg.get("record_id"),
                    "opposing_locator": neg.get("locator"),
                })
            else:
                unaligned_pairs.append({
                    "status": "UNKNOWN_CROSS_ENDPOINT_NONCOMPARABLE",
                    "supportive_entity_endpoint": f"{pe}/{pep}",
                    "opposing_entity_endpoint": f"{ne}/{nep}",
                    "supportive_record_id": pos.get("record_id"),
                    "opposing_record_id": neg.get("record_id"),
                })
    return {"comparable_conflict_edges": edges[:20], "noncomparable_unknown_pairs": unaligned_pairs[:20]}


# Longest-phrase canonical scientific concept dictionary (abbreviation / plural / multi-word unification)
CANONICAL_CONCEPT_PHRASES: list[tuple[str, str]] = sorted(
    [
        ("oxygen evolution reaction", "oxygen_evolution_reaction"),
        ("oxygen evolution electrocatalysis", "oxygen_evolution_reaction"),
        ("oxygen evolution", "oxygen_evolution_reaction"),
        ("hydrogen evolution reaction", "hydrogen_evolution_reaction"),
        ("proton exchange membrane water electrolysis", "pem_water_electrolysis"),
        ("proton exchange membrane", "proton_exchange_membrane"),
        ("anion exchange membrane", "anion_exchange_membrane"),
        ("short-chain fatty acids", "short_chain_fatty_acid"),
        ("short-chain fatty acid", "short_chain_fatty_acid"),
        ("short chain fatty acids", "short_chain_fatty_acid"),
        ("short chain fatty acid", "short_chain_fatty_acid"),
        ("inflammatory bowel disease", "inflammatory_bowel_disease"),
        ("ulcerative colitis", "ulcerative_colitis"),
        ("gut microbiota", "gut_microbiota"),
        ("intestinal microbiota", "gut_microbiota"),
        ("high-entropy alloys", "high_entropy_alloy"),
        ("high-entropy alloy", "high_entropy_alloy"),
        ("high entropy alloy", "high_entropy_alloy"),
        ("high-entropy oxides", "high_entropy_oxide"),
        ("high-entropy oxide", "high_entropy_oxide"),
        ("density functional theory", "density_functional_theory"),
        ("single-atom catalysts", "single_atom_catalyst"),
        ("single-atom catalyst", "single_atom_catalyst"),
        ("graph neural networks", "graph_neural_network"),
        ("graph neural network", "graph_neural_network"),
        ("large language models", "large_language_model"),
        ("large language model", "large_language_model"),
        ("randomized controlled trials", "randomized_controlled_trial"),
        ("randomized controlled trial", "randomized_controlled_trial"),
        ("machine learning potentials", "machine_learning_potential"),
        ("machine learning potential", "machine_learning_potential"),
        ("spin-group symmetry", "spin_group_symmetry"),
        ("band splitting", "altermagnetic_band_splitting"),
        ("altermagnetic tunnel junction", "altermagnetic_tunnel_junction"),
        ("tunnel magnetoresistance", "tunnel_magnetoresistance"),
        ("water splitting", "water_splitting"),
        ("noble metal", "noble_metal"),
        ("transition metal", "transition_metal"),
        ("iridium oxide", "iridium_oxide"),
        ("ruthenium oxide", "ruthenium_oxide"),
    ],
    key=lambda kv: len(kv[0]),
    reverse=True,
)
DEFAULT_ABBREV_CONCEPT_MAP: dict[str, str] = {
    "oer": "oxygen_evolution_reaction",
    "her": "hydrogen_evolution_reaction",
    "scfa": "short_chain_fatty_acid",
    "scfas": "short_chain_fatty_acid",
    "ibd": "inflammatory_bowel_disease",
    "hea": "high_entropy_alloy",
    "heas": "high_entropy_alloy",
    "heo": "high_entropy_oxide",
    "dft": "density_functional_theory",
    "sac": "single_atom_catalyst",
    "sacs": "single_atom_catalyst",
    "gnn": "graph_neural_network",
    "gnns": "graph_neural_network",
    "llm": "large_language_model",
    "llms": "large_language_model",
    "rct": "randomized_controlled_trial",
    "rcts": "randomized_controlled_trial",
    "pemwe": "pem_water_electrolysis",
    "aem": "anion_exchange_membrane",
    "tmr": "tunnel_magnetoresistance",
}
# Context-disambiguated homonyms (e.g., PEM = proton exchange membrane in electrochemistry vs post-exertional malaise in medicine)
HOMONYM_CONTEXT_MAP: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "pem": [
        (re.compile(r"\b(?:electrolys|catalyst|membrane|oer|her|iridium|anode|acidic)\b", re.I), "proton_exchange_membrane"),
        (re.compile(r"\b(?:fatigue|malaise|cfs|patients?|symptom|clinical)\b", re.I), "post_exertional_malaise"),
    ],
}


def normalize_concept_terms(
    text: str,
    *,
    aliases: dict[str, str] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Longest-phrase pre-tokenization canonical concept_id mapping with homonym disambiguation log."""
    low = (text or "").lower()
    mappings: list[dict[str, str]] = []
    extra = sorted(
        [(k.lower(), v.lower().replace(" ", "_")) for k, v in (aliases or {}).items() if " " in k],
        key=lambda kv: len(kv[0]),
        reverse=True,
    )
    for phrase, cid in extra + CANONICAL_CONCEPT_PHRASES:
        if phrase in low:
            pat = re.compile(r"(?<![a-z0-9-])" + re.escape(phrase) + r"(?![a-z0-9-])")
            if pat.search(low):
                low = pat.sub(cid, low)
                mappings.append({"surface": phrase, "concept_id": cid, "rule": "longest_phrase"})
    for hom, rules in HOMONYM_CONTEXT_MAP.items():
        if re.search(rf"\b{hom}\b", low):
            resolved = ""
            for ctx_re, cid in rules:
                if ctx_re.search(low):
                    resolved = cid
                    break
            if resolved:
                low = re.sub(rf"\b{hom}\b", resolved, low)
                mappings.append({"surface": hom, "concept_id": resolved, "rule": "homonym_context"})
    return low, mappings


def tokenize_terms(text: str, *, include_bigrams: bool = True, aliases: dict[str, str] | None = None) -> list[str]:
    """Extract normalized domain concept_ids, unigrams, and optional bigrams with longest-phrase & plural folding."""
    folded_text, _ = normalize_concept_terms(text, aliases=aliases)
    raw_tokens = re.findall(r"[a-z][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,6}", folded_text)
    alias_map = dict(DEFAULT_ABBREV_CONCEPT_MAP)
    for k, v in (aliases or {}).items():
        alias_map[k.lower()] = v.lower().replace(" ", "_")
    unigrams: list[str] = []
    for tok in raw_tokens:
        t = tok.strip("-_")
        if len(t) < 3 or t in STOPWORDS or t.isdigit():
            continue
        t = alias_map.get(t, t)
        if "_" not in t and len(t) > 4 and t.endswith("s") and not t.endswith(("ss", "us", "is", "ics")):
            t = t[:-1]
            t = alias_map.get(t, t)
        if t in STOPWORDS:
            continue
        unigrams.append(t)
    if not include_bigrams:
        return unigrams
    bigrams: list[str] = []
    for i in range(len(unigrams) - 1):
        if "_" in unigrams[i] or "_" in unigrams[i + 1]:
            continue
        bg = f"{unigrams[i]} {unigrams[i + 1]}"
        bg = alias_map.get(bg, bg)
        if unigrams[i] != unigrams[i + 1]:
            bigrams.append(bg)
    return unigrams + bigrams


def run_polarity_regression_suite() -> dict[str, Any]:
    """Built-in 4-case polarity/negation/comparability + concept-homonym regression verification."""
    cases = [
        ("no_adverse_events", "No serious adverse events were observed in patients receiving butyrate.", "supportive"),
        ("mortality_increased", "All-cause mortality was significantly increased in the high-dose cohort (p=0.004).", "opposing_or_null"),
        ("intro_prior_null", "Previous studies reported no significant effect of fiber supplementation on remission.", "UNKNOWN"),
    ]
    results = []
    ok = True
    for name, sent, expected in cases:
        got, meta = _classify_passage_polarity(sent)
        passed = got == expected
        ok = ok and passed
        results.append({"case": name, "expected": expected, "got": got, "passed": passed, "meta": meta})
    # Case 4: cross-endpoint pseudo-conflict MUST NOT emit a comparable conflict edge!
    cross = build_comparable_conflict_edges([
        {"record_id": "R1", "locator": "r1#p2:S1", "direction": "supportive", "entity": "patient", "endpoint": "survival rate"},
        {"record_id": "R2", "locator": "r2#p4:S2", "direction": "opposing_or_null", "entity": "patient", "endpoint": "toxicity"},
    ])
    case4_ok = len(cross["comparable_conflict_edges"]) == 0 and len(cross["noncomparable_unknown_pairs"]) == 1
    ok = ok and case4_ok
    results.append({"case": "cross_endpoint_pseudo_conflict", "expected": "0_edges_1_unknown", "passed": case4_ok})
    # Case 5: concept_id longest-phrase & homonym disambiguation
    t_elec = tokenize_terms("OER activity on PEM electrolyzer membrane", include_bigrams=False)
    t_med = tokenize_terms("Patients with CFS reported PEM fatigue symptoms", include_bigrams=False)
    case5_ok = ("oxygen_evolution_reaction" in t_elec and "proton_exchange_membrane" in t_elec and "post_exertional_malaise" in t_med)
    ok = ok and case5_ok
    results.append({"case": "concept_id_homonym_disambiguation", "passed": case5_ok})
    return {"ok": ok, "cases": results}


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
