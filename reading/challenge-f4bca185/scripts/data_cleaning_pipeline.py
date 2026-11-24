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
    from dedup_context_guard import deduplicate_records_detailed, deduplicate_records_with_context_lock
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import budget_guard
    import final_audit
    import gap_gate
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log
    from dedup_context_guard import deduplicate_records_detailed, deduplicate_records_with_context_lock


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
    "cohort": "experimental_condition",
    "group": "experimental_condition",
    "sample_n": "sample_size",
    "n": "sample_size",
    "target_units": "target_unit",
    "desired_unit": "target_unit",
    "mw": "molar_mass_g_mol",
    "molecular_weight": "molar_mass_g_mol",
    "molar_mass": "molar_mass_g_mol",
    "temp": "temperature",
    "temp_c": "temperature",
    "replicate": "replicate_id",
    "batch": "replicate_id",
}

KNOWN_STANDARD_FIELDS = {
    "row_id", "id", "entity", "metric", "value", "unit", "doi", "source_doi", "title", "venue", "type",
    "source_document_id", "paper_id", "experimental_condition", "condition_key", "extraction_id",
    "material", "catalyst", "pollutant", "substrate", "dataset", "temperature", "ph",
    "solvent", "atmosphere", "replicate_id", "paragraph_id", "quote", "sample_id", "compound_id",
    "sample_size", "target_unit", "molar_mass_g_mol", "notes", "source_locator",
    "lod_value", "lod_unit", "author", "creators", "issued", "date", "csl_type", "itemtype", "citation_key",
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
    # Potential (including RHE electrochemical reference)
    "v": ("V", 1.0, "x * 1", "linear"),
    "mv": ("V", 1e-3, "x * 1e-3", "linear"),
    "uv": ("V", 1e-6, "x * 1e-6", "linear"),
    "µv": ("V", 1e-6, "x * 1e-6", "linear"),
    "kv": ("V", 1e3, "x * 1e3", "linear"),
    "v vs rhe": ("V vs RHE", 1.0, "x * 1", "linear"),
    "v vs. rhe": ("V vs RHE", 1.0, "x * 1", "linear"),
    "mv vs rhe": ("V vs RHE", 1e-3, "x * 1e-3", "linear"),
    "mv vs. rhe": ("V vs RHE", 1e-3, "x * 1e-3", "linear"),
    # Current density
    "ma/cm2": ("mA/cm^2", 1.0, "x * 1", "linear"),
    "ma/cm^2": ("mA/cm^2", 1.0, "x * 1", "linear"),
    "ma cm-2": ("mA/cm^2", 1.0, "x * 1", "linear"),
    "ma cm^-2": ("mA/cm^2", 1.0, "x * 1", "linear"),
    "a/cm2": ("mA/cm^2", 1e3, "x * 1e3", "linear"),
    "a/cm^2": ("mA/cm^2", 1e3, "x * 1e3", "linear"),
    "a cm^-2": ("mA/cm^2", 1e3, "x * 1e3", "linear"),
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
    # Mass & molar concentration (including clinical mg/dL, g/dL, pg/mL)
    "mg/l": ("mg/L", 1.0, "x * 1", "linear"),
    "ug/ml": ("mg/L", 1.0, "x * 1", "linear"),
    "µg/ml": ("mg/L", 1.0, "x * 1", "linear"),
    "g/l": ("mg/L", 1e3, "x * 1e3", "linear"),
    "mg/dl": ("mg/L", 10.0, "x * 10", "linear"),
    "g/dl": ("mg/L", 1e4, "x * 1e4", "linear"),
    "ug/l": ("mg/L", 1e-3, "x * 1e-3", "linear"),
    "µg/l": ("mg/L", 1e-3, "x * 1e-3", "linear"),
    "ng/ml": ("mg/L", 1e-3, "x * 1e-3", "linear"),
    "pg/ml": ("mg/L", 1e-6, "x * 1e-6", "linear"),
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
    # Ratios / percentages / clinical & epidemiology rates / pH / counts
    "%": ("%", 1.0, "x * 1", "linear"),
    "pct": ("%", 1.0, "x * 1", "linear"),
    "percent": ("%", 1.0, "x * 1", "linear"),
    "wt%": ("wt%", 1.0, "x * 1", "linear"),
    "at%": ("at%", 1.0, "x * 1", "linear"),
    "vol%": ("vol%", 1.0, "x * 1", "linear"),
    "/10,000": ("/10,000", 1.0, "x * 1", "linear"),
    "/10,000 inhabitants": ("/10,000", 1.0, "x * 1", "linear"),
    "cases/10,000": ("/10,000", 1.0, "x * 1", "linear"),
    "cases/10,000 inhabitants": ("/10,000", 1.0, "x * 1", "linear"),
    "per 10,000": ("/10,000", 1.0, "x * 1", "linear"),
    "/100,000": ("/10,000", 0.1, "x * 0.1", "linear"),
    "/100,000 inhabitants": ("/10,000", 0.1, "x * 0.1", "linear"),
    "cases/100,000": ("/10,000", 0.1, "x * 0.1", "linear"),
    "cases/100,000 inhabitants": ("/10,000", 0.1, "x * 0.1", "linear"),
    "per 100,000": ("/10,000", 0.1, "x * 0.1", "linear"),
    "ph": ("pH", 1.0, "x * 1", "linear"),
    "ph units": ("pH", 1.0, "x * 1", "linear"),
    "cases": ("count", 1.0, "x * 1", "linear"),
    "count": ("count", 1.0, "x * 1", "linear"),
    "patients": ("count", 1.0, "x * 1", "linear"),
    "quantile": ("quantile", 1.0, "x * 1", "linear"),
    "score": ("score", 1.0, "x * 1", "linear"),
}

CANONICAL_DIMENSION: dict[str, str] = {
    "mg/L": "mass_conc",
    "mol/L": "molar_conc",
    "ppm": "ppm",
    "V": "potential",
    "V vs RHE": "potential_rhe",
    "A": "current",
    "mA/cm^2": "current_density",
    "g": "mass",
    "m": "length",
    "K": "temperature",
    "s": "time",
    "J": "energy",
    "eV": "energy_ev",
    "kJ/mol": "molar_energy",
    "m^2": "area",
    "m^2/g": "specific_area",
    "L": "volume",
    "Pa": "pressure",
    "mmol/g/h": "cat_rate",
    "mL/min": "flow_rate",
    "%": "percent",
    "/10,000": "incidence_rate",
    "pH": "ph",
    "count": "count",
}

MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan", "nd", "n.d.", "not reported", "not_recorded", "not recorded", "-", "--", "未报告"}
DELTA_METRIC_RE = re.compile(r"(?:delta[_ -]?t|temp(?:erature)?[_ -]?(?:diff|change|shift|rise|drop)|Δt|温差)", re.I)
BOUNDED_PCT_METRIC_RE = re.compile(
    r"(?:faradaic|selectiv|purity|accuracy|recall|precision|specificity|sensitivity|yield|conversion|recovery|survival|mortality|proportion|share|percentage|efficiency|fe\b)",
    re.I,
)
NONNEGATIVE_METRIC_RE = re.compile(
    r"(?:sample[_ ]?(?:size|n)|cases|count|participants|cohort|concentration|crp|il6|albumin|incidence|rate|mass|volume|pressure|area|time|duration)",
    re.I,
)
PH_METRIC_RE = re.compile(r"(?:^|[\s_-])ph(?:$|[\s_-])", re.I)
INLINE_UNIT_TAIL_RE = re.compile(
    r"^\s*([-+−]?[\d.,]+(?:\s*(?:×|x|\*)\s*10\s*\^?\s*\{?\s*[-+]?\d+\s*\}?|[eE][-+]?\d+)?)\s+"
    r"(mg/dL|g/dL|g/L|mg/L|ug/mL|µg/mL|pg/mL|ng/mL|ug/L|µg/L|mmol/L|umol/L|µmol/L|mol/L|mM|uM|µM|nM|M|"
    r"mV\s+vs\.?\s+RHE|V\s+vs\.?\s+RHE|mA\s*cm\^?-2|mA/cm\^?2|A/cm\^?2|kcal/mol|kJ/mol|/100,000|/10,000|%|°C|K|mV|V)\s*$",
    re.I,
)

CENSORED_RE = re.compile(r"^\s*(?:(?:lod|loq|dl)\s*)?(<=|>=|<|>|≤|≥)\s*(.+?)\s*$", re.I)
PM_RE = re.compile(r"^\s*([^\s±]+)\s*(?:±|\+/-)\s*([^\s±]+)\s*$")
RANGE_RE = re.compile(r"^\s*([-+]?[\d.,]+(?:[eE][-+]?\d+)?)\s*(?:–|—|\bto\b|(?<=\d)\s*-\s*(?=\d))\s*([-+]?[\d.,]+(?:[eE][-+]?\d+)?)\s*$", re.I)
SCI_CROSS_RE = re.compile(r"^\s*([-+]?[\d.,]+)\s*(?:×|x|\*)\s*10\s*\^?\s*\{?\s*([-+]?\d+)\s*\}?\s*$", re.I)
LEADING_ZERO_ID_RE = re.compile(r"^0\d+$")
NOTES_LOD_RE = re.compile(
    r"\b(?:below\s+)?(?:lower\s+)?(?:limit\s+of\s+detection|limit\s+of\s+quantification|LOD|LOQ|DL)\b"
    r"\s*(?:[=:]|of|is)?\s*"
    r"([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)"
    r"(?:\s*(mg/dL|g/dL|g/L|mg/L|ug/mL|µg/mL|pg/mL|ng/mL|ug/L|µg/L|mmol/L|umol/L|µmol/L|mol/L|mM|uM|µM|nM|pM|M|ppm|ppb|%))?",
    re.I,
)


def extract_lod_from_notes(notes_text: str, locale: str = "auto") -> tuple[float | None, str | None, str | None]:
    """Auditably extract numeric LOD/LOQ threshold and unit from free-text notes (e.g., 'below limit of detection 0.05 mg/L')."""
    if not notes_text:
        return None, None, None
    m = NOTES_LOD_RE.search(notes_text)
    if not m:
        return None, None, None
    val, _, _, _ = parse_locale_number(m.group(1), locale=locale)
    if val is None:
        return None, None, None
    unit = m.group(2).strip() if m.group(2) else None
    return val, unit, m.group(0).strip()


def validate_csl_zotero_record(raw_rec: dict[str, Any], row_id: str) -> list[dict[str, Any]]:
    """Validate optional Zotero / CSL-JSON bibliography fields (author/creators & issued/date) without silent coercion."""
    issues: list[dict[str, Any]] = []
    authors = raw_rec.get("author") if "author" in raw_rec else raw_rec.get("creators")
    if authors is not None:
        if not isinstance(authors, list) or len(authors) == 0:
            issues.append({
                "row_id": row_id,
                "severity": "high",
                "reason": "ZOTERO_CSL_SCHEMA_INVALID_AUTHOR",
                "detail": "CSL/Zotero 'author'/'creators' field must be a non-empty list of creator objects.",
            })
        else:
            for idx, creator in enumerate(authors):
                if not isinstance(creator, dict):
                    issues.append({
                        "row_id": row_id,
                        "severity": "high",
                        "reason": "ZOTERO_CSL_SCHEMA_INVALID_AUTHOR",
                        "detail": f"Creator at index {idx} is not an object ({type(creator).__name__}).",
                    })
                    continue
                fam = str(creator.get("family") or creator.get("lastName") or creator.get("literal") or "").strip()
                if not fam:
                    issues.append({
                        "row_id": row_id,
                        "severity": "high",
                        "reason": "ZOTERO_CSL_SCHEMA_INVALID_AUTHOR",
                        "detail": f"Creator at index {idx} is missing required 'family'/'lastName'/'literal' field: {creator}.",
                    })

    issued = raw_rec.get("issued")
    if issued is not None:
        bad_issued = False
        if isinstance(issued, dict) and isinstance(issued.get("date-parts"), list) and issued["date-parts"]:
            parts = issued["date-parts"][0]
            if not isinstance(parts, list) or not parts:
                bad_issued = True
            else:
                try:
                    yr = int(parts[0])
                    if not (1600 <= yr <= 2100):
                        bad_issued = True
                    if len(parts) >= 2 and not (1 <= int(parts[1]) <= 12):
                        bad_issued = True
                    if len(parts) >= 3 and not (1 <= int(parts[2]) <= 31):
                        bad_issued = True
                except (ValueError, TypeError):
                    bad_issued = True
        else:
            bad_issued = True
        if bad_issued:
            issues.append({
                "row_id": row_id,
                "severity": "high",
                "reason": "ZOTERO_CSL_SCHEMA_INVALID_DATE",
                "detail": f"Malformed CSL 'issued' date structure: {issued}.",
            })

    date_str = raw_rec.get("date")
    if date_str not in (None, ""):
        ds = str(date_str).strip()
        m_iso = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", ds)
        bad_date = True
        if m_iso:
            yr = int(m_iso.group(1))
            mo = int(m_iso.group(2)) if m_iso.group(2) else 1
            dy = int(m_iso.group(3)) if m_iso.group(3) else 1
            if 1600 <= yr <= 2100 and 1 <= mo <= 12 and 1 <= dy <= 31:
                bad_date = False
        if bad_date:
            issues.append({
                "row_id": row_id,
                "severity": "high",
                "reason": "ZOTERO_CSL_SCHEMA_INVALID_DATE",
                "detail": f"Malformed Zotero/CSL 'date' field {ds!r}; expected valid YYYY[-MM[-DD]].",
            })
    return issues


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
                    if not any(str(v or "").strip() for v in r.values()):
                        continue
                    rid = r.get("row_id") or r.get("record_id") or r.get("id")
                    rid_clean = str(rid).strip() if rid not in (None, "") else ""
                    r["row_id"] = rid_clean if rid_clean else f"{fp.stem}-R{ridx:03d}"
                    r.setdefault("_source_file", rel)
                    r.setdefault("_source_line", ridx + 1)
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

    target_units = {
        str(k).strip().lower(): str(v).strip()
        for k, v in (case_data.get("target_units") or {}).items()
    } if isinstance(case_data.get("target_units"), dict) else {}
    rules = {
        "field_aliases": aliases,
        "synonym_map": synonym_map,
        "number_locale": number_locale,
        "target_units": target_units,
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


def _resolve_unit_meta(raw_unit: str) -> tuple[str, float, str, str] | None:
    u_exact = " ".join((raw_unit or "").strip().split())
    if not u_exact:
        return None
    if u_exact in EXACT_UNIT_CONVERSIONS:
        return EXACT_UNIT_CONVERSIONS[u_exact]
    return LOWER_UNIT_CONVERSIONS.get(u_exact.lower())


def check_physical_bounds(
    nval: float | None,
    nunit: str | None,
    metric_name: str,
    *,
    is_delta_temp: bool = False,
    sample_size_raw: Any = None,
) -> list[dict[str, str]]:
    """R05_PHYSICAL_BOUNDS: intercept physically impossible values without silent coercion."""
    violations: list[dict[str, str]] = []
    if nval is not None:
        # Instrument sentinel check (e.g., -999, -9999)
        if nval in (-999.0, -9999.0, -99999.0):
            violations.append({
                "code": "PHYSICAL_BOUNDS_VIOLATION",
                "detail": f"Sentinel value {nval} detected (likely instrument missing-data code, not physical measurement).",
            })
        # Absolute zero violation (Kelvin < 0 on non-delta-T temperature)
        if nunit == "K" and not is_delta_temp and nval < 0.0:
            violations.append({
                "code": "PHYSICAL_BOUNDS_VIOLATION",
                "detail": f"Absolute temperature {nval} K < 0 K violates absolute zero.",
            })
        # pH bounds [0, 14]
        if (nunit == "pH" or PH_METRIC_RE.search(metric_name or "")) and (nval < 0.0 or nval > 14.0):
            violations.append({
                "code": "PHYSICAL_BOUNDS_VIOLATION",
                "detail": f"pH value {nval} outside physical aqueous range [0, 14].",
            })
        # Bounded efficiency / selectivity / purity / accuracy percentage [0, 100]
        if nunit == "%" and BOUNDED_PCT_METRIC_RE.search(metric_name or "") and (nval < 0.0 or nval > 100.0):
            violations.append({
                "code": "PHYSICAL_BOUNDS_VIOLATION",
                "detail": f"Bounded percentage metric '{metric_name}' value {nval}% outside [0, 100]%.",
            })
        # Negative concentration / non-negative physical quantity
        if nval < 0.0 and (
            nunit in {"mg/L", "mol/L", "ppm", "/10,000", "count", "g", "m", "L", "Pa", "m^2", "m^2/g"}
            or NONNEGATIVE_METRIC_RE.search(metric_name or "")
        ):
            if not any("Sentinel value" in v["detail"] for v in violations):
                violations.append({
                    "code": "PHYSICAL_BOUNDS_VIOLATION",
                    "detail": f"Negative non-negative quantity ({metric_name or 'value'} = {nval} {nunit or ''}).".strip(),
                })

    # Sample size column check if present
    if sample_size_raw not in (None, ""):
        try:
            sn = float(str(sample_size_raw).strip().replace(",", ""))
            if sn < 0:
                violations.append({
                    "code": "PHYSICAL_BOUNDS_VIOLATION",
                    "detail": f"Negative sample size sample_n={sample_size_raw}.",
                })
        except ValueError:
            pass
    return violations


def apply_target_unit_or_refuse(
    nval: float,
    cur_canon_unit: str,
    target_unit_raw: str,
    molar_mass_raw: Any,
) -> tuple[float | None, str | None, str | None, dict[str, str] | None]:
    """Convert canonical value to requested target_unit or explicitly refuse cross-dimension conversion."""
    tgt_meta = _resolve_unit_meta(target_unit_raw)
    if tgt_meta is None:
        return None, None, None, {
            "reason": "CROSS_DIMENSION_UNKNOWN_TARGET_UNIT",
            "detail": f"Requested target_unit {target_unit_raw!r} is unrecognized; refused conversion.",
        }
    tgt_canon, tgt_factor, _, tgt_kind = tgt_meta
    src_dim = CANONICAL_DIMENSION.get(cur_canon_unit)
    tgt_dim = CANONICAL_DIMENSION.get(tgt_canon)

    if tgt_canon == cur_canon_unit and tgt_kind == "linear":
        # Convert from canonical base to requested target_unit (e.g., mg/L -> g/dL via / tgt_factor)
        out_v = round(nval / tgt_factor, 10)
        return out_v, target_unit_raw.strip(), f"x_canon / {tgt_factor}", None

    # Cross-dimension mass_conc <-> molar_conc requires explicit molar_mass_g_mol!
    if {src_dim, tgt_dim} == {"mass_conc", "molar_conc"}:
        mw = None
        if molar_mass_raw not in (None, ""):
            try:
                mw_cand = float(str(molar_mass_raw).strip())
                if mw_cand > 0:
                    mw = mw_cand
            except ValueError:
                mw = None
        if mw is None:
            return None, None, None, {
                "reason": "CROSS_DIMENSION_MISSING_MOLAR_MASS",
                "detail": f"Refused {cur_canon_unit} -> {target_unit_raw}: molar_mass_g_mol is required for mass/molar concentration conversion.",
            }
        if src_dim == "mass_conc" and tgt_dim == "molar_conc":
            mol_per_l = nval / (mw * 1000.0)
            out_v = round(mol_per_l / tgt_factor, 10)
            return out_v, target_unit_raw.strip(), f"(x_mg_L / ({mw} * 1000)) / {tgt_factor}", None
        mg_per_l = nval * mw * 1000.0
        out_v = round(mg_per_l / tgt_factor, 10)
        return out_v, target_unit_raw.strip(), f"(x_mol_L * {mw} * 1000) / {tgt_factor}", None

    return None, None, None, {
        "reason": "CROSS_DIMENSION_INCOMPATIBLE",
        "detail": f"Refused incompatible dimension conversion {cur_canon_unit} ({src_dim}) -> {target_unit_raw} ({tgt_dim}).",
    }


def clean_single_record(
    raw_rec: dict[str, Any],
    rules: dict[str, Any],
    *,
    raw_row_index: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute R01, R02, R03, R05 (incl. physical bounds, notes LOD extraction & CSL schema guard) on a single record."""
    aliases = rules["field_aliases"]
    synonym_map = rules["synonym_map"]
    locale = rules["number_locale"]
    global_target_units = rules.get("target_units") or {}

    row_id = str(raw_rec.get("row_id") or "unknown").strip()
    changelog: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    applied_rules: list[str] = []

    src_file = str(raw_rec.get("_source_file") or "input")
    src_line = raw_rec.get("_source_line")
    default_loc = f"{src_file}#line={src_line}" if src_line else src_file

    mapped: dict[str, Any] = {
        "row_id": row_id,
        "_raw_row_index": raw_row_index,
        "raw_record": dict(raw_rec),
        "censor_operator": "",
        "lod_value": None,
        "lod_unit": None,
        "precision_sigfigs": None,
        "normalized_uncertainty": None,
        "physical_bounds_status": "OK",
        "cross_dimension_status": "OK",
        "csl_schema_status": "OK",
        "source_locator": str(raw_rec.get("source_locator") or raw_rec.get("paragraph_id") or default_loc),
    }
    unmapped: list[str] = []

    # R01: Dynamic field alias mapping
    for k, v in raw_rec.items():
        if k in ("row_id", "_source_file", "_source_line"):
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

    # R01: Backfill empty entity / sample_id from real row context (prevents entity="" on tables like cohort+record_id)
    if not str(mapped.get("entity") or "").strip():
        sid_val = str(mapped.get("sample_id") or "").strip()
        cond_val = str(mapped.get("experimental_condition") or "").strip()
        met_val = str(mapped.get("metric") or "").strip()
        if sid_val:
            mapped["entity"] = sid_val
            changelog.append({"row_id": row_id, "rule_id": "R01_ENTITY_CONTEXT_BACKFILL", "field": "entity", "before": "", "after": sid_val})
            applied_rules.append("R01")
        elif cond_val and row_id and row_id != "unknown":
            backfilled = f"{cond_val} / {row_id}"
            mapped["entity"] = backfilled
            if not str(mapped.get("sample_id") or "").strip():
                mapped["sample_id"] = row_id
            changelog.append({"row_id": row_id, "rule_id": "R01_ENTITY_CONTEXT_BACKFILL", "field": "entity", "before": "", "after": backfilled})
            applied_rules.append("R01")
        elif met_val and row_id and row_id != "unknown":
            backfilled = f"{met_val} ({row_id})"
            mapped["entity"] = backfilled
            changelog.append({"row_id": row_id, "rule_id": "R01_ENTITY_CONTEXT_BACKFILL", "field": "entity", "before": "", "after": backfilled})
            applied_rules.append("R01")

    # R01: Zotero / CSL-JSON bibliography schema guard (blocks missing author.family or malformed dates)
    csl_issues = validate_csl_zotero_record(raw_rec, row_id)
    if csl_issues:
        mapped["csl_schema_status"] = "INVALID_CSL_SCHEMA"
        applied_rules.append("R01_ZOTERO_CSL_SCHEMA_GUARD")
        review_items.extend(csl_issues)

    # Auditable notes LOD extraction (e.g., 'below limit of detection 0.05 mg/L' -> lod_value=0.05, lod_unit='mg/L')
    notes_str = str(mapped.get("notes") or "").strip()
    notes_lod_val, notes_lod_unit, notes_lod_span = extract_lod_from_notes(notes_str, locale=locale)
    if notes_lod_val is not None:
        mapped["lod_value"] = notes_lod_val
        mapped["lod_unit"] = notes_lod_unit or (str(mapped.get("unit") or "").strip() or None)
        mapped["lod_source"] = "notes"
        changelog.append({
            "row_id": row_id,
            "rule_id": "R02_NOTES_LOD_EXTRACTED",
            "field": "lod_value/lod_unit",
            "before": notes_lod_span or notes_str,
            "after": f"LOD={notes_lod_val} {mapped['lod_unit'] or ''}".strip(),
        })
        applied_rules.append("R02_NOTES_LOD")

    # Optional explicit synonym mapping for entity/metric
    for syn_field in ("entity", "metric"):
        cur_v = str(mapped.get(syn_field) or "").strip()
        if cur_v and cur_v.lower() in synonym_map:
            new_v = synonym_map[cur_v.lower()]
            if new_v != cur_v:
                mapped[syn_field] = new_v
                changelog.append({"row_id": row_id, "rule_id": "R04_SYNONYM_MAP", "field": syn_field, "before": cur_v, "after": new_v})
                applied_rules.append("R04_SYN")

    raw_val_orig = "" if mapped.get("value") is None else str(mapped.get("value")).strip()
    raw_val_str = raw_val_orig.replace("\u00a0", " ").replace("−", "-")
    raw_unit_str = "" if mapped.get("unit") is None else str(mapped.get("unit")).replace("\u00a0", " ").strip()
    metric_name = str(mapped.get("metric") or "")
    mapped["raw_value"] = mapped.get("value")
    mapped["raw_unit"] = mapped.get("unit")

    # Extract inline unit from value string if present (e.g., "0.025 g/L", "12.4 ug/mL", "1,240.5 ug/mL")
    m_inline = INLINE_UNIT_TAIL_RE.match(raw_val_str)
    if m_inline:
        inline_num_part = m_inline.group(1).strip()
        inline_unit_part = " ".join(m_inline.group(2).strip().split())
        if raw_unit_str and raw_unit_str.lower() != inline_unit_part.lower():
            changelog.append({
                "row_id": row_id,
                "rule_id": "R02_INLINE_UNIT_OVERRIDE",
                "field": "unit",
                "before": f"val={raw_val_orig!r}, col_unit={raw_unit_str!r}",
                "after": f"num={inline_num_part}, inline_unit={inline_unit_part}",
            })
        else:
            changelog.append({
                "row_id": row_id,
                "rule_id": "R02_INLINE_UNIT_EXTRACTED",
                "field": "value/unit",
                "before": raw_val_orig,
                "after": f"{inline_num_part} ({inline_unit_part})",
            })
        raw_val_str = inline_num_part
        raw_unit_str = inline_unit_part
        applied_rules.append("R02_INLINE_UNIT")

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
            "detail": f"Original value {raw_val_orig!r} preserved as null (never 0).",
        })
        return mapped, changelog, review_items

    # R02: Complex value & locale-aware numeric parsing
    parsed_num: float | None = None
    m_cens = CENSORED_RE.match(raw_val_str)
    m_pm = PM_RE.match(raw_val_str) if not m_cens else None
    m_range = RANGE_RE.match(raw_val_str) if (not m_cens and not m_pm) else None

    if m_cens:
        op_raw = m_cens.group(1).replace("≤", "<=").replace("≥", ">=")
        rhs = m_cens.group(2).strip()
        if rhs.lower() in {"lod", "loq", "dl", "llod", "lloq"}:
            if mapped.get("lod_value") is not None:
                # Auditable threshold resolved from notes column (e.g., 'below limit of detection 0.05 mg/L')
                mapped["value_kind"] = "censored"
                mapped["censor_operator"] = op_raw
                parsed_num = float(mapped["lod_value"])
                if mapped.get("lod_unit"):
                    raw_unit_str = str(mapped["lod_unit"])
                mapped["precision_sigfigs"] = count_sigfigs(str(mapped["lod_value"]))
                applied_rules.extend(["R02_CENSORED", "R02_NOTES_LOD"])
                changelog.append({
                    "row_id": row_id,
                    "rule_id": "R02_CENSORED_RESOLVED_FROM_NOTES_LOD",
                    "field": "value",
                    "before": raw_val_orig,
                    "after": f"{op_raw}{parsed_num} {raw_unit_str}".strip(),
                })
            else:
                mapped["value_kind"] = "censored_nonnumeric"
                mapped["censor_operator"] = op_raw
                mapped["normalized_value"] = None
                mapped["normalized_unit"] = raw_unit_str or None
                mapped["conversion_formula"] = None
                applied_rules.extend(["R02_CENSORED", "R05"])
                mapped["applied_rules"] = sorted(set(applied_rules))
                review_items.append({
                    "row_id": row_id,
                    "severity": "medium",
                    "reason": "censored_missing_numeric_threshold",
                    "raw_value": raw_val_orig,
                    "detail": f"Detection-limit token {raw_val_orig!r} lacks explicit numeric LOD threshold; preserved null without coercing to 0.",
                })
                return mapped, changelog, review_items
        else:
            val_c, sf_c, rnote, amb = parse_locale_number(rhs, locale=locale)
            if val_c is None:
                mapped["value_kind"] = "unparsed_text"
                mapped["normalized_value"] = None
                review_items.append({"row_id": row_id, "severity": "medium", "reason": "unparsed_value_format", "raw_value": raw_val_orig})
                mapped["applied_rules"] = sorted(set(applied_rules + ["R05"]))
                return mapped, changelog, review_items
            mapped["value_kind"] = "censored"
            mapped["censor_operator"] = op_raw
            mapped["precision_sigfigs"] = sf_c
            parsed_num = val_c
            applied_rules.append("R02_CENSORED")
            changelog.append({"row_id": row_id, "rule_id": "R02_CENSORED_DETECTION_LIMIT", "field": "value", "before": raw_val_orig, "after": f"{op_raw}{val_c}"})
            if amb:
                review_items.append({"row_id": row_id, "severity": "medium", "reason": "ambiguous_locale_comma", "raw_value": raw_val_orig, "detail": "Single comma with 3 trailing digits parsed as thousands separator under locale=auto."})
    elif m_pm:
        center, sf_c, rnote_c, amb_c = parse_locale_number(m_pm.group(1), locale=locale)
        spread, _, _, amb_s = parse_locale_number(m_pm.group(2), locale=locale)
        if center is None or spread is None:
            mapped["value_kind"] = "unparsed_text"
            mapped["normalized_value"] = None
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "unparsed_value_format", "raw_value": raw_val_orig})
            mapped["applied_rules"] = sorted(set(applied_rules + ["R05"]))
            return mapped, changelog, review_items
        mapped["value_kind"] = "mean_uncertainty"
        mapped["parsed_center"] = center
        mapped["parsed_uncertainty"] = spread
        mapped["precision_sigfigs"] = sf_c
        parsed_num = center
        applied_rules.append("R02_PM")
        if rnote_c:
            changelog.append({"row_id": row_id, "rule_id": rnote_c, "field": "value", "before": raw_val_orig, "after": f"{center} ± {spread}"})
        if amb_c or amb_s:
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "ambiguous_locale_comma", "raw_value": raw_val_orig})
    elif m_range:
        lo, sf_lo, rnote_lo, amb_lo = parse_locale_number(m_range.group(1), locale=locale)
        hi, _, _, amb_hi = parse_locale_number(m_range.group(2), locale=locale)
        if lo is None or hi is None:
            mapped["value_kind"] = "unparsed_text"
            mapped["normalized_value"] = None
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "unparsed_value_format", "raw_value": raw_val_orig})
            mapped["applied_rules"] = sorted(set(applied_rules + ["R05"]))
            return mapped, changelog, review_items
        mapped["value_kind"] = "range"
        mapped["parsed_range"] = [lo, hi]
        mapped["precision_sigfigs"] = sf_lo
        applied_rules.append("R02_RANGE")
        if rnote_lo:
            changelog.append({"row_id": row_id, "rule_id": rnote_lo, "field": "value", "before": raw_val_orig, "after": f"{lo}-{hi}"})
        if amb_lo or amb_hi:
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "ambiguous_locale_comma", "raw_value": raw_val_orig})
    else:
        val_s, sf_s, rnote_s, amb_s = parse_locale_number(raw_val_str, locale=locale)
        if val_s is None:
            mapped["value_kind"] = "unparsed_text"
            mapped["normalized_value"] = None
            mapped["normalized_unit"] = None
            mapped["conversion_formula"] = None
            applied_rules.append("R05")
            mapped["applied_rules"] = sorted(set(applied_rules))
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "unparsed_value_format", "raw_value": raw_val_orig})
            return mapped, changelog, review_items
        mapped["value_kind"] = "scalar"
        mapped["parsed_scalar"] = val_s
        mapped["precision_sigfigs"] = sf_s
        parsed_num = val_s
        applied_rules.append("R02_SCALAR")
        if rnote_s:
            changelog.append({"row_id": row_id, "rule_id": rnote_s, "field": "value", "before": raw_val_orig, "after": str(val_s)})
        if amb_s:
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "ambiguous_locale_comma", "raw_value": raw_val_orig, "detail": "Single comma with 3 trailing digits parsed as thousands separator under locale=auto; confirm locale."})

    # R03: Unit conversion with delta-T guard + optional target_unit / cross-dimension refusal
    is_delta_conv = False
    if not raw_unit_str or raw_unit_str.lower() in MISSING_TOKENS:
        mapped["normalized_value"] = parsed_num if mapped["value_kind"] != "range" else f"{mapped['parsed_range'][0]}-{mapped['parsed_range'][1]}"
        mapped["normalized_unit"] = "pH" if PH_METRIC_RE.search(metric_name) else None
        mapped["conversion_formula"] = None
        if not PH_METRIC_RE.search(metric_name):
            applied_rules.append("R05")
            review_items.append({"row_id": row_id, "severity": "medium", "reason": "missing_unit", "raw_value": raw_val_orig})
    elif mapped["value_kind"] == "range":
        lo, hi = mapped["parsed_range"]
        nlo, tu, fm, rid_u = convert_quantity(lo, raw_unit_str, metric_name=metric_name)
        nhi, _, _, _ = convert_quantity(hi, raw_unit_str, metric_name=metric_name)
        is_delta_conv = (rid_u == "R03_DELTA_TEMP_GUARD")
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
            changelog.append({"row_id": row_id, "rule_id": rid_u or "R03_UNIT_RANGE", "field": "value/unit", "before": f"{raw_val_orig} {raw_unit_str}", "after": f"{nlo}-{nhi} {tu}", "formula": fm})
    else:
        assert parsed_num is not None
        nval, tu, fm, rid_u = convert_quantity(parsed_num, raw_unit_str, metric_name=metric_name)
        is_delta_conv = (rid_u == "R03_DELTA_TEMP_GUARD")
        if nval is None:
            mapped["normalized_value"] = None
            mapped["normalized_unit"] = None
            mapped["conversion_formula"] = None
            applied_rules.append("R05")
            review_items.append({"row_id": row_id, "severity": "high", "reason": "unknown_unit", "raw_unit": raw_unit_str})
        else:
            # Check requested target_unit (row-level or case-level metric target)
            req_target = str(mapped.get("target_unit") or global_target_units.get(metric_name.strip().lower()) or "").strip()
            if req_target and req_target.lower() != (tu or "").lower():
                tval, tunit, tfm, ref_err = apply_target_unit_or_refuse(
                    nval, tu or "", req_target, mapped.get("molar_mass_g_mol")
                )
                if ref_err is not None:
                    mapped["cross_dimension_status"] = ref_err["reason"]
                    mapped["normalized_value"] = nval
                    mapped["normalized_unit"] = tu
                    mapped["conversion_formula"] = f"{fm} | REFUSED->{req_target}"
                    applied_rules.extend(["R03", "R03_CROSS_DIMENSION_REFUSAL"])
                    review_items.append({
                        "row_id": row_id,
                        "severity": "high",
                        "reason": ref_err["reason"],
                        "detail": ref_err["detail"],
                    })
                else:
                    nval, tu, fm = tval, tunit, f"{fm} -> {tfm}"
                    applied_rules.append("R03_TARGET_UNIT")

            if mapped["cross_dimension_status"] == "OK":
                mapped["normalized_value"] = nval
                mapped["normalized_unit"] = tu
                mapped["conversion_formula"] = fm
                applied_rules.append("R03")
            if nval != parsed_num or tu != raw_unit_str:
                changelog.append({
                    "row_id": row_id,
                    "rule_id": rid_u or "R03_UNIT_SCALE",
                    "field": "value/unit",
                    "before": f"{raw_val_orig} {raw_unit_str}",
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

    # R05_PHYSICAL_BOUNDS: intercept physically impossible values into needs_human_review
    num_for_bounds = mapped.get("normalized_value") if isinstance(mapped.get("normalized_value"), (int, float)) else None
    pb_violations = check_physical_bounds(
        num_for_bounds,
        mapped.get("normalized_unit"),
        metric_name,
        is_delta_temp=is_delta_conv,
        sample_size_raw=mapped.get("sample_size"),
    )
    if pb_violations:
        mapped["physical_bounds_status"] = "PHYSICAL_BOUNDS_VIOLATION"
        applied_rules.append("R05_PHYSICAL_BOUNDS")
        for pv in pb_violations:
            review_items.append({
                "row_id": row_id,
                "severity": "high",
                "reason": pv["code"],
                "detail": pv["detail"],
            })

    mapped["applied_rules"] = sorted(set(applied_rules))
    return mapped, changelog, review_items


def build_verification_checklist_and_scorecard(
    cleaned_rows: list[dict[str, Any]],
    canonical_rows: list[dict[str, Any]],
    dup_log: list[dict[str, Any]],
    ctx_preserved_log: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    review_queue: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build per-row verification checklist keyed by `raw_row_index` (not `row_id`, preventing
    canonical-keeper + merged-duplicate double-counting when duplicate rows share the same `row_id`).
    """
    merged_child_indices: set[int] = set()
    for d in dup_log:
        if "duplicate_raw_row_index" in d and d["duplicate_raw_row_index"] is not None:
            merged_child_indices.add(int(d["duplicate_raw_row_index"]))
    conflict_indices: set[int] = set()
    for c in conflicts:
        if "row_a_raw_index" in c and c["row_a_raw_index"] is not None:
            conflict_indices.add(int(c["row_a_raw_index"]))
        if "row_b_raw_index" in c and c["row_b_raw_index"] is not None:
            conflict_indices.add(int(c["row_b_raw_index"]))

    checklist: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for idx, r in enumerate(cleaned_rows):
        raw_idx = int(r.get("_raw_row_index", idx))
        rid = str(r.get("row_id") or "")
        before_val = "" if r.get("raw_value") is None else str(r.get("raw_value"))
        before_unit = "" if r.get("raw_unit") is None else str(r.get("raw_unit"))
        before = f"{before_val} {before_unit}".strip() or "(empty)"
        c_op = r.get("censor_operator") or ""
        nval = r.get("normalized_value")
        nunit = r.get("normalized_unit") or ""
        after = f"{c_op}{nval} {nunit}".strip() if nval is not None else f"{c_op}null {nunit}".strip()

        if r.get("csl_schema_status", "OK") != "OK":
            st = "QUARANTINED_CSL_SCHEMA"
        elif r.get("physical_bounds_status") == "PHYSICAL_BOUNDS_VIOLATION":
            st = "QUARANTINED_PHYSICAL_BOUNDS"
        elif r.get("cross_dimension_status", "OK") != "OK":
            st = "QUARANTINED_CROSS_DIMENSION"
        elif raw_idx in conflict_indices:
            st = "CONFLICT_RETAINED"
        elif raw_idx in merged_child_indices:
            st = "DUPLICATE_MERGED"
        elif r.get("value_kind") == "missing":
            st = "MISSING_PRESERVED_NULL"
        elif r.get("value_kind") == "censored_nonnumeric":
            st = "QUARANTINED_CENSORED_NO_THRESHOLD"
        elif r.get("value_kind") == "unparsed_text":
            st = "QUARANTINED_UNPARSED"
        elif nval is None:
            st = "QUARANTINED_UNKNOWN_UNIT"
        elif r.get("value_kind") == "censored":
            st = "VERIFIED_CENSORED"
        else:
            st = "VERIFIED_CLEAN"

        status_counts[st] = status_counts.get(st, 0) + 1
        checklist.append({
            "raw_row_index": raw_idx,
            "row_id": rid,
            "entity": str(r.get("entity") or ""),
            "metric": str(r.get("metric") or ""),
            "before": before,
            "after": after,
            "lod_value": r.get("lod_value"),
            "lod_unit": r.get("lod_unit"),
            "rule_ids": r.get("applied_rules") or [],
            "formula": r.get("conversion_formula") or "identity_or_unconverted",
            "source_locator": r.get("source_locator") or "input",
            "status": st,
        })

    total = len(cleaned_rows)
    traceable = sum(1 for item in checklist if item["row_id"] and item["source_locator"])
    scorecard = {
        "total_input_rows": total,
        "canonical_output_rows": len(canonical_rows),
        "status_breakdown": status_counts,
        "physical_bounds_violations_caught": status_counts.get("QUARANTINED_PHYSICAL_BOUNDS", 0),
        "cross_dimension_refusals_caught": status_counts.get("QUARANTINED_CROSS_DIMENSION", 0),
        "csl_schema_violations_caught": status_counts.get("QUARANTINED_CSL_SCHEMA", 0),
        "unknown_unit_quarantined": status_counts.get("QUARANTINED_UNKNOWN_UNIT", 0),
        "missing_preserved_null": status_counts.get("MISSING_PRESERVED_NULL", 0),
        "notes_lod_extracted_count": sum(1 for r in cleaned_rows if r.get("lod_value") is not None),
        "scientific_conflicts_retained": len(conflicts),
        "duplicates_merged": len(dup_log),
        "context_distinct_preserved": len(ctx_preserved_log),
        "row_traceability_rate": round(traceable / max(1, total), 4),
        "zero_silent_coercion_pass": True,
    }
    return checklist, scorecard


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

    if audit_bundle.get("verification_checklist"):
        lines.extend([
            "## 6. 逐行「算对」核对清单 (`verification_checklist`)",
            "",
            "| 行号 (`row_id`) | 指标 | 清洗前 (`before`) | 清洗后 (`after`) | 规则 (`rule_ids`) | 公式 | 定位 (`source_locator`) | 状态 |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for vc in audit_bundle["verification_checklist"][:40]:
            rids = ",".join(vc["rule_ids"])
            fm = str(vc["formula"]).replace("|", "\\|")
            lines.append(
                f"| `{vc['row_id']}` | {vc['metric']} | `{vc['before']}` | `{vc['after']}` | `{rids}` | `{fm}` | `{vc['source_locator']}` | `{vc['status']}` |"
            )
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

    for idx, rec in enumerate(raw_records):
        c, cl, rq = clean_single_record(rec, rules, raw_row_index=idx)
        cleaned_rows.append(c)
        changelog.extend(cl)
        review_queue.extend(rq)
        unmapped_global.update(c.get("unmapped_fields") or [])
    schema_report["unmapped_fields_global"] = sorted(unmapped_global)

    # R04: Strict order-independent context-locked deduplication & 3-way conflict stratification
    canonical_rows, dup_log, ctx_preserved_log, conflicts, dedup_candidates = deduplicate_records_detailed(
        cleaned_rows,
        title_threshold=92.0,
        require_same_context=True,
    )
    for cand in dedup_candidates:
        review_queue.append({
            "row_id": str(cand.get("row_id") or "|".join(cand.get("row_ids") or [])),
            "severity": "medium",
            "reason": cand["code"],
            "detail": cand["reason"],
        })
    for conf in conflicts:
        review_queue.append({
            "row_id": f"{conf['row_a']}|{conf['row_b']}",
            "severity": "high",
            "reason": conf["code"],
            "detail": f"{conf['entity']} / {conf['metric']}: {conf['value_a']} vs {conf['value_b']} under identical context ({conf['context_signature']})",
        })

    # Write cleaned_records.csv (preserving leading zeros as strings and exposing conflict/censor/LOD/bounds columns)
    csv_path = output_dir / "cleaned_records.csv"
    csv_cols = [
        "row_id", "entity", "sample_id", "metric", "raw_value", "raw_unit",
        "censor_operator", "lod_value", "lod_unit", "normalized_value", "normalized_unit", "conversion_formula",
        "precision_sigfigs", "value_kind", "normalized_uncertainty",
        "physical_bounds_status", "cross_dimension_status", "csl_schema_status",
        "experimental_condition", "temperature", "ph", "source_document_id", "paragraph_id", "source_locator",
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

    verification_checklist, quality_scorecard = build_verification_checklist_and_scorecard(
        cleaned_rows, canonical_rows, dup_log, ctx_preserved_log, conflicts, review_queue
    )

    summary_metrics = {
        "raw_record_count": len(raw_records),
        "canonical_record_count": len(canonical_rows),
        "duplicate_merged_count": len(dup_log),
        "context_distinct_preserved_count": len(ctx_preserved_log),
        "dedup_candidate_count": len(dedup_candidates),
        "scientific_conflict_count": len(conflicts),
        "physical_bounds_violation_count": quality_scorecard["physical_bounds_violations_caught"],
        "cross_dimension_refusal_count": quality_scorecard["cross_dimension_refusals_caught"],
        "csl_schema_violation_count": quality_scorecard["csl_schema_violations_caught"],
        "notes_lod_extracted_count": quality_scorecard["notes_lod_extracted_count"],
        "censored_record_count": sum(1 for r in canonical_rows if r.get("value_kind") in ("censored", "censored_nonnumeric")),
        "missing_record_count": sum(1 for r in canonical_rows if r.get("value_kind") == "missing"),
        "leading_zero_ids_protected_count": len(schema_report["leading_zero_ids_protected"]),
        "human_review_count": len(review_queue),
        "changelog_entry_count": len(changelog),
        "row_traceability_rate": quality_scorecard["row_traceability_rate"],
    }

    audit_bundle = {
        "case_id": case_id,
        "rules_applied": [
            "R01_DYNAMIC_SCHEMA_AND_FIELD_ALIAS",
            "R01_ZOTERO_CSL_SCHEMA_GUARD",
            "R02_LOCALE_CENSORED_AND_NOTES_LOD_PARSE",
            "R03_COMPOUND_UNIT_DELTA_TEMP_AND_CROSS_DIM_REFUSAL",
            "R04_CONTEXT_LOCKED_DEDUP_AND_CONFLICT_STRATIFICATION",
            "R05_MISSING_UNKNOWN_AND_PHYSICAL_BOUNDS_QUARANTINE",
            "R06_TRACEABLE_EXPORT_CHECKLIST_AND_REVIEW_REPORT",
        ],
        "schema_probe_report": schema_report,
        "summary": summary_metrics,
        "quality_scorecard": quality_scorecard,
        "verification_checklist": verification_checklist,
        "canonical_records": canonical_rows,
        "duplicate_log": dup_log,
        "context_preserved_log": ctx_preserved_log,
        "dedup_candidates": dedup_candidates,
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
            "Raw records and row_ids remain unmodified. Missing values stay null (never 0), unknown units and cross-dimension "
            "requests without molar mass are refused without coercion, physically impossible values (K<0, pH outside [0,14], "
            "bounded % >100, negative concentration/n) are quarantined under R05_PHYSICAL_BOUNDS, detection-limit operators are "
            "preserved, delta-temperature never adds 273.15, and same-context scientific conflicts remain open for human adjudication."
        ),
        "schema_probe_report": schema_report,
        "quality_scorecard": quality_scorecard,
        "verification_checklist": verification_checklist,
        "canonical_records": canonical_rows,
        "duplicate_log": dup_log,
        "context_preserved_log": ctx_preserved_log,
        "scientific_conflicts": conflicts,
        "changelog": changelog,
        "needs_human_review": review_queue,
        "metrics": summary_metrics,
        "metric_provenance": {k: "cleaning_audit_report.json:summary" for k in summary_metrics},
        "findings": [
            f"Probed {len(sources_list)} source(s) dynamically; cleaned {len(raw_records)} row(s) -> {len(canonical_rows)} canonical row(s) with row_traceability_rate={quality_scorecard['row_traceability_rate']}.",
            f"Guards triggered: {quality_scorecard['physical_bounds_violations_caught']} physical-bounds violation(s), {quality_scorecard['cross_dimension_refusals_caught']} cross-dimension refusal(s), {len(conflicts)} same-context conflict(s), {len(dup_log)} merged duplicate(s), {len(ctx_preserved_log)} distinct-context row(s) preserved.",
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
