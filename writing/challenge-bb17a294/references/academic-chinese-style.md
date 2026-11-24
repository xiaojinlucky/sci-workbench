# Academic Chinese and English Style Guide (Anti-Translationese & Anti-AI Prose)

Write target-language manuscripts in restrained, high-density scholarly prose that reads as if authored directly by a domain researcher, while keeping scientific terms, numbers, citations, and claim strength 100% invariant.

## 1. High-Frequency Chinese Translationese to Eliminate

| Category | Translationese / AI Pattern | Preferred Scholarly Rewrite |
| --- | --- | --- |
| Light-verb inflation | `进行了系统分析` / `作出了重要贡献` / `给予了支持` | `系统分析了` / `贡献了` / `支持了` (use the direct verb) |
| Role metaphor | `在……中扮演了关键角色` / `承担了重要角色` | `在……中起关键作用` / `介导了……` / `调控了……` |
| Manner wrapper | `以一种高效的方式` / `以一种稳健的形式` | `高效地` / `稳健地` (direct adverb or concrete mechanism) |
| Formal-subject filler | `值得注意的是` / `需要指出的是` / `有必要指出的是` | Delete filler and open directly with the finding or condition |
| Contrarian framing | `不是A，而是B` / `并非……而是……` (rhetorical inflation) | State the positive evidence-based judgment directly |
| Em-dash / colon overuse | Frequent `——` or prompt colons (`核心是：`, `结论是：`) | Split into clean clauses or use commas/parentheses |
| Nested `的` chains | `的……的……的` three-level pre-modifiers | Break into two concise clauses (subject + verb first) |
| Hedging/degree calque | `在某种程度上` / `从某种意义上说` / `在……的过程中` | Specify the exact scope condition or time window |
| AI summary clichés | `综上所述` / `不难发现` / `众所周知` / `显著赋能` / `深刻揭示` | Lead with concrete empirical results and bounded implications |

## 2. High-Frequency English AI Clichés to Eliminate

Avoid stock LLM fillers in English scholarly targets: `it is worth noting that`, `plays a pivotal role`, `delve into`, `in the realm of`, `a myriad of`, `tapestry of`, `underscores the importance of`, and routine em-dash `—` stacking. Replace with precise domain verbs (`regulates`, `inhibits`, `quantifies`, `evaluates`, `correlates with`).

## 3. Terminology Anchoring and Discipline Conventions

- Anchor critical receptors, genes, algorithms, and metrics on first appearance by pairing the canonical Chinese term with the English original/abbreviation, e.g. `β2-肾上腺素能受体（β2-adrenergic receptor, β2-AR）`, then use the locked short form consistently.
- Use established Chinese scholarly community wording (`批次效应`, `分辨率`, `过滤`, `显著相关`) instead of stiff literal calques (`批次假象`, `粒度`, `卡掉`).
- Never sacrifice scientific precision or modify locked glossary terms, gene symbols, equations, or statistical wording to chase stylistic smoothness.

## 4. Anti-Defensive Framing and Pre-Mortem Bilingual Self-Audit

- **Press-Release anti-defensive discipline**: Preserve the author's exact epistemic strength without injecting extra apologetic hedges ("遗憾的是", "仅作为初步尝试") that were absent from the source, and never weaken clear empirical findings or inflate tentative associations into causal proof.
- **Pre-mortem failure check (Blocking vs Optional)**: Before sealing `translated.md`, check the three highest-impact failure modes first (Blocking) before minor phrasing polish (Optional):
  1. *Silent numeric/unit/order distortion*: verify `bilingual_alignment_guard.py` reports zero value/unit drift and every syntax-driven reorder carries an explicit `reordered_clause_note`.
  2. *Detached live citations or swapped references*: verify every in-text key matches the target reference script, DOI, year, author, and translated title 1:1.
  3. *Polarity or claim-strength inversion*: verify every negation, modal qualifier, and correlational boundary matches the source proposition.
