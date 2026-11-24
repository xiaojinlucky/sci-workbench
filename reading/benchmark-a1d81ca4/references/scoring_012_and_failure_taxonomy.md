# 0/1/2 确定性评分与六类失败分类学

## 1. 六类核心失败分类学 (`FAILURE_TAXONOMY`)

每道候选输出按以下互斥/可组合标签精确归因，避免把单位错、数值错与引用错混为一谈：

| 失败标签 | 判定条件 | 典型根因 |
|---|---|---|
| `missing_answer_or_field` | 无回答、回答为空、或缺少题目要求的关键术语/字段 | 截断、漏答或遗漏关键条件 |
| `ungrounded_quote_or_id` | 结构化 `evidence` 的 `paragraph_id` 不存在于语料，或 `quote` 在**该指定段落内**逐字匹配失败（禁止跨段借尸还魂） | 编造引用、串段张冠李戴 |
| `citation_count_mismatch` | 正文内联引用标记数量（如 `[P001]`、`[1]`、`[Table 1]` 的去重集合）与结构化 `evidence` 数组条目数不一致 | 正文多引/少引与结构化证据清单脱节（典型如 NotebookLM #2416 现象） |
| `numeric_value_mismatch` | 同量纲/换算后数值相对误差超出容差（默认 `1e-3`）或缺失关键数值 | 抄错数字、算错或漏提数值 |
| `unit_mismatch` | 数值本身与参考值匹配，但单位错误或缺失（如把 `mV` 写成 `V` 且未换算，或丢失 `%`） | 单位漏写、数量级前缀混淆 |
| `false_certainty_on_missing_data` | `unanswerable=true` 题未明确弃答，反而编造确定性结论或数值 | 幻觉补答、拒答护栏失效 |
| `schema_or_parse_error` | 预测对象不是合法字典或字段类型损坏（如 `evidence` 非列表） | 输出格式不合规 |

## 2. 容错数值与单位双维校验规则

不再使用脆性子串匹配（如仅判断 `"88.5%" in answer`），而是按三步核验每个 `expected_values`：
1. **解析期望值与单位**：拆出 `(exp_num, exp_unit)` 并换算为规范 SI/领域标准单位 `(exp_si, exp_dim)`（例如 `1200 mV -> 1.2 V`，`88.5% -> 88.5 %`）。
2. **解析回答中的全部数值-单位对**：在回答中提取所有带单位与无单位数值。
3. **三态判定**：
   - 存在候选在相同量纲下换算后满足 `|cand_si - exp_si| <= max(rel_tol * |exp_si|, 1e-6)` → **通过**；
   - 存在候选原始数值相等（`|cand_raw - exp_raw| <= rel_tol`），但单位缺失或换算后量纲/数量级不符 → 记 **`unit_mismatch`**；
   - 否则 → 记 **`numeric_value_mismatch`**。

## 3. 0 / 1 / 2 分档规则

- **2 分（确定性检查全通过）**：无任何失败标签，且（对可答题）至少有 1 条通过原位校验的证据引用（若题卡要求 `required_paragraph_ids`，则须覆盖至少 1 个指定段落）。
- **1 分（部分完成 / 轻微瑕疵）**：回答非空、**未触发** `false_certainty_on_missing_data` 与 `schema_or_parse_error`，且仅存在 1 类可修复瑕疵（例如仅有 `unit_mismatch` 或仅有 `citation_count_mismatch`）。
- **0 分（严重失败）**：回答缺失、触发 `false_certainty_on_missing_data`、`schema_or_parse_error`，或同时触发 ≥2 类失败标签。

## 4. 重复运行稳定性与预算审计

当同一 `agent_id` 提供多轮重复运行（如 3 次重复）时：
- 计算该 Agent 总分与得分率的 `mean`、`std`、`min`、`max`；
- 计算每道题的跨轮次分数方差与 `flipped_items`（同一题在不同轮次得分变化）；
- 核对 `latency_sec`、`model_requests`、`output_tokens` 是否超出 Harbor 预算阈值（64 requests / 900 s / 16384 tokens per response）。
