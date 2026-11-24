# 科研分析代码契约与反静默错误清单

本规范定义科研数据分析脚本在进入隔离执行前必须满足的代码风格、数据语义保真与静态审计规则，确保分析代码不仅能够运行结束（`exit_code == 0`），而且在口径、字段语义和数值不变量上可被第三方独立复核。

## 1. 线性命令式研究脚本风格（Research-Script Readability）

科研分析代码的首要读者是领域研究者与复核者，其次才是自动化调度器：

- **自上而下线性展开**：按 `脚本头说明 -> 参数与随机种子 -> 数据读取与就地核对 -> 清洗与去重 -> 核心统计与复算 -> 结果导出` 顺序平铺，避免把 60 行分析逻辑藏进多层抽象类、通用 Pipeline 框架或回调注册表。
- **参数就近放置**：阈值、分组字段、排除规则与随机种子直接写在对应步骤旁，并用简短中文或英文注释说明来源（数据字典硬约定 vs 工具默认 vs 数据观察结果）。
- **关键节点就地核对（Inline Checkpoints）**：在数据读入、去重过滤、分组聚合和最终输出节点打印精炼摘要（如 `shape` / `len(rows)`、前 3 行样本、主键唯一数、缺失值计数、分组样本量），不打印整张大表，也不省略关键维度核对。
- **保留原始报错，禁止静默吞错**：直接调用已验证接口，出现缺列、类型解析失败或除零时暴露原始异常和上下文，禁止用 `except: pass` 或盲目回退默认值掩盖问题。
- **前端科学语义与内部实现分离**：输出表头、图表坐标轴与报告结论使用数据字典规定的领域术语（如 `cultivar_name`、`alcohol_pct`、`Not measured (NA)`），禁止把内部筛选标志、分支名或程序临时变量（如 `qc_branch_2`、`fallback_flag`）直接暴露为科学分类。

## 2. 六类高频静默错误与 AST 硬拦截规则

`scripts/code_contract_checker.py` 对目标脚本执行 AST 与词法审计，将高危缺陷分为 `BLOCKER`（阻断执行）、`WARNING`（需核查确认）和 `INFO`（可读性提示）：

| 规则 ID | 严重度 | 典型缺陷代码 | 真实科研危害 | 标准修法 |
|---|---|---|---|---|
| `CONTRACT_HOST_PATH` | BLOCKER | 硬编码本机盘符或个人主目录路径（如 `/home/alice/...`、`D:\LabData\...`） | 换机器或进入 Harbor 容器立即 `FileNotFoundError`，泄露宿主机私有目录结构 | 一律通过 CLI 参数（`--input` / `--output`）或相对工作路径传入，读取 `/input` 并写入 `/app/work` 或 `/app/output` |
| `CONTRACT_SILENT_ZERO_IMPUTE` | BLOCKER | `df.fillna(0)`、`df.replace(np.nan, 0)`、`np.nan_to_num(x, nan=0)` | 把“未检测 / 缺失（NA）”静默混同为真实数值 `0.0`，系统性拉低均值、扭曲方差与检验 p 值 | 保持 `NA/None` 原貌，分别汇报 `valid_n`、`missing_n` 与 `true_zero_n`；仅当数据字典明确授权填零时加行内注释 `# dictionary-authorized-zero` |
| `CONTRACT_LEADING_ZERO_CSV` | WARNING / BLOCKER | `pd.read_csv(path)` 未指定 `dtype=` 或 `converters=`（当 CSV 含前导零 ID 列如 `0001` 时升格为 BLOCKER） | pandas 自动将 `0001` 解析为整数 `1`，跨表连接键失配或与 `1` 误合并 | 显式传入 `dtype={"sample_code": str}` 或先全列 `dtype=str` 读取再显式转换数值列；或用 `csv.DictReader` 保持字符串 ID |
| `CONTRACT_UNSEEDED_RNG` | WARNING / BLOCKER | 调用 `random.*`、`np.random.*`、`train_test_split`、`KMeans`、`scipy.stats.*.rvs` 未设定种子 | 两次重跑得到不同抽样/置换 p 值或聚类标签，无法复现 | 在脚本顶部与随机调用处显式绑定种子（如 `random.seed(42)`、`np.random.default_rng(42)`、`random_state=42`）并配合 `PYTHONHASHSEED=42` |
| `CONTRACT_SWALLOWED_EXCEPTION` | BLOCKER | `except:` 或 `except Exception:` 块内只有 `pass`、`...`、`continue` 或返回硬编码常数 | 真实解析错误或计算异常被静默吃掉，下游产出看似正常的错误数字 | 删除静默吞错；仅捕获特定可预期异常并完整记录原始错误消息与样本标识，必要时显式失败退出 |
| `CONTRACT_SHELL_INJECTION` | BLOCKER | `os.system(...)`、`os.popen(...)`、`subprocess.run(..., shell=True)` | 命令注入风险、平台 Shell 差异导致不可复现且难以捕获精确返回码 | 使用 `subprocess.run([sys.executable, ...], shell=False, capture_output=True, text=True)` 参数列表形式 |
| `CONTRACT_SQLITE_WRITE` | BLOCKER | 对输入 SQLite 执行 `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`CREATE`/`ATTACH` 或未声明只读 URI | 污染只读原始数据快照或触发 `readonly database` 崩溃 | 使用 `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)` 只读读取，在内存中或 `/app/work` 写出派生文件 |
| `CONTRACT_UNCHECKED_GROUPBY` | WARNING | 调用 `.groupby(...)` 但脚本未出现去重或主键唯一性检查 | 多批次合并的重复记录或 1:N 连接结果直接进入分组均值/求和，导致样本量膨胀与权重畸变 | 聚合前检查主键唯一性、精确重复行数并记录去重前后行数；注意 `dropna=False` 以观察缺失分组 |
| `CONTRACT_BIOCODE_CHECKPOINTS` | INFO | 超过 20 行的数据分析脚本缺少任何就地核对输出（`print` / `shape` / `head` / `Counter` / `assert`） | 失败或异常时日志为空白，无法定位数据在哪一步发生维度漂移 | 在读入、清洗、聚合三个节点输出精炼维度与计数摘要 |

## 3. UTF-8 BOM 与脏文本预处理规范

真实仪器导出 CSV 常含以下隐蔽陷阱，脚本必须显式防御：

1. **UTF-8 BOM 表头污染**：第一列表头常带 `\ufeff`（如 `\ufeffrecord_id`），导致 `row["record_id"]` 抛出 `KeyError`。读取文本与 CSV 一律优先使用 `encoding="utf-8-sig"`。
2. **空行与尾随空白**：导出文件可能在表头后夹杂全空行或字符串首尾空格；解析时跳过全空行并对非自由文本标识符执行 `.strip()`，同时在日志中记录跳过的空行数。
3. **非数值标记混入数值列**：仪器导出的 `NA`、`N/A`、`ND`、`<LOD`、`""`、`null` 必须显式归类为缺失或检出限下样本，绝不强制替换为 `0`。
4. **单位与量纲核对**：汇总前检查是否存在同一指标混合单位（如 `mg/L` 与 `ug/L`、`g/L` 与 `%`），若未统一换算则禁止直接求均值，须先按数据字典换算或在报告中显式拆分标注。

## 4. 静态审计命令与判定标准

```bash
python3 -B /skills/weekly-skill/scripts/code_contract_checker.py \
  --script /app/work/analysis_task.py \
  --input-csv /input/dataset.csv \
  --out /app/work/code_contract_audit.json
```

- 返回码 `0`：`blockers` 为空，脚本可进入 `isolated_code_runner.py`。
- 返回码 `1`：存在至少 1 条 `BLOCKER`（或开启 `--strict` 时存在 `WARNING`），必须先修复对应行并复跑审计，严禁跳过静态检查直接交付结果。
