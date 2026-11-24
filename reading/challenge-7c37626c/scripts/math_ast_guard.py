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

_GREEK_CANONICAL_TO_UNICODE: dict[str, list[str]] = {}
for _gch, _gname in _GREEK_TO_ASCII.items():
    _canon = _gname.rstrip("_")
    _GREEK_CANONICAL_TO_UNICODE.setdefault(_canon, []).append(_gch)
_GREEK_CANONICAL_TO_UNICODE["varepsilon"] = ["ε", "ϵ"]
_GREEK_CANONICAL_TO_UNICODE["vartheta"] = ["θ", "ϑ"]
_GREEK_CANONICAL_TO_UNICODE["varphi"] = ["φ", "ϕ"]

_SUBSCRIPT_UNICODE_MAP: dict[str, str] = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
    "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}

MATHEMATICAL_CONSTANTS = {"pi", "e"}


def build_symbol_variants(sym: str) -> list[str]:
    """Generate Unicode, LaTeX, and subscript-normalized lookup variants for a symbol."""
    variants: list[str] = []

    def _add(v: str) -> None:
        if v and v not in variants:
            variants.append(v)

    _add(sym)
    if "_" in sym:
        base, sub = sym.split("_", 1)
        base_forms = [base]
        if base in _GREEK_CANONICAL_TO_UNICODE:
            base_forms.append("\\" + base)
            base_forms.extend(_GREEK_CANONICAL_TO_UNICODE[base])
        sub_uni = "".join(_SUBSCRIPT_UNICODE_MAP.get(ch, "") for ch in sub)
        has_uni_sub = len(sub_uni) == len(sub) and bool(sub_uni)
        for bf in base_forms:
            _add(f"{bf}_{sub}")
            _add(f"{bf}_{{{sub}}}")
            _add(f"{{{bf}}}_{{{sub}}}")
            if has_uni_sub:
                _add(f"{bf}{sub_uni}")
            if len(bf) == 1 and len(sub) <= 3:
                _add(f"{bf}{sub}")
    else:
        if sym in _GREEK_CANONICAL_TO_UNICODE:
            _add("\\" + sym)
            for u in _GREEK_CANONICAL_TO_UNICODE[sym]:
                _add(u)
        elif sym in _GREEK_TO_ASCII:
            canon = _GREEK_TO_ASCII[sym].rstrip("_")
            _add(canon)
            _add("\\" + canon)
    return variants

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
    "dec": ((0, 0, 0, 0, 0, 0, 0), 1.0, False),       # logarithmic decade (dimensionless, e.g. mV/dec)
    "decade": ((0, 0, 0, 0, 0, 0, 0), 1.0, False),
    "rpm": ((0, 0, -1, 0, 0, 0, 0), 1.0 / 60.0, False),
    "inhabitants": ((0, 0, 0, 0, 0, 0, 0), 1.0, False),
    "cases": ((0, 0, 0, 0, 0, 0, 0), 1.0, False),
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

# Safe lowercase aliases for common case-folded scientific units (never fold single-letter symbols like 'a'/'j'/'k'/'n')
_CASE_INSENSITIVE_ALIASES: dict[str, str] = {
    "hz": "Hz", "khz": "kHz", "mhz": "MHz", "ghz": "GHz",
    "pa": "Pa", "kpa": "kPa", "mpa": "MPa", "gpa": "GPa",
    "ev": "eV", "mev": "meV", "kev": "keV", "gev": "GeV",
    "kj": "kJ", "mj": "MJ", "gj": "GJ", "kcal": "kcal",
    "kw": "kW", "mw": "mW", "gw": "GW", "kwh": "kWh",
    "mv": "mV", "kv": "kV", "ma": "mA", "ua": "uA",
    "ml": "mL", "ul": "uL", "nl": "nL", "dl": "dL",
    "torr": "Torr", "mmhg": "mmHg", "angstrom": "Angstrom",
    "kda": "kDa", "mda": "MDa",
}

_UNIT_STOPWORDS: set[str] = {
    "a", "an", "as", "at", "by", "in", "is", "of", "on", "or", "to",
    "the", "and", "for", "per", "from", "with", "into", "that", "this",
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


_GREEK_CMD_NAMES = (
    "alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|"
    "lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|"
    "Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Phi|Psi|Omega"
)


def extract_symbols_from_latex(latex: str) -> list[str]:
    """Extract normalized mathematical symbols (preserving braced/Greek subscripts without fragmenting)."""
    work = re.sub(r"\\text\{[^}]*\}", " ", latex)
    work = re.sub(r"\\(?:begin|end)\{[^}]*\}", " ", work)
    # Normalize Unicode Greek characters to canonical ASCII names before subscript capture
    for gch, gname in _GREEK_TO_ASCII.items():
        canon = gname.rstrip("_")
        work = work.replace(gch, f" \\{canon} ")

    out: set[str] = set()

    # 1. Capture braced-base or plain-base Greek/Latin subscripted symbols first so they don't fragment
    #    e.g., {E}_{a}, {G}_{x}, {D}_{1}^{2}, \eta_{0}, \Delta_{G}, E_a, I_0, k_{B}, k_{cat}
    sub_patterns = (
        re.compile(rf"\{{\s*\\({_GREEK_CMD_NAMES})\s*\}}\s*_\s*\{{\s*([A-Za-z0-9,]+)\s*\}}"),
        re.compile(rf"\\({_GREEK_CMD_NAMES})\s*_\s*(?:\{{\s*([A-Za-z0-9,]+)\s*\}}|([A-Za-z0-9]+))"),
        re.compile(r"\{\s*([A-Za-z][A-Za-z0-9]*)\s*\}\s*_\s*\{\s*([A-Za-z0-9,]+)\s*\}"),
        re.compile(r"\b([A-Za-z][A-Za-z0-9]*)\s*_\s*(?:\{\s*([A-Za-z0-9,]+)\s*\}|([A-Za-z0-9]+))"),
    )
    for pat in sub_patterns:
        def _grab_sub(m: re.Match[str]) -> str:
            base = m.group(1).strip()
            sub = (m.group(2) if m.group(2) is not None else m.group(3)).strip().replace(",", "_")
            if base.lower() not in _LATEX_NON_SYMBOLS:
                out.add(f"{base}_{sub}")
            return " "
        work = pat.sub(_grab_sub, work)

    # 2. Capture remaining standalone Greek commands
    def _grab_greek(m: re.Match[str]) -> str:
        out.add(m.group(1))
        return " "

    work = re.sub(rf"\\({_GREEK_CMD_NAMES})\b", _grab_greek, work)

    # 3. Strip remaining LaTeX commands and extract plain identifiers
    stripped = re.sub(r"\\[A-Za-z]+", " ", work)
    for tok in re.findall(r"\b([A-Za-z][A-Za-z0-9_]{0,12})\b", stripped):
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
    #    Distinguish legitimate Chinese text inside \text{}/\mathrm{}/\operatorname{} from raw math-mode CJK or OCR side-note markers.
    text_mode_blocks = re.findall(r"\\(?:text|mathrm|operatorname)\{([^}]*)\}", latex)
    without_text_cmd = re.sub(r"\\(?:text|mathrm|operatorname)\{[^}]*\}", "", latex)
    cjk_outside = _CJK_RE.findall(without_text_cmd)
    _SIDENOTE_RE = re.compile(r"(?:旁注|见第|第\s*\d+\s*页|注[:：]|校验|\.pdf|CMYK|Equaçao)", re.I)
    suspicious_text_blocks = [tb for tb in text_mode_blocks if _CJK_RE.search(tb) and _SIDENOTE_RE.search(tb)]
    if cjk_outside:
        issues.append({
            "code": "UNEXPECTED_CJK_IN_FORMULA",
            "severity": "halt_for_verification",
            "message": f"Unexpected CJK characters outside text-mode wrappers in math mode: {''.join(cjk_outside[:12])!r}. Pause and verify against source image/XML.",
        })
    elif suspicious_text_blocks:
        issues.append({
            "code": "SUSPICIOUS_CJK_ANNOTATION_IN_FORMULA",
            "severity": "halt_for_verification",
            "message": f"Pix2Text-style CJK side-note/OCR artifact inside text command: {suspicious_text_blocks[0][:40]!r}.",
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
    if (
        re.search(r"\\frac\s*\{\s*\}\s*\{|\\frac\s*\{(?:[^{}]|\{[^{}]*\})*\}\s*\{\s*\}", latex)
        or re.search(r"[\^_]\s*$", latex.strip())
    ):
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
    if not atom or "_" in atom or atom in _UNIT_STOPWORDS:
        return None
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

    # Case-insensitive fallback ONLY for multi-char unambiguous spellings (never fold single letters)
    if len(atom) >= 2:
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
    raw_stripped = (unit_str or "").strip()
    if not raw_stripped or "_" in raw_stripped or raw_stripped in _UNIT_STOPWORDS:
        return {
            "ok": False,
            "status": "unknown_unit",
            "raw": unit_str,
            "error": f"Rejected non-unit or subscripted symbol token: {unit_str!r}",
        }
    cleaned = _preprocess_unit_str(unit_str)
    if "_" in cleaned:
        return {
            "ok": False,
            "status": "unknown_unit",
            "raw": unit_str,
            "error": f"Rejected subscripted symbol in unit expression: {unit_str!r}",
        }
    try:
        dim, scale, atoms = _UnitParser(cleaned).parse()
    except KeyError as exc:
        return {"ok": False, "status": "unknown_unit", "raw": unit_str, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status": "parse_error", "raw": unit_str, "error": str(exc)}

    rounded_dim = [int(round(v)) if abs(v - round(v)) < 1e-9 else round(v, 6) for v in dim]
    sem_notes: list[str] = []
    if any(a in ("dec", "decade") for a in atoms):
        sem_notes.append("dec/decade treated as dimensionless logarithmic decade (e.g., Tafel slope mV/dec has voltage dimension V)")
    if any(a in ("inhabitants", "cases") for a in atoms):
        sem_notes.append("count/population denominator treated as dimensionless epidemiological basis")
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
        "semantic_unit_notes": sem_notes,
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


def _extract_ast_free_variables(expr: str) -> tuple[bool, set[str], str | None]:
    """Parse normalized expression into safe AST and return (parseable, free_var_names, error)."""
    cleaned = normalize_math_expr(expr)
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        return False, set(), f"syntax_error: {exc.msg}"

    free_vars: set[str] = set()

    def _walk(node: ast.AST) -> str | None:
        if isinstance(node, ast.Expression):
            return _walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return None
        if isinstance(node, ast.Name):
            if node.id not in _SAFE_CONSTS:
                free_vars.add(node.id)
            return None
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _walk(node.left) or _walk(node.right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _walk(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCS:
            for arg in node.args:
                err = _walk(arg)
                if err:
                    return err
            return None
        if isinstance(node, ast.Call):
            fname = ast.unparse(node.func) if hasattr(ast, "unparse") else type(node.func).__name__
            return f"unsupported_callable: {fname}"
        return f"unsupported_ast_node: {type(node).__name__}"

    err = _walk(tree)
    if err:
        return False, free_vars, err
    return True, free_vars, None


_DETERMINISTIC_DOMAIN_PROFILES: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("positive_small", (0.073, 0.137, 0.193, 0.257, 0.311, 0.419, 0.523, 0.641)),
    ("positive_unit_near", (0.817, 1.193, 1.471, 1.831, 2.137, 2.617, 3.079, 3.541)),
    ("positive_mid", (2.371, 3.893, 5.171, 6.733, 8.291, 9.613, 11.47, 13.19)),
    ("positive_large", (17.31, 23.77, 31.19, 43.63, 59.17, 71.83, 89.29, 107.4)),
    ("negative_small", (-0.089, -0.173, -0.269, -0.353, -0.461, -0.577, -0.683, -0.797)),
    ("negative_mid", (-1.43, -2.79, -4.13, -5.87, -7.31, -8.93, -10.4, -12.7)),
    ("mixed_sign_a", (0.431, -0.619, 1.733, -2.141, 3.257, -0.887, 4.913, -1.531)),
    ("mixed_sign_b", (-0.517, 0.913, -1.617, 2.819, -3.413, 1.279, -0.337, 5.123)),
)


def _multi_domain_equivalence_probe(
    expr1: str,
    expr2: str,
    seed_env: dict[str, float] | None = None,
    rel_tol: float = 1e-9,
    min_valid_samples: int = 3,
) -> dict[str, Any]:
    """Deterministic multi-point cross-domain boundary sampling when SymPy is unavailable."""
    ok1, vars1, err1 = _extract_ast_free_variables(expr1)
    ok2, vars2, err2 = _extract_ast_free_variables(expr2)
    if not ok1 or not ok2:
        return {
            "verifiable": False,
            "method": "not_verifiable",
            "equivalent": None,
            "reason": f"unparseable_or_unsupported_symbolic_expr ({err1 or err2})",
            "samples_tested": 0,
            "domains_covered": [],
        }

    all_vars = sorted(vars1 | vars2)
    if not all_vars:
        try:
            v1 = safe_eval_ast(expr1, {})
            v2 = safe_eval_ast(expr2, {})
            abs_err = abs(v1 - v2)
            rel_err = abs_err / max(1e-12, abs(v1), abs(v2))
            return {
                "verifiable": True,
                "method": "ast_constant_exact",
                "equivalent": rel_err < rel_tol or abs_err < 1e-12,
                "samples_tested": 1,
                "domains_covered": ["constant"],
                "numeric_v1": round(v1, 10),
                "numeric_v2": round(v2, 10),
                "rel_error": round(rel_err, 12),
                "max_rel_error": round(rel_err, 12),
            }
        except Exception as exc:
            return {
                "verifiable": False,
                "method": "not_verifiable",
                "equivalent": None,
                "reason": f"constant_eval_failed: {exc}",
                "samples_tested": 0,
                "domains_covered": [],
            }

    candidate_points: list[tuple[str, dict[str, float]]] = []
    if seed_env:
        norm_seed = {normalize_math_expr(k): float(v) for k, v in seed_env.items()}
        if all(v in norm_seed for v in all_vars):
            for tag, factor in (("seed_perturb_a", 1.137), ("seed_perturb_b", 0.793), ("seed_perturb_c", 1.419)):
                pt = {}
                for i, vn in enumerate(all_vars):
                    base = norm_seed[vn]
                    shift = 0.031 * (i + 1)
                    pt[vn] = base * (factor + 0.017 * i) if abs(base) > 1e-9 else shift
                candidate_points.append((tag, pt))

    for domain_name, palette in _DETERMINISTIC_DOMAIN_PROFILES:
        pt = {vn: palette[(i * 3 + len(vn)) % len(palette)] * (1.0 + 0.037 * (i // len(palette))) for i, vn in enumerate(all_vars)}
        candidate_points.append((domain_name, pt))

    valid_samples = 0
    domains_covered: list[str] = []
    domain_asymmetries: list[str] = []
    max_rel_err = 0.0
    first_vals: tuple[float, float, float] | None = None

    for dname, pt in candidate_points:
        e1_err: str | None = None
        e2_err: str | None = None
        v1 = v2 = 0.0
        try:
            v1 = safe_eval_ast(expr1, pt)
        except Exception as exc:
            e1_err = str(exc)
        try:
            v2 = safe_eval_ast(expr2, pt)
        except Exception as exc:
            e2_err = str(exc)

        if e1_err and e2_err:
            continue
        if bool(e1_err) != bool(e2_err):
            domain_asymmetries.append(f"{dname}: lhs_err={e1_err!r}, rhs_err={e2_err!r}")
            continue

        abs_err = abs(v1 - v2)
        rel_err = abs_err / max(1e-12, abs(v1), abs(v2))
        valid_samples += 1
        domains_covered.append(dname)
        if first_vals is None:
            first_vals = (v1, v2, rel_err)
        if rel_err > max_rel_err:
            max_rel_err = rel_err

        if rel_err >= rel_tol and abs_err >= 1e-12:
            return {
                "verifiable": True,
                "method": "ast_multi_domain_sampling",
                "equivalent": False,
                "samples_tested": valid_samples,
                "domains_covered": domains_covered,
                "counterexample_domain": dname,
                "counterexample_env": {k: round(v, 6) for k, v in pt.items()},
                "numeric_v1": round(v1, 10),
                "numeric_v2": round(v2, 10),
                "rel_error": round(rel_err, 12),
                "max_rel_error": round(max_rel_err, 12),
            }

    if valid_samples < min_valid_samples:
        return {
            "verifiable": False,
            "method": "not_verifiable",
            "equivalent": None,
            "reason": f"insufficient_valid_domain_samples ({valid_samples} < {min_valid_samples}); free_vars={all_vars}",
            "samples_tested": valid_samples,
            "domains_covered": domains_covered,
            "domain_asymmetries": domain_asymmetries[:4],
        }

    if domain_asymmetries:
        # Domain asymmetry on negative/mixed sign branches (e.g. log(x/y) vs log(x)-log(y) when x,y<0)
        # is a valid domain restriction when all positive-domain & seed-neighborhood samples match identically.
        non_sign_asym = [da for da in domain_asymmetries if not da.startswith(("negative_", "mixed_sign_"))]
        if non_sign_asym:
            return {
                "verifiable": True,
                "method": "ast_multi_domain_sampling",
                "equivalent": False,
                "reason": "domain_asymmetry_between_expressions",
                "samples_tested": valid_samples,
                "domains_covered": domains_covered,
                "domain_asymmetries": domain_asymmetries[:4],
                "max_rel_error": round(max_rel_err, 12),
            }

    v1_0, v2_0, r0 = first_vals if first_vals is not None else (0.0, 0.0, 0.0)
    out: dict[str, Any] = {
        "verifiable": True,
        "method": "ast_multi_domain_sampling",
        "equivalent": True,
        "samples_tested": valid_samples,
        "domains_covered": domains_covered,
        "numeric_v1": round(v1_0, 10),
        "numeric_v2": round(v2_0, 10),
        "rel_error": round(r0, 12),
        "max_rel_error": round(max_rel_err, 12),
    }
    if domain_asymmetries:
        out["domain_restriction_note"] = f"Equivalent on valid positive/real domain; sign-restricted asymmetries: {domain_asymmetries[:2]}"
    return out


def check_equivalence(expr1: str, expr2: str, test_env: dict[str, float] | None = None) -> dict[str, Any]:
    """Verify symbolic equivalence via SymPy (if installed) or deterministic multi-domain AST sampling."""
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
        "method": "not_verifiable",
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
                if rel_err >= 1e-9 and abs_err >= 1e-12:
                    result["method"] = "ast_numeric"
                    result["equivalent"] = False
                else:
                    # Guard against single-point coincidence (e.g. x == x**2 at x=1) via multi-domain probe
                    probe = _multi_domain_equivalence_probe(expr1, expr2, seed_env=test_env)
                    result["method"] = probe["method"]
                    result["equivalent"] = probe["equivalent"]
                    result["samples_tested"] = probe["samples_tested"]
                    result["domains_covered"] = probe["domains_covered"]
                    if "max_rel_error" in probe:
                        result["max_rel_error"] = probe["max_rel_error"]
                    if "counterexample_domain" in probe:
                        result["counterexample_domain"] = probe["counterexample_domain"]
                        result["counterexample_env"] = probe["counterexample_env"]
                    if "reason" in probe:
                        result["verdict_reason"] = probe["reason"]
        except Exception as exc:
            result["numeric_error"] = str(exc)

    if result["equivalent"] is None:
        probe = _multi_domain_equivalence_probe(expr1, expr2, seed_env=test_env)
        result["method"] = probe["method"]
        result["equivalent"] = probe["equivalent"]
        result["samples_tested"] = probe["samples_tested"]
        result["domains_covered"] = probe["domains_covered"]
        for k in ("numeric_v1", "numeric_v2", "rel_error", "max_rel_error", "counterexample_domain", "counterexample_env", "domain_asymmetries"):
            if k in probe and k not in result:
                result[k] = probe[k]
        if "reason" in probe:
            result["verdict_reason"] = probe["reason"]

    return result


# ---------------------------------------------------------------------------
# Step-by-Step Derivation, Domain Constraint & Equation Dependency DAG Auditor
# ---------------------------------------------------------------------------

_CMP_OPS = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


def extract_lhs_rhs_symbols(latex: str) -> tuple[str | None, list[str]]:
    """Split an equation on top-level '=' to identify the defined LHS symbol and RHS symbols."""
    cleaned = re.sub(r"\\(?:le|ge|ne|approx|equiv|propto)\b", " ", latex)
    if "=" not in cleaned:
        return None, extract_symbols_from_latex(latex)
    lhs_str, rhs_str = cleaned.split("=", 1)
    lhs_syms = extract_symbols_from_latex(lhs_str)
    rhs_syms = extract_symbols_from_latex(rhs_str)
    lhs_target = lhs_syms[0] if len(lhs_syms) == 1 else (lhs_syms[0] if lhs_syms else None)
    return lhs_target, rhs_syms


def evaluate_domain_predicate(predicate: str, env: dict[str, float] | None) -> dict[str, Any]:
    """Safely check numeric domain/boundary predicates like 'T > 0' or '0 < alpha < 1' against env."""
    raw = predicate.strip()
    if not env:
        return {"predicate": raw, "status": "unverifiable_no_env", "satisfied": None}
    norm = raw.replace("≥", ">=").replace("≤", "<=").replace("≠", "!=").replace("≪", "<").replace("<<", "<").replace("≫", ">").replace(">>", ">")
    norm = re.sub(r"\b(?:for|where|with|assuming)\b", "", norm, flags=re.I).strip(" ,.;()")
    # Require a comparison operator
    if not re.search(r"<=|>=|!=|==|<|>", norm):
        return {"predicate": raw, "status": "qualitative_condition", "satisfied": None}
    # Normalize single '=' to '==' when not part of <=, >=, !=, ==
    norm = re.sub(r"(?<![<>!=])=(?!=)", "==", norm)
    expr_py = normalize_math_expr(norm)
    norm_env = {normalize_math_expr(k): float(v) for k, v in env.items()}
    try:
        tree = ast.parse(expr_py, mode="eval")
        if not isinstance(tree.body, ast.Compare):
            return {"predicate": raw, "status": "qualitative_condition", "satisfied": None}

        def _eval_node(node: ast.AST) -> float:
            sub_expr = ast.Expression(body=node)
            return safe_eval_ast(ast.unparse(sub_expr), norm_env)

        left_val = _eval_node(tree.body.left)
        ok = True
        for op, comp in zip(tree.body.ops, tree.body.comparators):
            if type(op) not in _CMP_OPS:
                return {"predicate": raw, "status": "qualitative_condition", "satisfied": None}
            right_val = _eval_node(comp)
            if not _CMP_OPS[type(op)](left_val, right_val):
                ok = False
                break
            left_val = right_val
        return {"predicate": raw, "status": "satisfied" if ok else "violated", "satisfied": ok}
    except Exception as exc:
        return {"predicate": raw, "status": "unverifiable_symbolic", "satisfied": None, "note": str(exc)[:120]}


_EQ_REF_RE = re.compile(
    r"(?:Eqs?\.?|Equations?|Formula|公式|式)\s*\(?\s*([0-9]+[A-Za-z]?)\s*\)?(?:\s*(?:and|to|&|,|与)\s*\(?\s*([0-9]+[A-Za-z]?)\s*\)?)?",
    re.I,
)

_PREMISE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("approximation", re.compile(
        r"(?:steady[- ]state|quasi[- ]steady|in\s+the\s+limit\s+of|high[- ]overpotential\s+limit|low[- ]overpotential\s+limit|"
        r"dilute\s+limit|small[- ]angle|first[- ]order\s+approximation|Taylor\s+expansion|neglecting\s+[^.;,]{3,80}|"
        r"for\s+[A-Za-z0-9_\\]+\s*(?:<<|>>|≪|≫)\s*[A-Za-z0-9_.\-]+|lineariz(?:ing|ed)|ignoring\s+[^.;,]{3,60}|"
        r"稳态|近似|忽略[^。；，]{2,40}|一阶展开)",
        re.I,
    )),
    ("assumption", re.compile(
        r"(?:assuming\s+[^.;]{4,110}|under\s+the\s+assumption\s+[^.;]{4,110}|we\s+assume\s+[^.;]{4,110}|"
        r"provided\s+that\s+[^.;]{4,90}|suppose\s+[^.;]{4,90}|when\s+no\s+inflection\s+points[^.;]{0,80}|"
        r"假设[^。；]{3,60})",
        re.I,
    )),
    ("boundary_or_domain_condition", re.compile(
        r"(?:boundary\s+condition[^.;]{0,90}|initial\s+condition[^.;]{0,90}|valid\s+for\s+[^.;]{3,80}|"
        r"cutoff\s+[^.;]{0,60}|until\s+[0-9.]+\s*[A-Za-z]+|at\s+[A-Za-z]\s*=\s*0|"
        r"\(\s*[A-Za-z_][A-Za-z0-9_]*\s*(?:>=|>|<=|<|≥|≤)\s*[-+]?[0-9.]+\s*\)|"
        r"where\s+[A-Za-z_][A-Za-z0-9_]*\s*(?:>=|>|<=|<|≥|≤)\s*[-+]?[0-9.]+|"
        r"边界条件[^。；]{0,50}|初始条件[^。；]{0,50}|成立范围[^。；]{0,50})",
        re.I,
    )),
    ("derivation_operation", re.compile(
        r"(?:substituting\s+Eq\.?\s*\(?[0-9]+\)?[^.;]{0,90}|taking\s+the\s+(?:natural\s+)?logarithm[^.;]{0,80}|"
        r"rearranging\s+Eq\.?\s*\(?[0-9]+\)?[^.;]{0,80}|combining\s+Eqs?\.?\s*\(?[0-9]+\)?[^.;]{0,90}|"
        r"differentiating\s+[^.;]{3,70}|integrating\s+[^.;]{3,70}|solving\s+for\s+[^.;]{2,60})",
        re.I,
    )),
]


def _extract_label_num(label: str | None, formula_id: str) -> str | None:
    if label:
        m = re.search(r"([0-9]+[A-Za-z]?)", label)
        if m:
            return m.group(1)
    m2 = re.search(r"(?:eq|formula)[_-]?([0-9]+[A-Za-z]?)", formula_id, re.I)
    return m2.group(1) if m2 else None


def extract_derivation_assumptions_and_boundaries(
    paras: list[dict[str, str]],
    formulas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract grounded assumptions, approximations, boundary conditions, and derivation cues from prose."""
    label_to_fid: dict[str, str] = {}
    for f in formulas:
        num = _extract_label_num(f.get("label"), str(f.get("formula_id", "")))
        if num:
            label_to_fid[num] = str(f["formula_id"])

    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    cid = 0
    for p in paras:
        txt = " ".join(p["text"].split())
        if not txt:
            continue
        linked_eqs: list[str] = []
        for rm in _EQ_REF_RE.finditer(txt):
            for g in (rm.group(1), rm.group(2)):
                if g and g in label_to_fid and label_to_fid[g] not in linked_eqs:
                    linked_eqs.append(label_to_fid[g])
        for cat, pat in _PREMISE_PATTERNS:
            for m in pat.finditer(txt):
                span = m.group(0).strip(" ,.;:()")
                sig = (p["paragraph_id"], cat, span.lower()[:80])
                if sig in seen or len(span) < 4:
                    continue
                seen.add(sig)
                cid += 1
                start = max(0, m.start() - 35)
                end = min(len(txt), m.end() + 65)
                findings.append({
                    "condition_id": f"C{cid:03d}",
                    "category": cat,
                    "linked_equations": linked_eqs,
                    "text": span,
                    "paragraph_id": p["paragraph_id"],
                    "quote": txt[start:end].strip(),
                })
    return findings


def build_equation_dependency_dag(
    formulas: list[dict[str, Any]],
    paras: list[dict[str, str]],
    derivation_chains: list[dict[str, Any]] | None = None,
    derivation_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build cross-equation dependency DAG from prose citations, derivation chains, and LHS->RHS symbol flows."""
    nodes: list[dict[str, Any]] = []
    label_to_fid: dict[str, str] = {}
    lhs_owner: dict[str, str] = {}
    fid_order: dict[str, int] = {}

    for idx, f in enumerate(formulas):
        fid = str(f.get("formula_id") or f"eq_{idx+1}")
        fid_order[fid] = idx
        lhs_sym, rhs_syms = extract_lhs_rhs_symbols(str(f.get("latex") or ""))
        num = _extract_label_num(f.get("label"), fid)
        if num:
            label_to_fid[num] = fid
        if lhs_sym and lhs_sym not in lhs_owner:
            lhs_owner[lhs_sym] = fid
        nodes.append({
            "formula_id": fid,
            "label": f.get("label"),
            "lhs_symbol": lhs_sym,
            "rhs_symbols": rhs_syms,
            "verification_status": f.get("verification_status", "parsed"),
        })

    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str, str]] = set()

    def _add_edge(src: str, dst: str, rel: str, syms: list[str], anchor: str, quote: str) -> None:
        if not src or not dst or src == dst:
            return
        if src not in fid_order or dst not in fid_order:
            return
        key = (src, dst, rel)
        if key in edge_keys:
            return
        edge_keys.add(key)
        edges.append({
            "from_formula_id": src,
            "to_formula_id": dst,
            "relation_type": rel,
            "shared_symbols": syms,
            "source_anchor": anchor,
            "evidence_quote": quote[:200],
        })

    # 1. Explicit derivation_chains / derivation_checks declared edges
    for ch in (derivation_chains or []):
        for st in (ch.get("steps") or []):
            src = str(st.get("from_formula_id") or "")
            dst = str(st.get("to_formula_id") or "")
            if src and dst:
                _add_edge(src, dst, "derivation_chain", [], f"chain:{ch.get('chain_id', 'C')}:{st.get('step_id', 'S')}", str(st.get("operation") or ""))
    for dc in (derivation_checks or []):
        src = str(dc.get("from_formula_id") or dc.get("formula_id") or "")
        dst = str(dc.get("to_formula_id") or "")
        if src and dst:
            _add_edge(src, dst, "derivation_chain", [], f"check:{dc.get('step_id', 'S')}", str(dc.get("operation") or "algebraic_step"))

    # 2. Explicit cross-equation prose citations (e.g. "substituting Eq. (1) into Eq. (2)", "from Eq. (1) ... Eq. (2)")
    for p in paras:
        txt = " ".join(p["text"].split())
        refs: list[str] = []
        for m in _EQ_REF_RE.finditer(txt):
            for g in (m.group(1), m.group(2)):
                if g and g in label_to_fid:
                    fid = label_to_fid[g]
                    if fid not in refs:
                        refs.append(fid)
        if len(refs) >= 2:
            for a, b in zip(refs, refs[1:]):
                src, dst = (a, b) if fid_order[a] < fid_order[b] else (b, a)
                _add_edge(src, dst, "explicit_citation", [], p["paragraph_id"], txt)

    # 3. Symbol-flow dependencies: earlier LHS symbol reused in later equation's RHS
    for n in nodes:
        dst = n["formula_id"]
        for rsym in n["rhs_symbols"]:
            src = lhs_owner.get(rsym)
            if src and src != dst and fid_order[src] < fid_order[dst]:
                _add_edge(src, dst, "symbol_flow", [rsym], f"symbol:{rsym}", f"LHS symbol `{rsym}` from `{src}` enters RHS of `{dst}`.")

    # Kahn topological sort
    indeg: dict[str, int] = {n["formula_id"]: 0 for n in nodes}
    adj: dict[str, list[str]] = {n["formula_id"]: [] for n in nodes}
    seen_pair: set[tuple[str, str]] = set()
    for e in edges:
        pair = (e["from_formula_id"], e["to_formula_id"])
        if pair not in seen_pair:
            seen_pair.add(pair)
            adj[pair[0]].append(pair[1])
            indeg[pair[1]] += 1
    queue = [fid for fid in fid_order if indeg[fid] == 0]
    topo: list[str] = []
    while queue:
        cur = queue.pop(0)
        topo.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    has_cycle = len(topo) < len(nodes)
    root_eqs = [n["formula_id"] for n in nodes if all(e["to_formula_id"] != n["formula_id"] for e in edges)]
    derived_eqs = [n["formula_id"] for n in nodes if any(e["to_formula_id"] == n["formula_id"] for e in edges)]

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "has_cycle": has_cycle,
        "topological_order": topo,
        "root_equations": root_eqs,
        "derived_equations": derived_eqs,
        "nodes": nodes,
        "edges": edges,
    }


def audit_derivation_chain(
    chain_spec: dict[str, Any],
    halted_formula_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Audit a multi-step mathematical derivation chain: equivalence, intermediate values, domain & units."""
    halted = halted_formula_ids or set()
    chain_id = str(chain_spec.get("chain_id") or "DCHAIN")
    shared_env = chain_spec.get("test_env") or {}
    steps_out: list[dict[str, Any]] = []

    for idx, st in enumerate(chain_spec.get("steps") or [], start=1):
        sid = str(st.get("step_id") or f"{chain_id}_S{idx}")
        from_fid = str(st.get("from_formula_id") or "")
        to_fid = str(st.get("to_formula_id") or "")
        expr_before = str(st.get("expr_before") or st.get("lhs") or "")
        expr_after = str(st.get("expr_after") or st.get("rhs") or "")
        operation = str(st.get("operation") or "algebraic_transformation")
        assumptions = [str(a) for a in (st.get("assumptions") or [])]
        approximations = [str(a) for a in (st.get("approximations") or [])]
        boundary_conditions = [str(b) for b in (st.get("boundary_conditions") or [])]
        domain_constraints = [str(d) for d in (st.get("domain_constraints") or [])]
        step_env = {**shared_env, **(st.get("test_env") or {})}
        approx_tol = float(st.get("approx_rel_tol", 0.05)) if approximations else 1e-9

        if (from_fid and from_fid in halted) or (to_fid and to_fid in halted):
            steps_out.append({
                "step_id": sid,
                "from_formula_id": from_fid or None,
                "to_formula_id": to_fid or None,
                "operation": operation,
                "assumptions": assumptions,
                "approximations": approximations,
                "boundary_conditions": boundary_conditions,
                "method": "halted_ocr_noise",
                "equivalent": None,
                "step_verdict": "halted_ocr_noise",
                "reason": "Skipped step because linked formula failed OCR noise gate.",
            })
            continue

        chk = check_equivalence(expr_before, expr_after, test_env=step_env or None) if (expr_before and expr_after) else {
            "method": "unverified_missing_expr",
            "equivalent": None,
        }

        # Evaluate numeric domain predicates (from domain_constraints + assumptions + boundary_conditions)
        domain_checks = [
            evaluate_domain_predicate(pred, step_env or None)
            for pred in (domain_constraints + assumptions + boundary_conditions)
        ]
        domain_violations = [d for d in domain_checks if d.get("status") == "violated"]

        # Optional step-level dimensional self-consistency check
        dim_check = None
        if st.get("unit_before") and st.get("unit_after"):
            dim_check = check_dimensional_balance(str(st["unit_before"]), str(st["unit_after"]))

        rel_err = chk.get("rel_error")
        if chk.get("method") == "halted_ocr_noise":
            verdict = "halted_ocr_noise"
        elif domain_violations:
            verdict = "domain_violation"
        elif dim_check and dim_check.get("consistent") is False:
            verdict = "dimensional_mismatch"
        elif chk.get("equivalent") is True:
            verdict = "verified"
        elif approximations and isinstance(rel_err, (int, float)) and rel_err <= approx_tol:
            verdict = "verified_under_approximation"
        elif chk.get("equivalent") is False:
            verdict = "failed_equivalence"
        elif assumptions or approximations or boundary_conditions:
            verdict = "conditional_on_stated_premise"
        else:
            verdict = "not_verifiable_from_supplied_materials"

        steps_out.append({
            "step_id": sid,
            "from_formula_id": from_fid or None,
            "to_formula_id": to_fid or None,
            "operation": operation,
            "expr_before": expr_before,
            "expr_after": expr_after,
            "assumptions": assumptions,
            "approximations": approximations,
            "boundary_conditions": boundary_conditions,
            "method": chk.get("method"),
            "equivalent": chk.get("equivalent"),
            "numeric_before": chk.get("numeric_v1"),
            "numeric_after": chk.get("numeric_v2"),
            "rel_error": rel_err,
            "domain_checks": [d for d in domain_checks if d["status"] in ("satisfied", "violated")],
            "domain_violations": domain_violations,
            "dimensional_check": dim_check,
            "step_verdict": verdict,
        })

    verdicts = [s["step_verdict"] for s in steps_out]
    if any(v in ("failed_equivalence", "domain_violation", "dimensional_mismatch") for v in verdicts):
        chain_status = "failed"
    elif any(v == "halted_ocr_noise" for v in verdicts):
        chain_status = "halted_ocr_noise"
    elif steps_out and all(v in ("verified", "verified_under_approximation") for v in verdicts):
        chain_status = "verified"
    else:
        chain_status = "partial"

    return {
        "chain_id": chain_id,
        "title": str(chain_spec.get("title") or chain_id),
        "chain_status": chain_status,
        "step_count": len(steps_out),
        "verified_step_count": sum(1 for v in verdicts if v in ("verified", "verified_under_approximation")),
        "steps": steps_out,
    }


def run_self_tests() -> dict[str, Any]:
    """Run deterministic stdlib unit tests for equivalence sampling, unit parsing, and symbol guards."""
    checks: list[dict[str, Any]] = []

    def _record(name: str, passed: bool, detail: Any = None) -> None:
        checks.append({"test": name, "passed": bool(passed), "detail": detail})

    # 1. Multi-domain sampling without test_env: true algebraic identity
    r_id = _multi_domain_equivalence_probe("((IR_t * exp(beta_1) - IR_t) / IR_t) * 100", "(exp(beta_1) - 1) * 100")
    _record("equiv_true_identity_no_env", r_id["equivalent"] is True and r_id["samples_tested"] >= 3, r_id)

    # 2. Multi-domain sampling catches false identity that coincides at x=1
    r_false = check_equivalence("x", "x**2", test_env={"x": 1.0})
    _record("equiv_rejects_single_point_coincidence", r_false["equivalent"] is False, r_false)

    # 3. Free unparseable / unknown symbolic callable -> explicit not_verifiable (never false pass)
    r_unver = check_equivalence("unknown_op(x)", "x + 1")
    _record("equiv_free_symbolic_not_verifiable", r_unver["equivalent"] is None and r_unver["method"] == "not_verifiable", r_unver)

    # 4. Unit parser rejects English article 'a' and subscripted symbol 'E_a', accepts 'mA/cm^2' and 'A'
    u_art = parse_unit_expression("a")
    u_sub = parse_unit_expression("E_a")
    u_amp = parse_unit_expression("A")
    u_comp = parse_unit_expression("mA/cm^2")
    _record("unit_rejects_article_a", u_art["ok"] is False, u_art)
    _record("unit_rejects_subscript_E_a", u_sub["ok"] is False, u_sub)
    _record("unit_accepts_uppercase_A", u_amp["ok"] is True and u_amp["dimension_named"] == {"I": 1}, u_amp)
    _record("unit_accepts_composite_mA_cm2", u_comp["ok"] is True and abs(u_comp["si_scale_factor"] - 10.0) < 1e-9, u_comp)

    all_ok = all(c["passed"] for c in checks)
    return {"ok": all_ok, "passed": sum(1 for c in checks if c["passed"]), "total": len(checks), "checks": checks}


if __name__ == "__main__":
    import json
    import sys

    res = run_self_tests()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if res["ok"] else 1)
