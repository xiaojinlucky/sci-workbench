# Long-Document Continuity, Multi-Chapter Synthesis & Tool-Boundary Protocol

Consult this reference when reconciling multi-chapter conflicts, drafting a thesis general discussion across compiled papers, auditing citation fields, or planning surgical revisions.

## 1. Manuscript State Separation

Classify manuscript information into five distinct layers so superseded wording or process notes never leak back into scientific prose:

- **Original source baseline**: Imported read-only chapter bytes (`v0` SHA-256, plus original binary SHA-256 for `.docx`/`.pdf` sources). Primary source for numbers, experimental conditions, and contradictions.
- **Editorial annotations & authorized replacements**: Clearly tagged annotations (`--mode annotate`) leave original text byte-identical; substantive paragraph rewrites (`--mode replace`) are isolated to authorized anchors with full before/after diffs and reversible snapshots.
- **Verified cross-chapter registry**: Unified terminology, acronym expansions, symbol definitions, figure/table labels, `(entity, unit, value, condition)` ledger, and `claim_support_matrix` across all inspected chapters.
- **Open scientific conflicts**: Contradictory values under matching conditions, ambiguous units, or unmapped citations present in source material. Keep untouched in source text and surface explicitly for author resolution.
- **Process receipts**: Import receipts, proposal decisions, rollback/reapply SHA-256 parity proofs, advisor comment action maps, and deliberate fault-injection logs.

## 2. Multi-Paper Thesis General Discussion (Anti-Duplication Synthesis)

A common failure in paper-compilation theses is copying discussion paragraphs from individual chapters into the final General Discussion or forcing an artificial narrative thread not supported by the chapters.

- **Map functional distribution first**: Each individual chapter owns its local mechanism and experimental detail; the General Discussion owns only cross-chapter synthesis:
  1. Convergent findings across chapters (citing exact chapter anchors and conditions).
  2. Divergent or condition-dependent outcomes (explaining differences in model, material, scale, or protocol rather than smoothing over contradictions).
  3. Cross-chapter methodological boundaries and concrete next validation steps.
- **Press-release clarity without defensive clutter**: Lead each synthesis paragraph with the substantiated cross-chapter claim and evidence anchor. Avoid opening key result paragraphs with self-undermining fillers ("unfortunately", "merely preliminary") when the sentence only wards off criticism rather than stating an actual experimental boundary; place real methodological boundaries directly where they constrain interpretation.
- **De-duplication check**: Inverted-index 3-gram bilingual shingle matching (`--dup-threshold 0.45`) scales linearly on 50,000+ word theses. Paragraphs with `Jaccard >= 0.65` across chapters fail as high-overlap copy-paste; `0.45–0.64` triggers a synthesis refactor warning.

## 3. Capability Boundary Comparison vs Existing Tools

| Capability / Failure Mode | `git diff` / `LaTeXdiff` | Word Track Changes | Zotero / EndNote | Generic LLM / Base Agent | This Skill (`longdoc_*`) |
| --- | --- | --- | --- | --- | --- |
| Byte-exact workorder scope firewall (block edits to unrequested chapters/blocks) | No (shows diff only after mutation) | No (author can edit anywhere) | N/A | Unreliable (frequently edits adjacent paragraphs) | **Enforced pre-write** (`BLOCKED_MISSING_TARGETS` / `UNAUTHORIZED_SCOPE`) |
| Deliberate fault-injection zero-mutation proof | No | No | No | Never verified | **Built-in 5-case `fault-test` with SHA-256 parity** |
| Domain-agnostic `(entity, unit, value, condition)` cross-chapter conflict ledger | No | No | No | Misses scattered numeric drift in 60KB+ manuscripts | **Deterministic SI + CN unit + condition-bound scanner** |
| Linear-time bilingual cross-chapter duplicate discussion audit | Line-level diff only | No | No | Loses recall past long context window | **Inverted-index 3-gram bilingual shingle Jaccard** |
| Flattened citation fields (`ADDIN ZOTERO_ITEM`, `[?]`) & `claim_support_matrix` | No | No (often causes flattening) | Corrupts on Word/Docs round-trip | Frequently overlooks silent field corruption | **Detects artifacts + unmapped `[N]` + claim-citation matrix** |
| Multi-format offline binary reading (`.docx`/`.pdf`) | `.tex`/text only | `.docx` only | App-dependent | Requires manual export | **Native `.md/.txt/.tex/.docx/.pdf` stdlib pipeline** |
| Binary `.docx` native Track Changes XML export | No | **Yes (native)** | N/A | No | Out of stdlib scope (exports editable `.md` + `.diff` + HTML preview) |

## 4. Cross-Chapter Consistency Dimensions

1. **Terminology & acronyms**: One canonical expansion per acronym across all chapters; flag conflicting expansions.
2. **Symbols & units**: Track variable/symbol bindings (`α`, `λ`, `T_g`, `σ_y`); flag incompatible cross-chapter definitions.
3. **Domain-agnostic `(entity, unit, value, condition)` & sample sizes**: Compare same-entity measurements under matching condition tags (e.g., same year, subgroup, arm, temperature, example ID) across chapters without cross-condition false positives.
4. **Numbering & cross-references**: Verify every `Fig. X.Y`, `Table X.Y`, `Eq. (X.Y)`, `图 X.Y`, `表 X.Y` resolves to a caption/label and flag sequence gaps.
5. **Citation field integrity & `claim_support_matrix`**: Detect mixed citation styles, unmapped `[N]` indices, flattened Zotero/EndNote artifacts, and bind each cited claim to its file/line/bibliography entry.
6. **Coverage, truncation & chapter planning**: Compare inspected chapters against TOC/numbering, flag abrupt endings, and validate or generate `chapter_plan.json` covering `research_question`, `methods`, `results`, `discussion`, and `limitations`.

## 5. Long-Document Pre-Mortem Risk Matrix

| Risk ID | Failure Mode | Early Signal | Built-In Prevention | Recovery Trigger |
| --- | --- | --- | --- | --- |
| R1 | Unintended edits to non-target chapters or original sentences | SHA-256 mismatch on unrequested files | `longdoc_revision_engine.py` pre-write scope check + `longdoc_chapter_consistency.py --baseline` | `rollback --to-version v0` then `reapply` valid proposals |
| R2 | Non-standard anchor naming (`SEC2-04`, `§3.1`, anchorless `.docx`/`.pdf`) causes silent scope bypass | `missing_workorder_targets > 0` | Dynamic anchor template induction + strict `BLOCKED_MISSING_TARGETS` gate (zero silent passthrough) | Inspect `import_receipt.json.anchor_index` and re-align targets |
| R3 | Cross-chapter conflict scanner misses non-engineering domains or false-alerts across subgroups | Generic SI units skipped or 2007 vs 2015 flagged | Domain-agnostic SI+CN unit scanner + ±75-char condition-phrase binder | Review `condition` field in `entity_value_conflicts` |
| R4 | Large thesis (`50k+` words) stalls `O(n²)` duplicate detection | Slow audit runtime | Inverted-index shingle Jaccard (`O(n)` average) with `dedup_performance.runtime_ms` logging | Tune `--dup-threshold` if needed |
| R5 | Invalid-operation test corrupts working state | Post-fault SHA-256 differs from pre-fault | Dedicated `fault-test` with pre/post full-tree SHA-256 assertion and `deliberate_fault_injection` tag | Abort export and inspect state isolation |
