# 真页码/行号回链与防批注过碎操作卡

## 1. 按输入模态严格使用真实定位锚点

严禁把非 PDF 文件的自建页码谎称为原论文 PDF 页码；锚点格式按实际输入模态严格区分：

| 输入格式 | 真实定位格式 (`anchor`) | 实现与诚实边界 |
|---|---|---|
| `.pdf` | `<file>#page=<P>&extracted-line=<L>` | 使用 `pdftotext -layout` 或 `pypdf (extraction_mode="layout")` 按换页符 `\f` 切分真实 PDF 页码；记录该页提取行号，并声明双栏/复杂排版阅读顺序需人工复核。若某页文本为空，立即记入 `gaps`（`PDF_PAGES_NEED_OCR`），不跳页重排页码。 |
| `.xml` / `.nxml` / `.jats` | `<file>#paragraph_id=<id>` | 使用 Python 标准库 `xml.etree.ElementTree` 解析 JATS `<sec>` 与 `<p id="...">` 真实段落 ID；无 `id` 属性时使用显式路径索引 `sec<S>_p<P>` 并标明 `anchor_kind: "xml-path"`。 |
| `.json`（结构化段落/章节） | `<file>#paragraph_id=<id>` | 优先保留输入 JSON 原生 `paragraph_id` / `id`；若仅含纯文本字段则使用 `json-block=<idx>`。 |
| `.md` / `.txt` / `.tex` | `<file>#source-line=<L>` | 使用 1-based 原始文件物理行号，明确标为 `anchor_kind: "source-line"`，绝不伪造 `page=1`。 |

## 2. 防批注过碎控制（解决 Zotero 零散高亮痛点）

科研阅读中常见失败是把论文拆成几十条高度重复、失去上下文的碎片批注。本技能通过四道机制控制批注粒度：

1. **章节级精华卡片（Section Synthesis Cards）**：每章仅生成 1 张结构化卡片，包含论证功能、与上一章的承接关系、2–3 句核心要点（按证据密度选取而非盲取首句）与关键锚点。
2. **去重归并术语/缩写词表（Terminology Glossary）**：自动识别 `Full Term (ABBR)`、`ABBR (Full Term)` 与中文 `全称（缩写）` 定义，按规范化缩写去重归并并保留首次定义锚点，避免正文知识点重复解释同一术语。
3. **可控上限的知识点（Density-Ranked Knowledge Points）**：
   - 取消“遇到前 15 句就截断”的盲截策略（避免 Intro 挤占 Results/Limitations）；
   - 按**证据密度**打分（含定量数值/单位、统计量 `p`/`CI`/`n=`、图表指向、对照比较词、局限前提词）；
   - 去重高相似度句（token Jaccard ≥ 0.78 合并）并执行**章节均衡配额**，默认上限 `--max-kps 12`（可按需调参）。
4. **逐句原位核验**：每条知识点的 `quote` 必须在其对应段落原文中通过 `verify_exact_quote` 严格命中；未命中者绝不放行。

## 3. 异常与降级处置清单

| 异常情形 | 触发条件 | 规范动作 |
|---|---|---|
| 扫描件/空白 PDF 页 | `pdftotext`/`pypdf` 提取后存在空文本页 | 保留其余可读页，写入 `PDF_PAGES_NEED_OCR`（列出具体页码），`status: "partial_with_gaps"` |
| PDF 加密或缺少提取器 | 无 `pdftotext` 且无 `pypdf`，或读取抛错 | 写入 `PDF_TEXT_EXTRACTION_FAILED`，不猜测 PDF 内容 |
| 无可识别章节标题 | 纯平铺文本无 `#`/编号/标准标题 | 保留原文段落锚点，写入 `NO_RECOGNIZED_SECTION_HEADINGS`，不伪称章节对齐 |
| 论文卡字段缺失 | 5 项核心字段任一缺失 | 字段标 `not_reported`，写入 `PAPER_CARD_FIELDS_INCOMPLETE` |
| 历史 `.tsv` 禁忌 | 任何场景 | 本技能不生成、不打包任何 `.tsv` 文件 |
