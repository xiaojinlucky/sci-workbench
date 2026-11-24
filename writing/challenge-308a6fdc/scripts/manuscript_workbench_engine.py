"""Offline Academic Manuscript Workbench Engine for academic-manuscript-writing.

Three subcommands (pure Python 3.12 stdlib, zero third-party dependencies):
  1. `index`:  Scan /input research materials (CSV/JSON/MD/TXT/BIB/DOCX/PDF),
               build a traceable material_index.json, detect cross-file conflicts
               and missing required declarations/captions.
  2. `audit`:  Run 8-gate manuscript verification (numeric/unit/sample-size
               provenance, citation existence vs support separation, Results vs
               Discussion layer separation & duplication guard, section/declaration
               completeness, Abstract-vs-Body numeric consistency, acronym first-use
               consistency, bilingual academic de-AI & press-release narrative check,
               and explicit [EVIDENCE_GAP] accounting).
  3. `export`: Build an editable OOXML .docx (with heading outline, three-line
               tables, and Liberation Serif default font), optionally render PDF/PNG
               preview if LibreOffice is available, and seal output-contract-compliant
               result.json (`case_id`, `deliverable`, `preserved_facts`,
               `evidence_map`, `change_log`).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import writing_io_and_invariants as wio  # noqa: E402

RE_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
RE_GAP_TAG = re.compile(r"\[EVIDENCE_GAP:\s*([^\]]+)\]", re.I)
RE_ACRONYM_DEF = re.compile(r"([A-Za-z][A-Za-z0-9\-\s]{3,60})\s*\(([A-Z]{2,8})\)")
RE_ACRONYM_USE = re.compile(r"\b([A-Z]{2,8})\b")
RE_BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.S)
RE_BIB_FIELD = re.compile(r"(\w+)\s*=\s*(?:\{((?:[^{}]|\{[^{}]*\})*)\}|\"([^\"]*)\"|(\d+))")
RE_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")

CLAIM_STOPWORDS = {
    "this", "that", "these", "those", "with", "from", "into", "were", "have", "been",
    "being", "which", "where", "when", "while", "also", "such", "than", "then", "them",
    "they", "their", "there", "here", "about", "after", "before", "between", "under",
    "over", "more", "most", "less", "some", "many", "each", "both", "only", "very",
    "study", "studies", "paper", "results", "method", "methods", "data", "analysis",
    "table", "figure", "model", "models", "using", "used", "based", "found", "showed",
    "shown", "reported", "observed", "significantly", "significant", "associated",
    "effect", "effects", "level", "levels", "group", "groups", "sample", "samples",
    "value", "values", "mean", "median", "standard", "total", "across", "within",
    "previous", "prior", "literature", "consistent", "contrast", "compare", "compared",
}

CONDITION_COL_HINTS = (
    "group", "condition", "arm", "cohort", "treatment", "dose", "subgroup",
    "stratum", "regime", "category", "class", "组别", "条件",
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}

COMMON_ACRONYM_WHITELIST = {
    "DNA", "RNA", "PCR", "MRI", "CT", "FDA", "WHO", "USA", "UK", "EU", "PDF",
    "CSV", "JSON", "DOI", "URL", "HTTP", "HTTPS", "SD", "SEM", "IQR", "CI",
    "FDR", "AUC", "ROC", "ANOVA", "OR", "HR", "RR", "BMI", "IC", "EC", "PH",
    "UV", "IR", "MS", "NMR", "XRD", "XPS", "TEM", "SEM", "AFM", "PBS", "DMSO",
    "RNA", "ATP", "NAD", "CO", "NO", "II", "III", "IV", "VI", "Fig", "SI",
    "OLS", "UCI", "MPa", "MPA", "GNN", "SCFA", "OER", "RMSE", "MAE", "AIC", "BIC",
}

RESULTS_MECHANISM_LEAK_PATTERNS = [
    re.compile(r"\b(?:this\s+suggests\s+a\s+novel\s+mechanism|likely\s+caused\s+by\s+downstream|could\s+be\s+explained\s+by\s+prior\s+theory|proves\s+that\s+the\s+pathway|presumably\s+driven\s+by|likely\s+mediated\s+through)\b", re.I),
    re.compile(r"(?:这(?:可能)?说明其深层机制在于|从机制上推测|由此证明了因果机制|与既往理论一致地解释了|可能是由.*?通路介导)"),
]

REQUIRED_SECTIONS_MAP = {
    "abstract": ("abstract", "摘要"),
    "introduction": ("introduction", "引言", "前言"),
    "methods": ("methods", "materials and methods", "methodology", "实验方法", "方法"),
    "results": ("results", "结果"),
    "discussion": ("discussion", "讨论"),
    "references": ("references", "bibliography", "参考文献"),
}

DECLARATION_PATTERNS = {
    "data_availability": re.compile(r"(?:data\s+availability|code\s+availability|数据可得性|数据可用性|代码可得性)", re.I),
    "ethics_or_competing": re.compile(r"(?:competing\s+interests|conflict\s+of\s+interest|ethics\s+statement|利益冲突|伦理声明)", re.I),
}


def _parse_bib_entries(text: str, source_file: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for m in RE_BIB_ENTRY.finditer(text):
        etype, key, body = m.group(1).lower(), m.group(2).strip(), m.group(3)
        fields: dict[str, str] = {}
        for fm in RE_BIB_FIELD.finditer(body):
            val = fm.group(2) if fm.group(2) is not None else (fm.group(3) if fm.group(3) is not None else fm.group(4))
            fields[fm.group(1).lower()] = re.sub(r"\s+", " ", (val or "").strip())
        entries.append({
            "citekey": key,
            "entry_type": etype,
            "title": fields.get("title", ""),
            "author": fields.get("author", ""),
            "year": fields.get("year", ""),
            "journal": fields.get("journal") or fields.get("booktitle", ""),
            "doi": fields.get("doi", "").strip(),
            "abstract": fields.get("abstract", ""),
            "source_file": source_file,
        })
    return entries


def _domain_tokens(text: str) -> set[str]:
    """Extract substantive domain keywords (English >=4 chars + CJK 2-grams) excluding generic academic stopwords."""
    toks: set[str] = set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", text.lower()):
        w_clean = w.strip("-")
        if len(w_clean) >= 4 and w_clean not in CLAIM_STOPWORDS and not w_clean.isdigit():
            toks.add(w_clean)
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for i in range(len(seg) - 1):
            toks.add(seg[i : i + 2])
    return toks


def build_material_index(input_dir: Path) -> dict[str, Any]:
    """Index all source files in /input for deterministic, budget-bounded provenance linking."""
    files_indexed: list[dict[str, Any]] = []
    number_locations: dict[str, list[str]] = defaultdict(list)
    doi_locations: dict[str, list[str]] = defaultdict(list)
    figure_captions: list[dict[str, Any]] = []
    bib_entries: list[dict[str, Any]] = []
    image_files: list[str] = []
    value_condition_owners: dict[str, set[str]] = defaultdict(set)
    condition_labels: set[str] = set()
    journal_structure_spec: str = "UNSPECIFIED"

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_dir).as_posix()
        suf = path.suffix.lower()
        if suf in IMAGE_SUFFIXES:
            image_files.append(rel)
            files_indexed.append({
                "relpath": rel,
                "format": suf.lstrip("."),
                "ok": True,
                "bytes": path.stat().st_size,
                "image_only": True,
            })
            continue
        doc = wio.read_document_text(path)
        text = doc.get("text", "")
        entry: dict[str, Any] = {
            "relpath": rel,
            "format": doc.get("format", suf.lstrip(".")),
            "ok": doc.get("ok", False),
            "bytes": path.stat().st_size,
        }
        if not doc.get("ok"):
            entry["error"] = doc.get("meta", {}).get("error", "unreadable")
            files_indexed.append(entry)
            continue

        # Check whether target journal explicitly specifies combined vs separate Results/Discussion
        low_text = text.lower()
        if "results and discussion" in low_text and ("combined" in low_text or "merge" in low_text or "合并" in text):
            journal_structure_spec = "COMBINED_RESULTS_AND_DISCUSSION"
        elif ("separate results and discussion" in low_text or "分列结果与讨论" in text) and journal_structure_spec == "UNSPECIFIED":
            journal_structure_spec = "SEPARATE_RESULTS_AND_DISCUSSION"

        if suf == ".bib":
            parsed = _parse_bib_entries(text, rel)
            bib_entries.extend(parsed)
            entry["bib_entry_count"] = len(parsed)
        elif suf == ".csv":
            rows = list(csv.DictReader(text.splitlines()))
            entry["csv_rows"] = len(rows)
            cols = list(rows[0].keys()) if rows else []
            entry["csv_columns"] = cols
            cond_col = next((c for c in cols if any(h in c.lower() for h in CONDITION_COL_HINTS)), None)
            col_nums: dict[str, list[float]] = defaultdict(list)
            large_csv = len(rows) > 80
            for r_idx, row in enumerate(rows, start=2):
                cond_val = str(row.get(cond_col) or "").strip() if cond_col else ""
                if cond_val and len(cond_val) >= 2 and not re.fullmatch(r"[\d.\-]+", cond_val):
                    condition_labels.add(cond_val)
                for col, val in row.items():
                    sval = str(val or "").strip()
                    if not large_csv or len(number_locations) < 1200:
                        inv = wio.extract_invariants(sval)
                        for num in inv["bare_numbers"]:
                            loc = f"{rel}:csv_row{r_idx}:{col}"
                            if len(number_locations[num]) < 4 and loc not in number_locations[num]:
                                number_locations[num].append(loc)
                            if cond_val and col != cond_col and not any(ph in col.lower() for ph in ("id", "index", "year")):
                                value_condition_owners[num].add(cond_val)
                    try:
                        fv = float(sval.replace(",", ""))
                        if math.isfinite(fv):
                            col_nums[col].append(fv)
                    except ValueError:
                        pass
            # Compute deterministic descriptive summaries (n, mean, sd, median, iqr, min, max)
            summaries: dict[str, dict[str, Any]] = {}
            for col, vals in col_nums.items():
                if len(vals) >= 3:
                    sv = sorted(vals)
                    q1 = sv[len(sv) // 4]
                    q3 = sv[(3 * len(sv)) // 4]
                    mean_v = statistics.fmean(vals)
                    sd_v = statistics.stdev(vals) if len(vals) >= 2 else 0.0
                    med_v = statistics.median(vals)
                    summaries[col] = {
                        "n": len(vals),
                        "mean": round(mean_v, 4),
                        "sd": round(sd_v, 4),
                        "median": round(med_v, 4),
                        "iqr": round(q3 - q1, 4),
                        "min": round(sv[0], 4),
                        "max": round(sv[-1], 4),
                    }
                    for k_stat in ("n", "mean", "sd", "median", "iqr", "min", "max"):
                        for fmt in (str(summaries[col][k_stat]), f"{summaries[col][k_stat]:.2f}", f"{summaries[col][k_stat]:.1f}"):
                            nn = wio._normalize_num_token(fmt)
                            loc = f"{rel}:summary:{col}:{k_stat}"
                            if len(number_locations[nn]) < 4 and loc not in number_locations[nn]:
                                number_locations[nn].append(loc)
            if summaries:
                entry["numeric_summaries"] = summaries
        elif suf == ".json":
            try:
                jobj = json.loads(text)
                if isinstance(jobj, dict):
                    msg = jobj.get("message") if isinstance(jobj.get("message"), dict) else jobj
                    if msg.get("DOI") and msg.get("title"):
                        t_raw = msg["title"][0] if isinstance(msg["title"], list) else str(msg["title"])
                        auths = msg.get("author") or []
                        a_str = ", ".join(f"{a.get('family', '')} {a.get('given', '')}".strip() for a in auths[:5]) if isinstance(auths, list) else str(auths)
                        yr = ""
                        for dk in ("published-print", "published-online", "issued", "created"):
                            dp = (msg.get(dk) or {}).get("date-parts")
                            if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
                                yr = str(dp[0][0])
                                break
                        cont = msg.get("container-title")
                        jname = cont[0] if isinstance(cont, list) and cont else str(cont or "")
                        bib_entries.append({
                            "citekey": Path(rel).stem,
                            "title": str(t_raw),
                            "author": a_str,
                            "year": yr,
                            "journal": jname,
                            "doi": str(msg["DOI"]).strip(),
                            "abstract": str(msg.get("abstract") or "")[:400],
                            "source_file": rel,
                        })
                    if isinstance(jobj.get("references"), list):
                        for ref in jobj["references"]:
                            if isinstance(ref, dict):
                                bib_entries.append({
                                    "citekey": str(ref.get("id") or ref.get("citekey") or ""),
                                    "title": str(ref.get("title") or ""),
                                    "author": str(ref.get("author") or ""),
                                    "year": str(ref.get("year") or ""),
                                    "journal": str(ref.get("journal") or ""),
                                    "doi": str(ref.get("doi") or ""),
                                    "abstract": str(ref.get("abstract") or ref.get("finding") or ""),
                                    "source_file": rel,
                                })
                    if jobj.get("journal_structure"):
                        journal_structure_spec = str(jobj["journal_structure"])
            except json.JSONDecodeError:
                pass

        # Skip redundant line-by-line scan on large CSVs (already indexed via DictReader + summaries)
        if suf != ".csv":
            for lineno, line in enumerate(text.splitlines(), start=1):
                inv = wio.extract_invariants(line)
                for num in inv["bare_numbers"]:
                    loc = f"{rel}:L{lineno}"
                    if len(number_locations[num]) < 4 and loc not in number_locations[num]:
                        number_locations[num].append(loc)
                for doi in inv["dois"]:
                    loc = f"{rel}:L{lineno}"
                    if len(doi_locations[doi.lower()]) < 4 and loc not in doi_locations[doi.lower()]:
                        doi_locations[doi.lower()].append(loc)
                if re.match(r"^\s*(?:Fig(?:ure|\.)?|Table|图|表)\s*S?\d+[A-Za-z]?[\s:.：\-]", line, re.I):
                    figure_captions.append({"ref": line[:48].strip(), "source": f"{rel}:L{lineno}", "caption": line.strip()})
        else:
            # Ensure DOIs inside CSV (if any) are still captured
            for doi in wio.extract_invariants(text)["dois"]:
                if rel not in doi_locations[doi.lower()]:
                    doi_locations[doi.lower()].append(rel)

        files_indexed.append(entry)

    # Identify image-only files lacking any matching text caption
    captions_blob = " ".join(c["caption"].lower() for c in figure_captions)
    image_without_caption = [
        img for img in image_files
        if Path(img).stem.lower() not in captions_blob
    ]

    return {
        "input_dir": str(input_dir),
        "file_count": len(files_indexed),
        "files": files_indexed,
        "journal_structure_spec": journal_structure_spec,
        "indexed_number_count": len(number_locations),
        "number_locations": dict(number_locations),
        "doi_locations": dict(doi_locations),
        "bib_entries": bib_entries,
        "figure_captions": figure_captions,
        "image_only_without_caption": image_without_caption,
        "condition_labels": sorted(condition_labels),
        "value_condition_owners": {k: sorted(v) for k, v in value_condition_owners.items()},
    }


def _split_markdown_sections(md_text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    cur_title = "Preamble"
    cur_level = 0
    cur_start = 1
    buf: list[tuple[int, str]] = []

    def _flush(end_ln: int) -> None:
        if buf or cur_title != "Preamble":
            sections.append({
                "title": cur_title,
                "level": cur_level,
                "start_line": cur_start,
                "end_line": end_ln,
                "lines": list(buf),
                "text": "\n".join(t for _, t in buf).strip(),
            })

    for lineno, line in enumerate(md_text.splitlines(), start=1):
        hm = RE_HEADING.match(line)
        if hm:
            _flush(lineno - 1)
            cur_level = len(hm.group(1))
            cur_title = hm.group(2).strip()
            cur_start = lineno
            buf = []
        else:
            buf.append((lineno, line))
    _flush(len(md_text.splitlines()))
    return sections


def _section_role(title: str) -> str:
    low = title.lower()
    if "results and discussion" in low or ("结果" in title and "讨论" in title):
        return "results_and_discussion_combined"
    for role, aliases in REQUIRED_SECTIONS_MAP.items():
        if any(a in low or a in title for a in aliases):
            return role
    if "conclusion" in low or "结论" in title:
        return "conclusion"
    if "limitation" in low or "局限" in title:
        return "limitations"
    return "other"


def audit_manuscript(draft_path: Path, input_dir: Path) -> dict[str, Any]:
    """Run deterministic 8-gate provenance and quality audit on a manuscript draft."""
    idx = build_material_index(input_dir)
    draft_doc = wio.read_document_text(draft_path)
    if not draft_doc.get("ok"):
        return {"ok": False, "error": f"unable to read draft {draft_path}: {draft_doc.get('meta')}"}

    text = draft_doc["text"]
    sections = _split_markdown_sections(text)
    roles_present = {_section_role(s["title"]) for s in sections}

    # Gate 1: Numeric / DOI grounding, condition-binding check & evidence map
    num_locs = idx["number_locations"]
    doi_locs = idx["doi_locations"]
    val_cond_owners = {k: set(v) for k, v in idx.get("value_condition_owners", {}).items()}
    cond_labels = sorted(idx.get("condition_labels", []), key=len, reverse=True)
    grounded_numbers: list[dict[str, Any]] = []
    ungrounded_numbers: list[dict[str, Any]] = []
    condition_binding_mismatches: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []

    for sec in sections:
        sec_role = _section_role(sec["title"])
        if sec_role == "references":
            continue
        for lineno, line in sec["lines"]:
            stripped = line.strip()
            if not stripped:
                continue
            for sent in RE_SENT_SPLIT.split(stripped):
                sent = sent.strip()
                if not sent:
                    continue
                inv = wio.extract_invariants(sent)
                matched_conds = [c for c in cond_labels if re.search(rf"(?<!\w){re.escape(c)}(?!\w)", sent, re.I)]
                for n_tok in inv["bare_numbers"]:
                    # Ignore small ordinal/index integers like 1, 2, 3 unless attached to units
                    if n_tok in {"0", "1", "2", "3", "4", "5"} and not any(u["value"] == n_tok for u in inv["numbers_with_units"]):
                        continue
                    if n_tok in num_locs:
                        grounded_numbers.append({"number": n_tok, "draft_line": lineno, "sources": num_locs[n_tok][:3]})
                        evidence_map.append({
                            "claim_location": f"{draft_path.name}:L{lineno}",
                            "section": sec["title"],
                            "token": n_tok,
                            "status": "GROUNDED",
                            "source_anchors": num_locs[n_tok][:3],
                        })
                        if len(matched_conds) == 1 and n_tok in val_cond_owners:
                            named_c = matched_conds[0]
                            owners = val_cond_owners[n_tok]
                            if named_c not in owners:
                                condition_binding_mismatches.append({
                                    "code": "CONDITION_BINDING_MISMATCH",
                                    "severity": "high",
                                    "draft_line": lineno,
                                    "section": sec["title"],
                                    "named_condition": named_c,
                                    "misattributed_value": n_tok,
                                    "actual_owner_conditions": sorted(owners),
                                    "sentence": sent[:180],
                                })
                    else:
                        ungrounded_numbers.append({
                            "number": n_tok,
                            "draft_line": lineno,
                            "section": sec["title"],
                            "context": sent[:160],
                        })

    draft_inv = wio.extract_invariants(text)
    ungrounded_dois = [d for d in draft_inv["dois"] if d.lower() not in doi_locs]

    # Gate 2: Citation existence AND claim-to-reference topical support separation
    ref_section_text = "\n".join(s["text"] for s in sections if _section_role(s["title"]) == "references")
    ref_entry_map: dict[str, str] = {}
    for m in re.finditer(r"(?:^|\n)\s*(?:\[(\d+)\]|(\d+)[.)])\s+([^\n]+)", ref_section_text):
        rnum = m.group(1) or m.group(2)
        if rnum:
            ref_entry_map[rnum] = m.group(3).strip()
    ref_numbers_defined = set(ref_entry_map.keys())

    # Enrich each reference number with indexed bib_entry title + abstract + passage excerpt for 3-tier verification
    bib_by_doi = {b["doi"].lower(): b for b in idx["bib_entries"] if b.get("doi")}
    ref_metadata_tokens: dict[str, set[str]] = {}
    ref_passage_tokens: dict[str, set[str]] = {}
    ref_matched_bib: dict[str, dict[str, Any] | None] = {}
    for rnum, rline in ref_entry_map.items():
        r_inv = wio.extract_invariants(rline)
        matched_be: dict[str, Any] | None = None
        for rd in r_inv["dois"]:
            if rd.lower() in bib_by_doi:
                matched_be = bib_by_doi[rd.lower()]
                break
        if matched_be is None:
            rline_low = rline.lower()
            for be in idx["bib_entries"]:
                bt = (be.get("title") or "").strip().lower()
                if bt and len(bt) >= 12 and bt[:24] in rline_low:
                    matched_be = be
                    break
        ref_matched_bib[rnum] = matched_be
        title_journal = f"{rline} {(matched_be or {}).get('title', '')} {(matched_be or {}).get('journal', '')}"
        passage_blob = str((matched_be or {}).get("abstract") or "").strip()
        ref_metadata_tokens[rnum] = _domain_tokens(title_journal)
        ref_passage_tokens[rnum] = _domain_tokens(passage_blob) if passage_blob else set()

    cited_nums: set[str] = set()
    claim_support_audit: list[dict[str, Any]] = []
    claim_support_low: list[dict[str, Any]] = []

    def _expand_bracket(br_str: str) -> list[str]:
        out: list[str] = []
        for part in br_str.strip("[]").split(","):
            part = part.strip()
            if "-" in part or "–" in part:
                a, b = re.split(r"[-–]", part, maxsplit=1)
                if a.strip().isdigit() and b.strip().isdigit():
                    for k in range(int(a.strip()), int(b.strip()) + 1):
                        out.append(str(k))
            elif part.isdigit():
                out.append(part)
        return out

    def _token_overlap(a: set[str], b: set[str]) -> list[str]:
        direct = sorted(a & b)
        if direct:
            return direct
        if a and b:
            bp = {t[:5] for t in b if len(t) >= 5}
            return sorted(t for t in a if len(t) >= 5 and t[:5] in bp)
        return []

    for sec in sections:
        if _section_role(sec["title"]) == "references":
            continue
        for lineno, line in sec["lines"]:
            for sent in RE_SENT_SPLIT.split(line):
                sent = sent.strip()
                if not sent:
                    continue
                s_inv = wio.extract_invariants(sent)
                if not s_inv["bracket_citations"]:
                    continue
                sent_toks = _domain_tokens(sent)
                for br in s_inv["bracket_citations"]:
                    for rnum in _expand_bracket(br):
                        cited_nums.add(rnum)
                        if rnum not in ref_metadata_tokens:
                            continue
                        be = ref_matched_bib.get(rnum)
                        title_toks = ref_metadata_tokens[rnum]
                        pass_toks = ref_passage_tokens[rnum]
                        meta_exists = be is not None or any(d.lower() in doi_locs for d in wio.extract_invariants(ref_entry_map[rnum])["dois"])
                        topic_overlap = _token_overlap(sent_toks, title_toks | pass_toks)
                        passage_overlap = _token_overlap(sent_toks, pass_toks) if pass_toks else []
                        topically_related = bool(topic_overlap)
                        passage_supported = bool(passage_overlap)
                        if len(title_toks | pass_toks) >= 3 and len(sent_toks) >= 2:
                            if passage_supported:
                                status = "SUPPORTED_BY_PASSAGE"
                            elif topically_related:
                                status = "TOPICAL_RELATED_NO_PASSAGE"
                            else:
                                status = "CLAIM_SUPPORT_LOW"
                        else:
                            status = "INSUFFICIENT_METADATA"
                        rec = {
                            "citation": f"[{rnum}]",
                            "draft_line": lineno,
                            "section": sec["title"],
                            "tier1_metadata_exists": meta_exists,
                            "tier2_topically_related": topically_related,
                            "tier3_passage_supported": passage_supported,
                            "support_status": status,
                            "shared_domain_tokens": topic_overlap[:8],
                            "shared_passage_tokens": passage_overlap[:8],
                            "matched_passage_excerpt": str((be or {}).get("abstract") or "")[:180],
                            "sentence_excerpt": sent[:160],
                            "reference_excerpt": ref_entry_map[rnum][:140],
                        }
                        claim_support_audit.append(rec)
                        evidence_map.append({
                            "claim_location": f"{draft_path.name}:L{lineno}",
                            "section": sec["title"],
                            "token": f"cite:[{rnum}]",
                            "status": status,
                            "tier1_metadata_exists": meta_exists,
                            "tier2_topically_related": topically_related,
                            "tier3_passage_supported": passage_supported,
                            "shared_domain_tokens": topic_overlap[:6],
                            "source_anchors": [f"References:[{rnum}]"],
                        })
                        if status == "CLAIM_SUPPORT_LOW":
                            claim_support_low.append(rec)

    dangling_ref_nums = sorted(cited_nums - ref_numbers_defined, key=int) if cited_nums else []
    uncited_ref_nums = sorted(ref_numbers_defined - cited_nums, key=int) if ref_numbers_defined else []

    # Gate 3: Results vs Discussion layer separation & duplication guard (r/PhD 1tmecro)
    rd_issues: list[dict[str, Any]] = []
    results_secs = [s for s in sections if _section_role(s["title"]) == "results"]
    disc_secs = [s for s in sections if _section_role(s["title"]) == "discussion"]
    combined_secs = [s for s in sections if _section_role(s["title"]) == "results_and_discussion_combined"]

    if combined_secs and idx["journal_structure_spec"] == "UNSPECIFIED":
        rd_issues.append({
            "code": "UNVERIFIED_COMBINED_RD_TEMPLATE",
            "severity": "medium",
            "detail": "Manuscript merges Results and Discussion while target journal structure is UNSPECIFIED; state structure choice explicitly or confirm target journal requirement.",
        })

    results_nums: set[str] = set()
    for rs in results_secs:
        results_nums |= set(wio.extract_invariants(rs["text"])["bare_numbers"])
        for lineno, line in rs["lines"]:
            for pat in RESULTS_MECHANISM_LEAK_PATTERNS:
                m = pat.search(line)
                if m:
                    rd_issues.append({
                        "code": "MECHANISM_INTERPRETATION_IN_RESULTS",
                        "severity": "high",
                        "line": lineno,
                        "matched": m.group(0),
                        "detail": "Move mechanistic interpretation or causal speculation from Results to Discussion.",
                    })

    for ds in disc_secs:
        disc_nums = set(wio.extract_invariants(ds["text"])["bare_numbers"])
        non_trivial_res = {n for n in results_nums if n not in {"0", "1", "2", "3"}}
        overlap = non_trivial_res & disc_nums
        if len(non_trivial_res) >= 4 and len(overlap) / max(1, len(non_trivial_res)) >= 0.75:
            rd_issues.append({
                "code": "DISCUSSION_VERBATIM_NUMERIC_REPETITION",
                "severity": "medium",
                "overlap_ratio": round(len(overlap) / len(non_trivial_res), 3),
                "detail": "Discussion repeats >=75% of Results numeric values; synthesize meaning, literature comparisons, and limitations instead of restating every figure.",
            })

    # Gate 4: Section & declaration completeness
    missing_sections: list[str] = []
    for req in ("abstract", "introduction", "methods", "references"):
        if req not in roles_present:
            missing_sections.append(req)
    if "results_and_discussion_combined" not in roles_present:
        if "results" not in roles_present:
            missing_sections.append("results")
        if "discussion" not in roles_present:
            missing_sections.append("discussion")

    missing_declarations = [
        k for k, pat in DECLARATION_PATTERNS.items() if not pat.search(text)
    ]

    # Gate 5: Abstract vs Body numeric consistency
    abstract_secs = [s for s in sections if _section_role(s["title"]) == "abstract"]
    body_text = "\n".join(s["text"] for s in sections if _section_role(s["title"]) not in ("abstract", "references"))
    abstract_only_numbers: list[str] = []
    if abstract_secs and body_text:
        abs_nums = set(wio.extract_invariants(abstract_secs[0]["text"])["bare_numbers"])
        body_nums = set(wio.extract_invariants(body_text)["bare_numbers"])
        abstract_only_numbers = sorted(n for n in (abs_nums - body_nums) if n not in {"0", "1", "2", "3"})

    # Gate 6: Acronym first-definition check
    defined_acronyms: dict[str, int] = {}
    undefined_acronyms: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in RE_ACRONYM_DEF.finditer(line):
            ac = m.group(2)
            defined_acronyms.setdefault(ac, lineno)
    seen_undef: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("#"):
            continue
        for m in RE_ACRONYM_USE.finditer(line):
            ac = m.group(1)
            if ac in COMMON_ACRONYM_WHITELIST or ac in seen_undef:
                continue
            if ac not in defined_acronyms or lineno < defined_acronyms[ac]:
                seen_undef.add(ac)
                undefined_acronyms.append({"acronym": ac, "first_line": lineno, "defined_at": defined_acronyms.get(ac)})

    # Gate 7: Bilingual academic prose & press-release narrative check
    prose_audit = wio.audit_academic_prose(text)

    # Gate 8: Explicit [EVIDENCE_GAP] markers
    explicit_gaps = [m.group(1).strip() for m in RE_GAP_TAG.finditer(text)]

    blocking_count = (
        len(ungrounded_numbers)
        + len(condition_binding_mismatches)
        + len(ungrounded_dois)
        + len(dangling_ref_nums)
        + len(claim_support_low)
        + sum(1 for x in rd_issues if x["severity"] == "high")
        + len(abstract_only_numbers)
        + prose_audit["high_count"]
    )

    return {
        "ok": blocking_count == 0,
        "blocking_issue_count": blocking_count,
        "draft_path": draft_path.name,
        "journal_structure_spec": idx["journal_structure_spec"],
        "sections_detected": [{"title": s["title"], "role": _section_role(s["title"]), "lines": [s["start_line"], s["end_line"]]} for s in sections],
        "missing_sections": missing_sections,
        "missing_declarations": missing_declarations,
        "image_only_without_caption": idx.get("image_only_without_caption", []),
        "numeric_grounding": {
            "grounded_count": len(grounded_numbers),
            "ungrounded_count": len(ungrounded_numbers),
            "ungrounded_numbers": ungrounded_numbers,
            "condition_binding_mismatches_count": len(condition_binding_mismatches),
            "condition_binding_mismatches": condition_binding_mismatches,
        },
        "citation_alignment": {
            "cited_numbers": sorted(cited_nums, key=int) if cited_nums else [],
            "defined_references": sorted(ref_numbers_defined, key=int) if ref_numbers_defined else [],
            "dangling_citations": dangling_ref_nums,
            "uncited_references": uncited_ref_nums,
            "ungrounded_dois": ungrounded_dois,
            "indexed_bib_entries": len(idx["bib_entries"]),
            "claim_support_low_count": len(claim_support_low),
            "claim_support_low": claim_support_low,
            "claim_support_audit": claim_support_audit[:40],
        },
        "results_discussion_separation": rd_issues,
        "abstract_body_mismatch_numbers": abstract_only_numbers,
        "undefined_or_premature_acronyms": undefined_acronyms[:20],
        "prose_deai_audit": prose_audit,
        "explicit_evidence_gaps": explicit_gaps,
        "evidence_map": evidence_map[:160],
    }


def build_grounded_scaffold(input_dir: Path, out_md: Path) -> dict[str, Any]:
    """Generate a zero-hallucination IMRaD manuscript scaffold with paragraph-level empirical sentences from /input."""
    idx = build_material_index(input_dir)
    lines: list[str] = ["# Abstract", ""]
    lines.append(
        "<!-- Four-beat structure: Problem -> Gap -> Approach -> Key Finding. "
        "Every numeric value must match Body Results and /input tables verbatim. -->"
    )
    lines.append("[EVIDENCE_GAP: Summarize problem, gap, method, and grounded key metrics from Results]")
    lines.extend(["", "# Introduction", ""])
    lines.append("[EVIDENCE_GAP: State specific research context, cited gap, and verifiable contributions]")
    lines.extend(["", "# Methods", ""])
    lines.append("Source files indexed from `/input`: " + ", ".join(f["relpath"] for f in idx["files"]) + ".")
    for f in idx["files"]:
        if f.get("format") == "csv" and f.get("ok"):
            lines.append(
                f"Structured dataset `{f['relpath']}` contains {f.get('csv_rows', 0)} records across "
                f"{len(f.get('csv_columns', []))} variables ({', '.join(f.get('csv_columns', [])[:10])})."
            )
    lines.append("[EVIDENCE_GAP: Insert only recorded experimental steps, sample sizes, software versions, and statistical tests]")
    lines.extend(["", "# Results", ""])
    if idx["journal_structure_spec"] == "UNSPECIFIED":
        lines.append("<!-- Target journal Results/Discussion layout is UNSPECIFIED; kept separate by default. -->")

    # Transcribe supplied CSV tables and synthesize grounded descriptive-statistics sentences
    table_num = 0
    for f in idx["files"]:
        if f.get("format") == "csv" and f.get("ok"):
            csv_path = input_dir / f["relpath"]
            rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8", errors="replace").splitlines()))
            summaries = f.get("numeric_summaries") or {}
            if summaries:
                lines.append(f"Quantitative summary across `{f['relpath']}` (n = {f.get('csv_rows', len(rows))}):")
                for col_name, st in list(summaries.items())[:10]:
                    lines.append(
                        f"- `{col_name}` ({f['relpath']}:summary:{col_name}): n = {st['n']}, "
                        f"mean = {st['mean']:.2f} (SD = {st['sd']:.2f}), median = {st['median']:.2f} "
                        f"(IQR = {st['iqr']:.2f}), range = {st['min']:.2f}–{st['max']:.2f}."
                    )
                lines.append("")
            if rows and len(rows) <= 25:
                table_num += 1
                cols = list(rows[0].keys())
                lines.append(f"Table {table_num}: Quantitative summary transcribed directly from `{f['relpath']}`.")
                lines.append("")
                lines.append("| " + " | ".join(cols) + " |")
                lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
                for r in rows[:25]:
                    lines.append("| " + " | ".join(str(r.get(c, "")).strip() for c in cols) + " |")
                lines.append("")
            elif summaries:
                table_num += 1
                lines.append(f"Table {table_num}: Full-cohort descriptive statistics computed directly from `{f['relpath']}` (n = {len(rows)}).")
                lines.append("")
                lines.append("| Variable | n | Mean | SD | Median | IQR | Min | Max |")
                lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
                for col_name, st in list(summaries.items())[:12]:
                    lines.append(
                        f"| {col_name} | {st['n']} | {st['mean']:.2f} | {st['sd']:.2f} | "
                        f"{st['median']:.2f} | {st['iqr']:.2f} | {st['min']:.2f} | {st['max']:.2f} |"
                    )
                lines.append("")
    if idx["figure_captions"]:
        for fc in idx["figure_captions"]:
            lines.append(f"- Grounded caption anchor (`{fc['source']}`): {fc['caption']}")
        lines.append("")
    for img in idx.get("image_only_without_caption", []):
        lines.append(f"- [EVIDENCE_GAP: Figure `{img}` is image-only without accompanying text caption or data table; author must verify caption and plotted values]")
    if idx.get("image_only_without_caption"):
        lines.append("")

    lines.extend(["# Discussion", ""])
    lines.append(
        "<!-- Four-beat synthesis: (1) direct answer to research question without repeating all Results numbers; "
        "(2) agreement/discrepancy vs cited literature with methodological differences; "
        "(3) explicitly hedged mechanistic hypotheses; (4) concrete method/sample/statistical limitations. -->"
    )
    lines.append("[EVIDENCE_GAP: Synthesize findings, compare with indexed references, and detail concrete limitations]")
    lines.extend(["", "# Data and Code Availability", ""])
    lines.append("[EVIDENCE_GAP: State data/code repository availability per author confirmation]")
    lines.extend(["", "# Competing Interests", ""])
    lines.append("[EVIDENCE_GAP: State competing interests and ethics approval number if applicable]")
    lines.extend(["", "# References", ""])
    if idx["bib_entries"]:
        for i, b in enumerate(idx["bib_entries"], start=1):
            doi_part = f" doi:{b['doi']}" if b.get("doi") else ""
            lines.append(f"[{i}] {b.get('author') or 'Unknown'}. {b.get('title') or 'Untitled'}. {b.get('journal') or ''} {b.get('year') or ''}.{doi_part}".strip())
    else:
        lines.append("[EVIDENCE_GAP: No bibliography entries found in /input]")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return {
        "ok": True,
        "scaffold_path": str(out_md),
        "tables_transcribed": table_num,
        "references_formatted": len(idx["bib_entries"]),
        "journal_structure_spec": idx["journal_structure_spec"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Academic Manuscript Workbench Engine.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_idx = sub.add_parser("index", help="Build traceable material_index.json from /input.")
    p_idx.add_argument("--input-dir", required=True)
    p_idx.add_argument("--out-json", required=True)

    p_scf = sub.add_parser("scaffold", help="Generate zero-hallucination IMRaD draft scaffold and transcribed tables from /input.")
    p_scf.add_argument("--input-dir", required=True)
    p_scf.add_argument("--out-md", required=True)

    p_aud = sub.add_parser("audit", help="Run 8-gate provenance & quality audit on manuscript draft.")
    p_aud.add_argument("--input-dir", required=True)
    p_aud.add_argument("--draft", required=True)
    p_aud.add_argument("--out-json", required=True)

    p_exp = sub.add_parser("export", help="Export editable OOXML .docx, optional preview, and structured result.json.")
    p_exp.add_argument("--input-dir", required=True)
    p_exp.add_argument("--draft", required=True)
    p_exp.add_argument("--case-id", required=True)
    p_exp.add_argument("--out-dir", required=True)
    p_exp.add_argument("--font", default="Liberation Serif")
    p_exp.add_argument("--render-preview", action="store_true", help="Attempt PDF/PNG preview (enabled by default unless --no-preview).")
    p_exp.add_argument("--no-preview", action="store_true", help="Skip LibreOffice/pdftoppm preview probe.")

    args = parser.parse_args(argv)

    if args.cmd == "index":
        res = build_material_index(Path(args.input_dir))
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "file_count": res["file_count"], "indexed_numbers": res["indexed_number_count"], "journal_structure_spec": res["journal_structure_spec"]}, ensure_ascii=False))
        return 0

    if args.cmd == "scaffold":
        res = build_grounded_scaffold(Path(args.input_dir), Path(args.out_md))
        print(json.dumps(res, ensure_ascii=False))
        return 0

    if args.cmd == "audit":
        res = audit_manuscript(Path(args.draft), Path(args.input_dir))
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": res.get("ok", False),
            "blocking_issue_count": res.get("blocking_issue_count", -1),
            "ungrounded_numbers": len(res.get("numeric_grounding", {}).get("ungrounded_numbers", [])),
            "condition_binding_mismatches": res.get("numeric_grounding", {}).get("condition_binding_mismatches_count", 0),
            "claim_support_low": res.get("citation_alignment", {}).get("claim_support_low_count", 0),
            "rd_issues": len(res.get("results_discussion_separation", [])),
            "dangling_citations": len(res.get("citation_alignment", {}).get("dangling_citations", [])),
            "missing_sections": res.get("missing_sections", []),
        }, ensure_ascii=False))
        return 0

    if args.cmd == "export":
        draft_path = Path(args.draft)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        audit = audit_manuscript(draft_path, Path(args.input_dir))
        md_text = draft_path.read_text(encoding="utf-8")
        docx_path = out_dir / "manuscript.docx"
        docx_meta = wio.build_docx_from_markdown(md_text, docx_path, font_name=args.font)
        do_preview = not getattr(args, "no_preview", False)
        preview_meta: dict[str, Any] = {"rendered": False, "note": "preview explicitly skipped via --no-preview"}
        if do_preview:
            preview_meta = wio.render_docx_preview(docx_path, out_dir / "preview")

        ev_path = out_dir / "evidence_map.json"
        ev_path.write_text(json.dumps(audit.get("evidence_map", []), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        aud_path = out_dir / "manuscript_audit.json"
        aud_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        gaps: list[dict[str, Any]] = []
        for g in audit.get("explicit_evidence_gaps", []):
            gaps.append({"type": "explicit_evidence_gap", "detail": g})
        for ms in audit.get("missing_sections", []):
            gaps.append({"type": "missing_section", "detail": ms})
        for md_dec in audit.get("missing_declarations", []):
            gaps.append({"type": "missing_declaration", "detail": md_dec})
        for img in audit.get("image_only_without_caption", []):
            gaps.append({"type": "image_only_without_caption", "detail": f"Image file '{img}' has no text caption or numeric table in /input."})
        for csl in audit.get("citation_alignment", {}).get("claim_support_low", []):
            gaps.append({"type": "claim_support_low", "detail": f"{csl['citation']} at L{csl['draft_line']} lacks topical overlap with citing claim."})
        if do_preview and not preview_meta.get("rendered"):
            gaps.append({"type": "preview_renderer_unavailable", "detail": preview_meta.get("degraded_reason", "unavailable")})

        artifacts = [draft_path.name, docx_path.name, ev_path.name, aud_path.name]
        if preview_meta.get("pdf_path"):
            artifacts.append(Path(preview_meta["pdf_path"]).name)

        case_contract = wio.load_case_contract(Path(args.input_dir))
        result_payload = wio.build_writing_result(
            case_id=args.case_id,
            task_id="academic-manuscript-writing",
            deliverable={
                "manuscript_markdown": str(draft_path.name),
                "manuscript_docx": docx_path.name,
                "sections_detected": [s["title"] for s in audit.get("sections_detected", [])],
                "journal_structure_spec": audit.get("journal_structure_spec"),
            },
            preserved_facts={
                "grounded_numbers_count": audit["numeric_grounding"]["grounded_count"],
                "ungrounded_numbers_count": audit["numeric_grounding"]["ungrounded_count"],
                "ungrounded_dois": audit["citation_alignment"]["ungrounded_dois"],
                "abstract_body_mismatch_numbers": audit["abstract_body_mismatch_numbers"],
            },
            evidence_map=audit.get("evidence_map", []),
            change_log=[
                {"action": "audit_8_gates", "blocking_issues": audit["blocking_issue_count"], "rd_issues": audit["results_discussion_separation"]},
                {"action": "export_editable_docx", "font": args.font, "preview": preview_meta},
            ],
            artifacts=artifacts,
            sources=[{"relpath": f["relpath"], "format": f["format"]} for f in build_material_index(Path(args.input_dir))["files"]],
            gaps=gaps,
            limitations=[
                "Offline audit verifies provenance against supplied /input files only; external bibliographic databases are not queried.",
                "Journal structure remains UNSPECIFIED unless explicitly stated in /input guidelines.",
            ],
            verification={
                "audit_ok": audit["ok"],
                "blocking_issue_count": audit["blocking_issue_count"],
                "docx_build": docx_meta,
                "preview": preview_meta,
            },
            case_contract=case_contract,
        )
        res_path = out_dir / "result.json"
        res_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "audit_ok": audit["ok"], "docx": str(docx_path), "result_json": str(res_path)}, ensure_ascii=False))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
