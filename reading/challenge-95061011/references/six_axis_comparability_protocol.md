# Six-Axis Comparability Protocol (`challenge-95061011`)

## 1. Mandatory Six Axes
Before any quantitative pooling, meta-average, or direct numeric ranking across papers, test all six axes for every effective document pair:

| Axis | What Must Align | Common False-Pooling Traps |
| --- | --- | --- |
| `task` | Underlying scientific task or assay endpoint | Mixing photocatalytic degradation with physical adsorption; mixing classification with regression |
| `metric` | Exact mathematical definition of reported outcome | Mixing removal percentage with rate constant `k (min^-1)`; mixing Macro-F1 with Micro-F1 or AUROC |
| `units` | Physical measurement unit and scale | Mixing `mg/L` with `ug/L` or `mg/g`; mixing `min` with `h`; mixing `%` with fractional `0..1` without explicit conversion |
| `material` | Active material, catalyst family, model architecture, or biological platform | Pooling TiO2 with g-C3N4 or Fe3O4; pooling mouse xenograft with human organoid |
| `pollutant` | Target analyte, dye, contaminant, substrate, or benchmark dataset | Pooling methylene blue (MB) with rhodamine B (RhB) or tetracycline; pooling Dataset A with Dataset B |
| `budget` | Experimental or computational budget (catalyst dosage, initial concentration, irradiation time, sample count, GPU/request budget) | Comparing 95% removal at 180 min / 2.0 g/L against 80% removal at 30 min / 0.2 g/L, or pooling when `budget` is `not_reported` |

## 2. Pairwise Decision Rules
For every pair of effective documents `(A, B)`:
1. **Confirmed Mismatch -> `noncomparable_pairs`**:
   - At least one of the six axes is `reported` in both `A` and `B`, and their canonical values conflict (or units differ in physical dimension or unscaled magnitude).
   - `pooling_blocked = true`. Record every mismatched axis with both original values and paragraph anchors.
2. **Unreported / Ambiguous Axis -> `indeterminate_pairs`**:
   - Zero confirmed mismatches among reported axes, **and** at least one of the six axes has `status != "reported"` (`not_reported` or `ambiguous`) in `A` or `B`.
   - `pooling_blocked = true`. **Unreported is never treated as equal.** Record `unreported_axes[]` explicitly.
3. **Full Six-Axis Match -> `comparable_pairs`**:
   - All six axes have `status == "reported"` in both `A` and `B`, and all six canonicalized values match without unresolved scale conversion.

## 3. Unit Normalization Advisory (Never Silent Mutation)
- **Strict Identity Aliases (Comparable Without Scale Change)**:
  - `%` == `percent` == `pct`
  - `mg/g` == `mg g-1` == `mg·g-1`
  - `mg/l` == `mg l-1` == `mg·l-1` (note: `ppm` in dilute aqueous media is flagged with an advisory rather than blindly equated across non-aqueous systems unless explicitly stated)
  - `min` == `minutes` == `mins`; `h` == `hr` == `hrs` == `hour` == `hours`
- **Convertible Scale Mismatches (Noncomparable Until Explicitly Converted)**:
  - Same physical dimension, different scale (e.g., `mg/L` vs `ug/L` factor `1000`, `h` vs `min` factor `60`, `s` vs `min` factor `60`, `mmol/L` vs `umol/L` factor `1000`).
  - Keep `raw_value` and `units` **unmodified** in the matrix cells.
  - Record an entry in `unit_normalization_advisories[]` naming both documents, raw units, exact conversion factor, and warning that raw numbers must not be pooled prior to conversion.
- **Incompatible Physical Dimensions (Hard Noncomparable)**:
  - E.g., `%` (dimensionless removal ratio) vs `mg/g` (adsorption capacity) vs `min^-1` (kinetic rate constant).

## 4. Clique Guarantee for `comparable_subgroups`
Connected components alone are unsafe if an approximate match could create non-transitive chains. `comparison_matrix_builder.py` builds `comparable_subgroups` via Union-Find followed by clique verification:
- Every pair `(u, v)` inside a returned subgroup MUST belong to `comparable_pairs`.
- If a connected component is not a complete clique, it is decomposed into maximal verified cliques of size `>= 2`.
- Quantitative synthesis (`quantitative_synthesis` / `pooled_estimates`) is permitted **only** over a subset of a single verified `comparable_subgroup` (size `>= 2`).

## 5. Single-Document & Deduplication Safeguards
- Preprint + formal publication pairs are collapsed to one canonical Version-of-Record before counting effective documents. Two versions of the same paper never form a `comparable_subgroup` and never satisfy multi-document comparison.
- Documents excluded as `out_of_scope_excluded` are omitted from pairwise comparability and pooling while preserved in `excluded_documents[]`.
- If effective in-scope document count `< 2`, set `multi_document_satisfied: false` and `pooling_allowed: false`.
