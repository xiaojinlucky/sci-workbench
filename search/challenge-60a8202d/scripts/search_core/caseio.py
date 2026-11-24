# -*- coding: utf-8 -*-
"""Case intake: discover /input, read case.json + task.md, classify every file, extract
documents and scholarly records, find seed/reference sets, snapshots, queries, criteria and the
output contract. Everything is structure-agnostic because the official cases are not public.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import scholarmeta as sm
    from . import textextract as te
    from . import lang as lg
except ImportError:  # flat script layout (scripts/core/*.py imported as top-level modules)
    import scholarmeta as sm  # type: ignore
    import textextract as te  # type: ignore
    import lang as lg  # type: ignore

WS = re.compile(r"\s+")
DOC_EXTS = {".pdf", ".docx", ".doc", ".html", ".htm", ".txt", ".md", ".xml", ".rtf"}
TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".bib", ".ris", ".nbib", ".xml", ".html", ".htm", ".ini", ".cfg", ".toml"}


@dataclass
class FileEntry:
    path: str
    rel: str
    ext: str
    size: int
    sha256: str
    role: str = "other"
    source_kind: str = ""
    note: str = ""

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class CaseBundle:
    input_dir: str
    case_id: str = ""
    case: Dict[str, Any] = field(default_factory=dict)
    case_json_path: str = ""
    task_md: str = ""
    task_paths: List[str] = field(default_factory=list)
    files: List[FileEntry] = field(default_factory=list)
    docs: List[Any] = field(default_factory=list)
    records: List[Any] = field(default_factory=list)
    record_files: Dict[str, int] = field(default_factory=dict)
    reference_sets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    snapshots: Dict[str, Any] = field(default_factory=dict)
    queries: List[Dict[str, Any]] = field(default_factory=list)
    criteria: Dict[str, Any] = field(default_factory=dict)
    contract: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    contract_source: str = ""
    question: str = ""
    question_lang: str = ""
    constraints: List[str] = field(default_factory=list)
    env: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    source_load_errors: List[Dict[str, Any]] = field(default_factory=list)
    opinions: List[Dict[str, Any]] = field(default_factory=list)
    pagination_issues: List[Dict[str, Any]] = field(default_factory=list)
    window_meta: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def rel(self, p: str) -> str:
        try:
            return os.path.relpath(p, self.input_dir).replace("\\", "/")
        except Exception:
            return p

    def inventory(self) -> List[Dict[str, Any]]:
        return [f.to_dict() for f in self.files]

    def docs_by_kind(self, *kinds: str):
        return [d for d in self.docs if d.kind in kinds]


# ----------------------------------------------------------------------------
def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _role_for(name: str, rel: str, json_kind: str) -> Tuple[str, str, str]:
    low = name.lower()
    rlow = rel.lower()
    if low == "case.json" or low.endswith("case.json"):
        return "case", json_kind or "", "case definition"
    if low in ("task.md", "task.txt", "instructions.md", "prompt.md", "readme_task.md") or low.startswith("task"):
        return "task", "", "task statement"
    if "contract" in low or "output_spec" in low or low.startswith("schema") or "output_schema" in low:
        return "contract", "", "output contract"
    if "criteria" in low or "protocol" in low or "inclusion" in low or "exclusion" in low or "eligibility" in low or "screening" in low:
        return "criteria", "", "criteria/protocol"
    if any(k in low for k in ("query", "queries", "search_plan", "iteration", "search_log", "run_log", "searches")):
        return "query", "", "queries / iterations"
    if any(k in low for k in ("snapshot", "previous", "current", "baseline", "import", "watermark", "state", "delta", "changes", "update")) and json_kind in ("generic", "list", "case-like", ""):
        return "snapshot", "", "snapshot/import state"
    if any(k in low for k in ("reference", "citation", "graph", "edges", "nodes", "cited_by", "cites", "seed", "known", "included", "gold", "relevant", "bibliography", "hit")):
        return "reference_graph", "", "reference/graph/known set"
    if any(k in low for k in ("metadata", "catalog", "manifest", "alias", "index", "log", "version", "notes", "inventory", "library")):
        return "metadata", "", "library metadata"
    if json_kind and json_kind not in ("generic",):
        return "api_response", json_kind, "API response"
    if json_kind == "generic":
        return "data", "generic", "generic JSON data"
    return "other", "", ""


def _json_kind(obj: Any) -> str:
    if isinstance(obj, dict):
        if obj.get("message-type") in ("work", "work-list") or ("message" in obj and "status" in obj):
            return "crossref"
        if "resultList" in obj or "hitCount" in obj:
            return "europepmc"
        if "esearchresult" in obj or "PubmedArticleSet" in obj:
            return "pubmed"
        if "results" in obj and "meta" in obj and isinstance(obj.get("results"), list):
            return "openalex"
        if "data" in obj and isinstance(obj.get("data"), list) and obj["data"] and isinstance(obj["data"][0], dict) and "paperId" in obj["data"][0]:
            return "s2"
        if "entries" in obj or "feed" in obj:
            return "arxiv"
        if isinstance(obj.get("results"), dict) and "cluster" in obj["results"]:
            return "patent_google"
        if any(k in obj for k in ("case_id", "caseId", "output_contract", "task", "question", "research_question", "criteria", "queries", "reference", "references", "known", "seed", "snapshots", "snapshot", "documents", "inputs", "materials")):
            return "case-like"
        if any(k in obj for k in ("title", "DOI", "doi", "abstract", "patent_number", "publication_number")):
            return "record-like"
        return "generic"
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            return _json_kind(obj[0]) if len(obj) < 50 or any(k in obj[0] for k in ("DOI", "doi", "title", "pmid", "publication_number")) else "list"
        return "list"
    return "generic"


def read_json(p: str) -> Any:
    raw = open(p, "rb").read()
    last = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252"):
        try:
            txt = raw.decode(enc)
        except UnicodeDecodeError as e:
            last = e
            continue
        try:
            return json.loads(txt)
        except json.JSONDecodeError as e:
            last = e
            try:
                lines = [l for l in txt.splitlines() if l.strip()]
                if len(lines) > 1:
                    return [json.loads(l) for l in lines]
            except Exception as e2:
                last = e2
            continue
    raise ValueError(f"unparseable JSON: {last}")


def read_text(p: str) -> str:
    raw = open(p, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# ----------------------------------------------------------------------------
BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.S)
BIB_FIELD = re.compile(r"\b(\w+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", re.S)


def parse_bib(text: str) -> List[sm.Record]:
    out = []
    for m in BIB_ENTRY.finditer(text):
        typ, key, body = m.group(1), m.group(2), m.group(3)
        fields = {k.lower(): sm.strip_html(v) for k, v in BIB_FIELD.findall(body)}
        r = sm.Record(source="bib", title=fields.get("title", ""), abstract=fields.get("abstract", ""), year=sm._year_from(fields.get("year", "")), venue=fields.get("journal") or fields.get("booktitle") or fields.get("publisher", ""), doi=sm.norm_doi(fields.get("doi", "")), url=fields.get("url", ""), pub_type=typ.lower())
        r.authors = [sm.author_display(a.strip()) for a in re.split(r"\s+and\s+", fields.get("author", "")) if a.strip()]
        r.version = "preprint" if "preprint" in typ.lower() or "arxiv" in fields.get("journal", "").lower() or "eprint" in fields else "conference" if typ.lower() in ("inproceedings", "conference") else "published"
        r.arxiv_id = sm.norm_arxiv(fields.get("eprint", ""))
        r.keywords = [k.strip() for k in re.split(r"[;,]", fields.get("keywords", "")) if k.strip()]
        out.append(r)
    return out


def parse_ris(text: str) -> List[sm.Record]:
    recs: List[sm.Record] = []
    cur: Dict[str, Any] = {}

    def flush():
        nonlocal cur
        if cur.get("TI") or cur.get("T1"):
            r = sm.Record(source="ris", title=cur.get("TI") or cur.get("T1", ""), abstract=cur.get("AB", ""), year=sm._year_from(cur.get("PY", "")), venue=cur.get("JO") or cur.get("JF") or cur.get("T2", ""), doi=sm.norm_doi(cur.get("DO", "")), url=cur.get("UR", ""), pub_type=cur.get("TY", "").lower())
            r.authors = [a for a in cur.get("AU", []) if isinstance(a, str)] or [a for a in cur.get("A1", []) if isinstance(a, str)]
            r.version = "preprint" if "preprint" in r.pub_type else "conference" if r.pub_type in ("confproc", "cpaper") else "published"
            r.keywords = [k for k in cur.get("KW", []) if isinstance(k, str)]
            recs.append(r)
        cur = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])\s{2}-\s?(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "ER":
            flush()
            continue
        cur.setdefault(tag, [])
        cur[tag].append(val)
    flush()
    for r in recs:
        for k in ("AB", "PY", "JO", "JF", "T2", "DO", "UR", "TY", "TI", "T1"):
            pass
    return recs


def parse_ris_fixed(text: str) -> List[sm.Record]:
    recs: List[sm.Record] = []
    cur: Dict[str, List[str]] = {}

    def flush():
        nonlocal cur
        if cur.get("TI") or cur.get("T1"):
            g = lambda k: (cur.get(k) or [""])[0]
            r = sm.Record(source="ris", title=g("TI") or g("T1"), abstract=g("AB"), year=sm._year_from(g("PY")), venue=g("JO") or g("JF") or g("T2"), doi=sm.norm_doi(g("DO")), url=g("UR"), pub_type=g("TY").lower())
            r.authors = cur.get("AU") or cur.get("A1") or []
            r.version = "preprint" if "preprint" in r.pub_type else "conference" if r.pub_type in ("confproc", "cpaper") else "published"
            r.keywords = cur.get("KW") or []
            recs.append(r)
        cur = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])\s{2}-\s?(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "ER":
            flush()
            continue
        cur.setdefault(tag, []).append(val)
    flush()
    return recs


def parse_nbib(text: str) -> List[sm.Record]:
    recs: List[sm.Record] = []
    cur: Dict[str, List[str]] = {}

    def flush():
        nonlocal cur
        if cur.get("PMID"):
            g = lambda k: (cur.get(k) or [""])[0]
            r = sm.Record(source="pubmed_nbib", title=g("TI"), abstract=" ".join(cur.get("AB", [])), year=sm._year_from(g("DP")), venue=g("JT"), doi=sm.norm_doi(g("LID")) if "doi" in g("LID").lower() else "", pmid=g("PMID"), pub_type=";".join(cur.get("PT", [])))
            r.authors = [a.split(".")[0].strip() for a in cur.get("AU", [])]
            recs.append(r)
        cur = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z]{2,4})\s*- ([^-]*)- ?(.*)$", line)
        if not m:
            continue
        tag, _, val = m.group(1), m.group(2), m.group(3).strip()
        if tag == "PMID" and cur.get("PMID"):
            flush()
        cur.setdefault(tag, []).append(val)
    flush()
    return recs


def parse_csv_records(p: str) -> List[sm.Record]:
    recs = []
    text = read_text(p)
    delim = "\t" if p.lower().endswith(".tsv") or (text.count("\t") > text.count(",") and "\t" in text.splitlines()[0]) else ","
    try:
        rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
    except Exception:
        return []
    if not rows:
        return []
    keys = {k.lower().strip(): k for k in rows[0].keys()}
    tkey = next((keys[k] for k in ("title", "ti", "article_title", "document_title", "name") if k in keys), None)
    if not tkey:
        return []
    for i, row in enumerate(rows):
        g = lambda *names: next((str(row[keys[n]]).strip() for n in names if n in keys and str(row[keys[n]]).strip()), "")
        r = sm.Record(source="csv", title=sm.strip_html(g("title", "ti", "article_title")), abstract=sm.strip_html(g("abstract", "ab")), year=sm._year_from(g("year", "py", "publication_year", "pub_year")), venue=g("journal", "source", "venue", "container", "publication_title"), doi=sm.norm_doi(g("doi")), pmid=sm.norm_pmid(g("pmid")), url=g("url", "link"), pub_type=g("type", "publication_type", "document_type"))
        au = g("authors", "author", "au", "inventors")
        r.authors = [sm.author_display(a.strip()) for a in re.split(r";|, (?=[A-Z])|\|", au) if a.strip()] if au else []
        r.version = g("version", "status") or ("preprint" if "preprint" in r.pub_type.lower() else "published" if r.doi else "unknown")
        if "patent_number" in keys or "publication_number" in keys:
            r.patent_number = g("patent_number", "publication_number", "pn")
            r.source = "patent"
            r.pub_type = "patent"
        recs.append(r)
    return recs


def parse_arxiv_xml(text: str) -> List[sm.Record]:
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(text)
    except Exception:
        return []
    out = []
    for e in root.findall("a:entry", ns):
        d = {"title": sm.norm_ws(e.findtext("a:title", default="", namespaces=ns)), "summary": sm.norm_ws(e.findtext("a:summary", default="", namespaces=ns)), "published": e.findtext("a:published", default="", namespaces=ns), "updated": e.findtext("a:updated", default="", namespaces=ns), "id": e.findtext("a:id", default="", namespaces=ns), "doi": e.findtext("arxiv:doi", default="", namespaces=ns), "journal_ref": e.findtext("arxiv:journal_ref", default="", namespaces=ns), "authors": [{"name": a.findtext("a:name", default="", namespaces=ns)} for a in e.findall("a:author", ns)], "tags": [{"term": c.get("term")} for c in e.findall("a:category", ns)], "links": [{"rel": l.get("rel"), "href": l.get("href")} for l in e.findall("a:link", ns)]}
        out.append(sm.parse_arxiv_entry(d))
    return out


def parse_pubmed_xml(text: str) -> List[sm.Record]:
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except Exception:
        return []
    out = []
    for a in root.iter("PubmedArticle"):
        try:
            out.append(sm.parse_pubmed_article(_et_to_dict(a)))
        except Exception:
            pass
    return out


def _et_to_dict(el) -> Dict[str, Any]:
    d: Dict[str, Any] = {}
    for ch in el:
        tag = ch.tag.split("}")[-1]
        if len(ch):
            if tag in ("AuthorList", "KeywordList", "MeshHeadingList", "PublicationTypeList", "Abstract", "ArticleIdList", "CommentsCorrectionsList", "Language"):
                d[tag] = {c.tag.split("}")[-1]: [_et_to_dict(x) for x in c] if len(c) else (c.text or "") for c in ch} if tag != "Abstract" else {"AbstractText": [_et_to_dict(x) for x in ch]}
            else:
                v = _et_to_dict(ch)
                if tag in d and not isinstance(d[tag], list):
                    d[tag] = [d[tag], v]
                else:
                    d[tag] = v
        else:
            t = (ch.text or "").strip()
            if tag in d:
                if isinstance(d[tag], list):
                    d[tag].append(t)
                else:
                    d[tag] = [d[tag], t]
            else:
                d[tag] = t
        for k, v in ch.attrib.items():
            d.setdefault("_attrs", {})[k] = v
    return d


# ----------------------------------------------------------------------------
CONTRACT_KEYS = ("output_contract", "outputContract", "contract", "output_schema", "outputSchema", "schema", "result_schema", "resultSchema", "required_outputs", "outputs", "output", "deliverables")
QUESTION_KEYS = ("question", "research_question", "researchQuestion", "task", "prompt", "query", "topic", "objective", "goal", "instruction", "instructions", "description", "scenario")
CONSTRAINT_KEYS = ("constraints", "restrictions", "limits", "requirements", "rules", "input_constraints", "scope")
CRITERIA_KEYS = ("criteria", "inclusion_criteria", "exclusion_criteria", "eligibility", "screening_criteria", "protocol", "inclusion", "exclusion")
SNAPSHOT_KEYS = ("snapshots", "snapshot", "previous_snapshot", "current_snapshot", "previous", "current", "baseline", "imports", "import_states", "incremental", "delta")
QUERY_KEYS = ("queries", "query", "searches", "iterations", "search_plan", "plans", "runs", "search_log", "api_responses", "responses")
REF_KEYS = ("references", "reference", "reference_set", "known_set", "seed", "seeds", "seed_set", "included", "include", "gold", "relevant", "key_papers", "known", "citations", "graph", "reference_graph", "hit_set")


def _norm_field_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(k).strip().lower()).strip("_")


SYNONYM_FIELD = {
    "results": "results", "result": "results", "ranked_results": "results", "candidates": "results", "search_results": "results", "retrieved": "results", "retrieved_documents": "results", "papers": "results", "records": "results", "hits": "results", "candidate_set": "results", "ranked_candidates": "results", "screening_results": "results", "selected": "results", "selected_documents": "results", "final_set": "results", "answer": "results", "answers": "results", "answer_set": "results", "findings": "results", "report": "report", "summary": "summary", "analysis": "report", "review": "report", "synthesis": "report", "draft": "report", "overview": "summary", "overview_text": "summary",
    "evidence": "evidence", "evidence_records": "evidence", "evidence_log": "evidence", "sources": "evidence", "source_records": "evidence", "provenance": "evidence", "citations": "evidence", "references": "evidence", "cited_sources": "evidence", "evidence_chain": "evidence", "justifications": "evidence", "ranking_rationale": "evidence", "screening_rationale": "evidence", "relevance_rationale": "evidence", "query_log": "evidence", "queries": "evidence", "search_log": "evidence", "trace": "evidence",
    "status": "status", "state": "status", "run_status": "status", "warnings": "status", "gaps": "status", "uncertainty": "status", "uncertainties": "status", "limitations": "status", "missing": "status", "errors": "status", "conflicts": "status", "excluded": "status", "exclusions": "status", "duplicates": "status", "dedup": "status", "deduplicated": "status", "change_records": "status", "changes": "status", "change_log": "status", "diff": "status", "delta": "status", "new_records": "results", "added": "results", "updated": "status", "removed": "status", "coverage": "metrics", "recall": "metrics", "precision": "metrics", "ndcg": "metrics", "metrics": "metrics", "stats": "metrics", "statistics": "metrics", "counts": "metrics", "quality": "metrics", "agreement": "metrics", "kappa": "metrics", "artifacts": "artifacts", "files": "artifacts", "outputs": "artifacts", "output_files": "artifacts", "tables": "artifacts", "figures": "artifacts",
}


def normalize_contract(obj: Any, source: str = "") -> Dict[str, Dict[str, Any]]:
    """Normalize any contract shape into {field: {description, required, type, path}}."""
    contract: Dict[str, Dict[str, Any]] = {}

    def add(name: str, desc: Any = "", required: Any = None, typ: str = "", path: str = ""):
        key = _norm_field_key(name)
        if not key:
            return
        if isinstance(desc, (dict, list)):
            desc = json.dumps(desc, ensure_ascii=False)[:600]
        entry = contract.get(key) or {}
        if desc and not entry.get("description"):
            entry["description"] = str(desc)[:600]
        if required is not None:
            entry["required"] = bool(required)
        if typ and not entry.get("type"):
            entry["type"] = typ
        entry.setdefault("name", name)
        entry.setdefault("path", path or name)
        contract[key] = entry

    if isinstance(obj, str):
        # markdown contract: parse table rows and bullet fields
        for line in obj.splitlines():
            m = re.match(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
            if m and not set(m.group(1).strip()) <= {"-", ":"}:
                add(m.group(1).strip(), m.group(2).strip(), None, "", source)
            else:
                m2 = re.match(r"^\s*[-*]?\s*`?([A-Za-z_][\w\.]*)`?\s*[:：]\s*(.+)$", line)
                if m2:
                    add(m2.group(1), m2.group(2), None, "", source)
        return contract
    if isinstance(obj, list):
        for it in obj:
            if isinstance(it, str):
                add(it, "", None, "", source)
            elif isinstance(it, dict):
                nm = it.get("name") or it.get("field") or it.get("key") or it.get("id")
                if nm:
                    add(nm, it.get("description") or it.get("desc") or it.get("requirement") or it.get("require") or "", it.get("required"), it.get("type", ""), source)
        return contract
    if isinstance(obj, dict):
        # JSON schema
        if "properties" in obj and isinstance(obj["properties"], dict):
            req = set(obj.get("required") or [])
            for k, v in obj["properties"].items():
                add(k, v.get("description", "") if isinstance(v, dict) else "", k in req, v.get("type", "") if isinstance(v, dict) else "", source)
            return contract
        if "fields" in obj and isinstance(obj["fields"], (list, dict)):
            return normalize_contract(obj["fields"], source)
        for k, v in obj.items():
            if isinstance(v, dict):
                nm = v.get("name") or v.get("field") or k
                add(nm, v.get("description") or v.get("desc") or v.get("requirement") or v.get("require") or (json.dumps(v, ensure_ascii=False)[:200] if v else ""), v.get("required"), v.get("type", ""), source + "/" + k)
            else:
                add(k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)[:200] if v is not None else "", None, type(v).__name__ if v is not None else "", source + "/" + k)
        return contract
    return contract


def find_contract(case: Dict[str, Any], task_md: str, files: List[FileEntry], read_json_fn, read_text_fn) -> Tuple[Dict[str, Dict[str, Any]], str]:
    for k in CONTRACT_KEYS:
        if k in case and case[k]:
            c = normalize_contract(case[k], f"case.json/{k}")
            if c:
                return c, f"case.json:{k}"
    # search case dict recursively for output_contract-like keys
    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in CONTRACT_KEYS and isinstance(v, (dict, list)) and v:
                    c = normalize_contract(v, f"case.json{path}/{k}")
                    if c:
                        return c, f"case.json:{path}/{k}"
                r = walk(v, f"{path}/{k}")
                if r:
                    return r
        elif isinstance(o, list):
            for i, v in enumerate(o[:50]):
                r = walk(v, f"{path}/{i}")
                if r:
                    return r
        return None
    r = walk(case)
    if r:
        return r
    for f in files:
        if f.role == "contract":
            try:
                if f.ext == ".json":
                    return normalize_contract(read_json_fn(f.path), f.rel), f.rel
                return normalize_contract(read_text_fn(f.path), f.rel), f.rel
            except Exception:
                pass
    # task.md section
    for marker in ("输出要求", "Output requirements", "Output Requirements", "## Output", "输出格式", "result.json", "输出字段", "Required outputs", "output_contract"):
        i = task_md.find(marker)
        if i >= 0:
            seg = task_md[i:i + 6000]
            j = seg.find("\n## ", 10)
            if j > 0:
                seg = seg[:j]
            c = normalize_contract(seg, "task.md")
            if c:
                return c, "task.md"
    return {}, ""


def _collect_keys(o: Any, keys: Tuple[str, ...], path: str = "", depth: int = 0, found=None) -> List[Tuple[str, Any, str]]:
    if found is None:
        found = []
    if depth > 6:
        return found
    if isinstance(o, dict):
        for k, v in o.items():
            if k in keys and v:
                found.append((k, v, f"{path}/{k}"))
            _collect_keys(v, keys, f"{path}/{k}", depth + 1, found)
    elif isinstance(o, list):
        for i, v in enumerate(o[:40]):
            _collect_keys(v, keys, f"{path}/{i}", depth + 1, found)
    return found


def load_case(input_dir: str, question_override: str = "") -> CaseBundle:
    input_dir = os.path.abspath(input_dir)
    b = CaseBundle(input_dir=input_dir)
    b.env["cwd"] = os.getcwd()
    b.env["input_exists"] = os.path.isdir(input_dir)
    if not b.env["input_exists"]:
        b.notes.append(f"input dir not found: {input_dir}")
        return b
    # ---- enumerate files
    for root, dirs, fnames in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fn in fnames:
            p = os.path.join(root, fn)
            rel = b.rel(p)
            ext = os.path.splitext(fn)[1].lower()
            try:
                size = os.path.getsize(p)
                sha = sha256_file(p)
            except Exception:
                size, sha = 0, ""
            b.files.append(FileEntry(path=p, rel=rel, ext=ext, size=size, sha256=sha))
    b.files.sort(key=lambda f: f.rel)
    # ---- case.json + task
    parsed_json: Dict[str, Any] = {}
    for f in b.files:
        base = os.path.basename(f.rel).lower()
        if base == "case.json" or (base.endswith("case.json") and not b.case):
            try:
                b.case = read_json(f.path)
                b.case_json_path = f.rel
                f.role, f.source_kind, f.note = "case", "case", "case definition"
            except Exception as e:
                b.notes.append(f"case.json unreadable: {e}")
        if base in ("task.md", "task.txt", "prompt.md", "instructions.md") or base.startswith("task."):
            try:
                t = read_text(f.path)
                b.task_md += ("\n\n" if b.task_md else "") + t
                b.task_paths.append(f.rel)
                f.role, f.note = "task", "task statement"
            except Exception as e:
                b.notes.append(f"task unreadable: {e}")
    if not b.task_md:
        # any markdown that looks like a task
        for f in b.files:
            if f.ext == ".md" and re.search(r"任务|task|要求|requirement|案例|case", read_text(f.path)[:3000], re.I):
                b.task_md = read_text(f.path)
                b.task_paths.append(f.rel)
                f.role, f.note = "task", "task statement (inferred)"
                break
    if isinstance(b.case, dict):
        b.case_id = str(b.case.get("case_id") or b.case.get("caseId") or b.case.get("id") or "")
        if not b.case_id:
            m = re.search(r"case[_\- ]?id\s*[:：]\s*([\w\-\.]+)", b.task_md, re.I)
            b.case_id = m.group(1) if m else ""
        if not b.case_id:
            b.case_id = os.path.basename(os.path.dirname(input_dir)) or os.path.basename(input_dir)
    else:
        b.case_id = ""
    # ---- parse remaining files
    docs: List[Any] = []
    records: List[sm.Record] = []
    ref_sets: Dict[str, Dict[str, Any]] = {}
    snapshots: Dict[str, Any] = {}
    queries: List[Dict[str, Any]] = []
    criteria: Dict[str, Any] = {}
    parsed: Dict[str, Any] = {}
    for f in b.files:
        if f.role in ("case", "task"):
            continue
        name = os.path.basename(f.rel)
        try:
            if f.ext == ".json" or f.ext == ".jsonl":
                obj = read_json(f.path)
                parsed[f.rel] = obj
                kind = _json_kind(obj)
                role, skind, note = _role_for(name, f.rel, kind)
                f.role, f.source_kind, f.note = role, skind, note
                if isinstance(obj, (str, int, float, bool)) or obj is None:
                    b.source_load_errors.append({"file": f.rel, "error_type": "unsupported_json_shape", "detail": f"top-level JSON type is {type(obj).__name__}, expected object or list"})
                elif isinstance(obj, dict):
                    # Detect explicit pagination/source error markers (e.g. PyAlex/API cursor error snapshots)
                    if obj.get("error") or obj.get("status") in ("error", "failed", "rate_limited", "timeout", "unavailable", "truncated"):
                        b.pagination_issues.append({"file": f.rel, "source": obj.get("source") or kind or f.rel, "status": str(obj.get("status") or "error"), "error": str(obj.get("error") or obj.get("message") or "source reported error"), "cursor": obj.get("cursor") or obj.get("next_cursor")})
                    if any(k in obj for k in ("window_start", "window_end", "timezone", "watermark", "as_of", "snapshot_date")):
                        for wk in ("window_start", "window_end", "timezone", "watermark", "as_of", "snapshot_date", "previous_watermark", "subscription_id", "schedule"):
                            if wk in obj and wk not in b.window_meta:
                                b.window_meta[wk] = obj[wk]
                    for ok in ("opinions", "reviewer_opinions", "conflicting_opinions", "annotations", "judgments"):
                        if isinstance(obj.get(ok), list):
                            for op in obj[ok]:
                                if isinstance(op, dict):
                                    b.opinions.append({**op, "_file": f.rel})
                if role in ("api_response", "data", "other"):
                    recs = sm.extract_records(obj, path=f.rel)
                    if recs:
                        records.extend(recs)
                        b.record_files[f.rel] = len(recs)
                        f.role = "api_response" if role != "data" else "data"
                    elif isinstance(obj, list) and len(obj) == 0:
                        b.record_files[f.rel] = 0
                        f.note = "valid empty JSON array (0 records)"
                # structured extras
                for k, v, pth in _collect_keys(obj, QUERY_KEYS):
                    if isinstance(v, (list, dict)):
                        queries.append({"file": f.rel, "key": k, "pointer": pth, "data": v})
                for k, v, pth in _collect_keys(obj, CRITERIA_KEYS):
                    if isinstance(v, (list, dict, str)):
                        criteria.setdefault(k, []).append({"file": f.rel, "pointer": pth, "data": v})
                for k, v, pth in _collect_keys(obj, REF_KEYS):
                    if isinstance(v, (list, dict)):
                        ref_sets.setdefault(f"{k}@{f.rel}", {"file": f.rel, "key": k, "pointer": pth, "data": v})
                for k, v, pth in _collect_keys(obj, SNAPSHOT_KEYS):
                    if isinstance(v, (list, dict)):
                        snapshots.setdefault(k, {"file": f.rel, "pointer": pth, "data": v})
                if role == "snapshot":
                    # a whole file is a snapshot
                    label = "current" if any(x in name.lower() for x in ("current", "new", "latest", "after")) else "previous" if any(x in name.lower() for x in ("previous", "old", "before", "baseline", "prior")) else f.rel
                    snapshots[label] = {"file": f.rel, "pointer": "", "data": obj}
                if role == "query":
                    queries.append({"file": f.rel, "key": "file", "pointer": "", "data": obj})
                if role == "criteria":
                    criteria.setdefault("file", []).append({"file": f.rel, "pointer": "", "data": obj})
                if role == "reference_graph":
                    ref_sets.setdefault(f"file@{f.rel}", {"file": f.rel, "pointer": "", "data": obj})
            elif f.ext == ".bib":
                recs = parse_bib(read_text(f.path))
                f.role, f.source_kind, f.note = "api_response" if recs else "other", "bib", f"{len(recs)} bib entries"
                if recs:
                    for r in recs:
                        r.path = f.rel
                    records.extend(recs); b.record_files[f.rel] = len(recs)
            elif f.ext == ".ris":
                recs = parse_ris_fixed(read_text(f.path))
                f.role, f.source_kind, f.note = "api_response" if recs else "other", "ris", f"{len(recs)} RIS entries"
                if recs:
                    for r in recs:
                        r.path = f.rel
                    records.extend(recs); b.record_files[f.rel] = len(recs)
            elif f.ext == ".nbib":
                recs = parse_nbib(read_text(f.path))
                f.role, f.source_kind, f.note = "api_response" if recs else "other", "nbib", f"{len(recs)} NBIB entries"
                if recs:
                    for r in recs:
                        r.path = f.rel
                    records.extend(recs); b.record_files[f.rel] = len(recs)
            elif f.ext == ".xml":
                txt = read_text(f.path)
                recs = parse_pubmed_xml(txt) or parse_arxiv_xml(txt)
                if recs:
                    f.role, f.source_kind, f.note = "api_response", recs[0].source, f"{len(recs)} records"
                    for r in recs:
                        r.path = f.rel
                    records.extend(recs); b.record_files[f.rel] = len(recs)
                else:
                    # XML could be a document (JATS/TEI/patent full text) -> text extraction
                    d = te.extract_doc(f.path, f.rel)
                    if d.text.strip():
                        docs.append(d); f.role, f.note = "doc", "XML document"
                        if "patent" in f.rel.lower() or re.search(r"<patent|us-patent-application|DOC\b", txt[:3000], re.I):
                            pm = _patent_from_html(d.text, f.rel)
                            if pm:
                                records.append(pm); b.record_files[f.rel] = 1
                                f.note += "; patent fields extracted"
            elif f.ext in (".csv", ".tsv"):
                recs = parse_csv_records(f.path)
                if recs:
                    f.role, f.source_kind, f.note = "api_response", "csv", f"{len(recs)} tabular records"
                    for r in recs:
                        r.path = f.rel
                    records.extend(recs); b.record_files[f.rel] = len(recs)
                else:
                    d = te.extract_doc(f.path, f.rel)
                    docs.append(d); f.role, f.note = "table", "tabular file"
            elif f.ext in (".yaml", ".yml"):
                try:
                    import yaml  # type: ignore
                    obj = yaml.safe_load(read_text(f.path))
                    parsed[f.rel] = obj
                    kind = _json_kind(obj)
                    role, skind, note = _role_for(name, f.rel, kind)
                    f.role, f.source_kind, f.note = role, skind, note
                    recs = sm.extract_records(obj, path=f.rel) if obj else []
                    if recs:
                        records.extend(recs); b.record_files[f.rel] = len(recs)
                    if role == "query":
                        queries.append({"file": f.rel, "key": "file", "pointer": "", "data": obj})
                    elif role in ("criteria", "snapshot", "reference_graph"):
                        (criteria if role == "criteria" else snapshots if role == "snapshot" else ref_sets).setdefault(f"file@{f.rel}", {"file": f.rel, "pointer": "", "data": obj})
                    if role == "contract":
                        c = normalize_contract(obj, f.rel)
                        if c:
                            b.contract, b.contract_source = c, f.rel
                except ImportError:
                    f.note = "yaml skipped (no PyYAML)"
                except Exception as e:
                    f.note = f"yaml error: {e}"
            elif f.ext in DOC_EXTS:
                # JSON-like html? treat as doc
                d = te.extract_doc(f.path, f.rel)
                docs.append(d)
                f.role, f.note = "doc", f"{d.kind} doc, {len(d.pages)} pages, {len(d.blocks)} blocks"
                if f.ext == ".html" and "patent" in f.rel.lower():
                    # Google Patents / USPTO HTML: try to pull structured patent fields
                    pm = _patent_from_html(d.text, f.rel)
                    if pm:
                        records.append(pm); b.record_files[f.rel] = 1
                        f.note += "; patent fields extracted"
            else:
                f.role, f.note = "other", "unsupported/unknown"
        except Exception as e:
            f.note = f"error: {type(e).__name__}: {e}"
            b.notes.append(f"{f.rel}: {f.note}")
            b.source_load_errors.append({"file": f.rel, "error_type": type(e).__name__, "detail": str(e)[:300]})
    # ---- case-level structured content
    if isinstance(b.case, dict):
        for k, v, pth in _collect_keys(b.case, QUESTION_KEYS):
            if isinstance(v, str) and len(v) > 5:
                b.question = b.question or v
                break
        for k, v, pth in _collect_keys(b.case, QUERY_KEYS):
            queries.append({"file": "case.json", "key": k, "pointer": pth, "data": v})
        for k, v, pth in _collect_keys(b.case, CRITERIA_KEYS):
            criteria.setdefault(k, []).append({"file": "case.json", "pointer": pth, "data": v})
        for k, v, pth in _collect_keys(b.case, REF_KEYS):
            ref_sets.setdefault(f"{k}@case.json", {"file": "case.json", "pointer": pth, "data": v})
        for k, v, pth in _collect_keys(b.case, SNAPSHOT_KEYS):
            snapshots.setdefault(k, {"file": "case.json", "pointer": pth, "data": v})
        for wk in ("window_start", "window_end", "timezone", "watermark", "as_of", "snapshot_date", "previous_watermark", "subscription_id", "schedule"):
            if wk in b.case and wk not in b.window_meta:
                b.window_meta[wk] = b.case[wk]
        for ok in ("opinions", "reviewer_opinions", "conflicting_opinions"):
            if isinstance(b.case.get(ok), list):
                for op in b.case[ok]:
                    if isinstance(op, dict):
                        b.opinions.append({**op, "_file": "case.json"})
        for k, v, pth in _collect_keys(b.case, CONSTRAINT_KEYS):
            if isinstance(v, (list, str)):
                b.constraints.extend([v] if isinstance(v, str) else [str(x) for x in v])
        # case-level record lists (e.g., "documents": [ {...}, ... ])
        case_records = sm.extract_records({k: v for k, v in b.case.items() if k not in ("output_contract",)}, path="case.json")
        if case_records:
            for r in case_records:
                r.path = r.path or "case.json"
            records.extend(case_records); b.record_files["case.json"] = len(case_records)
    if question_override:
        b.question = question_override
    if not b.question and b.task_md:
        b.question = _infer_question(b.task_md)
    try:
        b.question_lang, _, _ = lg.detect_language(b.question or b.task_md[:2000])
    except Exception:
        b.question_lang = "unknown"
    b.queries = queries
    b.criteria = criteria
    b.reference_sets = ref_sets
    b.snapshots = snapshots
    b.docs = docs
    # assign rids
    for i, r in enumerate(records):
        if not r.rid:
            r.rid = f"{r.path}#{i}"
        if not r.source and r.path in parsed:
            pass
    b.records = records
    b.contract, b.contract_source = find_contract(b.case, b.task_md, b.files, read_json, read_text)
    # ---- structured reference sets -> record list form
    for key, rs in list(ref_sets.items()):
        data = rs.get("data")
        if isinstance(data, list):
            recs = []
            for item in data:
                if isinstance(item, dict):
                    r = sm.parse_record_from(item, path=rs.get("file", ""), pointer=rs.get("pointer", ""))
                    if r is None and item:
                        # minimal identifier-like entries
                        ids = {k: item[k] for k in item if k.lower() in ("doi", "pmid", "title", "arxiv_id", "id", "number", "patent_number")}
                        if ids:
                            r = sm.parse_generic(ids, path=rs.get("file", ""), pointer=rs.get("pointer", ""))
                    if r is not None:
                        recs.append(r)
                elif isinstance(item, str):
                    m = sm.DOI_RE.search(item)
                    r = sm.Record(source="ref_string", doi=sm.norm_doi(m.group(0)) if m else "", title=sm.strip_html(item), path=rs.get("file", ""))
                    recs.append(r)
            rs["records"] = [r.to_dict() for r in recs]
        elif isinstance(data, dict):
            # graph with nodes/edges
            nodes = data.get("nodes") or data.get("vertices") or []
            edges = data.get("edges") or data.get("links") or []
            rs["nodes"] = nodes
            rs["edges"] = edges
            recs = []
            for n in nodes if isinstance(nodes, list) else []:
                if isinstance(n, dict):
                    r = sm.parse_record_from(n, path=rs.get("file", ""), pointer=rs.get("pointer", ""))
                    if r is not None:
                        recs.append(r)
            rs["records"] = [r.to_dict() for r in recs]
    b.extra["parsed"] = parsed
    return b


def _infer_question(task_md: str) -> str:
    # Take the first non-heading paragraph that looks like a request
    lines = [l.strip() for l in task_md.splitlines() if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("|") and not l.strip().startswith("-")]
    for l in lines:
        if re.search(r"(请|需要|找出|检索|search|find|identify|retrieve|which|how|what|列出|筛选|评估|分析|总结|回答|生成|整理)", l, re.I) and len(l) > 15:
            return l[:600]
    return (lines[0][:600] if lines else "")


def _patent_from_html(text: str, rel: str) -> Optional[sm.Record]:
    m = re.search(r"(US|EP|WO|CN|JP|KR)\s?(\d{2,4})?[-/\s]?(\d{6,})\s?([A-Z]\d?)", text[:20000])
    if not m:
        return None
    r = sm.Record(source="patent_html", path=rel, pub_type="patent")
    r.patent_number = m.group(0).strip()
    r.other_ids["patent_normalized"] = sm.norm_patent_number(r.patent_number)
    r.patent_kind = m.group(4) or ""
    # title: <meta name="DC.title">
    t = re.search(r'<meta name="DC\.title" content="([^"]+)"', text) or re.search(r"<title>([^<]+)</title>", text)
    if t:
        r.title = sm.strip_html(t.group(1))[:200]
    ab = re.search(r'<meta name="description" content="([^"]{20,})"', text)
    if ab:
        r.abstract = sm.strip_html(ab.group(1))[:2000]
    pd = re.search(r'<meta name="DC\.date" content="([^"]+)"', text) or re.search(r'itemprop="publicationDate"[^>]*content="([^"]+)"', text)
    if pd:
        r.date = pd.group(1)[:10]
        r.year = sm._year_from(r.date)
    au = re.findall(r'itemprop="inventor"[^>]*>([^<]+)<', text)
    r.authors = [sm.strip_html(a) for a in au][:10]
    asg = re.findall(r'itemprop="assigneeOriginal"[^>]*>([^<]+)<', text) or re.findall(r'itemprop="assigneeCurrent"[^>]*>([^<]+)<', text)
    r.applicants = [sm.strip_html(a) for a in asg][:10]
    pr = re.findall(r'itemprop="priorityDate"[^>]*content="([^"]+)"', text) or re.findall(r'itemprop="priorityDate"[^>]*>([^<]+)<', text)
    r.priority = [p.strip() for p in pr]
    ipc = re.findall(r'itemprop="Classifications"[^>]*>([^<]+)<', text) or re.findall(r'<meta name="DC\.subject" content="([^"]+)"', text)
    r.ipc = [sm.strip_html(x) for x in ipc][:12]
    # claims: <section itemprop="claims"> ... <div class="claim-text">
    cm = re.search(r'itemprop="claims"(.*?)</section>', text, re.S)
    if cm:
        r.claims = sm.norm_ws(re.sub(r"<[^>]+>", " ", cm.group(1)))[:6000]
    r.version = "granted" if (r.patent_kind or "").startswith("B") else "application"
    return r


def find_input_dir(explicit: str = "") -> str:
    cands = [explicit, "/input", os.path.join(os.getcwd(), "input"), os.path.join(os.getcwd(), "..", "input"), "/app/input", "/data/input"] if explicit else ["/input", os.path.join(os.getcwd(), "input"), "/app/input", "/data/input"]
    for c in cands:
        if c and os.path.isdir(c):
            return os.path.abspath(c)
    # any dir containing case.json under cwd (depth 3)
    base = os.getcwd()
    for root, dirs, files in os.walk(base):
        if root.count(os.sep) - base.count(os.sep) > 3:
            dirs[:] = []
            continue
        if "case.json" in files:
            return root
    return ""
