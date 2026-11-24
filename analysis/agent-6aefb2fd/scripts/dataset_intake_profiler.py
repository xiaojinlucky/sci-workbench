#!/usr/bin/env python3
"""Zero-assumption read-only dataset & SQLite intake profiler (pure Python 3.12 stdlib).

Profiles CSV, JSON, and read-only SQLite tables strictly without mutating inputs.
Detects leading-zero string IDs, low-cardinality coded integers (e.g., cultivar_code),
exact duplicates, missing rates, zero-variance columns, and IQR outliers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

_LEADING_ZERO_RE = re.compile(r"^0\d+$")
_CODE_NAME_HINTS = ("code", "id", "class", "type", "status", "cultivar", "species", "group", "batch", "flag", "label")
_MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "<lod", "nd", "-999", "-999.0"}


def _quantile_sorted(vals: list[float], p: float) -> float:
    n = len(vals)
    if n == 0:
        return math.nan
    if n == 1:
        return vals[0]
    pos = (n - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def load_records_ro(input_path: Path, table: str | None = None) -> dict[str, Any]:
    """Load CSV/JSON/SQLite strictly read-only preserving string representations."""
    abs_p = input_path.resolve()
    raw_bytes = abs_p.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    ext = abs_p.suffix.lower()

    if ext in {".sqlite", ".sqlite3", ".db"}:
        conn = sqlite3.connect(f"file:{abs_p}?mode=ro", uri=True)
        try:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            if not tables:
                raise ValueError(f"SQLite database has 0 user tables: {abs_p.name}")
            if table:
                chosen = table
            else:
                # Prefer largest fact table rather than smallest alphabetical lookup table
                ranked = sorted(
                    ((int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]), t) for t in tables),
                    reverse=True,
                )
                chosen = ranked[0][1]
            if chosen not in tables:
                raise ValueError(f"Table {chosen!r} not found in {abs_p.name}; available={tables}")
            cur = conn.execute(f'SELECT * FROM "{chosen}"')
            cols = [d[0] for d in cur.description]
            rows = [{c: ("" if v is None else str(v)) for c, v in zip(cols, rec)} for rec in cur.fetchall()]
            return {
                "input_kind": "sqlite_ro",
                "source_file": abs_p.name,
                "source_sha256": sha256,
                "table_used": chosen,
                "tables_available": tables,
                "columns": cols,
                "rows": rows,
            }
        finally:
            conn.close()

    if ext == ".json":
        obj = json.loads(raw_bytes.decode("utf-8-sig"))
        if isinstance(obj, dict) and isinstance(obj.get("records"), list):
            obj = obj["records"]
        if not isinstance(obj, list):
            raise ValueError("JSON input must be a list of record objects or {'records': [...]}")
        cols = list(dict.fromkeys(k for rec in obj if isinstance(rec, dict) for k in rec.keys()))
        rows = [{c: ("" if rec.get(c) is None else str(rec.get(c))) for c in cols} for rec in obj if isinstance(rec, dict)]
        return {
            "input_kind": "json",
            "source_file": abs_p.name,
            "source_sha256": sha256,
            "table_used": None,
            "tables_available": [],
            "columns": cols,
            "rows": rows,
        }

    # CSV or delimited text
    text = ""
    detected_enc = "utf-8"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            detected_enc = enc
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    delim = "\t" if ext == ".tsv" else ("," if not lines else max((",", ";", "\t", "|"), key=lambda d: lines[0].count(d)))
    reader = csv.DictReader(lines, delimiter=delim)
    cols = [c.strip() for c in (reader.fieldnames or [])]
    rows = []
    for rec in reader:
        if all((v is None or str(v).strip() == "") for v in rec.values()):
            continue
        rows.append({str(k).strip(): ("" if v is None else str(v).strip()) for k, v in rec.items() if k is not None})
    return {
        "input_kind": f"delimited({detected_enc},delim={delim!r})",
        "source_file": abs_p.name,
        "source_sha256": sha256,
        "table_used": None,
        "tables_available": [],
        "columns": cols,
        "rows": rows,
    }


def profile_loaded(loaded: dict[str, Any]) -> dict[str, Any]:
    cols = loaded["columns"]
    rows = loaded["rows"]
    seen_exact: set[tuple[str, ...]] = set()
    exact_dups = 0
    for r in rows:
        sig = tuple(r.get(c, "") for c in cols)
        if sig in seen_exact:
            exact_dups += 1
        else:
            seen_exact.add(sig)

    col_profiles: list[dict[str, Any]] = []
    leading_zero_cols: list[str] = []
    coded_int_cols: list[str] = []
    zero_var_cols: list[str] = []

    for c in cols:
        raw_vals = [r.get(c, "").strip() for r in rows]
        missing_cnt = sum(1 for v in raw_vals if v.lower() in _MISSING_TOKENS)
        non_miss = [v for v in raw_vals if v.lower() not in _MISSING_TOKENS]
        unique_vals = list(dict.fromkeys(non_miss))
        nunique = len(unique_vals)
        has_leading_zero = any(_LEADING_ZERO_RE.match(v) for v in non_miss)
        if has_leading_zero:
            leading_zero_cols.append(c)

        num_vals: list[float] = []
        all_int_like = True
        for v in non_miss:
            try:
                fv = float(v)
                if math.isfinite(fv):
                    num_vals.append(fv)
                    if fv != int(fv):
                        all_int_like = False
                else:
                    all_int_like = False
            except ValueError:
                all_int_like = False

        is_numeric = len(non_miss) > 0 and len(num_vals) == len(non_miss) and not has_leading_zero
        coded_flag = False
        stats: dict[str, Any] = {}
        if is_numeric and num_vals:
            svals = sorted(num_vals)
            n = len(svals)
            mean = sum(svals) / n
            sd = math.sqrt(sum((x - mean) ** 2 for x in svals) / (n - 1)) if n > 1 else 0.0
            q1, med, q3 = _quantile_sorted(svals, 0.25), _quantile_sorted(svals, 0.50), _quantile_sorted(svals, 0.75)
            iqr = q3 - q1
            iqr_out = sum(1 for x in svals if iqr > 0 and (x < q1 - 1.5 * iqr or x > q3 + 1.5 * iqr))
            if n > 1 and sd == 0.0:
                zero_var_cols.append(c)
            stats = {
                "min": round(svals[0], 6),
                "q1": round(q1, 6),
                "median": round(med, 6),
                "mean": round(mean, 6),
                "sd": round(sd, 6),
                "q3": round(q3, 6),
                "max": round(svals[-1], 6),
                "iqr_outlier_count": iqr_out,
            }
            if all_int_like and nunique <= min(12, max(3, n // 4)) and (
                any(h in c.lower() for h in _CODE_NAME_HINTS) or nunique <= 6
            ):
                coded_flag = True
                coded_int_cols.append(c)

        col_profiles.append({
            "column": c,
            "non_missing_count": len(non_miss),
            "missing_or_sentinel_count": missing_cnt,
            "missing_rate_pct": round((missing_cnt / len(rows) * 100.0), 2) if rows else 0.0,
            "unique_count": nunique,
            "is_numeric_continuous": bool(is_numeric and not coded_flag),
            "leading_zero_string_id": has_leading_zero,
            "coded_categorical_integer": coded_flag,
            "sample_values": unique_vals[:5],
            "stats": stats,
        })

    return {
        "ok": True,
        "input_kind": loaded["input_kind"],
        "source_file": loaded["source_file"],
        "source_sha256": loaded["source_sha256"],
        "table_used": loaded["table_used"],
        "tables_available": loaded["tables_available"],
        "rows_raw": len(rows),
        "columns_raw": len(cols),
        "exact_duplicate_rows": exact_dups,
        "leading_zero_id_columns": leading_zero_cols,
        "coded_integer_warnings": coded_int_cols,
        "zero_variance_columns": zero_var_cols,
        "columns": col_profiles,
    }


def parse_task_and_dictionary_context(
    columns: list[str],
    *,
    task_md_path: Path | None = None,
    case_json_path: Path | None = None,
    dictionary_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Parse task.md, case.json, and optional data dictionary/workorder files for explicit column-role evidence."""
    col_set = list(columns)
    col_lower = {c.lower(): c for c in col_set}
    mentions: dict[str, list[dict[str, Any]]] = {}
    target_candidates: list[dict[str, Any]] = []
    group_candidates: list[dict[str, Any]] = []
    predictor_candidates: list[dict[str, Any]] = []
    units: dict[str, str] = {}
    workorder_rules: dict[str, Any] = {
        "primary_key": [],
        "forbid_imputation": True,
        "extra_sentinel_tokens": [],
        "sources_parsed": [],
    }

    if task_md_path and task_md_path.is_file():
        workorder_rules["sources_parsed"].append(task_md_path.name)
        lines = task_md_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        for lineno, line in enumerate(lines, start=1):
            anchor = f"{task_md_path.name}#L{lineno}"
            low = line.lower()
            for clow, cname in col_lower.items():
                if re.search(rf"(?<![a-z0-9_]){re.escape(clow)}(?![a-z0-9_])", low):
                    mentions.setdefault(cname, []).append({"anchor": anchor, "excerpt": line.strip()[:160]})
                    m_unit = re.search(rf"{re.escape(clow)}\s*\(([^)]{{1,24}})\)", low)
                    if m_unit and cname not in units:
                        # Recover original casing from line
                        m_orig = re.search(rf"{re.escape(cname)}\s*\(([^)]{{1,24}})\)", line, flags=re.I)
                        units[cname] = m_orig.group(1).strip() if m_orig else m_unit.group(1).strip()

            # Explicit target patterns: "Compare <col> across <grp>", "Analyze <col> as a function of ...", "target/outcome: <col>"
            for pat, role_kind in (
                (r"\b(?:compare|analyze|analyse|predict|model|regress|evaluate)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?", "target_verb"),
                (r"\b(?:target|outcome|dependent|response|y_col|value_col)\s*[:=]\s*`?([a-zA-Z_][a-zA-Z0-9_]*)`?", "target_key"),
            ):
                for m in re.finditer(pat, line, flags=re.I):
                    cand = col_lower.get(m.group(1).lower())
                    if cand:
                        target_candidates.append({"column": cand, "anchor": anchor, "reason": role_kind, "excerpt": line.strip()[:160]})

            # Explicit group patterns: "across <grp>", "by <grp>", "group_col: <grp>"
            for pat, role_kind in (
                (r"\b(?:across|between|stratified\s+by|grouped\s+by|by)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?", "group_prep"),
                (r"\b(?:group|group_col|grouping|strata|factor)\s*[:=]\s*`?([a-zA-Z_][a-zA-Z0-9_]*)`?", "group_key"),
            ):
                for m in re.finditer(pat, line, flags=re.I):
                    cand = col_lower.get(m.group(1).lower())
                    if cand:
                        group_candidates.append({"column": cand, "anchor": anchor, "reason": role_kind, "excerpt": line.strip()[:160]})

            # Predictors pattern: "as a function of <c1>, <c2>, and <c3>"
            m_fn = re.search(r"as\s+a\s+function\s+of\s+([^\n.;]+)", line, flags=re.I)
            if m_fn:
                seg = m_fn.group(1).lower()
                for clow, cname in col_lower.items():
                    if re.search(rf"(?<![a-z0-9_]){re.escape(clow)}(?![a-z0-9_])", seg):
                        predictor_candidates.append({"column": cname, "anchor": anchor, "reason": "function_of_clause", "excerpt": line.strip()[:160]})

    if case_json_path and case_json_path.is_file():
        workorder_rules["sources_parsed"].append(case_json_path.name)
        try:
            cobj = json.loads(case_json_path.read_text(encoding="utf-8-sig"))
            for key in ("target_col", "value_col", "outcome_col", "dependent_variable"):
                val = cobj.get(key)
                if isinstance(val, str) and val.lower() in col_lower:
                    target_candidates.append({"column": col_lower[val.lower()], "anchor": f"{case_json_path.name}#{key}", "reason": "case_json_explicit"})
            for key in ("group_col", "grouping_col", "factor_col"):
                val = cobj.get(key)
                if isinstance(val, str) and val.lower() in col_lower:
                    group_candidates.append({"column": col_lower[val.lower()], "anchor": f"{case_json_path.name}#{key}", "reason": "case_json_explicit"})
            if isinstance(cobj.get("primary_key"), list):
                workorder_rules["primary_key"] = [str(k) for k in cobj["primary_key"] if str(k) in col_set]
        except Exception:
            pass

    for dp in (dictionary_paths or []):
        if not dp.is_file():
            continue
        workorder_rules["sources_parsed"].append(dp.name)
        if dp.suffix.lower() == ".json":
            try:
                dobj = json.loads(dp.read_text(encoding="utf-8-sig"))
                if isinstance(dobj, dict):
                    pk = dobj.get("primary_key")
                    if isinstance(pk, str) and pk in col_set:
                        workorder_rules["primary_key"] = [pk]
                    elif isinstance(pk, list):
                        workorder_rules["primary_key"] = [str(k) for k in pk if str(k) in col_set]
                    if "forbid_imputation" in dobj:
                        workorder_rules["forbid_imputation"] = bool(dobj["forbid_imputation"])
                    if isinstance(dobj.get("sentinel_taxonomy"), dict):
                        for vals in dobj["sentinel_taxonomy"].values():
                            if isinstance(vals, list):
                                workorder_rules["extra_sentinel_tokens"].extend(str(v).lower() for v in vals)
            except Exception:
                pass

    return {
        "mentions": mentions,
        "target_candidates": target_candidates,
        "group_candidates": group_candidates,
        "predictor_candidates": predictor_candidates,
        "units": units,
        "workorder_rules": workorder_rules,
    }


def audit_primary_key_near_duplicates(
    rows: list[dict[str, str]],
    columns: list[str],
    *,
    explicit_pk: list[str] | None = None,
) -> dict[str, Any]:
    """Audit same-primary-key records that carry differing non-key values (near-duplicates)."""
    pk_cols = [c for c in (explicit_pk or []) if c in columns]
    pk_source = "workorder_explicit" if pk_cols else "inferred_id_column"
    if not pk_cols:
        id_cands = [
            c for c in columns
            if c.lower() in {"id", "sample_id", "sample_code", "mix_id", "record_id", "patient_id", "subject_id"}
            or c.lower().endswith(("_id", "_code"))
        ]
        if id_cands:
            pk_cols = [id_cands[0]]
        else:
            pk_source = "no_explicit_id_column_found"
            return {
                "checked": True,
                "pk_columns": [],
                "pk_source": pk_source,
                "same_pk_conflict_groups": 0,
                "total_conflicting_rows": 0,
                "conflict_examples": [],
                "decision": "no_single_id_column_in_table_exact_row_dedup_only",
            }

    non_pk_cols = [c for c in columns if c not in pk_cols]
    by_pk: dict[tuple[str, ...], list[tuple[int, dict[str, str]]]] = {}
    for idx, r in enumerate(rows, start=2):
        key = tuple(r.get(c, "").strip() for c in pk_cols)
        if all(k == "" for k in key):
            continue
        by_pk.setdefault(key, []).append((idx, r))

    conflicts: list[dict[str, Any]] = []
    total_conflict_rows = 0
    for pk_val, recs in by_pk.items():
        if len(recs) <= 1:
            continue
        payload_sigs = {tuple(r.get(c, "").strip() for c in non_pk_cols) for _, r in recs}
        if len(payload_sigs) > 1:
            total_conflict_rows += len(recs)
            diff_cols = [
                c for c in non_pk_cols
                if len({r.get(c, "").strip() for _, r in recs}) > 1
            ]
            conflicts.append({
                "pk_value": dict(zip(pk_cols, pk_val)),
                "row_indices": [i for i, _ in recs[:10]],
                "occurrence_count": len(recs),
                "differing_columns": diff_cols[:10],
            })

    return {
        "checked": True,
        "pk_columns": pk_cols,
        "pk_source": pk_source,
        "unique_pk_count": len(by_pk),
        "same_pk_conflict_groups": len(conflicts),
        "total_conflicting_rows": total_conflict_rows,
        "conflict_examples": conflicts[:10],
        "decision": (
            "flagged_for_manual_review_no_silent_overwrite"
            if conflicts
            else "clean_no_same_pk_value_conflicts"
        ),
    }


def profile_input(input_path: Path, *, table: str | None = None) -> dict[str, Any]:
    return profile_loaded(load_records_ro(input_path, table=table))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only zero-assumption scientific dataset profiler.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--table", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    rep = profile_input(Path(args.input), table=args.table)
    txt = json.dumps(rep, indent=2, ensure_ascii=False)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
