#!/usr/bin/env python3
"""Multi-source experimental parameter extractor, Planned-vs-Actual conflict detector,
config-key miscopy auditor, and frontend scientific terminology purifier.

Target task: methods-experiments-writing (Methods & Experimental Setup).
Zero third-party dependencies (Python 3.12+ stdlib + sibling writing_io_and_invariants.py).
Strictly outputs CSV / JSON / Markdown (NEVER .tsv).
Produces:
  - experimental_parameters.csv     (provenance table: planned vs actual vs draft)
  - reproducibility_checklist.json  (6-element coverage, ELN back-links, [UNRECORDED] items)
  - methods_audit.json              (conflicts, key miscopies, fabrications, frontend leaks)
  - result.json                     (Harbor/structured output contract)
"""

from __future__ import annotations

import argparse
import ast
import configparser
import csv
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import writing_io_and_invariants as wio  # noqa: E402

# ---------------------------------------------------------------------------
# Canonical Parameter Alias Table & Extractors
# ---------------------------------------------------------------------------

CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "learning_rate": ("learning_rate", "lr", "init_lr", "base_lr", "eta", "学习率"),
    "weight_decay": ("weight_decay", "wd", "l2_reg", "l2_decay", "权重衰减"),
    "epochs": ("epochs", "num_epochs", "max_epochs", "n_epochs", "planned_epochs", "actual_epochs", "stopped_epoch", "early_stop_epoch", "训练轮数", "迭代轮数"),
    "batch_size": ("batch_size", "bs", "per_device_train_batch_size", "minibatch_size", "批大小"),
    "random_seed": ("seed", "random_seed", "torch_seed", "np_seed", "numpy_seed", "随机种子"),
    "dropout": ("dropout", "dropout_rate", "drop_prob"),
    "optimizer": ("optimizer", "opt", "优化器"),
    "temperature": ("temperature", "temp", "incubation_temp", "reaction_temp", "assay_temp", "温度", "孵育温度", "反应温度"),
    "repeat_count": ("repeats", "replicates", "n_repeats", "num_runs", "n_runs", "num_seeds", "重复次数", "生物学重复"),
    "train_split": ("train_split", "train_ratio", "train_frac", "训练集比例"),
    "val_split": ("val_split", "val_ratio", "valid_split", "validation_ratio", "验证集比例"),
    "test_split": ("test_split", "test_ratio", "test_frac", "测试集比例"),
    "sample_size": ("sample_size", "n_samples", "total_samples", "cohort_size", "样本量"),
    "dataset_version": ("dataset_version", "data_version", "release_version", "数据集版本"),
    "warmup_steps": ("warmup_steps",),
    "warmup_epochs": ("warmup_epochs",),
    "warmup_ratio": ("warmup_ratio",),
    "max_length": ("max_length", "max_seq_len", "seq_len", "context_length"),
}

ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _aliases in CANONICAL_ALIASES.items():
    for _al in _aliases:
        ALIAS_TO_CANON[_al.lower()] = _canon

UNIT_SUFFIX_RE = re.compile(
    r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(°C|degC|C|K|%|ms|s|min|h|hr|mL|uL|µL|mg/mL|mM|µM|uM|rpm|Hz|GB|MB|epochs?|steps?)?$"
)

FRONTEND_LEAK_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "backend_qc_flag",
        re.compile(r"\b(?:QC_[A-Za-z0-9_]+|qc_pass[A-Za-z0-9_]*)\b"),
        "Replace internal QC flag with scientific quality-control criteria (e.g., 'samples passing predefined quality thresholds').",
    ),
    (
        "backend_tmp_debug_token",
        re.compile(r"\b(?:tmp_[A-Za-z0-9_]+|temp_[A-Za-z0-9_]+|debug_[A-Za-z0-9_]+|raw_dump[A-Za-z0-9_]*)\b", re.I),
        "Remove temporary/debug variable names from publication-facing Methods prose.",
    ),
    (
        "backend_in_sample_tag",
        re.compile(r"\bin[-_]sample(?:_[A-Za-z0-9_]+)?\b", re.I),
        "Replace internal 'in-sample' tag with explicit scientific split name ('training partition' or 'internal development cohort').",
    ),
    (
        "backend_run_version_suffix",
        re.compile(r"\b[A-Za-z0-9]+_(?:run\d+(?:_final)?|v\d+_final|final_v\d+|merged_v\d+)\b", re.I),
        "Replace script run/version artifact identifier with canonical experiment ID or standard method name.",
    ),
    (
        "raw_db_prefix",
        re.compile(r"\b(?:GO:\d{5,7}|KEGG_[A-Z0-9_]+|REACTOME_[A-Z0-9_]+|HALLMARK_[A-Z0-9_]+)\b"),
        "Use human-readable scientific pathway name in Methods prose and keep database IDs in supplementary tables.",
    ),
]

ESSENTIAL_CHECKLIST_ITEMS: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "temperature_or_ambient_condition",
        "Experimental temperature / ambient assay condition",
        re.compile(r"(?:\b\d+(?:\.\d+)?\s*(?:°C|degC|K)\b|\b(?:temperature|room\s+temperature|ambient|incubat(?:ed|ion)|thermal)\b|温度|室温|孵育|恒温|℃)", re.I),
    ),
    (
        "randomization_and_grouping",
        "Sample randomization, blinding, and group allocation procedure",
        re.compile(r"(?:\b(?:randomiz(?:ed|ation)|random\s+allocat|stratified\s+split|blinded|group\s+allocation|block\s+random)\b|随机化|随机分组|盲法|分层抽样|分组方式)", re.I),
    ),
    (
        "random_seed",
        "Computational random seed(s)",
        re.compile(r"(?:\b(?:random\s+seed|seed\s*[=:]\s*\d+|torch\.manual_seed|np\.random\.seed)\b|随机种子)", re.I),
    ),
    (
        "repeat_count",
        "Independent repeat / replicate count",
        re.compile(r"(?:\b(?:triplicate|duplicate|replicates?|repeats?|independent\s+runs?|\d+\s+seeds?|\d+-fold)\b|重复次数|独立重复|生物学重复|技术重复|折交叉验证)", re.I),
    ),
    (
        "hardware_software_env",
        "Hardware specifications and software/library versions",
        re.compile(r"(?:\b(?:GPU|CPU|CUDA|PyTorch|Python\s+3\.\d+|TensorFlow|R\s+\d+\.\d+|Ubuntu|Linux|A100|V100|RTX|instrument|spectrometer|microscope|sequencer)\b|硬件|软件版本|仪器型号|操作系统)", re.I),
    ),
    (
        "dataset_version_and_split",
        "Dataset name, version, sample size, and train/val/test split",
        re.compile(r"(?:\b(?:train(?:ing)?\s*/\s*val|train/val/test|split\s+ratio|dataset\s+version|v\d+\.\d+|cohort\s+of\s+\d+|[nN]\s*=\s*\d+)\b|数据集版本|划分比例|训练集|验证集|测试集|样本量)", re.I),
    ),
    (
        "baseline_same_budget",
        "Baseline comparison under matched compute/data/budget conditions",
        re.compile(r"(?:\b(?:baseline|same\s+(?:budget|compute|split|hardware)|matched\s+budget|positive\s+control|negative\s+control|vehicle\s+control|control\s+group)\b|基线|同预算|对照组|阳性对照|阴性对照)", re.I),
    ),
    (
        "concrete_failure_limitations",
        "Concrete failure modes and operational applicability boundaries",
        re.compile(r"(?:\b(?:limitation|failure\s+mode|degrade(?:s|d)?\s+when|out-of-distribution|sensitivity\s+to|boundary\s+condition|does\s+not\s+generalize)\b|局限性?|失效场景|适用边界|退化|受限于)", re.I),
    ),
]


def _canonicalize_key(raw_key: str) -> str:
    cleaned = re.sub(r"^[^A-Za-z0-9\u4e00-\u9fff]+", "", raw_key.strip()).lower()
    cleaned = cleaned.replace("-", "_").replace(" ", "_")
    # Strip dot prefixes (e.g., training.learning_rate -> learning_rate)
    leaf = cleaned.split(".")[-1]
    if leaf in ALIAS_TO_CANON:
        return ALIAS_TO_CANON[leaf]
    if cleaned in ALIAS_TO_CANON:
        return ALIAS_TO_CANON[cleaned]
    return leaf or cleaned


def _parse_val_and_unit(raw_val: Any) -> tuple[str, str]:
    s = str(raw_val).strip().strip('"').strip("'")
    m = UNIT_SUFFIX_RE.match(s)
    if m:
        num_s, unit = m.group(1), (m.group(2) or "").strip()
        # Preserve exact numeric string precision instead of truncating via :g
        return num_s, unit
    return s, ""


def _values_equal(v1: str, v2: str) -> bool:
    s1, s2 = v1.strip().lower(), v2.strip().lower()
    if s1 == s2:
        return True
    try:
        return abs(float(s1) - float(s2)) <= 1e-12 * max(1.0, abs(float(s1)), abs(float(s2)))
    except ValueError:
        return False


def _parse_simple_yaml(text: str, rel_path: str) -> list[dict[str, Any]]:
    """Pure stdlib indentation-aware YAML key-value parser for scientific configs."""
    records: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        # Strip inline comment unless inside quotes
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped in ("---", "..."):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if stripped.startswith("- "):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*:\s*(.*)$", stripped)
        if not m:
            continue
        key, val_raw = m.group(1).strip(), m.group(2).strip()
        # Strip trailing comment
        if " #" in val_raw:
            val_raw = val_raw.split(" #", 1)[0].strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        full_key = ".".join([k for _, k in stack] + [key]) if stack else key
        if not val_raw:
            stack.append((indent, key))
        else:
            val, unit = _parse_val_and_unit(val_raw)
            role = "actual_executed_log" if any(t in full_key.lower() for t in ("actual_", "executed_", "observed_")) else "planned_config"
            records.append({
                "raw_key": full_key,
                "canonical_key": _canonicalize_key(full_key),
                "value": val,
                "unit": unit,
                "source_file": rel_path,
                "line": lineno,
                "role": role,
            })
    return records


def _flatten_dict(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            fk = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.extend(_flatten_dict(v, fk))
            elif isinstance(v, (str, int, float, bool)):
                out.append((fk, v))
            elif isinstance(v, list) and len(v) <= 8 and all(isinstance(x, (str, int, float)) for x in v):
                out.append((fk, "/".join(str(x) for x in v)))
    return out


def _extract_from_json(path: Path, rel_path: str) -> list[dict[str, Any]]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    is_log = any(k in path.name.lower() for k in ("log", "run", "exec", "metrics", "actual", "history"))
    default_role = "actual_executed_log" if is_log else "planned_config"
    records: list[dict[str, Any]] = []
    for fk, v in _flatten_dict(obj):
        if fk.lower() in {"case_id", "task_id", "title", "description"}:
            continue
        val, unit = _parse_val_and_unit(v)
        role = "actual_executed_log" if any(t in fk.lower() for t in ("actual", "executed", "stopped", "observed")) else default_role
        records.append({
            "raw_key": fk,
            "canonical_key": _canonicalize_key(fk),
            "value": val,
            "unit": unit,
            "source_file": rel_path,
            "line": 1,
            "role": role,
        })
    return records


def _extract_from_toml(path: Path, rel_path: str) -> list[dict[str, Any]]:
    try:
        obj = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    records: list[dict[str, Any]] = []
    for fk, v in _flatten_dict(obj):
        val, unit = _parse_val_and_unit(v)
        records.append({
            "raw_key": fk,
            "canonical_key": _canonicalize_key(fk),
            "value": val,
            "unit": unit,
            "source_file": rel_path,
            "line": 1,
            "role": "planned_config",
        })
    return records


def _extract_from_ini(path: Path, rel_path: str) -> list[dict[str, Any]]:
    cp = configparser.ConfigParser()
    records: list[dict[str, Any]] = []
    try:
        cp.read_string(path.read_text(encoding="utf-8", errors="replace"))
        for sec in cp.sections():
            for k, v in cp.items(sec):
                fk = f"{sec}.{k}"
                val, unit = _parse_val_and_unit(v)
                records.append({
                    "raw_key": fk,
                    "canonical_key": _canonicalize_key(fk),
                    "value": val,
                    "unit": unit,
                    "source_file": rel_path,
                    "line": 1,
                    "role": "planned_config",
                })
    except Exception:
        return _parse_simple_yaml(path.read_text(encoding="utf-8", errors="replace"), rel_path)
    return records


def _extract_from_python(path: Path, rel_path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    records: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"imports": [], "steps": [], "commands": []}
    for lineno, raw in enumerate(text.splitlines(), 1):
        sm = re.match(r"^\s*#\s*(Step\s+\d+[^#\n]{2,120}|Phase\s+\d+[^#\n]{2,120}|步骤\s*\d+[^#\n]{2,120})", raw, re.I)
        if sm:
            meta["steps"].append(f"{rel_path}:L{lineno}: {sm.group(1).strip()}")
    meta["commands"].append(f"python3 {rel_path}")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return records, meta

    kw_param_names = {
        "seed", "random_seed", "random_state", "test_size", "val_size", "train_size",
        "n_splits", "epochs", "num_epochs", "batch_size", "lr", "learning_rate",
        "weight_decay", "dropout", "alpha", "max_iter", "n_estimators",
    }
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                imports.append(al.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".")[0])
        elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            doc = (ast.get_docstring(node) or "").splitlines()[0][:90] if ast.get_docstring(node) else ""
            meta["steps"].append(f"{rel_path}:L{node.lineno}: def {node.name}()" + (f" — {doc}" if doc else ""))
        # Uppercase module constants (e.g., LEARNING_RATE = 3e-4, SEED = 42)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float, str, bool)):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and (tgt.id.isupper() or tgt.id.lower() in kw_param_names) and len(tgt.id) >= 2:
                    val, unit = _parse_val_and_unit(node.value.value)
                    records.append({
                        "raw_key": tgt.id,
                        "canonical_key": _canonicalize_key(tgt.id),
                        "value": val,
                        "unit": unit,
                        "source_file": rel_path,
                        "line": node.lineno,
                        "role": "planned_config",
                    })
        # argparse add_argument("--lr", default=3e-4) or keyword args like random_state=42
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
                flag_name = ""
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("--"):
                        flag_name = arg.value.lstrip("-").replace("-", "_")
                for kw in node.keywords:
                    if kw.arg == "default" and isinstance(kw.value, ast.Constant) and kw.value.value is not None and flag_name:
                        val, unit = _parse_val_and_unit(kw.value.value)
                        records.append({
                            "raw_key": flag_name,
                            "canonical_key": _canonicalize_key(flag_name),
                            "value": val,
                            "unit": unit,
                            "source_file": rel_path,
                            "line": node.lineno,
                            "role": "planned_config",
                        })
            else:
                for kw in node.keywords:
                    if kw.arg and kw.arg.lower() in kw_param_names and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, (int, float, str)):
                        val, unit = _parse_val_and_unit(kw.value.value)
                        records.append({
                            "raw_key": kw.arg,
                            "canonical_key": _canonicalize_key(kw.arg),
                            "value": val,
                            "unit": unit,
                            "source_file": rel_path,
                            "line": node.lineno,
                            "role": "planned_config",
                        })
    meta["imports"] = sorted(set(imports))
    return records, meta


def _extract_from_shell(path: Path, rel_path: str) -> tuple[list[dict[str, Any]], list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    records: list[dict[str, Any]] = []
    commands: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        st = raw.strip()
        if not st or st.startswith("#"):
            continue
        if any(cmd in st for cmd in ("python", "torchrun", "accelerate", "Rscript", "bash", "sh ")):
            commands.append(f"{rel_path}:L{lineno}: {st[:180]}")
        for m in re.finditer(r"--([A-Za-z0-9_\-]+)(?:=|\s+)([^\s\\'\"#]+)", st):
            k, v_raw = m.group(1).replace("-", "_"), m.group(2)
            val, unit = _parse_val_and_unit(v_raw)
            records.append({
                "raw_key": k,
                "canonical_key": _canonicalize_key(k),
                "value": val,
                "unit": unit,
                "source_file": rel_path,
                "line": lineno,
                "role": "actual_executed_log",
            })
        for m in re.finditer(r"^(?:export\s+)?([A-Z][A-Z0-9_]{1,32})=([^\s#]+)", st):
            k, v_raw = m.group(1), m.group(2)
            val, unit = _parse_val_and_unit(v_raw)
            records.append({
                "raw_key": k,
                "canonical_key": _canonicalize_key(k),
                "value": val,
                "unit": unit,
                "source_file": rel_path,
                "line": lineno,
                "role": "actual_executed_log",
            })
    return records, commands


LOG_KV_RE = re.compile(
    r"\b(learning_rate|lr|weight_decay|wd|batch_size|bs|seed|random_seed|dropout|optimizer|"
    r"epochs|num_epochs|actual_epochs|planned_epochs|temperature|temp|repeats|replicates|"
    r"train_split|val_split|test_split|sample_size|dataset_version|warmup_steps)\b\s*[:=]\s*([A-Za-z0-9_./+\-°%]+)",
    re.I,
)
EARLY_STOP_RE = re.compile(
    r"(?:early\s+stop(?:ping|ped)?(?:\s+triggered)?|stopped\s+training|finished\s+training|converged|completed)\s+(?:at|after)\s+epoch\s*[:=#]?\s*(\d+)"
    r"|实际训练(?:至|停止于)第\s*(\d+)\s*(?:轮|epoch)",
    re.I,
)


def _extract_from_text_lines(text: str, rel_path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        for m in LOG_KV_RE.finditer(raw):
            k, v_raw = m.group(1), m.group(2).rstrip(".,;)")
            val, unit = _parse_val_and_unit(v_raw)
            role = "planned_config" if k.lower().startswith("planned_") else "actual_executed_log"
            records.append({
                "raw_key": k,
                "canonical_key": _canonicalize_key(k),
                "value": val,
                "unit": unit,
                "source_file": rel_path,
                "line": lineno,
                "role": role,
            })
        em = EARLY_STOP_RE.search(raw)
        if em:
            ep = em.group(1) or em.group(2)
            records.append({
                "raw_key": "actual_epochs_stopped",
                "canonical_key": "epochs",
                "value": ep,
                "unit": "epochs",
                "source_file": rel_path,
                "line": lineno,
                "role": "actual_executed_log",
            })
    return records


def _extract_from_log(path: Path, rel_path: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return _extract_from_text_lines(text, rel_path)


def _profile_csv_dataset(path: Path, rel_path: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Profile a tabular CSV dataset for row count, columns, and sample_size provenance."""
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            if not header:
                return None, []
            n_rows = sum(1 for row in reader if any(cell.strip() for cell in row))
        prof = {
            "file": rel_path,
            "n_rows": n_rows,
            "n_cols": len(header),
            "columns": [h.strip() for h in header[:16]],
        }
        recs = [{
            "raw_key": f"{path.stem}.n_rows",
            "canonical_key": "sample_size",
            "value": str(n_rows),
            "unit": "rows",
            "source_file": rel_path,
            "line": 1,
            "role": "actual_executed_log",
        }]
        return prof, recs
    except Exception:
        return None, []


def _classify_input_file_role(fp: Path, rel: str, text_preview: str = "") -> str:
    """Pre-classify every /input file into experimental_config_or_code_or_log vs background_literature_or_reference."""
    suf = fp.suffix.lower()
    low = fp.name.lower()
    if suf in {".yaml", ".yml", ".toml", ".ini", ".cfg", ".py", ".sh", ".csv", ".log", ".sqlite", ".db"}:
        return "experimental_config_or_code_or_log"
    if suf == ".bib":
        return "background_literature_or_reference"
    if suf == ".json":
        if '"DOI"' in text_preview[:1200] or "references" in low or "bib" in low:
            return "background_literature_or_reference"
        return "experimental_config_or_code_or_log"
    # Text / Markdown / PDF / DOCX / XML / HTML: inspect filename & content cues
    exp_name_cues = ("lab_note", "labnote", "eln", "config", "train", "run_", "log", "protocol", "handover", "methods_draft", "experiment_note", "setup")
    if any(k in low for k in exp_name_cues):
        return "experimental_config_or_code_or_log"
    lit_name_cues = ("pmc", "arxiv", "paper", "review", "oer_", "scfa_", "gnn_", "literature", "reference", "editorial", "article")
    if any(k in low for k in lit_name_cues):
        return "background_literature_or_reference"
    head = text_preview[:3000]
    lit_content_hits = sum(
        1 for pat in (
            r"\bDOI\s*:\s*10\.\d{4,9}/",
            r"(?m)^#{1,3}\s*(?:\d+\.?\s*)?(?:Abstract|Introduction|References)\b",
            r"\b(?:Special\s+Issue|review\s+article|systematic\s+review|narrative\s+review|et\s+al\.)\b",
        )
        if re.search(pat, head, re.I)
    )
    exp_content_hits = sum(
        1 for pat in (
            r"\b(?:Lab\s+Handover|ELN-|learning_rate\s*[:=]|batch_size\s*[:=]|Early\s+stopping|Resolved\s+args)\b",
        )
        if re.search(pat, head, re.I)
    )
    if lit_content_hits >= 2 and exp_content_hits == 0:
        return "background_literature_or_reference"
    return "experimental_config_or_code_or_log"


def _summarize_background_literature(fp: Path, rel: str, text: str, eln_id: str) -> dict[str, Any]:
    """Summarize a background/reference literature file separately without conflating it with executed configs."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = ""
    for ln in lines[:12]:
        m = re.match(r"^#{1,3}\s+(.+)$", ln)
        if m:
            title = m.group(1).strip()
            break
    if not title and lines:
        title = lines[0][:140]
    doi_m = re.search(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", text[:3000], re.I)
    doi = doi_m.group(1).rstrip(".,;)") if doi_m else "not_stated"
    headings = [re.sub(r"^#{1,4}\s*", "", ln).strip() for ln in lines[:200] if re.match(r"^#{1,4}\s+", ln)][:8]
    doc_type = (
        "special_issue_editorial_or_review"
        if re.search(r"\b(?:Special\s+Issue|Editorial|Review|Survey)\b", text[:4000], re.I)
        else "background_research_article"
    )
    excerpt_lines = [ln for ln in lines[1:12] if not ln.startswith("#") and not ln.lower().startswith("doi:")]
    scope_summary = re.sub(r"\s+", " ", " ".join(excerpt_lines))[:360]
    return {
        "eln_id": eln_id,
        "file": rel,
        "input_role": "background_literature_or_reference",
        "title": title[:160],
        "doi": doi,
        "document_type": doc_type,
        "section_headings": headings,
        "scope_summary": scope_summary,
        "experimental_parameter_extraction_applied": False,
        "handling_note": (
            "Pre-classified as background literature/reference material rather than an executed experiment config/script/log. "
            "Contextualized separately in Section 6; parameters/conditions inside this reference are NOT merged into executed experiment provenance."
        ),
    }


def extract_all_source_parameters(source_dir: Path, draft_path: Path | None) -> dict[str, Any]:
    """Pre-classify all /input files and separately process experimental configs/code/logs vs background literature."""
    records: list[dict[str, Any]] = []
    commands: list[str] = []
    pipeline_steps: list[str] = []
    detected_imports: set[str] = set()
    dataset_profiles: list[dict[str, Any]] = []
    degraded_sources: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    background_literature: list[dict[str, Any]] = []
    source_text_chunks: list[str] = []

    for fp in sorted(source_dir.rglob("*")):
        if not fp.is_file() or "__pycache__" in fp.parts:
            continue
        if draft_path and fp.resolve() == draft_path.resolve():
            continue
        low = fp.name.lower()
        if low in {"task.md", "case.json"}:
            continue
        rel = fp.relative_to(source_dir).as_posix()
        suf = fp.suffix.lower()
        eln_id = f"ELN-SRC-{len(source_files) + 1:03d}"

        # Preview text for pre-classification
        preview_txt = ""
        doc_read: dict[str, Any] | None = None
        if suf in {".docx", ".pdf", ".xml", ".html", ".htm"}:
            doc_read = wio.read_document_text(fp)
            if doc_read.get("meta", {}).get("degraded_mode"):
                degraded_sources.append({
                    "code": "SOURCE_DEGRADED_EXTRACTION",
                    "severity": "minor",
                    "detail": f"{rel} extracted via degraded fallback ({doc_read.get('meta', {}).get('method')}).",
                })
            if not doc_read.get("ok"):
                source_files.append({
                    "eln_id": eln_id,
                    "file": rel,
                    "format": suf.lstrip("."),
                    "input_role": _classify_input_file_role(fp, rel, ""),
                    "extracted_param_count": 0,
                    "degraded": True,
                    "degraded_reason": str(doc_read.get("meta", {}).get("error", "extractor unavailable")),
                })
                continue
            preview_txt = doc_read.get("text", "")
        elif suf in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".py", ".sh", ".log", ".txt", ".md", ".bib"}:
            preview_txt = fp.read_text(encoding="utf-8", errors="replace")

        input_role = _classify_input_file_role(fp, rel, preview_txt)

        # Separate path for background literature / reference papers
        if input_role == "background_literature_or_reference":
            bg_card = _summarize_background_literature(fp, rel, preview_txt, eln_id)
            background_literature.append(bg_card)
            source_files.append({
                "eln_id": eln_id,
                "file": rel,
                "format": suf.lstrip("."),
                "input_role": input_role,
                "extracted_param_count": 0,
                "handling": "contextualized_separately_as_background_reference",
            })
            continue

        file_recs: list[dict[str, Any]] = []
        if suf in {".yaml", ".yml"}:
            source_text_chunks.append(preview_txt)
            file_recs = _parse_simple_yaml(preview_txt, rel)
        elif suf == ".json":
            source_text_chunks.append(preview_txt)
            if low != "case.json":
                file_recs = _extract_from_json(fp, rel)
        elif suf == ".toml":
            source_text_chunks.append(preview_txt)
            file_recs = _extract_from_toml(fp, rel)
        elif suf in {".ini", ".cfg"}:
            source_text_chunks.append(preview_txt)
            file_recs = _extract_from_ini(fp, rel)
        elif suf == ".py":
            source_text_chunks.append(preview_txt)
            file_recs, py_meta = _extract_from_python(fp, rel)
            detected_imports.update(py_meta["imports"])
            pipeline_steps.extend(py_meta["steps"])
            commands.extend(py_meta["commands"])
        elif suf == ".sh":
            source_text_chunks.append(preview_txt)
            file_recs, cmds = _extract_from_shell(fp, rel)
            commands.extend(cmds)
        elif suf == ".csv":
            prof, csv_recs = _profile_csv_dataset(fp, rel)
            if prof:
                dataset_profiles.append(prof)
                source_text_chunks.append(f"Dataset {rel}: n={prof['n_rows']} rows, columns={', '.join(prof['columns'])}")
            file_recs = csv_recs
        elif suf in {".log", ".txt", ".md"}:
            source_text_chunks.append(preview_txt)
            file_recs = _extract_from_text_lines(preview_txt, rel)
            for lno, lraw in enumerate(preview_txt.splitlines()[:400], 1):
                if re.match(r"^\s*(?:#{1,3}\s+.*(?:method|experiment|procedure|setup|material|characterization|synthesis)|Step\s+\d+[:.)]|步骤\s*\d+)", lraw, re.I):
                    pipeline_steps.append(f"{rel}:L{lno}: {lraw.strip()[:120]}")
        elif suf in {".docx", ".pdf", ".xml", ".html", ".htm"}:
            source_text_chunks.append(preview_txt)
            file_recs = _extract_from_text_lines(preview_txt, rel)
        else:
            continue

        records.extend(file_recs)
        source_files.append({
            "eln_id": eln_id,
            "file": rel,
            "format": suf.lstrip("."),
            "input_role": input_role,
            "extracted_param_count": len(file_recs),
        })

    return {
        "records": records,
        "commands": list(dict.fromkeys(commands)),
        "pipeline_steps": list(dict.fromkeys(pipeline_steps))[:25],
        "detected_imports": sorted(detected_imports),
        "dataset_profiles": dataset_profiles,
        "degraded_sources": degraded_sources,
        "source_files": source_files,
        "background_literature": background_literature,
        "input_role_summary": {
            "experimental_config_or_code_or_log": [sf["file"] for sf in source_files if sf.get("input_role") == "experimental_config_or_code_or_log"],
            "background_literature_or_reference": [sf["file"] for sf in source_files if sf.get("input_role") == "background_literature_or_reference"],
        },
        "combined_source_text": "\n".join(source_text_chunks),
    }


# ---------------------------------------------------------------------------
# Draft Prose Parameter Extraction & Cross-Verification
# ---------------------------------------------------------------------------

DRAFT_PARAM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("learning_rate", re.compile(r"(?:learning\s+rate|lr|学习率)\s*(?:of|was|set\s+to|=|:|为)?\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?\d+)?)", re.I)),
    ("weight_decay", re.compile(r"(?:weight\s+decay|l2\s+regularization|权重衰减)\s*(?:of|was|set\s+to|=|:|为)?\s*([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?\d+)?)", re.I)),
    ("epochs", re.compile(r"(?:trained\s+for|for\s+a\s+total\s+of|epochs?\s*(?:of|was|set\s+to|=|:|为)?|训练(?:了)?)\s*(\d+)\s*(?:epochs?|轮)?", re.I)),
    ("batch_size", re.compile(r"(?:batch\s+size|批大小)\s*(?:of|was|set\s+to|=|:|为)?\s*(\d+)", re.I)),
    ("random_seed", re.compile(r"(?:random\s+seed|seed|随机种子)\s*(?:of|was|set\s+to|=|:|为)?\s*(\d+)", re.I)),
    ("dropout", re.compile(r"(?:dropout(?:\s+rate)?)\s*(?:of|was|set\s+to|=|:|为)?\s*([0-9]+(?:\.[0-9]+)?)", re.I)),
    ("temperature", re.compile(r"(?:temperature|incubated\s+at|maintained\s+at|温度(?:为)?|孵育于)\s*(?:of|was|at|=|:)?\s*([+-]?\d+(?:\.\d+)?)\s*(°C|℃|degC|K)", re.I)),
    ("repeat_count", re.compile(r"(?:repeated|replicates?|runs?|重复)\s*(?:for|=|:|为)?\s*(\d+)\s*(?:times|independent\s+runs|replicates|次)", re.I)),
    ("train_split", re.compile(r"(?:train(?:ing)?\s*(?:set|split|ratio)|训练集)\s*(?:of|=|:|为)?\s*(\d+(?:\.\d+)?)\s*%", re.I)),
    ("val_split", re.compile(r"(?:val(?:idation)?\s*(?:set|split|ratio)|验证集)\s*(?:of|=|:|为)?\s*(\d+(?:\.\d+)?)\s*%", re.I)),
    ("test_split", re.compile(r"(?:test(?:ing)?\s*(?:set|split|ratio)|测试集)\s*(?:of|=|:|为)?\s*(\d+(?:\.\d+)?)\s*%", re.I)),
]


def _extract_draft_params(draft_text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for lineno, line in enumerate(draft_text.splitlines(), 1):
        st = line.strip()
        if not st or st.startswith("#"):
            continue
        for canon_key, pat in DRAFT_PARAM_PATTERNS:
            for m in pat.finditer(st):
                raw_v = m.group(1)
                unit = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
                val, parsed_u = _parse_val_and_unit(raw_v)
                if canon_key in ("train_split", "val_split", "test_split"):
                    unit = "%"
                found.append({
                    "canonical_key": canon_key,
                    "value": val,
                    "unit": unit or parsed_u,
                    "line": lineno,
                    "quote": st[:180],
                })
    return found


def audit_methods_and_experiments(
    source_dir: Path,
    draft_path: Path | None,
    *,
    case_id: str = "case-01",
    task_id: str = "methods-experiments-writing",
    case_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extracted = extract_all_source_parameters(source_dir, draft_path)
    records = extracted["records"]
    combined_source = extracted["combined_source_text"]

    draft_text = ""
    if draft_path and draft_path.is_file():
        r = wio.read_document_text(draft_path)
        if not r["ok"]:
            raise RuntimeError(f"Failed to read draft {draft_path}: {r.get('meta')}")
        draft_text = r["text"]

    # Group extracted source records by canonical_key -> planned vs actual
    by_canon: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for rec in records:
        ck = rec["canonical_key"]
        by_canon.setdefault(ck, {"planned_config": [], "actual_executed_log": []})
        by_canon[ck][rec["role"]].append(rec)

    findings: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = list(extracted.get("degraded_sources", []))

    def add_finding(code: str, severity: str, location: str, quote: str, message: str, remediation: str) -> None:
        fid = f"MTH-{len(findings) + 1:03d}"
        findings.append({
            "finding_id": fid,
            "code": code,
            "severity": severity,
            "location": location,
            "quote": quote[:180],
            "message": message,
            "remediation": remediation,
        })
        evidence_map.append({
            "finding_id": fid,
            "code": code,
            "location": location,
            "quote": quote[:140],
        })

    # =========================================================================
    # 1. Planned Config vs Actual Executed Log Conflicts
    # =========================================================================
    planned_vs_actual_conflicts: list[dict[str, Any]] = []
    for ck, buckets in sorted(by_canon.items()):
        p_recs = buckets["planned_config"]
        a_recs = buckets["actual_executed_log"]
        if p_recs and a_recs:
            p0 = p_recs[-1]
            a0 = a_recs[-1]
            if not _values_equal(p0["value"], a0["value"]):
                cf = {
                    "canonical_key": ck,
                    "planned_value": p0["value"],
                    "planned_source": f"{p0['source_file']}:L{p0['line']}",
                    "actual_value": a0["value"],
                    "actual_source": f"{a0['source_file']}:L{a0['line']}",
                }
                planned_vs_actual_conflicts.append(cf)
                add_finding(
                    code="PLANNED_VS_ACTUAL_CONFLICT",
                    severity="blocking",
                    location=f"{cf['planned_source']} vs {cf['actual_source']}",
                    quote=f"{ck}: planned={cf['planned_value']} vs actual={cf['actual_value']}",
                    message=(
                        f"Parameter '{ck}' diverges between planned config ({cf['planned_value']} in {cf['planned_source']}) "
                        f"and actual execution log ({cf['actual_value']} in {cf['actual_source']})."
                    ),
                    remediation=(
                        f"In Methods prose and experimental_parameters.csv, report BOTH the planned setting ({cf['planned_value']}) "
                        f"and actual executed value ({cf['actual_value']}) separately; never present the planned value as executed."
                    ),
                )

    # =========================================================================
    # 2. Cross-Audit Draft Prose against Source Parameters (Miscopy, Planned-as-Actual, Fabrication)
    # =========================================================================
    draft_params = _extract_draft_params(draft_text) if draft_text else []
    draft_by_canon: dict[str, list[dict[str, Any]]] = {}
    for dp in draft_params:
        draft_by_canon.setdefault(dp["canonical_key"], []).append(dp)

    key_miscopies: list[dict[str, Any]] = []
    ungrounded_params: list[dict[str, Any]] = []
    planned_as_actual_leaks: list[dict[str, Any]] = []

    for dp in draft_params:
        ck = dp["canonical_key"]
        dval = dp["value"]
        loc = f"{draft_path.name if draft_path else 'draft'}:L{dp['line']}"

        if ck in by_canon:
            p_recs = by_canon[ck]["planned_config"]
            a_recs = by_canon[ck]["actual_executed_log"]
            all_true = a_recs + p_recs
            # Normalize split percentage vs fraction (e.g., 70% vs 0.7)
            def _matches_any(recs: list[dict[str, Any]]) -> bool:
                for r in recs:
                    if _values_equal(dval, r["value"]):
                        return True
                    try:
                        if dp["unit"] == "%" and _values_equal(str(float(dval) / 100.0), r["value"]):
                            return True
                    except ValueError:
                        pass
                return False

            matched_actual = _matches_any(a_recs)
            matched_planned = _matches_any(p_recs)

            if a_recs and p_recs and matched_planned and not matched_actual:
                # Check if draft also mentions the actual value anywhere
                actual_val = a_recs[-1]["value"]
                if not any(_values_equal(other["value"], actual_val) for other in draft_by_canon.get(ck, [])):
                    leak = {
                        "canonical_key": ck,
                        "draft_value": dval,
                        "actual_value": actual_val,
                        "actual_source": f"{a_recs[-1]['source_file']}:L{a_recs[-1]['line']}",
                        "line": dp["line"],
                    }
                    planned_as_actual_leaks.append(leak)
                    add_finding(
                        code="PLANNED_REPORTED_AS_ACTUAL",
                        severity="blocking",
                        location=loc,
                        quote=dp["quote"],
                        message=(
                            f"Draft reports planned value '{ck}={dval}' without disclosing actual executed value "
                            f"'{actual_val}' recorded in {leak['actual_source']}."
                        ),
                        remediation=f"Update {loc} to distinguish planned configuration ({dval}) from actual executed value ({actual_val}).",
                    )
            elif not matched_actual and not matched_planned:
                # Check whether dval actually belongs to a DIFFERENT config key (Config Key Miscopy!)
                swapped_source = None
                for other_ck, other_buckets in by_canon.items():
                    if other_ck == ck:
                        continue
                    for orec in other_buckets["actual_executed_log"] + other_buckets["planned_config"]:
                        if _values_equal(dval, orec["value"]):
                            swapped_source = (other_ck, orec)
                            break
                    if swapped_source:
                        break
                true_rec = all_true[0]
                if swapped_source:
                    other_ck, orec = swapped_source
                    mc = {
                        "claimed_key": ck,
                        "draft_value": dval,
                        "true_value_for_claimed_key": true_rec["value"],
                        "true_source": f"{true_rec['source_file']}:L{true_rec['line']}",
                        "actual_owner_key": other_ck,
                        "actual_owner_source": f"{orec['source_file']}:L{orec['line']}",
                        "line": dp["line"],
                    }
                    key_miscopies.append(mc)
                    add_finding(
                        code="CONFIG_KEY_MISCOPY",
                        severity="blocking",
                        location=loc,
                        quote=dp["quote"],
                        message=(
                            f"Config key miscopy: draft assigns '{dval}' to '{ck}', but '{ck}' is '{true_rec['value']}' "
                            f"({mc['true_source']}); value '{dval}' actually belongs to '{other_ck}' ({mc['actual_owner_source']})."
                        ),
                        remediation=f"Correct '{ck}' at {loc} to '{true_rec['value']}' (from {mc['true_source']}) and verify '{other_ck}' separately.",
                    )
                else:
                    add_finding(
                        code="PARAMETER_VALUE_MISMATCH",
                        severity="blocking",
                        location=loc,
                        quote=dp["quote"],
                        message=(
                            f"Draft reports '{ck}={dval}', conflicting with verified source value '{true_rec['value']}' "
                            f"in {true_rec['source_file']}:L{true_rec['line']}."
                        ),
                        remediation=f"Reconcile '{ck}' against {true_rec['source_file']}:L{true_rec['line']} or document an explicit execution override.",
                    )
        else:
            # Parameter asserted in draft does NOT exist in structured configs/logs -> check raw source text
            if dval not in combined_source:
                ungrounded_params.append({
                    "canonical_key": ck,
                    "draft_value": dval,
                    "unit": dp["unit"],
                    "line": dp["line"],
                    "quote": dp["quote"],
                })
                add_finding(
                    code="UNGROUNDED_FABRICATED_PARAMETER",
                    severity="blocking",
                    location=loc,
                    quote=dp["quote"],
                    message=(
                        f"Parameter '{ck}={dval}{dp['unit']}' stated in draft has NO provenance in any supplied "
                        "config, script, log, or experimental record."
                    ),
                    remediation=f"Remove unverified value '{dval}{dp['unit']}' and replace with [UNRECORDED: AUTHOR_CONFIRM_REQUIRED] until primary source is supplied.",
                )

    # =========================================================================
    # 3. Missing Essential Items in Source Records -> [UNRECORDED: AUTHOR_CONFIRM_REQUIRED]
    # =========================================================================
    NEGATED_RECORD_RE = re.compile(r"\b(?:not\s+(?:logged|recorded|specified|reported|available)|omitted|missing|unrecorded)\b|未记录|缺失|未报告", re.I)
    positive_source_text = "\n".join(
        ln for ln in combined_source.splitlines() if not NEGATED_RECORD_RE.search(ln)
    )
    checklist_items: list[dict[str, Any]] = []
    missing_unrecorded: list[dict[str, Any]] = []
    for item_id, label, pat in ESSENTIAL_CHECKLIST_ITEMS:
        in_source = bool(pat.search(positive_source_text))
        in_draft = bool(pat.search(draft_text)) if draft_text else False
        if in_source:
            status = "RECORDED_IN_SOURCE_AND_DRAFT" if in_draft else "RECORDED_IN_SOURCE_MISSING_IN_DRAFT"
        else:
            status = "FABRICATED_IN_DRAFT_WITHOUT_SOURCE" if in_draft else "[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]"

        entry = {
            "item_id": item_id,
            "label": label,
            "present_in_source": in_source,
            "present_in_draft": in_draft,
            "status": status,
        }
        checklist_items.append(entry)

        if not in_source:
            missing_unrecorded.append(entry)
            gaps.append({
                "code": f"UNRECORDED_{item_id.upper()}",
                "severity": "major",
                "tag": "[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]",
                "detail": f"{label} is not recorded in supplied source configs, logs, or lab notes.",
            })
            if in_draft:
                add_finding(
                    code="UNRECORDED_ITEM_FILLED_WITHOUT_SOURCE",
                    severity="blocking",
                    location=draft_path.name if draft_path else "draft",
                    quote=f"{item_id}: present in draft but absent from all source records",
                    message=f"Draft fills in '{label}' even though it is absent from all supplied source files.",
                    remediation=f"Replace unverified '{label}' statement with [UNRECORDED: AUTHOR_CONFIRM_REQUIRED] or attach original lab/config evidence.",
                )
        elif draft_text and not in_draft:
            add_finding(
                code="SOURCE_ELEMENT_OMITTED_FROM_DRAFT",
                severity="major",
                location=draft_path.name if draft_path else "draft",
                quote=f"{item_id}: recorded in source files but omitted in Methods draft",
                message=f"Verified source files document '{label}', but the Methods draft omits it.",
                remediation=f"Incorporate the verified '{label}' details from source files into the Methods chapter.",
            )

    # =========================================================================
    # 4. Frontend Scientific Terminology Purification Lint
    # =========================================================================
    frontend_leaks: list[dict[str, Any]] = []
    if draft_text:
        for lineno, line in enumerate(draft_text.splitlines(), 1):
            st = line.strip()
            if not st or st.startswith("```"):
                continue
            for rule_name, pat, sug in FRONTEND_LEAK_PATTERNS:
                for m in pat.finditer(st):
                    leak = {
                        "rule": rule_name,
                        "line": lineno,
                        "matched": m.group(0),
                        "suggestion": sug,
                    }
                    frontend_leaks.append(leak)
                    add_finding(
                        code="FRONTEND_BACKEND_TOKEN_LEAK",
                        severity="major",
                        location=f"{draft_path.name if draft_path else 'draft'}:L{lineno}",
                        quote=st,
                        message=f"Internal backend/script identifier '{m.group(0)}' ({rule_name}) leaked into formal Methods prose.",
                        remediation=sug,
                    )

    # =========================================================================
    # 5. Build Consolidated Parameter Table Rows (for CSV, NEVER TSV)
    # =========================================================================
    csv_rows: list[dict[str, str]] = []
    rec_idx = 0
    for ck, buckets in sorted(by_canon.items()):
        rec_idx += 1
        p_recs = buckets["planned_config"]
        a_recs = buckets["actual_executed_log"]
        p_val = p_recs[-1]["value"] if p_recs else ""
        a_val = a_recs[-1]["value"] if a_recs else ""
        unit = (a_recs[-1]["unit"] if a_recs and a_recs[-1]["unit"] else (p_recs[-1]["unit"] if p_recs else ""))
        raw_k = (a_recs[-1]["raw_key"] if a_recs else p_recs[-1]["raw_key"])
        src_locs = []
        if p_recs:
            src_locs.append(f"planned:{p_recs[-1]['source_file']}:L{p_recs[-1]['line']}")
        if a_recs:
            src_locs.append(f"actual:{a_recs[-1]['source_file']}:L{a_recs[-1]['line']}")
        d_vals = [d["value"] for d in draft_by_canon.get(ck, [])]
        d_str = "; ".join(dict.fromkeys(d_vals)) if d_vals else "NOT_MENTIONED_IN_DRAFT"

        if p_val and a_val and not _values_equal(p_val, a_val):
            role = "planned_and_actual_conflict"
            status = "CONFLICT_PLANNED_VS_ACTUAL"
        elif a_val:
            role = "actual_executed_log"
            status = "VERIFIED"
        else:
            role = "planned_config"
            status = "PLANNED_ONLY_CONFIRM_EXECUTION"

        if any(mc["claimed_key"] == ck for mc in key_miscopies):
            status = "BLOCKING_CONFIG_KEY_MISCOPY"
        elif any(pl["canonical_key"] == ck for pl in planned_as_actual_leaks):
            status = "BLOCKING_PLANNED_REPORTED_AS_ACTUAL"

        csv_rows.append({
            "record_id": f"EXP-PARAM-{rec_idx:03d}",
            "canonical_key": ck,
            "raw_key": raw_k,
            "planned_value": p_val or "NOT_IN_CONFIG",
            "actual_value": a_val or "[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]",
            "unit": unit or "dimensionless",
            "role": role,
            "source_file_line": " | ".join(src_locs),
            "draft_reported_value": d_str,
            "audit_status": status,
        })

    # Append unrecorded essential items directly to CSV so authors see every gap in one table
    for mu in missing_unrecorded:
        rec_idx += 1
        csv_rows.append({
            "record_id": f"EXP-PARAM-{rec_idx:03d}",
            "canonical_key": mu["item_id"],
            "raw_key": mu["label"],
            "planned_value": "[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]",
            "actual_value": "[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]",
            "unit": "unspecified",
            "role": "missing_source_element",
            "source_file_line": "NONE_IN_INPUT_CORPUS",
            "draft_reported_value": "PRESENT_WITHOUT_SOURCE" if mu["present_in_draft"] else "[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]",
            "audit_status": mu["status"],
        })

    six_elements_coverage = {
        "1_methods_steps": bool(re.search(r"\b(?:step\s+\d|first|second|then|pipeline|workflow|procedure|protocol)\b|步骤|流程|预处理", draft_text or combined_source, re.I)),
        "2_experimental_setup": any(c["present_in_source"] or c["present_in_draft"] for c in checklist_items if c["item_id"] in ("hardware_software_env", "random_seed", "repeat_count")),
        "3_parameters_with_provenance": len(records) > 0,
        "4_dataset_and_splits": any(c["present_in_source"] or c["present_in_draft"] for c in checklist_items if c["item_id"] == "dataset_version_and_split"),
        "5_baselines_and_controls": any(c["present_in_source"] or c["present_in_draft"] for c in checklist_items if c["item_id"] == "baseline_same_budget"),
        "6_reproducibility_and_limitations": any(c["present_in_source"] or c["present_in_draft"] for c in checklist_items if c["item_id"] == "concrete_failure_limitations"),
    }

    prose_audit = wio.audit_academic_prose(draft_text) if draft_text else {"clean": True, "high_count": 0, "warning_count": 0, "findings": []}
    blocking_n = sum(1 for f in findings if f["severity"] == "blocking")

    checklist_payload = {
        "case_id": case_id,
        "task_id": task_id,
        "task_constraints_applied": (case_contract or {}).get("task_constraints", {}),
        "input_role_summary": extracted["input_role_summary"],
        "background_literature_inventory": extracted["background_literature"],
        "eln_source_backlinks": extracted["source_files"],
        "execution_commands_detected": extracted["commands"],
        "pipeline_steps_detected": extracted["pipeline_steps"],
        "detected_imports": extracted["detected_imports"],
        "dataset_profiles": extracted["dataset_profiles"],
        "six_elements_coverage": six_elements_coverage,
        "essential_items_checklist": checklist_items,
        "unrecorded_author_confirm_required": [m["item_id"] for m in missing_unrecorded],
    }

    audit_payload = {
        "case_id": case_id,
        "task_id": task_id,
        "task_constraints_applied": (case_contract or {}).get("task_constraints", {}),
        "input_role_summary": extracted["input_role_summary"],
        "background_literature_count": len(extracted["background_literature"]),
        "draft_file": draft_path.name if draft_path else None,
        "extracted_parameter_records_count": len(records),
        "unique_canonical_parameters": len(by_canon),
        "planned_vs_actual_conflicts_count": len(planned_vs_actual_conflicts),
        "planned_vs_actual_conflicts": planned_vs_actual_conflicts,
        "planned_reported_as_actual_count": len(planned_as_actual_leaks),
        "planned_reported_as_actual": planned_as_actual_leaks,
        "config_key_miscopies_count": len(key_miscopies),
        "config_key_miscopies": key_miscopies,
        "ungrounded_parameters_count": len(ungrounded_params),
        "ungrounded_parameters": ungrounded_params,
        "missing_unrecorded_count": len(missing_unrecorded),
        "missing_unrecorded_items": [m["item_id"] for m in missing_unrecorded],
        "frontend_semantic_leaks_count": len(frontend_leaks),
        "frontend_semantic_leaks": frontend_leaks,
        "six_elements_coverage": six_elements_coverage,
        "prose_audit_summary": {
            "clean": prose_audit["clean"],
            "high_count": prose_audit["high_count"],
            "warning_count": prose_audit["warning_count"],
        },
        "blocking_count": blocking_n,
        "findings_count": len(findings),
        "findings": findings,
    }

    return {
        "csv_rows": csv_rows,
        "checklist": checklist_payload,
        "audit": audit_payload,
        "evidence_map": evidence_map,
        "gaps": gaps,
        "invariants": wio.extract_invariants(draft_text) if draft_text else {"numbers_with_units": [], "sample_sizes": [], "dois": []},
        "source_files": extracted["source_files"],
        "background_literature": extracted["background_literature"],
        "input_role_summary": extracted["input_role_summary"],
    }


def render_methods_and_experiments_md(res: dict[str, Any], case_id: str, case_contract: dict[str, Any] | None = None) -> str:
    """Directly generate a complete, publication-ready, editable Methods & Experiments chapter.

    Covers all six required elements in clean academic prose, separates experimental configs/code/logs
    from background literature/reference files, preserves Planned-vs-Actual disclosures, and marks
    missing primary details honestly as [UNRECORDED: AUTHOR_CONFIRM_REQUIRED].
    """
    chk = res["checklist"]
    aud = res["audit"]
    eln_map = {sf["file"]: sf["eln_id"] for sf in res["source_files"]}
    tc = (case_contract or {}).get("task_constraints", {})
    exp_files = chk.get("input_role_summary", {}).get("experimental_config_or_code_or_log", [])
    bg_lit = res.get("background_literature", [])

    lines = [
        "# Methods and Experimental Setup",
        "",
    ]
    if tc:
        venue = tc.get("target_journal") or tc.get("audience") or ""
        if venue:
            lines.append(f"*Target Scope*: Prepared for {venue} (Case `{case_id}`). Every numerical setting and procedural step below is traced to primary input artifacts; unrecorded primary parameters are explicitly marked `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]` without fabrication.")
            lines.append("")

    # 1. Study Design and Executable Method Steps
    lines.extend(["## 1. Study Design and Executable Method Steps", ""])
    steps = chk.get("pipeline_steps_detected") or []
    exp_list_str = ", ".join(f"`{f}` (`{eln_map.get(f, 'ELN-SRC')}`)" for f in exp_files) if exp_files else "supplied experimental files"
    if steps:
        lines.append(
            f"The experimental and computational workflow was reconstructed directly from primary experimental artifacts ({exp_list_str}), "
            "separating executable source steps from background literature references. Each stage proceeds sequentially with explicit input-to-output provenance:"
        )
        lines.append("")
        for idx, st in enumerate(steps[:15], 1):
            src_f = st.split(":L", 1)[0]
            eln = eln_map.get(src_f, "ELN-SRC")
            lines.append(f"{idx}. **{src_f}** (`{eln}`): {st.split(': ', 1)[-1] if ': ' in st else st}")
    else:
        lines.append(
            f"Primary experimental inputs ({exp_list_str}) define the data and configuration boundaries, while explicit multi-stage execution logs "
            "or detailed decision criteria were not recorded in the supplied scripts (`[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`)."
        )
    lines.append("")

    # 2. Datasets, Samples, and Partitioning
    lines.extend(["## 2. Datasets, Samples, and Partitioning", ""])
    dprofs = chk.get("dataset_profiles") or []
    split_rows = [r for r in res["csv_rows"] if r["canonical_key"] in {"train_split", "val_split", "test_split", "dataset_version", "sample_size"}]
    if dprofs:
        for dp in dprofs:
            eln = eln_map.get(dp["file"], "ELN-SRC")
            cols = ", ".join(dp["columns"][:12]) + (" ..." if dp["n_cols"] > 12 else "")
            lines.append(
                f"Primary tabular data were inspected from `{dp['file']}` (`{eln}`), containing {dp['n_rows']} complete observation rows across "
                f"{dp['n_cols']} variables ({cols})."
            )
    if split_rows:
        parts = []
        for sr in split_rows:
            val_disp = sr["actual_value"] if sr["actual_value"] != "[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]" else sr["planned_value"]
            parts.append(f"{sr['canonical_key']} = {val_disp} ({sr['unit']}; `{sr['source_file_line']}`)")
        lines.append("Recorded sample and partitioning parameters: " + "; ".join(parts) + ".")
    if not dprofs and not split_rows:
        lines.append("Dataset version, sample counts, inclusion/exclusion criteria, and train/validation/test partitioning: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`.")
    rand_item = next((c for c in chk["essential_items_checklist"] if c["item_id"] == "randomization_and_grouping"), None)
    if rand_item and not rand_item["present_in_source"]:
        lines.append("Sample randomization, blinding, and cohort/database mapping verification (including external SQLite-to-CSV alignment where applicable): `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`.")
    lines.append("")

    # 3. Experimental Setup, Software Environment, and Execution Protocol
    lines.extend(["## 3. Experimental Setup, Software Environment, and Execution Protocol", ""])
    imports = chk.get("detected_imports") or []
    cmds = chk.get("execution_commands_detected") or []
    env_sentences: list[str] = []
    if imports:
        env_sentences.append(
            f"Source scripts import the following software modules: {', '.join(imports)} "
            "(exact runtime interpreter and library versions: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]` unless explicitly logged in primary records)."
        )
    for key_id in ("hardware_software_env", "temperature_or_ambient_condition", "random_seed", "repeat_count"):
        ci = next((c for c in chk["essential_items_checklist"] if c["item_id"] == key_id), None)
        if ci:
            if ci["present_in_source"]:
                env_sentences.append(f"{ci['label']} is documented in supplied experimental records (see exact values and line anchors in Table 1).")
            else:
                env_sentences.append(f"{ci['label']}: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`.")
    lines.append(" ".join(env_sentences))
    lines.append("")
    if cmds:
        lines.append("Reproducible command entry points detected in experimental files:")
        for c in cmds[:8]:
            lines.append(f"- `{c}`")
    else:
        lines.append("Reproducible execution commands and actual runtime execution logs: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`.")
    lines.append("")

    # 4. Experimental Parameters and Provenance Table
    lines.extend([
        "## 4. Experimental Parameters and Provenance (Planned vs. Actual)",
        "",
        "Table 1 summarizes every extracted parameter from `experimental_config_or_code_or_log` inputs, distinguishing planned configuration values from actual executed records.",
        "",
        "| Parameter | Planned Config Value | Actual Executed Value | Unit | Source Anchor (`file:line`) | Audit Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for row in res["csv_rows"]:
        lines.append(
            f"| `{row['canonical_key']}` (`{row['raw_key']}`) | {row['planned_value']} | {row['actual_value']} | "
            f"{row['unit']} | `{row['source_file_line']}` | `{row['audit_status']}` |"
        )
    if aud["planned_vs_actual_conflicts"]:
        lines.extend(["", "**Mandatory Planned-vs-Actual Disclosures**:"])
        for cf in aud["planned_vs_actual_conflicts"]:
            lines.append(
                f"- `{cf['canonical_key']}` diverged between planned configuration (`{cf['planned_value']}` in `{cf['planned_source']}`) "
                f"and actual execution (`{cf['actual_value']}` in `{cf['actual_source']}`); both values are reported separately."
            )
    lines.append("")

    # 5. Baselines, Controls, and Evaluation Protocol
    lines.extend(["## 5. Baselines, Controls, and Evaluation Protocol", ""])
    base_item = next((c for c in chk["essential_items_checklist"] if c["item_id"] == "baseline_same_budget"), None)
    if base_item and base_item["present_in_source"]:
        lines.append(
            "Comparative baseline or control signals were verified in the supplied experimental records. Evaluations should maintain matched "
            "computational/data budgets across all compared methods and align statistical testing units with independent experimental replicates."
        )
    else:
        lines.append(
            "Matched-budget baseline methods, positive/negative controls, and formal statistical significance tests were not specified in the "
            "supplied experimental records (`[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`)."
        )
    lines.append("")

    # 6. Background Literature Context, Reproducibility Registry, and Limitations
    lines.extend(["## 6. Background Literature Boundary, Reproducibility Registry, and Limitations", ""])
    lines.extend(["### 6.1 Background Literature and Reference Boundary", ""])
    if bg_lit:
        lines.append(
            "Input pre-classification identified the following scholarly/reference file(s) as `background_literature_or_reference` "
            "rather than executed experimental scripts or logs. They are documented here for domain context and explicitly excluded from executed hyperparameter provenance:"
        )
        lines.append("")
        for bg in bg_lit:
            lines.append(
                f"- **`{bg['file']}`** (`{bg['eln_id']}`, `{bg['document_type']}`, DOI: `{bg['doi']}`): *{bg['title']}*. "
                f"{bg['scope_summary']} — *Boundary*: {bg['handling_note']}"
            )
    else:
        lines.append("No separate background literature/reference articles were present in the input directory; all processed sources are primary experimental records.")
    lines.append("")

    lines.extend([
        "### 6.2 Reproducibility Registry (ELN Source Back-Links)",
        "",
        "| ELN ID | Source File | Pre-Classified Input Role | Format | Extracted Params | Status |",
        "| --- | --- | --- | --- | ---: | --- |",
    ])
    for sf in res["source_files"]:
        st_lbl = f"DEGRADED ({sf.get('degraded_reason', 'fallback')})" if sf.get("degraded") else "VERIFIED"
        lines.append(
            f"| `{sf['eln_id']}` | `{sf['file']}` | `{sf.get('input_role', 'experimental_config_or_code_or_log')}` | "
            f"`{sf['format']}` | {sf['extracted_param_count']} | {st_lbl} |"
        )
    lines.append("")

    lines.extend(["### 6.3 Author Confirmation Queue and Concrete Operational Limitations", ""])
    unrec = chk.get("unrecorded_author_confirm_required") or []
    if unrec:
        lines.append(
            f"Before journal submission, authors must verify and supply primary evidence for {len(unrec)} unrecorded reproducibility item(s): "
            + ", ".join(f"`{u}`" for u in unrec)
            + "."
        )
    else:
        lines.append("All eight essential reproducibility checklist dimensions have primary source provenance in the supplied experimental records.")
    lines.append(
        "Operational applicability remains strictly bounded by the verified dataset size, recorded software/hardware environment, "
        "and observed execution logs; any unexecuted scripts or external database paths not present in the archive cannot be treated as completed runs."
    )
    lines.append("")
    return "\n".join(lines)


def render_methods_scaffold_md(res: dict[str, Any], case_id: str, case_contract: dict[str, Any] | None = None) -> str:
    """Emit a zero-hallucination six-element Methods & Experiments chapter draft scaffold with three-line table and ELN anchors."""
    chk = res["checklist"]
    aud = res["audit"]
    eln_map = {sf["file"]: sf["eln_id"] for sf in res["source_files"]}
    tc = (case_contract or {}).get("task_constraints", {})

    lines = [
        f"# Methods and Experimental Setup — Zero-Hallucination Six-Element Draft Scaffold ({case_id})",
        "",
        "> Every value below is bound to an ELN source anchor (`ELN-SRC-###`). Any parameter or protocol detail absent from supplied files is explicitly marked `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]` and MUST NOT be fabricated.",
        "",
    ]
    if tc:
        lines.append("- **Task Constraints Consumed (`/input/task.md` & `case.json`)**: " + ", ".join(f"{k}={v}" for k, v in tc.items()))
        lines.append("")

    # Section 1: Executable Method Steps
    lines.extend(["## 1. Study Design and Executable Method Steps", ""])
    steps = chk.get("pipeline_steps_detected") or []
    if steps:
        lines.append("The experimental and computational workflow extracted from supplied source artifacts proceeds through the following ordered stages:")
        lines.append("")
        for idx, st in enumerate(steps[:15], 1):
            src_f = st.split(":L", 1)[0]
            eln = eln_map.get(src_f, "ELN-SRC")
            lines.append(f"{idx}. {st} (`{eln}`)")
    else:
        lines.append("1. Input preprocessing, primary experimental execution, and evaluation criteria: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]` (no explicit step sequence parsed; verify against primary lab notes).")
    lines.append("")

    # Section 2: Datasets, Samples & Partitioning
    lines.extend(["## 2. Datasets, Samples, and Partitioning", ""])
    dprofs = chk.get("dataset_profiles") or []
    if dprofs:
        for dp in dprofs:
            eln = eln_map.get(dp["file"], "ELN-SRC")
            cols = ", ".join(dp["columns"][:10]) + (" ..." if dp["n_cols"] > 10 else "")
            lines.append(
                f"- **Tabular Dataset `{dp['file']}`** (`{eln}`): `{dp['n_rows']}` records across `{dp['n_cols']}` columns (`{cols}`)."
            )
    split_rows = [r for r in res["csv_rows"] if r["canonical_key"] in {"train_split", "val_split", "test_split", "dataset_version", "sample_size"}]
    if split_rows:
        for sr in split_rows:
            val_disp = sr["actual_value"] if sr["actual_value"] != "[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]" else sr["planned_value"]
            lines.append(f"- **{sr['canonical_key']}**: `{val_disp}` ({sr['unit']}) — source `{sr['source_file_line']}`.")
    if not dprofs and not split_rows:
        lines.append("- Dataset version, sample count, preprocessing, and train/validation/test partitioning: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`.")
    rand_item = next((c for c in chk["essential_items_checklist"] if c["item_id"] == "randomization_and_grouping"), None)
    if rand_item and not rand_item["present_in_source"]:
        lines.append("- **Randomization & Group Allocation**: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]` (not documented in supplied source files).")
    lines.append("")

    # Section 3: Experimental Setup, Environment & Run Commands
    lines.extend(["## 3. Experimental Setup, Software Environment, and Execution Commands", ""])
    imports = chk.get("detected_imports") or []
    if imports:
        lines.append(f"- **Detected Software/Library Dependencies (from source scripts)**: `{', '.join(imports)}` (exact pinned versions: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]` unless specified in environment file).")
    cmds = chk.get("execution_commands_detected") or []
    if cmds:
        lines.append("- **Reproducible Run Commands**:")
        for c in cmds[:8]:
            lines.append(f"  - `{c}`")
    else:
        lines.append("- **Execution Commands**: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`.")
    for key_id in ("hardware_software_env", "temperature_or_ambient_condition", "random_seed", "repeat_count"):
        ci = next((c for c in chk["essential_items_checklist"] if c["item_id"] == key_id), None)
        if ci:
            stat = "Verified in supplied sources (cross-check Table 1)" if ci["present_in_source"] else "[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]"
            lines.append(f"- **{ci['label']}**: {stat}")
    lines.append("")

    # Section 4: Three-Line Hyperparameter & Experimental Parameter Table
    lines.extend([
        "## 4. Table 1. Experimental Parameters and Provenance (Planned vs Actual Executed)",
        "",
        "| Parameter (Canonical Key) | Planned Config Value | Actual Executed Value | Unit | Source Anchor (`file:line`) | Audit Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for row in res["csv_rows"]:
        lines.append(
            f"| `{row['canonical_key']}` (`{row['raw_key']}`) | {row['planned_value']} | {row['actual_value']} | "
            f"{row['unit']} | `{row['source_file_line']}` | `{row['audit_status']}` |"
        )
    if aud["planned_vs_actual_conflicts"]:
        lines.extend(["", "**Mandatory Planned-vs-Actual Disclosures**:"])
        for cf in aud["planned_vs_actual_conflicts"]:
            lines.append(
                f"- `{cf['canonical_key']}`: planned `{cf['planned_value']}` (`{cf['planned_source']}`) vs actual executed `{cf['actual_value']}` (`{cf['actual_source']}`) — BOTH must be disclosed separately in prose."
            )
    lines.append("")

    # Section 5: Baselines, Controls & Evaluation
    lines.extend(["## 5. Baselines, Controls, and Evaluation Protocol", ""])
    base_item = next((c for c in chk["essential_items_checklist"] if c["item_id"] == "baseline_same_budget"), None)
    if base_item and base_item["present_in_source"]:
        lines.append("- Baseline/control comparison cues were detected in the supplied corpus; verify that compute/data/experimental budgets are matched and statistical units correspond to independent replicates.")
    else:
        lines.append("- Baseline models, matched-budget controls, and statistical hypothesis tests: `[UNRECORDED: AUTHOR_CONFIRM_REQUIRED]`.")
    lines.append("")

    # Section 6: Reproducibility Registry & Concrete Limitations
    lines.extend([
        "## 6. Reproducibility Registry (ELN Back-Links) and Operational Limitations",
        "",
        "| ELN ID | Source File | Format | Extracted Parameters | Status |",
        "| --- | --- | --- | ---: | --- |",
    ])
    for sf in res["source_files"]:
        st_lbl = f"DEGRADED ({sf.get('degraded_reason', 'fallback')})" if sf.get("degraded") else "VERIFIED"
        lines.append(f"| `{sf['eln_id']}` | `{sf['file']}` | `{sf['format']}` | {sf['extracted_param_count']} | {st_lbl} |")
    lines.append("")
    unrec = chk.get("unrecorded_author_confirm_required") or []
    if unrec:
        lines.append(f"- **Author Confirmation Queue (`{len(unrec)}` unrecorded item(s))**: " + ", ".join(f"`{u}`" for u in unrec) + ".")
    else:
        lines.append("- All eight essential reproducibility checklist items have source evidence; verify concrete operational failure boundaries prior to submission.")
    lines.append("- **Concrete Limitations & Failure Modes**: State exact operational regimes (sample size, domain shift, electrochemical/assay bounds, hardware constraints) where the reported workflow degrades rather than generic boilerplate.")
    lines.append("")
    return "\n".join(lines)


def _discover_draft(input_dir: Path) -> tuple[Path | None, str]:
    case_id = "case-01"
    cj = input_dir / "case.json"
    if cj.is_file():
        try:
            cobj = json.loads(cj.read_text(encoding="utf-8"))
            case_id = str(cobj.get("case_id") or case_id)
            for key in ("draft", "methods_draft", "manuscript", "primary_manuscript"):
                val = cobj.get(key)
                if isinstance(val, str) and (input_dir / val.strip()).is_file():
                    return input_dir / val.strip(), case_id
        except Exception:
            pass
    cands = [
        p for p in sorted(input_dir.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in {".md", ".tex", ".docx", ".xml", ".html", ".htm"}
        and p.name.lower() not in {"task.md", "readme.md"}
        and any(k in p.name.lower() for k in ("method", "draft", "experiment", "manuscript"))
    ]
    cands.sort(key=lambda p: -p.stat().st_size)
    return (cands[0] if cands else None), case_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multi-source Methods & Experiments parameter extractor, scaffold generator, and reproducibility auditor.")
    parser.add_argument("--input-dir", required=True, help="Directory with configs, scripts, logs, datasets, papers, lab notes, and optional draft.")
    parser.add_argument("--draft", default="", help="Optional Methods & Experiments draft path (.md/.tex/.docx/.txt/.pdf/.xml/.html).")
    parser.add_argument("--out-dir", required=True, help="Output directory for CSV, scaffold MD, JSONs, and result.json.")
    parser.add_argument("--case-id", default="", help="Override case_id.")
    parser.add_argument("--emit-scaffold", dest="emit_scaffold", action="store_true", default=True, help="Generate zero-hallucination six-element methods_scaffold.md (enabled by default).")
    parser.add_argument("--no-emit-scaffold", dest="emit_scaffold", action="store_false", help="Disable methods_scaffold.md generation.")
    args = parser.parse_args(argv)

    in_dir = Path(args.input_dir)
    case_contract = wio.load_case_contract(in_dir)
    if args.draft:
        draft_p: Path | None = Path(args.draft)
        _, det_cid = _discover_draft(in_dir)
        case_id = args.case_id or case_contract.get("case_id") or det_cid
    else:
        draft_p, det_cid = _discover_draft(in_dir)
        case_id = args.case_id or case_contract.get("case_id") or det_cid

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    res = audit_methods_and_experiments(in_dir, draft_p, case_id=case_id, case_contract=case_contract)

    # Strictly output CSV (NEVER .tsv)
    csv_path = out_dir / "experimental_parameters.csv"
    fieldnames = [
        "record_id", "canonical_key", "raw_key", "planned_value", "actual_value",
        "unit", "role", "source_file_line", "draft_reported_value", "audit_status",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in res["csv_rows"]:
            writer.writerow(row)

    (out_dir / "reproducibility_checklist.json").write_text(
        json.dumps(res["checklist"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "methods_audit.json").write_text(
        json.dumps(res["audit"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    chapter_md = render_methods_and_experiments_md(res, case_id, case_contract)
    (out_dir / "methods_and_experiments.md").write_text(chapter_md, encoding="utf-8")

    artifacts = [
        "result.json",
        "methods_and_experiments.md",
        "experimental_parameters.csv",
        "reproducibility_checklist.json",
        "methods_audit.json",
    ]
    if args.emit_scaffold:
        scaffold_md = render_methods_scaffold_md(res, case_id, case_contract)
        (out_dir / "methods_scaffold.md").write_text(scaffold_md, encoding="utf-8")
        artifacts.append("methods_scaffold.md")

    aud = res["audit"]
    sources_list = [
        {"file": sf["file"], "eln_id": sf["eln_id"], "format": sf["format"], "input_role": sf.get("input_role", "experimental_config_or_code_or_log")}
        for sf in res["source_files"]
    ]
    if draft_p:
        sources_list.append({"file": draft_p.name, "role": "methods_draft"})

    role_sum = res.get("input_role_summary", {})
    deliverable_obj: dict[str, Any] = {
        "type": "methods_and_experiments_complete_chapter_and_reproducibility_audit",
        "methods_and_experiments_md": "methods_and_experiments.md",
        "methods_chapter_summary": {
            "file": "methods_and_experiments.md",
            "char_count": len(chapter_md),
            "section_count": 6,
            "experimental_source_files": len(role_sum.get("experimental_config_or_code_or_log", [])),
            "background_literature_files": len(role_sum.get("background_literature_or_reference", [])),
            "unrecorded_items_count": len(res["checklist"]["unrecorded_author_confirm_required"]),
        },
        "parameters_csv": "experimental_parameters.csv",
        "reproducibility_checklist_json": "reproducibility_checklist.json",
        "methods_audit_json": "methods_audit.json",
        "six_elements_coverage": aud["six_elements_coverage"],
        "blocking_count": aud["blocking_count"],
    }
    if args.emit_scaffold:
        deliverable_obj["methods_scaffold_md"] = "methods_scaffold.md"

    result_payload = wio.build_writing_result(
        case_id=case_id,
        task_id="methods-experiments-writing",
        deliverable=deliverable_obj,
        preserved_facts={
            "draft_file": draft_p.name if draft_p else None,
            "extracted_parameter_records": aud["extracted_parameter_records_count"],
            "unique_canonical_parameters": aud["unique_canonical_parameters"],
            "dataset_profiles_count": len(res["checklist"]["dataset_profiles"]),
            "background_literature_count": len(res.get("background_literature", [])),
            "sample_sizes": res["invariants"]["sample_sizes"],
            "numbers_with_units_count": len(res["invariants"]["numbers_with_units"]),
        },
        evidence_map=res["evidence_map"],
        change_log=[
            {"finding_id": f["finding_id"], "code": f["code"], "location": f["location"], "status": "audit_flagged"}
            for f in aud["findings"]
        ],
        artifacts=artifacts,
        sources=sources_list,
        gaps=res["gaps"],
        limitations=[
            "Parameters absent from all supplied configs, scripts, logs, and notes are explicitly labeled [UNRECORDED: AUTHOR_CONFIRM_REQUIRED] and never fabricated.",
            "Background literature/reference files are pre-classified separately and never conflated with executed experiment configs/logs.",
            "Strict CSV/JSON/Markdown deliverables only; no tab-delimited artifacts are generated.",
        ],
        verification={
            "engine": "methods_repro_extractor.py",
            "methods_and_experiments_emitted": True,
            "scaffold_emitted": bool(args.emit_scaffold),
            "input_role_summary": role_sum,
            "planned_vs_actual_conflicts": aud["planned_vs_actual_conflicts_count"],
            "planned_reported_as_actual": aud["planned_reported_as_actual_count"],
            "config_key_miscopies": aud["config_key_miscopies_count"],
            "ungrounded_parameters": aud["ungrounded_parameters_count"],
            "missing_unrecorded_items": aud["missing_unrecorded_count"],
            "frontend_semantic_leaks": aud["frontend_semantic_leaks_count"],
            "blocking_count": aud["blocking_count"],
            "task_constraints_consumed": bool(case_contract.get("task_constraints")),
        },
        case_contract=case_contract,
    )
    (out_dir / "result.json").write_text(json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "methods_and_experiments_md": "methods_and_experiments.md",
        "extracted_records": aud["extracted_parameter_records_count"],
        "dataset_profiles": len(res["checklist"]["dataset_profiles"]),
        "pipeline_steps": len(res["checklist"]["pipeline_steps_detected"]),
        "background_literature_count": len(res.get("background_literature", [])),
        "planned_vs_actual_conflicts": aud["planned_vs_actual_conflicts_count"],
        "planned_reported_as_actual": aud["planned_reported_as_actual_count"],
        "config_key_miscopies": aud["config_key_miscopies_count"],
        "ungrounded_parameters": aud["ungrounded_parameters_count"],
        "missing_unrecorded_items": aud["missing_unrecorded_count"],
        "frontend_semantic_leaks": aud["frontend_semantic_leaks_count"],
        "blocking_count": aud["blocking_count"],
        "artifacts": artifacts,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
