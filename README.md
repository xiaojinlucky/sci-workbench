# sci-workbench

An open-source collection of offline-executable, verifiable agent skills for end-to-end scientific research workflows.

Each skill module is packaged as self-contained UTF-8 text resources (zero runtime network dependency, zero symlinks) with:
- `SKILL.md`: declarative trigger conditions, step-by-step execution workflow, failure/degradation contracts, and explicit gap reporting.
- `scripts/`: offline Python 3.12 standard-library analysis, verification, and audit utilities.
- `references/`: domain methodology protocols, statistical/citation invariants, and structured output specifications.
- `examples/`: anonymized minimal input/output fixtures for reproducible local smoke testing.

## Domain Organization

| Research Domain | Directory | Core Capabilities |
| --- | --- | --- |
| Literature Search | `search/` | Query formulation, systematic screening, citation tracing, multi-database deduplication, cross-lingual & patent retrieval |
| Literature Reading | `reading/` | Document parsing, figure extraction, bilingual translation, grounded QA, multi-paper synthesis, math & method verification |
| Academic Writing | `writing/` | Section drafting, language polishing, template adaptation, citation management, peer-review rebuttal, LaTeX build, grant & proposal authoring |
| Knowledge & Library | `library/` | Research file indexing, local retrieval, automatic classification, knowledge-base QA, annotation synthesis |
| Deep Research | `research/` | Multi-step research synthesis, narrative & systematic reviews, frontier trend analysis, topic novelty assessment |
| Academic Review | `review/` | Manuscript quality audit, citation & claim verification, statistical/method review, argument logic & compliance checks |
| Data Analysis | `analysis/` | Data cleaning, statistical testing, reproducible analysis scripts, SQLite relational auditing, multi-step analytical pipelines |
| Scientific Graphics | `graphics/` | Publication-grade schematics, quantitative charts, biological pathway diagrams, experimental workflows, graphical abstracts |
| Academic Slides | `slides/` | Paper-to-slides conversion, slide authoring & editing, presentation narrative structuring, template alignment |
| Experiment Design | `experiment/` | Protocol generation, feasibility auditing, parameter optimization, comparative selection, anomaly attribution |

## Local Verification

Every module is designed to run deterministically in an isolated offline Python 3.12 container (`--network none`). Inspect the `SKILL.md` entry point inside each subdirectory for exact CLI invocations and output schemas.

## License

Released under the [Apache License 2.0](LICENSE).
