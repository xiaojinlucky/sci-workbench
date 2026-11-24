# 生物机制图与通路图 Skill

本 Skill 面向科研生物机制图与信号通路图的端到端生成、拓扑语义校验、源数据冲突检测（`DATA_DIAGRAM_CONFLICT`）、双源可编辑交付（分层 SVG + 原生绑定 `.drawio` + `300 dpi` PNG/PDF）与源保留增量编辑（source-preserving edit）。

## 核心能力

1. **亚细胞区室与极性拓扑门禁（`scripts/pathway_topology_linter.py`）**
   - 校验生物实体类型（`gene` / `protein` / `complex` / `family` / `metabolite` / `ligand` / `receptor` / `rna`）与命名规范（区分基因斜体与蛋白正体，检测同源词混用）。
   - 强制亚细胞空间约束：拦截跨膜受体误置入细胞核、胞外配体未过膜直接调控核基因等拓扑错误。
   - 严格区分激活（`->`）、抑制（`-|` 平头 T-bar）、结合（`--o`）、修饰（`+P`/`+Ub`）与转运/不确定虚线（`--`），并交叉核对渲染 SVG 中的 `<marker>` 引用。
   - 检查数据编码（颜色/线宽）与图例及源数据的三向一致性。无来源的边必须显式标为不确定，以图例中的开放端虚线显示并记入 `gaps`；否则构图前直接阻断，避免把无出处的关系画成确定机制。

2. **分层 SVG + 原生 `.drawio` + PNG/PDF 双源构建与增量编辑（`scripts/bio_pathway_builder.py`）**
   - 生成严格分层的 SVG（`layer-background` / `layer-compartments` / `layer-edges` / `layer-nodes` / `layer-modifications` / `layer-legend`），所有文本保持可编辑 `<text>`。
   - 通过纯 Python 的 `scripts/svg2drawio/` 将分层 SVG 转为逐元素可拖拽的 `.drawio` `mxCell`（`defusedxml` 优先 + 标准库 `xml.etree.ElementTree` 安全回退，并把 `arrow-inhibit` 映射为 draw.io `endArrow=dash` 平头抑制符号）。
   - 支持 `--edit-svg` + `--patch` 增量修补已有 SVG，保留未修改元素的 XML `id`、层结构与坐标并输出 `*_edit_diff.json`。
   - 调用 `scripts/render_preview.py`、`scripts/portable_svg_audit.py`、`scripts/audit_editability.py` 与 `scripts/drawio_layout_check.py` 执行零重叠、零越界、可编辑性与预览图闭环检查。

3. **沙箱防 0 分公共底座（`scripts/preflight_probe.py` 等 6 个基础护栏脚本）**
   - 自动探测 `/app/output` 与 `/app/work`，提前写盘骨架 `result.json`，跟踪 64 请求 / 900 秒预算，并在退出前通过 `final_audit.py` 校验交付契约。

## 目录结构

- `SKILL.md`：Agent 触发指令与标准运行流水线。
- `README.md`：本说明文档。
- `THIRD_PARTY_NOTICES.md`：第三方 MIT/Apache-2.0 组件归属与裁剪说明。
- `scripts/`：6 个基础护栏脚本、生物通路构建与拓扑 Linter、纯净 SVG/draw.io 转换与审计组件。
- `references/`：生物通路语义规范、区室与极性检查矩阵。
- `examples/`：最小合成调用示例（仅用于展示结构，非真实测试案例结果）。

## 依赖与限制

- 仅依赖 Python 3.10+ 标准库及容器基础科学栈（`matplotlib`、`numpy`）；`defusedxml` 与 `resvg_py` 为可选加速，缺失时自动安全回退到标准库 `xml.etree.ElementTree` 与 Matplotlib 矢量镜像渲染。
- 本 Skill 不负责上游生信差异分析或统计假设检验，仅依据输入材料忠实绘图并披露证据缺口。
