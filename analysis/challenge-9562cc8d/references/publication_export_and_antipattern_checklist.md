# 发表级导出与反例审查清单

在交付任何科研数据图前，按本清单逐项核验。凡命中阻断项，必须修改配置或脚本重新渲染并重验，严禁靠口头解释放行。

## 1. 八大致命反例拦截表

| 编号 | 致命反例 | 典型失真后果 | 本 Skill 的自动检测与修复 |
| --- | --- | --- | --- |
| V1 | 含负值或跨零数据仍硬写 `ylim(0, ...)` | 负值柱体与原始散点被彻底切掉，读者误以为负效应不存在 | `_compute_safe_ylim` 覆盖全部原始点、误差棒与 0 基线，绘制 `y=0` 虚线基准，对账检查 `points_clipped_outside_ylim == 0` |
| V2 | 柱状图上界仅按 `mean + sd` 设定而忽略离散高值点 | 偏态/离群原始点飞出画布上界被静默截断 | `extrema` 显式纳入 `all_pts` 与 `[m-err, m+err]` 并留出安全余量，`all_raw_points_within_ylim` 强校验 |
| V3 | 仅画均值柱状图，不叠加原始散点（`n >= 3`） | 隐藏样本量过小、双峰分布或极端离群点 | Gate 2 阻断 `show_points=false`；默认固定随机种子叠加抖动原始观测点并在横轴标注 `n=` |
| V4 | 源表含精确重复行或缺失值却直接作图 | 重复行虚增样本量，缺失值若被填 0 会拉偏均值与轴范围 | 渲染前精确去重、按缺失剔除并把 `rows_raw`、`exact_duplicates_removed`、`missing_excluded` 写入对账与图注 |
| V5 | 无序分类画饼图或时间序列画并列柱 | 误导视觉比例或割裂时间趋势 | Gate 2 拦截 `pie`/`donut`/`3d_bar`/`radar` 及 `time_series + bar`，路由至推荐图型 |
| V6 | 使用 `jet` / `rainbow` 或红绿不可分色板 | 色盲读者与黑白打印无法区分分组或定量高低 | `palette_cvd_auditor.py` 拦截彩虹色标并计算 Protan/Deutan/Tritan $\Delta E_{76}$ 与灰度单调性 |
| V7 | 图面出现后端内部词、截断标签或 `bbox_inches="tight"` | 暴露内部调试词，物理栏宽被 `tight` 改写导致投稿排版不一致 | Gate 4 扫描画布可见文案并锁定 `bbox_inches=None`、`pdf.fonttype=42`、`loc="center"` |
| V8 | 图注缺失单位、样本量或误差棒定义 | 读者脱离正文无法判断误差棒是 SD、SEM 还是 95% CI | 自动生成独立可读的 `<stem>_caption.md`，强制列出单位、每组 `n`、误差棒定义、统计口径与源表 SHA-256 |

## 2. 发表级导出核对单

1. **格式与物理尺寸**：
   - 纯标准库环境必产可编辑矢量 `<stem>.svg`（含明确 `width="...mm" height="...mm"` 与完整 `viewBox`）；
   - 安装 Matplotlib 时同步产出 `300 dpi` `<stem>.png`、可编辑字体 `<stem>.pdf`（`pdf.fonttype=42`）与 `<stem>.svg`（`svg.fonttype="none"`），三者共享同一物理尺寸与同一组 `actual_ylim`。
2. **源到图逐项对账（`<stem>_audit.json`）**：
   - 核对 `source_to_visual_reconciliation.reconciled == true`；
   - 核对 `plotted_points_count == clean_source_points`；
   - 核对 `points_clipped_outside_ylim == 0` 且 `all_error_bars_within_ylim == true`。
3. **视觉读回与诚实边界**：
   - 在支持读图的宿主环境中打开导出 PNG/SVG 核查标签防重叠、标题居中、图例净距与信息密度；
   - 在纯终端无视觉读回能力的离线容器中，如实记录“已完成确定性几何、对账与 WCAG/CVD 校验，但未执行人工/视觉模型目检”，不伪造视觉目检通过。
