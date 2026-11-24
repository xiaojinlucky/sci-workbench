# 编号清洗规则库 R01–R06 与优先级冲突消解

## 1. 规则总表（按固定执行优先级排序）

每条记录的每一次变换都在 `changelog` 中记录 `row_id`、`rule_id`、`field`、`before`、`after`（及 `formula`），确保任意输出值都能回溯至原始输入行。

| 规则编号 | 名称 | 适用条件 | 核心动作 | 失效/降级情形与处置 |
|---|---|---|---|---|
| `R01` | 动态 Schema 探查与字段映射 | 所有输入文件（CSV/JSON/JSONL/SQLite） | 先读取真实列头或 `PRAGMA table_info`，按别名表映射到规范字段；保留原值与前导零字符串 ID | 未知列写入 `unmapped_fields`，不静默丢弃；SQLite 损坏或无表则记 `gaps` |
| `R02` | 区域数字与复杂值解析 | 非缺失原始 `value` | 解析欧式/美式小数、科学计数法、`±` 均值误差、区间 `a–b`、删失检出限 `<x`/`>x` 并计算有效数字 `precision_sigfigs` | 无法解析的自由文本标 `value_kind="unparsed_text"`，入 `needs_human_review` |
| `R03` | 复合单位与 SI 换算（含温差守卫） | 数值已解析且 `unit` 非空 | 查表执行 SI/标准单位换算，保留 `raw_value`/`raw_unit`/`conversion_formula`；对 `Δ°C`/`±` 误差仅做比例缩放不加 `273.15` | 未知单位不猜测、不强转，置 `normalized_value=None` 入 `needs_human_review` |
| `R04` | 上下文锁去重与冲突分层 | 全表清洗完成后 | 仅在 `entity/DOI + metric + context_signature` 完全一致且数值等价时合并（`DUPLICATE_MERGED`）；不同条件分轨保留（`CONTEXT_DISTINCT_PRESERVED`）；同条件不同值绝不覆盖（`VALUE_CONFLICT_SAME_CONTEXT`） | 冲突行双双保留并进入 `scientific_conflicts` 与 `needs_human_review` |
| `R05` | 缺失值与异常隔离 | 值为 `NA/ND/null/-/未报告` 或单位未知 | 缺失值固定 `value_kind="missing"`、`normalized_value=None`（**严禁填 0**） | 全部进入 `needs_human_review` 并标明原因 |
| `R06` | 可追溯导出与人工复核报告 | 流水线收尾 | 输出 `cleaned_records.csv`、`cleaning_audit_report.json`、`cleaning_review_report.md`、`result.json` | 经 `gap_gate` 与 `final_audit` 验收后交付 |

## 2. 优先级冲突消解原则

1. **缺失优先于解析（`R05 > R02`）**：`ND`、`NA`、`not reported`、`-` 优先判为 `missing`，绝不尝试按负号或文本强解。
2. **删失符号优先于纯标量（`R02_CENSORED > R02_SCALAR`）**：`<0.05`、`<=0.1`、`>100`、`LOD < 0.05` 必须保留 `censor_operator` 并标记 `value_kind="censored"`，禁止偷去掉比较符当普通精确数值。
3. **温差守卫优先于绝对温度偏移（`R03_DELTA_TEMP > R03_ABS_TEMP`）**：单位含 `Δ`/`delta`/`diff` 或属于 `±` 不确定度项时，摄氏温差转为开尔文温差公式恒为 `x * 1`（严禁 `+ 273.15`）。
4. **科学冲突优先于去重合并（`R04_CONFLICT > R04_MERGE`）**：相同实验条件下出现不同有效数值时，字面换算绝不能替代科学裁决，禁止静默保留首行而吞掉冲突值。
