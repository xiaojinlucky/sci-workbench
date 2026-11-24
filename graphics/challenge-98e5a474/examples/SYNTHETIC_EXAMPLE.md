# SYNTHETIC EXAMPLE — NOT CASE OUTPUT

本示例仅演示 `scientific-schematic` Skill 的示意图生成与源保留增量编辑流程，所含字段均为合成教学示例，严禁充当正式病例或测试结果。

## 最小调用示例

```bash
python3 scripts/schematic_spec_builder.py --spec examples/synthetic_schematic_spec.json --data-table examples/synthetic_table.csv --out-dir /tmp/demo98 --stem demo_sch
python3 scripts/source_preserving_svg_editor.py --svg /tmp/demo98/demo_sch.svg --rebind-table examples/synthetic_table.csv --out-dir /tmp/demo98 --stem demo_sch_rebound
```
