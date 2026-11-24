# End-to-End Bilingual Academic Translation Example (Table + Math + Citations)

> Synthetic illustrative example only (not a formal evaluation case).

## Source (English)

In a multicenter cohort of 15,000 participants (n = 15,000), elevated biomarker X was associated with a 12.4% lower hazard ($p < 0.001$, 95% CI [0.81, 0.94]) after adjusting for baseline age [1]. However, these observational findings may not imply direct causation (Table 1; Fig. 2).

The kinetic decay followed $C(t) = C_0 e^{-k t}$ with rate constant $k = 1.5 \times 10^{-4}\ \mathrm{s}^{-1}$ at 310 K [2].

| Cohort | Sample Size | Mean Concentration (mg/L) | p-value |
| --- | --- | --- | --- |
| Control | 7,500 | 4.20 | - |
| High-X | 7,500 | 6.85 | < 0.001 |

### References
[1] Smith J, Lee M. Observational biomarker cohorts. J. Clin. Epidemiol. 2024. doi:10.1000/jce.2024.001
[2] Chen R, Park S. Kinetic stability in aqueous buffers. Anal. Chem. 2023. doi:10.1000/ac.2023.042

## Target (Chinese — Fidelity-Preserved)

在一项纳入 1.5 万名受试者（n = 1.5 万）的多中心队列研究中，校正基线年龄后，生物标志物 X 水平升高与风险降低 12.4% 相关（$p < 0.001$，95% CI [0.81, 0.94]）[1]。然而，上述观察性发现可能并不意味着直接因果关系（表 1；图 2）。

动力学衰减符合 $C(t) = C_0 e^{-k t}$，310 K 下的速率常数为 $k = 1.5 \times 10^{-4}\ \mathrm{s}^{-1}$ [2]。

| 队列 | 样本量 | 平均浓度（mg/L） | p 值 |
| --- | --- | --- | --- |
| 对照组 | 7,500 | 4.20 | - |
| 高 X 组 | 7,500 | 6.85 | < 0.001 |

### 参考文献
[1] Smith J, Lee M. Observational biomarker cohorts. J. Clin. Epidemiol. 2024. doi:10.1000/jce.2024.001
[2] Chen R, Park S. Kinetic stability in aqueous buffers. Anal. Chem. 2023. doi:10.1000/ac.2023.042

## Key Verified Invariants
- Magnitude equivalence: `15,000` ↔ `1.5 万`; scientific notation `$1.5 \times 10^{-4}$` and unit `mg/L`, `s^-1`, `310 K` preserved verbatim.
- Epistemic & negation fidelity: `associated with` / `may not imply` preserved as `相关` / `可能并不意味着` (no causal escalation).
- Live citations & displays: `[1]`, `[2]`, DOIs, `Table 1` ↔ `表 1`, `Fig. 2` ↔ `图 2`, and equation `$C(t) = C_0 e^{-k t}$` preserved 1:1.

## Companion `glossary.json` Snippet (Illustrative Example)

```json
{
  "terms": [
    {"source": "multicenter cohort", "target": "多中心队列", "forbidden_variants": ["多地人群"]},
    {"source": "biomarker X", "target": "生物标志物 X", "forbidden_variants": ["标记物X号"]},
    {"source": "kinetic decay", "target": "动力学衰减", "forbidden_variants": ["动态下降"]}
  ],
  "reordered_clause_notes": []
}
```

## Companion `evidence_map` & `translation_audit.json` Metrics Snippet (Illustrative Example)

```json
{
  "passed": true,
  "paragraph_alignment_ratio": 1.0,
  "sentence_alignment_ratio": 1.0,
  "glossary_coverage_rate": 1.0,
  "glossary_matched_terms": ["multicenter cohort", "biomarker X", "kinetic decay"],
  "glossary_missing_terms": [],
  "evidence_map": [
    {
      "paragraph": 1,
      "numbers_status": "order_exact",
      "citations_ok": true,
      "displays_ok": true,
      "equations_ok": true,
      "negation_ok": true,
      "causal_escalation_ok": true
    },
    {
      "paragraph": 2,
      "numbers_status": "order_exact",
      "citations_ok": true,
      "displays_ok": true,
      "equations_ok": true,
      "negation_ok": true,
      "causal_escalation_ok": true
    }
  ]
}
```
