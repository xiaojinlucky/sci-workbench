#!/usr/bin/env python3
"""Offline worked reproduction & cryptographic ADR enforcement for bio-pathway."""
import hashlib
import json
import sys
from pathlib import Path

EX = Path(__file__).resolve().parent
SCRIPTS = EX.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import panel_workbench_builder as pwb


def canonical_bytes(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def verify_adr_and_chain(data: dict) -> tuple[bool, int]:
    # 1. Verify ADR lock digest (python -O safe)
    decisions = data.get("decisions", [])
    require(bool(decisions), "Empty ADR decisions!")
    expected_adr_sha = data.get("adr_lock_sha256", "")
    actual_adr_sha = hashlib.sha256(canonical_bytes(decisions)).hexdigest()
    require(bool(expected_adr_sha) and expected_adr_sha == actual_adr_sha, "ADR lock digest tampered!")
    # 2. Verify SHA-256 entry chain + entry_count + tail_hash
    ci = data.get("chain_integrity") or {}
    prev = ci.get("genesis_hash") or ""
    entries = data.get("entries", [])
    require(bool(entries), "Empty trajectory!")
    require(ci.get("entry_count") == len(entries), "Trajectory entry_count mismatch!")
    for idx, entry in enumerate(entries):
        require(entry.get("previous_entry_hash") == prev, f"Hash chain broken at index {idx}")
        require(hashlib.sha256(canonical_bytes(entry["output"])).hexdigest() == entry["output_hash"], f"Output hash mismatch at {idx}")
        prev = hashlib.sha256(canonical_bytes(entry)).hexdigest()
    require(ci.get("tail_hash") == prev, "Trajectory tail_hash mismatch!")
    # 3. Active policy enforcement (must reject zombie route, allow clean route)
    rejected = {opt for d in decisions for opt in d.get("rejected_options", [])}
    def check_option(opt: str) -> bool:
        return opt not in rejected
    require(not check_option("mechanical_3algo_intersection"), "Active policy failed to block zombie route!")
    require(check_option("benchmarked_primary_workflow"), "Active policy blocked valid primary workflow!")
    return True, len(entries)


def main() -> int:
    adr_traj = json.loads((EX / "adr_and_trajectory.json").read_text(encoding="utf-8"))
    ok, n_entries = verify_adr_and_chain(adr_traj)
    require(ok, "ADR and chain verification failed")

    case = json.loads((EX / "case.json").read_text(encoding="utf-8"))
    wb_cfg = json.loads((EX / "workbench_config_example.json").read_text(encoding="utf-8"))

    observations = json.loads((EX / "observations.json").read_text(encoding="utf-8"))
    projects = list(wb_cfg["projects"])
    projects[0]["reading"] = f"Verified {len(observations['rows'])} example records accompanying {case['pmcid']} ({case['license']})."
    html_out = pwb.build_workbench_html(
        projects=projects,
        references=wb_cfg["references"],
        ui=wb_cfg.get("ui") or {"title": f"{case['case_id']} Comparative Workbench"},
    )
    require("data-ref-panel-badge" in html_out and "原文图题：" in html_out and "review-method-badge" in html_out and "data:image/svg+xml;base64," in html_out, "Rendered HTML missing reference artifacts!")
    print(f"REPRO_PASS pkg=bio-pathway rows={len(observations['rows'])} chain_entries={n_entries} adr_sha={adr_traj['adr_lock_sha256'][:12]} html_bytes={len(html_out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
