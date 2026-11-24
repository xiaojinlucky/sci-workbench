#!/usr/bin/env python3
"""Literature Extracted-Data Cleaning, Schema Probing & Standardization Pipeline (challenge-f4bca185).

Pure Python 3.12 standard library. Implements R01-R06:
- R01: Dynamic real schema probing across CSV, JSON, JSONL, and read-only SQLite (mode=ro + PRAGMA table_info),
       emitting unmapped_fields and protecting leading-zero string IDs (e.g., '0012').
- R02: Locale-aware number & complex value parser: European decimal comma (1,25 / 1.250,5) vs English thousands
       (1,250 / 1,250.5), scientific notation (1.2e-3 / 1.2x10^-3), mean +/- uncertainty, ranges (a-b),
       and detection-limit censored values (<0.05 / >100) preserving censor_operator + precision_sigfigs.
- R03: Extended compound unit & SI conversion engine with explicit delta-temperature guard
       (Δ°C / delta_C / ± spread NEVER adds 273.15!), retaining raw_value, raw_unit, conversion_formula.
- R04: Strict context-locked deduplication & 3-way conflict stratification (DUPLICATE_MERGED vs
       CONTEXT_DISTINCT_PRESERVED vs VALUE_CONFLICT_SAME_CONTEXT).
- R05: Missingness (NA/ND/null/- -> null, NEVER 0) and unknown-unit quarantine into needs_human_review.
- R06: Traceable export of result.json, cleaned_records.csv, cleaning_audit_report.json, cleaning_review_report.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    import budget_guard
    import final_audit
    import gap_gate
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log
    from dedup_context_guard import deduplicate_records_with_context_lock
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import budget_guard
    import final_audit
    import gap_gate
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log
    from dedup_context_guard import deduplicate_records_with_context_lock


DEFAULT_FIELD_ALIASES: dict[str, str] = {
    "record_id": "row_id",
    "item_id": "row_id",
    "itemid": "row_id",
    "entry_id": "row_id",
    "sample": "entity",
    "sample_name": "entity",
    "catalyst": "entity",
    "material": "entity",
    "compound": "entity",
    "substance": "entity",
    "analyte": "entity",
    "metric_name": "metric",
    "indicator": "metric",
    "parameter": "metric",
    "property": "metric",
    "endpoint": "metric",
    "val": "value",
    "raw_val": "value",
    "measured_value": "value",
    "measured_val": "value",
    "reported_value": "value",
    "unit_str": "unit",
    "units": "unit",
    "reported_unit": "unit",
    "doc_id": "source_document_id",
    "paper_id": "source_document_id",
    "source": "source_document_id",
    "source_doc": "source_document_id",
    "condition": "experimental_condition",
    "conditions": "experimental_condition",
    "exp_condition": "experimental_condition",
    "temp": "temperature",
    "temp_c": "temperature",
    "replicate": "replicate_id",
    "batch": "replicate_id",
}

KNOWN_STANDARD_FIELDS = {
    "row_id", "id", "entity", "metric", "value", "unit", "doi", "title", "venue", "type",
    "source_document_id", "paper_id", "experimental_condition", "condition_key",
    "material", "catalyst", "pollutant", "substrate", "dataset", "temperature", "ph",
    "solvent", "atmosphere", "replicate_id", "paragraph_id", "quote", "sample_id", "compound_id",
}

# Case-sensitive exact units checked first (M molarity vs m meter; C/ΔC)
EXACT_UNIT_CONVERSIONS: dict[str, tuple[str, float, str, str]] = {
    # (canonical_unit, factor, formula, kind)
    "M": ("mol/L", 1.0, "x * 1", "linear"),
    "mM": ("mol/L", 1e-3, "x * 1e-3", "linear"),
    "uM": ("mol/L", 1e-6, "x * 1e-6", "linear"),
    "µM": ("mol/L", 1e-6, "x * 1e-6", "linear"),
    "nM": ("mol/L", 1e-9, "x * 1e-9", "linear"),
    "pM": ("mol/L", 1e-12, "x * 1e-12", "linear"),
    "m": ("m", 1.0, "x * 1", "linear"),
    "cm": ("m", 1e-2, "x * 1e-2", "linear"),
    "mm": ("m", 1e-3, "x * 1e-3", "linear"),
    "um": ("m", 1e-6, "x * 1e-6", "linear"),
    "µm": ("m", 1e-6, "x * 1e-6", "linear"),
    "nm": ("m", 1e-9, "x * 1e-9", "linear"),
    "Å": ("m", 1e-10, "x * 1e-10", "linear"),
    "A": ("A", 1.0, "x * 1", "linear"),
    "mA": ("A", 1e-3, "x * 1e-3", "linear"),
    "uA": ("A", 1e-6, "x * 1e-6", "linear"),
    "µA": ("A", 1e-6, "x * 1e-6", "linear"),
    "nA": ("A", 1e-9, "x * 1e-9", "linear"),
}

# Lowercase unit dictionary
LOWER_UNIT_CONVERSIONS: dict[str, tuple[str, float, str, str]] = {
    # Potential
    "v": ("V", 1.0, "x * 1", "linear"),
    "mv": ("V", 1e-3, "x * 1e-3", "linear"),
    "uv": ("V", 1e-6, "x * 1e-6", "linear"),
    "µv": ("V", 1e-6, "x * 1e-6", "linear"),
    "kv": ("V", 1e3, "x * 1e3", "linear"),
    # Current density
    "ma/cm2": ("mA/cm^2", 1.0, "x * 1", "linear"),
    "ma/cm^2": ("mA/cm^2", 1.0, "x * 1", "linear"),
    "ma cm-2": ("mA/cm^2", 1.0, "x * 1", "linear"),
    "a/cm2": ("mA/cm^2", 1e3, "x * 1e3", "linear"),
    "a/cm^2": ("mA/cm^2", 1e3, "x * 1e3", "linear"),
    "ua/cm2": ("mA/cm^2", 1e-3, "x * 1e-3", "linear"),
    "µa/cm2": ("mA/cm^2", 1e-3, "x * 1e-3", "linear"),
    "a/m2": ("mA/cm^2", 0.1, "x * 0.1", "linear"),
    "a/m^2": ("mA/cm^2", 0.1, "x * 0.1", "linear"),
    # Mass
    "kg": ("g", 1e3, "x * 1e3", "linear"),
    "g": ("g", 1.0, "x * 1", "linear"),
    "mg": ("g", 1e-3, "x * 1e-3", "linear"),
    "ug": ("g", 1e-6, "x * 1e-6", "linear"),
    "µg": ("g", 1e-6, "x * 1e-6", "linear"),
    "ng": ("g", 1e-9, "x * 1e-9", "linear"),
    # Mass & molar concentration
    "mg/l": ("mg/L", 1.0, "x * 1", "linear"),
    "ug/ml": ("mg/L", 1.0, "x * 1", "linear"),
    "µg/ml": ("mg/L", 1.0, "x * 1", "linear"),
    "g/l": ("mg/L", 1e3, "x * 1e3", "linear"),
    "ug/l": ("mg/L", 1e-3, "x * 1e-3", "linear"),
    "µg/l": ("mg/L", 1e-3, "x * 1e-3", "linear"),
    "ng/ml": ("mg/L", 1e-3, "x * 1e-3", "linear"),
    "ppm": ("ppm", 1.0, "x * 1", "linear"),
    "ppb": ("ppm", 1e-3, "x * 1e-3", "linear"),
    "mol/l": ("mol/L", 1.0, "x * 1", "linear"),
    "mmol/l": ("mol/L", 1e-3, "x * 1e-3", "linear"),
    "umol/l": ("mol/L", 1e-6, "x * 1e-6", "linear"),
    "µmol/l": ("mol/L", 1e-6, "x * 1e-6", "linear"),
    # Temperature (absolute vs explicit delta-T)
    "k": ("K", 1.0, "x * 1", "linear"),
    "°c": ("K", 1.0, "x + 273.15", "abs_celsius"),
    "degc": ("K", 1.0, "x + 273.15", "abs_celsius"),
    "c": ("K", 1.0, "x + 273.15", "abs_celsius"),
    "°f": ("K", 5.0 / 9.0, "(x - 32) * 5/9 + 273.15", "abs_fahrenheit"),
    "degf": ("K", 5.0 / 9.0, "(x - 32) * 5/9 + 273.15", "abs_fahrenheit"),
    "δ°c": ("K", 1.0, "x * 1 (delta_T)", "delta_temp"),
    "δc": ("K", 1.0, "x * 1 (delta_T)", "delta_temp"),
    "Δ°c": ("K", 1.0, "x * 1 (delta_T)", "delta_temp"),
    "Δc": ("K", 1.0, "x * 1 (delta_T)", "delta_temp"),
    "delta_c": ("K", 1.0, "x * 1 (delta_T)", "delta_temp"),
    "delta°c": ("K", 1.0, "x * 1 (delta_T)", "delta_temp"),
    "degc_diff": ("K", 1.0, "x * 1 (delta_T)", "delta_temp"),
    "δ°f": ("K", 5.0 / 9.0, "x * 5/9 (delta_T)", "delta_temp"),
    "delta_f": ("K", 5.0 / 9.0, "x * 5/9 (delta_T)", "delta_temp"),
    # Time
    "s": ("s", 1.0, "x * 1", "linear"),
    "sec": ("s", 1.0, "x * 1", "linear"),
    "ms": ("s", 1e-3, "x * 1e-3", "linear"),
    "us": ("s", 1e-6, "x * 1e-6", "linear"),
    "µs": ("s", 1e-6, "x * 1e-6", "linear"),
    "min": ("s", 60.0, "x * 60", "linear"),
    "h": ("s", 3600.0, "x * 3600", "linear"),
    "hr": ("s", 3600.0, "x * 3600", "linear"),
    "hours": ("s", 3600.0, "x * 3600", "linear"),
    "d": ("s", 86400.0, "x * 86400", "linear"),
    "day": ("s", 86400.0, "x * 86400", "linear"),
    "days": ("s", 86400.0, "x * 86400", "linear"),
    # Energy
    "j": ("J", 1.0, "x * 1", "linear"),
    "kj": ("J", 1e3, "x * 1e3", "linear"),
    "mj": ("J", 1e6, "x * 1e6", "linear"),
    "ev": ("eV", 1.0, "x * 1", "linear"),
    "mev": ("eV", 1e-3, "x * 1e-3", "linear"),
    "kj/mol": ("kJ/mol", 1.0, "x * 1", "linear"),
    "j/mol": ("kJ/mol", 1e-3, "x * 1e-3", "linear"),
    "kcal/mol": ("kJ/mol", 4.184, "x * 4.184", "linear"),
    # Area & surface area
    "m2": ("m^2", 1.0, "x * 1", "linear"),
    "m^2": ("m^2", 1.0, "x * 1", "linear"),
    "cm2": ("m^2", 1e-4, "x * 1e-4", "linear"),
    "cm^2": ("m^2", 1e-4, "x * 1e-4", "linear"),
    "mm2": ("m^2", 1e-6, "x * 1e-6", "linear"),
    "mm^2": ("m^2", 1e-6, "x * 1e-6", "linear"),
    "m2/g": ("m^2/g", 1.0, "x * 1", "linear"),
    "m^2/g": ("m^2/g", 1.0, "x * 1", "linear"),
    # Volume
    "l": ("L", 1.0, "x * 1", "linear"),
    "ml": ("L", 1e-3, "x * 1e-3", "linear"),
    "ul": ("L", 1e-6, "x * 1e-6", "linear"),
    "µl": ("L", 1e-6, "x * 1e-6", "linear"),
    # Pressure
    "pa": ("Pa", 1.0, "x * 1", "linear"),
    "kpa": ("Pa", 1e3, "x * 1e3", "linear"),
    "mpa": ("Pa", 1e6, "x * 1e6", "linear"),
    "gpa": ("Pa", 1e9, "x * 1e9", "linear"),
    "bar": ("Pa", 1e5, "x * 1e5", "linear"),
    "atm": ("Pa", 101325.0, "x * 101325", "linear"),
    # Catalytic / flow rates
    "mmol/g/h": ("mmol/g/h", 1.0, "x * 1", "linear"),
    "mmol g-1 h-1": ("mmol/g/h", 1.0, "x * 1", "linear"),
    "umol/g/h": ("mmol/g/h", 1e-3, "x * 1e-3", "linear"),
    "µmol/g/h": ("mmol/g/h", 1e-3, "x * 1e-3", "linear"),
    "µmol g-1 h-1": ("mmol/g/h", 1e-3, "x * 1e-3", "linear"),
    "mol/g/h": ("mmol/g/h", 1e3, "x * 1e3", "linear"),
    "ml/min": ("mL/min", 1.0, "x * 1", "linear"),
    "l/h": ("mL/min", 1000.0 / 60.0, "x * 1000/60", "linear"),
    # Ratios / percentages
    "%": ("%", 1.0, "x * 1", "linear"),
    "pct": ("%", 1.0, "x * 1", "linear"),
    "percent": ("%", 1.0, "x * 1", "linear"),
    "wt%": ("wt%", 1.0, "x * 1", "linear"),
    "at%": ("at%", 1.0, "x * 1", "linear"),
    "vol%": ("vol%", 1.0, "x * 1", "linear"),
}

MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan", "nd", "n.d.", "not reported", "-", "--", "未报告"}
DELTA_METRIC_RE = re.compile(r"(?:delta[_ -]?t|temp(?:erature)?[_ -]?(?:diff|change|shift|rise|drop)|Δt|温差)", re.I)

CENSORED_RE = re.compile(r"^\s*(?:(?:lod|loq|dl)\s*)?(<=|>=|<|>|≤|≥)\s*(.+?)\s*$", re.I)
PM_RE = re.compile(r"^\s*([^\s±]+)\s*(?:±|\+/-)\s*([^\s±]+)\s*$")
RANGE_RE = re.compile(r"^\s*([-+]?[\d.,]+(?:[eE][-+]?\d+)?)\s*(?:–|—|\bto\b|(?<=\d)\s*-\s*(?=\d))\s*([-+]?[\d.,]+(?:[eE][-+]?\d+)?)\s*$", re.I)
SCI_CROSS_RE = re.compile(r"^\s*([-+]?[\d.,]+)\s*(?:×|x|\*)\s*10\s*\^?\s*\{?\s*([-+]?\d+)\s*\}?\s*$", re.I)
LEADING_ZERO_ID_RE = re.compile(r"^0\d+$")


def count_sigfigs(num_literal: str) -> int | None:
    """Compute significant figures from a normalized numeric literal (using '.' decimal)."""
    s = num_literal.strip().lstrip("+-")
    if "e" in s.lower():
        s = s.lower().split("e", 1)[0]
    s = s.replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?|\.\d+", s):
        return None
    if "." in s:
        digits = s.replace(".", "").lstrip("0")
        return len(digits) if digits else 1
    digits = s.lstrip("0").rstrip("0")
    return len(digits) if digits else 1


def parse_locale_number(token: str, locale: str = "auto") -> tuple[float | None, int | None, str | None, bool]:
    """Parse number supporting EN/EU decimals, thousands separators, and scientific notation.

    Returns (value, sigfigs, rule_note, ambiguous_comma_flag).
    """
    raw = token.strip().lstrip("~≈").strip()
    if not raw:
        return None, None, None, False

    # Scientific notation with x 10^n
    m_sci = SCI_CROSS_RE.match(raw)
    if m_sci:
        mant_val, sf, _, amb = parse_locale_number(m_sci.group(1), locale=locale)
        if mant_val is None:
            return None, None, None, False
        exp = int(m_sci.group(2))
        return mant_val * (10.0 ** exp), sf, "R02_SCIENTIFIC_NOTATION", amb

    # Standard e-notation (with optional European decimal comma in mantissa)
    if re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?[eE][-+]?\d+", raw):
        norm = raw.replace(",", ".")
        return float(norm), count_sigfigs(norm), "R02_SCIENTIFIC_NOTATION", False

    has_comma = "," in raw
    has_dot = "." in raw
    ambiguous = False
    rule_note: str | None = None

    if has_comma and has_dot:
        if raw.rfind(",") > raw.rfind("."):
            # EU thousands '.' + decimal ',' e.g. 1.250,5
            norm = raw.replace(".", "").replace(",", ".")
            rule_note = "R02_EU_THOUSANDS_DECIMAL"
        else:
            # EN thousands ',' + decimal '.' e.g. 1,250.5
            norm = raw.replace(",", "")
            rule_note = "R02_EN_THOUSANDS_DECIMAL"
    elif has_comma:
        parts = raw.split(",")
        if len(parts) == 2 and re.fullmatch(r"[-+]?\d+", parts[0]) and re.fullmatch(r"\d+", parts[1]):
            if len(parts[1]) != 3:
                # Unambiguous EU decimal comma (e.g. 1,25 or 0,0850 or 3,5)
                norm = f"{parts[0]}.{parts[1]}"
                rule_note = "R02_EU_DECIMAL_COMMA"
            else:
                # Single comma followed by exactly 3 digits (e.g. 1,250)
                if locale == "eu":
                    norm = f"{parts[0]}.{parts[1]}"
                    rule_note = "R02_EU_DECIMAL_COMMA"
                else:
                    norm = f"{parts[0]}{parts[1]}"
                    rule_note = "R02_EN_THOUSANDS_COMMA"
                    ambiguous = (locale == "auto")
        elif all(re.fullmatch(r"\d{3}", p) for p in parts[1:]) and re.fullmatch(r"[-+]?\d{1,3}", parts[0]):
            norm = "".join(parts)
            rule_note = "R02_EN_THOUSANDS_COMMA"
        else:
            return None, None, None, False
    else:
        if not re.fullmatch(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)", raw):
            return None, None, None, False
        norm = raw

    try:
        val = float(norm)
    except ValueError:
        return None, None, None, False
    return val, count_sigfigs(norm), rule_note, ambiguous


def probe_sqlite_readonly(db_path: Path, target_table: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read-only SQLite probing using mode=ro + PRAGMA table_info (never assumes hardcoded columns)."""
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    probe_info: dict[str, Any] = {
        "file": db_path.name,
        "format": "sqlite",
        "mode": "ro",
        "tables": {},
        "selected_tables": [],
        "row_count": 0,
        "errors": [],
    }
    rows_out: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.cursor()
            tables = [
                r[0] for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            for tname in tables:
                safe_t = tname.replace('"', '""')
                cols = cur.execute(f'PRAGMA table_info("{safe_t}")').fetchall()
                probe_info["tables"][tname] = [
                    {"name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": bool(c[5])}
                    for c in cols
                ]

            selected = [target_table] if (target_table and target_table in probe_info["tables"]) else tables
            for tname in selected:
                col_names = [c["name"] for c in probe_info["tables"][tname]]
                if not col_names:
                    continue
                # Skip pure metadata/migration tables when auto-selecting multiple tables
                if not target_table and len(selected) > 1 and tname.lower() in {"schema_migrations", "sqlite_sequence", "metadata"}:
                    continue
                safe_t = tname.replace('"', '""')
                fetched = cur.execute(f'SELECT * FROM "{safe_t}"').fetchall()
                probe_info["selected_tables"].append(tname)
                for ridx, tup in enumerate(fetched, start=1):
                    row_dict = {col_names[i]: tup[i] for i in range(len(col_names))}
                    low_map = {str(k).strip().lower(): v for k, v in row_dict.items()}
                    rid_raw = (
                        low_map.get("row_id")
                        or low_map.get("record_id")
                        or low_map.get("item_id")
                        or low_map.get("itemid")
                        or low_map.get("entry_id")
                        or low_map.get("id")
                    )
                    row_dict["row_id"] = str(rid_raw) if rid_raw not in (None, "") else f"{db_path.stem}_{tname}-R{ridx:03d}"
                    row_dict["_source_file"] = f"{db_path.name}:{tname}"
                    rows_out.append(row_dict)
            probe_info["row_count"] = len(rows_out)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        probe_info["errors"].append(f"SQLite read error: {exc}")
    return rows_out, probe_info


def load_raw_records_and_schema(input_dir: Path, case_data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Dynamically probe CSV/JSON/JSONL/SQLite schemas and preserve leading-zero string IDs."""
    records: list[dict[str, Any]] = []
    aliases = dict(DEFAULT_FIELD_ALIASES)
    if isinstance(case_data.get("field_aliases"), dict):
        aliases.update({str(k).strip().lower(): str(v).strip() for k, v in case_data["field_aliases"].items()})
    synonym_map = {
        str(k).strip().lower(): str(v).strip()
        for k, v in (case_data.get("synonym_map") or {}).items()
    } if isinstance(case_data.get("synonym_map"), dict) else {}
    number_locale = str(case_data.get("number_locale") or "auto").strip().lower()
    sqlite_table = str(case_data.get("sqlite_table") or "").strip()

    schema_files: list[dict[str, Any]] = []

    # Inline case.json records
    inline_rows = case_data.get("records") or case_data.get("rows") or []
    if inline_rows:
        cols_seen: set[str] = set()
        for idx, rec in enumerate(inline_rows, start=1):
            if isinstance(rec, dict):
                r = dict(rec)
                cols_seen.update(str(k) for k in r.keys())
                rid = r.get("row_id") or r.get("id")
                r["row_id"] = str(rid) if rid not in (None, "") else f"CASE-R{idx:03d}"
                r.setdefault("_source_file", "case.json")
                records.append(r)
        schema_files.append({"file": "case.json", "format": "json_inline", "columns": sorted(cols_seen), "row_count": len(inline_rows)})

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file() and not p.is_symlink()):
        if fp.name in ("case.json", "task.md"):
            continue
        ext = fp.suffix.lower()
        rel = fp.relative_to(input_dir).as_posix()
        if ext == ".csv":
            with fp.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
                reader = csv.DictReader(fh)
                cols = list(reader.fieldnames or [])
                cnt = 0
                for ridx, row in enumerate(reader, start=1):
                    r = {k: v for k, v in row.items() if k is not None}
                    rid = r.get("row_id") or r.get("id")
                    r["row_id"] = str(rid) if rid not in (None, "") else f"{fp.stem}-R{ridx:03d}"
                    r.setdefault("_source_file", rel)
                    records.append(r)
                    cnt += 1
            schema_files.append({"file": rel, "format": "csv", "columns": cols, "row_count": cnt})
        elif ext == ".jsonl":
            cols_seen = set()
            cnt = 0
            for ridx, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    r = dict(row)
                    cols_seen.update(str(k) for k in r.keys())
                    rid = r.get("row_id") or r.get("id")
                    r["row_id"] = str(rid) if rid not in (None, "") else f"{fp.stem}-R{ridx:03d}"
                    r.setdefault("_source_file", rel)
                    records.append(r)
                    cnt += 1
            schema_files.append({"file": rel, "format": "jsonl", "columns": sorted(cols_seen), "row_count": cnt})
        elif ext == ".json":
            try:
                obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
                rows = obj.get("records") or obj.get("rows") if isinstance(obj, dict) else (obj if isinstance(obj, list) else [])
                cols_seen = set()
                cnt = 0
                for ridx, row in enumerate(rows or [], start=1):
                    if isinstance(row, dict):
                        r = dict(row)
                        cols_seen.update(str(k) for k in r.keys())
                        rid = r.get("row_id") or r.get("id")
                        r["row_id"] = str(rid) if rid not in (None, "") else f"{fp.stem}-R{ridx:03d}"
                        r.setdefault("_source_file", rel)
                        records.append(r)
                        cnt += 1
                if cnt > 0:
                    schema_files.append({"file": rel, "format": "json", "columns": sorted(cols_seen), "row_count": cnt})
            except Exception:
                pass
        elif ext in (".sqlite", ".db", ".sqlite3"):
            sq_rows, sq_probe = probe_sqlite_readonly(fp, target_table=sqlite_table)
            records.extend(sq_rows)
            schema_files.append(sq_probe)

    # Track leading-zero string IDs across all records
    leading_zero_ids: list[dict[str, str]] = []
    for r in records:
        for id_col in ("row_id", "id", "sample_id", "item_id", "compound_id"):
            val = r.get(id_col)
            if isinstance(val, str) and LEADING_ZERO_ID_RE.match(val.strip()):
                leading_zero_ids.append({"row_id": str(r.get("row_id")), "field": id_col, "preserved_value": val.strip()})

    rules = {
        "field_aliases": aliases,
        "synonym_map": synonym_map,
        "number_locale": number_locale,
    }
    schema_report = {
        "files_probed": schema_files,
        "leading_zero_ids_protected": leading_zero_ids,
        "unmapped_fields_global": [],
    }
    return records, rules, schema_report


def convert_quantity(
    val: float,
    raw_unit: str,
    *,
    is_spread: bool = False,
    metric_name: str = "",
) -> tuple[float | None, str | None, str | None, str | None]:
    """Convert value to canonical SI/domain unit with strict delta-temperature guard.

    Returns (converted_val, canonical_unit, formula, rule_id).
    """
    u_exact = " ".join((raw_unit or "").strip().split())
    if not u_exact:
        return None, None, None, None

    if u_exact in EXACT_UNIT_CONVERSIONS:
        tu, factor, formula, _ = EXACT_UNIT_CONVERSIONS[u_exact]
        return round(val * factor, 10), tu, formula, "R03_UNIT_SCALE"

    u_low = u_exact.lower()
    if u_low not in LOWER_UNIT_CONVERSIONS:
        return None, None, None, None

    tu, factor, formula, kind = LOWER_UNIT_CONVERSIONS[u_low]
    is_delta_metric = bool(DELTA_METRIC_RE.search(metric_name or ""))

    if kind == "delta_temp":
        return round(val * factor, 10), tu, formula, "R03_DELTA_TEMP_GUARD"
    if kind == "abs_celsius":
        if is_spread or is_delta_metric:
            return round(val, 10), "K", "x * 1 (delta_T guard: no +273.15)", "R03_DELTA_TEMP_GUARD"
        return round(val + 273.15, 6), "K", "x + 273.15", "R03_TEMP_ABS_TO_K"
    if kind == "abs_fahrenheit":
        if is_spread or is_delta_metric:
            return round(val * (5.0 / 9.0), 10), "K", "x * 5/9 (delta_T guard)", "R03_DELTA_TEMP_GUARD"
        return round((val - 32.0) * 5.0 / 9.0 + 273.15, 6), "K", "(x - 32) * 5/9 + 273.15", "R03_TEMP_ABS_TO_K"

    return round(val * factor, 10), tu, formula, "R03_UNIT_SCALE"


def clean_single_record(
    raw_rec: dict[str, Any],
    rules: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute R01, R02, R03, R05 on a single record while keeping raw_record immutable."""
    aliases = rules["field_aliases"]
    synonym_map = rules["synonym_map"]
    locale = rules["number_locale"]

    row_id = str(raw_rec.get("row_id") or "unknown")
    changelog: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    applied_rules: list[str] = []

    mapped: dict[str, Any] = {
        "row_id": row_id,
        "raw_record": dict(raw_rec),
        "censor_operator": "",
        "precision_sigfigs": None,
        "normalized_uncertainty": None,
    }
    unmapped: list[str] = []

    # R01: Dynamic field alias mapping
    for k, v in raw_rec.items():
        if k in ("row_id", "_source_file"):
            continue
        k_low = str(k).strip().lower()
        target_k = aliases.get(k_low, k_low)
        if target_k not in mapped or mapped[target_k] in (None, ""):
            mapped[target_k] = v
            if target_k != k:
                changelog.append({"row_id": row_id, "rule_id": "R01_FIELD_ALIAS", "field": k, "before": k, "after": target_k})
                applied_rules.append("R01")
        if k_low not in aliases and k_low not in KNOWN_STANDARD_FIELDS:
            unmapped.append(str(k))
    if unmapped:
        mapped["unmapped_fields"] = unmapped

    # Optional explicit synonym mapping for entity/metric
    for syn_field in ("entity", "metric"):
        cur_v = str(mapped.get(syn_field) or "").strip()
        if cur_v and cur_v.lower() in synonym_map:
            new_v = synonym_map[cur_v.lower()]
            if new_v != cur_v:
                mapped[syn_field] = new_v
                changelog.append({"row_id": row_id, "rule_id": "R04_SYNONYM_MAP", "field": syn_field, "before": cur_v, "after": new_v})
                applied_rules.append("R04_SYN")

    raw_val_str = "" if mapped.get("value") is None else str(mapped.get("value")).strip()
    raw_unit_str = "" if mapped.get("unit") is None else str(mapped.get("unit")).strip()
    metric_name = str(mapped.get("metric") or "")
    mapped["raw_value"] = mapped.get("value")
    mapped["raw_unit"] = mapped.get("unit")

    # R05: Missing values (NEVER coerce to 0)
    if raw_val_str.lower() in MISSING_TOKENS:
        mapped["value_kind"] = "missing"
        mapped["normalized_value"] = None
        mapped["normalized_unit"] = None
        mapped["conversion_formula"] = None
        applied_rules.append("R05")
        mapped["applied_rules"] = sorted(set(applied_rules))
        review_items.append({
            "row_id": row_id,
            "severity": "info",
            "reason": "missing_value",
            "detail": f"Original value {raw_val_str!r} preserved as null (never 0).",
        })
        return mapped, changelog, review_items

    # R02: Complex value & locale-aware numeric parsing
    parsed_num: float | None = None
    m_cens = CENSORED_RE.match(raw_val_str)
    m_pm = PM_RE.match(raw_val_str) if not m_cens else None
    m_range = RANGE_RE.match(raw_val_str) if (not m_cens and not m_pm) else None

    if m_cens:
        op_raw = m_cens.group(1).replace("≤", "<=").replace("≥", ">=")
        val_c, sf_c, rnote, amb = parse_locale_number(m_cens.group(2), locale=locale)
        if val_c is None:
            mapped["value_kind"] = "unparsed_text"
            mapped["normalized_value"] = None
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "unparsed_value_format", "raw_value": raw_val_str})
            mapped["applied_rules"] = sorted(set(applied_rules + ["R05"]))
            return mapped, changelog, review_items
        mapped["value_kind"] = "censored"
        mapped["censor_operator"] = op_raw
        mapped["precision_sigfigs"] = sf_c
        parsed_num = val_c
        applied_rules.append("R02_CENSORED")
        changelog.append({"row_id": row_id, "rule_id": "R02_CENSORED_DETECTION_LIMIT", "field": "value", "before": raw_val_str, "after": f"{op_raw}{val_c}"})
        if amb:
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "ambiguous_locale_comma", "raw_value": raw_val_str, "detail": "Single comma with 3 trailing digits parsed as thousands separator under locale=auto."})
    elif m_pm:
        center, sf_c, rnote_c, amb_c = parse_locale_number(m_pm.group(1), locale=locale)
        spread, _, _, amb_s = parse_locale_number(m_pm.group(2), locale=locale)
        if center is None or spread is None:
            mapped["value_kind"] = "unparsed_text"
            mapped["normalized_value"] = None
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "unparsed_value_format", "raw_value": raw_val_str})
            mapped["applied_rules"] = sorted(set(applied_rules + ["R05"]))
            return mapped, changelog, review_items
        mapped["value_kind"] = "mean_uncertainty"
        mapped["parsed_center"] = center
        mapped["parsed_uncertainty"] = spread
        mapped["precision_sigfigs"] = sf_c
        parsed_num = center
        applied_rules.append("R02_PM")
        if rnote_c:
            changelog.append({"row_id": row_id, "rule_id": rnote_c, "field": "value", "before": raw_val_str, "after": f"{center} ± {spread}"})
        if amb_c or amb_s:
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "ambiguous_locale_comma", "raw_value": raw_val_str})
    elif m_range:
        lo, sf_lo, rnote_lo, amb_lo = parse_locale_number(m_range.group(1), locale=locale)
        hi, _, _, amb_hi = parse_locale_number(m_range.group(2), locale=locale)
        if lo is None or hi is None:
            mapped["value_kind"] = "unparsed_text"
            mapped["normalized_value"] = None
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "unparsed_value_format", "raw_value": raw_val_str})
            mapped["applied_rules"] = sorted(set(applied_rules + ["R05"]))
            return mapped, changelog, review_items
        mapped["value_kind"] = "range"
        mapped["parsed_range"] = [lo, hi]
        mapped["precision_sigfigs"] = sf_lo
        applied_rules.append("R02_RANGE")
        if rnote_lo:
            changelog.append({"row_id": row_id, "rule_id": rnote_lo, "field": "value", "before": raw_val_str, "after": f"{lo}-{hi}"})
        if amb_lo or amb_hi:
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "ambiguous_locale_comma", "raw_value": raw_val_str})
    else:
        val_s, sf_s, rnote_s, amb_s = parse_locale_number(raw_val_str, locale=locale)
        if val_s is None:
            mapped["value_kind"] = "unparsed_text"
            mapped["normalized_value"] = None
            mapped["normalized_unit"] = None
            mapped["conversion_formula"] = None
            applied_rules.append("R05")
            mapped["applied_rules"] = sorted(set(applied_rules))
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "unparsed_value_format", "raw_value": raw_val_str})
            return mapped, changelog, review_items
        mapped["value_kind"] = "scalar"
        mapped["parsed_scalar"] = val_s
        mapped["precision_sigfigs"] = sf_s
        parsed_num = val_s
        applied_rules.append("R02_SCALAR")
        if rnote_s:
            changelog.append({"row_id": row_id, "rule_id": rnote_s, "field": "value", "before": raw_val_str, "after": str(val_s)})
        if amb_s:
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "ambiguous_locale_comma", "raw_value": raw_val_str, "detail": "Single comma with 3 trailing digits parsed as thousands separator under locale=auto; confirm locale."})

    # R03: Unit conversion with delta-T guard
    if not raw_unit_str or raw_unit_str.lower() in MISSING_TOKENS:
        mapped["normalized_value"] = parsed_num if mapped["value_kind"] != "range" else f"{mapped['parsed_range'][0]}-{mapped['parsed_range'][1]}"
        mapped["normalized_unit"] = None
        mapped["conversion_formula"] = None
        applied_rules.append("R05")
        review_items.append({"row_id": row_id, "severity": "medium", "reason": "missing_unit", "raw_value": raw_val_str})
    elif mapped["value_kind"] == "range":
        lo, hi = mapped["parsed_range"]
        nlo, tu, fm, rid_u = convert_quantity(lo, raw_unit_str, metric_name=metric_name)
        nhi, _, _, _ = convert_quantity(hi, raw_unit_str, metric_name=metric_name)
        if nlo is None or nhi is None:
            mapped["normalized_value"] = None
            mapped["normalized_unit"] = None
            mapped["conversion_formula"] = None
            applied_rules.append("R05")
            review_items.append({"row_id": row_id, "severity": "high", "reason": "unknown_unit", "raw_unit": raw_unit_str})
        else:
            mapped["normalized_value"] = f"{nlo}-{nhi}"
            mapped["normalized_unit"] = tu
            mapped["conversion_formula"] = fm
            applied_rules.append("R03")
            changelog.append({"row_id": row_id, "rule_id": rid_u or "R03_UNIT_RANGE", "field": "value/unit", "before": f"{raw_val_str} {raw_unit_str}", "after": f"{nlo}-{nhi} {tu}", "formula": fm})
    else:
        assert parsed_num is not None
        nval, tu, fm, rid_u = convert_quantity(parsed_num, raw_unit_str, metric_name=metric_name)
        if nval is None:
            mapped["normalized_value"] = None
            mapped["normalized_unit"] = None
            mapped["conversion_formula"] = None
            applied_rules.append("R05")
            review_items.append({"row_id": row_id, "severity": "high", "reason": "unknown_unit", "raw_unit": raw_unit_str})
        else:
            mapped["normalized_value"] = nval
            mapped["normalized_unit"] = tu
            mapped["conversion_formula"] = fm
            applied_rules.append("R03")
            if nval != parsed_num or tu != raw_unit_str:
                changelog.append({
                    "row_id": row_id,
                    "rule_id": rid_u or "R03_UNIT_SCALE",
                    "field": "value/unit",
                    "before": f"{raw_val_str} {raw_unit_str}",
                    "after": f"{mapped['censor_operator']}{nval} {tu}",
                    "formula": fm,
                })
            if mapped["value_kind"] == "mean_uncertainty":
                spread = mapped["parsed_uncertainty"]
                nsp, _, sfm, srid = convert_quantity(spread, raw_unit_str, is_spread=True, metric_name=metric_name)
                mapped["normalized_uncertainty"] = nsp
                mapped["uncertainty_conversion_formula"] = sfm
                changelog.append({
                    "row_id": row_id,
                    "rule_id": srid or "R03_UNCERTAINTY_SCALE",
                    "field": "uncertainty/unit",
                    "before": f"±{spread} {raw_unit_str}",
                    "after": f"±{nsp} {tu}",
                    "formula": sfm,
                })

    mapped["applied_rules"] = sorted(set(applied_rules))
    return mapped, changelog, review_items


def render_cleaning_review_md(audit_bundle: dict[str, Any]) -> str:
    """Render concise human-readable cleaning and review report."""
    sp = audit_bundle["schema_probe_report"]
    lines = [
        f"# 文献数据清洗与标准化复核报告 (`{audit_bundle['case_id']}`)",
        "",
        "## 1. 动态 Schema 探查摘要 (`R01`)",
        "",
        f"- **已探查输入源**: `{len(sp['files_probed'])}` 个",
        f"- **受保护前导零字符串 ID**: `{len(sp['leading_zero_ids_protected'])}` 处",
        f"- **全局未映射字段 (`unmapped_fields_global`)**: `{sp['unmapped_fields_global'] or '无'}`",
        "",
    ]
    for f in sp["files_probed"]:
        if f["format"] == "sqlite":
            lines.append(f"- SQLite `{f['file']}` (`mode=ro`): 表 `{list(f['tables'].keys())}`｜选中 `{f['selected_tables']}`｜行数 `{f['row_count']}`")
        else:
            lines.append(f"- `{f['file']}` (`{f['format']}`): 列 `{f.get('columns', [])}`｜行数 `{f['row_count']}`")

    lines.extend([
        "",
        "## 2. 清洗与上下文锁分层结果 (`R02–R05`)",
        "",
        f"- **原始记录数**: `{audit_bundle['summary']['raw_record_count']}` -> **规范输出行数**: `{audit_bundle['summary']['canonical_record_count']}`",
        f"- **同上下文重复合并 (`DUPLICATE_MERGED`)**: `{len(audit_bundle['duplicate_log'])}`",
        f"- **不同实验条件保护保留 (`CONTEXT_DISTINCT_PRESERVED`)**: `{len(audit_bundle['context_preserved_log'])}`",
        f"- **同上下文科学数值冲突 (`VALUE_CONFLICT_SAME_CONTEXT`)**: `{len(audit_bundle['scientific_conflicts'])}`",
        f"- **人工复核队列总数 (`needs_human_review`)**: `{len(audit_bundle['needs_human_review'])}`",
        "",
    ])

    if audit_bundle["scientific_conflicts"]:
        lines.extend([
            "## 3. 科学数值冲突（严禁静默覆盖，双方已保留待裁决）",
            "",
            "| 行 A | 行 B | 实体 | 指标 | 冲突值 A vs B | 上下文签名 |",
            "|---|---|---|---|---|---|",
        ])
        for c in audit_bundle["scientific_conflicts"]:
            lines.append(f"| `{c['row_a']}` | `{c['row_b']}` | {c['entity']} | {c['metric']} | `{c['value_a']}` vs `{c['value_b']}` | `{c['context_signature']}` |")
        lines.append("")

    if audit_bundle["context_preserved_log"]:
        lines.extend(["## 4. 不同实验条件保护保留记录 (`CONTEXT_DISTINCT_PRESERVED`)", ""])
        for cp in audit_bundle["context_preserved_log"]:
            lines.append(f"- 行 `{cp['row_id']}` 与 `{cp['distinct_from_rows']}` 同属 `{cp['entity']} / {cp['metric']}` 但实验条件不同 (`{cp['context_signature']}`)，已独立保留未合并。")
        lines.append("")

    if audit_bundle["needs_human_review"]:
        lines.extend([
            "## 5. 人工复核队列 (`needs_human_review`)",
            "",
            "| 行号 (`row_id`) | 严重度 | 原因 (`reason`) | 详情 |",
            "|---|---|---|---|",
        ])
        for rq in audit_bundle["needs_human_review"]:
            det = str(rq.get("detail") or rq.get("raw_unit") or rq.get("raw_value") or "").replace("|", "\\|")
            lines.append(f"| `{rq['row_id']}` | `{rq.get('severity', 'medium')}` | `{rq['reason']}` | {det} |")
        lines.append("")

    return "\n".join(lines)


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="f4bca185-data-cleaner")

    case_path = input_dir / "case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace")) if case_path.is_file() else {}
    case_id = str(case_data.get("case_id") or "case-unknown")

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-f4bca185")

    raw_records, rules, schema_report = load_raw_records_and_schema(input_dir, case_data)
    budget_guard.tick_phase(budget_file, "clean_and_normalize")

    cleaned_rows: list[dict[str, Any]] = []
    changelog: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    unmapped_global: set[str] = set()

    for rec in raw_records:
        c, cl, rq = clean_single_record(rec, rules)
        cleaned_rows.append(c)
        changelog.extend(cl)
        review_queue.extend(rq)
        unmapped_global.update(c.get("unmapped_fields") or [])
    schema_report["unmapped_fields_global"] = sorted(unmapped_global)

    # R04: Strict context-locked deduplication & 3-way conflict stratification
    canonical_rows, dup_log, ctx_preserved_log, conflicts = deduplicate_records_with_context_lock(
        cleaned_rows,
        title_threshold=92.0,
        require_same_context=True,
    )
    for conf in conflicts:
        review_queue.append({
            "row_id": f"{conf['row_a']}|{conf['row_b']}",
            "severity": "high",
            "reason": conf["code"],
            "detail": f"{conf['entity']} / {conf['metric']}: {conf['value_a']} vs {conf['value_b']} under identical context ({conf['context_signature']})",
        })

    # Write cleaned_records.csv (preserving leading zeros as strings and exposing conflict/censor columns)
    csv_path = output_dir / "cleaned_records.csv"
    csv_cols = [
        "row_id", "entity", "metric", "raw_value", "raw_unit",
        "censor_operator", "normalized_value", "normalized_unit", "conversion_formula",
        "precision_sigfigs", "value_kind", "normalized_uncertainty",
        "experimental_condition", "temperature", "ph", "source_document_id", "paragraph_id",
        "merged_row_ids", "conflict_status", "applied_rules",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=csv_cols)
        writer.writeheader()
        for r in canonical_rows:
            row_out: dict[str, Any] = {}
            for col in csv_cols:
                val = r.get(col)
                if isinstance(val, list):
                    row_out[col] = ";".join(str(x) for x in val)
                elif val is None:
                    row_out[col] = ""
                else:
                    row_out[col] = val
            writer.writerow(row_out)

    summary_metrics = {
        "raw_record_count": len(raw_records),
        "canonical_record_count": len(canonical_rows),
        "duplicate_merged_count": len(dup_log),
        "context_distinct_preserved_count": len(ctx_preserved_log),
        "scientific_conflict_count": len(conflicts),
        "censored_record_count": sum(1 for r in canonical_rows if r.get("value_kind") == "censored"),
        "missing_record_count": sum(1 for r in canonical_rows if r.get("value_kind") == "missing"),
        "leading_zero_ids_protected_count": len(schema_report["leading_zero_ids_protected"]),
        "human_review_count": len(review_queue),
        "changelog_entry_count": len(changelog),
    }

    audit_bundle = {
        "case_id": case_id,
        "rules_applied": [
            "R01_DYNAMIC_SCHEMA_AND_FIELD_ALIAS",
            "R02_LOCALE_CENSORED_VALUE_PARSE",
            "R03_COMPOUND_UNIT_AND_DELTA_TEMP_GUARD",
            "R04_CONTEXT_LOCKED_DEDUP_AND_CONFLICT_STRATIFICATION",
            "R05_MISSING_AND_UNKNOWN_QUARANTINE",
            "R06_TRACEABLE_EXPORT_AND_REVIEW_REPORT",
        ],
        "schema_probe_report": schema_report,
        "summary": summary_metrics,
        "canonical_records": canonical_rows,
        "duplicate_log": dup_log,
        "context_preserved_log": ctx_preserved_log,
        "scientific_conflicts": conflicts,
        "changelog": changelog,
        "needs_human_review": review_queue,
    }
    bundle_path = output_dir / "cleaning_audit_report.json"
    result_guard.atomic_write_json(bundle_path, audit_bundle)

    report_md = render_cleaning_review_md(audit_bundle)
    (output_dir / "cleaning_review_report.md").write_text(report_md, encoding="utf-8")

    append_audit_entry(
        audit_file,
        phase="clean",
        action="cleaning_pipeline_completed",
        actor="data_cleaning_pipeline.py",
        output=summary_metrics,
        record_id=case_id,
    )

    gaps: list[dict[str, Any]] = []
    if not raw_records:
        gaps.append({"code": "EMPTY_INPUT_RECORDS", "severity": "error", "source": "load_raw_records_and_schema", "message": "No extracted data rows found in input directory."})
    if review_queue:
        gaps.append({"code": "ITEMS_REQUIRING_HUMAN_REVIEW", "severity": "info", "source": "data_cleaning_pipeline", "message": f"{len(review_queue)} record issue(s) routed to needs_human_review without silent coercion."})

    sources_list = [f["file"] for f in schema_report["files_probed"]]
    patch = {
        "artifacts": ["result.json", "cleaned_records.csv", "cleaning_audit_report.json", "cleaning_review_report.md"],
        "sources": sources_list,
        "uncertainty": (
            "Raw records and row_ids remain unmodified. Missing values stay null (never 0), unknown units are uncoerced, "
            "detection-limit operators (<, <=, >, >=) are preserved, delta-temperature never adds 273.15, and same-context "
            "scientific conflicts are preserved without overwriting for human adjudication."
        ),
        "schema_probe_report": schema_report,
        "canonical_records": canonical_rows,
        "duplicate_log": dup_log,
        "context_preserved_log": ctx_preserved_log,
        "scientific_conflicts": conflicts,
        "changelog": changelog,
        "needs_human_review": review_queue,
        "metrics": summary_metrics,
        "metric_provenance": {k: "cleaning_audit_report.json:summary" for k in summary_metrics},
        "findings": [
            f"Probed {len(sources_list)} source(s) dynamically; cleaned {len(raw_records)} row(s) -> {len(canonical_rows)} canonical row(s).",
            f"Context-lock stratification: {len(dup_log)} merged duplicate(s), {len(ctx_preserved_log)} distinct-context row(s) preserved, {len(conflicts)} same-context scientific conflict(s) retained for human review.",
            f"Flagged {len(review_queue)} item(s) in needs_human_review; protected {len(schema_report['leading_zero_ids_protected'])} leading-zero ID(s).",
        ],
        "gaps": gaps,
    }
    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["cleaned_records.csv", "cleaning_audit_report.json", "cleaning_review_report.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor challenge-f4bca185 Literature Data Cleaning Pipeline.")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    args = parser.parse_args(argv)

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"])
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "metrics": res["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
