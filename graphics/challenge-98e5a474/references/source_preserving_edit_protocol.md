# Source-Preserving Edit（源保留增量编辑）协议

1. 修改已有 `.svg` 时严禁整图重构重写，必须调用 `scripts/source_preserving_svg_editor.py`。
2. `patch.json` 支持四类原子操作：`update_text`、`update_attrs`、`move_node`、`delete_ids`；配合 `--rebind-table` 可仅更新数据驱动数值与颜色。
3. 每次编辑后必须同时交付：
   - 修改后分层 `.svg`（保留未改元素的 XML `id` 与图层顺序）
   - 同步重导的 `.drawio`、`PNG` 与 `PDF`
   - `*_edit_diff.json`（含 `before_sha256`、`after_sha256`、`preserved_id_ratio` 与逐项 `changes` 清单）
