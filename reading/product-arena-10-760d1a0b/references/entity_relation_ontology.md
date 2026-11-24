# Entity & Relation Ontology + Co-Occurrence Firewall

## 1. Scientific Entity Categories (Paper-Scoped by Default)

| Category | Examples | Paper-Scoped ID Rule |
|---|---|---|
| `material_or_entity` | Catalysts, compounds, genes, proteins, receptors, biomarkers (`Cu-ZnO`, `PD-L1`, `TP53`, `perovskite`) | `sha256(source_id + category + canonical_name)` |
| `method_or_model` | Assays, spectroscopy, ML architectures (`DFT`, `XPS`, `scRNA-seq`, `SciBERT`, `AlphaFold2`, `XGBoost`) | Same paper-scoped hash |
| `task_or_domain` | Scientific problem or reaction (`CO2 hydrogenation`, `protein folding`, `relation extraction`) | Same paper-scoped hash |
| `dataset_or_cohort` | Benchmarks or cohorts (`TCGA-BRCA`, `PubMedQA`, `SciFact`, `MIMIC-IV`, `clinical cohort`) | Same paper-scoped hash |
| `metric_or_endpoint` | Measurements (`Faradaic efficiency`, `methanol selectivity`, `AUROC`, `F1-score`, `IC50`) | Same paper-scoped hash |

## 2. Controlled Directed Relations vs Isolated Co-Occurrence

- **Directed Evidence Relations (`directed: true`)**:
  `uses`, `evaluates_on`, `outperforms`, `compares_with`, `supports`, `contradicts`, `increases`, `decreases`, `inhibits`, `activates`, `associated_with`, `causes_direct`.
- **Causal Claim Guard**: `causes_direct` triggers ONLY on explicit direct causal wording (`directly causes`, `causally drives`, `直接导致`). Observational correlation stays `associated_with`.
- **Undirected Co-Occurrence Firewall (`directed: false`)**:
  When two entities appear in the same sentence without an explicit intervening predicate (or separated by >90 characters), they are recorded ONLY in `co_occurrences` as `co_occurs_with_no_causal_claim`. They are NEVER inserted into `relations`.
- **Attribution Separation**: Sentences containing citation markers (`et al., 2022`, `[14]`, `previous studies`) are tagged `attribution: "cited_secondary"` rather than `author_reported`.
- **Held-for-Review Gate**: Sentences with negation or hedging (`not`, `may`, `suggests`, `might`, `未见`, `可能`) or missing `paragraph_id` are routed to `held_for_review` instead of emitting unverified directed edges.
