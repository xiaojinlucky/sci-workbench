#!/usr/bin/env python3
"""Cross-chapter consistency, dual-schema scope protection, entity-value conflict,
numbering gap, duplicate discussion, citation field integrity, and truncation auditor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

CHAPTER_EXTS = {".md", ".txt", ".tex"}
STOP_WORDS = {"a", "an", "the", "of", "for", "and", "in", "on", "to", "with", "by", "or", "as", "at", "from"}

ACRONYM_DEF_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9 -]{3,70}|[\u4e00-\u9fff]{2,20})[ \t]*[（(][ \t]*([A-Z]{2,10})[ \t]*[)）]"
)
SYMBOL_DEF_EN_RE = re.compile(
    r"(?:where|here)\s+\$?([A-Za-zα-ωΑ-Ω](?:_\{?[A-Za-z0-9]+\}?)?)\$?\s+"
    r"(?:denotes|represents|is defined as|stands for)\s+(?:the\s+)?([^.;,\n]{4,60})",
    re.I,
)
SYMBOL_DEF_ZH_RE = re.compile(
    r"(?:其中|式中)\s*\$?([A-Za-zα-ωΑ-Ω](?:_\{?[A-Za-z0-9]+\}?)?)\$?\s*"
    r"(?:表示|代表|定义为|为)\s*([^。；，\n]{2,30})"
)

FIG_LABEL_RE = re.compile(
    r"(?:^|\n)(?:#+\s*|\*{0,2})(?:Fig(?:ure)?\.?|图)\s*(\d+(?:\.\d+)?)\s*(?:[:：]|\*{2}\s*[:：]?|\s+[-—])",
    re.I,
)
FIG_REF_RE = re.compile(r"(?<![A-Za-z])(?:Fig(?:ure)?\.?|图)\s*(\d+(?:\.\d+)?)\b", re.I)
TAB_LABEL_RE = re.compile(
    r"(?:^|\n)(?:#+\s*|\*{0,2})(?:Table|Tab\.?|表)\s*(\d+(?:\.\d+)?)\s*(?:[:：]|\*{2}\s*[:：]?|\s+[-—])",
    re.I,
)
TAB_REF_RE = re.compile(r"(?<![A-Za-z])(?:Table|Tab\.?|表)\s*(\d+(?:\.\d+)?)\b", re.I)
EQ_LABEL_RE = re.compile(r"(?:^|\n).*?(?:Eq(?:uation)?\.?|式)\s*\(?(\d+(?:\.\d+)?)\)?\s*[:：]|\\label\{eq:([0-9.]+)\}", re.I)
EQ_REF_RE = re.compile(r"(?<![A-Za-z])(?:Eq(?:uation)?\.?|式)\s*\(?(\d+(?:\.\d+)?)\)?", re.I)

SAMPLE_SIZE_RE = re.compile(
    r"(?P<ctx>[A-Za-z\u4e00-\u9fff0-9_-]{2,28})\s*(?:cohort|group|patients|samples|specimens|mice|组|队列|样本|标本)?\s*"
    r"[（(]?\s*\b[nN]\s*=\s*(?P<val>\d{1,7})\b"
)
ENTITY_METRIC_RE = re.compile(
    r"(?P<metric>(?:tensile\s+strength|yield\s+strength|elastic\s+modulus|infill\s+density|layer\s+thickness|"
    r"nozzle\s+temperature|printing\s+temperature|accuracy|survival\s+rate|mean\s+age|sample\s+size|"
    r"抗拉强度|屈服强度|弹性模量|填充密度|层厚|喷嘴温度|打印温度|准确率|生存率|平均年龄|总样本量))\s*"
    r"(?:of|was|is|reached|at|=|为|达|约)?\s*(?P<val>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>MPa|GPa|kPa|%|％|mm|μm|um|°C|℃|K|h|min|years|岁|例|名)\b",
    re.I,
)

CITE_NUM_RE = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")
CITE_AUTHOR_YEAR_RE = re.compile(r"\([A-Z][A-Za-z-]+(?:\s+et\s+al\.?)?,\s*(?:19|20)\d{2}[a-z]?\)")
REF_ENTRY_RE = re.compile(r"(?:^|\n)\s*(?:\[(\d{1,3})\]|(\d{1,3})\.)\s+\S")
ZOTERO_CORRUPT_RE = re.compile(
    r"ADDIN\s+(?:ZOTERO_ITEM|EN\.CITE)|Error!\s*Reference\s+source\s+not\s+found|"
    r"\{\s*CITATION\s+[^}]*\}|\[\?\]|\(\?,\s*(?:19|20)\d{2}\)",
    re.I,
)
TRUNCATION_MARKER_RE = re.compile(
    r"\[(?:truncated|cut\s+off|page\s+missing|pages?\s+omitted|incomplete)\]|"
    r"（(?:此处截断|缺页|内容缺失|未完)）|(?:\.\.\.|……)\s*$",
    re.I,
)
CHAPTER_NUM_RE = re.compile(r"(?:^|[/_-])(?:ch(?:apter)?|sec(?:tion)?|part|第)\s*0*(\d+)", re.I)
ANCHOR_FIND_RE = re.compile(
    r"(?:^\s*\[|<!--\s*(?:id\s*:\s*)?|id=[\"']|\{#|^\s*(?:\*\*)?)([A-Za-z0-9]+-P\d+[A-Za-z0-9_-]*)",
    re.MULTILINE,
)
ANNOTATION_BLOCK_RE = re.compile(
    r"\n\n> \[Editorial Annotation \| ID: (?P<pid>[A-Za-z0-9_-]+) \|[^\n]*\n"
    r"(?:> [^\n]*\n)*"
    r"> \[/Editorial Annotation \| ID: (?P=pid)\]",
    re.MULTILINE,
)


def _strip_annotations(text: str) -> str:
    return ANNOTATION_BLOCK_RE.sub("", text)


def _normalize_acronym_expansion(raw_phrase: str, acro: str) -> str:
    phrase = " ".join(raw_phrase.strip().split())
    if re.search(r"[\u4e00-\u9fff]", phrase):
        return phrase.lower()
    words = re.findall(r"[A-Za-z0-9-]+", phrase)
    target = acro.upper()
    for start in range(len(words) - 1, -1, -1):
        suffix = words[start:]
        if suffix[0].lower() in STOP_WORDS:
            continue
        all_init = "".join(w[0].upper() for w in suffix if w)
        content_init = "".join(w[0].upper() for w in suffix if w and w.lower() not in STOP_WORDS)
        if all_init == target or content_init == target:
            return " ".join(suffix).lower()
    while words and words[0].lower() in STOP_WORDS:
        words.pop(0)
    return " ".join(words).lower()


def _bilingual_shingles(text: str) -> set[str]:
    """Build 3-word English shingles and 3-char Chinese shingles for cross-chapter dedup."""
    shingles: set[str] = set()
    en_words = re.findall(r"[a-z0-9]+", text.lower())
    if len(en_words) >= 3:
        for i in range(len(en_words) - 2):
            shingles.add(" ".join(en_words[i : i + 3]))
    else:
        shingles.update(w for w in en_words if len(w) >= 3)

    zh_seq = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    if len(zh_seq) >= 3:
        for i in range(len(zh_seq) - 2):
            shingles.add(zh_seq[i : i + 3])
    return shingles


def _find_numbering_gaps(labels: set[str]) -> list[dict[str, Any]]:
    """Detect missing sequential numbers within each chapter group (e.g., 2.1, 2.3 missing 2.2)."""
    grouped: dict[str, set[int]] = {}
    for lbl in labels:
        if "." in lbl:
            prefix, tail = lbl.rsplit(".", 1)
            if tail.isdigit():
                grouped.setdefault(prefix, set()).add(int(tail))
        elif lbl.isdigit():
            grouped.setdefault("", set()).add(int(lbl))

    gaps: list[dict[str, Any]] = []
    for prefix, nums in sorted(grouped.items()):
        if not nums:
            continue
        for n in range(1, max(nums) + 1):
            if n not in nums:
                missing_lbl = f"{prefix}.{n}" if prefix else str(n)
                gaps.append({"prefix": prefix or "global", "missing_label": missing_lbl})
    return gaps


def _expand_cite_numbers(group_str: str) -> list[int]:
    nums: list[int] = []
    for part in group_str.split(","):
        part = part.strip()
        m_rng = re.match(r"^(\d+)\s*[–-]\s*(\d+)$", part)
        if m_rng:
            a, b = int(m_rng.group(1)), int(m_rng.group(2))
            if a <= b and b - a <= 50:
                nums.extend(range(a, b + 1))
        elif part.isdigit():
            nums.append(int(part))
    return nums


def _parse_workorder_targets(
    workorder_path: Path | None,
    anchor_to_file: dict[str, str],
) -> tuple[str, list[str], list[str], list[str]]:
    """Support both organizer_edit_workorder.target_ids and edits[].file schemas."""
    if not workorder_path or not workorder_path.is_file():
        return "none", [], [], []
    raw = json.loads(workorder_path.read_text(encoding="utf-8"))
    wo = raw.get("organizer_edit_workorder", raw) if isinstance(raw, dict) else {}
    if not isinstance(wo, dict):
        return "none", [], [], []

    target_ids: list[str] = []
    target_files: list[str] = []
    if isinstance(wo.get("target_ids"), list):
        target_ids.extend(str(x).strip() for x in wo["target_ids"] if str(x).strip())
    if isinstance(wo.get("edits"), list):
        for edit in wo["edits"]:
            if isinstance(edit, dict):
                if isinstance(edit.get("file"), str):
                    p = PurePosixPath(edit["file"].replace("\\", "/"))
                    if not p.is_absolute() and ".." not in p.parts:
                        target_files.append(p.as_posix().removeprefix("chapters/"))
                if isinstance(edit.get("target_id"), str) and edit["target_id"].strip():
                    target_ids.append(edit["target_id"].strip())

    target_ids = sorted(set(target_ids))
    missing_ids: list[str] = []
    for tid in target_ids:
        if tid in anchor_to_file:
            target_files.append(anchor_to_file[tid])
        else:
            missing_ids.append(tid)
    target_files = sorted(set(target_files))

    if target_ids and wo.get("edits"):
        mode = "hybrid_target_ids_and_files"
    elif target_ids:
        mode = "paragraph_target_ids"
    elif target_files:
        mode = "chapter_edits_files"
    else:
        mode = "unconstrained"
    return mode, target_ids, target_files, missing_ids


def audit_longdoc(
    chapters_dir: Path,
    *,
    workorder_path: Path | None = None,
    baseline_path: Path | None = None,
    toc_path: Path | None = None,
    chapter_plan_path: Path | None = None,
    dup_threshold: float = 0.45,
) -> dict[str, Any]:
    files = sorted(p for p in chapters_dir.rglob("*") if p.is_file() and p.suffix.lower() in CHAPTER_EXTS)
    if not files:
        return {"passed": False, "error": f"No chapter files found in {chapters_dir}"}

    acronyms: dict[str, set[str]] = {}
    symbols: dict[str, dict[str, list[str]]] = {}
    entity_metrics: dict[tuple[str, str], list[dict[str, Any]]] = {}
    sample_sizes: dict[str, list[dict[str, Any]]] = {}

    defined_figs: set[str] = set()
    ref_figs: list[tuple[str, str]] = []
    defined_tabs: set[str] = set()
    ref_tabs: list[tuple[str, str]] = []
    defined_eqs: set[str] = set()
    ref_eqs: list[tuple[str, str]] = []

    paras_all: list[tuple[str, int, str, set[str]]] = []
    chapter_hashes: dict[str, str] = {}
    stripped_source_hashes: dict[str, str] = {}
    anchor_to_file: dict[str, str] = {}

    cite_nums_used: list[tuple[str, int]] = []
    ref_list_nums: set[int] = set()
    author_year_count = 0
    zotero_artifacts: list[dict[str, Any]] = []
    truncation_warnings: list[dict[str, Any]] = []
    chapter_nums: list[int] = []

    for fp in files:
        rel = fp.relative_to(chapters_dir).as_posix()
        contents = fp.read_bytes()
        raw = contents.decode("utf-8", errors="replace")
        chapter_hashes[rel] = hashlib.sha256(contents).hexdigest()
        stripped_raw = _strip_annotations(raw)
        stripped_source_hashes[rel] = hashlib.sha256(stripped_raw.encode("utf-8")).hexdigest()

        m_ch = CHAPTER_NUM_RE.search(rel)
        if m_ch:
            chapter_nums.append(int(m_ch.group(1)))

        for m_a in ANCHOR_FIND_RE.finditer(stripped_raw):
            anchor_to_file.setdefault(m_a.group(1), rel)

        # Strip editorial annotation blocks before checking source consistency
        text_for_scan = stripped_raw

        for m in ACRONYM_DEF_RE.finditer(text_for_scan):
            acro = m.group(2).strip()
            expansion = _normalize_acronym_expansion(m.group(1), acro)
            acronyms.setdefault(acro, set()).add(expansion)

        for pat in (SYMBOL_DEF_EN_RE, SYMBOL_DEF_ZH_RE):
            for m in pat.finditer(text_for_scan):
                sym = m.group(1).strip()
                defn = " ".join(m.group(2).strip().lower().split())
                symbols.setdefault(sym, {}).setdefault(defn, []).append(rel)

        for m in ENTITY_METRIC_RE.finditer(text_for_scan):
            metric = " ".join(m.group("metric").lower().split())
            unit = m.group("unit").replace("％", "%").replace("℃", "°C").replace("um", "μm")
            val = float(m.group("val"))
            line_no = text_for_scan.count("\n", 0, m.start()) + 1
            entity_metrics.setdefault((metric, unit), []).append({
                "file": rel,
                "line": line_no,
                "value": val,
                "raw": m.group(0),
            })

        for m in SAMPLE_SIZE_RE.finditer(text_for_scan):
            ctx = m.group("ctx").lower().strip("_- ")
            if ctx in {"in", "of", "the", "and", "with", "for", "from", "where"}:
                continue
            val = int(m.group("val"))
            line_no = text_for_scan.count("\n", 0, m.start()) + 1
            sample_sizes.setdefault(ctx, []).append({
                "file": rel,
                "line": line_no,
                "n": val,
                "raw": m.group(0),
            })

        for m in FIG_LABEL_RE.finditer(text_for_scan):
            defined_figs.add(m.group(1))
        for m in FIG_REF_RE.finditer(text_for_scan):
            ref_figs.append((rel, m.group(1)))

        for m in TAB_LABEL_RE.finditer(text_for_scan):
            defined_tabs.add(m.group(1))
        for m in TAB_REF_RE.finditer(text_for_scan):
            ref_tabs.append((rel, m.group(1)))

        for m in EQ_LABEL_RE.finditer(text_for_scan):
            num = m.group(1) or m.group(2)
            if num:
                defined_eqs.add(num)
        for m in EQ_REF_RE.finditer(text_for_scan):
            ref_eqs.append((rel, m.group(1)))

        # Citations and bibliography entries
        in_refs_section = False
        for line_idx, line in enumerate(text_for_scan.splitlines(), 1):
            if re.match(r"^\s*#+\s*(?:references|bibliography|参考文献)\b", line, re.I):
                in_refs_section = True
            for zm in ZOTERO_CORRUPT_RE.finditer(line):
                zotero_artifacts.append({"file": rel, "line": line_idx, "match": zm.group(0)})
            if in_refs_section:
                rm = REF_ENTRY_RE.match(line)
                if rm:
                    ref_list_nums.add(int(rm.group(1) or rm.group(2)))
            else:
                for cm in CITE_NUM_RE.finditer(line):
                    # Skip anchor patterns or markdown links like [1](url)
                    after = line[cm.end() : cm.end() + 1]
                    if after == "(":
                        continue
                    for num in _expand_cite_numbers(cm.group(1)):
                        cite_nums_used.append((rel, num))
                author_year_count += len(CITE_AUTHOR_YEAR_RE.findall(line))

        # Truncation scan
        if TRUNCATION_MARKER_RE.search(text_for_scan):
            truncation_warnings.append({
                "file": rel,
                "reason": "explicit_truncation_marker",
                "excerpt": text_for_scan.strip()[-100:],
            })
        else:
            st_end = text_for_scan.rstrip()
            if len(st_end) >= 80:
                last_line = st_end.splitlines()[-1].strip()
                if st_end[-1] not in ".!?。！？)]}”\"'`|>" and not last_line.startswith(("#", "|", "-", "*", "\\")):
                    truncation_warnings.append({
                        "file": rel,
                        "reason": "abrupt_unpunctuated_ending",
                        "excerpt": last_line[-100:],
                    })

        for idx, para in enumerate(re.split(r"\n\s*\n+", text_for_scan), 1):
            st = " ".join(para.split())
            if len(st) >= 80 and not st.startswith("#"):
                sh = _bilingual_shingles(st)
                if len(sh) >= 12:
                    paras_all.append((rel, idx, st, sh))

    acronym_conflicts = {k: sorted(v) for k, v in acronyms.items() if len(v) > 1}
    symbol_conflicts = {
        sym: {d: sorted(set(fs)) for d, fs in defs.items()}
        for sym, defs in symbols.items()
        if len(defs) > 1
    }

    entity_value_conflicts: list[dict[str, Any]] = []
    for (metric, unit), obs in sorted(entity_metrics.items()):
        distinct_vals = sorted({o["value"] for o in obs})
        distinct_files = {o["file"] for o in obs}
        if len(distinct_vals) > 1 and len(distinct_files) > 1:
            entity_value_conflicts.append({
                "type": "metric_value_conflict",
                "entity": metric,
                "unit": unit,
                "values": distinct_vals,
                "occurrences": obs,
            })
    for ctx, obs in sorted(sample_sizes.items()):
        distinct_n = sorted({o["n"] for o in obs})
        distinct_files = {o["file"] for o in obs}
        if len(distinct_n) > 1 and len(distinct_files) > 1:
            entity_value_conflicts.append({
                "type": "sample_size_conflict",
                "entity": ctx,
                "unit": "n",
                "values": distinct_n,
                "occurrences": obs,
            })

    dangling_figs = [{"file": f, "figure": n} for f, n in ref_figs if n not in defined_figs]
    dangling_tabs = [{"file": f, "table": n} for f, n in ref_tabs if n not in defined_tabs]
    dangling_eqs = [{"file": f, "equation": n} for f, n in ref_eqs if n not in defined_eqs]
    fig_gaps = _find_numbering_gaps(defined_figs)
    tab_gaps = _find_numbering_gaps(defined_tabs)

    cross_chapter_duplicates: list[dict[str, Any]] = []
    for i in range(len(paras_all)):
        fa, ia, ta, sa = paras_all[i]
        for j in range(i + 1, len(paras_all)):
            fb, ib, tb, sb = paras_all[j]
            if fa == fb:
                continue
            jacc = len(sa & sb) / len(sa | sb)
            if jacc >= dup_threshold:
                cross_chapter_duplicates.append({
                    "chapter_a": f"{fa}#P{ia}",
                    "chapter_b": f"{fb}#P{ib}",
                    "jaccard": round(jacc, 3),
                    "severity": "high" if jacc >= 0.65 else "medium",
                    "excerpt": tb[:120],
                })

    # Citation mapping checks
    unmapped_numeric_cites: list[dict[str, Any]] = []
    if cite_nums_used and ref_list_nums:
        for f, n in cite_nums_used:
            if n not in ref_list_nums:
                unmapped_numeric_cites.append({"file": f, "citation_number": n})
    mixed_citation_style = bool(cite_nums_used) and author_year_count > 0

    # Coverage gaps
    missing_chapters: list[str] = []
    if chapter_nums:
        present = set(chapter_nums)
        for n in range(min(present), max(present) + 1):
            if n not in present:
                missing_chapters.append(f"chapter_{n:02d}")
    if toc_path and toc_path.is_file():
        toc = json.loads(toc_path.read_text(encoding="utf-8"))
        present_rels = set(chapter_hashes.keys())
        for exp in (toc.get("expected_chapters", []) if isinstance(toc, dict) else []):
            name = exp.get("file", "") if isinstance(exp, dict) else str(exp)
            if name and name not in present_rels and name not in missing_chapters:
                missing_chapters.append(name)

    wo_mode, wo_target_ids, wo_target_files, wo_missing_ids = _parse_workorder_targets(
        workorder_path, anchor_to_file
    )

    errors: list[str] = []
    warnings: list[str] = []
    modified_outside_workorder: list[str] = []
    original_source_altered_files: list[str] = []

    if baseline_path is not None and baseline_path.is_file():
        base_doc = json.loads(baseline_path.read_text(encoding="utf-8"))
        base_hashes = base_doc.get("chapter_sha256")
        base_stripped = base_doc.get("stripped_source_sha256", base_hashes)
        if not isinstance(base_hashes, dict) or not base_hashes:
            errors.append("Baseline contains no chapter byte hashes.")
        else:
            compared = chapter_hashes.keys() | base_hashes.keys()
            modified_outside_workorder = sorted(
                name for name in compared - set(wo_target_files)
                if chapter_hashes.get(name) != base_hashes.get(name)
            )
            if modified_outside_workorder:
                errors.append(
                    f"{len(modified_outside_workorder)} chapter(s) modified outside approved workorder: {modified_outside_workorder}"
                )
            if wo_mode == "paragraph_target_ids" and isinstance(base_stripped, dict):
                for tf in wo_target_files:
                    if stripped_source_hashes.get(tf) != base_stripped.get(tf):
                        original_source_altered_files.append(tf)
                if original_source_altered_files:
                    errors.append(
                        f"Original publication bytes altered inside target chapter(s): {original_source_altered_files}"
                    )

    if wo_missing_ids:
        errors.append(f"Workorder target_ids not found in chapters: {wo_missing_ids}")
    if acronym_conflicts:
        errors.append(f"Conflicting acronym expansions across chapters: {list(acronym_conflicts.keys())}")
    if symbol_conflicts:
        errors.append(f"Conflicting symbol definitions across chapters: {list(symbol_conflicts.keys())}")
    if entity_value_conflicts:
        errors.append(f"{len(entity_value_conflicts)} cross-chapter numerical/sample-size conflict(s) detected")
    if dangling_figs:
        errors.append(f"{len(dangling_figs)} figure reference(s) lack matching caption label")
    if dangling_tabs:
        errors.append(f"{len(dangling_tabs)} table reference(s) lack matching caption label")
    if dangling_eqs:
        errors.append(f"{len(dangling_eqs)} equation reference(s) lack matching label")
    if fig_gaps or tab_gaps:
        errors.append(f"Display numbering gap(s) detected: figures={len(fig_gaps)}, tables={len(tab_gaps)}")
    if zotero_artifacts:
        errors.append(f"{len(zotero_artifacts)} broken/flattened citation field artifact(s) detected")
    if unmapped_numeric_cites:
        errors.append(f"{len(unmapped_numeric_cites)} numeric citation(s) missing from reference list")
    if any(d["severity"] == "high" for d in cross_chapter_duplicates):
        errors.append(f"{sum(1 for d in cross_chapter_duplicates if d['severity'] == 'high')} high-overlap duplicate discussion paragraph(s) across chapters")
    elif cross_chapter_duplicates:
        warnings.append(f"{len(cross_chapter_duplicates)} medium-overlap repeated discussion paragraph(s) across chapters")
    if mixed_citation_style:
        warnings.append("Mixed numeric [N] and Author-Year citation styles detected across chapters.")
    if missing_chapters:
        warnings.append(f"Incomplete chapter sequence/TOC coverage; missing: {missing_chapters}")
    if truncation_warnings:
        warnings.append(f"{len(truncation_warnings)} chapter(s) show truncation or missing-page signals.")

    chapter_plan_report = _audit_chapter_planning(files, chapters_dir, chapter_plan_path)
    if chapter_plan_report["budget_violations"]:
        errors.extend(chapter_plan_report["budget_violations"])

    return {
        "passed": len(errors) == 0,
        "chapters_count": len(files),
        "covered_chapters": sorted(chapter_hashes.keys()),
        "missing_chapters": missing_chapters,
        "truncation_warnings": truncation_warnings,
        "chapter_sha256": chapter_hashes,
        "stripped_source_sha256": stripped_source_hashes,
        "workorder_schema_mode": wo_mode,
        "workorder_target_ids": wo_target_ids,
        "workorder_targets": wo_target_files,
        "modified_outside_workorder": modified_outside_workorder,
        "original_source_altered_files": original_source_altered_files,
        "acronym_registry": {k: sorted(v) for k, v in acronyms.items()},
        "acronym_conflicts": acronym_conflicts,
        "symbol_conflicts": symbol_conflicts,
        "entity_value_conflicts": entity_value_conflicts,
        "defined_figures": sorted(defined_figs),
        "dangling_figure_refs": dangling_figs,
        "figure_numbering_gaps": fig_gaps,
        "defined_tables": sorted(defined_tabs),
        "dangling_table_refs": dangling_tabs,
        "table_numbering_gaps": tab_gaps,
        "defined_equations": sorted(defined_eqs),
        "dangling_equation_refs": dangling_eqs,
        "cross_chapter_duplicates": cross_chapter_duplicates,
        "citation_integrity": {
            "zotero_or_field_artifacts": zotero_artifacts,
            "unmapped_numeric_citations": unmapped_numeric_cites,
            "mixed_citation_style": mixed_citation_style,
            "reference_entries_found": len(ref_list_nums),
        },
        "chapter_planning_audit": chapter_plan_report,
        "errors": errors,
        "warnings": warnings,
    }


def _audit_chapter_planning(files: list[Path], chapters_dir: Path, chapter_plan_path: Path | None) -> dict[str, Any]:
    """Audit chapter word/character budgets and thesis argument roles (RQ/Methods/Results/Discussion/Limitations)."""
    role_patterns = {
        "research_question": re.compile(r"\b(?:research\s+question|hypothesis|objective|aim)\b|研究问题|科学问题|研究目标|假设", re.I),
        "methods": re.compile(r"\b(?:method(?:ology|s)?|experimental\s+design|protocol|materials)\b|研究方法|实验设计|材料与方法", re.I),
        "results": re.compile(r"\b(?:results?|findings?|observed|measured)\b|研究结果|实验结果|主要发现", re.I),
        "discussion": re.compile(r"\b(?:discussion|interpretation|implications|synthesis)\b|讨论|综合讨论|结果分析", re.I),
        "limitations": re.compile(r"\b(?:limitations?|caveats?|scope\s+boundary|future\s+work)\b|局限性|研究局限|适用边界|不足", re.I),
    }
    per_chapter: list[dict[str, Any]] = []
    covered_roles: set[str] = set()
    for fp in files:
        rel = fp.relative_to(chapters_dir).as_posix()
        txt = fp.read_text(encoding="utf-8", errors="replace")
        zh_chars = len(re.findall(r"[\u4e00-\u9fff]", txt))
        en_words = len(re.findall(r"[A-Za-z0-9_'-]+", txt))
        roles = [r for r, pat in role_patterns.items() if pat.search(txt)]
        covered_roles.update(roles)
        per_chapter.append({"file": rel, "zh_chars": zh_chars, "en_words": en_words, "detected_roles": roles})

    budget_violations: list[str] = []
    if chapter_plan_path and chapter_plan_path.is_file():
        plan = json.loads(chapter_plan_path.read_text(encoding="utf-8"))
        plan_items = plan.get("chapters", plan) if isinstance(plan, dict) else plan
        by_file = {c["file"]: c for c in per_chapter}
        if isinstance(plan_items, list):
            for item in plan_items:
                if not isinstance(item, dict) or not item.get("file"):
                    continue
                fn = str(item["file"]).replace("\\", "/").removeprefix("chapters/")
                actual = by_file.get(fn)
                if actual is None:
                    budget_violations.append(f"Planned chapter missing: {fn}")
                    continue
                length_val = actual["zh_chars"] if actual["zh_chars"] >= actual["en_words"] else actual["en_words"]
                min_len = item.get("min_length")
                max_len = item.get("max_length")
                if isinstance(min_len, (int, float)) and length_val < min_len:
                    budget_violations.append(f"{fn}: length {length_val} below planned min_length {min_len}")
                if isinstance(max_len, (int, float)) and length_val > max_len:
                    budget_violations.append(f"{fn}: length {length_val} exceeds planned max_length {max_len}")
                for req_role in item.get("required_roles", []):
                    if req_role not in actual["detected_roles"]:
                        budget_violations.append(f"{fn}: missing planned argument role {req_role!r}")

    missing_global_roles = [r for r in role_patterns if r not in covered_roles]
    return {
        "per_chapter": per_chapter,
        "covered_thesis_roles": sorted(covered_roles),
        "missing_thesis_roles": missing_global_roles,
        "budget_violations": budget_violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-chapter consistency, scope protection, and truncation auditor.")
    parser.add_argument("--chapters-dir", required=True, help="Directory with working thesis chapters.")
    parser.add_argument("--workorder", default="", help="Optional organizer-edit-workorder.json or case.json.")
    parser.add_argument("--baseline", type=Path, default=None, help="Baseline JSON produced from original chapter bytes.")
    parser.add_argument("--toc-json", type=Path, default=None, help="Optional TOC expectation JSON.")
    parser.add_argument("--chapter-plan-json", type=Path, default=None, help="Optional chapter length & argument-role plan JSON.")
    parser.add_argument("--dup-threshold", type=float, default=0.45, help="Shingle Jaccard threshold for cross-chapter duplicates.")
    parser.add_argument("--out-json", required=True, help="Output consistency report JSON.")
    args = parser.parse_args(argv)

    rep = audit_longdoc(
        Path(args.chapters_dir),
        workorder_path=Path(args.workorder) if args.workorder else None,
        baseline_path=args.baseline,
        toc_path=args.toc_json,
        chapter_plan_path=args.chapter_plan_json,
        dup_threshold=args.dup_threshold,
    )
    op = Path(args.out_json)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": rep["passed"],
                "chapters_count": rep.get("chapters_count", 0),
                "workorder_schema_mode": rep.get("workorder_schema_mode"),
                "errors": rep.get("errors", []),
                "warnings": rep.get("warnings", []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
