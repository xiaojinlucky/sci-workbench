# Temporal Accounting, Terminology Drift & Representative Selection Protocol

## 1. Date-Basis Precedence & Sensitivity
- Precedence on canonical deduplicated work: `published` (`published-print` / journal `issued`) > `online_first` (`published-online` / `electronicPublicationDate`) > `preprint` (`arXiv` / `bioRxiv` / `posted-content`) > `unknown`.
- Never use Crossref `created` deposit date or an incidental year mentioned in a paper's reference list as publication year.
- Always compare VoR-preferred yearly counts with preprint-first-appearance counts (`preprint_vs_vor_sensitivity_timeline`) when preprints are present.

## 2. Terminology Drift Guard
- Before declaring a keyword "newly emerging", check `alias_merges_applied` (user `--aliases-json` + automatic in-corpus acronym-expansion detection).
- Report genuine methodological or conceptual shifts alongside exact transition records and quotes.

## 3. Balanced Representative Selection
- Raw citation ranking biases heavily toward old reviews and misses recent or long-tail methodological breakthroughs.
- Always report all four roles (`foundational_origin`, `methodological_pioneer_long_tail`, `phase_transition_bridge`, `recent_frontier_anchor`) with explicit selection criteria and `material_access` disclosure.
