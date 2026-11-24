# Minimal Synthetic Example (`evidence-driven-writing`)

> **Notice**: Synthetic interface example only; does not represent any official evaluation test case.

## 1. Example Input (`/input/`)

- `paper_1.md` (Primary RCT): Under acidic conditions (pH 5.5), catalyst X increased conversion by 22% (p = 0.01).
- `paper_2.md` (Primary RCT): Under neutral conditions (pH 7.4), catalyst X showed no significant improvement (p = 0.62).
- `paper_3.md` (Observational cohort): Higher catalyst X surface area correlated with higher yield (r = 0.54; observational study cannot establish causality).

## 2. Example CLI Invocation

```bash
python3 -B scripts/evidence_driven_writer_guard.py \
  --draft /app/output/draft.md \
  --corpus-dir /input \
  --out-dir /app/output \
  --case-id "SYNTH-EVID-01" \
  --strict
```

## 3. Illustrative Calibrated Synthesis vs. Rejected Draft

- **Rejected Draft**: *"Catalyst X universally improves conversion [1] and directly causes higher yield [3]."*
  - `C01` → `PARTIALLY_SUPPORTED` (omits acidic condition boundary from `[1]`, causal overclaim on observational `[3]`); `conflict_synthesis_passed: false` (hides null finding in `[2]`).
- **Calibrated Draft**: *"The catalytic benefit of X is strongly pH-dependent: whereas controlled trials show a 22% conversion gain under acidic conditions (pH 5.5) [1], no significant effect is observed under neutral pH 7.4 [2]. Separately, observational cohort data indicate that higher surface area is associated with increased yield, though causality remains unestablished [3]."*
  - All claims `SUPPORTED`, `conflict_synthesis_passed: true`, structured 5-field `result.json` generated with exact locators and verbatim excerpts.
