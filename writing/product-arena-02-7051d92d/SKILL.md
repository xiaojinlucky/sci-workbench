---
name: academic-language-polishing
description: >-
  Use when polishing, copy-editing, or checking bilingual (English/Chinese) academic
  manuscripts, abstracts, grant proposals, theses, or peer-review rebuttals. Performs
  sentence-level minimal edits, enforces hard conservation of numbers/units/p-values/citations/
  negation/modality strength, dismisses false-positive grammar alerts (such as valid concessive
  "Although ..., ..." clauses), enforces full-manuscript glossary and abbreviation consistency,
  and strips AI clichés/translationese via a whitelist. Do NOT trigger for pure literature Q&A
  or requests that ask to invent new experimental data.
---

# Academic Language Polishing Skill

Target task: `academic-language-polishing` (Academic Language Polishing). Deliver a publication-ready manuscript plus a sentence-level, third-party auditable revision log without altering any empirical fact, statistical threshold, citation anchor, or epistemic hedge.

## Trigger Boundaries

- **Trigger when**: the user supplies a draft (Markdown, plain text, LaTeX, or `.docx`) and asks for grammar/syntax correction, academic register calibration, bilingual translationese removal, glossary/abbreviation unification, or de-AI polishing.
- **Do NOT trigger / Stop and ask when**:
  - The request is pure content Q&A without an editing ask (do not silently rewrite user text).
  - The user asks to fabricate missing metrics, statistical significance, or unsupported claims.
  - The input glossary has internal conflicts (flag `needs_author_confirm` rather than guessing).

## Core Operating Discipline (Four-Pillar Standard)

1. **Fix Real Errors Without Overwriting (`改得对` & Minimal Edit)**
   - Operate sentence by sentence; leave grammatically sound, domain-accurate sentences untouched.
   - Distinguish true errors (subject-verb disagreement, missing articles, dangling modifiers, `Although ... but ...` double conjunctions, Chinglish nominalization) from valid scholarly style.
   - **False-Positive Dismissal Protocol**: never accept mechanical grammar alerts that misclassify valid academic concessive clauses (`Although ..., ...` with finite verbs in both clauses) or standard passive voice in `Methods`. Record dismissed alerts explicitly as `status: rejected_false_positive`.
2. **Match Target Register & Full-Manuscript Terminology (`说得像`)**
   - Load `references/polishing_playbook.md` and `references/academic_writing_standards.md` to calibrate tone for the exact target genre (`journal_article`, `thesis`, `grant_proposal`, `rebuttal`, or `abstract`).
   - Enforce glossary-driven terminology consistency across all sections, unify UK/US spelling and hyphenation, and verify abbreviation first-definition rules (define once at first appearance; never use before definition; never silently redefine).
   - Apply **whitelist-only de-AI, anti-translationese, and Press-Release anti-defensive cleaning**: strip hollow openers, contrarian foils (`不是…而是…`), corporate buzzwords, mistranslations (`折叠变化` → `倍数变化`), and self-undermining phrasing in primary `Results`/`Abstract` (move genuine scientific boundaries to `Limitations`).
3. **Hard Invariant Blocking Gate (`没改坏`)**
   - Every revision must pass `scripts/polishing_invariant_engine.py --strict`.
   - Zero-tolerance hard blocks:
     - Numeric values, decimals, percentages, and units (`mg/kg`, `°C`, `μM`, etc.).
     - Statistical test values and inequality operators (`p = 0.041`, `FDR < 0.05`, `n = 42`).
     - Citation markers (`[1-3]`, `(Author et al., 2021)`, `\cite{}`) and figure/table anchors (`Fig. 2A`, `Table 1`).
     - Negation scope (`no significant difference`, `failed to`, `未见显著`).
     - **Epistemic / Modality Strength Conservation**: `may be associated with`, `suggests`, `trended toward` must NEVER be escalated to `proves`, `causes`, `establishes`, or `直接导致`.
     - Experimental condition/cohort qualifiers (`in the high-dose cohort`, `in vitro`, `under high-salt conditions`).
4. **Dual-Channel Edit & Audit Separation (`讲得清`)**
   - Channel 1 (Editor): produce the clean editable manuscript (`/app/output/manuscript_polished.md` and optional `.docx` via `writing_io_and_invariants.py`).
   - Channel 2 (Auditor): run `polishing_invariant_engine.py` independently against original vs. revised text to emit `/app/output/polishing_audit.json` and `/app/output/polishing_redline_diff.md` with per-sentence `sentence_id`, `original`, `revised`, `edit_type`, `rationale`, and `status` (`applied` | `rejected_false_positive` | `needs_author_confirm` | `blocked_invariant_violation`).

## Runtime Execution Workflow (Offline Linux Harbor)

All scripts run on Python 3.12+ stdlib with zero third-party packages.

### Step 1: Preflight, Budget, Result Init & Pre-Polish Scan (with Auto-Glossary & Case Contract)
```bash
python3 -B /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B /skills/weekly-skill/scripts/budget_guard.py --state /app/work/budget.json --init
python3 -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "academic-language-polishing"
python3 -B /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/blocks.json
python3 -B /skills/weekly-skill/scripts/polishing_invariant_engine.py \
  --source /input/<manuscript_file> \
  --input-dir /input \
  --scan-only \
  --auto-glossary \
  --out-glossary-json /app/work/auto_glossary.json \
  --glossary /input/<optional_glossary> \
  --out-audit-json /app/work/pre_polish_scan.json
```
- Supports `.md`, `.txt`, `.docx` (OOXML), `.xml`/`.html` (EuropePMC JATS), and `.pdf` (`pdftotext` + stdlib content-stream fallback).
- Automatically consumes `/input/case.json` (`case_id`, `output_contract`) and `/input/task.md` (`target_journal`, `audience`, `word_limit`, `genre`) via `writing_io_and_invariants.load_case_contract`.

### Step 2: Execute Sentence-Level Minimal-Edit Polishing
1. Read `/input/case.json`, `/input/task.md`, `references/academic_writing_standards.md`, and `references/polishing_playbook.md`.
2. Inspect `/app/work/pre_polish_scan.json` and `/app/work/auto_glossary.json` for cross-section compound hyphenation/spelling drift, abbreviation definition errors, English academic grammar/collocation/nominalization hits, AI/translationese hits, and protected clauses (`valid_concessive_clause`, `valid_methods_passive_voice`).
3. Draft `/app/output/polished_manuscript.md` (optionally seeding mechanical fixes via `--auto-polish --out-polished-md /app/output/polished_manuscript.md`, then applying scholarly minimal edits). If the task explicitly requests `.docx`, generate it via `writing_io_and_invariants.build_docx_from_markdown` and render preview via `render_docx_preview` (recording honest degradation if `soffice` is absent).
4. Record structured edit rationales in `/app/work/edits.json` (keyed by `sentence_id`, including any dismissed false-positive grammar alerts).

### Step 3: Independent Channel-2 Invariant & Redline Verification
```bash
python3 -B /skills/weekly-skill/scripts/polishing_invariant_engine.py \
  --source /input/<manuscript_file> \
  --revised /app/output/polished_manuscript.md \
  --input-dir /input \
  --auto-glossary \
  --glossary /input/<optional_glossary> \
  --edits-json /app/work/edits.json \
  --register auto \
  --out-audit-json /app/output/polishing_audit.json \
  --out-redline-md /app/output/polishing_redline_diff.md \
  --out-result-json /app/output/result.json \
  --case-id "<CASE_ID>" \
  --strict
```
- `--register auto` infers register from `/input/task.md` & `case.json` and checks `word_limit` constraints; `result.json` dynamically merges all `case.json.output_contract` keys.
- If exit code is non-zero (`blocked_invariant_violation` or unresolved glossary drift), fix the offending sentences in `/app/output/polished_manuscript.md` and re-run until `passed: true` (never bypass `--strict`).

### Step 4: Final Grounding & Package Audit
```bash
python3 -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json --require polished_manuscript.md --require polishing_redline_diff.md --require polishing_audit.json
```

## Exception & Boundary Handling

- **Ambiguous target register**: default to `journal_article`, record the assumption in `change_log` and `limitations`, and avoid aggressive stylistic rewrites.
- **LaTeX / equations / mixed CJK-English**: preserve math environments (`$...$`), `\cite{}`, `\ref{}`, and original English domain anchors unchanged.
- **Conflicting glossary or unclear factual intent**: keep original wording intact and emit `status: needs_author_confirm` rather than inventing a resolution.

## Output Contract

See `references/output_contract_spec.md`. Required deliverables under `/app/output/`:
1. `polished_manuscript.md` (clean editable polished text; plus `.docx` if requested)
2. `polishing_redline_diff.md` (sentence-level original vs. revised table with `edit_type`, `status`, `rationale`)
3. `polishing_audit.json` (machine-readable invariants, false-positive guard log, glossary/abbreviation audit, prose lint diff)
4. `result.json` (preserving `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, `status`, `sources`, `gaps`, `limitations`, `verification`)
