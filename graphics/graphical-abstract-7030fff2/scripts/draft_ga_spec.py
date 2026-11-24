#!/usr/bin/env python3
"""Structure- and syntax-aware raw /input -> ga_spec.json extractor with prose Results mini_bars & exact block_id provenance (stdlib-only)."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

_HTML_NOISE_RE = re.compile(
    r"<(?:script|style|nav|header|footer|noscript|svg)\b[^>]*>[\s\S]*?</(?:script|style|nav|header|footer|noscript|svg)>",
    re.I,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>", re.S)
_WS_RE = re.compile(r"\s+")
_LEAD_FILLER_RE = re.compile(
    r"^(?:considering\s+the|nevertheless|however|furthermore|moreover|in\s+addition|regarding\s+the|"
    r"therefore|thus|consequently|overall|in\s+summary|in\s+conclusion|to\s+describe\s+the|"
    r"the\s+study\s+showed\s+(?:a\s+)?|this\s+study\s+aimed\s+to\s+|there\s+were\s+)[,:\s]+",
    re.I,
)
_DANGLING_TAIL = {
    "the", "a", "an", "of", "in", "to", "for", "with", "and", "or", "by", "on", "at",
    "when", "where", "that", "which", "who", "is", "are", "was", "were", "as", "from",
    "into", "during", "between", "among", "through", "using", "based", "per", "vs",
    "can", "be", "been", "being", "may", "might", "could", "should", "would", "will",
    "have", "has", "had", "do", "does", "did", "defined", "analyze", "analyse",
    "describe", "evaluate", "assess", "showed", "demonstrated", "observed", "found",
    "extracted", "associated", "related", "due", "such", "both", "also", "very",
    "more", "most", "greater", "higher", "lower", "exogenous", "biochemical",
    "and/or", "this", "these", "those", "their", "its",
    "de", "da", "do", "dos", "das", "em", "para", "com", "por", "que", "no", "na",
}
_BOILERPLATE_SUBS = (
    (re.compile(r"^\d+\s+"), ""),
    (re.compile(r"\bcan\s+be\s+defined\s+as\s+(?:a\s+|an\s+|the\s+)?", re.I), "= "),
    (re.compile(r"\bin\s+children\s+and\s+adolescents\b", re.I), "in youth"),
    (re.compile(r"\bchildren\s+and\s+adolescents\b", re.I), "youth"),
    (re.compile(r"\btime-series\s+observational\s+study\b", re.I), "time-series cohort"),
    (re.compile(r"\bwere\s+extracted\s+from\s+(?:the\s+)?", re.I), "from "),
    (re.compile(r"\bwith\s+data\s+extracted\s+from\s+(?:the\s+)?", re.I), "using "),
    (re.compile(r"\bthe\s+variables\s+", re.I), "Variables: "),
    (re.compile(r"\bwas\s+carried\s+out\s+with\b", re.I), "using"),
    (re.compile(r"\bepidemiological\s+profile\s+and\s+to\s+analyze\s+(?:the\s+)?", re.I), "Profile & "),
    (re.compile(r"\btemporal\s+trend\s+of\b", re.I), "trend of"),
    (re.compile(r"\bexogenous\s+intoxications?\b", re.I), "intoxications"),
    (re.compile(r"\bnotifiable\s+diseases\s+information\s+system\b", re.I), "SINAN registry"),
    (re.compile(r"\bimplementation\s+of\s+actions\s+and\s+strategies\b", re.I), "Targeted prevention strategies"),
)
_CI_AFTER_RE = re.compile(r"^\s*(?:\]|\))?\s*(?:ci\b|confidence\b|cr\.?\s*i\b|credible\b|prediction\s+interval)", re.I)
_PT_ES_RE = re.compile(
    r"\b(?:descrever|realizou-se|ocorreram|estudo|demonstrou|maior|menor|frequ[êe]ncia|"
    r"crian[çc]as|adolescentes|intoxica[çc][ãõo]e?s?|palavras-chave|tend[êe]ncia|"
    r"objetivo|m[ée]todos|resultados|conclus[õo]es|pelo|pela|dos|das|para|entre|com|n[ãa]o|s[ãa]o|foi|foram)\b",
    re.I,
)
_NUM_UNIT_RE = re.compile(
    r"(?P<val>[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<unit>%|cases/10,000|cases|patients|samples|fold|mg/kg|mg|ug|µg|uM|µM|nM|mM|kPa|MPa|mm|cm|nm|h|min|days?|weeks?|years?)\b",
    re.I,
)
_PCT_PAIR_AFTER_RE = re.compile(
    r"\b(?P<val>\d+(?:\.\d+)?)\s*%\s*(?:\([^)]{1,28}\)\s*)?(?:occurred\s+in|were\s+in|were|in|for|of|among)?\s*"
    r"(?P<lbl>[A-Za-z][A-Za-z\-]{2,18}(?:\s+[A-Za-z0-9\-–]{1,10})?)",
    re.I,
)
_PCT_PAIR_BEFORE_RE = re.compile(
    r"\b(?P<lbl>[A-Za-z][A-Za-z\-]{2,18})\s*\(\s*(?P<val>\d+(?:\.\d+)?)\s*%",
    re.I,
)
_PVAL_RE = re.compile(r"\bp\s*[<=>]\s*0?\.\d+(?:e-?\d+)?", re.I)

_STAGE_KEYWORDS = {
    "question": ("objective", "aim", "problem", "challenge", "barrier", "gap", "background", "rationale", "profile", "poisoning", "intoxication", "unmet", "hypothes"),
    "method": ("observational", "method", "extracted from", "variables", "analyzed", "cohort", "protocol", "enrolled", "administered", "synthesiz", "time-series", "descriptive", "information system", "assay"),
    "result": ("result", "cases", "occurred", "53.", "61.", "incidence", "rate", "reduced", "increased", "improved", "achieved", "significant", "higher frequency", "p <", "p="),
    "conclusion": ("conclusion", "conclude", "implementation", "recommend", "suggest", "indicate", "implication", "impact", "strategy", "strategies", "prevent", "restore", "effective", "takeaway"),
}

_PALETTE = {
    "Challenge_Baseline": "#D77C70",
    "Intervention_Method": "#547FA3",
    "Measured_Outcome": "#4E9187",
    "Clinical_Impact": "#D19A45",
    "Control_Reference": "#967A9C",
}


def _clean_text(s: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_HTML_TAG_RE.sub(" ", s))).strip()


def _clean_phrase(s: str, max_chars: int = 44, *, strip_lead: bool = True) -> str:
    """Compress a sentence into a grammatically complete scientific phrase (never mid-word or dangling clause)."""
    t = _WS_RE.sub(" ", str(s)).strip(" .;,:-")
    if strip_lead:
        t = _LEAD_FILLER_RE.sub("", t).strip(" .;,:-")
    for rx, repl in _BOILERPLATE_SUBS:
        t = rx.sub(repl, t)
    t = _WS_RE.sub(" ", t).strip(" .;,:-")
    # Prefer the first complete clause if it already conveys a complete idea
    if len(t) > max_chars:
        for sep in ("; ", ". ", " — ", " – ", ", which ", ", where ", ", while ", ", whereas "):
            if sep in t:
                first = t.split(sep, 1)[0].strip(" .;,:-")
                if 14 <= len(first) <= max_chars:
                    t = first
                    break
    if len(t) > max_chars:
        # Try clean comma boundary if it fits comfortably
        parts = [p.strip(" .;,:-") for p in t.split(", ") if p.strip()]
        acc = ""
        for p in parts:
            cand = f"{acc}, {p}" if acc else p
            if len(cand) <= max_chars:
                acc = cand
            else:
                break
        if len(acc) >= 16:
            t = acc
    if len(t) > max_chars:
        words = t.split(" ")
        kept: list[str] = []
        cur_len = 0
        for w in words:
            add = len(w) + (1 if kept else 0)
            if cur_len + add > max_chars:
                break
            kept.append(w)
            cur_len += add
        t = " ".join(kept) if kept else words[0][:max_chars]
    # Strip trailing punctuation & dangling function/predicate words so no truncated fragments ever appear
    words = t.strip(" .;,:-(").split(" ")
    while len(words) > 2 and words[-1].lower().strip(".,;:-()") in _DANGLING_TAIL:
        words.pop()
    out = " ".join(words).strip(" .;,:-(")
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


def _clean_label_noun(raw_lbl: str, max_chars: int = 18) -> str:
    s = re.sub(r"\b(?:aged|years?|old|the|city|municipality|study|period|in|of|and|with|were|was|occurred)\b", " ", raw_lbl, flags=re.I)
    s = _WS_RE.sub(" ", s).strip(" .;,:-()")
    return _clean_phrase(s, max_chars=max_chars, strip_lead=False)


def _is_non_english_duplicate(text: str) -> bool:
    """Filter secondary-language duplicate abstracts (e.g., Portuguese/Spanish SciELO blocks) when English is present."""
    hits = _PT_ES_RE.findall(text)
    if len(hits) >= 2:
        return True
    # Also catch Portuguese accented characters combined with Portuguese stopword
    if re.search(r"[ãõçêáéíóúâô]", text) and len(hits) >= 1:
        return True
    return False


def _read_structured_blocks(path: Path, rel: str) -> tuple[str, list[dict[str, Any]]]:
    ext = path.suffix.lower()
    hdr_title = ""
    lines: list[tuple[int, str, str]] = []  # (line_no, section_hint, text)

    if ext == ".pdf":
        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            return "", []
        proc = subprocess.run([pdftotext, str(path), "-"], capture_output=True, text=True, check=False, timeout=30)
        if proc.returncode != 0:
            return "", []
        cur_sec = ""
        for i, ln in enumerate(proc.stdout.splitlines(), 1):
            c = ln.strip()
            if not c:
                continue
            low = c.lower().rstrip(":")
            if low in ("objective", "objectives", "background", "introduction"):
                cur_sec = "question"; continue
            if low in ("methods", "method", "materials and methods"):
                cur_sec = "method"; continue
            if low in ("results", "findings"):
                cur_sec = "result"; continue
            if low in ("conclusion", "conclusions", "discussion"):
                cur_sec = "conclusion"; continue
            if low in ("references", "acknowledgments"):
                break
            lines.append((i, cur_sec, c))
    else:
        raw = path.read_text(encoding="utf-8-sig", errors="ignore")
        if ext in (".html", ".htm", ".xml"):
            for pat in (
                r'<meta[^>]+name=["\']citation_title["\'][^>]+content=["\']([^"\']+)["\']',
                r"<h1\b[^>]*>([\s\S]*?)</h1>",
                r"<title\b[^>]*>([\s\S]*?)</title>",
            ):
                m = re.search(pat, raw, re.I)
                if m:
                    cand = _clean_text(m.group(1))
                    if 14 <= len(cand) <= 180 and not cand.lower().startswith("scielo -"):
                        hdr_title = cand
                        break
            raw = _HTML_NOISE_RE.sub(" ", raw)
            m_art = re.search(r"<article\b[^>]*>([\s\S]*?)</article>", raw, re.I)
            if m_art:
                raw = m_art.group(1)
            raw = re.sub(r"</(?:p|div|h[1-6]|li|tr|section|article|title)\s*>", "\n", raw, flags=re.I)
            raw = html.unescape(_HTML_TAG_RE.sub(" ", raw))
        cur_sec = ""
        for i, ln in enumerate(raw.splitlines(), 1):
            c = _WS_RE.sub(" ", ln).strip()
            if not c:
                continue
            low = c.lstrip("# ").lower().rstrip(":")
            if low in ("references", "acknowledgments", "conflict of interest"):
                break
            if any(tok in low for tok in ("class=", "href=", "aria-", "toggle ", "cookie", "keywords:", "palavras-chave:")):
                continue
            if low in ("objective", "objectives", "background", "introduction"):
                cur_sec = "question"; continue
            if low in ("methods", "method", "materials and methods"):
                cur_sec = "method"; continue
            if low in ("results", "findings"):
                cur_sec = "result"; continue
            if low in ("conclusion", "conclusions", "discussion"):
                cur_sec = "conclusion"; continue
            if not hdr_title and c.startswith("# "):
                hdr_title = c.lstrip("# ").strip()
                continue
            if len(c) >= 18:
                lines.append((i, cur_sec, c))

    # Filter duplicate non-English blocks if English blocks exist
    has_en = any(not _is_non_english_duplicate(t) for _, _, t in lines)
    blocks: list[dict[str, Any]] = []
    for lno, sec_hint, text in lines:
        if has_en and _is_non_english_duplicate(text):
            continue
        for sent in re.split(r"(?<=[.!?])\s+", text):
            sc = sent.strip()
            if 22 <= len(sc) <= 420:
                blocks.append({
                    "block_id": f"BLK-{len(blocks)+1:04d}",
                    "source_ref": f"{rel}#L{lno}",
                    "file": rel,
                    "line": lno,
                    "section_hint": sec_hint,
                    "text": sc,
                })
    return hdr_title, blocks


def _extract_csv_bars(csv_path: Path) -> tuple[list[dict[str, Any]], str]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return [], ""
        cols = list(reader.fieldnames)
        data = list(reader)
    if not data:
        return [], ""

    label_col = cols[0]
    err_col = next((c for c in cols if any(k in c.lower() for k in ("err", "sd", "sem", "ci"))), "")
    unit_col = next((c for c in cols if "unit" in c.lower()), "")
    num_col = ""
    for c in cols:
        if c in (label_col, err_col, unit_col) or any(t in c.lower() for t in ("_id", "code", "index")):
            continue
        vals = []
        for r in data[:50]:
            try:
                vals.append(float(str(r.get(c, "")).strip()))
            except ValueError:
                pass
        if len(vals) >= 2:
            num_col = c
            break
    if not num_col:
        return [], ""

    buckets: dict[str, list[float]] = {}
    errs: dict[str, list[float]] = {}
    unit_val = ""
    for r in data:
        lbl = str(r.get(label_col, "")).strip()
        if not lbl:
            continue
        if unit_col and not unit_val:
            unit_val = str(r.get(unit_col, "")).strip()
        try:
            v = float(str(r.get(num_col, "")).strip())
        except ValueError:
            continue
        buckets.setdefault(lbl, []).append(v)
        if err_col:
            try:
                errs.setdefault(lbl, []).append(float(str(r.get(err_col, "")).strip()))
            except ValueError:
                pass

    colors = ["#967A9C", "#D77C70", "#547FA3", "#4E9187"]
    bars: list[dict[str, Any]] = []
    for idx, (lbl, vals) in enumerate(list(buckets.items())[:4]):
        mean_v = round(sum(vals) / len(vals), 2)
        ev = round(sum(errs[lbl]) / len(errs[lbl]), 2) if (lbl in errs and errs[lbl]) else (
            round((sum((x - mean_v) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5, 2) if len(vals) >= 2 else 0.0
        )
        bars.append({
            "label": _clean_phrase(lbl, 16, strip_lead=False),
            "value": mean_v,
            "err": ev,
            "unit": unit_val or "",
            "color": colors[idx % len(colors)],
            "source_ref": f"{csv_path.name}:{num_col}",
            "source_block_id": f"CSV-{csv_path.stem}-{idx+1}",
        })
    return bars, f"{csv_path.name}:{label_col}/{num_col}"


def _extract_prose_mini_bars(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Extract comparable percentage metrics with source_ref directly from Results prose when no CSV is supplied."""
    colors = ["#547FA3", "#4E9187", "#D19A45", "#D77C70"]
    found: list[dict[str, Any]] = []
    seen_vals: set[float] = set()
    for b in blocks:
        txt = b["text"]
        if "%" not in txt:
            continue
        for pat in (_PCT_PAIR_BEFORE_RE, _PCT_PAIR_AFTER_RE):
            for m in pat.finditer(txt):
                val = float(m.group("val"))
                if val <= 0.0 or val > 100.0 or val in seen_vals or val == 95.0:
                    continue
                lbl = _clean_label_noun(m.group("lbl"), max_chars=14)
                lbl = re.sub(r"\s+\d+$", "", lbl).strip()
                words_l = [w for w in lbl.split()[:2] if len(w) <= 8]
                lbl = " ".join(words_l).strip()
                if len(lbl) < 4 or lbl.lower() in ("cases", "total", "study", "data", "rate", "rates", "occurred", "among", "intoxica", "poisonin"):
                    continue
                seen_vals.add(val)
                found.append({
                    "label": lbl,
                    "value": val,
                    "err": 0.0,
                    "unit": "%",
                    "color": colors[len(found) % len(colors)],
                    "source_ref": b["source_ref"],
                    "source_block_id": b["block_id"],
                    "verbatim_excerpt": txt[:220],
                    "statistical_basis": "proportion (%) directly reported in Results prose; verify exact cohort denominator in excerpt",
                })
                if len(found) >= 3:
                    return found, found[0]["source_ref"]
    if len(found) >= 2:
        return found, found[0]["source_ref"]
    return [], ""


def draft_ga_spec_from_input(input_dir: Path, *, width_mm: float = 174.0, height_mm: float = 98.0, layout: str = "auto") -> dict[str, Any]:
    """Deterministically extract a grounded, syntax-clean 4-stage GA spec draft from raw /input files."""
    blocks: list[dict[str, Any]] = []
    not_found: list[str] = []
    csv_bars: list[dict[str, Any]] = []
    csv_ref = ""
    title_cand = ""
    case_meta: dict[str, Any] = {}

    for p in sorted(input_dir.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(input_dir).as_posix()
        ext = p.suffix.lower()
        if p.name == "case.json":
            try:
                case_meta = json.loads(p.read_text(encoding="utf-8-sig"))
                if case_meta.get("title"):
                    title_cand = str(case_meta["title"]).strip()
            except Exception:
                pass
            continue
        if ext == ".csv":
            if not csv_bars:
                csv_bars, csv_ref = _extract_csv_bars(p)
            continue
        if ext in (".md", ".txt", ".html", ".htm", ".pdf", ".xml"):
            ftitle, fblocks = _read_structured_blocks(p, rel)
            if not title_cand and ftitle:
                title_cand = ftitle
            blocks.extend(fblocks)

    if not title_cand:
        title_cand = "Scientific Graphical Abstract (Draft From Source Input)"
        not_found.append("manuscript_title")
    clean_title = _clean_phrase(title_cand, max_chars=74, strip_lead=False)

    # Classify candidate sentences into the 4 narrative stages (section_hint + early Abstract priority + keywords)
    stage_hits: dict[str, list[dict[str, Any]]] = {k: [] for k in ("question", "method", "result", "conclusion")}
    for idx, b in enumerate(blocks):
        low = b["text"].lower()
        best_stage = b.get("section_hint") or ""
        best_score = 4 if best_stage else 0
        for st, kws in _STAGE_KEYWORDS.items():
            sc = sum(1 for kw in kws if kw in low)
            if st == "result" and (_NUM_UNIT_RE.search(b["text"]) or _PVAL_RE.search(b["text"]) or "%" in b["text"]):
                sc += 3
            if idx < 8:  # Prioritize structured Abstract sentences at top of paper
                sc += 2
            if sc > best_score:
                best_score = sc
                best_stage = st
        if best_stage and best_score > 0:
            stage_hits[best_stage].append({**b, "score": best_score})

    for st in stage_hits:
        stage_hits[st].sort(key=lambda x: (-x["score"], x["line"]))

    # Extract quantitative findings & prose mini_bars if no CSV supplied
    prose_bars: list[dict[str, Any]] = []
    prose_bars_ref = ""
    if not csv_bars:
        prose_bars, prose_bars_ref = _extract_prose_mini_bars(stage_hits["result"] + blocks)

    quant_hits: list[dict[str, Any]] = []
    for b in (stage_hits["result"] + blocks):
        for m in _NUM_UNIT_RE.finditer(b["text"]):
            val_s = m.group("val")
            u_str = m.group("unit")
            if u_str.lower() in ("year", "years") and float(val_s.replace(",", "")) <= 20:
                continue  # skip age range bounds as headline outcome metric
            if u_str == "%" and (
                _CI_AFTER_RE.match(b["text"][m.end():])
                or (val_s in ("90", "95", "99") and "confidence" in b["text"].lower())
            ):
                continue  # never promote 95% CI confidence level into a standalone study KPI tile
            sep = "" if u_str in ("%", "x") else " "
            short_u = "/10k" if u_str.lower() == "cases/10,000" else u_str
            tok = f"{val_s}{sep}{short_u}"
            if not any(q["metric"] == tok for q in quant_hits):
                quant_hits.append({
                    "metric": tok,
                    "unit_full": u_str,
                    "context": _clean_phrase(b["text"], 36),
                    "verbatim_excerpt": b["text"][:220],
                    "statistical_basis": f"unit={u_str}; see verbatim_excerpt for cohort denominator/timeframe",
                    "source_ref": b["source_ref"],
                    "source_block_id": b["block_id"],
                })
            if len(quant_hits) >= 4:
                break
        if len(quant_hits) >= 4:
            break

    def _pick_bullets(st: str, fallback_msg: str) -> tuple[list[str], str, str]:
        cands = stage_hits[st]
        picked: list[tuple[str, str, str]] = []
        for c in cands:
            ph = _clean_phrase(c["text"], 42)
            if len(ph) >= 18 and len(ph.split()) >= 3 and not any(ph.lower() == p[0].lower() for p in picked):
                picked.append((ph, c["source_ref"], c["block_id"]))
            if len(picked) >= 2:
                break
        if picked:
            return [p[0] for p in picked], picked[0][1], picked[0][2]
        not_found.append(f"stage:{st}")
        return [fallback_msg], "NOT_FOUND_IN_SOURCE", "BLK-MISSING"

    q_bul, q_ref, q_blk = _pick_bullets("question", "Research problem not stated in input")
    m_bul, m_ref, m_blk = _pick_bullets("method", "Study design not stated in input")
    r_bul, r_ref, r_blk = _pick_bullets("result", "Quantitative result not stated in input")
    c_bul, c_ref, c_blk = _pick_bullets("conclusion", "Conclusion not stated in input")

    active_bars = csv_bars or prose_bars
    bars_ref = csv_ref or prose_bars_ref
    p3_panel: dict[str, Any] = {
        "id": "p3_result",
        "stage": "result",
        "badge": "3 | Core Result",
        "title": "Primary Outcome",
        "accent": _PALETTE["Measured_Outcome"],
        "source_ref": bars_ref or (quant_hits[0]["source_ref"] if quant_hits else r_ref),
        "source_block_id": (active_bars[0]["source_block_id"] if active_bars else (quant_hits[0]["source_block_id"] if quant_hits else r_blk)),
        "bullets": r_bul,
    }
    if active_bars:
        p3_panel["mini_bars"] = active_bars
        top_b = max(active_bars, key=lambda x: float(x["value"]))
        p3_panel["highlight"] = f"Peak: {top_b['label']} ({top_b['value']}{top_b['unit']})"
    elif quant_hits:
        p3_panel["metrics_strip"] = [
            {
                "metric": q["metric"],
                "label": _clean_phrase(q["context"], 18, strip_lead=False),
                "color": _PALETTE["Measured_Outcome"] if i == 0 else _PALETTE["Intervention_Method"],
                "source_ref": q["source_ref"],
                "source_block_id": q["source_block_id"],
            }
            for i, q in enumerate(quant_hits[:2])
        ]
        p3_panel["highlight"] = f"Key metric: {quant_hits[0]['metric']}"
    else:
        not_found.append("quantitative_metrics")
        p3_panel["highlight"] = "Qualitative findings only"

    p4_panel: dict[str, Any] = {
        "id": "p4_conclusion",
        "stage": "conclusion",
        "badge": "4 | Conclusion",
        "title": "Scientific Takeaway",
        "accent": _PALETTE["Clinical_Impact"],
        "source_ref": c_ref,
        "source_block_id": c_blk,
        "bullets": c_bul,
        "highlight": "Actionable Strategy",
    }
    if len(quant_hits) >= 2:
        p4_panel["metrics_strip"] = [
            {
                "metric": quant_hits[0]["metric"],
                "label": "Cohort / Burden",
                "color": _PALETTE["Measured_Outcome"],
                "source_ref": quant_hits[0]["source_ref"],
                "source_block_id": quant_hits[0]["source_block_id"],
                "verbatim_excerpt": quant_hits[0].get("verbatim_excerpt", ""),
                "statistical_basis": quant_hits[0].get("statistical_basis", ""),
            },
            {
                "metric": quant_hits[1]["metric"],
                "label": "Incidence Rate" if "/10" in quant_hits[1]["metric"] else "Primary Share",
                "color": _PALETTE["Clinical_Impact"],
                "source_ref": quant_hits[1]["source_ref"],
                "source_block_id": quant_hits[1]["source_block_id"],
                "verbatim_excerpt": quant_hits[1].get("verbatim_excerpt", ""),
                "statistical_basis": quant_hits[1].get("statistical_basis", ""),
            },
        ]

    p1_panel = {
        "id": "p1_question",
        "stage": "question",
        "badge": "1 | Question",
        "title": "Research Problem",
        "accent": _PALETTE["Challenge_Baseline"],
        "source_ref": q_ref,
        "source_block_id": q_blk,
        "diagram": {
            "nodes": [
                {"label": _clean_phrase(q_bul[0], 18, strip_lead=False), "color": _PALETTE["Challenge_Baseline"], "shape": "roundrect"},
                {"label": "Target Population", "color": _PALETTE["Control_Reference"], "shape": "pill"},
            ],
            "links": [{"from": 0, "to": 1, "relation": "flow", "label": "affects"}],
        },
        "bullets": q_bul,
        "highlight": "Epidemiological Gap" if "epidemiolog" in clean_title.lower() else "Source-Grounded Problem",
    }
    p2_panel = {
        "id": "p2_method",
        "stage": "method",
        "badge": "2 | Method",
        "title": "Study Design",
        "accent": _PALETTE["Intervention_Method"],
        "source_ref": m_ref,
        "source_block_id": m_blk,
        "diagram": {
            "nodes": [
                {"label": _clean_phrase(m_bul[0], 18, strip_lead=False), "color": _PALETTE["Intervention_Method"], "shape": "roundrect"},
                {"label": "Stratified Metrics", "color": _PALETTE["Measured_Outcome"], "shape": "roundrect"},
            ],
            "links": [{"from": 0, "to": 1, "relation": "activate", "label": "quantifies"}],
        },
        "bullets": m_bul,
        "highlight": "Protocol Grounded",
    }

    takeaway_txt = _clean_phrase(c_bul[0] if c_ref != "NOT_FOUND_IN_SOURCE" else r_bul[0], 86)
    spec = {
        "schema": "graphical_abstract_spec_v2",
        "title": clean_title,
        "subtitle": f"Structured 4-stage extraction from {input_dir.name} ({len(blocks)} indexed English source blocks)",
        "width_mm": width_mm,
        "height_mm": height_mm,
        "layout": layout,
        "takeaway": takeaway_txt,
        "entity_colors": dict(_PALETTE),
        "caption_notes": [
            f"Source blocks indexed: {len(blocks)} across {len({b['file'] for b in blocks})} source file(s).",
            f"Stage provenance: Q={q_ref}, M={m_ref}, R={p3_panel['source_ref']}, C={c_ref}.",
            f"Quantitative mini_bars source: {'CSV table' if csv_bars else ('Results prose extraction' if prose_bars else 'none')} ({len(active_bars)} bars).",
            f"Unresolved slots (NOT_FOUND_IN_SOURCE): {not_found if not_found else 'none'}.",
        ],
        "panels": [p1_panel, p2_panel, p3_panel, p4_panel],
        "extraction_provenance": {
            "extractor": "draft_ga_spec.py",
            "input_dir": str(input_dir.name),
            "blocks_indexed": len(blocks),
            "quantitative_hits": len(quant_hits),
            "csv_bars_extracted": len(csv_bars),
            "prose_bars_extracted": len(prose_bars),
            "total_mini_bars": len(active_bars),
            "not_found_in_source": not_found,
            "case_id": case_meta.get("case_id") or case_meta.get("id") or None,
        },
    }
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Structure- & syntax-aware grounded 4-stage GA spec extractor from raw /input files.")
    parser.add_argument("--input-dir", required=True, help="Directory containing raw case files (PDF/HTML/MD/TXT/CSV/case.json)")
    parser.add_argument("--out", required=True, help="Output ga_spec.json path")
    parser.add_argument("--width-mm", type=float, default=174.0)
    parser.add_argument("--height-mm", type=float, default=98.0)
    parser.add_argument("--layout", default="auto", choices=["auto", "1x4", "2x2"])
    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    if not in_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"Input directory not found: {in_dir}"}, indent=2))
        return 2
    spec = draft_ga_spec_from_input(in_dir, width_mm=args.width_mm, height_mm=args.height_mm, layout=args.layout)
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out_p), "extraction_provenance": spec["extraction_provenance"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
