# 研究仓库复现医生检查清单（`research-repo-doctor` 适配）

## 1. 三分区可执行检查单架构
借鉴 `research-repo-doctor` 的“自动发现 → 人工待办 → 缺失登记”闭环，`env_lock_doctor.py` 在离线沙箱内自动生成以下三部分清单，落盘至 `reproduction_audit_report.json:environment_lockfile_audit.research_repo_doctor_checklist`：

1. **`auto_discovered`（自动核实的客观事实）**：
   - 运行时 Python 版本、操作系统与架构、`PYTHONHASHSEED` 设置状态；
   - 发现的候选数值脚本列表（`.py`）；
   - 发现的依赖声明文件（`environment.yml`/`yaml`、`requirements*.txt`、`pyproject.toml`、`Dockerfile`）；
   - 发现的严格锁文件（`conda-lock.yml`、`uv.lock`、`poetry.lock`、`Pipfile.lock`）；
   - 文档与规范元数据（`README*`、`LICENSE*`、`CITATION.cff`）。
2. **`manual_action_items`（按严重度分级的可执行修复项）**：
   - `HIGH`：`CONDA_PIP_MIXED_DRIFT_RISK`（混源无锁文件）、`UNPINNED_DEPENDENCIES`（未精确锁版本）、`DECLARED_VS_INSTALLED_VERSION_DRIFT`（声明与实装版本漂移）、`INSTALL_MISSING_DECLARED_PACKAGES`（声明依赖在当前环境缺失）；
   - `MEDIUM`：`PLATFORM_BOUND_PACKAGES`（含特定 OS/GPU 或 conda build hash 绑定包，跨机移植高风险）。
3. **`missing_items`（明确缺口登记）**：
   - 缺失严格锁文件、缺失 README、缺失许可证/引用元数据、声明包在当前运行时缺失、以及 `case.json` 中记录的原始数据集/原仓库缺失。

## 2. 工程就绪度 vs 科学可复现性的严格边界
- `engineering_readiness_ratio` 仅衡量代码仓与依赖声明的工程完整度（是否可读、可追踪、接近可重建）。
- **硬规则**：高工程就绪度**不等于**数值落入容差，更**不等于**原论文科学结论已复现。检查单中强制附带 `readiness_disclaimer`，禁止把“装得上/有 README/有锁文件”等价于“科学复现通过”。

## 3. 安全执行边界
- 动态执行候选数值脚本仅在隔离工作目录 `/app/work/run_<script>_r<k>` 中进行，带显式超时保护与输入/输出 SHA-256 留痕，严禁修改 `/input` 中的任何原始材料。
