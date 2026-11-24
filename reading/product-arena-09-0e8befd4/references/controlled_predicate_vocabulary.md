# Controlled Predicate Vocabulary, Polarity & Scope Rules

## 1. Controlled Predicates & Ontology Mapping

| Predicate Code | Ontology URI | Active Voice Cues | Passive Voice Supported |
|---|---|---|---|
| `catalyzes` | `obo:RO_0002327` | catalyzes, catalyzed, 催化 | is catalyzed by |
| `increases` | `obo:RO_0002213` | increases, elevates, enhances, boosts, raises, 提高/增加 | is increased/enhanced by |
| `decreases` | `obo:RO_0002212` | decreases, reduces, lowers, diminishes, 降低/减少 | is decreased/reduced by |
| `inhibits` | `obo:RO_0002449` | inhibits, suppresses, blocks, represses, 抑制/阻断 | is inhibited/suppressed by |
| `activates` | `obo:RO_0002448` | activates, induces, promotes, upregulates, 激活/诱导 | is activated/induced by |
| `regulates` | `obo:RO_0002211` | regulates, modulates, controls, 调控/调节 | is regulated/modulated by |
| `correlates_with` | `sio:SIO_001403` | correlates with, 相关 | — |
| `associated_with` | `sio:SIO_001403` | is associated with, 与…相关 | — |
| `expresses` | `obo:RO_0002292` | expresses, 表达/高表达 | — |
| `reaches` | `sio:SIO_000216` | reaches, attains, 达到 | — |
| `achieves` | `sio:SIO_000216` | achieves, yields, 获得/实现 | — |
| `outperforms` | `sio:SIO_000734` | outperforms, exceeds, surpasses, 优于/高于 | — |
| `compares_with` | `sio:SIO_000734` | compares with, 相比/对比 | — |
| `uses` | `obo:IAO_0000136` | uses, employs, utilizes, adopts, 使用/采用 | — |
| `supports` | `obo:IAO_0000136` | supports, confirms, corroborates, 支持/证实 | is supported/confirmed by |
| `contradicts` | `obo:IAO_0000136` | contradicts, refutes, challenges, 反驳/相矛盾 | is contradicted/refuted by |
| `causes_direct` | `obo:RO_0002410` | directly causes, causally drives, 直接导致 | — |
| `does_not_differ` | `sio:SIO_000734` | does/did not differ from, 无显著差异 | — |

## 2. Polarity & Qualifier Conservation Rules

- **`affirmed`**: Quote and statement contain neither negation (`no`, `not`, `without`, `did not`, `未见`, `不显著`) nor hedging (`may`, `might`, `could`, `suggests`, `可能`, `提示`).
- **`negated`**: Both quote and statement carry explicit negation.
- **`uncertain`**: Both quote and statement carry explicit hedging/uncertainty.
- Never promote `correlates_with` / `associated_with` to `causes_direct` or `increases`.

## 3. Scope & Evidence Tier Rules

- Whenever the quote includes a bounding condition (`only when`, `under X`, `at 250 °C`, `in vitro`, `pH 7.0`, `仅在`), `condition` MUST be populated verbatim from the quote AND reflected inside `atomic_statement`.
- Evidence tiers:
  - `direct_empirical`: direct measurement or experiment reported by the supplied paper itself.
  - `indirect_inference`: model-based or speculative interpretation (`suggests`, `implies`, `may explain`).
  - `cited_secondary`: statement attributed to a cited external work (`et al., 2021`, `[12]`, `previous studies`).
