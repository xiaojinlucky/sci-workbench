# 数据结构到图型选型决策树与五项 Gate

本文档给出从原始科研数据表到发表级图型的选型映射、拒绝反例依据及五项候选 Gate 审查标准。

## 1. 数据结构 × 分析目的 → 图型选型决策树

| 数据结构（`data_kind`） | 分析目的（`analysis_goal`） | 推荐主图型 | 视觉编码依据 | 明确不推荐的图型及原因 |
| --- | --- | --- | --- | --- |
| 分类 × 连续（`categorical_continuous`） | 组间比较（`group_comparison`） | `boxplot_points`（偏态/跨零/含离群点优先）或 `bar_with_points`（同号且需强调零基线均值） | 位置（最精确感知通道）+ 原始散点呈现真实分布与样本量 | 饼图/环形图（角度与面积精度远逊位置）；仅画均值的柱状图（隐藏样本量、双峰与离群值）；截断基线柱状图（夸大相对差异） |
| 分类 × 连续（`categorical_continuous`） | 分布形态（`distribution`） | `boxplot_points` | 中位数、四分位距、须线与全部原始观测点同时可见 | 纯误差棒折线图（暗示连续顺序）；仅均数 ± SD（偏态分布下均数失真） |
| 有序时间/剂量 × 连续（`time_series`） | 趋势/轨迹（`trend` / `trajectory`） | `line_with_band` | 折线斜率与连续阴影带直观表达单调变化与不确定性 | 并列柱状图（割裂连续时间轴并引入大量冗余柱墨迹） |
| 连续 × 连续（`bivariate_continuous`） | 相关性/回归（`correlation`） | `scatter_regression` | 二维笛卡尔位置直接揭示线性度、杠杆点与分群结构 | 离散分箱柱状图（损失连续信息并制造人为断点） |
| 多自变量连续（`multivariate_continuous`） | 多元回归与偏效应（`multivariable_regression`） | `multivariate_ols` | 左面板（a）标准化系数森林图含 95% HC3 CI 与 VIF + 右面板（b）Observed vs. Predicted 含 $R^2_{\text{adj}}$ 与 JB/BP 假设注记 | 仅挑单个自变量画双变量散点（遗漏混杂控制与共线性诊断） |
| 多变量数值矩阵（`matrix`） | 相关性/模式（`correlation` / `pattern`） | `heatmap` | 均匀单调色阶或零中心发散色阶 + 单元格精确数值标注 | 雷达图/蜘蛛图（多边形面积随变量排序剧烈畸变）；`jet`/`rainbow` 伪彩（亮度非单调产生虚假边界） |

## 2. PlotAgent 五项候选 Gate（全部通过方可交付）

1. **Gate 1 — 科学语义与数据保真（Data Fidelity）**：
   - 必须读取实际源表（CSV / JSON / 只读 SQLite）并记录 SHA-256；
   - 精确去重并记录去重条数，缺失值按缺失剔除（严禁填 0）；
   - 检测是否存在负值或跨零分布（`value_min < 0 < value_max`），锁定统计单位与测量单位。
2. **Gate 2 — 图型语法与反例拦截（Chart Grammar）**：
   - 硬拦截 `pie`、`donut`、`3d_bar`、`3d_pie`、`radar`；
   - 硬拦截截断柱状图基线（`truncate_y_baseline=true`）；
   - 硬拦截 `n >= 3` 时隐藏原始散点的纯均值柱状图（`show_points=false`）；
   - 硬拦截连续时间序列误用柱状图；类别数 `> 10` 时预警拥挤并建议分面或横向布局。
3. **Gate 3 — 色彩语义与 WCAG/CVD/灰度审核（Color & Accessibility）**：
   - 硬拦截 `jet`、`rainbow`、`hsv`、`turbo` 等彩虹伪彩；
   - 分类色板经 Protanopia/Deuteranopia/Tritanopia 线性 RGB 模拟计算 CIELAB $\Delta E_{76}$，红绿色盲下任一色对 $\Delta E < 9$ 直接阻断；
   - 顺序色板校验灰度相对亮度单调性；热图数字标注按 WCAG 2.1 对比度自动择优 `#111827` 或 `#FFFFFF`。
4. **Gate 4 — Cell 视觉基线与前端纯净化（Canvas, Typography & Semantics）**：
   - 物理宽度锁定 `85 / 114 / 174 mm`，字号 `7–12 pt`，`bbox_inches=None`（禁用 `tight` 破坏物理尺寸）；
   - 标题采用常规字重、相对 panel 绘图区水平居中（`loc="center"`）；
   - 图面零后端词泄露（严禁 `selected_flag`、`pass_filter`、`qc_branch`、`route_enum` 或后端名称上画布）。
5. **Gate 5 — 源数据到视觉元素逐项回读对账（Source-to-Visual Reconciliation）**：
   - 渲染落盘后从磁盘重新解析已写出的 `<stem>.svg` 几何坐标或单元格文本，反解回数据单位并与清洗源数据逐项比对计算真实 `max_abs_diff`（严禁硬编码 `0.0`），核对 `plotted_points_count == clean_source_points`、`points_clipped_outside_ylim == 0`、`all_error_bars_within_ylim == true`；
   - 同步输出独立可读的 `<stem>_caption.md` 与支持 CSV/JSON/只读 SQLite 的线性复现脚本 `reproduce_figure.py`。

## 3. A / B / C 三事实面契约

每次出图在 `<stem>_audit.json` 与 `viz_selection.json` 中固化三份事实面：
- **A (`scientific_invariants`)**：源表名、SHA-256、列映射、单位、缺失/去重策略、跨零标记（修图过程永不改变）；
- **B (`visual_grammar_retained_or_redesigned`)**：选定图型、备选列表、色彩角色、色板、标题居中与图例净距策略；
- **C (`surface_specific_overrides`)**：目标承载面（`paper | presentation | standalone`）、最终物理尺寸、字号、字体嵌入与导出参数。
