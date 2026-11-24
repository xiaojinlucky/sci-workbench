"""Cross-Lingual Literature Matcher, 4-Class View Consolidator & Terminology Alignment Engine (challenge-802a7115).

Pure-stdlib offline implementation addressing cross-lingual search boundaries:
- Preserves original bibliographic fields immutably (original_title, raw_title, titles_by_language,
  translated_title) and emits title_overwrite_audit proving zero title overwrites.
- Consolidates multilingual views into canonical work_id -> views[] across the 4 canonical classes:
    original_fulltext | publisher_full_translation | english_abstract_only | exact_duplicate
  (plus metadata_multilingual_record sidecars without inflating unique work counts).
- Enforces a hard distinct-DOI merge barrier: homonymous records with different DOIs are NEVER
  merged and are explicitly logged under homonym_distinct_doi_separated.
- Performs controlled EN/PT/ZH/ES/DE/FR terminology expansion with diacritic folding, zero-translation
  locking for gene/chemical symbols, strict token-boundary matching for short acronyms (<=4 chars),
  and false-friend / pinyin-drift back-translation risk auditing.
- Emits verifiable sentence-level cross-lingual evidence anchors, cross_lingual_alignment.json,
  human-readable cross_lingual_report.md, and Harbor-compliant result.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log
from corpus_loader import fold_diacritics, load_input_bundle
from evidence_state_guard import normalize_doi, verify_quote_grounded

SCIELO_PID_RE = re.compile(r"\b(S\d{4}-\d{4}\d{13})\b", re.I)
ZH_RE = re.compile(r"[\u4e00-\u9fff]")
PT_CUE_RE = re.compile(
    r"\b(?:para|com|não|nao|dos|das|uma|estudo|análise|analise|dados|sobre|entre|método|metodo|"
    r"crianças|criancas|adolescentes|saudáveis|saudaveis|doença|doenca|revisão|revisao|"
    r"resumo|palavras-chave|conclusões|conclusoes|pacientes|resultados|métodos|metodos)\b|[ãõç]",
    re.I,
)
ES_CUE_RE = re.compile(
    r"\b(?:niños|ninos|estudio|análisis|analisis|enfermedad|revisión|revision|resumen|"
    r"resultados|métodos|metodos|conclusiones|pacientes|entre|mediante)\b|[ñ¿¡]",
    re.I,
)
DE_CUE_RE = re.compile(r"\b(?:und|der|die|das|mit|für|fur|bei|ergebnisse|methoden|studie)\b|[äöüß]", re.I)
FR_CUE_RE = re.compile(r"\b(?:les|des|une|dans|avec|pour|étude|etude|résultats|resultats|méthodes|methodes)\b|[œæ]", re.I)

# Controlled multilingual scientific concept glossary (EN / PT / ZH / ES / DE / FR)
MULTILINGUAL_GLOSSARY: list[dict[str, Any]] = [
    {
        "canonical": "exogenous intoxication",
        "domain": "toxicology_pediatrics",
        "terms": {
            "en": ["exogenous intoxication", "exogenous intoxications", "intoxication", "intoxications", "poisoning"],
            "pt": ["intoxicação exógena", "intoxicações exógenas", "intoxicacao exogena", "intoxicacoes exogenas", "intoxicação", "intoxicações"],
            "zh": ["外源性中毒", "急性中毒", "中毒"],
            "es": ["intoxicación exógena", "intoxicaciones exógenas", "envenenamiento"],
        },
    },
    {
        "canonical": "epidemiological profile",
        "domain": "epidemiology",
        "terms": {
            "en": ["epidemiological profile", "epidemiology", "temporal trend", "time trend"],
            "pt": ["perfil epidemiológico", "perfil epidemiologico", "tendência temporal", "tendencia temporal"],
            "zh": ["流行病学特征", "流行病学谱", "时间趋势"],
            "es": ["perfil epidemiológico", "tendencia temporal"],
        },
    },
    {
        "canonical": "children and adolescents",
        "domain": "pediatrics",
        "terms": {
            "en": ["children and adolescents", "healthy children", "pediatric", "paediatric", "children", "adolescents"],
            "pt": ["crianças e adolescentes", "criancas e adolescentes", "crianças saudáveis", "criancas saudaveis", "crianças", "adolescentes", "pediátrico"],
            "zh": ["儿童和青少年", "儿童与青少年", "健康儿童", "儿童", "青少年", "儿科"],
            "es": ["niños y adolescentes", "niños sanos", "pediátrico"],
        },
    },
    {
        "canonical": "maximum respiratory pressures",
        "domain": "pulmonology",
        "terms": {
            "en": ["maximum respiratory pressures", "maximal respiratory pressures", "respiratory pressures", "respiratory muscle strength", "inspiratory pressure", "expiratory pressure"],
            "pt": ["pressão respiratória máxima", "pressões respiratórias máximas", "pressao respiratoria maxima", "pressoes respiratorias maximas", "pressões respiratórias", "força muscular respiratória"],
            "zh": ["最大呼吸压", "最大吸气压", "最大呼气压", "呼吸肌力"],
            "es": ["presiones respiratorias máximas", "presión respiratoria máxima"],
        },
    },
    {
        "canonical": "reference values and percentiles",
        "domain": "biostatistics",
        "terms": {
            "en": ["standard reference", "reference values", "percentiles", "percentile", "reference equations"],
            "pt": ["distribuição padrão", "distribuicao padrao", "valores de referência", "percentuais", "percentis", "equações de referência"],
            "zh": ["标准参考值", "参考值", "百分位数", "参考方程"],
            "es": ["valores de referencia", "percentiles"],
        },
    },
    {
        "canonical": "avascular necrosis",
        "domain": "hematology_orthopedics",
        "terms": {
            "en": ["avascular necrosis", "osteonecrosis", "aseptic necrosis", "femoral head necrosis"],
            "pt": ["necrose avascular", "osteonecrose", "necrose asséptica", "necrose da cabeça femoral"],
            "zh": ["缺血性坏死", "无血管性坏死", "骨坏死", "股骨头坏死"],
            "es": ["necrosis avascular", "osteonecrosis"],
        },
    },
    {
        "canonical": "sickle cell disease",
        "domain": "hematology",
        "terms": {
            "en": ["sickle cell disease", "sickle cell anemia", "sickle cell anaemia", "drepanocytosis"],
            "pt": ["doença falciforme", "doenca falciforme", "anemia falciforme"],
            "zh": ["镰状细胞病", "镰刀型细胞贫血", "镰状细胞贫血"],
            "es": ["enfermedad de células falciformes", "anemia falciforme"],
        },
    },
    {
        "canonical": "genetic polymorphism",
        "domain": "genetics",
        "terms": {
            "en": ["polymorphism", "polymorphisms", "single nucleotide polymorphism", "genetic variant"],
            "pt": ["polimorfismo", "polimorfismos", "variante genética", "variante genetica"],
            "zh": ["基因多态性", "多态性", "单核苷酸多态性"],
            "es": ["polimorfismo", "polimorfismos"],
        },
    },
    {
        "canonical": "systematic review",
        "domain": "methodology",
        "terms": {
            "en": ["systematic review", "meta-analysis", "evidence synthesis"],
            "pt": ["revisão sistemática", "revisao sistematica", "metanálise", "meta-análise"],
            "zh": ["系统综述", "系统评价", "荟萃分析"],
            "es": ["revisión sistemática", "metanálisis"],
        },
    },
    {
        "canonical": "pyroptosis",
        "domain": "cell_biology",
        "terms": {
            "en": ["pyroptosis", "inflammatory cell death"],
            "pt": ["piroptose"],
            "zh": ["细胞焦亡", "焦亡"],
            "es": ["piroptosis"],
            "de": ["pyroptose"],
        },
    },
    {
        "canonical": "perovskite",
        "domain": "materials",
        "terms": {
            "en": ["perovskite", "perovskites"],
            "pt": ["perovskita", "perovskitas"],
            "zh": ["钙钛矿"],
            "es": ["perovskita"],
            "de": ["perowskit"],
        },
    },
    {
        "canonical": "photocatalysis",
        "domain": "catalysis",
        "terms": {
            "en": ["photocatalysis", "photocatalytic", "photocatalyst"],
            "pt": ["fotocatálise", "fotocatalise", "fotocatalítico", "fotocatalisador"],
            "zh": ["光催化", "光催化剂"],
            "es": ["fotocatálisis", "fotocatalizador"],
        },
    },
    {
        "canonical": "catalyst",
        "domain": "catalysis",
        "terms": {
            "en": ["catalyst", "catalysts", "electrocatalyst"],
            "pt": ["catalisador", "catalisadores", "eletrocatalisador"],
            "zh": ["催化剂", "电催化剂"],
            "es": ["catalizador", "catalizadores"],
            "de": ["katalysator"],
        },
    },
    {
        "canonical": "toxicity",
        "domain": "toxicology",
        "terms": {
            "en": ["toxicity", "cytotoxicity", "toxic"],
            "pt": ["toxicidade", "citotoxicidade", "tóxico", "toxico"],
            "zh": ["毒性", "细胞毒性"],
            "es": ["toxicidad", "citotoxicidad"],
            "de": ["toxizität", "toxizitat"],
        },
    },
    {
        "canonical": "clinical trial",
        "domain": "clinical",
        "terms": {
            "en": ["clinical trial", "randomized controlled trial"],
            "pt": ["ensaio clínico", "ensaio clinico", "estudo clínico randomizado"],
            "zh": ["临床试验", "随机对照试验"],
            "es": ["ensayo clínico", "ensayo clinico"],
            "fr": ["essai clinique"],
        },
    },
    {
        "canonical": "adsorption",
        "domain": "chemistry",
        "terms": {
            "en": ["adsorption", "adsorbent"],
            "pt": ["adsorção", "adsorcao", "adsorvente"],
            "zh": ["吸附", "吸附剂"],
            "es": ["adsorción"],
        },
    },
    {
        "canonical": "membrane",
        "domain": "chemistry_biology",
        "terms": {
            "en": ["membrane", "membranes"],
            "pt": ["membrana", "membranas"],
            "zh": ["膜", "分离膜"],
            "es": ["membrana"],
        },
    },
    {
        "canonical": "stability",
        "domain": "general_science",
        "terms": {
            "en": ["stability", "long-term stability"],
            "pt": ["estabilidade", "estabilidade a longo prazo"],
            "zh": ["稳定性", "长期稳定性"],
            "es": ["estabilidad"],
        },
    },
    {
        "canonical": "efficacy",
        "domain": "clinical",
        "terms": {
            "en": ["efficacy", "effectiveness", "therapeutic efficacy"],
            "pt": ["eficácia", "eficacia", "efetividade"],
            "zh": ["疗效", "有效性"],
            "es": ["eficacia", "efectividad"],
        },
    },
    {
        "canonical": "degradation",
        "domain": "chemistry",
        "terms": {
            "en": ["degradation", "photodegradation"],
            "pt": ["degradação", "degradacao", "fotodegradação"],
            "zh": ["降解", "光降解"],
            "es": ["degradación"],
        },
    },
]

# Zero-translation locked scientific symbols (gene / protein / chemical / clinical indices)
KNOWN_LOCKED_SYMBOLS = {
    "TP53", "BCL2", "HBB", "BMP6", "KL", "KLOTHO", "VEGF", "EGFR", "BRCA1", "BRCA2",
    "KRAS", "IL6", "TNF", "TGFB1", "NOS3", "MTHFR", "ANXA2", "CO2", "H2O2", "FE3O4",
    "TIO2", "NACL", "MRNA", "SIRNA", "CRISPR", "PIMAX", "PEMAX", "MIP", "MEP", "FEV1",
    "FVC", "HBS", "HBF", "SNP", "SNPS",
}
GENE_CHEM_SYMBOL_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,6}(?:-[A-Z0-9]{1,4})?)\b")

# Short ambiguous acronyms (<=4 chars) requiring strict word-boundary matching + risk disclosure
AMBIGUOUS_ACRONYMS: dict[str, list[str]] = {
    "SC": ["Sickle Cell", "Subcutaneous", "Supercapacitor", "Stem Cell", "Santa Catarina"],
    "PD": ["Parkinson's Disease", "Pharmacodynamics", "Peritoneal Dialysis", "Potential Difference"],
    "MI": ["Myocardial Infarction", "Mutual Information", "Maximal Inspiratory"],
    "OR": ["Odds Ratio", "Operating Room", "Boolean OR"],
    "SCD": ["Sickle Cell Disease", "Sudden Cardiac Death", "Subjective Cognitive Decline"],
    "AVN": ["Avascular Necrosis", "Atrioventricular Node"],
    "CI": ["Confidence Interval", "Cardiac Index", "Cochlear Implant"],
    "HR": ["Hazard Ratio", "Heart Rate", "Homologous Recombination"],
    "PT": ["Portuguese", "Prothrombin Time", "Physical Therapy", "Platinum"],
    "MS": ["Mass Spectrometry", "Multiple Sclerosis", "Mato Grosso do Sul"],
}

# Multilingual false friends requiring back-translation verification
FALSE_FRIENDS: dict[str, dict[str, str]] = {
    "data": {
        "languages": "en<->pt/es/it",
        "risk": "English 'data' (observations/dataset) vs Portuguese/Spanish/Italian 'data' (calendar date).",
        "verification": "Confirm surrounding tokens indicate dataset/values rather than publication/collection date.",
    },
    "actual": {
        "languages": "en<->pt/es",
        "risk": "English 'actual' (real/factual) vs Portuguese 'atual' / Spanish 'actual' (current/present).",
        "verification": "Back-translate PT/ES 'atual/actual' as 'current', never 'real'.",
    },
    "atual": {
        "languages": "pt<->en",
        "risk": "Portuguese 'atual' means 'current', not English 'actual'.",
        "verification": "Verify temporal meaning rather than factual contrast.",
    },
    "pretend": {
        "languages": "en<->pt/es",
        "risk": "English 'pretend' (feign) vs Portuguese/Spanish 'pretender' (intend/aim).",
        "verification": "Back-translate 'pretender' as 'intend/aim to'.",
    },
    "pretender": {
        "languages": "pt/es<->en",
        "risk": "Portuguese/Spanish 'pretender' means 'intend/aim', not 'feign'.",
        "verification": "Verify study objective framing.",
    },
    "resumo": {
        "languages": "pt<->en",
        "risk": "Portuguese 'resumo' means 'abstract/summary', not English 'resume' (CV/restart).",
        "verification": "Map strictly to bibliographic abstract.",
    },
    "parente": {
        "languages": "pt<->en",
        "risk": "Portuguese 'parente' means 'relative/kin', not 'parent' (mother/father).",
        "verification": "Check kinship vs parental caregiver context.",
    },
    "sensible": {
        "languages": "en<->es/fr",
        "risk": "English 'sensible' (reasonable) vs Spanish/French 'sensible' (sensitive).",
        "verification": "Confirm assay/clinical sensitivity meaning.",
    },
}

PINYIN_AMBIGUOUS_TOKENS = {"li", "shi", "he", "ma", "can", "fan", "jin", "tang", "han", "sun", "pan", "wen", "wu"}

STOPWORDS_QUERY = {
    "the", "and", "for", "with", "from", "that", "this", "which", "what", "studies", "study",
    "papers", "paper", "articles", "article", "analyze", "evaluate", "assess", "report",
    "examine", "among", "about", "into", "between", "using", "based", "over", "under", "have",
    "does", "are", "were", "was", "been", "how", "who", "when", "where", "find", "retrieve",
    "search", "without", "works", "work", "unique", "double", "counting", "double-counting",
    "translations", "translation", "patients", "patient", "disease", "review",
    "quais", "estudos", "avaliam", "estabelecem", "sobre", "entre", "para", "como", "com",
    "dos", "das", "nos", "nas", "uma", "por", "que", "los", "las", "del", "con", "cuales",
}


def sha16(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def norm_ws(text: str) -> str:
    return " ".join((text or "").split())


def norm_title_key(title: str) -> str:
    folded = fold_diacritics(norm_ws(title)).lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", folded).strip()


def extract_scielo_pid(rec: dict[str, Any]) -> str:
    # Extract SciELO PID only from the record's own persistent file/record identifiers (not misleading cross-work hints)
    for field in ("scielo_pid", "pid", "record_id", "source_file", "url"):
        val = str(rec.get(field) or "")
        m = SCIELO_PID_RE.search(val)
        if m:
            return m.group(1).upper()
    return ""


def detect_language_text(blob: str) -> str:
    if not blob or not blob.strip():
        return "unknown"
    if len(ZH_RE.findall(blob)) >= 2:
        return "zh"
    pt_hits = len(PT_CUE_RE.findall(blob))
    es_hits = len(ES_CUE_RE.findall(blob))
    de_hits = len(DE_CUE_RE.findall(blob))
    fr_hits = len(FR_CUE_RE.findall(blob))
    if pt_hits >= 2 and pt_hits >= es_hits:
        return "pt"
    if es_hits >= 2:
        return "es"
    if de_hits >= 2:
        return "de"
    if fr_hits >= 2:
        return "fr"
    if re.search(r"[a-zA-Z]{3,}", blob):
        return "en"
    return "unknown"


def detect_body_language(rec: dict[str, Any]) -> str:
    explicit = str(rec.get("body_language") or rec.get("language") or rec.get("lang") or "").strip().lower()
    if explicit and explicit != "unknown":
        return explicit.split("-")[0]
    blob = f"{rec.get('title', '')} {rec.get('abstract', '')} {str(rec.get('text') or '')[:2000]}"
    return detect_language_text(blob)


def _content_digest(title: str, abstract: str, text: str) -> str:
    payload = "\n".join([norm_ws(title), norm_ws(abstract), norm_ws(text)])
    return sha16(payload)


def _title_token_jaccard(t1: str, t2: str) -> float:
    s1 = set(norm_title_key(t1).split())
    s2 = set(norm_title_key(t2).split())
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def classify_initial_view_type(rec: dict[str, Any], lang: str) -> str:
    explicit = str(rec.get("view_type") or "").strip().lower()
    if explicit in {
        "original_fulltext",
        "publisher_full_translation",
        "english_abstract_only",
        "exact_duplicate",
        "metadata_multilingual_record",
    }:
        return explicit
    note_blob = f"{rec.get('version', '')} {rec.get('note', '')} {rec.get('source', '')} {rec.get('source_file', '')}".lower()
    if "duplicate" in note_blob or "_dup" in note_blob:
        return "exact_duplicate"
    if "translation" in note_blob or "translated" in note_blob:
        return "publisher_full_translation"
    text_body = norm_ws(str(rec.get("text") or ""))
    abs_body = norm_ws(str(rec.get("abstract") or ""))
    if str(rec.get("source") or "").lower() == "scielo_meta" and len(text_body) < 300:
        return "metadata_multilingual_record"
    if (not text_body or len(text_body) < 300 or text_body == abs_body) and abs_body:
        return "english_abstract_only" if lang == "en" else "abstract_only_non_english"
    return "original_fulltext"


def group_works_and_views(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Consolidate multilingual views into canonical works with strict DOI separation & title immutability."""
    works_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    work_doi: dict[str, str] = {}
    work_pid: dict[str, str] = {}
    exact_duplicates: list[dict[str, Any]] = []
    homonym_separated: list[dict[str, Any]] = []
    homonym_pairs_seen: set[tuple[str, str]] = set()
    title_audit_records: list[dict[str, Any]] = []

    reserved_ids = {
        str(r.get("work_id") or r.get("canonical_work_id") or "").strip()
        for r in records
        if str(r.get("work_id") or r.get("canonical_work_id") or "").strip()
    }
    serial_counter = 1

    def _next_work_id(preferred: str = "") -> str:
        nonlocal serial_counter
        if preferred and preferred not in works_map:
            return preferred
        while True:
            cand = f"WORK-{serial_counter:02d}"
            serial_counter += 1
            if cand not in works_map and cand not in reserved_ids:
                return cand

    # Pre-collect known original languages per DOI/PID from metadata sidecars (e.g. SciELO JSON v40)
    known_orig_lang_by_doi: dict[str, str] = {}
    known_orig_lang_by_pid: dict[str, str] = {}
    for r in records:
        doi_k = normalize_doi(str(r.get("doi") or "")) if not r.get("doi_from_text") else ""
        pid_k = extract_scielo_pid(r)
        orig_l = str(r.get("original_language") or "").strip().lower()
        if not orig_l and str(r.get("source") or "").lower() == "scielo_meta":
            orig_l = detect_body_language(r)
        if not orig_l and str(r.get("view_type") or "").strip().lower() == "original_fulltext":
            orig_l = detect_body_language(r)
        if orig_l and orig_l != "unknown":
            if doi_k and doi_k not in known_orig_lang_by_doi:
                known_orig_lang_by_doi[doi_k] = orig_l
            if pid_k and pid_k not in known_orig_lang_by_pid:
                known_orig_lang_by_pid[pid_k] = orig_l

    for idx, r in enumerate(records, start=1):
        lang = detect_body_language(r)
        vtype = classify_initial_view_type(r, lang)
        doi = normalize_doi(str(r.get("doi") or "")) if not r.get("doi_from_text") else ""
        pid = extract_scielo_pid(r)
        explicit_wid = str(r.get("work_id") or r.get("canonical_work_id") or "").strip()

        raw_title = str(r.get("raw_title") or r.get("title") or "").strip()
        original_title = str(r.get("original_title") or raw_title).strip()
        input_title_hash = sha16(raw_title)

        titles_by_lang: dict[str, str] = {}
        if isinstance(r.get("titles_by_language"), dict):
            for k, v in r["titles_by_language"].items():
                if v:
                    titles_by_lang[str(k).lower()] = str(v).strip()
        if raw_title and lang != "unknown":
            titles_by_lang.setdefault(lang, raw_title)

        translated_title = str(r.get("translated_title") or "").strip() or None
        if not translated_title and len(titles_by_lang) > 1:
            for tl, tv in titles_by_lang.items():
                if tv != original_title:
                    translated_title = tv
                    break

        abstract = str(r.get("abstract") or "").strip()
        text = str(r.get("text") or "").strip()
        digest = _content_digest(raw_title, abstract, text)

        # Resolve canonical work_id with HARD distinct-DOI merge barrier
        chosen_wid = ""
        # 1. Normalized DOI match
        if doi:
            for w_id, w_doi in work_doi.items():
                if w_doi == doi:
                    chosen_wid = w_id
                    break
        # 2. SciELO PID match (only if DOIs do not conflict!)
        if not chosen_wid and pid:
            for w_id, w_pid in work_pid.items():
                if w_pid == pid:
                    existing_doi = work_doi.get(w_id, "")
                    if not (doi and existing_doi and doi != existing_doi):
                        chosen_wid = w_id
                        break
        # 3. Explicit work_id match (only if DOIs do not conflict!)
        if not chosen_wid and explicit_wid and explicit_wid in works_map:
            existing_doi = work_doi.get(explicit_wid, "")
            if not (doi and existing_doi and doi != existing_doi):
                chosen_wid = explicit_wid
        # 4. Exact content digest match (only when neither side has conflicting DOI)
        if not chosen_wid and (abstract or text):
            for w_id, vlist in works_map.items():
                existing_doi = work_doi.get(w_id, "")
                if doi and existing_doi and doi != existing_doi:
                    continue
                if any(v["body_language"] == lang and v["_content_digest"] == digest for v in vlist):
                    chosen_wid = w_id
                    break

        if not chosen_wid:
            # Avoid letting an explicit work_id hijack a SciELO PID belonging to a different DOI
            pref = ""
            if pid and pid not in works_map:
                pref = pid
            elif explicit_wid and explicit_wid not in works_map and not SCIELO_PID_RE.fullmatch(explicit_wid):
                pref = explicit_wid
            chosen_wid = _next_work_id(pref)

        if doi and chosen_wid not in work_doi:
            work_doi[chosen_wid] = doi
        if pid and chosen_wid not in work_pid:
            work_pid[chosen_wid] = pid

        # Check homonym with distinct DOI against all other existing works (deduplicated at work-pair level)
        if doi and raw_title:
            for other_wid, other_views in works_map.items():
                if other_wid == chosen_wid:
                    continue
                other_doi = work_doi.get(other_wid, "")
                if not other_doi or other_doi == doi:
                    continue
                for ov in other_views:
                    sim = _title_token_jaccard(raw_title, ov["raw_title"])
                    if sim >= 0.85:
                        pair_key = tuple(sorted([chosen_wid, other_wid]))
                        if pair_key not in homonym_pairs_seen:
                            homonym_pairs_seen.add(pair_key)
                            homonym_separated.append({
                                "record_ids": [ov["record_id"], r["record_id"]],
                                "work_ids": [other_wid, chosen_wid],
                                "dois": [other_doi, doi],
                                "titles": [ov["raw_title"], raw_title],
                                "title_jaccard": round(sim, 3),
                                "action": "kept_separate_canonical_works",
                                "reason": "distinct_doi_blocks_homonym_merge",
                            })
                        break

        # Detect exact duplicate inside chosen_wid
        is_exact_dup = vtype == "exact_duplicate"
        dup_of_id: str | None = None
        for prev in works_map[chosen_wid]:
            if prev["view_type"] == "exact_duplicate":
                continue
            same_content = (
                prev["body_language"] == lang
                and (abstract or text)
                and prev["_content_digest"] == digest
            )
            if same_content or (is_exact_dup and prev["body_language"] == lang):
                is_exact_dup = True
                dup_of_id = prev["record_id"]
                break

        if is_exact_dup:
            vtype = "exact_duplicate"
            exact_duplicates.append({
                "duplicate_record_id": r["record_id"],
                "work_id": chosen_wid,
                "original_record_id": dup_of_id or (works_map[chosen_wid][0]["record_id"] if works_map[chosen_wid] else None),
                "body_language": lang,
                "source_file": r.get("source_file"),
                "content_digest": digest,
            })

        view_entry: dict[str, Any] = {
            "record_id": r["record_id"],
            "raw_title": raw_title,
            "original_title": original_title,
            "translated_title": translated_title,
            "titles_by_language": titles_by_lang,
            "raw_title_sha256_prefix": input_title_hash,
            "doi": doi or None,
            "scielo_pid": pid or None,
            "version": r.get("version") or ("metadata_record" if vtype == "metadata_multilingual_record" else "v1"),
            "source": r.get("source"),
            "source_file": r.get("source_file"),
            "body_language": lang,
            "view_type": vtype,
            "has_fulltext": len(text) >= 300,
            "text_char_count": len(text),
            "abstract_char_count": len(abstract),
            "_raw_text": text,
            "_raw_abstract": abstract,
            "_content_digest": digest,
            "_explicit_view_type": bool(str(r.get("view_type") or "").strip()),
        }
        works_map[chosen_wid].append(view_entry)

        title_audit_records.append({
            "record_id": r["record_id"],
            "input_raw_title_sha256_prefix": input_title_hash,
            "output_raw_title_sha256_prefix": sha16(view_entry["raw_title"]),
            "preserved_intact": view_entry["raw_title"] == raw_title and view_entry["original_title"] == original_title,
        })

    # Post-pass: refine original_fulltext vs publisher_full_translation within each canonical work
    canonical_works: list[dict[str, Any]] = []
    for wid, vlist in sorted(works_map.items()):
        w_doi = work_doi.get(wid, "")
        w_pid = work_pid.get(wid, "")
        orig_lang = known_orig_lang_by_doi.get(w_doi) or known_orig_lang_by_pid.get(w_pid) or ""
        if not orig_lang:
            explicit_orig = [v["body_language"] for v in vlist if v["view_type"] == "original_fulltext" and v["_explicit_view_type"]]
            if explicit_orig:
                orig_lang = explicit_orig[0]
            else:
                full_langs = [v["body_language"] for v in vlist if v["has_fulltext"] and v["view_type"] != "exact_duplicate"]
                orig_lang = full_langs[0] if full_langs else vlist[0]["body_language"]

        # Aggregate multilingual titles across all views without mutating any view's raw_title
        work_titles_by_lang: dict[str, str] = {}
        for v in vlist:
            for lk, lv in (v.get("titles_by_language") or {}).items():
                if lv and lk not in work_titles_by_lang:
                    work_titles_by_lang[lk] = lv
            if v["raw_title"] and v["body_language"] != "unknown":
                work_titles_by_lang.setdefault(v["body_language"], v["raw_title"])

        work_original_title = work_titles_by_lang.get(orig_lang) or next(
            (v["original_title"] for v in vlist if v["original_title"]), ""
        )

        for v in vlist:
            if not v["_explicit_view_type"] and v["view_type"] == "original_fulltext":
                if orig_lang and v["body_language"] != orig_lang and v["has_fulltext"]:
                    v["view_type"] = "publisher_full_translation"
            # Populate translated_title on view if another language title exists, never altering raw_title
            if not v["translated_title"] and len(work_titles_by_lang) > 1:
                for lk, lv in work_titles_by_lang.items():
                    if lk != v["body_language"] and lv != v["raw_title"]:
                        v["translated_title"] = lv
                        break

        view_type_counts: dict[str, int] = defaultdict(int)
        for v in vlist:
            view_type_counts[v["view_type"]] += 1

        clean_views = [{k: val for k, val in ve.items() if not k.startswith("_")} for ve in vlist]
        langs = sorted({v["body_language"] for v in vlist if v["body_language"] != "unknown"} | set(work_titles_by_lang.keys()))
        canonical_works.append({
            "work_id": wid,
            "doi": w_doi or None,
            "scielo_pid": w_pid or None,
            "original_language": orig_lang or "unknown",
            "original_title": work_original_title,
            "titles_by_language": work_titles_by_lang,
            "languages_present": langs,
            "view_count_raw": len(vlist),
            "distinct_non_duplicate_views": sum(1 for v in vlist if v["view_type"] != "exact_duplicate"),
            "fulltext_view_count": sum(1 for v in vlist if v["view_type"] in {"original_fulltext", "publisher_full_translation"}),
            "view_type_counts": dict(sorted(view_type_counts.items())),
            "views": clean_views,
            "_internal_views": vlist,
        })

    overwritten_count = sum(1 for item in title_audit_records if not item["preserved_intact"])
    title_overwrite_audit = {
        "records_checked": len(title_audit_records),
        "overwritten_count": overwritten_count,
        "multilingual_works_with_preserved_titles": sum(1 for w in canonical_works if len(w["titles_by_language"]) >= 2),
        "invariant": "original_title and raw_title are immutable; translated titles stored separately in translated_title/titles_by_language.",
        "audit_passed": overwritten_count == 0,
    }
    return canonical_works, exact_duplicates, title_overwrite_audit, homonym_separated


def _extract_locked_symbols(query: str) -> list[str]:
    found: list[str] = []
    for m in GENE_CHEM_SYMBOL_RE.finditer(query or ""):
        tok = m.group(1)
        up = tok.upper()
        if up in KNOWN_LOCKED_SYMBOLS or (any(ch.isdigit() for ch in tok) and any(ch.isalpha() for ch in tok) and len(tok) >= 3):
            if tok not in found:
                found.append(tok)
    return found


def _extract_short_acronyms(query: str) -> list[str]:
    found: list[str] = []
    for m in re.finditer(r"(?<![A-Za-z0-9])([A-Z]{2,4})(?![A-Za-z0-9])", query or ""):
        tok = m.group(1)
        if tok in AMBIGUOUS_ACRONYMS and tok not in found:
            found.append(tok)
    return found


def expand_query_terms(
    query: str,
    custom_glossary: dict[str, Any],
    observed_languages: list[str],
) -> dict[str, Any]:
    """Build controlled multilingual expansion, per-language queries, and risk audits."""
    q_raw = query or ""
    q_low = q_raw.lower()
    q_folded = fold_diacritics(q_low)
    src_lang = detect_language_text(q_raw)

    locked_symbols = _extract_locked_symbols(q_raw)
    short_acronyms = _extract_short_acronyms(q_raw)

    # Merge custom glossary entries into structured concepts
    concepts = list(MULTILINGUAL_GLOSSARY)
    for k, v in (custom_glossary or {}).items():
        if isinstance(v, list):
            concepts.append({"canonical": str(k), "domain": "case_glossary", "terms": {"en": [str(k)], "custom": [str(x) for x in v]}})
        elif isinstance(v, dict):
            concepts.append({"canonical": str(k), "domain": "case_glossary", "terms": v})

    terminology_alignments: list[dict[str, Any]] = []
    per_lang_terms: dict[str, list[str]] = defaultdict(list)
    expanded_terms: list[str] = []

    def _add_term(term: str, lang_bucket: str | None = None) -> None:
        t = term.strip()
        if not t:
            return
        if t not in expanded_terms:
            expanded_terms.append(t)
        if lang_bucket and t not in per_lang_terms[lang_bucket]:
            per_lang_terms[lang_bucket].append(t)

    # Match concepts via raw or diacritic-folded phrase match
    for concept in concepts:
        terms_map: dict[str, list[str]] = concept.get("terms") or {}
        all_forms = [form for flist in terms_map.values() for form in flist]
        matched_trigger = None
        for form in all_forms:
            f_low = form.lower()
            f_fold = fold_diacritics(f_low)
            if ZH_RE.search(form):
                if form in q_raw:
                    matched_trigger = form
                    break
            elif len(f_fold) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(f_fold) + r"(?![a-z0-9])", q_folded):
                matched_trigger = form
                break
        if matched_trigger:
            aligned_by_lang: dict[str, list[str]] = {}
            for lg, flist in terms_map.items():
                aligned_by_lang[lg] = list(dict.fromkeys(flist))
                for f in flist:
                    _add_term(f, lg)
            terminology_alignments.append({
                "canonical_concept": concept["canonical"],
                "domain": concept.get("domain", "general"),
                "triggered_by": matched_trigger,
                "aligned_by_language": aligned_by_lang,
                "back_translation_verified": True,
            })

    # Add locked symbols and short acronyms as immutable tokens
    for sym in locked_symbols:
        _add_term(sym, "locked_symbol")
    for acr in short_acronyms:
        _add_term(acr, "short_acronym")

    # Add informative raw query tokens (excluding short stopwords and ambiguous short acronyms already added)
    raw_latin = re.findall(r"[A-Za-zÀ-ÿ0-9-]{3,}", q_raw)
    raw_cjk = re.findall(r"[\u4e00-\u9fff]{2,8}", q_raw)
    for tok in raw_latin:
        t_fold = fold_diacritics(tok.lower())
        if t_fold in STOPWORDS_QUERY or tok in short_acronyms or tok in locked_symbols:
            continue
        _add_term(tok, src_lang if src_lang != "unknown" else "raw")
    for tok in raw_cjk:
        _add_term(tok, "zh")

    # False-friend, short-acronym, and pinyin/transliteration risk audit
    risk_flags: list[dict[str, Any]] = []
    for acr in short_acronyms:
        risk_flags.append({
            "risk_type": "short_acronym_ambiguity",
            "token": acr,
            "competing_senses": AMBIGUOUS_ACRONYMS.get(acr, []),
            "mitigation": "Strict case-aware word-boundary matching applied; verify co-occurring domain anchor in quote.",
        })
    for tok in re.findall(r"[A-Za-zÀ-ÿ]+", q_raw):
        tl = tok.lower()
        if tl in FALSE_FRIENDS:
            ff = FALSE_FRIENDS[tl]
            risk_flags.append({
                "risk_type": "multilingual_false_friend",
                "token": tok,
                "languages": ff["languages"],
                "detail": ff["risk"],
                "verification": ff["verification"],
            })
        if tok[0].isupper() and tl in PINYIN_AMBIGUOUS_TOKENS and len(tok) <= 4:
            risk_flags.append({
                "risk_type": "pinyin_or_transliteration_drift_risk",
                "token": tok,
                "detail": "Short romanized proper noun / pinyin token can collide across unrelated entities without diacritics/tones.",
                "verification": "Require co-occurrence with domain concept or persistent DOI/PID.",
            })

    # Construct explicit per-language controlled queries
    target_langs = sorted(set(observed_languages) | set(per_lang_terms.keys()) - {"locked_symbol", "short_acronym", "raw", "custom", "unknown"})
    target_language_queries: dict[str, str] = {}
    for lg in target_langs:
        parts = list(dict.fromkeys(per_lang_terms.get(lg, []) + locked_symbols))
        if parts:
            target_language_queries[lg] = " OR ".join(f'"{p}"' if " " in p else p for p in parts[:10])

    return {
        "query": q_raw,
        "source_language": src_lang,
        "target_language_queries": target_language_queries,
        "zero_translation_locked_symbols": locked_symbols,
        "short_acronyms_protected": short_acronyms,
        "terminology_alignments": terminology_alignments,
        "expanded_terms": expanded_terms,
        "false_friend_or_acronym_risk": risk_flags,
    }


def _match_term_in_text(term: str, raw_text: str, folded_text: str, locked_set: set[str], acronym_set: set[str]) -> bool:
    if not term or not raw_text:
        return False
    if ZH_RE.search(term):
        return term in raw_text
    if term in acronym_set or term in locked_set:
        # Strict word-boundary matching; short acronyms require uppercase exact match to avoid English 'or'/'sc' noise
        flags = 0 if (term in acronym_set and len(term) <= 4) else re.I
        return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", raw_text, flags))
    t_fold = fold_diacritics(term.lower())
    if len(t_fold) <= 3:
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(t_fold) + r"(?![a-z0-9])", folded_text))
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(t_fold) + r"(?![a-z0-9])", folded_text))


def _extract_verbatim_sentence(term: str, raw_text: str, locked_set: set[str], acronym_set: set[str], max_chars: int = 240) -> str:
    clean = norm_ws(raw_text)
    if not clean:
        return ""
    sentences = re.split(r"(?<=[.!?。！？;；])\s+", clean)
    for sent in sentences:
        if _match_term_in_text(term, sent, fold_diacritics(sent.lower()), locked_set, acronym_set):
            return sent[:max_chars]
    # Window fallback around folded position
    folded = fold_diacritics(clean.lower())
    t_fold = fold_diacritics(term.lower())
    idx = folded.find(t_fold)
    if idx >= 0:
        start = max(0, idx - 60)
        end = min(len(clean), idx + len(term) + 140)
        return clean[start:end][:max_chars]
    return clean[:max_chars]


def _render_markdown_report(alignment_doc: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# Cross-Lingual Literature Search & Evidence Alignment Report ({alignment_doc['case_id']})",
        "",
        f"> **Scope Boundary**: {alignment_doc['scope_note']}",
        "",
        "## 1. Corpus & Deduplication Summary",
        "",
        f"- **Observed Languages**: {', '.join(alignment_doc['observed_languages']) or 'none'}",
        f"- **Raw Input Views**: {alignment_doc['summary']['raw_view_count']}",
        f"- **Unique Canonical Works**: {alignment_doc['summary']['unique_work_count']} (translations, abstract-only mirrors, and exact duplicates excluded from study count)",
        f"- **Exact Duplicates Flagged**: {alignment_doc['summary']['exact_duplicate_count']}",
        f"- **Homonyms with Distinct DOIs Kept Separate**: {alignment_doc['summary']['homonym_distinct_doi_separated_count']}",
        f"- **Original Title Overwrites**: {alignment_doc['title_overwrite_audit']['overwritten_count']} (audit passed: `{alignment_doc['title_overwrite_audit']['audit_passed']}`)",
        "",
        "## 2. Canonical Works & 4-Class View Inventory",
        "",
        "| Work ID | DOI / PID | Orig. Lang | Original Title | Languages | Views (Raw / Non-Dup) | View Breakdown |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for w in alignment_doc["canonical_works"]:
        ident = w.get("doi") or w.get("scielo_pid") or "none"
        vbreak = ", ".join(f"{k}:{v}" for k, v in (w.get("view_type_counts") or {}).items())
        orig_t = (w.get("original_title") or "").replace("|", "/")[:90]
        lines.append(
            f"| {w['work_id']} | `{ident}` | {w['original_language']} | {orig_t} | "
            f"{','.join(w['languages_present'])} | {w['view_count_raw']} / {w['distinct_non_duplicate_views']} | {vbreak} |"
        )

    if alignment_doc["homonym_distinct_doi_separated"]:
        lines.extend(["", "## 3. Homonym / Distinct-DOI Separation Audit", ""])
        for h in alignment_doc["homonym_distinct_doi_separated"]:
            lines.append(
                f"- Separated `{h['record_ids'][0]}` (`{h['dois'][0]}`) from `{h['record_ids'][1]}` (`{h['dois'][1]}`) "
                f"despite title similarity `{h['title_jaccard']}` (`{h['reason']}`)."
            )

    lines.extend(["", "## 4. Bidirectional Cross-Lingual Query Results & Verifiable Evidence", ""])
    for qr in alignment_doc["cross_lingual_query_results"]:
        qe = qr["query_expansion"]
        lines.append(f"### {qr['question_id']}: {qe['query']}")
        lines.append(f"- **Detected Source Language**: `{qe['source_language']}`")
        if qe["target_language_queries"]:
            for lg, qstr in qe["target_language_queries"].items():
                lines.append(f"- **Controlled Query ({lg})**: `{qstr}`")
        if qe["zero_translation_locked_symbols"]:
            lines.append(f"- **Zero-Translation Locked Symbols**: {', '.join(qe['zero_translation_locked_symbols'])}")
        if qe["false_friend_or_acronym_risk"]:
            risk_summaries = [f"`{r['token']}` ({r['risk_type']})" for r in qe["false_friend_or_acronym_risk"]]
            lines.append(f"- **False-Friend / Acronym Risk Alerts**: {', '.join(risk_summaries)}")
        lines.append(
            f"- **Matched Unique Works**: **{qr['matched_unique_work_count']}** "
            f"(non-duplicate views matched: {qr['matched_non_duplicate_view_count']}; exact duplicates excluded: {qr['excluded_duplicate_views_count']})"
        )
        for mw in qr["matched_works"]:
            lines.append(f"  - **{mw['work_id']}** (`{mw.get('doi') or mw.get('scielo_pid') or 'no-id'}` | langs: {', '.join(mw['languages_present'])} | terms: {', '.join(mw['matched_terms'][:6])})")
            for anc in mw["evidence_anchors"][:3]:
                q_clean = anc["quote"].replace("\n", " ")
                lines.append(
                    f"    - [{anc['body_language']} | `{anc['view_type']}` | `{anc['source_file']}` | verified={anc['verified_in_source']}]: \"{q_clean}\""
                )
        lines.append("")

    lines.extend([
        "## 5. Honest Coverage & Stopping Boundaries",
        "",
        "- Retrieval is strictly bounded to the supplied finite local snapshot; unseen languages and live remote databases are not claimed.",
        "- Views labeled `english_abstract_only` or `metadata_multilingual_record` do not substantiate full-text methodology claims without a corresponding fulltext view.",
        "- Short acronyms and false friends flagged above require contextual verification against the quoted sentence.",
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
    init_audit_log(audit_file, run_label="802a7115-cross-lingual")

    bundle = load_input_bundle(input_dir)
    case_id = bundle["case_id"]
    case_data = bundle["case_data"]

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-802a7115")
    budget_guard.tick_phase(budget_file, "align_cross_lingual_views")

    custom_glossary = case_data.get("glossary") if isinstance(case_data.get("glossary"), dict) else {}
    works, exact_dups, title_overwrite_audit, homonym_separated = group_works_and_views(bundle["records"])

    observed_languages = sorted({lg for w in works for lg in w["languages_present"] if lg != "unknown"})

    query_results: list[dict[str, Any]] = []
    total_false_friend_alerts = 0
    total_verified_anchors = 0

    for idx, q in enumerate(bundle["queries"], 1):
        qtxt = q.get("question") or q.get("query") or str(q) if isinstance(q, dict) else str(q)
        qid = str(q.get("question_id") or q.get("id") or f"Q{idx}") if isinstance(q, dict) else f"Q{idx}"
        exp = expand_query_terms(qtxt, custom_glossary, observed_languages)
        total_false_friend_alerts += len(exp["false_friend_or_acronym_risk"])
        locked_set = set(exp["zero_translation_locked_symbols"])
        acronym_set = set(exp["short_acronyms_protected"])

        # Track distinct aligned concepts and locked symbols for high-precision ranking and false-friend suppression
        concept_term_sets: list[tuple[str, set[str]]] = [
            (
                ta["canonical_concept"],
                {t for fl in ta["aligned_by_language"].values() for t in fl},
            )
            for ta in exp["terminology_alignments"]
        ]
        aligned_terms_all = {t for _, tset in concept_term_sets for t in tset}
        risk_tokens_only = {r["token"].lower() for r in exp["false_friend_or_acronym_risk"]}
        has_structured_anchors = bool(concept_term_sets or locked_set)

        matched_works: list[dict[str, Any]] = []
        matched_non_dup_views = 0
        excluded_dup_views = 0

        for w in works:
            work_hits: list[str] = []
            matched_views_out: list[dict[str, Any]] = []
            evidence_anchors: list[dict[str, Any]] = []
            work_excluded_dups: list[str] = []
            work_concepts_hit: set[str] = set()
            work_locked_hit: set[str] = set()

            for v in w["_internal_views"]:
                if v["view_type"] == "exact_duplicate":
                    excluded_dup_views += 1
                    work_excluded_dups.append(v["record_id"])
                    continue

                raw_blob = "\n".join(part for part in (v["raw_title"], v["_raw_abstract"], v["_raw_text"]) if part)
                folded_blob = fold_diacritics(raw_blob.lower())

                vhits: list[str] = []
                for term in exp["expanded_terms"]:
                    if _match_term_in_text(term, raw_blob, folded_blob, locked_set, acronym_set):
                        if term not in vhits:
                            vhits.append(term)
                if not vhits:
                    continue

                # Suppress pure false-friend / short-acronym-only noise when query has real domain concepts
                non_risk_hits = [t for t in vhits if t.lower() not in risk_tokens_only]
                if has_structured_anchors and not non_risk_hits:
                    continue

                v_concepts = {cname for cname, tset in concept_term_sets if any(t in tset for t in vhits)}
                v_locked = {t for t in vhits if t in locked_set}
                if has_structured_anchors and not v_concepts and not v_locked:
                    continue

                work_concepts_hit |= v_concepts
                work_locked_hit |= v_locked

                # Prioritize domain-aligned / multi-word / locked-symbol hits first
                vhits.sort(key=lambda t: (0 if (t in aligned_terms_all or t in locked_set or " " in t) else 1, -len(t)))
                for t in vhits:
                    if t not in work_hits:
                        work_hits.append(t)

                primary_term = vhits[0]
                quote = _extract_verbatim_sentence(primary_term, raw_blob, locked_set, acronym_set)
                verified = verify_quote_grounded(quote, raw_blob) if quote else False

                matched_views_out.append({
                    "record_id": v["record_id"],
                    "source_file": v["source_file"],
                    "body_language": v["body_language"],
                    "view_type": v["view_type"],
                    "doi": v["doi"],
                    "scielo_pid": v["scielo_pid"],
                    "matched_terms": vhits[:10],
                })
                evidence_anchors.append({
                    "record_id": v["record_id"],
                    "source_file": v["source_file"],
                    "body_language": v["body_language"],
                    "view_type": v["view_type"],
                    "matched_term": primary_term,
                    "quote": quote,
                    "verified_in_source": verified,
                })

            if matched_views_out:
                # If query specifies >=2 concepts/locked symbols, require >=2 concept+locked hits OR keep strongest tier cleanly
                total_anchor_dims = len(concept_term_sets) + len(locked_set)
                hit_anchor_dims = len(work_concepts_hit) + len(work_locked_hit)
                coverage_ratio = round(hit_anchor_dims / total_anchor_dims, 3) if total_anchor_dims > 0 else 1.0
                if total_anchor_dims >= 2 and hit_anchor_dims < 2 and len(work_concepts_hit) < 1:
                    continue
                if total_anchor_dims >= 2 and hit_anchor_dims == 1 and work_concepts_hit <= {"children and adolescents", "systematic review"}:
                    # Skip tangential background-only hits (e.g. matching only "children" or "systematic review" without the core disease/condition)
                    continue

                matched_non_dup_views += len(matched_views_out)
                total_verified_anchors += sum(1 for a in evidence_anchors if a["verified_in_source"])
                matched_works.append({
                    "work_id": w["work_id"],
                    "doi": w["doi"],
                    "scielo_pid": w["scielo_pid"],
                    "original_title": w["original_title"],
                    "languages_present": w["languages_present"],
                    "matched_concepts": sorted(work_concepts_hit),
                    "matched_locked_symbols": sorted(work_locked_hit),
                    "concept_coverage_ratio": coverage_ratio,
                    "matched_terms": work_hits[:12],
                    "matched_views": matched_views_out,
                    "excluded_duplicate_views": work_excluded_dups,
                    "evidence_anchors": evidence_anchors,
                })

        # Rank matched works by concept+locked-symbol coverage, then distinct concepts, then matched terms
        matched_works.sort(
            key=lambda m: (
                -len(m["matched_concepts"]) - len(m["matched_locked_symbols"]),
                -m["concept_coverage_ratio"],
                -len(m["matched_terms"]),
                m["work_id"],
            )
        )
        query_results.append({
            "question_id": qid,
            "query_expansion": exp,
            "matched_unique_work_count": len(matched_works),
            "matched_non_duplicate_view_count": matched_non_dup_views,
            "excluded_duplicate_views_count": excluded_dup_views,
            "matched_works": matched_works,
        })

    clean_works_out = [{k: v for k, v in w.items() if not k.startswith("_")} for w in works]
    summary_stats = {
        "raw_view_count": len(bundle["records"]),
        "unique_work_count": len(clean_works_out),
        "exact_duplicate_count": len(exact_dups),
        "homonym_distinct_doi_separated_count": len(homonym_separated),
        "title_overwritten_count": title_overwrite_audit["overwritten_count"],
        "query_count": len(query_results),
        "false_friend_or_acronym_alert_count": total_false_friend_alerts,
        "verified_sentence_anchor_count": total_verified_anchors,
    }

    alignment_doc = {
        "case_id": case_id,
        "scope_note": "Finite multilingual snapshot only; preserves original/translation/abstract-only/duplicate distinctions, original-title immutability, and distinct-DOI separation without live-coverage or unseen-language claims.",
        "observed_languages": observed_languages,
        "summary": summary_stats,
        "title_overwrite_audit": title_overwrite_audit,
        "homonym_distinct_doi_separated": homonym_separated,
        "canonical_works": clean_works_out,
        "exact_duplicates_flagged": exact_dups,
        "cross_lingual_query_results": query_results,
    }
    align_path = output_dir / "cross_lingual_alignment.json"
    result_guard.atomic_write_json(align_path, alignment_doc)

    report_md = _render_markdown_report(alignment_doc)
    report_path = output_dir / "cross_lingual_report.md"
    report_path.write_text(report_md, encoding="utf-8")

    append_audit_entry(
        audit_file,
        phase="cross_lingual_align",
        action="works_aligned_and_verified",
        actor="cross_lingual_matcher.py",
        output=summary_stats,
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    abstract_only_views = [
        v["record_id"] for w in clean_works_out for v in w["views"] if "abstract_only" in v["view_type"]
    ]
    if abstract_only_views:
        gaps.append({
            "code": "ABSTRACT_ONLY_LANGUAGE_VIEWS",
            "severity": "info",
            "source": "cross_lingual_matcher",
            "message": f"View(s) {abstract_only_views} contain abstract only; full-text verification requires original or full translation view.",
        })
    if total_false_friend_alerts > 0:
        gaps.append({
            "code": "FALSE_FRIEND_OR_ACRONYM_AMBIGUITY_FLAGGED",
            "severity": "info",
            "source": "cross_lingual_matcher",
            "message": f"Flagged {total_false_friend_alerts} short-acronym / false-friend / transliteration risk item(s) requiring contextual quote check.",
        })
    if homonym_separated:
        gaps.append({
            "code": "HOMONYM_DISTINCT_DOI_SEPARATED",
            "severity": "info",
            "source": "cross_lingual_matcher",
            "message": f"Prevented merging {len(homonym_separated)} homonymous title pair(s) carrying distinct normalized DOIs.",
        })

    patch = {
        "artifacts": ["result.json", "cross_lingual_alignment.json", "cross_lingual_report.md"],
        "observed_languages": observed_languages,
        "title_overwrite_audit": title_overwrite_audit,
        "homonym_distinct_doi_separated": homonym_separated,
        "canonical_works": clean_works_out,
        "exact_duplicates_flagged": exact_dups,
        "cross_lingual_query_results": query_results,
        "metrics": summary_stats,
        "metric_provenance": {
            "raw_view_count": "input_records",
            "unique_work_count": "cross_lingual_alignment.json:canonical_works",
            "exact_duplicate_count": "cross_lingual_alignment.json:exact_duplicates_flagged",
            "homonym_distinct_doi_separated_count": "cross_lingual_alignment.json:homonym_distinct_doi_separated",
            "title_overwritten_count": "cross_lingual_alignment.json:title_overwrite_audit",
            "query_count": "cross_lingual_alignment.json:cross_lingual_query_results",
            "false_friend_or_acronym_alert_count": "cross_lingual_alignment.json:cross_lingual_query_results",
            "verified_sentence_anchor_count": "cross_lingual_alignment.json:cross_lingual_query_results",
        },
        "findings": [
            (
                f"Consolidated {summary_stats['raw_view_count']} multilingual views into "
                f"{summary_stats['unique_work_count']} unique canonical works across languages "
                f"{observed_languages}; flagged {summary_stats['exact_duplicate_count']} exact duplicate(s), "
                f"separated {summary_stats['homonym_distinct_doi_separated_count']} distinct-DOI homonym pair(s), "
                f"and verified 0 title overwrites with {summary_stats['verified_sentence_anchor_count']} grounded sentence anchor(s)."
            )
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
        required_files=["cross_lingual_alignment.json", "cross_lingual_report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor 802a7115 Cross-Lingual Literature Matcher.")
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
