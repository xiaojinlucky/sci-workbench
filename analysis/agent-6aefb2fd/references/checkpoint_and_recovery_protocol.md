# Stage Checkpoint & Verified Recovery Protocol

## 1. Stage State Machine

Each run maintains `<stem>_checkpoint.json` tracking five strictly ordered stages:
`S1_intake -> S2_clean -> S3_stats -> S4_viz -> S5_report`.

Valid stage states:
- `PENDING`: Not started.
- `RUNNING`: Currently executing; if the process terminates in this state, the stage is **unconfirmed** and must be re-executed on resume.
- `COMPLETED`: All expected stage artifacts were written to disk and their SHA-256 digests were persisted.
- `FAILED`: Exception raised; exact error message and UTC timestamp recorded, downstream stages remain `PENDING`, and `overall_status` is set to `FAILED_AT_<stage>`.

## 2. Verified Resume Rules (`--resume`)

1. **Input Fingerprint Lock**: Compare current `sha256(input_file)`, `group_col`, and `value_col` against the stored checkpoint. Any change invalidates the entire checkpoint chain and restarts cleanly from `S1_intake`.
2. **Disk Hash Verification**: Before skipping a stage marked `COMPLETED`, re-compute SHA-256 for every artifact listed in `stages[stage].artifacts`. If any file is missing, truncated, or modified, do not skip — recompute that stage cleanly.
3. **No Unpersisted Text as Output**: Console logs or in-memory summaries never satisfy stage completion. Only disk-persisted, hash-verified artifacts advance the pointer.
4. **Honest Failure Propagation**: A failed stage exits with non-zero code (`1`) and preserves partial upstream artifacts alongside the exact failure record so a collaborator or follow-up Agent knows precisely what succeeded and where execution stopped.
