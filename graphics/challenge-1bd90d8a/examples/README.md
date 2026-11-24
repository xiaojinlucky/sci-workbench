# Worked Research Example: Source-Grounded Publication Figure Assembly

- **Primary Source**: `10.1038/s41467-021-25960-2` (`PMC8479118`, `CC BY 4.0`)
- **Build Offline Workbench On Demand**: See [`workbench_config_example.json`](workbench_config_example.json) and run `python ../scripts/panel_workbench_builder.py --config workbench_config_example.json -o /tmp/panel_workbench.html`.
- **Offline Verification & ADR Replay**: `python reproduce.py` (verifies `PMC8479118` tidy TSV + intact reference figure build + blocks `mechanical_3algo_intersection` zombie-route replay via `adr_and_trajectory.json`).
