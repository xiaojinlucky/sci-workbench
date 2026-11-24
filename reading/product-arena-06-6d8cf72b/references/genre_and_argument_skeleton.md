# 论文体裁识别与论证骨架指南（融合 Article Anatomy 与 Literature-Mining 发现路径）

## 1. 先判体裁，再定骨架（不套死通用模板）

处理任何输入文献前，先根据章节标题、数据归属与语篇信号识别体裁，避免把综述或基准论文硬塞进 IMRaD 模板：

| 体裁 (`paper_genre`) | 典型结构与语言信号 | 笔记组织策略 |
|---|---|---|
| `research_article` | 自有实验/计算数据，IMRaD 顺序（Abstract / Introduction / Methods / Results / Discussion），`we show / here we present`，定量结果与对照实验 | 按原始章节保留论证链，重点抽取「空白→方法与对照→主图证据→局限前提」 |
| `review` | 按主题/模态分章综合他人文献，大量引用、`systematic review / meta-analysis / PRISMA / outlook`，较少自有原始实验数据 | 按主题章建立「已有路线—共性/分歧—开放空白」卡片，标注无自有实验数据 |
| `perspective` | 历史/时代钩子、编号论点、规范性主张（`should / must / roadmap / call to action`）、路线图收尾 | 提炼核心论点编号、支撑案例与路线图建议，区分规范性主张与实证结果 |
| `benchmark_framework` | 空白为“领域缺乏统一标准/可复现评测框架”，大量对比表、数据集、版本与复现性声明 | 突出评测任务定义、基线集合、指标与版本/许可边界 |
| `comment` | 第一人称单数或短评体、无 IMRaD、极少或无图表、聚焦单一争议或政策/方法点评 | 提炼被评对象、核心质疑与作者立场，不强行补写 Methods/Dataset |

## 2. 论证功能定位（Article Anatomy）

在保留原文**真实章节顺序**的前提下，为每个章节标注其论证功能：

- **Abstract（漏斗五步）**：领域价值 → 空白（常在 `However / 然而` 之后）→ 贡献枢纽（`Here we show / present / 本文提出`）→ 核心定量结果 → 科学意义。
- **Introduction（钩子 → 空白 → 贡献）**：抓取空白信号词（`however`, `remains`, `unfortunately`, `underexplored`, `scarcity`, `lack of`, `鲜有`, `仍是挑战`, `缺乏`）与贡献枢纽句（`Here we...`, `In this work...`）。
- **Methods（操作与排除边界）**：不仅记工具名称，更记**输入对象、分组/对照、关键参数阈值与统计单位**，避免把“用了什么包”当成“解决了什么问题”。
- **Results（结论先行 + 图表举证）**：段首常给判断，随后给出 `Fig. X` / `Table X` 与数值；小标题序列本身构成证据升级阶梯。
- **Figure / Table Captions（自足描述 vs 末句解读分离）**：图注通常含样本量 `n=`、误差棒类型与统计检验；若图注末句出现推断性表达（`indicating that`, `suggesting that`, `demonstrating that`, `表明`, `提示`），**必须标注为 `legend_contains_interpretation: true`**，与原始观测分开。
- **Discussion / Limitations（主张边界）**：重点捕捉界定结论适用范围的句子（`limitation`, `caveat`, `remains to be tested`, `cannot exclude`, `confined to`, `局限`, `前提`, `尚需验证`）。

## 3. PaperStory 发现主线与「一图一方法」精读框架

在纵向拆解单篇论文时，重建四段式发现主线（保留真实分支与迭代，不把叙事顺序混同为生物学/实验时间）：

1. **问题与空白 (`problem_and_gap`)**：作者针对什么未解现象或现有方法短板？
2. **方法与操作 (`method_and_operation`)**：为什么选该方法/实验设计？实际做了哪些变换、筛选或比较？
3. **关键结果与图表证据 (`key_results_and_figures`)**：每张主图/主表（一图一方法）回答什么问题、设置什么对照、排除了哪类替代解释、证据边界在哪里？
4. **局限与成立前提 (`limitations_and_prerequisites`)**：结论仅在什么物种/条件/样本量/假设下成立？哪些替代解释尚未排除？

## 4. 章节间承接关系 (`transition_from_previous`)

每个章节卡片记录它如何承接上一章（例如：`gap_to_design`、`design_to_validation`、`evidence_to_interpretation`、`synthesis_to_outlook`），防止笔记沦为互不关联的孤立摘要块。
