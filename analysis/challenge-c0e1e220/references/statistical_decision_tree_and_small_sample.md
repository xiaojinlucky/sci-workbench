# 统计假设检验与小样本降级决策树

本参考文档定义了 `assumption_diagnostics.py` 与 `inference_effect_ci_runner.py` 的统计判定逻辑、数学公式与异常降级规则。

## 1. 前置清洗与推断单位锁（不可跳过）

在计算任何统计量之前，按以下顺序完成四项审计，且所有改动必须写入审计记录：

1. **推断单位核验（Inference Unit）**：
   - 确认表内每行是独立生物样本（例如 `sample_code`），还是同一样本的多次技术进样（例如 `hplc_injections` 的 `injection_id`），或是同一受试者的前后/多条件观测（例如 `paired_subject`）。
   - 技术重复不得直接作为独立样本进入组间检验；需按样本聚合后再做组间比较。
   - 同一受试者跨组出现时，须检查是否应走配对检验（`--design paired --subject-col`）以避免伪重复。
2. **精确重复行审计**：
   - 检测完全重复行并按首次出现顺序去重，显式记录 `rows_raw`、`exact_duplicates_removed` 与 `rows_after_dedup`。
3. **缺失值与真实零值分离**：
   - 缺失标记（空字符串、`NA`、`N/A`、`NaN`、`null`、`None`、`ND`、`.`、`-`）按缺失剔除，严禁以 `0` 填补。
   - 真实数值 `0.0` 完整保留并单独计数（`true_zero_count`）。
4. **编码整数列阻断（Encoded Integer Blocker）**：
   - 若数值列满足全为整数且命中以下任一条件，默认阻断将其作为连续结局计算均值/ANOVA/t 检验：
     - 列名匹配编码/标识/状态词尾（`code`, `id`, `flag`, `pass`, `status`, `label`, `class`, `batch`, `cultivar`, `species` 等）；
     - 存在前导零字符串标识（如 `"0001"`）；
     - 与表内某字符串标签列存在严格 1-to-1 双射（如 `cultivar_code` ↔ `cultivar_name`，`species_code` ↔ `species`）；
     - 仅取值 `{0, 1}` 的二元指示变量。
   - 阻断后建议改用真实连续测量列，或切换 `--design contingency` 进行分类关联检验。仅在人工确认该列为等距/有序量表分值时，允许显式传入 `--allow-encoded-integer`。

## 2. 假设诊断规则

### 2.1 正态性检验
- **条件**：仅对 `n >= 3` 且方差 `> 0` 的组执行。
- **双后端实现**：
  - 安装 SciPy 时调用 `scipy.stats.shapiro`（精确 Royston Shapiro-Wilk 算法）；
  - 纯标准库离线环境调用 Royston (1993) Shapiro-Francia 近似（Blom 正态分位数评分），并在 `normality.method` 如实标注 `stdlib_shapiro_francia_royston`。
- **判定**：任一组 `p < 0.05` 或存在极端 MAD 异常值（$|0.6745(x_i - \tilde{x})/\text{MAD}| > 3.5$）时，不采用依赖正态假定的参数检验作为主路线。

### 2.2 方差齐性检验（Brown-Forsythe）
- 采用**中位数中心化 Levene 检验（Brown-Forsythe）**：对每组计算 $z_{ij} = |x_{ij} - \text{median}(x_i)|$，再对 $z_{ij}$ 做单因素方差分析 $F$ 检验。相比均值中心化 Levene 与 Bartlett 检验，Brown-Forsythe 在偏态分布下第一类错误更稳健。
- `p < 0.05` 判为方差不齐，严禁使用等方差单因素 ANOVA 或 Student's t 检验。

## 3. 多组与两组精确路由树

### 3.1 独立多组（$k \ge 3$）
1. 所有组 `n >= 3`、方差 `> 0`、正态性通过、无极端 MAD 异常值，且方差齐（Brown-Forsythe `p >= 0.05`）→ **One-way ANOVA** + 事后 Student's t + Holm-Bonferroni & BH FDR。
2. 所有组 `n >= 3`、方差 `> 0`、正态性通过、无极端 MAD 异常值，但方差不齐（`p < 0.05`）→ **精确 Welch's ANOVA（Welch 1951）**：
   $$w_i = \frac{n_i}{s_i^2},\quad W = \sum_{i=1}^k w_i,\quad \bar{x}_w = \frac{\sum w_i \bar{x}_i}{W}$$
   $$F_{\text{Welch}} = \frac{\frac{1}{k-1}\sum w_i(\bar{x}_i - \bar{x}_w)^2}{1 + \frac{2(k-2)}{k^2-1}\sum \frac{(1 - w_i/W)^2}{n_i - 1}},\quad df_1 = k-1,\quad df_2 = \left[\frac{3}{k^2-1}\sum \frac{(1 - w_i/W)^2}{n_i - 1}\right]^{-1}$$
   事后两两比较使用**精确 Welch-Satterthwaite 自由度 t 检验**与 Holm-Bonferroni / BH 校正。
3. 任一组非正态、含极端 MAD 异常值、存在 `n < 3` 或零方差退化组 → **带结校正的 Kruskal-Wallis H 检验** + 事后两两 Mann-Whitney U（Holm & BH 校正）+ Hodges-Lehmann 中位数差 + Cliff's $\delta$。

### 3.2 独立两组（$k = 2$）
- 正态 + 方差齐 → Student's t（$df = n_1 + n_2 - 2$）。
- 正态 + 方差不齐 → Welch's t，精确自由度：
  $$df_W = \frac{(s_1^2/n_1 + s_2^2/n_2)^2}{\frac{(s_1^2/n_1)^2}{n_1 - 1} + \frac{(s_2^2/n_2)^2}{n_2 - 1}}$$
- 非正态 / 异常值 / 退化组 → Mann-Whitney U + Hodges-Lehmann Shift + Cliff's $\delta$。

### 3.3 配对两组（`--design paired`）
- 仅纳入在两个条件下均有有效观测的受试者计算差值 $d_i = x_{i,2} - x_{i,1}$；缺失配对的受试者显式列出 `n_unpaired_excluded`，严禁插补。
- 若差值近似正态且无极端异常值 → **配对 t 检验**（$df = n_{\text{pairs}} - 1$，Hedges' $g_z$）。
- 否则 → **Wilcoxon 符号秩检验**（$n_{\text{nz}} \le 12$ 使用纯标准库精确符号枚举 $p$ 值，更大样本使用结校正渐近 $p$ 值）+ Hodges-Lehmann 伪中位数 + 匹配对秩双列相关 $r_{rb}$。

### 3.4 相关性与列联表
- **双变量相关（`--design correlation`）**：双变量正态且无极端 MAD 异常值主报 Pearson $r$（精确 Fisher-z 95% CI 与 $R^2$），否则主报 Spearman $\rho$（带结平均秩与 Bonett-Wright 95% CI），同时附另一指标作稳健性对照。
- **分类列联表（`--design contingency`）**：输出观测频数与期望频数矩阵；2×2 表只要任意单元格期望频数 $< 5$ 即主路由精确 **Fisher 精确检验**（纯标准库超几何精确和）并报 Odds Ratio 95% CI；更大列联表报 Pearson $\chi^2$ 与 Cramér's $V$，并对期望频数 $< 5$ 的单元格数发出预警。

## 4. 小样本、零方差与缺格降级协议（`estimability_status`）

| 退化情形 | 组/对比状态码 | 处理方式 | 禁止行为 |
| --- | --- | --- | --- |
| 空组（`n = 0`） | `UNESTIMABLE_EMPTY_CELL` | 仅保留缺失计数，不参与检验 | 不得用 0 填充空组 |
| 单样本组（`n = 1`） | `UNESTIMABLE_SINGLETON` | 报告原始单值，排除出方差检验与两两对比 | 不得伪造 `sd=0` 强跑 t 检验 |
| 极小样本（`n = 2`） | `DEGRADED_SMALL_N` | 跳过正态检验，标记降级并路由至非参数秩检，注明统计效能不足 | 不得宣称“通过正态性检验” |
| 零方差组（`sd = 0`） | `DEGRADED_ZERO_VARIANCE` | Welch 权重与合并标准差无定义时显式返回 `hedges_g=None`，不除零，降级至秩检并说明常量组特征 | 严禁以 `max(pooled_sd, 1e-9)` 制造 $10^9$ 级虚假效应量 |
| 可估计组 `< 2` | `UNESTIMABLE_INSUFFICIENT_GROUPS` | 仅输出描述统计与缺口说明 | 不得输出虚构的 omnibus p 值 |
