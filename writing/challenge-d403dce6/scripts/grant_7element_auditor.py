#!/usr/bin/env python3
"""Offline 7-element grant proposal, organizer-workorder, provenance, matrix, and prose auditor.

Pure Python 3.12 standard library (zero external dependencies, zero network calls).
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

SEVEN_ELEMENTS: list[tuple[str, tuple[str, ...]]] = [
    ("background", ("立项依据", "研究背景", "rationale", "significance", "background")),
    ("question", ("科学问题", "研究问题", "scientific question", "hypothesis")),
    ("goals", ("研究目标", "objectives", "goals", "aims")),
    ("content", ("研究内容", "research content", "work packages")),
    ("innovation", ("创新点", "特色与创新", "innovation", "novelty")),
    ("route", ("技术路线", "研究方案", "technical roadmap", "technical route", "methodology")),
    ("foundation", ("研究基础", "可行性", "foundation", "preliminary", "feasibility")),
]

NEGATION_PREFIX_RE = re.compile(
    r"(?:并非|不是|不是真实|未曾|未能|尚未|尚无|未提供|未获得|未获批|未具备|不得|严禁|禁止|不能将|不将|切勿|区别于|而非|不属于|非货币|非已获|非真实|"
    r"not\s+(?:a|an|the|yet|supplied|provided|owned|funded|real)|never\s+|neither\s+|does\s+not\s+|without\s+|rather\s+than\s+|distinct\s+from\s+)$",
    re.I,
)

GAP_TAG_RE = re.compile(r"\[EVIDENCE_GAP[^\]]*\]|\[待申请人补充[^\]]*\]|\[待确认[^\]]*\]|\[\[EVIDENCE:[^\]]*\]\]", re.I)

FIRST_PERSON_PRIOR_RE = re.compile(
    r"(?:本课题组|本团队|申请人|本实验室|我们)(?:前期|此前|目前|已经|现)?[^。；;\n]{0,16}?"
    r"(?:已(?:经)?(?:完成|获得|发表|建立|证实|验证|掌握|研发|开发|获批|授权|通过|购置|具备)|前期成果表明|预实验证实)|"
    r"\bour\s+(?:group|lab|team|applicant)\s+(?:has\s+)?(?:previously\s+)?(?:demonstrated|published|obtained|completed|established|secured|patented)\b",
    re.I,
)

PROPOSED_AS_COMPLETED_RE = re.compile(
    r"(?:(?:G[1-9]|WP[1-9]|目标\s*[1-9]|工作包\s*[1-9]|拟开展(?:工作|研究|方案)|本项目(?:方案|计划|指标))[^。；;\n]{0,16}?"
    r"(?:已完成|已实现|已验证|已取得|已获得|确保提升|保证实现|保证达到|必将实现|必定达成))|"
    r"(?:规划单位[^。；;\n]{0,16}?(?:万元|经费已到位|已获批资金|资助金额|实际经费))|"
    r"(?:(?:经费|资金)已(?:获批|到位|落实))",
    re.I,
)

FUNDER_POLICY_FABRICATION_RE = re.compile(
    r"(?:(?:国家自然科学基金|国自然|NSFC|NIH|NSF|科技部|基金委|省自然|资助机构)(?:指南|政策|规定|通知|要求)(?:明确|规定|要求|资助|限制|支持))|"
    r"(?:(?:已|将)?获(?:现实)?资助(?:机构)?(?:批准|拨款|立项))|"
    r"(?:现实资助机构(?:政策|指南)规定)",
    re.I,
)

RESOURCE_FABRICATION_RE = re.compile(
    r"(?:(?:本实验室|本单位|本团队|课题组)[^。；;\n]{0,14}?(?:已具备|已购置|已配备|现有一台|拥有多台|已建成完整)|"
    r"(?:已通过|已获得)(?:伦理审查|伦理审批|伦理批件|动物伦理|IRB\s*批准|安全审批))",
    re.I,
)

PRIORITY_HYPE_RE = re.compile(
    r"国际领先|国内首创|填补(?:了)?(?:国内|国际)?空白|首次提出|首次揭示|前所未有|颠覆性突破|世界领先|world-first|unprecedented|groundbreaking",
    re.I,
)

POLICY_SLOGAN_RE = re.compile(
    r"赋能|抓手|卡点|打通最后一公里|重大战略意义|开启新时代|开启新的篇章|重磅|炸裂|令人振奋|意义深远|堪称|无疑",
    re.I,
)

AI_CLICHE_RE = re.compile(
    r"综上所述|总的来说|由此可见|不难发现|众所周知|值得注意的是|深刻揭示了|显著提升了|为未来研究奠定(?:了)?(?:坚实)?基础|提供(?:了)?新思路|提供(?:了)?新的可能",
    re.I,
)

DEFENSIVE_OPENING_RE = re.compile(
    r"^(?:遗憾的是|受限于|尽管本研究存在|虽然本方案|本文仅|本研究仅|无法证明|不能证明|Unfortunately,\s|Although\s+limited)",
    re.I,
)

OUTLINE_STUB_RE = re.compile(
    r"(?:写作建议|此处可写|建议补充|待撰写|TODO|TBD|FIXME|lorem\s+ipsum|\[insert\s+[^\]]+\])",
    re.I,
)

EVIDENCE_STATUS_LINE_RE = re.compile(
    r"^\s*(?:(?:applicant(?:'s)?(?:\s+preliminary)?\s+evidence|(?:preliminary\s+)?evidence)\s+(?:status|availability)|status|"
    r"申请人(?:前期)?(?:研究)?基础(?:证据)?状态|前期证据状态|材料状态)\s*[:：=]\s*(.*?)\s*$",
    re.I,
)
EVIDENCE_STATUS_JSON_RE = re.compile(r'"[^"\r\n]*evidence[^"\r\n]*status"\s*:\s*"([^"]+)"', re.I)
UNAVAILABLE_STATUS_RE = re.compile(
    r"^(?:unknown|unavailable|pending|tbd|not\s+(?:provided|supplied|available|confirmed)|absent|missing|n/?a|none|"
    r"未知|待核实|待确认|待补充|未提供|尚未提供|缺失|不详|暂无|未确认|未提交|无)"
    r"(?:\b|[\s.,;:：，；。()（）\[\]—-]|$)",
    re.I,
)

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
DRAFT_SIZE_RANGE_RE = re.compile(r"(\d{3,5})\s*[–—\-~至到]\s*(\d{3,5})\s*(?:个)?(?:汉字|字|中文字符)")


def _is_negated(line: str, match_start: int) -> bool:
    prefix = line[max(0, match_start - 20):match_start]
    return bool(NEGATION_PREFIX_RE.search(prefix.strip()))


def _count_han(text: str) -> int:
    return len(HAN_RE.findall(text))


def _count_en_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def _is_finite_number(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    try:
        f = float(val)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _declares_evidence_unavailable(text: str) -> bool:
    clean_text = text.replace("\ufeff", "")
    for match in EVIDENCE_STATUS_JSON_RE.finditer(clean_text):
        status = match.group(1).strip().strip("[](){}*` ")
        if UNAVAILABLE_STATUS_RE.match(status):
            return True
    first_content = True
    for line in clean_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        sm = EVIDENCE_STATUS_LINE_RE.match(stripped)
        status = sm.group(1) if sm else (stripped if first_content else "")
        status = status.strip().strip("[](){}*` ")
        if status and UNAVAILABLE_STATUS_RE.match(status):
            return True
        first_content = False
    return False


def _load_json_if_exists(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _extract_workorder(case_cfg: dict[str, Any], workorder_cfg: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(case_cfg.get("organizer_workorder"), dict):
        merged.update(case_cfg["organizer_workorder"])
    if isinstance(workorder_cfg.get("organizer_workorder"), dict):
        merged.update(workorder_cfg["organizer_workorder"])
    elif workorder_cfg:
        merged.update(workorder_cfg)
    return merged


def _parse_sections(lines: list[str]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Parse markdown/numbered sections and map matching headings to the 7 grant elements."""
    raw_sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    has_md_headings = any(re.match(r"^#{1,4}\s+\S", ln.strip()) for ln in lines)
    md_heading_re = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
    plain_heading_re = re.compile(
        r"^(?:([一二三四五六七八九十]+、\s*.{2,36})|(\d+[\.、]\s*[^\s。；:：]{2,32})|\*\*([一二三四五六七八九十\d]+[、.\s].{2,36}?)\*\*)\s*$"
    )

    for lno, ln in enumerate(lines, 1):
        st = ln.strip()
        m_md = md_heading_re.match(st)
        m_plain = None if (has_md_headings or m_md) else plain_heading_re.match(st)
        if m_md or m_plain:
            if m_md:
                level = len(m_md.group(1))
                title = m_md.group(2).strip()
            else:
                level = 2
                title = (m_plain.group(1) or m_plain.group(2) or m_plain.group(3) or st).strip()  # type: ignore[union-attr]
            # Treat top-level H1 document title without element keywords as document title
            if level == 1 and not any(
                any(kw in title.lower() for kw in kws) for _, kws in SEVEN_ELEMENTS
            ):
                continue
            if current is not None:
                raw_sections.append(current)
            current = {
                "title": title,
                "start_line": lno,
                "level": level,
                "lines": [],
            }
        else:
            if current is not None:
                current["lines"].append((lno, ln))
    if current is not None:
        raw_sections.append(current)

    found: dict[str, dict[str, Any]] = {}
    for sec in raw_sections:
        low_title = sec["title"].lower()
        body_lines = [ln for _, ln in sec["lines"]]
        body_text = "\n".join(body_lines).strip()
        prose_lines = [
            ln.strip()
            for ln in body_lines
            if ln.strip() and not ln.strip().startswith("|") and not ln.strip().startswith("```")
        ]
        table_text_lines = [
            " ".join(c.strip() for c in ln.strip().strip("|").split("|"))
            for ln in body_lines
            if ln.strip().startswith("|")
            and not all(re.fullmatch(r":?-{2,}:?", c.strip() or "---") for c in ln.strip().strip("|").split("|"))
        ]
        prose_text = "\n".join(prose_lines)
        full_content_text = "\n".join(prose_lines + table_text_lines)
        for key, kws in SEVEN_ELEMENTS:
            if any(k in low_title for k in kws) and key not in found:
                found[key] = {
                    "heading": sec["title"],
                    "start_line": sec["start_line"],
                    "han_chars": _count_han(prose_text),
                    "total_han_with_tables": _count_han(full_content_text),
                    "en_words": _count_en_words(full_content_text),
                    "body_text": body_text,
                    "prose_lines": prose_lines,
                    "first_prose_line": prose_lines[0] if prose_lines else "",
                    "has_stub_marker": bool(OUTLINE_STUB_RE.search(body_text)),
                }
                break
    return found, raw_sections


def _parse_markdown_tables(text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for ln in text.splitlines():
        st = ln.strip()
        if st.startswith("|") and st.endswith("|") and st.count("|") >= 3:
            cells = [c.strip() for c in st.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                continue
            cur.append(cells)
        else:
            if len(cur) >= 2:
                tables.append(cur)
            cur = []
    if len(cur) >= 2:
        tables.append(cur)
    return tables


def _fallback_parse_from_markdown(draft_text: str, crosswalk_text: str) -> dict[str, Any]:
    """Extract matrix, planning units, evidence map, risks, and human confirmations when plan_json is absent."""
    combined = draft_text + "\n\n" + crosswalk_text
    tables = _parse_markdown_tables(combined)
    parsed: dict[str, Any] = {
        "goals": [],
        "work_packages": [],
        "milestones": [],
        "evidence_map": [],
        "risks": [],
        "human_confirmations": [],
    }
    for tbl in tables:
        header = " ".join(tbl[0]).lower()
        rows = tbl[1:]
        if ("论断" in header or "claim" in header) and ("来源" in header or "source" in header or "引文" in header):
            for idx, r in enumerate(rows, 1):
                if len(r) >= 3 and any(r):
                    parsed["evidence_map"].append({
                        "claim_id": f"MD-CLM{idx}",
                        "claim": r[0],
                        "section": r[1],
                        "source_ref": r[2],
                    })
        elif ("目标" in header or "g1" in header or "goal" in header) and ("工作包" in header or "wp" in header) and ("里程碑" in header or "milestone" in header):
            for idx, r in enumerate(rows, 1):
                joined = " ".join(r)
                first_cell = (r[0] if r else "").strip().lower()
                if any(tok in first_cell for tok in ("合计", "总计", "汇总", "total", "sum")):
                    um_tot = re.search(r"(\d+(?:\.\d+)?)\s*(?:规划单位|单位|units?)", " ".join(r[1:]), re.I)
                    if not um_tot:
                        for c_idx, h_cell in enumerate(tbl[0]):
                            if ("规划单位" in h_cell or "unit" in h_cell.lower()) and c_idx < len(r):
                                um_tot = re.search(r"\b(\d+(?:\.\d+)?)\b", r[c_idx])
                                break
                    if um_tot:
                        parsed["planning_unit_total_declared"] = float(um_tot.group(1))
                    continue
                gm = re.search(r"\b(G\d+)\b", joined, re.I)
                wm = re.search(r"\b(WP\d+)\b", joined, re.I)
                mm = re.search(r"(?:M|第\s*)(\d{1,2})\s*(?:月)?", joined, re.I)
                um = re.search(r"(\d+(?:\.\d+)?)\s*(?:规划单位|单位|units?)\b", joined, re.I)
                gid = gm.group(1).upper() if gm else f"G{idx}"
                wid = wm.group(1).upper() if wm else f"WP{idx}"
                month_val = int(mm.group(1)) if mm else None
                units_val = float(um.group(1)) if um else None
                parsed["goals"].append({"goal_id": gid, "wp_id": wid, "metric": r[-1] if r else ""})
                wp_entry: dict[str, Any] = {"wp_id": wid, "goal_id": gid}
                if units_val is not None:
                    wp_entry["planning_units"] = units_val
                if month_val is not None:
                    wp_entry["end_month"] = month_val
                parsed["work_packages"].append(wp_entry)
                if month_val is not None:
                    parsed["milestones"].append({"milestone_id": f"MS{idx}", "goal_id": gid, "wp_id": wid, "month": month_val})
        elif ("规划单位" in header or "planning_unit" in header or "分项" in header) and ("合计" not in header or len(rows) >= 2):
            for idx, r in enumerate(rows, 1):
                row_txt = " ".join(r)
                if "合计" in row_txt or "total" in row_txt.lower():
                    continue
                nums = re.findall(r"\b\d+(?:\.\d+)?\b", row_txt)
                if nums:
                    wm = re.search(r"\b(WP\d+)\b", row_txt, re.I)
                    wid = wm.group(1).upper() if wm else f"WP{idx}"
                    existing = next((w for w in parsed["work_packages"] if w.get("wp_id") == wid), None)
                    val = float(nums[-1])
                    if existing is not None:
                        existing["planning_units"] = val
                    else:
                        parsed["work_packages"].append({"wp_id": wid, "planning_units": val})
        elif ("风险" in header or "risk" in header) and ("触发" in header or "trigger" in header or "备选" in header or "fallback" in header):
            for idx, r in enumerate(rows, 1):
                if len(r) >= 3:
                    parsed["risks"].append({
                        "risk_id": f"MD-R{idx}",
                        "description": r[0],
                        "trigger_signal": r[1],
                        "fallback_plan": r[-1],
                    })
        elif ("人工确认" in header or "确认项" in header or "human confirm" in header):
            for idx, r in enumerate(rows, 1):
                if any(r):
                    parsed["human_confirmations"].append({
                        "confirm_id": f"MD-HC{idx}",
                        "item": " ".join(r),
                    })
    return parsed


def _check_external_author_appropriation(
    lines: list[str],
    external_authors: list[str],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if not external_authors:
        return hits
    tokens: list[str] = []
    for author in external_authors:
        a = str(author).strip()
        if not a:
            continue
        tokens.append(a)
        parts = [p for p in re.split(r"[\s,]+", a) if len(p) >= 3]
        if parts:
            tokens.append(parts[-1])
    unique_tokens = sorted(set(tokens), key=len, reverse=True)
    if not unique_tokens:
        return hits
    author_re = re.compile("|".join(re.escape(t) for t in unique_tokens), re.I)
    claim_owner_re = re.compile(
        r"(?:本课题组|本团队|申请人|我们)(?:前期|此前)?(?:与|联合|主导|共同|已)?[^。；;\n]{0,20}?(?:发表|完成|提出|建立|报道|研究|工作)|"
        r"(?:our\s+(?:group|team|lab)\s+(?:with|and)\s+)",
        re.I,
    )
    for lno, ln in enumerate(lines, 1):
        clean_ln = GAP_TAG_RE.sub("", ln)
        m_auth = author_re.search(clean_ln)
        if not m_auth:
            continue
        m_own = claim_owner_re.search(clean_ln)
        if m_own and not _is_negated(clean_ln, m_own.start()):
            hits.append({
                "line": lno,
                "author_matched": m_auth.group(0),
                "excerpt": ln.strip()[:180],
                "issue": "External paper author appears tied to first-person applicant ownership.",
            })
    return hits


def _build_contract_helper(
    *,
    case_id: str,
    draft_path: Path,
    crosswalk_path: Path | None,
    plan_path: Path | None,
    plan: dict[str, Any],
    workorder: dict[str, Any],
    gaps_list: list[str],
) -> dict[str, Any]:
    sb = workorder.get("source_basis") if isinstance(workorder.get("source_basis"), dict) else {}
    preserved = [
        f"published_paper={sb.get('published_paper')}" if sb.get("published_paper") else None,
        f"external_authors={sb.get('external_authors')}" if sb.get("external_authors") else None,
        f"planning_months={workorder.get('planning_months')}" if workorder.get("planning_months") is not None else None,
        f"planning_unit_cap={workorder.get('planning_unit_cap')}" if workorder.get("planning_unit_cap") is not None else None,
        f"authority={workorder.get('authority')}" if workorder.get("authority") else None,
    ]
    return {
        "case_id": case_id,
        "recommended_status": "partial_with_gaps" if gaps_list else "ok",
        "deliverable": {
            "primary_draft": draft_path.name,
            "supporting_files": [
                p.name for p in (crosswalk_path, plan_path) if p and p.is_file()
            ],
        },
        "preserved_facts": [x for x in preserved if x],
        "evidence_map": plan.get("evidence_map", []),
        "change_log": {
            "human_confirmations": plan.get("human_confirmations", []),
            "rfp_coverage": plan.get("rfp_coverage", []),
            "conflict_decisions": plan.get("conflict_decisions", []),
            "unresolved_gaps": gaps_list,
        },
    }


def audit_grant_draft(
    draft_path: Path,
    *,
    plan_json_path: Path | None = None,
    case_json_path: Path | None = None,
    workorder_path: Path | None = None,
    applicant_evidence_path: Path | None = None,
    crosswalk_path: Path | None = None,
    strict_style: bool = False,
) -> dict[str, Any]:
    text = draft_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    crosswalk_text = (
        crosswalk_path.read_text(encoding="utf-8", errors="replace")
        if crosswalk_path and crosswalk_path.is_file()
        else ""
    )

    case_cfg = _load_json_if_exists(case_json_path)
    wo_file_cfg = _load_json_if_exists(workorder_path)
    workorder = _extract_workorder(case_cfg, wo_file_cfg)
    case_id = str(case_cfg.get("case_id") or workorder.get("id") or "unknown")

    source_basis = workorder.get("source_basis") if isinstance(workorder.get("source_basis"), dict) else {}
    external_authors = list(source_basis.get("external_authors") or [])
    planning_months_raw = workorder.get("planning_months")
    planning_months = int(planning_months_raw) if _is_finite_number(planning_months_raw) and int(planning_months_raw) > 0 else None
    planning_unit_cap_raw = workorder.get("planning_unit_cap")
    planning_unit_cap = float(planning_unit_cap_raw) if _is_finite_number(planning_unit_cap_raw) and float(planning_unit_cap_raw) > 0 else None

    draft_size_str = str(workorder.get("draft_size") or "")
    draft_size_range: list[int] | None = None
    dm = DRAFT_SIZE_RANGE_RE.search(draft_size_str)
    if dm:
        draft_size_range = [int(dm.group(1)), int(dm.group(2))]

    # 1. Seven elements coverage & prose thickness
    found_elements, _ = _parse_sections(lines)
    missing_elements = [k for k, _ in SEVEN_ELEMENTS if k not in found_elements]
    thin_elements: list[dict[str, Any]] = []
    stub_elements: list[str] = []
    seven_han_total = 0
    for key, _ in SEVEN_ELEMENTS:
        info = found_elements.get(key)
        if not info:
            continue
        seven_han_total += info["han_chars"]
        effective_units = info["total_han_with_tables"] + info["en_words"]
        if effective_units < 65:
            thin_elements.append({
                "element": key,
                "heading": info["heading"],
                "han_chars": info["han_chars"],
                "en_words": info["en_words"],
                "effective_units": effective_units,
            })
        if info["has_stub_marker"]:
            stub_elements.append(key)

    total_doc_han = _count_han(text)
    draft_size_ok = True
    if draft_size_range is not None:
        lo, hi = draft_size_range
        draft_size_ok = lo <= seven_han_total <= hi

    # 2. Evidence boundary resolution
    applicant_supplied = source_basis.get("applicant_owned_preliminary_results_supplied")
    funder_supplied = source_basis.get("actual_funder_notice_supplied")
    resource_supplied = source_basis.get("resource_availability_evidence_supplied")

    has_applicant_evidence = bool(applicant_supplied) if isinstance(applicant_supplied, bool) else False
    if applicant_evidence_path and applicant_evidence_path.is_file():
        blob = applicant_evidence_path.read_text(encoding="utf-8", errors="replace").strip()
        if len(blob) > 20 and not _declares_evidence_unavailable(blob):
            if applicant_supplied is not False:
                has_applicant_evidence = True
        else:
            has_applicant_evidence = False

    if workorder_path and workorder_path.is_file():
        wo_raw = workorder_path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"applicant.*missing|真实申请人证据.*缺失", wo_raw, re.I) or _declares_evidence_unavailable(wo_raw):
            has_applicant_evidence = False

    is_not_funder_policy = (
        str(workorder.get("authority") or "").strip().lower() == "organizer-workorder-not-funder-policy"
        or funder_supplied is False
    )

    # 3. Dual Anti-Fabrication Gate
    fabricated_applicant_claims: list[dict[str, Any]] = []
    proposed_as_completed_claims: list[dict[str, Any]] = []
    funder_policy_fabrications: list[dict[str, Any]] = []
    resource_fabrications: list[dict[str, Any]] = []

    for lno, ln in enumerate(lines, 1):
        clean_ln = GAP_TAG_RE.sub("", ln)
        if not has_applicant_evidence:
            m = FIRST_PERSON_PRIOR_RE.search(clean_ln)
            if m and not _is_negated(clean_ln, m.start()):
                fabricated_applicant_claims.append({
                    "line": lno,
                    "match": m.group(0),
                    "excerpt": ln.strip()[:180],
                })
        m_prop = PROPOSED_AS_COMPLETED_RE.search(clean_ln)
        if m_prop and not _is_negated(clean_ln, m_prop.start()):
            proposed_as_completed_claims.append({
                "line": lno,
                "match": m_prop.group(0),
                "excerpt": ln.strip()[:180],
            })
        if is_not_funder_policy:
            m_fun = FUNDER_POLICY_FABRICATION_RE.search(clean_ln)
            if m_fun and not _is_negated(clean_ln, m_fun.start()):
                funder_policy_fabrications.append({
                    "line": lno,
                    "match": m_fun.group(0),
                    "excerpt": ln.strip()[:180],
                })
        if resource_supplied is False:
            m_res = RESOURCE_FABRICATION_RE.search(clean_ln)
            if m_res and not _is_negated(clean_ln, m_res.start()):
                resource_fabrications.append({
                    "line": lno,
                    "match": m_res.group(0),
                    "excerpt": ln.strip()[:180],
                })

    external_author_appropriations = (
        _check_external_author_appropriation(lines, external_authors)
        if not has_applicant_evidence
        else []
    )

    gap_tags = GAP_TAG_RE.findall(text + "\n" + crosswalk_text)

    # 4. Plan / Matrix / 6 Supporting Files Audit
    plan_from_file = _load_json_if_exists(plan_json_path)
    plan = plan_from_file if plan_from_file else _fallback_parse_from_markdown(text, crosswalk_text)

    goals = plan.get("goals") if isinstance(plan.get("goals"), list) else []
    wps = plan.get("work_packages") if isinstance(plan.get("work_packages"), list) else []
    milestones = plan.get("milestones") if isinstance(plan.get("milestones"), list) else []
    evidence_map = plan.get("evidence_map") if isinstance(plan.get("evidence_map"), list) else []
    risks = plan.get("risks") if isinstance(plan.get("risks"), list) else []
    human_confirmations = plan.get("human_confirmations") if isinstance(plan.get("human_confirmations"), list) else []

    errors: list[str] = []

    if missing_elements:
        errors.append(f"Missing required grant element(s): {missing_elements}")
    if thin_elements:
        errors.append(f"Substantively thin / outline-only element(s): {[e['element'] for e in thin_elements]}")
    if stub_elements:
        errors.append(f"Template/stub marker(s) found inside element(s): {stub_elements}")
    if not draft_size_ok and draft_size_range is not None:
        errors.append(
            f"Seven-element Chinese character count {seven_han_total} outside workorder draft_size range {draft_size_range}"
        )

    if fabricated_applicant_claims:
        errors.append(f"Detected {len(fabricated_applicant_claims)} ungrounded first-person applicant prior-work claim(s)")
    if external_author_appropriations:
        errors.append(f"Detected {len(external_author_appropriations)} appropriation(s) of external authors' work")
    if proposed_as_completed_claims:
        errors.append(f"Detected {len(proposed_as_completed_claims)} proposed work/unit/metric claim(s) written as completed/funded/guaranteed")
    if funder_policy_fabrications:
        errors.append(f"Detected {len(funder_policy_fabrications)} fabricated real funding-agency policy/funding claim(s)")
    if resource_fabrications:
        errors.append(f"Detected {len(resource_fabrications)} ungrounded equipment/platform/ethics availability claim(s)")
    if (not has_applicant_evidence or is_not_funder_policy or resource_supplied is False) and len(gap_tags) == 0:
        errors.append("Missing applicant/funder/resource evidence in workorder but draft contains zero explicit [EVIDENCE_GAP] markers")

    # Goal <-> WP <-> Milestone <-> Metric parity (defensive against str/non-dict items)
    consistency_text = str(workorder.get("consistency") or "")
    requires_g1_g3 = bool(re.search(r"G1.*G2.*G3|3个.*目标", consistency_text))

    norm_goals: list[dict[str, Any]] = []
    for idx_g, g in enumerate(goals, 1):
        if isinstance(g, dict):
            norm_goals.append(g)
        elif isinstance(g, str) and g.strip():
            # Accept shorthand string entries like "G1" or "G1: description" without crashing
            gm = re.match(r"^\s*(G\d+|O\d+|Aim\s*\d+|目标\s*\d+)\b", g.strip(), re.I)
            gid_str = gm.group(1).upper().replace(" ", "") if gm else f"G{idx_g}"
            norm_goals.append({"goal_id": gid_str, "description": g.strip(), "metric": ""})
        else:
            errors.append(f"Goal entry #{idx_g} has unsupported type {type(g).__name__}; expected object or string")

    norm_wps: list[dict[str, Any]] = []
    for idx_w, w in enumerate(wps, 1):
        if isinstance(w, dict):
            norm_wps.append(w)
        elif isinstance(w, str) and w.strip():
            wm = re.match(r"^\s*(WP\d+|T\d+|Task\s*\d+)\b", w.strip(), re.I)
            wid_str = wm.group(1).upper().replace(" ", "") if wm else f"WP{idx_w}"
            norm_wps.append({"wp_id": wid_str, "title": w.strip()})
        else:
            errors.append(f"Work package entry #{idx_w} has unsupported type {type(w).__name__}; expected object or string")

    norm_milestones: list[dict[str, Any]] = []
    for idx_m, m in enumerate(milestones, 1):
        if isinstance(m, dict):
            norm_milestones.append(m)
        elif isinstance(m, str) and m.strip():
            mm_id = re.match(r"^\s*(MS?\d+)\b", m.strip(), re.I)
            mm_mo = re.search(r"\bM(\d{1,2})\b|第\s*(\d{1,2})\s*个?月", m.strip(), re.I)
            norm_milestones.append({
                "milestone_id": mm_id.group(1).upper() if mm_id else f"MS{idx_m}",
                "month": int(mm_mo.group(1) or mm_mo.group(2)) if mm_mo else None,
                "description": m.strip(),
            })
        else:
            errors.append(f"Milestone entry #{idx_m} has unsupported type {type(m).__name__}; expected object or string")

    goal_ids = [str(g.get("goal_id", "")).strip().upper() for g in norm_goals if g.get("goal_id")]
    wp_ids = [str(w.get("wp_id", "")).strip().upper() for w in norm_wps if w.get("wp_id")]
    ms_goal_ids = {str(m.get("goal_id", "")).strip().upper() for m in norm_milestones if m.get("goal_id")}
    wp_goal_ids = {str(w.get("goal_id", "")).strip().upper() for w in norm_wps if w.get("goal_id")}

    # Also check inline draft IDs as fallback if plan had none
    if not goal_ids:
        goal_ids = sorted(set(f"G{m}" for m in re.findall(r"\bG([1-9])\b|(?:目标|Objective|Aim)\s*([1-9])", text, re.I) for m in m if m))
    if not wp_ids:
        wp_ids = sorted(set(f"WP{m}" for m in re.findall(r"\bWP([1-9])\b|(?:工作包|任务|Task)\s*([1-9])", text, re.I) for m in m if m))

    parity_ok = True
    if requires_g1_g3:
        for req_g in ("G1", "G2", "G3"):
            if req_g not in goal_ids:
                errors.append(f"Workorder requires bounded goal {req_g} but it is missing from goals")
                parity_ok = False
    if not goal_ids:
        errors.append("No structured goals (e.g., G1/G2/G3) found")
        parity_ok = False
    if not wp_ids:
        errors.append("No structured work packages (e.g., WP1/WP2/WP3) found")
        parity_ok = False
    if goal_ids and wp_ids and len(set(goal_ids)) != len(set(wp_ids)):
        errors.append(f"Goal-to-work-package cardinality mismatch: goals={goal_ids} vs work_packages={wp_ids}")
        parity_ok = False
    if norm_goals:
        for g in norm_goals:
            gid = str(g.get("goal_id", "")).strip().upper() or "G?"
            if not str(g.get("metric") or "").strip():
                errors.append(f"Goal {gid} missing verifiable future metric")
                parity_ok = False
            if wp_goal_ids and gid not in wp_goal_ids:
                errors.append(f"Goal {gid} has no mapped work package")
                parity_ok = False
            if ms_goal_ids and gid not in ms_goal_ids:
                errors.append(f"Goal {gid} has no mapped milestone")
                parity_ok = False

    # Planning-unit arithmetic & timeline months check
    planning_unit_sum = 0.0
    unit_items_valid = 0
    max_month_seen = 0
    for wp in norm_wps:
        wid = str(wp.get("wp_id") or "WP?")
        pu = wp.get("planning_units")
        if pu is not None:
            if not _is_finite_number(pu) or float(pu) <= 0:
                errors.append(f"Work package {wid} has invalid non-positive or non-finite planning_units: {pu!r}")
            else:
                planning_unit_sum += float(pu)
                unit_items_valid += 1
        elif plan_from_file:
            errors.append(f"Work package {wid} missing planning_units allocation")
        sm = wp.get("start_month")
        em = wp.get("end_month")
        if sm is not None and em is not None:
            if not _is_finite_number(sm) or not _is_finite_number(em):
                errors.append(f"Work package {wid} has non-numeric month span [{sm}, {em}]")
            else:
                smi, emi = int(sm), int(em)
                max_month_seen = max(max_month_seen, emi)
                if smi < 1 or emi < smi:
                    errors.append(f"Work package {wid} has invalid month span [{smi}, {emi}]")
                if planning_months is not None and emi > planning_months:
                    errors.append(f"Work package {wid} end_month M{emi} exceeds planning_months {planning_months}")

    for ms in norm_milestones:
        mid = str(ms.get("milestone_id") or "MS?")
        mval = ms.get("month")
        if mval is None or not re.fullmatch(r"[+-]?\d+", str(mval).strip()):
            errors.append(f"Milestone {mid} missing valid integer month: {mval!r}")
            continue
        mi = int(str(mval).strip())
        max_month_seen = max(max_month_seen, mi)
        if mi < 1 or (planning_months is not None and mi > planning_months):
            errors.append(f"Milestone {mid} at M{mi} exceeds planning_months [1, {planning_months}]")

    planning_unit_sum = round(planning_unit_sum, 4)
    declared_total = plan.get("planning_unit_total_declared")
    if declared_total is not None:
        if not _is_finite_number(declared_total):
            errors.append(f"Declared planning_unit_total_declared is non-finite: {declared_total!r}")
        elif abs(float(declared_total) - planning_unit_sum) > 1e-4:
            errors.append(
                f"Planning-unit sum mismatch: sum(work_packages)={planning_unit_sum} != declared_total={declared_total}"
            )
    if planning_unit_cap is not None:
        if unit_items_valid == 0:
            errors.append("Workorder requires planning_unit breakdown but zero valid work-package allocations were found")
        elif planning_unit_sum > planning_unit_cap + 1e-6:
            errors.append(f"Planning-unit sum {planning_unit_sum} exceeds planning_unit_cap {planning_unit_cap}")

    # Evidence map (3-tier) check
    valid_ev_rows = 0
    for row in evidence_map:
        if isinstance(row, dict) and str(row.get("claim") or "").strip() and str(row.get("section") or row.get("paragraph") or "").strip() and str(row.get("source_ref") or "").strip():
            valid_ev_rows += 1
    if valid_ev_rows < 3:
        errors.append(f"3-tier claim-paragraph-source evidence map has only {valid_ev_rows} valid entries (minimum 3 required)")

    # Risks (>= 3 with trigger_signal + fallback_plan)
    valid_risks = 0
    for r in risks:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("risk_id") or "R?")
        if not str(r.get("trigger_signal") or "").strip() or not str(r.get("fallback_plan") or "").strip():
            errors.append(f"Risk {rid} missing trigger_signal or fallback_plan")
        else:
            valid_risks += 1
    if valid_risks < 3:
        errors.append(f"Insufficient valid risks with trigger_signal and fallback_plan: {valid_risks} < 3")

    # Human confirmations (>= 2, covering real applicant evidence & real funder notice when absent)
    hc_texts = [
        " ".join(str(hc.get(k, "")) for k in ("category", "item", "reason"))
        if isinstance(hc, dict)
        else str(hc)
        for hc in human_confirmations
    ]
    hc_joined = "\n".join(hc_texts)
    if len(hc_texts) < 2:
        errors.append(f"Insufficient human confirmation items: {len(hc_texts)} < 2")
    covers_applicant_hc = bool(re.search(r"申请人|前期(?:基础|数据|成果|证据)|设备|资源|applicant|preliminary", hc_joined, re.I))
    covers_funder_hc = bool(re.search(r"资助|指南|政策|预算|机构|funder|agency|notice|policy", hc_joined, re.I))
    if not has_applicant_evidence and not covers_applicant_hc:
        errors.append("Human confirmations must explicitly include real applicant evidence/resources confirmation")
    if is_not_funder_policy and not covers_funder_hc:
        errors.append("Human confirmations must explicitly include real funding-agency guidelines/policy confirmation")

    # 5. RFP/Workorder requirement coverage & Collaborator conflict resolution
    rfp_reqs = workorder.get("rfp_requirements") or case_cfg.get("rfp_requirements") or []
    rfp_cov = plan.get("rfp_coverage") if isinstance(plan.get("rfp_coverage"), list) else []
    rfp_cov_by_id = {
        str(item.get("requirement_id") or "").strip(): item
        for item in rfp_cov
        if isinstance(item, dict) and item.get("requirement_id")
    }
    rfp_omissions: list[dict[str, Any]] = []
    for idx, req in enumerate(rfp_reqs, 1):
        if isinstance(req, dict):
            rid = str(req.get("id") or req.get("requirement_id") or f"RFP{idx}")
            rtext = str(req.get("text") or req.get("requirement") or req.get("title") or "")
            kws = req.get("keywords") if isinstance(req.get("keywords"), list) else []
        else:
            rid = f"RFP{idx}"
            rtext = str(req)
            kws = []
        cov = rfp_cov_by_id.get(rid)
        explicitly_covered = bool(
            cov and str(cov.get("status", "")).lower() in {"covered", "addressed", "needs_human_confirmation"}
            and str(cov.get("location") or "").strip()
        )
        kw_hit = any(str(k).lower() in text.lower() for k in kws if str(k).strip())
        if not explicitly_covered and not kw_hit:
            rfp_omissions.append({"requirement_id": rid, "requirement": rtext})
    if rfp_omissions:
        errors.append(f"Missed {len(rfp_omissions)} required RFP/workorder item(s): {[o['requirement_id'] for o in rfp_omissions]}")

    collab_comments = (
        case_cfg.get("collaborator_comments")
        or workorder.get("collaborator_comments")
        or plan.get("collaborator_comments")
        or []
    )
    conflict_decisions = plan.get("conflict_decisions") if isinstance(plan.get("conflict_decisions"), list) else []
    unresolved_conflicts: list[dict[str, Any]] = []
    if isinstance(collab_comments, list) and len(collab_comments) >= 2:
        has_conflict_flag = any(
            isinstance(c, dict) and (c.get("conflicts_with") or c.get("conflict_group"))
            for c in collab_comments
        )
        if has_conflict_flag:
            valid_decisions = [
                d for d in conflict_decisions
                if isinstance(d, dict) and str(d.get("chosen_option") or "").strip() and str(d.get("rationale") or "").strip()
            ]
            if not valid_decisions:
                unresolved_conflicts.append({
                    "issue": "Conflicting collaborator comments supplied without explicit conflict_decisions log (chosen_option + rationale)."
                })
                errors.append("Conflicting collaborator feedback present without explicit conflict_decisions resolution log")

    # 6. Style, Anti-AI Cliché, Anti-Hype & Anti-Defensive Scan
    style_warnings: list[dict[str, Any]] = []
    for lno, ln in enumerate(lines, 1):
        for kind, pat in (
            ("priority_hype", PRIORITY_HYPE_RE),
            ("policy_slogan", POLICY_SLOGAN_RE),
            ("ai_cliche", AI_CLICHE_RE),
        ):
            m = pat.search(ln)
            if m and not _is_negated(ln, m.start()):
                style_warnings.append({
                    "kind": kind,
                    "line": lno,
                    "term": m.group(0),
                    "excerpt": ln.strip()[:140],
                })
    for key in ("background", "goals", "innovation"):
        info = found_elements.get(key)
        if info and DEFENSIVE_OPENING_RE.search(info["first_prose_line"]):
            style_warnings.append({
                "kind": "defensive_opening",
                "line": info["start_line"],
                "term": info["first_prose_line"][:36],
                "excerpt": info["first_prose_line"][:140],
            })
    if strict_style and style_warnings:
        errors.append(f"Strict style mode failed with {len(style_warnings)} hype/cliché/defensive warning(s)")

    supporting_status = {
        "1_editable_seven_element_draft": not missing_elements and not thin_elements and not stub_elements and draft_size_ok,
        "2_three_tier_evidence_map": valid_ev_rows >= 3,
        "3_goal_wp_milestone_metric_matrix": parity_ok and bool(goal_ids) and bool(wp_ids),
        "4_planning_unit_breakdown": (
            (planning_unit_cap is None or (unit_items_valid > 0 and planning_unit_sum <= planning_unit_cap + 1e-6))
            and (planning_months is None or max_month_seen <= planning_months)
        ),
        "5_risks_and_fallbacks_ge3": valid_risks >= 3,
        "6_human_confirmations_ge2": (
            len(hc_texts) >= 2
            and (has_applicant_evidence or covers_applicant_hc)
            and (not is_not_funder_policy or covers_funder_hc)
        ),
    }

    honest_gaps: list[str] = []
    if not has_applicant_evidence:
        honest_gaps.append("applicant_owned_preliminary_results_missing")
    if is_not_funder_policy:
        honest_gaps.append("actual_funder_notice_missing")
    if resource_supplied is False:
        honest_gaps.append("resource_availability_evidence_missing")

    contract_helper = _build_contract_helper(
        case_id=case_id,
        draft_path=draft_path,
        crosswalk_path=crosswalk_path,
        plan_path=plan_json_path,
        plan=plan,
        workorder=workorder,
        gaps_list=honest_gaps,
    )

    return {
        "passed": len(errors) == 0,
        "case_id": case_id,
        "seven_elements": {
            "found_elements": {k: {"heading": v["heading"], "start_line": v["start_line"], "han_chars": v["han_chars"], "en_words": v["en_words"]} for k, v in found_elements.items()},
            "missing_elements": missing_elements,
            "thin_elements": thin_elements,
            "stub_elements": stub_elements,
            "seven_section_han_chars": seven_han_total,
            "total_doc_han_chars": total_doc_han,
            "draft_size_range": draft_size_range,
            "draft_size_ok": draft_size_ok,
        },
        "provenance_and_boundary": {
            "applicant_evidence_present": has_applicant_evidence,
            "actual_funder_notice_supplied": not is_not_funder_policy,
            "resource_availability_evidence_supplied": resource_supplied,
            "external_authors_checked": external_authors,
            "explicit_evidence_gap_count": len(gap_tags),
            "fabricated_applicant_claims": fabricated_applicant_claims,
            "external_author_appropriations": external_author_appropriations,
            "proposed_as_completed_claims": proposed_as_completed_claims,
            "funder_policy_fabrications": funder_policy_fabrications,
            "resource_fabrications": resource_fabrications,
        },
        "plan_consistency": {
            "goal_ids": goal_ids,
            "wp_ids": wp_ids,
            "parity_ok": parity_ok,
            "planning_months": planning_months,
            "max_milestone_month": max_month_seen,
            "planning_unit_cap": planning_unit_cap,
            "planning_unit_sum": planning_unit_sum,
            "planning_unit_total_declared": declared_total,
        },
        "supporting_files_check": {
            "all_six_satisfied": all(supporting_status.values()),
            "details": supporting_status,
            "evidence_map_valid_count": valid_ev_rows,
            "risks_valid_count": valid_risks,
            "human_confirmations_count": len(hc_texts),
            "covers_applicant_confirmation": covers_applicant_hc,
            "covers_funder_confirmation": covers_funder_hc,
        },
        "rfp_and_conflicts": {
            "rfp_omissions": rfp_omissions,
            "unresolved_conflicts": unresolved_conflicts,
            "conflict_decisions_count": len(conflict_decisions),
        },
        "style_warnings": style_warnings,
        "errors": errors,
        "output_contract_preview": contract_helper,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit 7-element grant proposal draft, organizer-workorder compliance, provenance, matrix parity, and style."
    )
    parser.add_argument("--draft", required=True, help="Grant proposal chapter draft (.md).")
    parser.add_argument("--plan-json", default="", help="Optional structured grant_plan.json.")
    parser.add_argument("--case-json", default="", help="Optional /input/case.json.")
    parser.add_argument("--workorder", default="", help="Optional /input/organizer-workorder.json.")
    parser.add_argument("--applicant-evidence", default="", help="Optional applicant preliminary evidence file.")
    parser.add_argument("--crosswalk", default="", help="Optional standalone element_crosswalk.md.")
    parser.add_argument("--strict-style", action="store_true", help="Treat style warnings as blocking errors.")
    parser.add_argument("--emit-contract-helper", default="", help="Optional JSON path for 5-field output_contract helper.")
    parser.add_argument("--out-json", required=True, help="Output audit JSON path.")
    args = parser.parse_args(argv)

    rep = audit_grant_draft(
        Path(args.draft),
        plan_json_path=Path(args.plan_json) if args.plan_json else None,
        case_json_path=Path(args.case_json) if args.case_json else None,
        workorder_path=Path(args.workorder) if args.workorder else None,
        applicant_evidence_path=Path(args.applicant_evidence) if args.applicant_evidence else None,
        crosswalk_path=Path(args.crosswalk) if args.crosswalk else None,
        strict_style=args.strict_style,
    )

    op = Path(args.out_json)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.emit_contract_helper:
        hp = Path(args.emit_contract_helper)
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(json.dumps(rep["output_contract_preview"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "passed": rep["passed"],
        "seven_section_han_chars": rep["seven_elements"]["seven_section_han_chars"],
        "all_six_supporting_ok": rep["supporting_files_check"]["all_six_satisfied"],
        "planning_unit_sum": rep["plan_consistency"]["planning_unit_sum"],
        "error_count": len(rep["errors"]),
        "style_warning_count": len(rep["style_warnings"]),
        "errors": rep["errors"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
