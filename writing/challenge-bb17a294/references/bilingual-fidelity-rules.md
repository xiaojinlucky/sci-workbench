# Bilingual Fidelity Rules: Magnitudes, Clause Order, Citations, and Epistemic Strength

## 1. Exact EN-ZH Magnitude and Scientific Notation Equivalence

| Source Expression | Exact Normalized Value | Valid Equivalent Target Forms | Forbidden Drift |
| --- | --- | --- | --- |
| `1.5万` | `15000` | `15,000`, `15 thousand`, `1.5 × 10^4` | `150,000`, `1.5 million`, `~15k` |
| `2.3亿` | `2.3e8` | `230 million`, `230,000,000`, `2.3 × 10^8` | `2.3 billion`, `23 million` |
| `1.2千` | `1200` | `1,200`, `1.2 thousand` | `12,000`, `1,000` |
| `1.5 × 10^-4` | `1.5e-4` | `1.5e-4`, `1.5×10⁻⁴`, `1.5 × 10^{-4}` | `1.5e-3`, `0.0015` |
| `p < 0.001` | `op=<, val=0.001` | `p < 0.001`, `P < 0.001` | `p = 0.001`, `p < 0.01`, `highly significant` (omitting value) |
| `45.2%` / `百分之45.2` | `45.2%` | `45.2%`, `45.2％` | `45%`, `45.2` (dropping percent) |
| `12.5 μg/mL` | `12.5 ug/mL` | `12.5 μg/mL`, `12.5 µg/mL`, `12.5 微克/毫升` | `12.5 mg/mL`, `12.5 ug/L` |

## 2. Numeric Appearance Order and Attested Clause Reordering

By default, `bilingual_alignment_guard.py` enforces exact numeric appearance order per paragraph so swapped group/condition numbers (e.g., `"Group A: 12.4%, Group B: 27.8%"` mistranslated as `"A组 27.8%，B组 12.4%"`) fail immediately.

When natural EN↔ZH syntax genuinely requires reordering clauses (for example, moving a time expression or sample size to sentence head in Chinese while keeping every entity-value pairing 100% faithful), supply an explicit `reordered_clause_note` in `/app/work/glossary.json`:

```json
{
  "reordered_clause_notes": [
    {
      "paragraph": 2,
      "reason": "Chinese topic-first syntax placed year 2024 before sample count n=128 and response rate 45.2%; entity-to-value bindings verified unchanged."
    }
  ]
}
```

Alternatively, attach an inline HTML comment on the translated paragraph: `<!-- reordered_clause_note: Chinese topic-first syntax moved 2024 before n=128; bindings verified -->`. Unattested order inversions always fail the guard.

## 3. Immutable `source_citation_ledger`, Live Citation Fields, and Heading Hierarchy 1:1

Prevent the four highest-frequency structural and bibliography failures:
1. **No external additions or silent DOI completions**: Every reference in the source document is locked into an immutable `source_citation_ledger` (`cite_key`, original text, `source_doi`, `source_page`/`source_line`), with `translated_title` stored separately as a display-only field. Any target entry absent from source (`unverified_external_additions`) or any unauthorized completion of a missing/truncated source DOI (`unverified_doi_completions`) is barred from `verified_dois`/`verified_citations` and forces `passed=false` (non-zero exit).
2. **Orphaned script mismatch**: Converting the reference list from Chinese to English while leaving in-text citations as `（张伟等，2023）` breaks author-year lookup. Synchronize in-text citations (`(Zhang et al., 2023)`) with the target bibliography while keeping the immutable source record intact.
3. **Cross-entry title/DOI/year swap**: Never batch-translate titles without locking each entry to its `cite_key` and DOI. Every `[k]` entry must preserve the exact same DOI and publication year at position `[k]` and match its own translated title and author list.
4. **Heading hierarchy 1:1 & honest OOXML live-field degradation**: Never silently merge, drop, or simplify section headings (`H1/H2/H3` count and depth must align 1:1 or `HEADING_HIERARCHY_DRIFT` blocks). When extracting `.docx` files containing Zotero/CSL, Mendeley, EndNote, Citavi, or Word `REF` `w:instrText` fields into Markdown plain text, explicitly record `live_citation_fields` and `citation_fields_preserved: false` — never claim live citation links survive Markdown export.

## 4. Negation Scope and Epistemic Hedge Conservation

- **Negation parity**: Keep every negative condition, exclusion, or non-significant finding intact (`did not significantly differ` ↔ `未见显著差异`, never `差异较小` or dropped negation).
- **Anti-causal-escalation ladder**:
  - Associative/hedged source (`associated with`, `correlated with`, `may`, `might`, `suggests`, `preliminary`, `相关`, `可能`, `提示`, `初步`) MUST remain associative/hedged in target.
  - Translating associative/hedged source wording into causal/definitive verbs (`proves`, `demonstrates causation`, `causes`, `definitively`, `证明了`, `证实了`, `直接导致`, `必然引起`) is a hard error.
