# 上下文锁去重与重复实验边界指南

## 1. 上下文锁签名 (`context_signature`)

判断两条记录是否属于“同一抽取对象的重复录入”前，必须构建由全部条件维度组成的上下文锁签名：
`source_document_id`, `paper_id`, `material`, `catalyst`, `pollutant`, `substrate`, `dataset`, `temperature`, `ph`, `solvent`, `atmosphere`, `experimental_condition`, `condition_key`, `replicate_id`。

任一维度存在实质差异，即视为**不同实验条件或不同重复实验**，严格禁止合并。

## 2. 三分层精确判定（彻底区分去重、分轨保留与科学冲突）

| 分层状态 | 判定条件 | 处理动作 | 落盘位置 |
|---|---|---|---|
| `DUPLICATE_MERGED` | 相同 `entity`/`DOI` + 相同 `metric` + 完全一致的 `context_signature` + 标准化数值/删失态等价（相对误差 ≤ `1e-4`） | 合并为 1 条规范记录，保留 `merged_row_ids`、`alt_dois`，若预印本与正式版重复则自动升级为正式版 DOI | `cleaned_records.csv` + `duplicate_log` |
| `CONTEXT_DISTINCT_PRESERVED` | 相同 `DOI`/`entity` + 相同 `metric`，但 `context_signature` 不同（如 25 °C vs 60 °C、pH 4 vs pH 7、Batch A vs Batch B） | **强制保留为各自独立行**，严禁合并，也不污染 `scientific_conflicts` | `cleaned_records.csv` + `context_preserved_log` |
| `VALUE_CONFLICT_SAME_CONTEXT` | 相同 `entity`/`DOI` + 相同 `metric` + 完全一致的 `context_signature`，但标准化后数值或删失结论冲突 | **严禁静默覆盖或丢弃冲突行**；双方记录均保留并标记 `conflict_status="VALUE_CONFLICT_SAME_CONTEXT"`，写明冲突值对，路由人工科学裁决 | `cleaned_records.csv` + `scientific_conflicts` + `needs_human_review` |

## 3. 实体/指标同义词归一边界 (`R04_SYNONYM_MAP`)

仅在 `case.json` 明确提供 `synonym_map` 或内置高置信标准缩写映射时执行同义归一，且必须在 `changelog` 中记录 `before -> after`；严禁对化学结构/晶型/掺杂比例不同的材料（如 `Cu-OD` vs `CuIn-OD`）做激进模糊合并。
