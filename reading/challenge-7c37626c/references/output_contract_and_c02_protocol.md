# 输出契约与 C02 缺失分支协议 (`challenge-7c37626c`)

## 1. 运行路径与必交产物
- **输入目录**：`/input`（`case.json`, `task.md`, 可选 `artifacts/source-formulas.xml`, `artifacts/publisher-source.xml`, PDF/MD/TXT/TEX）。
- **工作目录**：`/app/work`（`env.json`, `.budget_state.json`, `audit_log.json`, `gaps.json`）。
- **输出目录**：`/app/output`，必须包含且非空：
  1. `result.json`（含 `case_id`、`status`、`formula_coverage_status`、结构化结果、指标与溯源、`gaps[]`）
  2. `formula_verification_report.json`（完整公式节点、变量溯源表、推导/量纲/数值核验及 Markdown 校验报告）
  3. `formula_notes.md`（带 `E001...` 锚点的可读公式核验笔记，须过 `math_markdown_validator.py`）

## 2. `result.json` 核心字段契约
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `case_id` | `string` | 与 `/input/case.json` 一致 |
| `status` | `"ok" \| "partial_with_gaps"` | 存在任何未闭环缺口、OCR 暂停或缺失分支时必须为 `"partial_with_gaps"` |
| `formula_coverage_status` | `"complete" \| "partial" \| "insufficient_evidence"` | `C02` 无公式快照必须为 `"insufficient_evidence"`；缺变量/OCR 暂停/XML 损坏为 `"partial"` |
| `xml_sources_used` | `string[]` | 实际读取的 XML 相对路径列表 |
| `formulas` | `object[]` | 每项含 `formula_id`, `source_file`, `xml_tag`, `representation_source`, `latex`, `symbols`, `ocr_noise`, `verification_status` |
| `variable_table` | `object[]` | 每项含 `symbol`, `status`（三级）, `paragraph_id`, `unit_hint`, `range_hint`, `definition_quote` |
| `derivation_checks` | `object[]` | 每项含 `step_id`, `method`, `equivalent`, `assumptions`, `boundary_conditions` |
| `dimensional_checks` | `object[]` | 每项含 `check_id`, `lhs_dim`, `rhs_dim`, `consistent`, `scale_ratio`, `order_of_magnitude_gap`, `scale_exact_match` |
| `numeric_checks` | `object[]` | 每项含 `check_id`, `status`, `computed`, `expected`, `within_tolerance`（缺参记 `insufficient_evidence` 与 `computed: null`） |
| `markdown_validation` | `object` | `math_markdown_validator.py` 结构化校验摘要 |
| `metrics` & `metric_provenance` | `object` | 全部数值指标有限且 1:1 绑定来源路径 |
| `gaps` | `object[]` | 结构化缺口列表（`code`, `severity`, `source`, `message`） |

## 3. C02 真实缺失分支硬协议
当输入快照没有公式表示（无 MathML、无 `<tex-math>`、无行间 LaTeX），或缺少可复算原始变量值时：
1. **禁止反推公式**：不得根据摘要或文字描述中的汇总指标反推出原论文未提供的数学方程。
2. **禁止未知填 0**：缺失的数值参数必须保留 `computed: null`、`status: "insufficient_evidence"` 与 `missing_variables[]`，绝不默认填 `0` 或随意估值。
3. **诚实覆盖状态**：
   - 无任何公式表示：`formula_coverage_status = "insufficient_evidence"`，记录 `MISSING_FORMULA_REPRESENTATION`。
   - 有公式但缺可复算变量或含 OCR 暂停项：`formula_coverage_status = "partial"`，记录 `MISSING_VARIABLE_VALUES_FOR_RECOMPUTATION` 或 `OCR_NOISE_DETECTED`。
4. **可核对部分保留**：快照中已明确给出的非公式条件与指标仍可如实核对，但必须把边界限制写进 `gaps[]` 与 `formula_notes.md`。
