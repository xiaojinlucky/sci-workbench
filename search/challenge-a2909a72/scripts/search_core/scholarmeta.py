# -*- coding: utf-8 -*-
"""Unified scholarly record model + parsers for Crossref / EuropePMC / PubMed / OpenAlex /
Semantic Scholar / arXiv / PatentsView-style patent JSON / generic bibliographic dicts.

Pure standard library. Provenance is kept per record as a JSON pointer into the input file so
every downstream statement can be traced back to the exact source field.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional

WS = re.compile(r"\s+")
HTML_TAG = re.compile(r"<[^>]+>")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
ARXIV_RE = re.compile(r"(?:arxiv[:/\s]*)?(\d{4}\.\d{4,5})(v\d+)?", re.I)
PMID_RE = re.compile(r"^\d{5,9}$")
PATENT_NUM_RE = re.compile(r"\b(US|EP|WO|CN|JP|KR|DE|FR|GB|AU|CA|IN|BR|RU|ES|IT|SE|NL|CH|TW)\s?[-\s]?\s?(\d{2,4})?[-/\s]?(\d{3,})\s?([A-Z]\d?)?\b")


def norm_ws(s: Any) -> str:
    if s is None:
        return ""
    return WS.sub(" ", str(s)).strip()


def strip_html(s: Any) -> str:
    if s is None:
        return ""
    s = str(s)
    if "<" in s and ">" in s:
        s = re.sub(r"<math.*?</math>", " ", s, flags=re.S | re.I)
        s = HTML_TAG.sub(" ", s)
        s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#x27;", "'").replace("&nbsp;", " ")
    return norm_ws(s)


def norm_doi(d: Any) -> str:
    if not d:
        return ""
    d = str(d).strip()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    d = d.strip().strip(".").lower()
    return d


def norm_arxiv(a: Any) -> str:
    if not a:
        return ""
    m = ARXIV_RE.search(str(a))
    return f"{m.group(1)}{m.group(2) or ''}".lower() if m else ""


def arxiv_base(a: Any) -> str:
    s = norm_arxiv(a)
    return re.sub(r"v\d+$", "", s)


def norm_pmid(p: Any) -> str:
    if p is None:
        return ""
    s = str(p).strip()
    return s if PMID_RE.match(s) else ""


def _unicode_fold(s: str) -> str:
    # fold diacritics for matching only (keeps non-Latin scripts)
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_title(t: Any) -> str:
    if not t:
        return ""
    s = strip_html(t)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    s = _unicode_fold(s).lower()
    s = re.sub(r"[^\w\s\-]", " ", s, flags=re.U)
    s = WS.sub(" ", s).strip(" .-")
    return s


def title_tokens(t: Any) -> List[str]:
    n = norm_title(t)
    return [w for w in n.split() if len(w) > 1]


def norm_family(a: Any) -> str:
    s = strip_html(a)
    s = _unicode_fold(s).lower()
    s = re.sub(r"[^a-z\s\-']", " ", s)
    return WS.sub(" ", s).strip()


def author_family(author: Any) -> str:
    """Extract family name from many author shapes."""
    if author is None:
        return ""
    if isinstance(author, str):
        s = strip_html(author)
        if "," in s:
            return norm_family(s.split(",")[0])
        parts = s.split()
        return norm_family(parts[-1]) if parts else ""
    if isinstance(author, dict):
        for k in ("family", "lastName", "last_name", "surname", "name", "literal"):
            if author.get(k):
                v = author[k]
                if isinstance(v, str) and k == "name" and "," in v:
                    return norm_family(v.split(",")[0])
                return norm_family(v)
        for k in ("given", "firstName", "first_name"):
            if author.get(k):
                return norm_family(author[k])
    return ""


def author_display(author: Any) -> str:
    if isinstance(author, str):
        return strip_html(author)
    if isinstance(author, dict):
        fam = author.get("family") or author.get("lastName") or author.get("last_name") or author.get("surname") or ""
        giv = author.get("given") or author.get("firstName") or author.get("first_name") or ""
        if fam or giv:
            return norm_ws(f"{giv} {fam}").strip() if not author.get("family") else norm_ws(f"{fam}, {giv}").strip(", ")
        return strip_html(author.get("name") or author.get("literal") or author.get("text") or "")
    return ""


def _year_from(*vals) -> int:
    for v in vals:
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            if v and isinstance(v[0], (int, str)) and re.match(r"^(1[89]|20)\d{2}$", str(v[0])):
                return int(str(v[0])[:4])
            continue
        m = re.search(r"(1[89]\d{2}|20\d{2})", str(v))
        if m:
            return int(m.group(1))
    return 0


@dataclass
class Record:
    rid: str = ""
    source: str = ""
    path: str = ""            # file path relative to input dir
    pointer: str = ""         # JSON pointer / index inside the file
    title: str = ""
    abstract: str = ""
    authors: List[str] = field(default_factory=list)
    year: int = 0
    date: str = ""
    venue: str = ""
    doi: str = ""
    pmid: str = ""
    arxiv_id: str = ""
    other_ids: Dict[str, str] = field(default_factory=dict)
    pub_type: str = ""
    language: str = ""
    version: str = ""         # published | preprint | conference | unknown
    url: str = ""
    references: List[Dict[str, Any]] = field(default_factory=list)
    referenced_ids: List[str] = field(default_factory=list)
    citations_count: Optional[int] = None
    is_retracted: bool = False
    is_correction: bool = False
    indexed_date: str = ""
    updated_date: str = ""
    openalex_id: str = ""
    alt_dois: List[str] = field(default_factory=list)
    license: str = ""
    keywords: List[str] = field(default_factory=list)
    mesh: List[str] = field(default_factory=list)
    index_fields: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    # patent-specific
    patent_number: str = ""
    patent_kind: str = ""
    priority: List[str] = field(default_factory=list)
    applicants: List[str] = field(default_factory=list)
    ipc: List[str] = field(default_factory=list)
    claims: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d

    @property
    def id_key(self) -> str:
        return self.doi or self.pmid or arxiv_base(self.arxiv_id) or norm_title(self.title)[:120] or self.rid

    @property
    def first_author(self) -> str:
        return author_family(self.authors[0]) if self.authors else ""

    @property
    def text_for_search(self) -> str:
        parts = [self.title, self.abstract, " ".join(self.keywords), " ".join(self.mesh), self.venue]
        return " ".join(p for p in parts if p)

    def citation(self) -> str:
        a = self.authors[0] if self.authors else "Anon."
        if isinstance(a, str):
            a = a.split(",")[0] if "," in a else a.split()[-1:][0] if a.split() else a
        t = (self.title or "").strip()
        t = t if len(t) <= 140 else t[:137] + "..."
        return f"{a} ({self.year or 'n.d.'}). {t}. {self.venue}".strip()


def _walk(d: Any, pointer: str = "") -> Iterable[(str, Any)]:
    yield pointer, d
    if isinstance(d, dict):
        for k, v in d.items():
            yield from _walk(v, f"{pointer}/{k}")
    elif isinstance(d, list):
        for i, v in enumerate(d):
            yield from _walk(v, f"{pointer}/{i}")


def looks_like_record(d: Dict[str, Any]) -> bool:
    keys = set(d.keys())
    title_keys = {"title", "Title", "TI", "article_title", "articleTitle", "display_name", "display_name", "name", "patent_title", "invention_title"}
    score = 0
    if keys & title_keys:
        score += 2
    for k in ("DOI", "doi", "pmid", "PMID", "arxiv_id", "arxivId", "publisher", "container-title", "journal", "journalTitle", "abstract", "AB", "abstractText", "author", "authors", "inventor", "assignee", "publication_number", "priority_date", "year", "issued", "published", "DP"):
        if k in keys:
            score += 1
    return score >= 4


def parse_crossref_work(w: Dict[str, Any], path: str = "", pointer: str = "") -> Record:
    r = Record(source="crossref", path=path, pointer=pointer)
    r.title = strip_html((w.get("title") or [""])[0]) if isinstance(w.get("title"), list) else strip_html(w.get("title"))
    r.abstract = strip_html(w.get("abstract"))
    r.authors = [author_display(a) for a in (w.get("author") or []) if isinstance(a, dict)]
    r.doi = norm_doi(w.get("DOI"))
    issued = w.get("issued", {}).get("date-parts", [[]])[0] if isinstance(w.get("issued"), dict) else []
    pp = w.get("published-print", {}).get("date-parts", [[]])[0] if isinstance(w.get("published-print"), dict) else []
    po = w.get("published-online", {}).get("date-parts", [[]])[0] if isinstance(w.get("published-online"), dict) else []
    r.year = _year_from(pp, issued, po, w.get("created", {}).get("date-parts", [[]])[0] if isinstance(w.get("created"), dict) else [])
    r.date = "-".join(str(x) for x in (pp or issued or po)) if (pp or issued or po) else ""
    r.venue = strip_html((w.get("container-title") or [""])[0]) if isinstance(w.get("container-title"), list) and w.get("container-title") else ""
    r.url = w.get("URL") or ""
    r.license = "; ".join(str(l.get("URL")) for l in (w.get("license") or []) if isinstance(l, dict))
    t = (w.get("type") or "").lower()
    r.pub_type = t
    r.version = "preprint" if t == "posted-content" else "conference" if t == "proceedings-article" else "published" if t in ("journal-article", "book-chapter", "book", "report", "posted-content", "journal", "proceedings") else "unknown"
    refs = []
    for ref in (w.get("reference") or []):
        if isinstance(ref, dict):
            refs.append({"doi": norm_doi(ref.get("DOI")), "unstructured": strip_html(ref.get("unstructured")), "key": ref.get("key") or "", "year": _year_from(ref.get("year"))})
    r.references = refs
    cc = w.get("is-referenced-by-count")
    r.citations_count = int(cc) if isinstance(cc, (int, float)) else None
    rel = w.get("relation", {}) or {}
    if "has-preprint" in rel or "is-preprint-of" in rel:
        r.other_ids["relation"] = json_dumps(rel)[:400]
    r.index_fields = {"type": t, "publisher": w.get("publisher", ""), "volume": w.get("volume", ""), "issue": w.get("issue", ""), "page": w.get("page", ""), "article_number": w.get("article-number", ""), "subject": w.get("subject", [])[:5], "language": w.get("language", "")}
    r.language = w.get("language", "") or ""
    upd = w.get("update-to") or (w.get("updated-by") if isinstance(w.get("updated-by"), list) else None)
    r.is_retracted = bool(isinstance(w.get("update-to"), list) and any(isinstance(u, dict) and u.get("type") == "retraction" for u in w.get("update-to")))
    return r


def parse_europepmc_result(x: Dict[str, Any], path: str = "", pointer: str = "") -> Record:
    r = Record(source="europepmc", path=path, pointer=pointer)
    r.title = strip_html(x.get("title"))
    r.abstract = strip_html(x.get("abstractText"))
    r.authors = [strip_html(a.get("fullName")) for a in (x.get("authorList", {}).get("author", []) or []) if isinstance(a, dict) and a.get("fullName")]
    if not r.authors and x.get("authorString"):
        r.authors = [strip_html(a) for a in str(x.get("authorString")).split(",")]
    r.year = _year_from(x.get("pubYear"), x.get("firstPublicationDate"), x.get("electronicPublicationDate"))
    r.date = x.get("firstPublicationDate") or x.get("electronicPublicationDate") or str(x.get("pubYear") or "")
    r.venue = strip_html(x.get("journalInfo", {}).get("journal", {}).get("title") or x.get("bookOrReportDetails", {}).get("publisher") or x.get("source"))
    r.doi = norm_doi(x.get("doi"))
    r.pmid = norm_pmid(x.get("pmid"))
    r.arxiv_id = norm_arxiv(x.get("arxivId"))
    r.other_ids["pmcid"] = x.get("pmcid", "")
    src = (x.get("source") or "").upper()
    r.pub_type = (x.get("pubTypeList", {}).get("pubType", [src]) or [src])
    if isinstance(r.pub_type, list):
        r.pub_type = ";".join(r.pub_type)
    r.version = "preprint" if src == "PPR" or "preprint" in r.pub_type.lower() else "published" if src in ("MED", "PMC", "PAT") else "unknown"
    r.url = x.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url", "") if x.get("fullTextUrlList") else ""
    r.license = x.get("license", "")
    r.keywords = [strip_html(k.get("keyword")) for k in (x.get("keywordList", {}).get("keyword", []) or []) if isinstance(k, dict)]
    r.mesh = [strip_html(m.get("descriptorName")) for m in (x.get("meshHeadingList", {}).get("meshHeading", []) or []) if isinstance(m, dict)]
    r.citations_count = x.get("citedByCount") if isinstance(x.get("citedByCount"), int) else None
    r.language = (x.get("language") or "").lower()
    r.index_fields = {"source": src, "journal_volume": x.get("journalInfo", {}).get("volume", ""), "issue": x.get("journalInfo", {}).get("issue", ""), "pages": x.get("pageInfo", ""), "pubmodel": x.get("pubModel", ""), "isOpenAccess": x.get("isOpenAccess"), "hasPDF": x.get("hasPDF"), "hasReferences": x.get("hasReferences"), "hasTextMinedTerms": x.get("hasTextMinedTerms")}
    if x.get("commentCorrectionList"):
        r.other_ids["comment_correction"] = json_dumps(x.get("commentCorrectionList"))[:400]
        r.is_retracted = "retract" in json_dumps(x.get("commentCorrectionList")).lower()
    return r


def parse_pubmed_article(a: Dict[str, Any], path: str = "", pointer: str = "") -> Record:
    r = Record(source="pubmed", path=path, pointer=pointer)
    mc = a.get("MedlineCitation", {})
    art = mc.get("Article", {})
    r.title = strip_html(art.get("ArticleTitle"))
    r.abstract = " ".join(strip_html(x.get("AbstractText")) for x in (art.get("Abstract", {}).get("AbstractText", []) or []) if isinstance(x, dict))
    r.authors = [author_display({"family": au.get("LastName") or au.get("CollectiveName"), "given": au.get("ForeName") or au.get("Initials")}) for au in (art.get("AuthorList", {}).get("Author", []) or []) if isinstance(au, dict)]
    r.year = _year_from(art.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {}), art.get("Date"))
    r.venue = strip_html(art.get("Journal", {}).get("Title"))
    r.pmid = norm_pmid(mc.get("PMID"))
    r.doi = ""
    for aid in (a.get("PubmedData", {}).get("ArticleIdList", {}).get("ArticleId", []) or []):
        if isinstance(aid, dict):
            if aid.get("IdType") == "doi":
                r.doi = norm_doi(aid.get("value"))
            elif aid.get("IdType") == "pmc":
                r.other_ids["pmcid"] = aid.get("value", "")
            elif aid.get("IdType") == "arxiv":
                r.arxiv_id = norm_arxiv(aid.get("value"))
    r.pub_type = ";".join(strip_html(p) for p in (art.get("PublicationTypeList", {}).get("PublicationType", []) or []) if isinstance(p, dict))
    r.version = "preprint" if "preprint" in r.pub_type.lower() else "published"
    r.language = (art.get("Language") or [""])[0] if isinstance(art.get("Language"), list) and art.get("Language") else ""
    r.mesh = [strip_html(m.get("DescriptorName")) for m in (mc.get("MeshHeadingList", {}).get("MeshHeading", []) or []) if isinstance(m, dict)]
    r.keywords = [strip_html(k) for k in (art.get("KeywordList", {}).get("Keyword", []) or [])]
    return r


def parse_openalex_work(w: Dict[str, Any], path: str = "", pointer: str = "") -> Record:
    r = Record(source="openalex", path=path, pointer=pointer)
    r.title = strip_html(w.get("display_name") or w.get("title"))
    inv = w.get("abstract_inverted_index")
    if isinstance(inv, dict):
        pos = {}
        for word, idxs in inv.items():
            for i in idxs or []:
                try:
                    pos[int(i)] = word
                except Exception:
                    pass
        r.abstract = " ".join(pos[k] for k in sorted(pos))
    r.authors = [author_display({"family": au.get("author", {}).get("display_name")}) for au in (w.get("authorships", []) or []) if isinstance(au, dict) and isinstance(au.get("author"), dict)]
    r.year = _year_from(w.get("publication_year"), w.get("publication_date"))
    r.date = w.get("publication_date") or ""
    pl = w.get("primary_location") or {}
    src = pl.get("source") or {}
    r.venue = strip_html(src.get("display_name"))
    r.doi = norm_doi(w.get("doi"))
    ids = w.get("ids") or {}
    r.pmid = norm_pmid((ids.get("pmid") or "").rsplit("/", 1)[-1])
    r.url = w.get("id") or ""
    r.pub_type = (w.get("type") or "")
    r.version = "preprint" if r.pub_type == "preprint" else "published"
    r.referenced_ids = list(w.get("referenced_works", []) or [])
    r.citations_count = w.get("cited_by_count")
    oid = str(w.get("id") or "").rsplit("/", 1)[-1]
    if oid.startswith("W"):
        r.openalex_id = oid
    r.updated_date = str(w.get("updated_date") or "")[:10]
    r.indexed_date = str(w.get("created_date") or w.get("publication_date") or "")[:10]
    if w.get("is_retracted"):
        r.is_retracted = True
    r.language = w.get("language", "") or ""
    r.license = (w.get("primary_location", {}).get("license") or "")
    kw = [k.get("display_name") for k in (w.get("keywords", []) or []) if isinstance(k, dict)]
    r.keywords = [strip_html(k) for k in kw if k]
    concepts = [c.get("display_name") for c in (w.get("concepts", []) or []) if isinstance(c, dict) and (c.get("score") or 0) > 0.5]
    r.mesh = [strip_html(c) for c in concepts if c]
    r.is_retracted = bool(w.get("is_retracted"))
    r.index_fields = {"openalex_id": (w.get("id") or "").rsplit("/", 1)[-1], "type": w.get("type"), "is_oa": (w.get("open_access") or {}).get("is_oa")}
    return r


def parse_s2_paper(w: Dict[str, Any], path: str = "", pointer: str = "") -> Record:
    r = Record(source="semantic_scholar", path=path, pointer=pointer)
    r.title = strip_html(w.get("title"))
    r.abstract = strip_html(w.get("abstract"))
    r.authors = [author_display({"family": a.get("name")}) for a in (w.get("authors", []) or []) if isinstance(a, dict)]
    r.year = _year_from(w.get("year"), w.get("publicationDate"))
    r.venue = strip_html(w.get("venue") or w.get("journal", {}).get("name") if isinstance(w.get("journal"), dict) else w.get("venue"))
    ext = w.get("externalIds") or {}
    r.doi = norm_doi(ext.get("DOI") or w.get("externalIds", {}).get("DOI") if isinstance(w.get("externalIds"), dict) else w.get("doi"))
    r.doi = r.doi or norm_doi(w.get("doi"))
    r.pmid = norm_pmid(ext.get("PubMed"))
    r.arxiv_id = norm_arxiv(ext.get("ArXiv"))
    r.pub_type = w.get("publicationTypes", [""])[0] if isinstance(w.get("publicationTypes"), list) and w.get("publicationTypes") else ""
    r.version = "preprint" if "arxiv" in (r.pub_type or "").lower() or r.arxiv_id and not r.doi else "published"
    r.citations_count = w.get("citationCount")
    r.url = w.get("url", "")
    r.language = ""
    return r


def parse_arxiv_entry(e: Dict[str, Any], path: str = "", pointer: str = "") -> Record:
    r = Record(source="arxiv", path=path, pointer=pointer)
    r.title = strip_html(e.get("title"))
    r.abstract = strip_html(e.get("summary") or e.get("abstract"))
    raw_authors = e.get("authors", []) or []
    r.authors = [
        author_display({"family": a.get("name")}) if isinstance(a, dict) else author_display(a)
        for a in raw_authors
        if a
    ]
    r.year = _year_from(e.get("published"), e.get("updated"), e.get("year"))
    r.date = str(e.get("published") or e.get("year") or "")
    r.arxiv_id = norm_arxiv(e.get("arxiv_id") or e.get("entry_id") or e.get("id"))
    r.doi = norm_doi(e.get("doi"))
    if isinstance(e.get("alt_dois"), list):
        r.alt_dois = [norm_doi(d) for d in e["alt_dois"] if norm_doi(d)]
    r.pub_type = "preprint"
    r.version = strip_html(e.get("version") or "") or "preprint"
    r.venue = strip_html(e.get("venue") or e.get("journal_ref") or "arXiv")
    raw_id = str(e.get("id") or "")
    r.url = raw_id if raw_id.startswith("http") else (f"https://arxiv.org/abs/{r.arxiv_id}" if r.arxiv_id else "")
    for l in (e.get("links", []) or []):
        if isinstance(l, dict) and (l.get("rel") == "related") and l.get("href", "").startswith("http://dx.doi.org/https://doi.org/"):
            r.doi = r.doi or norm_doi(l.get("href"))
    r.keywords = [strip_html(c.get("term")) for c in (e.get("tags", []) or e.get("categories", []) or []) if isinstance(c, dict)]
    if isinstance(e.get("journal_ref"), str) and e["journal_ref"].strip():
        r.venue = e["journal_ref"]
        r.version = "published" if r.doi else "preprint"
    return r


PATENT_KIND_MEANING = {
    "A1": "application published", "A2": "application published (without search report)", "A": "application",
    "B1": "granted (no prior publication)", "B2": "granted (after publication)", "B": "granted",
    "U1": "utility model", "U": "utility model", "S": "design", "Y1": "utility (JP)", "Y2": "utility (JP)",
}


def norm_patent_number(raw: Any) -> Dict[str, str]:
    """Normalize many patent number shapes into country/series/number/kind."""
    if not raw:
        return {}
    s = norm_ws(str(raw)).upper().replace(",", "")
    m = re.match(r"^(US|EP|WO|CN|JP|KR|DE|FR|GB|AU|CA|IN|BR|RU|ES|IT|SE|NL|CH|TW)[\s\-]*((?:\d{2,4})?)[\s\-/]*((?:\d{3,})(?:\d)*)[\s\-]*([A-Z]\d?)?$", s)
    if not m:
        m2 = PATENT_NUM_RE.search(s)
        if not m2:
            return {"raw": s}
        country, series, num, kind = m2.group(1), m2.group(2) or "", m2.group(3), m2.group(4) or ""
    else:
        country, series, num, kind = m.group(1), m.group(2) or "", m.group(3), m.group(4) or ""
    return {"raw": s, "country": country, "series": series, "number": num, "kind": kind, "base": f"{country}{series}{num}" if country in ("US",) else f"{country}{num}", "canonical": f"{country}-{series}-{num}-{kind}".strip("-") if series else f"{country}-{num}-{kind}".strip("-"), "kind_meaning": PATENT_KIND_MEANING.get(kind, "unknown kind")}


def parse_patent(w: Dict[str, Any], path: str = "", pointer: str = "") -> Record:
    r = Record(source="patent", path=path, pointer=pointer)
    r.title = strip_html(w.get("patent_title") or w.get("invention_title") or w.get("title") or w.get("inventionTitle"))
    r.abstract = strip_html(w.get("abstract") or w.get("patent_abstract"))
    num = w.get("publication_number") or w.get("publicationNumber") or w.get("patent_number") or w.get("patentNumber") or w.get("id")
    pn = norm_patent_number(num)
    r.patent_number = pn.get("raw", str(num or ""))
    r.patent_kind = pn.get("kind", "")
    r.other_ids["patent_normalized"] = pn
    inv = w.get("inventors") or w.get("inventor") or w.get("inventor_list", {}).get("inventor") or []
    if isinstance(inv, dict):
        inv = [inv]
    r.authors = [strip_html((i.get("name") if isinstance(i, dict) else i) or (i.get("inventor_name") if isinstance(i, dict) else "")) for i in inv]
    r.applicants = [strip_html((a.get("name") if isinstance(a, dict) else a) or (a.get("assignee_name") if isinstance(a, dict) else "")) for a in (w.get("assignees") or w.get("assignee") or w.get("applicants") or [])]
    if isinstance(w.get("assignee"), str):
        r.applicants = [strip_html(w.get("assignee"))]
    dates = w.get("priority_date") or w.get("priorityDate") or (w.get("priority_claims") or [{}])[0].get("priority_date") if isinstance(w.get("priority_claims"), list) and w.get("priority_claims") else w.get("priority_date")
    pr = []
    for pc in (w.get("priority_claims") or w.get("priorityClaims") or []):
        if isinstance(pc, dict) and (pc.get("priority_number") or pc.get("number")):
            pr.append(norm_patent_number(pc.get("priority_number") or pc.get("number")).get("raw", str(pc.get("priority_number") or pc.get("number"))))
    if dates:
        pr.append(str(dates))
    r.priority = pr
    r.year = _year_from(w.get("grant_date") or w.get("grantDate") or w.get("publication_date") or w.get("publicationDate") or w.get("filing_date") or w.get("filingDate"))
    r.date = str(w.get("grant_date") or w.get("grantDate") or w.get("publication_date") or w.get("publicationDate") or "")
    r.pub_type = "patent"
    r.version = "granted" if (r.patent_kind or "").startswith("B") else "application" if (r.patent_kind or "").startswith("A") else "unknown"
    ipc = w.get("ipc") or w.get("IPC") or w.get("classifications") or w.get("classification") or []
    if isinstance(ipc, list):
        r.ipc = [strip_html((c.get("code") or c.get("ipc_code") or c.get("section") or c) if isinstance(c, dict) else c) for c in ipc]
    elif isinstance(ipc, str):
        r.ipc = [x.strip() for x in re.split(r"[;,]", ipc) if x.strip()]
    claims = w.get("claims") or w.get("claim_text") or w.get("claim")
    if isinstance(claims, list):
        claims = " ".join(strip_html(c.get("text") if isinstance(c, dict) else c) for c in claims)
    r.claims = strip_html(claims)[:6000]
    r.url = w.get("pdf_link") or w.get("url") or (f"https://patents.google.com/patent/{r.patent_number}/en" if r.patent_number else "")
    r.language = w.get("language", "") or ""
    return r


GENERIC_TITLE_KEYS = ("title", "Title", "TI", "article_title", "articleTitle", "display_name", "patent_title", "invention_title", "name")
GENERIC_ABSTRACT_KEYS = ("abstract", "Abstract", "AB", "abstractText", "abstract_text", "summary", "description")
GENERIC_YEAR_KEYS = ("year", "Year", "PY", "pubYear", "publication_year", "issued", "published", "date", "DP", "publication_date")
GENERIC_VENUE_KEYS = ("venue", "journal", "journalTitle", "container-title", "publicationTitle", "SO", "publisher")
GENERIC_DOI_KEYS = ("DOI", "doi", "Doi")
GENERIC_PMID_KEYS = ("pmid", "PMID")
GENERIC_ARXIV_KEYS = ("arxiv_id", "arxivId", "arxiv", "ArXiv")


def parse_generic(w: Dict[str, Any], path: str = "", pointer: str = "") -> Record:
    explicit_src = strip_html(w.get("source") or w.get("database") or "").lower()
    r = Record(source=explicit_src or "generic", path=path, pointer=pointer)
    for k in GENERIC_TITLE_KEYS:
        if w.get(k):
            v = w[k]
            r.title = strip_html(v[0] if isinstance(v, list) and v else v)
            break
    for k in GENERIC_ABSTRACT_KEYS:
        if w.get(k) and k not in ("description",) :
            r.abstract = strip_html(w[k])
            break
    if not r.abstract and w.get("description"):
        r.abstract = strip_html(w["description"])[:3000]
    au = w.get("authors") or w.get("author") or w.get("creators") or w.get("inventors") or []
    if isinstance(au, str):
        au = [a.strip() for a in re.split(r";|, (?=[A-Z])", au)]
    r.authors = [author_display(a) for a in au if a]
    for k in GENERIC_YEAR_KEYS:
        if w.get(k):
            r.year = _year_from(w[k]) or r.year
            if not r.date:
                r.date = str(w[k]) if not isinstance(w[k], (dict, list)) else ""
    for k in GENERIC_VENUE_KEYS:
        if w.get(k):
            v = w[k]
            if isinstance(v, list) and v:
                v = v[0]
            if isinstance(v, dict):
                v = v.get("title") or v.get("display_name") or v.get("name") or ""
            r.venue = strip_html(v)
            break
    for k in GENERIC_DOI_KEYS:
        if w.get(k):
            r.doi = norm_doi(w[k]); break
    if isinstance(w.get("alt_dois"), list):
        r.alt_dois = [norm_doi(d) for d in w["alt_dois"] if norm_doi(d)]
    for k in GENERIC_PMID_KEYS:
        if w.get(k):
            r.pmid = norm_pmid(w[k]); break
    for k in GENERIC_ARXIV_KEYS:
        if w.get(k):
            r.arxiv_id = norm_arxiv(w[k]); break
    r.pub_type = strip_html(w.get("type") or w.get("pub_type") or w.get("document_type") or w.get("source_type") or "")
    r.version = strip_html(w.get("version") or w.get("status") or "") or ("preprint" if "preprint" in r.pub_type.lower() or (r.arxiv_id and not r.doi) else "published" if r.doi else "unknown")
    r.url = strip_html(w.get("url") or w.get("link") or w.get("URL") or "")
    if w.get("references"):
        if isinstance(w["references"], list):
            r.references = [x if isinstance(x, dict) else {"unstructured": strip_html(x)} for x in w["references"]]
    if w.get("referenced_works"):
        r.referenced_ids = list(w["referenced_works"])
    r.citations_count = w.get("citation_count") if isinstance(w.get("citation_count"), int) else w.get("cited_by_count") if isinstance(w.get("cited_by_count"), int) else None
    r.language = (w.get("language") or w.get("lang") or "").lower() if isinstance(w.get("language") or w.get("lang"), str) else ""
    if w.get("keywords"):
        kw = w["keywords"]
        r.keywords = [strip_html(k.get("keyword") if isinstance(k, dict) else k) for k in kw] if isinstance(kw, list) else [strip_html(kw)]
    # patent fields if present
    if any(k in w for k in ("publication_number", "patent_number", "publicationNumber", "priority_date", "priorityDate", "inventor", "assignee")):
        p = parse_patent(w, path, pointer)
        if p.patent_number:
            r.patent_number, r.patent_kind, r.other_ids["patent_normalized"] = p.patent_number, p.patent_kind, p.other_ids["patent_normalized"]
            r.priority, r.applicants, r.ipc, r.claims = p.priority, p.applicants, p.ipc, p.claims
            r.source = "patent"
            r.pub_type = "patent"
            r.version = p.version
    return r


def parse_record_from(obj: Any, path: str = "", pointer: str = "") -> Optional[Record]:
    """Detect the flavor of a dict and parse it into a Record."""
    if not isinstance(obj, dict) or not obj:
        return None
    if "MedlineCitation" in obj:
        return parse_pubmed_article(obj, path, pointer)
    if "message" in obj and isinstance(obj["message"], dict):
        return parse_record_from(obj["message"], path, pointer + "/message")
    if "authorships" in obj and ("referenced_works" in obj or "cited_by_count" in obj or obj.get("id", "").startswith("https://openalex")):
        return parse_openalex_work(obj, path, pointer)
    if "externalIds" in obj or ("paperId" in obj and "authors" in obj):
        return parse_s2_paper(obj, path, pointer)
    if ("journalInfo" in obj) or ("pubTypeList" in obj) or ("authorString" in obj) or ("pmcid" in obj and "source" in obj):
        return parse_europepmc_result(obj, path, pointer)
    if "DOI" in obj and ("container-title" in obj or "title" in obj or "author" in obj or "issued" in obj):
        return parse_crossref_work(obj, path, pointer)
    if "publication_number" in obj or "patent_number" in obj or "patentNumber" in obj or ("inventor" in obj and "priority_date" in obj):
        return parse_patent(obj, path, pointer)
    if ("arxiv_id" in obj) or ("entry_id" in obj) or ("summary" in obj and "authors" in obj and "title" in obj):
        return parse_arxiv_entry(obj, path, pointer)
    if looks_like_record(obj):
        return parse_generic(obj, path, pointer)
    return None


def extract_records(obj: Any, path: str = "", pointer: str = "", max_records: int = 5000) -> List[Record]:
    """Recursively find record-like dicts in a parsed JSON/YAML/XML structure."""
    out: List[Record] = []
    seen = set()

    def rec(o, p):
        if len(out) >= max_records:
            return
        if isinstance(o, dict):
            r = parse_record_from(o, path, p)
            if r is not None and (r.title or r.patent_number):
                key = (r.title, r.doi, r.pmid, r.arxiv_id, r.patent_number)
                if key not in seen:
                    seen.add(key)
                    r.rid = f"{path}#{p}"
                    out.append(r)
                # still descend for nested lists (e.g., reference lists) but not into the same record again
                for k, v in o.items():
                    if k in ("reference", "references", "referenced_works", "abstract_inverted_index", "authorships"):
                        continue
                    rec(v, f"{p}/{k}")
            else:
                for k, v in o.items():
                    rec(v, f"{p}/{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                rec(v, f"{p}/{i}")

    rec(obj, pointer)
    return out


def json_dumps(o: Any) -> str:
    import json
    try:
        return json.dumps(o, ensure_ascii=False)
    except Exception:
        return str(o)
