---
name: citation-fact-audit
description: Offline-first academic citation reconciliation, atomic sub-claim decomposition, five-state source-support grading, retraction/preprint flagging, and cross-section numerical fact verification. Distinguishes 'citation exists' from 'source supports claim' with zero third-party dependencies.
---

# 引用与事实核验 (challenge-cbe96f01)

本 Skill 面向学术稿件、综述、实验材料及结构化引文导出件，执行可复核、可落位、抗误报的引用与事实核验。它把「引文是否存在」「引用标记与参考文献/来源对象是否一一对应」「原子子声明是否被来源原文实际支持」「稿件内部数值是否自洽」四件事严格分层，避免把“缺原文”误判为“伪造”，也避免把“DOI 匹配”误判为“论断成立”。

## 适用边界与不负责范围

- **负责**：多体系引文提取与三向对账、复合论断原子化拆解与五态支持度判定、来源出版状态（撤稿/勘误/预印本）告警、裸“前人研究+数值”无引文检测、摘要与正文跨章节数值/样本量对账、输出可交办作者的逐条定位与修订清单。
- **不负责**：全文润色、综述改写、文献补检索联网查新（离线沙箱无外网时不编造外部文献）、代替作者重写科学结论。

## 核心规则与反误报闸门

1. **三向对账与 PDF 多行文献重建（Three-Way Reconciliation）**：
   - 参考文献表自动按编号前缀（`[1]`、`1.`）、LaTeX `\bibitem` 或 Author-Year 首行边界合并跨行/缩进/空行续行（保留完整 DOI、PMID、arXiv 与撤稿/勘误标记），避免把第 2 行期刊名拆成假条目。
   - 同时核对 (a) 正文引文标记计数（`[n]`、`[1-3, 5]`、`\cite{k}`、`(Author et al., YYYY; Author & B, YYYY)`、DOI/PMID/arXiv；排除量表/区间 `[1-5]` 与期刊/实体名 `(Nature, 2022)` 误报），(b) 参考文献表 / `.bib` 唯一条目，(c) 离线提供的实际来源正文/摘要对象或结构化 `references` 对象。
   - 捕获**悬空断链**（正文有标记、文献表无条目）、**重复编号**（文献表同一编号出现两次导致指代歧义）、**孤立未引条目**、以及**多标记坍缩到单对象**（如正文 10+ 个不同引文编号仅映射到 1 个历史来源对象：明确区分“导出/持久化丢失”与“伪造”，不把 marker 数量当成有效来源数）。
2. **复合论断原子化拆解（Atomic Sub-Claim Decomposition）**：
   - 含引文的复合句必须拆分为独立的原子子声明（例如按 `;`、`whereas`/`while`/`however`、各归属不同引文标记的并列分句拆分），逐条绑定其局部引文键与量化数值、临床极性方向、因果强度及实验前提。
   - 禁止对一条含多个事实成分的复合句给笼统总评；若其中一项被反驳、一项缺原文、一项被支持，必须分别归因并落到具体行号/页码。
3. **五态精确判定矩阵与临床极性防误杀（Five-State Support Grading）**：
   - `supported_by_source`：离线对齐的来源正文支持该原子子声明的主题、临床方向、数值与条件边界，附 `source_file:L<n>` 与逐字摘录（`<= 200` 字符）。支持正向靶指标（`survival`/`response`/`accuracy` 等）与负向靶指标（`mortality`/`death`/`relapse`/`risk` 等）极性等价对齐（例如 `improved survival` 与 `reduced mortality` 同归 `beneficial_outcome`，绝不误判矛盾）。
   - `partially_supported`：来源支持核心方向，但稿件存在**因果升级**（来源仅报相关/观察性，稿件写成 `causes/proves/直接导致`）、**条件/物种范围遗漏**（来源限定 `in vitro` / `in mice` / 亚组，稿件泛化到人类患者）、仅部分同指标数值命中，或靶指标极性不确定需人工复核（降级 `minor | needs_human_review`）。
   - `contradicted_by_source`：来源正文明确反驳稿件所引同指标数值（排除 `95% CI`/`80% power` 常量且无单位数值仅比较共享指标词）、临床方向或显著性结论（显著 vs 无显著差异）。
   - `source_not_provided`：未提供对应来源原文，或来源为低文本扫描 PDF（`PDF_LOW_TEXT_PAGE`）。**严禁将缺失原文或扫描件不可读判定为伪造或不支持！**
   - `doi_or_metadata_only_unsupported`：来源仅提供 DOI/作者/年份/标题元数据卡片而无正文证据（仅凭 DOI 存在不可推定论断成立），**或** DOI/作者年份虽匹配到来源全文，但来源内容与子声明主题几乎零重叠（张冠李戴/错引）。
4. **反误报与句子角色过滤（Anti-False-Positive Firewall）**：
   - 先区分每句是「作者引用前人事实断言」「稿件自身实验结果（含 `our study` / `Table` / `Fig.`）」「显式假设/局限性/阴性对照」。不对合理局限性、假设或阴性结果强造缺陷。
   - 若稿件经核查无实质 P0/P1 缺陷，诚实报告 `no_major_defects_detected`，并注明已检范围与未核验边界，严禁凑缺陷配额。
   - 遵守 `references/evidence_and_fairness_protocol.md` 的五门准入与 ACL H1–H17 公平性防火墙。
5. **出版可靠性与内部事实一致性**：
   - 参考文献或来源含撤稿/撤回/关注声明（`RETRACTED`/`WITHDRAWN`/`Expression of Concern`）判 `critical` `factual_error`；含勘误/更正（`ERRATUM`/`CORRIGENDUM`）判 `major` `needs_human_review`；预印本（`arXiv`/`bioRxiv`/`medRxiv`/`SSRN`）记入版本告警并在正文误称“同行评审”时升级为 `major`。
   - 检出无引文锚点的“前人研究+具体数值”断言，并对 Abstract 与 Methods/Results 之间的总样本量 `N` 与锚定指标百分比做跨章节对账。

## 运行环境与输出契约约定

- 工作区：`/app/work`；最终产出目录：`/app/output/`；只读输入：`/input/case.json`、`/input/task.md` 及配套稿件/文献文件。
- **纯标准库离线安全**：`citation_fact_verifier.py` 与 `review_io_core.py` 零第三方依赖，可在 `python:3.12-slim --network none` 稳定运行；解析 `.docx` 自动回退 stdlib OOXML XML 解析；解析 `.pdf` 按 `pdfplumber -> pypdf -> pdftotext` 级联，低文本扫描页自动记录 `PDF_LOW_TEXT_PAGE` gap，绝不杜撰缺页文字。
- **`result.json` 双状态兼容约定**：
  - 顶层 `status` 必须保持 `ok` 或 `partial_with_gaps`（由 `result_guard.py --finalize` 写入，兼容 `final_audit.py` 门禁）；
  - 顶层同步写入 `audit_status`（`completed` | `partial` | `indeterminate` | `failed`）、`case_id`、`sources`、`uncertainty`、`gaps`、`artifacts`、`metrics`、`metric_provenance`、`findings` 与 `citation_matrix` 摘要。

## 标准执行流程（DeepSeek Harness）

### Step 1：环境探测、预算初始化与输入索引
```bash
python -B /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B /skills/weekly-skill/scripts/budget_guard.py --state /app/work/budget.json --init
python -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-cbe96f01"
python -B /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```
读取 `/input/case.json` 与 `/input/task.md`，获取真实 `case_id`、题目要求与自定义检查面。

### Step 2：运行确定性引用与事实核验引擎（同步直出 `result.json`）
```bash
python -B /skills/weekly-skill/scripts/citation_fact_verifier.py \
  --input /input \
  --out-json /app/output/citation_audit.json \
  --out-md /app/output/citation_review_report.md \
  --out-result-json /app/output/result.json
```
检查 stdout 摘要与 `/app/output/citation_audit.json`：确认已扫描文件清单、三向对账计数、五态分布、初筛 `findings` 与 `gaps`；`--out-result-json` 已自动生成含 `case_id`、`status`、`audit_status`、`sources`、`artifacts`、`metrics`、`metric_provenance`、`findings`、`uncertainty` 与 `gaps` 的双兼容 `/app/output/result.json`。

### Step 3：结合领域规范做深层语义复核（防漏报 + 防误报）
读取 `references/citation_verification_rubric.md`、`references/evidence_and_fairness_protocol.md` 与 `references/output_contract_spec.md`，并对脚本输出的 `citation_matrix` 与 `findings` 执行以下人工级复核：
1. **逐条复查 `partially_supported`、`contradicted_by_source` 与 `doi_or_metadata_only_unsupported`**：对照来源原句确认数值、方向、因果强度与物种/亚组限定确实存在语义偏差，补充或修正更精准的原文引文和可操作改写建议。
2. **审查 `source_not_provided` 条目**：若输入目录中另有未自动匹配到的补充材料/附表/JSON，手动核对来源后更新对应条目；若确实缺失原文，保持 `source_not_provided` 并在 `uncertainty` 中诚实声明，绝不凭记忆补造外部文献内容。
3. **补查高阶逻辑与规范面**：按题面 6 类审查面（事实核验、引用支持、方法设计、统计报告、论证链、学术规范）核对是否存在脚本正则未覆盖的隐蔽过度推断、选择性引用、署名/伦理/数据可用性缺口，并严格过滤掉不符合五门准入或触发 ACL H1–H17 的伪缺陷。
4. 若 Step 3 对 `findings`/`uncertainty` 做了人工修订，同步更新 `/app/output/citation_audit.json`、`/app/output/citation_review_report.md`，并用 `result_guard.py --patch-json` 同步 `/app/output/result.json`。

### Step 4：归档校验、溯源闸门与最终审计
```bash
python -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --require citation_audit.json --require citation_review_report.md
```
确保 `result_guard.py --validate` 通过、`gap_gate.py` 无 `UNGROUNDED_IDENTIFIER` 与 `MISSING_METRIC_PROVENANCE`，且 `final_audit.py` 返回 `"passed": true` 后再结束任务。

## 离线异常降级策略

| 异常场景 | 识别信号 | 规定处置 |
| --- | --- | --- |
| PDF 为扫描图片/低文本页 | `gaps` 中出现 `PDF_LOW_TEXT_PAGE` | 保留页码定位与 gap，相关引文标 `source_not_provided`，`status: "partial_with_gaps"`、`audit_status: "partial"` |
| 沙箱缺少 PDF 解析工具 | `PDF_STDLIB_FALLBACK_USED` / `PDF_EXTRACTOR_UNAVAILABLE` | `review_io_core.py` 依次级联 `pypdf`（双栏阅读序）→ `pdfplumber` → `pdftotext` → 纯 stdlib FlateDecode `BT..ET` 解码；全部不可用时记 gap 并继续处理其余格式，绝不编造 PDF 内容 |
| 医学/温哥华上标数字引文 | `detect_superscript_numeric_style` 命中 | 自动识别正文上标数字引文并避免对整表参考文献误报孤立未引，同步记录 `uncertainty` 边界说明 |
| 结构化导出多标记坍缩到单对象 | `many_to_one_source_collapses` 非空 | 明确区分解析/持久化丢失与伪造，列出全部未映射标记并要求核对原始来源图例 |
| 仅有参考文献表、无原文全文 | `source_documents_indexed == []` | 完成三向对账、撤稿/预印本检查、裸引文与跨章节数值对账；语义支持度一律标 `source_not_provided` 并在 `uncertainty` 写明边界；同步输出六维度协同审查 `six_facet_coverage` 与 `semantic_review_checklist` |
| 临近预算/时间上限 | `budget_guard.py` 告警 | 立即用 `result_guard.py --finalize` 落盘已有确定性审计结果并标注未完章节于 `gaps` |
