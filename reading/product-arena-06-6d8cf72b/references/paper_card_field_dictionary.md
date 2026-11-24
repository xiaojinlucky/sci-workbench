# 结构化论文卡字段字典与三层证据归属规范

## 1. 三层证据归属标签（严格互斥）

任何进入 `paper_card.json`、`structured_notes.json` 与 `structured_notes.md` 的结构化条目，必须显式标记归属层级：

| 归属标签 (`attribution`) | 定义 | 锚点与核验要求 |
|---|---|---|
| `author_reported` | 原文直接陈述的问题、样本量、实验条件、定量指标、对照或作者自述局限 | **必须**绑定至少 1 个通过逐字核验（`exact_match`）的原文定位锚点与原句摘录 |
| `model_synthesized` | 基于多段原文综合提炼的章节主旨、承接关系、体裁判定或跨句发现主线概括 | 必须列出所综合的来源段落锚点列表，且严禁引入原文不存在的新数值、新对照或新结论 |
| `not_reported` | 当前输入材料中未出现、被截断或因扫描件缺文本层而无法核实的字段 | `value` 固定写明未报告原因（如 `"Not reported in available source text."`），**同时写入 `gaps`**，严禁用常识或领域标签补齐 |

## 2. 论文卡标准字段字典 (`paper_card.json`)

| 字段名 | 科学含义 | 抽取优先级与来源 | 允许为空/未报告时的处置 |
|---|---|---|---|
| `title` | 论文标题 | `case.json.title` → 首个一级标题/JATS `<article-title>` | 标注 `not_reported` |
| `identifier` | DOI / PMID / PMCID / arXiv ID | `case.json.primary_doi` → 正文正则提取 | 标注 `not_reported` |
| `paper_genre` | 论文体裁分类 | 基于章节结构与语篇线索判定 | 标注 `model_synthesized` + 判定依据 |
| `research_question` | 核心科学问题与拟填补空白 | 优先匹配空白+贡献枢纽句（`However ... Here we ...`），其次 Abstract/Introduction 核心句 | 不可抓无关背景句冒充；无线索则 `not_reported` |
| `dataset_or_sample` | 数据集、样本量、队列、材料或生物对象 | 扫描 Methods/Results 中的 `n=`、cohort、dataset、material、species、sample 句 | 未报告则 `not_reported`（绝不填默认样本量） |
| `method_conditions` | 核心方法、关键参数、实验/计算条件 | Methods 段落优先，结合文中明确给出的温度/pH/电位/模型超参/统计检验 | 未报告则 `not_reported` |
| `controls_and_exclusions` | 对照组、基线比较与被排除的替代解释 | 扫描 `control / baseline / compared with / placebo / ablation / ruled out / excluded` 句 | 无明确对照说明时诚实记 `not_reported` |
| `main_findings` | 核心定量与定性发现列表（≤5 条） | 优先 Results 含定量指标、统计显著性或图表引用的核验句 | 无结果句时记空列表 + `not_reported` |
| `limitations_and_prerequisites` | 局限性、适用前提与未排除风险 | 同时扫描独立 Limitations 章节与 Discussion/Conclusion 中的 `limitation / caveat / remains to be / confined to / 局限 / 前提` 句 | 严禁编造通用局限套话；无依据即 `not_reported` |

## 3. 多篇论文横向可比性保证

所有字段保持一致的 JSON 结构：
```json
{
  "value": "...",
  "attribution": "author_reported",
  "evidence_anchors": ["paper.md#source-line=14"],
  "evidence_quotes": ["Verbatim sentence from source..."]
}
```
这种固定契约使下游检索或横向比较工具无需解析自然语言即可并排对比多篇论文的 `dataset_or_sample`、`controls_and_exclusions` 和 `limitations_and_prerequisites`。
