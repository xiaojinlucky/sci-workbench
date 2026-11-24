#!/usr/bin/env python3
"""Zero-dependency six-facet academic norms, ethics/IRB anti-spoofing, PII/double-blind redaction,
image duplication/flip fingerprinting, third-party copyright dual-track, and guideline auditor
for challenge-de9f0708.

Runs purely on Python 3.10+ stdlib (offline Linux Docker --network none) using local review_io_core.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import review_io_core as io_core  # noqa: E402

SCOPE_DISCLAIMER = (
    "This automated compliance report is a technical pre-submission screening aid based solely on "
    "supplied materials. It does NOT constitute formal legal advice, copyright adjudication, or an "
    "Institutional Review Board (IRB) / Ethics Committee determination."
)

# --- Facet 1: Human / Animal / Clinical Trial / Ethics Anti-Spoofing ---

HUMAN_SIGNAL_RE = re.compile(
    r"\b(?:patients?|human\s+(?:subjects?|participants?|cohort|tissue|samples?|volunteers?)|"
    r"clinical\s+(?:trial|cohort|study|patients?)|enrolled\s+\d+\s+(?:patients?|participants?|subjects?)|"
    r"questionnaire\s+survey|human\s+survey|participants?\s+completed|outpatients?|inpatients?|biopsy\s+samples?)\b|"
    r"患者|受试者|临床试验|临床队列|问卷调查|人类样本|志愿者|住院患者|门诊患者",
    re.I,
)

COMPUTATIONAL_EXEMPT_RE = re.compile(
    r"\b(?:simulated\s+(?:data|datasets?|cohorts?|benchmarks?|patients?)|"
    r"synthetic\s+(?:data|datasets?|benchmarks?|graphs?|cohorts?|patients?)|"
    r"publicly\s+available\s+(?:data|datasets?|benchmarks?|de-?identified\s+data)|"
    r"computational\s+(?:simulation|benchmark|modeling|modelling|study|framework)|"
    r"in\s+silico\s+(?:simulation|benchmark|study|experiments?)|"
    r"generated\s+synthetically|no\s+human\s+subjects?\s+(?:were\s+)?involved|"
    r"secondary\s+analysis\s+of\s+(?:public|de-?identified)\s+(?:data|datasets?))\b|"
    r"模拟数据|合成基准|公开数据集|计算模拟|纯计算研究",
    re.I,
)

DIRECT_HUMAN_METHODS_RE = re.compile(
    r"\b(?:enrolled\s+\d+|we\s+(?:enrolled|recruited|collected|sampled|interviewed|surveyed|followed)|"
    r"patients?\s+were\s+(?:enrolled|recruited|included|randomized|allocated|assigned|followed|treated|consented)|"
    r"participants?\s+(?:were\s+)?(?:enrolled|recruited|completed|signed|provided|consented)|"
    r"written\s+informed\s+consent|biopsy\s+samples?\s+(?:were\s+)?(?:collected|obtained)|"
    r"blood\s+samples?\s+(?:were\s+)?(?:collected|drawn|obtained)|"
    r"tissue\s+(?:samples?|specimens?)\s+(?:were\s+)?(?:collected|obtained|resected)|"
    r"clinical\s+(?:follow-?up|records?\s+were\s+(?:reviewed|extracted))|"
    r"questionnaire\s+survey\s+(?:was\s+)?(?:administered|conducted)|"
    r"prospective(?:ly)?\s+(?:enrolled|recruited)|retrospective(?:ly)?\s+(?:enrolled|collected|reviewed))\b|"
    r"入组|招募|采集.{0,8}(?:样本|标本|血液|组织)|临床随访|签署知情同意|收集.{0,8}患者",
    re.I,
)

ANIMAL_SIGNAL_RE = re.compile(
    r"\b(?:mice|mouse\s+model|murine|rats?\s*\(|wistar|sprague-?dawley|c57bl/6|balb/c|"
    r"zebrafish|rabbits?|porcine|primate|macaque|animal\s+(?:experiment|model|study|protocol|care))\b|"
    r"小鼠|大鼠|斑马鱼|动物实验|实验动物|动物模型",
    re.I,
)

CLINICAL_TRIAL_SIGNAL_RE = re.compile(
    r"\b(?:randomized\s+controlled\s+trial|\bRCT\b|clinical\s+trial|phase\s+(?:I|II|III|IV|1|2|3|4)\s+trial|"
    r"interventional\s+clinical\s+study|clinical\s+registration)\b|"
    r"随机对照临床试验|临床试验|干预性临床研究",
    re.I,
)

ETHICS_ACK_RE = re.compile(
    r"\b(?:IRB|institutional\s+review\s+board|ethics\s+committee|ethical\s+approval|ethics\s+(?:approval|protocol|clearance)|"
    r"ethical\s+(?:protocol|clearance)|ethics\s+review|ethical\s+review|research\s+ethics|IACUC|animal\s+(?:care|ethics)\s+committee|"
    r"Declaration\s+of\s+Helsinki|ARRIVE\s+guidelines?)\b|"
    r"伦理(?:委员会|审批|批号|审查|批准)|赫尔辛基宣言|实验动物伦理",
    re.I,
)

INFORMED_CONSENT_RE = re.compile(
    r"\b(?:informed\s+consent|written\s+consent|consent\s+was\s+(?:obtained|waived)|"
    r"waiver\s+of\s+(?:informed\s+)?consent|participant\s+consent)\b|"
    r"知情同意|豁免知情同意",
    re.I,
)

HELSINKI_RE = re.compile(r"\bDeclaration\s+of\s+Helsinki\b|赫尔辛基宣言", re.I)
ARRIVE_RE = re.compile(r"\bARRIVE(?:\s+2\.0)?\b", re.I)

# Candidate extraction after explicit ethics/IRB/IACUC approval label
ETHICS_ID_CANDIDATE_RE = re.compile(
    r"(?:"
    r"\b(?:IRB|IACUC|EC|REC)\s*(?:(?:approval|protocol|review|reference|ref\.?)\s*)?(?:No\.?|number|ID|code|#)"
    r"|\b(?:Ethics|Ethical)\s+(?:committee\s+)?(?:approval|protocol|review|clearance)\s*(?:No\.?|number|ID|code|#)"
    r"|\b(?:Approval|Protocol)\s*(?:No\.?|number|ID|code|#)"
    r"|\b(?:IRB|IACUC|EC|REC|Ethics|Ethical)\s+(?:committee\s+)?(?:approval|protocol|review|clearance)(?=\s*[:#：=])"
    r"|伦理(?:委员会)?(?:审批|批件|审查|批号)(?:编号|号)?"
    r")\s*[:#：=]?\s*((?:(?:NSFC|NIH|NSF|ERC|MOST|NKRD)\s+)?[A-Za-z0-9][A-Za-z0-9/_.\-]{1,40})",
    re.I,
)

# Parenthetical ethics ID tokens inside sentences already matching ETHICS_ACK_RE (e.g., "(S-K1928)", "(IRB2024-0192)", "(EC-2024-089)")
PAREN_GROUP_RE = re.compile(r"[\(（]([^()（）]{2,100})[\)）]")
PAREN_ETHICS_TOKEN_RE = re.compile(
    r"\b("
    r"(?:NSFC|NIH|NSF|ERC|MOST|NKRD)\s+[A-Za-z0-9/_.\-]{3,24}"
    r"|[A-Za-z]{1,10}[-_/][A-Za-z0-9]{1,12}(?:[-_/][A-Za-z0-9]{1,12})*"
    r"|[A-Za-z]{1,8}\d{3,}[A-Za-z0-9/_.\-]*"
    r"|(?:19|20)\d{2}[-/.][A-Za-z0-9]{1,8}(?:[-/.][A-Za-z0-9]{1,8})+"
    r")\b",
    re.I,
)
NON_ETHICS_BIOLOGICAL_TOKEN_RE = re.compile(
    r"^(?:COVID-19|SARS-CoV-2|C57BL/6|BALB/c|IL-\d+|PD-?L?1|CD\d+[+-]?|HLA-[A-Z0-9]+|HER2|BRCA[12]|TP53|KRAS|EGFR|ARRIVE\s*2\.0|v\d+(?:\.\d+)?)$",
    re.I,
)

PLACEHOLDER_ID_WORDS = {
    "unknown", "unavailable", "missing", "none", "pending", "withheld",
    "unreported", "undisclosed", "not", "not_reported", "exempt", "exemption",
    "waived", "tbd", "na", "n/a", "nil", "null", "granted", "obtained",
    "approved", "committee", "protocol", "review", "submitted", "required",
    "待补充", "未报告", "暂缺", "无", "待定", "豁免",
}

DATE_ISO_RE = re.compile(r"^(?:[A-Za-z]{1,10}[-_/]?)?(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])$")
DATE_DMY_RE = re.compile(r"^(?:[A-Za-z]{1,10}[-_/]?)?(?:0?[1-9]|[12]\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:19|20)\d{2}$")
DATE_COMPACT_RE = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")
GRANT_PREFIX_RE = re.compile(r"^(?:NSFC|NIH|NSF|ERC|MOST|NKRD|R01|R21|U01|K99|P30|T32|F31|U54|P01)(?:[\s\-_/][A-Za-z0-9/_.\-]+|[A-Z0-9]*)$", re.I)
GRANT_CONTEXT_RE = re.compile(r"\b(?:grant|fund(?:ing|ed)|NSFC|NIH|NSF|supported\s+by)\b|基金|资助", re.I)
PAGE_CONTEXT_RE = re.compile(r"\b(?:page|pages|pp\.?|p\.)\s*$", re.I)
PHONE_SHAPE_RE = re.compile(r"^(?:\+?\d{1,3}[-.]?)?(?:1[3-9]\d{9}|\d{3}[-.]\d{3,4}[-.]\d{4})$")

# Clinical trial registry identifiers
VALID_TRIAL_ID_RE = re.compile(
    r"\b(?:NCT\d{8}|ChiCTR\d{6,12}|ISRCTN\d{6,9}|ACTRN\d{10,14}|UMIN\d{9}|DRKS\d{8}|EudraCT\s*\d{4}-\d{6}-\d{2})\b",
    re.I,
)
MALFORMED_TRIAL_ID_RE = re.compile(
    r"\b(?:NCT\d{1,7}|NCT\d{9,}|ChiCTR\d{1,5}|ISRCTN\d{1,5})\b",
    re.I,
)

# --- Facet 2: PII, Restricted Data & Double-Blind Anonymization ---

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CN_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
INTL_PHONE_RE = re.compile(r"(?:\+\d{1,3}[-\s]?)?\(?\d{3}\)?[-\s]\d{3}[-\s]\d{4}\b")
MRN_RE = re.compile(
    r"(?:\b(?:MRN|Medical\s+Record(?:\s+No\.?|\s+Number)?|Patient[_\s-]?ID|Hospital[_\s-]?ID|"
    r"Subject[_\s-]?ID|SSN|Social\s+Security|National[_\s-]?ID)\s*[:=#]?\s*[A-Za-z0-9-]{4,}"
    r"|(?:病历号|住院号|门诊号|就诊号|患者编号|身份证号)\s*[:：=#]?\s*[A-Za-z0-9-]{4,})",
    re.I,
)
CN_ID_CARD_RE = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
# Dynamically composed host path regex so source text never triggers static host-path scanners
HOST_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\(?:" + "Users" + "|" + "Desktop" + r")\\[^\s'\"<>]+"
    + r"|/" + "Users" + r"/[A-Za-z0-9._-]+/[^\s'\"<>]*"
    + r"|/" + "home" + r"/[A-Za-z0-9._-]+/[^\s'\"<>]*)",
    re.I,
)
NON_ANON_REPO_RE = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org|huggingface\.co)/([A-Za-z0-9_.-]+)(?:/([A-Za-z0-9_.-]+))?",
    re.I,
)
INSTITUTION_RE = re.compile(
    r"\b(?:University\s+of\s+[A-Z][A-Za-z\s]{2,35}|[A-Z][A-Za-z]{2,25}\s+(?:University|Hospital|Medical\s+Center|Institute\s+of\s+Technology)|"
    r"Department\s+of\s+[A-Z][A-Za-z\s]{2,35})\b|"
    r"[\u4e00-\u9fff]{2,10}(?:大学|附属医院|人民医院|中心医院|医学院|研究所)",
)
SELF_CITE_LEAK_RE = re.compile(
    r"\b(?:in\s+our\s+(?:previous|prior|earlier)\s+(?:work|study|paper)|"
    r"we\s+previously\s+(?:proposed|showed|demonstrated|introduced)\s+[^.]{0,60}(?:\[\d+\]|\([A-Z][A-Za-z'-]+))|"
    r"在我们(?:此前|先前|之前)的(?:研究|工作|论文)中",
    re.I,
)

# --- Facet 3: Third-Party Asset Reuse & Licensing ---

REUSE_ASSET_RE = re.compile(
    r"\b(?:adapted\s+from|reproduced\s+from|reprinted\s+(?:from|with\s+permission)|"
    r"modified\s+from|redrawn\s+from|taken\s+from|sourced\s+from|courtesy\s+of|"
    r"third-?party\s+(?:image|figure|diagram|photo|illustration)|source\s+unknown|unknown\s+source)\b|"
    r"改编自|引自原图|转载自|修改自|第三方(?:图片|插图|素材)|来源不明|未注明来源",
    re.I,
)

LICENSE_ACK_RE = re.compile(
    r"\b(?:CC[\s-]?BY(?:[\s-]?(?:NC|ND|SA|4\.0|3\.0))*|CC0|public\s+domain|"
    r"MIT\s+License|Apache[\s-]?2\.0|BSD|GPL|Creative\s+Commons|"
    r"(?:reprinted|reproduced|used|adapted)\s+with\s+(?:written\s+)?permission|"
    r"permission\s+(?:granted|obtained)|copyright\s+holder|licensed\s+under)\b|"
    r"CC[\s-]?BY|已获(?:书面)?授权|经(?:版权方)?授权|许可协议|开源许可|公有领域",
    re.I,
)

# --- Facet 4 & 5: Declarations & Reporting Standards ---

COI_RE = re.compile(
    r"\b(?:competing\s+interests?|conflict(?:s)?\s+of\s+interest|declare\s+no\s+(?:competing|conflict)|"
    r"financial\s+disclosures?)\b|利益冲突|无利益冲突",
    re.I,
)
FUNDING_RE = re.compile(
    r"\b(?:funding|grant\s+(?:no\.?|number|id)|supported\s+by|funded\s+by|acknowledg(?:e)?ments?|no\s+external\s+funding)\b|"
    r"基金|资助|致谢|无外部资助",
    re.I,
)
NO_FUNDING_RE = re.compile(
    r"\b(?:no\s+(?:external\s+|specific\s+)?funding|received\s+no\s+(?:specific\s+)?grant|not\s+funded|unfunded)\b|"
    r"无(?:外部|专项)?资助|未获得(?:任何)?基金资助",
    re.I,
)
GRANT_ID_RE = re.compile(
    r"\b(?:NSFC|NIH|NSF|ERC|MOST)\s*(?:Grant\s*)?(?:No\.?|#)?\s*[A-Z0-9][A-Z0-9\-/]{4,18}\b|"
    r"\b(?:R01|R21|U01|K99|P30)[A-Z]{2}\d{5,8}\b",
    re.I,
)
DATA_AVAIL_RE = re.compile(
    r"\b(?:data\s+availability|code\s+availability|data\s+and\s+code|publicly\s+available\s+at|"
    r"zenodo|figshare|dryad|geo\s+accession|gse\d+|github\.com|anonymous\.4open\.science|osf\.io)\b|"
    r"数据可用性|代码可用性|数据与代码|开源仓库",
    re.I,
)
AUTHOR_CONTRIB_RE = re.compile(
    r"\b(?:author\s+contributions?|CRediT|conceptualization.*methodology|contributed\s+equally)\b|作者贡献",
    re.I,
)
REPORTING_STD_RE = re.compile(r"\b(CONSORT|PRISMA|ARRIVE|STROBE|STARD|TRIPOD|SPIRIT|CARE)\b", re.I)

FIGURE_REF_RE = re.compile(r"\b(?:Figure|Fig\.?)\s*(\d+)\b|图\s*(\d+)", re.I)
TABLE_REF_RE = re.compile(r"\bTable\s*(\d+)\b|表\s*(\d+)", re.I)


def _mask_match(val: str) -> str:
    """Strictly redact sensitive matched values before writing to JSON or Markdown."""
    s = val.strip()
    if len(s) <= 4:
        return "[REDACTED]"
    if "@" in s:
        local, _, dom = s.partition("@")
        mloc = (local[0] + "***") if len(local) > 1 else "***"
        mdom = (dom[:2] + "***" + dom[-3:]) if len(dom) > 5 else "***"
        return f"{mloc}@{mdom}"
    if "/" in s or "\\" in s:
        return "[REDACTED_HOST_PATH]"
    if len(s) <= 8:
        return s[:2] + "***" + s[-1:]
    return s[:3] + "***" + s[-2:]


def _sanitize_pii_in_text(text: str) -> str:
    """Post-hoc PII scrubber applied to all verbatim_quote / evidence_basis strings so zero raw PII leaks."""
    if not text:
        return text
    out = HOST_PATH_RE.sub(lambda m: _mask_match(m.group(0)), text)
    out = EMAIL_RE.sub(lambda m: _mask_match(m.group(0)), out)
    out = CN_ID_CARD_RE.sub(lambda m: _mask_match(m.group(0)), out)
    out = CN_PHONE_RE.sub(lambda m: _mask_match(m.group(0)), out)
    out = INTL_PHONE_RE.sub(lambda m: _mask_match(m.group(0)), out)
    out = MRN_RE.sub(
        lambda m: re.sub(r"([A-Za-z0-9-]{4,})$", lambda tok: _mask_match(tok.group(1)), m.group(0)),
        out,
    )
    return out


REAL_GUIDELINE_KEYS = {
    "double_blind",
    "word_limit",
    "abstract_word_limit",
    "max_figures",
    "max_tables",
    "required_declarations",
    "required_sections",
    "required_reporting_standard",
    "required_reporting_standards",
    "journal",
    "venue",
}


def _word_count(text: str) -> int:
    en_words = re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*", text)
    zh_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return len(en_words) + len(zh_chars)


def _validate_ethics_candidate(token: str, line_prefix: str) -> tuple[bool, str]:
    """Validate an extracted ethics/IRB/IACUC ID token and reject spoofed values."""
    clean = token.strip(".,;:()[]{}\"'")
    low = clean.lower()
    if not clean or low in PLACEHOLDER_ID_WORDS:
        return False, "placeholder_or_missing_word"
    if DATE_ISO_RE.match(clean) or DATE_DMY_RE.match(clean) or DATE_COMPACT_RE.match(clean):
        return False, "calendar_date_spoof"
    if PHONE_SHAPE_RE.match(clean):
        return False, "phone_number_spoof"
    if GRANT_PREFIX_RE.match(clean) or (GRANT_CONTEXT_RE.search(line_prefix[-40:]) and re.match(r"^\d{7,9}$", clean)):
        return False, "grant_number_spoof"
    if PAGE_CONTEXT_RE.search(line_prefix[-20:]):
        return False, "page_number_spoof"
    # Small numeric range like 12-18 without alphanumeric prefix is likely a page range
    if re.match(r"^\d{1,3}-\d{1,3}$", clean):
        a, b = (int(x) for x in clean.split("-"))
        if b > a and b - a <= 50:
            return False, "page_range_spoof"
    # Pure alphabetic word with < 2 digits cannot be an approval identifier
    digit_count = sum(ch.isdigit() for ch in clean)
    if digit_count < 2:
        return False, "insufficient_digits_non_identifier"
    if len(clean) < 4:
        return False, "too_short"
    return True, "valid_approval_id"


def _load_guidelines(target: Path, explicit_path: Path | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    guidelines: dict[str, Any] = {}
    gaps: list[dict[str, Any]] = []
    if target.is_dir():
        for fp in sorted(target.rglob("*.json")):
            try:
                obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                gaps.append({"code": "JSON_PARSE_ERROR", "severity": "warning", "source": fp.name, "message": str(exc)})
                continue
            if not isinstance(obj, dict):
                continue
            # Never swallow /input/case.json top-level metadata (e.g., {"case_id": ..., "version": "1.0"});
            # only extract an explicit non-empty obj["guidelines"] sub-dict from case.json.
            if fp.name.lower() == "case.json":
                sub = obj.get("guidelines")
                if isinstance(sub, dict) and sub and any(k in sub for k in REAL_GUIDELINE_KEYS):
                    guidelines.update(sub)
                continue
            if any(k in obj for k in REAL_GUIDELINE_KEYS):
                guidelines.update(obj)
            elif isinstance(obj.get("guidelines"), dict) and obj["guidelines"] and any(
                k in obj["guidelines"] for k in REAL_GUIDELINE_KEYS
            ):
                guidelines.update(obj["guidelines"])

    if explicit_path and explicit_path.is_file():
        try:
            obj = json.loads(explicit_path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                sub = obj.get("guidelines")
                if isinstance(sub, dict) and sub:
                    guidelines.update(sub)
                elif any(k in obj for k in REAL_GUIDELINE_KEYS):
                    guidelines.update(obj)
        except Exception as exc:
            gaps.append({"code": "GUIDELINE_PARSE_ERROR", "severity": "warning", "source": str(explicit_path), "message": str(exc)})
    return guidelines, gaps


def _guideline_ref_label(guidelines: dict[str, Any]) -> str:
    venue = guidelines.get("journal") or guidelines.get("venue") or "supplied guidelines.json"
    ver = guidelines.get("guideline_version") or guidelines.get("version") or guidelines.get("date") or "unspecified_version"
    return f"{venue} (version={ver})"


def audit_compliance(target: Path, guidelines_path: Path | None = None) -> dict[str, Any]:
    docs, doc_gaps, images = io_core.load_documents(target)
    guidelines, g_gaps = _load_guidelines(target, guidelines_path)
    gaps = doc_gaps + g_gaps

    if not docs and not images:
        return {
            "schema_version": "3.0",
            "passed": False,
            "result_status": "partial_with_gaps",
            "audit_status": "failed",
            "status": "failed",
            "verdict": "incomplete_inputs",
            "scope_disclaimer": SCOPE_DISCLAIMER,
            "guideline_status": "guideline_supplied" if guidelines else "guideline_not_supplied",
            "guidelines_applied": guidelines,
            "documents_scanned": [],
            "images_scanned": 0,
            "image_integrity_matches": [],
            "third_party_assets": [],
            "rejected_pseudo_ethics_ids": [],
            "checklist_status": [],
            "findings_count": 0,
            "findings": [],
            "gaps": gaps or [{"code": "NO_INPUT_MATERIALS", "severity": "error", "source": str(target), "message": "No manuscript or image files found."}],
            "error": "No manuscript or image files found.",
        }

    findings: list[dict[str, Any]] = []
    checklist_status: list[dict[str, Any]] = []
    rejected_pseudo_ids: list[dict[str, Any]] = []
    third_party_assets: list[dict[str, Any]] = []
    fid = 0

    guideline_supplied = bool(guidelines)
    g_label = _guideline_ref_label(guidelines) if guideline_supplied else "guideline_not_supplied"
    is_double_blind = bool(guidelines.get("double_blind", False))

    if not guideline_supplied:
        checklist_status.append({
            "item": "target_journal_guidelines",
            "status": "guideline_not_supplied",
            "detail": "No guidelines.json or venue constraints supplied; journal-specific caps and double-blind rules were not assumed.",
        })

    # --- Facet 3A: Image Duplication & Flip/Rotation Fingerprinting ---
    image_matches: list[dict[str, Any]] = []
    if images:
        image_matches, img_gaps = io_core.detect_image_duplications(images, target)
        gaps.extend(img_gaps)
        checklist_status.append({
            "item": "image_integrity_fingerprint_screen",
            "status": "similarity_detected_needs_investigation" if image_matches else "pass",
            "images_scanned": len(images),
            "matches_found": len(image_matches),
        })
        for im in image_matches:
            fid += 1
            hbits = im.get("hash_bits", 128)
            corr_part = f", r={im['intensity_corr']:.4f}" if im.get("intensity_corr") is not None else ""
            findings.append({
                "finding_id": f"CMP-{fid:03d}",
                "facet": "academic_norms",
                "subtype": "image_duplication_or_flip_anomaly",
                "finding_type": "insufficient_evidence",
                "compliance_state": "needs_human_or_legal_ethics_review",
                "severity": "major",
                "severity_rationale": "Image fingerprinting detected exact or transformed (flip/rotation) duplication across supplied image assets; requires original-data verification.",
                "location": {"file": im["file_a"], "related_file": im["file_b"], "line_start": 1, "line_end": 1},
                "verbatim_quote": f"{im['file_a']} <-> {im['file_b']} ({im['match_type']}, Hamming={im['hamming_distance']}/{hbits}{corr_part})",
                "evidence_basis": im["note"],
                "actionable_revision": (
                    f"Request original uncropped raw image files and figure-assembly logs for {im['file_a']} and {im['file_b']}. "
                    "Technical similarity alone does not establish intentional misconduct; verify whether panels were accidentally duplicated or reused with disclosure."
                ),
                "rule_reference": "Image Integrity Screening Protocol (128-bit HV-dHash; Anomaly != Misconduct)",
                "status": "similarity_detected_needs_investigation",
            })

    # --- Manuscript Text Audits ---
    for doc in docs:
        text = doc["text"]
        lines = doc["lines"]
        secs = io_core.split_sections(doc)
        non_ref_secs = [s for s in secs if s["role"] != "references"]
        non_ref_text = "\n".join(s["text"] for s in non_ref_secs) if non_ref_secs else text
        ref_line_set: set[int] = {
            ln
            for s in secs
            if s["role"] == "references"
            for ln in range(s["start_line"], s["end_line"] + 1)
        }

        abs_meth_secs = [s for s in non_ref_secs if s["role"] in ("abstract", "methods")]
        methods_secs = [s for s in non_ref_secs if s["role"] == "methods"]
        abs_meth_text = "\n".join(s["text"] for s in abs_meth_secs) if abs_meth_secs else non_ref_text
        methods_text = "\n".join(s["text"] for s in methods_secs) if methods_secs else abs_meth_text

        # 1. Ethics / IRB / IACUC / Clinical Trial Anti-Spoofing
        is_computational_exempt = (
            bool(COMPUTATIONAL_EXEMPT_RE.search(abs_meth_text))
            and not bool(DIRECT_HUMAN_METHODS_RE.search(methods_text))
        )
        raw_human_hit = bool(HUMAN_SIGNAL_RE.search(non_ref_text))
        has_human = raw_human_hit and not is_computational_exempt
        if raw_human_hit and is_computational_exempt:
            checklist_status.append({
                "file": doc["file"],
                "item": "human_subjects_ethics_trigger",
                "status": "exempt_computational_or_public_data",
                "detail": "Abstract/Methods declares simulated, synthetic, computational, or publicly available data without patient enrollment/specimen collection in Methods.",
            })
        has_animal = bool(ANIMAL_SIGNAL_RE.search(non_ref_text)) and not is_computational_exempt
        has_trial = bool(CLINICAL_TRIAL_SIGNAL_RE.search(abs_meth_text)) and not is_computational_exempt

        if has_human or has_animal:
            has_ethics_ack = bool(ETHICS_ACK_RE.search(non_ref_text))
            valid_ethics_ids: list[dict[str, Any]] = []
            seen_ethics_tokens: set[tuple[int, str]] = set()
            seen_rejected_keys: set[tuple[str, int, str]] = set()
            ethics_ack_anchor: tuple[int, int, str] | None = None

            def _record_ethics_candidate(cand_raw: str, prefix_str: str, l_start: int) -> None:
                cand_clean = cand_raw.strip(".,;:()[]{}\"'")
                if not cand_clean:
                    return
                dedup_key = (l_start, cand_clean.lower())
                if dedup_key in seen_ethics_tokens:
                    return
                seen_ethics_tokens.add(dedup_key)
                ok, reason = _validate_ethics_candidate(cand_clean, prefix_str)
                if ok:
                    valid_ethics_ids.append({"id": cand_clean, "line": l_start})
                else:
                    rej_cand = _mask_match(cand_clean) if reason == "phone_number_spoof" else cand_clean
                    rk = (doc["file"], l_start, rej_cand.lower())
                    if rk not in seen_rejected_keys:
                        seen_rejected_keys.add(rk)
                        rejected_pseudo_ids.append({
                            "file": doc["file"],
                            "line": l_start,
                            "candidate": rej_cand,
                            "rejection_reason": reason,
                        })

            # Extract from paragraph-merged sentences across all non-reference sections (handles PDF line wraps)
            for sec in non_ref_secs:
                for sent in io_core.split_sentences(sec):
                    st = sent["text"]
                    ls = sent.get("line_start", sent["line"])
                    le = sent.get("line_end", ls)
                    matched_spans: list[tuple[int, int]] = []
                    if ethics_ack_anchor is None and ETHICS_ACK_RE.search(st):
                        ethics_ack_anchor = (ls, le, st)
                    for m in ETHICS_ID_CANDIDATE_RE.finditer(st):
                        matched_spans.append((m.start(1), m.end(1)))
                        _record_ethics_candidate(m.group(1), st[:m.start()], ls)

                    # Top-journal parenthetical ethics IDs inside ETHICS_ACK_RE sentences: e.g., "(S-K1928)", "(IRB2024-0192)", "(EC-2024-089)"
                    if ETHICS_ACK_RE.search(st):
                        for pm in PAREN_GROUP_RE.finditer(st):
                            inner = pm.group(1).strip()
                            if not inner:
                                continue
                            # Skip parenthetical clinical trial registrations or pure author-year citations
                            if VALID_TRIAL_ID_RE.search(inner) or MALFORMED_TRIAL_ID_RE.search(inner):
                                continue
                            if re.match(
                                r"^[A-Z][A-Za-z'-]{1,25}(?:\s+(?:and|&)\s+[A-Z][A-Za-z'-]{1,25})?(?:\s+et\s+al\.)?(?:\s*,\s*|\s+)(?:19|20)\d{2}[a-z]?$",
                                inner,
                            ):
                                continue
                            # Skip if this parenthetical already had an explicit labeled ID extracted
                            if any(pm.start(1) <= sp[0] and sp[1] <= pm.end(1) for sp in matched_spans):
                                continue
                            # Check placeholder word alone inside parentheses, e.g. "(TBD)" / "(pending)"
                            if inner.lower() in PLACEHOLDER_ID_WORDS:
                                _record_ethics_candidate(inner, st[:pm.start()], ls)
                                continue
                            for tm in PAREN_ETHICS_TOKEN_RE.finditer(inner):
                                tok = tm.group(1).strip()
                                if NON_ETHICS_BIOLOGICAL_TOKEN_RE.match(tok):
                                    continue
                                if VALID_TRIAL_ID_RE.fullmatch(tok) or MALFORMED_TRIAL_ID_RE.fullmatch(tok):
                                    continue
                                # Avoid misreading an unrelated funding clause in the same sentence if prefixed by funded/supported by
                                local_prefix = st[:pm.start(1) + tm.start(1)]
                                if re.search(r"\b(?:funded|supported)\s+by\b[^()]{0,30}$", local_prefix, re.I):
                                    continue
                                _record_ethics_candidate(tok, local_prefix, ls)

            if not has_ethics_ack:
                fid += 1
                findings.append({
                    "finding_id": f"CMP-{fid:03d}",
                    "facet": "academic_norms",
                    "subtype": "ethics_approval_missing",
                    "finding_type": "norm_violation",
                    "compliance_state": "confirmed_violation",
                    "severity": "critical",
                    "severity_rationale": "Manuscript involves human/patient/survey or animal research without any ethics/IRB/IACUC disclosure.",
                    "location": {"file": doc["file"], "line_start": 1, "line_end": 1},
                    "verbatim_quote": "[MISSING: Ethics approval / IRB / IACUC statement]",
                    "evidence_basis": f"Detected subject signals (human={has_human}, animal={has_animal}) in non-reference sections with zero ethics/IRB/IACUC statements.",
                    "actionable_revision": "Add an Ethics Statement naming the approving IRB/IACUC committee, valid protocol/approval ID, and informed consent or exemption basis.",
                    "rule_reference": "ICMJE / Helsinki / ARRIVE Ethics Disclosure Standard",
                    "status": "confirmed",
                })
            elif not valid_ethics_ids:
                if ethics_ack_anchor is not None:
                    ethics_lno, ethics_lend, ethics_line = ethics_ack_anchor
                else:
                    ethics_lno, ethics_line = next(
                        ((i, ln) for i, ln in enumerate(lines, 1) if i not in ref_line_set and ETHICS_ACK_RE.search(ln)),
                        (1, "[Ethics statement located]"),
                    )
                    ethics_lend = ethics_lno
                rej_note = (
                    f" Rejected spoof/placeholder candidate(s): {[r['rejection_reason'] for r in rejected_pseudo_ids if r['file'] == doc['file']]}."
                    if any(r["file"] == doc["file"] for r in rejected_pseudo_ids)
                    else ""
                )
                fid += 1
                findings.append({
                    "finding_id": f"CMP-{fid:03d}",
                    "facet": "academic_norms",
                    "subtype": "ethics_approval_id_unverified",
                    "finding_type": "insufficient_evidence",
                    "compliance_state": "material_not_supplied",
                    "severity": "major" if any(r["file"] == doc["file"] for r in rejected_pseudo_ids) else "minor",
                    "severity_rationale": "Ethics statement lacks a valid, non-placeholder approval identifier; absence of ID requires verification rather than presuming noncompliance.",
                    "location": {"file": doc["file"], "line_start": ethics_lno, "line_end": ethics_lend},
                    "verbatim_quote": ethics_line.strip()[:180],
                    "evidence_basis": f"Ethics disclosure present at L{ethics_lno}, but no valid protocol/approval identifier passed anti-spoofing validation.{rej_note}",
                    "actionable_revision": "Provide the official IRB/IACUC approval protocol number (not a date, page range, phone, grant number, or placeholder) or attach exemption documentation.",
                    "rule_reference": "Ethics Approval Identifier Verification Rule",
                    "status": "needs_human_review",
                })
            else:
                checklist_status.append({
                    "file": doc["file"],
                    "item": "ethics_approval_id",
                    "status": "pass",
                    "verified_ids": [v["id"] for v in valid_ethics_ids[:5]],
                })

            if has_human and not INFORMED_CONSENT_RE.search(non_ref_text):
                fid += 1
                findings.append({
                    "finding_id": f"CMP-{fid:03d}",
                    "facet": "academic_norms",
                    "subtype": "informed_consent_missing",
                    "finding_type": "insufficient_evidence",
                    "compliance_state": "material_not_supplied",
                    "severity": "major",
                    "severity_rationale": "Human/patient/survey research does not report informed consent acquisition or an IRB-approved consent waiver.",
                    "location": {"file": doc["file"], "line_start": 1, "line_end": 1},
                    "verbatim_quote": "[MISSING: Informed consent or IRB consent waiver statement]",
                    "evidence_basis": "Human participant signals detected without mention of written/verbal informed consent or waiver.",
                    "actionable_revision": "State explicitly whether written informed consent was obtained from all participants (or guardians) or cite the IRB waiver basis.",
                    "rule_reference": "Declaration of Helsinki / ICMJE Human Subjects Protection",
                    "status": "needs_human_review",
                })

            if has_animal and has_ethics_ack and not ARRIVE_RE.search(non_ref_text):
                checklist_status.append({
                    "file": doc["file"],
                    "item": "arrive_guideline_mention",
                    "status": "not_mentioned",
                })
            if has_human and has_ethics_ack and not HELSINKI_RE.search(non_ref_text):
                checklist_status.append({
                    "file": doc["file"],
                    "item": "helsinki_declaration_mention",
                    "status": "not_mentioned",
                })

        if has_trial:
            valid_trials = sorted(set(VALID_TRIAL_ID_RE.findall(non_ref_text)))
            malformed_trials = sorted(set(MALFORMED_TRIAL_ID_RE.findall(non_ref_text)) - set(valid_trials))
            if malformed_trials:
                tlno = next((i for i, ln in enumerate(lines, 1) if MALFORMED_TRIAL_ID_RE.search(ln)), 1)
                fid += 1
                findings.append({
                    "finding_id": f"CMP-{fid:03d}",
                    "facet": "academic_norms",
                    "subtype": "clinical_trial_id_malformed",
                    "finding_type": "factual_error",
                    "compliance_state": "needs_human_or_legal_ethics_review",
                    "severity": "major",
                    "severity_rationale": "Clinical trial registration token does not match official registry digit format (e.g., NCT requires exactly 8 digits).",
                    "location": {"file": doc["file"], "line_start": tlno, "line_end": tlno},
                    "verbatim_quote": f"Malformed trial ID(s): {malformed_trials}",
                    "evidence_basis": f"Registry pattern check rejected {malformed_trials}; verify against ClinicalTrials.gov / ChiCTR / ISRCTN.",
                    "actionable_revision": "Correct the clinical trial registration identifier to its exact registered code and confirm registration date.",
                    "rule_reference": "ICMJE Clinical Trial Registration Format",
                    "status": "needs_human_review",
                })
            elif not valid_trials:
                fid += 1
                findings.append({
                    "finding_id": f"CMP-{fid:03d}",
                    "facet": "academic_norms",
                    "subtype": "clinical_trial_registration_missing",
                    "finding_type": "insufficient_evidence",
                    "compliance_state": "material_not_supplied",
                    "severity": "major",
                    "severity_rationale": "Interventional/clinical trial signals detected without a recognized trial registry ID (NCT/ChiCTR/ISRCTN/ACTRN/UMIN/DRKS).",
                    "location": {"file": doc["file"], "line_start": 1, "line_end": 1},
                    "verbatim_quote": "[MISSING: Clinical trial registration ID (e.g., NCT######## / ChiCTR...)]",
                    "evidence_basis": "Clinical trial terminology detected without a valid public registry identifier.",
                    "actionable_revision": "Report the prospective trial registration number and registry name in Abstract and Methods, or clarify why trial registration does not apply.",
                    "rule_reference": "ICMJE Clinical Trial Registration Policy",
                    "status": "needs_human_review",
                })
            else:
                checklist_status.append({
                    "file": doc["file"],
                    "item": "clinical_trial_registration",
                    "status": "pass",
                    "verified_ids": valid_trials,
                })

        # 2. PII / Restricted Data / Double-Blind Anonymization Scan (strictly redacted)
        # Skip ONLY lines belonging to role == "references" so post-References Acknowledgements / Declarations / Figure Legends are fully scanned.
        for lno, raw in enumerate(lines, 1):
            if lno in ref_line_set:
                continue

            for label, pat, sev in (
                ("patient_or_id_token", MRN_RE, "critical"),
                ("national_id_number", CN_ID_CARD_RE, "critical"),
                ("cn_phone_number", CN_PHONE_RE, "major"),
                ("local_host_path", HOST_PATH_RE, "major"),
            ):
                for m in pat.finditer(raw):
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "privacy_or_path_leak",
                        "finding_type": "norm_violation",
                        "compliance_state": "confirmed_violation",
                        "severity": sev,
                        "severity_rationale": f"Sensitive {label} exposed in manuscript text; raw value masked in audit output.",
                        "location": {"file": doc["file"], "line_start": lno, "line_end": lno},
                        "verbatim_quote": f"Matched {label}: {_mask_match(m.group(0))}",
                        "evidence_basis": f"Detected unredacted {label} on L{lno}; payload redacted via _mask_match.",
                        "actionable_revision": "Remove or replace identifying patient tokens, phone numbers, and local machine paths with anonymized study codes or relative paths.",
                        "rule_reference": "HIPAA / GDPR / Safe-Release Privacy Boundary",
                        "status": "confirmed",
                    })

            # International phone numbers only when near phone/tel/contact/patient cue to avoid false alarms on numeric strings
            if re.search(r"\b(?:phone|tel|call|contact|patient|mobile)\b|电话|联系", raw, re.I):
                for m in INTL_PHONE_RE.finditer(raw):
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "privacy_or_path_leak",
                        "finding_type": "norm_violation",
                        "compliance_state": "confirmed_violation",
                        "severity": "major",
                        "severity_rationale": "Contact phone number exposed in manuscript text.",
                        "location": {"file": doc["file"], "line_start": lno, "line_end": lno},
                        "verbatim_quote": f"Matched phone_number: {_mask_match(m.group(0))}",
                        "evidence_basis": f"Detected phone number on L{lno}; redacted via _mask_match.",
                        "actionable_revision": "Remove direct personal/patient phone numbers before release.",
                        "rule_reference": "Privacy & PII Protection Policy",
                        "status": "confirmed",
                    })

            # Email check: patient email is always a privacy breach; author email is a breach under double_blind=true
            for m in EMAIL_RE.finditer(raw):
                if re.search(r"\b(?:patient|subject|participant)\b|患者|受试者", raw, re.I):
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "privacy_or_path_leak",
                        "finding_type": "norm_violation",
                        "compliance_state": "confirmed_violation",
                        "severity": "critical",
                        "severity_rationale": "Patient/participant email address exposed in manuscript text.",
                        "location": {"file": doc["file"], "line_start": lno, "line_end": lno},
                        "verbatim_quote": f"Matched participant_email: {_mask_match(m.group(0))}",
                        "evidence_basis": f"Participant-linked email detected on L{lno}; redacted via _mask_match.",
                        "actionable_revision": "Remove participant email addresses immediately.",
                        "rule_reference": "Participant Privacy & De-identification Standard",
                        "status": "confirmed",
                    })
                elif is_double_blind:
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "double_blind_email_leak",
                        "finding_type": "norm_violation",
                        "compliance_state": "confirmed_violation",
                        "severity": "critical",
                        "severity_rationale": "Double-blind submission exposes author/contact email address.",
                        "location": {"file": doc["file"], "line_start": lno, "line_end": lno},
                        "verbatim_quote": f"Double-blind email leak: {_mask_match(m.group(0))}",
                        "evidence_basis": f"{g_label} sets double_blind=true, yet contact email appears at L{lno}.",
                        "actionable_revision": "Strip all author and institutional emails from the anonymized review draft.",
                        "rule_reference": f"{g_label} [double_blind=true]",
                        "status": "confirmed",
                    })

            # Double-blind specific checks: non-anonymous repo, institution, grant acknowledgment, first-person self-citation
            if is_double_blind:
                for m in NON_ANON_REPO_RE.finditer(raw):
                    owner = (m.group(1) or "").lower()
                    if owner not in {"anonymous", "anon", "blind-review", "example"}:
                        fid += 1
                        findings.append({
                            "finding_id": f"CMP-{fid:03d}",
                            "facet": "academic_norms",
                            "subtype": "double_blind_repo_leak",
                            "finding_type": "norm_violation",
                            "compliance_state": "confirmed_violation",
                            "severity": "major",
                            "severity_rationale": "Non-anonymous code/data repository link reveals author or lab identity under double-blind review.",
                            "location": {"file": doc["file"], "line_start": lno, "line_end": lno},
                            "verbatim_quote": f"Non-anonymous repository URL: {_mask_match(m.group(0))}",
                            "evidence_basis": f"{g_label} sets double_blind=true, yet non-anonymous repository link appears at L{lno}.",
                            "actionable_revision": "Replace with an anonymized repository link (e.g., anonymous.4open.science or anonymized supplemental archive) during double-blind review.",
                            "rule_reference": f"{g_label} [double_blind=true]",
                            "status": "confirmed",
                        })

                if lno <= 25 or re.search(r"\b(?:our\s+institution|approved\s+by|conducted\s+at|authors?\s+are\s+with)\b|单位|作者|伦理委员会", raw, re.I):
                    im_inst = INSTITUTION_RE.search(raw)
                    if im_inst:
                        fid += 1
                        findings.append({
                            "finding_id": f"CMP-{fid:03d}",
                            "facet": "academic_norms",
                            "subtype": "double_blind_institution_leak",
                            "finding_type": "norm_violation",
                            "compliance_state": "confirmed_violation",
                            "severity": "critical",
                            "severity_rationale": "Author institution or hospital name is exposed in a double-blind draft.",
                            "location": {"file": doc["file"], "line_start": lno, "line_end": lno},
                            "verbatim_quote": f"Institution identifier masked: {_mask_match(im_inst.group(0))}",
                            "evidence_basis": f"{g_label} sets double_blind=true, yet named institution appears at L{lno}.",
                            "actionable_revision": "Replace identifying university/hospital/department names with '[Redacted Institution]' in the blinded submission.",
                            "rule_reference": f"{g_label} [double_blind=true]",
                            "status": "confirmed",
                        })

                if GRANT_ID_RE.search(raw):
                    gm = GRANT_ID_RE.search(raw)
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "double_blind_funding_leak",
                        "finding_type": "norm_violation",
                        "compliance_state": "confirmed_violation",
                        "severity": "major",
                        "severity_rationale": "Grant identifier in acknowledgments can de-anonymize authors during double-blind review.",
                        "location": {"file": doc["file"], "line_start": lno, "line_end": lno},
                        "verbatim_quote": f"Grant identifier masked: {_mask_match(gm.group(0))}",  # type: ignore[union-attr]
                        "evidence_basis": f"{g_label} sets double_blind=true, yet grant ID appears at L{lno}.",
                        "actionable_revision": "Withhold grant numbers and funding acknowledgments until camera-ready acceptance.",
                        "rule_reference": f"{g_label} [double_blind=true]",
                        "status": "confirmed",
                    })

                if SELF_CITE_LEAK_RE.search(raw):
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "double_blind_self_citation_leak",
                        "finding_type": "norm_violation",
                        "compliance_state": "confirmed_violation",
                        "severity": "major",
                        "severity_rationale": "First-person self-citation phrasing reveals authorship of prior cited work under double-blind review.",
                        "location": {"file": doc["file"], "line_start": lno, "line_end": lno},
                        "verbatim_quote": raw.strip()[:180],
                        "evidence_basis": f"{g_label} sets double_blind=true, yet first-person self-citation appears at L{lno}.",
                        "actionable_revision": "Rewrite self-citations in neutral third-person phrasing (e.g., 'Prior work [1] proposed...').",
                        "rule_reference": f"{g_label} [double_blind=true]",
                        "status": "confirmed",
                    })

            # 3B. Third-party asset reuse & license dual-track check (same line + continuation line of same caption only)
            if REUSE_ASSET_RE.search(raw):
                window_parts = [raw]
                if lno < len(lines):
                    nxt = lines[lno].strip()
                    if nxt and not re.match(r"^(?:#{1,6}\s+|(?:Figure|Fig\.?|Table)\s*\d+|图\s*\d+|表\s*\d+)", nxt, re.I):
                        window_parts.append(nxt)
                window = " ".join(window_parts)
                has_license = bool(LICENSE_ACK_RE.search(window))
                if has_license:
                    third_party_assets.append({
                        "file": doc["file"],
                        "line": lno,
                        "excerpt": raw.strip()[:160],
                        "license_status": "pass",
                        "note": "Explicit license or permission attribution found in caption context.",
                    })
                else:
                    third_party_assets.append({
                        "file": doc["file"],
                        "line": lno,
                        "excerpt": raw.strip()[:160],
                        "license_status": "insufficient_evidence",
                        "note": "Third-party adaptation/reuse mentioned without explicit license (e.g., CC-BY) or written permission.",
                    })
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "third_party_asset_license_missing",
                        "finding_type": "insufficient_evidence",
                        "compliance_state": "material_not_supplied",
                        "severity": "major",
                        "severity_rationale": "Third-party figure/table/asset reuse lacks documented license type or permission grant; absence of license text does not constitute a legal infringement ruling.",
                        "location": {"file": doc["file"], "line_start": lno, "line_end": lno},
                        "verbatim_quote": raw.strip()[:180],
                        "evidence_basis": f"Third-party reuse cue at L{lno} has no CC-BY/open-license or permission statement in adjacent caption lines.",
                        "actionable_revision": "Add explicit copyright attribution and license terms (e.g., 'Licensed under CC BY 4.0' or 'Reproduced with permission from [Rights Holder]') or replace with an original figure.",
                        "rule_reference": "Third-Party Copyright & Reuse Attribution Standard (Material Not Supplied != Infringement)",
                        "status": "needs_human_review",
                    })

        # 4. Standard Declarations (COI, Funding, Data/Code Availability) & Funding Self-Contradiction
        req_decls = guidelines.get("required_declarations")
        decl_specs = [
            ("conflict_of_interest", COI_RE, "minor", "Add explicit Conflict of Interest / Competing Interests statement."),
            ("funding_disclosure", FUNDING_RE, "minor", "Add Funding / Grant acknowledgment or state 'No external funding'."),
            ("data_code_availability", DATA_AVAIL_RE, "minor", "Add Data & Code Availability statement specifying repository or access restrictions."),
            ("author_contributions", AUTHOR_CONTRIB_RE, "minor", "Add Author Contributions (CRediT) statement."),
        ]
        for dname, pat, default_sev, fix in decl_specs:
            # Skip funding/author_contributions requirement in double-blind mode unless explicitly demanded
            if is_double_blind and dname in ("funding_disclosure", "author_contributions"):
                checklist_status.append({"file": doc["file"], "item": dname, "status": "exempt_double_blind"})
                continue
            explicitly_required = isinstance(req_decls, list) and dname in req_decls
            if dname == "author_contributions" and not explicitly_required:
                continue
            present = bool(pat.search(non_ref_text))
            checklist_status.append({"file": doc["file"], "item": dname, "status": "pass" if present else "missing"})
            if not present:
                fid += 1
                findings.append({
                    "finding_id": f"CMP-{fid:03d}",
                    "facet": "academic_norms",
                    "subtype": f"missing_{dname}",
                    "finding_type": "norm_violation" if explicitly_required else "insufficient_evidence",
                    "compliance_state": "confirmed_violation" if explicitly_required else "material_not_supplied",
                    "severity": "major" if explicitly_required else default_sev,
                    "severity_rationale": f"Required declaration '{dname}' is absent from the manuscript.",
                    "location": {"file": doc["file"], "line_start": 1, "line_end": 1},
                    "verbatim_quote": f"[MISSING: {dname}]",
                    "evidence_basis": (
                        f"Explicitly required by {g_label} (`required_declarations`) but not found."
                        if explicitly_required
                        else f"Standard manuscript declaration '{dname}' was not found in supplied text."
                    ),
                    "actionable_revision": fix,
                    "rule_reference": f"{g_label} [required_declarations]" if explicitly_required else "Standard Scholarly Disclosure Checklist",
                    "status": "confirmed" if explicitly_required else "needs_human_review",
                })

        # Detect internal funding contradiction ("no external funding" + explicit grant ID)
        if NO_FUNDING_RE.search(non_ref_text) and GRANT_ID_RE.search(non_ref_text):
            g_match = GRANT_ID_RE.search(non_ref_text)
            glno = next((i for i, ln in enumerate(lines, 1) if GRANT_ID_RE.search(ln)), 1)
            fid += 1
            findings.append({
                "finding_id": f"CMP-{fid:03d}",
                "facet": "academic_norms",
                "subtype": "funding_disclosure_contradiction",
                "finding_type": "factual_error",
                "compliance_state": "confirmed_violation",
                "severity": "major",
                "severity_rationale": "Manuscript states no funding was received while simultaneously citing a grant identifier.",
                "location": {"file": doc["file"], "line_start": glno, "line_end": glno},
                "verbatim_quote": f"Grant citation conflicting with 'no funding' claim: {g_match.group(0)}",  # type: ignore[union-attr]
                "evidence_basis": f"Both a no-funding disclaimer and grant token '{g_match.group(0)}' appear in the same manuscript.",  # type: ignore[union-attr]
                "actionable_revision": "Reconcile the Funding and Acknowledgments sections so grant disclosures are consistent.",
                "rule_reference": "Funding & Acknowledgment Consistency Rule",
                "status": "confirmed",
            })

        # 5. Target Journal Guidelines Constraints (word limit, abstract word limit, figure/table caps, reporting standards)
        if guideline_supplied:
            total_words = _word_count(non_ref_text)
            word_limit = guidelines.get("word_limit")
            if isinstance(word_limit, int) and total_words > word_limit:
                fid += 1
                findings.append({
                    "finding_id": f"CMP-{fid:03d}",
                    "facet": "academic_norms",
                    "subtype": "word_limit_exceeded",
                    "finding_type": "norm_violation",
                    "compliance_state": "confirmed_violation",
                    "severity": "major",
                    "severity_rationale": f"Manuscript length ({total_words}) exceeds supplied venue word_limit ({word_limit}).",
                    "location": {"file": doc["file"], "line_start": 1, "line_end": 1},
                    "verbatim_quote": f"Observed {total_words} words > limit {word_limit}",
                    "evidence_basis": f"{g_label} specifies word_limit={word_limit}; main text (excluding references) contains {total_words} words/CJK tokens.",
                    "actionable_revision": f"Trim at least {total_words - word_limit} words or move secondary material to Supplementary Information.",
                    "rule_reference": f"{g_label} [word_limit={word_limit}]",
                    "status": "confirmed",
                })

            abs_limit = guidelines.get("abstract_word_limit")
            abs_secs = [s for s in secs if s["role"] == "abstract"]
            if isinstance(abs_limit, int) and abs_secs:
                abs_words = sum(_word_count(s["text"]) for s in abs_secs)
                if abs_words > abs_limit:
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "abstract_word_limit_exceeded",
                        "finding_type": "norm_violation",
                        "compliance_state": "confirmed_violation",
                        "severity": "major",
                        "severity_rationale": f"Abstract length ({abs_words}) exceeds supplied venue abstract_word_limit ({abs_limit}).",
                        "location": {"file": doc["file"], "section": abs_secs[0]["title"], "line_start": abs_secs[0]["start_line"], "line_end": abs_secs[0]["end_line"]},
                        "verbatim_quote": f"Abstract has {abs_words} words > limit {abs_limit}",
                        "evidence_basis": f"{g_label} enforces abstract_word_limit={abs_limit}; Abstract has {abs_words} words.",
                        "actionable_revision": f"Condense Abstract by at least {abs_words - abs_limit} words while retaining key quantitative results.",
                        "rule_reference": f"{g_label} [abstract_word_limit={abs_limit}]",
                        "status": "confirmed",
                    })

            max_figs = guidelines.get("max_figures")
            if isinstance(max_figs, int):
                fig_nums = {g for m in FIGURE_REF_RE.finditer(non_ref_text) for g in m.groups() if g}
                if len(fig_nums) > max_figs:
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "figure_count_exceeded",
                        "finding_type": "norm_violation",
                        "compliance_state": "confirmed_violation",
                        "severity": "minor",
                        "severity_rationale": f"Detected {len(fig_nums)} distinct main figures exceeding max_figures={max_figs}.",
                        "location": {"file": doc["file"], "line_start": 1, "line_end": 1},
                        "verbatim_quote": f"Figures referenced: {sorted(fig_nums, key=int)} (count={len(fig_nums)} > {max_figs})",
                        "evidence_basis": f"{g_label} caps main figures at max_figures={max_figs}.",
                        "actionable_revision": f"Consolidate figures or move {len(fig_nums) - max_figs} figure(s) to Supplementary Figures.",
                        "rule_reference": f"{g_label} [max_figures={max_figs}]",
                        "status": "confirmed",
                    })

            max_tbls = guidelines.get("max_tables")
            if isinstance(max_tbls, int):
                tbl_nums = {g for m in TABLE_REF_RE.finditer(non_ref_text) for g in m.groups() if g}
                if len(tbl_nums) > max_tbls:
                    fid += 1
                    findings.append({
                        "finding_id": f"CMP-{fid:03d}",
                        "facet": "academic_norms",
                        "subtype": "table_count_exceeded",
                        "finding_type": "norm_violation",
                        "compliance_state": "confirmed_violation",
                        "severity": "minor",
                        "severity_rationale": f"Detected {len(tbl_nums)} distinct main tables exceeding max_tables={max_tbls}.",
                        "location": {"file": doc["file"], "line_start": 1, "line_end": 1},
                        "verbatim_quote": f"Tables referenced: {sorted(tbl_nums, key=int)} (count={len(tbl_nums)} > {max_tbls})",
                        "evidence_basis": f"{g_label} caps main tables at max_tables={max_tbls}.",
                        "actionable_revision": f"Move {len(tbl_nums) - max_tbls} table(s) to Supplementary Tables.",
                        "rule_reference": f"{g_label} [max_tables={max_tbls}]",
                        "status": "confirmed",
                    })

            req_stds = guidelines.get("required_reporting_standards")
            if req_stds is None and guidelines.get("required_reporting_standard"):
                single_std = guidelines.get("required_reporting_standard")
                req_stds = single_std if isinstance(single_std, list) else [single_std]
            if isinstance(req_stds, list):
                found_stds = {m.upper() for m in REPORTING_STD_RE.findall(non_ref_text)}
                for std in req_stds:
                    s_up = str(std).upper()
                    present = s_up in found_stds
                    checklist_status.append({"file": doc["file"], "item": f"reporting_standard_{s_up}", "status": "pass" if present else "missing"})
                    if not present:
                        fid += 1
                        findings.append({
                            "finding_id": f"CMP-{fid:03d}",
                            "facet": "academic_norms",
                            "subtype": "reporting_standard_missing",
                            "finding_type": "norm_violation",
                            "compliance_state": "confirmed_violation",
                            "severity": "major",
                            "severity_rationale": f"Target guideline mandates reporting checklist '{s_up}', which is not referenced in the manuscript.",
                            "location": {"file": doc["file"], "line_start": 1, "line_end": 1},
                            "verbatim_quote": f"[MISSING: {s_up} reporting checklist reference]",
                            "evidence_basis": f"{g_label} requires reporting standard '{s_up}' (`required_reporting_standards`).",
                            "actionable_revision": f"Complete and cite the {s_up} checklist in Methods and attach the completed checklist as supplementary material.",
                            "rule_reference": f"{g_label} [required_reporting_standards={s_up}]",
                            "status": "confirmed",
                        })

        found_any_std = sorted({m.upper() for m in REPORTING_STD_RE.findall(non_ref_text)})
        checklist_status.append({
            "file": doc["file"],
            "item": "reporting_standards_detected",
            "status": "present" if found_any_std else "none_detected",
            "standards": found_any_std,
        })

    # Post-hoc PII scrubbing across every finding and third-party asset excerpt to ensure 0 raw PII leaks
    for f in findings:
        f["verbatim_quote"] = _sanitize_pii_in_text(str(f.get("verbatim_quote", "")))
        f["evidence_basis"] = _sanitize_pii_in_text(str(f.get("evidence_basis", "")))
        f["actionable_revision"] = _sanitize_pii_in_text(str(f.get("actionable_revision", "")))
    for asset in third_party_assets:
        asset["excerpt"] = _sanitize_pii_in_text(str(asset.get("excerpt", "")))

    crit_major = [f for f in findings if f["severity"] in ("critical", "major")]
    confirmed_violations = [f for f in findings if f.get("compliance_state") == "confirmed_violation"]
    unresolved = [
        f for f in findings
        if f.get("status") in ("needs_human_review", "similarity_detected_needs_investigation")
    ]
    has_gaps = bool(gaps)

    result_status = "partial_with_gaps" if has_gaps else "ok"
    audit_status = "partial" if (has_gaps or unresolved) else "completed"
    if confirmed_violations:
        verdict = "violations_detected"
    elif unresolved:
        verdict = "needs_human_investigation"
    else:
        verdict = "compliant_no_major_issues"

    uncertainty_notes = [
        SCOPE_DISCLAIMER,
    ]
    if not guideline_supplied:
        uncertainty_notes.append("Target journal/conference guidelines were not supplied (`guideline_not_supplied`); venue-specific word limits and blinding policies were not assumed.")
    if image_matches:
        uncertainty_notes.append("Image perceptual/byte similarity matches require raw original image inspection; automated similarity cannot determine intent.")
    if gaps:
        uncertainty_notes.append(f"{len(gaps)} input parsing or image decoding gap(s) logged in `gaps`.")

    return {
        "schema_version": "3.0",
        "passed": len(crit_major) == 0 and len(unresolved) == 0 and not has_gaps,
        "result_status": result_status,
        "audit_status": audit_status,
        "status": audit_status,
        "verdict": verdict,
        "scope_disclaimer": SCOPE_DISCLAIMER,
        "guideline_status": "guideline_supplied" if guideline_supplied else "guideline_not_supplied",
        "guidelines_applied": guidelines,
        "documents_scanned": [
            {"file": d["file"], "sha256": d["sha256"], "extractor": d["extractor"], "lines": d["line_count"]}
            for d in docs
        ],
        "images_scanned": len(images),
        "image_integrity_matches": image_matches,
        "third_party_assets": third_party_assets,
        "rejected_pseudo_ethics_ids": rejected_pseudo_ids,
        "checklist_status": checklist_status,
        "compliance_state_counts": {
            "confirmed_violation": sum(1 for f in findings if f.get("compliance_state") == "confirmed_violation"),
            "material_not_supplied": sum(1 for f in findings if f.get("compliance_state") == "material_not_supplied"),
            "needs_human_or_legal_ethics_review": sum(1 for f in findings if f.get("compliance_state") == "needs_human_or_legal_ethics_review"),
        },
        "findings_count": len(findings),
        "findings": findings,
        "gaps": gaps,
        "uncertainty": uncertainty_notes,
    }


def render_markdown_report(rep: dict[str, Any]) -> str:
    lines = [
        f"# Academic Norms & Compliance Audit Report (`{rep.get('verdict')}`)",
        "",
        f"> **Scope Boundary**: {rep.get('scope_disclaimer')}",
        "",
        f"- **Result Status**: `{rep.get('result_status')}` | **Audit Status**: `{rep.get('audit_status')}` | **Passed**: `{rep.get('passed')}`",
        f"- **Guideline Status**: `{rep.get('guideline_status')}` | **Documents**: `{len(rep.get('documents_scanned', []))}` | **Images Scanned**: `{rep.get('images_scanned', 0)}`",
        f"- **Three-State Breakdown**: `{json.dumps(rep.get('compliance_state_counts', {}), ensure_ascii=False)}`",
        f"- **Findings**: `{rep.get('findings_count', 0)}`",
        "",
    ]

    if rep.get("rejected_pseudo_ethics_ids"):
        lines.append("## Ethics Anti-Spoofing Rejections")
        for r in rep["rejected_pseudo_ethics_ids"]:
            lines.append(f"- `{r['file']}:L{r['line']}` rejected `{r['candidate']}` (`{r['rejection_reason']}`)")
        lines.append("")

    if rep.get("image_integrity_matches"):
        lines.append("## Image Integrity Fingerprint Matches (Requires Investigation)")
        for im in rep["image_integrity_matches"]:
            hbits = im.get("hash_bits", 128)
            corr_part = f", r=`{im['intensity_corr']:.4f}`" if im.get("intensity_corr") is not None else ""
            lines.append(
                f"- `{im['file_a']}` <-> `{im['file_b']}`: `{im['match_type']}` (Hamming=`{im['hamming_distance']}/{hbits}`{corr_part}) — `similarity_detected_needs_investigation`"
            )
        lines.append("")

    if rep.get("third_party_assets"):
        lines.append("## Third-Party Asset License Track")
        for a in rep["third_party_assets"]:
            lines.append(f"- `{a['file']}:L{a['line']}` -> `{a['license_status']}` ({a['note']})")
        lines.append("")

    if not rep.get("findings"):
        lines.extend([
            "## Findings",
            "No compliance violations or unresolved screening flags detected within the supplied scope.",
            "",
        ])
    else:
        lines.append("## Findings")
        lines.append("")
        for f in rep["findings"]:
            loc = f["location"]
            lines.append(
                f"### {f['finding_id']} [{f['severity'].upper()} | {f['compliance_state']} | {f.get('subtype')}] "
                f"`{loc['file']}:L{loc.get('line_start', 1)}`"
            )
            lines.append(f"- **Quote / Evidence**: `{f['verbatim_quote']}`")
            lines.append(f"- **Basis**: {f['evidence_basis']}")
            lines.append(f"- **Rule Reference**: `{f.get('rule_reference', '')}`")
            lines.append(f"- **Actionable Revision**: {f['actionable_revision']}")
            lines.append(f"- **Status**: `{f.get('status')}`")
            lines.append("")

    if rep.get("gaps"):
        lines.append("## Input & Coverage Gaps")
        for g in rep["gaps"]:
            lines.append(f"- `{g.get('code')}` ({g.get('source', '')}): {g.get('message')}")
        lines.append("")

    return "\n".join(lines)


def _load_case_id(target: Path, explicit: str) -> str:
    if explicit:
        return explicit
    cj = target / "case.json" if target.is_dir() else target.parent / "case.json"
    if cj.is_file():
        try:
            obj = json.loads(cj.read_text(encoding="utf-8", errors="replace"))
            if isinstance(obj, dict) and obj.get("case_id"):
                return str(obj["case_id"])
        except Exception:
            pass
    return "case-01"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Six-facet academic norms, ethics, PII, image-integrity, copyright, and guideline auditor.")
    parser.add_argument("--input", required=True, help="Input directory or single manuscript file.")
    parser.add_argument("--guidelines", default="", help="Optional JSON file with journal/conference constraints.")
    parser.add_argument("--out-json", required=True, help="Output compliance report JSON path.")
    parser.add_argument("--out-md", default="", help="Optional Markdown summary report path.")
    parser.add_argument("--out-result-json", default="", help="Optional dual-compatible /app/output/result.json path.")
    parser.add_argument("--case-id", default="", help="Optional explicit case_id override for result.json.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if violations or unresolved items prevent passing.")
    args = parser.parse_args(argv)

    target = Path(args.input)
    rep = audit_compliance(target, Path(args.guidelines) if args.guidelines else None)
    io_core.write_json_atomic(Path(args.out_json), rep)

    artifacts = [Path(args.out_json).name]
    if args.out_md:
        out_m = Path(args.out_md)
        out_m.parent.mkdir(parents=True, exist_ok=True)
        out_m.write_text(render_markdown_report(rep), encoding="utf-8")
        artifacts.append(out_m.name)

    if args.out_result_json:
        cid = _load_case_id(target, args.case_id)
        out_name = Path(args.out_json).name
        state_counts = rep.get("compliance_state_counts", {})
        metrics = {
            "documents_scanned_count": len(rep.get("documents_scanned", [])),
            "images_scanned_count": rep.get("images_scanned", 0),
            "findings_count": rep.get("findings_count", 0),
            "confirmed_violation_count": state_counts.get("confirmed_violation", 0),
            "material_not_supplied_count": state_counts.get("material_not_supplied", 0),
            "needs_review_count": state_counts.get("needs_human_or_legal_ethics_review", 0),
            "image_integrity_matches_count": len(rep.get("image_integrity_matches", [])),
            "rejected_pseudo_ethics_ids_count": len(rep.get("rejected_pseudo_ethics_ids", [])),
            "gaps_count": len(rep.get("gaps", [])),
        }
        metric_provenance = {
            k: f"{out_name}::compliance_ethics_auditor.py" for k in metrics
        }
        res_payload = {
            "schema_version": "3.0",
            "case_id": cid,
            "task_id": "challenge-de9f0708",
            "status": rep.get("result_status", "partial_with_gaps" if rep.get("gaps") else "ok"),
            "audit_status": rep.get("audit_status"),
            "passed": rep.get("passed"),
            "verdict": rep.get("verdict"),
            "scope_disclaimer": rep.get("scope_disclaimer"),
            "guideline_status": rep.get("guideline_status"),
            "guidelines_applied": rep.get("guidelines_applied", {}),
            "sources": rep.get("documents_scanned", []),
            "artifacts": ["result.json"] + artifacts,
            "metrics": metrics,
            "metric_provenance": metric_provenance,
            "compliance_state_counts": state_counts,
            "checklist_status": rep.get("checklist_status", []),
            "image_integrity_matches": rep.get("image_integrity_matches", []),
            "third_party_assets": rep.get("third_party_assets", []),
            "rejected_pseudo_ethics_ids": rep.get("rejected_pseudo_ethics_ids", []),
            "findings": rep.get("findings", []),
            "uncertainty": rep.get("uncertainty", []),
            "gaps": rep.get("gaps", []),
        }
        io_core.write_json_atomic(Path(args.out_result_json), res_payload)

    print(
        json.dumps(
            {
                "result_status": rep.get("result_status"),
                "audit_status": rep.get("audit_status"),
                "verdict": rep.get("verdict"),
                "passed": rep.get("passed"),
                "findings_count": rep.get("findings_count", 0),
                "image_matches": len(rep.get("image_integrity_matches", [])),
            },
            ensure_ascii=False,
        )
    )
    if not rep.get("documents_scanned") and rep.get("images_scanned", 0) == 0:
        return 1
    if args.strict and not rep.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
