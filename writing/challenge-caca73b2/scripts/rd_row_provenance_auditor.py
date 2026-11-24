"""Offline Results & Discussion Row-Condition Provenance Auditor (results-discussion-writing).

Pure Python 3.12 stdlib (zero third-party dependencies).
Solves critical failure modes missed by flat number-set checkers:
  1. Row-Condition Numeric Misattribution: binds each value in source CSV/JSON
     tables to its exact condition/group row label and catches values assigned
     to the wrong row (e.g., High-dose value written in a Low-dose sentence).
  2. Error-Bar Definition Fidelity: checks SD vs SEM vs IQR vs 95% CI alignment
     between source table columns/captions and manuscript prose.
  3. Null / Non-Significant Row Retention & Epistemic Calibration: identifies
     every row with p >= 0.05 / FDR >= 0.05, flags silent omission (cherry-picking),
     and blocks promoting non-significant or observational trends into causal or
     significant claims.
  4. Strict Results-vs-Discussion Layering: detects mechanistic speculation inside
     Results, verbatim numeric dumping inside Discussion, missing literature
     comparison / methodological-difference explanation, and missing concrete
     limitations (method/sample/measurement/statistical).
  5. Structured Output Contract Sealing: emits result.json preserving case_id,
     deliverable, preserved_facts, evidence_map, and change_log.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import writing_io_and_invariants as wio  # noqa: E402

RE_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
RE_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")

CONDITION_COL_HINTS = ("group", "condition", "arm", "cohort", "treatment", "dose", "subgroup", "model", "method", "sample", "condition_id", "组别", "条件")
PVAL_COL_HINTS = ("p_value", "pval", "p.value", "p_adj", "padj", "fdr", "q_value", "qval")
ERROR_TYPE_HINTS = {
    "SD": re.compile(r"(?:_sd\b|\bsd\b|std_?dev|标准差)", re.I),
    "SEM": re.compile(r"(?:_sem\b|\bsem\b|std_?err|标准误)", re.I),
    "IQR": re.compile(r"(?:_iqr\b|\biqr\b|四分位距)", re.I),
    "CI95": re.compile(r"(?:95%?\s*ci|ci_?95|ci_?low|ci_?high|置信区间)", re.I),
}

RESULTS_INTERP_PATTERNS = [
    re.compile(r"\b(?:this\s+indicates?\s+a\s+causal\s+mechanism|likely\s+mediated\s+by|can\s+be\s+explained\s+by\s+previous\s+studies|consistent\s+with\s+prior\s+reports\s+showing\s+mechanism|proves?\s+that)\b", re.I),
    re.compile(r"(?:这(?:可能)?由.*?机制介导|说明其深层机制|证明了.*?因果|由此解释了前人研究)"),
]

SIGNIFICANT_CLAIM_PATTERNS = [
    re.compile(r"\b(?:statistically\s+significant(?:ly)?|significantly\s+(?:increased|decreased|higher|lower|improved|reduced)|proves?|causes?|demonstrates?\s+a\s+causal)\b", re.I),
    re.compile(r"(?:显著(?:升高|降低|增加|减少|改善|高于|低于)|具有统计学意义|证明了|直接导致)"),
]

NEGATED_SIG_PATTERNS = [
    re.compile(r"\b(?:not\s+statistically\s+significant|non-significant|did\s+not\s+reach\s+statistical\s+significance|no\s+significant|failed\s+to\s+reach\s+significance|trend(?:ed|ing)?\s+toward)\b", re.I),
    re.compile(r"(?:未达(?:到)?统计学显著|无显著(?:差异|变化)|不具有统计学意义|仅呈.*?趋势)"),
]

LIMITATION_CONCRETE_SIGNALS = [
    re.compile(r"\b(?:limitation|sample\s+size|underpowered|observational|cross-sectional|confound|measurement\s+error|selection\s+bias|generalizability|external\s+validation|assay\s+sensitivity|missing\s+data|multiple\s+testing)\b", re.I),
    re.compile(r"(?:局限|样本量|统计效力|观察性|横断面|混杂因素|测量误差|选择偏倚|外推性|外部验证|多重比较)"),
]

LIT_COMPARISON_SIGNALS = [
    re.compile(r"\b(?:consistent\s+with|in\s+contrast\s+to|unlike|differ(?:s|ed|ing)?\s+from|compared\s+with\s+prior|previous\s+stud(?:y|ies)|whereas\s+\w+\s+et\s+al\.|methodological\s+difference)\b", re.I),
    re.compile(r"(?:与既往研究|相比之下|不同于|方法(?:学)?差异|一致|存在分歧)"),
]


def _safe_float(s: str) -> float | None:
    try:
        cleaned = str(s).strip().replace(",", "").lstrip("<>≤≥= ")
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _norm_num(s: str) -> str:
    return wio._normalize_num_token(str(s).strip())


def load_structured_data(input_dir: Path) -> dict[str, Any]:
    """Extract condition-row tuples, error bar conventions, null/nonsig rows, and all raw numbers."""
    row_records: list[dict[str, Any]] = []
    all_numbers: dict[str, list[str]] = defaultdict(list)
    error_types_declared: set[str] = set()
    condition_to_values: dict[str, set[str]] = defaultdict(set)
    value_to_conditions: dict[str, set[str]] = defaultdict(set)
    nonsig_rows: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_dir).as_posix()
        suf = path.suffix.lower()
        doc = wio.read_document_text(path)
        text = doc.get("text", "")
        source_files.append({"relpath": rel, "format": doc.get("format", suf.lstrip(".")), "ok": doc.get("ok", False)})
        if not doc.get("ok"):
            continue

        # Detect declared error-bar types from text/captions/headers
        for etype, pat in ERROR_TYPE_HINTS.items():
            if pat.search(text):
                error_types_declared.add(etype)

        if suf == ".csv":
            rows = list(csv.DictReader(text.splitlines()))
            if not rows:
                continue
            cols = list(rows[0].keys())
            cond_col = None
            for c in cols:
                if any(h in c.lower() for h in CONDITION_COL_HINTS):
                    cond_col = c
                    break
            if cond_col is None:
                cond_col = cols[0]

            for r_idx, row in enumerate(rows, start=2):
                cond_val = str(row.get(cond_col) or "").strip()
                if not cond_val:
                    continue
                row_nums: dict[str, str] = {}
                row_pvals: list[tuple[str, float]] = []
                for c, v in row.items():
                    vs = str(v or "").strip()
                    if not vs:
                        continue
                    for etype, pat in ERROR_TYPE_HINTS.items():
                        if pat.search(c):
                            error_types_declared.add(etype)
                    fv = _safe_float(vs)
                    if fv is not None:
                        nn = _norm_num(vs)
                        row_nums[c] = nn
                        all_numbers[nn].append(f"{rel}:row{r_idx}:{cond_val}:{c}")
                        if c != cond_col and not any(ph in c.lower() for ph in ("id", "index", "year")):
                            condition_to_values[cond_val].add(nn)
                            value_to_conditions[nn].add(cond_val)
                        if any(ph in c.lower() for ph in PVAL_COL_HINTS):
                            row_pvals.append((c, fv))
                is_nonsig = any(pv >= 0.05 for _, pv in row_pvals)
                rec = {
                    "source": f"{rel}:row{r_idx}",
                    "condition": cond_val,
                    "metrics": row_nums,
                    "pvalues": [{"col": c, "val": pv} for c, pv in row_pvals],
                    "is_nonsig": is_nonsig,
                }
                row_records.append(rec)
                if is_nonsig:
                    nonsig_rows.append(rec)
        else:
            for lineno, line in enumerate(text.splitlines(), start=1):
                inv = wio.extract_invariants(line)
                for n in inv["bare_numbers"]:
                    loc = f"{rel}:L{lineno}"
                    if len(all_numbers[n]) < 12 and loc not in all_numbers[n]:
                        all_numbers[n].append(loc)

    return {
        "source_files": source_files,
        "row_records": row_records,
        "condition_to_values": {k: sorted(v) for k, v in condition_to_values.items()},
        "value_to_conditions": {k: sorted(v) for k, v in value_to_conditions.items()},
        "nonsig_rows": nonsig_rows,
        "error_types_declared": sorted(error_types_declared),
        "all_numbers": dict(all_numbers),
    }


def _split_rd_sections(md_text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]], list[tuple[int, str]]]:
    results_lines: list[tuple[int, str]] = []
    discussion_lines: list[tuple[int, str]] = []
    other_lines: list[tuple[int, str]] = []
    mode = "other"
    for lineno, line in enumerate(md_text.splitlines(), start=1):
        hm = RE_HEADING.match(line)
        if hm:
            title = hm.group(2).strip().lower()
            if ("results" in title or "结果" in title) and not ("discussion" in title or "讨论" in title):
                mode = "results"
            elif "discussion" in title or "讨论" in title or "limitation" in title or "局限" in title:
                mode = "discussion"
            elif "conclusion" in title or "结论" in title:
                mode = "discussion"
            else:
                mode = "other"
            continue
        if mode == "results":
            results_lines.append((lineno, line))
        elif mode == "discussion":
            discussion_lines.append((lineno, line))
        else:
            other_lines.append((lineno, line))
    return results_lines, discussion_lines, other_lines


def audit_results_and_discussion(draft_path: Path, input_dir: Path) -> dict[str, Any]:
    """Run deep row-condition, null-retention, error-bar, layering, and epistemic audit."""
    data = load_structured_data(input_dir)
    doc = wio.read_document_text(draft_path)
    if not doc.get("ok"):
        return {"ok": False, "error": f"cannot read draft: {draft_path}"}

    full_text = doc["text"]
    res_lines, disc_lines, _ = _split_rd_sections(full_text)
    res_text = "\n".join(t for _, t in res_lines)
    disc_text = "\n".join(t for _, t in disc_lines)

    issues: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []

    if not res_text.strip():
        issues.append({"code": "MISSING_RESULTS_SECTION", "severity": "high", "detail": "No dedicated Results section heading found."})
    if not disc_text.strip():
        issues.append({"code": "MISSING_DISCUSSION_SECTION", "severity": "high", "detail": "No dedicated Discussion section heading found."})

    # 1. Numeric grounding & row-condition binding audit
    all_nums = data["all_numbers"]
    cond_to_vals = {k: set(v) for k, v in data["condition_to_values"].items()}
    val_to_conds = {k: set(v) for k, v in data["value_to_conditions"].items()}
    conditions = sorted(cond_to_vals.keys(), key=len, reverse=True)
    nonsig_cond_names = {r["condition"] for r in data["nonsig_rows"]}

    grounded_count = 0
    ungrounded_numbers: list[dict[str, Any]] = []
    row_mismatches: list[dict[str, Any]] = []
    nonsig_overclaims: list[dict[str, Any]] = []

    for section_name, lines in (("Results", res_lines), ("Discussion", disc_lines)):
        for lineno, line in lines:
            if not line.strip():
                continue
            for sent in RE_SENT_SPLIT.split(line):
                sent = sent.strip()
                if not sent:
                    continue
                inv = wio.extract_invariants(sent)
                # Detect which conditions are named in this sentence
                matched_conds = [c for c in conditions if re.search(rf"(?<!\w){re.escape(c)}(?!\w)", sent, re.I)]

                for num in inv["bare_numbers"]:
                    if num in {"0", "1", "2", "3", "4", "5", "95"} and not any(u["value"] == num for u in inv["numbers_with_units"]):
                        continue
                    if num in all_nums:
                        grounded_count += 1
                        evidence_map.append({
                            "section": section_name,
                            "line": lineno,
                            "value": num,
                            "matched_conditions": matched_conds,
                            "source_anchors": all_nums[num][:3],
                        })
                        # Row-condition misattribution check (only when sentence names exactly 1 specific condition
                        # and the numeric token uniquely belongs to OTHER condition(s) in the table)
                        if len(matched_conds) == 1 and num in val_to_conds:
                            named_c = matched_conds[0]
                            owner_conds = val_to_conds[num]
                            if named_c not in owner_conds:
                                row_mismatches.append({
                                    "code": "ROW_CONDITION_VALUE_MISMATCH",
                                    "severity": "high",
                                    "section": section_name,
                                    "line": lineno,
                                    "named_condition": named_c,
                                    "misattributed_value": num,
                                    "actual_owner_conditions": sorted(owner_conds),
                                    "sentence": sent[:200],
                                })
                    else:
                        ungrounded_numbers.append({
                            "section": section_name,
                            "line": lineno,
                            "value": num,
                            "sentence": sent[:180],
                        })

                # Non-significant row overclaim check
                if len(matched_conds) == 1 and matched_conds[0] in nonsig_cond_names:
                    has_sig_claim = any(p.search(sent) for p in SIGNIFICANT_CLAIM_PATTERNS)
                    has_neg_hedge = any(p.search(sent) for p in NEGATED_SIG_PATTERNS)
                    if has_sig_claim and not has_neg_hedge:
                        nonsig_overclaims.append({
                            "code": "NONSIG_PROMOTED_TO_SIGNIFICANT_OR_CAUSAL",
                            "severity": "high",
                            "section": section_name,
                            "line": lineno,
                            "condition": matched_conds[0],
                            "sentence": sent[:200],
                        })

    issues.extend(row_mismatches)
    issues.extend(nonsig_overclaims)
    for ug in ungrounded_numbers:
        issues.append({"code": "UNGROUNDED_VALUE", "severity": "high", **ug})

    # 2. Check omitted negative / non-significant rows (cherry-picking guard)
    omitted_nonsig: list[dict[str, Any]] = []
    for nr in data["nonsig_rows"]:
        cname = nr["condition"]
        p_vals_str = [_norm_num(str(p["val"])) for p in nr["pvalues"]]
        mentioned_cond = bool(re.search(rf"(?<!\w){re.escape(cname)}(?!\w)", full_text, re.I))
        mentioned_p = any(pv in full_text for pv in p_vals_str)
        if not mentioned_cond and not mentioned_p:
            omitted_nonsig.append({"condition": cname, "source": nr["source"], "pvalues": nr["pvalues"]})
            issues.append({
                "code": "OMITTED_NULL_OR_NONSIG_ROW",
                "severity": "high",
                "condition": cname,
                "source": nr["source"],
                "detail": f"Non-significant row '{cname}' ({nr['pvalues']}) from {nr['source']} is omitted from Results/Discussion.",
            })

    # 3. Error-bar definition conflict check (SD vs SEM vs IQR vs CI95)
    draft_err_types = {k for k, pat in ERROR_TYPE_HINTS.items() if pat.search(full_text)}
    src_err_types = set(data["error_types_declared"])
    if src_err_types and draft_err_types:
        conflicting_err = sorted(draft_err_types - src_err_types)
        if conflicting_err:
            issues.append({
                "code": "ERROR_BAR_DEFINITION_CONFLICT",
                "severity": "high",
                "source_declared": sorted(src_err_types),
                "draft_used": sorted(draft_err_types),
                "detail": f"Draft uses error metric(s) {conflicting_err} not declared in source tables/captions ({sorted(src_err_types)}).",
            })

    # 4. Results-vs-Discussion layering & synthesis quality
    for lineno, line in res_lines:
        for pat in RESULTS_INTERP_PATTERNS:
            m = pat.search(line)
            if m:
                issues.append({
                    "code": "MECHANISM_INTERPRETATION_IN_RESULTS",
                    "severity": "high",
                    "line": lineno,
                    "matched": m.group(0),
                    "detail": "Results must report empirical observations only; move mechanistic or literature interpretation to Discussion.",
                })

    res_nums = {n for n in wio.extract_invariants(res_text)["bare_numbers"] if n not in {"0", "1", "2", "3", "95"}}
    disc_nums = {n for n in wio.extract_invariants(disc_text)["bare_numbers"] if n not in {"0", "1", "2", "3", "95"}}
    if len(res_nums) >= 4 and len(res_nums & disc_nums) / len(res_nums) >= 0.75:
        issues.append({
            "code": "DISCUSSION_VERBATIM_NUMERIC_REPETITION",
            "severity": "medium",
            "overlap_ratio": round(len(res_nums & disc_nums) / len(res_nums), 3),
            "detail": "Discussion repeats >=75% of Results numeric tokens; focus on interpretation, literature comparison, and concrete limitations.",
        })

    if disc_text.strip():
        if not any(p.search(disc_text) for p in LIMITATION_CONCRETE_SIGNALS):
            issues.append({
                "code": "MISSING_CONCRETE_LIMITATIONS_IN_DISCUSSION",
                "severity": "high",
                "detail": "Discussion lacks concrete methodological, sample-size, measurement, or statistical limitations.",
            })
        if not any(p.search(disc_text) for p in LIT_COMPARISON_SIGNALS):
            issues.append({
                "code": "MISSING_LITERATURE_COMPARISON_IN_DISCUSSION",
                "severity": "medium",
                "detail": "Discussion does not explicitly compare findings (agreement/discrepancy/methodological differences) with prior literature.",
            })

    prose_audit = wio.audit_academic_prose(full_text)
    high_issues = [x for x in issues if x.get("severity") == "high"]

    return {
        "ok": len(high_issues) == 0 and prose_audit["high_count"] == 0,
        "high_issue_count": len(high_issues) + prose_audit["high_count"],
        "medium_issue_count": sum(1 for x in issues if x.get("severity") == "medium") + prose_audit["warning_count"],
        "grounded_numbers_count": grounded_count,
        "ungrounded_numbers_count": len(ungrounded_numbers),
        "row_condition_mismatches_count": len(row_mismatches),
        "omitted_nonsig_rows_count": len(omitted_nonsig),
        "nonsig_overclaims_count": len(nonsig_overclaims),
        "source_error_bar_types": sorted(src_err_types),
        "draft_error_bar_types": sorted(draft_err_types),
        "issues": issues,
        "prose_deai_audit": prose_audit,
        "evidence_map": evidence_map[:120],
        "source_files": data["source_files"],
    }


def build_rd_scaffold(data_dir: Path, out_md: Path) -> dict[str, Any]:
    """Generate a row-bound Results & four-beat Discussion scaffold directly from structured /input data."""
    data = load_structured_data(data_dir)
    err_str = ", ".join(data["error_types_declared"]) if data["error_types_declared"] else "UNSPECIFIED"
    lines = [
        "# Results",
        "",
        f"<!-- Observations only (zero mechanistic speculation). Declared error-bar types in /input: {err_str}. -->",
        "",
    ]
    for rec in data["row_records"]:
        metrics_str = ", ".join(f"{k}={v}" for k, v in rec["metrics"].items())
        tag = "[NON-SIGNIFICANT / MUST RETAIN AS HEDGED TREND]" if rec["is_nonsig"] else "[OBSERVED ROW]"
        lines.append(f"- {tag} Condition `{rec['condition']}` (`{rec['source']}`): {metrics_str}.")
    if not data["row_records"]:
        lines.append("[EVIDENCE_GAP: No structured CSV tables found under /input; transcribe verified text/figure values explicitly]")
    lines.extend([
        "",
        "# Discussion",
        "",
        "## Key Finding Synthesis",
        "[Synthesize how observed outcomes answer the primary research question without copying every Results number verbatim.]",
        "",
        "## Comparison with Prior Literature",
        "[Compare agreement and discrepancies against supplied references, explicitly contrasting study design, population, assay, or statistical methods.]",
        "",
        "## Mechanistic Interpretation (Explicitly Hedged)",
        "[Present candidate mechanisms clearly qualified with 'may reflect' / 'is consistent with'; never upgrade observational or non-significant trends to causality.]",
        "",
        "## Methodological, Sample, Measurement, and Statistical Limitations",
        "[State concrete limitations across design, sample size, measurement precision, and multiple testing.]",
    ])
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "scaffold_path": str(out_md),
        "rows_emitted": len(data["row_records"]),
        "nonsig_rows_flagged": len(data["nonsig_rows"]),
        "error_types_declared": data["error_types_declared"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Results & Discussion Row-Condition Provenance Auditor.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_idx = sub.add_parser("index", help="Inspect source tables, condition rows, error types, and non-significant rows.")
    p_idx.add_argument("--data-dir", required=True)
    p_idx.add_argument("--out-json", required=True)

    p_scf = sub.add_parser("scaffold", help="Generate row-bound Results & four-beat Discussion scaffold from /input.")
    p_scf.add_argument("--data-dir", required=True)
    p_scf.add_argument("--out-md", required=True)

    p_aud = sub.add_parser("audit", help="Audit Results & Discussion draft against source tables and captions.")
    p_aud.add_argument("--data-dir", required=True)
    p_aud.add_argument("--draft", required=True)
    p_aud.add_argument("--out-json", required=True)

    p_seal = sub.add_parser("seal", help="Audit draft and emit output-contract-compliant result.json.")
    p_seal.add_argument("--data-dir", required=True)
    p_seal.add_argument("--draft", required=True)
    p_seal.add_argument("--case-id", required=True)
    p_seal.add_argument("--out-dir", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "index":
        data = load_structured_data(Path(args.data_dir))
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": True,
            "row_records": len(data["row_records"]),
            "nonsig_rows": len(data["nonsig_rows"]),
            "error_types_declared": data["error_types_declared"],
        }, ensure_ascii=False))
        return 0

    if args.cmd == "scaffold":
        res = build_rd_scaffold(Path(args.data_dir), Path(args.out_md))
        print(json.dumps(res, ensure_ascii=False))
        return 0

    if args.cmd == "audit":
        res = audit_results_and_discussion(Path(args.draft), Path(args.data_dir))
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": res.get("ok", False),
            "high_issue_count": res.get("high_issue_count", -1),
            "row_mismatches": res.get("row_condition_mismatches_count", 0),
            "omitted_nonsig": res.get("omitted_nonsig_rows_count", 0),
            "nonsig_overclaims": res.get("nonsig_overclaims_count", 0),
        }, ensure_ascii=False))
        return 0

    if args.cmd == "seal":
        draft_path = Path(args.draft)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        res = audit_results_and_discussion(draft_path, Path(args.data_dir))
        aud_path = out_dir / "rd_audit_report.json"
        aud_path.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ev_path = out_dir / "rd_evidence_map.json"
        ev_path.write_text(json.dumps(res.get("evidence_map", []), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        gaps = [
            {"type": iss["code"], "detail": iss.get("detail") or iss.get("sentence", "")}
            for iss in res.get("issues", [])
            if iss.get("severity") == "high"
        ]
        payload = wio.build_writing_result(
            case_id=args.case_id,
            task_id="results-discussion-writing",
            deliverable={
                "results_and_discussion_markdown": draft_path.name,
                "audit_report": aud_path.name,
                "evidence_map_file": ev_path.name,
            },
            preserved_facts={
                "grounded_numbers_count": res["grounded_numbers_count"],
                "ungrounded_numbers_count": res["ungrounded_numbers_count"],
                "row_condition_mismatches_count": res["row_condition_mismatches_count"],
                "omitted_nonsig_rows_count": res["omitted_nonsig_rows_count"],
                "source_error_bar_types": res["source_error_bar_types"],
                "draft_error_bar_types": res["draft_error_bar_types"],
            },
            evidence_map=res.get("evidence_map", []),
            change_log=[
                {"action": "row_condition_and_layer_audit", "high_issues": res["high_issue_count"], "medium_issues": res["medium_issue_count"], "issues": res["issues"]}
            ],
            artifacts=[draft_path.name, aud_path.name, ev_path.name],
            sources=res.get("source_files", []),
            gaps=gaps,
            limitations=[
                "Row-condition cross-check operates on structured CSV/JSON tables and text inputs supplied under /input; raster-only charts without accompanying numeric tables require explicit manual reading notes.",
            ],
            verification={"audit_ok": res["ok"], "high_issue_count": res["high_issue_count"]},
        )
        res_path = out_dir / "result.json"
        res_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "audit_ok": res["ok"], "result_json": str(res_path)}, ensure_ascii=False))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
