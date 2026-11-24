# 只读 Schema 探查与编码列识别指南

在科研数据库查询任务中，最容易被忽视的风险发生在写出第一行 `SELECT` 之前：无意中改写了原始快照、误信缺失约束的副本表结构、把分类编码整数当作连续物理量求均值，或把前导零文本主键强转为整数。本指南规定只读探查与字段语义接地的标准动作。

## 1. 严格只读连接与不可变性证明

对 `/input` 下任何 `.sqlite` / `.db` 授权快照，必须同时落实三道只读防线：

1. **URI 只读打开**：使用 `sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True)`，操作系统与 SQLite 引擎层面拒绝任何写入或隐式创建新空库。
2. **会话只读开关**：连接建立后立即执行 `PRAGMA query_only = ON;`。
3. **前后 SHA-256 哈希核验**：探查与查询执行前后各计算一次数据库文件 SHA-256，确认 `sha256_before == sha256_after`（输出 `db_immutability_verified: true`），并在报告中保留完整哈希作为快照版本指纹。

## 2. 防范 `sqlite-utils` 与外部导入导致的约束丢失

科研人员常借助 `sqlite-utils`、pandas `to_sql()` 或 CSV 批量导入构建 SQLite 分析库，这类工具在表重构或副本导出时极易静默丢失 `PRIMARY KEY`、`FOREIGN KEY`、`NOT NULL`、`UNIQUE` 与 `CHECK` 约束。因此 `sqlite_schema_inspector.py` 绝不只读建表 DDL，而是执行**声明 + 实测双轨审计**：

- **DDL 声明层**：读取 `sqlite_master`、`PRAGMA table_info`、`PRAGMA foreign_key_list`、`PRAGMA index_list` 与 `PRAGMA index_info`。若某表缺少显式 `PRIMARY KEY`，或存在命名类似 `*_code`/`*_id` 匹配另一表主键却未声明 `FOREIGN KEY` 的列，自动发出 `constraint_loss_warning`。
- **数据实测层**：
  - 逐表统计真实行数 `row_count`、精确重复行数 `exact_duplicate_rows`（按全列 `GROUP BY` 核查）与主键唯一性 `primary_key_unique`；
  - 逐列统计真实 SQLite 存储类分布（`typeof(col)`：`integer` / `real` / `text` / `null` / `blob`），及时抓出同一列混存数字与字符串的动态类型脏数据；
  - 逐列区分 `SQL NULL`（`null_count`）与空字符串 `""`（`empty_string_count`）；
  - 对每条显式外键及隐式同名键实测**外键孤儿记录数**（child 中非空键在 parent 不存在）与**零子记录父键数**（parent 中在 child 有 0 条匹配的维度成员，如对照空组）。

## 3. 编码整数列（Coded Integer Columns）识别与禁聚合红线

在 UCI Wine、Iris 及临床/组学实验库中，分类标签和质控开关常以整数存储：

| 常见列名 | 实际取值示例 | 真实语义 | 允许的 SQL 操作 | 禁止的误聚合 |
|---|---|---|---|---|
| `cultivar_code` | `1, 2, 3, 4` | 酿酒葡萄品种编号 | `GROUP BY`, `JOIN`, `COUNT(DISTINCT ...)` | `AVG(cultivar_code)`, `SUM(cultivar_code)` |
| `species_code` | `0, 1, 2` | 鸢尾花物种编号 | `GROUP BY`, 映射 `species` 标签 | `AVG(species_code)`, `SUM(species_code)` |
| `qc_pass` | `0, 1` | 质控通过标志（二值指示器） | `SUM(qc_pass)`（通过件数）、`AVG(CAST(qc_pass AS REAL))`（明确命名为 `qc_pass_rate`） | 当作连续物理测量做均值/标准差解读 |
| `stage_code` / `status_id` | `1, 2, 3` | 有序分期或状态码 | 频数表、`CASE WHEN` 语义标签映射 | 直接当等距连续变量求算术平均 |

`sqlite_schema_inspector.py` 结合三项信号自动标记 `coded_categorical_flag = true`：
1. 整数存储类型且非连续高基数浮点；
2. 唯一值数较低（如 `<= 12` 或二值 `{0, 1}`）；
3. 列名命中分类/状态词根（`code`, `id`, `class`, `type`, `status`, `cultivar`, `species`, `group`, `flag`, `pass`, `grade`, `stage`, `tier`, `arm`）。

## 4. 前导零字符串 ID（Leading-Zero Text IDs）保护规则

当主外键（如 `sample_code = '0001'`、`specimen_id = '001'`）以带前导零的 `TEXT` 存储时：
- `sqlite_schema_inspector.py` 自动扫描所有 `TEXT` 列，检测符合 `^0[0-9]+$` 模式的记录，输出 `leading_zero_text_flag = true` 及样例值；
- 严禁在 SQL `JOIN`/`WHERE` 中执行 `CAST(sample_code AS INTEGER)` 或与裸整数 `1` 比较（SQLite 亲和类型错配会导致 `0001 = 1` 匹配失败或与不同前缀编号误合并）；
- 导出 CSV 并在 Python 中复算时，必须保持字符串原貌，不得经 `int()` 转换后再回拼。
