# 变量定义溯源与推导边界审查清单

## 1. 三级变量溯源表（Variable Provenance Tiers）
严禁按常识臆测符号含义后冒充原文定义。每个符号必须归入下列三级之一：

| 层级 | 判定条件 | 必填字段 | 禁止行为 |
| --- | --- | --- | --- |
| `source_defined` | 原文（`publisher-source.xml` `<p>`、PDF/MD/TXT、`case.json`）明确出现 `"where x denotes ..."` / `"x is the ..."` / `"其中 x 表示 ..."` 等显式定义句 | `paragraph_id`, `definition_quote`, `unit_hint`, `range_hint` | 不可篡改原文符号或静默换单位 |
| `context_inferred` | 原文出现该符号但无显式定义句，仅能从上下文提及位置推测语境 | `paragraph_id`, `definition_quote` | 不可伪装成 `source_defined` |
| `undefined_in_supplied_text` | 全部输入材料均未提及或未定义该符号 | `paragraph_id: null`, `definition_quote: ""` | 严禁按教科书常识直接填定义或赋 0 值 |

## 2. 推导步骤、假设与边界条件审查
`source-formulas.xml` 只是从出版商 XML 抽取出的公式节点目录，不等于完整推导。对每条关键推导步骤核查四项：
1. **跨公式依赖**：标明前序公式 ID 与目标公式 ID（如 `eq1 -> eq3`）。
2. **代数/微积分等价性**：调用 `math_ast_guard.check_equivalence()`（有 SymPy 时隔离保留字 `E, I, S, N, O, Q, C` 做符号化简，并辅以 AST 多点数值交叉验证）。
3. **显式假设与近似**：逐条记录所用假设（如稳态假设、小角度近似 `\sin\theta \approx \theta`、稀溶液假设、线性响应区、忽略高阶项）及其原文段落锚点；若文中未声明关键近似，标记为 `conditional_on_unstated_assumption`。
4. **成立域与边界条件**：检查分母不为零、对数真数为正、温度 `T > 0 K`、收敛条件与极限边界。

## 3. Pix2Text #142 类 OCR/转写噪音拦截协议
公开社区报告（如 Pix2Text Issue #142）显示，数学证明或公式截图 OCR 常将旁注或误识别的异常中文字符、乱码、控制字符混入公式中。若不拦截直接代入推导，会导致后续代数和数值结论全盘失真。

`math_ast_guard.inspect_formula_noise()` 强制执行以下闸门：
- **`UNEXPECTED_CJK_IN_FORMULA` / `SUSPICIOUS_CJK_ANNOTATION_IN_FORMULA`**：公式中混入异常中文字符或旁注片段 → **立即暂停推导与数值代入**，标为 `halted_for_visual_check`，要求回核原始图像或 XML。
- **`CONTROL_CHAR_OR_TAB_CORRUPTION`**：存在字面制表符 `\t`（典型 `\tau`/`\theta` 转义损坏）或控制符 → 暂停核验。
- **`MOJIBAKE_OR_REPLACEMENT_GLYPH`**：存在 `\ufffd` 或乱码字符 → 暂停核验。
- **`UNBALANCED_DELIMITERS` / `MISMATCHED_LEFT_RIGHT` / `MALFORMED_LATEX_STRUCTURE`**：括号/花括号不配对、`\left`/`\right` 数量不匹配、空 `\frac{}{}` → 暂停核验。

## 4. Markdown 数学渲染自检（`math_markdown_validator.py`）
交付 `formula_notes.md` 前必须通过离线验证器：
- 禁止正文中出现裸 LaTeX 命令（`\frac`, `\sum`, `\alpha`, `\begin` 等未包在 `$`/`$$` 中）。
- 禁止未闭合的 `$`、`$$` 或代码围栏。
- 禁止用普通圆括号冒充数学定界符（如 `(I_0)`、`(E_0 = ...)`、`(tau)`），必须写为 `$I_0$`、`$\tau$`。
- 禁止行间公式重复粘贴；3 个及以上行间公式须提供 `Equation Index` 与连续 `E001, E002, ...` 锚点。
