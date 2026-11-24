---
name: methods-stats-audit
description: Zero-dependency pure-stdlib StatCheck exact p-value engine (Lentz incomplete Beta/Gamma < 1e-9 accuracy), effect-size/95% CI consistency checker, GRIM/group-N conservation & pseudoreplication detector, and 5-facet methodology auditor separating under_reported from method_flaw and decision_error.
---

# 研究方法与统计审查 (challenge-9e7302fd)

本 Skill 面向科研稿件的方法与统计质量审查，融合 CNS 审稿视角（推断单位与筛选漏斗）和确定性数学复算，产出一份能直接交给作者逐条回应的审查单：指出精确章节/行号/原句、区分「计算/决策硬错」「方法学缺陷」与「未报告缺口」、提供可复算依据和可执行修订指令。

## 适用边界与不负责范围

- **负责**：研究设计、变量定义、统计方法、样本量与效能、结果报告五维审查；`t / F / chi2 / r / z` 精确双尾/单尾 p 值复算（严格不等式边界）；效应量与 95% CI vs p 值自洽核验；GRIM（仅限离散整数评分量表）、整数比例 `k/n (pct%)` 与分组样本量守恒；伪重复（Pseudoreplication）与推断单位筛查；可选 p-curve 群体分布诊断（严格仅 `n_significant >= 5`）。
- **不负责**：文献检索与综述、全文翻译、语言润色、代写录用/拒稿结论、在无原始数据下对单篇论文定性学术不端或 p-hacking。

## 核心规则与三分边界铁律

1. **纯标准库高精度数学引擎（禁裸依赖崩溃）**：
   - `stat_math_core.py` 以 Modified Lentz 连续分数法实现正则不完全 Beta/Gamma 与 Acklam+Halley 正态分位数（精度 `< 1e-9`），在 `python:3.12-slim --network none` 无 `scipy/numpy` 镜像中零崩溃稳定运行；若沙箱装有 `scipy` 则优先调用并记录交叉验算最大误差。
2. **段落级 PDF 折行合并、多期刊格式 & 统计量舍入区间不等式判定**：
   - 先通过 `review_io_core.merge_paragraph_lines()` 合并 PDF 跨行折行（保留精确 `line_start..line_end`），支持 APA 括号式（`t(28) = 1.98, p < 0.05`）、非 APA 写法（`t = 1.98, df = 28, p = 0.012`、`F = 4.95, df = 2, 57, p < 0.01`）以及前导小数点 `P = .018`。
   - 对 `p < x` / `p <= x` 引入统计量四舍五入下界 `p_lo`：若点值 `p_calc >= p_rep` 但 `p_lo < p_rep`（如真实 `t(100)=1.9845` 保留两位写 `t(100)=1.98, p < 0.05`，`p_lo=0.04988 < 0.05`），判 `rounding_ambiguous`（`minor | needs_human_review`，绝不误杀 `CRITICAL`）；仅当 `p_lo >= p_rep`（如 `t(28)=1.98, p < 0.05` 其 `p_lo=0.057 >= 0.05`）才判确凿 `decision_error`；对 `p > x` / `p >= x` 对称核验 `p_hi`。同时捕获 `p = 0.000` 违规写法并提示改为 `p < 0.001`。
3. **效应量、95% CI 与 p 值自洽核验（兼容 NEJM/JAMA/Lancet 与 APA）**：
   - 解析比值类（`OR/aOR/HR/aHR/RR/IRR`，零值 `1.0`）与差值/回归类（`beta/MD/SMD/Cohen's d/Hedges' g`，零值 `0.0`）及其 `95% CI` 与 `p`，同时支持 APA 冒号括号格式与医学顶刊逗号格式（如 `(OR, 1.48; 95% CI, 0.91-2.41; P = .018)`、`HR, 2.35; 95% CI, 1.10 to 1.95; P = 0.009`）。
   - 抓出：CI 上下界倒置、点估计落在 CI 之外、比值类 CI 严格跨 `1.0` 却报 `p < 0.05`、差值类 CI 严格跨 `0` 却报 `p < 0.05`（以及无多重校正说明时的反向矛盾）。
   - **反误报保护**：若同行明确说明 `Bonferroni/FDR/adjusted p` 且仅出现“未校正 95% CI 不跨零而校正后 `p > 0.05`”，判定为 `consistent_adjusted_p`，绝不误报！
4. **CNS 推断单位、筛选漏斗、GRIM 泛化与 CSV 表核验**：
   - **伪重复筛查与观察性范围隔离**：比对 `df` 与生物独立单位 `N`（动物/患者/供体）；若 `N = 6` 小鼠却报 `t(598)` 且提及细胞/切片/视野而无嵌套/混合效应模型，标为 `method_flaw`（`major`）。观察性研究判定仅扫描 Abstract 与 Methods 非局限/非前人引用句，杜绝 Discussion 写 `Future RCT` 击穿观察性因果检测。
   - **整数比例、自然句式 GRIM、互斥分组守恒与 `.csv` 对账**：核对 `k/n (pct%)` 与 `100*k/n` 偏差；分组守恒仅在明确互斥分组分配语境（`divided/allocated/randomized/assigned/split into`、`arms/groups:`、干预/对照臂）下触发，排除 `including older adults (n=40) and smokers (n=35)` 非互斥亚组误报；GRIM 检验严格限定离散/李克特/评分量表（支持自然科研句式如 `Mean Likert score in N = 12 participants was 3.47`），严禁对连续物理量误跑；若输入含 `.csv` 文件，自动执行行数/列数/缺失值/分组计数与正文总 `N` 对账并写入 `csv_checks`。
5. **`under_reported` vs `method_flaw` vs `decision_error` 严格三分（核心反误报红线）**：
   - `decision_error`（`critical`/`major`，`factual_error`）：确凿数学/统计/CI/比例/守恒硬错误（如显著性翻转、点估计在 CI 外、比例算错）。
   - `method_flaw`（`major`，`insufficient_evidence`/`factual_error`）：有明确稿件证据的方法学错配（如纯观察性研究下强因果断言、疑似将细胞/技术重复当独立动物单位的伪重复、`df > N` 且无重复测量模型）。
   - `under_reported`（`minor`/`suggestion`，`insufficient_evidence` + 附 `missing_inputs` 与 `unassessable_items`）：作者未交代的细节（如报告 `>= 3` 个检验但未说明多重比较校正、缺样本量效能依据、缺效应量/95% CI、缺缺失值处理）。**严禁把“未报告多重比较校正且缺原始数据”直接判为 p-hacking 或方法做错！**
   - 可选 `phack_detect.py`（同样已纯标准库重构）仅在提取到 `n_significant >= 5` 个精确 `p = ...` 时运行，且结果仅作为描述性参考放入不确定性区，绝不据此指控单篇稿件不端。

## 运行环境与输出契约约定

- 工作区：`/app/work`；最终交付目录：`/app/output/`；只读输入：`/input/case.json`、`/input/task.md` 及稿件/附表文件。
- **`result.json` 双状态兼容约定**：
  - 顶层 `status` 必须为 `ok` 或 `partial_with_gaps`（通过 `result_guard.py --finalize` 与 `final_audit.py` 门禁）；
  - 顶层同步保留 `audit_status`（`completed` | `partial` | `indeterminate` | `failed`）、`case_id`、`sources`、`uncertainty`、`gaps`、`artifacts`、`metrics`、`metric_provenance`、`findings` 与 `methodology_coverage` 摘要。

## 标准执行流程（DeepSeek Harness）

### Step 1：环境探测、预算初始化与输入索引
```bash
python -B /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B /skills/weekly-skill/scripts/budget_guard.py --state /app/work/budget.json --init
python -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-9e7302fd"
python -B /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```
读取 `/input/case.json` 与 `/input/task.md`，锁定 `case_id` 和题面特定要求。

### Step 2：运行确定性方法与统计审计引擎（同步直出 `result.json`）
```bash
python -B /skills/weekly-skill/scripts/statcheck_and_phack_auditor.py \
  --input /input \
  --out-json /app/output/stats_audit.json \
  --out-md /app/output/stats_review_report.md \
  --out-result-json /app/output/result.json
```
检查 stdout 摘要与 `/app/output/stats_audit.json`，确认 `math_backend`、`statcheck_results`、`effect_ci_checks`、`proportion_checks`、`grim_checks`、`pseudoreplication_flags`、`csv_checks`、`methodology_coverage` 与 `gaps`；`--out-result-json` 已自动写入双兼容 `/app/output/result.json`（含完整 `metrics` 与 `metric_provenance`）。

### Step 3：按五维方法学框架与 CNS 视角做深度语义复核
读取 `references/methods_stats_rubric.md`、`references/evidence_and_fairness_protocol.md`、`references/phack_concepts.md` 与 `references/output_contract_spec.md`，在脚本结果基础上补充人工级深度审查：
1. **研究设计与偏倚控制**：核对设计类型是否能回答研究问题、对照/随机化/盲法/主要终点是否事前明确，区分“未报告（`under_reported`）”与“设计错误（`method_flaw`）”。
2. **变量定义与数据筛选漏斗**：核对暴露/结局/协变量编码、分类切点（是否事后挑选？）、单位换算、入排标准与失访分母是否贯穿 Methods、Results 和图表。
3. **统计模型与数据结构匹配**：核对独立性/嵌套结构（患者/动物 vs 细胞/重复测量）、分布假设、交互项、混杂控制与敏感性分析；若提供原始 CSV 数据，审阅 `csv_checks` 并视需要用纯 Python 做独立复算并记录步骤。
4. **反误报与公平性校准**：对照 ACL H1–H17 过滤掉“方法不够新潮”“阴性结果”“可再加无关实验”等伪缺陷；对于合法的无缺陷或纯探索性披露稿件，诚实报告无重大缺陷并写清未核验边界。
5. 若 Step 3 有人工增补/修订，同步更新 `/app/output/stats_audit.json`、`/app/output/stats_review_report.md`，并用 `result_guard.py --patch-json` 同步 `/app/output/result.json`。

### Step 4：归档校验、溯源闸门与最终审计
```bash
python -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --require stats_audit.json --require stats_review_report.md
```
确认 `result_guard.py --validate`、`gap_gate.py` 与 `final_audit.py` 全部通过（`"passed": true`）再结束。

## 离线异常降级策略

| 异常场景 | 识别信号 | 规定处置 |
| --- | --- | --- |
| 镜像无 `scipy`/`numpy` | `math_backend == "pure_stdlib_lentz"` | 自动启用 `stat_math_core.py` Lentz Beta/Gamma 引擎与纯 stdlib `phack_detect.py`，零报错完成全量复算 |
| 扫描 PDF 低文本页或无解析器 | `gaps` 含 `PDF_LOW_TEXT_PAGE` / `PDF_EXTRACTOR_UNAVAILABLE` | 标注缺页定位，不杜撰图表或统计数值，`status: "partial_with_gaps"`、`audit_status: "partial"` |
| 缺少原始数据与预注册方案 | `unassessable_items` 非空 | 明确列出不可判断项与缺失输入，将未说明多重比较校正归为 `under_reported`，严禁定性 p-hacking |
| 精确显著 p 值 `< 5` | `phack_analysis.ran == false` | 自动跳过 p-curve 群体检验以防低效能误报 |
