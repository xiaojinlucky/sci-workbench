# 防误用检查单与结果表述规范

本清单用于在统计分析完成、交付报告与 `result.json` 前逐项核查常见科研统计失误，确保输出达到“结果准确、依据完整、表述克制、他人可复跑”的标准。

## 1. 十大高频统计误用防线

| 编号 | 常见错误 | 本 Skill 的硬拦截与处置 |
| --- | --- | --- |
| M1 | 缺失值被静默填补为 `0` | 仅识别显式缺失标记并剔除计数，真实 `0.0` 单独保留统计 |
| M2 | 精确重复行未去重导致样本量虚增 | 全列精确去重并报告 `rows_raw`、`exact_duplicates_removed`、`rows_after_dedup` |
| M3 | 对编码整数列（如 `cultivar_code`、`qc_pass`）求均值或做 ANOVA | `detect_encoded_integer_columns` 自动阻断并提示改用连续结局或列联表 |
| M4 | 异方差多组数据仍套用等方差单因素 ANOVA | Brown-Forsythe `p < 0.05` 时路由至精确 Welch's ANOVA（Welch 1951）与精确 Welch-Satterthwaite $df$ |
| M5 | 非正态或含重尾异常值的小样本仍用均数 ± SD 与 t 检验 | 自动路由 Kruskal-Wallis / Mann-Whitney U + Hodges-Lehmann Shift + Cliff's $\delta$，图上展示中位数/IQR 与全部原始散点 |
| M6 | 零方差组触发除零崩溃或 $10^9$ 级伪 Hedges' $g$ | 检测到 `sd == 0` 即标 `DEGRADED_ZERO_VARIANCE`，`hedges_g` 置 `None`，拒绝除 `1e-9` |
| M7 | 多组两两比较不做多重检验校正 | 默认同时输出 `p_raw`、`p_holm`（FWER）与 `q_bh_fdr`（FDR），显著性判定绑定校正后阈值 |
| M8 | 仅报 $p$ 值不报效应量与置信区间 | 强制绑定主差异量、95% CI、标准化/非参数效应量与方向（`B minus A`） |
| M9 | 配对数据或技术重复直接按独立样本检验 | 审计 `--subject-col` 跨组重叠并提示使用 `--design paired` 或按样本聚合 |
| M10 | 仅在作者本机依赖完整包跑通，离线精简环境报错 | Dual-Backend 架构在纯 Python 3.12 标准库下零依赖完成统计、脚本与矢量 SVG 产出 |

## 2. 前端科学表达与后端状态分离规范

面向科研读者的报告正文、表格、图表和摘要应严格遵守领域语言与克制原则：

1. **禁止后端内部词泄露到结论与图面**：
   - 图标题、坐标轴、图例与报告核心结论应使用科学变量名、单位与研究分组（例如 `Proline (mg/L) across Cultivar`），不要把 `route=parametric_welch_anova`、`DEGRADED_SMALL_N`、`stdlib_shapiro_francia` 直接塞进图表画布或当作科学结论标题。
   - 技术诊断、后端模式与校验哈希放在方法注记、技术诊断附录或 `stats_report.json` 中，确保可追溯而不污染主叙述。
2. **禁止把显著性写成因果性**：
   - 禁止表述：“p = 0.02 证明药物有效 / 导致指标升高”。
   - 推荐表述：“在当前观测样本中，Drug_Low 组较 Vehicle 组的中位差值（Hodges-Lehmann 估计）为 +2.85（95% CI [1.30, 23.20]，Cliff's $\delta = 1.00$，Holm 校正 $p = 0.048$）；该结果支持组间存在显著差异，但受限于小样本与高离散度，其实际临床/生物学意义与因果机制仍需独立验证。”
3. **禁止把不显著写成无差异或等价**：
   - 推荐表述：“未观察到达到多重校正阈值（$p_{\text{holm}} < 0.05$）的统计学差异（$p_{\text{holm}} = 0.24$，95% CI [-1.8, 6.4]）；置信区间较宽，尚不能排除中等幅度效应。”
4. **四种状态严格区分**：
   - **未检验（Untested / Unestimable）**：因 `n < 2`、空组或编码阻断未执行检验；
   - **缺失（Missing）**：原始记录无有效数值，明确写明缺失条数；
   - **真实零值（True Zero）**：观测值为 `0.0`，计入样本量；
   - **未显著（Not Significant）**：已完成检验但未达预设校正阈值。
5. **复现证据闭环**：
   - 每次分析必须保留：输入文件名与 SHA-256、清洗规则与样本量流转、实际软件版本与后端标识、独立可运行的 `reproduce_stats.py`，以及覆盖全部原始散点的统计图。
