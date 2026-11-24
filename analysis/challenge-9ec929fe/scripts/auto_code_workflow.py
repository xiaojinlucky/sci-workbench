#!/usr/bin/env python3
"""Zero-manual /input -> /app/output reproducible scientific code workflow (challenge-9ec929fe).

Builds an explicit, evidence-grounded `analysis_plan.json` BEFORE code generation or execution:
  - Parses `/input/case.json`, discovered data dictionaries/workorders, `/input/task.md`
    semantic role clauses, and SQLite PRAGMAs (`table_info`, `foreign_key_list`, `index_list`,
    `index_info`) without row-count heuristics, column-order guessing, or hardcoded column names.
  - Verifies join-key uniqueness, orphan keys, and 1:N JOIN cardinality prior to execution.
  - Stops honestly (`status="partial_with_gaps"` + structured `gaps`) whenever candidate tables,
    join keys, target/grouping columns, or observation primary keys are ambiguous or invalid.
  - Enforces primary-key-bound deduplication with full row-level `dedup_audit.json` (and disables
    silent row deletion when no observation primary key is authorized).
  - Audits offline environment lock, AST static contract, isolated execution, fresh SHA-256
    artifacts, structural invariants, anti-stale markers, and Gate 7 independent numeric
    cross-check (`max_rel_err < 1e-9`), then seals `result.json` with zero host paths.
Pure Python 3.12 stdlib.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from case_contract_sealer import discover_input_dir, seal_result_contract
from code_contract_checker import audit_script_contract
from env_lock_auditor import audit_environment
from isolated_code_runner import run_isolated

_DEFAULT_MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "<lod", "nd", "-999"}
_CODED_ID_SUFFIXES = ("_id", "_code", "code")
_NUMERIC_SQL_TYPES = ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")


def _is_coded_or_id(col: str) -> bool:
    low = col.lower()
    return low.endswith(_CODED_ID_SUFFIXES) or low in {"id", "uuid", "pk", "record_id"}


def _is_sql_numeric_type(declared_type: str) -> bool:
    up = (declared_type or "").strip().upper()
    return any(tok in up for tok in _NUMERIC_SQL_TYPES)


def _as_str_list(val: Any) -> list[str]:
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    return []


def _load_case_and_dictionary_metadata(
    input_dir: Path,
    disc: dict[str, Any],
    task_text: str,
) -> dict[str, Any]:
    """Extract structured schema, role, PK, and constraint declarations from case.json, dictionaries, and task.md."""
    sources_used: list[str] = []
    meta: dict[str, Any] = {
        "primary_table": None,
        "dimension_table": None,
        "dimension_key": None,
        "dimension_label": None,
        "child_table": None,
        "child_key": None,
        "join_keys": {},
        "target_cols": [],
        "predictors": [],
        "group_col": None,
        "primary_key": [],
        "primary_key_source": None,
        "dedup_authorized": None,
        "do_not_average": [],
        "missing_tokens": sorted(_DEFAULT_MISSING_TOKENS),
        "column_roles": {},
        "sources_used": sources_used,
    }

    def _merge_dict_spec(obj: dict[str, Any], src_label: str) -> None:
        if not isinstance(obj, dict):
            return
        containers = [obj]
        for nested_k in ("analysis_spec", "schema", "data_dictionary", "metadata", "relations", "cleaning_rules"):
            if isinstance(obj.get(nested_k), dict):
                containers.append(obj[nested_k])
        for c in containers:
            for k in ("primary_table", "dimension_table", "dimension_key", "dimension_label", "child_table", "child_key", "group_col"):
                if c.get(k) and not meta[k]:
                    meta[k] = str(c[k]).strip()
            if c.get("group_column") and not meta["group_col"]:
                meta["group_col"] = str(c["group_column"]).strip()
            if isinstance(c.get("join_keys"), dict):
                meta["join_keys"].update(c["join_keys"])
            for tk in ("target", "target_column", "target_columns", "dependent_variable", "outcome"):
                if c.get(tk):
                    for t_name in _as_str_list(c[tk]):
                        if t_name not in meta["target_cols"]:
                            meta["target_cols"].append(t_name)
            for pk_pred in ("predictors", "features", "independent_variables"):
                if c.get(pk_pred):
                    for p_name in _as_str_list(c[pk_pred]):
                        if p_name not in meta["predictors"]:
                            meta["predictors"].append(p_name)
            for pk_k in ("primary_key", "observation_key", "observation_primary_key", "dedup_key"):
                if c.get(pk_k) and not meta["primary_key"]:
                    meta["primary_key"] = _as_str_list(c[pk_k])
                    meta["primary_key_source"] = src_label
            if "dedup_authorized" in c and meta["dedup_authorized"] is None:
                meta["dedup_authorized"] = bool(c["dedup_authorized"])
            for dna_k in ("do_not_average", "coded_columns", "exclude_from_averaging"):
                for col in _as_str_list(c.get(dna_k)):
                    if col not in meta["do_not_average"]:
                        meta["do_not_average"].append(col)
            if isinstance(c.get("missing_tokens"), list):
                toks = set(meta["missing_tokens"]) | {str(x).strip().lower() for x in c["missing_tokens"]}
                meta["missing_tokens"] = sorted(toks)
            for cols_k in ("columns", "fields", "variables"):
                if isinstance(c.get(cols_k), dict):
                    for cname, cspec in c[cols_k].items():
                        if isinstance(cspec, dict):
                            meta["column_roles"][cname] = cspec
                            role = str(cspec.get("role", "")).lower()
                            if role in {"target", "dependent", "outcome"} and cname not in meta["target_cols"]:
                                meta["target_cols"].append(cname)
                            elif role in {"predictor", "feature", "independent"} and cname not in meta["predictors"]:
                                meta["predictors"].append(cname)
                            elif role in {"group", "grouping", "strata"} and not meta["group_col"]:
                                meta["group_col"] = cname
                            elif role in {"primary_key", "pk", "observation_key"} and cname not in meta["primary_key"]:
                                meta["primary_key"].append(cname)
                                meta["primary_key_source"] = src_label
                            if cspec.get("do_not_average") and cname not in meta["do_not_average"]:
                                meta["do_not_average"].append(cname)

    case_path = input_dir / "case.json"
    if case_path.is_file():
        try:
            _merge_dict_spec(json.loads(case_path.read_text(encoding="utf-8-sig")), "case.json")
            sources_used.append("case.json")
        except Exception:
            pass

    for entry in (disc.get("dictionaries") or []) + (disc.get("workorders") or []):
        fp = Path(entry["abs"])
        if fp.is_file() and fp.suffix.lower() == ".json":
            try:
                _merge_dict_spec(json.loads(fp.read_text(encoding="utf-8-sig")), fp.name)
                sources_used.append(fp.name)
            except Exception:
                pass

    if task_text.strip():
        sources_used.append("task.md")
        # Extract explicit `do not average ...` directives from task.md
        for sentence in re.findall(r"do not average[^\n.]*", task_text, re.IGNORECASE):
            for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sentence):
                if tok not in meta["do_not_average"]:
                    meta["do_not_average"].append(tok)
        # Extract explicit primary key / dedup key directives from task.md
        pk_match = re.search(
            r"\b(?:primary\s+key|observation\s+key|deduplicate\s+by)\s*[:=]?\s*`?([A-Za-z_][A-Za-z0-9_]*)`?",
            task_text,
            re.IGNORECASE,
        )
        if pk_match and not meta["primary_key"]:
            meta["primary_key"] = [pk_match.group(1)]
            meta["primary_key_source"] = "task.md"
            if meta["dedup_authorized"] is None:
                meta["dedup_authorized"] = True

    return meta


def _inspect_csv_full(csv_path: Path, missing_tokens: set[str]) -> dict[str, Any]:
    """Stream full CSV preserving embedded newlines and leading-zero IDs; profile every column."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = [c.strip() for c in (reader.fieldnames or [])]
        raw_rows = [
            {k.strip(): ("" if v is None else v.strip()) for k, v in r.items() if k is not None}
            for r in reader
        ]

    n_rows = len(raw_rows)
    full_sigs = [tuple(r.get(c, "") for c in cols) for r in raw_rows]
    exact_dup_count = n_rows - len(set(full_sigs))

    col_profiles: dict[str, dict[str, Any]] = {}
    cont_cols: list[str] = []
    cat_cols: list[str] = []
    coded_cols: list[str] = []
    unique_id_cols: list[str] = []

    for c in cols:
        vals_all = [r.get(c, "") for r in raw_rows]
        non_miss = [v for v in vals_all if v.lower() not in missing_tokens]
        miss_n = n_rows - len(non_miss)
        distinct_non_miss = len(set(non_miss))
        has_lz = any(len(v) > 1 and v.startswith("0") and v.isdigit() for v in non_miss)
        is_coded = has_lz or _is_coded_or_id(c)
        is_unique_full = (n_rows > 0 and miss_n == 0 and distinct_non_miss == n_rows)

        nums: list[float] = []
        all_numeric = len(non_miss) > 0
        for v in non_miss:
            try:
                fv = float(v)
                if math.isfinite(fv):
                    nums.append(fv)
                else:
                    all_numeric = False
                    break
            except ValueError:
                all_numeric = False
                break

        inferred_kind = "text"
        if is_coded and is_unique_full:
            inferred_kind = "unique_identifier"
            unique_id_cols.append(c)
            coded_cols.append(c)
        elif is_coded:
            inferred_kind = "coded_or_id"
            coded_cols.append(c)
        elif all_numeric and len(set(nums)) > 1:
            inferred_kind = "numeric_continuous"
            cont_cols.append(c)
        elif not all_numeric and 2 <= distinct_non_miss <= 20:
            inferred_kind = "categorical"
            cat_cols.append(c)

        col_profiles[c] = {
            "inferred_kind": inferred_kind,
            "non_missing": len(non_miss),
            "missing": miss_n,
            "distinct_non_missing": distinct_non_miss,
            "leading_zero_detected": has_lz,
            "is_unique_non_null": is_unique_full,
        }

    return {
        "cols": cols,
        "n_rows": n_rows,
        "exact_duplicate_full_rows": exact_dup_count,
        "cont_cols": cont_cols,
        "cat_cols": cat_cols,
        "coded_cols": coded_cols,
        "unique_id_cols": unique_id_cols,
        "col_profiles": col_profiles,
    }


def _match_columns_in_fragment(fragment: str, candidates: list[str]) -> list[str]:
    hits: list[tuple[int, str]] = []
    for c in candidates:
        m = re.search(rf"(?<![A-Za-z0-9_]){re.escape(c)}(?![A-Za-z0-9_])", fragment)
        if m:
            hits.append((m.start(), c))
    hits.sort(key=lambda x: x[0])
    return [c for _, c in hits]


def _plan_csv_analysis(
    csv_path: Path,
    task_text: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Build explicit CSV analysis_plan.json from metadata + task.md semantic clauses; stop on ambiguity."""
    missing_tokens = set(meta["missing_tokens"])
    prof = _inspect_csv_full(csv_path, missing_tokens)
    cols = prof["cols"]
    cont_cols = prof["cont_cols"]
    cat_cols = prof["cat_cols"]
    coded_cols = sorted(set(prof["coded_cols"]) | {c for c in meta["do_not_average"] if c in cols})

    gaps: list[dict[str, str]] = []
    uncertainties: list[str] = []

    # 1. Observation Primary Key & Deduplication Authorization
    pk_cols = [c for c in meta["primary_key"] if c in cols]
    pk_source = meta["primary_key_source"]
    dedup_authorized = meta["dedup_authorized"]

    if pk_cols:
        dedup_policy = "pk_bound_dedup"
        if dedup_authorized is None:
            dedup_authorized = True
    elif len(prof["unique_id_cols"]) == 1:
        pk_cols = [prof["unique_id_cols"][0]]
        pk_source = "unique_identifier_column"
        dedup_authorized = True if dedup_authorized is None else bool(dedup_authorized)
        dedup_policy = "pk_bound_dedup"
    else:
        pk_cols = []
        pk_source = "none"
        dedup_authorized = bool(dedup_authorized) if dedup_authorized is not None else False
        dedup_policy = "report_only_no_pk"
        if prof["exact_duplicate_full_rows"] > 0 and not dedup_authorized:
            gaps.append(
                {
                    "code": "UNKEYED_DUPLICATE_ROWS_NO_PK_AUTHORIZATION",
                    "severity": "BLOCKER",
                    "detail": (
                        f"Detected {prof['exact_duplicate_full_rows']} exact duplicate row(s) in {csv_path.name} "
                        "with no declared observation primary key or deduplication authorization; refusing silent row deletion."
                    ),
                    "remediation": "Declare observation primary_key or explicit dedup_authorized policy in case.json or data dictionary.",
                }
            )
        else:
            uncertainties.append(
                f"No observation primary key column in {csv_path.name}; silent row deduplication is disabled "
                f"(report-only audit verified {prof['exact_duplicate_full_rows']} exact duplicate rows)."
            )

    # 2. Semantic Role Resolution (NO blind column-order guessing!)
    mode: str | None = None
    target_col: str | None = None
    target_source: str | None = None
    predictors: list[str] = []
    predictors_source: str | None = None
    group_col: str | None = None
    group_source: str | None = None

    # 2a. Explicit case.json / dictionary specification
    meta_targets = [c for c in meta["target_cols"] if c in cols]
    meta_preds = [c for c in meta["predictors"] if c in cols]
    meta_group = meta["group_col"] if meta.get("group_col") in cols else None

    if meta_targets and meta_group:
        mode = "csv_group"
        target_col = meta_targets[0]
        target_source = "case_json_or_dictionary"
        group_col = meta_group
        group_source = "case_json_or_dictionary"
    elif meta_targets and meta_preds:
        mode = "csv_regression"
        target_col = meta_targets[0]
        target_source = "case_json_or_dictionary"
        predictors = [p for p in meta_preds if p != target_col]
        predictors_source = "case_json_or_dictionary"

    # 2b. Semantic clause parsing from task.md (order-invariant for target vs predictors/group)
    if mode is None and task_text.strip() and cont_cols:
        cont_alt = "|".join(re.escape(c) for c in sorted(cont_cols, key=len, reverse=True))
        cat_candidates = list(dict.fromkeys(cat_cols + [c for c in cols if c not in cont_cols and c not in pk_cols]))
        cat_alt = "|".join(re.escape(c) for c in sorted(cat_candidates, key=len, reverse=True)) if cat_candidates else ""

        # Check explicit group-comparison semantic clauses first
        if cat_alt:
            grp_patterns = [
                ("task_md_semantic:compare_Y_across_G", rf"\bcompare\s+(?P<y>{cont_alt})\b[^.\n]{{0,40}}\b(?:across|by|between|among)\s+(?P<g>{cat_alt})\b"),
                ("task_md_semantic:across_G_compare_Y", rf"\b(?:across|by|between|among)\s+(?P<g>{cat_alt})\b[^.\n]{{0,50}}\bcompare\s+(?P<y>{cont_alt})\b"),
                ("task_md_semantic:differences_in_Y_by_G", rf"\bdifference(?:s)?\s+in\s+(?P<y>{cont_alt})\b[^.\n]{{0,40}}\b(?:across|by|between|among)\s+(?P<g>{cat_alt})\b"),
                ("task_md_semantic:Y_across_G", rf"(?P<y>{cont_alt})\b[^.\n]{{0,30}}\b(?:across|by|between)\s+(?P<g>{cat_alt})\b"),
            ]
            for src_tag, pat in grp_patterns:
                m = re.search(pat, task_text, re.IGNORECASE)
                if m:
                    mode = "csv_group"
                    target_col = next(c for c in cont_cols if c.lower() == m.group("y").lower())
                    group_col = next(c for c in cat_candidates if c.lower() == m.group("g").lower())
                    target_source = src_tag
                    group_source = src_tag
                    break

        # Check explicit multivariable regression semantic clauses (handles both Y-first and X-first phrasing)
        if mode is None:
            reg_patterns = [
                ("task_md_semantic:Y_as_function_of_X", rf"(?P<y>{cont_alt})\b[^.\n]{{0,40}}\bas\s+a\s+function\s+of\s+(?P<xs>[^.\n]+)"),
                ("task_md_semantic:regress_Y_on_X", rf"\bregress\s+(?P<y>{cont_alt})\s+on\s+(?P<xs>[^.\n]+)"),
                ("task_md_semantic:predict_Y_from_X", rf"\bpredict\s+(?P<y>{cont_alt})\b[^.\n]{{0,25}}\b(?:from|using|with|by)\s+(?P<xs>[^.\n]+)"),
                ("task_md_semantic:using_X_predict_Y", rf"\b(?:using|from|with)\s+(?P<xs>[^.\n]{{1,220}}?)\s*(?:,|\s)+(?:to\s+)?predict\s+(?P<y>{cont_alt})\b"),
                ("task_md_semantic:effect_of_X_on_Y", rf"\beffect(?:s)?\s+of\s+(?P<xs>[^.\n]{{1,180}}?)\s+on\s+(?P<y>{cont_alt})\b"),
            ]
            for src_tag, pat in reg_patterns:
                m = re.search(pat, task_text, re.IGNORECASE)
                if m:
                    y_cand = next(c for c in cont_cols if c.lower() == m.group("y").lower())
                    xs_matched = [c for c in _match_columns_in_fragment(m.group("xs"), cont_cols) if c != y_cand]
                    if xs_matched:
                        mode = "csv_regression"
                        target_col = y_cand
                        predictors = xs_matched
                        target_source = src_tag
                        predictors_source = src_tag
                        break

    # 2c. Refuse blind column-order fallback on ambiguous CSVs
    if mode is None:
        gaps.append(
            {
                "code": "CSV_AMBIGUOUS_TARGET_OR_ROLE",
                "severity": "BLOCKER",
                "detail": (
                    f"Cannot unambiguously determine dependent target, predictors, or grouping column for {csv_path.name} "
                    f"(continuous candidates={cont_cols}, categorical candidates={cat_cols}) from case.json, data dictionary, or task.md. "
                    "Refusing heuristic column-order guessing."
                ),
                "remediation": "Specify target_column and predictors (or group_column) explicitly in case.json, data dictionary, or task.md.",
            }
        )

    status = "ready" if not gaps else "partial_with_gaps"
    return {
        "schema_version": "sci-analysis-plan/v2",
        "status": status,
        "source_type": "csv",
        "source_file": csv_path.name,
        "metadata_sources": meta["sources_used"],
        "mode": mode or "stopped_ambiguous",
        "primary_table": csv_path.stem,
        "primary_table_role_source": "single_csv_input",
        "rows_raw": prof["n_rows"],
        "columns": cols,
        "target_col": target_col,
        "target_cols": [target_col] if target_col else [],
        "target_selection_source": target_source,
        "predictors": predictors,
        "predictors_selection_source": predictors_source,
        "group_col": group_col,
        "group_selection_source": group_source,
        "coded_or_excluded_columns": coded_cols,
        "observation_primary_key": {
            "columns": pk_cols,
            "source": pk_source,
            "dedup_authorized": dedup_authorized,
            "dedup_policy": dedup_policy,
            "exact_duplicate_full_rows": prof["exact_duplicate_full_rows"],
        },
        "column_profiles": prof["col_profiles"],
        "uncertainty": uncertainties,
        "gaps": gaps,
    }


def _plan_sqlite_analysis(
    db_path: Path,
    task_text: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Build explicit SQLite relational & statistical analysis_plan.json via PRAGMA introspection + metadata."""
    gaps: list[dict[str, str]] = []
    uncertainties: list[str] = []

    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        schema: dict[str, dict[str, Any]] = {}
        fk_edges: list[dict[str, Any]] = []

        for t in tables:
            tinfo = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
            fks = conn.execute(f'PRAGMA foreign_key_list("{t}")').fetchall()
            idxs = conn.execute(f'PRAGMA index_list("{t}")').fetchall()
            unique_indexes: list[list[str]] = []
            for idx_row in idxs:
                # (seq, name, unique, origin, partial)
                if idx_row[2] == 1:
                    idx_name = idx_row[1]
                    idx_cols = [r[2] for r in conn.execute(f'PRAGMA index_info("{idx_name}")').fetchall() if r[2]]
                    if idx_cols:
                        unique_indexes.append(idx_cols)
            pk_cols = [row[1] for row in sorted((r for r in tinfo if r[5] > 0), key=lambda r: r[5])]
            col_meta = [
                {
                    "name": r[1],
                    "type": r[2],
                    "notnull": bool(r[3]),
                    "pk_order": int(r[5]),
                    "is_numeric_declared": _is_sql_numeric_type(r[2]),
                }
                for r in tinfo
            ]
            cnt = int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
            schema[t] = {
                "rows": cnt,
                "cols": [c["name"] for c in col_meta],
                "col_meta": col_meta,
                "pk_cols": pk_cols,
                "unique_indexes": unique_indexes,
                "foreign_keys": [
                    {"from_table": t, "from_col": fk[3], "to_table": fk[2], "to_col": fk[4]}
                    for fk in fks
                ],
            }
            fk_edges.extend(schema[t]["foreign_keys"])

        # 1. Focus tables strictly from explicit metadata or task.md mentions (no blind multi-table guessing)
        explicit_tables: list[str] = []
        for k in ("primary_table", "dimension_table", "child_table"):
            if meta.get(k) and meta[k] in schema and meta[k] not in explicit_tables:
                explicit_tables.append(meta[k])
        mentioned_tables = [
            t for t in tables if re.search(rf"(?<![A-Za-z0-9_]){re.escape(t)}(?![A-Za-z0-9_])", task_text)
        ]
        if explicit_tables:
            focus_tables = list(dict.fromkeys(explicit_tables + mentioned_tables))
            focus_source = "case_json_or_dictionary"
        elif mentioned_tables:
            focus_tables = mentioned_tables
            focus_source = "task_md_explicit"
        elif len(tables) == 1:
            focus_tables = list(tables)
            focus_source = "single_table_in_db"
        else:
            focus_tables = []
            focus_source = "ambiguous"
            gaps.append(
                {
                    "code": "SQLITE_AMBIGUOUS_FOCUS_TABLES",
                    "severity": "BLOCKER",
                    "detail": (
                        f"Database {db_path.name} contains {len(tables)} tables ({tables}) and neither case.json, "
                        "data dictionary, nor task.md names the target tables."
                    ),
                    "remediation": "Specify primary_table (and optional dimension_table/child_table) in case.json or task.md.",
                }
            )

        # 2. Resolve Table Roles & Join Keys via explicit metadata or PRAGMA foreign_key_list graph (NO row-count/shared-col guessing!)
        primary_tbl = meta.get("primary_table") if meta.get("primary_table") in schema else None
        dim_tbl = meta.get("dimension_table") if meta.get("dimension_table") in schema else None
        dim_key = meta.get("dimension_key")
        dim_label = meta.get("dimension_label")
        child_tbl = meta.get("child_table") if meta.get("child_table") in schema else None
        child_key = meta.get("child_key")
        role_source = "case_json_or_dictionary" if primary_tbl else None

        focus_edges = [e for e in fk_edges if e["from_table"] in focus_tables and e["to_table"] in focus_tables]

        if not primary_tbl and focus_tables:
            if len(focus_tables) == 1:
                primary_tbl = focus_tables[0]
                role_source = "single_focus_table"
            elif not focus_edges:
                gaps.append(
                    {
                        "code": "SQLITE_UNDECLARED_JOIN_KEY",
                        "severity": "BLOCKER",
                        "detail": (
                            f"Focus tables {focus_tables} have zero PRAGMA foreign_key_list relationships and no "
                            "explicit join_keys in case.json/dictionary. Refusing heuristic row-count or first-shared-column guessing."
                        ),
                        "remediation": "Define SQLite FOREIGN KEY constraints or declare primary_table/dimension_table/child_table and join keys in case.json.",
                    }
                )
            else:
                out_map: dict[str, list[dict[str, Any]]] = {t: [] for t in focus_tables}
                in_map: dict[str, list[dict[str, Any]]] = {t: [] for t in focus_tables}
                for e in focus_edges:
                    out_map[e["from_table"]].append(e)
                    in_map[e["to_table"]].append(e)

                # Case A: Hub primary table has both outgoing FK (to dimension) and incoming FK (from 1:N child)
                hubs = [t for t in focus_tables if out_map[t] and in_map[t]]
                if len(hubs) == 1:
                    primary_tbl = hubs[0]
                    role_source = "pragma_foreign_key_graph"
                    if len(out_map[primary_tbl]) == 1:
                        e_dim = out_map[primary_tbl][0]
                        dim_tbl = e_dim["to_table"]
                        dim_key = e_dim["from_col"] if e_dim["from_col"] == e_dim["to_col"] else None
                        if dim_key is None:
                            dim_key = e_dim["to_col"]
                    elif len(out_map[primary_tbl]) > 1:
                        gaps.append(
                            {
                                "code": "SQLITE_AMBIGUOUS_DIMENSION_FK",
                                "severity": "BLOCKER",
                                "detail": f"Primary table {primary_tbl} has multiple outgoing FKs ({out_map[primary_tbl]}); case.json must specify dimension_table.",
                                "remediation": "Declare dimension_table and dimension_key in case.json.",
                            }
                        )
                    if len(in_map[primary_tbl]) == 1:
                        e_ch = in_map[primary_tbl][0]
                        child_tbl = e_ch["from_table"]
                        child_key = e_ch["from_col"]
                    elif len(in_map[primary_tbl]) > 1:
                        gaps.append(
                            {
                                "code": "SQLITE_AMBIGUOUS_CHILD_FK",
                                "severity": "BLOCKER",
                                "detail": f"Primary table {primary_tbl} has multiple incoming child FKs ({in_map[primary_tbl]}); case.json must specify child_table.",
                                "remediation": "Declare child_table and child_key in case.json.",
                            }
                        )
                elif len(focus_tables) == 2 and len(focus_edges) == 1:
                    e0 = focus_edges[0]
                    from_t, to_t = e0["from_table"], e0["to_table"]
                    # Distinguish fact->dimension (many-to-one lookup) vs child->primary via task phrasing & schema text labels
                    to_has_label = any(
                        not c["is_numeric_declared"] and c["pk_order"] == 0 and not _is_coded_or_id(c["name"])
                        for c in schema[to_t]["col_meta"]
                    )
                    from_has_real = any(
                        "REAL" in c["type"].upper() or "FLOA" in c["type"].upper() or "DOUB" in c["type"].upper()
                        for c in schema[from_t]["col_meta"]
                    )
                    if to_has_label and from_has_real:
                        primary_tbl = from_t
                        dim_tbl = to_t
                        dim_key = e0["to_col"]
                        role_source = "pragma_foreign_key_graph_fact_dim"
                    else:
                        gaps.append(
                            {
                                "code": "SQLITE_AMBIGUOUS_TABLE_ROLES",
                                "severity": "BLOCKER",
                                "detail": (
                                    f"Two-table FK edge {from_t}.{e0['from_col']} -> {to_t}.{e0['to_col']} is ambiguous "
                                    "between fact->dimension and child->primary without explicit case.json role declaration."
                                ),
                                "remediation": "Specify primary_table and dimension_table/child_table in case.json.",
                            }
                        )
                else:
                    gaps.append(
                        {
                            "code": "SQLITE_AMBIGUOUS_TABLE_ROLES",
                            "severity": "BLOCKER",
                            "detail": (
                                f"Cannot uniquely assign primary/dimension/child roles across {focus_tables} from FK edges "
                                f"{focus_edges} without explicit case.json specification."
                            ),
                            "remediation": "Declare primary_table, dimension_table, child_table, and join keys in case.json.",
                        }
                    )

        # Resolve FK column pairs precisely (handling same-name or different-name FK columns)
        dim_primary_fk_col = dim_key
        dim_ref_col = dim_key
        child_fk_col = child_key
        child_primary_ref_col = child_key
        for e in focus_edges:
            if primary_tbl and dim_tbl and e["from_table"] == primary_tbl and e["to_table"] == dim_tbl:
                dim_primary_fk_col = e["from_col"]
                dim_ref_col = e["to_col"]
                dim_key = dim_primary_fk_col
            if primary_tbl and child_tbl and e["from_table"] == child_tbl and e["to_table"] == primary_tbl:
                child_fk_col = e["from_col"]
                child_primary_ref_col = e["to_col"]
                child_key = child_fk_col

        # 3. Primary Table Observation Primary Key Verification
        primary_pk_cols: list[str] = []
        primary_pk_source = "none"
        if primary_tbl:
            if meta["primary_key"] and all(c in schema[primary_tbl]["cols"] for c in meta["primary_key"]):
                primary_pk_cols = list(meta["primary_key"])
                primary_pk_source = str(meta["primary_key_source"] or "case.json")
            elif schema[primary_tbl]["pk_cols"]:
                primary_pk_cols = list(schema[primary_tbl]["pk_cols"])
                primary_pk_source = "pragma_table_info_pk"
            elif schema[primary_tbl]["unique_indexes"]:
                primary_pk_cols = list(schema[primary_tbl]["unique_indexes"][0])
                primary_pk_source = "pragma_unique_index"
            else:
                gaps.append(
                    {
                        "code": "SQLITE_MISSING_PRIMARY_KEY",
                        "severity": "BLOCKER",
                        "detail": (
                            f"Primary table {primary_tbl!r} has no PRIMARY KEY in PRAGMA table_info, no UNIQUE index, "
                            "and no primary_key in case.json/dictionary; refusing unkeyed deduplication or join."
                        ),
                        "remediation": "Define a PRIMARY KEY on the primary table or declare observation primary_key in case.json.",
                    }
                )

        # 4. Dimension Label Column Resolution (strictly from schema metadata, never arbitrary col[0])
        dim_label_source = None
        if dim_tbl:
            if dim_label and dim_label in schema[dim_tbl]["cols"]:
                dim_label_source = "case_json_or_dictionary"
            else:
                dim_pk_set = set(schema[dim_tbl]["pk_cols"]) | ({dim_ref_col} if dim_ref_col else set())
                text_non_coded = [
                    c["name"]
                    for c in schema[dim_tbl]["col_meta"]
                    if c["name"] not in dim_pk_set
                    and not c["is_numeric_declared"]
                    and not _is_coded_or_id(c["name"])
                ]
                explicit_label_hits = [c for c in text_non_coded if "label" in c.lower() or "name" in c.lower()]
                if len(explicit_label_hits) == 1:
                    dim_label = explicit_label_hits[0]
                    dim_label_source = "pragma_table_info_label_column"
                elif len(text_non_coded) == 1:
                    dim_label = text_non_coded[0]
                    dim_label_source = "pragma_table_info_sole_text_attribute"
                else:
                    gaps.append(
                        {
                            "code": "SQLITE_AMBIGUOUS_DIM_LABEL",
                            "severity": "BLOCKER",
                            "detail": (
                                f"Dimension table {dim_tbl!r} has ambiguous label column candidates {text_non_coded}; "
                                "refusing arbitrary column fallback."
                            ),
                            "remediation": "Specify dimension_label explicitly in case.json or data dictionary.",
                        }
                    )

        # 5. Pre-Execution Join Key Uniqueness, Orphan Key, and Cardinality Verification
        join_checks: dict[str, Any] = {}
        if primary_tbl and dim_tbl and dim_primary_fk_col and dim_ref_col:
            dim_total = schema[dim_tbl]["rows"]
            dim_nulls = int(conn.execute(f'SELECT COUNT(*) FROM "{dim_tbl}" WHERE "{dim_ref_col}" IS NULL').fetchone()[0])
            dim_distinct = int(conn.execute(f'SELECT COUNT(DISTINCT "{dim_ref_col}") FROM "{dim_tbl}"').fetchone()[0])
            dim_unique = (dim_nulls == 0 and dim_total == dim_distinct)
            orphan_p_to_d = int(
                conn.execute(
                    f'SELECT COUNT(*) FROM "{primary_tbl}" p '
                    f'WHERE p."{dim_primary_fk_col}" IS NOT NULL '
                    f'AND NOT EXISTS (SELECT 1 FROM "{dim_tbl}" d WHERE d."{dim_ref_col}" = p."{dim_primary_fk_col}")'
                ).fetchone()[0]
            )
            empty_dim_rows = conn.execute(
                f'SELECT d."{dim_ref_col}" FROM "{dim_tbl}" d '
                f'WHERE NOT EXISTS (SELECT 1 FROM "{primary_tbl}" p WHERE p."{dim_primary_fk_col}" = d."{dim_ref_col}")'
            ).fetchall()
            join_checks["primary_to_dimension"] = {
                "from": f"{primary_tbl}.{dim_primary_fk_col}",
                "to": f"{dim_tbl}.{dim_ref_col}",
                "dimension_key_unique": dim_unique,
                "dimension_null_keys": dim_nulls,
                "dimension_rows": dim_total,
                "dimension_distinct_keys": dim_distinct,
                "orphan_primary_keys": orphan_p_to_d,
                "empty_dimension_keys_preserved": [r[0] for r in empty_dim_rows],
            }
            if not dim_unique:
                gaps.append(
                    {
                        "code": "SQLITE_JOIN_KEY_NOT_UNIQUE",
                        "severity": "BLOCKER",
                        "detail": (
                            f"Dimension join key {dim_tbl}.{dim_ref_col} is not unique/non-null "
                            f"(rows={dim_total}, distinct={dim_distinct}, nulls={dim_nulls}); JOIN would cause row fan-out."
                        ),
                        "remediation": "Deduplicate or fix primary key uniqueness on dimension table before joining.",
                    }
                )
            if orphan_p_to_d > 0:
                uncertainties.append(
                    f"{orphan_p_to_d} row(s) in {primary_tbl} reference {dim_primary_fk_col} missing from {dim_tbl}."
                )

        child_agg_specs: list[dict[str, Any]] = []
        if primary_tbl and child_tbl and child_fk_col and child_primary_ref_col:
            p_total = schema[primary_tbl]["rows"]
            p_nulls = int(conn.execute(f'SELECT COUNT(*) FROM "{primary_tbl}" WHERE "{child_primary_ref_col}" IS NULL').fetchone()[0])
            p_distinct = int(conn.execute(f'SELECT COUNT(DISTINCT "{child_primary_ref_col}") FROM "{primary_tbl}"').fetchone()[0])
            p_ref_unique = (p_nulls == 0 and p_total == p_distinct)
            c_total = schema[child_tbl]["rows"]
            c_distinct = int(conn.execute(f'SELECT COUNT(DISTINCT "{child_fk_col}") FROM "{child_tbl}"').fetchone()[0])
            orphan_c_to_p = int(
                conn.execute(
                    f'SELECT COUNT(*) FROM "{child_tbl}" c '
                    f'WHERE c."{child_fk_col}" IS NOT NULL '
                    f'AND NOT EXISTS (SELECT 1 FROM "{primary_tbl}" p WHERE p."{child_primary_ref_col}" = c."{child_fk_col}")'
                ).fetchone()[0]
            )
            max_mult_row = conn.execute(
                f'SELECT MAX(cnt) FROM (SELECT COUNT(*) AS cnt FROM "{child_tbl}" GROUP BY "{child_fk_col}")'
            ).fetchone()
            max_mult = int(max_mult_row[0] or 0) if max_mult_row else 0
            naive_join_rows = int(
                conn.execute(
                    f'SELECT COUNT(*) FROM "{primary_tbl}" p '
                    f'INNER JOIN "{child_tbl}" c ON p."{child_primary_ref_col}" = c."{child_fk_col}"'
                ).fetchone()[0]
            )
            join_checks["child_to_primary"] = {
                "from": f"{child_tbl}.{child_fk_col}",
                "to": f"{primary_tbl}.{child_primary_ref_col}",
                "primary_ref_key_unique": p_ref_unique,
                "child_rows": c_total,
                "child_distinct_keys": c_distinct,
                "max_child_rows_per_primary_key": max_mult,
                "orphan_child_keys": orphan_c_to_p,
                "naive_inner_join_rows": naive_join_rows,
                "safe_preagg_expected_rows": p_total,
                "requires_pre_aggregation": (max_mult > 1),
            }
            if not p_ref_unique:
                gaps.append(
                    {
                        "code": "SQLITE_JOIN_KEY_NOT_UNIQUE",
                        "severity": "BLOCKER",
                        "detail": (
                            f"Primary join target key {primary_tbl}.{child_primary_ref_col} is not unique/non-null "
                            f"(rows={p_total}, distinct={p_distinct}, nulls={p_nulls})."
                        ),
                        "remediation": "Ensure primary table join key is unique and non-null before joining child table.",
                    }
                )
            if orphan_c_to_p > 0:
                uncertainties.append(
                    f"{orphan_c_to_p} orphan row(s) in {child_tbl}.{child_fk_col} do not match {primary_tbl}.{child_primary_ref_col}."
                )

            # Dynamic discovery of child numeric summary columns (ZERO hardcoded column names!)
            child_excl = set(schema[child_tbl]["pk_cols"]) | {child_fk_col} | set(meta["do_not_average"])
            for cm in schema[child_tbl]["col_meta"]:
                cname = cm["name"]
                if cname in child_excl or _is_coded_or_id(cname) or not cm["is_numeric_declared"]:
                    continue
                vals = [
                    r[0]
                    for r in conn.execute(f'SELECT "{cname}" FROM "{child_tbl}" WHERE "{cname}" IS NOT NULL').fetchall()
                    if isinstance(r[0], (int, float)) and not isinstance(r[0], bool) and math.isfinite(float(r[0]))
                ]
                if not vals:
                    continue
                distinct_vals = set(float(v) for v in vals)
                is_binary = distinct_vals.issubset({0.0, 1.0}) and len(distinct_vals) >= 1
                child_agg_specs.append(
                    {
                        "column": cname,
                        "declared_type": cm["type"],
                        "kind": "binary_indicator" if is_binary else "continuous_numeric",
                        "output_metric": f"rate_{cname}" if is_binary else f"mean_{cname}",
                    }
                )

        # 6. Target Column Resolution on Primary Table (strictly typed numeric, never TEXT, never blind first col)
        all_schema_cols = {c for info in schema.values() for c in info["cols"]}
        do_not_avg = sorted(
            {c for c in meta["do_not_average"] if c in all_schema_cols}
            | ({dim_primary_fk_col} if dim_primary_fk_col else set())
        )
        target_cols: list[str] = []
        target_source = None
        if primary_tbl:
            p_fk_cols = {fk["from_col"] for fk in schema[primary_tbl]["foreign_keys"]}
            p_excl = set(primary_pk_cols) | p_fk_cols | set(do_not_avg)
            verified_numeric_cols: list[str] = []
            non_key_text_cols: list[str] = []
            for cm in schema[primary_tbl]["col_meta"]:
                cname = cm["name"]
                if cname in p_excl or _is_coded_or_id(cname):
                    continue
                if cm["is_numeric_declared"]:
                    sample_v = conn.execute(
                        f'SELECT "{cname}" FROM "{primary_tbl}" WHERE "{cname}" IS NOT NULL LIMIT 20'
                    ).fetchall()
                    if sample_v and all(isinstance(r[0], (int, float)) and not isinstance(r[0], bool) for r in sample_v):
                        verified_numeric_cols.append(cname)
                else:
                    non_key_text_cols.append(cname)

            meta_t = [c for c in meta["target_cols"] if c in verified_numeric_cols]
            task_t = [c for c in _match_columns_in_fragment(task_text, verified_numeric_cols)]
            if meta_t:
                target_cols = meta_t
                target_source = "case_json_or_dictionary"
            elif task_t:
                target_cols = task_t
                target_source = "task_md_explicit"
            elif verified_numeric_cols and not non_key_text_cols and task_text.strip():
                # Whole-table quantitative profile where all non-key columns are verified numeric measurements
                target_cols = list(verified_numeric_cols)
                target_source = "pragma_all_verified_numeric_measurements"
                if len(target_cols) > 1:
                    uncertainties.append(
                        f"task.md requested whole-table analysis of {primary_tbl} without singling out one analyte; "
                        f"all {len(target_cols)} verified numeric measurement columns ({', '.join(target_cols)}) are analyzed."
                    )
            else:
                gaps.append(
                    {
                        "code": "SQLITE_AMBIGUOUS_OR_MISSING_TARGET",
                        "severity": "BLOCKER",
                        "detail": (
                            f"Cannot unambiguously select numeric target column(s) on {primary_tbl!r} "
                            f"(verified_numeric={verified_numeric_cols}, non_key_text={non_key_text_cols}). "
                            "Refusing blind first-column heuristic."
                        ),
                        "remediation": "Specify target_columns explicitly in case.json, data dictionary, or task.md.",
                    }
                )
    finally:
        conn.close()

    status = "ready" if not gaps else "partial_with_gaps"
    return {
        "schema_version": "sci-analysis-plan/v2",
        "status": status,
        "source_type": "sqlite",
        "source_file": db_path.name,
        "metadata_sources": meta["sources_used"],
        "mode": "sqlite_multi" if status == "ready" else "stopped_ambiguous",
        "focus_tables": focus_tables,
        "focus_tables_source": focus_source,
        "primary_table": primary_tbl,
        "primary_table_role_source": role_source,
        "primary_expected_rows": schema[primary_tbl]["rows"] if (primary_tbl and primary_tbl in schema) else None,
        "dimension_table": dim_tbl,
        "dimension_key": dim_key,
        "dimension_ref_key": dim_ref_col,
        "dimension_label": dim_label,
        "dimension_label_source": dim_label_source,
        "child_table": child_tbl,
        "child_key": child_key,
        "child_primary_ref_key": child_primary_ref_col,
        "child_numeric_aggregations": child_agg_specs,
        "target_col": target_cols[0] if target_cols else None,
        "target_cols": target_cols,
        "target_selection_source": target_source,
        "coded_or_excluded_columns": do_not_avg,
        "observation_primary_key": {
            "columns": primary_pk_cols,
            "source": primary_pk_source,
            "dedup_authorized": bool(primary_pk_cols),
            "dedup_policy": "pk_bound_dedup" if primary_pk_cols else "blocked_missing_pk",
        },
        "join_checks": join_checks,
        "pragma_foreign_keys": fk_edges,
        "uncertainty": uncertainties,
        "gaps": gaps,
    }


def _generate_linear_analysis_script(
    data_path: Path,
    out_script: Path,
    plan: dict[str, Any],
) -> Path:
    """Emit a portable, zero-host-path, linear imperative research script strictly bound to analysis_plan.json."""
    mode = plan["mode"]
    pk_info = plan["observation_primary_key"]
    pk_cols = list(pk_info.get("columns") or [])
    pk_source = str(pk_info.get("source") or "none")
    dedup_policy = str(pk_info.get("dedup_policy") or "report_only_no_pk")
    dedup_authorized = bool(pk_info.get("dedup_authorized", False))

    if mode == "sqlite_multi":
        ptbl = plan["primary_table"] or ""
        dtbl = plan["dimension_table"] or ""
        dkey = plan["dimension_key"] or ""
        dref = plan["dimension_ref_key"] or dkey
        dlabel = plan["dimension_label"] or ""
        ctbl = plan["child_table"] or ""
        ckey = plan["child_key"] or ""
        cref = plan["child_primary_ref_key"] or ckey
        child_specs = list(plan.get("child_numeric_aggregations") or [])
        tcols = list(plan.get("target_cols") or [])
        lead_target = tcols[0] if tcols else ""
        other_targets = tcols[1:]
        no_avg = list(plan.get("coded_or_excluded_columns") or [])
        join_checks_plan = plan.get("join_checks") or {}

        code = f'''#!/usr/bin/env python3
"""Linear reproducible read-only SQLite scientific analysis script for {data_path.name}."""

import argparse, csv, hashlib, json, math, random, sqlite3, sys
from pathlib import Path

SEED = 42  # fixed deterministic seed adjacent to parameters
random.seed(SEED)

parser = argparse.ArgumentParser(description="Read-only SQLite scientific analysis")
parser.add_argument("--input", default="{data_path.name}", help="Path to input SQLite DB")
parser.add_argument("--lib-dir", default="", help="Optional directory containing stdlib_scientific_stats.py")
args = parser.parse_args()
if args.lib_dir and args.lib_dir not in sys.path:
    sys.path.insert(0, args.lib_dir)

from stdlib_scientific_stats import compare_groups_full, render_group_comparison_svg

INPUT_DB = Path(args.input)
OUT_JSON = Path("analysis_summary.json")
OUT_DEDUP_JSON = Path("dedup_audit.json")
OUT_PROFILE_CSV = Path("table_profile.csv")
OUT_JOINED_CSV = Path("safe_joined_samples.csv")
OUT_GROUP_CSV = Path("group_comparison.csv")
OUT_SVG = Path("scientific_figure.svg")

PRIMARY_TBL = {ptbl!r}
PRIMARY_PK_COLS = {pk_cols!r}
PRIMARY_PK_SOURCE = {pk_source!r}
DEDUP_POLICY = {dedup_policy!r}
DIM_TBL = {dtbl!r}
DIM_KEY = {dkey!r}
DIM_REF_KEY = {dref!r}
DIM_LABEL = {dlabel!r}
CHILD_TBL = {ctbl!r}
CHILD_KEY = {ckey!r}
CHILD_PRIMARY_REF_KEY = {cref!r}
CHILD_AGG_SPECS = {child_specs!r}
LEAD_ANALYTE = {lead_target!r}
ADDITIONAL_ANALYTES = {other_targets!r}
DO_NOT_AVERAGE = set({no_avg!r})
PLAN_JOIN_CHECKS = {join_checks_plan!r}

# Step 1. Verify read-only input digest and inspect PRAGMA schema
db_sha_before = hashlib.sha256(INPUT_DB.read_bytes()).hexdigest()
conn = sqlite3.connect(f"file:{{INPUT_DB.resolve().as_posix()}}?mode=ro", uri=True)
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
print(f"[checkpoint:tables] count={{len(tables)}} tables={{tables}}")

# Step 2. Profile every table using PRAGMA table_info without averaging coded keys or silent zero imputation
profiles = []
for tbl in tables:
    tinfo = conn.execute(f'PRAGMA table_info("{{tbl}}")').fetchall()
    pk_set = {{r[1] for r in tinfo if r[5] > 0}}
    num_type_set = {{r[1] for r in tinfo if any(tok in (r[2] or "").upper() for tok in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC"))}}
    cur = conn.execute(f'SELECT * FROM "{{tbl}}"')
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    null_cells = sum(1 for r in rows for v in r if v is None or str(v).strip() == "")
    numeric_means = {{}}
    for ci, cname in enumerate(cols):
        low = cname.lower()
        if cname in pk_set or cname in DO_NOT_AVERAGE or low.endswith(("_id", "_code", "code")) or cname not in num_type_set:
            continue
        vals = [float(r[ci]) for r in rows if isinstance(r[ci], (int, float)) and not isinstance(r[ci], bool) and math.isfinite(float(r[ci]))]
        if vals and len(vals) == sum(1 for r in rows if r[ci] is not None):
            numeric_means[cname] = sum(vals) / len(vals)
    profiles.append({{"table": tbl, "rows": len(rows), "columns": len(cols), "null_cells": null_cells, "pk_columns": sorted(pk_set), "numeric_means": numeric_means}})
    print(f"[checkpoint:table] {{tbl}}: rows={{len(rows)}} cols={{len(cols)}} nulls={{null_cells}}")

# Step 3. Primary-key-bound deduplication audit + schema-driven 1:N child pre-aggregation + LEFT JOIN
safe_join_audit = {{}}
dedup_audit = {{}}
group_comparison = None
secondary_comparisons = {{}}

if PRIMARY_TBL:
    p_cur = conn.execute(f'SELECT * FROM "{{PRIMARY_TBL}}"')
    p_cols = [d[0] for d in p_cur.description]
    p_rows = [dict(zip(p_cols, r)) for r in p_cur.fetchall()]

    seen_keys = {{}}
    dedup_rows = []
    removed_rows = []
    pk_conflicts = []
    for row_idx, r in enumerate(p_rows, start=1):
        pk_val = tuple(str(r.get(k, "")) for k in PRIMARY_PK_COLS)
        full_sig = tuple(r.get(c) for c in p_cols)
        if pk_val not in seen_keys:
            seen_keys[pk_val] = (row_idx, full_sig)
            dedup_rows.append(r)
        else:
            kept_idx, kept_sig = seen_keys[pk_val]
            if full_sig == kept_sig:
                removed_rows.append({{"primary_key": dict(zip(PRIMARY_PK_COLS, pk_val)), "source_row": row_idx, "kept_row": kept_idx, "reason": "exact_duplicate_on_primary_key"}})
            else:
                pk_conflicts.append({{"primary_key": dict(zip(PRIMARY_PK_COLS, pk_val)), "source_row": row_idx, "kept_row": kept_idx, "reason": "conflicting_payload_same_primary_key"}})
                dedup_rows.append(r)

    exact_dups_removed = len(removed_rows)
    dedup_audit = {{
        "table": PRIMARY_TBL,
        "observation_primary_key": PRIMARY_PK_COLS,
        "primary_key_source": PRIMARY_PK_SOURCE,
        "dedup_policy": DEDUP_POLICY,
        "dedup_authorized": True,
        "raw_row_count": len(p_rows),
        "retained_row_count": len(dedup_rows),
        "removed_row_count": exact_dups_removed,
        "exact_duplicate_row_count": exact_dups_removed,
        "pk_conflict_row_count": len(pk_conflicts),
        "retained_key_sample": [dict(zip(PRIMARY_PK_COLS, k)) for k in list(seen_keys.keys())[:10]],
        "removed_rows": removed_rows,
        "pk_conflicts": pk_conflicts,
    }}
    OUT_DEDUP_JSON.write_text(json.dumps(dedup_audit, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
    print(f"[checkpoint:primary_dedup] table={{PRIMARY_TBL}} pk={{PRIMARY_PK_COLS}} raw={{len(p_rows)}} clean={{len(dedup_rows)}} dups={{exact_dups_removed}} conflicts={{len(pk_conflicts)}}")
    assert len(pk_conflicts) == 0, f"Primary-key payload conflicts detected: {{len(pk_conflicts)}}"

    # Pre-aggregate 1:N child table dynamically from PRAGMA-discovered numeric columns (zero hardcoded column names)
    child_agg = {{}}
    child_raw_rows = 0
    if CHILD_TBL and CHILD_KEY:
        c_cur = conn.execute(f'SELECT * FROM "{{CHILD_TBL}}"')
        c_cols = [d[0] for d in c_cur.description]
        c_rows = [dict(zip(c_cols, r)) for r in c_cur.fetchall()]
        child_raw_rows = len(c_rows)
        grouped_c = {{}}
        for cr in c_rows:
            k = str(cr.get(CHILD_KEY, ""))
            grouped_c.setdefault(k, []).append(cr)
        for k, items in grouped_c.items():
            rec_agg = {{"child_n": len(items)}}
            for spec in CHILD_AGG_SPECS:
                scol = spec["column"]
                out_m = spec["output_metric"]
                vals = [float(x[scol]) for x in items if scol in x and isinstance(x[scol], (int, float)) and not isinstance(x[scol], bool) and math.isfinite(float(x[scol]))]
                rec_agg[out_m] = (sum(vals) / len(vals)) if vals else None
                rec_agg[f"n_valid_{{scol}}"] = len(vals)
            child_agg[k] = rec_agg
        print(f"[checkpoint:child_preagg] table={{CHILD_TBL}} raw_rows={{child_raw_rows}} distinct_keys={{len(child_agg)}} metrics={{[s['output_metric'] for s in CHILD_AGG_SPECS]}}")

    # Dimension lookup preserving empty n=0 dimension groups via explicit LEFT JOIN semantics
    dim_order = []
    dim_map = {{}}
    if DIM_TBL and DIM_REF_KEY and DIM_LABEL:
        d_cur = conn.execute(f'SELECT * FROM "{{DIM_TBL}}" ORDER BY "{{DIM_REF_KEY}}"')
        d_cols = [d[0] for d in d_cur.description]
        for r in d_cur.fetchall():
            rec = dict(zip(d_cols, r))
            lbl = str(rec[DIM_LABEL])
            dim_map[rec[DIM_REF_KEY]] = lbl
            dim_order.append(lbl)
        print(f"[checkpoint:dimension] table={{DIM_TBL}} groups={{dim_order}}")

    # Join 1:1 with pre-aggregated child stats (zero row inflation)
    default_child_agg = {{"child_n": 0}}
    for spec in CHILD_AGG_SPECS:
        default_child_agg[spec["output_metric"]] = None
        default_child_agg[f"n_valid_{{spec['column']}}"] = 0

    joined_rows = []
    for pr in dedup_rows:
        out_r = dict(pr)
        if DIM_KEY and DIM_KEY in pr:
            out_r[DIM_LABEL] = dim_map.get(pr[DIM_KEY], str(pr[DIM_KEY]))
        if CHILD_PRIMARY_REF_KEY and CHILD_PRIMARY_REF_KEY in pr and CHILD_TBL:
            out_r.update(child_agg.get(str(pr[CHILD_PRIMARY_REF_KEY]), dict(default_child_agg)))
        joined_rows.append(out_r)

    assert len(joined_rows) == len(dedup_rows), f"JOIN row inflation detected: {{len(joined_rows)}} != {{len(dedup_rows)}}"
    safe_join_audit = {{
        "primary_table": PRIMARY_TBL,
        "primary_pk_columns": PRIMARY_PK_COLS,
        "primary_rows": len(dedup_rows),
        "exact_duplicates_removed": exact_dups_removed,
        "child_table": CHILD_TBL or None,
        "child_join_key": CHILD_KEY or None,
        "child_raw_rows": child_raw_rows,
        "child_distinct_keys": len(child_agg),
        "child_aggregated_metrics": [s["output_metric"] for s in CHILD_AGG_SPECS],
        "joined_sample_rows": len(joined_rows),
        "row_inflation_prevented": (len(joined_rows) == len(dedup_rows)),
        "dimension_table": DIM_TBL or None,
        "dimension_join_key": DIM_KEY or None,
        "dimension_groups_total": len(dim_order),
        "coded_columns_excluded_from_averaging": sorted(set(list(DO_NOT_AVERAGE) + ([DIM_KEY] if DIM_KEY else []))),
        "pre_execution_join_checks": PLAN_JOIN_CHECKS,
    }}
    print(f"[checkpoint:safe_join] joined_rows={{len(joined_rows)}} (no 1:N inflation from {{child_raw_rows}} child rows)")

    if joined_rows:
        j_fields = list(joined_rows[0].keys())
        with OUT_JOINED_CSV.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=j_fields)
            w.writeheader()
            w.writerows(joined_rows)

    # Step 4. Group comparison across dimension labels (preserving empty n=0 groups) for ALL verified numeric analytes
    if DIM_LABEL and LEAD_ANALYTE:
        groups_map = {{g: [] for g in dim_order}}
        for jr in joined_rows:
            g = str(jr.get(DIM_LABEL, ""))
            v = jr.get(LEAD_ANALYTE)
            if v is not None and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                groups_map.setdefault(g, []).append(float(v))
        group_comparison = compare_groups_full(
            groups_map,
            group_col=DIM_LABEL,
            target_col=LEAD_ANALYTE,
            ordered_groups=dim_order if dim_order else None,
        )
        group_comparison["source_table"] = PRIMARY_TBL
        group_comparison["dimension_table"] = DIM_TBL
        group_comparison["dimension_key"] = DIM_KEY
        group_comparison["dimension_label"] = DIM_LABEL

        render_group_comparison_svg(
            OUT_SVG,
            groups_map,
            group_comparison,
            seed=SEED,
            title=f"{{PRIMARY_TBL}}: {{LEAD_ANALYTE}} by {{DIM_LABEL}} (safe 1:N join, n={{len(joined_rows)}})",
        )
        with OUT_GROUP_CSV.open("w", encoding="utf-8", newline="") as fh:
            g_fields = ["group", "n", "status", "mean", "sd", "se", "ci95_low", "ci95_high", "median", "iqr", "min", "max"]
            w = csv.DictWriter(fh, fieldnames=g_fields)
            w.writeheader()
            w.writerows(group_comparison["group_summaries"])

        for sec_t in ADDITIONAL_ANALYTES:
            sec_map = {{g: [] for g in dim_order}}
            for jr in joined_rows:
                g = str(jr.get(DIM_LABEL, ""))
                v = jr.get(sec_t)
                if v is not None and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                    sec_map.setdefault(g, []).append(float(v))
            sec_res = compare_groups_full(sec_map, group_col=DIM_LABEL, target_col=sec_t, ordered_groups=dim_order if dim_order else None)
            secondary_comparisons[sec_t] = {{
                "n_total": sec_res["n_total"],
                "empty_groups": sec_res["empty_groups"],
                "omnibus_tests": sec_res["omnibus_tests"],
                "group_summaries": sec_res["group_summaries"],
            }}

conn.close()

# Step 5. Verify read-only immutability and persist artifacts
db_sha_after = hashlib.sha256(INPUT_DB.read_bytes()).hexdigest()
assert db_sha_before == db_sha_after, "Source SQLite hash changed!"
with OUT_PROFILE_CSV.open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["table", "rows", "columns", "null_cells"])
    w.writeheader()
    for p in profiles:
        w.writerow({{k: p[k] for k in ["table", "rows", "columns", "null_cells"]}})

diag_warnings = list((group_comparison or {{}}).get("diagnostics_warnings") or [])
summary = {{
    "ok": True,
    "seed": SEED,
    "analysis_mode": "sqlite_safe_join_group_comparison",
    "source_db": INPUT_DB.name,
    "db_sha256": db_sha_before,
    "immutability_verified": True,
    "tables_profiled": len(profiles),
    "profiles": profiles,
    "dedup_audit": dedup_audit,
    "safe_join_audit": safe_join_audit,
    "group_comparison": group_comparison,
    "secondary_analytes": secondary_comparisons,
    "diagnostics_warnings": diag_warnings,
}}
OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
print(f"[checkpoint:done] wrote {{OUT_JSON.name}}, {{OUT_DEDUP_JSON.name}}, {{OUT_PROFILE_CSV.name}}, {{OUT_JOINED_CSV.name}}, {{OUT_GROUP_CSV.name}}, {{OUT_SVG.name}}")
'''
    elif mode == "csv_regression":
        target_col = plan["target_col"]
        pred_cols = plan["predictors"]
        code = f'''#!/usr/bin/env python3
"""Linear reproducible CSV multivariable OLS & diagnostics script for {data_path.name}."""

import argparse, csv, hashlib, json, math, random, sys
from pathlib import Path

SEED = 42  # fixed deterministic seed adjacent to parameters
random.seed(SEED)

parser = argparse.ArgumentParser(description="Multivariable OLS scientific CSV analysis")
parser.add_argument("--input", default="{data_path.name}", help="Path to input CSV")
parser.add_argument("--lib-dir", default="", help="Optional directory containing stdlib_scientific_stats.py")
args = parser.parse_args()
if args.lib_dir and args.lib_dir not in sys.path:
    sys.path.insert(0, args.lib_dir)

from stdlib_scientific_stats import fit_ols_full, render_regression_svg

INPUT_CSV = Path(args.input)
TARGET_COL = {target_col!r}
PREDICTORS = {pred_cols!r}
PRIMARY_PK_COLS = {pk_cols!r}
PRIMARY_PK_SOURCE = {pk_source!r}
DEDUP_POLICY = {dedup_policy!r}
DEDUP_AUTHORIZED = {dedup_authorized!r}
OUT_JSON = Path("analysis_summary.json")
OUT_DEDUP_JSON = Path("dedup_audit.json")
OUT_COL_CSV = Path("column_summary.csv")
OUT_COEF_CSV = Path("regression_coefficients.csv")
OUT_SVG = Path("scientific_figure.svg")
MISSING = {{"", "na", "n/a", "nan", "null", "none", "<lod", "nd", "-999"}}

# Step 1. Stream raw CSV strictly as strings to preserve leading-zero IDs and embedded newlines
input_sha = hashlib.sha256(INPUT_CSV.read_bytes()).hexdigest()
with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
    reader = csv.DictReader(fh)
    cols = [c.strip() for c in (reader.fieldnames or [])]
    raw_rows = [{{k.strip(): ("" if v is None else v.strip()) for k, v in r.items() if k is not None}} for r in reader]
print(f"[checkpoint:load] rows={{len(raw_rows)}} cols={{len(cols)}} head={{raw_rows[:1]}}")

# Step 2. Primary-key-bound deduplication audit (never silently drop rows when unkeyed)
seen_keys = {{}}
clean_rows = []
removed_rows = []
pk_conflicts = []
exact_dup_detected = 0

if DEDUP_POLICY == "pk_bound_dedup" and PRIMARY_PK_COLS:
    for row_idx, r in enumerate(raw_rows, start=2):
        pk_val = tuple(r.get(k, "") for k in PRIMARY_PK_COLS)
        full_sig = tuple(r.get(c, "") for c in cols)
        if pk_val not in seen_keys:
            seen_keys[pk_val] = (row_idx, full_sig)
            clean_rows.append(r)
        else:
            kept_idx, kept_sig = seen_keys[pk_val]
            if full_sig == kept_sig:
                exact_dup_detected += 1
                removed_rows.append({{"primary_key": dict(zip(PRIMARY_PK_COLS, pk_val)), "source_line": row_idx, "kept_line": kept_idx, "reason": "exact_duplicate_on_primary_key"}})
            else:
                pk_conflicts.append({{"primary_key": dict(zip(PRIMARY_PK_COLS, pk_val)), "source_line": row_idx, "kept_line": kept_idx, "reason": "conflicting_payload_same_primary_key"}})
                clean_rows.append(r)
else:
    seen_full = set()
    for r in raw_rows:
        sig = tuple(r.get(c, "") for c in cols)
        if sig in seen_full:
            exact_dup_detected += 1
        else:
            seen_full.add(sig)
    clean_rows = list(raw_rows)

exact_dups_removed = len(removed_rows)
dedup_audit = {{
    "observation_primary_key": PRIMARY_PK_COLS,
    "primary_key_source": PRIMARY_PK_SOURCE,
    "dedup_policy": DEDUP_POLICY,
    "dedup_authorized": DEDUP_AUTHORIZED,
    "raw_row_count": len(raw_rows),
    "retained_row_count": len(clean_rows),
    "removed_row_count": exact_dups_removed,
    "exact_duplicate_row_count": exact_dup_detected,
    "pk_conflict_row_count": len(pk_conflicts),
    "removed_rows": removed_rows,
    "pk_conflicts": pk_conflicts,
    "note": "No observation primary key declared; silent deduplication disabled." if DEDUP_POLICY == "report_only_no_pk" else "Primary-key-bound deduplication applied.",
}}
OUT_DEDUP_JSON.write_text(json.dumps(dedup_audit, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
print(f"[checkpoint:dedup] policy={{DEDUP_POLICY}} raw={{len(raw_rows)}} retained={{len(clean_rows)}} exact_dup_detected={{exact_dup_detected}} removed={{exact_dups_removed}}")

# Step 3. Column-level summary keeping true zeros distinct from missing tokens
col_stats = []
for c in cols:
    vals = [r.get(c, "") for r in clean_rows]
    miss = sum(1 for v in vals if v.lower() in MISSING)
    non_miss = [v for v in vals if v.lower() not in MISSING]
    has_lz = any(len(v) > 1 and v.startswith("0") and v.isdigit() for v in non_miss)
    nums = []
    if not has_lz:
        for v in non_miss:
            try:
                fv = float(v)
                if math.isfinite(fv):
                    nums.append(fv)
            except ValueError:
                pass
    is_cont = (len(nums) == len(non_miss) and len(nums) > 0 and not c.lower().endswith(("_id", "_code", "code")))
    mean_v = (sum(nums) / len(nums)) if is_cont else None
    sd_v = math.sqrt(sum((x - mean_v) ** 2 for x in nums) / max(len(nums) - 1, 1)) if (is_cont and len(nums) > 1 and mean_v is not None) else None
    zero_n = sum(1 for x in nums if x == 0.0) if is_cont else 0
    col_stats.append({{"column": c, "non_missing": len(non_miss), "missing": miss, "true_zero_count": zero_n, "leading_zero_id": has_lz, "mean": mean_v, "sd": sd_v}})

with OUT_COL_CSV.open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["column", "non_missing", "missing", "true_zero_count", "leading_zero_id", "mean", "sd"])
    w.writeheader()
    w.writerows(col_stats)

# Step 4. Multivariable OLS regression (beta, classical SE, HC3 robust SE, t, p, R^2, F, VIF, JB, BP)
num_rows = []
for r in clean_rows:
    rec = {{}}
    for c in [TARGET_COL] + PREDICTORS:
        raw_v = r.get(c, "")
        if raw_v.lower() in MISSING:
            rec[c] = None
        else:
            try:
                rec[c] = float(raw_v)
            except ValueError:
                rec[c] = None
    num_rows.append(rec)

ols_res = fit_ols_full(num_rows, target=TARGET_COL, predictors=PREDICTORS)
print(
    f"[checkpoint:ols] n={{ols_res['n_complete']}} R2={{ols_res['r_squared']:.6f}} "
    f"adj_R2={{ols_res['adj_r_squared']:.6f}} F={{ols_res['f_stat']:.3f}} p={{ols_res['f_pvalue']:.4g}}"
)

# Step 5. Render publication-style 2-panel SVG and export CSV/JSON
render_regression_svg(OUT_SVG, ols_res)
coef_fields = ["term", "beta", "se", "t_stat", "p_value", "ci95_low", "ci95_high", "hc3_se", "hc3_t_stat", "hc3_p_value", "std_beta", "vif"]
with OUT_COEF_CSV.open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=coef_fields)
    w.writeheader()
    for c_row in ols_res["coefficients"]:
        w.writerow({{k: c_row.get(k) for k in coef_fields}})

ols_export = {{k: v for k, v in ols_res.items() if k != "_plot_data"}}
summary = {{
    "ok": True,
    "seed": SEED,
    "analysis_mode": "multivariable_ols_regression",
    "source_file": INPUT_CSV.name,
    "input_sha256": input_sha,
    "rows_raw": len(raw_rows),
    "rows_dedup": len(clean_rows),
    "exact_duplicates_removed": exact_dups_removed,
    "dedup_audit": dedup_audit,
    "column_stats": col_stats,
    "regression_ols": ols_export,
    "diagnostics_warnings": ols_export["diagnostics_warnings"],
}}
OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
print(f"[checkpoint:done] wrote {{OUT_JSON.name}}, {{OUT_DEDUP_JSON.name}}, {{OUT_COL_CSV.name}}, {{OUT_COEF_CSV.name}}, {{OUT_SVG.name}}")
'''
    elif mode == "csv_group":
        target_col = plan["target_col"]
        group_col = plan["group_col"]
        code = f'''#!/usr/bin/env python3
"""Linear reproducible CSV group comparison & assumption-checked inference script for {data_path.name}."""

import argparse, csv, hashlib, json, math, random, sys
from pathlib import Path

SEED = 42  # fixed deterministic seed adjacent to parameters
random.seed(SEED)

parser = argparse.ArgumentParser(description="Group comparison scientific CSV analysis")
parser.add_argument("--input", default="{data_path.name}", help="Path to input CSV")
parser.add_argument("--lib-dir", default="", help="Optional directory containing stdlib_scientific_stats.py")
args = parser.parse_args()
if args.lib_dir and args.lib_dir not in sys.path:
    sys.path.insert(0, args.lib_dir)

from stdlib_scientific_stats import compare_groups_full, render_group_comparison_svg

INPUT_CSV = Path(args.input)
GROUP_COL = {group_col!r}
TARGET_COL = {target_col!r}
PRIMARY_PK_COLS = {pk_cols!r}
PRIMARY_PK_SOURCE = {pk_source!r}
DEDUP_POLICY = {dedup_policy!r}
DEDUP_AUTHORIZED = {dedup_authorized!r}
OUT_JSON = Path("analysis_summary.json")
OUT_DEDUP_JSON = Path("dedup_audit.json")
OUT_COL_CSV = Path("column_summary.csv")
OUT_GROUP_CSV = Path("group_comparison.csv")
OUT_PAIR_CSV = Path("pairwise_comparisons.csv")
OUT_SVG = Path("scientific_figure.svg")
MISSING = {{"", "na", "n/a", "nan", "null", "none", "<lod", "nd", "-999"}}

# Step 1. Stream raw CSV strictly as strings to preserve leading-zero IDs
input_sha = hashlib.sha256(INPUT_CSV.read_bytes()).hexdigest()
with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
    reader = csv.DictReader(fh)
    cols = [c.strip() for c in (reader.fieldnames or [])]
    raw_rows = [{{k.strip(): ("" if v is None else v.strip()) for k, v in r.items() if k is not None}} for r in reader]
print(f"[checkpoint:load] rows={{len(raw_rows)}} cols={{len(cols)}} head={{raw_rows[:1]}}")

# Step 2. Primary-key-bound deduplication audit (never silently drop rows when unkeyed)
seen_keys = {{}}
clean_rows = []
removed_rows = []
pk_conflicts = []
exact_dup_detected = 0

if DEDUP_POLICY == "pk_bound_dedup" and PRIMARY_PK_COLS:
    for row_idx, r in enumerate(raw_rows, start=2):
        pk_val = tuple(r.get(k, "") for k in PRIMARY_PK_COLS)
        full_sig = tuple(r.get(c, "") for c in cols)
        if pk_val not in seen_keys:
            seen_keys[pk_val] = (row_idx, full_sig)
            clean_rows.append(r)
        else:
            kept_idx, kept_sig = seen_keys[pk_val]
            if full_sig == kept_sig:
                exact_dup_detected += 1
                removed_rows.append({{"primary_key": dict(zip(PRIMARY_PK_COLS, pk_val)), "source_line": row_idx, "kept_line": kept_idx, "reason": "exact_duplicate_on_primary_key"}})
            else:
                pk_conflicts.append({{"primary_key": dict(zip(PRIMARY_PK_COLS, pk_val)), "source_line": row_idx, "kept_line": kept_idx, "reason": "conflicting_payload_same_primary_key"}})
                clean_rows.append(r)
else:
    seen_full = set()
    for r in raw_rows:
        sig = tuple(r.get(c, "") for c in cols)
        if sig in seen_full:
            exact_dup_detected += 1
        else:
            seen_full.add(sig)
    clean_rows = list(raw_rows)

exact_dups_removed = len(removed_rows)
dedup_audit = {{
    "observation_primary_key": PRIMARY_PK_COLS,
    "primary_key_source": PRIMARY_PK_SOURCE,
    "dedup_policy": DEDUP_POLICY,
    "dedup_authorized": DEDUP_AUTHORIZED,
    "raw_row_count": len(raw_rows),
    "retained_row_count": len(clean_rows),
    "removed_row_count": exact_dups_removed,
    "exact_duplicate_row_count": exact_dup_detected,
    "pk_conflict_row_count": len(pk_conflicts),
    "retained_key_sample": [dict(zip(PRIMARY_PK_COLS, k)) for k in list(seen_keys.keys())[:10]] if PRIMARY_PK_COLS else [],
    "removed_rows": removed_rows,
    "pk_conflicts": pk_conflicts,
    "note": "No observation primary key declared; silent deduplication disabled." if DEDUP_POLICY == "report_only_no_pk" else "Primary-key-bound deduplication applied.",
}}
OUT_DEDUP_JSON.write_text(json.dumps(dedup_audit, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
print(f"[checkpoint:dedup] policy={{DEDUP_POLICY}} pk={{PRIMARY_PK_COLS}} raw={{len(raw_rows)}} retained={{len(clean_rows)}} removed={{exact_dups_removed}}")

# Step 3. Column-level summary preserving leading-zero IDs and NA vs zero separation
col_stats = []
for c in cols:
    vals = [r.get(c, "") for r in clean_rows]
    miss = sum(1 for v in vals if v.lower() in MISSING)
    non_miss = [v for v in vals if v.lower() not in MISSING]
    has_lz = any(len(v) > 1 and v.startswith("0") and v.isdigit() for v in non_miss)
    nums = []
    if not has_lz:
        for v in non_miss:
            try:
                fv = float(v)
                if math.isfinite(fv):
                    nums.append(fv)
            except ValueError:
                pass
    is_cont = (len(nums) == len(non_miss) and len(nums) > 0 and not c.lower().endswith(("_id", "_code", "code")))
    mean_v = (sum(nums) / len(nums)) if is_cont else None
    sd_v = math.sqrt(sum((x - mean_v) ** 2 for x in nums) / max(len(nums) - 1, 1)) if (is_cont and len(nums) > 1 and mean_v is not None) else None
    col_stats.append({{"column": c, "non_missing": len(non_miss), "missing": miss, "leading_zero_id": has_lz, "mean": mean_v, "sd": sd_v}})

with OUT_COL_CSV.open("w", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["column", "non_missing", "missing", "leading_zero_id", "mean", "sd"])
    w.writeheader()
    w.writerows(col_stats)

# Step 4. Group comparison (Brown-Forsythe, Classical ANOVA, Welch ANOVA, Kruskal-Wallis, Holm + Hedges' g)
groups_map = {{}}
for r in clean_rows:
    g = r.get(GROUP_COL, "")
    t_raw = r.get(TARGET_COL, "")
    if not g or t_raw.lower() in MISSING:
        continue
    try:
        fv = float(t_raw)
        if math.isfinite(fv):
            groups_map.setdefault(g, []).append(fv)
    except ValueError:
        pass

comp_res = compare_groups_full(groups_map, group_col=GROUP_COL, target_col=TARGET_COL)
omn = comp_res["omnibus_tests"]
print(
    f"[checkpoint:group_test] k={{comp_res['k_non_empty_groups']}} n={{comp_res['n_total']}} "
    f"welch_F={{omn['welch_anova']['f_stat']:.3f}} p={{omn['welch_anova']['p_value']:.4g}} "
    f"KW_H={{omn['kruskal_wallis']['h_stat']:.3f}} eta2={{omn['classical_anova']['eta_squared']:.6f}}"
)

# Step 5. Render publication-style jitter + mean +/- 95% CI SVG figure and persist artifacts
render_group_comparison_svg(OUT_SVG, groups_map, comp_res, seed=SEED)
with OUT_GROUP_CSV.open("w", encoding="utf-8", newline="") as fh:
    g_fields = ["group", "n", "status", "mean", "sd", "se", "ci95_low", "ci95_high", "median", "iqr", "min", "max"]
    w = csv.DictWriter(fh, fieldnames=g_fields)
    w.writeheader()
    w.writerows(comp_res["group_summaries"])

with OUT_PAIR_CSV.open("w", encoding="utf-8", newline="") as fh:
    p_fields = ["group_a", "group_b", "mean_diff", "welch_t", "welch_df", "p_raw", "p_holm", "hedges_g", "hedges_g_ci95_low", "hedges_g_ci95_high"]
    w = csv.DictWriter(fh, fieldnames=p_fields)
    w.writeheader()
    w.writerows(comp_res["pairwise_comparisons"])

summary = {{
    "ok": True,
    "seed": SEED,
    "analysis_mode": "group_comparison",
    "source_file": INPUT_CSV.name,
    "input_sha256": input_sha,
    "rows_raw": len(raw_rows),
    "rows_dedup": len(clean_rows),
    "exact_duplicates_removed": exact_dups_removed,
    "dedup_audit": dedup_audit,
    "column_stats": col_stats,
    "group_comparison": comp_res,
    "diagnostics_warnings": comp_res["diagnostics_warnings"],
}}
OUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
print(f"[checkpoint:done] wrote {{OUT_JSON.name}}, {{OUT_DEDUP_JSON.name}}, {{OUT_COL_CSV.name}}, {{OUT_GROUP_CSV.name}}, {{OUT_PAIR_CSV.name}}, {{OUT_SVG.name}}")
'''
    else:
        raise ValueError(f"Unsupported ready plan mode: {mode!r}")

    out_script.parent.mkdir(parents=True, exist_ok=True)
    out_script.write_text(code, encoding="utf-8")
    return out_script


def run_auto_code_workflow(
    input_dir: Path,
    out_dir: Path,
    *,
    stem: str = "exec_verification",
    script_override: Path | None = None,
    fast_path: bool = False,
) -> dict[str, Any]:
    t0 = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "_exec_work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    disc = discover_input_dir(input_dir)
    case_json_p = (input_dir / "case.json") if (input_dir / "case.json").is_file() else None
    task_md_p = (input_dir / "task.md") if (input_dir / "task.md").is_file() else None
    task_text = task_md_p.read_text(encoding="utf-8-sig") if task_md_p else ""

    target_script = script_override
    generated = False
    plan: dict[str, Any] = {}
    primary_data = Path(disc["primary_table_candidate"]) if disc.get("primary_table_candidate") else None
    is_csv = bool(primary_data and primary_data.suffix.lower() == ".csv")
    is_db = bool(primary_data and primary_data.suffix.lower() in {".sqlite", ".sqlite3", ".db"})
    script_args: list[str] = []
    arts: list[str] = []

    if target_script is None:
        py_scripts = [s for s in (disc.get("scripts") or []) if str(s.get("rel", "")).endswith(".py")]
        if py_scripts:
            target_script = Path(py_scripts[0]["abs"])
        elif primary_data and primary_data.is_file():
            meta = _load_case_and_dictionary_metadata(input_dir, disc, task_text)
            if is_db:
                plan = _plan_sqlite_analysis(primary_data, task_text, meta)
            elif is_csv:
                plan = _plan_csv_analysis(primary_data, task_text, meta)
            else:
                plan = {
                    "schema_version": "sci-analysis-plan/v2",
                    "status": "partial_with_gaps",
                    "source_file": primary_data.name,
                    "mode": "stopped_unsupported",
                    "uncertainty": [],
                    "gaps": [
                        {
                            "code": "UNSUPPORTED_INPUT_FORMAT",
                            "severity": "BLOCKER",
                            "detail": f"Unsupported tabular/database extension: {primary_data.suffix}",
                            "remediation": "Provide a .csv or .sqlite/.db file or explicit --script.",
                        }
                    ],
                }

            plan_p = out_dir / "analysis_plan.json"
            plan_p.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            arts.append(plan_p.name)

            # STOP immediately on semantic ambiguity, missing primary key, or invalid join key (ZERO blind guessing!)
            if plan.get("status") != "ready":
                # Emit report-only dedup_audit.json if CSV unkeyed duplicates triggered stop
                if is_csv and plan.get("observation_primary_key"):
                    da_stop = {
                        "observation_primary_key": plan["observation_primary_key"].get("columns") or [],
                        "primary_key_source": plan["observation_primary_key"].get("source"),
                        "dedup_policy": plan["observation_primary_key"].get("dedup_policy"),
                        "dedup_authorized": False,
                        "raw_row_count": plan.get("rows_raw"),
                        "retained_row_count": plan.get("rows_raw"),
                        "removed_row_count": 0,
                        "exact_duplicate_row_count": plan["observation_primary_key"].get("exact_duplicate_full_rows", 0),
                        "removed_rows": [],
                        "note": "Execution halted prior to analysis due to blocking ambiguity or unauthorized unkeyed duplicates; zero rows dropped.",
                    }
                    da_p = out_dir / "dedup_audit.json"
                    da_p.write_text(json.dumps(da_stop, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    arts.append(da_p.name)

                stop_payload = {
                    "ok": False,
                    "status": "partial_with_gaps",
                    "stopped_before_execution": True,
                    "analysis_plan": plan,
                    "gaps": plan.get("gaps") or [],
                    "uncertainty": plan.get("uncertainty") or [],
                }
                stop_payload_p = out_dir / f"{stem}_stop_payload.json"
                stop_payload_p.write_text(json.dumps(stop_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                seal = seal_result_contract(
                    case_json_path=case_json_p,
                    result_json_path=out_dir / "result.json",
                    task_id="challenge-9ec929fe",
                    status="partial_with_gaps",
                    payload_path=stop_payload_p,
                    artifacts=arts,
                    sources=[primary_data.name],
                    uncertainty=plan.get("uncertainty") or [],
                    gaps=[f"[{g['code']}] {g['detail']}" for g in (plan.get("gaps") or [])],
                    fresh=True,
                )
                if stop_payload_p.exists():
                    stop_payload_p.unlink()
                elapsed = round(time.monotonic() - t0, 4)
                return {
                    "ok": False,
                    "status": "partial_with_gaps",
                    "stopped_before_execution": True,
                    "analysis_mode": plan.get("mode"),
                    "analysis_plan": plan_p.name,
                    "gaps": plan.get("gaps") or [],
                    "elapsed_seconds": elapsed,
                    "result_json": seal["result_json"],
                }

            target_script = _generate_linear_analysis_script(
                primary_data,
                out_dir / f"{stem}_generated_analysis.py",
                plan,
            )
            generated = True
            script_args = ["--input", str(primary_data.resolve()), "--lib-dir", str(HERE)]
        else:
            return {"ok": False, "status": "partial_with_gaps", "gaps": [{"code": "NO_INPUT_DATA", "detail": f"No Python script or data table found under {input_dir.name}"}]}

    if generated:
        arts.append(target_script.name)
    if not fast_path:
        env_rep = audit_environment(script_path=target_script)
        env_p = out_dir / f"{stem}_env_lock.json"
        env_p.write_text(json.dumps(env_rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        arts.append(env_p.name)

        ast_rep = audit_script_contract(target_script, input_csv=primary_data if is_csv else None)
        ast_p = out_dir / f"{stem}_static_audit.json"
        ast_p.write_text(json.dumps(ast_rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        arts.append(ast_p.name)

    expected_query_rows = plan.get("primary_expected_rows") if is_db else None
    run_rep = run_isolated(
        target_script,
        work_dir=work_dir,
        out_dir=out_dir,
        stem=stem,
        input_csv=primary_data if is_csv else None,
        input_db=primary_data if is_db else None,
        expected_query_rows=expected_query_rows,
        script_args=script_args,
    )
    elapsed = round(time.monotonic() - t0, 4)
    arts.extend([f"{stem}_report.json", f"{stem}_report.md", f"{stem}_gate_summary.svg"])

    # Copy fresh scientific outputs from work_dir into out_dir
    sci_summary: dict[str, Any] = {}
    if run_rep.get("ok"):
        for item in (run_rep.get("snapshot_audit") or {}).get("fresh_artifacts") or []:
            src_f = work_dir / item["rel"]
            if src_f.is_file():
                dst_f = out_dir / src_f.name
                shutil.copy2(src_f, dst_f)
                if src_f.name not in arts:
                    arts.append(src_f.name)
                if src_f.name == "analysis_summary.json":
                    try:
                        sci_summary = json.loads(src_f.read_text(encoding="utf-8-sig"))
                    except Exception:
                        sci_summary = {}

    sealed_payload = dict(run_rep)
    if plan:
        sealed_payload["analysis_plan"] = plan
    if sci_summary:
        sealed_payload["scientific_analysis"] = sci_summary
        key_metrics: dict[str, Any] = {
            "analysis_mode": sci_summary.get("analysis_mode"),
            "seed": sci_summary.get("seed"),
            "numeric_crosscheck_ok": (run_rep.get("numeric_crosscheck") or {}).get("ok"),
            "numeric_crosscheck_max_rel_err": (run_rep.get("numeric_crosscheck") or {}).get("max_rel_err"),
            "dedup_policy": (sci_summary.get("dedup_audit") or {}).get("dedup_policy"),
            "dedup_removed_rows": (sci_summary.get("dedup_audit") or {}).get("removed_row_count"),
        }
        if isinstance(sci_summary.get("regression_ols"), dict):
            ols = sci_summary["regression_ols"]
            key_metrics["regression_ols"] = {
                "target": ols["target"],
                "predictors": ols["predictors"],
                "n_complete": ols["n_complete"],
                "r_squared": ols["r_squared"],
                "adj_r_squared": ols["adj_r_squared"],
                "cohens_f2": ols["cohens_f2"],
                "rmse": ols["rmse"],
                "f_stat": ols["f_stat"],
                "f_pvalue": ols["f_pvalue"],
                "coefficients": ols["coefficients"],
                "vif": ols["vif"],
                "diagnostics": ols["diagnostics"],
                "log1p_sensitivity": ols["log1p_sensitivity"],
            }
        if isinstance(sci_summary.get("group_comparison"), dict):
            gc = sci_summary["group_comparison"]
            key_metrics["group_comparison"] = {
                "group_column": gc["group_column"],
                "target_column": gc["target_column"],
                "n_total": gc["n_total"],
                "empty_groups": gc["empty_groups"],
                "group_summaries": gc["group_summaries"],
                "omnibus_tests": gc["omnibus_tests"],
                "pairwise_comparisons": gc["pairwise_comparisons"],
            }
        if isinstance(sci_summary.get("safe_join_audit"), dict):
            key_metrics["safe_join_audit"] = sci_summary["safe_join_audit"]
        sealed_payload["metrics"] = key_metrics

    merged_payload_p = out_dir / f"{stem}_sealed_payload.json"
    merged_payload_p.write_text(json.dumps(sealed_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    seal = seal_result_contract(
        case_json_path=case_json_p,
        result_json_path=out_dir / "result.json",
        task_id="challenge-9ec929fe",
        status="ok" if run_rep.get("ok") else "error",
        payload_path=merged_payload_p,
        artifacts=arts,
        sources=[target_script.name] + ([primary_data.name] if primary_data else []),
        uncertainty=plan.get("uncertainty") or [],
        fresh=True,
    )
    if merged_payload_p.exists():
        merged_payload_p.unlink()

    return {
        "ok": bool(run_rep.get("ok") and seal.get("ok")),
        "status": "ok" if (run_rep.get("ok") and seal.get("ok")) else "error",
        "analysis_mode": sci_summary.get("analysis_mode") or plan.get("mode"),
        "analysis_plan": "analysis_plan.json" if plan else None,
        "dedup_audit": "dedup_audit.json" if (out_dir / "dedup_audit.json").is_file() else None,
        "script_used": target_script.name,
        "script_auto_generated": generated,
        "fast_path": fast_path,
        "elapsed_seconds": elapsed,
        "exit_code": run_rep.get("execution", {}).get("exit_code"),
        "fresh_artifacts": len((run_rep.get("snapshot_audit") or {}).get("fresh_artifacts") or []),
        "explicit_seed_detected": (run_rep.get("static_audit") or {}).get("readability_summary", {}).get("explicit_seed_detected"),
        "dedup_check_detected": (run_rep.get("static_audit") or {}).get("readability_summary", {}).get("dedup_check_detected"),
        "gate7_crosscheck_ok": (run_rep.get("numeric_crosscheck") or {}).get("ok"),
        "gate7_max_rel_err": (run_rep.get("numeric_crosscheck") or {}).get("max_rel_err"),
        "result_json": seal["result_json"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-manual /input -> /app/output scientific code generation, audit, and isolated runner.")
    parser.add_argument("--input-dir", required=True, help="Case input directory (/input).")
    parser.add_argument("--out-dir", required=True, help="Output directory (/app/output).")
    parser.add_argument("--stem", default="exec_verification", help="Output filename stem.")
    parser.add_argument("--script", default=None, help="Optional explicit script override.")
    parser.add_argument("--fast-path", action="store_true", help="64-request budget fast path skipping duplicate standalone pre-runs.")
    args = parser.parse_args(argv)

    rep = run_auto_code_workflow(
        Path(args.input_dir),
        Path(args.out_dir),
        stem=args.stem,
        script_override=Path(args.script) if args.script else None,
        fast_path=args.fast_path,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
