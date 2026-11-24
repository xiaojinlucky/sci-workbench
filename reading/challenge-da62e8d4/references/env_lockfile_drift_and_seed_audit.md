# Conda/Pip/UV 锁文件漂移与随机种子审计指南

## 1. Conda #15581 / PR #15838 混源漂移风险
在真实科研复现中（如 Conda Issue #15581 及修复 PR #15838 所暴露的痛点），`conda env export` 即使成功导出 `environment.yml`，若其中嵌套 `- pip:` 子列表或与未锁版本的 `requirements.txt` 并存，换机重建时 pip 侧依赖仍可能解析到不同版本，导致数值悄然跑偏。

`env_lock_doctor.py` 针对四类高危环境缺陷逐项机检：
1. **混源无锁（`conda_pip_mixed`）**：`environment.yml` 内含 `- pip:` 区块，或同目录同时出现 `environment.yml` 与 `requirements*.txt` 且缺少 `conda-lock.yml`/`uv.lock`/`poetry.lock`。
2. **未锁版本依赖（`unpinned_dependencies`）**：
   - 裸包名（如 `scipy`、`scikit-learn`）；
   - 仅设下界或范围（如 `numpy>=1.24`、`torch>=2.0,<3.0`）；
   - 通配符版本（如 `pandas==2.*`）；
   - 未锁定 commit SHA 的 Git/URL 依赖（如 `@main`）。
3. **平台/硬件绑定包（`platform_bound_dependencies`）**：含 `cudatoolkit`、`nvidia-*`、`win-64`、`win32`、`osx-arm64` 或 conda 平台 build hash（如 `=py311h5eee18b_0`）的条目，在异构 Linux 容器中无法直接重建。
4. **声明 vs 实装版本漂移（`version_drifts` & `missing_in_runtime`）**：通过 `importlib.metadata` 读取容器实际安装的包版本并与声明精确对比，区分 `exact_match`、`version_drift`、`unpinned_in_manifest` 与 `declared_but_missing_in_runtime`。

## 2. 深度 AST 超参抽取与随机种子卫生审计
旧版仅抓顶层常量和函数默认值的做法会漏掉大量真实科研代码中的超参入口。`repro_audit_runner.py` 扫描五类超参定义位点：
1. 顶层常量与类型注解常量（含负号常量，如 `LR = 0.01`, `OFFSET: int = -1`）；
2. 函数与方法的位置默认值及关键字默认值；
3. `argparse.ArgumentParser.add_argument("--learning-rate", default=...)`（自动归一化 `--learning-rate` → `learning_rate`）；
4. 顶层或函数内配置字典（`CONFIG = {"seed": 42, ...}`）；
5. `@dataclass` 字段默认值及 `field(default=...)`。

对每个参数输出四态对账结果：
- `matched`：论文声明值与代码默认值一致；
- `paper_code_conflict`：论文值与代码值冲突（逐文件逐行保留，不因首脚本匹配而掩盖次脚本冲突）；
- `paper_only_missing_in_code`：论文明确声明但候选代码中未找到对应参数；
- `code_only_undocumented`：代码中存在隐式超参默认设置但论文未声明。

## 3. 随机种子卫生与多跑抖动检验
- **静态种子卫生**：AST 识别 `random.seed`、`np.random.seed`、`default_rng`、`torch.manual_seed`、`PYTHONHASHSEED` 及 `seed=`/`random_state=` 参数；若代码调用了随机采样函数（`random.gauss`/`random.uniform`/`np.random.normal` 等）却未设置固定种子（或使用 `None`/`time.time()`），立即标为 `unseeded_stochastic_risk`。
- **动态多跑抖动检验**：默认在隔离目录对每个候选脚本运行 `2` 次并注入固定 `PYTHONHASHSEED`；若两次输出的数值指标存在差异（`spread > 1e-12`），标记 `deterministic: false`、`STOCHASTIC_RUN_JITTER` 并记录 `max_run_jitter` 与逐指标抖动区间。
