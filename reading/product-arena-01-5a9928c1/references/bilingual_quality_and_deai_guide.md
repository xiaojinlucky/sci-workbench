# Bilingual Quality, Chunk Continuation & Anti-Translationese Guide

## 1. Explanatory Translation with Term Anchoring

Scientific prose in English often packs cause, concession, condition, and quantitative evidence into a single multi-clause sentence. Follow these four rules:

1. **Restore Implicit Logical Connectors**: When English links clauses via participles or terse subordinators (`While`, `Whereas`, `Although`, `However`, `Given that`), express the logical relation explicitly in Chinese (`尽管…但…`, `相比之下`, `然而`, `鉴于…`).
2. **Anchor Technical Terms at First Mention**: For key receptors, genes, proteins, clinical endpoints, algorithms, and metrics, provide the locked Chinese translation followed by the original English term/abbreviation in parentheses on first appearance—for example `药物不良反应 (adverse drug reaction, ADR)` or `β2-肾上腺素能受体 (β2-adrenergic receptor, β2-AR)`—then use the locked form consistently thereafter.
3. **Faithful Full Translation, Never Summary**: Every proposition, condition, sample size, comparator, and caveat in `source_en` must appear in `target_zh`. Do not omit methodological details or footnotes to make prose shorter.
4. **No External Extrapolation**: Never insert domain claims, drug names, or causal assertions that do not exist in the source paragraph.

## 2. Long-Document Chunk Continuation Protocol

When translating multi-page or multi-column papers (> 12 paragraphs):

- Process sequential batches of 6–10 paragraphs.
- Carry three read-only anchors across chunk boundaries:
  1. The frozen `/app/work/glossary.json`.
  2. The previous chunk's last 2 translated sentences (to keep pronoun antecedents and discourse connectors coherent across page/column breaks).
  3. The current section heading and source page number.
- If a PDF sentence splits across a page or column break, keep the immutable `paragraph_id` mapping from `source_segments.json` and document the cross-page continuation in `translation_basis` rather than silently deleting or renumbering segments.

## 3. Anti-Translationese & De-AI Blacklist Union

`scripts/academic_deai_lint.py` checks Chinese translations against the union of two pattern families while protecting locked glossary terms and scientific expressions:

### A. Chinese Translationese Patterns (Rewrite to Active Academic Chinese)

| Pattern | Typical Failure | Direct Academic Rewrite |
| --- | --- | --- |
| Light-verb nominalization | `进行了系统的评估` / `作出了分析` | `系统评估了…` / `分析了…` |
| Awkward passive shell | `被…所证实` / `被认为具有…` | `…证实了` / `学界认为…` or `现有证据提示…` |
| Clumsy framing shell | `在…的背景下` / `在…的过程中` / `对于…来说` | `在…条件下` / `…期间` / direct subject |
| Literal plural/degree filler | `一系列的` / `在某种程度上` / `以一种…的方式` | `多项/若干` / `在一定程度上` or exact scope / direct adverb |
| Chained attributives (`的…的…的`) | `一个基于多中心队列的长期的随访的研究` | Split into two crisp clauses |
| Redundant causal/topic filler | `使得…能够` / `扮演了重要角色` / `存在着…` | `使…可…` / `起关键作用` / `存在…` |

### B. AI Template Clichés (Remove Unless Literally Required by Source)

- Chinese AI fillers: `综上所述`, `总而言之`, `值得注意的是`, `有必要指出的是`, `显而易见`, `毋庸置疑`, `显著赋能`, `赋能`, `抓手`, `卡点`, `无缝衔接`, `全方位`, `深层次`, `不可或缺的`, `至关重要的作用`, `不仅如此`, `深刻揭示了`.
- English AI fillers (for Zh->En direction): `delve into`, `tapestry of`, `testament to`, `pivotal role`, `in the realm of`, `underscores the importance`, `it is worth noting that`, `game-changer`, `multifaceted`.

### C. Surgical Edit Discipline

Never let stylistic editing weaken scientific accuracy. After editing any flagged sentence:
- Keep every locked glossary term exact.
- Keep every number, SI unit, citation `[n]`, figure tag, negation, and hedge intact.
- Re-run `bilingual_alignment_guard.py --audit` to verify zero regression.
