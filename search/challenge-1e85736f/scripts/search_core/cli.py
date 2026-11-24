# -*- coding: utf-8 -*-
"""Common CLI entry for offline search skills: path resolution, early partial guard, JSON summary."""
from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from typing import Any, Callable, Dict

try:
    from . import caseio
except ImportError:
    import caseio  # type: ignore


def parse_args(argv=None, skill_name: str = "skill") -> argparse.Namespace:
    p = argparse.ArgumentParser(prog=skill_name, description=f"{skill_name} - offline, evidence-bound pipeline")
    p.add_argument("--input", "-i", default="", help="case input dir (default: auto-detect /input)")
    p.add_argument("--output", "-o", default="", help="output dir (default: /app/output or ./output)")
    p.add_argument("--work", "-w", default="", help="work dir (default: /app/work or ./work)")
    p.add_argument("--question", "-q", default="", help="override research question")
    p.add_argument("--no-docs", action="store_true", help="skip PDF/DOCX extraction (metadata-only)")
    p.add_argument("--max-records", type=int, default=0, help="cap records processed (0 = all)")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def resolve_dirs(ns: argparse.Namespace):
    input_dir = caseio.find_input_dir(ns.input) if not ns.input else os.path.abspath(ns.input)
    out = os.path.abspath(ns.output) if ns.output else ("/app/output" if os.path.isdir("/app") else os.path.join(os.getcwd(), "output"))
    work = os.path.abspath(ns.work) if ns.work else ("/app/work" if os.path.isdir("/app") else os.path.join(os.getcwd(), "work"))
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    return input_dir, out, work


def write_partial(out: str, skill: str, stage: str, error: str, case_id: str = ""):
    try:
        os.makedirs(out, exist_ok=True)
        partial = {
            "case_id": case_id,
            "skill": skill,
            "status": {
                "state": "partial",
                "stage": stage,
                "errors": [{"stage": stage, "error": error[:800]}],
                "completed": [],
                "not_completed": ["pipeline did not finish"],
            },
            "results": {"status": "not_produced", "note": f"pipeline stopped at {stage}"},
            "evidence": {"status": "not_produced"},
            "summary": {"text": f"Pipeline stopped at stage '{stage}'. See status.errors."},
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        target = os.path.join(out, "result.json")
        if not os.path.exists(target):
            with open(target, "w", encoding="utf-8") as f:
                json.dump(partial, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def run(skill: str, main_fn: Callable[[argparse.Namespace, str, str, str], Dict[str, Any]], argv=None) -> int:
    ns = parse_args(argv, skill)
    input_dir, out, work = resolve_dirs(ns)
    case_id = ""
    try:
        c = os.path.join(input_dir, "case.json")
        if os.path.exists(c):
            try:
                case_id = str(caseio.read_json(c).get("case_id") or "")
            except Exception:
                pass
        result = main_fn(ns, input_dir, out, work)
        sc = result.get("self_check", {}) if isinstance(result, dict) else {}
        summary = {
            "skill": skill,
            "case_id": result.get("case_id") if isinstance(result, dict) else case_id,
            "ok": sc.get("ok"),
            "issues": [i["msg"] for i in sc.get("issues", [])][:8],
            "contract_fields": sc.get("contract_fields"),
            "filled": sc.get("filled"),
            "output": os.path.join(out, "result.json"),
            "report": os.path.join(out, "report.md"),
            "duration_s": result.get("run", {}).get("duration_s") if isinstance(result, dict) else None,
        }
        if not ns.quiet:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if sc.get("ok", True) else 1
    except Exception as e:
        tb = traceback.format_exc()
        write_partial(out, skill, "exception", f"{type(e).__name__}: {e}\n{tb[-1500:]}", case_id)
        if not ns.quiet:
            print(
                json.dumps(
                    {
                        "skill": skill,
                        "case_id": case_id,
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "output": os.path.join(out, "result.json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 1
