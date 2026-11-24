# MathML/XML 嵌套解析与反重计规范

## 1. JATS 出版商 XML 结构与重计陷阱
真实科研出版商 XML（如 JATS `publisher-source.xml` 及其派生节点集 `source-formulas.xml`）常在同一公式节点内提供多种等价表示：

```xml
<disp-formula id="eq1">
  <label>(1)</label>
  <alternatives>
    <tex-math>E_a = R T \ln(A / k)</tex-math>
    <mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">
      <mml:semantics>
        <mml:mrow>...</mml:mrow>
        <mml:annotation encoding="application/x-tex">E_a = R T \ln(A / k)</mml:annotation>
      </mml:semantics>
    </mml:math>
  </alternatives>
</disp-formula>
```

常见解析错误（旧版或朴素 `root.iter()` 遍历）：
1. **双重计数**：既匹配外层 `<disp-formula>`，又把内部的 `<tex-math>` 或 `<mml:math>` 再次当成独立公式计入，导致 `formula_count` 虚高。
2. **注释串联污染**：遍历 `<mml:semantics>` 时未剥离 `<mml:annotation>`，把 MathML 降维结果与原始 TeX 注释直接拼接成一段乱码公式。
3. **跨文件重复计入**：同时读取 `source-formulas.xml` 与 `publisher-source.xml` 中的同一公式集合。
4. **忽略正文段落**：因把 `.xml` 全部跳过，导致 `publisher-source.xml` 中 `<p id="...">` 内的变量定义完全漏抽。

## 2. 本 Skill 的确定性提取与反重计规则
1. **文件层级优先与容错回退**：
   - 优先解析 `source-formulas.xml`；仅当其不可读或为空时回退解析 `publisher-source.xml`，并将不可读文件名记入 `gaps[]`（`FORMULA_SOURCE_UNREADABLE`）。
   - `publisher-source.xml` 的 `<p>` 段落始终参与变量定义溯源，但不重复计入公式总数。
2. **自顶向下阻断式遍历**：
   - 遇到 `<disp-formula>`、`<inline-formula>`、`<formula>` 时立即收拢为一个公式条目，**停止向下递归生成新条目**；`<disp-formula-group>` 则继续遍历其子 `<disp-formula>`。
   - 仅当 `<math>` 不在任何公式容器内时，才作为独立 `<math>` 条目输出。
3. **完整来源与表示记录**：
   - 每条公式必须记录：`source_file`、`formula_id`、`xml_tag`、`label`、`representation_source`（`mathml` / `tex-math` / `mathml+tex-math` / `inline_latex` / `raw_text`）。
   - 当同一容器同时包含 `<mml:math>` 与 `<tex-math>` 时，合并为单一公式并交叉比对两者符号集合；若符号集合不一致，标记 `representation_symbol_divergence: true` 并发出 `MATHML_TEX_SYMBOL_DIVERGENCE` 告警。
4. **MathML 完整节点覆盖**：
   - 覆盖 `math`, `mrow`, `mstyle`, `mpadded`, `semantics`（仅渲染首展示子节点，独立提取 TeX `annotation`）, `mi`, `mn`, `mo`（过滤不可见乘号 `\u2062` 与函数应用符 `\u2061`）, `mtext`, `mfrac`, `msup`, `msub`, `msubsup`, `msqrt`, `mroot`, `mtable`/`mtr`/`mtd`（转 `\begin{matrix}`）, `mfenced`（保留 `open`/`close`/`separators`）, `mover`, `munder`, `munderover`, `mmultiscripts`。
5. **XML 安全解析**：
   - 有 `defusedxml` 时优先使用；纯标准库回退时在解析前阻断 `<!DOCTYPE` 与 `<!ENTITY` 实体声明，防范 XXE。
