"""M3: 64-request and 900-second Harbor harness budget circuit breaker."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_DEADLINE_SEC = 900.0
DEFAULT_RESERVE_SEC = 120.0
DEFAULT_MAX_REQUESTS = 64
DEFAULT_RESERVE_REQUESTS = 8


def _atomic_save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def init_budget(
    state_path: str | Path,
    *,
    deadline_sec: float = DEFAULT_DEADLINE_SEC,
    reserve_sec: float = DEFAULT_RESERVE_SEC,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    reserve_requests: int = DEFAULT_RESERVE_REQUESTS,
) -> dict[str, Any]:
    """Initialize budget ledger at the beginning of a sandbox run."""
    p = Path(state_path)
    state: dict[str, Any] = {
        "start_epoch": time.monotonic(),
        "wall_start_epoch": time.time(),
        "deadline_sec": float(deadline_sec),
        "reserve_sec": float(reserve_sec),
        "max_requests": int(max_requests),
        "reserve_requests": int(reserve_requests),
        "requests_used": 0,
        "phases": [],
    }
    _atomic_save(p, state)
    return evaluate_budget(state)


def evaluate_budget(state: dict[str, Any]) -> dict[str, Any]:
    """Compute remaining time/request headroom and circuit-breaker recommendation."""
    now_wall = time.time()
    elapsed_sec = max(0.0, now_wall - float(state.get("wall_start_epoch", now_wall)))
    deadline_sec = float(state.get("deadline_sec", DEFAULT_DEADLINE_SEC))
    reserve_sec = float(state.get("reserve_sec", DEFAULT_RESERVE_SEC))
    max_req = int(state.get("max_requests", DEFAULT_MAX_REQUESTS))
    reserve_req = int(state.get("reserve_requests", DEFAULT_RESERVE_REQUESTS))
    used_req = int(state.get("requests_used", 0))

    rem_sec = round(deadline_sec - elapsed_sec, 2)
    rem_req = max_req - used_req
    soft_sec_hit = elapsed_sec >= max(0.0, deadline_sec - reserve_sec * 1.5)
    hard_sec_hit = elapsed_sec >= max(0.0, deadline_sec - reserve_sec)
    hard_req_hit = used_req >= max(1, max_req - reserve_req)

    must_finalize = hard_sec_hit or hard_req_hit
    if must_finalize:
        action = "FINALIZE_AND_AUDIT_NOW"
    elif soft_sec_hit or rem_req <= reserve_req * 2:
        action = "SWITCH_TO_LIGHTWEIGHT_FALLBACK"
    else:
        action = "CONTINUE"

    return {
        "elapsed_sec": round(elapsed_sec, 2),
        "remaining_sec": rem_sec,
        "requests_used": used_req,
        "remaining_requests": rem_req,
        "must_finalize": must_finalize,
        "action": action,
        "last_phase": (state.get("phases") or [{}])[-1].get("name"),
    }


def tick_phase(state_path: str | Path, phase_name: str, *, step_inc: int = 1) -> dict[str, Any]:
    """Record a completed work step and evaluate circuit-breaker status."""
    p = Path(state_path)
    if not p.is_file():
        init_budget(p)
    state = json.loads(p.read_text(encoding="utf-8"))
    state["requests_used"] = int(state.get("requests_used", 0)) + max(0, int(step_inc))
    phases = list(state.get("phases") or [])
    phases.append({
        "name": phase_name,
        "ts_epoch": round(time.time(), 2),
        "requests_used": state["requests_used"],
    })
    state["phases"] = phases[-50:]
    _atomic_save(p, state)
    return evaluate_budget(state)


def resolve_default_state_path(app_root: str | Path = "/app") -> Path:
    """Place transient budget state under /app/work when /app exists, avoiding /app/output pollution."""
    app_path = Path(app_root)
    if app_path.is_dir():
        return app_path / "work" / ".budget_state.json"
    return Path(".budget_state.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor 64-req / 900s budget circuit breaker.")
    parser.add_argument("--state", default="", help="Budget ledger JSON path (defaults to /app/work/.budget_state.json or ./.budget_state.json).")
    parser.add_argument("--init", action="store_true", help="Initialize budget state.")
    parser.add_argument("--tick", default="", help="Record named phase step.")
    parser.add_argument("--steps", type=int, default=1, help="Request steps to add for --tick.")
    parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE_SEC)
    parser.add_argument("--reserve-sec", type=float, default=DEFAULT_RESERVE_SEC)
    parser.add_argument("--max-req", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--status", action="store_true", help="Read current budget status.")
    args = parser.parse_args(argv)

    p = Path(args.state) if args.state else resolve_default_state_path()
    if args.init:
        res = init_budget(
            p,
            deadline_sec=args.deadline,
            reserve_sec=args.reserve_sec,
            max_requests=args.max_req,
        )
    elif args.tick:
        res = tick_phase(p, args.tick, step_inc=args.steps)
    else:
        if not p.is_file():
            res = init_budget(p, deadline_sec=args.deadline, reserve_sec=args.reserve_sec, max_requests=args.max_req)
        else:
            res = evaluate_budget(json.loads(p.read_text(encoding="utf-8")))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
