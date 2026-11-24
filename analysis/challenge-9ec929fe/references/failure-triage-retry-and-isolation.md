# 失败分诊、重试上限与隔离回退策略

面对报错时，最危险的做法是“不读报错直接换一种写法盲试”或“捕获异常后填入看似合理的假数值”。本手册规定科学分析代码执行失败后的分诊树、严格重试上限、防陈旧产物机制与诚实停机条件。

## 1. 错误分诊矩阵（Root-Cause First）

出现非零退出码、超时或校验不通过时，先读取 `<stem>_report.json` 中的 `failure_classification`、`stderr_tail`、`static_audit` 与 `invariant_violations`，归入以下六类之一再采取最小定点修复：

1. **`STATIC_CONTRACT_BLOCKER`**
   - **特征**：`code_contract_checker.py` 检出硬阻断项（硬编码宿主机路径、`fillna(0)`、吞异常、`shell=True`、SQLite 写操作、CSV 前导零 ID 未保护）。
   - **修复动作**：按 `line` 与 `remediation` 精确修改对应行，严禁通过删除检查或绕过静态审计强行执行。
   - **重试上限**：最多 2 轮。

2. **`MISSING_OFFLINE_DEPENDENCY`**
   - **特征**：`ModuleNotFoundError`、`ImportError` 或 `env_lock_auditor.py` 报告 `offline_dependency_gaps`。
   - **修复动作**：离线沙箱严禁尝试联网 `pip install`；立即切换为已验证可用的标准库（`csv`, `sqlite3`, `statistics`, `math`, `json`）或容器内已装科学包，并在报告 `uncertainty` 与 `change_records` 记录降级事实。
   - **重试上限**：1 轮。

3. **`INPUT_SCHEMA_OR_ENCODING_ERROR`**
   - **特征**：`KeyError`、`FileNotFoundError`、UTF-8 BOM 表头（`\ufeff...`）、空行导致的 `IndexError`、数值列混入 `<LOD`/`NA` 引发 `ValueError`。
   - **修复动作**：回到 `/input` 重新核对真实文件名、表头原字面与前 5 行样本；统一使用 `utf-8-sig` 解码，对非数值标记做显式缺失分类而不是强转 0。
   - **重试上限**：2 轮；若 `/input` 确实缺失题目要求的字段或文件，立即停止并标记 `BLOCKED`。

4. **`NUMERIC_OR_STRUCTURAL_INVARIANT_FAILURE`**
   - **特征**：输出 JSON/CSV 含 `NaN`/`Inf`、生成 0 字节文件或 0 数据行表、行数相对输入基准异常膨胀或丢失。
   - **修复动作**：核查除零保护、去重逻辑、JOIN 基数与空分组处理；对合法空分组保留 `null` 均值并明确给出 `n=0`，不把 `NaN` 硬编码成 `0`。
   - **重试上限**：2 轮。

5. **`EXECUTION_TIMEOUT_OR_MEMORY`**
   - **特征**：子进程超过 `--timeout` 被终止（`status="timeout"`，退出码 `124`）或 OOM（退出码 `137`）。
   - **修复动作**：排查笛卡尔积 JOIN、嵌套 $O(N^2)$ 扫描或全量打印大表；改用哈希索引映射、流式读写或先聚合后关联。
   - **重试上限**：1 轮；仍超限则保存已验证的阶段性摘要并如实报告 `partial`。

6. **`STALE_ARTIFACT_CONTAMINATION`**
   - **特征**：第 2 次改动运行失败，但工作目录中仍留有第 1 次成功运行遗留的 `.csv`/`.json`/`.png`。
   - **拦截机制**：`isolated_code_runner.py` 强制执行“防陈旧产物守卫”：
     - 每次运行先快照工作区；
     - 若本次失败，报告状态强制置为 `"stale_or_failed"`，自动删除任何历史 `<stem>_VERIFIED.ok` 印章，并写出 `<stem>_FAILED_MARKER.json`，逐一列出 `stale_preexisting_files`；
     - 禁止把 `unchanged_preexisting_files` 计入 `fresh_artifacts`，禁止将它们复制到 `/app/output`。

## 2. 隔离沙箱规范

- **读写分离**：`/input` 视为严格只读源，任何中间脚本、临时数据库副本或实验产物均写入 `/app/work/<run_subdir>`；仅经 `isolated_code_runner.py` 全部门禁通过的新鲜产物，才复制到 `/app/output` 并写入 `result.json`。
- **清洁重跑优先**：遇到复杂状态污染或前一次失败残留文件时，为下一轮重跑分配新的隔离工作子目录（如 `/app/work/exec_run_02`），彻底切断旧文件干扰。
- **预算感知**：每次重试通过 `budget_guard.py --tick <step>` 记账；达到单类错误重试上限或剩余时间不足 120 秒时，立即停止无意义试错，转为整理已核实的部分结果与精确缺口说明。

## 3. 诚实停机与部分交付标准

当遇到真实不可恢复障碍（源数据缺列、离线缺少不可替代求解器、数据字典自相矛盾）时，按以下契约收口：
1. 在 `/app/output/result.json` 中诚实设置 `"status": "partial"` 或 `"failed"`；
2. 保留原始命令、完整错误摘要（`stderr_tail`）、输入快照哈希与已通过校验的前置产物；
3. 在 `gaps` / `uncertainty` 中逐条列出缺失条件、已排查原因与后续人工确认建议；
4. 绝不通过注释掉断言、删除检验项或填入猜测数值来伪造 `"status": "ok"`。
