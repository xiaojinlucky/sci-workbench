# Audience Matrix, Keep/Compress/Omit Strategy & Natural Scientific Prose Rules

Consult this reference before drafting `/app/output/adapted_article.md`.

## 1. Five-Audience Adaptation Matrix

| Audience Profile | Reader Goal | First-Use Acronym Rule | Jargon Ceiling (per 100 words/chars) | Target Sentence Length | Mandatory Retained Evidence |
| --- | --- | --- | --- | --- | --- |
| `public_science` | Understand what was found, why it matters, and what it does NOT yet mean for patients/daily life | Expand every specialist acronym on first use with plain gloss | <= 1.5 unexpanded acronyms; explain technical methods via concrete mechanism | ZH <= 34 chars; EN <= 22 words | Core finding, experimental model/species, association vs causation boundary, next validation step |
| `industry_pitch` | Evaluate technical differentiator, maturity stage (TRL), scalability constraints, and validation gap | Expand non-universal domain acronyms on first use | <= 2.5 | ZH <= 38 chars; EN <= 24 words | Quantitative performance vs baseline, model/scale limit, missing industrial/clinical validation |
| `policy_memo` | Support governance, funding, or regulatory decisions with bounded evidence | Expand every scientific/agency acronym on first use | <= 2.0 | ZH <= 38 chars; EN <= 24 words | Population/sample scope, effect magnitude, uncertainty bounds, applicability limit |
| `grant_brief` | Judge scientific gap, methodology rigor, preliminary evidence strength, and feasibility | Expand project-specific acronyms on first use | <= 4.0 | ZH <= 45 chars; EN <= 28 words | Problem-Gap-Approach-Key Finding arc, sample size, baseline comparison, statistical metric, risk boundary |
| `tech_report` | Reproduce or integrate technical outcomes across adjacent engineering/scientific teams | Expand uncommon sub-domain acronyms on first use | <= 5.0 | ZH <= 48 chars; EN <= 30 words | Full quantitative metrics, units, sample sizes, statistical口径 (`p`, CI), baseline, failure/scope limits |

## 2. Three-Tier Content Priority (Keep / Compress / Omit)

- **Tier A — `KEEP` (Non-negotiable evidentiary backbone)**:
  - Primary outcome numbers, units, direction of effect, and comparison baseline;
  - Experimental model/species/population (`in vitro` cell line, mouse xenograft, retrospective single-center cohort, RCT);
  - Sample size (`n`), core statistical strength or uncertainty interval where present;
  - Explicit scope limitations, negative/null subgroups, and unresolved confounders.
- **Tier B — `COMPRESS` (Condense without losing meaning)**:
  - Multi-step reagent protocols -> concise mechanism summary ("what was perturbed and how response was measured");
  - Dense per-condition tables -> headline range + comparison anchor + pointer to source figure/table;
  - Multi-paragraph background -> crisp Problem -> Gap -> Approach framing.
- **Tier C — `OMIT_REDUNDANCY` (Safe to cut)**:
  - Repetitive restatements of the same result across Abstract/Results/Discussion;
  - Routine instrument brand boilerplate or textbook definitions already unnecessary for the target reader;
  - Throat-clearing transitions and redundant qualifiers.
  - **Hard rule**: Never assign `OMIT_REDUNDANCY` to a paragraph's sole mention of model species, sample size, control baseline, or study limitation.

## 3. Meaning-First Translation & Direct Scientific Prose

1. **Answer three reader questions early** (especially for non-specialists):
   - What phenomenon or change was actually observed?
   - Why was this experimental or analytical approach chosen?
   - What does it inform for the next scientific, clinical, or engineering decision?
2. **Ground every paragraph in real source material**: Never pad length with invented "imagine a patient" vignettes, unsupported typical scenarios, or repetitive rephrasing of one abstract point.
3. **Direct claims with single-pass evidence boundaries**:
   - Lead each paragraph with the concrete observation or bounded claim rather than opening with "this study cannot prove...".
   - State the boundary precisely once where it governs interpretation using the pattern: **"what the evidence establishes so far + what validation step is required next"** (e.g., "In mouse xenografts, compound X reduced tumor volume by 38% relative to vehicle control (n=12 per group); human safety and efficacy remain to be tested in clinical trials.").
4. **Hard prose bans (checked deterministically while protecting real scientific terminology)**:
   - **No promotional hype**: `颠覆`, `炸裂`, `重磅`, `改写医学史`, `史无前例`, `彻底攻克`, `堪称奇迹`, `breakthrough cure`, `game-changing`, `miracle`.
   - **No corporate/consulting buzzwords**: `赋能`, `抓手`, `商业闭环`, `价值闭环`, `底层逻辑`, `顶层设计`, `降本增效`, `打开想象空间`, `全链路`, `组合拳`.
   - **No rhetorical false-pivot formulas**: `不是……而是……`, `并非……而是……`, `不在于……而在于……`, `与其说……不如说……`, `表面……实际……`, `看似……实则……`. State the positive judgment directly with its evidence.
   - **No AI transition signposts or hard stops**: `说白了`, `说穿了`, `先说结论`, `值得注意的是`, `需要指出的是`, `更微妙的是`, `从某种意义上说`, `综上所述`, `不难发现`, `众所周知`.
   - **Clean punctuation & active verbs**: Avoid double em-dash (`——`) clutter; replace heavy nominalizations (`完成了对……的优化`, `实现了……的提升`) with concrete verbs and measurements.
   - **Scientific whitelist**: Never alter legitimate scientific terms such as `细胞凋亡`, `肿瘤杀伤`, `温度升高`, `冷却速率`, `血管生成`, `沉淀反应`, `分子骨架`, gene symbols, or software names.
