# Academic Polishing Playbook: Register, Minimal Edits & Guardrails

## 1. Register Calibration Matrix

| Target Register | Voice & Person | Sentence Rhythm | Key Pitfalls to Eliminate |
| --- | --- | --- | --- |
| `journal_article` | Objective 3rd person; passive permitted in `Methods`, active crisp verbs in `Results` | Tight, high information density; topic-stress flow | Promotional adjectives (`groundbreaking`, `pivotal`), causal escalation of correlations |
| `thesis` | Formal scholarly tone, consistent cross-chapter definitions | Clear transitional bridges across multi-chapter arguments | Redundant abbreviation re-definitions, cross-chapter term drift |
| `grant_proposal` | Confident, feasibility-anchored 1st-person plural (`we will`) | Problem → Gap → Specific Aim → Measurable Deliverable | Self-undermining hedges in aims, ungrounded hype without preliminary anchors |
| `rebuttal` | Respectful, evidence-bound, direct response to each critique | Point-by-point: acknowledgment → exact change + location | Defensive tone, vague promises without concrete section/line anchors |
| `abstract` | Ultra-compact four-beat arc (Problem → Gap → Approach → Key Finding) | Zero filler clauses; quantitative headline with cohort/stat bounds | Hollow temporal openers (`In recent years...`), undefined acronyms |

## 2. Minimal-Edit Decision Hierarchy

1. **Preserve First**: If a sentence is grammatically valid, idiomatically academic, and consistent with the glossary, make **zero edits**.
2. **Surgical Syntax Fix**: Correct hard errors (subject-verb agreement, missing articles, tense drift between `Methods` past tense and established-fact present tense, redundant `Although ... but ...`) without restructuring clauses unnecessarily.
3. **Terminology & Abbreviation Unification**: Replace non-canonical synonyms or inconsistent hyphenation (`RNA seq` / `RNASeq` → `RNA-seq`), define non-universal abbreviations once at first appearance, and flag conflicting glossary entries as `needs_author_confirm`.
4. **Whitelist De-AI & Anti-Translationese**: Remove only concrete, verifiable defects (contrarian foils, corporate buzzwords, literal English-to-Chinese calques like `折叠变化`, `抗原呈递`, `吞噬救赎`, `统计平局`).
5. **Press-Release Anti-Defensive Check**: In `Abstract` and primary `Results`, strip self-defeating qualifiers (`遗憾的是`, `仅是一个初步尝试`, `unfortunately, our`) that weaken verified findings without adding scientific scope; keep honest boundaries in `Limitations`.

## 3. False-Positive Grammar Guardrails

Automated grammar checkers frequently generate scholarly false positives (e.g., LanguageTool Issue #12061 flagging valid `Although ..., ...` sentences or style linters flagging standard `Methods` passive voice).

- **Valid Concessive Clause**: `Although <subordinate clause with finite verb>, <main clause with finite verb>.` → **Do NOT rewrite or remove `Although`**. Log as `status: rejected_false_positive` with `LT_12061_CONCESSIVE_GUARD`.
- **True Concessive Error**: `Although <clause>, but <clause>.` → **Fix** by removing `but` while keeping `Although` and all scientific qualifiers intact.
- **Standard Methods Passive**: `Samples were centrifuged at 12,000 × g for 10 min.` → **Preserve**; log any style-linter active-voice push as `rejected_false_positive`.

## 4. Modality & Causal Strength Conservation Table

| Source Epistemic Level | Allowed Polishing Equivalents | Forbidden Escalations (Hard Block) |
| --- | --- | --- |
| `may be associated with` / `might correlate with` | `may be linked to`, `appears associated with`, `shows an association with` | `proves`, `causes`, `drives`, `directly triggers`, `establishes causality` |
| `suggests` / `indicates a trend toward` | `points toward`, `is consistent with` | `conclusively demonstrates`, `confirms`, `definitively shows` |
| `no significant difference (p = 0.24)` | `did not differ significantly (p = 0.24)` | `showed a slight improvement`, dropping `no` or altering `p = 0.24` |
| `in the high-dose cohort (n = 42)` | exact cohort and `n = 42` retained | generalizing to `in all patients` or omitting `n = 42` |
