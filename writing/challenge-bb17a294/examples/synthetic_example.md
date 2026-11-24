# Minimal Synthetic Invocation Example (`bilingual-academic-translation`)

> **Notice**: This is a synthetic interface illustration only and does not represent any official evaluation case or test answer.

## Synthetic Input Excerpts

### `source.md` (EN)
```markdown
Across 15 thousand samples (n = 15,000), Treatment A did not significantly alter biomarker levels at 2.5 μg/mL (p = 0.42; Fig. 1) [1], suggesting only a preliminary association.

## References
[1] Wang L, Chen Y. Biomarker stability under low-dose exposure. J Example Med. 2023. doi:10.1000/jex.2023.001
```

### `translated.md` (ZH)
```markdown
在 1.5 万份样本（n = 15,000）中，2.5 μg/mL 的处理 A 未显著改变生物标志物水平（p = 0.42；图 1）[1]，仅提示初步关联。

## 参考文献
[1] Wang L, Chen Y. 低剂量暴露下的生物标志物稳定性. J Example Med. 2023. doi:10.1000/jex.2023.001
```

## CLI Invocation

```bash
python /skills/weekly-skill/scripts/bilingual_alignment_guard.py \
  --source /input/source.md \
  --target /app/output/translated.md \
  --glossary /app/work/glossary.json \
  --case-id "SYNTHETIC-EXAMPLE-01" \
  --out-json /app/output/translation_audit.json \
  --out-aligned /app/output/bilingual_aligned.md \
  --out-contract-patch /app/work/contract_patch.json
```

## Illustrative Output Snippet (`translation_audit.json`, synthetic example)

```json
{
  "passed": true,
  "source_paragraphs": 3,
  "target_paragraphs": 3,
  "errors": [],
  "warnings": [],
  "style_warnings": [],
  "bibliography_check": {
    "source_entries": 1,
    "target_entries": 1,
    "doi_matches": 1,
    "errors": []
  }
}
```
