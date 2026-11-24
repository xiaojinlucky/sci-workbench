# 离线环境锁定与可复现哈希审计规范

在科研数据分析场景中，“昨天能跑通、今天结果变了”与“开发机能跑、评测容器缺包崩溃”是最常见的可复现性故障。本规范规定离线环境探测、依赖缺口降级、哈希快照链与可复现证明的统一标准。

## 1. 离线环境真相探针（`env_lock_auditor.py`）

严禁凭记忆或题面标签臆测容器中已安装的 Python 包或版本。在编写或运行分析脚本前，必须调用 `scripts/env_lock_auditor.py` 生成真实环境清单 `env_lock_manifest.json`：

- **基础运行时**：精确采集 `python_version`、`python_implementation`、`executable`、`sqlite_version`、`platform`、`machine`、`cpu_count`。
- **确定性环境变量**：检查并记录 `PYTHONHASHSEED`、`OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS`、`MKL_NUM_THREADS`、`MPLBACKEND`、`LC_ALL` / `LANG`。
- **科学包探测矩阵**：通过 `importlib.util.find_spec` 与 `importlib.metadata.version` 逐一实测 `numpy`、`pandas`、`scipy`、`matplotlib`、`statsmodels`、`scikit-learn`、`seaborn` 的真实状态；未安装一律如实标记 `"NOT_INSTALLED"`，绝不虚报或尝试联网安装。
- **脚本级导入交叉核对**：传入 `--script <path>` 时，通过 AST 提取脚本全部 `import` / `from ... import` 顶层模块，自动归类为：
  1. `stdlib`（Python 3.12 标准库，离线 100% 可用）；
  2. `installed_third_party`（已确认安装并附带精确版本号）；
  3. `local_relative`（脚本同级本地模块）；
  4. `missing_offline_gap`（容器内未安装，直接阻断执行并给出标准库替代建议）。
- **环境指纹（`environment_fingerprint_sha256`）**：对规范化后的 Python/SQLite/OS/已安装包版本字典计算 SHA-256，便于跨次运行核对是否发生环境漂移。

## 2. 离线降级与零外部依赖兜底原则

| 目标任务 | 完整科学栈可用时 | 精简离线环境（仅 Python 3.12 标准库） | 必须记录的诚实声明 |
|---|---|---|---|
| CSV / 表格清洗与分组统计 | `pandas` / `numpy`（显式 `dtype=str` 保护 ID） | `csv.DictReader` + `collections.defaultdict` + `statistics` + `math` | 在报告 `environment` 字段写明纯标准库执行 |
| SQLite 查询与复算 | `sqlite3`（原生标准库）+ `pandas` 交叉核验 | `sqlite3` + 纯 Python 字典聚合对账 | `sqlite3` 为 Python 内置标准库，功能零降级 |
| 门禁与结果摘要可视化 | 纯标准库 SVG + 可选 Matplotlib 300 dpi PNG | 纯标准库生成可编辑矢量 SVG（`<stem>_gate_summary.svg`） | 写明 `matplotlib: NOT_INSTALLED`，`png_generated: false` |
| 高级数值优化 / 特殊统计检验 | `scipy.stats` / `statsmodels` | 仅实现数学定义严格等价的闭式公式；无法等价实现时诚实标注 `UNAVAILABLE` 缺口 | 严禁编造近似 p 值或假装已运行检验 |

## 3. 前后双快照与 SHA-256 证据链

`isolated_code_runner.py` 在隔离工作目录执行脚本前后建立完整哈希链，防止“静默改写输入”和“陈旧产物冒充新结果”：

1. **输入与脚本指纹**：执行前计算目标脚本的 `script_sha256` 及可选 `--input-dir` 下全部输入文件的 SHA-256；输入目录始终只读引用，绝不原地覆写。
2. **执行前工作区快照（Pre-Run Snapshot）**：扫描 `--work-dir` 全部既有文件，记录相对路径、字节数与 SHA-256。
3. **隔离执行环境注入**：子进程强制注入：
   - `PYTHONHASHSEED=<seed>`（默认 `42`）
   - `PYTHONDONTWRITEBYTECODE=1`
   - `MPLBACKEND=Agg`
   - `OMP_NUM_THREADS=1`、`OPENBLAS_NUM_THREADS=1`、`MKL_NUM_THREADS=1`
4. **执行后工作区快照（Post-Run Snapshot）与增量分类**：
   - `created_files`：本次运行新创建的文件；
   - `modified_files`：已存在且字节数或 SHA-256 发生变化的文件；
   - `unchanged_preexisting_files`：运行前后哈希完全未变的旧文件（**不计入本次新鲜产物**，若本次运行失败则立即归入 `stale_preexisting_files` 风险清单）。
5. **数值不变量自动核查**：对所有 `fresh_artifacts`（`created_files + modified_files`）执行：
   - `.json` 文件：严格解析 JSON，递归检查任意层级是否存在 `NaN`、`Infinity`、`-Infinity` 或解析损坏；
   - `.csv` 文件：拦截 0 字节空文件、仅表头 0 数据行文件、列数漂移行、数值字段中的 `nan`/`inf`/`-inf` 异常串，并在提供 `--expected-rows` 时核对行数守恒。

## 4. 可复现交付清单

每次成功运行后，以下四类信息必须落盘于 `<stem>_report.json` 并被 `/app/output/result.json` 引用：
- **谁运行了什么**：`script_name`、`script_sha256`、完整命令行参数、工作目录与退出码；
- **在什么环境下运行**：`env_lock_manifest` 中的 Python/SQLite/包版本与 `environment_fingerprint_sha256`、`seed`；
- **产出了什么新鲜产物**：`fresh_artifacts` 列表及每个文件的字节数与 SHA-256；
- **通过了哪些校验**：静态审计摘要、数值不变量检查结果、耗时以及原始 `stdout`/`stderr` 尾部截取。
