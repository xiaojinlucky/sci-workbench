# Terminology, Bibliography Identity & Invariants Protocol

This reference defines the exact verification boundaries enforced by `scripts/bilingual_alignment_guard.py`.

## 1. Source Bibliography Identity & Cross-Item Contamination Protection

Real literature workflows frequently corrupt bibliographic records when translation overwrites the original title or leaks text from a previously opened paper into the current record.

- **Immutable Lock (`source_metadata_lock.json`)**: Created during `--prepare` before any translation starts. Captures `item_id`, `source_title`, `authors`, `doi`, `year`, and `source_sha256`.
- **No Overwrite Rule**: Never replace `source_title` with Chinese text. Store the translated title in `translated_title` only.
- **Cross-Item Contamination Check**:
  - Every item in `bilingual_segments` must carry an `item_id` matching the locked `item_id` (if `item_id` is present in source).
  - If `case.json` supplies `other_items` / `neighbor_items` (or multiple bibliographic records), the audit verifies that neither titles nor DOIs from non-target items bleed into `source_metadata` or `bilingual_segments[].target_zh`.

## 2. Two-Stage Terminology Locking

1. **Candidate Extraction (`--prepare`)**:
   - Acronym definitions: `Full Term (ABBR)` and `ABBR (Full Term)`.
   - Chemical/molecular/gene tokens (for example `β2-AR`, `TP53`, `IL-6`, `SARS-CoV-2`, `CO2`, `H2O2`).
   - Recurring multi-word capitalized or technical noun phrases.
2. **Enforcement (`--audit`)**:
   - Every locked term detected in `source_en` of paragraph `P` must have its canonical `target` (or `first_mention_anchor`) present in `target_zh` of the same paragraph `P`.
   - Any occurrence of `forbidden_variants` in `target_zh` fails the audit immediately with exact `paragraph_id` and `section`.
   - Consistency rate = `compliant_term_paragraph_pairs / total_term_paragraph_pairs`.

## 3. Numeric, Magnitude & SI Unit Pairing Invariants

- **Chinese/English Magnitude Equivalence**:
  - `1.5万` == `15,000` == `15 thousand`
  - `2.3亿` == `230,000,000` == `230 million`
  - `1.2 billion` == `12亿` == `1,200,000,000`
  - Scientific notation (`1.5 × 10^-3`, `1.5e-3`, `1.5×10^4`) normalized to float values before comparison.
- **SI Unit & Value Pairing (No Silent Unit Conversion)**:
  - Mass: `kg`, `g`, `mg`, `μg`/`µg`/`ug`/`mcg`, `ng`, `pg`
  - Volume: `L`, `mL`, `μL`/`µL`/`uL`
  - Length: `m`, `cm`, `mm`, `μm`/`µm`/`um`, `nm`, `pm`
  - Time: `s`, `ms`, `μs`, `min`, `h`/`hr`, `d`/`day`
  - Electrical: `kV`, `V`, `mV`, `μV`, `A`, `mA`, `μA`
  - Concentration: `M`, `mM`, `μM`, `nM`, `pM`, `mg/L`, `μg/L`, `mg/kg`, `mg/mL`, `ppm`, `ppb`, `wt%`, `vol%`
  - Pressure/Energy/Frequency/Temp: `Pa`, `kPa`, `MPa`, `GPa`, `eV`, `keV`, `MeV`, `J`, `kJ`, `Hz`, `kHz`, `MHz`, `GHz`, `°C`/`℃`, `K`
  - Compound & rate units: `mA cm^-2` / `mA/cm2`, `A cm^-2`, `mV dec^-1`, `mg cm^-2`, `g cm^-3`, `mol L^-1`, `inhabitants/km2`, and rate-per-10k denominators (`/10,000` == `/万` == `每万`).
  - Thousands-separated sample sizes (`n=1,010`, `n=5,539`) are audited as exact integer multisets so commas never trigger truncation (`n=1,010` -> `n=1`).
  - Each `(normalized_value, canonical_unit)` pair in `source_en` must match `target_zh` in count, canonical unit, and value. A change such as `5 mg` -> `5 μg`, `10 mA cm^-2` -> `10 A cm^-2`, `50 mV` -> `50 V`, `10 μm` -> `10 nm`, or `25 °C` -> `25 K` is a **blocking error**.
- **Ranges & Statistical Expressions**:
  - Preserve `±` uncertainties, `95% CI`, `p < 0.05` / `P = 0.001`, `n = ...`, and numeric intervals (`10–20`, `10-20`).

## 4. Formulas, Citations & Figure/Table Anchors

- **Citations**: Numeric brackets (`[1]`, `[1, 2]`, `[3-5]`) and LaTeX `\cite{...}` keys must match as exact multisets per paragraph.
- **Figure/Table/Equation References**: `Fig. 2B` / `Figure 2B` -> `图2B` or `Fig. 2B` (preserving number + subpanel letter `2B`); `Table 1` -> `表1` or `Table 1`; `Eq. (1)` -> `式(1)` or `Eq. (1)`. Missing or altered numbers/letters are blocking errors.
- **Formulas & Symbols**: Inline/display LaTeX (`$...$`, `$$...$$`, `\sum`, `\sqrt`, `\frac`) and scientific Greek/math symbols (`α, β, γ, δ, Δ, μ, σ, λ, ≤, ≥, ≈, ±, ×`) must be preserved without silent substitution.

## 5. Negation Polarity & Epistemic Modal Hedges

- **Negation Polarity**:
  - English cues: `not`, `no`, `never`, `neither`, `nor`, `without`, `none`, `cannot`, `can't`, `fail(s/ed) to`, `unlike`, `absent`, `rarely`, `seldom`, `lack(s/ed)`.
  - Chinese cues: `不`, `未`, `无`, `非`, `否`, `罕见`, `极少`, `缺乏`, `未能`, `不同于`, `没有`.
  - Dropping negation when source is negative (or inserting negation when source is affirmative) is a **blocking error**.
- **Epistemic Modal Hedges & Anti-Upgrade Gate**:
  - English hedge cues: `may`, `might`, `could`, `suggests`, `indicates`, `appears to`, `approximately`, `roughly`, `only`, `primarily`, `limited to`, `potentially`, `likely`.
  - Chinese hedge cues: `可能`, `或许`, `提示`, `表明可能`, `约`, `大约`, `仅`, `主要`, `局限于`, `潜在`, `倾向于`.
  - Chinese certainty upgrade blacklist: `确切证明`, `充分证明`, `完全证明`, `证实了`, `确定无疑`, `必然导致`, `肯定会`, `彻底解决`, `绝对`.
  - When a source sentence uses tentative modal language (`may/might/could/suggests`) and has no certainty verb (`prove/confirm/establish`), translating it with a Chinese certainty-upgrade term is flagged as a **blocking `modal_upgrade_drift` error**.

## 6. Sentence-Density & Logical Discourse Connector Invariants

- **Sentence Count & EN-Word/ZH-Char Information Density**:
  - For substantial paragraphs (`en_words >= 30`), `bilingual_alignment_guard.py` checks sentence count alignment and `zh_chars / en_words` density ratio. A ratio `< 0.78` or severe sentence collapse (`en_sentences >= 3` reduced to `<= en_sentences // 3` with ratio `< 1.25`) triggers a blocking `IMPLICIT_OMISSION_OR_SUMMARY_COLLAPSE` error.
- **Logical Discourse Connector Conservation**:
  - Contrast/concession markers (`However`, `Whereas`, `Although`, `While`, `Despite`, `Conversely`, `Nevertheless`) must retain explicit Chinese contrast/concession markers (`然而`, `相比之下`, `尽管`, `虽然`, `相反`, `却`, `但`).
  - Causality/consequence markers (`Because`, `Therefore`, `Thus`, `Hence`, `Consequently`, `Due to`) must retain explicit Chinese causality/consequence markers (`因为`, `由于`, `因此`, `从而`, `导致`, `由此`).
  - Dropping either logical relation class triggers a blocking `DISCOURSE_LOGIC_DRIFT` error.
