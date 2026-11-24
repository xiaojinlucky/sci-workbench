# Scientific Table Fault Taxonomy & Leading-Zero Protection

## 1. Leading-Zero Identifier Collision Mechanism

Standard tabular readers (e.g., default `pandas.read_csv()` without `dtype=str`) infer purely numeric-looking identifiers (`00123`, `0123`, `123`) as integer `123`. In scientific cohorts, biobank barcodes, specimen labels, and registry codes, stripping leading zeros causes irreversible identity loss and silently merges distinct subjects or cohorts.

**Mandatory Safeguards**:
1. Read all raw tables strictly as strings during intake (`csv.DictReader` or `pd.read_csv(..., dtype=str, keep_default_na=False)`).
2. Flag any identifier column matching `^0\d+$` as `must_read_as_string = true`.
3. Detect explicit **implicit integer-cast collisions**: distinct string IDs sharing the same `int(id)` bucket (e.g., `00123` and `123`).
4. Primary-key normalization may trim accidental outer spaces (`" 00131 "` -> `"00131"`), but must **never** strip leading zeros or cast to integer/float.

## 2. Five-Way Missingness & Sentinel Taxonomy vs True Numeric Zero

Never collapse distinct measurement states into a single `NaN` or replace missing markers with `0`.

| Category (`value_state`) | Typical Raw Tokens | Cleaned Numeric Cell | Scientific Semantics |
| --- | --- | --- | --- |
| `valid` (with `true_zero_preserved`) | `0`, `0.0`, `0.00` | `0` | True observed zero measurement; must remain numeric zero |
| `below_lod` | `<LOD`, `BLOD`, `<LOQ`, `below LOD` | `""` (NULL) | Left-censored below detection limit; never silently set to `0` |
| `not_detected` | `ND`, `N.D.`, `Not Detected`, `BDL` | `""` (NULL) | Analyte not detected; distinct from true numeric `0` and blank missing |
| `instrument_sentinel` | `-999`, `-999.0`, `9999`, `-99.9` | `""` (NULL) | Hardware/export placeholder code; never include in numeric averages |
| `entry_missing` | `""`, `NA`, `N/A`, `NULL`, `None`, `-` | `""` (NULL) | Unrecorded/unentered value; never impute on published tables |
| `invalid_unparseable_fault` | `not_recorded`, `err#`, garbled text | `""` (NULL) | Transcription or corruption fault requiring source check |

## 3. Storage & Encoding Artifacts (Safe Deterministic Repairs)

The following formatting corruptions are deterministic storage faults (not scientific modifications) and are normalized while preserving `<col>_raw`:
- **UTF-8 BOM (`\xef\xbb\xbf`)** at file start preventing clean first-column header matching.
- **Unicode minus / dashes (`−` U+2212, `–` U+2013, `—` U+2014)** preventing float parsing.
- **Non-breaking & zero-width spaces (`\u00a0`, `\u200b`, `\u202f`)** embedded between digits and units.
- **Thousands separator commas** inside quoted numeric strings (e.g., `"1,240.5 ug/mL"`).
- **Fullwidth ASCII digits/punctuation** (`０-９`, `．`, `－`) via controlled NFKC normalization.
- **Greek micro variants** (`µ` U+00B5, `μ` U+03BC) harmonized to canonical `u` for unit lookup while recording `<unit>_raw`.

## 4. Exact vs Conflicting Duplicate Disposition

- **Exact duplicate**: All columns match byte-for-byte after reading. Drop subsequent rows, retain earliest `source_line`, log in `duplicate_audit`.
- **Consistent key duplicate**: Primary keys match after trimming outer spaces, and all non-key fields are identical. Drop subsequent rows, retain earliest `source_line`, log in `duplicate_audit`.
- **Conflicting key duplicate**: Primary keys match, but one or more non-key values differ (e.g., `00131` has both `28.4` and `31.9`). **Never silently pick first or last**. Quarantine all rows sharing that key to `*_quarantine_conflicts.csv` for investigator adjudication.
