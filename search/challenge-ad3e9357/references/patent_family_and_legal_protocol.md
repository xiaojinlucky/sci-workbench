# Patent Family Definitions, WIPO ST.16 Kind Codes, CPC Escaping & Legal Boundaries

## 1. WIPO ST.16 Kind Code Reference Table

Never conflate published applications with granted patents or utility models.

| Kind Code | Canonical Stage | Typical Meaning Across Major Offices (US / EP / WO / CN / JP / NL) |
| --- | --- | --- |
| `A`, `A0` | `published_application` | First-level published patent application (e.g., CN invention pub `CN...A`, AU provisional `A0`) |
| `A1` | `published_application` | Published application with search report (US pre-grant pub, EP A1, WO PCT A1) |
| `A2` | `published_application` | Published application without search report (US/EP/WO A2) |
| `A3` | `published_application` | Separate publication of search report (EP/WO A3) |
| `A4`, `A8`, `A9` | `reissue_or_correction` | Amended/supplementary/corrected application publication (same base number as A1; distinct publication document, same DOCDB family) |
| `B`, `B1`, `B2` | `grant` | Granted patent specification (CN `B`, EP/US/NL `B1`, US/EP/AU `B2`) |
| `C`, `C1`, `C2` | `grant` | Granted patent or reexamination certificate (CA `C`, DE/US `C1/C2`) |
| `U`, `U1`, `Y` | `utility_model` | Utility model publication/grant (CN `U`/`Y`, DE `U1`) — distinct examination rigor from invention patents |
| `E`, `E1` | `reissue_or_correction` | Reissue patent (e.g., US reissue) |
| `T3` | `translation_grant` | National translation of granted European patent (e.g., ES `T3`) |

## 2. Mathematical Definitions: Publication Documents vs DOCDB Simple vs INPADOC Extended Families

This distinction directly resolves Espacenet/commercial count discrepancies (e.g., 118 publication documents vs 71 families):

1. **Publication Document Count ($N_{\text{pub}}$)**:
   - Number of distinct normalized `(jurisdiction, serial_number, kind_code)` tuples in the corpus.
   - Example: `CN117374351A` (published application) and `CN117374351B` (granted patent) count as **2** publication documents, as do `WO2010068979A1` and `WO2010068979A4`.

2. **DOCDB Simple Patent Family ($N_{\text{DOCDB}}$)**:
   - Let $P(d)$ be the normalized set of priority application numbers claimed by document $d$.
   - Two documents $d_1, d_2$ belong to the same **DOCDB simple family** if and only if they claim the **exact same priority set**:
     $$d_1 \sim_{\text{DOCDB}} d_2 \iff P(d_1) = P(d_2) \neq \emptyset$$
   - (When priority metadata is absent in a finite snapshot, documents sharing the exact same `base_number` such as `A1`/`A4` or `A`/`B` fall in the same simple family; documents lacking links remain singletons.)
   - Example: Patent X with $P(X) = \{P_1\}$ and continuation-in-part Patent Y with $P(Y) = \{P_1, P_2\}$ have $P(X) \neq P(Y)$ and therefore form **2 distinct DOCDB simple families**.

3. **INPADOC Extended Patent Family ($N_{\text{INPADOC}}$)**:
   - Construct an undirected graph $G = (V, E)$ over corpus documents $V$, placing an edge $(d_1, d_2) \in E$ whenever $P(d_1) \cap P(d_2) \neq \emptyset$ OR one document references the other's application number as a parent/continuation/divisional OR lists the other in observed family tables.
   - **INPADOC extended families** are the connected components of $G$ (computed via Union-Find).
   - Example: Patent X ($P(X)=\{P_1\}$) and Patent Y ($P(Y)=\{P_1, P_2\}$) share $P_1 \in P(X) \cap P(Y)$, so they merge into **1 INPADOC extended family**.
   - **Hard Invariant**: $N_{\text{INPADOC}} \le N_{\text{DOCDB}} \le N_{\text{pub}}$. Observed snapshot links are strictly labeled `observed_links_only_not_exhaustive: true` and NEVER equate claim scope across members (`equal_claim_scope_assumed: false`).
   - **Zero Keyword-Based Family Merging**: Two patents sharing technical keywords or CPC codes without any priority/continuation/family edge MUST remain in distinct families.

## 3. CPC/IPC Normalization & Slash-Escaping Standard (`pyUSPTO #101`)

Patent classification codes contain a forward slash (`/`) separating main group and subgroup (e.g., `H01M 10/0525`, `C25B 11/04`). Unescaped slashes break Lucene/Solr/USPTO API parsers (interpreted as regex delimiters or syntax errors).

| Canonical Code | Quoted USPTO / API Safe Form | Lucene / Solr Escaped Form | Espacenet CQL Form |
| --- | --- | --- | --- |
| `H01M 10/0525` | `cpc:"H01M 10/0525"` | `cpc:(H01M10\/0525)` | `cpc="H01M 10/0525"` |
| `C25B 11/04` | `cpc:"C25B 11/04"` | `cpc:(C25B11\/04)` | `cpc="C25B 11/04"` |

Always separate **patent search queries** (CPC/IPC + claim structural nouns + kind/jurisdiction filters) from **scientific literature queries** (strip CPC/IPC codes and patent numbers so PubMed/OpenAlex/Crossref searches do not suffer zero-recall).

## 4. Source Authorization Tiers (Green / Yellow / Red) & Degradation Matrix

| Tier | Source Examples | Permitted Actions | Required Disclosures / Degradation |
| --- | --- | --- | --- |
| **Green** | Supplied local official facsimiles (USPTO/EP/WO PDFs), authorized open patent datasets, open-access scientific papers | Full bibliographic parsing, dual family computation, source-located claim analysis | Cite exact `source_file`, `claim_version_source`, and snapshot boundary |
| **Yellow** | Secondary aggregators (e.g., Google Patents HTML snapshots, dated CSV/JSON exports) | Discovery, classification extraction, observed priority/family links | Mark `current_official_legal_status = UNKNOWN`; retain `observed_snapshot_legal_status` + `legal_status_snapshot_date`; flag OCR caveats |
| **Red** | Paywalled commercial patent databases (Derwent/PatSnap/Orbit without license), confidential internal disclosures | **PROHIBITED** from scraping, login bypass, upload, or full-text display | Record explicit authorization gap; degrade to authorized Green/Yellow metadata only |

## 5. Non-FTO & Non-Legal-Advice Boundary

This Skill performs technical and bibliographic information retrieval only. It MUST NEVER render Freedom-to-Operate (FTO) clearance, infringement conclusions, invalidity verdicts, or patent valuation opinions. All claim comparisons are informational and require qualified patent counsel plus live official patent register verification.
