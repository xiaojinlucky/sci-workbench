# Cross-Lingual Terminology, Immutability & View Consolidation Protocol

This protocol defines deterministic rules that eliminate the highest-frequency failure modes in multilingual scientific literature search.

## 1. Original-Title Immutability Principle (Anti-Overwrite Invariant)

Reference managers and multilingual harvesters frequently overwrite a non-English primary title with an English translated title (or concatenate them destructively). In this Skill:

- Every ingested record preserves four distinct fields:
  - `raw_title`: Exact string extracted from that specific file/record.
  - `original_title`: Exact primary-language title of the work (never replaced by a translation).
  - `titles_by_language`: Language-keyed map (e.g., `{"pt": "...", "en": "..."}`) accumulating verified multilingual titles across views/metadata.
  - `translated_title`: Non-empty only when a verified translated title differs from `original_title`.
- `title_overwrite_audit` verifies that every emitted record retains byte-identical `raw_title` (`sha256` equality) and that `overwritten_count == 0`.

## 2. Strict 4-Class View Taxonomy & Identity Binding

Each supplied file or record belonging to a scholarly work is assigned a canonical `view_type`:

| `view_type` | Definition | Counts as New Work? | Full-Text Method Verification Allowed? |
| --- | --- | --- | --- |
| `original_fulltext` | Primary-language full text of the study | Yes (1 per canonical `work_id`) | Yes |
| `publisher_full_translation` | Full-text translation of the same DOI/PID in another language | No | Yes (with translation-layer attribution) |
| `english_abstract_only` | English abstract mirror without full body text | No | No (must flag `ABSTRACT_ONLY_LANGUAGE_VIEWS`) |
| `exact_duplicate` | Content-equivalent duplicate view in the same language | No | No (collapsed to original view) |
| `metadata_multilingual_record` | Structured bibliographic sidecar (e.g., SciELO ArticleMeta JSON) carrying multilingual titles/abstracts without full body text | No (enriches canonical work metadata) | No |

### Identity Binding & Homonym Separation Rules
1. **Normalized DOI Match**: Strongest work identifier (`https://doi.org/` stripped, lowercased, trailing punctuation stripped).
2. **Hard Distinct-DOI Barrier**: If two records carry **different non-empty normalized DOIs**, they MUST NEVER be merged—even if their titles are word-for-word identical or an upstream export misassigned them the same `work_id`. Every prevented merge is recorded in `homonym_distinct_doi_separated`.
3. **SciELO PID / Persistent ID Binding**: Standardized SciELO PIDs (`S\d{4}-\d{4}\d{13}`) bind multilingual views of the same paper when one view omits a DOI, subject to the distinct-DOI barrier.
4. **Content Digest Fallback**: Only when neither record carries a conflicting DOI/PID may identical normalized `(body_language, title, abstract, text)` content be flagged as `exact_duplicate`.

## 3. Diacritic Folding & Morphological Normalization

Non-English scientific terms frequently appear both with and without diacritics across PDFs, legacy exports, and user queries:

- Apply Unicode NFKD decomposition and strip combining marks (`ç->c`, `ã->a`, `õ->o`, `á->a`, `é->e`, `í->i`, `ó->o`, `ú->u`, `â->a`, `ê->e`, `ô->o`, `ñ->n`, `ü->u`, `ö->o`, `ä->a`).
- Match against both original UTF-8 text and diacritic-folded text while **always quoting the verbatim original UTF-8 sentence** in `evidence_anchors`.
- Examples: `intoxicações exógenas` <-> `intoxicacoes exogenas`, `pressões respiratórias máximas` <-> `pressoes respiratorias maximas`, `revisão sistemática` <-> `revisao sistematica`.

## 4. Zero-Translation Lock: Gene, Protein, Chemical & Standardized Symbols

Never translate or morphologically stem:
- **Gene / protein symbols**: `TP53`, `BCL2`, `HBB`, `BMP6`, `KL`, `VEGF`, `IL6`, `TNF`, `EGFR`, `BRCA1`, `CRISPR`.
- **Chemical formulas & alloys**: `CO2`, `H2O2`, `Fe3O4`, `TiO2`, `NaCl`, `LiFePO4`, `VO2+`.
- **Physiological / statistical symbols**: `PImax`, `PEmax`, `FEV1`, `FVC`, `BMI`, `HbS`, `HbF`.

Match these symbols with strict word boundaries (`(?<![A-Za-z0-9])TERM(?![A-Za-z0-9])`) and preserve canonical casing in reports.

## 5. Short Acronym Protection, False Friends & Pinyin Drift Checklist

### Short Acronyms (`<= 4` Latin characters)
Short acronyms MUST use strict token-boundary matching (never substring matching) and emit `false_friend_or_acronym_risk` when ambiguous:
- `SC`: Sickle Cell / Subcutaneous / Supercapacitor / Stem Cell / Santa Catarina.
- `PD`: Parkinson's Disease / Pharmacodynamics / Peritoneal Dialysis / Potential Difference.
- `MI`: Myocardial Infarction / Mutual Information / Maximal Inspiratory.
- `OR`: Odds Ratio / Operating Room / Logical OR.
- `SCD`: Sickle Cell Disease / Sudden Cardiac Death / Subjective Cognitive Decline.
- `AVN`: Avascular Necrosis / Atrioventricular Node.

### Multilingual False Friends (Require Back-Translation Verification)
| Term | English Meaning | Non-English Trap | Verification Rule |
| --- | --- | --- | --- |
| `data` | Scientific dataset / observations | PT/IT/ES `data` = calendar date | Check context for date patterns vs measurement/dataset terms |
| `actual` / `atual` | Real, factual | PT `atual` / ES `actual` = current/present | Back-translate as "current", not "actual/real" |
| `pretend` / `pretender` | Feign, simulate | PT/ES `pretender` = intend/aim to | Back-translate as "intend/aim" |
| `resumo` / `resume` | Curriculum vitae or restart | PT `resumo` / ES `resumen` = abstract/summary | Map strictly to `abstract` |
| `parente` / `parent` | Mother/father | PT `parente` = relative (kin) | Verify kinship vs parental role |
| `sensible` | Reasonable, prudent | ES/FR `sensible` = sensitive | Map to `sensitive` in experimental/clinical assays |
| `intoxicação` | Poisoning / toxic exposure | Colloquial EN "intoxication" (alcohol) | In pediatrics/toxicology, anchor to `exogenous poisoning / intoxication` |

### Pinyin & Proper-Noun Transliteration Drift
When queries include Chinese/Japanese/Korean author surnames, institutions, or regional place names romanized without tone marks (e.g., `Li`, `Shi`, `He`, `Ma`, `Can`, `Fan`, `Jin`, `Tang`) or phonetic loanwords, flag `pinyin_or_transliteration_drift_risk` and require co-occurrence with a domain anchor term or persistent identifier before accepting a hit.
