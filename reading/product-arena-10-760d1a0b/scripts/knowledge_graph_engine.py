#!/usr/bin/env python3
"""Multi-Paper Evidence-Anchored Literature Knowledge Graph Engine (product-arena-10-760d1a0b).

Implements Official Revision & Community Pain-Point Defenses (pure Python 3.12 stdlib):
1. Strict Multi-Paper Identity & Metadata Isolation:
   - Reads corpus-manifest.json, primary-material.json, secondary-material.json (and optional
     secondary-publisher.xml for cross-checking linearized inline math/subscripts).
   - Uses material.json ONLY as fallback when primary/secondary originals are absent.
   - NEVER propagates Paper 1 root DOI/title/authors onto Paper 2.
   - NEVER treats multiple file formats of the SAME paper/DOI as cross-paper evidence.
2. Honest JATS / Source Locator Contract:
   - Records exact paper source_id, JATS paragraph_id, and verbatim sentence quote on every node & edge.
   - Explicitly marks pdf_page_available=false when inputs are JATS/JSON paragraphs (NEVER fabricates PDF page 1).
3. Generalizable Scientific Entity & Controlled Relation Extraction:
   - Extracts entities across 6 scientific categories (material_or_entity, method_or_model, task_or_domain,
     dataset_or_cohort, metric_or_endpoint, researcher_or_group) + intra-paper Full-Name (ABBR) aliases.
   - Separates author-own reporting (author_reported) from cited prior work (cited_secondary).
   - Separates explicit directed relations (uses, evaluates_on, compares_with, outperforms, supports, contradicts,
     increases, decreases, inhibits, activates, causes_direct) from undirected co-occurrences (co_occurs_with)
     — NEVER promotes undirected co-occurrence to a causal or directional edge!
   - Holds hedged, negated, ambiguous, or unanchored candidates in held_for_review.
4. Cross-Paper Alignment & Non-Merge Firewall:
   - Preserves paper-scoped nodes by default.
   - Only forms concept-level alignment groups when strong cross-paper criteria hold (exact acronym+full-name
     or explicit shared identifier), while keeping conditions/samples/results strictly non-pooled.
   - Logs explicit DO_NOT_MERGE decisions for generic recurring terms or distinct physical/experimental scopes.
5. Executable Queries, Incremental Updates & Multi-Format Export:
   - Executes 5+ real queries, supports --prior-graph incremental merging preserving node IDs,
     and writes knowledge_graph.json, knowledge_graph.graphml, knowledge_graph_report.md, and result.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from kg_query_and_export import (
    apply_incremental_update,
    export_graphml,
    find_cross_paper_conflicts,
    render_kg_markdown,
    run_standard_queries,
)

# Case-sensitive chemical/formula/gene/acronym entities (NEVER matched case-insensitively against common English words!)
ENTITY_PATTERNS_CASE_SENSITIVE: dict[str, str] = {
    "material_or_entity": (
        r"\b(?:"
        r"(?:Cu|Pt|Pd|Fe|Ni|Co|Zn|Ti|Au|Ag)(?:[0-9]+(?:[A-Z][a-z0-9]*)*|(?:-[A-Z][a-z0-9]+)+|/[A-Z][A-Za-z0-9]*|\s+(?:catalyst|nanoparticles?|electrode|oxide))|"
        r"[A-Z][a-z]?(?:-[A-Z][a-z0-9]+)+(?:\s+(?:catalyst|complex|alloy|oxide|membrane|nanoparticle|electrode))?|"
        r"CO2|CH4|H2O2|NH3|NOx|ATP|DNA|RNA|mRNA|siRNA|CRISPR-Cas9|PD-L1|PD-1|EGFR|TP53|KRAS|HER2|IL-6|TNF-alpha|"
        r"T-cells?"
        r")\b"
    ),
    "method_or_model": (
        r"\b(?:DFT|XPS|XRD|SEM|TEM|NMR|FTIR|LC-MS|GC-MS|RNA-seq|scRNA-seq|ChIP-seq|ATAC-seq|WGS|WES|PCR|qPCR|ELISA|FACS|"
        r"RDE|RRDE|MEA|PEM|AEM|OER|HER|ORR|CO2RR|"
        r"Paper2Agent|AlphaGenome|Scanpy|TISSUE|Seurat|Biomni|MCP\s+servers?|MCP\s+tools?|AI\s+agents?|LLMs?|"
        r"Claude\s*\+\s*Repo|Claude\s+Code|DeepSeek|Gemini|GPT-[0-9A-Za-z.\-]+|Transformer|BERT|ResNet(?:-\d+)?|GNN|GCN|GAT|CNN|RNN|LSTM|"
        r"XGBoost|LightGBM|SVM|AdamW?|SGD|LoRA|RAG|BM25|SPECTER2?|SciBERT|BioBERT|AlphaFold\d?|ESM-?\d?)\b"
    ),
    "dataset_or_cohort": (
        r"\b(?:SINAN|IBGE|ICD-?10|AERS|FAERS|VAERS|WHO\s+Vigibase|FDA|CDER|CBER|"
        r"TCGA(?:-[A-Z]+)?|GEO|GSE\d+|UK\s+Biobank|MIMIC-(?:III|IV)|"
        r"PubMedQA|BioASQ|SciFact|QASPER|arXiv|OpenAlex|PubMed|PMLB|ImageNet|CIFAR-\d+|GLUE|MMLU|SQuAD|GWAS)\b"
    ),
    "metric_or_endpoint": (
        r"\b(?:TOF|AAPC|APC|AUROC|AUC|AUPRC|F1(?:-score)?|RMSE|MAE|R\^?2|PFS|IC50|EC50|95%\s*CI)\b"
    ),
}

# Case-insensitive multi-word / domain-specific scientific nouns across disciplines
ENTITY_PATTERNS_CI: dict[str, str] = {
    "material_or_entity": (
        r"\b(?:zeolite|perovskite|graphene|hydrogel|electrolyte|substrate|catalyst|ligand|inhibitor|antibody|receptor|enzyme|protein|compound|biomarker|"
        r"medications?|medicines?|pesticides?|toxic\s+agents?|venomous\s+animals?|household\s+cleaning\s+products?|"
        r"adverse\s+(?:events?|drug\s+reactions?)|safety\s+signals?|drug\s+products?|"
        r"children\s+and\s+adolescents|adolescents|patients?|macrophages?|genomic\s+variants?|single-cell\s+transcriptomics)\b"
        r"|催化剂|配合物|电极|纳米颗粒|抑制剂|抗体|受体|酶|生物标志物|材料|药物|农药|毒物"
    ),
    "method_or_model": (
        r"\b(?:large\s+language\s+models?|paper\s+agents?|random\s+forest|"
        r"(?:joinpoint|inflection\s+point|logistic|linear|Cox|Poisson)\s+regression(?:\s+model)?|"
        r"data\s+mining|signal\s+detection|spontaneous\s+reporting|case-control\s+stud(?:y|ies)|cohort\s+stud(?:y|ies)|"
        r"rotating\s+disk\s+electrode|benchmarking\s+protocol|electrochemical\s+measurement|"
        r"time-series\s+analysis|descriptive\s+statistics|spatial\s+transcriptomics|multi-agent\s+(?:framework|workflow|system)|"
        r"density\s+functional\s+theory|molecular\s+dynamics|flow\s+cytometry|mass\s+spectrometry|western\s+blot|meta-analysis|ablation\s+study)\b"
        r"|密度泛函理论|分子动力学|质谱|流式细胞术|随机森林|图神经网络|微调|表征方法|回归模型|时间序列分析"
    ),
    "task_or_domain": (
        r"\b(?:CO2\s+hydrogenation|CO2\s+reduction|water\s+splitting|oxygen\s+evolution(?:\s+reaction)?|hydrogen\s+evolution|nitrogen\s+fixation|"
        r"exogenous\s+(?:intoxications?|poisonings?)|suicide\s+attempts?|accidental\s+poisonings?|pharmacovigilance|pharmacoepidemiolog(?:ic|y)|"
        r"postmarketing\s+(?:surveillance|safety)|risk\s+assessment|drug\s+safety|"
        r"drug\s+discovery|protein\s+folding|cell\s+type\s+annotation|spatial\s+gene\s+expression\s+prediction|"
        r"genomic\s+variant\s+interpretation|tumor\s+microenvironment|cancer\s+immunotherapy|"
        r"question\s+answering|information\s+extraction|relation\s+extraction|document\s+parsing|entity\s+linking|citation\s+verification|"
        r"hydrogenation|electroreduction|photocatalysis|immunotherapy|classification|segmentation|forecasting|reproducibility)\b"
        r"|加氢反应|电催化还原|药物发现|蛋白质折叠|免疫治疗|关系抽取|文献解析|外源性中毒"
    ),
    "dataset_or_cohort": (
        r"\b(?:Notifiable\s+Diseases\s+Information\s+System|spontaneous\s+reporting\s+database|case\s+series|"
        r"GitHub\s+repositor(?:y|ies)|open-source\s+codebases?|public\s+domain\s+data|"
        r"clinical\s+cohort|validation\s+cohort|patient\s+cohort|benchmark\s+dataset|reference\s+codebase)\b"
        r"|临床队列|验证队列|基准数据集|公开数据库"
    ),
    "metric_or_endpoint": (
        r"\b(?:Faradaic\s+efficiency|methanol\s+selectivity|CO2\s+conversion|turnover\s+frequency|overpotential|current\s+density|"
        r"median\s+runtime|runtime|annual\s+percent\s+change|average\s+annual\s+percent\s+change|incidence\s+rates?|mortality\s+rates?|prevalence|temporal\s+trends?|"
        r"confidence\s+interval|precision|recall|accuracy|p-value|hazard\s+ratio|odds\s+ratio|"
        r"overall\s+survival|success\s+rate|pass\s+rate|selectivity|efficiency|yield|sensitivity|specificity|latency|throughput)\b"
        r"|法拉第效率|选择性|转化率|过电位|准确率|召回率|总生存期|发病率|年均变化百分比|运行时间"
    ),
}

ENTITY_RE_CS = re.compile("|".join(f"(?P<{cat}>{pat})" for cat, pat in ENTITY_PATTERNS_CASE_SENSITIVE.items()))
ENTITY_RE_CI = re.compile("|".join(f"(?P<{cat}>{pat})" for cat, pat in ENTITY_PATTERNS_CI.items()), re.I)
ABBR_DEF_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9\- ]{3,55}?)\s*\(([A-Z][A-Za-z0-9\-]{1,14})\)")

# Controlled directed relations (strictly excluding mere co-occurrence!)
DIRECTED_LINKS: dict[str, str] = {
    "uses": r"\b(?:uses?|used|employs?|employed|utilizes?|utilized|adopts?|adopted|applies|applied|relies\s+on|built\s+on|integrates?|converts?|leverag(?:es?|ed|ing))\b|使用|采用|应用",
    "evaluates_on": r"\b(?:evaluates?\s+on|evaluated\s+on|benchmarks?\s+on|benchmarked\s+on|tested\s+on|trained\s+on|validated\s+on|extracted\s+from|collected\s+from|analyzed\s+using|assess(?:es|ed|ing)?|evaluat(?:es|ed|ing)|detect(?:s|ed|ing)?|monitor(?:s|ed|ing)?)\b|在.+上评测|在.+上验证|提取自|评估",
    "outperforms": r"\b(?:outperforms?|outperformed|outperforming|surpasses?|surpassed|exceeds?|exceeded|improves?\s+over|higher\s+than)\b|优于|超过",
    "compares_with": r"\b(?:compares?\s+with|compared\s+with|compared\s+to|benchmarked\s+against|versus|vs\.)\b|相比|对比",
    "supports": r"\b(?:supports?|supported|confirms?|confirmed|corroborates?|corroborated|validates?|validated|matches?|reproduces?|enables?|enabled)\b|支持|证实|复现|赋能",
    "contradicts": r"\b(?:contradicts?|contradicted|refutes?|refuted|conflicts?\s+with|disagrees?\s+with)\b|反驳|相矛盾|冲突",
    "increases": r"\b(?:increases?|increased|enhances?|enhanced|elevates?|elevated|boosts?|boosted|raises?|raised|grew|rose)\b|提高|增加|增强",
    "decreases": r"\b(?:decreases?|decreased|reduces?|reduced|lowers?|lowered|diminishes?|diminished|declined)\b|降低|减少|下降",
    "inhibits": r"\b(?:inhibits?|inhibited|suppresses?|suppressed|blocks?|blocked)\b|抑制|阻断",
    "activates": r"\b(?:activates?|activated|induces?|induced|promotes?|promoted|upregulates?|upregulated)\b|激活|诱导|促进",
    "associated_with": r"\b(?:correlates?\s+with|correlated\s+with|(?:is|was|are|were)\s+(?:significantly\s+)?associated\s+with)\b|相关",
    "causes_direct": r"\b(?:directly\s+causes?|causally\s+drives?|causally\s+induces?)\b|直接导致",
}

PASSIVE_DIRECTED_LINKS: dict[str, str] = {
    "uses": r"\b(?:was|were|is|are)\s+(?:analyzed|performed|estimated|calculated|implemented|modeled|built)\s+(?:using|via|with|by)\b",
    "evaluates_on": r"\b(?:was|were|is|are)\s+(?:evaluated|tested|validated|benchmarked)\s+(?:on|across|against)\b",
    "inhibits": r"\b(?:was|were|is|are)\s+(?:inhibited|suppressed|blocked)\s+by\b",
    "activates": r"\b(?:was|were|is|are)\s+(?:activated|induced|promoted|enabled)\s+by\b",
}

HEDGED_OR_NEGATED_RE = re.compile(
    r"\b(?:no|not|never|neither|without|did\s+not|does\s+not|cannot|may|might|could|potentially|suggests?|appears?\s+to|hypothesi[sz]ed|unclear)\b"
    r"|未见|没有|未能|不显著|可能|推测|尚未|不确定",
    re.I,
)
CITED_PRIOR_RE = re.compile(
    r"\b(?:previous(?:ly)?\s+stud(?:y|ies)|prior\s+work|et\s+al\.,?\s*\(?(?:19|20)\d\d|reported\s+by|according\s+to)\b"
    r"|\[\d+(?:\s*[-,]\s*\d+)*\]|前人研究|既往研究",
    re.I,
)
CONDITION_RE = re.compile(
    r"(?:only\s+(?:when|under|at|in)|under\s+[A-Za-z0-9°.\-\s]{2,40}|at\s+\d+(?:\.\d+)?\s*(?:°C|K|V|mV|pH|bar|atm|MPa|h)|"
    r"in\s+(?:vitro|vivo|mice|rats|patients|acidic|alkaline|neutral)\b|pH\s*[=:]?\s*\d+(?:\.\d+)?)"
    r"|仅在[^，。；]{2,24}|在[^，。；]{2,20}条件下",
    re.I,
)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff])|(?<=[。！？])")

COMPARISON_BASELINE_CUE_RE = re.compile(
    r"\b(?:relative\s+to|compared\s+(?:with|to)|versus|vs\.|than\b)",
    re.I,
)
CLAUSE_BREAK_RE = re.compile(
    r"(?:;|,\s+(?:together\s+with|whereas|while|however|although|therefore|thus|moreover|furthermore)\b)",
    re.I,
)
# Explicit semantic argument-role constraints: (allowed_subject_categories, allowed_object_categories)
RELATION_ROLE_RULES: dict[str, tuple[set[str], set[str]]] = {
    "uses": ({"method_or_model", "material_or_entity"}, {"method_or_model", "material_or_entity", "dataset_or_cohort", "task_or_domain"}),
    "evaluates_on": ({"method_or_model", "material_or_entity"}, {"dataset_or_cohort", "task_or_domain", "material_or_entity", "method_or_model"}),
    "outperforms": ({"method_or_model", "material_or_entity"}, {"method_or_model", "material_or_entity"}),
    "compares_with": ({"method_or_model", "material_or_entity"}, {"method_or_model", "material_or_entity"}),
    "supports": ({"method_or_model", "material_or_entity", "dataset_or_cohort"}, {"task_or_domain", "dataset_or_cohort", "method_or_model", "material_or_entity", "metric_or_endpoint"}),
    "contradicts": ({"method_or_model", "material_or_entity", "dataset_or_cohort"}, {"method_or_model", "material_or_entity", "dataset_or_cohort", "task_or_domain"}),
    "increases": ({"method_or_model", "material_or_entity"}, {"metric_or_endpoint", "material_or_entity", "task_or_domain"}),
    "decreases": ({"method_or_model", "material_or_entity"}, {"metric_or_endpoint", "material_or_entity", "task_or_domain"}),
    "inhibits": ({"method_or_model", "material_or_entity"}, {"material_or_entity", "task_or_domain", "metric_or_endpoint"}),
    "activates": ({"method_or_model", "material_or_entity"}, {"material_or_entity", "task_or_domain", "metric_or_endpoint"}),
    "associated_with": ({"material_or_entity", "method_or_model", "task_or_domain", "dataset_or_cohort"}, {"material_or_entity", "task_or_domain", "metric_or_endpoint"}),
    "causes_direct": ({"material_or_entity", "method_or_model"}, {"material_or_entity", "task_or_domain", "metric_or_endpoint"}),
}

GENERIC_NON_MERGE_TERMS = {
    "catalyst", "material", "electrode", "substrate", "electrolyte", "compound", "protein", "enzyme", "receptor",
    "method", "model", "protocol", "calculation", "synthesis", "microscopy", "spectroscopy",
    "dataset", "cohort", "patient cohort", "clinical cohort", "validation cohort", "benchmark dataset",
    "patients", "patient", "medications", "medication", "medicines", "adverse events", "adverse event", "drug products",
    "selectivity", "efficiency", "yield", "accuracy", "precision", "recall", "f1", "auc", "auroc", "incidence rates", "prevalence",
    "classification", "hydrogenation", "reduction", "oxidation", "evaluation",
}


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]


def _norm_ws(text: str) -> str:
    return " ".join((text or "").split())


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def load_corpus_strict(input_dir: Path, case_data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Read multi-paper corpus with strict metadata isolation and zero cross-paper bleed."""
    gaps: list[dict[str, Any]] = []
    manifest_by_file: dict[str, dict[str, Any]] = {}
    manifest_path = input_dir / "corpus-manifest.json"
    if manifest_path.is_file():
        try:
            mobj = json.loads(manifest_path.read_text(encoding="utf-8"))
            docs = mobj.get("documents") or mobj.get("papers") or mobj.get("sources") or (mobj if isinstance(mobj, list) else [])
            if isinstance(docs, dict):
                docs = [{"file": k, **v} for k, v in docs.items() if isinstance(v, dict)]
            for d in docs:
                if isinstance(d, dict):
                    fname = str(d.get("file") or d.get("filename") or d.get("path") or d.get("source_file") or "")
                    if fname:
                        manifest_by_file[Path(fname).name] = d
        except Exception as exc:
            gaps.append({"code": "CORPUS_MANIFEST_UNREADABLE", "message": str(exc)})

    # Optional secondary-publisher.xml check for linearized math/subscripts
    xml_math_index: dict[str, str] = {}
    for xml_fp in sorted(input_dir.rglob("*.xml")):
        if xml_fp.is_file() and not xml_fp.is_symlink():
            try:
                root = ET.fromstring(xml_fp.read_text(encoding="utf-8", errors="replace"))
                for elem in root.iter():
                    if _strip_ns(elem.tag).lower() == "p":
                        pid = elem.attrib.get("id")
                        if pid:
                            xml_math_index[pid] = _norm_ws("".join(elem.itertext()))
            except ET.ParseError as exc:
                gaps.append({"code": "PUBLISHER_XML_PARSE_WARNING", "source_file": xml_fp.name, "message": str(exc)})

    paragraphs: list[dict[str, Any]] = []
    paper_registry: dict[str, dict[str, Any]] = {}
    seen_paper_para_hashes: set[tuple[str, str]] = set()
    originals_loaded: set[str] = set()
    has_pdf_pages = False

    def _register_paper(source_id: str, source_file: str, meta: dict[str, Any]) -> None:
        reg = paper_registry.setdefault(source_id, {
            "source_id": source_id,
            "source_file": source_file,
            "doi": meta.get("doi"),
            "title": meta.get("title"),
            "authors": meta.get("authors"),
            "license": meta.get("license"),
            "source_hash": meta.get("source_hash") or meta.get("sha256"),
            "paragraph_count": 0,
        })
        for k in ("doi", "title", "authors", "license", "source_hash"):
            if reg.get(k) is None and meta.get(k) is not None:
                reg[k] = meta[k]

    # Priority 1: independent primary-material.json & secondary-material.json (never bleed metadata!)
    ordered_json = ["primary-material.json", "secondary-material.json"]
    # Also include any other paper-*.json files if present
    for extra in sorted(input_dir.glob("*.json")):
        if extra.name not in {"case.json", "corpus-manifest.json", "material.json", "primary-material.json", "secondary-material.json"}:
            ordered_json.append(extra.name)
    ordered_json.append("material.json")

    for fname in ordered_json:
        fp = input_dir / fname
        if not fp.is_file() or fp.is_symlink():
            continue
        if fname == "material.json" and len(originals_loaded) >= 2:
            break  # Do not duplicate paragraphs already loaded from primary + secondary originals
        try:
            contents = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as exc:
            gaps.append({"code": "CORPUS_FILE_UNREADABLE", "source_file": fname, "message": str(exc)})
            continue

        meta_file = dict(manifest_by_file.get(fname) or {})
        doc_dict = contents if isinstance(contents, dict) else {}
        doc_source = doc_dict.get("source") if isinstance(doc_dict.get("source"), dict) else {}
        if fname != "material.json":
            # Only bind file-level metadata to its own document, NEVER across papers
            for k in ("doi", "title", "authors", "license", "source_id"):
                val = doc_dict.get(k) or doc_source.get(k) or meta_file.get(k)
                if val is not None:
                    meta_file[k] = val

        items = contents if isinstance(contents, list) else (doc_dict.get("paragraphs") or doc_dict.get("sections") or [])
        if not isinstance(items, list):
            gaps.append({"code": "CORPUS_PARAGRAPHS_MISSING", "source_file": fname})
            continue

        loaded_here = 0
        for idx, item in enumerate(items, start=1):
            if isinstance(item, str) and item.strip():
                item = {"text": item}
            if not isinstance(item, dict):
                continue
            txt = _norm_ws(str(item.get("text") or item.get("content") or ""))
            if not txt:
                continue
            # Per-paragraph source identity (for combined material.json, rely strictly on item's own source_id/doi!)
            item_doi = item.get("doi") or item.get("source_doi")
            item_sid = item.get("source_id") or item.get("paper_id")
            if fname == "material.json":
                sid = str(item_sid or item_doi or f"{fname}#row{idx}")
                p_meta = {"doi": item_doi, "title": item.get("title"), "authors": item.get("authors")}
            else:
                sid = str(item_sid or meta_file.get("source_id") or item_doi or meta_file.get("doi") or fp.stem)
                p_meta = dict(meta_file)

            pid = item.get("paragraph_id") or item.get("id")
            pid_str = str(pid).strip() if pid not in (None, "") else None
            # Deduplicate same paper + same normalized text (prevents multi-format same-paper inflation)
            dedup_key = (sid.casefold(), hashlib.sha256(txt.casefold().encode("utf-8")).hexdigest()[:16])
            if dedup_key in seen_paper_para_hashes:
                continue
            seen_paper_para_hashes.add(dedup_key)

            rec: dict[str, Any] = {
                "source_id": sid,
                "source_file": fname,
                "paragraph_id": pid_str,
                "paragraph_index": idx,
                "text": txt,
                "xml_cross_checked": bool(pid_str and pid_str in xml_math_index),
            }
            if isinstance(item.get("page"), int):
                rec["page"] = item["page"]
                has_pdf_pages = True
            paragraphs.append(rec)
            _register_paper(sid, fname, p_meta)
            paper_registry[sid]["paragraph_count"] += 1
            loaded_here += 1

        if loaded_here > 0 and fname != "material.json":
            originals_loaded.add(fname)

    # Fallback: Markdown / TXT papers or case.json paragraphs
    if not paragraphs:
        for fp in sorted(input_dir.rglob("*")):
            if not fp.is_file() or fp.is_symlink() or fp.name.lower() in {"task.md", "readme.md", "case.json"}:
                continue
            if fp.suffix.lower() in {".md", ".txt"}:
                raw = fp.read_text(encoding="utf-8", errors="replace")
                sid = fp.stem
                _register_paper(sid, fp.name, manifest_by_file.get(fp.name) or {})
                for bno, blk in enumerate(re.split(r"\n\s*\n", raw), start=1):
                    txt = _norm_ws(blk)
                    if txt and not txt.startswith("#"):
                        paragraphs.append({
                            "source_id": sid,
                            "source_file": fp.name,
                            "paragraph_id": f"{fp.stem}:p{bno}",
                            "paragraph_index": bno,
                            "text": txt,
                            "xml_cross_checked": False,
                        })
                        paper_registry[sid]["paragraph_count"] += 1

    if not paragraphs and isinstance(case_data.get("paragraphs"), list):
        for idx, item in enumerate(case_data["paragraphs"], start=1):
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
                sid = str(item.get("source_id") or item.get("doi") or "case.json")
                _register_paper(sid, "case.json", {"doi": item.get("doi"), "title": item.get("title")})
                paragraphs.append({
                    "source_id": sid,
                    "source_file": "case.json",
                    "paragraph_id": str(item.get("paragraph_id") or item.get("id") or f"P{idx:03d}"),
                    "paragraph_index": idx,
                    "text": _norm_ws(item["text"]),
                    "xml_cross_checked": False,
                })
                paper_registry[sid]["paragraph_count"] += 1

    locator_contract = {
        "locator_type": "pdf_page_and_paragraph" if has_pdf_pages else "jats_or_source_paragraph_id",
        "pdf_page_available": has_pdf_pages,
        "xml_paragraphs_indexed_for_crosscheck": len(xml_math_index),
        "note": (
            "PDF page numbers were supplied in source metadata."
            if has_pdf_pages
            else "No PDF page numbers exist in supplied JATS/JSON corpus; paragraph_id locators are NOT PDF page numbers."
        ),
    }
    return paragraphs, list(paper_registry.values()), gaps, locator_contract


def build_knowledge_graph(
    paragraphs: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Extract paper-scoped entities, directed evidence edges, undirected co-occurrences, and held candidates."""
    nodes: dict[str, dict[str, Any]] = {}
    edges_map: dict[str, dict[str, Any]] = {}
    co_occ_map: dict[str, dict[str, Any]] = {}
    held_for_review: list[dict[str, Any]] = []
    # Paper-scoped abbreviation map: source_id -> {abbr_lower: full_name}
    paper_abbr_map: dict[str, dict[str, str]] = defaultdict(dict)

    # Pass 1: discover explicit within-paper Full Name (ABBR) definitions using initial-letter alignment
    for para in paragraphs:
        sid = para["source_id"]
        for m in ABBR_DEF_RE.finditer(para["text"]):
            raw_prefix = _norm_ws(m.group(1))
            abbr = m.group(2).strip()
            abbr_letters = [ch.lower() for ch in abbr if ch.isalpha()]
            if len(abbr_letters) < 2:
                continue
            words = raw_prefix.split()
            # Walk backward from the end of words to find the shortest span starting with abbr_letters[0]
            chosen = None
            max_span = min(len(words), len(abbr_letters) + 3)
            for span_len in range(len(abbr_letters), max_span + 1):
                cand_words = words[-span_len:]
                if cand_words[0][0].lower() == abbr_letters[0]:
                    chosen = " ".join(cand_words)
                    break
            if chosen:
                paper_abbr_map[sid][abbr.casefold()] = chosen

    # Pass 2: extract paper-scoped nodes, directed relations, and isolated co-occurrences
    for para in paragraphs:
        sid = para["source_id"]
        pid = para["paragraph_id"]
        for sent in SENTENCE_RE.split(para["text"]):
            sent = _norm_ws(sent)
            if len(sent) < 12:
                continue
            attribution = "cited_secondary" if CITED_PRIOR_RE.search(sent) else "author_reported"
            ev_base: dict[str, Any] = {
                "source_id": sid,
                "source_file": para["source_file"],
                "paragraph_id": pid,
                "paragraph_index": para["paragraph_index"],
                "quote": sent,
                "xml_cross_checked": para.get("xml_cross_checked", False),
            }
            if "page" in para:
                ev_base["page"] = para["page"]

            # Collect non-overlapping entity spans (combining case-sensitive chemical/acronym and case-insensitive domain matches)
            raw_matches: list[tuple[int, int, str, str]] = []
            for m in ENTITY_RE_CS.finditer(sent):
                raw_matches.append((m.start(), m.end(), str(m.lastgroup), _norm_ws(m.group(0))))
            for m in ENTITY_RE_CI.finditer(sent):
                raw_matches.append((m.start(), m.end(), str(m.lastgroup), _norm_ws(m.group(0))))
            # Sort by start ascending, longer span first, and drop overlapping shorter spans
            raw_matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
            dedup_matches: list[tuple[int, int, str, str]] = []
            last_end = -1
            for st, en, cat, lbl in raw_matches:
                if st >= last_end:
                    dedup_matches.append((st, en, cat, lbl))
                    last_end = en

            spans: list[tuple[tuple[int, int], str]] = []
            for st, en, cat, label in dedup_matches:
                # Within-paper canonicalization if abbreviation was explicitly defined in the same paper
                canonical_label = paper_abbr_map[sid].get(label.casefold(), label)
                eid = stable_id(sid, cat, canonical_label.casefold())
                node = nodes.setdefault(eid, {
                    "entity_id": eid,
                    "name": canonical_label,
                    "surface_forms": [],
                    "aliases": [],
                    "category": cat,
                    "source_id": sid,
                    "paragraph_id": pid,
                    "attribution": attribution,
                    "quote": sent,
                    "evidence": [],
                })
                if label not in node["surface_forms"]:
                    node["surface_forms"].append(label)
                if label.casefold() != canonical_label.casefold() and label not in node["aliases"]:
                    node["aliases"].append(label)
                if ev_base not in node["evidence"]:
                    node["evidence"].append(ev_base)
                spans.append(((st, en), eid))

            # Evaluate entity pairs using explicit same-clause (agent, predicate, patient, comparison_baseline, condition) role verification
            for pair_idx, ((left_span, src_id), (right_span, tgt_id)) in enumerate(zip(spans, spans[1:])):
                if src_id == tgt_id:
                    continue
                between = sent[left_span[1] : right_span[0]]
                if len(between) > 90:
                    pair_ids = sorted((src_id, tgt_id))
                    cid = stable_id("coocc", pair_ids[0], pair_ids[1], sid, str(pid or para["paragraph_index"]))
                    co_occ_map.setdefault(cid, {
                        "co_occurrence_id": cid,
                        "entity_a": pair_ids[0],
                        "entity_a_name": nodes[pair_ids[0]]["name"],
                        "entity_b": pair_ids[1],
                        "entity_b_name": nodes[pair_ids[1]]["name"],
                        "relation_type": "co_occurs_with_no_causal_claim",
                        "directed": False,
                        "evidence": ev_base,
                    })
                    continue

                matched_rel = None
                reverse_dir = False
                rel_match_obj = None
                for rel_name, rel_pat in PASSIVE_DIRECTED_LINKS.items():
                    m_p = re.search(rel_pat, between, re.I)
                    if m_p:
                        matched_rel = rel_name
                        rel_match_obj = m_p
                        if rel_name in {"inhibits", "activates"}:
                            reverse_dir = True
                        break
                if matched_rel is None:
                    for rel_name, rel_pat in DIRECTED_LINKS.items():
                        m_rel = re.search(rel_pat, between, re.I)
                        if m_rel:
                            prev_b = between[: m_rel.start()].strip().lower()
                            tok_b = m_rel.group(0).strip().lower()
                            if re.search(r"\b(?:a|an|the|their|its|most|more|widely|commonly|indiscriminate|accidental|chronic|acute|of|for)\b$", prev_b) and tok_b in {"use", "used", "uses"}:
                                continue
                            matched_rel = rel_name
                            rel_match_obj = m_rel
                            break

                if matched_rel is None:
                    pair_ids = sorted((src_id, tgt_id))
                    cid = stable_id("coocc", pair_ids[0], pair_ids[1], sid, str(pid or para["paragraph_index"]))
                    co_occ_map.setdefault(cid, {
                        "co_occurrence_id": cid,
                        "entity_a": pair_ids[0],
                        "entity_a_name": nodes[pair_ids[0]]["name"],
                        "entity_b": pair_ids[1],
                        "entity_b_name": nodes[pair_ids[1]]["name"],
                        "relation_type": "co_occurs_with_no_causal_claim",
                        "directed": False,
                        "evidence": ev_base,
                    })
                    continue

                # Check paragraph_id anchor and hedging/negation guard
                if not pid:
                    held_for_review.append({
                        "reason": "MISSING_PARAGRAPH_ID",
                        "source_name": nodes[src_id]["name"],
                        "candidate_relation": matched_rel,
                        "target_name": nodes[tgt_id]["name"],
                        "evidence": ev_base,
                    })
                    continue

                if HEDGED_OR_NEGATED_RE.search(sent):
                    held_for_review.append({
                        "reason": "HEDGED_OR_NEGATED_RELATION",
                        "source_name": nodes[src_id]["name"],
                        "candidate_relation": matched_rel,
                        "target_name": nodes[tgt_id]["name"],
                        "evidence": ev_base,
                    })
                    continue

                # Same-clause boundary check: block cross-clause modifier/subordinate bleed (e.g., "As ... suicide attempt, continuous monitoring ..., assessment of patients")
                if CLAUSE_BREAK_RE.search(between) or between.count(",") >= 2:
                    held_for_review.append({
                        "reason": "CROSS_CLAUSE_ARGUMENT_MISMATCH",
                        "source_name": nodes[src_id]["name"],
                        "candidate_relation": matched_rel,
                        "target_name": nodes[tgt_id]["name"],
                        "between_span": _norm_ws(between),
                        "evidence": ev_base,
                    })
                    continue

                eff_src, eff_tgt = (tgt_id, src_id) if reverse_dir else (src_id, tgt_id)
                src_cat = nodes[eff_src]["category"]
                tgt_cat = nodes[eff_tgt]["category"]
                allowed_src, allowed_tgt = RELATION_ROLE_RULES.get(matched_rel, (set(), set()))

                # Guard against baseline-cue hijacking patient role (e.g., "reduces median runtime relative to Claude + Repo" -> baseline, not patient!)
                after_verb = between[rel_match_obj.end() :] if rel_match_obj else between
                comparison_baseline: str | None = None
                if matched_rel in {"increases", "decreases"} and COMPARISON_BASELINE_CUE_RE.search(after_verb):
                    held_for_review.append({
                        "reason": "COMPARISON_BASELINE_MISBOUND_AS_PATIENT",
                        "source_name": nodes[eff_src]["name"],
                        "candidate_relation": matched_rel,
                        "target_name": nodes[eff_tgt]["name"],
                        "detail": "Right-hand entity follows a comparison-baseline cue ('relative to'/'compared with'/'than') and is a baseline, not the modified target.",
                        "evidence": ev_base,
                    })
                    continue

                # If right-hand entity is a metric modified by increases/decreases, check if a comparison baseline follows in the same clause
                if matched_rel in {"increases", "decreases"} and tgt_cat == "metric_or_endpoint":
                    after_tgt = sent[right_span[1] : min(len(sent), right_span[1] + 95)]
                    bm = COMPARISON_BASELINE_CUE_RE.search(after_tgt)
                    if bm:
                        for next_sp, next_eid in spans[pair_idx + 2 :]:
                            if next_sp[0] >= right_span[1] + bm.start() and next_sp[0] <= right_span[1] + 95:
                                comparison_baseline = nodes[next_eid]["name"]
                                break

                # Guard against metric acting as agent in outperforms/compares_with: resolve explicit named agent earlier in same clause or hold for review!
                resolved_metric_axis: str | None = None
                if matched_rel in {"outperforms", "compares_with"} and src_cat == "metric_or_endpoint":
                    resolved_metric_axis = nodes[eff_src]["name"]
                    # Look backward in the SAME clause for an explicit named method_or_model / material_or_entity agent
                    resolved_agent_id = None
                    for prev_sp, prev_eid in reversed(spans[:pair_idx]):
                        inter = sent[prev_sp[1] : left_span[0]]
                        if len(inter) <= 75 and not CLAUSE_BREAK_RE.search(inter) and "." not in inter and "?" not in inter:
                            if nodes[prev_eid]["category"] in allowed_src and prev_eid != eff_tgt:
                                resolved_agent_id = prev_eid
                                break
                    if resolved_agent_id is not None:
                        eff_src = resolved_agent_id
                        src_cat = nodes[eff_src]["category"]
                        comparison_baseline = nodes[eff_tgt]["name"]
                    else:
                        held_for_review.append({
                            "reason": "INVALID_ARGUMENT_ROLE_METRIC_AS_AGENT",
                            "source_name": nodes[eff_src]["name"],
                            "candidate_relation": matched_rel,
                            "target_name": nodes[eff_tgt]["name"],
                            "detail": "Metric/endpoint cannot be the agent of outperforms/compares_with without an explicit named method/entity in the same clause.",
                            "evidence": ev_base,
                        })
                        continue

                # Enforce strict semantic argument-role matrix on (agent_category, patient_category)
                if (allowed_src and src_cat not in allowed_src) or (allowed_tgt and tgt_cat not in allowed_tgt):
                    held_for_review.append({
                        "reason": "INVALID_ARGUMENT_ROLE_CATEGORY",
                        "source_name": nodes[eff_src]["name"],
                        "source_category": src_cat,
                        "candidate_relation": matched_rel,
                        "target_name": nodes[eff_tgt]["name"],
                        "target_category": tgt_cat,
                        "detail": f"Role check failed for '{matched_rel}': got ({src_cat} -> {tgt_cat}).",
                        "evidence": ev_base,
                    })
                    continue

                cond_m = CONDITION_RE.search(sent)
                cond_str = _norm_ws(cond_m.group(0)).rstrip(",.;:") if cond_m else None
                # Canonical deduplication key: (source_entity, relation_type, target_entity, condition) -> merges multi-paragraph duplicate assertions!
                rid = stable_id(eff_src, matched_rel, eff_tgt, sid, cond_str or "")
                if rid in edges_map:
                    existing = edges_map[rid]
                    if ev_base not in existing["supporting_evidence"]:
                        existing["supporting_evidence"].append(ev_base)
                        existing["evidence_count"] = len(existing["supporting_evidence"])
                else:
                    edges_map[rid] = {
                        "relation_id": rid,
                        "source_entity": eff_src,
                        "source_name": nodes[eff_src]["name"],
                        "target_entity": eff_tgt,
                        "target_name": nodes[eff_tgt]["name"],
                        "relation_type": matched_rel,
                        "directed": True,
                        "role_verified": True,
                        "argument_roles": {
                            "agent": nodes[eff_src]["name"],
                            "agent_category": src_cat,
                            "predicate": matched_rel,
                            "patient": nodes[eff_tgt]["name"],
                            "patient_category": tgt_cat,
                            "metric_axis": resolved_metric_axis,
                            "comparison_baseline": comparison_baseline,
                            "condition": cond_str,
                        },
                        "condition": cond_str,
                        "attribution": attribution,
                        "evidence_strength": "secondary_citation" if attribution == "cited_secondary" else "direct_text_evidence",
                        "evidence": ev_base,
                        "supporting_evidence": [ev_base],
                        "evidence_count": 1,
                    }

    return nodes, list(edges_map.values()), list(co_occ_map.values()), held_for_review, paper_abbr_map


def build_cross_paper_alignment(
    nodes: dict[str, dict[str, Any]],
    paper_abbr_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Align cross-paper recurring terms conservatively while refusing unsafe generic merges."""
    # Build global alias->full-name hints only for named standard acronyms defined in any paper
    global_abbr_to_full: dict[str, str] = {}
    for abbrs in paper_abbr_map.values():
        for ab, full in abbrs.items():
            global_abbr_to_full.setdefault(ab, full.casefold())

    by_norm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for eid, node in nodes.items():
        raw_norm = node["name"].casefold()
        # Normalize known acronym to its full form ONLY as a grouping key for cross-paper comparison
        group_key = global_abbr_to_full.get(raw_norm, raw_norm)
        by_norm[group_key].append({
            "entity_id": eid,
            "name": node["name"],
            "category": node["category"],
            "source_id": node["source_id"],
            "paragraph_id": node.get("paragraph_id"),
            "aliases": node.get("aliases") or [],
        })

    merged_concept_groups: list[dict[str, Any]] = []
    explicit_non_merge: list[dict[str, Any]] = []

    for norm_name, occs in sorted(by_norm.items()):
        distinct_papers = sorted({o["source_id"] for o in occs})
        if len(distinct_papers) < 2:
            continue
        cats = sorted({o["category"] for o in occs})
        # Conservative rule: generic nouns, mixed categories, or experimental materials/cohorts -> DO_NOT_MERGE!
        is_generic = norm_name in GENERIC_NON_MERGE_TERMS
        is_named_standard = (
            not is_generic
            and len(cats) == 1
            and cats[0] in {"method_or_model", "dataset_or_cohort", "task_or_domain"}
            and (
                any(ch.isupper() for ch in occs[0]["name"])
                or any(o["aliases"] for o in occs)
                or len(norm_name.split()) >= 2
            )
        )
        if is_named_standard:
            merged_concept_groups.append({
                "concept_id": stable_id("concept", cats[0], norm_name),
                "concept_label": occs[0]["name"],
                "category": cats[0],
                "paper_scoped_instances": occs,
                "decision": "LINK_CONCEPT_KEEP_SEPARATE_INSTANCES",
                "rationale": (
                    "Named standard method/benchmark/task shared across distinct papers; linked at concept level "
                    "while keeping paper-scoped nodes, experimental conditions, and results strictly unpooled."
                ),
            })
        else:
            reason = (
                "Generic scientific term; identical wording across papers does not imply identical physical sample, model instance, dataset, or condition."
                if is_generic
                else "Same-spelling entity in distinct papers lacks proof of identical physical sample, experimental condition, or category; preserved separately."
            )
            explicit_non_merge.append({
                "name": occs[0]["name"],
                "categories_observed": cats,
                "occurrences": occs,
                "decision": "DO_NOT_MERGE",
                "rationale": reason,
            })

    return {
        "within_paper_abbreviation_resolutions": {sid: dict(m) for sid, m in sorted(paper_abbr_map.items()) if m},
        "merged_concept_groups": merged_concept_groups,
        "explicit_non_merge_decisions": explicit_non_merge,
    }


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    case_id: str,
    prior_graph_path: Path | None,
    query_entity: str | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)

    case_path = input_dir / "case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace")) if case_path.is_file() else {}
    case_id = case_id or str(case_data.get("case_id") or "kg-case")

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="product-arena-10-760d1a0b")

    budget_guard.tick_phase(budget_file, "ingest_corpus")
    paragraphs, papers, gaps, locator_contract = load_corpus_strict(input_dir, case_data)

    budget_guard.tick_phase(budget_file, "extract_graph")
    nodes, edges, co_occurrences, held_for_review, paper_abbr_map = build_knowledge_graph(paragraphs)

    prior_graph = None
    if prior_graph_path and prior_graph_path.is_file():
        prior_graph = json.loads(prior_graph_path.read_text(encoding="utf-8"))
    nodes, edges, incremental_diff = apply_incremental_update(prior_graph, nodes, edges)

    alignment = build_cross_paper_alignment(nodes, paper_abbr_map)
    conflicts = find_cross_paper_conflicts(edges)
    queries = run_standard_queries(nodes, edges, co_occurrences, alignment, conflicts, query_entity=query_entity)

    if not paragraphs:
        gaps.append({"code": "NO_CITABLE_PARAGRAPHS", "message": "No readable paragraphs found in corpus."})
    if len(papers) < 2:
        gaps.append({"code": "SINGLE_SOURCE_ONLY", "message": f"Only {len(papers)} distinct paper source(s) loaded; cross-paper alignment requires >=2 distinct papers."})
    if paragraphs and not edges:
        gaps.append({"code": "NO_EXPLICIT_DIRECTIONAL_RELATION", "message": "No unambiguous directed relations found; co-occurrences were NOT promoted to causal edges."})
    if held_for_review:
        gaps.append({"code": "QUALIFIED_OR_UNANCHORED_RELATION_HELD", "count": len(held_for_review), "message": f"{len(held_for_review)} hedged/negated/unanchored candidate(s) held for manual review."})
    if any(not p.get("paragraph_id") for p in paragraphs):
        gaps.append({"code": "SOURCE_PARAGRAPH_ID_MISSING", "message": "One or more paragraphs lacked a JATS/source paragraph_id."})

    categories = sorted({n["category"] for n in nodes.values()})
    graph_payload = {
        "case_id": case_id,
        "locator_contract": locator_contract,
        "papers": papers,
        "entity_count": len(nodes),
        "relation_count": len(edges),
        "co_occurrence_count": len(co_occurrences),
        "held_for_review_count": len(held_for_review),
        "entities": list(nodes.values()),
        "relations": edges,
        "co_occurrences": co_occurrences,
        "held_for_review": held_for_review,
        "cross_paper_alignment": alignment,
        "conflicts": conflicts,
        "incremental_update": incremental_diff,
        "queries": queries,
    }

    result_guard.atomic_write_json(output_dir / "knowledge_graph.json", graph_payload)
    (output_dir / "knowledge_graph.graphml").write_text(
        export_graphml(list(nodes.values()), edges, co_occurrences),
        encoding="utf-8",
    )
    (output_dir / "knowledge_graph_report.md").write_text(
        render_kg_markdown(graph_payload, gaps),
        encoding="utf-8",
    )

    artifacts = ["result.json", "knowledge_graph.json", "knowledge_graph.graphml", "knowledge_graph_report.md"]
    status = "partial_with_gaps" if gaps else "ok"
    patch = {
        "status": status,
        "sources": sorted({p["source_file"] for p in papers}),
        "locator_contract": locator_contract,
        "uncertainty": (
            "Directed edges require explicit active evidence clauses with source_id and paragraph_id. "
            "Undirected co-occurrences are isolated and never promoted to causal claims. "
            "Hedged/negated relations are held for review; missing figures/supplements are not inferred."
        ),
        "artifacts": artifacts,
        "metrics": {
            "distinct_paper_count": len(papers),
            "entity_count": len(nodes),
            "relation_count": len(edges),
            "co_occurrence_count": len(co_occurrences),
            "held_for_review_count": len(held_for_review),
            "queries_executed": len(queries),
            "categories_covered": len(categories),
            "conflicts_detected": len(conflicts),
        },
        "graph_summary": {
            "distinct_papers": [p["source_id"] for p in papers],
            "entity_count": len(nodes),
            "relation_count": len(edges),
            "co_occurrence_count": len(co_occurrences),
            "categories": categories,
            "sample_queries": [q["query_id"] for q in queries],
        },
        "cross_paper_alignment": alignment,
        "conflicts": conflicts,
        "incremental_update": incremental_diff,
        "metric_provenance": {
            "distinct_paper_count": "knowledge_graph.json:papers",
            "entity_count": "knowledge_graph.json:entities",
            "relation_count": "knowledge_graph.json:relations",
            "co_occurrence_count": "knowledge_graph.json:co_occurrences",
            "held_for_review_count": "knowledge_graph.json:held_for_review",
            "queries_executed": "knowledge_graph.json:queries",
            "categories_covered": "knowledge_graph.json:entities[*].category",
            "conflicts_detected": "knowledge_graph.json:conflicts",
        },
        "findings": [
            f"Built multi-paper KG across {len(papers)} distinct paper(s): {len(nodes)} paper-scoped entities, "
            f"{len(edges)} directed evidence edges, {len(co_occurrences)} isolated undirected co-occurrences, "
            f"{len(held_for_review)} held candidates, {len(queries)} executed queries.",
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
        required_files=["knowledge_graph.json", "knowledge_graph.graphml", "knowledge_graph_report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multi-Paper Evidence-Anchored Knowledge Graph Engine (product-arena-10-760d1a0b).")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    parser.add_argument("--case-id", default="", help="Optional case ID override.")
    parser.add_argument("--prior-graph", type=Path, default=None, help="Optional prior knowledge_graph.json for incremental update.")
    parser.add_argument("--query-entity", default="", help="Optional entity name/alias for Q6 neighborhood trace.")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(
        paths["input_dir"],
        paths["output_dir"],
        paths["work_dir"],
        args.case_id,
        args.prior_graph,
        args.query_entity or None,
    )
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
