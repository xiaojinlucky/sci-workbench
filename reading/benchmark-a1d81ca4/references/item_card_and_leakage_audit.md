# Benchmark 题卡规范与防答案泄漏审计指南

## 1. Organizer Revision 2 边界声明

- 本工具面向**基于段落/表格文本的来源条件化 `benchmark-DRAFT` 工单**（通常为 4 题或 N 题），输入语料明确标记为 `paragraph_and_table_text_only_not_pdf_pixel_gold`。
- 本地合成检查与确定性规则评分**严禁冒充**官方 DeepSeek Harness (DSH) 平台分或独立人工语义验证。
- 严禁静默改写原始科学语料字节或已锁定的题面措辞。

## 2. 标准题卡字段字典

| 字段 | 必填 | 含义与校验要求 |
|---|---|---|
| `item_id` | 是 | 全局唯一题号（如 `DRAFT-01`），严禁重复 |
| `task_type` | 是 | 题型分类（如 `numeric_extraction`, `table_lookup`, `cross_paragraph_reasoning`, `unanswerable_abstention`） |
| `prompt` | 是 | 完整任务提问，必须信息完备且不含答案剧透 |
| `required_paragraph_ids` | 建议 | 支撑本题的语料段落/表格 ID 列表；若填写，每个 ID 必须真实存在于已提供语料中 |
| `reference_answer` | 可答题必填 | 人工复核标准参考答案；`unanswerable: false` 时不可为空 |
| `expected_values` | 按需 | 关键数值与单位列表（如 `["88.5%", "-0.7 V"]`），用于容错数值/单位双维核验 |
| `expected_keywords` | 按需 | 关键实体或判断词列表 |
| `unanswerable` | 是 | 布尔值，标识本题在当前材料下是否应当诚实弃答 |
| `rubric` | 是 | 明确 0 / 1 / 2 分档判据与通过阈值 |
| `version` | 是 | 题卡版本号（如 `draft-r2`） |
| `data_license` | 建议 | 语料授权标识（如 `CC-BY-4.0`、`open-access`、`organizer-supplied`），缺失时记告警 |

## 3. 预检门禁清单（`item_bank_auditor.py`）

运行评测前必须先执行题库预检，拦截以下 6 类建题缺陷：

1. **`DUPLICATE_ITEM_ID`**：题库存在重复 `item_id`，会导致成绩映射覆盖。
2. **`MISSING_REQUIRED_FIELD`**：缺少 `prompt`、`rubric`、`task_type` 或 `version`。
3. **`EMPTY_REFERENCE_ANSWER`**：可答题（`unanswerable=false`）缺少 `reference_answer` 且无 `expected_values`/`expected_keywords`，导致题目“判不了”。
4. **`MISSING_CORPUS_PARAGRAPH`**：`required_paragraph_ids` 中存在当前语料找不到的段落 ID。
5. **`PROMPT_ANSWER_LEAKAGE`**：`prompt` 正文中直接泄漏了 `reference_answer` 完整串或所有 `expected_values`（排除题面明确标注为条件/给定前提的短数字），导致评测失真。
6. **`MISSING_DATA_LICENSE`**：缺少 `data_license` / `authorization` 授权声明（提示级告警，供人工补录）。

## 4. 双哈希指纹规范

每道题卡同时计算两枚 SHA-256 指纹：
- **`semantic_sha256`**：仅覆盖影响评分语义的字段（`prompt`, `task_type`, `expected_values`, `expected_keywords`, `required_paragraph_ids`, `unanswerable`, `reference_answer`, `rubric`）。
- **`full_sha256`**：覆盖整个题卡 JSON（含 `notes`, `tags`, `author`, `data_license`, `version` 等元数据）。
