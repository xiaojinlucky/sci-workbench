# -*- coding: utf-8 -*-
"""Lightweight, dependency-free language + version detection for scholarly text.

Script detection via Unicode blocks; language scoring via small embedded stopword lists
(the approach used by stopword-based detectors such as langdetect / lingua's low-resource
mode). Accuracy is deliberately reported as a confidence, not a fact. Chinese/Japanese/
Korean are separated by script (Han + Hiragana => ja; Han + Hangul => ko; Han only => zh).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Tuple

# --- scripts -----------------------------------------------------------------
def script_profile(text: str) -> Dict[str, int]:
    prof = {"latin": 0, "cyrillic": 0, "greek": 0, "han": 0, "hiragana": 0, "katakana": 0, "hangul": 0, "arabic": 0, "hebrew": 0, "devanagari": 0, "thai": 0, "other": 0}
    for ch in text or "":
        o = ord(ch)
        if ch.isspace() or ch.isdigit() or ch.isascii() and not ch.isalpha():
            continue
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            prof["han"] += 1
        elif 0x3040 <= o <= 0x309F:
            prof["hiragana"] += 1
        elif 0x30A0 <= o <= 0x30FF:
            prof["katakana"] += 1
        elif 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF:
            prof["hangul"] += 1
        elif 0x0400 <= o <= 0x04FF or 0x0500 <= o <= 0x052F:
            prof["cyrillic"] += 1
        elif 0x0370 <= o <= 0x03FF or 0x1F00 <= o <= 0x1FFF:
            prof["greek"] += 1
        elif 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0xFB50 <= o <= 0xFDFF:
            prof["arabic"] += 1
        elif 0x0590 <= o <= 0x05FF:
            prof["hebrew"] += 1
        elif 0x0900 <= o <= 0x097F:
            prof["devanagari"] += 1
        elif 0x0E00 <= o <= 0x0E7F:
            prof["thai"] += 1
        elif (0x0041 <= o <= 0x005A) or (0x0061 <= o <= 0x007A) or (0x00C0 <= o <= 0x024F) or o in (0x0141, 0x0142):
            prof["latin"] += 1
        else:
            prof["other"] += 1
    return prof


def detect_script(text: str) -> str:
    p = script_profile(text)
    letters = sum(v for k, v in p.items() if k != "other")
    if letters == 0:
        return "unknown"
    if p["hangul"]:
        return "hangul"
    if p["hiragana"] or p["katakana"]:
        return "japanese"
    if p["han"]:
        return "han"
    if p["cyrillic"] > letters * 0.3:
        return "cyrillic"
    if p["greek"] > letters * 0.3:
        return "greek"
    if p["arabic"]:
        return "arabic"
    if p["hebrew"]:
        return "hebrew"
    if p["devanagari"]:
        return "devanagari"
    if p["thai"]:
        return "thai"
    if p["latin"]:
        return "latin"
    return "unknown"


# --- stopwords (top function words; ~12 per language, enough for short texts) ----
STOP = {
    "en": {"the", "of", "and", "to", "in", "a", "is", "that", "for", "with", "was", "were", "are", "this", "these", "we", "our", "has", "have", "from", "by", "on", "as", "be", "or", "an", "it", "its", "not", "which", "can", "may", "using", "used", "between", "than", "into", "also", "such", "each", "both", "more", "most", "results", "study", "significantly"},
    "pt": {"o", "a", "os", "as", "de", "da", "do", "dos", "das", "e", "em", "um", "uma", "que", "com", "para", "por", "no", "na", "nos", "nas", "se", "ao", "à", "é", "são", "foi", "foram", "como", "mais", "ou", "seu", "sua", "seus", "suas", "entre", "também", "não", "ser", "com", "resultados", "estudo", "conclusão", "objetivo", "métodos", "foram"},
    "es": {"el", "la", "los", "las", "de", "del", "y", "en", "un", "una", "que", "con", "para", "por", "no", "se", "su", "sus", "al", "lo", "como", "más", "o", "entre", "también", "fue", "son", "es", "este", "esta", "estos", "estas", "resultados", "estudio", "conclusión", "objetivo", "métodos"},
    "fr": {"le", "la", "les", "de", "des", "du", "et", "en", "un", "une", "que", "qui", "dans", "pour", "par", "sur", "avec", "est", "sont", "a", "au", "aux", "ce", "cette", "ces", "se", "plus", "ou", "entre", "aussi", "ne", "pas", "nous", "résultats", "étude"},
    "de": {"der", "die", "das", "des", "dem", "den", "ein", "eine", "einer", "eines", "einem", "einen", "und", "in", "im", "für", "mit", "von", "vom", "zu", "zum", "zur", "auf", "aus", "bei", "ist", "sind", "war", "wurden", "wir", "nicht", "auch", "als", "durch", "über", "zwischen", "ergebnisse", "studie"},
    "it": {"il", "lo", "la", "i", "gli", "le", "di", "del", "della", "dei", "delle", "e", "in", "un", "una", "che", "con", "per", "da", "su", "nel", "nella", "sono", "è", "come", "più", "o", "anche", "tra", "fra", "non", "si", "questo", "questa", "risultati", "studio"},
    "nl": {"de", "het", "een", "en", "van", "voor", "in", "is", "dat", "op", "te", "zijn", "met", "worden", "ook", "als", "bij", "door", "over", "deze", "dit", "niet", "naar", "uit", "werd", "voor", "tussen", "resultaten", "studie"},
    "ru": {"и", "в", "во", "не", "на", "что", "с", "со", "как", "а", "то", "все", "она", "он", "они", "мы", "вы", "из", "или", "но", "да", "бы", "при", "для", "от", "по", "это", "этой", "этого", "результаты", "исследование"},
    "zh": {"的", "了", "在", "是", "和", "与", "为", "对", "中", "及", "等", "研究", "方法", "结果", "分析", "我们", "通过", "可以", "一个", "以及", "并", "也", "但", "由", "于", "其", "该"},
    "ja": {"の", "は", "を", "に", "が", "で", "と", "から", "また", "よる", "れる", "た", "し", "する", "です", "ます", "これ", "それ", "おり", "ため", "こと", "もの", "研究", "結果", "方法", "分析"},
    "ko": {"은", "는", "이", "가", "을", "를", "의", "에", "에서", "으로", "와", "과", "한", "한다", "되는", "있는", "것", "및", "또한", "위해", "통해", "결과", "연구", "방법"},
}


def _tokens(text: str) -> list:
    t = unicodedata.normalize("NFKC", text or "").lower()
    return [w for w in re.findall(r"[\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", t) if w]


def detect_language(text: str) -> Tuple[str, float, Dict[str, float]]:
    """Return (iso_code, confidence, scores). iso in en/pt/es/fr/de/it/nl/ru/zh/ja/ko or 'unknown'."""
    script = detect_script(text)
    if script == "han":
        return "zh", 0.6, {"zh": 0.6}
    if script == "japanese":
        return "ja", 0.95, {"ja": 0.95}
    if script == "hangul":
        return "ko", 0.95, {"ko": 0.95}
    if script == "cyrillic":
        toks = _tokens(text)
        sc = sum(1 for w in toks if w in STOP["ru"])
        return "ru", min(0.95, 0.5 + sc / max(1, len(toks))), {"ru": sc}
    if script in ("arabic", "hebrew", "devanagari", "thai", "greek"):
        return {"arabic": "ar", "hebrew": "he", "devanagari": "hi", "thai": "th", "greek": "el"}[script], 0.9, {script: 1}
    if script == "unknown":
        return "unknown", 0.0, {}
    toks = _tokens(text)
    if len(toks) < 3:
        return "unknown", 0.0, {}
    # script-specific diacritic hints (strong signals for short texts)
    low = (text or "").lower()
    hints = 0.0
    hint_lang = ""
    if re.search(r"[ãõç]", low) and re.search(r"ção|ções|ahead|mente\b", low):
        hints, hint_lang = 2.0, "pt"
    elif re.search(r"ñ|¿|¡", low):
        hints, hint_lang = 2.0, "es"
    elif re.search(r"[äßüö]", low) or re.search(r"\bsich\b|\bund\b|\bder\b", low):
        hints, hint_lang = 2.0, "de"
    elif re.search(r"[åøæ]", low):
        hints, hint_lang = 2.0, "da/no/sv"
    elif re.search(r"[łżąęśźć]", low):
        hints, hint_lang = 2.0, "pl"
    scores = {}
    for lang, sw in STOP.items():
        if lang in ("ru", "zh", "ja", "ko"):
            continue
        n = sum(1 for w in toks if w in sw)
        scores[lang] = n / len(toks)
    best = max(scores, key=scores.get)
    if hint_lang and scores.get(hint_lang, 0) > 0.02:
        scores[hint_lang] += hints * 0.05
        best = max(scores, key=scores.get)
    total = sum(scores.values()) or 1.0
    conf = min(0.98, 0.35 + 2.5 * scores[best] + (0.15 if scores[best] > 0.12 else 0))
    if scores[best] < 0.03:
        return "unknown", 0.2, scores
    return best, conf, scores


# --- version / translation classification ---------------------------------------
def classify_view(title: str, abstract: str, fulltext: str, lang_hint: str = "") -> Dict[str, str]:
    """Classify a document view: original | full_translation | abstract_only_translation | duplicate.

    Based on what language each part is in; a 'full translation' has body in lang B while the
    work's original (another record) is in lang A; we only label the *view* here.
    """
    l_title, c_title, _ = detect_language(title or "")
    l_abs, c_abs, _ = detect_language(abstract or "")
    l_body, c_body, _ = detect_language((fulltext or "")[:4000])
    lang = l_body if l_body != "unknown" else l_abs if l_abs != "unknown" else l_title
    kind = "original"
    if (fulltext or "").strip() and len(fulltext) > 1500 and l_body not in ("unknown",) and l_title not in ("unknown",) and l_body != l_title and l_abs not in ("unknown",) and l_abs == l_body:
        kind = "full_translation"
    elif (fulltext or "").strip() and len(fulltext) > 1500 and l_title != "unknown" and l_body != "unknown" and l_body != l_title:
        kind = "full_translation"
    elif not (fulltext or "").strip() and (abstract or "").strip() and l_title != "unknown" and l_abs != "unknown" and l_abs != l_title:
        kind = "abstract_translation"
    return {"language": lang or lang_hint or "unknown", "lang_title": l_title, "lang_abstract": l_abs, "lang_body": l_body, "conf": round(max(c_title, c_abs, c_body), 2), "view_kind": kind, "has_fulltext": bool((fulltext or "").strip())}


def is_same_language_family(a: str, b: str) -> bool:
    fam = {"pt": "romance", "es": "romance", "fr": "romance", "it": "romance", "ro": "romance", "de": "germanic", "nl": "germanic", "en": "germanic", "sv": "germanic", "da": "germanic", "no": "germanic", "ru": "slavic", "pl": "slavic", "zh": "sinitic", "ja": "japonic", "ko": "koreanic"}
    return fam.get(a) == fam.get(b) and a in fam


# tiny PT<->EN academic glossary (function-word free; content terms common in science)
GLOSSARY = {
    ("pt", "en"): {
        "resultados": "results", "método": "method", "métodos": "methods", "conclusão": "conclusion", "objetivo": "objective", "objetivos": "objectives", "resumo": "abstract", "introdução": "introduction", "análise": "analysis", "estudo": "study", "dados": "data", "modelo": "model", "modelos": "models", "desempenho": "performance", "eficiência": "efficiency", "células": "cells", "proteína": "protein", "proteínas": "proteins", "reação": "reaction", "catalisador": "catalyst", "eletrodo": "electrode", "energia": "energy", "material": "material", "materiais": "materials", "temperatura": "temperature", "quantidade": "amount", "medida": "measurement", "medidas": "measurements", "saúde": "health", "doença": "disease", "doenças": "diseases", "pacientes": "patients", "tratamento": "treatment", "computação": "computing", "rede": "network", "redes": "networks", "aprendizado": "learning", "aprendizagem": "learning", "máquina": "machine", "inteligência": "intelligence", "artificial": "artificial", "simulação": "simulation", "otimização": "optimization", "algoritmo": "algorithm", "algoritmos": "algorithms", "avaliação": "evaluation", "validação": "validation", "experimento": "experiment", "experimentos": "experiments", "crescimento": "growth", "resistência": "resistance", "estabilidade": "stability", "estrutura": "structure", "propriedades": "properties", "caracterização": "characterization", "níveis": "levels", "efeito": "effect", "efeitos": "effects", "elevada": "high", "elevado": "high", "maior": "higher", "menor": "lower", "entre": "between", "sobre": "about", "com": "with", "sem": "without", "após": "after", "antes": "before", "durante": "during", "espécies": "species", "diversidade": "diversity", "comunidade": "community", "comunidades": "communities", "microbioma": "microbiome", "intestinal": "intestinal", "imune": "immune", "inflamação": "inflammation", "ácidos": "acids", "gordura": "fat", "cadeia": "chain", "curta": "short", "fermentação": "fermentation", "bactérias": "bacteria",
    },
    ("es", "en"): {
        "resultados": "results", "método": "method", "métodos": "methods", "conclusión": "conclusion", "objetivo": "objective", "resumen": "abstract", "introducción": "introduction", "análisis": "analysis", "estudio": "study", "datos": "data", "modelo": "model", "desempeño": "performance", "eficiencia": "efficiency", "células": "cells", "proteína": "protein", "reacción": "reaction", "catalizador": "catalyst", "energía": "energy", "materiales": "materials", "temperatura": "temperature", "salud": "health", "enfermedad": "disease", "pacientes": "patients", "tratamiento": "treatment", "red": "network", "redes": "networks", "aprendizaje": "learning", "simulación": "simulation", "optimización": "optimization", "algoritmo": "algorithm", "evaluación": "evaluation", "validación": "validation", "experimento": "experiment", "crecimiento": "growth", "resistencia": "resistance", "estabilidad": "stability", "estructura": "structure", "propiedades": "properties", "caracterización": "characterization", "niveles": "levels", "efecto": "effect", "efectos": "effects", "mayor": "higher", "menor": "lower", "entre": "between", "sobre": "about", "con": "with", "sin": "without", "después": "after", "antes": "before", "durante": "during", "especies": "species", "diversidad": "diversity", "comunidad": "community", "microbioma": "microbiome", "intestinal": "intestinal", "inmune": "immune", "inflamación": "inflammation", "ácidos": "acids", "cadena": "chain", "corta": "short", "fermentación": "fermentation", "bacterias": "bacteria",
    },
}
GLOSSARY[("en", "pt")] = {v: k for k, v in GLOSSARY[("pt", "en")].items()}
GLOSSARY[("en", "es")] = {v: k for k, v in GLOSSARY[("es", "en")].items()}


def translate_terms(terms, src: str, dst: str) -> Dict[str, str]:
    table = GLOSSARY.get((src, dst))
    if not table:
        return {}
    return {t: table[t] for t in terms if t in table}
