# 五轴差异归因与事前容差协议

## 1. 四级复现证据阶梯（Four-Tier Evidence Ladder）
严禁把“代码存在”或“退出码 0”写成“论文已复现”。必须逐级分开判定：

| 层级 | 字段 | 成立条件 | 常见误判防范 |
| --- | --- | --- | --- |
| Tier 1 | `tier_1_code_present` | 存在候选 `.py` 脚本且无语法错误 | 代码存在 ≠ 能运行 |
| Tier 2 | `tier_2_clean_exit` | 全部候选脚本在隔离目录退出码为 `0` 且无超时 | 退出码 0 ≠ 数值正确 |
| Tier 3 | `tier_3_within_prior_tolerance` | 全部可比指标落入事前声明的 `abs_tol`/`rel_tol`、无缺失指标、且重复运行 `deterministic == true` | 仅跑通一次或存在随机抖动不算 Tier 3 |
| Tier 4 | `tier_4_scientific_claim_supported` | Tier 3 成立且无缺失原始数据/仓库、无未解决超参冲突 | 组织方合成试验或缺原数据/PMLB 时必为 `not_established` |

## 2. 事前容差纪律与失败运行隔离
1. **事前容差不可事后放宽**：`abs_tol` 与 `rel_tol` 必须严格取自输入 `case.json` 事前声明（未逐指标声明时使用事前默认 `default_abs_tol` / `default_rel_tol`）；严禁为凑 `within_tolerance` 事后调大容差。
2. **失败运行强制排除**：退出码非 0 或超时的运行，其 stdout 残片严禁进入容差统计分母；必须在 `failed_runs_excluded[]` 单列并记录返回码与 `stderr_tail`。
3. **成功运行缺指标单报**：脚本退出码为 0 但未输出目标数值指标时，标为 `metric_not_emitted` 并发出 `METRIC_COMPARISON_INCOMPLETE`，不得按 0 补齐。

## 3. 证据驱动的五轴差异归因（Five-Axis Attribution）
对每一条 `out_of_tolerance`、`metric_not_emitted` 或失败项，必须结合实测证据归因到以下五个标准轴，给出主因 `primary_attribution_axis`、辅因 `supporting_axes[]` 与可复查证据 `attribution_evidence[]`：

| 归因轴 | 触发证据信号 |
| --- | --- |
| `parameter_mismatch` | 该候选脚本存在 AST 检出的 `paper_code_conflict`（附具体参数名、论文值、代码行号与取值） |
| `environment_or_seed` | 重复运行检出 `deterministic == false`（附 `max_run_jitter`）、AST 检出 `unseeded_stochastic_risk`，或 `env_lock_doctor` 检出混源无锁/版本漂移 |
| `data_or_split_difference` | `missing_original_materials` 非空（缺少原始数据集、划分文件或全量 PMLB，仅以合成/子集数据运行） |
| `paper_underspecification` | 存在 `paper_only_missing_in_code` 或该脚本含未在论文说明的 `code_only_undocumented` 隐式超参 |
| `implementation_difference` | 脚本崩溃/未输出声明指标，或在排除上述显式差异后仍存在算法实现层面的数值偏差 |
