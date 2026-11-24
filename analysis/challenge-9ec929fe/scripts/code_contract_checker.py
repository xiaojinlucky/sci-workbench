#!/usr/bin/env python3
"""Deep AST & lexical static contract checker for reproducible scientific Python scripts.

Pure Python 3.12 standard library. Detects:
- Hardcoded host/user absolute paths
- Silent zero imputation (.fillna(0), .replace(NaN, 0), nan_to_num(..., nan=0))
- Unprotected pandas.read_csv() leading-zero ID ingestion (cross-checked with optional --input-csv)
- Unseeded randomness (random, numpy.random, scipy.stats.*.rvs, sklearn stochastic estimators)
- Swallowed exceptions (bare except / except Exception with pass/.../continue/constant return)
- Unsafe shell execution (os.system, os.popen, subprocess(..., shell=True))
- Unintended SQLite write/DDL operations or missing read-only URI mode
- Unchecked groupby aggregations without visible deduplication awareness
- Missing inline verification checkpoints (research-script readability check)
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from pathlib import Path
from typing import Any

# Regex carefully structured to detect hardcoded Windows/Unix user paths without embedding literal private paths
_WIN_ABS_PATH_RE = re.compile(r"""(?<![A-Za-z0-9_])([A-Za-z]:(?:\\\\|\\|/)(?:[^\\/\s'"]+(?:\\\\|\\|/))+[^\\/\s'"]*)""")
_UNIX_USER_PATH_RE = re.compile(r"""(?<![A-Za-z0-9_])(/(?:home|root|Users)/[A-Za-z0-9_.-]+(?:/[^\s'"]*)?)""")
_SQL_WRITE_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+(?:TABLE|INDEX|VIEW)|"
    r"ALTER\s+TABLE|CREATE\s+(?:TABLE|INDEX|VIEW)|REPLACE\s+INTO|ATTACH\s+DATABASE)\b",
    re.IGNORECASE,
)
_LEADING_ZERO_TOKEN_RE = re.compile(r"^0\d+$")

_STOCHASTIC_ATTRS = {
    "random",
    "rand",
    "randn",
    "randint",
    "normal",
    "uniform",
    "choice",
    "shuffle",
    "permutation",
    "sample",
    "rvs",
    "binomial",
    "poisson",
    "exponential",
    "bootstrap",
}
_STOCHASTIC_ESTIMATORS = {
    "train_test_split",
    "KMeans",
    "RandomForestClassifier",
    "RandomForestRegressor",
    "GradientBoostingClassifier",
    "GradientBoostingRegressor",
    "StratifiedKFold",
    "KFold",
    "TSNE",
    "UMAP",
    "MLPClassifier",
    "MLPRegressor",
}
_CHECKPOINT_ATTRS = {"head", "tail", "shape", "info", "describe", "value_counts", "nunique", "isna", "isnull"}
_CHECKPOINT_FUNCS = {"print", "len", "Counter"}


def _is_zero_constant(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value) == 0.0
    return False


def _is_nan_expr(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Attribute) and node.attr.lower() == "nan":
        return True
    if isinstance(node, ast.Name) and node.id.lower() in {"nan", "na"}:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
        if node.args and isinstance(node.args[0], ast.Constant) and str(node.args[0].value).lower() == "nan":
            return True
    return False


def _is_swallowed_handler(handler: ast.ExceptHandler) -> bool:
    # Only flag bare except or broad Exception/BaseException catches
    is_broad = handler.type is None
    if isinstance(handler.type, ast.Name) and handler.type.id in {"Exception", "BaseException"}:
        is_broad = True
    if not is_broad:
        return False
    body = handler.body
    if not body:
        return True
    if len(body) == 1:
        stmt = body[0]
        if isinstance(stmt, (ast.Pass, ast.Continue)):
            return True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
            return True
        if isinstance(stmt, ast.Return) and (stmt.value is None or isinstance(stmt.value, ast.Constant)):
            return True
    return False


def _detect_csv_leading_zero_columns(csv_path: Path) -> dict[str, Any]:
    """Scan an optional CSV input to identify columns with leading-zero identifiers."""
    info: dict[str, Any] = {"csv": csv_path.name, "leading_zero_columns": [], "bom_detected": False}
    raw = csv_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        info["bom_detected"] = True
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return info
        hits: dict[str, list[str]] = {c: [] for c in reader.fieldnames if c}
        for idx, row in enumerate(reader):
            if idx >= 500:
                break
            for col in list(hits.keys()):
                val = (row.get(col) or "").strip()
                if val and _LEADING_ZERO_TOKEN_RE.match(val):
                    if len(hits[col]) < 3 and val not in hits[col]:
                        hits[col].append(val)
        info["leading_zero_columns"] = [
            {"column": col, "examples": ex} for col, ex in hits.items() if ex
        ]
    return info


def _get_kw(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _line_has_override(lines: list[str], lineno: int, marker: str) -> bool:
    if 1 <= lineno <= len(lines):
        return marker in lines[lineno - 1]
    return False


def audit_script_contract(
    script_path: Path,
    *,
    input_csv: Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Perform deep AST & lexical contract audit on a scientific Python script."""
    src = script_path.read_text(encoding="utf-8-sig")
    lines = src.splitlines()
    findings: list[dict[str, Any]] = []

    def add_finding(rule_id: str, severity: str, line: int, message: str, remediation: str) -> None:
        findings.append(
            {
                "rule_id": rule_id,
                "severity": severity,
                "line": line,
                "message": message,
                "remediation": remediation,
            }
        )

    csv_profile = _detect_csv_leading_zero_columns(input_csv) if input_csv and input_csv.is_file() else None
    has_leading_zero_csv = bool(csv_profile and csv_profile.get("leading_zero_columns"))

    # 1. Parse AST
    try:
        tree = ast.parse(src, filename=str(script_path))
    except SyntaxError as exc:
        add_finding(
            "CONTRACT_SYNTAX_ERROR",
            "BLOCKER",
            int(exc.lineno or 1),
            f"Python syntax error: {exc.msg}",
            "Fix syntax error before running contract checks or isolated execution.",
        )
        return {
            "ok": False,
            "script": script_path.name,
            "line_count": len(lines),
            "blocker_count": 1,
            "warning_count": 0,
            "info_count": 0,
            "blockers": [f"Line {exc.lineno}: {exc.msg}"],
            "warnings": [],
            "findings": findings,
            "csv_profile": csv_profile,
        }

    # 2. Scan string literals & raw lines for hardcoded host paths and SQLite writes
    sqlite_imported = False
    sqlite_connect_lines: list[tuple[int, bool]] = []
    uses_stochastic = False
    has_explicit_seed = "PYTHONHASHSEED" in src
    stochastic_lines: list[int] = []
    groupby_lines: list[int] = []
    groupby_variants: list[tuple[int, str]] = []
    sql_groupby_lines: list[int] = []
    has_dedup_check = False
    checkpoint_count = 0
    has_data_io = False

    def _call_chain_contains_groupby(expr: ast.AST) -> bool:
        cur: ast.AST | None = expr
        while cur is not None:
            if isinstance(cur, ast.Call):
                if isinstance(cur.func, ast.Attribute) and cur.func.attr == "groupby":
                    return True
                cur = cur.func
            elif isinstance(cur, ast.Attribute):
                cur = cur.value
            elif isinstance(cur, ast.Subscript):
                cur = cur.value
            else:
                break
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "sqlite3":
                    sqlite_imported = True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] == "sqlite3":
                sqlite_imported = True

        # String literal checks for host paths, SQL write statements, and SQL GROUP BY
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            ln = int(getattr(node, "lineno", 1))
            if not _line_has_override(lines, ln, "contract-allow-path"):
                if _WIN_ABS_PATH_RE.search(val) or _UNIX_USER_PATH_RE.search(val):
                    add_finding(
                        "CONTRACT_HOST_PATH",
                        "BLOCKER",
                        ln,
                        f"Hardcoded host/user absolute path literal detected: {val[:60]!r}",
                        "Pass paths via CLI arguments (--input/--output) or relative workspace paths under /input and /app/work.",
                    )
            if sqlite_imported and _SQL_WRITE_RE.search(val) and not _line_has_override(lines, ln, "contract-allow-sqlite-write"):
                add_finding(
                    "CONTRACT_SQLITE_WRITE",
                    "BLOCKER",
                    ln,
                    "SQLite write/DDL statement detected in string literal.",
                    "Open input SQLite databases strictly read-only (file:<db>?mode=ro) and write derived outputs to /app/work.",
                )
            if sqlite_imported and re.search(r"\bGROUP\s+BY\b", val, re.IGNORECASE):
                sql_groupby_lines.append(ln)
            if re.search(r"\bSELECT\s+DISTINCT\b", val, re.IGNORECASE):
                has_dedup_check = True

        # f-string SQL GROUP BY detection
        if isinstance(node, ast.JoinedStr) and sqlite_imported:
            joined_txt = "".join(
                part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            ln = int(getattr(node, "lineno", 1))
            if re.search(r"\bGROUP\s+BY\b", joined_txt, re.IGNORECASE):
                sql_groupby_lines.append(ln)
            if re.search(r"\bSELECT\s+DISTINCT\b", joined_txt, re.IGNORECASE):
                has_dedup_check = True

        # Swallowed exception handlers
        if isinstance(node, ast.ExceptHandler) and _is_swallowed_handler(node):
            ln = int(getattr(node, "lineno", 1))
            add_finding(
                "CONTRACT_SWALLOWED_EXCEPTION",
                "BLOCKER",
                ln,
                "Broad exception handler silently swallows errors (pass/continue/constant return).",
                "Remove silent swallowing; catch only expected exception classes and log or propagate raw diagnostics.",
            )

        # Assert counts as checkpoint
        if isinstance(node, ast.Assert):
            checkpoint_count += 1

        # Attribute / Call inspections
        if isinstance(node, ast.Attribute):
            if node.attr in _CHECKPOINT_ATTRS:
                checkpoint_count += 1
            if node.attr in {"drop_duplicates", "duplicated", "is_unique"}:
                has_dedup_check = True

        # Stdlib deduplication variable/set awareness (seen = set(), exact_dups_removed, etc.)
        if isinstance(node, ast.Name) and node.id.lower() in {
            "seen", "seen_keys", "seen_rows", "dups_removed", "exact_dups_removed", "dedup_rows", "unique_keys"
        }:
            has_dedup_check = True

        if isinstance(node, ast.Call):
            ln = int(getattr(node, "lineno", 1))
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name in _CHECKPOINT_FUNCS:
                checkpoint_count += 1

            # Silent zero imputation
            if func_name == "fillna":
                val_arg = node.args[0] if node.args else _get_kw(node, "value")
                if _is_zero_constant(val_arg) and not _line_has_override(lines, ln, "dictionary-authorized-zero"):
                    add_finding(
                        "CONTRACT_SILENT_ZERO_IMPUTE",
                        "BLOCKER",
                        ln,
                        "Silent zero imputation via .fillna(0) blurs missing values (NA) with true numerical zero.",
                        "Keep NA distinct from 0, report valid_n and missing_n separately, or annotate with # dictionary-authorized-zero if explicitly required by the data dictionary.",
                    )
            elif func_name == "replace" and len(node.args) >= 2:
                if _is_nan_expr(node.args[0]) and _is_zero_constant(node.args[1]):
                    if not _line_has_override(lines, ln, "dictionary-authorized-zero"):
                        add_finding(
                            "CONTRACT_SILENT_ZERO_IMPUTE",
                            "BLOCKER",
                            ln,
                            "Silent NaN-to-zero replacement via .replace(NaN, 0) detected.",
                            "Preserve NA separately from true zero; do not coerce missing assay measurements to 0.",
                        )
            elif func_name == "nan_to_num":
                nan_kw = _get_kw(node, "nan")
                if (nan_kw is None or _is_zero_constant(nan_kw)) and not _line_has_override(lines, ln, "dictionary-authorized-zero"):
                    add_finding(
                        "CONTRACT_SILENT_ZERO_IMPUTE",
                        "BLOCKER",
                        ln,
                        "np.nan_to_num() defaults nan to 0.0, silently imputing missing observations.",
                        "Filter missing values explicitly instead of coercing NaN to 0.0.",
                    )

            # CSV loading without leading-zero ID protection
            if func_name in {"read_csv", "read_table"}:
                has_data_io = True
                dtype_kw = _get_kw(node, "dtype")
                conv_kw = _get_kw(node, "converters")
                if dtype_kw is None and conv_kw is None:
                    sev = "BLOCKER" if has_leading_zero_csv else "WARNING"
                    extra = (
                        f" Verified leading-zero columns in {csv_profile['csv']}: "
                        + ", ".join(c["column"] for c in csv_profile["leading_zero_columns"])
                        if has_leading_zero_csv and csv_profile
                        else ""
                    )
                    add_finding(
                        "CONTRACT_LEADING_ZERO_CSV",
                        sev,
                        ln,
                        f"pandas.{func_name}() called without dtype= or converters=; leading-zero sample IDs (e.g. '0001') can be silently truncated to integers.{extra}",
                        "Pass explicit dtype={'sample_code': str, ...} or dtype=str before converting numeric measurement columns.",
                    )
            elif func_name in {"DictReader", "reader", "read_json", "read_sql_query", "read_sql", "open"}:
                has_data_io = True

            # Shell injection / unsafe subprocess
            if func_name in {"system", "popen"}:
                add_finding(
                    "CONTRACT_SHELL_INJECTION",
                    "BLOCKER",
                    ln,
                    f"Unsafe shell call .{func_name}() detected.",
                    "Use subprocess.run([...], shell=False, capture_output=True, text=True) with explicit argument list.",
                )
            elif func_name in {"run", "Popen", "call", "check_call", "check_output"}:
                sh_kw = _get_kw(node, "shell")
                if isinstance(sh_kw, ast.Constant) and sh_kw.value is True:
                    add_finding(
                        "CONTRACT_SHELL_INJECTION",
                        "BLOCKER",
                        ln,
                        f"subprocess.{func_name}(..., shell=True) detected.",
                        "Pass command as a list with shell=False to prevent shell injection and quoting bugs.",
                    )

            # SQLite connect read-only inspection
            if func_name == "connect" and sqlite_imported:
                uri_kw = _get_kw(node, "uri")
                is_ro = isinstance(uri_kw, ast.Constant) and uri_kw.value is True
                if is_ro and node.args and isinstance(node.args[0], ast.Constant) and "mode=ro" not in str(node.args[0].value):
                    is_ro = False
                sqlite_connect_lines.append((ln, is_ro))

            # Randomness & seed checks
            if func_name in {"seed", "default_rng", "RandomState", "Random"} and (node.args or node.keywords):
                has_explicit_seed = True
            if _get_kw(node, "random_state") is not None or _get_kw(node, "seed") is not None:
                has_explicit_seed = True
            if func_name in _STOCHASTIC_ATTRS:
                # Exclude attribute names on clearly unrelated objects only if needed; conservative check
                uses_stochastic = True
                stochastic_lines.append(ln)
            if func_name in _STOCHASTIC_ESTIMATORS and _get_kw(node, "random_state") is None:
                uses_stochastic = True
                stochastic_lines.append(ln)

            # Groupby deduplication awareness (including .transform / .filter / .agg / .apply / .pipe chains)
            if func_name == "groupby":
                groupby_lines.append(ln)
            elif func_name in {"transform", "filter", "agg", "aggregate", "apply", "pipe"} and isinstance(node.func, ast.Attribute):
                if _call_chain_contains_groupby(node.func.value):
                    groupby_variants.append((ln, func_name))

    # Post-walk checks
    if uses_stochastic and not has_explicit_seed:
        add_finding(
            "CONTRACT_UNSEEDED_RNG",
            "BLOCKER",
            stochastic_lines[0] if stochastic_lines else 1,
            "Stochastic operation detected without explicit RNG seed (random.seed / default_rng(seed) / random_state).",
            "Pin an explicit seed (e.g., SEED = 42; random.seed(SEED) or np.random.default_rng(SEED)) adjacent to the stochastic step.",
        )

    for ln, is_ro in sqlite_connect_lines:
        if not is_ro and not _line_has_override(lines, ln, "contract-allow-sqlite-write"):
            add_finding(
                "CONTRACT_SQLITE_NON_RO",
                "WARNING",
                ln,
                "sqlite3.connect() called without read-only URI mode (file:<path>?mode=ro, uri=True).",
                "Open input scientific SQLite snapshots with sqlite3.connect(f'file:{db}?mode=ro', uri=True) to guarantee zero mutation.",
            )

    if (groupby_lines or groupby_variants or sql_groupby_lines) and not has_dedup_check and "set(" not in src and "distinct" not in src.lower():
        if groupby_variants:
            ln0, var_name = groupby_variants[0]
            add_finding(
                "CONTRACT_UNCHECKED_GROUPBY",
                "WARNING",
                ln0,
                f"groupby().{var_name}() aggregation detected without an explicit duplicate-row or primary-key uniqueness check.",
                "Verify exact duplicates and primary-key uniqueness (drop_duplicates / set / DISTINCT) before groupby transform/filter/agg, and log pre/post row counts.",
            )
        elif groupby_lines:
            add_finding(
                "CONTRACT_UNCHECKED_GROUPBY",
                "WARNING",
                groupby_lines[0],
                "groupby() aggregation detected without an explicit duplicate-row or primary-key uniqueness check.",
                "Verify exact duplicates and primary-key uniqueness before aggregating, and log pre/post row counts.",
            )
        elif sql_groupby_lines:
            add_finding(
                "CONTRACT_UNCHECKED_GROUPBY",
                "WARNING",
                sql_groupby_lines[0],
                "SQL GROUP BY query detected without visible primary-key cardinality or DISTINCT deduplication verification.",
                "Pre-aggregate 1:N child tables or check COUNT(*) vs COUNT(DISTINCT key) before joining/grouping.",
            )

    if has_data_io and len(lines) >= 20 and checkpoint_count == 0:
        add_finding(
            "CONTRACT_BIOCODE_CHECKPOINTS",
            "INFO",
            1,
            "Analysis script (>=20 lines) has no inline verification checkpoints (print/shape/head/len/Counter/assert).",
            "Add concise inline checks at load, clean, and aggregate transitions so logs document row counts and group sizes.",
        )

    blockers = [f"[{f['rule_id']}] L{f['line']}: {f['message']}" for f in findings if f["severity"] == "BLOCKER"]
    warnings = [f"[{f['rule_id']}] L{f['line']}: {f['message']}" for f in findings if f["severity"] == "WARNING"]
    infos = [f"[{f['rule_id']}] L{f['line']}: {f['message']}" for f in findings if f["severity"] == "INFO"]

    ok = len(blockers) == 0 and (len(warnings) == 0 if strict else True)
    return {
        "ok": ok,
        "strict": strict,
        "script": script_path.name,
        "line_count": len(lines),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "info_count": len(infos),
        "blockers": blockers,
        "warnings": warnings,
        "infos": infos,
        "findings": findings,
        "csv_profile": csv_profile,
        "readability_summary": {
            "inline_checkpoints_detected": checkpoint_count,
            "explicit_seed_detected": has_explicit_seed,
            "dedup_check_detected": has_dedup_check,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deep AST static contract checker for scientific Python scripts.")
    parser.add_argument("--script", required=True, help="Path to target Python script to audit.")
    parser.add_argument("--input-csv", default=None, help="Optional CSV input to check for BOM & leading-zero ID columns.")
    parser.add_argument("--out", default=None, help="Optional JSON output report path.")
    parser.add_argument("--strict", action="store_true", help="Fail (exit 1) on warnings in addition to blockers.")
    args = parser.parse_args(argv)

    rep = audit_script_contract(
        Path(args.script),
        input_csv=Path(args.input_csv) if args.input_csv else None,
        strict=args.strict,
    )
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
