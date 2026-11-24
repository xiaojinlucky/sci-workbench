# 学术格式与模板适配规则（防混搭、禁臆补与转换保真）

## 一、冲突模板隔离与“未规定（UNSPECIFIED）”零臆补红线

1. **只执行指定目标模板**：当 `/input` 中同时出现多个期刊/学校模板、旧版模板与补充说明（例如一份要求结果与讨论合并、另一份要求分列）时，必须先运行 `template_spec_and_adapter.py compare-templates` 锁定本题指定目标模板：
   - 冲突字段按目标模板执行，并把所有冲突差异写入 `template_comparison.json` 与 `change_log`；
   - **严禁混搭**：仅在非目标模板中出现而目标模板未规定的字段（页眉、字号、行距、栏数、引用格式）归入 `alternate_only_blocked_from_mixing`，**严禁偷偷继承到成品中**。
2. **未规定字段显式标 `UNSPECIFIED`**：目标模板未写明字体、页边距、行距或章节合并方式时，在规格清单和 `gaps` 中显式标记 `UNSPECIFIED`，交由作者确认，绝不凭期刊习惯臆补。

## 二、字体策略与环境实测（含 `Liberation Serif` 适配）

1. 适配前必须通过 `fc-list` 真实探测容器内已安装字体，不可盲信系统一定装有专有商用字体（如 `Times New Roman`、`SimSun`）。
2. 当目标模板指定了容器缺失的衬线字体（或官方修订要求使用随环境提供的 `Liberation Serif`）时，诚实记录 `font_fallback_reason` 并使用已实测安装的 `Liberation Serif`（或模板指定的可用开源字体），绝不静默伪造字体可用状态。

## 三、内容转换保真度 9 项守恒（禁破坏性拆文本重写）

处理 `.docx` 源稿时，**必须采用无损 OOXML 原地样式改写**（保留原包内除目标样式外全部部件），修改后重读输出 `.docx` 并逐项核对 9 项守恒：
1. 段落数（`paragraphs_preserved`）
2. 表格数与单元格内容（`tables_preserved`）
3. OMML 数学公式（`m:oMath` / `m:oMathPara`，`equations_preserved`）
4. 脚注与尾注（`footnotes_preserved`）
5. 审阅批注（`comments_preserved`）
6. 修订痕迹（`w:ins` / `w:del`，`tracked_changes_preserved`）
7. 活域代码（交叉引用、目录、Zotero/EndNote 引用域，`live_fields_preserved`）
8. 嵌入图片与媒体文件（`media_preserved`）
9. 全部科学数值、单位、p 值、引用与限定词（`text_invariants_preserved`）

## 四、标题层级、三线表与交叉引用联动检查

- 标题必须绑定真实 Word 样式（`Heading1`/`Heading2`/`Heading3` + `w:outlineLvl`），严禁仅通过手调字号与加粗伪造标题导致目录/书签失效。
- 检查 `Figure 1..N` 与 `Table 1..M` 题注编号是否连续，是否存在正文引用了未定义图表的悬空交叉引用（`DANGLING_FIGURE_CROSSREF` / `DANGLING_TABLE_CROSSREF`）。
- 导出后若容器含 LibreOffice Writer（`soffice`）与 `pdftoppm`，执行真实 PDF/PNG 渲染校验；若缺失则诚实记录降级原因并交付可验证的 `.docx` 与保真度报告。
