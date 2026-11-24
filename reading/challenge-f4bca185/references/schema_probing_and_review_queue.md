# SQLite / CSV / JSON 真实 Schema 探查与人工复核队列规范

## 1. 攻克 SQLite Schema 错位痛点（`mode=ro` + `PRAGMA table_info`）

文献库与 Zotero 类工具导出的 `.sqlite` / `.db` 数据库常存在版本间列名漂移（如 `itemID` vs `row_id`、`measured_val` vs `value`、`source_doc` vs `doi`）。本技能严禁硬编码 SQL 列名强读，强制按以下四步执行：

1. **只读打开**：使用 `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`，绝不修改或锁死原始数据库。
2. **表与列实探**：查询 `sqlite_master` 获取全部用户表（排除 `sqlite_%` 系统表），对每张表执行 `PRAGMA table_info(<table>)` 记录真实列名与声明类型。
3. **数据表自动识别与读取**：按表结构匹配目标提取表（或读取 `case.json` 指定的 `sqlite_table`），以 `SELECT *` + 真实列名构造字典，避免 `OperationalError: no such column`。
4. **探查留痕 (`schema_probe_report`)**：记录每个输入文件的格式、表名、原始列集合、已映射列、未映射列 (`unmapped_fields`) 与前导零字符串 ID 清单。

## 2. 前导零 ID 字符串保护 (`leading_zero_ids_protected`)

文献样本号、化合物编号或条目编号常带前导零（如 `"0012"`、`"0045"`）。本技能全程以字符串处理 `row_id` / `sample_id` / `item_id`，严禁转成整数导致 `"0012"` 丢失为 `12`，并在 `schema_probe_report` 中显式列出受保护的前导零编号。

## 3. 人工复核队列 (`needs_human_review`) 分级与处置

以下 6 类情形绝不静默修复，全部结构化入队并写入 `cleaning_review_report.md`：

| 复核原因 (`reason`) | 严重级别 | 机器处置 | 人工接管动作 |
|---|---|---|---|
| `VALUE_CONFLICT_SAME_CONTEXT` | `high` | 保留双方记录并标冲突，不覆盖 | 回查原文表/正文判断抽取错误或文献内部矛盾 |
| `unknown_unit` | `high` | 置 `normalized_value=None`，保留原值与原单位 | 核实单位定义并补充换算规则或保持原口径 |
| `unparsed_value_format` | `medium` | 置 `normalized_value=None`，保留原始文本 | 拆分复合描述或修正 OCR/抽取乱码 |
| `missing_value` | `info` | 保持 `null`/空值（**严禁填 0**） | 确认原文确系未报告而非漏抽 |
| `missing_unit` | `medium` | 保留数值但 `normalized_unit=None` | 回查表头/图注补全缺失单位 |
| `ambiguous_locale_comma` | `medium` | 按默认区域规则解析并标告警 | 确认 `1,250` 是千分位还是欧式小数 `1.25` |
