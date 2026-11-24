# SQL 跨表 JOIN 防膨胀模式与 CTE 改写手册

跨表分析中最隐蔽的科学错误不是语法报错，而是 **1 对多（1:N）连接导致的静默行数膨胀与不均匀加权**。本手册给出基数推断标准、膨胀检测公式及可直接套用的先聚合 CTE 改写模式。

## 1. 自动关系基数推断（`1:1` vs `1:N`）

在编写跨表 SQL 前，先读取 `sqlite_schema_inspector.py` 输出的 `relationship_cardinalities`。对每一条关联边 `(child_table.fk_col -> parent_table.pk_col)`，系统实测每个父键对应的子表行数分布（`min_children`, `median_children`, `max_children`）：

- **`1:1`（安全直接连接）**：`max_children <= 1`，每个父记录最多对应 1 条子记录，`LEFT JOIN` 不会增加基准表行数。
- **`1:N`（高风险，强制先聚合）**：`max_children > 1`（例如 `wine_samples.sample_code -> hplc_injections.sample_code`，178 个样本对应 356 次进样，单样本进样次数在 1 到 3 次之间不等），标记 `join_inflation_risk = true`。

### 为什么直接 `JOIN` 会摧毁科学结论？

假设按 `cultivar_code` 计算样本平均酒精度 `AVG(w.alcohol_pct)`，同时想带入各样本的 HPLC 进样质控通过率：

```sql
-- [ANTI-PATTERN: 严禁使用！存在 1:N JOIN 静默膨胀与加权畸变]
SELECT
    c.cultivar_label,
    COUNT(*) AS n_samples,          -- 实际返回 356 而非 178（膨胀 2.0x）
    AVG(w.alcohol_pct) AS avg_alc   -- 进样 3 次的样本权重是进样 1 次样本的 3 倍！
FROM cultivars c
LEFT JOIN wine_samples w ON c.cultivar_code = w.cultivar_code
LEFT JOIN hplc_injections h ON w.sample_code = h.sample_code
GROUP BY c.cultivar_code, c.cultivar_label;
```

该查询**不会报任何 SQL 错误**，但 `n_samples` 从 178 虚增至 356，且每个品种的 `avg_alc` 被重复进样次数静默扭曲。

## 2. 标准解法：先聚合 CTE 再关联（Pre-Aggregate-Then-Join Pattern）

将多对一子表先按父表主键粒度聚合到 **严格 1 行/主键**，再与主表做 `LEFT JOIN`：

```sql
-- [SAFE PATTERN: 先按 sample_code 聚合子表到 1:1，再与样本表和品种维表关联]
WITH hplc_per_sample AS (
    SELECT
        sample_code,
        COUNT(*) AS n_injections,
        SUM(qc_pass) AS n_qc_pass,
        AVG(CAST(qc_pass AS REAL)) AS sample_qc_pass_rate,
        AVG(retention_drift_s) AS mean_retention_drift_s
    FROM hplc_injections
    GROUP BY sample_code
)
SELECT
    c.cultivar_code,
    c.cultivar_label,
    COUNT(w.sample_code) AS n_samples,
    AVG(w.alcohol_pct) AS mean_alcohol_pct,
    COALESCE(SUM(h.n_injections), 0) AS total_injections,
    AVG(h.sample_qc_pass_rate) AS mean_sample_qc_pass_rate
FROM cultivars c
LEFT JOIN wine_samples w
    ON c.cultivar_code = w.cultivar_code
LEFT JOIN hplc_per_sample h
    ON w.sample_code = h.sample_code
GROUP BY c.cultivar_code, c.cultivar_label
ORDER BY c.cultivar_code;
```

核对不变量：
1. `hplc_per_sample` 的主键 `sample_code` 唯一（178 行）；
2. 所有分组的 `SUM(n_samples) == 178`（严格等于 `wine_samples` 基准行数）；
3. `mean_alcohol_pct` 在样本粒度等权计算，与单表 `wine_samples` 聚合结果逐位一致（差值 `< 1e-9`）；
4. 维表 `cultivars` 中无样本的对照品种（如 `cultivar_code = 4`）通过 `LEFT JOIN` 完整保留为 `n_samples = 0, mean_alcohol_pct = NULL`。

## 3. `sql_cross_table_runner.py` 的双重膨胀守卫

运行器同时覆盖**明细查询**与**汇总查询**两类膨胀场景：

1. **明细级守卫**：若查询输出包含基准主键列 `base_pk`（如 `sample_code`），自动对比 `returned_rows` 与 `COUNT(DISTINCT base_pk)`；一旦 `returned_rows > distinct_base_pk`，立即置 `join_inflation_detected = true`、报告膨胀倍率 `inflation_ratio` 并附带上述 CTE 改写提示。
2. **汇总级守卫**：对含分组计数列（如 `n_samples` / `sample_count` / `count_col`）的查询，自动对比 `SUM(count_col)` 与基准表真实行数 `base_table_rows`（以及可选 `pre_agg_probe_sql` 的 `COUNT(*)` vs `COUNT(DISTINCT base_pk)`）；一旦超出基准行数，立即阻断并报告具体膨胀量。
3. **查询计划留痕**：每条查询自动捕获 `EXPLAIN QUERY PLAN`，记录索引命中与全表扫描细节，便于复核执行路径。


## Single-Table Continuous Variable Stratification / Binning Pattern (Concrete-Style)

When analyzing a continuous single-table SQLite snapshot (such as `concrete_mixes` with 1,030 rows and no categorical foreign-key table), avoid averaging continuous predictors without stratification or dividing by zero:

```sql
WITH binned AS (
    SELECT
        mix_id,
        CASE
            WHEN age_days <= 7 THEN '01_early_le_7d'
            WHEN age_days <= 28 THEN '02_standard_8_28d'
            ELSE '03_mature_gt_28d'
        END AS age_stratum,
        water_kg_m3 / NULLIF(cement_kg_m3, 0.0) AS wc_ratio,
        compressive_strength_mpa
    FROM concrete_mixes
)
SELECT
    age_stratum,
    COUNT(*) AS n_samples,
    ROUND(AVG(compressive_strength_mpa), 4) AS mean_strength_mpa,
    ROUND(AVG(wc_ratio), 4) AS mean_wc_ratio
FROM binned
GROUP BY age_stratum
ORDER BY age_stratum;
```

Always assert `SUM(n_samples) == COUNT(*)` of the base table (no rows lost in `CASE` boundary gaps) and cross-check every stratum mean independently in Python (`max_abs_diff < 1e-9`).
