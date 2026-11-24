# Introduction & Related Work Protocol (`introduction-related-work-writing`)

## 1. Press-Release Four-Beat Opening Architecture

Structure the Introduction around four tightly linked beats (no generic throat-clearing):
1. **Beat 1 — Concrete Problem & Constraint**: Name the exact scientific/technical bottleneck, operational regime, and why existing capability fails today.
2. **Beat 2 — Evidence-Derived Bounded Gap**: Synthesize cited limitations or condition-dependent contradictions across prior studies; state explicitly **what remains unresolved, under which scope, and why it matters**.
3. **Beat 3 — Core Approach**: Present the proposed method/design as a direct, minimal response to Beat 2.
4. **Beat 4 — Hardest Empirical Finding + 1:1 Itemized Contributions**: Preview the primary quantitative result (anchored to Table/Figure) and list 2–4 verifiable contributions, each pointing to an actual section, figure, or table in the supplied manuscript.

## 2. Dimension-Grouped Comparative Related Work (Anti-Chronological Rule)

Never list literature sequentially by publication year (`A (2019) ... B (2020) ... C (2021) ...`). Instead:
1. Define 2–4 explicit **comparison axes** grounded in the supplied papers (e.g., algorithmic mechanism, experimental regime, sample/cohort coverage, evaluation metric).
2. Group papers by axis and write a **topic sentence asserting the trade-off or divergence** before citing individual studies.
3. For narrow-scope conflicting studies (e.g., Study A shows positive effect in Regime X; Study B shows null/negative effect in Regime Y), explicitly juxtapose their conditions instead of smoothing them into a fake consensus.

## 3. Bounded Gap Derivation & "First-Ever" Firewall

An acceptable gap statement satisfies three criteria:
- **Cited Anchor**: Points to concrete limitation(s) or conflicting finding(s) in supplied citations.
- **Scope Boundary**: Explicitly bounds the claim to the tested regime, modality, cohort, or problem setting.
- **Significance ("Who Cares")**: Explains the downstream scientific or practical consequence of leaving the gap unresolved.

Forbidden ungrounded novelty patterns (intercepted as `UNSUBSTANTIATED_GAP_CLAIM`):
- "尚无研究", "国内外尚未见报道", "首次提出", "填补空白", "first ever", "no prior work", "unprecedented" when lacking both cited limitations and explicit scope boundaries.

## 4. 1:1 Contribution-to-Evidence Verification Checklist

For every contribution bullet in the Introduction:
- Does the claimed mechanism/algorithm exist in supplied Methods?
- Does every mentioned Figure/Table (`Fig. 2`, `Table 1`) exist in supplied outputs?
- Does every numeric improvement match the supplied Results table without rounding drift or condition-row swap?
- Are all supplied key references covered, and are 0 unverified citations introduced?
