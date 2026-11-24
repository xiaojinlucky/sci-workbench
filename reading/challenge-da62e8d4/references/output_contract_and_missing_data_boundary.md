# 输出契约与缺失数据边界 (`challenge-da62e8d4`)

## 1. 运行路径与必交产物
- **输入目录**：`/input`（`case.json`, `task.md`, 候选数值脚本 `.py`, 可选环境清单 `environment.yml`/`requirements.txt`/`pyproject.toml`/`Dockerfile`/`README.md`）。
- **工作目录**：`/app/work`（`env.json`, `env_doctor.json`, `.budget_state.json`, `audit_log.json`, 隔离运行目录 `run_*_r1/r2/`, `gaps.json`）。
- **输出目录**：`/app/output`，必须包含且非空：
  1. `result.json`（含 `case_id`, `status`, `reproducibility_grade`, `evidence_ladder`, `environment_snapshot`, `environment_lockfile_summary`, `parameter_reconciliation[]`, `execution_runs[]`, `failed_runs_excluded[]`, `tolerance_comparisons[]`, `missing_original_materials[]`, `metrics`, `metric_provenance`, `gaps[]`）
  2. `reproduction_audit_report.json`（完整环境锁文件博士报告、逐脚本 AST 与种子卫生、多跑明细、排除的失败运行及五轴归因证据）
  3. `reproduction_report.md`（清晰可读的复现核验报告，呈现四级证据阶梯、环境漂移清单、参数对账、多跑抖动、容差对比与缺失边界）

## 2. 缺失原始材料与合成试验边界
按本题官方范围约定：
1. **组织方合成试验 ≠ 原作者代码、物理实验或全量 PMLB 复现**。当原始数据、原始仓库、预训练权重或外部基准未提供时，必须在 `missing_original_materials[]` 和 `scope_disclaimer` 中明确保留。
2. **严禁伪造缺失数据或外部基线**：不得生成伪造数据冒充原论文数据集，不得把缺失的 `paper_targets` 设为 0 或任意数值。
3. **诚实分级**：
   - 无候选脚本或全部候选运行失败：`reproducibility_grade = "not_reproducible_or_missing_code"`；
   - 存在任意失败运行、越界指标、随机抖动、超参冲突、缺失对比项或缺失原始材料：`reproducibility_grade = "partially_reproducible"`，`status = "partial_with_gaps"`；
   - 仅当全部候选干净退出、全部指标在事前容差内且确定性一致、无超参冲突且无缺失材料时，才可标 `reproducibility_grade = "reproducible_within_tolerance"`。
