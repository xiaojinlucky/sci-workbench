# Long-Document Continuity, Multi-Chapter Synthesis & Pre-Mortem Protocol

Consult this reference when reconciling multi-chapter conflicts, drafting a thesis general discussion across compiled papers, auditing citation fields, or planning surgical revisions.

## 1. Manuscript State Separation

Classify manuscript information into five distinct layers so superseded wording or process notes never leak back into scientific prose:

- **Original source baseline**: Imported read-only chapter bytes (`v0` SHA-256). Primary source for numbers, experimental conditions, and contradictions.
- **Editorial annotations**: Clearly tagged additions attached to authorized anchors (`target_ids`). Explain scientific conditions, limitations, or cross-chapter links without rewriting source wording.
- **Verified cross-chapter registry**: Unified terminology, acronym expansions, symbol definitions, figure/table labels, and entity-value ledger across all inspected chapters.
- **Open scientific conflicts**: Contradictory values, ambiguous units, or unverified claims present in the source material. Keep untouched in source text and surface explicitly for author resolution.
- **Process receipts**: Import receipts, proposal decisions, rollback/reapply hashes, and deliberate fault-injection logs.

## 2. Multi-Paper Thesis General Discussion (Anti-Duplication Synthesis)

A common failure in paper-compilation theses is copying discussion paragraphs from individual chapters into the final General Discussion or forcing an artificial narrative thread not supported by the chapters.

- **Map functional distribution first**: Each individual chapter owns its local mechanism and experimental detail; the General Discussion owns only cross-chapter synthesis:
  1. Convergent findings across chapters (citing exact chapter anchors and conditions).
  2. Divergent or condition-dependent outcomes (explaining differences in model, material, scale, or protocol rather than smoothing over contradictions).
  3. Cross-chapter methodological boundaries and concrete next validation steps.
- **Press-release clarity without defensive clutter**: Lead each synthesis paragraph with the substantiated cross-chapter claim and evidence anchor. Avoid opening key result paragraphs with self-undermining fillers ("unfortunately", "merely preliminary") when the sentence only wards off criticism rather than stating an actual experimental boundary; place real methodological boundaries directly where they constrain interpretation.
- **De-duplication check**: Any paragraph sharing high shingle overlap (Jaccard >= 0.45) with an earlier chapter must be refactored into higher-level synthesis with explicit back-references instead of verbatim repetition.

## 3. Cross-Chapter Consistency Dimensions

Check all six dimensions systematically and record exact chapter/anchor coordinates for every discrepancy:

1. **Terminology & acronyms**: One canonical expansion per acronym across all chapters; flag conflicting expansions and unexpanded specialist abbreviations.
2. **Symbols & units**: Track variable/symbol bindings (`α`, `λ`, `T_g`, `σ_y`); flag when the same symbol acquires incompatible meanings or units across chapters.
3. **Same-entity values & sample sizes**: Compare repeated mentions of the same cohort, material, specimen count (`n = ...`), or primary metric across chapters. Flag numerical conflicts without silently harmonizing them unless an authoritative source table resolves the discrepancy.
4. **Numbering & cross-references**: Verify every `Fig. X.Y`, `Table X.Y`, `Eq. (X.Y)`, `图 X.Y`, `表 X.Y` resolves to a caption/label and check sequence gaps (e.g., `Fig. 2.1` jumping to `Fig. 2.3`).
5. **Citation field & reference-list integrity**: Detect mixed citation styles within one manuscript, numeric citations `[N]` exceeding the reference list count or lacking bibliography entries, and flattened reference-manager artifacts (`{ADDIN ZOTERO_ITEM...}`, `Error! Reference source not found`, `[?]`) caused by Word/Google Docs round-trips.
6. **Coverage & truncation honesty**: Compare actual inspected chapters against any supplied table of contents or sequential chapter numbering. Flag missing chapters, abrupt sentence truncation, or ellipsis markers so the report never claims full-document verification over partial input.

## 4. Long-Document Pre-Mortem Risk Matrix

Before committing revisions, verify defenses against the five highest-probability failure modes:

| Priority | Failure Mode | Early Signal | Built-In Prevention | Recovery Trigger |
| --- | --- | --- | --- | --- |
| P1 | Unintended edits to non-target chapters or original source sentences | SHA-256 mismatch on unrequested files or changed original block text | Run `longdoc_revision_engine.py` in `annotate` mode + `longdoc_chapter_consistency.py --baseline` | Immediately `rollback --to-version v0` and re-apply only valid proposals |
| P2 | Silent correction of source contradictions or missing units | Diff modifies numbers/units inside original source text | Keep source bytes frozen; place conflict explanation inside tagged editorial annotation | Restore original block from `v0` snapshot and log in `open_conflicts` |
| P3 | Workorder schema mismatch (`target_ids` ignored when parser expects `edits[].file`) | `workorder_targets` empty despite non-empty `organizer-edit-workorder.json` | Dual-schema parser supporting `target_ids`, nested `organizer_edit_workorder.target_ids`, and `edits[].file` | Re-run import with explicit `--workorder` and confirm anchor map |
| P4 | False claim of full-thesis audit on truncated or missing chapters | Missing chapter index in sequence or trailing unfinished sentence | Coverage manifest + truncation scanner in `import_receipt.json` and `longdoc_audit.json` | Mark `status: "partial_with_gaps"` and list exact uncovered range |
| P5 | Invalid-operation test corrupts working state or looks like a crash | Post-fault SHA-256 differs from pre-fault snapshot | Dedicated `fault-test` command with pre/post full-tree SHA-256 assertion and `deliberate_fault_injection` tag | Reject test run and inspect write path isolation |
