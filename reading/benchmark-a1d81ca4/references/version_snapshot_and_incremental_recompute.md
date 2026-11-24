# 版本快照、可比性判定与增量重算协议

## 1. 为什么不能只用整卡哈希判定可比性

如果对整个题卡字典做单一 SHA-256 哈希，仅修正作者备注（`notes`）、标签（`tags`）或补充 `data_license` 元数据就会让整库哈希变化，误判历史跑分“不可比”并被迫全量重跑。

本协议采用**双哈希分层对比**（`semantic_sha256` vs `full_sha256`）：

| 变更类型 | 哈希表现 | 对历史分数的可比性影响 | 增量重算动作 |
|---|---|---|---|
| `unchanged` | `semantic_sha256` 与 `full_sha256` 均相同 | **完全可比** | 无需重算 |
| `metadata_only_modified` | `semantic_sha256` 相同，仅 `full_sha256` 变化 | **仍然可比**（记录元数据变更日志） | 无需重算 |
| `breaking_modified` | `semantic_sha256` 变化（改动 `prompt`、`rubric`、`expected_values`、`reference_answer`、`unanswerable` 或 `required_paragraph_ids`） | **单题历史分失效** | 将该 `item_id` 加入 `incremental_recompute_ids` |
| `added` | 新版本新增 `item_id` | 仅新题无历史分；旧题交集仍可比 | 将新 `item_id` 加入 `incremental_recompute_ids` |
| `removed` | 新版本删除 `item_id` | 总分口径改变；仅在保留交集上可比 | 标记 `removed_items`，总分按交集重新汇总 |

## 2. 增量重算清单输出格式 (`version_diff`)

```json
{
  "has_prior_snapshot": true,
  "comparable_with_prior": true,
  "full_bank_comparable": false,
  "added": ["DRAFT-05"],
  "removed": [],
  "breaking_modified": ["DRAFT-02"],
  "metadata_only_modified": ["DRAFT-01"],
  "unchanged": ["DRAFT-03", "DRAFT-04"],
  "incremental_recompute_ids": ["DRAFT-02", "DRAFT-05"],
  "reusable_prior_item_ids": ["DRAFT-01", "DRAFT-03", "DRAFT-04"]
}
```

## 3. 从失败样例库到下一版专项回归题 (`regression_item_drafts`)

每当本轮评测在 `failure_taxonomy_bank` 中捕获到典型失败（得分 < 2），运行器按失败标签聚合生成下一版专项回归题草案，包含：
- `draft_item_id`（如 `REG-UNIT-01`）
- `target_failure_tag`（精确绑定六类失败标签之一）
- `source_failures`（追溯原始失败的 `run_id` 与 `item_id`）
- `draft_design_guidance`（建议如何收紧题面/评分器或增加对抗边界）
- `status: "draft_requires_human_review"`（严禁自动冒充已验收正式题）。
