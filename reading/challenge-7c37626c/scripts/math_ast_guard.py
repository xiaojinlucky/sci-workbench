"""Safe JATS/MathML parser, composite SI dimensional algebra, OCR noise interceptor, and AST verifier.

Pure Python 3.12 standard library baseline with optional enhancement when defusedxml or sympy
is installed. Zero mandatory third-party dependencies.

Key capabilities:
1. JATS/MathML nested XML extraction without double-counting (<disp-formula><alternatives><mml:math> + <tex-math>).
2. General composite SI dimensional algebra parser supporting arbitrary unit expressions, SI prefixes,
   7-dimensional SI exponent vectors (L, M, T, I, Theta, N, J), and exact SI scale factor / order-of-magnitude checks.
3. OCR/transcription noise interception (Pix2Text #142 class: injected CJK side-notes, tab/control corruption,
   mojibake, unbalanced delimiters/braces) that halts derivation instead of propagating corrupt tokens.
4. Safe AST numeric evaluator with Greek/subscript normalization and optional SymPy symbolic check
   with reserved-symbol isolation (E, I, S, N, O, Q, C).
"""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import Any
import xml.etree.ElementTree as ET

try:
    import defusedxml.ElementTree as SafeET  # type: ignore
except ImportError:
    SafeET = None

try:
    import sympy as sp  # type: ignore
except ImportError:
    sp = None

SYMPY_RESERVED = {"E", "I", "S", "N", "O", "Q", "C"}
DIM_AXES = ("L", "M", "T", "I", "Theta", "N", "J")

_SAFE_FUNCS: dict[str, Any] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "ln": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "sqrt": math.sqrt,
    "abs": abs,
    "pow": pow,
    "min": min,
    "max": max,
}
_SAFE_CONSTS: dict[str, float] = {"pi": math.pi, "e": math.e}
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_GREEK_TO_ASCII: dict[str, str] = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ϑ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda_", "μ": "mu", "µ": "mu", "ν": "nu", "ξ": "xi", "π": "pi",
    "ρ": "rho", "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "phi", "ϕ": "phi",
    "χ": "chi", "ψ": "psi", "ω": "omega",
    "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi",
    "Π": "Pi", "Σ": "Sigma", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega",
}

_UNICODE_OP_TO_LATEX: dict[str, str] = {
    "\u2212": "-",
    "\u2013": "-",
    "\u00d7": r"\times",
    "\u00b7": r"\cdot",
    "\u22c5": r"\cdot",
    "\u00b1": r"\pm",
    "\u2264": r"\le",
    "\u2265": r"\ge",
    "\u2260": r"\ne",
    "\u2248": r"\approx",
    "\u2192": r"\to",
    "\u221e": r"\infty",
    "\u2202": r"\partial",
    "\u2207": r"\nabla",
    "\u2211": r"\sum",
    "\u220f": r"\prod",
    "\u222b": r"\int",
}

_LATEX_NON_SYMBOLS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "exp", "log", "ln", "log10", "sqrt", "abs",
    "max", "min", "arg", "lim", "sup", "inf", "det", "tr", "diag", "mod",
    "sum", "prod", "int", "oint", "partial", "nabla", "cdot", "times", "pm",
    "le", "ge", "ne", "approx", "equiv", "propto", "to", "infty", "left", "right",
    "begin", "end", "matrix", "pmatrix", "bmatrix", "vmatrix", "aligned", "cases",
    "frac", "dfrac", "tfrac", "text", "mathrm", "mathbf", "mathit", "mathcal",
    "mathbb", "operatorname", "hat", "bar", "tilde", "widetilde", "overline",
    "underline", "vec", "quad", "qquad", "displaystyle", "limits", "nolimits",
}

# SI prefixes: ordered with 2-char prefix first to prevent ambiguous prefix matching
_SI_PREFIXES: tuple[tuple[str, float], ...] = (
    ("da", 1e1),
    ("Y", 1e24), ("Z", 1e21), ("E", 1e18), ("P", 1e15), ("T", 1e12),
    ("G", 1e9), ("M", 1e6), ("k", 1e3), ("h", 1e2),
    ("d", 1e-1), ("c", 1e-2), ("m", 1e-3), ("u", 1e-6),
    ("\u00b5", 1e-6), ("\u03bc", 1e-6),
    ("n", 1e-9), ("p", 1e-12), ("f", 1e-15), ("a", 1e-18), ("z", 1e-21), ("y", 1e-24),
)

# Canonical unit definitions: unit_symbol -> (SI_7_dim_tuple, scale_to_SI_base, prefixable)
# SI base vector order: (L[m], M[kg], T[s], I[A], Theta[K], N[mol], J[cd])
_UNIT_TABLE: dict[str, tuple[tuple[float, ...], float, bool]] = {
    # Dimensionless
    "1": ((0, 0, 0, 0, 0, 0, 0), 1.0, False),
    "dimensionless": ((0, 0, 0, 0, 0, 0, 0), 1.0, False),
    "rad": ((0, 0, 0, 0, 0, 0, 0), 1.0, True),
    "sr": ((0, 0, 0, 0, 0, 0, 0), 1.0, True),
    "%": ((0, 0, 0, 0, 0, 0, 0), 1e-2, False),
    "ppm": ((0, 0, 0, 0, 0, 0, 0), 1e-6, False),
    "ppb": ((0, 0, 0, 0, 0, 0, 0), 1e-9, False),
    "deg": ((0, 0, 0, 0, 0, 0, 0), math.pi / 180.0, False),
    "\u00b0": ((0, 0, 0, 0, 0, 0, 0), math.pi / 180.0, False),
    # SI base units (gram has scale 1e-3 so kg = 1.0)
    "m": ((1, 0, 0, 0, 0, 0, 0), 1.0, True),
    "g": ((0, 1, 0, 0, 0, 0, 0), 1e-3, True),
    "s": ((0, 0, 1, 0, 0, 0, 0), 1.0, True),
    "sec": ((0, 0, 1, 0, 0, 0, 0), 1.0, False),
    "A": ((0, 0, 0, 1, 0, 0, 0), 1.0, True),
    "K": ((0, 0, 0, 0, 1, 0, 0), 1.0, True),
    "mol": ((0, 0, 0, 0, 0, 1, 0), 1.0, True),
    "cd": ((0, 0, 0, 0, 0, 0, 1), 1.0, True),
    # Named SI derived units
    "Hz": ((0, 0, -1, 0, 0, 0, 0), 1.0, True),
    "N": ((1, 1, -2, 0, 0, 0, 0), 1.0, True),
    "Pa": ((-1, 1, -2, 0, 0, 0, 0), 1.0, True),
    "J": ((2, 1, -2, 0, 0, 0, 0), 1.0, True),
    "W": ((2, 1, -3, 0, 0, 0, 0), 1.0, True),
    "C": ((0, 0, 1, 1, 0, 0, 0), 1.0, True),
    "V": ((2, 1, -3, -1, 0, 0, 0), 1.0, True),
    "F": ((-2, -1, 4, 2, 0, 0, 0), 1.0, True),
    "Ohm": ((2, 1, -3, -2, 0, 0, 0), 1.0, True),
    "ohm": ((2, 1, -3, -2, 0, 0, 0), 1.0, True),
    "\u03a9": ((2, 1, -3, -2, 0, 0, 0), 1.0, True),
    "\u2126": ((2, 1, -3, -2, 0, 0, 0), 1.0, True),
    "S": ((-2, -1, 3, 2, 0, 0, 0), 1.0, True),
    "Wb": ((2, 1, -2, -1, 0, 0, 0), 1.0, True),
    "T": ((0, 1, -2, -1, 0, 0, 0), 1.0, True),
    "H": ((2, 1, -2, -2, 0, 0, 0), 1.0, True),
    "lm": ((0, 0, 0, 0, 0, 0, 1), 1.0, True),
    "lx": ((-2, 0, 0, 0, 0, 0, 1), 1.0, True),
    "Bq": ((0, 0, -1, 0, 0, 0, 0), 1.0, True),
    "Gy": ((2, 0, -2, 0, 0, 0, 0), 1.0, True),
    "Sv": ((2, 0, -2, 0, 0, 0, 0), 1.0, True),
    "kat": ((0, 0, -1, 0, 0, 1, 0), 1.0, True),
    # Accepted scientific non-SI units
    "L": ((3, 0, 0, 0, 0, 0, 0), 1e-3, True),
    "l": ((3, 0, 0, 0, 0, 0, 0), 1e-3, True),
    "min": ((0, 0, 1, 0, 0, 0, 0), 60.0, False),
    "h": ((0, 0, 1, 0, 0, 0, 0), 3600.0, False),
    "hr": ((0, 0, 1, 0, 0, 0, 0), 3600.0, False),
    "d": ((0, 0, 1, 0, 0, 0, 0), 86400.0, False),
    "day": ((0, 0, 1, 0, 0, 0, 0), 86400.0, False),
    "yr": ((0, 0, 1, 0, 0, 0, 0), 31557600.0, False),
    "eV": ((2, 1, -2, 0, 0, 0, 0), 1.602176634e-19, True),
    "cal": ((2, 1, -2, 0, 0, 0, 0), 4.184, True),
    "bar": ((-1, 1, -2, 0, 0, 0, 0), 1e5, True),
    "atm": ((-1, 1, -2, 0, 0, 0, 0), 101325.0, False),
    "Torr": ((-1, 1, -2, 0, 0, 0, 0), 101325.0 / 760.0, False),
    "mmHg": ((-1, 1, -2, 0, 0, 0, 0), 133.322387415, False),
    "M": ((-3, 0, 0, 0, 0, 1, 0), 1e3, True),  # molarity = mol / L = 1000 mol / m^3
    "Da": ((0, 1, 0, 0, 0, 0, 0), 1.66053906660e-27, True),
    "amu": ((0, 1, 0, 0, 0, 0, 0), 1.66053906660e-27, False),
    "\u00c5": ((1, 0, 0, 0, 0, 0, 0), 1e-10, False),
    "Angstrom": ((1, 0, 0, 0, 0, 0, 0), 1e-10, False),
    "t": ((0, 1, 0, 0, 0, 0, 0), 1e3, False),
    "tonne": ((0, 1, 0, 0, 0, 0, 0), 1e3, False),
    "Wh": ((2, 1, -2, 0, 0, 0, 0), 3600.0, True),
    "Ah": ((0, 0, 1, 1, 0, 0, 0), 3600.0, True),
}

# Safe lowercase aliases for common case-folded scientific units
_CASE_INSENSITIVE_ALIASES: dict[str, str] = {
    "hz": "Hz", "khz": "kHz", "mhz": "MHz", "ghz": "GHz",
    "pa": "Pa", "kpa": "kPa", "mpa": "MPa", "gpa": "GPa",
    "ev": "eV", "mev": "meV", "kev": "keV", "gev": "GeV",
    "kj": "kJ", "mj": "MJ", "gj": "GJ", "kcal": "kcal",
    "kw": "kW", "mw": "mW", "gw": "GW", "kwh": "kWh",
    "mv": "mV", "kv": "kV", "ma": "mA", "ua": "uA",
    "ml": "mL", "ul": "uL", "nl": "nL", "dl": "dL",
    "torr": "Torr", "mmhg": "mmHg", "angstrom": "Angstrom",
    "da": "Da", "kda": "kDa", "mda": "MDa",
    "j": "J", "w": "W", "v": "V", "a": "A", "k": "K", "n": "N",
}

_SUPERSCRIPT_MAP = str.maketrans({
    "\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3", "\u2074": "4",
    "\u2075": "5", "\u2076": "6", "\u2077": "7", "\u2078": "8", "\u2079": "9",
    "\u207b": "-", "\u207a": "+",
})

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_MOJIBAKE_RE = re.compile(r"(?:\ufffd|\u00ef\u00bb\u00bf|\u25a1|\u25af|Ã[©¨ª«¬®¯±²³¶·¸¹º»¼½¾]|â€[œ˜™¦—–])")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\t]")


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def safe_parse_xml_string(raw: str) -> ET.Element:
    """Parse XML safely using defusedxml when available, or XXE-guarded stdlib ElementTree."""
    if SafeET is not None:
        return SafeET.fromstring(raw)
    if re.search(r"<!(?:DOCTYPE|ENTITY)\b", raw, re.IGNORECASE):
        raise ValueError("Unsafe XML DOCTYPE/ENTITY declaration blocked in stdlib fallback mode")
    return ET.fromstring(raw)


def _extract_semantics_tex(math_node: ET.Element) -> str:
    """Return TeX annotation inside MathML <semantics><annotation encoding='*tex*'> if present."""
    for child in math_node.iter():
        if _strip_ns(child.tag).lower() == "annotation":
            enc = (child.attrib.get("encoding") or "").lower()
            if "tex" in enc:
                txt = (child.text or "").strip()
                if txt:
                    return txt
    return ""


def mathml_node_to_latex(node: ET.Element) -> str:
    """Recursively convert a MathML presentation tree into deterministic LaTeX."""
    tag = _strip_ns(node.tag).lower()
    children = list(node)
    text = (node.text or "").strip()

    if tag in ("annotation", "annotation-xml"):
        return ""
    if tag == "semantics":
        # Render only the primary MathML presentation child, never concatenate annotations
        for child in children:
            ctag = _strip_ns(child.tag).lower()
            if ctag not in ("annotation", "annotation-xml"):
                return mathml_node_to_latex(child)
        return ""
    if tag in ("math", "mrow", "mstyle", "mpadded", "mphantom", "menclose"):
        parts = [mathml_node_to_latex(c) for c in children]
        joined = " ".join(p for p in parts if p)
        return joined if joined else text
    if tag == "mspace":
        return " "
    if tag in ("mi", "mn", "mo", "mtext", "ms"):
        if tag == "mo" and text in ("\u2061", "\u2062"):
            return ""
        if tag == "mo" and text == "\u2063":
            return ","
        mapped = _UNICODE_OP_TO_LATEX.get(text, text)
        if tag == "mtext" and mapped:
            return f"\\text{{{mapped}}}"
        return mapped
    if tag == "mfrac" and len(children) >= 2:
        return f"\\frac{{{mathml_node_to_latex(children[0])}}}{{{mathml_node_to_latex(children[1])}}}"
    if tag == "msup" and len(children) >= 2:
        return f"{{{mathml_node_to_latex(children[0])}}}^{{{mathml_node_to_latex(children[1])}}}"
    if tag == "msub" and len(children) >= 2:
        return f"{{{mathml_node_to_latex(children[0])}}}_{{{mathml_node_to_latex(children[1])}}}"
    if tag == "msubsup" and len(children) >= 3:
        return (
            f"{{{mathml_node_to_latex(children[0])}}}"
            f"_{{{mathml_node_to_latex(children[1])}}}"
            f"^{{{mathml_node_to_latex(children[2])}}}"
        )
    if tag == "msqrt":
        inner = " ".join(p for p in (mathml_node_to_latex(c) for c in children) if p)
        return f"\\sqrt{{{inner}}}"
    if tag == "mroot" and len(children) >= 2:
        return f"\\sqrt[{mathml_node_to_latex(children[1])}]{{{mathml_node_to_latex(children[0])}}}"
    if tag == "mfenced":
        open_ch = node.attrib.get("open", "(")
        close_ch = node.attrib.get("close", ")")
        seps = node.attrib.get("separators", ",")
        sep_list = [ch for ch in seps if not ch.isspace()] or [","]
        rendered = [mathml_node_to_latex(c) for c in children]
        if not rendered:
            return f"{open_ch}{close_ch}"
        out = rendered[0]
        for i, item in enumerate(rendered[1:]):
            sep = sep_list[min(i, len(sep_list) - 1)]
            out += f"{sep} {item}"
        return f"{open_ch}{out}{close_ch}"
    if tag == "mtable":
        rows: list[str] = []
        for row in children:
            rtag = _strip_ns(row.tag).lower()
            if rtag in ("mtr", "mlabeledtr"):
                cells = [
                    mathml_node_to_latex(cell)
                    for cell in list(row)
                    if _strip_ns(cell.tag).lower() == "mtd"
                ]
                rows.append(" & ".join(cells))
            else:
                rows.append(mathml_node_to_latex(row))
        return "\\begin{matrix} " + " \\\\ ".join(rows) + " \\end{matrix}"
    if tag in ("mtr", "mlabeledtr"):
        return " & ".join(mathml_node_to_latex(c) for c in children)
    if tag == "mtd":
        return " ".join(p for p in (mathml_node_to_latex(c) for c in children) if p)
    if tag in ("mover", "munder", "munderover") and children:
        base = mathml_node_to_latex(children[0])
        if tag == "munder" and len(children) >= 2:
            return f"{base}_{{{mathml_node_to_latex(children[1])}}}"
        if tag == "mover" and len(children) >= 2:
            over = mathml_node_to_latex(children[1]).strip()
            accent_map = {"^": r"\hat", "\u02c6": r"\hat", "~": r"\tilde", "\u00af": r"\overline", "\u2192": r"\vec"}
            if over in accent_map:
                return f"{accent_map[over]}{{{base}}}"
            return f"{base}^{{{over}}}"
        if len(children) >= 3:
            return f"{base}_{{{mathml_node_to_latex(children[1])}}}^{{{mathml_node_to_latex(children[2])}}}"
        return base
    if tag == "mmultiscripts" and children:
        base = mathml_node_to_latex(children[0])
        post_subs: list[str] = []
        post_sups: list[str] = []
        pre_subs: list[str] = []
        pre_sups: list[str] = []
        in_pre = False
        idx = 1
        while idx < len(children):
            if _strip_ns(children[idx].tag).lower() == "mprescripts":
                in_pre = True
                idx += 1
                continue
            sub_s = mathml_node_to_latex(children[idx]) if _strip_ns(children[idx].tag).lower() != "none" else ""
            sup_s = ""
            if idx + 1 < len(children) and _strip_ns(children[idx + 1].tag).lower() != "mprescripts":
                if _strip_ns(children[idx + 1].tag).lower() != "none":
                    sup_s = mathml_node_to_latex(children[idx + 1])
                idx += 2
            else:
                idx += 1
            if in_pre:
                if sub_s:
                    pre_subs.append(sub_s)
                if sup_s:
                    pre_sups.append(sup_s)
            else:
                if sub_s:
                    post_subs.append(sub_s)
                if sup_s:
                    post_sups.append(sup_s)
        prefix = ""
        if pre_subs or pre_sups:
            prefix = f"{{}}_{{{' '.join(pre_subs)}}}^{{{' '.join(pre_sups)}}}"
        suffix = ""
        if post_subs:
            suffix += f"_{{{' '.join(post_subs)}}}"
        if post_sups:
            suffix += f"^{{{' '.join(post_sups)}}}"
        return f"{prefix}{{{base}}}{suffix}"
    return " ".join(p for p in ([text] + [mathml_node_to_latex(c) for c in children]) if p)


def _strip_tex_wrappers(tex: str) -> str:
    s = tex.strip()
    for pat in (
        r"^\$\$(.*)\$\$$",
        r"^\$(.*)\$$",
        r"^\\\[(.*)\\\]$",
        r"^\\\((.*)\\\)$",
        r"^\\begin\{equation\*?\}(.*)\\end\{equation\*?\}$",
        r"^\\begin\{align\*?\}(.*)\\end\{align\*?\}$",
    ):
        m = re.match(pat, s, re.S)
        if m:
            s = m.group(1).strip()
    return s


def extract_symbols_from_latex(latex: str) -> list[str]:
    """Extract candidate mathematical variable identifiers from LaTeX while excluding commands."""
    # Preserve Greek command names as normalized symbols before stripping other commands
    greek_cmds = re.findall(
        r"\\(alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|"
        r"lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|"
        r"Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Phi|Psi|Omega)\b",
        latex,
    )
    # Remove \text{...} and \mathrm{...} prose blocks from variable scan? Keep short 1-3 char mathrm symbols if subscripted
    no_text = re.sub(r"\\text\{[^}]*\}", " ", latex)
    # Capture subscripted variables like E_a, I_0, k_{cat}, w_0
    sub_vars = re.findall(r"\b([A-Za-z][A-Za-z0-9]*_(?:\{[A-Za-z0-9,]+\}|[A-Za-z0-9]+))\b", no_text)
    cleaned_subs = [sv.replace("{", "").replace("}", "") for sv in sub_vars]
    # Remove LaTeX commands
    stripped = re.sub(r"\\[A-Za-z]+", " ", no_text)
    # Also capture Unicode Greek chars
    unicode_greeks = [_GREEK_TO_ASCII[ch].rstrip("_") for ch in stripped if ch in _GREEK_TO_ASCII]
    # Plain identifiers
    plain = re.findall(r"\b([A-Za-z][A-Za-z0-9_]{0,12})\b", stripped)
    out: set[str] = set()
    for tok in plain + cleaned_subs + greek_cmds + unicode_greeks:
        t = tok.strip("_")
        if not t or t.isdigit() or t.lower() in _LATEX_NON_SYMBOLS:
            continue
        out.add(t)
    return sorted(out)


def inspect_formula_noise(latex: str, raw_text: str = "") -> dict[str, Any]:
    """Detect OCR/transcription corruption (Pix2Text #142 class) before derivation or computation."""
    issues: list[dict[str, str]] = []
    # 1. Control characters / literal tab (e.g. unescaped \tau -> <TAB>au)
    if _CONTROL_RE.search(latex):
        issues.append({
            "code": "CONTROL_CHAR_OR_TAB_CORRUPTION",
            "severity": "halt_for_verification",
            "message": "Literal tab or control character inside formula (possible corrupted escape like \\tau).",
        })
    # 2. Mojibake or replacement glyph
    if _MOJIBAKE_RE.search(latex) or _MOJIBAKE_RE.search(raw_text):
        issues.append({
            "code": "MOJIBAKE_OR_REPLACEMENT_GLYPH",
            "severity": "halt_for_verification",
            "message": "Replacement glyph or mojibake detected in formula transcription.",
        })
    # 3. Unexpected CJK characters in mathematical formula (Pix2Text #142 hallucinated Chinese side-notes)
    without_text_cmd = re.sub(r"\\text\{[^}]*\}", "", latex)
    cjk_outside = _CJK_RE.findall(without_text_cmd)
    cjk_inside_text = _CJK_RE.findall(latex)
    if cjk_outside:
        issues.append({
            "code": "UNEXPECTED_CJK_IN_FORMULA",
            "severity": "halt_for_verification",
            "message": f"Unexpected CJK characters outside \\text{{}} in formula: {''.join(cjk_outside[:12])!r}. Pause and verify against source image/XML.",
        })
    elif cjk_inside_text and re.search(r"[A-Za-z\\]", without_text_cmd):
        issues.append({
            "code": "SUSPICIOUS_CJK_ANNOTATION_IN_FORMULA",
            "severity": "halt_for_verification",
            "message": f"CJK text mixed into symbolic formula (Pix2Text-style side-note contamination): {''.join(cjk_inside_text[:12])!r}.",
        })
    # 4. Unbalanced brackets/braces
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i = 0
    unbalanced = False
    while i < len(latex):
        ch = latex[i]
        if ch == "\\" and i + 1 < len(latex):
            i += 2
            continue
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                unbalanced = True
                break
            stack.pop()
        i += 1
    if unbalanced or stack:
        issues.append({
            "code": "UNBALANCED_DELIMITERS",
            "severity": "halt_for_verification",
            "message": "Unbalanced parentheses, brackets, or braces detected in formula expression.",
        })
    # 5. Mismatched \left / \right
    left_cnt = len(re.findall(r"\\left(?![A-Za-z])", latex))
    right_cnt = len(re.findall(r"\\right(?![A-Za-z])", latex))
    if left_cnt != right_cnt:
        issues.append({
            "code": "MISMATCHED_LEFT_RIGHT",
            "severity": "halt_for_verification",
            "message": f"Mismatched \\left ({left_cnt}) and \\right ({right_cnt}) delimiters.",
        })
    # 6. Empty \frac or dangling script operator
    if re.search(r"\\frac\s*\{\s*\}\s*\{|\\frac\s*\{[^}]*\}\s*\{\s*\}", latex) or re.search(r"[\^_]\s*$", latex.strip()):
        issues.append({
            "code": "MALFORMED_LATEX_STRUCTURE",
            "severity": "halt_for_verification",
            "message": "Empty fraction argument or dangling subscript/superscript detected.",
        })

    severity = "halt_for_verification" if any(x["severity"] == "halt_for_verification" for x in issues) else ("warning" if issues else "clean")
    return {
        "noise_detected": bool(issues),
        "severity": severity,
        "issues": issues,
    }


def _top_level_math_nodes(container: ET.Element) -> list[ET.Element]:
    """Find <math> descendants without descending into nested <math> elements."""
    found: list[ET.Element] = []

    def _walk(node: ET.Element) -> None:
        for child in list(node):
            if _strip_ns(child.tag).lower() == "math":
                found.append(child)
            else:
                _walk(child)

    _walk(container)
    return found


def parse_xml_formulas(xml_path: str, source_file: str | None = None) -> list[dict[str, Any]]:
    """Safely parse JATS publisher XML / source-formulas.xml without double-counting nested representations."""
    with open(xml_path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    root = safe_parse_xml_string(raw)
    formulas: list[dict[str, Any]] = []
    idx = 0
    seen_ids: dict[str, int] = {}

    def _emit_container(elem: ET.Element, tag: str) -> None:
        nonlocal idx
        idx += 1
        raw_fid = (
            elem.attrib.get("id")
            or elem.attrib.get("source_id")
            or elem.attrib.get("{http://www.w3.org/XML/1998/namespace}id")
            or f"eq_{idx}"
        )
        if raw_fid in seen_ids:
            seen_ids[raw_fid] += 1
            fid = f"{raw_fid}_{seen_ids[raw_fid]}"
        else:
            seen_ids[raw_fid] = 1
            fid = raw_fid

        label_text = ""
        for ch in list(elem):
            if _strip_ns(ch.tag).lower() == "label":
                label_text = " ".join("".join(ch.itertext()).split())
                break

        math_nodes = [elem] if tag == "math" else _top_level_math_nodes(elem)
        tex_nodes = [
            sub for sub in elem.iter()
            if _strip_ns(sub.tag).lower() == "tex-math" and "".join(sub.itertext()).strip()
        ]

        mathml_parts = [mathml_node_to_latex(mn).strip() for mn in math_nodes]
        mathml_latex = " ; ".join(p for p in mathml_parts if p)

        tex_parts = [_strip_tex_wrappers("".join(tn.itertext())) for tn in tex_nodes]
        for mn in math_nodes:
            sem_tex = _extract_semantics_tex(mn)
            if sem_tex:
                tex_parts.append(_strip_tex_wrappers(sem_tex))
        tex_math = " ; ".join(p for p in tex_parts if p)

        # Build raw text excluding <label>
        raw_chunks: list[str] = []
        for sub in elem.iter():
            if _strip_ns(sub.tag).lower() == "label":
                continue
            if sub.text and sub.text.strip():
                raw_chunks.append(sub.text.strip())
        text_fallback = " ".join(raw_chunks)

        if mathml_latex and tex_math:
            rep_source = "mathml+tex-math"
            primary_latex = mathml_latex
        elif mathml_latex:
            rep_source = "mathml"
            primary_latex = mathml_latex
        elif tex_math:
            rep_source = "tex-math"
            primary_latex = tex_math
        else:
            rep_source = "raw_text"
            primary_latex = text_fallback

        if not primary_latex.strip():
            return

        symbols = extract_symbols_from_latex(primary_latex)
        noise = inspect_formula_noise(primary_latex, text_fallback)
        representation_conflict = False
        if mathml_latex and tex_math:
            sym_m = set(extract_symbols_from_latex(mathml_latex))
            sym_t = set(extract_symbols_from_latex(tex_math))
            if sym_m and sym_t and sym_m != sym_t:
                representation_conflict = True

        entry: dict[str, Any] = {
            "formula_id": fid,
            "source_file": source_file or xml_path,
            "xml_tag": tag,
            "label": label_text or None,
            "representation_source": rep_source,
            "has_mathml": bool(mathml_latex),
            "has_tex_math": bool(tex_math),
            "latex": primary_latex,
            "mathml_latex": mathml_latex or None,
            "tex_math": tex_math or None,
            "representation_symbol_divergence": representation_conflict,
            "symbols": symbols,
            "ocr_noise": noise,
            "verification_status": "halted_for_visual_check" if noise["severity"] == "halt_for_verification" else "parsed",
            "raw_text": text_fallback[:400],
        }
        formulas.append(entry)

    def _walk(node: ET.Element) -> None:
        tag = _strip_ns(node.tag).lower()
        if tag in ("disp-formula", "inline-formula", "formula", "math"):
            _emit_container(node, tag)
            return  # Do NOT descend: prevents double-counting nested <alternatives>/<mml:math>/<tex-math>
        for child in list(node):
            _walk(child)

    _walk(root)
    return formulas


# ---------------------------------------------------------------------------
# General Composite SI Dimensional Algebra Engine
# ---------------------------------------------------------------------------

def _resolve_unit_atom(atom: str) -> tuple[tuple[float, ...], float] | None:
    """Resolve a single unit token (with optional SI prefix) into (7-dim vector, SI scale)."""
    if atom in _UNIT_TABLE:
        dim, scale, _ = _UNIT_TABLE[atom]
        return dim, scale

    # Try exact SI prefix + prefixable base unit
    for prefix, p_scale in _SI_PREFIXES:
        if atom.startswith(prefix) and len(atom) > len(prefix):
            rest = atom[len(prefix):]
            if rest in _UNIT_TABLE:
                dim, base_scale, prefixable = _UNIT_TABLE[rest]
                if prefixable:
                    return dim, p_scale * base_scale

    # Case-insensitive fallback for known unambiguous spellings
    low = atom.lower()
    if low in _CASE_INSENSITIVE_ALIASES:
        canonical = _CASE_INSENSITIVE_ALIASES[low]
        if canonical != atom:
            return _resolve_unit_atom(canonical)
    return None


def _preprocess_unit_str(unit_str: str) -> str:
    s = unit_str.strip()
    s = s.strip("$")
    s = re.sub(r"\\(?:mathrm|text|operatorname)\{([^}]*)\}", r"\1", s)
    s = s.replace(r"\cdot", "*").replace(r"\times", "*")
    s = s.replace("\u00b7", "*").replace("\u22c5", "*").replace("\u00d7", "*").replace("\u2022", "*")
    s = s.replace("**", "^")
    # Convert Unicode superscript runs like s⁻² -> s^-2
    def _sup_repl(m: re.Match[str]) -> str:
        return "^" + m.group(0).translate(_SUPERSCRIPT_MAP)
    s = re.sub(r"[\u2070\u00b9\u00b2\u00b3\u2074-\u2079\u207a\u207b]+", _sup_repl, s)
    return s


_UNIT_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<NUMBER>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<OP>[*/^(){}\+\-])"
    r"|(?P<IDENT>[A-Za-z\u00b5\u03bc\u03a9\u2126\u00c5\u00b0%]+)"
    r")"
)


class _UnitParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            if text[pos].isspace():
                pos += 1
                continue
            m = _UNIT_TOKEN_RE.match(text, pos)
            if not m:
                raise ValueError(f"Unexpected character {text[pos]!r} at position {pos} in unit {text!r}")
            kind = m.lastgroup or ""
            self.tokens.append((kind, m.group(kind)))
            pos = m.end()
        self.idx = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.idx] if self.idx < len(self.tokens) else None

    def _consume(self, expected_val: str | None = None) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise ValueError("Unexpected end of unit expression")
        if expected_val is not None and tok[1] != expected_val:
            raise ValueError(f"Expected {expected_val!r}, got {tok[1]!r}")
        self.idx += 1
        return tok

    def parse(self) -> tuple[tuple[float, ...], float, list[str]]:
        if not self.tokens:
            raise ValueError("Empty unit expression")
        dim, scale, atoms = self._parse_expr()
        if self._peek() is not None:
            raise ValueError(f"Trailing unexpected token: {self._peek()!r}")
        return dim, scale, atoms

    def _parse_expr(self) -> tuple[tuple[float, ...], float, list[str]]:
        dim, scale, atoms = self._parse_term()
        while True:
            nxt = self._peek()
            if nxt is None or nxt[1] in (")", "}"):
                break
            if nxt[1] == "*":
                self._consume("*")
                d2, s2, a2 = self._parse_term()
                dim = tuple(a + b for a, b in zip(dim, d2))
                scale *= s2
                atoms.extend(a2)
            elif nxt[1] == "/":
                self._consume("/")
                d2, s2, a2 = self._parse_term()
                dim = tuple(a - b for a, b in zip(dim, d2))
                scale /= s2
                atoms.extend(a2)
            elif nxt[0] in ("IDENT", "NUMBER") or nxt[1] == "(":
                # Implicit multiplication (e.g. "m s^-2", "kg m^2 s^-3")
                d2, s2, a2 = self._parse_term()
                dim = tuple(a + b for a, b in zip(dim, d2))
                scale *= s2
                atoms.extend(a2)
            else:
                break
        return dim, scale, atoms

    def _parse_term(self) -> tuple[tuple[float, ...], float, list[str]]:
        dim, scale, atoms = self._parse_factor()
        nxt = self._peek()
        if nxt is not None and nxt[1] == "^":
            self._consume("^")
            exp = self._parse_exponent()
            dim = tuple(d * exp for d in dim)
            scale = scale ** exp
        return dim, scale, atoms

    def _parse_exponent(self) -> float:
        nxt = self._peek()
        if nxt is None:
            raise ValueError("Missing exponent after '^'")
        if nxt[1] in ("(", "{"):
            close = ")" if nxt[1] == "(" else "}"
            self._consume()
            val = self._parse_signed_number()
            if self._peek() is not None and self._peek()[1] == "/":  # type: ignore[index]
                self._consume("/")
                denom = self._parse_signed_number()
                if denom == 0:
                    raise ValueError("Zero denominator in unit exponent")
                val /= denom
            self._consume(close)
            return val
        return self._parse_signed_number()

    def _parse_signed_number(self) -> float:
        sign = 1.0
        nxt = self._peek()
        if nxt is not None and nxt[1] in ("+", "-"):
            if nxt[1] == "-":
                sign = -1.0
            self._consume()
        tok = self._consume()
        if tok[0] != "NUMBER":
            raise ValueError(f"Expected numeric exponent, got {tok[1]!r}")
        return sign * float(tok[1])

    def _parse_factor(self) -> tuple[tuple[float, ...], float, list[str]]:
        nxt = self._peek()
        if nxt is None:
            raise ValueError("Unexpected end of unit factor")
        if nxt[1] == "(":
            self._consume("(")
            res = self._parse_expr()
            self._consume(")")
            return res
        if nxt[0] == "NUMBER":
            self._consume()
            val = float(nxt[1])
            if val <= 0:
                raise ValueError("Numeric scale factor in unit must be positive")
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), val, [nxt[1]]
        if nxt[0] == "IDENT":
            self._consume()
            atom = nxt[1]
            resolved = _resolve_unit_atom(atom)
            if resolved is None:
                raise KeyError(f"Unknown unit symbol: {atom!r}")
            return resolved[0], resolved[1], [atom]
        raise ValueError(f"Unexpected token in unit factor: {nxt!r}")


def parse_unit_expression(unit_str: str) -> dict[str, Any]:
    """Parse arbitrary composite SI unit string into 7-dim vector and SI base scale factor."""
    cleaned = _preprocess_unit_str(unit_str)
    try:
        dim, scale, atoms = _UnitParser(cleaned).parse()
    except KeyError as exc:
        return {"ok": False, "status": "unknown_unit", "raw": unit_str, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status": "parse_error", "raw": unit_str, "error": str(exc)}

    rounded_dim = [int(round(v)) if abs(v - round(v)) < 1e-9 else round(v, 6) for v in dim]
    return {
        "ok": True,
        "status": "parsed",
        "raw": unit_str,
        "normalized": cleaned,
        "dimension_vector": rounded_dim,
        "dimension_named": {ax: val for ax, val in zip(DIM_AXES, rounded_dim) if val != 0},
        "si_scale_factor": scale,
        "log10_si_scale": round(math.log10(scale), 6) if scale > 0 else None,
        "atoms": atoms,
    }


def check_dimensional_balance(lhs_unit: str, rhs_unit: str) -> dict[str, Any]:
    """Compare arbitrary composite SI units for dimensional equivalence and order-of-magnitude scale."""
    u1 = parse_unit_expression(lhs_unit)
    u2 = parse_unit_expression(rhs_unit)
    if not u1["ok"] or not u2["ok"]:
        return {
            "status": "unknown_unit" if (u1.get("status") == "unknown_unit" or u2.get("status") == "unknown_unit") else "parse_error",
            "lhs_unit": lhs_unit,
            "rhs_unit": rhs_unit,
            "consistent": None,
            "lhs_error": u1.get("error"),
            "rhs_error": u2.get("error"),
        }

    d1 = u1["dimension_vector"]
    d2 = u2["dimension_vector"]
    consistent = all(abs(a - b) < 1e-9 for a, b in zip(d1, d2))
    dim_delta = {
        ax: (int(round(a - b)) if abs((a - b) - round(a - b)) < 1e-9 else round(a - b, 6))
        for ax, a, b in zip(DIM_AXES, d1, d2)
        if abs(a - b) >= 1e-9
    }
    scale_ratio = None
    oom_gap = None
    scale_exact = None
    warning = None
    if consistent and u2["si_scale_factor"] > 0:
        scale_ratio = u1["si_scale_factor"] / u2["si_scale_factor"]
        oom_gap = round(math.log10(scale_ratio), 6) if scale_ratio > 0 else None
        scale_exact = abs(scale_ratio - 1.0) <= 1e-9
        if not scale_exact:
            warning = (
                f"SI dimensions match, but base scale differs by factor {scale_ratio:.6g} "
                f"(order-of-magnitude gap {oom_gap:+.3f}): verify unit prefix/conversion factor."
            )
    elif not consistent:
        warning = f"Dimensional mismatch across SI axes: {dim_delta}"

    return {
        "status": "checked",
        "lhs_unit": lhs_unit,
        "rhs_unit": rhs_unit,
        "lhs_dim": d1,
        "rhs_dim": d2,
        "lhs_si_scale": u1["si_scale_factor"],
        "rhs_si_scale": u2["si_scale_factor"],
        "consistent": consistent,
        "dimension_delta": dim_delta,
        "scale_ratio": round(scale_ratio, 12) if scale_ratio is not None else None,
        "order_of_magnitude_gap": oom_gap,
        "scale_exact_match": scale_exact,
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# Safe AST Numeric & Optional SymPy Symbolic Verifier
# ---------------------------------------------------------------------------

def normalize_math_expr(expr: str) -> str:
    """Normalize Greek symbols, subscripts, and common LaTeX constructs into Python-AST syntax."""
    s = expr.strip()
    for g_ch, g_name in _GREEK_TO_ASCII.items():
        s = s.replace(g_ch, g_name)
    s = s.replace("\u2212", "-").replace("\u00d7", "*").replace("\u00b7", "*").replace("\u22c5", "*")
    s = re.sub(r"\\cdot\b|\\times\b", "*", s)
    s = re.sub(r"\\(sin|cos|tan|exp|log|ln|sqrt|pi)\b", r"\1", s)
    # Handle simple \frac{a}{b} iteratively
    for _ in range(6):
        new_s = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"((\1)/(\2))", s)
        if new_s == s:
            break
        s = new_s
    # Handle \sqrt{x}
    for _ in range(6):
        new_s = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", s)
        if new_s == s:
            break
        s = new_s
    # Normalize subscript braces like E_{a} -> E_a
    s = re.sub(r"([A-Za-z][A-Za-z0-9]*)_\{([A-Za-z0-9_]+)\}", r"\1_\2", s)
    # Normalize exponent braces like x^{2} -> x**(2)
    s = re.sub(r"\^\{([^{}]+)\}", r"**(\1)", s)
    s = s.replace("^", "**")
    return s


def safe_eval_ast(expr: str, env: dict[str, float]) -> float:
    """Strictly evaluate an arithmetic/scientific expression via Python AST without eval()."""
    cleaned = normalize_math_expr(expr)
    norm_env = {normalize_math_expr(k): float(v) for k, v in env.items()}
    tree = ast.parse(cleaned, mode="eval")

    def _visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in norm_env:
                return float(norm_env[node.id])
            if node.id in _SAFE_CONSTS:
                return _SAFE_CONSTS[node.id]
            raise KeyError(f"unbound variable: {node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return float(_BIN_OPS[type(node.op)](_visit(node.left), _visit(node.right)))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return float(_UNARY_OPS[type(node.op)](_visit(node.operand)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCS:
            args = [_visit(a) for a in node.args]
            return float(_SAFE_FUNCS[node.func.id](*args))
        raise ValueError(f"unsupported AST construct: {type(node).__name__}")

    val = _visit(tree)
    if not math.isfinite(val):
        raise ValueError(f"non-finite evaluation result: {val}")
    return val


def check_equivalence(expr1: str, expr2: str, test_env: dict[str, float] | None = None) -> dict[str, Any]:
    """Verify symbolic equivalence via SymPy (if installed) and AST numeric cross-check."""
    noise1 = inspect_formula_noise(expr1)
    noise2 = inspect_formula_noise(expr2)
    if noise1["severity"] == "halt_for_verification" or noise2["severity"] == "halt_for_verification":
        return {
            "expr1": expr1,
            "expr2": expr2,
            "method": "halted_ocr_noise",
            "equivalent": None,
            "noise_issues": noise1["issues"] + noise2["issues"],
        }

    n1 = normalize_math_expr(expr1)
    n2 = normalize_math_expr(expr2)
    result: dict[str, Any] = {
        "expr1": expr1,
        "expr2": expr2,
        "method": "ast_numeric",
        "equivalent": None,
    }
    if sp is not None:
        try:
            combined = n1 + " " + n2
            local_dict = {
                name: sp.Symbol(name)
                for name in SYMPY_RESERVED
                if re.search(r"\b" + name + r"\b", combined)
            }
            e1 = sp.sympify(n1, locals=local_dict)
            e2 = sp.sympify(n2, locals=local_dict)
            diff = sp.simplify(e1 - e2)
            result["method"] = "sympy_symbolic"
            result["symbolic_diff"] = str(diff)
            result["equivalent"] = bool(diff == 0)
        except Exception as exc:
            result["sympy_note"] = str(exc)

    if test_env:
        try:
            v1 = safe_eval_ast(expr1, test_env)
            v2 = safe_eval_ast(expr2, test_env)
            abs_err = abs(v1 - v2)
            rel_err = abs_err / max(1e-12, abs(v1), abs(v2))
            result["numeric_v1"] = round(v1, 10)
            result["numeric_v2"] = round(v2, 10)
            result["rel_error"] = round(rel_err, 12)
            if result["equivalent"] is None:
                result["equivalent"] = rel_err < 1e-9
        except Exception as exc:
            result["numeric_error"] = str(exc)
    return result
