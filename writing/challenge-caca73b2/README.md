# Results & Discussion Provenance Writer (`results-discussion-writing`)

Offline, zero-dependency Skill for drafting and auditing scientific Results and Discussion chapters with row-condition numeric binding, error-bar fidelity, negative-result retention, strict section layering, and structured `output_contract` compliance.

## Key Capabilities

1. **Row-Condition Numeric Binding**: Maps every number in source CSV/JSON tables to its exact condition/group row and detects values assigned to the wrong row (`ROW_CONDITION_VALUE_MISMATCH`).
2. **Null & Non-Significant Row Guard**: Automatically detects `p >= 0.05` / `FDR >= 0.05` rows, catches silent omissions (`OMITTED_NULL_OR_NONSIG_ROW`), and blocks upgrading non-significant trends to significant or causal claims.
3. **Error-Bar Definition Check**: Catches `SD` vs `SEM` vs `IQR` vs `95% CI` conflicts against source tables and captions.
4. **Four-Beat Discussion & Layering Audit**: Blocks mechanistic speculation inside Results, flags verbatim numeric dumping in Discussion, and verifies methodological literature comparisons plus concrete limitations.

See `examples/minimal_example.md` for a synthetic invocation example.
