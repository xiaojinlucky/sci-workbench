#!/usr/bin/env python3
"""Sentence-level academic polishing invariant, anti-false-positive, glossary, and de-AI engine.

Zero third-party dependencies (Python 3.12+ stdlib). Built on top of
`writing_io_and_invariants.py` to enforce hard scientific fact conservation,
modality/causal strength preservation, LanguageTool-#12061-style false-positive
grammar rejection, glossary & abbreviation consistency, and bilingual de-AI /
Press-Release anti-defensive prose checks.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from writing_io_and_invariants import (  # noqa: E402
    audit_academic_prose,
    build_writing_result,
    classify_epistemic_strength,
    diff_invariants,
    extract_invariants,
    read_document_text,
)

# ---------------------------------------------------------------------------
# 1. Sentence Tokenizer & Section Parser (Bilingual EN/ZH)
# ---------------------------------------------------------------------------

ABBREV_PROTECTION = (
    r"\b(?:Fig|Figs|Eq|Eqs|Ref|Refs|Tab|Tbl|No|Nos|Vol|Dr|Prof|Mr|Mrs|Ms|St|vs|etc|al|e\.g|i\.e|ca|approx)\."
)

UNIVERSAL_ABBRS = {
    "DNA", "RNA", "mRNA", "rRNA", "tRNA", "cDNA", "PCR", "qPCR", "RT", "ATP", "ADP",
    "GDP", "GTP", "NAD", "NADH", "PBS", "BSA", "SDS", "PAGE", "ELISA", "FACS", "MRI",
    "CT", "PET", "EEG", "ECG", "ICU", "BMI", "WHO", "FDA", "NIH", "CDC", "ISO", "IEEE",
    "DOI", "URL", "PDF", "CSV", "JSON", "XML", "HTML", "CPU", "GPU", "RAM", "AI", "ML",
    "SD", "SEM", "IQR", "CI", "OR", "HR", "RR", "AUC", "ROC", "FDR", "ANOVA", "ANCOVA",
    "PCA", "UMAP", "UV", "IR", "NMR", "MS", "HPLC", "GC", "LC", "TEM", "SEM", "AFM",
    "XRD", "XPS", "RHE", "SHE", "CO2", "O2", "H2", "N2", "CH4", "H2O", "NaCl", "HCl",
    "USA", "UK", "EU", "CN", "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
}

EXTRA_EN_DEAI = [
    (r"\bsheds?\s+light\s+on\b", "en_ai_cliche", "warning", "Replace 'shed light on' with a concrete verb (e.g., clarify, quantify, identify)."),
    (r"\bit\s+is\s+worth\s+noting\s+that\b", "en_ai_cliche", "info", "Remove filler opener and state the observation directly."),
    (r"\bin\s+today'?s\s+rapidly\s+evolving\b", "hollow_opener", "warning", "Replace generic temporal filler with specific research context."),
    (r"\bmultifaceted\b", "en_ai_cliche", "info", "Specify the exact dimensions or mechanisms instead of 'multifaceted'."),
    (r"\bgame-?changer\b", "non_academic_jargon", "high", "Remove promotional buzzword 'game-changer' in scholarly prose."),
    (r"\ba\s+comprehensive\s+understanding\b", "en_ai_cliche", "info", "Specify what variables or mechanisms were characterized."),
]

EXTRA_ZH_DEAI = [
    (r"进行了(?:系统性的|深入的|全面的)?(?:探讨|分析|研究|评估)", "translationese", "warning", "改用直接动宾学术表达（如“评估了… / 比较了…”），避免冗余名词化。"),
    (r"在一个[\u4e00-\u9fff]{2,10}的框架下", "translationese", "warning", "避免英文直译腔“在一个…的框架下”，直接写方法或分析依据。"),
    (r"通过采用[\u4e00-\u9fff]{2,10}的方式", "translationese", "warning", "精简为“采用…”或“通过…”。"),
    (r"总而言之|毋庸置疑|不可或缺的|至关重要的作用|无缝衔接|全方位", "zh_ai_cliche", "warning", "删除模板化强化词，直接给出定量变化或具体机制边界。"),
]

# Legitimate academic concessive clauses (LanguageTool #12061 protection)
CONCESSIVE_STARTERS = re.compile(
    r"^\s*(Although|Though|Even\s+though|Whereas|While)\s+([^,;]+?),\s+([A-Za-z0-9\"'(].+)$"
)
FINITE_VERB_HINT = re.compile(
    r"\b(?:is|are|was|were|be|been|has|have|had|do|does|did|can|could|may|might|must|shall|should|will|would|"
    r"show(?:s|ed)?|demonstrat(?:e|es|ed)|indicat(?:e|es|ed)|suggest(?:s|ed)?|remain(?:s|ed)?|exhibit(?:s|ed)?|"
    r"observ(?:e|es|ed)|report(?:s|ed)?|reduc(?:e|es|ed)|increas(?:e|es|ed)|differ(?:s|ed)?|correlat(?:e|es|ed)|"
    r"occur(?:s|red)?|requir(?:e|es|ed)|provid(?:e|es|ed)|affect(?:s|ed)?|vary|varies|varied|yield(?:s|ed)?|"
    r"[a-z]{3,}(?:ed|es|s))\b",
    re.I,
)
SCIENTIFIC_PASSIVE_RE = re.compile(
    r"\b(?:was|were|is|are|been)\s+(?:measured|analyzed|analysed|incubated|collected|centrifuged|sequenced|"
    r"synthesized|evaluated|normalized|filtered|calibrated|recorded|acquired|performed|conducted|calculated|"
    r"assessed|validated|confirmed|cultured|stained|imaged|quantified|compared|tested|administered|enrolled)\b",
    re.I,
)
CONDITION_CLAUSE_RE = re.compile(
    r"\b(?:in\s+the\s+[a-z0-9\-]+\s+(?:cohort|group|subgroup|arm|model)|"
    r"under\s+[a-z0-9\-\s]{3,35}\s+conditions?|"
    r"in\s+vitro|in\s+vivo|ex\s+vivo|in\s+silico|"
    r"at\s+\d+(?:\.\d+)?\s*(?:°C|K|mg/kg|mM|µM|uM|nM|h|min|days?|weeks?)|"
    r"only\s+in\s+[a-z0-9\-\s]{3,30})\b|"
    r"(?:仅在[\u4e00-\u9fffA-Za-z0-9]{2,15}(?:条件下|组中|队列中|模型中))",
    re.I,
)
MODAL_HEDGES_EXPLICIT = re.compile(
    r"\b(may\s+be\s+associated\s+with|might\s+be\s+associated\s+with|could\s+be\s+associated\s+with|"
    r"may|might|could|possibly|potentially|appears?\s+to|tends?\s+to|suggests?|associated\s+with|"
    r"correlated\s+with|correlates\s+with|linked\s+to|trended\s+toward|consistent\s+with)\b|"
    r"(可能与.{0,8}相关|可能|提示|倾向于|伴随|相关)",
    re.I,
)
CAUSAL_UPGRADE_RE = re.compile(
    r"\b(proves?|proved|proven|causes?|caused|causing|drives?|drove|driving|determines?|determined|"
    r"directly\s+triggers?|establishes?\s+that|conclusively\s+shows?|confirms?\s+(?:a\s+)?causal|"
    r"guarantees?|definitively\s+demonstrates?|is\s+caused\s+by)\b|"
    r"(证明了|直接导致|引发了|决定性地证明|证实了因果|必然引起)",
    re.I,
)


def split_sentences_with_sections(text: str) -> list[dict[str, Any]]:
    """Split bilingual academic Markdown/plain text into indexed sentences preserving section names."""
    records: list[dict[str, Any]] = []
    section = "Preamble"
    sent_idx = 0

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("```"):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            continue
        hm = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if hm:
            section = hm.group(2).strip()
            continue

        # Protect abbreviations and decimal numbers from false sentence breaks
        protected = re.sub(ABBREV_PROTECTION, lambda m: m.group(0).replace(".", "<DOT>"), stripped)
        protected = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", protected)
        protected = re.sub(r"(10\.\d{4,9}/[^\s,;)]+)", lambda m: m.group(0).replace(".", "<DOT>"), protected)

        parts = re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])(?=[^\s”’\"')\]])", protected)
        for part in parts:
            restored = part.replace("<DOT>", ".").strip()
            if len(restored) < 4:
                continue
            sent_idx += 1
            records.append({
                "sentence_id": f"S{sent_idx:03d}",
                "section": section,
                "line": lineno,
                "text": restored,
            })
    return records


# ---------------------------------------------------------------------------
# 2. Anti-False-Positive Grammar Validator (LanguageTool #12061 & Scholarly Syntax)
# ---------------------------------------------------------------------------

def evaluate_false_positive_guard(
    sentences: list[dict[str, Any]],
    candidate_alerts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate legitimate academic syntax and dismiss false-positive grammar alerts."""
    protected_clauses: list[dict[str, Any]] = []
    rejected_alerts: list[dict[str, Any]] = []
    accepted_alerts: list[dict[str, Any]] = []

    sent_by_id = {s["sentence_id"]: s for s in sentences}

    for s in sentences:
        txt = s["text"]
        m_conc = CONCESSIVE_STARTERS.match(txt)
        if m_conc:
            sub_clause = m_conc.group(2).strip()
            main_clause = m_conc.group(3).strip()
            sub_has_verb = bool(FINITE_VERB_HINT.search(sub_clause))
            main_has_verb = bool(FINITE_VERB_HINT.search(main_clause))
            # Check for true Chinese-English double-conjunction error: "Although ..., but ..."
            has_double_but = bool(re.match(r"^(?:but|yet|however)\b", main_clause, re.I))
            if sub_has_verb and main_has_verb and not has_double_but:
                protected_clauses.append({
                    "sentence_id": s["sentence_id"],
                    "section": s["section"],
                    "construct": "valid_concessive_clause",
                    "starter": m_conc.group(1),
                    "text": txt,
                    "guard_rule": "LT_12061_CONCESSIVE_GUARD",
                    "note": "Grammatically complete subordinate + main clause without redundant 'but'; do not flag as fragment or force deletion of concessive marker.",
                })
        if SCIENTIFIC_PASSIVE_RE.search(txt) and re.search(r"method|material|experiment|protocol|procedure|方法|实验", s["section"], re.I):
            protected_clauses.append({
                "sentence_id": s["sentence_id"],
                "section": s["section"],
                "construct": "valid_methods_passive_voice",
                "text": txt,
                "guard_rule": "SCHOLARLY_PASSIVE_GUARD",
                "note": "Standard scientific passive voice in Methods; preserve objective register.",
            })

    protected_ids = {(p["sentence_id"], p["construct"]) for p in protected_clauses}

    for idx, alert in enumerate(candidate_alerts or [], start=1):
        sid = str(alert.get("sentence_id", "")).strip()
        rule_id = str(alert.get("rule_id") or alert.get("rule") or f"ALERT-{idx}").strip()
        msg = str(alert.get("message") or alert.get("rationale") or "").strip()
        orig = str(alert.get("original") or (sent_by_id.get(sid, {}).get("text", ""))).strip()
        suggested = str(alert.get("suggested") or alert.get("revised") or "").strip()

        is_fp = False
        fp_reason = ""

        # Case A: LanguageTool #12061 style false positive on valid Although/Though/Whereas/While clause
        if (
            any(k in (rule_id + " " + msg).lower() for k in ("although", "concessive", "fragment", "missing_verb", "12061", "comma_splice"))
            and (sid, "valid_concessive_clause") in protected_ids
        ):
            is_fp = True
            fp_reason = "Dismissed as false positive (LT_12061_CONCESSIVE_GUARD): both subordinate and main clauses contain finite verbs with no redundant 'but'."
        elif CONCESSIVE_STARTERS.match(orig) and any(
            k in (rule_id + " " + msg).lower() for k in ("although", "concessive", "fragment", "12061")
        ):
            mc = CONCESSIVE_STARTERS.match(orig)
            if mc and FINITE_VERB_HINT.search(mc.group(2)) and FINITE_VERB_HINT.search(mc.group(3)) and not re.match(r"^(?:but|yet)\b", mc.group(3).strip(), re.I):
                is_fp = True
                fp_reason = "Dismissed as false positive (LT_12061_CONCESSIVE_GUARD): valid concessive complex sentence verified."
        # Case B: Passive voice alert in Methods
        elif "passive" in (rule_id + " " + msg).lower() and (
            (sid, "valid_methods_passive_voice") in protected_ids or SCIENTIFIC_PASSIVE_RE.search(orig)
        ):
            is_fp = True
            fp_reason = "Dismissed as false positive (SCHOLARLY_PASSIVE_GUARD): objective scientific passive voice is standard in experimental reporting."
        # Case C: Alert suggests strengthening hedging/modality into causal claim
        elif suggested and orig:
            inv = diff_invariants(orig, suggested)
            if not inv["preserved"]:
                codes = [v["code"] for v in inv["violations"] if v["severity"] == "high"]
                if codes:
                    is_fp = True
                    fp_reason = f"Dismissed unsafe suggestion: violates hard scientific invariants ({', '.join(codes)})."

        entry = {
            "alert_id": str(alert.get("alert_id") or f"FP-{idx:03d}"),
            "sentence_id": sid or "UNMAPPED",
            "rule_id": rule_id,
            "original": orig,
            "suggested": suggested,
            "message": msg,
            "status": "rejected_false_positive" if is_fp else "accepted_for_review",
            "rationale": fp_reason if is_fp else "Passed false-positive guard; eligible for minimal-edit review.",
        }
        if is_fp:
            rejected_alerts.append(entry)
        else:
            accepted_alerts.append(entry)

    return {
        "protected_clauses_count": len(protected_clauses),
        "protected_clauses": protected_clauses,
        "rejected_false_positives_count": len(rejected_alerts),
        "rejected_false_positives": rejected_alerts,
        "accepted_alerts_count": len(accepted_alerts),
        "accepted_alerts": accepted_alerts,
    }


# ---------------------------------------------------------------------------
# 3. Glossary Consistency & Abbreviation First-Definition Auditor
# ---------------------------------------------------------------------------

def load_glossary(glossary_path: Path | None) -> list[dict[str, Any]]:
    """Load glossary entries from JSON or CSV."""
    if not glossary_path or not glossary_path.is_file():
        return []
    suf = glossary_path.suffix.lower()
    entries: list[dict[str, Any]] = []
    if suf == ".json":
        raw = json.loads(glossary_path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("terms", raw.get("glossary", []))
        for item in items:
            if not isinstance(item, dict):
                continue
            canonical = str(item.get("canonical") or item.get("term") or "").strip()
            if not canonical:
                continue
            entries.append({
                "canonical": canonical,
                "abbreviation": str(item.get("abbreviation") or item.get("abbr") or "").strip(),
                "forbidden_variants": [str(v).strip() for v in (item.get("forbidden_variants") or item.get("variants") or []) if str(v).strip()],
                "zh_canonical": str(item.get("zh_canonical") or "").strip(),
                "zh_forbidden": [str(v).strip() for v in (item.get("zh_forbidden") or []) if str(v).strip()],
            })
    elif suf == ".csv":
        with glossary_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh):
                canonical = (row.get("canonical") or row.get("term") or "").strip()
                if not canonical:
                    continue
                forb = [x.strip() for x in (row.get("forbidden_variants") or row.get("variants") or "").split(";") if x.strip()]
                entries.append({
                    "canonical": canonical,
                    "abbreviation": (row.get("abbreviation") or row.get("abbr") or "").strip(),
                    "forbidden_variants": forb,
                    "zh_canonical": (row.get("zh_canonical") or "").strip(),
                    "zh_forbidden": [x.strip() for x in (row.get("zh_forbidden") or "").split(";") if x.strip()],
                })
    return entries


def audit_glossary_and_abbreviations(
    text: str,
    sentences: list[dict[str, Any]],
    glossary: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check full-manuscript glossary drift, hyphen/spelling inconsistency, and abbreviation first-definition rules."""
    glossary_violations: list[dict[str, Any]] = []
    internal_glossary_conflicts: list[dict[str, Any]] = []

    # Check internal conflicts in glossary (same abbreviation mapped to different canonical terms)
    abbr_map: dict[str, set[str]] = {}
    for g in glossary:
        if g["abbreviation"]:
            abbr_map.setdefault(g["abbreviation"], set()).add(g["canonical"])
    for ab, canons in abbr_map.items():
        if len(canons) > 1:
            internal_glossary_conflicts.append({
                "abbreviation": ab,
                "conflicting_canonicals": sorted(canons),
                "action": "needs_author_confirm",
            })

    # Scan glossary forbidden variants across sentences
    for g in glossary:
        for var in g["forbidden_variants"]:
            pat = re.compile(rf"(?<!\w){re.escape(var)}(?!\w)", re.I) if re.search(r"[A-Za-z]", var) else re.compile(re.escape(var))
            for s in sentences:
                if pat.search(s["text"]):
                    # Avoid flagging exact canonical if case-insensitive match equals canonical
                    if var.lower() == g["canonical"].lower() and g["canonical"] in s["text"] and var not in s["text"]:
                        continue
                    glossary_violations.append({
                        "sentence_id": s["sentence_id"],
                        "section": s["section"],
                        "found_variant": var,
                        "required_canonical": g["canonical"],
                        "issue": "glossary_variant_drift",
                    })
        for zvar in g["zh_forbidden"]:
            for s in sentences:
                if zvar in s["text"]:
                    glossary_violations.append({
                        "sentence_id": s["sentence_id"],
                        "section": s["section"],
                        "found_variant": zvar,
                        "required_canonical": g["zh_canonical"] or g["canonical"],
                        "issue": "zh_terminology_drift",
                    })

    # Auto-detect hyphenation / casing drift for technical tokens (e.g., RNA-seq vs RNA seq vs RNASeq)
    token_forms: dict[str, dict[str, list[str]]] = {}
    for s in sentences:
        for m in re.finditer(r"\b([A-Za-z]{2,}(?:[-][A-Za-z0-9]{2,})+)\b", s["text"]):
            form = m.group(1)
            norm_key = re.sub(r"[-_\s]+", "", form).lower()
            token_forms.setdefault(norm_key, {}).setdefault(form, []).append(s["sentence_id"])
    # Also check unhyphenated/spaced variants of those keys
    for norm_key in list(token_forms.keys()):
        if len(norm_key) < 5:
            continue
        for s in sentences:
            for m in re.finditer(r"\b([A-Za-z]{2,}(?:\s+[A-Za-z0-9]{2,})?)\b", s["text"]):
                cand = m.group(1)
                if re.sub(r"[-_\s]+", "", cand).lower() == norm_key and cand not in token_forms[norm_key]:
                    token_forms[norm_key].setdefault(cand, []).append(s["sentence_id"])

    spelling_drift: list[dict[str, Any]] = []
    for norm_key, forms_map in sorted(token_forms.items()):
        if len(forms_map) > 1:
            # Ignore pure title-case vs lowercase difference at sentence start unless inner casing/hyphen differs
            lower_set = {f.lower() for f in forms_map}
            if len(lower_set) > 1 or any(any(c.isupper() for c in f[1:]) for f in forms_map):
                spelling_drift.append({
                    "normalized_term": norm_key,
                    "observed_forms": {k: sorted(set(v)) for k, v in forms_map.items()},
                    "issue": "cross_section_spelling_or_hyphen_drift",
                })

    # UK vs US spelling mix check (tumour/tumor, analyse/analyze, behaviour/behavior, etc.)
    uk_us_pairs = [
        ("tumour", "tumor"), ("analyse", "analyze"), ("analysed", "analyzed"),
        ("behaviour", "behavior"), ("colour", "color"), ("optimise", "optimize"),
        ("randomised", "randomized"), ("modelling", "modeling"), ("centre", "center"),
    ]
    low_text = text.lower()
    for uk, us in uk_us_pairs:
        if re.search(rf"\b{uk}\b", low_text) and re.search(rf"\b{us}\b", low_text):
            spelling_drift.append({
                "normalized_term": f"{uk}/{us}",
                "observed_forms": {uk: ["present"], us: ["present"]},
                "issue": "uk_us_spelling_mix",
            })

    # Abbreviation first-definition tracker
    def_re = re.compile(r"([A-Za-z][A-Za-z0-9\-\s]{3,60}|[\u4e00-\u9fff]{2,20})\s*[\(（]([A-Z][A-Za-z0-9\-]{1,9})[\)）]")
    bare_abbr_re = re.compile(r"\b([A-Z]{2,}(?:-[A-Z0-9]+)*)\b")

    definitions: dict[str, list[dict[str, Any]]] = {}
    first_use: dict[str, dict[str, Any]] = {}

    for idx, s in enumerate(sentences):
        for m in def_re.finditer(s["text"]):
            abbr = m.group(2).strip()
            if abbr in UNIVERSAL_ABBRS or re.fullmatch(r"S\d+", abbr):
                continue
            definitions.setdefault(abbr, []).append({
                "sentence_index": idx,
                "sentence_id": s["sentence_id"],
                "section": s["section"],
                "expanded_form": m.group(1).strip(),
            })
        for m in bare_abbr_re.finditer(s["text"]):
            abbr = m.group(1)
            if abbr in UNIVERSAL_ABBRS or len(abbr) < 2 or len(abbr) > 8 or re.fullmatch(r"S?\d+", abbr):
                continue
            if abbr not in first_use:
                first_use[abbr] = {"sentence_index": idx, "sentence_id": s["sentence_id"], "section": s["section"]}

    abbr_issues: list[dict[str, Any]] = []
    for abbr, defs in sorted(definitions.items()):
        if len(defs) > 1:
            abbr_issues.append({
                "abbreviation": abbr,
                "issue": "redefined_multiple_times",
                "locations": [d["sentence_id"] for d in defs],
            })
        first_def_idx = defs[0]["sentence_index"]
        if abbr in first_use and first_use[abbr]["sentence_index"] < first_def_idx:
            abbr_issues.append({
                "abbreviation": abbr,
                "issue": "used_before_first_definition",
                "first_use": first_use[abbr]["sentence_id"],
                "first_definition": defs[0]["sentence_id"],
            })

    for abbr, loc in sorted(first_use.items()):
        if abbr not in definitions:
            # Only flag abbreviations that look domain-specific (appear >=2 times or in glossary)
            occurrences = sum(1 for s in sentences if re.search(rf"\b{re.escape(abbr)}\b", s["text"]))
            in_gloss = any(g["abbreviation"] == abbr for g in glossary)
            if occurrences >= 2 or in_gloss:
                abbr_issues.append({
                    "abbreviation": abbr,
                    "issue": "undefined_abbreviation",
                    "first_use": loc["sentence_id"],
                    "occurrences": occurrences,
                })

    return {
        "glossary_terms_loaded": len(glossary),
        "internal_glossary_conflicts": internal_glossary_conflicts,
        "glossary_violations_count": len(glossary_violations),
        "glossary_violations": glossary_violations,
        "spelling_drift_count": len(spelling_drift),
        "spelling_drift": spelling_drift,
        "abbreviations_defined": {k: v[0] for k, v in sorted(definitions.items())},
        "abbreviation_issues_count": len(abbr_issues),
        "abbreviation_issues": abbr_issues,
    }


# ---------------------------------------------------------------------------
# 4. Extended De-AI / Anti-Translationese / Anti-Defensive Check
# ---------------------------------------------------------------------------

def audit_prose_extended(text: str, sentences: list[dict[str, Any]]) -> dict[str, Any]:
    """Run shared whitelist academic linter plus extended de-AI and Press-Release anti-defensive checks."""
    base = audit_academic_prose(text)
    findings = list(base["findings"])

    for s in sentences:
        txt = s["text"]
        in_limitations = bool(re.search(r"limitation|caveat|局限|不足", s["section"], re.I))
        for pat, cat, sev, sug in EXTRA_EN_DEAI + EXTRA_ZH_DEAI:
            for m in re.finditer(pat, txt, re.I):
                findings.append({
                    "line": s["line"],
                    "sentence_id": s["sentence_id"],
                    "section": s["section"],
                    "category": cat,
                    "severity": sev,
                    "matched": m.group(0),
                    "suggestion": sug,
                })
        # Press-Release anti-defensive rule: flag self-undermining phrasing outside Limitations
        if not in_limitations:
            for pat_d in (
                r"遗憾的是", r"仅(?:仅)?是一个初步尝试", r"仍明显落后于", r"效果(?:非常)?有限",
                r"\bunfortunately,\s+(?:our|we|the)\b", r"\bmerely\s+a\s+preliminary\s+attempt\b",
                r"\bwe\s+only\s+managed\s+to\b", r"\bseverely\s+limited\s+by\s+our\s+lack\b",
            ):
                for m in re.finditer(pat_d, txt, re.I):
                    findings.append({
                        "line": s["line"],
                        "sentence_id": s["sentence_id"],
                        "section": s["section"],
                        "category": "defensive_self_undermining_outside_limitations",
                        "severity": "warning",
                        "matched": m.group(0),
                        "suggestion": "Press-Release rule: remove self-undermining hedge from primary Results/Abstract or relocate specific scientific boundary to Limitations.",
                    })

    # Deduplicate by (line, category, matched)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, str, str]] = set()
    for f in findings:
        key = (f.get("line"), f.get("category", ""), f.get("matched", "").lower())
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    high_n = sum(1 for f in deduped if f["severity"] == "high")
    warn_n = sum(1 for f in deduped if f["severity"] == "warning")
    return {
        "clean": high_n == 0 and warn_n == 0,
        "high_count": high_n,
        "warning_count": warn_n,
        "info_count": len(deduped) - high_n - warn_n,
        "total_findings": len(deduped),
        "findings": deduped,
    }


# ---------------------------------------------------------------------------
# 5. Sentence-Level Alignment & Hard Invariant Audit
# ---------------------------------------------------------------------------

def _sentence_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _check_sentence_invariants(orig: str, rev: str) -> dict[str, Any]:
    """Deep sentence-level invariant check combining diff_invariants with modality & condition checks."""
    inv = diff_invariants(orig, rev)
    violations = list(inv["violations"])

    # Explicit modal-to-causal escalation check (catches "may be associated with" -> "proves"/"causes")
    orig_modals = [m.group(0).lower() for m in MODAL_HEDGES_EXPLICIT.finditer(orig)]
    rev_modals = [m.group(0).lower() for m in MODAL_HEDGES_EXPLICIT.finditer(rev)]
    orig_causal = [m.group(0).lower() for m in CAUSAL_UPGRADE_RE.finditer(orig)]
    rev_causal = [m.group(0).lower() for m in CAUSAL_UPGRADE_RE.finditer(rev)]

    # Allow Press-Release removal of self-undermining "preliminary attempt" when no epistemic modal was dropped
    if not orig_modals and not rev_causal and re.search(r"\b(?:our\s+)?preliminary\s+attempt\b|初步尝试", orig, re.I):
        violations = [v for v in violations if v["code"] != "HEDGE_STRIPPED"]

    if orig_modals and not rev_modals and not any(v["code"] == "HEDGE_STRIPPED" for v in violations):
        violations.append({
            "code": "HEDGE_STRIPPED",
            "severity": "high",
            "detail": f"Original hedging markers {orig_modals} disappeared in revision.",
        })
    if len(rev_causal) > len(orig_causal):
        if not any(v["code"] == "EPISTEMIC_STRENGTH_ESCALATION" for v in violations):
            violations.append({
                "code": "EPISTEMIC_STRENGTH_ESCALATION",
                "severity": "high",
                "detail": f"Unwarranted causal verb(s) introduced: {rev_causal} (source had {orig_causal}).",
            })

    # Condition clause conservation
    orig_conds = {m.group(0).lower().strip() for m in CONDITION_CLAUSE_RE.finditer(orig)}
    rev_conds = {m.group(0).lower().strip() for m in CONDITION_CLAUSE_RE.finditer(rev)}
    dropped_conds = sorted(orig_conds - rev_conds)
    if dropped_conds:
        violations.append({
            "code": "CONDITION_BOUNDARY_DROPPED",
            "severity": "high",
            "items": dropped_conds,
            "detail": f"Experimental condition/cohort qualifier omitted: {dropped_conds}.",
        })

    # Negation polarity flip (critical if negation dropped completely or newly inserted)
    src_neg = inv["source_invariants"]["epistemic"]["negation_count"]
    rev_neg = inv["revised_invariants"]["epistemic"]["negation_count"]
    if (src_neg > 0 and rev_neg == 0) or (src_neg == 0 and rev_neg > 0):
        for v in violations:
            if v["code"] == "NEGATION_COUNT_SHIFT":
                v["severity"] = "high"
                v["code"] = "NEGATION_POLARITY_REVERSAL"

    high_cnt = sum(1 for v in violations if v["severity"] == "high")
    med_cnt = sum(1 for v in violations if v["severity"] == "medium")
    return {
        "preserved": high_cnt == 0,
        "high_severity_count": high_cnt,
        "medium_severity_count": med_cnt,
        "violations": violations,
        "source_epistemic": classify_epistemic_strength(orig),
        "revised_epistemic": classify_epistemic_strength(rev),
    }


def _infer_edit_type(orig: str, rev: str, inv_check: dict[str, Any], orig_prose: dict[str, Any], rev_prose: dict[str, Any]) -> tuple[str, str]:
    if not inv_check["preserved"]:
        codes = ", ".join(v["code"] for v in inv_check["violations"] if v["severity"] == "high")
        return "invariant_violation_blocked", f"Blocked: revision violated hard scientific invariant(s): {codes}."
    if orig_prose["total_findings"] > rev_prose["total_findings"]:
        cats = sorted({f["category"] for f in orig_prose["findings"]})
        if "defensive_self_undermining_outside_limitations" in cats:
            return "anti_defensive_press_release", "Removed self-undermining phrasing in primary results while conserving facts."
        return "de_ai_translationese", f"Cleaned AI/translationese phrasing ({', '.join(cats[:3])}) with full invariant parity."
    if len(rev) < len(orig) * 0.85:
        return "conciseness", "Tightened wordy syntax while preserving original claims, numbers, and hedges."
    return "grammar_syntax", "Minimal grammar, collocation, or academic register refinement with invariants preserved."


def align_and_audit(
    source_text: str,
    revised_text: str,
    *,
    glossary: list[dict[str, Any]],
    candidate_alerts: list[dict[str, Any]] | None = None,
    explicit_edits: list[dict[str, Any]] | None = None,
    register: str = "journal_article",
) -> dict[str, Any]:
    """Align source vs revised sentences, enforce hard invariants, run false-positive/glossary/de-AI guards."""
    src_sents = split_sentences_with_sections(source_text)
    rev_sents = split_sentences_with_sections(revised_text)

    fp_report = evaluate_false_positive_guard(src_sents, candidate_alerts)
    src_gloss = audit_glossary_and_abbreviations(source_text, src_sents, glossary)
    rev_gloss = audit_glossary_and_abbreviations(revised_text, rev_sents, glossary)
    src_prose = audit_prose_extended(source_text, src_sents)
    rev_prose = audit_prose_extended(revised_text, rev_sents)

    # Index optional human/agent explicit rationales by sentence_id
    explicit_by_sid = {str(e.get("sentence_id", "")): e for e in (explicit_edits or [])}

    # Align source sentences to revised sentences via greedy monotonic best match
    used_rev: set[int] = set()
    edit_records: list[dict[str, Any]] = []
    invariant_violations_total = 0
    modality_drift_count = 0
    numeric_drift_count = 0
    citation_drop_count = 0

    for s_idx, s in enumerate(src_sents):
        sid = s["sentence_id"]
        best_r_idx = None
        best_sim = -1.0
        # Search window around s_idx first, then global fallback
        candidates = [r_i for r_i in range(len(rev_sents)) if r_i not in used_rev]
        for r_i in candidates:
            sim = _sentence_similarity(s["text"], rev_sents[r_i]["text"])
            # Slight bonus for same section and nearby position
            if rev_sents[r_i]["section"] == s["section"]:
                sim += 0.06
            if abs(r_i - s_idx) <= 2:
                sim += 0.04
            if sim > best_sim:
                best_sim = sim
                best_r_idx = r_i

        if best_r_idx is None or best_sim < 0.25:
            # Sentence was deleted
            inv_check = _check_sentence_invariants(s["text"], "")
            status = "needs_author_confirm" if inv_check["preserved"] else "blocked_invariant_violation"
            if not inv_check["preserved"]:
                invariant_violations_total += inv_check["high_severity_count"]
            edit_records.append({
                "sentence_id": sid,
                "section": s["section"],
                "original": s["text"],
                "revised": "",
                "changed": True,
                "similarity": 0.0,
                "edit_type": "sentence_deleted",
                "rationale": "Sentence removed in revised draft; verify deletion did not discard scientific facts.",
                "status": status,
                "invariants_preserved": inv_check["preserved"],
                "invariant_violations": inv_check["violations"],
            })
            continue

        used_rev.add(best_r_idx)
        r = rev_sents[best_r_idx]
        changed = s["text"].strip() != r["text"].strip()
        inv_check = _check_sentence_invariants(s["text"], r["text"])

        if not inv_check["preserved"]:
            invariant_violations_total += inv_check["high_severity_count"]
            for v in inv_check["violations"]:
                if v["severity"] != "high":
                    continue
                if v["code"] in ("EPISTEMIC_STRENGTH_ESCALATION", "HEDGE_STRIPPED"):
                    modality_drift_count += 1
                elif v["code"] in ("NUMERIC_DROPPED_OR_ALTERED", "NUMERIC_UNGROUNDED_ADDITION", "VALUE_UNIT_MISMATCH", "PVALUE_OR_SIGNIFICANCE_MUTATION", "SAMPLE_SIZE_MUTATION"):
                    numeric_drift_count += 1
                elif v["code"] == "CITATION_DROPPED":
                    citation_drop_count += 1

        if changed:
            sp = audit_prose_extended(s["text"], [s])
            rp = audit_prose_extended(r["text"], [r])
            inferred_type, inferred_rat = _infer_edit_type(s["text"], r["text"], inv_check, sp, rp)
            exp = explicit_by_sid.get(sid, {})
            edit_type = str(exp.get("edit_type") or inferred_type)
            rationale = str(exp.get("rationale") or inferred_rat)
            if not inv_check["preserved"]:
                status = "blocked_invariant_violation"
                edit_type = "invariant_violation_blocked"
                rationale = inferred_rat
            elif inv_check["medium_severity_count"] > 0 or best_sim < 0.45:
                status = "needs_author_confirm"
            else:
                status = str(exp.get("status") or "applied")
            edit_records.append({
                "sentence_id": sid,
                "section": s["section"],
                "original": s["text"],
                "revised": r["text"],
                "changed": True,
                "similarity": round(min(1.0, best_sim), 3),
                "edit_type": edit_type,
                "rationale": rationale,
                "status": status,
                "invariants_preserved": inv_check["preserved"],
                "invariant_violations": inv_check["violations"],
            })
        else:
            # Check if this unchanged sentence was protected from a false-positive alert
            fp_hits = [a for a in fp_report["rejected_false_positives"] if a["sentence_id"] == sid]
            if fp_hits:
                for a in fp_hits:
                    edit_records.append({
                        "sentence_id": sid,
                        "section": s["section"],
                        "original": s["text"],
                        "revised": r["text"],
                        "changed": False,
                        "similarity": 1.0,
                        "edit_type": "false_positive_guard",
                        "rationale": a["rationale"],
                        "status": "rejected_false_positive",
                        "invariants_preserved": True,
                        "invariant_violations": [],
                    })

    # Any unmatched revised sentences are newly inserted
    for r_i, r in enumerate(rev_sents):
        if r_i not in used_rev:
            inv_new = extract_invariants(r["text"])
            has_new_facts = bool(inv_new["bare_numbers"] or inv_new["pvalues"] or inv_new["sample_sizes"])
            edit_records.append({
                "sentence_id": f"NEW-{r['sentence_id']}",
                "section": r["section"],
                "original": "",
                "revised": r["text"],
                "changed": True,
                "similarity": 0.0,
                "edit_type": "sentence_inserted",
                "rationale": "New sentence inserted during polishing; confirm no ungrounded facts were added.",
                "status": "blocked_invariant_violation" if has_new_facts else "needs_author_confirm",
                "invariants_preserved": not has_new_facts,
                "invariant_violations": [{"code": "UNGROUNDED_NEW_FACT_SENTENCE", "severity": "high"}] if has_new_facts else [],
            })
            if has_new_facts:
                invariant_violations_total += 1

    # Also include document-level invariant diff for full-text coverage
    doc_inv = diff_invariants(source_text, revised_text)
    passed = (
        invariant_violations_total == 0
        and doc_inv["preserved"]
        and rev_gloss["glossary_violations_count"] == 0
    )

    return {
        "passed": passed,
        "register": register,
        "summary": {
            "source_sentences": len(src_sents),
            "revised_sentences": len(rev_sents),
            "edited_sentences": sum(1 for e in edit_records if e["changed"]),
            "applied_count": sum(1 for e in edit_records if e["status"] == "applied"),
            "rejected_false_positive_count": sum(1 for e in edit_records if e["status"] == "rejected_false_positive"),
            "needs_author_confirm_count": sum(1 for e in edit_records if e["status"] == "needs_author_confirm"),
            "blocked_invariant_violations": sum(1 for e in edit_records if e["status"] == "blocked_invariant_violation"),
            "total_high_invariant_violations": invariant_violations_total,
            "modality_drift_count": modality_drift_count,
            "numeric_drift_count": numeric_drift_count,
            "citation_drop_count": citation_drop_count,
            "source_prose_findings": src_prose["total_findings"],
            "revised_prose_findings": rev_prose["total_findings"],
            "glossary_violations_before": src_gloss["glossary_violations_count"],
            "glossary_violations_after": rev_gloss["glossary_violations_count"],
            "abbreviation_issues_after": rev_gloss["abbreviation_issues_count"],
            "spelling_drift_after": rev_gloss["spelling_drift_count"],
        },
        "document_invariants": doc_inv,
        "false_positive_guard": fp_report,
        "glossary_audit_before": src_gloss,
        "glossary_audit_after": rev_gloss,
        "prose_audit_before": src_prose,
        "prose_audit_after": rev_prose,
        "edits": edit_records,
    }


def render_redline_markdown(audit: dict[str, Any]) -> str:
    """Render structured sentence-level redline diff and audit summary as Markdown."""
    s = audit["summary"]
    lines = [
        "# Academic Language Polishing Redline & Invariant Audit",
        "",
        f"- **Target Register**: `{audit['register']}`",
        f"- **Invariant Gate Passed**: `{audit['passed']}`",
        f"- **Sentences**: {s['source_sentences']} source -> {s['revised_sentences']} revised ({s['edited_sentences']} edited)",
        f"- **Statuses**: `applied={s['applied_count']}`, `rejected_false_positive={s['rejected_false_positive_count']}`, `needs_author_confirm={s['needs_author_confirm_count']}`, `blocked_invariant_violation={s['blocked_invariant_violations']}`",
        f"- **Hard Invariants**: modality drift=`{s['modality_drift_count']}`, numeric drift=`{s['numeric_drift_count']}`, citation drops=`{s['citation_drop_count']}`",
        f"- **De-AI / Anti-Translationese Findings**: {s['source_prose_findings']} -> {s['revised_prose_findings']}",
        f"- **Glossary Violations**: {s['glossary_violations_before']} -> {s['glossary_violations_after']} | **Abbreviation Issues**: {s['abbreviation_issues_after']} | **Spelling Drift**: {s['spelling_drift_after']}",
        "",
        "## Sentence-Level Revision Log",
        "",
        "| ID | Section | Edit Type | Status | Original | Revised | Rationale |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in audit["edits"]:
        orig = e["original"].replace("|", "/").replace("\n", " ")[:140]
        rev = e["revised"].replace("|", "/").replace("\n", " ")[:140]
        rat = e["rationale"].replace("|", "/").replace("\n", " ")[:140]
        lines.append(
            f"| {e['sentence_id']} | {e['section']} | `{e['edit_type']}` | `{e['status']}` | {orig} | {rev} | {rat} |"
        )

    if audit["false_positive_guard"]["rejected_false_positives"]:
        lines.extend(["", "## Dismissed False-Positive Grammar Alerts", ""])
        for fp in audit["false_positive_guard"]["rejected_false_positives"]:
            lines.append(f"- **{fp['alert_id']}** (`{fp['sentence_id']}`, rule `{fp['rule_id']}`): {fp['rationale']}")

    ga = audit["glossary_audit_after"]
    if ga["glossary_violations"] or ga["abbreviation_issues"] or ga["spelling_drift"]:
        lines.extend(["", "## Terminology & Abbreviation Consistency Notes", ""])
        for v in ga["glossary_violations"]:
            lines.append(f"- [Glossary] `{v['sentence_id']}` ({v['section']}): `{v['found_variant']}` -> required `{v['required_canonical']}`")
        for a in ga["abbreviation_issues"]:
            lines.append(f"- [Abbreviation] `{a['abbreviation']}`: `{a['issue']}` ({a})")
        for sd in ga["spelling_drift"]:
            lines.append(f"- [Spelling/Hyphen] `{sd['normalized_term']}`: `{sd['observed_forms']}`")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentence-level academic polishing invariant and de-AI engine.")
    parser.add_argument("--source", required=True, help="Path to original manuscript (.md/.txt/.tex/.docx).")
    parser.add_argument("--revised", default="", help="Path to polished manuscript (.md/.txt/.tex/.docx). Omit with --scan-only.")
    parser.add_argument("--scan-only", action="store_true", help="Pre-polish diagnostic scan of --source without a revised file.")
    parser.add_argument("--glossary", default="", help="Optional glossary JSON or CSV path.")
    parser.add_argument("--candidate-alerts", default="", help="Optional JSON list of grammar checker alerts to test against false-positive guard.")
    parser.add_argument("--edits-json", default="", help="Optional JSON list of explicit edit rationales keyed by sentence_id.")
    parser.add_argument("--register", default="journal_article", choices=["journal_article", "thesis", "grant_proposal", "rebuttal", "abstract"])
    parser.add_argument("--out-audit-json", required=True, help="Output polishing_audit.json path.")
    parser.add_argument("--out-redline-md", default="", help="Output polishing_redline_diff.md path.")
    parser.add_argument("--out-result-json", default="", help="Optional structured/Harbor 5-field result.json path.")
    parser.add_argument("--case-id", default="CASE-UNSPECIFIED", help="Case ID for result.json.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if hard invariants or glossary checks fail.")
    args = parser.parse_args(argv)

    src_path = Path(args.source)
    src_doc = read_document_text(src_path)
    if not src_doc["ok"]:
        print(json.dumps({"passed": False, "error": f"Failed to read source: {src_doc['meta']}"}, ensure_ascii=False))
        return 2

    glossary = load_glossary(Path(args.glossary)) if args.glossary else []
    alerts = json.loads(Path(args.candidate_alerts).read_text(encoding="utf-8")) if args.candidate_alerts and Path(args.candidate_alerts).is_file() else []
    explicit = json.loads(Path(args.edits_json).read_text(encoding="utf-8")) if args.edits_json and Path(args.edits_json).is_file() else []

    if args.scan_only or not args.revised:
        sents = split_sentences_with_sections(src_doc["text"])
        scan = {
            "mode": "scan_only",
            "source": str(src_path),
            "register": args.register,
            "sentence_count": len(sents),
            "invariants": extract_invariants(src_doc["text"]),
            "false_positive_guard": evaluate_false_positive_guard(sents, alerts),
            "glossary_audit": audit_glossary_and_abbreviations(src_doc["text"], sents, glossary),
            "prose_audit": audit_prose_extended(src_doc["text"], sents),
        }
        out_p = Path(args.out_audit_json)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(scan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"mode": "scan_only", "sentences": len(sents), "prose_findings": scan["prose_audit"]["total_findings"], "glossary_violations": scan["glossary_audit"]["glossary_violations_count"]}, ensure_ascii=False))
        return 0

    rev_path = Path(args.revised)
    rev_doc = read_document_text(rev_path)
    if not rev_doc["ok"]:
        print(json.dumps({"passed": False, "error": f"Failed to read revised: {rev_doc['meta']}"}, ensure_ascii=False))
        return 2

    audit = align_and_audit(
        src_doc["text"],
        rev_doc["text"],
        glossary=glossary,
        candidate_alerts=alerts,
        explicit_edits=explicit,
        register=args.register,
    )

    out_j = Path(args.out_audit_json)
    out_j.parent.mkdir(parents=True, exist_ok=True)
    out_j.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.out_redline_md:
        out_m = Path(args.out_redline_md)
        out_m.parent.mkdir(parents=True, exist_ok=True)
        out_m.write_text(render_redline_markdown(audit), encoding="utf-8")

    if args.out_result_json:
        gaps: list[dict[str, Any]] = []
        if audit["summary"]["blocked_invariant_violations"] > 0:
            gaps.append({"code": "INVARIANT_VIOLATION_BLOCKED", "count": audit["summary"]["blocked_invariant_violations"]})
        if audit["glossary_audit_after"]["internal_glossary_conflicts"]:
            gaps.append({"code": "GLOSSARY_INTERNAL_CONFLICT", "items": audit["glossary_audit_after"]["internal_glossary_conflicts"]})
        res = build_writing_result(
            case_id=args.case_id,
            task_id="academic-language-polishing",
            deliverable={
                "polished_manuscript": str(rev_path.name),
                "redline_diff": Path(args.out_redline_md).name if args.out_redline_md else None,
                "audit_json": out_j.name,
                "register": args.register,
            },
            preserved_facts={
                "invariants_preserved": audit["document_invariants"]["preserved"],
                "source_invariants": audit["document_invariants"]["source_invariants"],
                "revised_invariants": audit["document_invariants"]["revised_invariants"],
                "modality_drift_count": audit["summary"]["modality_drift_count"],
                "numeric_drift_count": audit["summary"]["numeric_drift_count"],
                "citation_drop_count": audit["summary"]["citation_drop_count"],
            },
            evidence_map=[
                {
                    "sentence_id": e["sentence_id"],
                    "section": e["section"],
                    "source_file": src_path.name,
                    "status": e["status"],
                    "invariants_preserved": e["invariants_preserved"],
                }
                for e in audit["edits"]
            ],
            change_log=[
                {
                    "sentence_id": e["sentence_id"],
                    "edit_type": e["edit_type"],
                    "status": e["status"],
                    "original": e["original"],
                    "revised": e["revised"],
                    "rationale": e["rationale"],
                }
                for e in audit["edits"]
            ],
            artifacts=[p for p in [rev_path.name, Path(args.out_redline_md).name if args.out_redline_md else "", out_j.name, "result.json"] if p],
            sources=[{"file": src_path.name, "role": "source_manuscript"}],
            gaps=gaps,
            limitations=[
                "Offline deterministic invariant, glossary, false-positive, and de-AI checks do not replace domain-expert scientific peer review.",
            ],
            verification={"polishing_invariant_engine_passed": audit["passed"], "summary": audit["summary"]},
            status="ok" if audit["passed"] else "partial_with_gaps",
        )
        out_r = Path(args.out_result_json)
        out_r.parent.mkdir(parents=True, exist_ok=True)
        out_r.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"passed": audit["passed"], **audit["summary"]}, ensure_ascii=False))
    if args.strict and not audit["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
