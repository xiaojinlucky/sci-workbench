# 合法空组与异常空查询鉴别及复算对账协议

在科学数据库分析中，“查出 0” 存在截然不同的两种语义：一种是实验设计或维度表本身存在的**合法零样本组**，另一种是过滤条件写错、大小写失配或连接键损坏导致的**异常空查询**。混淆二者会直接导致错误删除对照组或掩盖查询故障。

## 1. 两类空结果的三态判定矩阵

`sql_cross_table_runner.py` 对每条成功执行的查询给出明确 `empty_set_classification`：

| 判定状态 | 触发条件 | 科学含义与处置 | 是否视作查询失败 |
|---|---|---|---|
| `NON_EMPTY_STANDARD` | 返回 `>= 1` 行，且全部组均有 `count > 0` | 常规有观测结果集；继续进入膨胀守卫与 Python 复算对账 | 否（正常通过） |
| `LEGITIMATE_EMPTY_GROUP` | 返回 `>= 1` 行，存在来自维度表 `LEFT JOIN` 的分组满足 `n_samples == 0` 且对应连续量聚合为 `NULL`（如 `Nebbiolo_Control_Empty`） | 维度中确实存在尚未观测/零样本的合法对照组。**必须保留该行**，将均值/标准差保留为 `null`（**严禁把空组均值填成 `0.0`**），并在图表和报告中显式标注 `n=0 (empty group)` | 否（正常通过并显式记录 `empty_groups`） |
| `ANOMALOUS_EMPTY_RESULT` | 整条查询返回 `0` 行（且未显式声明 `allow_empty_result: true`） | 高度疑似 `WHERE` 过滤值拼写/大小写错误、前导零 ID 失配、错误的 `INNER JOIN` 键或矛盾筛选条件。自动触发诊断探针：检查涉及表是否非空、核对 `WHERE col = 'lit'` 的字面值是否存在于目标列并给出近似值建议 | **是（阻断通过，进入报错自定位修复）** |

> 例外情况：若某条质控核查查询的预期结果本身就是 0 行（例如“查找外键孤儿记录”或“查找超出合理范围的异常值”），应在 `query_spec` 中显式设置 `"allow_empty_result": true`，运行器将其标注为 `EXPECTED_EMPTY_AUDIT` 并正常通过。

## 2. SQL 报错自定位协议（Error Self-Localization）

当 SQLite 抛出 `OperationalError` 时，严禁盲目重写整段 SQL。`sql_cross_table_runner.py` 自动执行三步定位：
1. 捕获完整原始错误消息（保留逐字原文）；
2. 从报错正则提取缺失的表名、列名或歧义列名（`no such column: ...`、`no such table: ...`、`ambiguous column name: ...`）；
3. 基于当前数据库实测 Schema 使用 `difflib.get_close_matches` 推荐最接近的真实表名/列名，并列出该表可用字段，写入 `self_diagnosis` 供下一轮精准修复（单条查询最多自动修复 2 轮）。

## 3. 独立原生 Python 复算对账（`max_abs_diff < 1e-9`）

为彻底排除 SQL 逻辑隐错（如错误分组键、遗漏 `LEFT JOIN`、把 `NULL` 混入分母或 1:N 重复加权），关键汇总查询必须配置 `reconcile` 块，由原生 Python（不复用同一条 SQL 聚合语句）独立从原始基础表重新计算并对账：

- **独立读取**：直接对 `base_table`（及可选 `dimension_table`、`child_table`）执行最简全表只读读取 `SELECT * FROM ...`；
- **独立聚合**：在纯 Python 中按 `group_key` 组织列表，如有 `1:N` 子表则先按 `base_pk` 聚合成 1:1 字典，再用 `statistics.fmean` / `sum` / `len` 计算每组指标；
- **双向严格核对**：
  1. **分组键集合一致**：`set(sql_groups) == set(python_groups)`（防止 `INNER JOIN` 丢掉合法空组）；
  2. **计数完全一致**：每组 `sql_count == python_count`；
  3. **空值语义一致**：`None` 只能与 `None` 匹配，绝不把 `None` 与 `0.0` 判为相等；
  4. **浮点精度阈值**：全部非空数值指标满足 `abs(sql_val - py_val) < 1e-9`（记录实测 `max_abs_diff`）。

只有当分组集合、样本计数、空值语义与浮点精度四项全部通过时，`python_reconciliation.passed` 才记为 `true`。
