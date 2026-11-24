# Federated Schema Mapping, Deduplication & Conflict Resolution (`challenge-5c918ff0`)

## 1. Cross-Database Field Mapping Matrix
| Unified Field | Crossref REST | OpenAlex | Europe PMC | PubMed | arXiv Atom XML |
|---|---|---|---|---|---|
| `title` | `title[0]` | `display_name` / `title` | `title` | `ArticleTitle` | `<title>` |
| `abstract` | `abstract` (JATS stripped; often absent) | `abstract_inverted_index` reconstructed | `abstractText` | `AbstractText` joined | `<summary>` |
| `authors` | `author[].{family,given}` | `authorships[].author.display_name` | `authorList` / `authorString` | `AuthorList` | `<author><name>` |
| `year` | `published-print` > `issued` > `published-online` | `publication_year` | `pubYear` / `firstPublicationDate` | `PubDate` | `<published>` |
| `venue` | `container-title[0]` | `primary_location.source.display_name` | `journalInfo.journal.title` | `Journal.Title` | `journal_ref` or `"arXiv"` |
| `doi` | `DOI` (lowercased) | `doi` (URL prefix stripped) | `doi` | `ArticleId[IdType=doi]` | `arxiv:doi` / `10.48550/arxiv.*` |
| `citations_count` | `is-referenced-by-count` | `cited_by_count` | `citedByCount` | Structurally absent (`null`) | Structurally absent (`null`) |
| `version` | `type=="posted-content"` -> `preprint`, else `published` | `type=="preprint"` -> `preprint`, else `published` | `source=="PPR"` -> `preprint`, `MED/PMC` -> `published` | `published` (unless preprint type) | `preprint` (or `published` if `journal_ref`+formal DOI) |

## 2. Missing vs Unknown Semantics
- **Populated**: Valid normalized value with `source:file#pointer` provenance.
- **Structurally Absent (`null`)**: Database schema does not provide the field (e.g., arXiv `citations_count`, Crossref publisher-withheld `abstract`). Never converted to `0` or empty string assertions.
- **Unavailable / Degraded Source (`null` fill rate)**: Database returned HTTP 429, timeout, parse error, or is absent from snapshot. Reported under `degraded_sources` with `ALL_UNAVAILABLE_SOURCE`, strictly separate from 0% field coverage.

## 3. 4-Tier Identity Resolution & Hard Anti-False-Merge Rule
1. **Tier 1 (Exact Strong ID)**: Identical normalized `doi`, `pmid`, or `arxiv_base(arxiv_id)`.
2. **Tier 2 (Preprint <-> VoR Bridge)**: Shared `alt_dois`, preprint DOI prefix (`10.48550/arxiv.*`, `10.1101/*`, `10.26434/*`, etc.) + formal DOI with `title_sim >= 0.88` and compatible first author, or arXiv ID <-> formal DOI with `title_sim >= 0.92` (`>= 0.85` with same first author).
3. **Tier 3/4 (Fuzzy Metadata)**: `title_sim >= 0.92` with `|year_diff| <= 1` or matching first author, OR `title_sim >= 0.82` with matching first author AND `|year_diff| <= 1`.
4. **Hard Distinct-Formal-DOI Guard**: When `r1.doi != r2.doi` AND neither DOI is a preprint prefix:
   - Auto-merge is **prohibited**.
   - If `title_sim >= 0.80`, emit `ambiguous:distinct_formal_dois` into `ambiguous_arbitration_queue.json` and keep both records as separate canonical entries.

## 4. Deterministic Field Conflict Resolution
For every conflicting field inside a merged cluster, candidate records are ranked by:
`key(r) = (version_rank, source_authority[field], field_richness)`
- `version_rank`: `published=4 > conference=3 > preprint=2 > unknown=0`
- `source_authority`:
  - `doi / title / year / venue`: `crossref=5, publisher=5 > openalex/europepmc/pubmed=4..3 > arxiv=2..1`
  - `abstract`: `europepmc=5, pubmed=5 > arxiv=4, openalex=4 > crossref=3`
  - `citations_count`: `openalex=5 > crossref=4, s2=4 > europepmc=3`
- `field_richness`: string/list length or presence indicator.
All raw conflicting values and their source pointers are preserved in `field_conflicts_resolved`.
