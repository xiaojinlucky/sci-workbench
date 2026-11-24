# 效应量与置信区间选择矩阵

仅报告 $p$ 值无法回答“差异有多大”以及“估计有多确定”。本 Skill 要求每一项统计检验都与匹配研究设计的效应量和 95% 置信区间强绑定。

## 1. 效应量选择矩阵

| 分析场景 | 主效应量 | 定义与方向约定 | 经验量级参考（须结合领域最小重要差异） | 置信区间方法 |
| --- | --- | --- | --- | --- |
| 独立两组（参数） | **Hedges' $g$** | $g = \frac{\bar{x}_B - \bar{x}_A}{s_p} \cdot \left(1 - \frac{3}{4(n_A+n_B-2)-1}\right)$，正值表示 B 高于 A | $\|g\|$: 0.2 小，0.5 中，0.8 大 | 均值差使用精确 Welch-Satterthwaite $df_W$（方差不齐）或 Student $df$（方差齐）的 95% t 区间 |
| 独立两组（非参数） | **Cliff's $\delta$** 与 **Hodges-Lehmann Shift ($\hat{\Delta}_{HL}$)** | $\delta = \frac{\#(b_j > a_i) - \#(b_j < a_i)}{n_A n_B} \in [-1, 1]$（等价于秩双列相关 $r_{rb}$）；$\hat{\Delta}_{HL} = \text{median}(b_j - a_i)$ | $\|\delta\|$: <0.147 可忽略，0.147–0.33 小，0.33–0.474 中，$\ge 0.474$ 大 | $\hat{\Delta}_{HL}$ 采用 Moses 顺序统计量 95% 区间（$n_A n_B \le 50000$ 精确计算） |
| 独立多组（等方差 ANOVA） | **$\eta^2$** 与 **$\omega^2$** | $\eta^2 = SS_{\text{between}}/SS_{\text{total}}$；$\omega^2$ 校正小样本正向偏倚 | $\eta^2$: 0.01 小，0.06 中，0.14 大 | 事后对比逐对报均值差 95% CI 与 Hedges' $g$ |
| 独立多组（Welch's ANOVA） | **$\omega^2_{\text{est}}$** | $\max\left(0, \frac{df_1(F_{\text{Welch}}-1)}{df_1(F_{\text{Welch}}-1)+N}\right)$ | 同上，反映异方差全局解释比例 | 事后对比逐对报 Welch 95% CI 与 Hedges' $g$ |
| 独立多组（Kruskal-Wallis） | **$\epsilon^2$** | $\max\left(0, \frac{H - k + 1}{N - k}\right)$ | 0.01 小，0.06 中，0.14 大 | 事后对比逐对报 $\hat{\Delta}_{HL}$ 95% CI 与 Cliff's $\delta$ |
| 配对两组（参数） | **Hedges' $g_z$** | 差值均数除以差值标准差再乘小样本校正因子 $J(n-1)$ | $\|g_z\|$: 0.2 小，0.5 中，0.8 大 | 差值均值 $\bar{d} \pm t_{0.975, n-1} \cdot s_d/\sqrt{n}$ |
| 配对两组（非参数） | **匹配对秩双列相关 $r_{rb}$** 与 **Hodges-Lehmann 伪中位数** | $r_{rb} = \frac{W^+ - W^-}{W^+ + W^-}$；伪中位数为 Walsh 平均 $\frac{d_i+d_j}{2}$ ($i \le j$) 的中位数 | $\|r_{rb}\|$: 0.1 小，0.3 中，0.5 大 | Walsh 顺序统计量 95% 区间 |
| 双变量相关（Pearson） | **$r$** 与 **$R^2$** | 线性相关系数及决定系数 | $\|r\|$: 0.1 弱，0.3 中，0.5 强 | **精确 Fisher-z 变换 95% CI**：$\tanh\left(\text{atanh}(r) \pm \frac{1.96}{\sqrt{n-3}}\right)$ |
| 双变量相关（Spearman） | **$\rho$** | 单调秩相关系数（含结平均秩） | 同上 | Bonett-Wright 95% CI |
| 分类列联表（$R \times C$） | **Cramér's $V$**（含 Bergsma 偏倚校正 $V$） | $V = \sqrt{\frac{\chi^2}{N \cdot \min(R-1, C-1)}}$ | 依自由度解释，$df=1$ 时 0.1 小，0.3 中，0.5 大 | 2×2 表附 Odds Ratio 及 Woolf 95% CI（存在零单元格时显式披露 Haldane-Anscombe +0.5 校正） |

## 2. 置信区间的正确解读与常见误区

1. **区间含义**：95% 置信区间反映在重复抽样下估计量的不确定性范围，**不是**“95% 的个体观测值落在该区间内”（个体离散范围应看 SD、IQR 或 Min–Max）。
2. **与检验结论的一致性**：
   - 独立两组参数比较中，若均值差 95% CI（采用与检验一致的自由度 $df_W$ 或 $df$）不包含 0，则对应的双侧检验 $p_{\text{raw}} < 0.05$；若 CI 跨 0，则 $p_{\text{raw}} \ge 0.05$。
   - 多重比较场景下，报告的单个两两 95% CI 未做 Holm 校正时，须明确区分单次对比区间与多重校正后的 $p_{\text{holm}}$ / $q_{\text{BH}}$。
3. **小样本极宽区间的诚实提示**：当 `n = 2` 或 `n = 3` 时，$t_{0.975, df}$ 极大（$df=1$ 时为 12.71，$df=2$ 时为 4.30），95% CI 会非常宽；此时须明确指出估计精度不足，不得将其包装为确定结论。

## 3. 多重比较校正口径

- **Holm-Bonferroni（`p_holm`）**：控制家族错误率（FWER），在组数较少（如 3–6 组两两对比）时作为默认主校正指标，严格优于经典 Bonferroni（同样控制 FWER 但检验效能更高）。
- **Benjamini-Hochberg（`q_bh_fdr`）**：控制错误发现率（FDR），同步输出供探索性多指标筛选参考；同一结论不得在 Holm 不显著时悄悄切换为未校正 `p_raw` 宣称显著。
