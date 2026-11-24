#!/usr/bin/env python3
"""Example linear imperative research script (stdlib-only) demonstrating safe scientific CSV analysis.

Purpose: Read a cultivar chemistry table (or inline illustrative records), preserve leading-zero
sample codes as text, separate missing values from true numerical zero, check primary-key
uniqueness before grouping, and write verified JSON/CSV summaries.
Run environment: Python 3.12 standard library only (zero external packages required).
Outputs (written to current working directory): cultivar_summary.json, cultivar_summary.csv.
"""

from __future__ import annotations

import csv
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 1. Explicit seed & analysis parameters (kept adjacent to usage)
SEED = 42
random.seed(SEED)
MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nd", "<lod"}

# 2. Load input CSV if provided as argv[1]; otherwise run on a small illustrative dataset
if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
    input_path = Path(sys.argv[1])
    with input_path.open("r", encoding="utf-8-sig", newline="") as fh:
        raw_rows = [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]
    source_label = input_path.name
else:
    raw_rows = [
        {"sample_code": "0001", "cultivar_name": "Cultivar_1", "alcohol": "14.23", "proline": "1065.0"},
        {"sample_code": "0002", "cultivar_name": "Cultivar_1", "alcohol": "13.20", "proline": "1050.0"},
        {"sample_code": "0003", "cultivar_name": "Cultivar_2", "alcohol": "12.37", "proline": "NA"},
        {"sample_code": "0004", "cultivar_name": "Cultivar_2", "alcohol": "12.08", "proline": "0.0"},
    ]
    source_label = "illustrative_in_memory_example"

print(f"[checkpoint:load] source={source_label} raw_rows={len(raw_rows)} sample_head={raw_rows[:2]}")

# 3. Preserve leading-zero sample_code as str and check primary-key uniqueness before aggregation
seen_ids: set[str] = set()
dedup_rows: list[dict[str, str]] = []
duplicate_ids: list[str] = []
for row in raw_rows:
    sid = str(row["sample_code"]).strip()
    if sid in seen_ids:
        duplicate_ids.append(sid)
        continue
    seen_ids.add(sid)
    dedup_rows.append(row)

assert len(dedup_rows) > 0, "Expected at least one valid unique sample record"
print(f"[checkpoint:dedup] unique_samples={len(dedup_rows)} duplicates_dropped={len(duplicate_ids)}")

# 4. Group by cultivar_name while keeping missing (NA) strictly distinct from true zero (0.0)
alcohol_by_group: dict[str, list[float]] = defaultdict(list)
proline_by_group: dict[str, list[float]] = defaultdict(list)
missing_proline_counts: Counter[str] = Counter()
true_zero_proline_counts: Counter[str] = Counter()

for row in dedup_rows:
    grp = str(row["cultivar_name"]).strip()
    alc_txt = str(row.get("alcohol", "")).strip()
    if alc_txt.lower() not in MISSING_TOKENS:
        alcohol_by_group[grp].append(float(alc_txt))
    pro_txt = str(row.get("proline", "")).strip()
    if pro_txt.lower() in MISSING_TOKENS:
        missing_proline_counts[grp] += 1
    else:
        pro_val = float(pro_txt)
        if pro_val == 0.0:
            true_zero_proline_counts[grp] += 1
        proline_by_group[grp].append(pro_val)

summary_rows: list[dict[str, object]] = []
for grp in sorted(set(alcohol_by_group) | set(proline_by_group) | set(missing_proline_counts)):
    alc_vals = alcohol_by_group[grp]
    pro_vals = proline_by_group[grp]
    summary_rows.append(
        {
            "cultivar_name": grp,
            "n_samples": len(alc_vals),
            "mean_alcohol": round(statistics.fmean(alc_vals), 6) if alc_vals else None,
            "valid_proline_n": len(pro_vals),
            "missing_proline_n": missing_proline_counts[grp],
            "true_zero_proline_n": true_zero_proline_counts[grp],
            "mean_proline": round(statistics.fmean(pro_vals), 6) if pro_vals else None,
        }
    )

print(f"[checkpoint:summary] groups={len(summary_rows)} total_n={sum(int(r['n_samples']) for r in summary_rows)}")

# 5. Export verified JSON and CSV outputs into isolated working directory
Path("cultivar_summary.json").write_text(
    json.dumps(
        {
            "source": source_label,
            "seed": SEED,
            "unique_samples": len(dedup_rows),
            "duplicates_dropped": len(duplicate_ids),
            "groups": summary_rows,
        },
        indent=2,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)

with Path("cultivar_summary.csv").open("w", encoding="utf-8", newline="") as fh:
    fieldnames = [
        "cultivar_name",
        "n_samples",
        "mean_alcohol",
        "valid_proline_n",
        "missing_proline_n",
        "true_zero_proline_n",
        "mean_proline",
    ]
    writer = csv.DictWriter(fh, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(summary_rows)
